#!/usr/bin/env python3
"""
edge_audit.py — วัด "edge จริง" เทียบราคาตลาด Polymarket ย้อนหลัง

ทำอะไร:
  1) ดึงตลาดที่ resolve แล้ว (winner bucket) = เฉลยจริง
  2) ดึง price history ของทุก bin จาก CLOB (clob.polymarket.com/prices-history)
     → เอาราคา ณ เวลา T ก่อน resolve (ค่าเริ่มต้น: D-1 12:00 น. ตาม timezone สถานี)
  3) เทียบ: โมเดล (จาก dataset.csv) vs ราคาตลาด ณ เวลานั้น vs ผลจริง
  4) คำนวณ: hit rate ของโมเดล, hit rate ของ "ตัวเต็งตลาด", และ PnL ของกลยุทธ์
     "ซื้อ bin ที่ p_model − price > threshold"

รัน: python3 edge_audit.py --cities nyc,london,miami,austin,karachi --days 5 --hours-before 24
"""
import argparse, csv, json, math, os, sys, time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wxedge as W

HERE = os.path.dirname(os.path.abspath(__file__))


def event_for(city, date_str):
    d = datetime.strptime(date_str, "%Y-%m-%d")
    slug = "highest-temperature-in-%s-on-%s-%d-%d" % (city, d.strftime("%B").lower(), d.day, d.year)
    try:
        j = W.get_json("https://gamma-api.polymarket.com/events?slug=" + slug, ttl=3600, tag="rme")
        return j[0] if isinstance(j, list) and j else None
    except Exception:
        return None


def winner_bin(ev):
    for m in ev.get("markets", []):
        try:
            pr = json.loads(m.get("outcomePrices") or "[]")
        except json.JSONDecodeError:
            continue
        if pr and float(pr[0]) > 0.5:
            return m.get("groupItemTitle")
    return None


