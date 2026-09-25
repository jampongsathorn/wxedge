#!/usr/bin/env python3
"""
wxedge — แปลงพยากรณ์อากาศ (ensemble + deterministic) เป็นความน่าจะเป็นราย bin
สำหรับตลาด Polymarket "Highest temperature in <city> on <date>?"

แนวคิด:
  1) ดึง daily-max temperature_2m ต่อ "สมาชิก" ของ ensemble (ECMWF ENS / GFS GEFS / ICON EPS)
     ผ่าน Open-Meteo Ensemble API (ฟรี ไม่ต้องใช้ key)
  2) ดึง deterministic: NBM (สหรัฐฯ แม่นสุดเชิงสถิติ), HRRR, NWS gridpoint, AIFS, IFS, ICON ...
     → ใช้เป็น "anchor" ดึงค่าเฉลี่ยของ ensemble ให้ตรงจุด
  3) Bias-correct ด้วยการเทียบย้อนหลัง (Open-Meteo Historical Forecast API vs observations)
  4) ใส่ residual noise (จาก RMSE − ensemble spread) แล้ว Monte-Carlo
  5) ถ้าเป็นตลาดของ "วันนี้" ที่ผ่าน peak แล้ว → ใช้ค่าที่วัดได้จริง (observed max so far) บีบการแจกแจง
  6) เทียบกับราคา yes จริงจาก Polymarket Gamma API → edge / EV / Kelly

ใช้: python3 wxedge.py predict nyc --date 2026-09-24
     python3 wxedge.py scan --limit 25
     python3 wxedge.py calibrate nyc --days 60
     python3 wxedge.py verify --days 5
"""
import argparse, csv, hashlib, io, json, math, os, re, sys, time, urllib.parse, urllib.request
from datetime import datetime, timedelta, date as _date
from zoneinfo import ZoneInfo

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
STATIONS = os.path.join(HERE, "stations.json")
CACHE = os.path.join(HERE, ".cache")
REPORTS = os.path.join(HERE, "reports")
UA = {"User-Agent": "wxedge/0.2 (weather research; contact: user@example.com)"}

ENS_MODELS = ["ecmwf_ifs025", "gfs025", "icon_seamless"]
ENS_W = {"ecmwf_ifs025": 1.3, "gfs025": 1.0, "icon_seamless": 0.9}
# ชื่อคอลัมน์จริงจาก Open-Meteo (สำคัญ: GEFS = "ncep_gefs025" ไม่ใช่ "gfs025")
ENS_TAG = {"ecmwf_ifs025": "ecmwf_ifs025", "gfs025": "ncep_gefs025", "icon_seamless": "icon_seamless"}
_ENS_RE = re.compile(r"temperature_2m(?:_max)?_member(\d+)_(.+?)(?:_ensemble|_eps)?$")


def ens_members(hourly_or_daily, key="temperature_2m_max"):
    """แยกสมาชิก ensemble จากคอลัมน์ → {model_request_name: [column keys]}
    จับเฉพาะคอลัมน์ memberNN (ไม่รวมคอลัมน์ mean) และ map ชื่อจริงของแต่ละศูนย์"""
    out = {}
    for k in hourly_or_daily:
        if k == "time" or not k.startswith(key):
            continue
        m = _ENS_RE.match(k)
        if not m:
            continue
        tag = m.group(2)
        for name, real in ENS_TAG.items():
            if real in tag:
                out.setdefault(name, []).append(k)
                break
    return out
DET_CONUS = ["ncep_nbm_conus", "ncep_hrrr_conus"]
DET_GLOBAL = ["best_match", "gfs_seamless", "ecmwf_ifs025", "ecmwf_aifs025_single", "icon_seamless",
              "ukmo_seamless", "jma_seamless", "meteofrance_seamless", "gem_seamless",
              "bom_access_global"]
DET_SAFE = ["gfs_seamless", "ecmwf_ifs025", "icon_seamless"]
DET_W = {"ncep_nbm_conus": 3.0, "nws_grid": 2.0, "ncep_hrrr_conus": 1.5, "best_match": 1.5,
         "ecmwf_ifs025": 1.2, "ecmwf_aifs025_single": 1.1, "gfs_seamless": 1.0,
         "icon_seamless": 0.9, "ukmo_seamless": 0.8, "jma_seamless": 0.7,
         "meteofrance_seamless": 0.7, "gem_seamless": 0.7, "bom_access_global": 0.6}

# สถิติเริ่มต้น (ใช้เมื่อยังไม่ calibrate): RMSE ของ daily max ~ 1.0°C
DEFAULT_RMSE_C = 1.0
SIGMA_FLOOR_C = 0.40
ANCHOR_WEIGHT = 0.35          # น้ำหนักของ deterministic ในการดึงค่าเฉลี่ย ensemble
PEAK_HOUR = 15                # ชั่วโมง local ที่อุณหภูมิสูงสุดโดยทั่วไป


# ────────────────────────────── HTTP + cache ──────────────────────────────
def _cache_path(url, tag):
    return os.path.join(CACHE, f"{tag}_{hashlib.sha1(url.encode()).hexdigest()[:16]}.txt")


_LAST_CALL = {"t": 0.0, "host": ""}
HOST_RATE = {"mesonet.agron.iastate.edu": 2.0, "api.weather.gov": 1.0}

# ── pacing ต่อ host แบบ thread-safe (จำเป็นเมื่อสแกนหลายเมืองพร้อมกัน — ไม่งั้นยิง IEM พร้อมกันแล้วโดน 429) ──
import threading as _threading
_LOCK = _threading.Lock()
_HOST_LOCKS = {}
_LAST_BY_HOST = {}


def _host_slot(host, gap):
    """จองคิว: เว้นระยะขั้นต่ำ gap วินาทีต่อ host (ทั้งโปรเซส)"""
    with _LOCK:
        lk = _HOST_LOCKS.setdefault(host, _threading.Lock())
    with lk:
        wait = gap - (time.time() - _LAST_BY_HOST.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        _LAST_BY_HOST[host] = time.time()


def http_get(url, ttl=600, tag="get", no_cache=False):
    """GET + cache (ttl) + retry/backoff + pacing ต่อ host (IEM/NOAA มี rate limit)"""
    os.makedirs(CACHE, exist_ok=True)
    p = _cache_path(url, tag)
    if not no_cache and os.path.exists(p) and time.time() - os.path.getmtime(p) < ttl:
        return open(p, encoding="utf-8").read()
    host = urllib.parse.urlparse(url).netloc
    for attempt in range(5):
        _host_slot(host, HOST_RATE.get(host, 1.2))
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=90) as r:
                txt = r.read().decode("utf-8", "replace")
            if "Too many requests" in txt or "rate limit" in txt.lower():
                # IEM ตอบ 200 พร้อมข้อความ rate limit → ห้าม cache แล้ว retry
                time.sleep(5 * (attempt + 1))
                continue
            open(p, "w", encoding="utf-8").write(txt)
            return txt
        except Exception as e:
            code = getattr(e, "code", None)
            if attempt == 4:
                raise
            # 429 → ถอยนานขึ้นแบบสุ่ม เพื่อไม่ให้หลาย thread กลับมายิงพร้อมกันอีก
            back = 3.0 * (attempt + 1) + (6.0 if code == 429 else 0) + __import__("random").random() * 2
            time.sleep(back)
    return ""


def get_json(url, ttl=600, tag="j", no_cache=False):
    return json.loads(http_get(url, ttl, tag, no_cache))


