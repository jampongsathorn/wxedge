#!/usr/bin/env python3
"""exec_backtest.py — rebuild backtest ด้วย "ราคาที่ซื้อได้จริง" จากดีลจริง (data-api trades)

ที่มา: backtest เดิมใช้ราคา last-trade ที่ stale ณ ขอบชั่วโมง (ดู reports/live_reality_check.md)
ตัวนี้ใช้ **ดีล BUY จริง** บน YES token ของ bin ที่เลือก เพื่อตอบให้ได้ว่า
    "ถ้าใช้กติกาของเรา (16:00 local · p≥0.90 · ask 0.02–0.25) เราซื้อได้จริงไหม และได้ผลเท่าไร"

นิยาม executable price (ยืนยันโดยผู้ใช้ 26 ก.ย. 2026 · option ข):
    ไล่ดีล BUY บน YES token ในหน้าต่าง [T, T+window] ตามลำดับเวลา → notional สะสม
    ราคาของดีลที่ทำให้ notional สะสม ≥ stake = ราคาที่ซื้อได้จริง (marginal fill)
    ถ้าไม่ถึง stake = **no-fill** (ไม่มีไม้ — ไม่นับเป็นแพ้)

แคช: data/exec_cache/trades_<conditionId>.json + meta_<city>_<date>.json (รันซ้ำได้ไม่ต้องดึงเน็ต)

รัน: python3 exec_backtest.py [--stake 12] [--window-min 20] [--limit N] [--offset N]
"""
import argparse
import csv
import json
import os
import statistics as st
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wxedge as W  # noqa: E402

DATA = os.path.join(HERE, "data")
CACHE = os.path.join(DATA, "exec_cache")
BETS = os.path.join(DATA, "cheap_bets.csv")
OUT_CSV = os.path.join(DATA, "exec_backtest.csv")
REPORT = os.path.join(HERE, "reports", "exec_backtest.md")
UA = {"User-Agent": "wxedge/1.0 (exec-backtest)"}
API = "https://data-api.polymarket.com/trades"


def _cached(name, fn):
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, name)
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    v = fn()
    json.dump(v, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    return v


def cohort(path=BETS, hour=16, pmin=0.90, lo=0.02, hi=0.25):
    out = []
    for r in csv.DictReader(open(path, encoding="utf-8")):
        try:
            if int(r["hour"]) != hour:
                continue
            p, mp = float(r["price"]), float(r["model_p"])
        except (ValueError, KeyError):
            continue
        if lo <= p <= hi and mp >= pmin:
            out.append(r)
    return out


def market_meta(city, date):
    """{bin_label: {token, cond}} ของตลาดวันนั้น (แคช)"""
    def _f():
        ev = W.polymarket_event(city, date, no_cache=True)
        m = {}
        for x in (ev or {}).get("markets") or []:
            lab = (x.get("groupItemTitle") or "").strip()
            try:
                toks = json.loads(x.get("clobTokenIds") or "[]")
            except json.JSONDecodeError:
                toks = []
            if lab and toks:
                m[lab] = {"token": toks[0], "cond": x.get("conditionId"), "token_no": toks[1] if len(toks) > 1 else None}
        return m
    return _cached("meta_%s_%s.json" % (city, date), _f)


def fetch_trades(cond, max_pages=25):
    """ดีลทั้งหมดของตลาด (แคช) — data-api ไม่มีตัวกรองเวลา ต้องไล่ offset"""
    def _f():
        out, off = [], 0
        for _ in range(max_pages):
            q = "%s?market=%s&limit=500&offset=%d" % (API, cond, off)
            got = None
            for attempt in range(4):
                try:
                    got = json.loads(urllib.request.urlopen(urllib.request.Request(q, headers=UA), timeout=60).read().decode())
                    break
                except Exception:
                    if attempt == 3:
                        raise
                    time.sleep(1.5 * (attempt + 1))
            if not got:
                break
            out += got
            off += 500
            if len(got) < 500:
                break
            time.sleep(0.2)
        return out
    return _cached("trades_%s.json" % cond, _f)


def yes_fills(trades, token):
    """[(ts, side, price, size)] เฉพาะ YES token เรียงตามเวลา"""
    out = []
    for t in trades:
        if t.get("asset") != token:
            continue
        try:
            out.append((int(t["timestamp"]), str(t.get("side")), float(t["price"]), float(t["size"])))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out)


def exec_price(fills, t0, window_min=20, stake=12.0):
    """ราคาที่ซื้อได้จริง: ดีล BUY ที่ทำให้ notional สะสมครบ stake ภายในหน้าต่าง

    คืน dict(px, notional, n_buy, first_px, secs) หรือ None ถ้า notional ไม่ถึง stake (no-fill)
    """
    t1 = t0 + window_min * 60
    cum = 0.0
    n = 0
    first_px = None
    for ts, side, px, size in fills:
        if ts < t0 or ts > t1 or side != "BUY":
            continue
        n += 1
        if first_px is None:
            first_px = px
        cum += px * size
        if cum >= stake:
            return dict(px=px, notional=round(cum, 2), n_buy=n, first_px=first_px, secs=ts - t0)
    return None


