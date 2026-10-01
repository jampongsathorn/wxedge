#!/usr/bin/env python3
"""execution_report.py — เทียบ PnL 4 แบบของไม้ที่บันทึกไว้ (หลักฐานระดับ execution)

แบบที่ 1  last-trade PnL        — ราคา prices-history (วิธีเก่า · มักเป็นภาพลวง)
แบบที่ 2  best-ask PnL          — ราคา ask จากสมุด ณ ตอนเข้า (Gamma หรือ CLOB)
แบบที่ 3  trade-replay PnL      — ราคาที่ "สะสมfill ได้จริง" จากดีลจริง (exec_price)
แบบที่ 4  ladder-VWAP PnL       — ราคาที่กินบันไดจริง (slippage-aware จาก depth_live)

พร้อม fill rate (มีดีลพอไหม) และ slippage ต่อไม้ → ตอบได้ว่า "strategy กำไรจริงจากราคาที่ execute ได้ไหม"

รัน: python3 execution_report.py [--stake 12] [--window-min 20]
"""
import argparse
import csv
import json
import os
import statistics as st
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wxedge as W  # noqa: E402
from exec_backtest import yes_fills, exec_price  # noqa: E402

LOG = os.path.join(HERE, "data", "cheap_live_log.csv")
TRADES = os.path.join(HERE, "data", "trades_live.jsonl")
DEPTH = os.path.join(HERE, "data", "depth_live.jsonl")
REPORT = os.path.join(HERE, "reports", "execution_report.md")


def load_log():
    if not os.path.exists(LOG):
        return []
    return [r for r in csv.DictReader(open(LOG, encoding="utf-8")) if r.get("city")]


def load_jsonl(p):
    out = []
    if not os.path.exists(p):
        return out
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def ladder_vwap(asks, notional):
    """VWAP กินบันไดจนครบ notional (None ถ้าไม่พอ)"""
    spent = size = 0.0
    for px, sz in asks:
        take = min(sz, (notional - spent) / px)
        spent += take * px
        size += take
        if spent >= notional - 1e-9:
            return spent / size if size else None
    return None