def price_at(token_id, ts_target, ttl=86400):
    """ราคา yes ที่ใกล้เวลาที่กำหนด (unix ts) จาก CLOB price history"""
    try:
        h = W.get_json("https://clob.polymarket.com/prices-history?market=%s&interval=max&fidelity=60"
                       % token_id, ttl=ttl, tag="clob")
    except Exception:
        return None, None
    pts = h.get("history") or []
    if not pts:
        return None, None
    best = min(pts, key=lambda p: abs(p["t"] - ts_target))
    if abs(best["t"] - ts_target) > 6 * 3600:      # ไกลเกิน 6 ชม. = ไม่มีข้อมูลช่วงนั้น
        return None, None
    return best["p"], best["t"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(HERE, "data", "dataset.csv"))
    ap.add_argument("--cities", default="nyc,london,miami,austin,karachi,hong-kong,paris,beijing")
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--hours-before", type=float, default=24.0,
                    help="วัดราคาย้อนหลังกี่ชั่วโมงก่อนเที่ยงคืนของวันนั้น (24 = เที่ยงวันก่อนหน้า)")
    ap.add_argument("--min-edge", type=float, default=0.05)
    ap.add_argument("--out", default=os.path.join(HERE, "reports", "edge_audit.md"))
    a = ap.parse_args()

    rows = list(csv.DictReader(open(a.data, encoding="utf-8")))
    by = {}
    for r in rows:
        by[(r["city"], r["date"])] = r

    today = datetime.now(timezone.utc).date()
    cities = a.cities.split(",")
    results, summary = [], dict(n=0, model_hit=0, mkt_hit=0, bet_n=0, bet_hit=0, bet_pnl=0.0, stake=0.0)
    for city in cities:
        for k in range(1, a.days + 1):
            d = today - timedelta(days=k)
            ds = d.isoformat()
            row = by.get((city, ds))
            if not row:
                continue
            ev = event_for(city, ds)
            if not ev:
                continue
            win = winner_bin(ev)
            tz = ZoneInfo(row["tz"])
            # เวลาวัดราคา: เที่ยงคืนของวันนั้น ลบ hours_before
            target_dt = datetime(d.year, d.month, d.day, 0, 0, tzinfo=tz) - timedelta(hours=a.hours_before)
            ts_target = int(target_dt.timestamp())
            prices = {}
            for m in ev.get("markets", []):
                lab = m.get("groupItemTitle")
                toks = json.loads(m.get("clobTokenIds") or "[]")
                if not toks:
                    continue
                p, t = price_at(toks[0], ts_target)
                if p is not None:
                    prices[lab] = p
            if not prices:
                continue
            # โมเดล: ใช้ mu/sigma จาก dataset
            unit = row["unit"]
            mu = float(row["mu_c"]); sigma = float(row["sigma_c"]) * (9 / 5 if unit == "F" else 1)
            mkt_mu = mu * 9 / 5 + 32 if unit == "F" else mu
            from scipy.stats import norm
            bins = W.event_bins(ev)
            pm = {}
            for b in bins:
                lo, hi = b["lo"], b["hi"]
                if lo == float("-inf"):
                    pm[b["label"]] = norm.cdf((hi + 0.5 - mkt_mu) / sigma)
                elif hi == float("inf"):
                    pm[b["label"]] = 1 - norm.cdf((lo - 0.5 - mkt_mu) / sigma)
                else:
                    pm[b["label"]] = norm.cdf((hi + 0.5 - mkt_mu) / sigma) - norm.cdf((lo - 0.5 - mkt_mu) / sigma)
            top_model = max(pm, key=pm.get)
            top_mkt = max(prices, key=prices.get) if prices else None
            summary["n"] += 1
            summary["model_hit"] += (top_model == win)
            summary["mkt_hit"] += (top_mkt == win)
            # กลยุทธ์ value bet
            bets = [(l, pm[l] - prices[l]) for l in prices if pm.get(l, 0) - prices[l] > a.min_edge and 0.01 < prices[l] < 0.9]
            for lab, edge in bets:
                stake = 1.0
                pnl = (1.0 / prices[lab] - 1.0) if lab == win else -1.0
                summary["bet_n"] += 1
                summary["bet_hit"] += (lab == win)
                summary["bet_pnl"] += pnl
                summary["stake"] += stake
                results.append(dict(city=city, date=ds, bin=lab, p_model=pm[lab], price=prices[lab],
                                    edge=edge, win=(lab == win), pnl=pnl))
            results.append(dict(city=city, date=ds, bin="(top model) " + top_model,
                                p_model=pm[top_model], price=prices.get(top_model),
                                edge=None, win=(top_model == win), pnl=None))
            print("  %-12s %s | โมเดล: %-14s (%2.0f%%) | ตลาด: %-14s (%2.0f%%) | จริง: %-14s %s"
                  % (city, ds, top_model, pm[top_model] * 100, top_mkt,
                     (prices.get(top_mkt) or 0) * 100, win,
                     "✅" if top_model == win else "❌"))

    n = max(summary["n"], 1)
    print("\n=== สรุป (%d ตลาด-วัน) ===" % summary["n"])
    print("  โมเดลทาย bin ถูก:      %.0f%%" % (100 * summary["model_hit"] / n))
    print("  ตัวเต็งของตลาดถูก:     %.0f%%" % (100 * summary["mkt_hit"] / n))
    if summary["bet_n"]:
        print("  value bet (edge>%.0f%%): %d ไม้ ถูก %.0f%% | PnL รวม %+.2f หน่วย/ไม้ละ 1 | ROI %+.1f%%"
              % (a.min_edge * 100, summary["bet_n"], 100 * summary["bet_hit"] / summary["bet_n"],
                 summary["bet_pnl"], 100 * summary["bet_pnl"] / summary["stake"]))
    else:
        print("  value bet: ไม่มีไม้ที่ผ่านเกณฑ์ (ไม่มี edge)")

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write("# edge audit — %s\n\n" % datetime.now().strftime("%Y-%m-%d %H:%M"))
        f.write("เทียบโมเดล vs ราคาตลาดจริง (CLOB price history) ณ เวลา D-%.0f ชม. ก่อนเที่ยงคืนของวันนั้น\n\n"
                % a.hours_before)
        f.write("| city | date | bin | p_model | ราคาตลาด | edge | ถูก? | PnL |\n|---|---|---|---|---|---|---|---|\n")
        for r in results:
            f.write("| %s | %s | %s | %.1f%% | %s | %s | %s | %s |\n" % (
                r["city"], r["date"], r["bin"], r["p_model"] * 100,
                ("%.1f%%" % (r["price"] * 100)) if r["price"] is not None else "-",
                ("%+.1f%%" % (r["edge"] * 100)) if r["edge"] is not None else "-",
                "✅" if r["win"] else "❌",
                ("%+.2f" % r["pnl"]) if r["pnl"] is not None else "-"))
        f.write("\n**สรุป:** โมเดลถูก %.0f%% | ตลาดถูก %.0f%% | value bet %d ไม้ ถูก %.0f%% ROI %+.1f%%\n"
                % (100 * summary["model_hit"] / n, 100 * summary["mkt_hit"] / n, summary["bet_n"],
                   100 * summary["bet_hit"] / max(summary["bet_n"], 1),
                   100 * summary["bet_pnl"] / max(summary["stake"], 1e-9)))
    print("  → เขียน %s" % a.out)


if __name__ == "__main__":
    main()
