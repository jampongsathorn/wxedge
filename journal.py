#!/usr/bin/env python3
"""
journal.py — ระบบเก็บสถิติเดินหน้า (forward test) สำหรับวัด hit rate จริงของตัวเอง

  log   : บันทึกคำทำนายของทุกตลาดที่เปิดอยู่ ลง data/journal.csv (ใช้ซ้ำได้ทุกวัน/ทุกชั่วโมง)
  score : เทียบคำทำนายที่บันทึกไว้กับ "bucket ที่ตลาด resolve จริง" และค่าจริงจากสถานี
          → hit rate, coverage, PnL ของ value bet (ใช้ราคาที่บันทึกตอน log)

ทำไมต้องมี: archive ของ ensemble ให้ข้อมูลย้อนหลังแค่ ~4-5 วัน การเก็บ log ทุกวัน
คือวิธีเดียวที่จะสร้างสถิติที่แน่นขึ้นเรื่อย ๆ (และเป็นตัวตัดสินว่า edge จริงหรือไม่)

รัน:
  python3 journal.py log --cities nyc,london,miami,tel-aviv,madrid,paris
  python3 journal.py score
"""
import argparse, csv, json, os, sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wxedge as W

HERE = os.path.dirname(os.path.abspath(__file__))
JOURNAL = os.path.join(HERE, "data", "journal.csv")
FIELDS = ["logged_at", "city", "target", "lead_days", "unit", "station",
          "bin_top1", "p_top1", "bin2", "p2", "coverage_p", "strategy",
          "vb_bin", "vb_edge", "vb_price", "vb_kelly", "mean_c", "sigma_c",
          "history_top1", "history_n", "resolved_bin", "hit1", "hit2", "vb_win"]


def ensure():
    os.makedirs(os.path.dirname(JOURNAL), exist_ok=True)
    if not os.path.exists(JOURNAL):
        with open(JOURNAL, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, FIELDS).writeheader()


def hr_for(city, path):
    try:
        return W.load_hitrate(path).get(city, {})
    except Exception:
        return {}


def cmd_log(a):
    ensure()
    cfgs = json.load(open(W.STATIONS))
    cities = list(cfgs) if a.cities == "all" else a.cities.split(",")
    hr_cache = {}
    n = 0
    with open(JOURNAL, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, FIELDS)
        for city in cities:
            if city not in cfgs:
                continue
            cfg = cfgs[city]
            tz = ZoneInfo(cfg["tz"])
            for offset in (a.offset, a.offset + 1):
                target = (datetime.now(tz) + timedelta(days=offset)).strftime("%Y-%m-%d")
                ev = W.polymarket_event(city, target, False)
                if not ev:
                    continue
                try:
                    res, samples = W.predict(cfg, target, days_cal=a.days_cal)
                except Exception as e:
                    print("  ! %s %s: %s" % (city, target, e))
                    continue
                bins = W.bin_probabilities(samples, W.event_bins(ev), cfg["unit"])
                rows = W.edge_table(bins, 0.0, 0.0)
                if not rows:
                    continue
                top = max(rows, key=lambda r: r["p"])
                idx = rows.index(top)
                cands = [rows[i] for i in (idx - 1, idx + 1) if 0 <= i < len(rows)]
                second = max(cands, key=lambda r: r["p"]) if cands else None
                best_edge = max(rows, key=lambda r: r["edge"])
                rec = dict(logged_at=datetime.now(tz).strftime("%Y-%m-%dT%H:%M"),
                           city=city, target=target, lead_days=res["lead_days"], unit=cfg["unit"],
                           station=cfg["station"],
                           bin_top1=top["label"], p_top1=round(top["p"], 4),
                           bin2=second["label"] if second else "", p2=round(second["p"], 4) if second else "",
                           coverage_p=round(top["p"] + (second["p"] if second else 0), 4),
                           strategy=("top-1" if top["p"] >= 0.55 else
                                     ("top-2" if second and top["p"] + second["p"] >= 0.75 else "รอ")),
                           vb_bin=best_edge["label"], vb_edge=round(best_edge["edge"], 4),
                           vb_price=best_edge["price"], vb_kelly=round(best_edge["kelly"], 3),
                           mean_c=res["mean_c"], sigma_c=res["sigma_res_c"])
                w.writerow(rec)
                n += 1
                print("  %-12s %s lead=%d | %-12s p=%.2f | VB %s edge=%+.3f price=%.3f"
                      % (city, target, res["lead_days"], top["label"], top["p"],
                         best_edge["label"], best_edge["edge"], best_edge["price"]))
    print("บันทึก %d แถว → %s" % (n, JOURNAL))


