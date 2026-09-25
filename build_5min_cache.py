#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_5min_cache.py — obs ความละเอียดสูง (IEM report_type=1 ≈ ทุก 5 นาที, สถานี ASOS สหรัฐ)
เก็บแบบ compact: ต่อ (city, date, hour) = ค่าสูงสุดสะสม "ถึงชั่วโมงนั้น" (cumulative max through hour)
→ data/obs5min_upto.csv : city,date,hour,upto_c    (hour=23 → ทั้งวัน)
ใช้ตอบคำถาม: "API ที่ละเอียดขึ้น (5 นาที vs 60 นาที) ทำให้ทายถูกเร็วขึ้นไหม"
"""
import csv, io, json, os, time, urllib.request
from collections import defaultdict
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "obs5min_upto.csv")
CITIES = ["nyc", "chicago", "miami", "denver", "austin", "dallas", "houston", "atlanta",
          "seattle", "los-angeles", "san-francisco"]
CHUNKS = [(date(2026, 5, 25), date(2026, 5, 31)), (date(2026, 6, 1), date(2026, 6, 30)),
          (date(2026, 7, 1), date(2026, 7, 31)), (date(2026, 8, 1), date(2026, 8, 31)),
          (date(2026, 9, 1), date(2026, 9, 24))]


def fetch(icao, tz, d0, d1):
    url = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station=%s&data=tmpc"
           "&year1=%d&month1=%d&day1=%d&year2=%d&month2=%d&day2=%d&tz=%s&format=onlycomma"
           "&report_type=1&missing=M&trace=T&direct=no"
           % (icao, d0.year, d0.month, d0.day, d1.year, d1.month, d1.day, tz))
    req = urllib.request.Request(url, headers={"User-Agent": "wxedge-study/1.0"})
    return urllib.request.urlopen(req, timeout=90).read().decode()


def main():
    cfgs = json.load(open(os.path.join(HERE, "stations.json"), encoding="utf-8"))
    have = set()
    if os.path.exists(OUT):
        for r in csv.DictReader(open(OUT, encoding="utf-8")):
            have.add((r["city"], r["date"], r["hour"]))
    f = open(OUT, "a" if have else "w", newline="", encoding="utf-8")
    w = csv.writer(f)
    if not have:
        w.writerow(["city", "date", "hour", "upto_c"])
    t0 = time.time()
    for city in CITIES:
        cfg = cfgs.get(city)
        if not cfg:
            continue
        per_day = defaultdict(list)     # date -> [(minute_of_day, t)]
        for (d0, d1) in CHUNKS:
            if all((city, d0.isoformat(), str(h)) in have for h in range(24)):
                continue
            txt = None
            for attempt in range(4):
                try:
                    txt = fetch(cfg["icao"], cfg["tz"], d0, d1)
                    break
                except Exception as e:
                    print(f"  {city} {d0} attempt {attempt}: {e}", flush=True)
                    time.sleep(6 + 6 * attempt)
            if not txt:
                continue
            rows = list(csv.DictReader(io.StringIO(txt)))
            for r in rows:
                v = r.get("tmpc")
                if not v or v in ("M", ""):
                    continue
                try:
                    t = float(v)
                except ValueError:
                    continue
                vd = r["valid"]
                dd = vd[:10]
                hh, mm = int(vd[11:13]), int(vd[14:16])
                per_day[dd].append((hh * 60 + mm, t))
            time.sleep(2.5)
            print(f"  {city} {d0}-{d1}: {len(rows)} rows ({time.time()-t0:.0f}s)", flush=True)
        for dd, obs in per_day.items():
            hourmax = {}
            for mod, t in obs:
                h = mod // 60
                hourmax[h] = max(hourmax.get(h, -99.0), t)
            run = None
            for h in range(24):
                if h in hourmax:
                    run = hourmax[h] if run is None else max(run, hourmax[h])
                if run is not None:
                    w.writerow([city, dd, h, round(run, 3)])
        f.flush()
        print(f"{city}: done ({time.time()-t0:.0f}s)", flush=True)
    f.close()
    print("done ->", OUT, flush=True)


if __name__ == "__main__":
    main()
