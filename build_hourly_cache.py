#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_hourly_cache.py — cache METAR hourly (IEM) ต่อเมือง → data/hourly_obs.csv
คอลัมน์: city,date,hour,tmpc  (ใช้ทั้งสร้าง max-so-far และการกระจายตัวของ rise)
"""
import csv, json, os, sys, time
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import intraday as ID

OUT = os.path.join(HERE, "data", "hourly_obs.csv")
D0 = date(2026, 5, 25)
D1 = date(2026, 9, 24)


def main():
    cfgs = json.load(open(os.path.join(HERE, "stations.json"), encoding="utf-8"))
    cities = sorted(cfgs) if isinstance(cfgs, dict) else [c["city"] for c in cfgs]
    get = (lambda c: cfgs[c]) if isinstance(cfgs, dict) else (lambda c: next(x for x in cfgs if x["city"] == c))
    done = set()
    if os.path.exists(OUT):
        for r in csv.DictReader(open(OUT, encoding="utf-8")):
            done.add(r["city"])
    mode = "a" if done and os.path.exists(OUT) else "w"
    f = open(OUT, mode, newline="", encoding="utf-8")
    w = csv.writer(f)
    if mode == "w":
        w.writerow(["city", "date", "hour", "tmpc"])
    t0 = time.time()
    for i, c in enumerate(cities, 1):
        if c in done:
            continue
        cfg = get(c)
        try:
            ser = ID.hourly_series(cfg, D0, D1, no_cache=False)
        except Exception as e:
            print(f"[{i}/{len(cities)}] {c} ERROR {e}", flush=True)
            continue
        n = 0
        for d, hrs in ser.items():
            for h, t in hrs.items():
                w.writerow([c, d, h, round(t, 3)])
                n += 1
        f.flush()
        print(f"[{i}/{len(cities)}] {c}: {len(ser)} days / {n} rows  ({time.time()-t0:.0f}s)", flush=True)
    f.close()
    print("done ->", OUT, f"({time.time()-t0:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
