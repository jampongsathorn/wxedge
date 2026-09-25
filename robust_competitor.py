#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
robust_competitor.py — ทดสอบความทนทานของไอเดียคู่แข่งที่ดูดี (กันหลงตัวเลขสวย)
 1) Champion rule แยกรายเมือง + ผลของการตัดเมืองที่ obs พัง (HK/Shenzhen)
 2) Ladder/cheap-longshot rule (p≥0.90 · ราคา ≤0.10) : train/test, ครึ่งเวลา, LOO, spread, การกระจุกตัวของกำไร
 3) Sell-overpriced rule (buy NO) : train/test, ครึ่งเวลา, LOO, spread
 4) ตรวจความครอบคลุม obs ของเมืองจีน (ชั่วโมง/วัน) — หาสาเหตุที่ HK/Shenzhen ตรงต่ำ
รัน: python3 robust_competitor.py --split 2026-08-15
"""
import argparse, csv, os, statistics as st
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "reports", "competitor_robust.md")


def load(path):
    out = []
    for r in csv.DictReader(open(path, newline="", encoding="utf-8")):
        try:
            out.append(dict(city=r["city"], date=r["date"], hour=int(r["hour"]), bin=r["bin"], unit=r["unit"],
                            p=float(r["model_p"]), price=float(r["price"]), won=int(r["won"])))
        except (KeyError, ValueError):
            continue
    return out


def mk(rows):
    m = defaultdict(list)
    for r in rows:
        m[(r["city"], r["date"], r["hour"])].append(r)
    return m


def basket_roi(pairs, tick=0.0):
    """pairs = [(cost, payout)]"""
    if not pairs:
        return None
    cost = sum(t[0] + tick * t[2] for t in pairs)
    pay = sum(t[1] for t in pairs)
    return dict(n=len(pairs), roi=(pay - cost) / cost * 100 if cost else 0, cost=cost / len(pairs),
                win=sum(1 for t in pairs if t[1] > 0) / len(pairs) * 100)


def legs_roi(legs, tick=0.0, side="no"):
    if not legs:
        return None
    if side == "no":
        cost = sum((1 - b["price"]) + tick for b in legs)
        pay = sum(1 - b["won"] for b in legs)
    else:
        cost = sum(b["price"] + tick for b in legs)
        pay = sum(b["won"] for b in legs)
    n = len(legs)
    return dict(n=n, roi=(pay - cost) / cost * 100 if cost else 0, cost=cost / n, pay=pay / n)


def sel_longshot(m, hour, pmax=0.10, pmin=0.90):
    out = []
    for (c, d, h), bins in m.items():
        if h != hour:
            continue
        cand = sorted([b for b in bins if b["p"] >= pmin and 0.02 <= b["price"] <= pmax], key=lambda b: -b["p"])[:3]
        if cand:
            out.append((sum(b["price"] for b in cand), 1.0 if any(b["won"] for b in cand) else 0.0, len(cand), cand))
    return out


def sel_champion(m, hour, pmax=0.25, pmin=0.90):
    out = []
    for (c, d, h), bins in m.items():
        if h != hour:
            continue
        cand = [b for b in bins if b["p"] >= pmin and 0.02 <= b["price"] <= pmax]
        if not cand:
            continue
        b = max(cand, key=lambda x: (x["p"], -x["price"]))
        out.append((b["price"], float(b["won"]), 1, [b]))
    return out


def sel_overpriced(m, hour, lo=0.50, hi=0.97, margin=0.30):
    legs = []
    for (c, d, h), bins in m.items():
        if h != hour:
            continue
        legs += [b for b in bins if lo <= b["price"] <= hi and (b["price"] - b["p"]) >= margin]
    return legs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="2026-08-15")
    ap.add_argument("--hour", type=int, default=16)
    a = ap.parse_args()
    rows = load(os.path.join(HERE, "data", "cheap_bets.csv"))
    m_all = mk(rows)
    tr = [r for r in rows if r["date"] < a.split]
    te = [r for r in rows if r["date"] >= a.split]
    m_tr, m_te = mk(tr), mk(te)
    L = []
    print(f"rows={len(rows):,} · split={a.split} · hour={a.hour}\n")

    # ── 1) Champion รายเมือง + ตัดเมือง obs พัง ──
    print("[1] Champion (p≥0.90 · 0.02–0.25 · argmax) รายเมือง @16:00")
    per_city = defaultdict(list)
    for (c, d, h), bins in m_all.items():
        if h != a.hour:
            continue
        cand = [b for b in bins if b["p"] >= 0.90 and 0.02 <= b["price"] <= 0.25]
        if cand:
            b = max(cand, key=lambda x: (x["p"], -x["price"]))
            per_city[c].append((b["price"], float(b["won"]), 1, [b]))
    city_rows = sorted(((c, basket_roi(v)) for c, v in per_city.items()), key=lambda x: -x[1]["roi"])
    bad_cities = [c for c, s in city_rows if s["roi"] < 0]
    for c, s in city_rows:
        flag = "  ⚠" if s["roi"] < 0 else ""
        print(f"    {c:16s} n={s['n']:4d} ROI={s['roi']:+8.1f}% ชนะ={s['win']:5.1f}% avg={s['cost']:.3f}{flag}")
    good = [p for c, v in per_city.items() if c not in ("hong-kong", "shenzhen") for p in v]
    s_all, s_good = basket_roi([p for v in per_city.values() for p in v]), basket_roi(good)
    print(f"    ทั้งหมด: n={s_all['n']} ROI={s_all['roi']:+.1f}%  |  ตัด hong-kong+shenzhen: n={s_good['n']} ROI={s_good['roi']:+.1f}%")
    print(f"    เมืองที่ ROI ติดลบ: {bad_cities}")

    # ── 2) Long-shot (ladder) robustness ──
    print("\n[2] Long-shot/ladder (p≥0.90 · ราคา 0.02–0.10) @16:00 — ความทนทาน")
    for tag, m in (("all", m_all), ("train", m_tr), ("test", m_te)):
        s = basket_roi(sel_longshot(m, a.hour))
        if s:
            print(f"    {tag:6s} n={s['n']:4d} ROI={s['roi']:+8.1f}% ต้นทุน/ตะกร้า={s['cost']:.3f} ถูก≥1ใบ={s['win']:5.1f}%")
    print("    ความไวต่อ spread (จ่ายแพงกว่าที่บันทึก):")
    for tk in (0.0, 0.005, 0.01, 0.02, 0.03):
        s = basket_roi(sel_longshot(m_all, a.hour), tick=tk)
        print(f"      +{tk:.3f}/ใบ → ต้นทุน {s['cost']:.3f} ROI {s['roi']:+8.1f}%")
    pairs = sel_longshot(m_all, a.hour)
    prof = sorted((t[1] - t[0] for t in pairs), reverse=True)
    tot = sum(prof)
    top10 = sum(prof[:max(1, len(prof) // 10)])
    print(f"    การกระจุกตัว: กำไรรวม(ก่อนหารต้นทุน)={tot:.1f} · จาก 10% ของตะกร้าที่ดีสุด={top10/tot*100:.1f}% · "
          f"ตะกร้าที่กำไรบวก={sum(1 for x in prof if x>0)/len(prof)*100:.1f}%")
    bycity = defaultdict(list)
    for c, pp in sel_longshot_by_city(m_all, a.hour):
        bycity[c].append(pp)
    loo = None
    for c in bycity:
        s = basket_roi([p for cc, v in bycity.items() if cc != c for p in v])
        if s and (loo is None or s["roi"] < loo[1]["roi"]):
            loo = (c, s)
    print(f"    leave-one-city-out แย่สุด (ถอด {loo[0]}): n={loo[1]['n']} ROI={loo[1]['roi']:+.1f}%")

    # ── 3) Sell overpriced robustness ──
    print("\n[3] ขายแพงเกิน (buy NO · ราคา 0.50–0.97 & price−p ≥ 0.30) @16:00 — ความทนทาน")
    for tag, m in (("all", m_all), ("train", m_tr), ("test", m_te)):
        s = legs_roi(sel_overpriced(m, a.hour))
        if s:
            print(f"    {tag:6s} n={s['n']:4d} ROI={s['roi']:+8.1f}% จ่ายเฉลี่ย(NO)={s['cost']:.3f} รับเฉลี่ย={s['pay']:.3f}")
    print("    ความไวต่อ spread ของฝั่ง NO:")
    for tk in (0.0, 0.005, 0.01, 0.02, 0.03):
        s = legs_roi(sel_overpriced(m_all, a.hour), tick=tk)
        print(f"      +{tk:.3f} → จ่าย {s['cost']:.3f} ROI {s['roi']:+8.1f}%")
    ol = defaultdict(list)
    for (c, d, h), bins in m_all.items():
        if h != a.hour:
            continue
        for b in bins:
            if 0.50 <= b["price"] <= 0.97 and (b["price"] - b["p"]) >= 0.30:
                ol[c].append(b)
    cnt = defaultdict(int)
    for c, v in ol.items():
        for b in v:
            cnt[c] += 1
    top5 = sorted(cnt.items(), key=lambda x: -x[1])[:5]
    print(f"    เมืองที่ออกไม้มากสุด: " + ", ".join(f"{c}({n})" for c, n in top5))
    loo = None
    for c in ol:
        s = legs_roi([b for cc, v in ol.items() if cc != c for b in v])
        if s and (loo is None or s["roi"] < loo[1]["roi"]):
            loo = (c, s)
    if loo:
        print(f"    leave-one-city-out แย่สุด (ถอด {loo[0]}): n={loo[1]['n']} ROI={loo[1]['roi']:+.1f}%")

    # ── 4) เมืองจีน: ความครอบคลุม obs ──
    print("\n[4] ความครอบคลุม obs รายชั่วโมง (hourly_obs.csv) — หาสาเหตุที่ HK/Shenzhen ตรงต่ำ")
    cov = defaultdict(list)
    p = os.path.join(HERE, "data", "hourly_obs.csv")
    if os.path.exists(p):
        tmp = defaultdict(lambda: defaultdict(int))
        for r in csv.DictReader(open(p, encoding="utf-8")):
            tmp[r["city"]][r["date"]] += 1
        for c, dd in tmp.items():
            for d, n in dd.items():
                if d >= "2026-07-01":
                    cov[c].append(n)
    for c in ["hong-kong", "shenzhen", "guangzhou", "beijing", "shanghai", "qingdao", "chengdu", "wuhan", "chongqing", "taipei", "tokyo", "nyc", "london"]:
        if c in cov:
            v = cov[c]
            print(f"    {c:14s} ชั่วโมง/วัน เฉลี่ย {st.mean(v):4.1f} (ต่ำสุด {min(v)}) n={len(v)}")
    # diff ของ obs_c กับ label
    labels = {(r["city"], r["date"]): (float(r["lo"]), r["unit"])
              for r in csv.DictReader(open(os.path.join(HERE, "data", "market_labels.csv"), encoding="utf-8"))}
    diff = defaultdict(list)
    for r in csv.DictReader(open(os.path.join(HERE, "data", "dataset_full.csv"), encoding="utf-8")):
        k = (r["city"], r["date"])
        if k in labels and r.get("obs_c"):
            lo, unit = labels[k]
            v = float(r["obs_c"])
            v = v * 9 / 5 + 32 if unit == "F" else v
            diff[r["city"]].append(v - lo)
    for c in ["hong-kong", "shenzhen", "guangzhou", "beijing", "moscow", "paris", "munich", "helsinki", "seoul", "tokyo"]:
        if c in diff:
            print(f"    {c:14s} obs − label เฉลี่ย {st.mean(diff[c]):+.2f}° (มัธยฐาน {st.median(diff[c]):+.2f}) n={len(diff[c])}")


def sel_longshot_by_city(m, hour, pmax=0.10, pmin=0.90):
    out = []
    for (c, d, h), bins in m.items():
        if h != hour:
            continue
        cand = sorted([b for b in bins if b["p"] >= pmin and 0.02 <= b["price"] <= pmax], key=lambda b: -b["p"])[:3]
        if cand:
            out.append((c, (sum(b["price"] for b in cand), 1.0 if any(b["won"] for b in cand) else 0.0, len(cand), cand)))
    return out


if __name__ == "__main__":
    main()
