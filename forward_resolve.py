#!/usr/bin/env python3
"""forward_resolve.py — ปิดวง forward-test: เติม "เฉลย" ลง log + วัด hit rate จริง

ทำอะไร:
  1) อ่าน data/cheap_live_log.csv (v3) แล้วเติมคอลัมน์ won / resolved_bin / resolved_max_c / resolved_at
     ให้แถวที่ตลาดปิดแล้ว — แหล่งเฉลย: data/market_labels.csv ก่อน (เร็ว/ไม่ต้องเน็ต) แล้วค่อยยิง gamma ถ้ายังว่าง
  2) คำนวณสถิติจริงจาก log: hit rate แยก YES/NO/tier/เมือง · ต้นทุนจ่ายจริง · EV · PnL (ถือถึงเฉลย)
     · Brier score + ตาราง calibration ของ model_p (เทียบว่า p ของเราเชื่อได้แค่ไหน)
  3) เขียน reports/forward_report.md + data/forward_stats.json

รัน:
  python3 forward_resolve.py            # เติมเฉลย + รายงาน
  python3 forward_resolve.py --no-net   # ห้ามยิงเน็ต (ใช้ market_labels.csv เท่านั้น)
  python3 forward_resolve.py --report-only   # ไม่แตะ log แค่คำนวณ/รายงานใหม่
"""
import argparse, csv, json, math, os, sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wxedge as W

LOG = os.path.join(HERE, "data", "cheap_live_log.csv")
LABELS = os.path.join(HERE, "data", "market_labels.csv")
REPORT = os.path.join(HERE, "reports", "forward_report.md")
STATS = os.path.join(HERE, "data", "forward_stats.json")
FILL_COLS = ["won", "resolved_bin", "resolved_max_c", "resolved_at"]


# ── เฉลยจากไฟล์ label (เมือง, วัน) → (winner label, unit, lo, hi) ──
def load_labels():
    out = {}
    if not os.path.exists(LABELS):
        return out
    for r in csv.DictReader(open(LABELS, encoding="utf-8")):
        try:
            out[(r["city"], r["date"])] = (r["winner"], r["unit"], float(r["lo"]), float(r["hi"]))
        except (ValueError, KeyError):
            continue
    return out


def mid_to_c(lo, hi, unit):
    """ค่ากลางของ bin ที่ตลาดตัดสิน (เปิดปลายข้าง → None) แปลงเป็น °C"""
    if not (math.isfinite(lo) and math.isfinite(hi)):
        return None
    mid = (lo + hi) / 2
    return round(mid * 5 / 9 - 32 * 5 / 9, 2) if unit == "F" else round(mid, 2)


def resolve_row(r, labels, cfgs, no_net=False):
    """คืน dict เฉลยหรือ None ถ้ายังเฉลยไม่ได้ (ตลาดยังไม่ปิด/วันยังไม่จบ)"""
    key = (r["city"], r["target"])
    today_local = datetime.now(ZoneInfo(cfgs[r["city"]]["tz"])).date().isoformat()
    if r["target"] >= today_local:        # วันยังไม่จบ → ยังไม่รู้ผล
        return None
    if key in labels:
        win, unit, lo, hi = labels[key]
        return dict(resolved_bin=win, resolved_max_c=mid_to_c(lo, hi, unit))
    if no_net:
        return None
    ev = W.polymarket_event(r["city"], r["target"], no_cache=True)
    if not ev or not ev.get("closed"):
        return None
    for m in ev.get("markets", []):
        try:
            pr = json.loads(m.get("outcomePrices") or "[]")
        except json.JSONDecodeError:
            continue
        if pr and float(pr[0]) > 0.5:
            return dict(resolved_bin=m.get("groupItemTitle"), resolved_max_c=None)
    return None


