#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ensemble_members.py — ทดลองไอเดียจาก repo คู่แข่ง (suislanchez / AadiXD200 / kylecwalden):
"ใช้สมาชิก ensemble นับเป็นความน่าจะเป็น" (ensemble agreement → probability) ดีกว่า Gaussian บนค่าเฉลี่ยหรือไม่
  A) ล่วงหน้า:        p(bin) = สัดส่วนของโมเดล (bias-corrected) ที่ตกลงใน bin   vs   Gaussian(mu, sigma)
  B) ระหว่างวัน 16:00: value_i = max(max_so_far, m_i) → นับสมาชิก   vs   วิธีของเรา (max_so_far + การกระจาย increment จากอดีต)
  วัดกับ bucket ที่ตลาด resolve จริง · bias/sigma fit บน train เท่านั้น
รัน: python3 ensemble_members.py --split 2026-08-15
"""
import argparse, csv, json, math, os, statistics as st
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
import headtohead as H
import api_value as AV

OUT = os.path.join(HERE, "reports", "ensemble_members.md")
MODELS = AV.MODELS


def load_maxso():
    ser = defaultdict(lambda: defaultdict(dict))
    p = os.path.join(HERE, "data", "hourly_obs.csv")
    for r in csv.DictReader(open(p, encoding="utf-8")):
        try:
            ser[r["city"]][r["date"]][int(r["hour"])] = float(r["tmpc"])
        except ValueError:
            continue
    return ser


def kernel_p(vals, unit, bins, kern=0.35):
    """นับสมาชิกเข้า bin + เกลี่ยด้วย kernel เล็ก ๆ (กัน bin ที่ไม่มีสมาชิกตกได้ 0 เป๊ะ)"""
    k = kern * 9 / 5 if unit == "F" else kern
    out = {}
    for lo, hi in bins:
        s = 0.0
        for v in vals:
            vv = v * 9 / 5 + 32 if unit == "F" else v
            s += H.norm_cdf((hi + 0.5 - vv) / k) - H.norm_cdf((lo - 0.5 - vv) / k)
        out[(lo, hi)] = s / max(1, len(vals))
    tot = sum(out.values()) or 1.0
    return {b: v / tot for b, v in out.items()}


def evalp(prob_rows):
    n = t1 = t2 = 0
    brier = 0.0
    for p, key in prob_rows:
        order = sorted(p.items(), key=lambda kv: -kv[1])
        n += 1
        if order[0][0] == key:
            t1 += 1
        if key in [b for b, _ in order[:2]]:
            t2 += 1
        brier += sum((pv - (1.0 if b == key else 0.0)) ** 2 for b, pv in p.items())
    return dict(n=n, top1=t1 / n * 100 if n else 0, top2=t2 / n * 100 if n else 0, brier=brier / n if n else 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="2026-08-15")
    a = ap.parse_args()
    rows = AV.load_ds()
    labels = AV.load_labels()
    bins_by_unit = {u: H.W.market_bins(u) for u in ("C", "F")}
    tr = [r for r in rows if r["date"] < a.split]
    te = [r for r in rows if r["date"] >= a.split]
    bias = AV.bias_fix(tr, MODELS)
    # sigma pooled (train) จาก blend
    err = defaultdict(list)
    for r, mu, _ in AV.blend(tr, MODELS, bias, {}):
        o = H.num(r.get("obs_c"))
        if o is not None:
            err[r["unit"]].append(mu - o)
    sigma = {u: max(0.4, math.sqrt(sum(e * e for e in v) / len(v))) for u, v in err.items() if v}
    print(f"train={len(tr)} test={len(te)} · sigma(train) C={sigma.get('C',0):.3f} F={sigma.get('F',0):.3f}\n")

    # ── A) ล่วงหน้า ──
    gauss, memb, memb_k, memb_med = [], [], [], []
    for r in te:
        key = (r["city"], r["date"])
        if key not in labels:
            continue
        vals = [v for v in (H.num(r.get("m_" + m)) for m in MODELS) if v is not None]
        vals = [v - bias.get((m, r["unit"]), 0.0) for v, m in zip(vals, [m for m in MODELS if H.num(r.get("m_" + m)) is not None])]
        if len(vals) < 3:
            continue
        lo, hi, unit, _lab = labels[key]
        kk = (lo, hi)
        gauss.append((H.prob_of_bins(st.mean(vals), sigma.get(unit, 1.5), unit, bins_by_unit[unit]), kk))
        memb.append((kernel_p(vals, unit, bins_by_unit[unit], kern=0.01), kk))     # นับดิบ ๆ
        memb_k.append((kernel_p(vals, unit, bins_by_unit[unit], kern=0.35), kk))   # เกลี่ยเล็กน้อย
        # ใช้มัธยฐานแทนค่าเฉลี่ย
        med = st.median(vals)
        memb_med.append((H.prob_of_bins(med, sigma.get(unit, 1.5), unit, bins_by_unit[unit]), kk))
    for name, pr in (("Gaussian บนค่าเฉลี่ย (วิธีเรา)", gauss), ("นับสมาชิกดิบ (ensemble agreement)", memb),
                     ("นับสมาชิก + kernel 0.35", memb_k), ("Gaussian บนมัธยฐานของสมาชิก", memb_med)):
        s = evalp(pr)
        print(f"  [A] {name:38s} n={s['n']:4d} top-1 {s['top1']:5.1f}%  top-2 {s['top2']:5.1f}%  Brier {s['brier']:.3f}")

    # ── B) ระหว่างวัน 16:00 ──
    ser = load_maxso()
    A, B, C = [], [], []
    for r in te:
        key = (r["city"], r["date"])
        if key not in labels:
            continue
        c, d = key
        day = ser.get(c, {}).get(d) or {}
        past = [v for h, v in day.items() if h <= 16]
        if len(day) < 8 or not past:
            continue
        ms = max(past)
        lo, hi, unit, _lab = labels[key]
        kk = (lo, hi)
        vals = [v for v in (H.num(r.get("m_" + m)) for m in MODELS) if v is not None]
        vals = [v - bias.get((m, r["unit"]), 0.0) for v, m in zip(vals, [m for m in MODELS if H.num(r.get("m_" + m)) is not None])]
        if len(vals) < 3:
            continue
        # สมาชิกแบบ max(ค่าที่วัดได้, พยากรณ์)
        impl = [max(ms, v) for v in vals]
        B.append((kernel_p(impl, unit, bins_by_unit[unit], kern=0.35), kk))
        # วิธีของเรา: max_so_far + การกระจาย increment จากอดีต (causal, 30 วัน)
        A.append((None, kk))   # เติมทีหลัง
        C.append((vals, ms, unit, kk, kk))
    # คำนวณวิธีของเรา (ต้องใช้ประวัติ)
    dates = sorted({d for _, d in labels})
    ours, memb2 = [], []
    for idx, r in enumerate(te):
        key = (r["city"], r["date"])
        if key not in labels:
            continue
        c, d = key
        day = ser.get(c, {}).get(d) or {}
        past = [v for h, v in day.items() if h <= 16]
        if len(day) < 8 or not past:
            continue
        ms = max(past)
        hist_ms, hist_full = [], []
        for dd in sorted(ser[c]):
            if dd >= d:
                break
            dd_day = ser[c][dd]
            p2 = [v for h, v in dd_day.items() if h <= 16]
            if len(dd_day) >= 8 and p2:
                hist_ms.append(max(p2)); hist_full.append(max(dd_day.values()))
        rises = [hf - hm for hf, hm in zip(hist_full[-30:], hist_ms[-30:])]
        if len(rises) < 8:
            continue
        lo, hi, unit, _lab = labels[key]
        ours.append((AV.p_obs(rises, ms, unit, bins_by_unit[unit]), (lo, hi)))
        vals = [v for v in (H.num(r.get("m_" + m)) for m in MODELS) if v is not None]
        vals = [v - bias.get((m, r["unit"]), 0.0) for v, m in zip(vals, [m for m in MODELS if H.num(r.get("m_" + m)) is not None])]
        impl = [max(ms, v) for v in vals]
        memb2.append((kernel_p(impl, unit, bins_by_unit[unit], kern=0.35), (lo, hi)))
    if ours and memb2:
        s_o, s_m = evalp(ours), evalp(memb2)
        print(f"\n  [B] 16:00 · วิธีเรา (max + increment ย้อนหลัง)   n={s_o['n']:4d} top-1 {s_o['top1']:5.1f}%  top-2 {s_o['top2']:5.1f}%  Brier {s_o['brier']:.3f}")
        print(f"  [B] 16:00 · สมาชิก max(obs, พยากรณ์) + kernel    n={s_m['n']:4d} top-1 {s_m['top1']:5.1f}%  top-2 {s_m['top2']:5.1f}%  Brier {s_m['brier']:.3f}")
        # ผสม 50/50
        mix = []
        for (po, k1), (pm, k2) in zip(ours, memb2):
            if k1 != k2:
                continue
            mix.append((AV.logpool([(po, 0.5), (pm, 0.5)], 1.0), k1))
        s_x = evalp(mix)
        print(f"  [B] 16:00 · ผสม 50/50 (ของเรา ⊕ สมาชิก)          n={s_x['n']:4d} top-1 {s_x['top1']:5.1f}%  top-2 {s_x['top2']:5.1f}%  Brier {s_x['brier']:.3f}")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write(f"# ทดลองไอเดีย 'ensemble agreement → probability' (repo คู่แข่ง) — `--split {a.split}`\n\n")
        f.write("| การตั้งค่า | n | top-1 | top-2 | Brier |\n|---|---|---|---|---|\n")
        for name, pr in (("A · Gaussian บนค่าเฉลี่ย (วิธีเรา)", gauss), ("A · นับสมาชิกดิบ", memb),
                         ("A · นับสมาชิก + kernel", memb_k), ("A · Gaussian บนมัธยฐาน", memb_med)):
            s = evalp(pr)
            f.write(f"| {name} | {s['n']} | {s['top1']:.1f}% | {s['top2']:.1f}% | {s['brier']:.3f} |\n")
        if ours:
            for name, pr in (("B16 · วิธีเรา (increment)", ours), ("B16 · สมาชิก max(obs,fc)", memb2), ("B16 · ผสม 50/50", mix)):
                s = evalp(pr)
                f.write(f"| {name} | {s['n']} | {s['top1']:.1f}% | {s['top2']:.1f}% | {s['brier']:.3f} |\n")
    print(f"\nเขียน {OUT}")


if __name__ == "__main__":
    main()
