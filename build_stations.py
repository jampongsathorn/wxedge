#!/usr/bin/env python3
"""สร้าง stations.json จากกติกาการ resolve จริงของ Polymarket (ดึง station/site จาก description)
รัน: python3 build_stations.py
"""
import json, re, os, urllib.request, collections

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
AIRPORTS = os.path.join(DATA, "airports.json")
OUT = os.path.join(HERE, "stations.json")

# เมืองที่ description ไม่ได้ระบุ site= (ต้องใส่เอง) + Hong Kong ที่ไม่ใช้ METAR
MANUAL = {
    "hong-kong":  dict(icao="HKO",  name="Hong Kong Observatory", lat=22.3019, lon=114.1740, tz="Asia/Hong_Kong", obs="hko",  country="HK"),
    "istanbul":   dict(icao="LTFM", name="Istanbul Airport",       lat=None, lon=None, tz="Europe/Istanbul", obs="iem", country="TR"),
    "moscow":     dict(icao="UUWW", name="Vnukovo International Airport", lat=None, lon=None, tz="Europe/Moscow", obs="iem", country="RU"),
    "tel-aviv":   dict(icao="LLBG", name="Ben Gurion International Airport", lat=None, lon=None, tz="Asia/Jerusalem", obs="iem", country="IL"),
    "taipei":     dict(icao="RCSS", name="Taipei Songshan Airport", lat=None, lon=None, tz="Asia/Taipei", obs="iem", country="TW"),
    "jinan":      dict(icao="ZSJN", name="Jinan Yaoqiang International Airport", lat=None, lon=None, tz="Asia/Shanghai", obs="iem", country="CN"),
}


def fetch_events():
    url = ("https://gamma-api.polymarket.com/events?closed=false&limit=200"
           "&order=volume24hr&ascending=false&tag_slug=weather")
    req = urllib.request.Request(url, headers={"User-Agent": "wxedge/0.1 (research)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def main():
    if not os.path.exists(AIRPORTS):
        with urllib.request.urlopen(urllib.request.Request(
            "https://raw.githubusercontent.com/mwgg/Airports/master/airports.json",
            headers={"User-Agent": "wxedge/0.1"}), timeout=120) as r:
            open(AIRPORTS, "wb").write(r.read())
    airports = json.load(open(AIRPORTS))
    events = fetch_events()

    cfg = {}
    for e in events:
        slug = e.get("slug", "")
        if not slug.startswith("highest-temperature-in"):
            continue
        desc = e.get("description", "") or ""
        city = slug[len("highest-temperature-in-"):].rsplit("-on-", 1)[0]
        unit = "F" if "degrees Fahrenheit" in desc else ("C" if "degrees Celsius" in desc else "C")
        site = re.search(r"site=([a-z0-9]+)", desc)
        station = re.search(r"recorded by (.*?) (?:at|from|in) the (.+?)(?: Station)? in degrees", desc)
        icao = site.group(1).upper() if site else None
        name = station.group(2) if station else None

        if city in MANUAL:
            m = MANUAL[city]
            icao = m["icao"]
            name = m["name"]
        if not icao:
            continue

        a = airports.get(icao, {})
        lat = a.get("lat") or (MANUAL.get(city, {}) or {}).get("lat")
        lon = a.get("lon") or (MANUAL.get(city, {}) or {}).get("lon")
        tz = a.get("tz") or (MANUAL.get(city, {}) or {}).get("tz")
        if lat is None or tz is None:
            continue
        # ตลาดสหรัฐฯ ตัดสินจาก "Hourly Data" บน WRH → ใช้ METAR รายชั่วโมง (IEM)
        obs = "hko" if city in MANUAL and MANUAL[city]["obs"] == "hko" else "iem"

        prev = cfg.get(city, {})
        cfg[city] = dict(
            city=city, icao=icao, station=name or prev.get("station"),
            lat=round(lat, 4), lon=round(lon, 4), tz=tz, unit=unit,
            obs=obs, country=a.get("country"),
            vol24h=max(prev.get("vol24h", 0), e.get("volume24hr") or 0),
        )

    cfg = dict(sorted(cfg.items(), key=lambda kv: -(kv[1]["vol24h"] or 0)))
    json.dump(cfg, open(OUT, "w"), ensure_ascii=False, indent=1)
    print(f"เขียน {OUT}: {len(cfg)} เมือง")
    units = collections.Counter(v["unit"] for v in cfg.values())
    obs = collections.Counter(v["obs"] for v in cfg.values())
    print("  unit:", dict(units), "| obs source:", dict(obs))
    for c, v in list(cfg.items())[:5]:
        print(f"  {c:12s} {v['icao']:5s} {v['unit']} {v['tz']:20s} {v['station']}")


if __name__ == "__main__":
    main()
