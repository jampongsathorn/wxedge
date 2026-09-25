#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_wu.py — cache obs ของ Wunderground (api.weather.com) = "แหล่งที่ตลาดสหรัฐใช้ตัดสิน"
เก็บ cumulative max ต่อชั่วโมง: data/wu_upto.csv  (city,date,hour,upto_c)
ทำไม: ทดสอบว่า "เพิ่ม API obs อีกแหล่ง (ที่ตรงกับแหล่งตัดสิน) ทำให้ทายเร็วขึ้นแม่นขึ้นไหม"
"""
import csv, json, os, time, urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "wu_upto.csv")
KEY = "e1f10a1e78da46f5b10a1e78da96f525"     # key เดียวกับที่ repo cr_weatherbot ฝังไว้ (ใช้ทดสอบ)
CITIES = ["nyc", "chicago", "miami", "denver", "austin", "dallas", "houston", "atlanta",
          "seattle", "los-angeles", "san-francisco"]
D0, D1 = date(2026, 5, 25), date(2026, 9, 24)
STEP = 7


def fetch(icao, cc, d0, d1):
    url = ("https://api.weather.com/v1/location/%s:9:%s/observations/historical.json?apiKey=%s"
           "&units=e&startDate=%s&endDate=%s" % (icao, cc, KEY, d0.strftime("%Y%m%d"), d1.strftime("%Y%m%d")))
    req = urllib.request.Request(url, headers={"User-Agent": "wxedge-study/1.0"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read().decode())


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
        icao, cc, tz = cfg["icao"], cfg.get("country", "US"), cfg["tz"]
        per_day = defaultdict(list)
        d = D0
        while d <= D1:
            chunk_end = min(d + timedelta(days=STEP - 1), D1)
            try:
                js = fetch(icao, cc, d, chunk_end)
            except Exception as e:
                print(f"  {city} {d} ERR {e}", flush=True)
                time.sleep(3)
                d = chunk_end + timedelta(days=1)
                continue
            for o in js.get("observations", []):
                t = o.get("temp")
                ts = o.get("valid_time_gmt")
                if t is None or ts is None:
                    continue
                try:
                    lt = datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(ZoneInfo(tz))
                except Exception:
                    continue
                c = (float(t) - 32.0) * 5.0 / 9.0     # units=e → ค่าเป็น °F เต็มองศา
                per_day[lt.date().isoformat()].append((lt.hour * 60 + lt.minute, c))
            print(f"  {city} {d}..{chunk_end}: total days {len(per_day)} ({time.time()-t0:.0f}s)", flush=True)
            time.sleep(0.35)
            d = chunk_end + timedelta(days=1)
        for dd, obs in sorted(per_day.items()):
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
