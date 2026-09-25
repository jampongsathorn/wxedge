#!/usr/bin/env python3
"""
intraday.py — ทำนาย bucket "ระหว่างวัน" (ตอนที่รู้ค่าสูงสุดถึงกลางวันแล้ว)

แนวคิด: daily max เกือบทั้งหมดเกิดช่วง 13:00–16:00 local
  ถ้าเวลานั้นผ่านไปแล้ว ค่าสูงสุดสุดท้าย = max(ค่าที่วัดได้, ค่าที่จะเพิ่มขึ้นอีกเล็กน้อย)
  "ส่วนที่จะเพิ่มอีก" มีการแจกแจงที่วัดได้จากอดีตของสถานีนั้น (empirical increment distribution)
  → ได้ความน่าจะเป็นต่อ bin ที่แคบมาก → hit rate สูง (นี่คือช่วงที่ทำ ≥70% ได้จริง)

ทำ: สำหรับแต่ละเมือง/วัน ย้อนหลัง 90 วัน + เทียบกับ bucket ที่ "ตลาด resolve จริง" (ถ้ามี)
รัน: python3 intraday.py --days 75 --hours 11,13,15,17 --out reports/intraday.md
"""
import argparse, csv, json, math, os, statistics as st, sys, collections
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wxedge as W

HERE = os.path.dirname(os.path.abspath(__file__))


def hourly_series(cfg, d0, d1, no_cache=False):
    """{date: {hour: temp_c}} จาก METAR รายชั่วโมง (IEM)"""
    import io, json, urllib.parse
    d1 = d1 + timedelta(days=1)   # IEM ตัดวันสิ้นสุดออก → ขอเกินไป 1 วัน
    url = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station=%s&data=tmpc"
           "&year1=%d&month1=%d&day1=%d&year2=%d&month2=%d&day2=%d&tz=%s&format=onlycomma"
           "&report_type=3&missing=M&trace=T&direct=no"
           % (cfg["icao"], d0.year, d0.month, d0.day, d1.year, d1.month, d1.day,
              urllib.parse.quote(cfg["tz"])))
    txt = W.http_get(url, ttl=3600, tag="iemh", no_cache=no_cache)
    out = collections.defaultdict(dict)
    for row in csv.DictReader(io.StringIO(txt)):
        v = row.get("tmpc")
        if not v or v in ("M", ""):
            continue
        try:
            t = float(v)
        except ValueError:
            continue
        day = row["valid"][:10]
        hh = int(row["valid"][11:13])
        prev = out[day].get(hh)
        out[day][hh] = t if prev is None else max(prev, t)
    return out


def num_label(x):
    x = str(x)
    if x in ("inf", "+inf"):
        return float("inf")
    if x == "-inf":
        return float("-inf")
    return float(x)


def to_market(c, unit):
    return c * 9 / 5 + 32 if unit == "F" else c