# ────────────────────────────── พยากรณ์: ensemble ──────────────────────────────
def ens_daily_max(cfg, target, no_cache=False):
    """คืน {model: np.array(สมาชิก, °C)} ของ daily max ใน target (local day)"""
    url = ("https://ensemble-api.open-meteo.com/v1/ensemble?latitude=%.4f&longitude=%.4f"
           "&daily=temperature_2m_max&models=%s&forecast_days=7&timezone=%s"
           % (cfg["lat"], cfg["lon"], ",".join(ENS_MODELS), urllib.parse.quote(cfg["tz"])))
    d = None
    for _try in range(3):
        try:
            d = get_json(url, ttl=900, tag="ens", no_cache=no_cache or _try > 0)
            if d.get("daily"):
                break
        except Exception:
            time.sleep(2 * (_try + 1))
    if not d:
        raise RuntimeError("ดึง ensemble ไม่ได้ (Open-Meteo error/ถูกจำกัด) — ลองใหม่")
    daily = d.get("daily", {})
    if target not in daily.get("time", []):
        return {}
    i = daily["time"].index(target)
    groups = ens_members(daily)
    out = {}
    for m, cols in groups.items():
        vals = [daily[c][i] for c in cols if daily[c][i] is not None]
        if vals:
            out[m] = np.array(vals, dtype=float)
    return out


def ens_remaining_max(cfg, target, from_hour, no_cache=False):
    """ต่อสมาชิก ensemble: ค่าสูงสุดของ remaining hours (>= from_hour) ของวัน target
    ใช้กับตลาด 'วันนี้' ที่ peak อาจผ่านไปแล้ว — แม่นกว่าการใช้ daily max ทั้งวัน"""
    url = ("https://ensemble-api.open-meteo.com/v1/ensemble?latitude=%.4f&longitude=%.4f"
           "&hourly=temperature_2m&models=%s&forecast_days=2&timezone=%s"
           % (cfg["lat"], cfg["lon"], ",".join(ENS_MODELS), urllib.parse.quote(cfg["tz"])))
    try:
        d = get_json(url, ttl=900, tag="ensh", no_cache=no_cache)
    except Exception:
        return {}
    h = d.get("hourly", {})
    times = h.get("time", [])
    idx = [i for i, t in enumerate(times)
           if t[:10] == target and int(t[11:13]) >= from_hour]
    if not idx:
        return {}
    out = {}
    for m, cols in ens_members(h).items():
        vals = []
        for c in cols:
            series = [h[c][i] for i in idx if h[c][i] is not None]
            if series:
                vals.append(max(series))
        if vals:
            out[m] = np.array(vals, dtype=float)
    return out


# ────────────────────────────── พยากรณ์: deterministic ──────────────────────────────
def det_daily_max(cfg, target, no_cache=False):
    models = (DET_CONUS if cfg.get("country") == "US" else []) + DET_GLOBAL
    for attempt, ms in enumerate([models, DET_SAFE]):
        url = ("https://api.open-meteo.com/v1/forecast?latitude=%.4f&longitude=%.4f"
               "&daily=temperature_2m_max&models=%s&forecast_days=7&timezone=%s"
               % (cfg["lat"], cfg["lon"], ",".join(ms), urllib.parse.quote(cfg["tz"])))
        try:
            d = get_json(url, ttl=900, tag="det", no_cache=no_cache)
            break
        except Exception:
            if attempt == 1:
                return {}
    daily = d.get("daily", {})
    if target not in daily.get("time", []):
        return {}
    i = daily["time"].index(target)
    return {k.replace("temperature_2m_max_", ""): v[i]
            for k, v in daily.items() if k != "time" and v[i] is not None}


def nws_grid_daily_max(cfg, target, no_cache=False):
    """NWS gridpoint (api.weather.gov) — ใช้ได้เฉพาะสหรัฐฯ"""
    if cfg.get("country") != "US":
        return None
    try:
        pts = get_json("https://api.weather.gov/points/%.4f,%.4f" % (cfg["lat"], cfg["lon"]),
                       ttl=86400, tag="nwsp", no_cache=no_cache)
        grid = get_json(pts["properties"]["forecastGridData"], ttl=1800, tag="nwsg", no_cache=no_cache)
        vals = grid["properties"]["temperature"]["values"]
    except Exception:
        return None
    tz = ZoneInfo(cfg["tz"])
    best = None
    for v in vals:
        if v.get("value") is None:
            continue
        t0 = datetime.fromisoformat(v["validTime"].split("/")[0]).astimezone(tz)
        if t0.strftime("%Y-%m-%d") == target:
            best = v["value"] if best is None else max(best, v["value"])
    return best


# ────────────────────────────── Observations ──────────────────────────────
def iem_daily_max(cfg, d0, d1, no_cache=False):
    """IEM ASOS (METAR) → {date_local: max °C} รองรับทั้ง US และต่างประเทศ"""
    icao = cfg["icao"]
    for icao_try in [icao, "K" + icao if not icao.startswith("K") else icao]:
        url = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station=%s&data=tmpc"
               "&year1=%d&month1=%d&day1=%d&year2=%d&month2=%d&day2=%d&tz=%s&format=onlycomma"
               "&report_type=3&missing=M&trace=T&direct=no"
               % (icao_try, d0.year, d0.month, d0.day, d1.year, d1.month, d1.day,
                  urllib.parse.quote(cfg["tz"])))
        try:
            txt = http_get(url, ttl=1800, tag="iem", no_cache=no_cache)
        except Exception:
            continue
        out = {}
        for row in csv.DictReader(io.StringIO(txt)):
            v = row.get("tmpc")
            if not v or v in ("M", ""):
                continue
            try:
                x = float(v)
            except ValueError:
                continue
            day = row["valid"][:10]
            out[day] = max(out.get(day, -99), x)
        if out:
            return out
    return {}


