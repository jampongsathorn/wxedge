#!/usr/bin/env python3
"""
market_labels.py — ดึง "bucket ที่ตลาด resolve จริง" (เฉลยจริง) ย้อนหลังเป็นชุดข้อมูล

ทำไมสำคัญ: ค่าจริงที่เราใช้ก่อนหน้า (METAR รายชั่วโมง) ไม่ตรงกับแหล่งตัดสินของตลาด 100%
  - ตลาดสหรัฐฯ ใช้ WRH ที่แสดงค่าจากข้อมูล 5 นาที → สูงกว่า METAR รายชั่วโมง ~0.5-1°C
  - ทำให้ label เพี้ยนได้ ~1 bin → hit rate ที่วัดได้ต่ำกว่าความจริง
ใช้ตลาดที่ resolve แล้วเป็นเฉลยโดยตรง = ตรงตามโจทย์ที่สุด

รัน: python3 market_labels.py --start 2026-06-01 --out data/market_labels.csv
"""
import argparse, csv, json, os, sys, time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wxedge as W

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = 100
SLUG_RE = None


def page_desc(offset):
    u = ("https://gamma-api.polymarket.com/events?closed=true&tag_slug=daily-temperature"
         "&order=endDate&ascending=false&limit=%d&offset=%d" % (PAGE, offset))
    return W.get_json(u, ttl=3600, tag="mlabd")


def page(offset, ascending=True):
    u = ("https://gamma-api.polymarket.com/events?closed=true&tag_slug=daily-temperature"
         "&order=endDate&ascending=%s&limit=%d&offset=%d" % ("true" if ascending else "false", PAGE, offset))
    return W.get_json(u, ttl=3600, tag="mlab")


def parse_slug(slug):
    import re
    m = re.match(r"^highest-temperature-in-(.+)-on-([a-z]+)-(\d+)-(\d{4})$", slug)
    if not m:
        return None
    city, month, day, year = m.groups()
    try:
        d = datetime.strptime("%s %s %s" % (month, day, year), "%B %d %Y")
    except ValueError:
        return None
    return city, d.date().isoformat()


def winner(ev):
    for m in ev.get("markets", []):
        try:
            pr = json.loads(m.get("outcomePrices") or "[]")
        except json.JSONDecodeError:
            continue
        if pr and float(pr[0]) > 0.5:
            return m.get("groupItemTitle")
    return None


def load_existing(path):
    if not os.path.exists(path):
        return {}
    out = {}
    for r in csv.DictReader(open(path, encoding="utf-8")):
        out[(r["city"], r["date"])] = r
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-06-01")
    ap.add_argument("--end", default="2026-09-24")
    ap.add_argument("--out", default=os.path.join(HERE, "data", "market_labels.csv"))
    ap.add_argument("--mode", default="page", choices=["page", "slugs"])
    ap.add_argument("--cities", default="")
    a = ap.parse_args()

    cfgs = json.load(open(W.STATIONS))
    start = datetime.strptime(a.start, "%Y-%m-%d").date()
    end = datetime.strptime(a.end, "%Y-%m-%d").date()
    seen = load_existing(a.out)
    print("มีอยู่แล้ว %d labels" % len(seen))

    if a.mode == "slugs":
        want = list(cfgs) if a.cities in ("", "all") else a.cities.split(",")
        got = 0
        d = start
        while d <= end:
            for city in want:
                if city not in cfgs or (city, d.isoformat()) in seen:
                    continue
                ev = W.polymarket_event(city, d.isoformat(), no_cache=False)
                if not ev:
                    continue
                win = winner(ev)
                b = W.parse_bin(win or "")
                if not b:
                    continue
                seen[(city, d.isoformat())] = dict(city=city, date=d.isoformat(), winner=win,
                                                   unit=cfgs[city]["unit"], lo=b[0], hi=b[1],
                                                   slug=ev.get("slug"))
                got += 1
                if got % 50 == 0:
                    print("  ...%d ใหม่ (วันที่ %s)" % (got, d), flush=True)
            d += timedelta(days=1)
        rows = sorted(seen.values(), key=lambda r: (r["city"], r["date"]))
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        with open(a.out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["city", "date", "winner", "unit", "lo", "hi", "slug"])
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print("รวม %d labels (เพิ่ม %d) → %s" % (len(rows), got, a.out))
        return

    off = 0
    while off <= 1900:
        try:
            d = page_desc(off)
        except Exception as e:
            print("  offset %d ล้มเหลว: %s" % (off, str(e)[:50]))
            break
        if not d:
            break
        for ev in d:
            slug = ev.get("slug") or ""
            if not slug.startswith("highest-temperature-in") or "-lowest-" in slug:
                continue
            pp = parse_slug(slug)
            if not pp:
                continue
            city, date = pp
            if city not in cfgs or date < a.start or date > a.end:
                continue
            if (city, date) in seen:
                continue
            win = winner(ev)
            b = W.parse_bin(win or "")
            if not b:
                continue
            seen[(city, date)] = dict(city=city, date=date, winner=win, unit=cfgs[city]["unit"],
                                      lo=b[0], hi=b[1], slug=slug)
        off += PAGE
        print("  offset %d → รวม %d labels" % (off, len(seen)), flush=True)
    rows = sorted(seen.values(), key=lambda r: (r["city"], r["date"]))
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["city", "date", "winner", "unit", "lo", "hi", "slug"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    cities = sorted(set(r["city"] for r in rows))
    print("\nเขียน %s: %d ตลาด-วัน จาก %d เมือง" % (a.out, len(rows), len(cities)))
    print("  ช่วง:", min(r["date"] for r in rows), "..", max(r["date"] for r in rows))
    import collections
    c = collections.Counter(r["city"] for r in rows)
    print("  เมืองที่มีข้อมูลมากสุด:", dict(c.most_common(8)))
    print("  เมืองที่มีข้อมูลน้อยสุด:", dict(c.most_common()[-5:]))


if __name__ == "__main__":
    main()