def roi_of(price):
    """PnL ต่อ $1 (ชนะได้ 1/price, แพ้เสีย 1) — ใช้กับไม้ที่ชนะ/แพ้เท่านั้น"""
    return 1.0 / price - 1.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stake", type=float, default=12.0, help="notional ขั้นต่ำที่ต้องซื้อได้ (ค่าเริ่มต้น $12 = 1% ของทุน $1,200)")
    ap.add_argument("--window-min", type=int, default=20)
    ap.add_argument("--pmax", type=float, default=0.25, help="ราคาสูงสุดที่กติกายอมซื้อ")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.25, help="เว้นจังหวะการยิง API (วินาที) ไม่ให้ชนเรต")
    a = ap.parse_args()

    rows = cohort()
    if a.offset:
        rows = rows[a.offset:]
    if a.limit:
        rows = rows[:a.limit]
    print("cohort: %d ไม้ (h=16 · p≥0.90 · ราคา 0.02–0.25) · stake $%.0f · window %d นาที" % (len(rows), a.stake, a.window_min))

    cfgs = json.load(open(W.STATIONS, encoding="utf-8"))
    out = []
    for i, r in enumerate(rows, 1):
        city, date, bn = r["city"], r["date"], r["bin"]
        tz = ZoneInfo(cfgs[city]["tz"])
        T = datetime.strptime(date + " 16:00", "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        rec = dict(city=city, date=date, bin=bn, unit=r.get("unit"), won=1 if r.get("won") == "1" else 0,
                   stale_price=float(r["price"]), model_p=float(r["model_p"]),
                   ex_px=None, ex_notional=None, n_buy=0, first_px=None, secs=None, status="")
        try:
            meta = market_meta(city, date)
        except Exception as e:
            rec["status"] = "no_market"
            out.append(rec)
            continue
        info = meta.get(bn)
        if not info or not info.get("token"):
            rec["status"] = "no_bin"
            out.append(rec)
            continue
        try:
            tr = fetch_trades(info["cond"])
        except Exception as e:
            rec["status"] = "api_error"
            out.append(rec)
            continue
        fills = yes_fills(tr, info["token"])
        ex = exec_price(fills, int(T.timestamp()), a.window_min, a.stake)
        if not ex:
            rec["status"] = "no_fill"
        else:
            rec["ex_px"] = round(ex["px"], 4)
            rec["ex_notional"] = ex["notional"]
            rec["n_buy"] = ex["n_buy"]
            rec["first_px"] = round(ex["first_px"], 4)
            rec["secs"] = ex["secs"]
            rec["status"] = "traded" if ex["px"] <= a.pmax else "too_expensive"
        out.append(rec)
        if i % 25 == 0 or i == len(rows):
            print("  ...%d/%d" % (i, len(rows)))
        time.sleep(a.sleep)

    # ── สรุป ──
    def agg(sel, px_key="ex_px"):
        s = [x for x in out if sel(x)]
        if not s:
            return dict(n=0, w=0, hit=None, roi=None)
        w = sum(x["won"] for x in s)
        pnl = sum((roi_of(x[px_key]) if x["won"] else -1.0) for x in s)
        return dict(n=len(s), w=w, hit=100.0 * w / len(s), roi=100.0 * pnl / len(s))

    traded = agg(lambda x: x["status"] == "traded")
    stale = agg(lambda x: True, px_key="stale_price")   # baseline: ใช้ราคา stale แบบเดิมทุกไม้
    by_status = {}
    for x in out:
        by_status[x["status"]] = by_status.get(x["status"], 0) + 1
    # แยกตามช่วงราคา executable — ทั้งหมดที่ซื้อได้ (traded + too_expensive) เพื่อดูความสัมพันธ์ hit↔ราคา
    def wilson(w, n):
        if not n:
            return None, None
        p = w / n
        z = 1.96
        den = 1 + z * z / n
        c = (p + z * z / (2 * n)) / den
        hw = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / den
        return 100 * (c - hw), 100 * (c + hw)

    buckets = [("≤0.10", 0, 0.10), ("0.10–0.25", 0.10, 0.25), ("0.25–0.50", 0.25, 0.50),
               ("0.50–0.75", 0.50, 0.75), (">0.75", 0.75, 1.01)]
    bt = []
    for name, lo, hi in buckets:
        g = agg(lambda x, lo=lo, hi=hi: x["status"] in ("traded", "too_expensive") and lo < x["ex_px"] <= hi)
        if g["n"]:
            g["ci"] = wilson(g["w"], g["n"])
            g["avg_px"] = round(st.mean(x["ex_px"] for x in out
                                        if x["status"] in ("traded", "too_expensive") and lo < x["ex_px"] <= hi), 3)
        bt.append((name, g))
    caps = []
    for cap in (0.25, 0.40, 0.60, 1.0):
        g = agg(lambda x, cap=cap: x["status"] in ("traded", "too_expensive") and x["ex_px"] <= cap)
        caps.append((cap, g))

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)

    L = ["# exec_backtest — ผลจริงถ้าซื้อที่ราคาที่ซื้อได้จริง (ดีลจริงจาก data-api)", "",
         "อัปเดต %s · cohort %d ไม้ (h=16 · p≥0.90 · ราคา stale 0.02–0.25) · stake $%.0f · หน้าต่าง %d นาที" % (
             datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ"), len(out), a.stake, a.window_min), "",
         "**นิยาม**: executable price = ราคาดีล BUY บน YES token ที่ทำให้ notional สะสมครบ $%.0f ภายใน %d นาทีหลัง 16:00 local" % (a.stake, a.window_min),
         "(ไม่มีดีลพอ = no-fill · ราคาเกิน %.2f = too_expensive — ทั้งคู่ = ไม่มีไม้)" % a.pmax, "",
         "| กลุ่ม | ไม้ | ชนะ | hit | ROI |", "|---|---|---|---|---|",
         "| **ซื้อได้จริง ≤ $%.2f (กติกาจริง)** | %d | %d | %s | %s |" % (
             a.pmax, traded["n"], traded["w"],
             ("%.1f%%" % traded["hit"]) if traded["hit"] is not None else "—",
             ("%+.0f%%" % traded["roi"]) if traded["roi"] is not None else "—"),
         "| baseline เดิม (ราคา stale ทุกไม้) | %d | %d | %.1f%% | %+.0f%% |" % (
             stale["n"], stale["w"], stale["hit"], stale["roi"]), "",
         "**สถานะรายไม้**", "", "| สถานะ | จำนวน |", "|---|---|"]
    for k, v in sorted(by_status.items(), key=lambda kv: -kv[1]):
        L.append("| %s | %d |" % (k, v))
    L += ["", "**ทั้งหมดที่ซื้อได้จริง (traded + ราคาเกินเพดาน) แยกตามราคาจริง — hit เทียบราคา (ตลาดถูกหรือแพงเกิน?)**", "",
          "| ช่วงราคาจริง | ไม้ | ราคาเฉลี่ย | ชนะ | hit | CI95 | ROI | ราคา vs hit |", "|---|---|---|---|---|---|---|---|"]
    for name, g in bt:
        if not g["n"]:
            L.append("| %s | 0 | — | — | — | — | — | — |" % name)
            continue
        cal = g["hit"] - 100 * g["avg_px"]
        L.append("| %s | %d | %.3f | %d | %.1f%% | %.0f–%.0f%% | %+.0f%% | %+.1f จุด |" % (
            name, g["n"], g["avg_px"], g["w"], g["hit"], g["ci"][0], g["ci"][1], g["roi"], cal))
    L += ["", "> อ่านตาราง: คอลัมน์ 'ราคา vs hit' เป็นบวก = ผลจริงชนะบ่อยกว่าราคาที่ตลาดคิด (ตลาดประเมินต่ำไป) · ลบ = ตลาดถูกกว่าเรา",
          "", "**ถ้าขยายเพดานราคาที่กล้าจ่าย**", "", "| เพดาน | ไม้ | hit | ROI |", "|---|---|---|---|"]
    for cap, g in caps:
        L.append("| ≤%.2f | %d | %s | %s |" % (cap, g["n"],
                 ("%.1f%%" % g["hit"]) if g["hit"] is not None else "—",
                 ("%+.0f%%" % g["roi"]) if g["roi"] is not None else "—"))
    L += ["", "**ตัวอย่าง 15 ไม้น่าสนใจ** (ราคา stale → ราคาจริง)", "",
          "| เมือง | วัน | bin | stale | → จริง | notional | ดีล | ผล |", "|---|---|---|---|---|---|---|---|"]
    show = [x for x in out if x["status"] in ("traded", "too_expensive")][:15]
    for x in show:
        L.append("| %s | %s | %s | %.3f | **%.3f** | $%.1f | %d | %s |" % (
            x["city"], x["date"], x["bin"], x["stale_price"], x["ex_px"], x["ex_notional"], x["n_buy"],
            "ชนะ" if x["won"] else "แพ้"))
    L += ["", "ข้อมูลดิบ: `%s` · แคชดีล: `data/exec_cache/`" % os.path.relpath(OUT_CSV, HERE), ""]
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    open(REPORT, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("เขียน %s + %s" % (OUT_CSV, REPORT))
    print("traded n=%d hit=%s ROI=%s · no_fill=%d · too_expensive=%d" % (
        traded["n"], traded["hit"], traded["roi"], by_status.get("no_fill", 0), by_status.get("too_expensive", 0)))


if __name__ == "__main__":
    main()
