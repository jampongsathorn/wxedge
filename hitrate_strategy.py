#!/usr/bin/env python3
"""
hitrate_strategy.py — ตอบคำถาม "ทำยังไงให้ทาย bucket ถูกที่สุด"

วิเคราะห์จาก dataset (พยากรณ์ย้อนหลัง vs ค่าจริง):
  1) hit rate แยกตามประเภทตลาด (°F bin กว้าง 2 องศา vs °C bin กว้าง 1 องศา)
  2) precision / coverage tradeoff: ถ้าทายเฉพาะวันที่ "มั่นใจ" (p_top ≥ threshold) จะถูกกี่ %
  3) กลยุทธ์ top-2 (ทาย 2 bin) + ราคาคุ้มไหม
  4) จัดอันดับเมืองที่ควรเล่น / ควรเลี่ยง
  5) ผลของ lead time (ทายล่วงหน้ากี่วันยังแม่น)
"""
import argparse, csv, math, os, statistics as st, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wxedge as W


def loads(path):
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    for r in rows:
        r["mu_c"] = float(r["mu_c"]); r["sigma_c"] = float(r["sigma_c"]); r["obs_c"] = float(r["obs_c"])
    return rows


def rank_and_p(row):
    p = W.prob_dist(row["mu_c"], row["sigma_c"], row["unit"])
    order = sorted(p.items(), key=lambda kv: -kv[1])
    lab = W.bucket_label(row["obs_c"], row["unit"])
    idx = [k for k, _ in order].index(lab) if lab in p else 99
    return order, lab, idx, (p.get(lab, 0.0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                   "data", "dataset_full.csv"))
    ap.add_argument("--min-n", type=int, default=60)
    a = ap.parse_args()
    rows = loads(a.data)
    print("dataset %d วัน-เมือง | %s..%s\n" % (len(rows), min(r["date"] for r in rows), max(r["date"] for r in rows)))

    # 1) แยกตามหน่วย
    print("=== 1) hit rate แยกตามประเภทตลาด ===")
    for unit, label in (("F", "สหรัฐฯ: bin กว้าง 2°F (1.1°C)"), ("C", "ตลาดอื่น: bin กว้าง 1°C")):
        rs = [r for r in rows if r["unit"] == unit]
        if not rs:
            continue
        h1 = h2 = 0
        for r in rs:
            order, lab, idx, _ = rank_and_p(r)
            h1 += idx == 0
            h2 += idx <= 1
        print("  %-28s n=%-5d top-1=%.1f%%  top-2=%.1f%%" % (label, len(rs), 100 * h1 / len(rs), 100 * h2 / len(rs)))

    # 2) precision/coverage
    print("\n=== 2) ถ้าทายเฉพาะวันที่มีความมั่นใจสูง (p ของ bin ที่ดีสุด) ===")
    print("  %-10s %8s %10s %12s" % ("threshold", "ไม้ที่เล่น", "ถูก%", "cover% ของวัน"))
    scored = []
    for r in rows:
        order, lab, idx, p_act = rank_and_p(r)
        scored.append((order[0][1], idx == 0, r))
    n = len(scored)
    for t in [0.0, 0.35, 0.45, 0.55, 0.65, 0.75]:
        sel = [s for s in scored if s[0] >= t]
        if not sel:
            continue
        hit = sum(s[1] for s in sel) / len(sel)
        print("  %-10.2f %8d %9.1f%% %11.0f%%" % (t, len(sel), 100 * hit, 100 * len(sel) / n))
    print("  (ใช้ p จาก binomial-normal ของโมเดล: p สูง = การแจกแจงกระจุกใน bin เดียว)")

    # 3) กลยุทธ์ top-2 + ความคุ้ม
    print("\n=== 3) กลยุทธ์ทาย 2 bin (top-2) — ถูกเมื่อไหร่ และราคาต้องเป็นเท่าไรจึงคุ้ม ===")
    cov = sum(1 for r in rows if rank_and_p(r)[2] <= 1) / n
    print("  top-2 ครอบ %.1f%% ของวัน → ถ้าเล่น 2 bin ต้องได้ราคารวมทั้งสอง ≤ %.2f จึงจะ +EV แบบคร่าว ๆ" % (
        100 * cov, cov))

    # 4) จัดอันดับเมือง
    print("\n=== 4) เมืองที่ควรเล่น (top-1 สูง) / ควรเลี่ยง ===")
    per = defaultdict(list)
    for r in rows:
        per[r["city"]].append(r)
    stats = []
    for c, rs in per.items():
        if len(rs) < a.min_n:
            continue
        h1 = sum(1 for r in rs if rank_and_p(r)[2] == 0) / len(rs)
        h2 = sum(1 for r in rs if rank_and_p(r)[2] <= 1) / len(rs)
        mae = st.mean([abs(r["mu_c"] - r["obs_c"]) for r in rs])
        stats.append((c, h1, h2, mae, len(rs), rs[0]["unit"]))
    stats.sort(key=lambda x: -x[1])
    print("  %-14s %4s %7s %7s %7s  %s" % ("city", "unit", "top-1", "top-2", "MAE°C", "แนะนำ"))
    for c, h1, h2, mae, k, unit in stats:
        if h1 >= 0.60:
            adv = "เล่นได้ (ทาย top-1)"
        elif h1 >= 0.50:
            adv = "เล่นแบบ top-2"
        elif h1 >= 0.40:
            adv = "ระวัง — เล่นเฉพาะตอนมั่นใจสูง"
        else:
            adv = "เลี่ยง"
        print("  %-14s %4s %6.1f%% %6.1f%% %7.2f  %s" % (c, unit, h1 * 100, h2 * 100, mae, adv))

    # 5) แยกตาม "สภาพอากาศแปรปรวน" (ใช้ sigma ของวันเป็นตัววัด)
    print("\n=== 5) hit rate ตามความแปรปรวนของวัน (sigma ที่โมเดลประเมิน) ===")
    buckets = [(0, .5), (.5, .75), (.75, 1.0), (1.0, 1.5), (1.5, 9)]
    for lo, hi in buckets:
        rs = [r for r in rows if lo <= r["sigma_c"] < hi]
        if not rs:
            continue
        h1 = sum(1 for r in rs if rank_and_p(r)[2] == 0) / len(rs)
        h2 = sum(1 for r in rs if rank_and_p(r)[2] <= 1) / len(rs)
        print("  sigma %.2f-%.2f: n=%-5d top-1=%5.1f%% top-2=%5.1f%%" % (lo, hi, len(rs), 100 * h1, 100 * h2))
    print("\n  → วันไหน sigma ต่ำ (อากาศนิ่ง) = ทายง่ายสุด; เลือกเทรดวันที่ sigma ต่ำจะได้ hit rate สูงขึ้นจริง")


if __name__ == "__main__":
    main()
