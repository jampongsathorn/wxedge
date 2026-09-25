#!/usr/bin/env python3
"""tests_live_v2.py — ตรวจ logic ของ cheap_live.py v2 ด้วยข้อมูลจำลอง (ไม่ต้องพึ่งเครือข่าย)
รัน: python3 tests_live_v2.py
"""
import os, sys, json, tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cheap_live as CL

DAY = date(2026, 9, 25)
CFG = dict(tz="America/Chicago", unit="F", icao="KDAL", city="Dallas")

# ── ของปลอม: obs 30 วันย้อนหลัง + วันนี้ max-so-far = 91°F (32.78°C) ──
MX_C = (91 - 32) * 5 / 9          # 32.78 °C
INCS = [1.0] * 30                  # วันต่อ ๆ ไปเพิ่มอีก ~1°C → พยากรณ์วันนี้ ~ 33.8°C = 92.8°F → bin 92-93


def fake_event(bins):
    return {"markets": [dict(groupItemTitle=lab, bestAsk=ask, bestBid=bid,
                             spread=round(ask - bid, 4), orderMinSize=5,
                             volumeNum=20000, liquidityNum=3000, clobTokenIds="[]")
                        for lab, ask, bid in bins]}


def scan_with(bins, pmin=0.90, min_edge=0.15, pmax=0.25):
    orig_ev, orig_md = CL.W.polymarket_event, CL.model_dist
    CL.W.polymarket_event = lambda *a, **k: fake_event(bins)
    CL.model_dist = lambda *a, **k: (MX_C, INCS)
    try:
        return CL.scan_city("dallas", CFG, 16, pmax, min_edge, pmin, target=DAY.isoformat())
    finally:
        CL.W.polymarket_event, CL.model_dist = orig_ev, orig_md


fails = []


def check(name, cond, extra=""):
    print(("  ✓ " if cond else "  ✗ ") + name + (("  " + extra) if extra else ""))
    if not cond:
        fails.append(name)


print("T1) YES tier ตามราคา + เลือก bin เดียว (argmax)")
r = scan_with([("92-93°F", 0.06, 0.05), ("90-91°F", 0.05, 0.04), ("88-89°F", 0.02, 0.01)])
check("ได้ไม้เดียว", len(r["picks"]) == 1, str([p["bin"] for p in r["picks"]]))
if r["picks"]:
    p = r["picks"][0]
    check("tier = หลัก (ask ≤0.10)", p["tier"].startswith("หลัก"), p["tier"])
    check("ราคา ≤ 0.10", p["ask"] <= 0.10, str(p["ask"]))

r2 = scan_with([("92-93°F", 0.06, 0.05), ("90-91°F", 0.18, 0.17)])
check("bin ที่ p สูงสุดถูกเลือก", r2["picks"][0]["bin"] == "92-93°F", r2["picks"][0]["bin"])

r3 = scan_with([("92-93°F", 0.18, 0.17)])
check("tier = รอง เมื่อ ask 0.10–0.25", r3["picks"] and r3["picks"][0]["tier"].startswith("รอง"),
      r3["picks"][0]["tier"] if r3["picks"] else "no pick")

print("T2) ฝั่ง NO: bin เป็นไปไม่ได้ (bin top < max-so-far − 0.5) + YES bid ≥0.50 + bid−p ≥0.30")
# 88-89°F: top 89 < 91−0.5=90.5 → เป็นไปไม่ได้ · bid 0.67 → จ่าย NO 0.33
r4 = scan_with([("88-89°F", 0.70, 0.67), ("92-93°F", 0.06, 0.05)])
check("มีไม้ NO 1 ไม้", len(r4["no_picks"]) == 1, str(len(r4["no_picks"])))
if r4["no_picks"]:
    n = r4["no_picks"][0]
    check("bin = 88-89°F", n["bin"] == "88-89°F", n["bin"])
    check("จ่าย NO = 1 − bid (0.33)", abs(n["no_ask_implied"] - 0.33) < 1e-9, str(n["no_ask_implied"]))
    check("edge = bid − p ≥ 0.30", n["edge"] >= 0.30, str(n["edge"]))

print("T3) ฝั่ง NO: bin ที่ยังเป็นไปได้ / bid ต่ำ / margin ไม่พอ → ต้องไม่เข้า")
r5 = scan_with([("90-91°F", 0.80, 0.78)])          # top 91 = max-so-far → ยังเป็นไปได้
check("bin ที่ยังเป็นไปได้ ไม่เข้า", len(r5["no_picks"]) == 0, str(r5["no_picks"]))
r6 = scan_with([("88-89°F", 0.45, 0.42)])          # bid < 0.50
check("bid < 0.50 ไม่เข้า", len(r6["no_picks"]) == 0, str(r6["no_picks"]))
r7 = scan_with([("88-89°F", 0.70, 0.67)], min_edge=0.80)   # margin ไม่ถึง
check("margin bid−p < 0.30 ไม่เข้า", len(r7["no_picks"]) == 0, str(r7["no_picks"]))

print("T4) parse ราคา/bin ไม่เพี้ยน (92-93°F ต้องไม่ถูกอ่านเป็น 92, −93)")
check("parse 92-93°F", CL.parse_bin_num("92-93°F") == (92.0, 93.0), str(CL.parse_bin_num("92-93°F")))
check("parse 23°C", CL.parse_bin_num("23°C") == (23.0, 23.0))
check("parse 23°C or below", CL.parse_bin_num("23°C or below") == (float("-inf"), 23.0))
check("parse 98°F or above", CL.parse_bin_num("98°F or above") == (98.0, float("inf")))

print("T5) จักรวาลเมือง (สร้างจากข้อมูลจริง) + log v2")
u = CL.build_universe()
check("จักรวาลมี 33 เมือง (±3)", u is not None and 30 <= len(u) <= 36, str(len(u) if u else None))
check("hong-kong/shenzhen ถูกตัด", u is not None and "hong-kong" not in u and "shenzhen" not in u)
check("denver อยู่ในจักรวาล", u is not None and "denver" in u)

tmp = tempfile.mkdtemp()
CL.LOG = os.path.join(tmp, "log.csv")
orig_scan = CL.scan_city
CL.scan_city = lambda *a, **k: r4
sys.argv = ["cheap_live.py", "--cities", "dallas", "--log", "--no-universe-filter"]
try:
    CL.main()
except SystemExit:
    pass
finally:
    CL.scan_city = orig_scan
import csv as _csv
rows = list(_csv.DictReader(open(CL.LOG)))
check("log v2 เขียน 2 แถว (YES+NO)", len(rows) == 2, str(len(rows)))
check("คอลัมน์ครบตาม v2", list(rows[0].keys()) == CL.LOG_COLS if rows else False)
if rows:
    side = {r["side"] for r in rows}
    check("มีทั้ง side=YES และ side=NO", side == {"YES", "NO"}, str(side))
    no_row = [r for r in rows if r["side"] == "NO"][0]
    check("แถว NO มี yes_bid + no_ask_implied", no_row["yes_bid"] != "" and no_row["no_ask_implied"] != "")

print()
print("ผลรวม: %s" % ("ผ่านทั้งหมด ✓" if not fails else "ไม่ผ่าน %d รายการ: %s" % (len(fails), fails)))
sys.exit(1 if fails else 0)
