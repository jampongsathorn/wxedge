#!/usr/bin/env python3
"""trades_probe.py — เก็บ "ดีลจริง" (fills) ของ bin ที่เราสนใจ ในหน้าต่างเวลาเข้าไม้

ทำไมต้องมี (ผู้ใช้ยืนยัน 26 ก.ย. 2026 · Q3): snapshot เก็บได้แค่ bid/ask ที่อาจเป็น quote ค้าง
(เคสจริง: dallas บันทึก ask 0.04 แต่ไม่มีดีลเลยหลัง 16:00) — ดีลจริงคือหลักฐานว่า "ซื้อได้จริง"
เก็บลง data/trades_live.jsonl (dedupe ด้วย txHash+asset+t+size+price) แล้วโซ่ push ขึ้น GitHub

⛔ บทเรียนราคาแพง (พบ 1 ต.ค. 2026): **ห้ามใช้ `?asset_id=` กับ data-api** — มันเพิกเฉย param นี้
แล้วคืน "global trade feed" (ทดสอบ: asset_id=NOT_A_REAL_TOKEN → ยังได้ 5 rows; asset_id=<token จริง>
→ ได้ 28 asset อื่น + ตลาด BTC ปนมา) ทำให้ข้อมูล 80,000 แถวแรก (26 ก.ย.–1 ต.ค.) ใช้ไม่ได้ทั้งไฟล์
ต้องล้างทิ้ง · ทางที่ถูก = `?market=<conditionId>&limit=N` (คืนเฉพาะตลาดนั้น แล้วกรอง asset = YES token)
ทุกแถวใหม่มี provenance: src="market" · asset · cond → ตรวจย้อนได้

รัน: python3 trades_probe.py --log
จบเร็วเมื่อไม่มีเมืองในหน้าต่าง (เรียกได้ทุก 5 นาทีโดยไม่มีค่าใช้จ่าย)
"""
import argparse
import glob
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wxedge as W  # noqa: E402

SNAP = os.path.join(HERE, "data", "snapshots")
OUT = os.path.join(HERE, "data", "trades_live.jsonl")
API = "https://data-api.polymarket.com/trades"
UA = {"User-Agent": "wxedge/1.0 (trades-probe)"}


def in_window(local_hour, win="14:30-17:30"):
    """อยู่ในหน้าต่างเก็บไหม (รองรับครึ่งชั่วโมง)"""
    h = float(local_hour)
    a, b = win.split("-")
    f = lambda x: int(x.split(":")[0]) + int(x.split(":")[1]) / 60.0   # noqa: E731
    return f(a) <= h <= f(b)


def dedupe_key(t):
    return "%s|%s|%s|%s|%s" % (t.get("transactionHash"), t.get("asset"), t.get("timestamp"),
                               t.get("size"), t.get("price"))


def recent(trades, since_ts):
    """เฉพาะดีลที่เกิดหลัง since_ts (ดีลในรอบนั้น ๆ)"""
    out = []
    for t in trades:
        try:
            if int(t.get("timestamp", 0)) >= since_ts:
                out.append(t)
        except (TypeError, ValueError):
            continue
    return out


def fetch_market_trades(cond, limit=500):
    """ดีล "ล่าสุด" ของตลาดเดียว (conditionId) เรียงตามเวลา

    ⚠️ ต้องเป็น market= เท่านั้น — asset_id= ถูกเพิกเฉย (global feed) ดู docstring หัวไฟล์
    """
    q = "%s?market=%s&limit=%d" % (API, cond, limit)
    for attempt in range(3):
        try:
            got = json.loads(urllib.request.urlopen(urllib.request.Request(q, headers=UA), timeout=45).read().decode())
            return got if isinstance(got, list) else []
        except Exception:
            if attempt == 2:
                return []
            time.sleep(1.5)
    return []


def keep_token_rows(rows, token, token_no=None):
    """เหลือเฉพาะดีลของ YES token นี้ → (kept, n_other, n_weird)

    n_other  = ดีลของ NO token ของตลาดเดียวกัน (ปกติ — ไม่เก็บ เพราะเราซื้อ YES)
    n_weird  = ดีลที่ asset ไม่ใช่ทั้ง YES/NO ของตลาดนี้ = สัญญาณ param เพี้ยนอีก (ต้องเป็น 0)
    """
    ok = {str(token)}
    if token_no:
        ok.add(str(token_no))
    kept, other, weird = [], 0, 0
    for t in rows:
        a = str(t.get("asset") or "")
        if a == str(token):
            kept.append(t)
        elif a in ok:
            other += 1
        else:
            weird += 1
    return kept, other, weird


