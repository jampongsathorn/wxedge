#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
edge_search.py — ค้นกฎ "win rate > 70% + ราคา YES <= 0.30 + EV > 0" จากข้อมูลจริง
ข้อมูล: data/cheap_bets.csv (ราคาทุก bin รายชั่วโมง, 48 เมือง x 85 วัน) + คอลัมน์ won = เฉลยจริง
ทดสอบ: ค้นบน train (< --split) แล้วยืนยันบน test เพื่อกัน overfit จากการกวาดกริด
ใช้:  python3 edge_search.py --split 2026-08-15
"""
import argparse, csv, os, statistics as st
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "cheap_bets.csv")

HOURS = [12, 14, 15, 16, 17]
PMINS = [0.30, 0.35, 0.50, 0.70, 0.80, 0.90, 0.95, 0.97]
BANDS = [(0.02, 0.30), (0.05, 0.30), (0.10, 0.30), (0.15, 0.30), (0.20, 0.30),
         (0.02, 0.25), (0.02, 0.20), (0.02, 0.15), (0.02, 0.10)]
# หมายเหตุ: EV/share = win% - avg_price ; ROI = EV / avg_price
# เนื่องจาก win > 70% และ price <= 0.30 -> EV/share >= 0.40 เสมอ (เงื่อนไข EV>0 ไม่ผูกมัดจริง)


def load(path=DATA):
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                rows.append(dict(city=r["city"], date=r["date"], hour=int(r["hour"]),
                                 unit=r["unit"], bin=r["bin"], p=float(r["model_p"]),
                                 price=float(r["price"]), won=int(r["won"])))
            except (KeyError, ValueError):
                continue
    return rows


def by_market(rows):
    """(city,date,hour) -> [bins]"""
    m = defaultdict(list)
    for r in rows:
        m[(r["city"], r["date"], r["hour"])].append(r)
    return m


def select(markets, hour, pmin, lo, hi, mode="argmax"):
    """คืนลิสต์ไม้ที่ผ่านเกต; mode=argmax -> 1 ไม้/ตลาด (bin ที่ p สูงสุด), mode=all -> ทุก bin ที่ผ่าน"""
    picks = []
    for (city, date, h), bins in markets.items():
        if h != hour:
            continue
        cand = [b for b in bins if lo <= b["price"] <= hi and b["p"] >= pmin]
        if not cand:
            continue
        if mode == "argmax":
            cand = [max(cand, key=lambda b: (b["p"], -b["price"]))]
        picks.extend(cand)
    return picks


def stats(picks, tick=0.0):
    if not picks:
        return None
    n = len(picks)
    w = sum(p["won"] for p in picks) / n
    ap = st.mean(p["price"] + tick for p in picks)
    ev = w - ap
    roi = (ev / ap * 100) if ap > 0 else 0.0
    return dict(n=n, win=w * 100, avg=ap + 0, ev=ev, roi=roi)


def eff(picks, extra_tick):
    """ราคาที่จ่ายจริง = price + tick (จำลอง spread/ask สูงกว่าราคาซื้อขายล่าสุด)"""
    if not picks:
        return None
    n = len(picks)
    w = sum(p["won"] for p in picks) / n
    ap = min(0.999, st.mean(p["price"] for p in picks) + extra_tick)
    ev = w - ap
    return dict(n=n, win=w * 100, avg=ap, ev=ev, roi=ev / ap * 100 if ap else 0)


def halves(picks):
    """แบ่งครึ่งตามเวลา (ตามลำดับวัน) เพื่อดูเสถียรภาพ"""
    ds = sorted({p["date"] for p in picks})
    if len(ds) < 8:
        return None, None
    mid = ds[len(ds) // 2]
    a = [p for p in picks if p["date"] < mid]
    b = [p for p in picks if p["date"] >= mid]
    return stats(a), stats(b)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="2026-08-15")
    ap.add_argument("--min-n", type=int, default=40)
    ap.add_argument("--out", default=os.path.join(HERE, "reports", "edge_search.md"))
    a = ap.parse_args()

    rows = load()
    tr = [r for r in rows if r["date"] < a.split]
    te = [r for r in rows if r["date"] >= a.split]
    mtr, mte, mall = by_market(tr), by_market(te), by_market(rows)
    print(f"rows={len(rows)}  train={len(tr)}  test={len(te)}  split={a.split}\n")

    # ── 1) กวาดกริด บน TRAIN เท่านั้น แล้วคัดเฉพาะที่ผ่าน 3 เงื่อนไข ──
    grid = []
    for hour in HOURS:
        for pmin in PMINS:
            for (lo, hi) in BANDS:
                for mode in ("argmax", "all"):
                    s = stats(select(mtr, hour, pmin, lo, hi, mode))
                    if not s or s["n"] < a.min_n:
                        continue
                    if s["win"] > 70 and s["avg"] <= 0.30 and s["ev"] > 0:
                        grid.append((hour, pmin, lo, hi, mode, s))
    grid.sort(key=lambda g: (-g[5]["win"], -g[5]["n"]))
    print(f"=== กฎที่ผ่านเกตบน TRAIN (win>70, price<=0.30, EV>0, n>={a.min_n}): {len(grid)} กฎ ===\n")
    for hour, pmin, lo, hi, mode, s in grid[:18]:
        print(f"  {hour}:00 p>={pmin:.2f} price {lo:.2f}-{hi:.2f} {mode:6s} | n={s['n']:5d} win={s['win']:5.1f}% avg={s['avg']:.3f} EV={s['ev']:+.3f} ROI={s['roi']:+.0f}%")

    # ── 2) ยืนยันบน TEST (ไม่เคยใช้เลือก) ──
    print("\n=== ยืนยันบน TEST (одинаковый rule, ไม่ fit อะไรเพิ่ม) ===")
    confirmed = []
    for hour, pmin, lo, hi, mode, s_tr in grid:
        s_te = stats(select(mte, hour, pmin, lo, hi, mode))
        s_all = stats(select(mall, hour, pmin, lo, hi, mode))
        if not s_te:
            continue
        ok = s_te["win"] > 70 and s_te["avg"] <= 0.30 and s_te["ev"] > 0
        confirmed.append((hour, pmin, lo, hi, mode, s_tr, s_te, s_all, ok))
    confirmed.sort(key=lambda c: (-c[6]["win"], -c[6]["n"]))
    for hour, pmin, lo, hi, mode, s_tr, s_te, s_all, ok in confirmed[:14]:
        flag = "✅" if ok else "❌"
        print(f"  {flag} {hour}:00 p>={pmin:.2f} price {lo:.2f}-{hi:.2f} {mode:6s} | "
              f"train n={s_tr['n']:5d} win={s_tr['win']:5.1f}% | test n={s_te['n']:5d} win={s_te['win']:5.1f}% avg={s_te['avg']:.3f} EV={s_te['ev']:+.3f} ROI={s_te['roi']:+.0f}%" )

    n_ok = sum(1 for c in confirmed if c[8])
    print(f"\n--> ผ่านทั้ง train และ test: {n_ok}/{len(confirmed)} กฎ")

    # ── 3) วิเคราะห์เชิงลึกของกฎแนะนำ ──
    rec = None
    for hour, pmin, lo, hi, mode, s_tr, s_te, s_all, ok in confirmed:
        if ok and s_all["n"] >= 100:
            rec = (hour, pmin, lo, hi, mode, s_all); break
    if not rec:
        print("ไม่พบกฎที่ผ่านทั้งสองช่วง"); return

    hour, pmin, lo, hi, mode, s_all = rec
    picks_all = select(mall, hour, pmin, lo, hi, mode)
    print(f"\n=== กฎแนะนำ: {hour}:00 local · p>={pmin} · ราคา {lo}-{hi} · {mode} ===")
    print(f"  รวม 85 วัน: n={s_all['n']} win={s_all['win']:.1f}% avg={s_all['avg']:.3f} "
          f"EV/share={s_all['ev']:+.3f} ROI={s_all['roi']:+.0f}%")

    # ความไวต่อ spread (ราคาจริง = last trade + tick)
    print("  ความไวต่อ spread (สมมติราคาที่จ่ายจริงสูงกว่าราคาบันทึก):")
    for tk in (0.0, 0.005, 0.01, 0.02, 0.03):
        s = eff(picks_all, tk)
        print(f"    +{tk:.3f} -> avg={s['avg']:.3f} win={s['win']:.1f}% EV={s['ev']:+.3f} ROI={s['roi']:+.0f}%")

    # แบ่งครึ่งเวลา
    h1, h2 = halves(picks_all)
    if h1 and h2:
        print(f"  ครึ่งแรก n={h1['n']} win={h1['win']:.1f}% ROI={h1['roi']:+.0f}% | ครึ่งหลัง n={h2['n']} win={h2['win']:.1f}% ROI={h2['roi']:+.0f}%")

    # ต่อเมือง
    per_city = defaultdict(list)
    for p in picks_all:
        per_city[p["city"]].append(p)
    city_rows = []
    for c, ps in per_city.items():
        s = stats(ps)
        city_rows.append((c, s))
    city_rows.sort(key=lambda x: -x[1]["win"])
    tot_n = sum(s["n"] for _, s in city_rows)
    tot_w = sum(s["win"] / 100 * s["n"] for _, s in city_rows)
    print(f"  เมืองที่เข้าเกณฑ์: {len(city_rows)} เมือง · เมืองที่ดีที่สุด 5: " +
          ", ".join(f"{c} {s['win']:.0f}% (n={s['n']})" for c, s in city_rows[:5]))
    print(f"  เมืองที่แย่สุด 5: " + ", ".join(f"{c} {s['win']:.0f}% (n={s['n']})" for c, s in city_rows[-5:]))

    # ถอดเมืองที่ดีสุดออก → ยังผ่านไหม (กันพึ่งเมืองเดียว)
    top5 = {c for c, _ in city_rows[:5]}
    rest = [p for p in picks_all if p["city"] not in top5]
    s_rest = stats(rest)
    print(f"  ถอด 5 เมืองที่ดีสุดออก: n={s_rest['n']} win={s_rest['win']:.1f}% avg={s_rest['avg']:.3f} ROI={s_rest['roi']:+.0f}%")
    # ถอดเมืองที่ดีสุดออกทีละเมืองซ้ำ ๆ (worst-case leave-one-out)
    worst = None
    for c, _ in city_rows:
        s = stats([p for p in picks_all if p["city"] != c])
        if worst is None or s["win"] < worst[1]["win"]:
            worst = (c, s)
    print(f"  leave-one-out แย่สุด (ถอด {worst[0]}): n={worst[1]['n']} win={worst[1]['win']:.1f}% ROI={worst[1]['roi']:+.0f}%")

    # จำนวนไม้ต่อวัน / วันที่ไม่มีไม้เลย
    per_day = defaultdict(int)
    for p in picks_all:
        per_day[p["date"]] += 1
    days = sorted({p["date"] for p in picks_all})
    zero_days = sum(1 for d in sorted({r['date'] for r in rows}) if per_day.get(d, 0) == 0)
    print(f"  ความถี่: {st.mean(per_day.values()):.1f} ไม้/วัน (สูงสุด {max(per_day.values())}) · "
          f"มีไม้ {len(days)}/85 วัน · วันที่ไม่มีไม้เลย {zero_days} วัน")

    # ── 4) ทางเลือกที่ win สูงกว่านี้ (แลกด้วยจำนวนไม้) ──
    print("\n=== ทางเลือก 'คมสุด' (win สูง แต่ไม้น้อยลง) ===")
    for pmin2 in (0.90, 0.95, 0.97, 0.98):
        for (lo2, hi2) in [(0.02, 0.30), (0.02, 0.25), (0.02, 0.15)]:
            for h2_ in (15, 16, 17):
                s = stats(select(mall, h2_, pmin2, lo2, hi2, "argmax"))
                if s and s["n"] >= 25:
                    mark = " ✅" if (s["win"] > 70 and s["avg"] <= 0.30 and s["ev"] > 0) else ""
                    if s["win"] >= 75:
                        print(f"  {h2_}:00 p>={pmin2:.2f} price {lo2:.2f}-{hi2:.2f} | n={s['n']:4d} "
                              f"win={s['win']:5.1f}% avg={s['avg']:.3f} EV={s['ev']:+.3f} ROI={s['roi']:+.0f}%{mark}")

    # ── 5) เขียนรายงาน ──
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(f"# ค้นหา edge: win > 70% · ราคา YES ≤ 0.30 · EV > 0\n\n> สร้างโดย `edge_search.py --split {a.split}` · ข้อมูล `data/cheap_bets.csv` "
                f"({len(rows):,} แถว bin×วัน×ชั่วโมง, 48 เมือง × 85 วัน) · คำตอบ = bucket ที่ตลาด resolve จริง\n"
                f"> เกตผู้ใช้: win > 70% AND avg price ≤ 0.30 AND EV > 0 → บน TRAIN n≈{len(tr):,}\n\n")
        f.write(f"## กฎที่ผ่านทั้ง train และ test ({n_ok}/{len(confirmed)})\n\n| กฎ | train n / win | test n / win | test avg price | EV/share | ROI |\n|---|---|---|---|---|---|\n")
        for hour, pmin, lo, hi, mode, s_tr, s_te, s_all, ok in confirmed[:20]:
            if not ok:
                continue
            f.write(f"| {hour}:00 · p≥{pmin} · {lo:.2f}–{hi:.2f} · {mode} | {s_tr['n']} / {s_tr['win']:.1f}% | "
                    f"{s_te['n']} / **{s_te['win']:.1f}%** | {s_te['avg']:.3f} | {s_te['ev']:+.3f} | **{s_te['roi']:+.0f}%** |\n")
        f.write(f"\n## กฎแนะนำ (ทั้ง 85 วัน)\n\n**{hour}:00 local · p≥{pmin} · ราคา {lo}–{hi} · {mode}**\n\n")
        f.write(f"- n = {s_all['n']} ไม้ · ชนะ {s_all['win']:.1f}% · ราคาเฉลี่ย {s_all['avg']:.3f} · EV/share {s_all['ev']:+.3f} · ROI {s_all['roi']:+.0f}%\n")
        f.write(f"- ครึ่งแรก/ครึ่งหลัง: " + (f"{h1['win']:.1f}% (n={h1['n']}) / {h2['win']:.1f}% (n={h2['n']})" if h1 else "n/a") + "\n")
        f.write(f"- {len(city_rows)} เมืองเข้าเกณฑ์ · ถอด 5 เมืองที่ดีสุดออกยังได้ {s_rest['win']:.1f}% (n={s_rest['n']})\n")
        f.write(f"- ความถี่ {st.mean(per_day.values()):.1f} ไม้/วัน · มี {len(days)}/85 วันที่มีไม้\n")
        f.write("\n### ความไวต่อ spread\n\n| ราคาที่จ่ายจริง = last trade + | avg | win | EV/share | ROI |\n|---|---|---|---|---|\n")
        for tk in (0.0, 0.005, 0.01, 0.02, 0.03):
            s = eff(picks_all, tk)
            f.write(f"| {tk:.3f} | {s['avg']:.3f} | {s['win']:.1f}% | {s['ev']:+.3f} | {s['roi']:+.0f}% |\n")
        f.write("\n### รายเมือง (เรียงตาม win)\n\n| city | n | win% | avg | ROI |\n|---|---|---|---|---|\n")
        for c, s in city_rows:
            f.write(f"| {c} | {s['n']} | {s['win']:.1f} | {s['avg']:.3f} | {s['roi']:+.0f}% |\n")
    print(f"\nเขียน {a.out}")


if __name__ == "__main__":
    main()
