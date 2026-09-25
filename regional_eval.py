#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
regional_eval.py — ทดลองไอเดีย @AlterEgo_eth: "ใส่โมเดลภูมิภาคความละเอียดสูงต่อภูมิภาค" ช่วยจริงไหม
  โมเดลภูมิภาคที่ทดสอบ: icon_eu · icon_d2 · meteofrance_arome_france_hd · ukmo_uk_deterministic_2km · jma_msm
  ฐานเปรียบเทียบ = 9 โมเดลทั่วโลกใน data/dataset_full.csv (bias-corrected, fit บน train เท่านั้น)
  วัด: top-1 / top-2 / Brier เทียบ bucket ที่ตลาด resolve จริง (test ≥ --split)
รัน: python3 regional_eval.py --split 2026-08-15        (fetch ครั้งแรก ~1-2 นาที แล้ว cache)
"""
import argparse, csv, json, math, os, statistics as st, time, urllib.parse, urllib.request
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
import headtohead as H

CACHE = os.path.join(HERE, "data", "regional_models.json")
EU_CITIES = ["london", "paris", "madrid", "milan", "amsterdam", "munich", "warsaw", "helsinki"]
ASIA_CITIES = ["tokyo", "seoul", "beijing", "shanghai", "taipei", "hong-kong"]
REGIONAL = {
    "icon_eu": EU_CITIES,
    "icon_d2": ["munich", "warsaw", "amsterdam", "milan", "paris"],
    "meteofrance_arome_france_hd": ["paris", "madrid", "london", "milan"],
    "ukmo_uk_deterministic_2km": ["london"],
    "jma_msm": ["tokyo", "seoul", "taipei"],
    "jma_gsm": ASIA_CITIES,
}
GLOBAL9 = ["ecmwf_ifs025", "gfs_seamless", "icon_seamless", "gem_seamless", "meteofrance_seamless",
           "jma_seamless", "ukmo_seamless", "ecmwf_aifs025_single", "best_match"]


def fetch_one(cfg, models, past_days=92):
    url = ("https://historical-forecast-api.open-meteo.com/v1/forecast?latitude=%.4f&longitude=%.4f"
           "&daily=temperature_2m_max&models=%s&past_days=%d&forecast_days=0&timezone=%s"
           % (cfg["lat"], cfg["lon"], ",".join(models), past_days, urllib.parse.quote(cfg["tz"])))
    req = urllib.request.Request(url, headers={"User-Agent": "wxedge-study/1.0"})
    d = json.loads(urllib.request.urlopen(req, timeout=60).read().decode())
    daily = d.get("daily", {})
    out = {}
    for k, v in daily.items():
        if k == "time":
            continue
        m = k.replace("temperature_2m_max_", "")
        out[m] = {t: x for t, x in zip(daily["time"], v) if x is not None}
    return out


def build_cache(cities=None):
    cfgs = json.load(open(os.path.join(HERE, "stations.json"), encoding="utf-8"))
    data = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {}
    todo = defaultdict(list)
    for m, cs in REGIONAL.items():
        for c in cs:
            todo[c].append(m)
    for c, models in todo.items():
        if cities and c not in cities:
            continue
        have = set(data.get(c, {}))
        need = [m for m in models if m not in have]
        if not need:
            continue
        try:
            got = fetch_one(cfgs[c], need)
        except Exception as e:
            print(f"  {c}: ERR {e}", flush=True)
            continue
        data.setdefault(c, {}).update(got)
        print(f"  {c}: +{len(got)} models → {list(got)}", flush=True)
        time.sleep(0.6)
    json.dump(data, open(CACHE, "w", encoding="utf-8"))
    return data


def load_labels():
    return {(r["city"], r["date"]): (float(r["lo"]), float(r["hi"]), r["unit"])
            for r in csv.DictReader(open(os.path.join(HERE, "data", "market_labels.csv"), encoding="utf-8"))}


def load_ds():
    return list(csv.DictReader(open(os.path.join(HERE, "data", "dataset_full.csv"), encoding="utf-8")))


def eval_rows(values_by_key, rows, labels, bins_by_unit, models, bias, sigma):
    """values_by_key[(city,date)][model] = ค่า °C · rows = แถว dataset (city, date, unit)"""
    n = t1 = t2 = 0
    brier = 0.0
    for r in rows:
        key = (r["city"], r["date"])
        if key not in labels or key not in values_by_key:
            continue
        vals = [values_by_key[key][m] - bias.get((m, r["unit"]), 0.0)
                for m in models if m in values_by_key[key]]
        if not vals:
            continue
        lo, hi, unit = labels[key]
        p = H.prob_of_bins(st.mean(vals), sigma.get(unit, 1.5), unit, bins_by_unit[unit])
        order = sorted(p.items(), key=lambda kv: -kv[1])
        n += 1
        if order[0][0] == (lo, hi):
            t1 += 1
        if (lo, hi) in [b for b, _ in order[:2]]:
            t2 += 1
        brier += sum((pv - (1.0 if b == (lo, hi) else 0.0)) ** 2 for b, pv in p.items())
    return dict(n=n, top1=t1 / n * 100 if n else 0, top2=t2 / n * 100 if n else 0, brier=brier / n if n else 0)


def bias_of(values_by_key, rows, models):
    acc = defaultdict(list)
    for r in rows:
        key = (r["city"], r["date"])
        if key not in values_by_key:
            continue
        o = H.num(r.get("obs_c"))
        if o is None:
            continue
        for m in models:
            if m in values_by_key[key]:
                acc[(m, r["unit"])].append(values_by_key[key][m] - o)
    return {k: st.mean(v) for k, v in acc.items() if v}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="2026-08-15")
    ap.add_argument("--no-fetch", action="store_true")
    a = ap.parse_args()
    data = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {}
    if not a.no_fetch:
        data = build_cache()
    labels = load_labels()
    rows_all = load_ds()
    bins_by_unit = {u: H.W.market_bins(u) for u in ("C", "F")}
    reg_models = sorted({m for v in data.values() for m in v if m != "temperature_2m_max"})
    print(f"\nregional cache: {len(data)} เมือง · โมเดลภูมิภาค: {reg_models}\n")

    # ค่าของ 9 โมเดลทั่วโลก จัดกลุ่มตาม (city,date)
    glob = defaultdict(dict)
    for r in rows_all:
        for m in GLOBAL9:
            v = H.num(r.get("m_" + m))
            if v is not None:
                glob[(r["city"], r["date"])][m] = v
    # ค่าของโมเดลภูมิภาค (cache เป็น °C อยู่แล้ว)
    reg = defaultdict(dict)
    for c, mods in data.items():
        for m, ser in mods.items():
            if m == "temperature_2m_max":
                continue
            for d, v in ser.items():
                if v is not None:
                    reg[(c, d)][m] = v

    panels = {"ยุโรป (8 เมือง)": [c for c in EU_CITIES if c in data],
              "เอเชีย (tokyo/seoul/taipei)": [c for c in ("tokyo", "seoul", "taipei") if c in data]}
    out = []
    for pname, cities in panels.items():
        cset = set(cities)
        tr = [r for r in rows_all if r["date"] < a.split and r["city"] in cset]
        te = [r for r in rows_all if r["date"] >= a.split and r["city"] in cset]
        print(f"── {pname} · train n={len(tr)} · test n={len(te)} ──")
        bias0 = bias_of(glob, tr, GLOBAL9)
        # sigma จาก blend 9 โมเดล (train)
        err = defaultdict(list)
        for r in tr:
            key = (r["city"], r["date"])
            if key not in glob:
                continue
            o = H.num(r.get("obs_c"))
            if o is None:
                continue
            vals = [glob[key][m] - bias0.get((m, r["unit"]), 0.0) for m in GLOBAL9 if m in glob[key]]
            if vals:
                err[r["unit"]].append(st.mean(vals) - o)
        sigma = {u: max(0.4, math.sqrt(sum(e * e for e in v) / len(v))) for u, v in err.items() if v}
        s0 = eval_rows(glob, te, labels, bins_by_unit, GLOBAL9, bias0, sigma)
        print(f"   ฐาน 9 โมเดลทั่วโลก            n={s0['n']:4d} top-1 {s0['top1']:5.1f}%  top-2 {s0['top2']:5.1f}%  Brier {s0['brier']:.3f}")
        out.append((pname, "ฐาน 9 โมเดลทั่วโลก", s0))
        have = sorted({m for c in cities for m in data.get(c, {}) if m != "temperature_2m_max"})
        for m in have:
            merged = defaultdict(dict)
            for k, v in glob.items():
                merged[k].update(v)
            for k, v in reg.items():
                if k[0] in cset and m in v:
                    merged[k][m] = v[m]
            rows_m = [r for r in te if m in reg.get((r["city"], r["date"]), {})]
            if len(rows_m) < 30:
                continue
            b2 = bias_of(merged, tr, GLOBAL9 + [m])
            s1 = eval_rows(merged, rows_m, labels, bins_by_unit, GLOBAL9 + [m], b2, sigma)
            s0s = eval_rows(glob, rows_m, labels, bins_by_unit, GLOBAL9, bias0, sigma)
            print(f"   + {m:28s} n={s1['n']:4d} top-1 {s1['top1']:5.1f}%  top-2 {s1['top2']:5.1f}%  "
                  f"Brier {s1['brier']:.3f}  (ฐานชุดเดียวกัน {s0s['top1']:5.1f}% → Δ {s1['top1']-s0s['top1']:+.1f})")
            out.append((pname, "+" + m, s1))
        merged = defaultdict(dict)
        for k, v in glob.items():
            merged[k].update(v)
        for k, v in reg.items():
            if k[0] in cset:
                merged[k].update(v)
        rows_all_m = [r for r in te if all(mm in reg.get((r["city"], r["date"]), {}) for mm in have)]
        if rows_all_m:
            b3 = bias_of(merged, tr, GLOBAL9 + have)
            s2 = eval_rows(merged, rows_all_m, labels, bins_by_unit, GLOBAL9 + have, b3, sigma)
            s0s = eval_rows(glob, rows_all_m, labels, bins_by_unit, GLOBAL9, bias0, sigma)
            print(f"   + ภูมิภาคทั้งหมด ({len(have)})        n={s2['n']:4d} top-1 {s2['top1']:5.1f}%  top-2 {s2['top2']:5.1f}%  "
                  f"Brier {s2['brier']:.3f}  (ฐานชุดเดียวกัน {s0s['top1']:5.1f}% → Δ {s2['top1']-s0s['top1']:+.1f})")
            out.append((pname, "ภูมิภาคทั้งหมด", s2))
    with open(os.path.join(HERE, "reports", "regional_models.md"), "w", encoding="utf-8") as f:
        f.write(f"# ทดลอง: โมเดลภูมิภาคความละเอียดสูง (@AlterEgo_eth idea)\n\n"
                f"> `regional_eval.py --split {a.split}` · ฐาน = 9 โมเดลทั่วโลก (bias fit บน train เท่านั้น) · วัดกับ bucket ที่ตลาด resolve จริง\n\n"
                "| กลุ่มเมือง | การตั้งค่า | n | top-1 | top-2 | Brier |\n|---|---|---|---|---|---|\n")
        for pname, name, s in out:
            f.write(f"| {pname} | {name} | {s['n']} | {s['top1']:.1f}% | {s['top2']:.1f}% | {s['brier']:.3f} |\n")
    print("\nเขียน reports/regional_models.md")


if __name__ == "__main__":
    main()
