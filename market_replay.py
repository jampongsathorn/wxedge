#!/usr/bin/env python3
"""market_replay.py — ย้อนดูตลาดที่ปิดแล้ว: "ถ้าเข้าไม้ตอนนั้น เราจ่ายได้จริงเท่าไร"

ใช้กับตลาดไหนก็ได้ (เมือง + วัน) · ตอบ 3 คำถามพร้อมกัน:
  1) ราคา last-trade ที่ backtest เดิมจะใช้ (= ราคาที่อาจเป็นภาพลวง)
  2) ราคา "ซื้อได้จริง" = BUY fills จริงที่สะสม notional ครบ stake ในหน้าต่างเข้าไม้
  3) ผลจริง (bin ผู้ชนะ) → hit/ROI แบบ executable เทียบกับแบบ last-trade

รัน: python3 market_replay.py --city nyc --date 2026-09-14 [--stake 12] [--window 15:00-17:00]
"""
import argparse
import csv
import json
import os
import statistics as st
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wxedge as W  # noqa: E402
from exec_backtest import exec_price, yes_fills  # noqa: E402  (ตรรกะเดียว ไม่คัดลอก)

API = "https://data-api.polymarket.com/trades"
UA = {"User-Agent": "wxedge/1.0 (market-replay)"}
REPORT = os.path.join(HERE, "reports", "market_replay_%s_%s.md")


def _get(u, tries=3):
    for i in range(tries):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60).read().decode())
        except Exception:
            if i == tries - 1:
                return None
            time.sleep(2)
    return None


def hm(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", required=True)
    ap.add_argument("--date", required=True)
    ap.add_argument("--stake", type=float, default=12.0)
    ap.add_argument("--window", default="15:00-17:00", help="หน้าต่าง local ที่จะดู")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    cfgs = json.load(open(W.STATIONS, encoding="utf-8"))
    tz = ZoneInfo(cfgs[a.city]["tz"])
    ev = W.polymarket_event(a.city, a.date, no_cache=True)
    if not ev:
        print("ไม่พบตลาด %s %s" % (a.city, a.date))
        return
    ms = ev.get("markets", [])
    winner = None
    for m in ms:
        try:
            pr = json.loads(m.get("outcomePrices") or "[]")
        except json.JSONDecodeError:
            pr = []
        if pr and float(pr[0]) > 0.5:
            winner = (m.get("groupItemTitle") or "").strip()
    w0 = datetime.strptime(a.date + " " + a.window.split("-")[0], "%Y-%m-%d %H:%M").replace(tzinfo=tz)
    w1 = datetime.strptime(a.date + " " + a.window.split("-")[1], "%Y-%m-%d %H:%M").replace(tzinfo=tz)

    print("══ %s %s ══ closed=%s · %d bins · ผู้ชนะ = %s" % (a.city, a.date, ev.get("closed"), len(ms), winner))
    rows = []
    for m in ms:
        lab = (m.get("groupItemTitle") or "").strip()
        toks = json.loads(m.get("clobTokenIds") or "[]")
        if not toks:
            continue
        cond = m.get("conditionId")
        tr = []
        off = 0
        while True:                                  # ดีลทั้งชีวิตของตลาดนี้
            p = _get("%s?market=%s&limit=500&offset=%d" % (API, cond, off))
            if not p:
                break
            tr += p
            off += 500
            if len(p) < 500:
                break
            time.sleep(0.15)
        fills = yes_fills(tr, toks[0])
        ex = exec_price(fills, int(w0.timestamp()), int((w1 - w0).total_seconds() // 60), a.stake)
        # ราคา last-trade ตอนเริ่มหน้าต่าง (สิ่งที่ backtest เดิมใช้)
        last = None
        try:
            ph = _get("https://clob.polymarket.com/prices-history?market=%s&startTs=%d&endTs=%d&fidelity=1"
                      % (toks[0], int(w0.timestamp()) - 3600, int(w0.timestamp())))
            h = (ph or {}).get("history") or []
            if h:
                last = h[-1]["p"]
        except Exception:
            pass
        rows.append(dict(bin=lab, last=last, ex_px=(ex or {}).get("px"), notional=(ex or {}).get("notional"),
                         n_buy=(ex or {}).get("n_buy", 0), secs=(ex or {}).get("secs"),
                         n_fills=len([f for f in fills if int(w0.timestamp()) <= f[0] <= int(w1.timestamp())]),
                         won=(lab == winner)))
        time.sleep(0.15)

    print("\n| bin | last-trade ก่อน %s | ซื้อได้จริง (≥$%.0f) | ดีล | ผล |" % (a.window.split('-')[0], a.stake))
    for r in sorted(rows, key=lambda x: -(x["ex_px"] or -1)):
        print("| %-14s | %-8s | %-10s | %d | %s |" % (
            r["bin"], ("%.3f" % r["last"]) if r["last"] else "—",
            ("%.3f ($%.0f)" % (r["ex_px"], r["notional"])) if r["ex_px"] else "ไม่มีดีลพอ",
            r["n_fills"], "ชนะ ✓" if r["won"] else ""))

    # สรุป: ไม้ที่ "last-trade บอกว่าถูก" เทียบกับราคาจริง
    cheap = [r for r in rows if r["last"] and 0.02 <= r["last"] <= 0.25]
    print("\n── bin ที่ last-trade อยู่ในโซน 'ไม้ถูก' (0.02–0.25) ──")
    if not cheap:
        print("   ไม่มี")
    for r in cheap:
        gap = ("+%.3f" % (r["ex_px"] - r["last"])) if (r["ex_px"] and r["last"]) else "ซื้อไม่ได้"
        print("   %-14s last %.3f → จริง %s (%s) · ผล %s" % (
            r["bin"], r["last"], ("%.3f" % r["ex_px"]) if r["ex_px"] else "—", gap, "ชนะ" if r["won"] else "แพ้"))

    if a.save:
        L = ["# market replay — %s %s" % (a.city, a.date), "",
             "closed=%s · %d bins · ผู้ชนะ **%s** · stake $%.0f · หน้าต่าง %s (local)" % (
                 ev.get("closed"), len(ms), winner, a.stake, a.window), "",
             "| bin | last-trade ก่อนหน้าต่าง | ราคาซื้อได้จริง | notional | ดีล | ผล |", "|---|---|---|---|---|---|"]
        for r in sorted(rows, key=lambda x: -(x["ex_px"] or -1)):
            L.append("| %s | %s | %s | %s | %d | %s |" % (
                r["bin"], ("%.3f" % r["last"]) if r["last"] else "—",
                ("%.3f" % r["ex_px"]) if r["ex_px"] else "—",
                ("$%.0f" % r["notional"]) if r["notional"] else "—",
                r["n_fills"], "ชนะ ✓" if r["won"] else ""))
        L += ["", "ที่มา: ดีลจริง data-api · ราคา last-trade จาก clob prices-history (fidelity=1)", ""]
        out = REPORT % (a.city, a.date)
        open(out, "w", encoding="utf-8").write("\n".join(L))
        print("\nเขียน %s" % out)


if __name__ == "__main__":
    main()
