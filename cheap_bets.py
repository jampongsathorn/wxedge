#!/usr/bin/env python3
"""
cheap_bets.py — ตอบคำถาม: "ซื้อ bin ราคา < 0.25 แล้วชนะตอนปิด" ได้จริงไหม?

วิธี: ดึง "ราคาจริงของทุก bin" (CLOB prices-history) ของทุกตลาดที่ resolve แล้ว
     + โมเดล intraday (ค่าที่วัดได้ถึงชั่วโมงนั้น + การกระจายตัวของส่วนที่เพิ่มอีก)
     แล้ววัด: ราคาเท่าไร, ทายถูกกี่ %, และ EV/ROI ของ "ไม้ถูก" (price ≤ 0.25)

รัน: python3 cheap_bets.py --days 60 --hours 12,15,16 --cities all --pmax 0.25
ผล: data/cheap_bets.csv + reports/cheap_bets.md
"""
import argparse, collections, concurrent.futures as cf, csv, json, math, os, statistics as st, sys
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wxedge as W
import intraday as I

# CLOB/Gamma เป็น CDN ปกติ → ยิงถี่ได้ (ค่าเริ่มต้น 1.2s/คำขอ ทำให้ backtest ช้ามาก)
W.HOST_RATE["clob.polymarket.com"] = 0.15
W.HOST_RATE["gamma-api.polymarket.com"] = 0.15

HERE = os.path.dirname(os.path.abspath(__file__))


def market_bins_of(ev):
    """คืน [(label, (lo,hi), token)] ของทุก bin ในตลาดนี้"""
    out = []
    for m in (ev.get("markets") or []):
        lab = (m.get("groupItemTitle") or "").strip()
        b = W.parse_bin(lab)
        if not b:
            continue
        toks = json.loads(m.get("clobTokenIds") or "[]")
        tok = toks[0] if toks else None
        out.append((lab, b, tok))
    return out


def price_series(tok, day=None):
    """ราคารายชั่วโมง: ระบุช่วงเวลา (startTs/endTs) — แม่นกว่า interval=max และย้อนได้ไกลกว่า 1 เดือน"""
    if not tok:
        return []
    urls = []
    if day:
        from datetime import timezone
        d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        s0 = int((d - timedelta(days=2)).timestamp())
        e0 = int((d + timedelta(days=1)).timestamp())
        urls.append("https://clob.polymarket.com/prices-history?market=%s&startTs=%d&endTs=%d&fidelity=60" % (tok, s0, e0))
    urls.append("https://clob.polymarket.com/prices-history?market=%s&interval=max&fidelity=60" % tok)
    for u in urls:
        try:
            h = W.get_json(u, ttl=86400, tag="clob")
            pts = [(p["t"], p["p"]) for p in (h.get("history") or [])]
            if pts:
                return pts
        except Exception:
            continue
    return []


def price_at(series, ts):
    """ราคา ณ เวลา ts (epoch) — ต้องมีจุดก่อน ts ไม่เกิน 6 ชั่วโมง"""
    best = None
    for t, p in series:
        if t <= ts:
            best = (t, p)
        else:
            break
    if best and ts - best[0] <= 6 * 3600:
        return best[1]
    return None


