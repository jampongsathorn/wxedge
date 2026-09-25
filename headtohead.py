#!/usr/bin/env python3
"""
headtohead.py — พิสูจน์ว่า "กลยุทธ์ของ repo ไหนดีสุด" ด้วยข้อมูลชุดเดียวกัน

วิธี: เอา 4 เครื่องยนต์มาคำนวณบนอินพุตเดียวกัน (พยากรณ์รายโมเดลใน data/dataset_full.csv)
      แล้ววัดกับเฉลยเดียวกัน (bucket ที่ตลาด Polymarket resolve จริง · data/market_labels.csv)
      และราคาจริงทุก bin (data/cheap_bets.csv) เพื่อวัด ROI

เครื่องยนต์:
  1. polyBot     : Gaussian + ตาราง RMSE ตาม lead (2.5°F@24h → 1.39°C) + เลือก bin edge สูงสุด (edge ≥ 0.10)
  2. weatherbot  : sigma = MAE pooled, bias = EWMA(0.25) จากตลาดที่ resolve (fit บน train) + EV ≥ 0.10, ราคา ≤ 0.45
  3. PolyWeather : DEB ถ่วงน้ำหนักโมเดล w ∝ 1/(MAE + 0.5|bias| + 0.1) + sigma ตาม strata (lead × ย่านอุณหภูมิ)
                   + city-bias แบบ James–Stein shrinkage + floor sigma 0.5 + edge สูงสุด
  4. wxedge      : blend bias-corrected + sigma จาก rolling RMSE ของเรา (ตามที่ใช้จริง)
  (5. wxedge-intraday: การแจกแจงเชิงประจักษ์ตอน 16:00 — ใช้ในภาค ROI เท่านั้น)

แบ่ง train/test ตามเวลา (fit พารามิเตอร์บน train เท่านั้น) เพื่อไม่ให้ overfit
รัน: python3 headtohead.py --split 2026-08-15
"""
import argparse, collections, csv, json, math, os, statistics as st, sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wxedge as W

HERE = os.path.dirname(os.path.abspath(__file__))

MODELS = ["best_match", "ecmwf_ifs025", "ecmwf_aifs025_single", "gfs_seamless", "icon_seamless",
          "gem_seamless", "jma_seamless", "meteofrance_seamless", "ukmo_seamless",
          "ncep_nbm_conus", "ncep_hrrr_conus"]
FAMILY = {}  # ตระกูลโมเดล (สำหรับ DEB dedupe)
for m in MODELS:
    FAMILY[m] = m.split("_")[0]


def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def prob_of_bins(mu_c, sigma_c, unit, bins=None):
    """ความน่าจะเป็นต่อ bin (หน่วยตลาด, ปัดครึ่งองศาแบบตลาด)"""
    if unit == "F":
        mu, s = mu_c * 9 / 5 + 32, max(sigma_c, 0.1) * 9 / 5
    else:
        mu, s = mu_c, max(sigma_c, 0.1)
    out = {}
    for lo, hi in (bins or W.market_bins(unit)):
        p = norm_cdf((hi + 0.5 - mu) / s) - norm_cdf((lo - 0.5 - mu) / s)
        out[(lo, hi)] = max(0.0, p)
    tot = sum(out.values()) or 1.0
    return {k: v / tot for k, v in out.items()}


def load_dataset(path):
    rows = []
    for r in csv.DictReader(open(path, encoding="utf-8")):
        rows.append(r)
    return rows


