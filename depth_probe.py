#!/usr/bin/env python3
"""depth_probe.py — เก็บ order book เต็มบันได (L2 ladder) ของ bin ที่สนใจ ในหน้าต่างเข้าไม้

ทำไมต้องมี (ช่องสุดท้ายที่เหลือ): snapshot เก็บ best bid/ask + depth $ ของไม้ที่เข้าเท่านั้น
        trades_probe เก็บดีลจริง — แต่ยังขาด "บันไดราคาทั้งเล่ม" ที่ตอบคำถาม capacity:
        "ถ้าซื้อ $500 ที่ bin นี้ ราคาจะไถล (slippage) แค่ไหน"

เก็บ: data/depth_live.jsonl — หนึ่งบรรทัด = หนึ่ง bin หนึ่งรอบ
      {ts, city, bin, token, mid, asks:[[px,size]...], bids:[[px,size]...], n_ask, n_bid}

รัน: python3 depth_probe.py --log                (เรียกจากโซ่ทุก 5 นาที · จบเร็วถ้าไม่มีเมืองในหน้าต่าง)
"""
import argparse
import glob
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wxedge as W  # noqa: E402
from trades_probe import in_window, latest_rows  # noqa: E402  (ตรรกะเดียว ไม่คัดลอก)

OUT = os.path.join(HERE, "data", "depth_live.jsonl")
UA = {"User-Agent": "wxedge/1.0 (depth-probe)"}


def book_full(token, tries=3):
    """บันไดเต็มจาก CLOB /book → (asks, bids) เรียงตามราคา (ถูก→แพง / แพง→ถูก)"""
    for i in range(tries):
        try:
            j = json.loads(urllib.request.urlopen(urllib.request.Request(
                "https://clob.polymarket.com/book?token_id=%s" % token, headers=UA), timeout=45).read().decode())
            asks = sorted(([float(a["price"]), float(a["size"])] for a in (j.get("asks") or [])), key=lambda x: x[0])
            bids = sorted(([float(b["price"]), float(b["size"])] for b in (j.get("bids") or [])), key=lambda x: -x[0])
            if asks or bids:
                return asks, bids
            return [], []
        except Exception:
            if i == tries - 1:
                return None, None
            time.sleep(1.5)
    return None, None


def slippage_for(asks, notional):
    """ซื้อไล่บันไดจนใช้เงินครบ notional → คืน (ราคาเฉลี่ย, ราคาสุดท้ายที่จ่าย, size รวม) หรือ None"""
    spent = 0.0
    size = 0.0
    last = None
    for px, sz in asks:
        take = min(sz, (notional - spent) / px)
        spent += take * px
        size += take
        last = px
        if spent >= notional - 1e-9:
            return (spent / size, last, size) if size else None
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", action="store_true")
    ap.add_argument("--window", default="14:30-17:30")
    ap.add_argument("--max-bins", type=int, default=3, help="จำนวน bin ต่อเมือง (เรียงตาม model_p)")
    ap.add_argument("--min-gap-sec", type=int, default=240, help="ข้ามถ้าเก็บ token นี้ไปแล้วภายใน N วินาที")
    ap.add_argument("--levels", type=int, default=0,
                    help="จำนวนระดับที่บันทึกลงไฟล์ (0 = ทั้งเล่มเท่าที่ API ส่งมา · slippage คำนวณจากทั้งเล่มเสมอ)")
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
        bins = [b for b in (r.get("bins") or []) if len(b) >= 6 and b[5] and b[1] is not None]
        bins.sort(key=lambda b: -(b[1] or 0))
        if bins:
            picked.append((city, lt.strftime("%H:%M"), bins[:a.max_bins]))
    if not picked:
        print("ไม่มีเมืองในหน้าต่าง %s — ไม่เก็บ" % a.window)
        return

    # อ่านเวลาที่เก็บล่าสุดต่อ token (กันเก็บซ้ำถี่)
    last_seen = {}
    if a.log and os.path.exists(OUT):
        for line in open(OUT, encoding="utf-8"):
            try:
                r = json.loads(line)
                last_seen[r["token"]] = max(last_seen.get(r["token"], 0), int(r["ts"]))
            except Exception:
                continue
    now_ts = int(now.timestamp())
    f = open(OUT, "a", encoding="utf-8") if a.log else None
    added = skipped = 0
    for city, local, bins in picked:
        for b in bins:
            tok = b[5]
            if now_ts - last_seen.get(tok, 0) < a.min_gap_sec:
                skipped += 1
                continue
            asks, bids = book_full(tok)
            if asks is None:
                continue
            keep = a.levels if a.levels > 0 else None      # None = เก็บทั้งเล่ม
            rec = dict(ts=now_ts, city=city, bin=b[0], token=tok, local=local,
                       model_p=b[1], best_ask=(asks[0][0] if asks else None), best_bid=(bids[0][0] if bids else None),
                       asks=(asks if keep is None else asks[:keep]), bids=(bids if keep is None else bids[:keep]),
                       n_ask=len(asks), n_bid=len(bids),
                       depth_ask_usd=round(sum(p * s for p, s in asks), 1) if asks else 0.0,
                       slip_12=slippage_for(asks, 12.0), slip_100=slippage_for(asks, 100.0),
                       slip_500=slippage_for(asks, 500.0))
            if f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                added += 1
            last_seen[tok] = now_ts
            time.sleep(0.2)
    if f:
        f.close()
    print("เก็บ ladder %d bin จาก %d เมือง (ข้าม %d ที่เพิ่งเก็บ)" % (added, len(picked), skipped))


if __name__ == "__main__":
    main()
