#!/usr/bin/env python3
"""analyze.py — วิเคราะห์ข้อมูลที่เก็บจริง (depth_live × trades_live) แบบระวังกับดัก

บทเรียนที่ฝังไว้ในเครื่องมือนี้ (จากการตรวจข้อมูลจริง 1 ต.ค. 2026):
  1. fills ต้องดู "ไปข้างหน้า" เท่านั้น — fill ก่อนเวลาที่เราเห็น ask ไม่ใช่หลักฐานว่าซื้อได้
  2. fills ส่วนใหญ่ในตลาดกลุ่มนี้เป็น "event cluster" — ดีลหลายราคาในวินาทีเดียวกัน (span กว้าง)
     ซึ่งสมุดจริงตอนนั้นไม่มีราคาเหล่านั้น (เช่น 0.04–0.98 ทั้งที่ ask 0.994–0.999)
     → ต้องกรองเหลือเฉพาะดีลที่ "อยู่ในช่วงราคาที่สมุดเราบันทึก" (in_book) ก่อนใช้เป็นหลักฐาน
  3. ค่าที่วัดได้ = feasibility (ซื้อได้ไหม/ราคาเท่าไร) ไม่ใช่ hit rate (ต้องรอตลาดปิด)

ตอนนี้ตอบ:
  A) กิจกรรมจริงต่อ bin + ราคาดีลจริงตรงกับ ask ที่เห็นไหม (เฉพาะดีล in_book)
  B) slippage/capacity ตามช่วงราคา (จากบันได L2 เต็ม)
  C) spread/queue หัวสมุด (วัตถุดิบของข้อ ข market-making)
  D) zone กติกาเดิม (model_p≥0.90 · ask≤0.25) — เหลือกี่จังหวะ · ลึกไหม
  E) price dynamics (Δask ต่อรอบติดกัน)
  F) คุณภาพข้อมูล: สัดส่วน event cluster · in_book · joinable

รัน: python3 analyze.py [--window-min 20] [--save]
"""
import argparse
import collections
import json
import os
import statistics as st
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
DEPTH = os.path.join(DATA, "depth_live.jsonl")
TRADES = os.path.join(DATA, "trades_live.jsonl")
REPORT = os.path.join(HERE, "reports", "data_analysis.md")
JSON_OUT = os.path.join(DATA, "data_analysis.json")

TICK = 0.001
ZONES = [(0.0, 0.10), (0.10, 0.25), (0.25, 0.50), (0.50, 1.01)]
NEAR_BOOK_SEC = 600          # ใช้สมุดที่เก็บภายใน ±10 นาที ในการตัดสิน in_book


def load_jsonl(p):
    out = []
    if not os.path.exists(p):
        return out
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def zone_of(px):
    for lo, hi in ZONES:
        if lo <= px < hi:
            return "%.2f–%.2f" % (lo, hi)
    return "อื่น"


