#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ml_boost.py — ทดลองไอเดียสุดท้ายที่ยังไม่ได้ตรวจ: "เอา ML (XGBoost) มาแทนสูตรของเรา"
เทียบแบบ head-to-head บนข้อมูลเดียวกัน กับกฎที่ทดสอบแล้วใน reports/strategy_full.md

  E1) day-ahead : ทำนาย bin ที่ตลาดจะ resolve  — XGBoost vs Gaussian(median ของโมเดล)
  E2) 16:00     : ทำนาย max สุดท้ายของวัน     — XGBoost vs วิธี empirical increment ของเรา
  แล้วเอา E2 ไปทดสอบเงินจริง: กฎ YES (argmax · p≥0.90 · ราคา 0.02–0.25 @16:00) เทียบ win rate/ROI

รัน: python3 ml_boost.py --split 2026-08-15        # → reports/ml_boost.md
"""
import argparse, csv, json, math, os, statistics as st, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import headtohead as H
import api_value as AV

MD = os.path.join(HERE, "reports", "ml_boost.md")
DS = os.path.join(HERE, "data", "dataset_full.csv")
HOURLY = os.path.join(HERE, "data", "hourly_obs.csv")
BETS = os.path.join(HERE, "data", "cheap_bets.csv")
MODELS = AV.MODELS


def make_regressor():
    """XGBoost ถ้ามี ไม่งั้น fallback เป็น sklearn HistGradientBoosting (ตระกูล gradient boosting เดียวกัน)"""
    try:
        import xgboost as xgb
        return ("xgboost " + xgb.__version__,
                xgb.XGBRegressor(n_estimators=500, max_depth=4, learning_rate=0.05,
                                 subsample=0.8, colsample_bytree=0.8, reg_lambda=1.0,
                                 random_state=7, n_jobs=2))
    except Exception:
        from sklearn.ensemble import HistGradientBoostingRegressor
        return ("sklearn HistGradientBoosting (fallback)",
                HistGradientBoostingRegressor(max_iter=400, learning_rate=0.05, max_depth=4,
                                              l2_regularization=1.0, random_state=7))


def load_rows():
    return AV.load_ds()


def load_hourly():
    ser = defaultdict(lambda: defaultdict(dict))
    for r in csv.DictReader(open(HOURLY, encoding="utf-8")):
        try:
            ser[r["city"]][r["date"]][int(r["hour"])] = float(r["tmpc"])
        except ValueError:
            continue
    return ser


def load_bets():
    out = []
    for r in csv.DictReader(open(BETS, encoding="utf-8")):
        try:
            out.append(dict(city=r["city"], date=r["date"], hour=int(r["hour"]), bin=r["bin"],
                            unit=r["unit"], p=float(r["model_p"]), price=float(r["price"]), won=int(r["won"])))
        except (KeyError, ValueError):
            continue
    return out


def num(x):
    return H.num(x)


def model_vals(r, bias):
    """ค่าของทุกโมเดลที่หัก bias รายโมเดลแล้ว (หน่วยของตลาด)"""
    out = []
    for m in MODELS:
        v = num(r.get("m_" + m))
        if v is not None:
            out.append(v - bias.get((m, r["unit"]), 0.0))
    return out


def build_features(rows, hourly, bias, city_stats, hour=None):
    """คืน (X, meta) — X เป็นลิสต์ dict ต่อแถว; ถ้า hour ให้ใช้โหมด 16:00 (มี obs + increment stats)"""
    X, meta = [], []
    for r in rows:
        c, d = r["city"], r["date"]
        unit = r["unit"]
        vals = model_vals(r, bias)
        if len(vals) < 3:
            continue
        med = st.median(vals)
        spread = num(r.get("model_spread_c")) or st.pstdev(vals) if len(vals) > 1 else 0.0
        cs = city_stats.get(c, {})
        f = dict(unit=0.0 if unit == "C" else 1.0,
                 m_med=med, m_mean=st.mean(vals), m_std=st.pstdev(vals) if len(vals) > 1 else 0.0,
                 m_spread=spread or 0.0, m_min=min(vals), m_max=max(vals), n_models=float(len(vals)),
                 city_bias=cs.get("bias", 0.0), city_sigma=cs.get("sigma", 1.0),
                 doy=float(int(d[5:7]) * 30 + int(d[8:10])))
        if hour is not None:
            day = hourly.get(c, {}).get(d) or {}
            past = [v for h, v in day.items() if h <= hour]
            if len(past) < 3:
                continue
            mx = max(past)
            f["obs_max"] = mx
            f["obs_n"] = float(len(past))
            f["obs_last"] = day.get(hour, past[-1])
            f["inc_mean"] = cs.get("inc_mean", 1.0)
            f["inc_sd"] = cs.get("inc_sd", 1.0)
            f["obs_minus_med"] = mx - med
        X.append(f)
        meta.append(r)
    return X, meta


def eval_probs(probs, labels):
    n = t1 = t2 = 0
    brier = 0.0
    for p, key in probs:
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
    rows = load_rows()
    hourly = load_hourly()
    labels = AV.load_labels()
    bins_by_unit = {u: H.W.market_bins(u) for u in ("C", "F")}
    tr = [r for r in rows if r["date"] < a.split]
    te = [r for r in rows if r["date"] >= a.split]

    bias = AV.bias_fix(tr, MODELS)
    # สถิติรายเมือง (fit บน train เท่านั้น): bias ของ median blend + sigma + การเพิ่มขึ้นของอุณหภูมิหลัง 16:00
    city_stats = {}
    acc = defaultdict(list)
    inc = defaultdict(list)
    for r in tr:
        vals = model_vals(r, bias)
        o = num(r.get("obs_c"))
        if len(vals) < 3:
            continue
        med = st.median(vals)
        oo = o * 9 / 5 + 32 if False else o
        if o is not None:
            acc[r["city"]].append(med - o)
        day = hourly.get(r["city"], {}).get(r["date"]) or {}
        past = [v for h, v in day.items() if h <= 16]
        if len(past) >= 3 and len(day) >= 8:
            inc[r["city"]].append(max(day.values()) - max(past))
    for c in {r["city"] for r in rows}:
        v = acc.get(c, [])
        i = inc.get(c, [])
        city_stats[c] = dict(bias=st.mean(v) if v else 0.0,
                             sigma=math.sqrt(sum(x * x for x in v) / len(v)) if len(v) > 1 else 1.2,
                             inc_mean=st.mean(i) if i else 1.0,
                             inc_sd=math.sqrt(sum(x * x for x in i) / len(i)) if len(i) > 1 else 0.8,
                             inc=list(i))
    name, reg = make_regressor()
    print(f"โมเดล ML: {name} · train={len(tr)} test={len(te)}")

    out = []
    # ───────── E1: day-ahead ─────────
    Xtr, mtr = build_features(tr, hourly, bias, city_stats)
    Xte, mte = build_features(te, hourly, bias, city_stats)
    ytr = [num(r.get("obs_c")) for r in mtr]
    keep = [i for i, y in enumerate(ytr) if y is not None]
    Xtr = [Xtr[i] for i in keep]
    mtr = [mtr[i] for i in keep]
    ytr = [ytr[i] for i in keep]
    feats = sorted({k for f in Xtr for k in f})
    to_mat = lambda X: [[f.get(k, 0.0) for k in feats] for f in X]
    reg.fit(to_mat(Xtr), ytr)
    pred_tr = reg.predict(to_mat(Xtr))
    res_tr = [p - y for p, y in zip(pred_tr, ytr)]
    sig = {}
    for r, e in zip(mtr, res_tr):
        sig.setdefault(r["unit"], []).append(e)
    sigma_ml = {u: max(0.4, math.sqrt(sum(e * e for e in v) / len(v))) for u, v in sig.items()}
    pred_te = reg.predict(to_mat(Xte))
    ml_probs, base_probs = [], []
    for r, f, mu in zip(mte, Xte, pred_te):
        k = (r["city"], r["date"])
        if k not in labels:
            continue
        lo, hi, unit, key = labels[k]
        unit = r["unit"]
        if unit != labels[k][2]:
            unit = labels[k][2]
        ml_probs.append((H.prob_of_bins(mu, sigma_ml.get(unit, 1.5), unit, bins_by_unit[unit]), key))
        base_probs.append((H.prob_of_bins(f["m_med"], sigma_ml.get(unit, 1.5), unit, bins_by_unit[unit]), key))
    e1_ml, e1_base = eval_probs(ml_probs, labels), eval_probs(base_probs, labels)
    print(f"\nE1 day-ahead   ML  : n={e1_ml['n']} top-1 {e1_ml['top1']:.1f}%  top-2 {e1_ml['top2']:.1f}%  Brier {e1_ml['brier']:.3f}")
    print(f"E1 day-ahead   ฐาน: n={e1_base['n']} top-1 {e1_base['top1']:.1f}%  top-2 {e1_base['top2']:.1f}%  Brier {e1_base['brier']:.3f}")

    # ───────── E2: 16:00 (เป้าหมายคือ max สุดท้ายของวัน) ─────────
    # เฉลย = max ของวันจริง (จาก hourly) เพื่อให้เทียบกับวิธี empirical ได้ตรง ๆ
    def truth_max(r):
        day = hourly.get(r["city"], {}).get(r["date"]) or {}
        return max(day.values()) if len(day) >= 6 else None

    X2tr, m2tr = build_features(tr, hourly, bias, city_stats, hour=16)
    y2tr, k2 = [], []
    for r in m2tr:
        t = truth_max(r)
        if t is not None:
            y2tr.append(t)
            k2.append(r)
    X2tr = [X2tr[i] for i, r in enumerate(m2tr) if truth_max(r) is not None]
    m2tr = k2
    # สำคัญ: ต้องใช้ชุดฟีเจอร์ของ E2 เอง (มี obs_max/increment) ไม่ใช่ชุดของ E1
    feats2 = sorted({k for f in X2tr for k in f})
    to_mat2 = lambda X: [[f.get(k, 0.0) for k in feats2] for f in X]
    reg2 = make_regressor()[1]
    reg2.fit(to_mat2(X2tr), y2tr)
    pr2_tr = reg2.predict(to_mat2(X2tr))
    sig2 = {}
    for r, p, y in zip(m2tr, pr2_tr, y2tr):
        sig2.setdefault(r["unit"], []).append(p - y)
    sigma2 = {u: max(0.3, math.sqrt(sum(e * e for e in v) / len(v))) for u, v in sig2.items()}
    X2te, m2te = build_features(te, hourly, bias, city_stats, hour=16)
    pred2 = reg2.predict(to_mat2(X2te))
    ml2, base2 = [], []
    for r, f, mu in zip(m2te, X2te, pred2):
        k = (r["city"], r["date"])
        t = truth_max(r)
        if t is None or k not in labels:
            continue
        unit, key = r["unit"], labels[k][3]
        ml2.append((H.prob_of_bins(mu, sigma2.get(unit, 1.0), unit, bins_by_unit[unit]), key))
        base2.append((H.prob_of_bins(f["obs_max"], sigma2.get(unit, 1.0), unit, bins_by_unit[unit]), key))
    e2_ml, e2_base = eval_probs(ml2, labels), eval_probs(base2, labels)
    print(f"\nE2 16:00 (max) ML  : n={e2_ml['n']} top-1 {e2_ml['top1']:.1f}%  top-2 {e2_ml['top2']:.1f}%  Brier {e2_ml['brier']:.3f}")
    print(f"E2 16:00 (max) ฐาน: n={e2_base['n']} top-1 {e2_base['top1']:.1f}%  top-2 {e2_base['top2']:.1f}%  Brier {e2_base['brier']:.3f}")

    # ───────── E2-เงิน: ใช้ p จาก ML กับกฎ YES @16:00 เทียบกับ p เดิมของเรา ─────────
    from collections import defaultdict as dd
    bets_te = [b for b in load_bets() if b["date"] >= a.split and b["hour"] == 16]
    pred_map = {}
    for r, f, mu in zip(m2te, X2te, pred2):
        pred_map[(r["city"], r["date"])] = (mu, r["unit"])
    # จักรวาลเมือง (obs ตรงกับ bin ที่ตลาดตัดสิน ≥90% บน train)
    acc_c = defaultdict(lambda: [0, 0])
    for r in tr:
        k = (r["city"], r["date"])
        o = num(r.get("obs_c"))
        if k not in labels or o is None:
            continue
        lo, hi, unit, _ = labels[k]
        oo = o * 9 / 5 + 32 if unit == "F" else o
        acc_c[r["city"]][1] += 1
        if lo - 0.5 <= oo <= hi + 0.5:
            acc_c[r["city"]][0] += 1
    univ = {c for c, (k_, n_) in acc_c.items() if n_ >= 10 and k_ / n_ >= 0.90}

    def prob_index(col):
        """col[(city,date)] = {(lo,hi): p} — รับได้ทั้งคีย์แบบ (lo,hi) และแบบ label"""
        idx = {}
        for k, pr in col.items():
            d = {}
            for kk, v in pr.items():
                if isinstance(kk, tuple):
                    a_, b_ = kk
                    key = (a_ if a_ == float("-inf") else int(a_), b_ if b_ == float("inf") else int(b_))
                    d[key] = v
                else:
                    b = H.W.parse_bin(kk)
                    if b:
                        d[(b[0], b[1])] = v
            idx[k] = d
        return idx

    def money(idx, tag, pmin=0.90):
        byday = dd(list)
        for b in bets_te:
            byday[(b["city"], b["date"])].append(b)
        n = w = 0
        cost = 0.0
        for k, bs in byday.items():
            if k not in labels or k[0] not in univ:
                continue
            cand = [b for b in bs if 0.02 <= b["price"] <= 0.25]
            pr = idx.get(k) or {}
            if not cand or not pr:
                continue
            best = None
            for b in cand:
                bb = H.W.parse_bin(b["bin"])
                if not bb:
                    continue
                key = (bb[0] if bb[0] == float("-inf") else int(bb[0]),
                       bb[1] if bb[1] == float("inf") else int(bb[1]))
                pv = pr.get(key, 0.0)
                if best is None or pv > best[1]:
                    best = (b, pv)
            if best is None or best[1] < pmin:
                continue
            b = best[0]
            n += 1
            w += b["won"]
            cost += b["price"]
        if not n:
            print(f"\nE2-เงิน {tag}: ไม่มีไม้ (n=0)")
            return dict(n=0, win=0.0, avg=0.0, roi=0.0)
        wr = w / n * 100
        avg = cost / n
        roi = (w / n - avg) / avg * 100
        print(f"\nE2-เงิน {tag}: n={n} ชนะ {wr:.1f}% ราคาเฉลี่ย {avg:.3f} ROI {roi:+.1f}%")
        return dict(n=n, win=wr, avg=avg, roi=roi)

    # p ของเรา = ค่า model_p ที่บันทึกไว้ใน cheap_bets (วิธี empirical increment) → คีย์เป็น (lo,hi)
    ours_col = dd(dict)
    for b in bets_te:
        bb = H.W.parse_bin(b["bin"])
        if bb:
            key = (bb[0] if bb[0] == float("-inf") else int(bb[0]),
                   bb[1] if bb[1] == float("inf") else int(bb[1]))
            ours_col[(b["city"], b["date"])][key] = max(
                ours_col[(b["city"], b["date"])].get(key, 0.0), b["p"])
    ml_col = {}
    for r, f, mu in zip(m2te, X2te, pred2):
        k = (r["city"], r["date"])
        if k not in labels:
            continue
        unit = labels[k][2]
        ml_col[k] = H.prob_of_bins(mu, sigma2.get(unit, 1.0), unit, bins_by_unit[unit])
    base_col = {}
    for r, f, mu in zip(m2te, X2te, pred2):
        k = (r["city"], r["date"])
        if k in labels:
            base_col[k] = H.prob_of_bins(f["obs_max"], sigma2.get(labels[k][2], 1.0), labels[k][2],
                                         bins_by_unit[labels[k][2]])

    results = {}
    for tag, col in (("XGBoost (μML → Gaussian)", ml_col), ("max-so-far ตรง ๆ (ฐาน 16:00)", base_col),
                     ("วิธีเรา empirical increment", ours_col)):
        idx = prob_index(col)
        results[tag] = dict(gate=money(idx, tag + " · ประตู p≥0.90", 0.90),
                            argmax=money(idx, tag + " · argmax ล้วน (ไม่ใช้ประตู p)", 0.0))

    # ───────── E3) hybrid: ใช้ "ศูนย์กลาง" จาก ML แต่คง spread แบบ empirical ของเรา ─────────
    def train_hour(hour):
        Xa, ma = build_features(tr, hourly, bias, city_stats, hour=hour)
        Xb, mb = build_features(te, hourly, bias, city_stats, hour=hour)
        yy, keep2 = [], []
        for i, r in enumerate(ma):
            day = hourly.get(r["city"], {}).get(r["date"]) or {}
            if len(day) >= 6:
                yy.append(max(day.values()))
                keep2.append(i)
        Xa = [Xa[i] for i in keep2]
        ma = [ma[i] for i in keep2]
        f2 = sorted({k for f in Xa for k in f})
        mfunc = lambda X: [[f.get(k, 0.0) for k in f2] for f in X]
        nm, rr = make_regressor()
        rr.fit(mfunc(Xa), yy)
        return {(r["city"], r["date"]): mu for r, mu in zip(mb, rr.predict(mfunc(Xb)))}

    def samples_h(city, date, unit, hour, mu_ml=None):
        day = hourly.get(city, {}).get(date) or {}
        past = [v for hh, v in day.items() if hh <= hour]
        if len(past) < 3:
            return None
        mx = max(past)
        incs = []
        for hd in sorted(d for d in (hourly.get(city) or {}) if d < date)[-30:]:
            p2 = [v for hh, v in hourly[city][hd].items() if hh <= hour]
            if len(p2) >= 3:
                incs.append(max(hourly[city][hd].values()) - max(p2))
        if len(incs) < 8:
            return None
        shift = (mu_ml - (mx + st.mean(incs))) if mu_ml is not None else 0.0
        s = [mx + x + shift for x in incs]
        return [v * 9 / 5 + 32 if unit == "F" else v for v in s]

    from collections import defaultdict as _dd

    bets_all = [b for b in load_bets() if b["date"] >= a.split]

    def money_intraday(hour, mu_map, tag):
        byday = _dd(list)
        for b in bets_all:
            byday[(b["city"], b["date"], b["hour"])].append(b)
        n = w = 0
        cost = 0.0
        for (city, date, h), bs in byday.items():
            if h != hour or city not in univ or (city, date) not in labels:
                continue
            unit = labels[(city, date)][2]
            smp = samples_h(city, date, unit, hour, (mu_map or {}).get((city, date)))
            if not smp:
                continue
            mbs = [(b["bin"], H.W.parse_bin(b["bin"])) for b in bs]
            mbs = [(lab, bb) for lab, bb in mbs if bb]
            mp = CB.model_probs(smp, unit, mbs)
            cand = [b for b in bs if 0.02 <= b["price"] <= 0.25]
            best = None
            for b in cand:
                pv = mp.get(b["bin"], 0.0)
                if best is None or pv > best[1]:
                    best = (b, pv)
            if best is None or best[1] < 0.90:
                continue
            n += 1
            w += best[0]["won"]
            cost += best[0]["price"]
        if not n:
            print(f"\nE3 {tag} {hour}:00: ไม่มีไม้")
            return dict(n=0, win=0.0, avg=0.0, roi=0.0)
        wr = w / n * 100
        avg = cost / n
        roi = (w / n - avg) / avg * 100
        print(f"\nE3 {tag} {hour}:00: n={n} ชนะ {wr:.1f}% ราคา {avg:.3f} ROI {roi:+.1f}%")
        return dict(n=n, win=wr, avg=avg, roi=roi)

    try:
        import cheap_bets as CB
        ml_h = {h: train_hour(h) for h in (14, 16)}
        e3 = {}
        for h in (14, 16):
            e3[h] = dict(ours=money_intraday(h, None, "empirical ของเรา"),
                         hybrid=money_intraday(h, ml_h[h], "hybrid (ML center + spread ของเรา)"))
    except Exception as e:      # ถ้า cheap_bets/hist ไม่พร้อม อย่าให้ล้มทั้งสคริปต์
        print("E3 ข้าม:", type(e).__name__, e)
        e3 = {}

    # ───────── รายงาน ─────────
    with open(MD, "w", encoding="utf-8") as f:
        w = f.write
        w("# ทดลอง ML (XGBoost) แทนสูตรของเรา — head-to-head\n\n")
        w(f"> `ml_boost.py --split {a.split}` · โมเดล ML: **{name}** · เทรนบน {len(tr)} เมือง-วัน (<{a.split}) · ทดสอบ {len(te)}\n\n")
        w("## E1) day-ahead — ทำนาย bin ที่ตลาดจะ resolve\n\n")
        w("| วิธี | n | top-1 | top-2 | Brier |\n|---|---|---|---|---|\n")
        w(f"| XGBoost (ทำนายอุณหภูมิ → Gaussian σ จาก residual) | {e1_ml['n']} | {e1_ml['top1']:.1f}% | {e1_ml['top2']:.1f}% | {e1_ml['brier']:.3f} |\n")
        w(f"| ฐานของเรา (median ของ 11 โมเดล → Gaussian) | {e1_base['n']} | {e1_base['top1']:.1f}% | {e1_base['top2']:.1f}% | {e1_base['brier']:.3f} |\n")
        w("\n## E2) 16:00 — ทำนาย max สุดท้ายของวัน (truth = max ของ obs รายชั่วโมง)\n\n")
        w("| วิธี | n | top-1 | top-2 | Brier |\n|---|---|---|---|---|\n")
        w(f"| XGBoost (ใช้ max-so-far + สถิติ increment รายเมือง) | {e2_ml['n']} | {e2_ml['top1']:.1f}% | {e2_ml['top2']:.1f}% | {e2_ml['brier']:.3f} |\n")
        w(f"| ฐาน (max-so-far ตรง ๆ) | {e2_base['n']} | {e2_base['top1']:.1f}% | {e2_base['top2']:.1f}% | {e2_base['brier']:.3f} |\n")
        w("\n## E2-เงิน — เอา p ของแต่ละวิธีเข้า 'กฎ YES @16:00' เดียวกัน (จักรวาล 33 เมือง · ราคา 0.02–0.25 · argmax bin)\n\n")
        w("| วิธีให้ p | ประตู | n | ชนะ% | ราคาเฉลี่ย | ROI |\n|---|---|---|---|---|---|\n")
        for tag, rr in results.items():
            for gname, m in (("p ≥ 0.90 (กฎจริง)", rr["gate"]), ("ไม่ใช้ประตู (argmax ล้วน)", rr["argmax"])):
                w(f"| {tag} | {gname} | {m['n']} | {m['win']:.1f}% | {m['avg']:.3f} | {m['roi']:+.1f}% |\n")
        if e3:
            w("\n## E3) ระหว่างวัน — hybrid: ใช้ 'ศูนย์กลาง' จาก ML แต่คง spread แบบ empirical ของเรา\n\n")
            w("| ชั่วโมง | วิธี | n | ชนะ% | ราคาเฉลี่ย | ROI |\n|---|---|---|---|---|---|\n")
            for h in (14, 16):
                for nm2, m in (("empirical ของเรา", e3[h]["ours"]), ("hybrid (ML center + spread ของเรา)", e3[h]["hybrid"])):
                    if m:
                        w(f"| {h}:00 | {nm2} | {m['n']} | {m['win']:.1f}% | {m['avg']:.3f} | {m['roi']:+.1f}% |\n")
        w("\n## หมายเหตุวิธีทดลอง\n\n")
        w("- ทำนาย **อุณหภูมิ** (regression) แล้วแปลงเป็นความน่าจะเป็นราย bin ด้วย Gaussian(μ_ML, σ_ML) โดย σ_ML วัดจาก residual บน train — วิธีเดียวกันทั้งสองฝั่งเพื่อให้เทียบได้ตรง\n")
        w(f"- ฟีเจอร์: 11 ค่าโมเดล (หัก bias รายโมเดล), median/mean/std/spread/min/max ของสมาชิก, สถิติรายเมือง (bias, σ), วันของปี, หน่วย · โหมด 16:00 เพิ่ม max-so-far, จำนวนชั่วโมง, ค่า obs ล่าสุด, สถิติ increment รายเมือง\n")
        w(f"- โมเดลที่ใช้จริง: {name}\n")
    print(f"\nเขียน {MD}")


if __name__ == "__main__":
    main()
