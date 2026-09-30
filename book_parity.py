#!/usr/bin/env python3
"""book_parity.py — วัดความต่างระหว่างราคา Gamma (bestAsk/bestBid) กับ CLOB order book จริง

ทำไมต้องมี: ก่อนหน้านี้เราสรุปว่า "snapshot เชื่อถือได้" จากการวัดครั้งเดียว — ยังไม่พอ
ตัวนี้บันทึก **timestamp ของทั้งสอง response** แล้ววัด: delta_ms · abs diff · ทิศทาง
→ ถ้า diff ใหญ่แต่ delta_ms ใหญ่ด้วย = "ราคาเคลื่อนตามเวลา" ไม่ใช่ parser bug
→ ถ้า delta_ms เล็กแต่ diff ใหญ่ = ต้องสงสัยข้อมูล/โค้ด

เขียนผล: data/book_parity.jsonl (ต่อท้าย · dedupe ต่อ token ในรอบเดียว) + สรุปบนหน้าจอ
รัน: python3 book_parity.py --cities sao-paulo,nyc --top 4
"""
import argparse
import json
import os
import statistics as st
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wxedge as W  # noqa: E402
import depth_probe as DP  # noqa: E402

OUT = os.path.join(HERE, "data", "book_parity.jsonl")
UA = {"User-Agent": "wxedge/1.0 (book-parity)"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cities", default="", help="รายการเมืองคั่นด้วย , (ว่าง = อ่านจาก snapshot ล่าสุด)")
    ap.add_argument("--top", type=int, default=4, help="จำนวน bin ต่อเมือง (เรียงตาม volume)")
    ap.add_argument("--log", action="store_true")
    a = ap.parse_args()

    if a.cities:
        cities = [c.strip() for c in a.cities.split(",") if c.strip()]
    else:
        from trades_probe import latest_rows
        cities = [r["city"] for r in latest_rows()][:3]

    today = datetime.now(timezone.utc).date().isoformat()
    rows = []
    for city in cities:
        t_gamma0 = time.time()
        ev = W.polymarket_event(city, today, no_cache=True)
        t_gamma1 = time.time()
        if not ev:
            print("%-14s ไม่พบตลาดวันนี้" % city)
            continue
        ms = [m for m in ev.get("markets", []) if m.get("bestAsk") not in (None, "")]
        ms.sort(key=lambda m: -float(m.get("volumeNum") or m.get("volume") or 0))
        for m in ms[:a.top]:
            lab = (m.get("groupItemTitle") or "").strip()
            try:
                tok = json.loads(m.get("clobTokenIds") or "[]")[0]
            except (json.JSONDecodeError, IndexError):
                continue
            g_ask = float(m["bestAsk"])
            g_bid = float(m["bestBid"]) if m.get("bestBid") not in (None, "") else None
            t_clob0 = time.time()
            asks, bids = DP.book_full(tok)
            t_clob1 = time.time()
            c_ask = asks[0][0] if asks else None
            c_bid = bids[0][0] if bids else None
            rec = dict(
                ts=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                city=city, bin=lab, token=tok,
                t_gamma=round(t_gamma1, 3), t_clob=round(t_clob1, 3),
                delta_ms=round(1000 * (t_clob0 - t_gamma1), 1),
                gamma_ask=g_ask, clob_ask=c_ask,
                gamma_bid=g_bid, clob_bid=c_bid,
                diff_ask=(round(c_ask - g_ask, 4) if c_ask is not None else None),
                diff_bid=(round(c_bid - g_bid, 4) if (c_bid is not None and g_bid is not None) else None),
                n_ask=len(asks) if asks else 0, n_bid=len(bids) if bids else 0)
            rows.append(rec)
            time.sleep(0.2)
    if not rows:
        print("ไม่มีข้อมูลให้วัด")
        return
    if a.log:
        with open(OUT, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    d_ask = [abs(r["diff_ask"]) for r in rows if r["diff_ask"] is not None]
    d_bid = [abs(r["diff_bid"]) for r in rows if r["diff_bid"] is not None]
    print("%-14s %-12s %8s %8s %8s %8s %7s" % ("city", "bin", "gamma", "clob", "diff", "delta_ms", "n_ask"))
    for r in rows:
        print("%-14s %-12s %8.3f %8s %8s %8.0f %7d" % (
            r["city"], r["bin"][:12], r["gamma_ask"],
            ("%.3f" % r["clob_ask"]) if r["clob_ask"] is not None else "—",
            ("%+.3f" % r["diff_ask"]) if r["diff_ask"] is not None else "—",
            r["delta_ms"], r["n_ask"]))
    print("\nสรุป (%d คู่): |diff ask| มัธยฐาน %.4f · สูงสุด %.4f · delta_ms มัธยฐาน %.0f" % (
        len(rows), st.median(d_ask) if d_ask else -1, max(d_ask) if d_ask else -1,
        st.median([r["delta_ms"] for r in rows])))
    if d_bid:
        print("         |diff bid| มัธยฐาน %.4f · สูงสุด %.4f" % (st.median(d_bid), max(d_bid)))
    big = [r for r in rows if r["diff_ask"] is not None and abs(r["diff_ask"]) > 0.02]
    print("คู่ที่ diff > 0.02: %d %s" % (len(big), [(r["city"], r["bin"], r["delta_ms"]) for r in big[:4]]))
    if a.log:
        print("ต่อท้าย %s แล้ว" % OUT)


if __name__ == "__main__":
    main()
