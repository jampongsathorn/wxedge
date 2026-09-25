#!/usr/bin/env python3
"""gate.py — ตัดสินว่ารอบนี้ควรสแกนเมืองไหน (stdlib ล้วน รันก่อนติดตั้ง numpy ได้)

หลักคิด: เก็บให้ "ถี่ตรงจุดตัดสินใจ" ไม่ใช่ถี่ทั้งวัน — ประหยัดนาที CI ระดับ 10 เท่า
  • ช่วงเข้าไม้ (entry)  : local 15:40–16:20 → เก็บถี่ (ค่าเริ่มต้นทุก 15 นาที/เมือง)
  • ช่วงบริบท (context)  : local 15:00–17:00 นอกช่วงบน → เก็บห่าง (ค่าเริ่มต้นทุก 60 นาที/เมือง)
  • นอกนั้น               : ข้าม
สถานะ "สแกนล่าสุด" อ่านจาก data/snapshots/<วันนี้>.jsonl (cheap_live เขียนไว้แล้ว) — ไม่ต้องมีไฟล์ state เพิ่ม

ใช้:
  python3 deploy/gate.py --entry 15:40-16:20 --entry-gap 15 --ctx 15:00-17:00 --ctx-gap 60 >> "$GITHUB_OUTPUT"
ผลลัพธ์: run=true|false · cities=... · entry_cities=... · mode=dense|sparse|none
"""
import argparse, json, os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def parse_hhmm(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def parse_window(s):
    a, b = s.split("-")
    lo, hi = parse_hhmm(a), parse_hhmm(b)
    if hi < lo:                      # ข้ามเที่ยงคืน
        hi += 24 * 60
    return lo, hi


def in_window(mins, win):
    lo, hi = win
    if mins < lo:
        mins += 24 * 60
    return lo <= mins <= hi


def last_scans(state_file):
    """{city: datetime ของการสแกนล่าสุด} จาก snapshot ของวันนี้"""
    out = {}
    if not state_file or not os.path.exists(state_file):
        return out
    try:
        for ln in open(state_file, encoding="utf-8"):
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            city, ts = d.get("city"), d.get("ts")
            if not city or not ts:
                continue
            try:
                t = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if city not in out or t > out[city]:
                out[city] = t
    except OSError:
        pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--entry", default="15:40-16:20", help="ช่วงเวลา local ที่อยากเก็บถี่")
    ap.add_argument("--entry-gap", type=int, default=15, help="ระยะเว้นขั้นต่ำ (นาที) ในช่วง entry")
    ap.add_argument("--ctx", default="15:00-17:00", help="ช่วงเวลา local สำหรับเก็บเป็นบริบท")
    ap.add_argument("--ctx-gap", type=int, default=60, help="ระยะเว้นขั้นต่ำ (นาที) ในช่วงบริบท")
    ap.add_argument("--state-file", default="", help="ไฟล์สถานะการสแกนล่าสุด (ค่าเริ่มต้น data/snapshots/<วันนี้>.jsonl)")
    ap.add_argument("--root", default=".", help="โฟลเดอร์โปรเจกต์ (มี stations.json)")
    ap.add_argument("--now", default="", help="บังคับเวลาทดสอบ (ISO UTC)")
    a = ap.parse_args()

    entry_win, ctx_win = parse_window(a.entry), parse_window(a.ctx)
    state_file = a.state_file or os.path.join(
        a.root, "data", "snapshots", datetime.now(timezone.utc).strftime("%Y-%m-%d") + ".jsonl")
    last = last_scans(state_file)
    now = (datetime.strptime(a.now, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
           if a.now else datetime.now(timezone.utc))

    try:
        cfgs = json.load(open(os.path.join(a.root, "stations.json"), encoding="utf-8"))
    except OSError:
        print("run=false\ncities=\nentry_cities=\nmode=none\nerror=ไม่พบ stations.json")
        return 0

    univ = None
    up = os.path.join(a.root, "data", "universe.json")
    if os.path.exists(up):
        try:
            univ = set(json.load(open(up, encoding="utf-8")).get("cities") or [])
        except (OSError, ValueError):
            univ = None

    entry_hits, ctx_hits, skipped_recent = [], [], 0
    for c, v in cfgs.items():
        if not v.get("icao") or (univ is not None and c not in univ):
            continue
        loc = now.astimezone(ZoneInfo(v["tz"]))
        mins = loc.hour * 60 + loc.minute
        if in_window(mins, entry_win):
            gap, bucket = a.entry_gap, entry_hits
        elif in_window(mins, ctx_win):
            gap, bucket = a.ctx_gap, ctx_hits
        else:
            continue
        prev = last.get(c)
        if prev is not None and (now - prev) < timedelta(minutes=gap):
            skipped_recent += 1
            continue
        bucket.append(c)

    cities = sorted(entry_hits + ctx_hits)
    mode = "dense" if entry_hits else ("sparse" if ctx_hits else "none")
    print("run=%s" % ("true" if cities else "false"))
    print("cities=%s" % ",".join(cities))
    print("entry_cities=%s" % ",".join(sorted(entry_hits)))
    print("mode=%s" % mode)
    print("skipped_recent=%d" % skipped_recent)
    if not a.now:
        print("checked_at=%s" % now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