def latest_rows():
    """แถว snapshot ล่าสุดต่อเมือง ของวันนี้ (UTC)"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = os.path.join(SNAP, "%s.jsonl" % today)
    if not os.path.exists(path):                     # ต้นวัน UTC อาจยังไม่มีไฟล์วันนี้ → ย้อนวันก่อนหน้า
        files = sorted(glob.glob(os.path.join(SNAP, "*.jsonl")))
        if not files:
            return []
        path = files[-1]
    last = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        last[r.get("city")] = r
    return list(last.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", action="store_true", help="เขียนต่อท้าย data/trades_live.jsonl")
    ap.add_argument("--window", default="14:30-17:30", help="หน้าต่าง local hour ที่เก็บ (ครึ่งชั่วโมงได้)")
    ap.add_argument("--max-bins", type=int, default=3, help="จำนวน bin ต่อเมือง (เรียงตาม model_p)")
    ap.add_argument("--within-sec", type=int, default=420, help="เอาเฉพาะดีลที่เกิดภายใน N วินาทีที่ผ่านมา")
    a = ap.parse_args()

    cfgs = json.load(open(W.STATIONS, encoding="utf-8"))
    rows = latest_rows()
    now = datetime.now(timezone.utc)
    picked = []
    for r in rows:
        city = r.get("city")
        if city not in cfgs:
            continue
        lt = now.astimezone(ZoneInfo(cfgs[city]["tz"]))
        if not in_window(lt.hour + lt.minute / 60.0, a.window):
            continue
        bins = [b for b in (r.get("bins") or []) if len(b) >= 4 and b[1] is not None and b[2] is not None]
        bins.sort(key=lambda b: -(b[1] or 0))
        picked.append((city, r, bins[:a.max_bins]))
    if not picked:
        print("ไม่มีเมืองในหน้าต่าง %s — ไม่เก็บ" % a.window)
        return
    # token จาก snapshot (ธาตุที่ 6) · cond จาก meta ของ exec_backtest (แคชต่อ city/วัน)
    keys = set()
    if a.log and os.path.exists(OUT):
        for line in open(OUT, encoding="utf-8"):
            try:
                keys.add(dedupe_key(json.loads(line)))
            except json.JSONDecodeError:
                continue
    since = int((now - timedelta(seconds=a.within_sec)).timestamp())
    added = anomaly = 0
    f = open(OUT, "a", encoding="utf-8") if a.log else None
    import exec_backtest as EB                       # แคช meta ต่อ (city, date) — ยิง Gamma ครั้งเดียว/เมือง/วัน
    for city, r, bins in picked:
        target = r.get("target") or datetime.now(ZoneInfo(cfgs[city]["tz"])).date().isoformat()
        try:
            meta = EB.market_meta(city, target) or {}
        except Exception:
            meta = {}
        for b in bins:
            lab = b[0]
            m = meta.get(lab) or {}
            token = b[5] if len(b) >= 6 and b[5] else m.get("token")
            cond = m.get("cond")
            if not token or not cond:                # ไม่มี conditionId → ยิงไม่ได้ (snapshot เพียว ๆ ไม่พอ)
                continue
            kept, _other, weird = keep_token_rows(fetch_market_trades(cond), token, m.get("token_no"))
            anomaly += weird                         # >0 = ได้ดีลที่ไม่ใช่ YES/NO ของตลาดนี้ = param เพี้ยนอีก
            for t in recent(kept, since):
                k = dedupe_key(t)
                if k in keys:
                    continue
                keys.add(k)
                rec = dict(ts_collected=now.strftime("%Y-%m-%dT%H:%M:%SZ"), city=city, bin=lab, token=token,
                           t=int(t.get("timestamp", 0)), side=t.get("side"), price=t.get("price"),
                           size=t.get("size"), tx=t.get("transactionHash"), local=lt.strftime("%H:%M"),
                           src="market", asset=str(t.get("asset") or ""), cond=cond)   # provenance
                if f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    added += 1
            time.sleep(0.15)
    if f:
        f.close()
    print("เก็บดีลใหม่ %d รายการ จาก %d เมือง%s" % (
        added, len(picked), (" · ⚠ anomaly %d bin (ได้ดีลแต่ไม่ใช่ token เรา — ตรวจ param!)" % anomaly) if anomaly else ""))


if __name__ == "__main__":
    main()
