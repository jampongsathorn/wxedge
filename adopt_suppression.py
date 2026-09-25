#!/usr/bin/env python3
"""
adopt_suppression.py — ทดลองนำไอเดียจาก PolyWeather (dynamic_forecast) มาเสริม intraday.py
  1) Weather suppression: ฝน/พายุฝนฟ้าคะนอง/เมฆหนา ช่วงบ่าย → ลดความน่าจะเป็นของ bin ที่สูงเกิน
  2) Trend rate: ความชันอุณหภูมิจาก obs 2 จุดล่าสุด → ขยับ mu ไปทาง peak (แบบหน่วง)
  3) Hard floor: bin ที่ต่ำกว่า max_so_far แล้ว เป็นไปไม่ได้ (intraday.py ทำอยู่แล้วโดยปริยาย)

วัดกับ bucket ที่ตลาด resolve จริง (data/market_labels.csv) เทียบกับ baseline ของ intraday.py
รัน: python3 adopt_suppression.py --hours 15,16 --cities all
"""
import argparse, collections, csv, json, math, os, statistics as st, sys, urllib.parse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wxedge as W
import intraday as I

HERE = os.path.dirname(os.path.abspath(__file__))

RAIN = {"RA", "SHRA", "TSRA", "TS", "+RA", "-RA", "DZ", "-DZ", "+TSRA", "FZRA", "VCTS"}
HEAVY = {"TSRA", "+TSRA", "TS", "+RA", "FZRA", "VCTS"}


def wx_series(cfg, d0, d1):
    """ดึง wxcodes/skyc1/ดิวพอยต์ ต่อชั่วโมง (เวลาท้องถิ่น) → {date: {hour: {'wx':…, 'sky':…, 'dwp':…}}}"""
    out = collections.defaultdict(lambda: collections.defaultdict(dict))
    for field, key in (("wxcodes", "wx"), ("skyc1", "sky"), ("dwpc", "dwp")):
        url = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station=%s&data=%s"
               "&year1=%d&month1=%d&day1=%d&year2=%d&month2=%d&day2=%d&tz=%s&format=onlycomma"
               "&report_type=3&missing=M&trace=T&direct=no"
               % (cfg["icao"], field, d0.year, d0.month, d0.day,
                  (d1 + timedelta(days=1)).year, (d1 + timedelta(days=1)).month, (d1 + timedelta(days=1)).day,
                  urllib.parse.quote(cfg["tz"])))
        try:
            txt = W.http_get(url, ttl=3600, tag="iemwx")
        except Exception:
            continue
        import io
        for row in csv.DictReader(io.StringIO(txt)):
            v = (row.get(field) or "").strip()
            if not v or v in ("M", ""):
                continue
            day, hh = row["valid"][:10], int(row["valid"][11:13])
            out[day][hh][key] = v
    return out


def rain_flag(wx, hour, lookback=3):
    """ฝน/พายุ ในช่วง lookback ชั่วโมงก่อนหน้า (รวมชั่วโมงนั้น)"""
    codes = []
    for h in range(max(0, hour - lookback), hour + 1):
        c = (wx.get(h) or {}).get("wx") or ""
        codes += [t for t in c.replace("|", " ").split() if t]
    heavy = any(t in HEAVY for t in codes)
    rain = heavy or any(t in RAIN for t in codes)
    return rain, heavy


