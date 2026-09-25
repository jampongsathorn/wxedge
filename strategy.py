#!/usr/bin/env python3
"""
strategy.py — หา "กลยุทธ์ทาย bucket" ที่ให้ hit rate สูงสุด จาก dataset.csv
และวัดคุณภาพความน่าจะเป็น (reliability/Brier) + เทียบ baseline
"""
import argparse, csv, json, math, os, statistics as st, sys
from collections import defaultdict, Counter

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wxedge as W
from scipy.stats import norm


def load(path):
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    for r in rows:
        for k in ("obs_c", "mu_c", "sigma_c", "model_spread_c"):
            r[k] = float(r[k])
        r["n_models"] = int(r["n_models"])
    return rows


def buckets_for(unit):
    """ใช้ bin มาตรฐานจาก wxedge (F = 2° ผูกเลขคู่ / C = 1°)"""
    return W.market_bins(unit)


def label_of(row):
    return W.bucket_label(row["obs_c"], row["unit"])


def probs_for(row, sigma_scale=1.0, bias_shift=0.0):
    return W.prob_dist(row["mu_c"] + bias_shift, row["sigma_c"] * sigma_scale, row["unit"])


def fmt(b, unit="F"):
    return W.label_str(b, unit)


def evaluate(rows, sigma_scale=1.0, bias_shift=0.0, verbose=False):
    top1 = top2 = pm1 = 0
    briers, p_actual, n = [], [], 0
    per_city = defaultdict(lambda: dict(n=0, top1=0, top2=0, lo=0.0, psum=0.0))
    for r in rows:
        p = probs_for(r, sigma_scale, bias_shift)
        order = sorted(p.items(), key=lambda kv: -kv[1])
        lab = label_of(r)
        n += 1
        hit1 = order[0][0] == lab
        hit2 = lab in [k for k, _ in order[:2]]
        idx = [k for k, _ in order].index(lab) if lab in p else 99
        top1 += hit1
        top2 += hit2
        pm1 += (idx <= 1)
        p_actual.append(p.get(lab, 0.0))
        briers.append(sum((pv - (1.0 if k == lab else 0.0)) ** 2 for k, pv in p.items()))
        c = per_city[r["city"]]
        c["n"] += 1; c["top1"] += hit1; c["top2"] += hit2
        c["lo"] += idx; c["psum"] += p.get(lab, 0.0)
        if verbose and not hit1:
            print("    %-12s %s  model=%s (%.0f%%) | actual=%s" % (
                r["city"], r["date"], fmt(order[0][0], r["unit"]), order[0][1] * 100,
                fmt(lab, r["unit"])))
    return dict(n=n, top1=top1 / max(n, 1), top2=top2 / max(n, 1), pm1=pm1 / max(n, 1),
                p_actual=st.mean(p_actual) if p_actual else 0,
                brier=st.mean(briers) if briers else 1, per_city=per_city)


def baselines(rows):
    """persistence: bucket ของเมื่อวาน / climatology: bucket ที่พบบ่อยสุดของ 21 วันก่อนหน้า"""
    by_city = defaultdict(list)
    for r in rows:
        by_city[r["city"]].append(r)
    res = dict(n=0, pers=0, clim=0, clim1=0)
    for city, rs in by_city.items():
        rs.sort(key=lambda r: r["date"])
        for i, r in enumerate(rs):
            if i < 21:
                continue
            lab = label_of(r)
            hist = [label_of(x) for x in rs[max(0, i - 21):i]]
            cnt = Counter(hist)
            mode, mc = cnt.most_common(1)[0]
            pers = label_of(rs[i - 1])
            res["n"] += 1
            res["pers"] += (pers == lab)
            res["clim"] += (mode == lab)
            res["clim1"] += (mode == lab or cnt.most_common(2)[-1][0] == lab)
    n = max(res["n"], 1)
    return dict(n=res["n"], pers=res["pers"] / n, clim=res["clim"] / n, clim1=res["clim1"] / n)


def tune(rows, train_frac=0.65):
    by_city = defaultdict(list)
    for r in rows:
        by_city[r["city"]].append(r)
    train, test = [], []
    for city, rs in by_city.items():
        rs.sort(key=lambda r: r["date"])
        k = int(len(rs) * train_frac)
        train += rs[:k]; test += rs[k:]
    best = None
    for ss in np.arange(0.5, 2.01, 0.05):
        for bs in np.arange(-0.6, 0.61, 0.1):
            m = evaluate(train, ss, bs)
            score = m["top1"]
            if best is None or score > best[0]:
                best = (score, ss, bs)
    return best, train, test


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(HERE, "data", "dataset.csv"))
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--tune", action="store_true")
    a = ap.parse_args()
    rows = load(a.data)
    print("dataset: %d แถว-วัน | %d เมือง | %s..%s" % (
        len(rows), len(set(r["city"] for r in rows)),
        min(r["date"] for r in rows), max(r["date"] for r in rows)))

    print("\n=== 1) ค่าเริ่มต้น (sigma_scale=1.0, bias_shift=0) ===")
    m = evaluate(rows, 1.0, 0.0, a.verbose)
    bl = baselines(rows)
    print("  top-1 bucket ถูก:      %.1f%%   (n=%d)" % (m["top1"] * 100, m["n"]))
    print("  top-2 bucket ครอบ:     %.1f%%" % (m["top2"] * 100))
    print("  ±1 bucket ครอบ:        %.1f%%" % (m["pm1"] * 100))
    print("  p(จริง) เฉลี่ย:        %.1f%%  |  Brier: %.3f" % (m["p_actual"] * 100, m["brier"]))
    print("  --- baseline ---")
    print("  persistence (bucket เมื่อวาน): %.1f%%" % (bl["pers"] * 100))
    print("  climatology (mode 21 วัน):     %.1f%%  (top-2: %.1f%%)" % (bl["clim"] * 100, bl["clim1"] * 100))

    print("\n=== 2) แยกตามเมือง (top-1 / top-2 / เฉลี่ยอันดับที่ถูก) ===")
    for c, v in sorted(m["per_city"].items(), key=lambda kv: -kv[1]["top1"] / max(kv[1]["n"], 1)):
        print("  %-14s n=%-3d top1=%5.1f%%  top2=%5.1f%%  mean_rank=%.2f  p(จริง)=%.1f%%" % (
            c, v["n"], 100 * v["top1"] / max(v["n"], 1), 100 * v["top2"] / max(v["n"], 1),
            v["lo"] / max(v["n"], 1), 100 * v["psum"] / max(v["n"], 1)))

    if a.tune:
        print("\n=== 3) จูน sigma/scale บน train (65%%) แล้ววัดบน test (35%%) ===")
        (score, ss, bs), train, test = tune(rows)
        print("  ค่าที่ดีสุดบน train: sigma_scale=%.2f bias_shift=%+.1f°C (top1 train=%.1f%%)" % (
            ss, bs, score * 100))
        tr = evaluate(train, ss, bs); te = evaluate(test, ss, bs)
        print("  train top1=%.1f%% | test top1=%.1f%% (n_test=%d)" % (
            tr["top1"] * 100, te["top1"] * 100, te["n"]))
        base_te = evaluate(test, 1.0, 0.0)
        print("  เทียบ test ด้วยค่าดิบ: top1=%.1f%%" % (base_te["top1"] * 100))
        print("  test: top2=%.1f%% | p(จริง)=%.1f%% | Brier=%.3f" % (
            te["top2"] * 100, te["p_actual"] * 100, te["brier"]))


if __name__ == "__main__":
    main()