def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def live_mode(a, cfgs):
    """ใช้จริง: ดึง obs ของวันนี้ถึงชั่วโมงปัจจุบัน + การกระจายตัวของส่วนที่เพิ่มอีกจาก 30 วันก่อน"""
    import json as _json
    out = {}
    for city in a.live.split(","):
        cfg = cfgs.get(city)
        if not cfg:
            out[city] = {"error": "ไม่รู้จักเมืองนี้"}
            continue
        tz = ZoneInfo(cfg["tz"])
        now = datetime.now(tz)
        day = datetime.strptime(a.day, "%Y-%m-%d").date() if a.day else now.date()
        ser = hourly_series(cfg, day - timedelta(days=a.lookback + 2), day)
        today_key = day.isoformat()
        hist = sorted(d for d in ser if d < today_key)
        cur = ser.get(today_key, {})
        if not cur:
            out[city] = {"error": "ยังไม่มี obs ของวันนี้", "tz_now": now.strftime("%Y-%m-%d %H:%M")}
            continue
        h_cur = a.hour if a.hour else max(cur)
        cur = {hh: v for hh, v in cur.items() if hh <= h_cur}
        if not cur:
            out[city] = {"error": "ไม่มี obs ถึงชั่วโมง %d" % h_cur}
            continue
        so_far = [v for hh, v in cur.items() if hh <= h_cur]
        mx = max(so_far)
        incs = []
        for hd in hist[-a.lookback:]:
            past = [v for hh, v in ser[hd].items() if hh <= h_cur]
            if len(past) < 3:
                continue
            incs.append(max(ser[hd].values()) - max(past))
        if len(incs) < 8:
            out[city] = {"error": "ข้อมูลอดีตไม่พอ", "n_hist": len(incs)}
            continue
        samples = sorted(mx + x for x in incs)
        bins = W.market_bins(cfg["unit"])
        cnt = collections.Counter(W.bucket_label(s, cfg["unit"]) for s in samples)
        tot = sum(cnt.values())
        prob = sorted(((k, v / tot) for k, v in cnt.items()), key=lambda kv: -kv[1])
        p95 = samples[int(0.975 * len(samples)) - 1]
        out[city] = dict(
            station=cfg["icao"], unit=cfg["unit"], local_time=now.strftime("%Y-%m-%d %H:%M"),
            target_date=day.isoformat(),
            hour_covered=h_cur, obs_so_far_c=round(mx, 2),
            n_hist=len(incs),
            mean_final_c=round(st.mean(samples), 2),
            p05_c=round(samples[int(0.025 * len(samples))], 2), p95_c=round(p95, 2),
            top_bin=prob[0][0], top_p=round(prob[0][1], 3),
            bins={k: round(v, 3) for k, v in prob[:4]},
            hit_rate_ref="~76% ที่ 15:00 / ~88% ที่ 17:00 (per station local)",
            note="ใช้ค่า obs สูงสุดถึงชั่วโมงที่ระบุ + การกระจายตัวของส่วนที่เพิ่มอีกของสถานีนี้เอง")
    print(_json.dumps(out, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=75)
    ap.add_argument("--hours", default="11,13,15,17")
    ap.add_argument("--cities", default="all")
    ap.add_argument("--lookback", type=int, default=30, help="วันย้อนหลังสำหรับสร้างการแจกแจง increment")
    ap.add_argument("--uplift", default="none", choices=["none", "auto"],
                    help="auto = ปรับชดเชยกรณีตลาดตัดสินจากข้อมูล 5 นาที (สูงกว่า METAR รายชั่วโมง)")
    ap.add_argument("--out", default=os.path.join(HERE, "reports", "intraday.md"))
    ap.add_argument("--live", default="", help="ทำนายของวันนี้สำหรับเมืองนี้ (คั่นด้วย ,) แบบ JSON")
    ap.add_argument("--day", default="", help="ใช้กับ --live: วันเป้าหมาย YYYY-MM-DD (ค่าเริ่มต้น=วันนี้ตามเวลาท้องถิ่น)")
    ap.add_argument("--hour", type=int, default=0, help="ใช้กับ --live: ตัดข้อมูลถึงชั่วโมงนี้ (ค่าเริ่มต้น=ชั่วโมงปัจจุบัน)")
    a = ap.parse_args()
    hours = [int(x) for x in a.hours.split(",")]

    cfgs = json.load(open(W.STATIONS))
    cities = list(cfgs) if a.cities in ("", "all") else a.cities.split(",")

    labels = {}
    lp = os.path.join(HERE, "data", "market_labels.csv")
    if os.path.exists(lp):
        for r in csv.DictReader(open(lp, encoding="utf-8")):
            labels[(r["city"], r["date"])] = r

    if a.live:
        return live_mode(a, cfgs)
    # ตาราง uplift: (city,date) -> ค่า median จากข้อมูลที่ "ผ่านมาแล้ว" เท่านั้น
    uplift_tbl = {}
    if a.uplift == "auto":
        per_unit = collections.defaultdict(list)
        per_city = collections.defaultdict(list)
        for (c2, d2), ml in sorted(labels.items(), key=lambda kv: kv[0][1]):
            per_unit[ml["unit"]].append(None)  # เติมทีหลัง
        # คำนวณค่าจริง: ต้องมีชุด IEM ต่อเมือง
        for c2 in cities:
            cfg2 = cfgs[c2]
            ds2 = sorted(d for (cc, d) in labels if cc == c2)
            if not ds2:
                continue
            try:
                ser2 = hourly_series(cfg2, datetime.strptime(ds2[0], "%Y-%m-%d").date(),
                                     datetime.strptime(ds2[-1], "%Y-%m-%d").date())
            except Exception:
                continue
            hist_delta = []
            for d2 in ds2:
                if d2 not in ser2:
                    continue
                daily = max(ser2[d2].values())
                lo2, hi2 = num(ml["lo"]) if False else None, None
                ml2 = labels[(c2, d2)]
                lo2 = num_label(ml2["lo"]); hi2 = num_label(ml2["hi"])
                if lo2 == float("-inf"):
                    lo2 = hi2 - 1
                if hi2 == float("inf"):
                    hi2 = lo2 + 1
                # เทียบที่ "ขอบล่าง" ของ bin ที่ชนะ (ตลาดอาจสูงกว่าเพราะเป็น bin)
                delta = (lo2 + hi2) / 2 - daily
                uplift_tbl[(c2, d2)] = (list(hist_delta), delta)
                hist_delta = (hist_delta + [delta])[-14:]
    today = datetime.now(ZoneInfo("UTC")).date()
    d0 = today - timedelta(days=a.days + a.lookback)
    per_hour = {h: dict(n=0, hit=0, hit_mkt=0, n_mkt=0, hit_mkt2=0, errs=[]) for h in hours}
    city_hour = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
    city_mkt = collections.defaultdict(lambda: [0, 0, 0])   # [top1 hit, top2 hit, n] เทียบตลาดจริง
    detail = []

    for city in cities:
        cfg = cfgs.get(city)
        if not cfg:
            continue
        ser = hourly_series(cfg, d0, today, no_cache=False)
        if not ser:
            continue
        days = sorted(ser)
        for i, day in enumerate(days):
            if day > (today - timedelta(days=1)).isoformat():
                continue
            hist_days = days[max(0, i - a.lookback):i]
            if len(hist_days) < 10:
                continue
            full = max(ser[day].values())
            for h in hours:
                so_far = [v for hh, v in ser[day].items() if hh <= h]
                if not so_far:
                    continue
                mx = max(so_far)
                # การแจกแจง "ส่วนที่จะเพิ่มอีก" จากอดีต (เฉพาะวันที่เคยมีค่าถึงชั่วโมง h)
                incs = []
                for hd in hist_days:
                    past = [v for hh, v in ser[hd].items() if hh <= h]
                    if len(past) < 3:
                        continue
                    incs.append(max(ser[hd].values()) - max(past))
                if len(incs) < 8:
                    continue
                up = 0.0
                if a.uplift == "auto":
                    hd, _ = uplift_tbl.get((city, day), ([], 0))
                    if len(hd) >= 5:
                        up = st.median(hd)
                    else:
                        up = 0.5 if cfg["unit"] == "C" else 0.0
                samples = [mx + x + up for x in incs]
                # ความน่าจะเป็นต่อ bin
                bins = W.market_bins(cfg["unit"])
                cnt = collections.Counter(W.bucket_label(s, cfg["unit"]) for s in samples)
                tot = sum(cnt.values())
                prob = {k: v / tot for k, v in cnt.items()}
                top = max(prob, key=prob.get)
                second = sorted(prob.values(), reverse=True)
                # เทียบกับค่าจริงสุดท้าย (IEM)
                truth = W.bucket_label(full, cfg["unit"])
                d = per_hour[h]
                d["n"] += 1
                d["hit"] += (top == truth)
                d["errs"].append(abs(mx + st.mean(incs) - full))
                city_hour[city][h][0] += (top == truth)
                city_hour[city][h][1] += 1
                # เทียบกับ bucket ที่ตลาด resolve (ถ้ามี)
                ml = labels.get((city, day))
                if ml:
                    c2 = city_mkt[city]
                    c2[2] += 1
                    c2[0] += (top == ml["winner"])
                    d["n_mkt"] += 1
                    d["hit_mkt"] += (top == ml["winner"])
                    top2 = [k for k, _ in sorted(prob.items(), key=lambda kv: -kv[1])[:2]]
                    d["hit_mkt2"] += (ml["winner"] in top2)
                    c2[1] += (ml["winner"] in top2)
                    if h == 15:
                        detail.append((city, day, h, top, ml["winner"], top == ml["winner"]))

    print("=== ทำนาย 'ระหว่างวัน' (ใช้ค่าที่วัดได้ถึงชั่วโมงนั้น + การกระจายตัวของส่วนที่เพิ่มอีกจากอดีต) ===")
    print("  %-8s %8s %10s %8s %8s %12s %10s" % ("ชั่วโมง", "n(สถานี)", "ถูก%(IEM)", "n(ตลาด)", "ถูก%(ตลาด)",
                                                 "ถูกใน2bin", "err เฉลี่ย"))
    for h in hours:
        d = per_hour[h]
        if not d["n"]:
            continue
        print("  %02d:00   %8d %9.1f%% %8d %8.1f%% %11.1f%% %9.2f°C" % (
            h, d["n"], 100 * d["hit"] / d["n"], d["n_mkt"],
            100 * d["hit_mkt"] / max(d["n_mkt"], 1), 100 * d["hit_mkt2"] / max(d["n_mkt"], 1),
            st.mean(d["errs"])))

    # สรุปเฉพาะวันที่ตลาดมีจริง (ช่วง 3-23 ก.ย.)
    print("\n=== เฉพาะตลาดจริง (3–23 ก.ย.) — hit rate ของ bin ที่ทำนาย ===")
    for h in hours:
        d = per_hour[h]
        if d["n_mkt"]:
            print("  %02d:00: ทายถูก %.1f%% (n=%d) | ถูกใน 2 bin %.1f%%" % (
                h, 100 * d["hit_mkt"] / d["n_mkt"], d["n_mkt"], 100 * d["hit_mkt2"] / d["n_mkt"]))

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write("# intraday backtest — %s\n\n" % datetime.now().strftime("%Y-%m-%d %H:%M"))
        f.write("การทำนายระหว่างวัน: max(ค่าที่วัดได้ถึงชั่วโมงนั้น) + การกระจายตัวของส่วนที่เพิ่มอีก (จาก %d วันก่อนหน้า)\n\n" % a.lookback)
        f.write("| ชั่วโมง | n (เทียบค่าสถานี) | ถูก% | n (เทียบตลาดจริง) | ถูก% | ถูกใน 2 bin | MAE |\n|---|---|---|---|---|---|---|\n")
        for h in hours:
            d = per_hour[h]
            if not d["n"]:
                continue
            f.write("| %02d:00 | %d | %.1f%% | %d | %.1f%% | %.1f%% | %.2f°C |\n" % (
                h, d["n"], 100 * d["hit"] / d["n"], d["n_mkt"],
                100 * d["hit_mkt"] / max(d["n_mkt"], 1), 100 * d["hit_mkt2"] / max(d["n_mkt"], 1),
                st.mean(d["errs"])))
        f.write("\n## แยกตามเมือง (เทียบ bucket ที่ตลาด resolve จริง, ชั่วโมงที่เลือก)\n\n| city | n | ถูก% | ถูกใน2bin% |\n|---|---|---|---|\n")
        for c in sorted(city_mkt, key=lambda k: -(city_mkt[k][0] / max(city_mkt[k][2], 1))):
            h1, h2, n_ = city_mkt[c]
            if n_ >= 5:
                f.write("| %s | %d | %.1f%% | %.1f%% |\n" % (c, n_, 100 * h1 / n_, 100 * h2 / n_))
        f.write("\n## ตัวอย่างวันที่ 15:00 (ตลาดจริง)\n\n| city | date | ทำนาย | จริง | ถูก |\n|---|---|---|---|---|\n")
        for c, d, h, top, win, ok in detail[:80]:
            f.write("| %s | %s | %s | %s | %s |\n" % (c, d, top, win, "✅" if ok else "❌"))
    print("\nเขียนรายงาน: %s" % a.out)


if __name__ == "__main__":
    main()