def cloud_flag(wx, hour):
    sky = ((wx.get(hour) or {}).get("sky") or "").upper()
    return sky in ("OVC", "BKN", "VV"), sky


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", default="14,15,16")
    ap.add_argument("--cities", default="all")
    ap.add_argument("--lookback", type=int, default=30)
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--out", default=os.path.join(HERE, "reports", "adopt_suppression.md"))
    a = ap.parse_args()
    hours = [int(x) for x in a.hours.split(",")]
    cfgs = json.load(open(W.STATIONS))
    cities = [c for c in cfgs if cfgs[c].get("icao")] if a.cities in ("", "all") else a.cities.split(",")
    labels = {}
    for r in csv.DictReader(open(os.path.join(HERE, "data", "market_labels.csv"), encoding="utf-8")):
        labels[(r["city"], r["date"])] = r

    rows = []
    for city in cities:
        cfg = cfgs[city]
        ds = sorted(d for (cc, d) in labels if cc == city)
        if not ds:
            continue
        d0 = datetime.strptime(ds[0], "%Y-%m-%d").date()
        d1 = datetime.strptime(ds[-1], "%Y-%m-%d").date()
        try:
            ser = I.hourly_series(cfg, d0 - timedelta(days=a.lookback + 2), d1)
            wx = wx_series(cfg, d0 - timedelta(days=a.lookback + 2), d1)
        except Exception as e:
            print("  %s: error %s" % (city, str(e)[:50]), flush=True)
            continue
        for day in ds:
            m = ser.get(day)
            if not m or day not in wx:
                continue
            hist = sorted(d for d in ser if d < day)[-a.lookback:]
            for h in hours:
                past = {hh: v for hh, v in m.items() if hh <= h}
                if len(past) < 3:
                    continue
                mx = max(past.values())
                incs = []
                for hd in hist:
                    p2 = {hh: v for hh, v in ser[hd].items() if hh <= h}
                    if len(p2) < 3:
                        continue
                    incs.append(max(ser[hd].values()) - max(p2.values()))
                if len(incs) < 8:
                    continue
                rain, heavy = rain_flag(wx[day], h)
                cloudy, sky = cloud_flag(wx[day], h)
                # trend rate: 2 จุดล่าสุด (องศา/ชม.)
                hs = sorted(past)
                trend = None
                if len(hs) >= 2 and hs[-1] - hs[-2] <= 3:
                    trend = (past[hs[-1]] - past[hs[-2]]) / max(1, hs[-1] - hs[-2])
                win = labels[(city, day)]["winner"]
                rows.append(dict(city=city, date=day, hour=h, unit=cfg["unit"], mx=mx, incs=incs,
                                 rain=rain, heavy=heavy, cloudy=cloudy, sky=sky, trend=trend, win=win))
        print("  %-14s เสร็จ (%d แถว)" % (city, len(rows)), flush=True)

    def predict(r, f_rain=1.0, f_heavy=1.0, f_cloud=1.0, trend_w=0.0, trend_hours=2.0):
        f = 1.0
        if r["heavy"]:
            f *= f_heavy
        elif r["rain"]:
            f *= f_rain
        if r["cloudy"]:
            f *= f_cloud
        shift = 0.0
        if trend_w and r["trend"] is not None:
            shift = max(-1.5, min(1.5, r["trend"] * trend_hours * trend_w))
        samples = [r["mx"] + x * f + shift for x in r["incs"]]
        vals = [s * 9 / 5 + 32 if r["unit"] == "F" else s for s in samples]
        cnt = collections.Counter(W.bucket_label(v, r["unit"]) for v in vals)
        return max(cnt, key=cnt.get)

    def rate(rs, **kw):
        n = len(rs)
        if not n:
            return 0.0, 0
        return 100 * sum(1 for r in rs if predict(r, **kw) == r["win"]) / n, n

    L = ["# ทดลองนำไอเดีย PolyWeather มาเสริมกติกา intraday (weather suppression + trend)\n",
         "วันที่ %s · ประเมินกับ bucket ที่ตลาด resolve จริง · %d เมือง-วัน-ชั่วโมง\n"
         % (datetime.now().strftime("%Y-%m-%d %H:%M"), len(rows))]
    variants = [
        ("baseline (ของเราเดิม)", dict()),
        ("ฝน → ×0.85", dict(f_rain=0.85)),
        ("ฝน → ×0.70, พายุ → ×0.55", dict(f_rain=0.70, f_heavy=0.55)),
        ("ฝน ×0.70 + เมฆ OVC ×0.85", dict(f_rain=0.70, f_heavy=0.55, f_cloud=0.85)),
        ("ฝน ×0.85 + เมฆ ×0.9 + trend 0.5", dict(f_rain=0.85, f_heavy=0.7, f_cloud=0.9, trend_w=0.5)),
        ("trend อย่างเดียว (0.5)", dict(trend_w=0.5)),
        ("trend แรง (1.0)", dict(trend_w=1.0)),
    ]
    print("\n=== hit rate (เทียบตลาดจริง) ===")
    print("  %-34s %s" % ("variant", "  ".join("%02d:00" % h for h in hours)))
    for name, kw in variants:
        cells = []
        for h in hours:
            hr, n = rate([r for r in rows if r["hour"] == h], **kw)
            cells.append("%5.1f%% (n=%d)" % (hr, n) if n else "  –  ")
        print("  %-34s %s" % (name, "  ".join(cells)))
    # ลงรายงาน
    L.append("## hit rate เทียบตลาดจริง\n")
    L.append("| variant | " + " | ".join("%02d:00" % h for h in hours) + " |")
    L.append("|---|" + "---|" * len(hours))
    for name, kw in variants:
        cells = []
        for h in hours:
            hr, n = rate([r for r in rows if r["hour"] == h], **kw)
            cells.append("%.1f%% (n=%d)" % (hr, n) if n else "–")
        L.append("| %s | %s |" % (name, " | ".join(cells)))
    # สถิติสภาพอากาศ
    h0 = hours[0]
    day_rows = {}
    for r in rows:
        day_rows[(r["city"], r["date"])] = r
    tot = len(day_rows)
    ra = sum(1 for r in day_rows.values() if r["rain"])
    hv = sum(1 for r in day_rows.values() if r["heavy"])
    cl = sum(1 for r in day_rows.values() if r["cloudy"])
    L.append("\n## สภาพอากาศในชุดข้อมูล (ชั่วโมง %02d:00)\n" % h0)
    L.append("- วันที่มีฝน/พายุ: %d/%d (%.0f%%) · พายุฝนฟ้าคะนอง: %d (%.0f%%) · เมฆหนา OVC/BKN: %d (%.0f%%)"
             % (ra, tot, 100 * ra / max(tot, 1), hv, 100 * hv / max(tot, 1), cl, 100 * cl / max(tot, 1)))
    # แยกตามสภาพอากาศ (baseline)
    L.append("\n## baseline แยกตามสภาพอากาศ\n")
    L.append("| สภาพ | n | ทายถูก% |")
    L.append("|---|---|---|")
    for nm, sel in [("ฝน/พายุ", lambda r: r["rain"]), ("ไม่มีฝน", lambda r: not r["rain"]),
                    ("เมฆหนา", lambda r: r["cloudy"]), ("โปร่ง", lambda r: not r["cloudy"]),
                    ("trend บวก", lambda r: (r["trend"] or 0) > 0.3), ("trend ลบ", lambda r: (r["trend"] or 0) < -0.3)]:
        rs = [r for r in rows if r["hour"] == hours[-1] and sel(r)]
        hr, n = rate(rs)
        if n:
            L.append("| %s | %d | %.1f%% |" % (nm, n, hr))
    open(a.out, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\nเขียนรายงาน: %s" % a.out)


if __name__ == "__main__":
    main()
