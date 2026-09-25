#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bidask_check.py — ตอบคำถาม: "PnL ที่พิสูจน์ พิสูจน์จาก bid/ask หรือจาก price (last trade)?"

วิธีวัด (ทำได้จริงตอนนี้):
  • ไล่ทุกเมือง หา "ตลาดของวันนี้ที่ยังไม่ตัดสิน" (ask สูงสุด < 0.995 และ ask ต่ำสุด < 0.90)
  • สำหรับแต่ละ bin: เทียบ bestBid/bestAsk (จาก Gamma) กับ ราคาซื้อขายล่าสุด (clob prices-history)
  • วัด: ask − last trade (ต้นทุนจริงที่ต้องจ่ายเพิ่มเมื่อซื้อ YES) · last − bid · spread
  • ตลาดที่ตัดสินแล้ว (ราคานิ่ง 0.001/0.999) ถูกตัดออก เพราะไม่สะท้อนต้นทุนตอนเทรด

รัน: python3 bidask_check.py            → reports/bidask_check.md + data/bidask_probe.json
"""
import argparse, json, os, statistics as st, sys, time
from datetime import datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wxedge as W
import cheap_bets as CB

MD = os.path.join(HERE, "reports", "bidask_check.md")
PROBE = os.path.join(HERE, "data", "bidask_probe.json")


def q(v, p):
    if not v:
        return None
    s = sorted(v)
    return s[min(len(s) - 1, int(p * len(s)))]


def collect(max_bins=140, pace=0.2):
    cfgs = json.load(open(W.STATIONS, encoding="utf-8"))
    rows, settled = [], []
    for c, cfg in cfgs.items():
        if not cfg.get("icao"):
            continue
        local = datetime.now(ZoneInfo(cfg["tz"]))
        day = local.date().isoformat()
        try:
            ev = W.polymarket_event(c, day, no_cache=True)
        except Exception:
            continue
        if not ev or ev.get("closed"):
            continue
        ms = ev.get("markets") or []
        asks = [float(m["bestAsk"]) for m in ms if m.get("bestAsk") not in (None, "")]
        if not asks:
            continue
        if not (min(asks) < 0.90 and max(asks) < 0.995):
            settled.append(dict(city=c, local=local.strftime("%H:%M"), lo=min(asks), hi=max(asks)))
            continue
        for m in ms:
            try:
                ask = float(m["bestAsk"])
            except (TypeError, ValueError):
                continue
            if ask >= 0.99 or ask <= 0.002:      # ปลายสุดของสมุดคำสั่ง ไม่สะท้อนต้นทุนจริง
                continue
            bid = m.get("bestBid")
            try:
                bid = float(bid) if bid not in (None, "") else None
            except (TypeError, ValueError):
                bid = None
            tok = json.loads(m.get("clobTokenIds") or "[]")
            lt = None
            if tok:
                try:
                    s = CB.price_series(tok[0], day)
                    lt = s[-1][1] if s else None
                except Exception:
                    lt = None
                time.sleep(pace)
            rows.append(dict(city=c, local=local.strftime("%H:%M"), date=day, bin=m.get("groupItemTitle"),
                             bid=bid, ask=ask, last=lt,
                             spread=(round(ask - bid, 4) if bid is not None else None),
                             vol=float(m.get("volumeNum") or 0)))
            if len(rows) >= max_bins:
                break
        print("  %-14s %s — เก็บ %d bin (สะสม %d)" % (c, local.strftime("%H:%M"),
                                                    sum(1 for r in rows if r["city"] == c), len(rows)), flush=True)
        if len(rows) >= max_bins:
            break
    return rows, settled


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-bins", type=int, default=140)
    ap.add_argument("--stake", type=float, default=100.0)
    a = ap.parse_args()
    rows, settled = collect(a.max_bins)
    ok = [r for r in rows if r["last"] is not None and r["ask"] is not None]
    ta = [r["ask"] - r["last"] for r in ok]
    tb = [r["last"] - r["bid"] for r in ok if r["bid"] is not None]
    sp = [r["spread"] for r in rows if r["spread"] is not None]
    outside = [r for r in ok if r["bid"] is not None and (r["last"] < r["bid"] - 1e-9 or r["last"] > r["ask"] + 1e-9)]
    liquid = [r for r in ok if r["vol"] >= 20000]
    ta_liq = [r["ask"] - r["last"] for r in liquid]
    json.dump(dict(measured_at=datetime.utcnow().isoformat() + "Z", n_bins=len(rows), n_compared=len(ok),
                   settled_markets=settled, rows=rows), open(PROBE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\nbin %d · เทียบได้ %d · ตลาดที่ตัดสินแล้ว (ตัดออก) %d" % (len(rows), len(ok), len(settled)))
    if ta:
        print("ask − last : median %+.4f · เฉลี่ย %+.4f · p90 %+.4f" % (q(ta, .5), st.mean(ta), q(ta, .9)))
    if tb:
        print("last − bid : median %+.4f · เฉลี่ย %+.4f" % (q(tb, .5), st.mean(tb)))
    if sp:
        print("spread     : median %.4f · เฉลี่ย %.4f · p90 %.4f" % (q(sp, .5), st.mean(sp), q(sp, .9)))

    # แปลเป็นผลกับ PnL ของเรา: ใช้ไม้จริง 379 ไม้ แล้วบวกต้นทุนตามที่วัดได้
    trades = []
    tr_path = os.path.join(HERE, "data", "trades_recommended.csv")
    if os.path.exists(tr_path):
        import csv as _csv
        trades = list(_csv.DictReader(open(tr_path, encoding="utf-8")))

    def pnl_with(delta, only_no=False):
        tot = 0.0
        for t in trades:
            if only_no and t["side"] != "NO":
                continue
            e = min(0.999, float(t["entry"]) + delta)
            tot += a.stake * (int(t["won"]) / e - 1.0)
        return tot

    base = pnl_with(0.0)
    med = q(ta, .5) or 0.0
    p90 = q(ta, .9) or 0.0

    with open(MD, "w", encoding="utf-8") as f:
        w = f.write
        w("# พิสูจน์ PnL จากอะไร — price (last trade) หรือ bid/ask?\n\n")
        w("## คำตอบตรง ๆ\n\n")
        w("**การพิสูจน์ PnL ทั้งหมดที่ผ่านมา (379 ไม้ · $201,884) ใช้ ‘price’ = ราคาซื้อขายล่าสุดรายชั่วโมง** "
          "จาก `clob.polymarket.com/prices-history` — **ไม่ใช่ bid/ask จริง**\n\n")
        w("| ที่ใช้อยู่ | แหล่ง | หมายความว่า |\n|---|---|---|\n")
        w("| `price` ใน `data/cheap_bets.csv` (ใช้สร้าง PnL/ROI ทั้งหมด) | CLOB prices-history (last trade) | ราคาที่เพิ่งมีคนตกลงซื้อขายกัน — ไม่ใช่ราคาที่เราจะได้เป๊ะ |\n")
        w("| ฝั่ง NO = `1 − price(YES)` | คำนวณจาก last trade | **ยังไม่พิสูจน์** ต้องเทียบกับ `1 − YES bestBid` จริง |\n")
        w("| `bestBid`/`bestAsk` | Gamma API | ใช้ **เฉพาะในเครื่องมือสด** (`cheap_live.py` v2 + แดชบอร์ด) ตั้งแต่ 25 ก.ย. — ยังไม่มีข้อมูลย้อนหลังพอจะพิสูจน์ |\n\n")
        w("## วัดช่องว่างจริง (ตลาดที่ยังไม่ตัดสิน ณ ตอนวัด)\n\n")
        w(f"> `bidask_check.py` · {len(rows)} bin ใน {len({r['city'] for r in rows})} เมือง · เวลาไทย "
          f"{datetime.now(ZoneInfo('Asia/Bangkok')).strftime('%Y-%m-%d %H:%M')} · ตัดตลาดที่ตัดสินแล้วออก {len(settled)} แห่ง "
          f"(ราคานิ่ง 0.001/0.999) เพราะไม่สะท้อนต้นทุนตอนเทรด\n\n")
        w("| ตัวชี้วัด | median | เฉลี่ย | p90 |\n|---|---|---|---|\n")
        if ta:
            w(f"| **ask − last trade** (ต้นทุนที่ต้องจ่ายเพิ่มเมื่อซื้อ YES) | **{q(ta,.5):+.4f}** | {st.mean(ta):+.4f} | {q(ta,.9):+.4f} |\n")
        if tb:
            w(f"| last − bid (ได้น้อยลงถ้าต้องขาย NO) | {q(tb,.5):+.4f} | {st.mean(tb):+.4f} | — |\n")
        if sp:
            w(f"| spread (ask − bid) | {q(sp,.5):.4f} | {st.mean(sp):.4f} | {q(sp,.9):.4f} |\n")
        w(f"\n- last trade อยู่ **นอก** ช่วง bid–ask เพียง **{len(outside)}/{len(ok)}** ({len(outside)/max(1,len(ok))*100:.0f}%) "
          f"→ ราคาที่เราใช้พิสูจน์ 'เกาะกลางสมุด' อยู่แล้ว ไม่ได้ใช้ราคาประหลาด\n")
        if ta_liq:
            w(f"- เฉพาะ bin ที่ volume ≥ $20k (สภาพคล่องดี n={len(liquid)}): ask − last median {q(ta_liq,.5):+.4f}\n")

        w("## แปลตรง ๆ ว่า PnL จริงจะเหลือเท่าไร\n\n")
        w(f"ถ้าต้องจ่ายที่ **ask จริง** (บวกช่องว่างตามที่วัดได้ให้ทุกไม้):\n\n")
        w("| สมมติฐาน | PnL (379 ไม้ · $100/ไม้) | เทียบฐาน |\n|---|---|---|\n")
        w(f"| ฐาน (last trade — ที่รายงานไว้) | ${base:+,.0f} | — |\n")
        w(f"| + median ที่วัดได้ ({med:+.4f}) | ${pnl_with(med):+,.0f} | {pnl_with(med)-base:+,.0f} |\n")
        w(f"| + p90 ที่วัดได้ ({p90:+.4f}) | ${pnl_with(p90):+,.0f} | {pnl_with(p90)-base:+,.0f} |\n")
        w(f"| + 3 ticks (0.03, เผื่อไว้มาก) | ${pnl_with(0.03):+,.0f} | {pnl_with(0.03)-base:+,.0f} |\n")
        w(f"\n**ฝั่ง NO อย่างเดียว** (ส่วนที่ยังไม่พิสูจน์) ถ้าต้องซื้อ NO แพงขึ้นตามช่องว่างที่วัดได้:\n\n")
        w("| สมมติฐาน | PnL ฝั่ง NO |\n|---|---|\n")
        for d in (0.0, med, p90, 0.10):
            w(f"| ต้นทุน NO + {d:.4f} | ${pnl_with(d, only_no=True):+,.0f} |\n")
        w("\n## ยังต้องปิดอีก 3 ช่อง (ทำได้ด้วย log จริงเท่านั้น)\n\n")
        w("1. **ask ณ เวลา 16:00 local ของเมืองนั้น** — การวัดนี้ทำตอนที่ตลาดยังเปิดแต่คนละชั่วโมง (เช่น ตี 3 ของยุโรป) "
          "ช่องว่างอาจกว้างกว่านี้ตอนกลางวัน (คนเทรดเยอะ = แคบกว่า, แต่ตอนตลาดเงียบอาจกว้างกว่า)\n")
        w("2. **YES bestBid จริงตอนเข้าไม้** สำหรับฝั่ง NO → `cheap_live.py --log` เก็บคอลัมน์นี้ไว้แล้ว\n")
        w("3. **ความลึก (depth)** — ไม้ราคา 0.03 ให้ตัวคูณ ~33×: ต้องเช็คว่าขนาดไม้ของเราไม่กินเกิน ~5% ของ volume ที่ราคานั้น\n")
        w(f"\nข้อมูลดิบทั้งหมด (ทุก bin ทุกคอลัมน์): `data/bidask_probe.json`\n")
    print("เขียน %s" % MD)
    print("เขียน %s" % PROBE)


if __name__ == "__main__":
    main()