def pct(a, b):
    return round(100.0 * a / b, 1) if b else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-min", type=int, default=20, help="หน้าต่างไปข้างหน้าหา fill (นาที)")
    ap.add_argument("--stake", type=float, default=12.0)
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()
    W = a.window_min * 60

    depth = [d for d in load_jsonl(DEPTH) if d.get("token") and d.get("best_ask") is not None]
    raw_trades = load_jsonl(TRADES)
    # provenance: แถวดีลต้องมาจาก ?market=<cond> (มี asset/cond/src) — แถวเก่าแบบ asset_id (global feed)
    # ถูกถอนทิ้ง 1 ต.ค. 2026 (README §33) → ถ้ามีตกค้าง ให้ตัดออกจากทุกสถิติ
    trades = [t for t in raw_trades if t.get("asset") and t.get("cond")]
    legacy_rows = len(raw_trades) - len(trades)
    if not depth:
        sys.exit("ยังไม่มี data/depth_live.jsonl")

    # ── โครงสร้าง: fills ต่อ token + สมุดต่อ token ──
    fills_by_token = collections.defaultdict(list)
    for t in trades:
        try:
            fills_by_token[t["token"]].append((int(t["t"]), t.get("side", ""), float(t["price"]), float(t["size"])))
        except (KeyError, TypeError, ValueError):
            continue
    book_by_token = collections.defaultdict(list)          # token → [(ts, lo, hi)]
    for d in depth:
        asks = d.get("asks") or []
        if asks:
            book_by_token[d["token"]].append((int(d["ts"]), min(x[0] for x in asks), max(x[0] for x in asks)))
    for tok in book_by_token:
        book_by_token[tok].sort()

    def in_book(token, ts, px):
        """ราคาดีลอยู่ในช่วงที่สมุด (ที่เก็บใกล้สุด ±10 นาที) มีจริงไหม"""
        seq = book_by_token.get(token)
        if not seq:
            return None
        best = min(seq, key=lambda s: abs(s[0] - ts))
        if abs(best[0] - ts) > NEAR_BOOK_SEC:
            return None                                    # ไม่มีสมุดใกล้เวลา → ตัดสินไม่ได้
        return (best[1] - TICK) <= px <= (best[2] + TICK)

    now_ts = max([int(d["ts"]) for d in depth] + [f[0] for fl in fills_by_token.values() for f in fl])
    R = {}

    # ── F) คุณภาพข้อมูล (ทำก่อน แล้วใช้ผลไปกรอง A) ──
    ib_ok = ib_no = ib_unk = 0
    for t in trades:
        try:
            r = in_book(t["token"], int(t["t"]), float(t["price"]))
        except (KeyError, TypeError, ValueError):
            continue
        ib_ok += (r is True)
        ib_no += (r is False)
        ib_unk += (r is None)
    R["F_quality"] = dict(
        trade_rows=len(trades), legacy_rows_excluded=legacy_rows,
        in_book_yes=ib_ok, in_book_no=ib_no, in_book_unknown=ib_unk,
        in_book_pct=pct(ib_ok, ib_ok + ib_no),
        depth_rows=len(depth), depth_tokens=len({d["token"] for d in depth}),
        trade_tokens=len(fills_by_token),
        join_tokens=len(set(book_by_token) & set(fills_by_token)),
    )

    # ── A) กิจกรรม + ความตรงกันของราคา (ใช้เฉพาะดีล in_book · ไปข้างหน้า) ──
    def evidence(token, ts, cap_px):
        """(notional ของดีล in_book ที่ px ≤ cap_px ใน (ts, ts+W], n ดีล)"""
        tot = n = 0.0
        for f in fills_by_token.get(token, []):
            if ts < f[0] <= ts + W and f[1] == "BUY" and f[2] <= cap_px + 1e-9:
                r = in_book(token, f[0], f[2])
                if r is True:
                    tot += f[2] * f[3]
                    n += 1
        return tot, n

    rows_A, active, agree, agree_n, ev12, ev12_lad = 0, 0, 0, 0, 0, 0
    n_lad = 0
    skipped_censor = 0
    for d in depth:
        tok, ts, ask = d["token"], int(d["ts"]), d["best_ask"]
        if ts + W > now_ts:
            skipped_censor += 1
            continue
        if tok not in fills_by_token:
            continue
        rows_A += 1
        # กิจกรรม: มีดีล in_book ที่ราคาใกล้ ask (±0.05) ในหน้าต่างไหม
        near = [f for f in fills_by_token[tok]
                if ts < f[0] <= ts + W and abs(f[2] - ask) <= 0.05 and in_book(tok, f[0], f[2]) is True]
        if near:
            active += 1
            agree_n += 1
            med = st.median([f[2] for f in near])
            if abs(med - ask) <= 2 * TICK:
                agree += 1
        tot, _n = evidence(tok, ts, ask + TICK)
        if tot >= a.stake:
            ev12 += 1
        lad = d.get("slip_12")
        if lad:
            n_lad += 1
            tot2, _n2 = evidence(tok, ts, lad[0] + TICK)
            if tot2 >= a.stake:
                ev12_lad += 1
    R["A_activity"] = dict(
        sampled_rows_joinable=rows_A, skipped_censored=skipped_censor,
        active_rows=active, active_pct=pct(active, rows_A),
        near_ask_agreement_pct=pct(agree, agree_n),
        evidence_12usd_at_ask_pct=pct(ev12, rows_A),
        evidence_12usd_at_ladder_pct=pct(ev12_lad, n_lad),
        ladder_rows=n_lad,
        window_min=a.window_min, stake=a.stake,
    )

    # ── B) slippage / capacity ──
    cap = collections.defaultdict(lambda: dict(n=0, s12=0, s100=0, s500=0, dep=[], slip12=[], slip100=[]))
    for d in depth:
        ask = d["best_ask"]
        c = cap[zone_of(ask)]
        c["n"] += 1
        dep = d.get("depth_ask_usd") or 0
        c["dep"].append(dep)
        c["s12"] += dep >= 12
        c["s100"] += dep >= 100
        c["s500"] += dep >= 500
        if d.get("slip_12"):
            c["slip12"].append(d["slip_12"][0] - ask)
        if d.get("slip_100"):
            c["slip100"].append(d["slip_100"][0] - ask)
    R["B_capacity"] = {}
    for z in ["%.2f–%.2f" % zz for zz in ZONES]:
        c = cap.get(z)
        if not c or not c["n"]:
            continue
        dep = sorted(c["dep"])
        R["B_capacity"][z] = dict(
            n=c["n"],
            depth_median_usd=round(st.median(dep), 1),
            depth_p10_usd=round(dep[int(0.1 * (len(dep) - 1))], 1),
            usd12_pct=pct(c["s12"], c["n"]), usd100_pct=pct(c["s100"], c["n"]), usd500_pct=pct(c["s500"], c["n"]),
            slip12_median=round(st.median(c["slip12"]), 4) if c["slip12"] else None,
            slip100_median=round(st.median(c["slip100"]), 4) if c["slip100"] else None,
        )

    # ── C) spread + queue หัวสมุด ──
    spread, q_ask, q_bid, spread2 = [], [], [], 0
    for d in depth:
        ba, bb = d.get("best_ask"), d.get("best_bid")
        if ba is None or bb is None or ba <= bb:
            continue
        spread.append(ba - bb)
        spread2 += (ba - bb) >= 2 * TICK
        asks, bids = d.get("asks") or [], d.get("bids") or []
        if asks:
            q_ask.append(asks[0][0] * asks[0][1])
        if bids:
            q_bid.append(bids[0][0] * bids[0][1])
    R["C_mm"] = dict(
        n_two_sided=len(spread),
        spread_median=round(st.median(spread), 4) if spread else None,
        spread_p25=round(sorted(spread)[int(0.25 * (len(spread) - 1))], 4) if spread else None,
        spread_p75=round(sorted(spread)[int(0.75 * (len(spread) - 1))], 4) if spread else None,
        spread_ge2tick_pct=pct(spread2, len(spread)),
        toq_ask_median_usd=round(st.median(q_ask), 1) if q_ask else None,
        toq_bid_median_usd=round(st.median(q_bid), 1) if q_bid else None,
    )

    # ── D) zone กติกาเดิม ──
    zone_rows = [d for d in depth if (d.get("model_p") or 0) >= 0.90 and d["best_ask"] <= 0.25]
    zd = collections.Counter(d["city"] for d in zone_rows)
    R["D_zone"] = dict(
        rows=len(zone_rows), tokens=len({d["token"] for d in zone_rows}), cities=len(zd),
        top_cities=zd.most_common(8),
        median_depth_usd=round(st.median([d.get("depth_ask_usd") or 0 for d in zone_rows]), 1) if zone_rows else None,
        median_ask=round(st.median([d["best_ask"] for d in zone_rows]), 4) if zone_rows else None,
        joinable=sum(1 for d in zone_rows if d["token"] in fills_by_token),
    )

    # ── E) price dynamics ──
    by_tok = collections.defaultdict(list)
    for d in depth:
        by_tok[d["token"]].append((int(d["ts"]), d["best_ask"]))
    moves = []
    for tok, seq in by_tok.items():
        seq.sort()
        for i in range(1, len(seq)):
            if 60 <= seq[i][0] - seq[i - 1][0] <= 600:
                moves.append(seq[i][1] - seq[i - 1][1])
    nz = [m for m in moves if abs(m) > 1e-9]
    R["E_dynamics"] = dict(
        n_moves=len(moves),
        abs_move_median=round(st.median([abs(m) for m in moves]), 4) if moves else None,
        moved_pct=pct(len(nz), len(moves)),
        up_pct=pct(sum(1 for m in nz if m > 0), len(nz)),
        big_move_ge5c_pct=pct(sum(1 for m in moves if abs(m) >= 0.05), len(moves)),
    )

    # ── รายงาน ──
    F, A, C, D, E = R["F_quality"], R["A_activity"], R["C_mm"], R["D_zone"], R["E_dynamics"]
    L = ["# วิเคราะห์ข้อมูลจริง (depth × trades) — ฉบับกรองกับดักแล้ว",
         "",
         "อัปเดต %s · depth %d แถว · fills %d รายการ · หน้าต่างไปข้างหน้า +%d นาที · stake $%.0f" % (
             datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ"), len(depth), F["trade_rows"],
             a.window_min, a.stake),
         "",
         "> ⚠️ ตัวเลขนี้คือ **feasibility** — ซื้อได้ไหม/ราคาเท่าไร · ยังไม่มีผลเฉลยยุคใหม่ จึงยังไม่ใช่ hit rate",
         ""]
    L += ["## F) คุณภาพข้อมูล (อ่านก่อนใช้ทุกตัวเลข)", "",
          "- provenance: ดีลที่ใช้ = %d แถว (src=market · มี asset/cond) · ตัดแถวเก่าที่ปน global feed ออก %d แถว" % (
              F["trade_rows"], F["legacy_rows_excluded"]),
          "- ดีลที่ \"อยู่ในช่วงราคาที่สมุดบันทึก\" (in_book): %d จาก %d ที่ตัดสินได้ (%s%%) · ตัดสินไม่ได้ %d" % (
              F["in_book_yes"], F["in_book_yes"] + F["in_book_no"], F["in_book_pct"], F["in_book_unknown"]),
          "- token: depth %d · trades %d · ทับกัน %d (trades เก็บเฉพาะ bin ที่มีดีลจริงในช่วง 7 นาที)" % (
              F["depth_tokens"], F["trade_tokens"], F["join_tokens"]),
          ""]
    L += ["## A) กิจกรรมจริง + ความตรงกันของราคา (เฉพาะดีล in_book · ไม่มี look-ahead)", ""]
    if not rows_A:
        L += ["- **ยังไม่มีดีลชุดใหม่ให้วิเคราะห์** — ไฟล์ trades_live ถูกล้างเมื่อ 1 ต.ค. (แก้ asset_id bug,"
              " README §33) · จะเริ่มสะสมใหม่ในรอบเข้าไม้ถัดไป (trades_probe ใช้ `market=<cond>` แล้ว)", ""]
    L += ["- แถวที่ join ได้และหน้าต่างครบ: **%d** (ตัดแถวท้าย ๆ %d)" % (A["sampled_rows_joinable"], A["skipped_censored"]),
          "- มีดีลจริงราคาใกล้ ask (±5¢) ในหน้าต่าง +%d นาที: **%s%%** ของแถว → bin ส่วนใหญ่ **แทบไม่มีดีล**" % (
              A["window_min"], A["active_pct"]),
          "- เมื่อมีดีล: ราคาดีลตรงกับ ask ภายใน 2 ticks = **%s%%** → ask ที่เราบันทึกคือระดับที่ตลาดซื้อขายจริง" % (
              A["near_ask_agreement_pct"]),
          "- หลักฐาน \"มีดีลจริงสะสม ≥$%.0f ที่ราคา ≤ ask+1tick\": **%s%%** ของแถว · ที่ราคา ≤ ladder-VWAP+1tick: **%s%%** (n=%d)" % (
              a.stake, A["evidence_12usd_at_ask_pct"], A["evidence_12usd_at_ladder_pct"], A["ladder_rows"]),
          ""]
    L += ["## B) Slippage / capacity ตามช่วงราคา", "",
          "| ช่วง ask | n | depth มัธยฐาน | ≥$12 | ≥$100 | ≥$500 | slip $12 |", "|---|---|---|---|---|---|---|"]
    for z, c in R["B_capacity"].items():
        L.append("| %s | %d | $%.1f | %.1f%% | %.1f%% | %.1f%% | %s |" % (
            z, c["n"], c["depth_median_usd"], c["usd12_pct"], c["usd100_pct"], c["usd500_pct"],
            ("%+.4f" % c["slip12_median"]) if c["slip12_median"] is not None else "—"))
    L += ["", "## C) ข้อ ข — spread/queue หัวสมุด", "",
          "- 2 ฝั่ง %d แถว · spread มัธยฐาน **%.4f** (P25 %.4f · P75 %.4f) · ≥2 ticks = %s%%" % (
              C["n_two_sided"], C["spread_median"] or 0, C["spread_p25"] or 0, C["spread_p75"] or 0,
              C["spread_ge2tick_pct"]),
          "- queue หัวสมุด: ask $%s · bid $%s ← **บางมาก** เทียบกับ spread ที่กว้าง" % (
              C["toq_ask_median_usd"], C["toq_bid_median_usd"]),
          ""]
    L += ["## D) zone กติกาเดิม (model_p≥0.90 · ask≤0.25)", "",
          "- **%d แถว · %d token · %d เมือง** · ask มัธยฐาน %s · depth มัธยฐาน $%s · join fills ได้ %d" % (
              D["rows"], D["tokens"], D["cities"], D["median_ask"], D["median_depth_usd"], D["joinable"]),
          "- เมืองที่เจอบ่อย: %s" % ", ".join("%s(%d)" % (c, n) for c, n in D["top_cities"]),
          ""]
    L += ["## E) price dynamics", "",
          "- %d คู่ติดกัน · |Δask| มัธยฐาน %.4f · ขยับจริง %s%% (ขึ้น %s%%) · ≥5¢ = %s%%" % (
              E["n_moves"], E["abs_move_median"] or 0, E["moved_pct"], E["up_pct"], E["big_move_ge5c_pct"]),
          ""]
    L += ["## ข้อจำกัด", "",
          "1. trades ทับ depth แค่ bin ที่คึกคัก (~7–10 bin/วัน) — สถิติกิจกรรม A ใช้ได้เฉพาะกลุ่มนั้น",
          "2. in_book = เทียบกับสมุดที่เก็บใกล้สุด ±10 นาที — สมุดเคลื่อนระหว่างนั้นได้ (ยอมรับความคลาดเคลื่อนนี้)",
          "3. ยังไม่มีผลเฉลยของยุคใหม่ → ตอบได้แค่ \"ซื้อได้ไหม/ราคาเท่าไร\"",
          "4. ⚠️ ไฟล์ fills เก่า (ถึง 1 ต.ค.) ถูกถอนทั้งชุดเพราะ asset_id bug (README §33) — เครื่องมือที่อิง fills"
          " ต้องอ่านเฉพาะแถว provenance ใหม่ (บังคับในโค้ดแล้ว) · exec_backtest/market_replay ไม่กระทบ (ใช้ market=cond ตั้งแต่แรก)", ""]
    out = "\n".join(L)
    print(out)
    if a.save:
        os.makedirs(os.path.dirname(REPORT), exist_ok=True)
        open(REPORT, "w", encoding="utf-8").write(out)
        with open(JSON_OUT, "w", encoding="utf-8") as f:
            json.dump(R, f, ensure_ascii=False, indent=1)
        print("เขียน %s + %s" % (REPORT, JSON_OUT))


if __name__ == "__main__":
    main()
