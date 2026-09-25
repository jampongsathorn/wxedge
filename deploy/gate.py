#!/usr/bin/env python3
"""gate.py — เช็คว่ามีเมือง "ถึงเวลาเข้าไม้" ไหม (ใช้ stdlib ล้วน เพื่อให้รันได้ก่อนติดตั้ง numpy)

ใช้ใน GitHub Actions / cron เพื่อ "ข้ามรอบเปล่า" ไม่ให้เผานาที CI ตอนไม่มีเมืองอยู่ในช่วงเวลา:
    python3 deploy/gate.py --hour-window 15-17 >> "$GITHUB_OUTPUT"
ผลลัพธ์: 2 บรรทัด key=value → run=true|false · cities=เมือง1,เมือง2,...
"""
import argparse, json, os
from datetime import datetime
from zoneinfo import ZoneInfo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hour-window", default="15-17", help="ช่วง local hour ที่สนใจ (เช่น 15-17 = ใกล้เวลาเข้าไม้ 16:00)")
    ap.add_argument("--root", default=".", help="โฟลเดอร์โปรเจกต์ (ที่มี stations.json)")
    a = ap.parse_args()

    try:
        lo, hi = [int(x) for x in a.hour_window.split("-")]
    except ValueError:
        lo, hi = 15, 17

    cfgs = json.load(open(os.path.join(a.root, "stations.json"), encoding="utf-8"))
    univ = None
    up = os.path.join(a.root, "data", "universe.json")
    if os.path.exists(up):
        try:
            univ = set(json.load(open(up, encoding="utf-8")).get("cities") or [])
        except (OSError, ValueError):
            univ = None

    hits = []
    for c, v in cfgs.items():
        if not v.get("icao"):
            continue
        if univ is not None and c not in univ:
            continue
        if lo <= datetime.now(ZoneInfo(v["tz"])).hour <= hi:
            hits.append(c)

    print("run=%s" % ("true" if hits else "false"))
    print("cities=%s" % ",".join(sorted(hits)))
    print("window=%d-%d" % (lo, hi))


if __name__ == "__main__":
    main()