def model_probs(inc_samples_bins, unit, mbins):
    """จับค่าตัวอย่าง (หน่วยตลาด, เป็นทศนิยม) เข้า bin ของตลาดจริง
    mbins: [(label,(lo,hi))] — รองรับ 'or higher' / 'or below' """
    cnt = collections.Counter()
    for v in inc_samples_bins:
        vv = int(math.floor(v + 0.5))          # ปัดแบบตลาด
        for lab, (lo, hi) in mbins:
            if lo == float("-inf"):
                if vv <= hi:
                    cnt[lab] += 1
                    break
            elif hi == float("inf"):
                if vv >= lo:
                    cnt[lab] += 1
                    break
            elif lo <= vv <= hi:
                cnt[lab] += 1
                break
    tot = sum(cnt.values()) or 1
    return {k: v / tot for k, v in cnt.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--hours", default="12,15,16")
    ap.add_argument("--cities", default="all")
    ap.add_argument("--lookback", type=int, default=30)
    ap.add_argument("--pmax", type=float, default=0.25, help="เพดานราคาที่ถือว่า 'ถูก'")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default=os.path.join(HERE, "data", "cheap_bets.csv"))
    ap.add_argument("--report", default=os.path.join(HERE, "reports", "cheap_bets.md"))
    a = ap.parse_args()
    hours = [int(x) for x in a.hours.split(",")]

    cfgs = json.load(open(W.STATIONS))
    cities = [c for c in cfgs if cfgs[c].get("icao")] if a.cities in ("", "all") else a.cities.split(",")
    labels = {}
    for r in csv.DictReader(open(os.path.join(HERE, "data", "market_labels.csv"), encoding="utf-8")):
        labels[(r["city"], r["date"])] = r

    rows = []
    for city in cities:
        cfg = cfgs[city]
        ds = sorted(d for (cc, d) in labels if cc == city)
        if not ds:
            continue
        d0 = datetime.strptime(ds[0], "%Y-%m-%d").date()
        d1 = datetime.strptime(ds[-1], "%Y-%m-%d").date()
        try:
            ser = I.hourly_series(cfg, d0 - timedelta(days=a.lookback), d1)
        except Exception as e:
            print("  %s: IEM error %s" % (city, str(e)[:40]), flush=True)
            continue
        tz = ZoneInfo(cfg["tz"])
        # ── ดึงราคาทุก bin แบบขนาน ──
        def fetch(day):
            ev = W.polymarket_event(city, day)
            if not ev:
                return day, None, None
            mb = market_bins_of(ev)
            return day, mb, None
        with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
            evs = dict()
            for day, mb, _ in ex.map(fetch, ds):
                evs[day] = mb
        # ราคา: ทุก bin ของทุกวัน (ขนาน)
        jobs = [(day, mb[0], mb[2]) for day, mb in evs.items() if mb for mb in [mb] for _ in [0]]
        jobs = []
        for day, mb in evs.items():
            if not mb:
                continue
            for lab, b, tok in mb:
                jobs.append((day, lab, b, tok))
        with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
            series = list(ex.map(lambda j: price_series(j[3], j[0]), jobs))
        # ── คำนวณต่อวัน ──
        for day in ds:
            mb = evs.get(day)
            if not mb:
                continue
            mbin_labels = [(lab, b) for lab, b, _ in mb]
            ml = labels[(city, day)]
            dd = datetime.strptime(day, "%Y-%m-%d").date()
            idx = {j: i for i, j in enumerate(jobs) if j[0] == day}
            ser_by_lab = {}
            for j, s in zip(jobs, series):
                if j[0] == day:
                    ser_by_lab[j[1]] = s
            if day not in ser:
                continue
            for h in hours:
                past = [v for hh, v in ser[day].items() if hh <= h]
                if len(past) < 3:
                    continue
                mx = max(past)
                incs = []
                for hd in sorted(d for d in ser if d < day)[-a.lookback:]:
                    p2 = [v for hh, v in ser[hd].items() if hh <= h]
                    if len(p2) < 3:
                        continue
                    incs.append(max(ser[hd].values()) - max(p2))
                if len(incs) < 8:
                    continue
                samples = [mx + x for x in incs]
                vals = [s * 9 / 5 + 32 if cfg["unit"] == "F" else s for s in samples]
                mp = model_probs(vals, cfg["unit"], mbin_labels)
                ts = int(datetime(dd.year, dd.month, dd.day, h, 0, tzinfo=tz).timestamp())
                for lab, b, tok in mb:
                    s = ser_by_lab.get(lab) or []
                    p = price_at(s, ts)
                    if p is None:
                        continue
                    rows.append(dict(city=city, date=day, hour=h, unit=cfg["unit"], bin=lab,
                                     model_p=round(mp.get(lab, 0.0), 4), price=round(p, 4),
                                     won=1 if lab == ml["winner"] else 0))
        print("  %-14s เสร็จ (%d แถวสะสม)" % (city, len(rows)), flush=True)

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["city", "date", "hour", "unit", "bin", "model_p", "price", "won"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print("เขียน %s (%d แถว)" % (a.out, len(rows)))

    # ────────────────── สรุปผล ──────────────────
    def stats(rs):
        if not rs:
            return dict(n=0, hit=0, avg_p=0, ev=0, roi=0, stake=0, ret=0)
        n = len(rs)
        hit = sum(r["won"] for r in rs) / n
        avg = st.mean(r["price"] for r in rs)
        ret = sum(r["won"] for r in rs)
        stake = sum(r["price"] for r in rs)
        return dict(n=n, hit=hit, avg_p=avg, ev=hit - avg, roi=(ret - stake) / stake if stake else 0,
                    stake=stake, ret=ret)

    L = ["# ซื้อ bin ราคาถูก (< $%.2f) แล้วชนะตอนปิด — วัดจากราคาจริงทุก bin\n" % a.pmax,
         "วันที่: %s · ข้อมูล: %d แถว (bin × วัน × ชั่วโมง) จาก %d เมือง\n"
         % (datetime.now().strftime("%Y-%m-%d %H:%M"), len(rows), len({r["city"] for r in rows}))]

    for h in hours:
        L.append("\n## ชั่วโมง %02d:00 (เวลาท้องถิ่น) — ราคา ณ ชั่วโมงนั้น\n" % h)
        L.append("| กลุ่มที่ซื้อ | n ไม้ | ชนะ% | ราคาเฉลี่ย | EV/หุ้น | ROI |")
        L.append("|---|---|---|---|---|---|")
        hs = [r for r in rows if r["hour"] == h]
        for name, rs in [
            ("ซื้อทุก bin ราคา < %.2f" % a.pmax, [r for r in hs if r["price"] < a.pmax]),
            ("+ โมเดล p ≥ 0.25", [r for r in hs if r["price"] < a.pmax and r["model_p"] >= 0.25]),
            ("+ โมเดล p ≥ 0.35", [r for r in hs if r["price"] < a.pmax and r["model_p"] >= 0.35]),
            ("+ โมเดล p ≥ 0.50", [r for r in hs if r["price"] < a.pmax and r["model_p"] >= 0.50]),
            ("+ โมเดล p ≥ 0.65", [r for r in hs if r["price"] < a.pmax and r["model_p"] >= 0.65]),
            ("ทุก bin (ฐานเทียบ)", hs),
            ("bin แพง ≥ %.2f" % a.pmax, [r for r in hs if r["price"] >= a.pmax]),
        ]:
            s = stats(rs)
            if s["n"]:
                L.append("| %s | %d | %.1f%% | %.3f | %+.3f | %+.1f%% |"
                         % (name, s["n"], 100 * s["hit"], s["avg_p"], s["ev"], 100 * s["roi"]))
        # แยกช่วงราคา (เฉพาะที่โมเดล p ≥ 0.35)
        L.append("\n**แยกช่วงราคา** (เฉพาะ bin ที่โมเดลให้ p ≥ 0.35)\n")
        L.append("| ช่วงราคา | n | ชนะ% | ROI |")
        L.append("|---|---|---|---|")
        for lo, hi in [(0, 0.05), (0.05, 0.10), (0.10, 0.15), (0.15, 0.25), (0.25, 0.40), (0.40, 0.60), (0.60, 1.01)]:
            rs = [r for r in hs if lo <= r["price"] < hi and r["model_p"] >= 0.35]
            s = stats(rs)
            if s["n"]:
                L.append("| %.2f–%.2f | %d | %.1f%% | %+.1f%% |" % (lo, hi, s["n"], 100 * s["hit"], 100 * s["roi"]))

    # ตารางเมือง (ใช้ค่าที่ดีที่สุด: ชั่วโมงสุดท้าย + p≥0.35 + ราคา<pmax)
    hlast = hours[-1]
    sel = [r for r in rows if r["hour"] == hlast and r["price"] < a.pmax and r["model_p"] >= 0.35]
    L.append("\n## แยกตามเมือง (%02d:00, ราคา < %.2f, โมเดล p ≥ 0.35)\n" % (hlast, a.pmax))
    L.append("| city | n | ชนะ% | ROI |")
    L.append("|---|---|---|---|")
    per = collections.defaultdict(list)
    for r in sel:
        per[r["city"]].append(r)
    for c, rs in sorted(per.items(), key=lambda kv: -stats(kv[1])["hit"]):
        s = stats(rs)
        L.append("| %s | %d | %.1f%% | %+.1f%% |" % (c, s["n"], 100 * s["hit"], 100 * s["roi"]))

    os.makedirs(os.path.dirname(a.report), exist_ok=True)
    open(a.report, "w", encoding="utf-8").write("\n".join(L) + "\n")

    # ย่อหน้าสรุปบนจอ
    for h in hours:
        hs = [r for r in rows if r["hour"] == h]
        for name, rs in [("ทุก bin <%.2f" % a.pmax, [r for r in hs if r["price"] < a.pmax]),
                         ("<%.2f & p≥0.35" % a.pmax, [r for r in hs if r["price"] < a.pmax and r["model_p"] >= 0.35])]:
            s = stats(rs)
            if s["n"]:
                print("  %02d:00 %-16s n=%4d ชนะ=%5.1f%% ราคา=%.3f EV=%+.3f ROI=%+.1f%%"
                      % (h, name, s["n"], 100 * s["hit"], s["avg_p"], s["ev"], 100 * s["roi"]))
    print("รายงาน: %s" % a.report)


if __name__ == "__main__":
    main()
