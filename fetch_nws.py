#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_nws.py — ดึง obs 5 นาทีของ NWS (api.weather.gov) สำหรับเมืองสหรัฐที่เลือก → data/nws5min.csv
ทำไม: ทดสอบว่า "เพิ่ม API สภาพอากาศความละเอียดสูง" ทำให้ทายถูกเร็วขึ้นจริงไหม (NWS = 5 นาที vs IEM = 60 นาที)
รูปแบบ: city,date,hour,tmpc   (max ต่อชั่วโมง เพื่อเทียบกับ IEM)
resume ได้: อ่านไฟล์เดิม + ข้าม chunk ที่มีแล้ว
"""
import csv, json, os, sys, time, urllib.request, urllib.error
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "nws5min.csv")
UA = "wxedge-study/1.0 (research; contact: user)"
CITIES = ["nyc", "chicago", "miami", "denver", "austin", "los-angeles", "houston", "atlanta"]
D0 = date(2026, 7, 1)
D1 = date(2026, 9, 24)
CHUNK = 2  # วันต่อคำขอ (~576 obs < 500 limit → ใช้ 1.4 วัน; ปลอดภัยที่ 1 วัน)
CHUNK = 1


def fetch(icao, d0, d1):
    url = ("https://api.weather.gov/stations/%s/observations?start=%sT00:00:00Z&end=%sT23:59:59Z"
           % (icao, d0.isoformat(), d1.isoformat()))
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/geo+json"})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read().decode())


def main():
    cfgs = json.load(open(os.path.join(HERE, "stations.json"), encoding="utf-8"))
    done = set()
    if os.path.exists(OUT):
        for r in csv.DictReader(open(OUT, encoding="utf-8")):
            done.add((r["city"], r["date"]))
    f = open(OUT, "a" if done else "w", newline="", encoding="utf-8")
    w = csv.writer(f)
    if not done:
        w.writerow(["city", "date", "hour", "tmpc"])
    t0 = time.time()
    for city in CITIES:
        cfg = cfgs.get(city)
        if not cfg:
            print("skip", city, flush=True); continue
        icao = cfg["icao"]
        d = D0
        while d <= D1:
            key = (city, d.isoformat())
            if key in done:
                d += timedelta(days=1); continue
            try:
                js = fetch(icao, d, d)
            except urllib.error.HTTPError as e:
                print(f"{city} {d} HTTP {e.code}", flush=True); time.sleep(2.0); continue
            except Exception as e:
                print(f"{city} {d} ERR {e}", flush=True); time.sleep(1.5); continue
            best = {}
            for feat in (js or {}).get("features", []):
                pr = feat.get("properties", {})
                ts = pr.get("timestamp")
                v = (pr.get("temperature") or {}).get("value")
                if ts is None or v is None:
                    continue
                t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                local = t.astimezone(timezone.utc)  # เก็บเป็น UTC ชั่วโมง แล้วค่อยแปลงทีหลังไม่ได้ → ใช้ local hour ของเมือง
                try:
                    from zoneinfo import ZoneInfo
                    lt = t.astimezone(ZoneInfo(cfg["tz"]))
                except Exception:
                    lt = t
                day, hh = lt.date().isoformat(), lt.hour
                if day != d.isoformat():
                    continue
                best[hh] = max(best.get(hh, -99), float(v))
            for hh, v in sorted(best.items()):
                w.writerow([city, d.isoformat(), hh, round(v, 3)])
            f.flush()
            print(f"{city} {d}: {len(best)} hours ({time.time()-t0:.0f}s)", flush=True)
            time.sleep(0.35)
            d += timedelta(days=1)
    f.close()
    print("done ->", OUT, f"({time.time()-t0:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
