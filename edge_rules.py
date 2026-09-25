#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
edge_rules.py — ตรวจกฎที่ "win > 70% · ราคา YES ≤ 0.30 · EV > 0" แบบเจาะลึกทีละกฎ
- bootstrap 95% CI ของ win rate และ ROI
- ยืนยันด้วยช่วงทดสอบ (split 2026-08-15)
- ความไวต่อ spread (จ่ายแพงกว่าราคาบันทึก 1/2/3 tick)
- leave-one-city-out แย่สุด (กันพึ่งเมืองเดียว)
- ความถี่ไม้/วัน และจำนวนวันที่ไม่มีไม้

ใช้: python3 edge_rules.py --split 2026-08-15
"""
import argparse, csv, os, random, statistics as st
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "cheap_bets.csv")

RULES = [
    # (ชื่อ, ชั่วโมง, pmin, lo, hi, mode)
    ("A กว้าง · 16:00",        16, 0.90, 0.02, 0.30, "argmax"),
    ("A2 กว้าง · 16:00 ≤.25",  16, 0.90, 0.02, 0.25, "argmax"),
    ("B คม · 16:00",           16, 0.97, 0.02, 0.25, "argmax"),
    ("C คมสุด · 16:00 ≤.15",   16, 0.97, 0.02, 0.15, "argmax"),
    ("D สาย · 17:00",          17, 0.95, 0.02, 0.30, "argmax"),
    ("E เร็ว · 15:00",         15, 0.97, 0.02, 0.30, "argmax"),
    ("F ทุก bin · 16:00 p≥.97", 16, 0.97, 0.02, 0.30, "all"),
]


def load(path=DATA):
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                out.append(dict(city=r["city"], date=r["date"], hour=int(r["hour"]),
                                bin=r["bin"], p=float(r["model_p"]), price=float(r["price"]), won=int(r["won"])))
            except (KeyError, ValueError):
                continue
    return out


def markets(rows):
    m = defaultdict(list)
    for r in rows:
        m[(r["city"], r["date"], r["hour"])].append(r)
    return m


def pick(mk, hour, pmin, lo, hi, mode):
    res = []
    for (c, d, h), bins in mk.items():
        if h != hour:
            continue
        cand = [b for b in bins if lo <= b["price"] <= hi and b["p"] >= pmin]
        if not cand:
            continue
        if mode == "argmax":
            cand = [max(cand, key=lambda b: (b["p"], -b["price"]))]
        res.extend(cand)
    return res


def stat(picks, tick=0.0):
    if not picks:
        return None
    n = len(picks)
    win = sum(p["won"] for p in picks) / n
    avg = min(0.999, st.mean(p["price"] for p in picks) + tick)
    ev = win - avg
    return dict(n=n, win=win * 100, avg=avg, ev=ev, roi=ev / avg * 100 if avg else 0.0)


def boot(picks, iters=2000, seed=7):
    rnd = random.Random(seed)
    n = len(picks)
    if n < 10:
        return None, None, None, None
    w, r = [], []
    for _ in range(iters):
        s = [picks[rnd.randrange(n)] for _ in range(n)]
        win = sum(p["won"] for p in s) / n
        avg = st.mean(p["price"] for p in s)
        w.append(win * 100); r.append((win - avg) / avg * 100)
    w.sort(); r.sort()
    return w[int(.025 * iters)], w[int(.975 * iters)], r[int(.025 * iters)], r[int(.975 * iters)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="2026-08-15")
    ap.add_argument("--out", default=os.path.join(HERE, "reports", "edge_rules.md"))
    a = ap.parse_args()
    rows = load()
    tr = [r for r in rows if r["date"] < a.split]
    te = [r for r in rows if r["date"] >= a.split]
    mtr, mte, mall = markets(tr), markets(te), markets(rows)
    allday = sorted({r["date"] for r in rows})

    print(f"rows={len(rows):,} · train={len(tr):,} · test={len(te):,} · split={a.split}\n")
    lines = []
    for name, hour, pmin, lo, hi, mode in RULES:
        tr_s = stat(pick(mtr, hour, pmin, lo, hi, mode))
        te_s = stat(pick(mte, hour, pmin, lo, hi, mode))
        all_p = pick(mall, hour, pmin, lo, hi, mode)
        al_s = stat(all_p)
        if not tr_s or not te_s or not al_s:
            continue
        wlo, whi, rlo, rhi = boot(all_p)
        # spread sensitivity
        sens = [stat(all_p, t) for t in (0.0, 0.01, 0.02, 0.03)]
        # leave-one-city-out
        cities = sorted({p["city"] for p in all_p})
        loo = None
        for c in cities:
            s = stat([p for p in all_p if p["city"] != c])
            if s and (loo is None or s["win"] < loo[1]["win"]):
                loo = (c, s)
        per_day = defaultdict(int)
        for p in all_p:
            per_day[p["date"]] += 1
        zero = sum(1 for d in allday if per_day.get(d, 0) == 0)
        gates = (al_s["win"] > 70 and al_s["avg"] <= 0.30 and al_s["ev"] > 0
                 and te_s["win"] > 70 and te_s["avg"] <= 0.30 and te_s["ev"] > 0)
        print(f"{'✅' if gates else '❌'} {name}  p≥{pmin} price {lo}–{hi} {mode}")
        print(f"   ทั้งหมด n={al_s['n']:4d} win={al_s['win']:5.1f}% (CI {wlo:.1f}–{whi:.1f}) avg={al_s['avg']:.3f} "
              f"EV={al_s['ev']:+.3f} ROI={al_s['roi']:+.0f}% (CI {rlo:+.0f}–{rhi:+.0f})")
        print(f"   train n={tr_s['n']:4d} win={tr_s['win']:5.1f}%  |  test n={te_s['n']:4d} win={te_s['win']:5.1f}% ROI={te_s['roi']:+.0f}%")
        print(f"   จ่ายแพงขึ้น +1/+2/+3 tick → win {sens[1]['win']:.1f}% avg "
              f"{sens[1]['avg']:.3f}/{sens[2]['avg']:.3f}/{sens[3]['avg']:.3f} ROI {sens[1]['roi']:+.0f}/{sens[2]['roi']:+.0f}/{sens[3]['roi']:+.0f}%")
        if loo:
            print(f"   leave-one-out แย่สุด (ถอด {loo[0]}): n={loo[1]['n']} win={loo[1]['win']:.1f}% ROI={loo[1]['roi']:+.0f}%")
        print(f"   เมือง {len(cities)} · {st.mean(per_day.values()):.1f} ไม้/วัน · มีไม้ {len(per_day)}/{len(allday)} วัน · วันที่ไม่มีไม้ {zero}\n")
        lines.append(dict(name=name, hour=hour, pmin=pmin, lo=lo, hi=hi, mode=mode, al=al_s, te=te_s, tr=tr_s,
                          ci=(wlo, whi, rlo, rhi), sens=sens, loo=loo, nc=len(cities),
                          fpd=st.mean(per_day.values()), nd=len(per_day), zero=zero, ok=gates))

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(f"# กฎที่ผ่านเกต: win > 70% · ราคา YES ≤ 0.30 · EV > 0\n\n"
                f"> `edge_rules.py --split {a.split}` · ข้อมูล 220,338 แถว (48 เมือง × 85 วัน) · เฉลย = bucket ที่ตลาด resolve จริง\n\n"
                f"> **หมายเหตุเชิงตรรกะ:** เมื่อ win > 70% และราคา ≤ 0.30 → EV/share ≥ +0.40 โดยอัตโนมัติ (EV = win − price)\n"
                f"> เงื่อนไขที่ผูกมัดจริงจึงมีแค่ **\"ชนะเกิน 70% ที่ราคาไม่เกิน 0.30\"** เท่านั้น\n\n"
                f"| กฎ | n | win% (CI95) | avg price | EV/share | ROI (CI95) | test win% | ไม้/วัน |\n|---|---|---|---|---|---|---|---|\n")
        for L in lines:
            f.write(f"| {L['name']} p≥{L['pmin']} · {L['lo']}–{L['hi']} | {L['al']['n']} | **{L['al']['win']:.1f}** "
                    f"({L['ci'][0]:.1f}–{L['ci'][1]:.1f}) | {L['al']['avg']:.3f} | {L['al']['ev']:+.3f} | "
                    f"**{L['al']['roi']:+.0f}%** ({L['ci'][2]:+.0f}–{L['ci'][3]:+.0f}) | {L['te']['win']:.1f} | {L['fpd']:.1f} |\n")
        f.write("\n## ความไวต่อ spread / ราคาที่จ่ายจริง\n\n| กฎ | +0 | +1 tick | +2 tick | +3 tick |\n|---|---|---|---|---|\n")
        for L in lines:
            f.write(f"| {L['name']} | ROI {L['sens'][0]['roi']:+.0f}% | {L['sens'][1]['roi']:+.0f}% | {L['sens'][2]['roi']:+.0f}% | {L['sens'][3]['roi']:+.0f}% |\n")
        f.write("\n## ถอดเมืองที่ดีที่สุดออก (leave-one-city-out แย่สุด) + ความครอบคลุม\n\n| กฎ | เมือง | ไม้/วัน | วันที่มีไม้ | ถอดเมืองแย่สุด | win หลังถอด |\n|---|---|---|---|---|---|\n")
        for L in lines:
            f.write(f"| {L['name']} | {L['nc']} | {L['fpd']:.1f} | {L['nd']}/85 | {L['loo'][0] if L['loo'] else '-'} | "
                    f"{L['loo'][1]['win']:.1f}% (n={L['loo'][1]['n']}) |\n")
    print(f"เขียน {a.out}")


if __name__ == "__main__":
    main()