def fill_log(no_net=False):
    if not os.path.exists(LOG):
        print("ยังไม่มี %s — ยังไม่มีอะไรต้องเติมเฉลย" % LOG)
        return None, 0, 0
    rows = list(csv.DictReader(open(LOG, encoding="utf-8")))
    if not rows:
        print("log ว่าง (มีแค่หัวตาราง) — รอ cron เก็บข้อมูลก่อน")
        return None, 0, 0
    cols = list(rows[0].keys())
    for c in FILL_COLS:
        if c not in cols:
            cols.append(c)
    cfgs = json.load(open(W.STATIONS))
    labels = load_labels()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    filled = unresolved = 0
    for r in rows:
        if r.get("won") not in (None, "") and r.get("resolved_bin") not in (None, ""):
            continue
        sol = resolve_row(r, labels, cfgs, no_net=no_net)
        if not sol:
            unresolved += 1
            continue
        won = 1 if ((r["side"] == "YES") == (r["bin"] == sol["resolved_bin"])) else 0
        r["won"] = won
        r["resolved_bin"] = sol["resolved_bin"]
        r["resolved_max_c"] = sol["resolved_max_c"] if sol["resolved_max_c"] is not None else ""
        r["resolved_at"] = ts
        filled += 1
    with open(LOG, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    print("เติมเฉลย %d แถว · ยังเฉลยไม่ได้ %d แถว (ตลาดยังไม่ปิด/วันยังไม่จบ)" % (filled, unresolved))
    return rows, filled, unresolved


# ── สถิติจริงจาก log ──
def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def summarize(rows, stake=100.0):
    res = [r for r in rows if r.get("won") not in (None, "")]
    o = dict(n=len(rows), resolved=len(res), yes=dict(n=0, w=0, pnl=0.0, cost=0.0),
             no=dict(n=0, w=0, pnl=0.0, cost=0.0), by_tier={}, by_city={}, brier=[], calib={})
    for r in res:
        side = "yes" if str(r["side"]).upper() == "YES" else "no"   # key ใน o เป็นตัวพิมพ์เล็ก
        cost = num(r.get("yes_ask")) if side == "yes" else num(r.get("no_ask_implied"))
        if cost is None:
            continue
        won = int(num(r["won"]) or 0)
        d = o[side]
        d["n"] += 1
        d["w"] += won
        d["cost"] += cost
        d["pnl"] += (stake / cost - stake) if won else -stake       # ถือถึงเฉลย จ่ายตามราคาจริง
        t = o["by_tier"].setdefault(r.get("tier") or "?", dict(n=0, w=0, pnl=0.0))
        t["n"] += 1
        t["w"] += won
        t["pnl"] += (stake / cost - stake) if won else -stake
        c = o["by_city"].setdefault(r["city"], dict(n=0, w=0, pnl=0.0))
        c["n"] += 1
        c["w"] += won
        c["pnl"] += (stake / cost - stake) if won else -stake
        if side == "yes":
            p = num(r.get("model_p"))
            if p is not None:
                o["brier"].append((p - won) ** 2)
                b = o["calib"].setdefault(min(int(p * 10) / 10, 0.9), dict(n=0, w=0))
                b["n"] += 1
                b["w"] += won
    o["brier"] = round(sum(o["brier"]) / len(o["brier"]), 4) if o["brier"] else None
    return o


def money(v):
    """รูปแบบเงินมี comma: +1,234 / -567 (Python %-format ไม่รองรับ comma → ใช้ format())"""
    try:
        return format(float(v), "+,.0f")
    except (TypeError, ValueError):
        return "0"


def rate(d):
    return (100.0 * d["w"] / d["n"]) if d["n"] else float("nan")


def render_report(o, stake=100.0, day=""):
    """สร้างรายงาน markdown จาก o = summarize() — แยกออกมาเพื่อให้เทสต์ได้โดยไม่ต้องมีเน็ต
    (บั๊ก 26 ก.ย. 2026: โค้ดเดิมฝังใน main() → เทสต์ไม่ได้ → พังเงียบจนมีไม้จริงไม้แรก)"""
    L = ["# forward-test — ผลจริงจาก log (ไม่ใช่ backtest)", "",
         "อัปเดต %s · แถวใน log %d · เฉลยแล้ว %d · stake $%.0f/ไม้ (ถือถึงเฉลย)" % (day, o["n"], o["resolved"], stake), ""]
    if o["n"] == 0:
        L += ["**ยังไม่มีข้อมูล** — log มีแค่หัวตาราง รอ cron เก็บไม้ (จะเริ่มมีเมื่อมีเมืองถึงเวลาเข้าไม้)", ""]
    else:
        L += ["| ฝั่ง | ไม้ | ชนะ | hit rate | ต้นทุนเฉลี่ย | PnL รวม |", "|---|---|---|---|---|---|",
              "| YES | %d | %d | %.1f%% | %.3f | $%s |" % (o["yes"]["n"], o["yes"]["w"], rate(o["yes"]),
                                                          (o["yes"]["cost"] / o["yes"]["n"]) if o["yes"]["n"] else float("nan"),
                                                  money(o["yes"]["pnl"])),
              "| NO | %d | %d | %.1f%% | %.3f | $%s |" % (o["no"]["n"], o["no"]["w"], rate(o["no"]),
                                                           (o["no"]["cost"] / o["no"]["n"]) if o["no"]["n"] else float("nan"),
                                                           money(o["no"]["pnl"])), ""]
        if o["brier"] is not None:
            L += ["**Brier score (YES, เทียบ p ของโมเดลกับผลจริง 0/1):** %.4f (ยิ่งต่ำยิ่งดี · 0.25 = เดาสุ่ม)" % o["brier"], ""]
        if o["calib"]:
            L += ["**Calibration ของ model_p (YES) — p ที่บอกควรตรงกับอัตราชนะจริง**", "",
                  "| ช่วง p | ไม้ | ชนะ | จริง |", "|---|---|---|---|"]
            for k in sorted(o["calib"]):
                b = o["calib"][k]
                L += ["| %.1f–%.1f | %d | %d | %.0f%% |" % (k, k + 0.1, b["n"], b["w"], 100.0 * b["w"] / b["n"])]
            L += [""]
        L += ["**แยกตามชั้นไม้**", "", "| ชั้น | ไม้ | ชนะ | hit rate | PnL |", "|---|---|---|---|---|"]
        for t, d in sorted(o["by_tier"].items(), key=lambda kv: -kv[1]["n"]):
            L += ["| %s | %d | %d | %.1f%% | $%s |" % (t, d["n"], d["w"], rate(d), money(d["pnl"]))]
        L += ["", "**แยกตามเมือง (เฉพาะที่มีไม้)**", "", "| เมือง | ไม้ | ชนะ | hit rate | PnL |", "|---|---|---|---|---|"]
        for c, d in sorted(o["by_city"].items(), key=lambda kv: -kv[1]["n"]):
            L += ["| %s | %d | %d | %.1f%% | $%s |" % (c, d["n"], d["w"], rate(d), money(d["pnl"]))]
        L += [""]
    L += ["---", "",
          "**เกณฑ์ผ่านก่อนใช้เงินจริง (จากรายงานกลยุทธ์)**",
          "- log ≥ 50 ไม้ · hit rate จริง ≥ 85% (YES) และ NO ต้องไม่หลุดจาก 100% ในกติกาเดิม",
          "- ต้นทุนจ่ายจริง (ask) ไม่แย่กว่าที่ stress ไว้ (+0.03)",
          "- Brier (YES) ≤ 0.20 → p ของเราเชื่อถือได้",
          "",
          "หมายเหตุ: PnL ที่นี่ใช้ราคา ask/bid จริงตอนบันทึก log (ไม่ใช่ last trade) และถือถึงเฉลย ไม่มี TP",
          "ข้อมูลดิบ: data/cheap_live_log.csv · snapshot การแจกแจง: data/snapshots/*.jsonl"]
    return L


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-net", action="store_true", help="ไม่ยิงเน็ต (ใช้ market_labels.csv เท่านั้น)")
    ap.add_argument("--report-only", action="store_true", help="ไม่แก้ log")
    ap.add_argument("--stake", type=float, default=100.0)
    a = ap.parse_args()

    rows = list(csv.DictReader(open(LOG, encoding="utf-8"))) if os.path.exists(LOG) else []
    if not a.report_only:
        out = fill_log(no_net=a.no_net)
        if out and out[0]:
            rows = out[0]

    o = summarize(rows, a.stake)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ")
    L = render_report(o, a.stake, day)
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    open(REPORT, "w", encoding="utf-8").write("\n".join(L) + "\n")
    json.dump(o, open(STATS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("เขียน %s + %s" % (REPORT, STATS))


if __name__ == "__main__":
    main()