def nws_obs_daily_max(cfg, target, no_cache=False):
    tz = ZoneInfo(cfg["tz"])
    d = _date.fromisoformat(target)
    start = datetime(d.year, d.month, d.day, tzinfo=tz).astimezone(ZoneInfo("UTC"))
    end = start + timedelta(days=1)
    url = ("https://api.weather.gov/stations/%s/observations?start=%s&end=%s&limit=500"
           % (cfg["icao"], start.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ")))
    try:
        j = get_json(url, ttl=1800, tag="nwso", no_cache=no_cache)
    except Exception:
        return None, 0
    vals = [f["properties"]["temperature"]["value"] for f in j.get("features", [])
            if f["properties"].get("temperature", {}).get("value") is not None]
    return (max(vals) if vals else None), len(vals)


_HKO_CACHE = {}


def hko_month(year, month, no_cache=False):
    """แหล่งตัดสินจริงของตลาดฮ่องกง: HKO Daily Extract (Absolute Daily Max °C)
    ไฟล์ .xml แต่เนื้อในเป็น JSON — cache 30 นาทีสำหรับเดือนปัจจุบัน"""
    key = (year, month)
    now = datetime.now(ZoneInfo("Asia/Hong_Kong"))
    if key in _HKO_CACHE and not no_cache:
        return _HKO_CACHE[key]
    url = "https://www.hko.gov.hk/cis/dailyExtract/dailyExtract_%d%02d.xml" % (year, month)
    ttl = 1800 if (year, month) == (now.year, now.month) else 86400
    try:
        j = json.loads(http_get(url, ttl=ttl, tag="hkom", no_cache=no_cache))
    except Exception:
        return {}
    out = {}
    for blk in j.get("stn", {}).get("data", []):
        mm = blk.get("month") or month
        for row in blk.get("dayData", []):
            try:
                out["%d-%02d-%02d" % (year, mm, int(row[0]))] = float(row[2])
            except (ValueError, TypeError, IndexError):
                continue
    _HKO_CACHE[key] = out
    return out


def hko_recent(days=60, no_cache=False):
    """daily max ของ HKO ย้อนหลัง N วัน (ข้ามเดือนได้)"""
    now = datetime.now(ZoneInfo("Asia/Hong_Kong")).date()
    d0 = now - timedelta(days=days)
    out = {}
    y, m = d0.year, d0.month
    while (y, m) <= (now.year, now.month):
        out.update(hko_month(y, m, no_cache))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return {t: v for t, v in out.items() if d0.isoformat() <= t <= now.isoformat()}


def hko_daily_max(cfg, target, no_cache=False):
    d = _date.fromisoformat(target)
    v = hko_month(d.year, d.month, no_cache).get(target)
    if v is not None:
        return v
    try:  # fallback: ตารางประวัติเต็ม (อาจล่าช้า ~1 เดือน)
        return hko_series(no_cache).get(target)
    except Exception:
        return None


def hko_series(no_cache=False):
    """CLMMAXT (ประวัติยาว แต่เดือนปัจจุบันมักยังไม่อัปเดต)"""
    url = ("https://data.weather.gov.hk/weatherAPI/opendata/opendata.php?dataType=CLMMAXT"
           "&lang=en&rformat=csv&station=HKO")
    txt = http_get(url, ttl=86400, tag="hko", no_cache=no_cache)
    out = {}
    for row in csv.reader(io.StringIO(txt.lstrip("\ufeff"))):
        if len(row) >= 4 and row[0].strip().isdigit() and row[1].strip().isdigit() and row[2].strip().isdigit():
            try:
                out[_date(int(row[0]), int(row[1]), int(row[2])).isoformat()] = float(row[3])
            except (ValueError, TypeError):
                continue
    return out


def obs_daily_max(cfg, target, no_cache=False):
    """ค่าสูงสุดที่ 'วัดได้จริง' ในวันนั้น (ใช้ยืนยัน / calibration)"""
    d0 = d1 = _date.fromisoformat(target)
    if cfg["obs"] == "hko":
        v = hko_daily_max(cfg, target, no_cache)
        return v, (1 if v is not None else 0)
    if cfg["obs"] == "nws":
        v, n = nws_obs_daily_max(cfg, target, no_cache)
        if v is not None:
            return v, n
    m = iem_daily_max(cfg, d0, d1, no_cache)
    return m.get(target), (1 if target in m else 0)


def obs_max_so_far(cfg, no_cache=False):
    """ค่าสูงสุดของ 'วันนี้' จนถึงตอนนี้ + ชั่วโมง local ปัจจุบัน"""
    tz = ZoneInfo(cfg["tz"])
    now = datetime.now(tz)
    today = now.strftime("%Y-%m-%d")
    if cfg["obs"] == "hko":
        v = hko_daily_max(cfg, today, no_cache)
        if v is not None:
            return v, now.hour
        ap = iem_daily_max(dict(cfg, icao="VHHH"), _date.fromisoformat(today),
                           _date.fromisoformat(today), no_cache)
        return ap.get(today), now.hour
    m = iem_daily_max(cfg, _date.fromisoformat(today), _date.fromisoformat(today), no_cache)
    if m.get(today) is not None:
        return m[today], now.hour
    if cfg["obs"] == "nws":
        v, n = nws_obs_daily_max(cfg, today, no_cache)
        if v is not None and n > 0:
            return v, now.hour
    return None, now.hour


# ────────────────────────────── Calibration ──────────────────────────────
def model_history(cfg, models, days, no_cache=False):
    url = ("https://historical-forecast-api.open-meteo.com/v1/forecast?latitude=%.4f&longitude=%.4f"
           "&daily=temperature_2m_max&models=%s&past_days=%d&forecast_days=0&timezone=%s"
           % (cfg["lat"], cfg["lon"], ",".join(models), min(days, 92), urllib.parse.quote(cfg["tz"])))
    try:
        d = get_json(url, ttl=21600, tag="hist", no_cache=no_cache)
    except Exception:
        return {}
    daily = d.get("daily", {})
    out = {}
    for k, v in daily.items():
        if k == "time":
            continue
        m = k.replace("temperature_2m_max_", "")
        out[m] = {t: x for t, x in zip(daily["time"], v) if x is not None}
    return out


def calibrate(cfg, days=60, no_cache=False):
    """เทียบพยากรณ์ย้อนหลังกับค่าจริง → {model: dict(bias, rmse, n)} (เป็น °C)"""
    today = datetime.now(ZoneInfo(cfg["tz"])).date()
    d0 = today - timedelta(days=days)
    if cfg["obs"] == "hko":
        obs = hko_recent(days, no_cache)
    else:
        obs = iem_daily_max(cfg, d0, today, no_cache)
    if not obs:
        return {}
    models = ENS_MODELS + (DET_CONUS if cfg.get("country") == "US" else []) + DET_GLOBAL
    hist = model_history(cfg, models, days, no_cache)
    stats = {}
    for m, series in hist.items():
        pairs = [(v, obs[t]) for t, v in series.items() if t in obs]
        if len(pairs) < 8:
            continue
        err = np.array([v - o for v, o in pairs])
        stats[m] = dict(bias=float(err.mean()), rmse=float(np.sqrt((err ** 2).mean())),
                        mae=float(np.abs(err).mean()), n=len(pairs))
    return stats


# ────────────────────────────── Polymarket ──────────────────────────────
def parse_bin(label):
    lab = label.strip()
    m = re.match(r"^(-?\d+)°([FC]) or below$", lab)
    if m:
        return float("-inf"), int(m.group(1))
    m = re.match(r"^(-?\d+)°([FC]) or higher$", lab)
    if m:
        return int(m.group(1)), float("inf")
    m = re.match(r"^(-?\d+)-(-?\d+)°([FC])$", lab)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r"^(-?\d+)°([FC])$", lab)
    if m:
        return int(m.group(1)), int(m.group(1))
    return None


def polymarket_event(city, target, no_cache=False):
    d = _date.fromisoformat(target)
    slug = "highest-temperature-in-%s-on-%s-%d-%d" % (city, d.strftime("%B").lower(), d.day, d.year)
    try:
        j = get_json("https://gamma-api.polymarket.com/events?slug=" + slug, ttl=60, tag="pm", no_cache=no_cache)
        if isinstance(j, list) and j:
            return j[0]
    except Exception:
        pass
    # fallback: ค้นจาก tag
    try:
        evs = get_json("https://gamma-api.polymarket.com/events?closed=false&limit=200&order=volume24hr"
                       "&ascending=false&tag_slug=weather", ttl=120, tag="pmw", no_cache=no_cache)
        for e in evs:
            if e.get("slug") == slug:
                return e
    except Exception:
        pass
    return None


def event_bins(ev):
    out = []
    for m in ev.get("markets", []):
        lab = m.get("groupItemTitle") or m.get("question")
        rng = parse_bin(lab or "")
        if not rng:
            continue
        try:
            prices = json.loads(m.get("outcomePrices") or "[]")
        except (json.JSONDecodeError, TypeError):
            prices = []
        yes = float(prices[0]) if prices else None
        out.append(dict(label=lab, lo=rng[0], hi=rng[1], yes=yes,
                        volume=float(m.get("volume") or 0),
                        bid=m.get("bestBid"), ask=m.get("bestAsk"),
                        liquidity=float(m.get("liquidity") or 0)))
    out.sort(key=lambda b: (b["lo"] == float("-inf"), b["lo"]))
    return out


# ────────────────────────────── แกนหลัก: พยากรณ์ → ความน่าจะเป็น ──────────────────────────────
def predict(cfg, target, days_cal=60, n=20000, no_cache=False, verbose=True):
    tz = ZoneInfo(cfg["tz"])
    now = datetime.now(tz)
    today = now.strftime("%Y-%m-%d")
    lead = (_date.fromisoformat(target) - _date.fromisoformat(today)).days

    ens = ens_daily_max(cfg, target, no_cache)
    det = det_daily_max(cfg, target, no_cache)
    grid = nws_grid_daily_max(cfg, target, no_cache)
    if grid is not None:
        det["nws_grid"] = grid

    # ── ensemble: รวมสมาชิกทั้งหมดพร้อมน้ำหนักต่อโมเดล (ตีความแบบ weighted bootstrap)
    pooled, weights = [], []
    for m, arr in ens.items():
        pooled.append(arr)
        weights.append(np.full(arr.shape, ENS_W.get(m, 1.0)))
    if not pooled:
        raise RuntimeError("ไม่ได้รับข้อมูล ensemble — เช็ค lat/lon/tz หรือลองใหม่")
    members = np.concatenate(pooled)
    w = np.concatenate(weights)
    ens_mean_raw = float(np.average(members, weights=w))
    ens_spread = float(np.sqrt(np.average((members - ens_mean_raw) ** 2, weights=w)))

    # ── calibration / bias correction
    stats = calibrate(cfg, days=days_cal, no_cache=no_cache) if days_cal else {}
    # bias ของ ensemble เฉลี่ย = ค่า bias เฉลี่ยของสมาชิกที่ประกอบเป็น ensemble
    b_items = [(stats[m]["bias"], ENS_W.get(m, 1.0)) for m in ens if m in stats]
    ens_bias = (sum(b * wt for b, wt in b_items) / sum(wt for _, wt in b_items)) if b_items else 0.0
    members_adj = members - ens_bias

    # ── anchor จาก deterministic
    a_num, a_den, used = 0.0, 0.0, {}
    for m, v in det.items():
        a_num += DET_W.get(m, 0.7) * (v - stats.get(m, {}).get("bias", 0.0))
        a_den += DET_W.get(m, 0.7)
        used[m] = v - stats.get(m, {}).get("bias", 0.0)
    anchor = a_num / a_den if a_den else None
    ens_mean = ens_mean_raw - ens_bias
    if anchor is not None:
        shift = ANCHOR_WEIGHT * (anchor - ens_mean)
    else:
        shift = 0.0

    # ── residual noise: RMSE ดีสุดที่ calibrate ได้ เทียบกับ spread ที่มีอยู่แล้ว
    if stats:
        best_rmse = min(s["rmse"] for s in stats.values())
    else:
        best_rmse = DEFAULT_RMSE_C
    sigma = math.sqrt(max(best_rmse ** 2 - ens_spread ** 2, SIGMA_FLOOR_C ** 2))

    rng = np.random.default_rng(7)
    # weighted bootstrap จากสมาชิก + noise
    idx = rng.choice(len(members_adj), size=n, p=w / w.sum())
    samples = members_adj[idx] + shift + rng.normal(0, sigma, n)

    # ── ข้อมูลจริงของวันนี้ (ถ้ามี) → บีบการแจกแจง
    obs_so_far = peak_hour = None
    rem_used = False
    if target == today:
        obs_so_far, peak_hour = obs_max_so_far(cfg, no_cache)
        if obs_so_far is not None:
            # แนวทางหลัก: daily_max = max(ค่าที่วัดแล้ว, max ของชั่วโมงที่เหลือจาก ensemble)
            rem = ens_remaining_max(cfg, target, peak_hour, no_cache)
            if rem:
                r_members, r_w = [], []
                for m, arr in rem.items():
                    r_members.append(arr)
                    r_w.append(np.full(arr.shape, ENS_W.get(m, 1.0)))
                r_members = np.concatenate(r_members) - ens_bias * 0.3 + shift * 0.5
                r_w = np.concatenate(r_w)
                idx_r = rng.choice(len(r_members), size=n, p=r_w / r_w.sum())
                rem_samples = r_members[idx_r] + rng.normal(0, 0.35, n)
                samples = np.maximum(obs_so_far + rng.normal(0, 0.10, n), rem_samples)
                rem_used = True
            else:  # fallback เดิม: บีบ daily-max distribution ตามชั่วโมงที่ผ่านไป
                decay = max(0.10, math.exp(-(peak_hour - PEAK_HOUR) / 1.5)) if peak_hour >= PEAK_HOUR else 1.0
                excess = samples - obs_so_far
                samples = np.where(excess <= 0, obs_so_far + rng.normal(0, 0.12, n),
                                   obs_so_far + excess * decay)
            samples = np.maximum(samples, obs_so_far)

    # ── สรุปผล
    res = dict(city=cfg["city"], station=cfg["station"], icao=cfg["icao"], date=target,
               unit=cfg["unit"], lead_days=lead,
               ens_mean_c=round(ens_mean, 2), ens_spread_c=round(ens_spread, 2),
               bias_c=round(ens_bias, 2), anchor_c=round(anchor, 2) if anchor is not None else None,
               shift_c=round(shift, 2), sigma_res_c=round(sigma, 2),
               best_model_rmse_c=round(best_rmse, 2),
               det_models={k: round(v, 2) for k, v in used.items()},
               members_n=int(len(members)),
               obs_so_far_c=None if obs_so_far is None else round(obs_so_far, 2),
               obs_ok=bool(obs_so_far is not None), rem_used=rem_used,
               local_hour=peak_hour,
               mean_c=round(float(samples.mean()), 2),
               p05_c=round(float(np.percentile(samples, 5)), 1),
               p95_c=round(float(np.percentile(samples, 95)), 1),
               calib_n=(max((s["n"] for s in stats.values()), default=0)))
    return res, samples


def bin_probabilities(samples, bins, unit):
    """แจกแจงความน่าจะเป็นลง bin ของตลาด (ปัดเป็นจำนวนเต็มในหน่วยของตลาดก่อน)"""
    x = samples * 9 / 5 + 32 if unit == "F" else samples
    xi = np.floor(x + 0.5)
    out = []
    for b in bins:
        lo, hi = b["lo"], b["hi"]
        p = float(np.mean((xi >= lo) & (xi <= hi)))
        out.append({**b, "p": p})
    return out


def edge_table(bins, min_edge=0.03, min_vol=0.0):
    rows = []
    for b in bins:
        if b["yes"] is None:
            continue
        price = b["yes"]
        p = b["p"]
        edge = p - price
        ev = (p / price - 1) if price > 0 else float("inf")
        kelly = (p - price) / (1 - price) if 0 < price < 1 else 0.0
        rows.append(dict(label=b["label"], lo=b["lo"], hi=b["hi"], p=p, price=price, edge=edge, ev=ev,
                         kelly=max(kelly, 0.0), volume=b["volume"],
                         actionable=(edge > min_edge and b["volume"] >= min_vol and 0.005 < price < 0.995)))
    return rows


# ────────────────────────────── Commands ──────────────────────────────
def load_cfg(city):
    cfgs = json.load(open(STATIONS))
    if city not in cfgs:
        raise SystemExit("ไม่รู้จักเมือง '%s' — ดูรายชื่อ: python3 wxedge.py stations" % city)
    return cfgs[city]


def cmd_predict(args):
    cfg = load_cfg(args.city)
    target = args.date or datetime.now(ZoneInfo(cfg["tz"])).strftime("%Y-%m-%d")
    res, samples = predict(cfg, target, days_cal=args.days_cal, no_cache=args.no_cache)
    ev = polymarket_event(cfg["city"], target, args.no_cache)
    print("=" * 78)
    print("  %s (%s) | %s | หน่วยตลาด: °%s | lead %d วัน"
          % (res["station"], res["icao"], target, res["unit"], res["lead_days"]))
    print("  ensemble: mean %.2f°C spread %.2f | bias %+.2f | anchor %s | sigma_res %.2f"
          % (res["ens_mean_c"], res["ens_spread_c"], res["bias_c"],
             "%.2f" % res["anchor_c"] if res["anchor_c"] is not None else "n/a", res["sigma_res_c"]))
    if res["lead_days"] == 0 and res["obs_so_far_c"] is None:
        print("  ⚠️  ดึงค่าจริงของวันนี้ไม่ได้ → ยังไม่ได้ใช้ข้อมูล observation บีบการแจกแจง")
    print("  ช่วง 5–95%%: %.1f – %.1f °C   (ค่าจริงวันนี้จนถึง %.02d:00 น. = %s)"
          % (res["p05_c"], res["p95_c"], res["local_hour"] or 0,
             ("%.1f °C" % res["obs_so_far_c"]) if res["obs_so_far_c"] is not None else "n/a"))
    print("  deterministic:", ", ".join("%s=%.1f" % (k, v) for k, v in sorted(res["det_models"].items())))
    print("=" * 78)
    if not ev:
        print("  (ยังไม่พบตลาดของวันนี้ — แสดงเฉพาะการแจกแจง)")
        for b in bin_probabilities(samples, [], res["unit"]):
            pass
        return
    bins = bin_probabilities(samples, event_bins(ev), res["unit"])
    rows = edge_table(bins, args.min_edge, args.min_vol)
    print("  %-18s %7s %7s %8s %8s %7s  %s" % ("bin", "model", "ตลาด", "edge", "EV/$", "kelly", ""))
    for r in rows:
        flag = "  <== VALUE" if r["actionable"] else ""
        print("  %-18s %6.1f%% %6.1f%% %+7.1f%% %+7.1f%% %6.2f%s"
              % (r["label"], r["p"] * 100, r["price"] * 100, r["edge"] * 100, r["ev"] * 100, r["kelly"], flag))
    tot = sum(r["price"] for r in rows)
    print("  Σ price = %.3f (overround %+.1f%%) | Σ model p = %.3f" % (tot, (tot - 1) * 100,
                                                                     sum(r["p"] for r in rows)))
    best = max(rows, key=lambda r: r["edge"])
    print("  มากสุด: %s → model %.1f%% vs ตลาด %.1f%% (edge %+.1f%%)"
          % (best["label"], best["p"] * 100, best["price"] * 100, best["edge"] * 100))
    if args.json:
        os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
        json.dump(dict(meta=res, rows=rows), open(args.json, "w"), indent=1)
        print("  → เขียน %s" % args.json)


def cmd_scan(args):
    cfgs = json.load(open(STATIONS))
    evs = get_json("https://gamma-api.polymarket.com/events?closed=false&limit=200&order=volume24hr"
                   "&ascending=false&tag_slug=weather", ttl=120, tag="pmw", no_cache=args.no_cache)
    evs = [e for e in evs if e.get("slug", "").startswith("highest-temperature-in")]
    if args.city:
        evs = [e for e in evs if e["slug"].startswith("highest-temperature-in-" + args.city + "-on-")]
    evs.sort(key=lambda e: -(e.get("volume24hr") or 0))
    evs = evs[:args.limit]

    all_rows, meta = [], []
    for e in evs:
        m = re.match(r"highest-temperature-in-(.+)-on-([a-z]+)-(\d+)-(\d{4})$", e["slug"])
        if not m:
            continue
        city, month, day, year = m.groups()
        cfg = cfgs.get(city)
        if not cfg:
            continue
        try:
            target = datetime.strptime("%s %s %s" % (month, day, year), "%B %d %Y").strftime("%Y-%m-%d")
        except ValueError:
            continue
        if args.date and target != args.date:
            continue
        try:
            res, samples = predict(cfg, target, days_cal=args.days_cal, no_cache=args.no_cache)
        except Exception as ex:
            print("  ! %s: %s" % (city, ex))
            continue
        bins = bin_probabilities(samples, event_bins(e), cfg["unit"])
        rows = edge_table(bins, args.min_edge, args.min_vol)
        vol = e.get("volume24hr") or 0
        meta.append(dict(city=city, date=target, volume24hr=vol, unit=cfg["unit"], **{
            k: res[k] for k in ("ens_mean_c", "sigma_res_c", "obs_so_far_c", "lead_days", "station")}))
        for r in rows:
            r.update(city=city, date=target, unit=cfg["unit"], volume24hr=vol, icao=cfg["icao"])
            all_rows.append(r)
        top = max(rows, key=lambda r: r["edge"]) if rows else None
        _mv = res["ens_mean_c"] * 9 / 5 + 32 if cfg["unit"] == "F" else res["ens_mean_c"]
        print("  %-14s %s (%.1f°%s) | lead %d | %s" % (
            city, target, _mv, cfg["unit"], res["lead_days"],
            ("%s %.1f%% vs %.1f%% (edge %+.1f%%)" % (top["label"], top["p"] * 100, top["price"] * 100,
                                                     top["edge"] * 100)) if top else "ไม่มีตลาด"))

    if not all_rows:
        return
    import collections
    by_bin = collections.defaultdict(list)
    for r in all_rows:
        by_bin[(r["city"], r["date"])].append(r)
    # เขียน report
    os.makedirs(REPORTS, exist_ok=True)
    path = args.out or os.path.join(REPORTS, "scan_%s.md" % datetime.now().strftime("%Y%m%d_%H%M"))
    with open(path, "w", encoding="utf-8") as f:
        f.write("# wxedge scan — %s\n\n" % datetime.now().strftime("%Y-%m-%d %H:%M"))
        f.write("แหล่งพยากรณ์: Open-Meteo ensemble (ECMWF EPS + GEFS + ICON EPS), "
                "NBM/HRRR/NWS gridpoint, bias-corrected จาก IEM ASOS\n\n")
        for (city, date), rows in sorted(by_bin.items(), key=lambda kv: -max(r["volume24hr"] for r in kv[1])):
            f.write("## %s — %s (vol24h $%s)\n\n" % (city, date, format(int(rows[0]["volume24hr"]), ",")))
            f.write("| bin | model p | ราคาตลาด | edge | EV/$ | kelly | vol |\n|---|---|---|---|---|---|---|\n")
            for r in sorted(rows, key=lambda r: r["lo"]):
                star = "**" if r["actionable"] else ""
                f.write("| %s%s%s | %.1f%% | %.1f%% | %+.1f%% | %+.1f%% | %.3f | $%.0f |\n"
                        % (star, r["label"], star, r["p"] * 100, r["price"] * 100, r["edge"] * 100,
                           r["ev"] * 100, r["kelly"], r["volume"]))
            f.write("\n")
    print("\nเขียนรายงาน: %s" % path)

    # สรุปรวมเฉพาะที่คุ้ม
    act = [r for r in all_rows if r["actionable"]]
    act.sort(key=lambda r: -r["edge"])
    print("\nรวม %d bin ที่ edge > %.0f%% (จาก %d เมือง x bin)" % (len(act), args.min_edge * 100, len(evs)))
    print("  %-14s %-11s %-16s %7s %7s %7s %7s" % ("city", "date", "bin", "model", "ตลาด", "edge", "vol24h"))
    for r in act[:args.top]:
        print("  %-14s %-11s %-16s %6.1f%% %6.1f%% %+6.1f%% $%s"
              % (r["city"], r["date"], r["label"], r["p"] * 100, r["price"] * 100, r["edge"] * 100,
                 format(int(r["volume24hr"]), ",")))


def cmd_calibrate(args):
    cfg = load_cfg(args.city)
    st = calibrate(cfg, days=args.days, no_cache=args.no_cache)
    if not st:
        print("calibrate ไม่สำเร็จ (ข้อมูลไม่พอ / เมืองนี้ไม่มี obs)")
        return
    print("%s (%s) — เทียบ %d วันย้อนหลังกับค่าจริง\n" % (cfg["city"], cfg["icao"], args.days))
    print("  %-24s %8s %8s %8s %5s" % ("model", "bias C", "RMSE C", "MAE C", "n"))
    for m, s in sorted(st.items(), key=lambda kv: kv[1]["rmse"]):
        print("  %-24s %+8.2f %8.2f %8.2f %5d" % (m, s["bias"], s["rmse"], s["mae"], s["n"]))
    print("\n(bias = พยากรณ์ − ค่าจริง, + = โมเดลร้อนเกิน | RMSE = ความคลาดเคลื่อนรวม °C)")


def ens_archive(cfg, days, no_cache=False):
    """ensemble ของวันย้อนหลัง (archived run ~ lead 1 วัน) → {date: (members, weights)}"""
    url = ("https://ensemble-api.open-meteo.com/v1/ensemble?latitude=%.4f&longitude=%.4f"
           "&daily=temperature_2m_max&hourly=temperature_2m&models=%s&past_days=%d&forecast_days=1&timezone=%s"
           % (cfg["lat"], cfg["lon"], ",".join(ENS_MODELS), min(days, 92), urllib.parse.quote(cfg["tz"])))
    try:
        d = get_json(url, ttl=21600, tag="ensa", no_cache=no_cache)
    except Exception:
        return {}
    daily = d.get("daily", {})
    # นับชั่วโมงที่มีข้อมูลต่อวัน (คอลัมน์แรกของ member แรก) — archive บางวันมีไม่ครบชั่วโมง
    hours_ok = {}
    hh = d.get("hourly", {})
    cols0 = [k for k in hh if k != "time" and "member01" in k][:5]
    for t in times_all if False else set(x[:10] for x in hh.get("time", [])):
        hours_ok[t] = 0
    for c in cols0:
        for t, v in zip(hh.get("time", []), hh.get(c, [])):
            if v is not None:
                hours_ok[t[:10]] = hours_ok.get(t[:10], 0) + 1
    times = daily.get("time", [])
    groups = ens_members(daily)
    n_cols = sum(len(v) for v in groups.values())
    per_day = {t: ([], []) for t in times}
    for m, cols in groups.items():
        wt = ENS_W.get(m, 1.0)
        for c in cols:
            v = daily[c]
            for i, t in enumerate(times):
                if v[i] is not None:
                    per_day[t][0].append(v[i])
                    per_day[t][1].append(wt)
    # กรองวันข้อมูล sparse (Open-Meteo archive ของ ensemble อาจมีแค่บางชั่วโมง → daily max ต่ำเกินจริง)
    out = {}
    per_col_hours = max(len(cols0), 1)
    for t, (a, b) in per_day.items():
        if len(a) < max(8, 0.4 * n_cols):
            continue
        # ต้องมีข้อมูลรายชั่วโมงจริง ≥ 80% ของ 24 ชม. ต่อคอลัมน์ที่สุ่มตรวจ
        if hours_ok.get(t, 0) < 0.8 * 24 * per_col_hours:
            continue
        out[t] = (np.array(a), np.array(b))
    return out


def bin_key(x, unit):
    """คีย์ bin ของตลาด: °F กว้าง 2 องศา (ผูกกับเลขคู่), °C กว้าง 1 องศา"""
    if unit == "F":
        f = math.floor(x * 9 / 5 + 32 + 0.5)
        return f - (f % 2)
    return math.floor(x + 0.5)


def cmd_verify(args):
    """Backtest: ใช้ ensemble รุ่นที่เก็บไว้ (~lead 1 วัน) เทียบค่าจริง → hit rate + Brier + calibration"""
    cfgs = json.load(open(STATIONS))
    cities = [args.city] if args.city else [c for c, v in cfgs.items()
                                           if (v["vol24h"] or 0) > 0][:args.cities]
    stats_by_city, pooled, rows = {}, [], []
    for city in cities:
        cfg = cfgs.get(city)
        if not cfg:
            continue
        arch = ens_archive(cfg, args.days, args.no_cache)
        if not arch:
            continue
        cal = calibrate(cfg, days=args.days_cal, no_cache=args.no_cache)
        bias_items = []
        for m in ENS_MODELS:
            if m in cal:
                bias_items.append((cal[m]["bias"], ENS_W.get(m, 1.0)))
        ens_bias = (sum(b * w for b, w in bias_items) / sum(w for _, w in bias_items)) if bias_items else 0.0
        best_rmse = min([s["rmse"] for s in cal.values()], default=DEFAULT_RMSE_C)
        rng = np.random.default_rng(11)
        tz_now = datetime.now(ZoneInfo(cfg["tz"]))
        today_s = tz_now.strftime("%Y-%m-%d")
        past = [t for t in sorted(arch) if t < today_s]
        if not past:
            continue
        d0 = _date.fromisoformat(past[0]); d1 = _date.fromisoformat(past[-1])
        if cfg["obs"] == "hko":
            ser = {t: v for t, v in hko_recent(args.days + 2, args.no_cache).items()
                   if past[0] <= t <= past[-1]}
        elif cfg["obs"] == "nws":
            ser = {}
            for t in past:
                val, _n = nws_obs_daily_max(cfg, t, args.no_cache)
                if val is not None:
                    ser[t] = val
            if not ser:
                ser = iem_daily_max(cfg, d0, d1, args.no_cache)
        else:
            ser = iem_daily_max(cfg, d0, d1, args.no_cache)
        for t, (mem, wt) in sorted(arch.items()):
            if t not in past:
                continue
            v = ser.get(t)
            if v is None:
                continue
            m_adj = mem - ens_bias
            mu = float(np.average(m_adj, weights=wt))
            spread = float(np.sqrt(np.average((m_adj - mu) ** 2, weights=wt)))
            sigma = math.sqrt(max(best_rmse ** 2 - spread ** 2, SIGMA_FLOOR_C ** 2))
            idx = rng.choice(len(m_adj), size=8000, p=wt / wt.sum())
            samples = m_adj[idx] + rng.normal(0, sigma, 8000)
            keys = np.array([bin_key(x, cfg["unit"]) for x in samples])
            actual = bin_key(v, cfg["unit"])
            uk = np.unique(keys)
            probs = {int(k): float(np.mean(keys == k)) for k in uk}
            p_act = probs.get(actual, 0.0)
            top = max(probs, key=probs.get)
            brier = sum((p - (1 if k == actual else 0)) ** 2 for k, p in probs.items())
            std = 2 if cfg["unit"] == "F" else 1
            actual_msgs = []
            if cfg["unit"] == "F":
                a_lo, a_hi = actual, actual + 1
                f_obs = v * 9 / 5 + 32
                actual_msgs = ["%.0f-%.0f°F" % (a_lo, a_hi), "obs %.0f°F" % f_obs]
            else:
                actual_msgs = ["%d°C" % actual, "obs %.1f°C" % v]
            unit_name = "F" if cfg["unit"] == "F" else "C"
            top_w = top + (1 if cfg["unit"] == "F" else 0)
            rows.append((city, t, top_w, 1 if unit_name == "F" else 0, actual, p_act, top == actual, brier))
            pooled.append((p_act, top == actual, brier))
            if args.verbose:
                print("  %-13s %s  โมเดล: %d-%d°%s (%.0f%%) | จริง: %d-%d°%s (%.0f%% ของมวล) %s"
                      % (city, t, top, top + (1 if cfg["unit"] == "F" else 0), cfg["unit"],
                         probs[top] * 100, actual, actual + (1 if cfg["unit"] == "F" else 0), cfg["unit"],
                         p_act * 100, "ok" if top == actual else "MISS"))
    if not pooled:
        print("ไม่มีข้อมูลพอสำหรับ backtest")
        return
    import statistics
    p_act = [x[0] for x in pooled]
    hit = [x[1] for x in pooled]
    brier = [x[2] for x in pooled]
    print("\n  จำนวนวันทดสอบ: %d วัน-เมือง (จาก %d เมือง, ensemble รุ่นเก็บไว้ ~lead 1 วัน)"
          % (len(pooled), len(set(r[0] for r in rows))))
    print("  bin ที่ให้ความน่าจะเป็นสูงสุด = bin จริง:  %.0f%%  (hit rate)" % (100 * sum(hit) / len(hit)))
    print("  ความน่าจะเป็นเฉลี่ยที่ให้กับ bin จริง:   %.1f%%  (มี bias เกิน/ขาด?)" % (100 * statistics.mean(p_act)))
    print("  Brier score: %.3f  (ต่ำดี; 0 = สมบูรณ์แบบ)" % statistics.mean(brier))
    print("  คลุม bin จริงด้วย bin ที่ p สูงสุด + เพื่อนบ้าน: (ดู --verbose)")
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write("# wxedge backtest — %s\n\n" % datetime.now().strftime("%Y-%m-%d %H:%M"))
            f.write("- ensemble: ECMWF EPS + GEFS + ICON EPS (รุ่นที่เก็บไว้, lead ~1 วัน)\n")
            f.write("- bias-correct จาก %d วันย้อนหลังเทียบ IEM ASOS/METAR\n\n" % args.days_cal)
            f.write("| เมือง | วันที่ | โมเดลบอก (top bin) | จริง | p(จริง) | hit | brier |\n|---|---|---|---|---|---|---|\n")
            for c, t, top, isf, actual, p_act_, ok, br in rows:
                u = "F" if isf else "C"
                w = 1 if isf else 0
                f.write("| %s | %s | %d-%d°%s | %d-%d°%s | %.0f%% | %s | %.3f |\n"
                        % (c, t, top, top + w, u, actual, actual + w, u, p_act_ * 100, "✅" if ok else "❌", br))
            f.write("\n**สรุป:** hit rate %.0f%% | p(จริง) เฉลี่ย %.1f%% | Brier %.3f\n"
                    % (100 * sum(hit) / len(hit), 100 * statistics.mean(p_act), statistics.mean(brier)))
        print("  → เขียน %s" % args.out)


# ────────────────────────────── hitrate / advise (สำหรับ LLM) ──────────────────────────────
def load_hitrate(path):
    """อ่าน dataset แล้วสรุป hit rate ต่อเมือง (top-1 / top-2)"""
    import csv as _csv, statistics as _st
    from collections import defaultdict
    rows = list(_csv.DictReader(open(path, encoding="utf-8")))
    per = defaultdict(list)
    for r in rows:
        per[r["city"]].append(r)
    out = {}
    for city, rs in per.items():
        unit = rs[0]["unit"]
        hit1 = hit2 = 0
        for r in rs:
            mu, sig, obs = float(r["mu_c"]), float(r["sigma_c"]), float(r["obs_c"])
            bins = market_bins(unit)
            p = prob_dist(mu, sig, unit, bins)
            order = sorted(p.items(), key=lambda kv: -kv[1])
            lab = bucket_label(obs, unit)
            hit1 += (order[0][0] == lab)
            hit2 += (lab in [k for k, _ in order[:2]])
        out[city] = dict(n=len(rs), unit=unit, top1=hit1 / len(rs), top2=hit2 / len(rs),
                         mae_c=_st.mean([abs(float(r["mu_c"]) - float(r["obs_c"])) for r in rs]),
                         period="%s..%s" % (min(r["date"] for r in rs), max(r["date"] for r in rs)))
    return out


def market_bins(unit):
    """bin มาตรฐานของตลาด: °F กว้าง 2 องศา (ผูกเลขคู่), °C กว้าง 1 องศา
    ครอบช่วงกว้างพอสำหรับทุกเมือง (ไม่ผูกกับ bin ที่ตลาดใดตลาดหนึ่งโชว์)"""
    if unit == "F":
        return [(b, b + 1) for b in range(0, 131, 2)]
    return [(b, b) for b in range(-40, 61)]


def prob_dist(mu_c, sigma_c, unit, bins=None):
    if unit == "F":
        m, s = mu_c * 9 / 5 + 32, sigma_c * 9 / 5
    else:
        m, s = mu_c, sigma_c
    out = {}
    for b in (bins or market_bins(unit)):
        lo, hi = b
        out[label_str(b, unit)] = float(norm_cdf((hi + 0.5 - m) / s) - norm_cdf((lo - 0.5 - m) / s))
    return out


def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def label_str(b, unit):
    lo, hi = b
    return ("%d-%d°%s" % (lo, hi, unit)) if lo != hi else ("%d°%s" % (lo, unit))


def bucket_label(obs_c, unit):
    """bin ที่ค่าจริงตกอยู่ (ใช้กติกาปัดเดียวกับตลาด)"""
    if unit == "F":
        v = math.floor(obs_c * 9 / 5 + 32 + 0.5)
        return label_str((v - (v % 2), v - (v % 2) + 1), unit)
    v = math.floor(obs_c + 0.5)
    return label_str((v, v), unit)


def cmd_hitrate(args):
    hr = load_hitrate(args.data)
    items = sorted(hr.items(), key=lambda kv: -kv[1]["top1"])
    if args.json:
        print(json.dumps({c: {**v, "top1": round(v["top1"], 3), "top2": round(v["top2"], 3),
                              "mae_c": round(v["mae_c"], 2)} for c, v in items},
                         ensure_ascii=False, indent=1))
        return
    print("hit rate ย้อนหลัง (ทาย bucket ก่อนวันมาถึง) จาก %s\n" % args.data)
    print("  %-14s %5s %8s %8s %8s   %s" % ("city", "n", "top-1", "top-2", "MAE°C", "ช่วงวันที่"))
    for c, v in items:
        flag = "★" if v["top1"] >= 0.85 and v["n"] >= args.min_n else ("·" if v["top1"] >= 0.7 else " ")
        print("  %-14s %5d %7.1f%% %7.1f%% %8.2f %s %s" % (
            c, v["n"], v["top1"] * 100, v["top2"] * 100, v["mae_c"], v["period"], flag))
    n_all = sum(v["n"] for _, v in items)
    h1 = sum(v["top1"] * v["n"] for _, v in items) / n_all
    h2 = sum(v["top2"] * v["n"] for _, v in items) / n_all
    print("\n  รวม %d วัน-เมือง: top-1 = %.1f%% | top-2 = %.1f%%" % (n_all, h1 * 100, h2 * 100))


def cmd_advise(args):
    """คำตอบเดียวจบสำหรับ LLM/บอท: bin ที่ควรทาย + ความมั่นใจ + สถิติย้อนหลัง"""
    cfg = load_cfg(args.city)
    target = args.date or datetime.now(ZoneInfo(cfg["tz"])).strftime("%Y-%m-%d")
    res, samples = predict(cfg, target, days_cal=args.days_cal, no_cache=args.no_cache)
    ev = polymarket_event(cfg["city"], target, args.no_cache)
    out = dict(city=cfg["city"], station=cfg["station"], icao=cfg["icao"], date=target,
               unit=cfg["unit"], lead_days=res["lead_days"],
               forecast=dict(mean_c=res["mean_c"], p05_c=res["p05_c"], p95_c=res["p95_c"],
                             obs_so_far_c=res["obs_so_far_c"], bias_c=res["bias_c"],
                             sigma_c=res["sigma_res_c"], members=res["members_n"]))
    # สถิติย้อนหลังของเมืองนี้
    hr = {}
    if os.path.exists(args.hitrate_data):
        hr = load_hitrate(args.hitrate_data).get(cfg["city"], {})
    out["history"] = {k: (round(v, 3) if isinstance(v, float) else v)
                      for k, v in hr.items() if k in ("n", "top1", "top2", "mae_c", "period")}
    rec = None
    if ev:
        bins = bin_probabilities(samples, event_bins(ev), cfg["unit"])
        rows = edge_table(bins, args.min_edge, args.min_vol)
        out["bins"] = [{k: (round(v, 3) if isinstance(v, float) else v)
                        for k, v in r.items() if k in ("label", "p", "price", "edge", "ev", "kelly",
                                                       "volume", "actionable")} for r in rows]
        top = max(rows, key=lambda r: r["p"])
        # เพื่อนบ้านที่ติดกัน (บน/ล่าง) = ทางเลือกสำรองที่ถูกต้องตามธรรมชาติของ bin
        idx = rows.index(top)
        cands = [rows[i] for i in (idx - 1, idx + 1) if 0 <= i < len(rows)]
        second = max(cands, key=lambda r: r["p"]) if cands else None
        best_edge = max(rows, key=lambda r: r["edge"])
        rec = dict(bin=top["label"], p=round(top["p"], 3), price=top["price"])
        if second:
            rec["coverage_bins"] = sorted([top["label"], second["label"]])
            rec["coverage_p"] = round(top["p"] + second["p"], 3)
        # แนะนำกลยุทธ์ตามความมั่นใจ
        if top["p"] >= 0.55:
            rec["strategy"] = "top-1"
        elif second and top["p"] + second["p"] >= 0.75:
            rec["strategy"] = "top-2 (เล่น 2 bin ติดกัน)"
        else:
            rec["strategy"] = "รอ/เลี่ยงวันนี้ (การแจกแจงกว้างเกินราคา)"
        rec["value_bet"] = dict(bin=best_edge["label"], edge=round(best_edge["edge"], 3),
                                price=best_edge["price"], kelly=round(best_edge["kelly"], 3),
                                actionable=best_edge["actionable"])
    out["recommendation"] = rec
    warns = []
    if res["lead_days"] >= 4:
        warns.append("lead ≥4 วัน: ความไม่แน่นอนสูงกว่า bin — ควรเล่นแบบ top-2")
    if res["lead_days"] == 0 and not res["obs_ok"]:
        warns.append("ดึง observation วันนี้ไม่ได้ → ยังไม่ใช้ข้อมูลจริงบีบการแจกแจง")
    if hr and hr.get("top1", 0) < 0.45:
        warns.append("เมืองนี้ hit rate ย้อนหลังต่ำมาก (%.0f%% จาก %d วัน) — ควรเลี่ยง" % (hr["top1"] * 100, hr["n"]))
    elif hr and hr.get("top1", 0) < 0.55:
        warns.append("hit rate ย้อนหลังระดับกลาง (%.0f%%) — ใช้กลยุทธ์ top-2" % (hr["top1"] * 100))
    elif hr:
        warns.append("เมืองนี้ hit rate ย้อนหลังดี (%.0f%% จาก %d วัน)" % (hr["top1"] * 100, hr["n"]))
    out["warnings"] = warns
    print(json.dumps(out, ensure_ascii=False, indent=1))
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        json.dump(out, open(args.out, "w"), ensure_ascii=False, indent=1)


def cmd_stations(args):
    cfgs = json.load(open(STATIONS))
    print("  %-14s %-6s %-4s %-20s %-28s %s" % ("city", "icao", "unit", "tz", "station", "obs"))
    for c, v in cfgs.items():
        print("  %-14s %-6s %-4s %-20s %-28s %s" % (c, v["icao"], v["unit"], v["tz"],
                                                     (v["station"] or "")[:28], v["obs"]))
    print("\nรวม %d เมือง" % len(cfgs))


def main():
    ap = argparse.ArgumentParser(description="wxedge — weather edge สำหรับ Polymarket temperature markets")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("predict", help="ทำนายเมืองเดียวเทียบราคาตลาด")
    p.add_argument("city")
    p.add_argument("--date")
    p.add_argument("--days-cal", type=int, default=60, help="จำนวนวันย้อนหลังสำหรับ bias/RMSE (0=ปิด)")
    p.add_argument("--min-edge", type=float, default=0.03)
    p.add_argument("--min-vol", type=float, default=0.0)
    p.add_argument("--json")
    p.add_argument("--no-cache", action="store_true")
    p.set_defaults(func=cmd_predict)

    p = sub.add_parser("scan", help="สแกนทุกตลาดที่ยังเปิดอยู่ หา bin ที่ราคาผิด")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--city")
    p.add_argument("--date")
    p.add_argument("--top", type=int, default=25)
    p.add_argument("--min-edge", type=float, default=0.04)
    p.add_argument("--min-vol", type=float, default=200.0)
    p.add_argument("--days-cal", type=int, default=45)
    p.add_argument("--out")
    p.add_argument("--no-cache", action="store_true")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("calibrate", help="วัด bias/RMSE ของแต่ละโมเดลกับสถานีนั้น")
    p.add_argument("city")
    p.add_argument("--days", type=int, default=60)
    p.add_argument("--no-cache", action="store_true")
    p.set_defaults(func=cmd_calibrate)

    p = sub.add_parser("verify", help="backtest: ensemble รุ่นย้อนหลัง vs ค่าจริง")
    p.add_argument("--days", type=int, default=14, help="จำนวนวันย้อนหลังที่ทดสอบ")
    p.add_argument("--days-cal", type=int, default=60)
    p.add_argument("--cities", type=int, default=8, help="จำนวนเมืองที่ทดสอบ (เรียงตาม volume)")
    p.add_argument("--city")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--out")
    p.add_argument("--no-cache", action="store_true")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("hitrate", help="hit rate ย้อนหลังต่อเมือง (จาก dataset)")
    p.add_argument("--data", default=os.path.join(HERE, "data", "dataset_full.csv"))
    p.add_argument("--min-n", type=int, default=30)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_hitrate)

    p = sub.add_parser("advise", help="คำตอบเดียวจบ (JSON) สำหรับ LLM/บอท")
    p.add_argument("city")
    p.add_argument("--date")
    p.add_argument("--days-cal", type=int, default=45)
    p.add_argument("--min-edge", type=float, default=0.03)
    p.add_argument("--min-vol", type=float, default=0.0)
    p.add_argument("--hitrate-data", default=os.path.join(HERE, "data", "dataset_full.csv"))
    p.add_argument("--out")
    p.add_argument("--no-cache", action="store_true")
    p.set_defaults(func=cmd_advise)

    p = sub.add_parser("stations", help="รายชื่อเมือง/สถานี")
    p.set_defaults(func=cmd_stations)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
