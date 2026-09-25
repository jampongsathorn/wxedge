#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
api_value.py — หลักฐาน: "การเพิ่ม API สภาพอากาศ (แหล่งพยากรณ์ / แหล่ง obs / ความละเอียด)" ทำให้ทายแม่นขึ้น "เร็วขึ้น" จริงไหม

Phase 1 (day-ahead): ใช้ dataset_full.csv (11 โมเดล) — เทียบ top-1/top-2 ที่ได้จากจำนวนโมเดลต่าง ๆ (fit bias บน train เท่านั้น)
Phase 2 (ระหว่างวัน ชั่วโมงเร็ว): ใช้ data/hourly_obs.csv — เทียบโมเดล "obs ล้วน" กับ "obs + พยากรณ์ (blend)" ที่ 12:00 / 14:00 / 16:00
Phase 3: (แยกสคริปต์) obs ความละเอียดสูง (5 นาที) vs รายชั่วโมง

รัน: python3 api_value.py --split 2026-08-15 [--phase 1,2]
"""
import argparse, csv, json, math, os, statistics as st
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
import headtohead as H   # prob_of_bins, norm_cdf

MODELS = ["ecmwf_ifs025", "gfs_seamless", "icon_seamless", "gem_seamless", "meteofrance_seamless",
          "jma_seamless", "ukmo_seamless", "ecmwf_aifs025_single", "best_match",
          "ncep_nbm_conus", "ncep_hrrr_conus"]
CORE3 = ["ecmwf_ifs025", "gfs_seamless", "icon_seamless"]


def load_ds():
    rows = []
    for r in csv.DictReader(open(os.path.join(HERE, "data", "dataset_full.csv"), encoding="utf-8")):
        rows.append(r)
    return rows


def load_labels():
    lab = {}
    for r in csv.DictReader(open(os.path.join(HERE, "data", "market_labels.csv"), encoding="utf-8")):
        lab[(r["city"], r["date"])] = (float(r["lo"]), float(r["hi"]), r["unit"],
                                       (float(r["lo"]), float(r["hi"])))
    return lab


def load_hourly():
    ser = defaultdict(lambda: defaultdict(dict))
    p = os.path.join(HERE, "data", "hourly_obs.csv")
    if not os.path.exists(p):
        return ser
    for r in csv.DictReader(open(p, encoding="utf-8")):
        try:
            ser[r["city"]][r["date"]][int(r["hour"])] = float(r["tmpc"])
        except ValueError:
            continue
    return ser


# ───────────────────────── Phase 1: day-ahead, จำนวนแหล่งพยากรณ์ ─────────────────────────
def bias_fix(ds_tr, models):
    """bias ต่อ (model, unit) fit บน train"""
    acc = defaultdict(list)
    for r in ds_tr:
        for m in models:
            v = H.num(r.get("m_" + m))
            o = H.num(r.get("obs_c"))
            if v is None or o is None:
                continue
            acc[(m, r["unit"])].append(v - o)
    return {k: st.mean(v) for k, v in acc.items() if v}


def blend(ds, models, bias, sigma_by_unit, fixed=True):
    """mu = เฉลี่ย (โมเดล − bias) · sigma = pooled RMSE ของ blend (fit บน train)"""
    out = []
    for r in ds:
        vals = []
        for m in models:
            v = H.num(r.get("m_" + m))
            if v is None:
                continue
            if fixed:
                v -= bias.get((m, r["unit"]), 0.0)
            vals.append(v)
        if len(vals) < 1:
            continue
        out.append((r, st.mean(vals), sigma_by_unit.get(r["unit"], 1.5)))
    return out


def eval_rows(pairs, labels, bins_by_unit):
    n = t1 = t2 = 0
    brier = 0.0
    for r, mu, sg in pairs:
        k = (r["city"], r["date"])
        if k not in labels:
            continue
        lo, hi, unit, key = labels[k]
        p = H.prob_of_bins(mu, sg, unit, bins_by_unit[unit])
        order = sorted(p.items(), key=lambda kv: -kv[1])
        n += 1
        if order[0][0] == key:
            t1 += 1
        if key in [b for b, _ in order[:2]]:
            t2 += 1
        brier += sum((pv - (1.0 if b == key else 0.0)) ** 2 for b, pv in p.items())
    if not n:
        return None
    return dict(n=n, top1=t1 / n * 100, top2=t2 / n * 100, brier=brier / n)


def phase1(split):
    ds = load_ds()
    labels = load_labels()
    bins_by_unit = {u: H.W.market_bins(u) for u in ("C", "F")}
    tr = [r for r in ds if r["date"] < split]
    te = [r for r in ds if r["date"] >= split]
    print(f"PHASE 1 — day-ahead · dataset_full {len(ds)} rows · train {len(tr)} / test {len(te)}\n")

    # sigma pooled ต่อ unit จาก blend เต็ม (fit train เท่านั้น)
    bias_full = bias_fix(tr, MODELS)
    pairs_tr = blend(tr, MODELS, bias_full, {})
    err = defaultdict(list)
    for r, mu, _ in pairs_tr:
        o = H.num(r.get("obs_c"))
        if o is not None:
            err[r["unit"]].append(mu - o)
    sigma_full = {u: max(0.4, math.sqrt(sum(e * e for e in v) / len(v))) for u, v in err.items() if v}

    res = []
    # 1) แต่ละโมเดลเดี่ยว (bias fix ของตัวเอง)
    for m in MODELS:
        b = bias_fix(tr, [m])
        ptr = blend(tr, [m], b, {})
        e2 = defaultdict(list)
        for r, mu, _ in ptr:
            o = H.num(r.get("obs_c"))
            if o is not None:
                e2[r["unit"]].append(mu - o)
        sg = {u: max(0.4, math.sqrt(sum(x * x for x in v) / len(v))) for u, v in e2.items() if v}
        te_s = eval_rows(blend(te, [m], b, sg), labels, bins_by_unit)
        if te_s:
            res.append(("โมเดลเดี่ยว: " + m, 1, te_s))
    # 2) สะสมทีละโมเดล (เรียงตามคุณภาพเดี่ยว)
    single = {name.split(": ")[1]: s["top1"] for name, _, s in res}
    order = [m for m in sorted(MODELS, key=lambda m: -single.get(m, 0))]
    print("  ลำดับคุณภาพโมเดลเดี่ยว (test top-1):", ", ".join(f"{m} {single[m]:.1f}%" for m in order), "\n")
    cum = []
    for m in order:
        cum.append(m)
        b = bias_fix(tr, cum)
        ptr = blend(tr, cum, b, {})
        e2 = defaultdict(list)
        for r, mu, _ in ptr:
            o = H.num(r.get("obs_c"))
            if o is not None:
                e2[r["unit"]].append(mu - o)
        sg = {u: max(0.4, math.sqrt(sum(x * x for x in v) / len(v))) for u, v in e2.items() if v}
        te_s = eval_rows(blend(te, cum, b, sg), labels, bins_by_unit)
        res.append((f"สะสม {len(cum)} แหล่ง", len(cum), te_s))
    # 3) ไม่หัก bias (ใช้แหล่งเท่ากัน)
    for k in (1, 3, len(MODELS)):
        cum = order[:k]
        ptr = blend(tr, cum, {}, {})
        e2 = defaultdict(list)
        for r, mu, _ in ptr:
            o = H.num(r.get("obs_c"))
            if o is not None:
                e2[r["unit"]].append(mu - o)
        sg = {u: max(0.4, math.sqrt(sum(x * x for x in v) / len(v))) for u, v in e2.items() if v}
        te_s = eval_rows(blend(te, cum, {}, sg), labels, bins_by_unit)
        res.append((f"สะสม {k} แหล่ง · ไม่หัก bias", k, te_s))

    print(f"  {'การตั้งค่า':38s} {'#API':>4s} {'n':>6s} {'top-1':>7s} {'top-2':>7s} {'Brier':>7s}")
    for name, k, s in res:
        if not s:
            continue
        print(f"  {name:38s} {k:4d} {s['n']:6d} {s['top1']:6.1f}% {s['top2']:6.1f}% {s['brier']:7.3f}")

    # ── 1b) แยกชัด ๆ: โมเดลที่มีทั่วโลก (9) สะสมทีละตัว ──
    GLOBAL9 = [m for m in MODELS if m not in ("ncep_hrrr_conus", "ncep_nbm_conus")]
    print("\n  ▸ โมเดลที่มีทุกเมือง (9 แหล่ง) สะสมตามคุณภาพ:")
    cum = []
    for m in sorted(GLOBAL9, key=lambda x: -single.get(x, 0)):
        cum.append(m)
        b = bias_fix(tr, cum)
        ptr = blend(tr, cum, b, {})
        e2 = defaultdict(list)
        for r, mu, _ in ptr:
            o = H.num(r.get("obs_c"))
            if o is not None:
                e2[r["unit"]].append(mu - o)
        sg = {u: max(0.4, math.sqrt(sum(x * x for x in v) / len(v))) for u, v in e2.items() if v}
        s = eval_rows(blend(te, cum, b, sg), labels, bins_by_unit)
        if s:
            print(f"     {len(cum)} แหล่ง (+{m:22s}) n={s['n']:5d} top-1 {s['top1']:5.1f}%  top-2 {s['top2']:5.1f}%  Brier {s['brier']:.3f}")
            res.append((f"GLOBAL9 สะสม {len(cum)}", len(cum), s))

    # ── 1c) มูลค่าเพิ่มของ API ภูมิภาค (HRRR/NBM ใช้ได้เฉพาะสหรัฐ) ──
    us = [r for r in te if r["unit"] == "F"]
    print(f"\n  ▸ เมืองสหรัฐเท่านั้น (n={len(us)} test): เพิ่ม API ภูมิภาคบนฐาน 9 แหล่ง:")
    base = ",".join(GLOBAL9)
    for extra in ("(ไม่มี)", "ncep_hrrr_conus", "ncep_nbm_conus", "ncep_hrrr_conus+ncep_nbm_conus"):
        mods = GLOBAL9 + ([] if extra == "(ไม่มี)" else extra.split("+"))
        b = bias_fix(tr, mods)
        ptr = blend(tr, mods, b, {})
        e2 = defaultdict(list)
        for r, mu, _ in ptr:
            o = H.num(r.get("obs_c"))
            if o is not None:
                e2[r["unit"]].append(mu - o)
        sg = {u: max(0.4, math.sqrt(sum(x * x for x in v) / len(v))) for u, v in e2.items() if v}
        s = eval_rows(blend(us, mods, b, sg), labels, bins_by_unit)
        if s:
            print(f"     9 + {extra:34s} top-1 {s['top1']:5.1f}%  top-2 {s['top2']:5.1f}%  Brier {s['brier']:.3f}")
    return res


# ───────────────────────── Phase 2: ระหว่างวัน ชั่วโมงเร็ว ─────────────────────────
def pct(d, lim):  # ชั่วโมง ≤ lim
    return max([t for h, t in d.items() if h <= lim], default=None)


def rise_dist(ser_city, dates, upto, lookback):
    """การกระจายตัวของ (full_max − max_upto) จากวันก่อนหน้า (causal)"""
    rises, sidx = [], {d: i for i, d in enumerate(dates)}
    for d in dates:
        i = sidx[d]
        for prev in dates[max(0, i - lookback):i]:
            hp = ser_city.get(prev)
            if not hp:
                continue
            ms = pct(hp, upto)
            if ms is None or len(hp) < 8:
                continue
            rises.append(max(hp.values()) - ms)
    return rises


def p_obs(rise, ms_c, unit, bins, kern=0.28):
    """ความน่าจะเป็นต่อ bin จาก max-so-far + การกระจายของส่วนที่ยังเพิ่มได้"""
    k = kern * 9 / 5 if unit == "F" else kern
    vals = [(ms_c * 9 / 5 + 32 + r * 9 / 5) if unit == "F" else (ms_c + r) for r in rise]
    out = {}
    for lo, hi in bins:
        s = 0.0
        for v in vals:
            s += H.norm_cdf((hi + 0.5 - v) / k) - H.norm_cdf((lo - 0.5 - v) / k)
        out[(lo, hi)] = s / max(1, len(vals))
    tot = sum(out.values()) or 1.0
    return {b: v / tot for b, v in out.items()}


def logpool(ps, w):
    out = {}
    for b in ps[0][0]:
        v = 1.0
        for p, ww in ps:
            v *= max(p.get(b, 1e-9), 1e-9) ** ww
        out[b] = v
    tot = sum(out.values()) or 1.0
    return {b: v / tot for b, v in out.items()}


def phase2(split, hours=(12, 14, 16), lookback=30):
    ds = load_ds()
    labels = load_labels()
    hourly = load_hourly()
    if not hourly:
        print("PHASE 2: ยังไม่มี data/hourly_obs.csv"); return None
    bins_by_unit = {u: H.W.market_bins(u) for u in ("C", "F")}
    tr = [r for r in ds if r["date"] < split]
    bias_full = bias_fix(tr, MODELS)
    # sigma ของ day-ahead blend (train)
    e = defaultdict(list)
    for m in [0]:
        pass
    ptr = blend(tr, MODELS, bias_full, {})
    for r, mu, _ in ptr:
        o = H.num(r.get("obs_c"))
        if o is not None:
            e[r["unit"]].append(mu - o)
    sg_day = {u: max(0.4, math.sqrt(sum(x * x for x in v) / len(v))) for u, v in e.items() if v}
    # เก็บ day-ahead p ต่อ (city,date)
    by_key = {}
    for r in ds:
        vals = []
        for m in MODELS:
            v = H.num(r.get("m_" + m))
            if v is not None:
                vals.append(v - bias_full.get((m, r["unit"]), 0.0))
        if not vals:
            continue
        by_key[(r["city"], r["date"])] = H.prob_of_bins(st.mean(vals), sg_day.get(r["unit"], 1.5), r["unit"], bins_by_unit[r["unit"]])
    print(f"\nPHASE 2 — ระหว่างวัน · hourly_obs {len(hourly)} เมือง · lookback {lookback} วัน · split {split}\n")
    weights = [1.0, 0.75, 0.5, 0.25, 0.0]
    out = {}
    for h in hours:
        stats = {w: {"tr": [0, 0, 0, 0.0], "te": [0, 0, 0, 0.0]} for w in weights}
        for city, days in hourly.items():
            dates = sorted(days)
            hist_ms, hist_full = [], []
            for i, d in enumerate(dates):
                day = days[d]
                ms = pct(day, h)
                full = max(day.values()) if day else None
                ok = len(day) >= 8
                if ok and ms is not None and full is not None:
                    key = (city, d)
                    if key in labels:
                        lo, hi, unit, kk = labels[key]
                        rises = [hist_full[j] - hist_ms[j] for j in range(max(0, i - lookback), i)
                                 if hist_full[j] is not None and hist_ms[j] is not None]
                        pf = by_key.get(key)
                        if len(rises) >= 8 and pf is not None:
                            po = p_obs(rises, ms, unit, bins_by_unit[unit])
                            side = "tr" if d < split else "te"
                            for w in weights:
                                p = po if w == 1.0 else (pf if w == 0.0 else logpool([(po, w), (pf, 1 - w)], 1.0))
                                order = sorted(p.items(), key=lambda kv: -kv[1])
                                s = stats[w][side]
                                s[0] += 1
                                if order[0][0] == kk:
                                    s[1] += 1
                                if kk in [b for b, _ in order[:2]]:
                                    s[2] += 1
                                s[3] += sum((pv - (1.0 if b == kk else 0.0)) ** 2 for b, pv in p.items())
                hist_ms.append(ms)
                hist_full.append(full)
        n_tr = stats[1.0]["tr"][0]
        n_te = stats[1.0]["te"][0]
        print(f"  ── {h}:00 local (train n={n_tr} · test n={n_te}) ──")
        for w in weights:
            tag = "obs ล้วน" if w == 1.0 else ("พยากรณ์ล้วน" if w == 0.0 else f"blend obs^{w}×fc^{1-w:.2f}")
            line = f"     {tag:26s}"
            for side, lbl in (("tr", "train"), ("te", "test ")):
                n2, t1, t2, br = stats[w][side]
                line += (f" | {lbl} top-1 {t1/n2*100:5.1f}% top-2 {t2/n2*100:5.1f}% B {br/n2:.3f}" if n2 else f" | {lbl} -")
            print(line)
        out[h] = stats
    # หลักฐานประกอบ: ชั่วโมงที่ daily max เกิดจริง
    peaks = defaultdict(int)
    tot = 0
    for city, days in hourly.items():
        for d, hrs in days.items():
            if len(hrs) < 12:
                continue
            mx = max(hrs.values())
            ph = max([h for h, t in hrs.items() if t >= mx - 0.05])
            peaks[ph] += 1
            tot += 1
    print("\n  ชั่วโมงที่ daily max เกิดจริง (% ของวัน-เมือง):")
    cum = 0
    for h in range(0, 24):
        c = peaks.get(h, 0)
        if c:
            cum += c
            print(f"     {h:02d}:00  {c/tot*100:5.1f}%   (สะสม {cum/tot*100:5.1f}%)")
    return out, peaks, tot




# ───────────────────────── Phase 3: obs ความละเอียดสูง (5 นาที) vs รายชั่วโมง ─────────────────────────
def load_5min():
    """{city: {date: {hour_upto: cummax}}}"""
    ser = defaultdict(lambda: defaultdict(dict))
    p = os.path.join(HERE, "data", "obs5min_upto.csv")
    if not os.path.exists(p):
        return ser
    for r in csv.DictReader(open(p, encoding="utf-8")):
        try:
            ser[r["city"]][r["date"]][int(r["hour"])] = float(r["upto_c"])
        except ValueError:
            continue
    return ser


def phase3(split, hours=(12, 14, 16), lookback=30):
    """เทียบ: obs รายชั่วโมง (60 นาที) vs obs ละเอียด (5 นาที) — ทายถูกเร็วขึ้นไหม"""
    ds = load_ds()
    labels = load_labels()
    h1 = load_hourly()
    h5 = load_5min()
    if not h5:
        print("PHASE 3: ยังไม่มี data/obs5min_upto.csv"); return
    cities = sorted(h5)
    bins_by_unit = {u: H.W.market_bins(u) for u in ("C", "F")}
    tr = [r for r in ds if r["date"] < split]
    bf = bias_fix(tr, MODELS)
    e = defaultdict(list)
    for r, mu, _ in blend(tr, MODELS, bf, {}):
        o = H.num(r.get("obs_c"))
        if o is not None:
            e[r["unit"]].append(mu - o)
    sg_day = {u: max(0.4, math.sqrt(sum(x * x for x in v) / len(v))) for u, v in e.items() if v}
    by_key = {}
    for r in ds:
        vals = []
        for m in MODELS:
            v = H.num(r.get("m_" + m))
            if v is not None:
                vals.append(v - bf.get((m, r["unit"]), 0.0))
        if vals:
            by_key[(r["city"], r["date"])] = H.prob_of_bins(st.mean(vals), sg_day.get(r["unit"], 1.5),
                                                            r["unit"], bins_by_unit[r["unit"]])
    print(f"\nPHASE 3 — obs 5 นาที vs 60 นาที · {len(cities)} เมืองสหรัฐ · {lookback} วัน lookback\n")
    res = {}
    for h in hours:
        acc = {"1h": [0, 0, 0, 0.0], "5min": [0, 0, 0, 0.0], "5min+fc(0.5)": [0, 0, 0, 0.0], "1h+fc(0.5)": [0, 0, 0, 0.0]}
        lag, lag_big, n_lag = 0.0, 0, 0
        for city in cities:
            dates = sorted(h1.get(city, {}))
            hist1, hist5 = [], []
            for i, d in enumerate(dates):
                day1 = h1[city].get(d) or {}
                day5 = h5[city].get(d) or {}
                ms1 = pct(day1, h)
                ms5 = day5.get(h)
                full1 = max(day1.values()) if day1 else None
                full5 = day5.get(23)
                if ms1 is not None and ms5 is not None:
                    lag += (ms5 - ms1); n_lag += 1
                    if ms5 - ms1 >= 0.5:
                        lag_big += 1
                key = (city, d)
                if key in labels and ms1 is not None and ms5 is not None and full1 is not None and full5 is not None:
                    lo, hi, unit, kk = labels[key]
                    r1 = [hist1[j] for j in range(max(0, i - lookback), i) if hist1[j] is not None]
                    r5 = [hist5[j] for j in range(max(0, i - lookback), i) if hist5[j] is not None]
                    pf = by_key.get(key)
                    if len(r1) >= 8 and len(r5) >= 8 and pf:
                        p1 = p_obs(r1, ms1, unit, bins_by_unit[unit])
                        p5 = p_obs(r5, ms5, unit, bins_by_unit[unit])
                        variants = {"1h": p1, "5min": p5,
                                    "5min+fc(0.5)": logpool([(p5, 0.5), (pf, 0.5)], 1.0),
                                    "1h+fc(0.5)": logpool([(p1, 0.5), (pf, 0.5)], 1.0)}
                        for name, pp in variants.items():
                            order = sorted(pp.items(), key=lambda kv: -kv[1])
                            s = acc[name]
                            s[0] += 1
                            if order[0][0] == kk:
                                s[1] += 1
                            if kk in [b for b, _ in order[:2]]:
                                s[2] += 1
                            s[3] += sum((pv - (1.0 if b == kk else 0.0)) ** 2 for b, pv in pp.items())
                hist1.append(full1 - ms1 if (full1 is not None and ms1 is not None) else None)
                hist5.append(full5 - ms5 if (full5 is not None and ms5 is not None) else None)
        print(f"  ── {h}:00 local ──   (obs 5 นาที สูงกว่ารายชั่วโมง เฉลี่ย {lag/max(1,n_lag):+.3f}°C · "
              f"ต่าง ≥0.5°C ใน {lag_big/max(1,n_lag)*100:.1f}% ของวัน)")
        for name, s in acc.items():
            if s[0]:
                print(f"     {name:14s} n={s[0]:5d} top-1 {s[1]/s[0]*100:5.1f}%  top-2 {s[2]/s[0]*100:5.1f}%  Brier {s[3]/s[0]:.3f}")
        res[h] = acc
    return res




# ───── Phase 2b: ระหว่างวัน — เพิ่มจำนวน "แหล่งพยากรณ์" ในสูตร fusion ทำให้ทายเร็วขึ้นแม่นขึ้นไหม ─────
def phase2b(split, hours=(12, 14), lookback=30):
    ds = load_ds()
    labels = load_labels()
    hourly = load_hourly()
    if not hourly:
        return
    bins_by_unit = {u: H.W.market_bins(u) for u in ("C", "F")}
    tr = [r for r in ds if r["date"] < split]
    SUBS = {"1 (icon)": ["icon_seamless"], "3": ["icon_seamless", "best_match", "gem_seamless"],
            "9 (ทุกโมเดลทั่วโลก)": [m for m in MODELS if m not in ("ncep_hrrr_conus", "ncep_nbm_conus")],
            "11 (รวมภูมิภาค)": MODELS}
    pf_by = {}
    for name, mods in SUBS.items():
        b = bias_fix(tr, mods)
        e = defaultdict(list)
        for r, mu, _ in blend(tr, mods, b, {}):
            o = H.num(r.get("obs_c"))
            if o is not None:
                e[r["unit"]].append(mu - o)
        sg = {u: max(0.4, math.sqrt(sum(x * x for x in v) / len(v))) for u, v in e.items() if v}
        d = {}
        for r in ds:
            vals = []
            for m in mods:
                v = H.num(r.get("m_" + m))
                if v is not None:
                    vals.append(v - b.get((m, r["unit"]), 0.0))
            if vals:
                d[(r["city"], r["date"])] = H.prob_of_bins(st.mean(vals), sg.get(r["unit"], 1.5),
                                                           r["unit"], bins_by_unit[r["unit"]])
        pf_by[name] = d
    print("\nPHASE 2b — จำนวนแหล่งพยากรณ์ในสูตร fusion (obs ⊕ พยากรณ์) ช่วงเช้า\n")
    for h in hours:
        acc = defaultdict(lambda: [0, 0, 0, 0.0])
        for city, days in hourly.items():
            dates = sorted(days)
            hist_ms, hist_full = [], []
            for i, d in enumerate(dates):
                day = days[d]
                ms = pct(day, h)
                full = max(day.values()) if day else None
                key = (city, d)
                if len(day) >= 8 and ms is not None and full is not None and key in labels and d >= split:
                    lo, hi, unit, kk = labels[key]
                    rises = [hist_full[j] - hist_ms[j] for j in range(max(0, i - lookback), i)
                             if hist_full[j] is not None and hist_ms[j] is not None]
                    if len(rises) >= 8:
                        po = p_obs(rises, ms, unit, bins_by_unit[unit])
                        variants = {"obs ล้วน": po}
                        for name, dd in pf_by.items():
                            pf = dd.get(key)
                            if pf:
                                variants[f"obs^0.5 ⊕ fc {name}"] = logpool([(po, 0.5), (pf, 0.5)], 1.0)
                                variants[f"obs^0.25 ⊕ fc {name}"] = logpool([(po, 0.25), (pf, 0.75)], 1.0)
                        for name, pp in variants.items():
                            order = sorted(pp.items(), key=lambda kv: -kv[1])
                            s = acc[name]
                            s[0] += 1
                            if order[0][0] == kk:
                                s[1] += 1
                            if kk in [b for b, _ in order[:2]]:
                                s[2] += 1
                            s[3] += sum((pv - (1.0 if b == kk else 0.0)) ** 2 for b, pv in pp.items())
                hist_ms.append(ms)
                hist_full.append(full)
        print(f"  ── {h}:00 local (test) ──")
        for name, s in sorted(acc.items(), key=lambda kv: -kv[1][1] / max(1, kv[1][0])):
            if s[0]:
                print(f"     {name:26s} n={s[0]:5d} top-1 {s[1]/s[0]*100:5.1f}%  top-2 {s[2]/s[0]*100:5.1f}%  Brier {s[3]/s[0]:.3f}")




# ── Phase 3w: เพิ่ม API obs อีกแหล่ง (Wunderground = แหล่งที่ตลาดสหรัฐใช้ตัดสิน) ──
def load_wu():
    ser = defaultdict(lambda: defaultdict(dict))
    p = os.path.join(HERE, "data", "wu_upto.csv")
    if not os.path.exists(p):
        return ser
    for r in csv.DictReader(open(p, encoding="utf-8")):
        try:
            ser[r["city"]][r["date"]][int(r["hour"])] = float(r["upto_c"])
        except ValueError:
            continue
    return ser


def phase3w(split, hours=(12, 14, 16), lookback=30):
    ds, labels = load_ds(), load_labels()
    h1, wu = load_hourly(), load_wu()
    if not wu:
        print("\nPHASE 3w: ยังไม่มี data/wu_upto.csv"); return
    bins_by_unit = {u: H.W.market_bins(u) for u in ("C", "F")}
    tr = [r for r in ds if r["date"] < split]
    bf = bias_fix(tr, MODELS)
    e = defaultdict(list)
    for r, mu, _ in blend(tr, MODELS, bf, {}):
        o = H.num(r.get("obs_c"))
        if o is not None:
            e[r["unit"]].append(mu - o)
    sg_day = {u: max(0.4, math.sqrt(sum(x * x for x in v) / len(v))) for u, v in e.items() if v}
    by_key = {}
    for r in ds:
        vals = [v for v in (H.num(r.get("m_" + m)) for m in MODELS) if v is not None]
        vals = [v - 0 for v in vals]
        mu = st.mean([H.num(r.get("m_" + m)) - bf.get((m, r["unit"]), 0.0)
                      for m in MODELS if H.num(r.get("m_" + m)) is not None])
        by_key[(r["city"], r["date"])] = H.prob_of_bins(mu, sg_day.get(r["unit"], 1.5), r["unit"], bins_by_unit[r["unit"]])
    cities = [c for c in sorted(wu) if c in h1]
    print(f"\nPHASE 3w — obs แหล่งที่ 2 (WU = แหล่งตัดสินของตลาดสหรัฐ) · {len(cities)} เมือง · เดือน test เท่านั้น\n")
    for h in hours:
        acc = defaultdict(lambda: [0, 0, 0, 0.0])
        lag, big, n_lag, bdiff = 0.0, 0, 0, 0
        for city in cities:
            dates = sorted(h1[city])
            hist1, histw = [], []
            for i, d in enumerate(dates):
                day1 = h1[city].get(d) or {}
                dayw = wu[city].get(d) or {}
                ms1, msw = pct(day1, h), dayw.get(h)
                full1, fullw = (max(day1.values()) if day1 else None), dayw.get(23)
                key = (city, d)
                if key in labels and None not in (ms1, msw, full1, fullw) and d >= split:
                    lo, hi, unit, kk = labels[key]
                    lag += (msw - ms1); n_lag += 1
                    if msw - ms1 >= 0.5:
                        big += 1
                    v1 = ms1 * 9 / 5 + 32 if unit == "F" else ms1
                    vw = msw * 9 / 5 + 32 if unit == "F" else msw
                    if round(v1) != round(vw):
                        bdiff += 1
                    r1 = [hist1[j] for j in range(max(0, i - lookback), i) if hist1[j] is not None]
                    rw = [histw[j] for j in range(max(0, i - lookback), i) if histw[j] is not None]
                    pf = by_key.get(key)
                    if len(r1) >= 8 and len(rw) >= 8 and pf:
                        p1 = p_obs(r1, ms1, unit, bins_by_unit[unit])
                        pw = p_obs(rw, msw, unit, bins_by_unit[unit])
                        variants = {"IEM (รายชั่วโมง)": p1, "WU (แหล่งตัดสิน)": pw,
                                    "fusion IEM⊕WU": logpool([(p1, 0.5), (pw, 0.5)], 1.0),
                                    "IEM ⊕ พยากรณ์": logpool([(p1, 0.5), (pf, 0.5)], 1.0),
                                    "WU ⊕ พยากรณ์": logpool([(pw, 0.5), (pf, 0.5)], 1.0),
                                    "fusion3 (IEM⊕WU⊕พยากรณ์)": logpool([(p1, 0.4), (pw, 0.4), (pf, 0.2)], 1.0)}
                        for name, pp in variants.items():
                            order = sorted(pp.items(), key=lambda kv: -kv[1])
                            s = acc[name]
                            s[0] += 1
                            if order[0][0] == kk:
                                s[1] += 1
                            if kk in [b for b, _ in order[:2]]:
                                s[2] += 1
                            s[3] += sum((pv - (1.0 if b == kk else 0.0)) ** 2 for b, pv in pp.items())
                hist1.append(full1 - ms1 if None not in (full1, ms1) else None)
                histw.append(fullw - msw if None not in (fullw, msw) else None)
        print(f"  ── {h}:00 local (test, n≈{acc['IEM (รายชั่วโมง)'][0]}) ── max-so-far WU vs IEM: เฉลี่ย {lag/max(1,n_lag):+.3f}°C · ต่าง ≥0.5°C {big/max(1,n_lag)*100:.1f}% · ปัดเป็นองศาต่างกัน {bdiff/max(1,n_lag)*100:.1f}%")
        for name, s in sorted(acc.items(), key=lambda kv: -kv[1][1] / max(1, kv[1][0])):
            if s[0]:
                print(f"     {name:26s} top-1 {s[1]/s[0]*100:5.1f}%  top-2 {s[2]/s[0]*100:5.1f}%  Brier {s[3]/s[0]:.3f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="2026-08-15")
    ap.add_argument("--phase", default="1,2,3,2b,3w")
    a = ap.parse_args()
    if "1" in a.phase:
        phase1(a.split)
    if "2" in a.phase:
        phase2(a.split)
    if "3" in a.phase:
        phase3(a.split)
    if "2b" in a.phase:
        phase2b(a.split)
    if "3w" in a.phase:
        phase3w(a.split)


if __name__ == "__main__":
    main()