def num(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


# ─────────────────────────── พารามิเตอร์ที่ fit บน TRAIN ───────────────────────────

def fit_params(rows, labels, split):
    train = [r for r in rows if r["date"] < split and (r["city"], r["date"]) in labels]
    P = {"wb": {}, "deb": {}, "pb": {}}
    # --- weatherbot: sigma = MAE pooled ตามหน่วย, bias = EWMA(0.25) ตามหน่วย ---
    for unit in ("F", "C"):
        errs = []
        for r in sorted(train, key=lambda r: r["date"]):
            if r["unit"] != unit:
                continue
            obs = num(r["obs_c"])
            vals = [num(r.get("m_" + m)) for m in MODELS]
            vals = [v for v in vals if v is not None]
            if obs is None or not vals:
                continue
            errs.append((st.mean(vals), obs))
        if errs:
            sigma = sum(abs(f - o) for f, o in errs) / len(errs)
            bias = errs[0][0] - errs[0][1]
            for f, o in errs[1:]:
                bias = 0.25 * (f - o) + 0.75 * bias
            P["wb"][unit] = (sigma, bias, len(errs))
    # --- PolyWeather DEB: MAE/bias ต่อโมเดล+เมือง (decay), strata sigma, city bias ---
    city_stats = collections.defaultdict(lambda: collections.defaultdict(list))
    per_mod_city = collections.defaultdict(lambda: collections.defaultdict(list))
    by_city = collections.defaultdict(list)
    for r in train:
        obs = num(r["obs_c"])
        if obs is None:
            continue
        by_city[r["city"]].append(r)
        for m in MODELS:
            v = num(r.get("m_" + m))
            if v is not None:
                per_mod_city[r["city"]][m].append((r["date"], v - obs))
    deb = {}
    for city, mods in per_mod_city.items():
        w = {}
        for m, errs in mods.items():
            if len(errs) < 5:
                continue
            errs_s = sorted(errs)
            tot_w = decay = 0.0
            mae_s = 0.0
            for i, (d, e) in enumerate(errs_s):
                ww = 0.85 ** (len(errs_s) - 1 - i)
                mae_s += abs(e) * ww
                tot_w += ww
            mae = mae_s / tot_w if tot_w else 2.0
            bias_m = st.mean([e for _, e in errs_s])
            w[m] = 1.0 / (mae + 0.5 * abs(bias_m) + 0.1)
        if w:
            deb[city] = {"weights": w, "d": dict(mods)}
    # strata sigma (lead=1 เท่านั้นในชุดนี้) × ย่านอุณหภูมิ
    strat = collections.defaultdict(list)
    for r in train:
        obs = num(r["obs_c"])
        if obs is None:
            continue
        vals = [num(r.get("m_" + m)) for m in MODELS]
        vals = [v for v in vals if v is not None]
        if not vals:
            continue
        mu = st.mean(vals)
        key = "<=32" if mu <= 32 else ("33-36" if mu <= 36 else ">=37") if r["unit"] == "C" else \
              ("<=89" if mu * 9 / 5 + 32 <= 89 else ("90-98" if mu * 9 / 5 + 32 <= 98 else ">=99"))
        strat[(r["unit"], key)].append(mu - obs)
    sigma_strat = {}
    for (unit, k), errs in strat.items():
        if len(errs) >= 15:
            sigma_strat[(unit, k)] = math.sqrt(sum(e * e for e in errs) / len(errs))
    pooled_sigma = {}
    for unit in ("F", "C"):
        errs = [e for (u, k), l in [] for e in l]
    for unit in ("F", "C"):
        all_e = [e for (u, k), l in strat.items() if u == unit for e in l]
        if all_e:
            pooled_sigma[unit] = math.sqrt(sum(e * e for e in all_e) / len(all_e))
    # city bias (median residual vs pooled median) + shrinkage n/(n+5)
    med_all = {u: st.median([e for (uu, k), l in strat.items() if uu == u for e in l])
               for u in ("F", "C") if any(uu == u for (uu, k) in strat)}
    city_bias = {}
    for city, rs in by_city.items():
        errs = []
        for r in rs:
            obs = num(r["obs_c"])
            vals = [num(r.get("m_" + m)) for m in MODELS]
            vals = [v for v in vals if v is not None]
            if obs is None or not vals:
                continue
            errs.append(st.mean(vals) - obs)
        if errs:
            n = len(errs)
            unit = rs[0]["unit"]
            med = st.median(errs)
            base = med_all.get(unit, 0.0)
            city_bias[city] = (n / (n + 5.0)) * (med - base)
    P["deb"] = {"models": deb, "sigma_strat": sigma_strat, "pooled_sigma": pooled_sigma,
                "city_bias": city_bias, "med_all": med_all}
    P["wb_fair"] = _wb_pooled(rows, split)
    return P


# ─────────────────────────── 4 เครื่องยนต์ ───────────────────────────

def eng_polybot(r, P, bins):
    mu_c = num(r.get("m_best_match"))
    if mu_c is None:
        vals = [num(r.get("m_" + m)) for m in MODELS]
        vals = [v for v in vals if v is not None]
        if not vals:
            return None
        mu_c = st.mean(vals)
    sigma_c = 2.5 * 5 / 9          # ตาราง RMSE ของ polyBot: 24 ชม. = 2.5°F
    return prob_of_bins(mu_c, sigma_c, r["unit"], bins)


def eng_weatherbot(r, P, bins):
    ws = P["wb"].get(r["unit"])
    if not ws:
        return None
    sigma, bias, _ = ws
    vals = [num(r.get("m_" + m)) for m in MODELS]
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    mu = st.mean(vals) - bias       # หัก bias ออก (เหมือน take_forecast_snapshot)
    return prob_of_bins(mu, sigma, r["unit"], bins)


def eng_polyweather(r, P, bins):
    d = P["deb"]
    city, unit = r["city"], r["unit"]
    info = d["models"].get(city)
    vals, wts = {}, {}
    for m in MODELS:
        v = num(r.get("m_" + m))
        if v is None:
            continue
        vals[m] = v
        if info and m in info["weights"]:
            wts[m] = info["weights"][m]
    if not vals:
        return None
    if wts:
        mu = sum(vals[m] * wts[m] for m in wts) / sum(wts.values())
    else:
        mu = st.mean(vals.values())          # เท่ากับ _equal_weight_result ของเขา
    mu -= d["city_bias"].get(city, 0.0)      # ปรับ city bias แบบ shrinkage
    key = "<=32" if mu <= 32 else ("33-36" if mu <= 36 else ">=37") if unit == "C" else \
          ("<=89" if mu * 9 / 5 + 32 <= 89 else ("90-98" if mu * 9 / 5 + 32 <= 98 else ">=99"))
    sigma = d["sigma_strat"].get((unit, key)) or d["pooled_sigma"].get(unit) or (1.5 if unit == "C" else 2.7)
    sigma = max(sigma, d["pooled_sigma"].get(unit, 0.0) or 0.0, 0.5)   # floor: ไม่แคบกว่า pooled, ไม่ต่ำกว่า 0.5
    return prob_of_bins(mu, sigma, unit, bins)


REGIONAL = {"us": "ncep_hrrr_conus", "eu": "icon_seamless", "asia": "icon_seamless",
            "ca": "gem_seamless", "sa": "gfs_seamless", "oc": "gfs_seamless", "me": "icon_seamless"}
US_CITIES = {"nyc", "chicago", "miami", "dallas", "seattle", "atlanta", "houston", "los-angeles",
             "denver", "austin", "san-francisco"}
EU_CITIES = {"london", "paris", "munich", "ankara", "madrid", "amsterdam", "warsaw", "milan",
             "helsinki", "moscow", "istanbul"}


def _wb_pooled(rows, split):
    """weatherbot (ดีไซน์จริง): ต่อแหล่ง (ecmwf / regional) หา sigma=MAE และ bias=EWMA(0.25) รายเมือง
    แล้ว fallback เป็น pooled ตามหน่วย+แหล่ง (ตาม get_sigma/get_bias ของเขา)"""
    per = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in rows:
        if r["date"] >= split:
            continue
        obs = num(r["obs_c"])
        if obs is None:
            continue
        city, unit = r["city"], r["unit"]
        for src, col in (("ecmwf", "m_ecmwf_ifs025"),
                         ("regional", "m_" + (REGIONAL["us"] if city in US_CITIES else
                                              (REGIONAL["eu"] if city in EU_CITIES else "gfs_seamless")))):
            v = num(r.get(col))
            if v is None:
                continue
            per[(city, src)][unit].append((r["date"], v - obs))
    city_stats, pooled = {}, collections.defaultdict(list)
    for (city, src), byunit in per.items():
        for unit, errs in byunit.items():
            errs_s = sorted(errs)
            mae = sum(abs(e) for _, e in errs_s) / len(errs_s)
            bias = errs_s[0][1]
            for _, e in errs_s[1:]:
                bias = 0.25 * e + 0.75 * bias
            city_stats[(city, src)] = (mae, bias, len(errs_s))
            pooled[(unit, src)] += [e for _, e in errs_s]
    pooled_stat = {}
    for (unit, src), errs in pooled.items():
        if len(errs) >= 40:
            pooled_stat[(unit, src)] = (sum(abs(e) for e in errs) / len(errs),
                                        sum(e for e in errs) / len(errs), len(errs))
    return {"city": city_stats, "pooled": pooled_stat}


def eng_weatherbot_fair(r, P, bins):
    wb = P.get("wb_fair")
    if not wb:
        return None
    city, unit = r["city"], r["unit"]
    regional_col = "m_" + (REGIONAL["us"] if city in US_CITIES else
                           (REGIONAL["eu"] if city in EU_CITIES else "gfs_seamless"))
    cal = []
    for src, col in (("ecmwf", "m_ecmwf_ifs025"), ("regional", regional_col)):
        v = num(r.get(col))
        st_ = wb["city"].get((city, src)) or wb["pooled"].get((unit, src))
        if v is None or not st_:
            continue
        mae, bias, _ = st_
        cal.append((v - bias, max(mae, 0.4)))
    if len(cal) >= 2:
        wsum = sum(1.0 / (sg ** 2) for _, sg in cal)
        mu = sum(v * (1.0 / sg ** 2) for v, sg in cal) / wsum
        sigma = wsum ** -0.5
    elif len(cal) == 1:
        mu, sigma = cal[0]
    else:
        return None
    return prob_of_bins(mu, sigma, unit, bins)


def eng_polyweather_intraday(r, P, bins, floor_c):
    """PolyWeather ระหว่างวัน: หัก bin ที่ต่ำกว่า ceil(max_so_far-0.5) แล้วกระจายมวลเท่ากัน (ตาม dynamic_forecast)"""
    p = eng_polyweather(r, P, bins)
    if not p or floor_c is None:
        return None
    unit = r["unit"]
    floor_m = floor_c * 9 / 5 + 32 if unit == "F" else floor_c
    lim = floor_m - 0.5
    out, freed, keep = {}, 0.0, 0
    for (lo, hi) in p:
        if hi < lim:
            freed += p[(lo, hi)]
            out[(lo, hi)] = 0.0
        else:
            out[(lo, hi)] = p[(lo, hi)]
            keep += 1
    if keep and freed > 0:
        for k in out:
            if out[k] > 0:
                out[k] += freed / keep
    tot = sum(out.values()) or 1
    return {k: v / tot for k, v in out.items()}


def eng_wxedge(r, P, bins):
    mu = num(r.get("mu_c"))
    sigma = num(r.get("sigma_c"))
    if mu is None or sigma is None:
        return None
    return prob_of_bins(mu + 0.1, sigma, r["unit"], bins)   # +0.1 = bias_shift ที่จูนไว้เดิม


ENGINES = [("polyBot", eng_polybot), ("weatherbot", eng_weatherbot),
           ("weatherbot(ดีไซน์จริง 2 แหล่ง)", eng_weatherbot_fair),
           ("PolyWeather(DEB)", eng_polyweather), ("wxedge(ของเรา)", eng_wxedge)]


# ─────────────────────────── ตัวชี้วัด ───────────────────────────

MAX16 = {}
OUR_INTRA = {}     # (city,date) -> bin label ที่โมเดล intraday ของเราเลือก (argmax p) ที่ 16:00


def load_our_intraday(path):
    """อ่าน cheap_bets.csv → หา bin ที่โมเดล intraday ของเราให้ p สูงสุดต่อ city/date (ชั่วโมง 16)"""
    best = {}
    if not os.path.exists(path):
        return
    for r in csv.DictReader(open(path, encoding="utf-8")):
        if int(r["hour"]) != 16:
            continue
        k = (r["city"], r["date"])
        p = float(r["model_p"])
        if k not in best or p > best[k][0]:
            best[k] = (p, r["bin"], int(r["won"]))
    OUR_INTRA.update(best)


def load_max16(path):
    if not os.path.exists(path):
        return
    for r in csv.DictReader(open(path, encoding="utf-8")):
        MAX16[(r["city"], r["date"])] = float(r["max16_c"])


def metrics(rows, labels, P, split=None, which="test"):
    if split:
        rows = [r for r in rows if (r["date"] >= split) == (which == "test")]
    res = collections.defaultdict(lambda: dict(n=0, top1=0, top2=0, brier=0.0, pit=[], cov90=0))
    for r in rows:
        lab = labels.get((r["city"], r["date"]))
        if not lab:
            continue
        try:
            lo, hi = float(lab["lo"].replace("inf", "1")), float(lab["hi"].replace("inf", "1"))
        except Exception:
            continue
        if lab["lo"] == "-inf":
            lo = hi - 1
        if lab["hi"] == "inf":
            hi = lo + 1
        win = (lo, hi)
        bins = W.market_bins(r["unit"])
        if win not in bins:
            win = W.parse_bin(lab["winner"]) or None
            if win is None:
                continue
        obs = num(r["obs_c"])
        intraday_engines = []
        if MAX16:
            m16 = MAX16.get((r["city"], r["date"]))
            intraday_engines = [("PolyWeather+floor(16:00)", lambda rr, PP, bb: eng_polyweather_intraday(rr, PP, bb, m16))]
        for name, fn in list(ENGINES) + intraday_engines:
            p = fn(r, P, bins)
            if not p:
                continue
            s = res[name]
            order = sorted(p.items(), key=lambda kv: -kv[1])
            s["n"] += 1
            s["top1"] += (order[0][0] == win)
            s["top2"] += (win in [k for k, _ in order[:2]])
            s["brier"] += sum((p[k] - (1.0 if k == win else 0.0)) ** 2 for k in p)
            # PIT / coverage โดยประมาณ: ใช้ค่าจริง (obs_c) เทียบกับการแจกแจง
            if obs is not None:
                mu = None
                # แปลงการแจกแจง bin กลับเป็น CDF ที่ค่า obs (ใช้วิธีสะสม bin)
                unit = r["unit"]
                obs_m = obs * 9 / 5 + 32 if unit == "F" else obs
                c = 0.0
                for (bl, bh) in sorted(p):
                    if obs_m >= bh + 0.5:
                        c += p[(bl, bh)]
                    elif obs_m <= bl - 0.5:
                        continue
                    else:
                        # อยู่ใน bin นี้ → interpolate เชิงเส้นภายใน bin
                        frac = (obs_m - (bl - 0.5)) / max(1e-9, (bh + 0.5) - (bl - 0.5))
                        c += p[(bl, bh)] * frac
                        break
                s["pit"].append(min(max(c, 0.0), 1.0))
                s["cov90"] += (1 if 0.05 <= c <= 0.95 else 0)
    out = {}
    for name, s in res.items():
        if not s["n"]:
            continue
        pit = s["pit"]
        out[name] = dict(
            n=s["n"], top1=100 * s["top1"] / s["n"], top2=100 * s["top2"] / s["n"],
            brier=s["brier"] / s["n"],
            pit_mean=(st.mean(pit) if pit else None), pit_std=(st.pstdev(pit) if len(pit) > 1 else None),
            cov90=(100 * s["cov90"] / len(pit) if pit else None),
        )
    return out


def roi_compare(labels, split, pmax=0.25):
    """ภาคเงิน: ใช้ราคาจริงทุก bin ตอน 16:00 (cheap_bets.csv) + กฎของแต่ละเจ้า"""
    path = os.path.join(HERE, "data", "cheap_bets.csv")
    if not os.path.exists(path):
        return None
    price16 = {}
    ourp = {}
    for r in csv.DictReader(open(path, encoding="utf-8")):
        if int(r["hour"]) != 16:
            continue
        price16[(r["city"], r["date"], float(r["bin"].split("°")[0].split("-")[0]) if False else r["bin"])] = float(r["price"])
        ourp[(r["city"], r["date"], r["bin"])] = float(r["model_p"])
    saved = price16
    return saved, ourp


# ─────────────────────────── Ablation: blend ดี หรือ sigma ดี ───────────────────────────

def ablations(rows, labels, P, split):
    """ใช้ mu เดียวกัน (blend ของเรา) แต่เปลี่ยน sigma ตามสูตรของแต่ละเจ้า → ดูว่าอะไรทำให้ต่าง"""
    def eng(mu_fn, sig_fn):
        def f(r, _P, bins):
            mu = mu_fn(r)
            sg = sig_fn(r, _P)
            if mu is None or sg is None:
                return None
            return prob_of_bins(mu, sg, r["unit"], bins)
        return f
    mu_ours = lambda r: num(r.get("mu_c"))
    mu_raw = lambda r: (st.mean([v for v in (num(r.get("m_" + m)) for m in MODELS) if v is not None])
                        if any(num(r.get("m_" + m)) is not None for m in MODELS) else None)
    variants = [
        ("mu = blend เรา, sigma = เรา", mu_ours, lambda r, P: num(r.get("sigma_c"))),
        ("mu = blend เรา, sigma = polyBot (1.39°C ตายตัว)", mu_ours, lambda r, P: 2.5 * 5 / 9),
        ("mu = blend เรา, sigma = weatherbot (MAE pooled)", mu_ours,
         lambda r, P: (P["wb"].get(r["unit"]) or (None,))[0]),
        ("mu = blend เรา, sigma = PolyWeather (strata)",
         mu_ours, lambda r, P: None),
        ("mu = ค่าเฉลี่ยดิบ (ไม่หัก bias), sigma = เรา", mu_raw, lambda r, P: num(r.get("sigma_c"))),
    ]
    out = {}
    for name, mf, sf in variants:
        res = dict(n=0, top1=0)
        for r in rows:
            if r["date"] < split or (r["city"], r["date"]) not in labels:
                continue
            lab = labels[(r["city"], r["date"])]
            win = W.parse_bin(lab["winner"])
            if not win:
                continue
            if name.endswith("(strata)"):
                p = eng_polyweather_sigma(r, P)
            else:
                p = eng(mf, sf)(r, P, W.market_bins(r["unit"]))
            if not p:
                continue
            res["n"] += 1
            res["top1"] += (max(p, key=p.get) == win)
        if res["n"]:
            out[name] = dict(n=res["n"], top1=100 * res["top1"] / res["n"])
    return out


def eng_polyweather_sigma(r, P):
    """ใช้ mu ของเรา แต่ sigma ตามสูตร PolyWeather (strata)"""
    d = P["deb"]
    mu = num(r.get("mu_c"))
    if mu is None:
        return None
    unit = r["unit"]
    key = "<=32" if mu <= 32 else ("33-36" if mu <= 36 else ">=37") if unit == "C" else \
          ("<=89" if mu * 9 / 5 + 32 <= 89 else ("90-98" if mu * 9 / 5 + 32 <= 98 else ">=99"))
    sg = d["sigma_strat"].get((unit, key)) or d["pooled_sigma"].get(unit) or 1.5
    sg = max(sg, 0.5)
    return prob_of_bins(mu, sg, unit, W.market_bins(unit))


# ─────────────────────────── ชั้นเงิน: ROI กับราคาจริง ───────────────────────────

def money_layer(rows, labels, P, split, out_lines):
    path = os.path.join(HERE, "data", "cheap_bets.csv")
    if not os.path.exists(path):
        return
    price = collections.defaultdict(dict)
    ours = {}
    for r in csv.DictReader(open(path, encoding="utf-8")):
        if int(r["hour"]) != 16:
            continue
        price[(r["city"], r["date"])][r["bin"]] = (float(r["price"]), int(r["won"]))
        oursp = ours.setdefault((r["city"], r["date"]), {})
        oursp[r["bin"]] = (float(r["model_p"]), int(r["won"]))
    # p ของ 4 เครื่องยนต์ (day-ahead) ต่อ (city,date)
    P_by_day = {}
    for r in rows:
        if (r["city"], r["date"]) not in price:
            continue
        P_by_day[(r["city"], r["date"])] = (r, {name: fn(r, P, W.market_bins(r["unit"])) for name, fn in ENGINES}
                                            if False else {n: f(r, P, W.market_bins(r["unit"])) for n, f in ENGINES})
    period = lambda d: d >= split
    strat = {
        "PolyWeather+floor: edge > 0 (16:00)": ("POLY_FLOOR", lambda p, pr: (p - pr) > 0),
        "PolyWeather+floor: เลือก bin p สูงสุด": ("POLY_FLOOR_TOP", lambda p, pr: True),
        "polyBot: edge ≥ 0.10 (argmax edge)": ("polyBot", lambda p, pr: (p - pr) >= 0.10 and pr <= 0.95),
        "weatherbot: EV ≥ 0.10 และราคา ≤ 0.45": ("weatherbot", None),
        "PolyWeather: edge > 0 (argmax edge)": ("PolyWeather(DEB)", lambda p, pr: (p - pr) > 0),
        "wxedge: ราคา 0.02–0.25 และ p ≥ 0.35": ("wxedge(ของเรา)", lambda p, pr: 0.02 <= pr < 0.25 and p >= 0.35),
        "wxedge: ราคา 0.02–0.25 และ p ≥ 0.90": ("wxedge(ของเรา)", lambda p, pr: 0.02 <= pr < 0.25 and p >= 0.90),
        "wxedge-intraday: ราคา 0.02–0.25 และ p ≥ 0.90 (ใช้ obs 16:00)": ("INTRADAY", lambda p, pr: 0.02 <= pr < 0.25 and p >= 0.90),
    }
    out_lines.append("\n## 3) ชั้นเงิน: ซื้อ bin ที่แต่ละกลยุทธ์เลือก ณ 16:00 น. (ราคาจริงจาก CLOB)\n")
    out_lines.append("| กลยุทธ์ (กฎของเจ้าของ) | ช่วงทดสอบ | n ไม้ | ชนะ% | ราคาเฉลี่ย | ROI |")
    out_lines.append("|---|---|---|---|---|---|")
    for name, (eng, cond) in strat.items():
        for lbl, sel in (("test", lambda d: d >= split), ("ทั้งหมด", lambda d: True)):
            n = w = 0
            cost = ret = 0.0
            for (city, date), bins in price.items():
                if not sel(date):
                    continue
                if eng in ("POLY_FLOOR", "POLY_FLOOR_TOP"):
                    city_, date_ = city, date
                    r_ = P_by_day.get((city_, date_), (None, {}))[0]
                    if r_ is None:
                        continue
                    pf = eng_polyweather_intraday(r_, P, W.market_bins(r_["unit"]), MAX16.get((city_, date_)))
                    if not pf:
                        continue
                    cand = []
                    for (lo, hi), pp in pf.items():
                        b = W.label_str((lo, hi), r_["unit"])
                        if b not in bins:
                            continue
                        pr, won = bins[b]
                        if eng == "POLY_FLOOR_TOP":
                            cand.append((pp, b, pr, won))
                        elif cond(pp, pr):
                            cand.append((pp - pr, b, pr, won))
                elif eng == "INTRADAY":
                    pm = ours.get((city, date))
                    if not pm:
                        continue
                    cand = []
                    for b, (p, won) in pm.items():
                        pr = bins.get(b, (None,))[0]
                        if pr is None or not cond(p, pr):
                            continue
                        cand.append((p - pr, b, pr, won))
                else:
                    r = P_by_day.get((city, date), (None, {}))[0]
                    if r is None:
                        continue
                    pd = P_by_day[(city, date)][1].get(eng) or {}
                    cand = []
                    for (lo, hi), p in pd.items():
                        b = W.label_str((lo, hi), r["unit"])
                        if b not in bins:
                            continue
                        pr, won = bins[b]
                        if eng == "weatherbot":
                            ev = p * (1 / pr - 1) - (1 - p) if 0 < pr < 1 else -1
                            if ev >= 0.10 and pr <= 0.45:
                                cand.append((ev, b, pr, won))
                        elif cond(p, pr):
                            cand.append((p - pr, b, pr, won))
                if not cand:
                    continue
                cand.sort(key=lambda x: -x[0])
                _, b, pr, won = cand[0]
                n += 1
                w += won
                cost += pr
                ret += 1.0 if won else 0.0
            if n and lbl == "test":
                out_lines.append("| %s | %s | %d | %.1f%% | %.3f | %+.0f%% |"
                                 % (name, lbl, n, 100 * w / n, cost / n, 100 * (ret - cost) / cost))
                print("  %-58s test n=%4d ชนะ=%5.1f%% ROI=%+7.1f%%" % (name, n, 100 * w / n, 100 * (ret - cost) / cost))
    # แถว "ทั้งหมด" สำหรับกฎของเรา
    for name in ["wxedge: ราคา 0.02–0.25 และ p ≥ 0.35", "wxedge-intraday: ราคา 0.02–0.25 และ p ≥ 0.90 (ใช้ obs 16:00)"]:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(HERE, "data", "dataset_full.csv"))
    ap.add_argument("--labels", default=os.path.join(HERE, "data", "market_labels.csv"))
    ap.add_argument("--split", default="2026-08-15")
    ap.add_argument("--out", default=os.path.join(HERE, "reports", "headtohead_raw.md"))
    a = ap.parse_args()

    rows = load_dataset(a.data)
    load_max16(os.path.join(HERE, "data", "maxsofar16.csv"))
    load_our_intraday(os.path.join(HERE, "data", "cheap_bets.csv"))
    labels = {}
    for r in csv.DictReader(open(a.labels, encoding="utf-8")):
        labels[(r["city"], r["date"])] = r
    P = fit_params(rows, labels, a.split)

    tr = metrics(rows, labels, P, a.split, "train")
    te = metrics(rows, labels, P, a.split, "test")

    L = ["# Head-to-head: 4 กลยุทธ์ บนข้อมูล/เฉลยชุดเดียวกัน — อันไหนดีสุดและพิสูจน์ได้แค่ไหน\n",
         "วันที่ %s · อินพุต: พยากรณ์รายโมเดลใน `data/dataset_full.csv` · เฉลย: bucket ที่ตลาด resolve จริง (`data/market_labels.csv`)\n"
         % datetime.now().strftime("%Y-%m-%d %H:%M"),
         "แบ่งเวลา: **train < %s ≤ test** (fit σ/bias/weights บน train เท่านั้น) · หน่วย: %% ของการทายถูก bucket เดียว\n"
         % a.split,
         "## 1) คุณภาพความน่าจะเป็น (ทดสอบ)\n",
         "| เครื่องยนต์ | n | top-1 ถูก% | top-2 ถูก% | Brier (ต่ำดี) | PIT mean (ควร 0.50) | PIT std (ควร 0.29) | cov90% (ควร 90) |",
         "|---|---|---|---|---|---|---|---|"]
    for name, _ in ENGINES:
        s = te.get(name)
        if not s:
            continue
        L.append("| %s | %d | **%.1f%%** | %.1f%% | %.4f | %.3f | %.3f | %.1f%% |"
                 % (name, s["n"], s["top1"], s["top2"], s["brier"],
                    s["pit_mean"] or 0, s["pit_std"] or 0, s["cov90"] or 0))
    ab = ablations(rows, labels, P, a.split)
    if ab:
        L.append("\n## 2) Ablation: ที่ชนะเพราะ 'blend' หรือเพราะ 'sigma'?\n")
        L.append("ใช้ mu เดียวกัน (blend ของเรา) แล้วสลับเฉพาะ sigma ตามสูตรของแต่ละเจ้า:\n")
        L.append("| การจับคู่ | n | top-1 ถูก% |")
        L.append("|---|---|---|")
        for k, v in ab.items():
            L.append("| %s | %d | **%.1f%%** |" % (k, v["n"], v["top1"]))
        print("\n=== ABLATION (test) ===")
        for k, v in ab.items():
            print("  %-52s n=%4d top1=%5.1f%%" % (k, v["n"], v["top1"]))

    # แถวพิเศษ: โมเดล intraday ของเรา (ใช้ obs ถึง 16:00) — เทียบในหน้าต่างเดียวกัน
    def intra_stats(which):
        n = h = 0
        for (city, date), (p, b, won) in OUR_INTRA.items():
            if (date >= a.split) != (which == "test"):
                continue
            n += 1
            h += won
        return n, (100 * h / n if n else 0)
    nte, te_hit = intra_stats("test")
    ntr, tr_hit = intra_stats("train")
    if nte:
        L.append("| wxedge+intraday(16:00) | %d | **%.1f%%** | — | — | — | — | — |" % (nte, te_hit))
        print("  wxedge+intraday(16:00) test n=%d top1=%.1f%% | train n=%d top1=%.1f%%" % (nte, te_hit, ntr, tr_hit))
        L.append("| _(wxedge+intraday, train)_ | %d | %.1f%% | | | | | |" % (ntr, tr_hit))

    L.append("\n**เทียบ train (ไว้ดู overfit):**\n")
    L.append("| เครื่องยนต์ | n | top-1 | Brier |")
    L.append("|---|---|---|---|")
    for name, _ in ENGINES:
        s = tr.get(name)
        if s:
            L.append("| %s | %d | %.1f%% | %.4f |" % (name, s["n"], s["top1"], s["brier"]))

    print("\n=== TEST (>= %s) ===" % a.split)
    for name, s in sorted(te.items(), key=lambda kv: -kv[1]["top1"]):
        print("  %-18s n=%4d top1=%5.1f%% top2=%5.1f%% Brier=%.4f PITstd=%.3f cov90=%.1f%%"
              % (name, s["n"], s["top1"], s["top2"], s["brier"], s["pit_std"] or 0, s["cov90"] or 0))
    print("=== TRAIN ===")
    for name, s in sorted(tr.items(), key=lambda kv: -kv[1]["top1"]):
        print("  %-18s n=%4d top1=%5.1f%% Brier=%.4f" % (name, s["n"], s["top1"], s["brier"]))

    money_layer(rows, labels, P, a.split, L)

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    open(a.out, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\nเขียน %s" % a.out)


if __name__ == "__main__":
    main()