def pnl_of(won, px):
    return (1.0 / px - 1.0) if won else -1.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stake", type=float, default=12.0)
    ap.add_argument("--window-min", type=int, default=20)
    ap.add_argument("--fetch", action="store_true",
                    help="ไม้ที่ไม่มีดีลใน trades_live → ดึงดีลย้อนหลังจาก data-api (แคชเดียวกับ exec_backtest)")
    a = ap.parse_args()
    cfgs = json.load(open(W.STATIONS, encoding="utf-8"))
    log = load_log()
    # provenance: ใช้เฉพาะดีลรูปแบบใหม่ (src=market · มี asset/cond) — แถวเก่า (asset_id bug,
    # global feed) ถูกถอนทิ้ง 1 ต.ค. 2026 (README §33) ถ้ามีตกค้างในไฟล์ ให้ข้าม + นับไว้
    raw_trades = load_jsonl(TRADES)
    trades = [t for t in raw_trades if t.get("asset")]
    legacy = len(raw_trades) - len(trades)
    if legacy:
        print("⚠ ข้ามดีลรูปแบบเก่า %d แถว (ไม่มี provenance — ดู README §33)" % legacy)
    depth = load_jsonl(DEPTH)

    rows = []
    for r in log:
        city, bin_ = r["city"], r["bin"]
        tz = ZoneInfo(cfgs[city]["tz"])
        try:
            T = datetime.strptime(r["target"] + " 16:00", "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        except Exception:
            T = None
        won = (r.get("won") == "1")
        rec = dict(city=city, bin=bin_, side=r.get("side"), won=won,
                   last=None, ask=None, ask_src="", trade_px=None, trade_n=0, ladder_px=None,
                   slip=None, status="")

        # 1) last-trade ณ เวลานั้น (คอลัมน์ yes_last)
        try:
            rec["last"] = float(r["yes_last"]) if r.get("yes_last") not in (None, "") else None
        except ValueError:
            pass
        # 2) best ask: ใช้ราคาสมุดจริงถ้ามี (book_best_ask) ไม่งั้น Gamma ask
        for k, src in (("book_best_ask", "CLOB book"), ("yes_ask", "Gamma")):
            try:
                v = float(r.get(k)) if r.get(k) not in (None, "") else None
            except ValueError:
                v = None
            if v is not None:
                rec["ask"], rec["ask_src"] = v, src
                break
        # 3) trade-replay: ดีลจริงที่เก็บไว้ (trades_live.jsonl) ในหน้าต่าง T..T+window
        #    กันสับสน bin ชื่อซ้ำข้ามวัน → เทียบ token_id จริงก่อน
        tok0 = r.get("token_id")
        local_rows = [t for t in trades
                      if t.get("city") == city and t.get("bin") == bin_
                      and (not tok0 or not t.get("token") or t.get("token") == tok0)]
        local_hit = bool(local_rows)
        if T is not None and local_hit:
            tok = r.get("token_id")
            fills = []
            for t in trades:
                if t.get("city") != city or t.get("bin") != bin_ or t.get("side") != "BUY":
                    continue
                if tok and t.get("token") and t["token"] != tok:
                    continue
                try:
                    fills.append((int(t["t"]), "BUY", float(t["price"]), float(t["size"])))
                except (KeyError, TypeError, ValueError):
                    continue
            fills.sort()
            ex = exec_price(fills, int(T.timestamp()), a.window_min, a.stake)
            if ex:
                rec["trade_px"], rec["trade_n"] = ex["px"], ex["n_buy"]
            local_hit = True
        if a.fetch and T is not None and rec["trade_px"] is None and r.get("target"):
            try:
                import exec_backtest as EB
                meta = EB.market_meta(city, r["target"]).get(bin_) or {}
                if meta.get("cond"):
                    fills = yes_fills(EB.fetch_trades(meta["cond"]), meta.get("token"))
                    ex = exec_price(fills, int(T.timestamp()), a.window_min, a.stake)
                    print("  fetch %s %s: ดีล YES %d · fill %s" % (city, bin_, len(fills), "ได้" if ex else "ไม่ได้ (no-fill)"))
                    if ex:
                        rec["trade_px"], rec["trade_n"] = ex["px"], ex["n_buy"]
                    else:
                        rec["trade_n"] = 0
            except Exception as e:
                print("fetch ล้ม (%s %s): %s" % (city, bin_, str(e)[:50]))
        # 4) ladder-VWAP จาก depth_live.jsonl (จุดที่ใกล้ T ที่สุดของ bin นี้)
        cand = [d for d in depth if d.get("city") == city and d.get("bin") == bin_ and d.get("asks")]
        if cand and T is not None:
            best = min(cand, key=lambda d: abs(int(d["ts"]) - int(T.timestamp())))
            v = ladder_vwap(best["asks"], a.stake)
            if v:
                rec["ladder_px"] = v
                if rec["ask"]:
                    rec["slip"] = round(v - rec["ask"], 4)
        rows.append(rec)

    # ── สรุป ──
    def summary(key):
        s = [x for x in rows if x[key] is not None]
        if not s:
            return dict(n=0, w=0, hit=None, pnl=None)
        w = sum(1 for x in s if x["won"])
        pnl = st.mean(pnl_of(x["won"], x[key]) for x in s) * a.stake
        return dict(n=len(s), w=w, hit=100.0 * w / len(s), pnl=pnl * len(s))

    L = ["# execution_report — PnL 4 แบบ เทียบกัน (หลักฐานระดับ execution)", "",
         "อัปเดต %s · ไม้ใน log %d · stake $%.0f · หน้าต่าง %d นาที" % (
             datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ"), len(rows), a.stake, a.window_min), ""]
    if not rows:
        L += ["**ยังไม่มีไม้ใน log** — ระบบจะรายงานอัตโนมัติเมื่อมีไม้จริง", ""]
    else:
        L += ["| แบบ | ไม้ | ชนะ | hit | PnL รวม (ที่ stake นี้) |", "|---|---|---|---|---|"]
        for key, name in (("last", "1) last-trade (วิธีเก่า)"), ("ask", "2) best-ask ณ ตอนเข้า"),
                          ("trade_px", "3) trade-replay (ดีลจริงสะสม)"), ("ladder_px", "4) ladder-VWAP (slippage)")):
            g = summary(key)
            L.append("| %s | %d | %d | %s | %s |" % (
                name, g["n"], g["w"], ("%.1f%%" % g["hit"]) if g["hit"] is not None else "—",
                ("$%+.2f" % g["pnl"]) if g["pnl"] is not None else "—"))
        L += ["", "**รายไม้**", "",
              "| เมือง | bin | ผล | last | ask (แหล่ง) | trade-replay | ดีล | ladder-VWAP | slippage |",
              "|---|---|---|---|---|---|---|---|---|"]
        for x in rows:
            L.append("| %s | %s | %s | %s | %s | %s | %d | %s | %s |" % (
                x["city"], x["bin"], "ชนะ" if x["won"] else "แพ้",
                ("%.3f" % x["last"]) if x["last"] is not None else "—",
                ("%.3f (%s)" % (x["ask"], x["ask_src"])) if x["ask"] is not None else "—",
                ("%.3f" % x["trade_px"]) if x["trade_px"] is not None else "no-fill",
                x["trade_n"],
                ("%.4f" % x["ladder_px"]) if x["ladder_px"] is not None else "—",
                ("%+.4f" % x["slip"]) if x["slip"] is not None else "—"))
        L += ["", "**ที่มา**: last = `yes_last` (CLOB prices-history) · ask = `book_best_ask`/`yes_ask` (Gamma) · "
              "trade-replay = ดีลจริงใน `trades_live.jsonl` (exec_price) · ladder-VWAP = `depth_live.jsonl`", ""]
    L += ["## ข้อจำกัดที่ต้องเขียนให้แม่น", "",
          "- **trade-replay = executed-trade path** ไม่ใช่ historical L2 order book — วัดได้ว่า \"มีคนซื้อขายจริงที่ราคานี้\" "
          "ไม่ใช่ \"มีคำสั่งค้างให้ซื้อได้\"", 
          "- **ladder-VWAP** ใช้ได้เฉพาะช่วงที่ `depth_live.jsonl` เก็บแล้ว (ตลาดอนาคต) — ย้อนหลังไม่ได้",
          "- **book_parity** (วัด Gamma vs CLOB พร้อม timestamp) พบ diff ได้ถึง 0.08 ที่ delta_ms=0 → ราคา Gamma อาจค้าง "
          "ดังนั้นราคาเข้าให้ใช้ CLOB book เป็นหลัก (`--levels 0` เก็บทั้งเล่ม)", ""]
    open(REPORT, "w", encoding="utf-8").write("\n".join(L))
    print("เขียน %s" % REPORT)
    print("ไม้ %d · trade-replay วัดได้ %d · ladder วัดได้ %d" % (
        len(rows), sum(1 for x in rows if x["trade_px"] is not None),
        sum(1 for x in rows if x["ladder_px"] is not None)))


if __name__ == "__main__":
    main()
