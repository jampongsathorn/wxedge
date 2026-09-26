#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
refine_competitor.py — เจาะลึก 3 คำถามที่เหลือจากไอเดียคู่แข่ง
 1) Champion: ควร "จำกัดเพดานราคาต่ำลง" (≤0.10) ไหม — แยกตามช่วงราคา
 2) Laddering แท้ ๆ (หลายใบจริง): แบบต่าง ๆ ให้ ROI ดีกว่าซื้อใบเดียวหรือไม่
 3) ฝั่งขาย (buy NO): กำไรมาจาก bin ที่ "เป็นไปไม่ได้แล้ว" (ต่ำกว่า max-so-far) จริงไหม + ราคาเสถียรหรือเป็น quote เก่า
รัน: python3 refine_competitor.py --split 2026-08-15
"""
import argparse, csv, math, os, re, statistics as st
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "reports", "competitor_refine.md")


def load_bets():
    out = []
    for r in csv.DictReader(open(os.path.join(HERE, "data", "cheap_bets.csv"), newline="", encoding="utf-8")):
        try:
            out.append(dict(city=r["city"], date=r["date"], hour=int(r["hour"]), bin=r["bin"], unit=r["unit"],
                            p=float(r["model_p"]), price=float(r["price"]), won=int(r["won"])))
        except (KeyError, ValueError):
            continue
    return out


def parse_bin(label, unit):
    """'23°C' / '23°C or below' / '23°C or above' / '92-93°F' → (lo, hi) หน่วยตลาด
    ระวัง: '92-93°F' ต้องไม่ถูกอ่านเป็น 92 กับ −93"""
    s = str(label).replace("°C", " ").replace("°F", " ").replace("–", "-").strip()
    neg = s.startswith("-")
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", s)]
    if neg and nums:
        nums[0] = -nums[0]
    if not nums:
        return (-math.inf, math.inf)
    if "or below" in s or "or lower" in s:          # ตลาดจริงใช้ "or below" / "or higher"
        return (-math.inf, nums[0])
    if "or above" in s or "or higher" in s:
        return (nums[0], math.inf)
    if len(nums) == 1:
        return (nums[0], nums[0])
    return (nums[0], nums[1])


def mk(rows):
    m = defaultdict(list)
    for r in rows:
        m[(r["city"], r["date"], r["hour"])].append(r)
    return m


def roi(cost, pay, n):
    return (pay - cost) / cost * 100 if cost else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="2026-08-15")
    a = ap.parse_args()
    rows = load_bets()
    M = mk(rows)
    L = []
    print(f"rows={len(rows):,}\n")

    # ── 1) Champion แยกตามช่วงราคา (16:00) ──
    print("[1] Champion (p≥0.90 · argmax) @16:00 — แยกตามราคาของใบที่ซื้อ")
    bands = [(0.02, 0.05), (0.05, 0.10), (0.10, 0.15), (0.15, 0.20), (0.20, 0.25)]
    tbl = []
    for lo, hi in bands:
        n = cost = pay = win = 0
        for (c, d, h), bins in M.items():
            if h != 16:
                continue
            cand = [b for b in bins if b["p"] >= 0.90 and lo <= b["price"] < hi]
            if not cand:
                continue
            b = max(cand, key=lambda x: (x["p"], -x["price"]))
            n += 1; cost += b["price"]; pay += b["won"]; win += b["won"]
        if n:
            tbl.append((f"{lo:.2f}–{hi:.2f}", n, win / n * 100, cost / n, roi(cost, pay, n)))
            print(f"    ราคา {lo:.2f}–{hi:.2f}: n={n:4d} ชนะ={win/n*100:5.1f}% avg={cost/n:.3f} ROI={roi(cost,pay,n):+8.1f}%")
    tot_n = sum(t[1] for t in tbl)
    for cap in (0.05, 0.10, 0.15, 0.25):
        n = cost = pay = win = 0
        for (c, d, h), bins in M.items():
            if h != 16:
                continue
            cand = [b for b in bins if b["p"] >= 0.90 and 0.02 <= b["price"] <= cap]
            if not cand:
                continue
            b = max(cand, key=lambda x: (x["p"], -x["price"]))
            n += 1; cost += b["price"]; pay += b["won"]; win += b["won"]
        print(f"    ★ เพดาน ≤{cap:.2f}: n={n:4d} ชนะ={win/n*100:5.1f}% avg={cost/n:.3f} ROI={roi(cost,pay,n):+8.1f}%")
        tbl.append((f"เพดาน ≤{cap:.2f}", n, win / n * 100, cost / n, roi(cost, pay, n)))

    # ── 2) Laddering แท้ (หลายใบจริง) @16:00 ──
    print("\n[2] Laddering แท้ (ซื้อหลายใบจริง) @16:00")
    def run(sel, name):
        n = cost = pay = win = legs = 0
        for (c, d, h), bins in M.items():
            if h != 16:
                continue
            cand = sel(bins)
            if not cand:
                continue
            n += 1; legs += len(cand)
            cost += sum(b["price"] for b in cand)
            w = 1.0 if any(b["won"] for b in cand) else 0.0
            pay += w; win += w
        if n:
            print(f"    {name:44s} n={n:4d} ใบ/ตะกร้า={legs/n:.2f} ต้นทุน={cost/n:.3f} ถูก≥1={win/n*100:5.1f}% ROI={roi(cost,pay,n):+8.1f}%")
            return (name, n, legs / n, cost / n, win / n * 100, roi(cost, pay, n))
    variants = [
        (lambda b: sorted([x for x in b if x["p"] >= 0.35 and 0.02 <= x["price"] <= 0.10], key=lambda x: -x["p"])[:3],
         "3 ใบ p≥0.35 · ราคา ≤0.10"),
        (lambda b: sorted([x for x in b if x["p"] >= 0.10 and 0.02 <= x["price"] <= 0.05], key=lambda x: -x["p"])[:3],
         "3 ใบ p≥0.10 · ราคา 0.02–0.05"),
        (lambda b: sorted([x for x in b if 0.05 <= x["p"] <= 0.60 and 0.02 <= x["price"] <= 0.10], key=lambda x: -x["p"])[:3],
         "3 ใบ central band (p .05–.60) ราคา ≤0.10"),
        (lambda b: (lambda ch: [ch] + sorted([x for x in b if x is not ch and 0.02 <= x["price"] <= 0.10],
                                             key=lambda x: -x["p"])[:2] if ch else [])(
            max([x for x in b if x["p"] >= 0.90 and 0.02 <= x["price"] <= 0.25], key=lambda x: x["p"], default=None)),
         "ใบแชมป์ + 2 ใบถูกสุดที่เหลือ"),
        (lambda b: [x for x in b if 0.02 <= x["price"] <= 0.05], "ทุกใบราคา 0.02–0.05 (กลุ่มควบคุม)"),
    ]
    ladder_rows = [run(sel, name) for sel, name in variants]

    # ── 3) ฝั่งขาย (buy NO): อธิบายได้ไหมว่าเป็น bin ที่ "เป็นไปไม่ได้แล้ว" ──
    print("\n[3] ฝั่งขาย (buy NO @16:00, ราคา 0.50–0.97 & price−p ≥ 0.30) — แยกตามเหตุผล")
    # max-so-far จาก hourly_obs
    msf = defaultdict(lambda: defaultdict(dict))
    p = os.path.join(HERE, "data", "hourly_obs.csv")
    for r in csv.DictReader(open(p, encoding="utf-8")):
        try:
            msf[r["city"]][r["date"]][int(r["hour"])] = float(r["tmpc"])
        except ValueError:
            continue
    leg_rows = []
    for (c, d, h), bins in M.items():
        if h != 16:
            continue
        day = msf.get(c, {}).get(d) or {}
        past = [v for hh, v in day.items() if hh <= 16]
        mx = max(past) if past else None
        for b in bins:
            if 0.50 <= b["price"] <= 0.97 and (b["price"] - b["p"]) >= 0.30:
                lo, hi = parse_bin(b["bin"], b["unit"])
                mxu = None
                if mx is not None:
                    mxu = mx * 9 / 5 + 32 if b["unit"] == "F" else mx
                imp = mxu is not None and hi != math.inf and hi < mxu - 0.5
                tight = mxu is not None and lo != -math.inf and lo > mxu + 0.5
                if imp and tight:      # bin ครอบคลุมทั้งสองฝั่งของ max-so-far
                    imp = tight = False
                leg_rows.append(dict(**b, msf=mxu, impossible=imp, tight=tight))
    for name, sel in (("ทั้งหมด", lambda x: True),
                      ("bin ต่ำกว่า max-so-far แล้ว (เป็นไปไม่ได้)", lambda x: x["impossible"]),
                      ("bin สูงกว่า max-so-far อยู่ >0.5 (ยังเป็นไปได้)", lambda x: x["tight"]),
                      ("อื่น ๆ (bin คร่อม max-so-far)", lambda x: not x["impossible"] and not x["tight"])):
        legs = [x for x in leg_rows if sel(x)]
        if len(legs) < 20:
            print(f"    {name:44s} n={len(legs)} (น้อยเกินไป)")
            continue
        cost = sum(1 - x["price"] for x in legs)
        pay = sum(1 - x["won"] for x in legs)
        print(f"    {name:44s} n={len(legs):4d} จ่ายเฉลี่ย={cost/len(legs):.3f} ชนะ(NO)={pay/len(legs)*100:5.1f}% ROI={roi(cost,pay,len(legs)):+8.1f}%")
    # persistence: bin นี้ราคา ≥0.5 มากี่ชั่วโมงก่อนหน้า
    by_key = defaultdict(dict)
    for r in rows:
        by_key[(r["city"], r["date"], r["bin"])][r["hour"]] = r["price"]
    for tag, cond in (("ราคา ≥0.50 ต่อเนื่อง ≥2 ชั่วโมงก่อนหน้า", lambda prev: sum(1 for x in prev if x >= 0.50) >= 2),
                      ("ราคา ≥0.50 แค่ชั่วโมงนี้ (อาจเป็น quote เก่า)", lambda prev: sum(1 for x in prev if x >= 0.50) == 0)):
        legs = []
        for x in leg_rows:
            prev = [by_key[(x["city"], x["date"], x["bin"])].get(hh, 0.0) for hh in (12, 14, 15)]
            if cond(prev):
                legs.append(x)
        if len(legs) >= 20:
            cost = sum(1 - x["price"] for x in legs)
            pay = sum(1 - x["won"] for x in legs)
            print(f"    {tag:44s} n={len(legs):4d} จ่ายเฉลี่ย={cost/len(legs):.3f} ชนะ(NO)={pay/len(legs)*100:5.1f}% ROI={roi(cost,pay,len(legs)):+8.1f}%")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write(f"# เจาะลึกไอเดียคู่แข่ง: เพดานราคา · Laddering · ฝั่งขาย\n\n> `refine_competitor.py --split {a.split}` · 48 เมือง × 85 วัน · เฉลยจริง\n\n## 1) Champion แยกตามช่วงราคาของใบที่ซื้อ (@16:00)\n\n| ช่วงราคา/เพดาน | n | ชนะ% | ราคาเฉลี่ย | ROI |\n|---|---|---|---|---|\n")
        for name, n, win, avg, r in tbl:
            f.write(f"| {name} | {n} | {win:.1f}% | {avg:.3f} | **{r:+.1f}%** |\n")
        f.write("\n## 2) Laddering แท้ (หลายใบจริง) @16:00\n\n| กลยุทธ์ | n | ใบ/ตะกร้า | ต้นทุน/ตะกร้า | ถูก ≥1 ใบ | ROI |\n|---|---|---|---|---|---|\n")
        for row in ladder_rows:
            if row:
                f.write(f"| {row[0]} | {row[1]} | {row[2]:.2f} | {row[3]:.3f} | {row[4]:.1f}% | **{row[5]:+.1f}%** |\n")
        f.write("\n## 3) ฝั่งขาย (buy NO) — แยกตามเหตุผล\n\n| กลุ่ม | n | ชนะ(NO)% | จ่ายเฉลี่ย | ROI |\n|---|---|---|---|---|\n")
        for name, sel in (("ทั้งหมด", lambda x: True), ("bin ต่ำกว่า max-so-far (เป็นไปไม่ได้)", lambda x: x["impossible"]),
                          ("bin สูงกว่า max-so-far (ยังเป็นไปได้)", lambda x: x["tight"]), ("อื่น ๆ", lambda x: not x["impossible"] and not x["tight"])):
            legs = [x for x in leg_rows if sel(x)]
            if len(legs) < 20:
                continue
            cost = sum(1 - x["price"] for x in legs)
            pay = sum(1 - x["won"] for x in legs)
            f.write(f"| {name} | {len(legs)} | {pay/len(legs)*100:.1f}% | {cost/len(legs):.3f} | **{roi(cost,pay,len(legs)):+.1f}%** |\n")
    print(f"\nเขียน {OUT}")


if __name__ == "__main__":
    main()