def cmd_score(a):
    if not os.path.exists(JOURNAL):
        print("ยังไม่มี journal — รัน log ก่อน")
        return
    rows = list(csv.DictReader(open(JOURNAL, encoding="utf-8")))
    if not rows:
        print("journal ว่าง")
        return
    cfgs = json.load(open(W.STATIONS))
    # เอาแถวล่าสุดต่อ (city,target) และ target ที่ผ่านไปแล้ว
    keep = {}
    for r in rows:
        k = (r["city"], r["target"])
        if k not in keep or r["logged_at"] > keep[k]["logged_at"]:
            keep[k] = r
    hit1 = hit2 = n = scored = 0
    vb_n = vb_win = 0
    vb_pnl = 0.0
    out = []
    for (city, target), r in sorted(keep.items()):
        cfg = cfgs.get(city)
        if not cfg:
            continue
        # ประเมินเฉพาะวันที่ "จบแล้ว" ในเขตเวลาของสถานี (ไม่งั้นจะนับค่า max ช่วงเช้า)
        tz = ZoneInfo(cfg["tz"])
        now_local = datetime.now(tz)
        if target > now_local.strftime("%Y-%m-%d"):
            continue
        if target == now_local.strftime("%Y-%m-%d") and now_local.hour < 23:
            continue
        v, _ = W.obs_daily_max(cfg, target)
        if v is None:                       # ยังไม่รู้ผล
            continue
        lab = W.bucket_label(v, cfg["unit"])
        h1 = (lab == r["bin_top1"])
        h2 = (lab in (r["bin_top1"], r.get("bin2") or ""))
        win_vb = (lab == r["vb_bin"])
        scored += 1
        hit1 += h1; hit2 += h2
        if r["vb_bin"] and float(r["vb_price"] or 0) > 0:
            vb_n += 1
            vb_win += win_vb
            vb_pnl += ((1 / float(r["vb_price"]) - 1) if win_vb else -1.0)
        out.append((city, target, r["bin_top1"], lab, h1, h2, r["vb_bin"], win_vb,
                    float(r["vb_price"] or 0)))
        print("  %-12s %s | ทาย %-12s จริง %-12s %s | VB %s %s (price %.3f)"
              % (city, target, r["bin_top1"], lab, "✅" if h1 else "❌",
                 r["vb_bin"], "✅" if win_vb else "❌", float(r["vb_price"] or 0)))
    if scored:
        print("\n  ประเมินแล้ว %d ตลาด-วัน" % scored)
        print("  top-1 hit: %.0f%% | top-2 (bin+เพื่อนบ้าน) hit: %.0f%%" % (100 * hit1 / scored, 100 * hit2 / scored))
        if vb_n:
            print("  value bet: %d ไม้ ถูก %.0f%% | PnL %+.1f หน่วย (ไม้ละ 1) = ROI %+.1f%%"
                  % (vb_n, 100 * vb_win / vb_n, vb_pnl, 100 * vb_pnl / vb_n))
    else:
        print("  ยังไม่มีคำทำนายที่รู้ผล (รอตลาด resolve)")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("log")
    p.add_argument("--cities", default="all")
    p.add_argument("--offset", type=int, default=0, help="0 = วันนี้, 1 = พรุ่งนี้")
    p.add_argument("--days-cal", type=int, default=45)
    p.set_defaults(func=cmd_log)
    p = sub.add_parser("score")
    p.set_defaults(func=cmd_score)
    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
