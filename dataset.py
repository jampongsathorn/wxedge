#!/usr/bin/env python3
"""
dataset.py — สร้างชุดข้อมูลย้อนหลัง (features จากพยากรณ์รุ่นเก่าจริง + label = bucket ที่ถูกจริง)
ใช้ตอบคำถาม: "ทาย bucket ล่วงหน้าได้ถูกกี่ % และต้องทำยังไงให้ถูกที่สุด"

คุณสมบัติที่สำคัญ (กัน data leakage ทั้งหมด):
  - feature ของวัน d ใช้เฉพาะพยากรณ์ที่ "มีอยู่ก่อน" วัน d (historical-forecast-api = archived model runs)
  - bias ต่อโมเดล = ค่าเฉลี่ย error ของ 14 วันก่อนหน้า (rolling, ไม่รวมวันปัจจุบัน)
  - sigma = rolling RMSE ของ blend ใน 14 วันก่อนหน้า
  - label = ค่าจริงจาก IEM METAR / HKO + เทียบกับ winner bucket ของตลาดที่ resolve แล้ว (ถ้ามี)

รัน: python3 dataset.py --days 75 --out data/dataset.csv
"""
import argparse, csv, json, math, os, sys, statistics as st
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wxedge as W

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

DET_ALL = ["best_match", "ncep_nbm_conus", "ncep_hrrr_conus", "gfs_seamless", "ecmwf_ifs025",
           "ecmwf_aifs025_single", "icon_seamless", "ukmo_seamless", "jma_seamless",
           "meteofrance_seamless", "gem_seamless", "bom_access_global"]


def rolling_stats(series, obs, i, idx_of, window=14):
    """ค่าเฉลี่ย error (bias) และ RMSE ของ 14 วันก่อนหน้าจุด i (ไม่รวม i)"""
    errs = []
    for j in range(max(0, i - window), i):
        t = idx_of[j]
        if t in obs and t in series:
            errs.append(series[t] - obs[t])
    if len(errs) < 5:
        return None, None, len(errs)
    bias = st.mean(errs)
    rmse = math.sqrt(sum(e * e for e in errs) / len(errs))
    return bias, rmse, len(errs)


def build_city(cfg, days=75, model_list=None, no_cache=False):
    models = model_list or DET_ALL
    hist = W.model_history(cfg, models, days, no_cache)
    if not hist:
        return []
    tz = ZoneInfo(cfg["tz"])
    today = datetime.now(tz).date()
    d0 = today - timedelta(days=days)

    # observations (label)
    if cfg["obs"] == "hko":
        obs = W.hko_recent(days + 2, no_cache)
    else:
        obs = W.iem_daily_max(cfg, d0, today, no_cache)
    if len(obs) < 20:
        return []

    dates = sorted(set(obs) & set().union(*[set(h) for h in hist.values()]))
    if len(dates) < 20:
        return []
    rows = []
    for i, d in enumerate(dates):
        vals, wts, biases = {}, {}, {}
        for m, ser in hist.items():
            if d not in ser:
                continue
            b, r, n = rolling_stats(ser, obs, i, dates)
            vals[m] = ser[d]
            biases[m] = b
            if b is not None:
                wts[m] = W.DET_W.get(m, 0.7)
        if not vals:
            continue
        # blend = weighted mean ของ (พยากรณ์ − bias)
        num = sum(wts[m] * (vals[m] - biases.get(m, 0.0)) for m in vals if m in wts)
        den = sum(wts[m] for m in vals if m in wts)
        if den <= 0:
            continue
        mu = num / den
        # rmse ของ blend ย้อนหลัง (rolling 14 วัน)
        blend_hist, blend_obs = {}, {}
        for j in range(max(0, i - 14), i):
            td = dates[j]
            vj = {}
            for m, ser in hist.items():
                if td in ser:
                    vj[m] = ser[td]
            if not vj:
                continue
            bj = {}
            for m in vj:
                b_, r_, n_ = rolling_stats(hist[m], obs, j, dates)
                bj[m] = b_ if b_ is not None else 0.0
            blend_hist[td] = sum(W.DET_W.get(m, 0.7) * (vj[m] - bj[m]) for m in vj) / \
                             sum(W.DET_W.get(m, 0.7) for m in vj)
        errs = [blend_hist[t] - obs[t] for t in blend_hist if t in obs]
        if len(errs) >= 8:
            sigma = math.sqrt(sum(e * e for e in errs) / len(errs))
            sigma = max(sigma, 0.5)
            n_rmse = len(errs)
        else:
            sigma, n_rmse = 1.2, 0
        spread = float(np.std([vals[m] for m in vals])) if len(vals) > 1 else 0.0
        rows.append(dict(
            city=cfg["city"], date=d, unit=cfg["unit"], icao=cfg["icao"], tz=cfg["tz"],
            obs_c=obs[d], mu_c=round(mu, 3), sigma_c=round(sigma, 3), model_spread_c=round(spread, 3),
            n_models=len(vals), rmse_n=n_rmse,
            **{("m_" + m): round(vals[m], 2) for m in vals},
            **{("b_" + m): (round(biases[m], 3) if biases.get(m) is not None else "") for m in vals},
        ))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=75)
    ap.add_argument("--cities", default="all")
    ap.add_argument("--out", default=os.path.join(DATA, "dataset.csv"))
    ap.add_argument("--no-cache", action="store_true")
    a = ap.parse_args()

    cfgs = json.load(open(W.STATIONS))
    cities = list(cfgs) if a.cities == "all" else a.cities.split(",")
    allrows, fail = [], []
    for c in cities:
        if c not in cfgs:
            continue
        try:
            rows = build_city(cfgs[c], a.days, no_cache=a.no_cache)
        except Exception as e:
            fail.append((c, str(e)[:60]))
            continue
        allrows += rows
        print("  %-14s rows=%-4d %s..%s | obs=%d" % (c, len(rows),
              rows[0]["date"] if rows else "-", rows[-1]["date"] if rows else "-", len(rows)))
    if not allrows:
        print("ไม่มีข้อมูล"); return
    keys = sorted({k for r in allrows for k in r})
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in allrows:
            w.writerow(r)
    print("\nเขียน %s: %d แถว-วัน (%d เมือง)" % (a.out, len(allrows), len(set(r["city"] for r in allrows))))
    if fail:
        print("ล้มเหลว:", fail[:8])


if __name__ == "__main__":
    main()
