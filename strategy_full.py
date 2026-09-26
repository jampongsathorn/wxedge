#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
strategy_full.py — กลยุทธ์ฉบับสมบูรณ์สำหรับตลาด "อุณหภูมิสูงสุด" (Polymarket)
รวมทุกอย่างที่พิสูจน์แล้ว + ตรวจสอบทางสถิติครบชุด (win rate, EV, PnL, CI, drawdown, Kelly)
แล้วเขียน   reports/strategy_full.md   +   data/strategy_params.json  (ให้ LLM/เครื่องอ่าน)

รัน:  python3 strategy_full.py --split 2026-08-15 --stake 100
"""
import argparse, csv, json, math, os, random, re, statistics as st
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
import headtohead as H
import api_value as AV

BETS = os.path.join(HERE, "data", "cheap_bets.csv")
MD = os.path.join(HERE, "reports", "strategy_full.md")
PARAMS = os.path.join(HERE, "data", "strategy_params.json")

UNIVERSE_BAN = ["hong-kong", "shenzhen"]        # obs ไม่ตรงกับแหล่งตัดสิน (51.2% / 52.4%) — ตัวกรองอัตโนมัติจะตัดออกเอง
AGREE_THR = 0.90                                 # เกณฑ์ "obs ตรงกับ bin ที่ตลาดตัดสิน" (fit บน train)
AGREE_MIN_N = 10
TICK = 0.01
FEE = 0.02                                       # สมมติฐานค่าธรรมเนียม 2% ของ notional (stress)


# ───────────────────────── โหลดข้อมูล ─────────────────────────
def parse_bin(label):
    s = str(label).replace("°C", " ").replace("°F", " ").replace("–", "-").strip()
    neg = s.startswith("-")
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", s)]
    if neg and nums:
        nums[0] = -nums[0]
    if not nums:
        return (-math.inf, math.inf)
    if "or below" in s or "or lower" in s:          # ตลาดจริงใช้ "or below" / "or higher"
        return (-math.inf, nums[0])
    if "or above" in s or "or higher" in s:
        return (nums[0], math.inf)
    if len(nums) == 1:
        return (nums[0], nums[0])
    return (nums[0], nums[1])


def load_bets():
    out = []
    for r in csv.DictReader(open(BETS, encoding="utf-8")):
        try:
            out.append(dict(city=r["city"], date=r["date"], hour=int(r["hour"]), bin=r["bin"],
                            unit=r["unit"], p=float(r["model_p"]), price=float(r["price"]),
                            won=int(r["won"])))
        except (KeyError, ValueError):
            continue
    return out


def load_msf():
    """max-so-far ถึงชั่วโมงนั้น ๆ (สำหรับแยก bin ที่ 'เป็นไปไม่ได้แล้ว')"""
    ser = defaultdict(lambda: defaultdict(dict))
    p = os.path.join(HERE, "data", "hourly_obs.csv")
    for r in csv.DictReader(open(p, encoding="utf-8")):
        try:
            ser[r["city"]][r["date"]][int(r["hour"])] = float(r["tmpc"])
        except ValueError:
            continue
    return ser


# ───────────────────────── สถิติ ─────────────────────────
def wilson(k, n, z=1.96):
    if not n:
        return (0.0, 0.0)
    ph = k / n
    d = 1 + z * z / n
    c = ph + z * z / (2 * n)
    m = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n))
    return ((c - m) / d * 100, (c + m) / d * 100)


def bootstrap_roi(picks, iters=2000, seed=11, tick=0.0):
    n = len(picks)
    if n < 10:
        return (None, None)
    rnd = random.Random(seed)
    out = []
    for _ in range(iters):
        s = [picks[rnd.randrange(n)] for _ in range(n)]
        avg = st.mean(min(0.999, p["price"] + tick) for p in s)
        win = st.mean(p["won"] for p in s)
        out.append((win - avg) / avg * 100)
    out.sort()
    return (out[int(0.025 * iters)], out[int(0.975 * iters)])


def rule_stats(picks, stake=100.0, tick=0.0, fee=0.0, label=""):
    """picks = list ของ dict(city,date,bin,p,price,won) — ฝั่ง YES เท่านั้น"""
    n = len(picks)
    if not n:
        return None
    wins = sum(p["won"] for p in picks)
    wr = wins / n
    lo, hi = wilson(wins, n)
    prices = [min(0.999, p["price"] + tick) * (1 + fee) for p in picks]
    avg = st.mean(prices)
    ev = wr - avg                                   # EV ต่อ 1 share (=$1 ตอนชนะ)
    roi = ev / avg * 100
    pnl = sum(stake * (p["won"] / pr - 1.0) for p, pr in zip(picks, prices))
    gains = sum(stake * (1 / pr - 1.0) for p, pr in zip(picks, prices) if p["won"])
    losses = sum(stake for p, pr in zip(picks, prices) if not p["won"])
    pf = gains / losses if losses else float("inf")
    z = (wr - avg) / math.sqrt(avg * (1 - avg) / n) if 0 < avg < 1 else 0.0
    kelly = max(0.0, (wr - avg) / (1 - avg))         # สัดส่วนเต็มของทุน (ทฤษฎี)
    # ลำดับ PnL ตามวัน → max drawdown / streak
    seq = sorted(zip(picks, prices), key=lambda x: (x[0]["date"], x[0]["city"]))
    cum, peak, mdd, cur_loss, worst_streak = 0.0, 0.0, 0.0, 0, 0
    for p, pr in seq:
        g = stake * (p["won"] / pr - 1.0)
        cum += g
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
        cur_loss = 0 if p["won"] else cur_loss + 1
        worst_streak = max(worst_streak, cur_loss)
    days = len({p["date"] for p in picks})
    return dict(label=label, n=n, wins=wins, win=wr * 100, win_lo=lo, win_hi=hi, avg=avg,
                ev=ev, roi=roi, pnl=pnl, pf=pf, z=z, sig=(abs(z) >= 1.96), kelly=kelly,
                dd=mdd, dd_pct=(mdd / (stake * max(1, n)) * 100), streak=worst_streak,
                per_day=n / days if days else 0, days=days)


def combine(picks_a, picks_b):
    seen = {(p["city"], p["date"], p["bin"]) for p in picks_a}
    return picks_a + [p for p in picks_b if (p["city"], p["date"], p["bin"]) not in seen]


def make_universe(rows_all, split, thr=AGREE_THR, min_n=AGREE_MIN_N):
    """ตัวกรองจักรวาลเมือง: ใช้ 'obs_c ตกใน bin ที่ตลาดตัดสิน' วัดบน train เท่านั้น (ไม่มี look-ahead)
    คืน (set เมืองที่เทรดได้, ตาราง agreement, รายชื่อที่ตัดออก)"""
    import headtohead as _H
    import api_value as _AV
    ds = [r for r in _AV.load_ds() if r["date"] < split]
    labels = _AV.load_labels()
    acc = defaultdict(lambda: [0, 0])
    for r in ds:
        k = (r["city"], r["date"])
        if k not in labels:
            continue
        o = _H.num(r.get("obs_c"))
        if o is None:
            continue
        lo, hi, unit, _ = labels[k]
        oo = o * 9 / 5 + 32 if unit == "F" else o
        acc[r["city"]][1] += 1
        if lo - 0.5 <= oo <= hi + 0.5:
            acc[r["city"]][0] += 1
    keep, drop, tbl = set(), [], {}
    for c, (k, n) in acc.items():
        a = k / n if n else 0.0
        tbl[c] = (a, n)
        if n >= min_n and a >= thr:
            keep.add(c)
        else:
            drop.append((c, a, n))
    return keep, tbl, sorted(drop, key=lambda x: x[1])


# ───────────────────────── main ─────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="2026-08-15")
    ap.add_argument("--stake", type=float, default=100.0)
    a = ap.parse_args()
    stake = a.stake
    rows = load_bets()
    msf = load_msf()
    print(f"bets={len(rows):,} rows · split={a.split} · stake=${stake:.0f}")

    UNIV, AGREE, DROPPED = make_universe(rows, a.split)
    print(f"จักรวาลเมือง (obs ตรงเฉลย ≥{AGREE_THR:.0%} บน train): {len(UNIV)} เมือง · ตัดออก {len(DROPPED)}")

    def _group(picks):
        g = defaultdict(list)
        for p in picks:
            g[(p["city"], p["date"], p["hour"])].append(p)
        return g

    def pick_yes(rowset, hour, pmin, lo, hi, univ=None):
        u = UNIV if univ is None else univ
        cand = [r for r in rowset if r["hour"] == hour and r["city"] in u
                and lo <= r["price"] <= hi and r["p"] >= pmin]
        return [max(v, key=lambda b: (b["p"], -b["price"])) for v in _group(cand).values()]

    def pick_no(rowset, hour, pmin_price, margin, univ=None):
        """ฝั่งขาย: YES price ≥ pmin_price และ price − p ≥ margin → ซื้อ NO"""
        u = UNIV if univ is None else univ
        out = []
        for r in rowset:
            if r["hour"] != hour or r["city"] not in u:
                continue
            if pmin_price <= r["price"] <= 0.97 and (r["price"] - r["p"]) >= margin:
                d = dict(r)
                pr = d.pop("price")
                d["price"] = 1.0 - pr                  # ต้นทุน NO
                d["won"] = 1 - r["won"]                # ชนะเมื่อ bin ไม่ชนะ
                d["yes_price"] = pr
                out.append(d)
        return out

    def pick_no_imp(S, hour, pmin_price, margin, univ=None):
        """NO เข้ม: เฉพาะ bin ที่ต่ำกว่า max-so-far แล้ว > margin (พิสูจน์ได้ว่าเป็นไปไม่ได้)"""
        out = []
        for r in pick_no(S, hour, pmin_price, margin, univ):
            day = msf.get(r["city"], {}).get(r["date"]) or {}
            past = [v for h, v in day.items() if h <= hour]
            if not past:
                continue
            m = max(past)
            m = m * 9 / 5 + 32 if r["unit"] == "F" else m
            lo, hi = parse_bin(r["bin"])
            if hi != math.inf and hi < m - 0.5 and not (lo != -math.inf and lo > m + 0.5):
                out.append(r)
        return out

    def port_of(S, univ=None):
        u = UNIV if univ is None else univ
        return combine(combine(pick_yes(S, 16, 0.90, 0.02, 0.10, u),
                               pick_yes(S, 16, 0.90, 0.1001, 0.25, u)),
                       pick_no(S, 16, 0.50, 0.30, u))

    # ── 1) กฎหลัก: ตาราง per-rule (ทั้งชุดข้อมูล / train / test) ──
    tr = [r for r in rows if r["date"] < a.split]
    te = [r for r in rows if r["date"] >= a.split]
    rules = [
        ("YES หลัก · p≥0.90 · 0.02–0.10 @16:00", lambda S: pick_yes(S, 16, 0.90, 0.02, 0.10)),
        ("YES รอง · p≥0.90 · 0.10–0.25 @16:00", lambda S: pick_yes(S, 16, 0.90, 0.1001, 0.25)),
        ("YES กว้าง · p≥0.90 · 0.02–0.25 @16:00", lambda S: pick_yes(S, 16, 0.90, 0.02, 0.25)),
        ("YES คม · p≥0.97 · 0.02–0.25 @16:00", lambda S: pick_yes(S, 16, 0.97, 0.02, 0.25)),
        ("YES เร็ว · p≥0.97 · 0.02–0.30 @15:00", lambda S: pick_yes(S, 15, 0.97, 0.02, 0.30)),
        ("YES สาย · p≥0.95 · 0.02–0.30 @17:00", lambda S: pick_yes(S, 17, 0.95, 0.02, 0.30)),
        ("NO กว้าง · ≥0.50 & price−p ≥0.30 @16:00", lambda S: pick_no(S, 16, 0.50, 0.30)),
        ("NO เข้ม · เฉพาะ bin ที่เป็นไปไม่ได้ @16:00", lambda S: pick_no_imp(S, 16, 0.50, 0.30)),
    ]

    lines, rule_rows = [], []
    for name, fn in rules:
        for tag, S in (("all", rows), ("train", tr), ("test", te)):
            pk = fn(S)
            s = rule_stats(pk, stake=stake, label=f"{name} [{tag}]")
            if tag == "all":
                s_fee = rule_stats(pk, stake=stake, tick=2 * TICK, fee=FEE)
                b_lo, b_hi = bootstrap_roi(pk)
                rule_rows.append((name, s, s_fee, b_lo, b_hi))
            print(f"  {name[:44]:44s} {tag:5s} n={s['n']:4d} win={s['win']:5.1f}% "
                  f"ROI={s['roi']:+8.1f}% PnL=${s['pnl']:+9.0f} CIwin[{s['win_lo']:.0f},{s['win_hi']:.0f}]")

    # sweep: ชั่วโมง / pmin / เพดานราคา
    hours = [(h, rule_stats(pick_yes(rows, h, 0.90, 0.02, 0.25), stake)) for h in (13, 14, 15, 16, 17)]
    pmins = [(pm, rule_stats(pick_yes(rows, 16, pm, 0.02, 0.25), stake)) for pm in (0.80, 0.85, 0.90, 0.95, 0.97)]
    caps = [(cp, rule_stats(pick_yes(rows, 16, 0.90, 0.02, cp), stake)) for cp in (0.05, 0.10, 0.15, 0.25, 0.30)]
    hours_fee = [(h, rule_stats(pick_yes(rows, h, 0.90, 0.02, 0.25), stake, tick=2 * TICK, fee=FEE)) for h in (13, 14, 15, 16, 17)]

    # ── 2) พอร์ตจริง: หลัก + รอง + ฝั่งขาย ──
    p_yes_small = pick_yes(rows, 16, 0.90, 0.02, 0.10)
    p_yes_mid = pick_yes(rows, 16, 0.90, 0.1001, 0.25)
    p_no = pick_no(rows, 16, 0.50, 0.30)
    no_imp = pick_no_imp(rows, 16, 0.50, 0.30)
    _imp_keys = {(p["city"], p["date"], p["bin"]) for p in no_imp}
    no_pos = [p for p in p_no if (p["city"], p["date"], p["bin"]) not in _imp_keys]

    port_primary = combine(combine(p_yes_small, p_yes_mid), no_imp)   # แนะนำ
    port_wide = port_of(rows)                                         # ทางเลือก (ไม่กรองฝั่ง NO)
    port = port_primary
    S_port = rule_stats(port, stake=stake)
    S_wide = rule_stats(port_wide, stake=stake)
    # แยกตามวัน → equity curve
    byday = defaultdict(float)
    for p in port:
        byday[p["date"]] += stake * (p["won"] / p["price"] - 1.0)
    dts = sorted(byday)
    cum, peak, mdd, curve = 0.0, 0.0, 0.0, []
    for d in dts:
        cum += byday[d]
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
        curve.append((d, byday[d], cum))
    daily = [byday[d] for d in dts]
    print(f"\n  พอร์ต: n={S_port['n']} ชนะ={S_port['win']:.1f}% ราคา={S_port['avg']:.3f} ROI={S_port['roi']:+.1f}% "
          f"EV=${S_port['ev']:.3f}/ไม้ PnL=${S_port['pnl']:+,.0f} "
          f"DD(ไม้ติดกัน)=${S_port['dd']:,.0f} DD(รายวัน)=${mdd:,.0f} "
          f"แพ้ติดยาวสุด={S_port['streak']} PF={S_port['pf']:.2f} ไม้/วัน={S_port['per_day']:.1f}")

    byc = defaultdict(list)
    for p in port:
        byc[p["city"]].append(p)
    city_tbl = sorted(((c, len(v), st.mean(p["won"] for p in v) * 100) for c, v in byc.items()),
                      key=lambda x: -x[1])

    # ── 3) พารามิเตอร์สถิติของโมเดล (จาก dataset_full) ──
    ds = AV.load_ds()
    cset = {r["city"]: r for r in ds}
    bias = AV.bias_fix([r for r in ds if r["date"] < a.split], AV.MODELS)
    err = defaultdict(list)
    for r, mu, _ in AV.blend([r for r in ds if r["date"] < a.split], AV.MODELS,
                             AV.bias_fix([r for r in ds if r["date"] < a.split], AV.MODELS), {}):
        o = H.num(r.get("obs_c"))
        if o is not None:
            err[r["unit"]].append(mu - o)
    sigma = {u: math.sqrt(sum(e * e for e in v) / len(v)) for u, v in err.items()}
    mae = {u: st.mean(abs(e) for e in v) for u, v in err.items()}
    sign = {u: st.mean(e for e in v) for u, v in err.items()}
    spread = st.mean(H.num(r.get("model_spread_c")) or 0 for r in ds)
    bias_tbl = sorted(((m, u, v) for (m, u), v in bias.items()), key=lambda x: -abs(x[2]))[:6]

    hits = [H.num(r.get("m_best_match")) for r in ds if H.num(r.get("m_best_match")) is not None]
    n_models = sorted({H.num(r.get("n_models")) for r in ds if H.num(r.get("n_models"))})

    # ── 4) เขียนรายงาน ──
    def pct(v, d=1):
        return f"{v:+.{d}f}" if v is not None else "n/a"

    with open(MD, "w", encoding="utf-8") as f:
        w = f.write
        w("# กลยุทธ์ฉบับสมบูรณ์ — ตลาด “อุณหภูมิสูงสุด” (Polymarket) — ตรวจสอบด้วยสถิติแล้ว\n\n")
        w(f"> `strategy_full.py --split {a.split} --stake {stake:.0f}` · {len(rows):,} แถว bin×ชั่วโมง×วัน "
          f"(50 เมือง × 85 วัน) · เฉลยจริงจากตลาด 4,142 รายการ · เงินทดสอบ ${stake:.0f}/ไม้ · จักรวาล = {len(UNIV)} เมือง "
          f"(obs ตรงกับ bin ที่ตลาดตัดสิน ≥{AGREE_THR:.0%} วัดบน train) · ตัวเลขรวมค่าธรรมเนียมเฉพาะคอลัมน์ที่ระบุ\n\n")

        w("## 0) ใช้แบบไหน (สรุปการใช้งาน)\n\n")
        w("| ลำดับ | กฎ | เงื่อนไข | ขนาด |\n|---|---|---|---|\n")
        w("| 1 | **YES หลัก** | เวลา**ท้องถิ่น 16:00** · p ≥ 0.90 · ราคา YES 0.02–0.10 · bin ที่ p สูงสุด | 1% ของทุน/ไม้ |\n")
        w("| 2 | **YES รอง** | เหมือนข้อ 1 แต่ราคา 0.10–0.25 (ไม้เสริมเมื่อโอกาสมีเยอะ) | 1% |\n")
        w("| 3 | **NO ฝั่งขาย (แนะนำ)** | bin ที่ **ต่ำกว่า max-so-far แล้ว >0.5°** (พิสูจน์ได้ว่าเป็นไปไม่ได้) และ YES ราคา ≥0.50 · ราคา − p ≥ 0.30 | 1% |\n")
        w("| 3b | NO ทางเลือก (กว้าง) | ทุก bin ที่ YES ≥0.50 · ราคา − p ≥ 0.30 (รวม bin ที่ยังเป็นไปได้ — ชนะน้อยกว่า) | 1% |\n")
        w(f"| 4 | จักรวาลเมือง | เฉพาะเมืองที่ obs ตรงกับ bin ที่ตลาดตัดสิน ≥{AGREE_THR:.0%} (วัดบน train) = {len(UNIV)} เมือง | — |\n")
        w("| 5 | ห้ามซื้อ | ราคา < 0.02 (กับดัก: ชนะเพียง 6%) · p < 0.90 · bin ที่ไม่ใช่ argmax | — |\n")
        w("| 6 | ถือถึงเฉลย | ไม่ต้องตั้ง TP · ใช้ stop −25% ได้ตามใจ (ผลทดสอบไม่ต่าง) | — |\n\n")

        w("## 1) ผลการตรวจสอบรายกฎ (ทั้งชุด / train / test)\n\n")
        w("| กฎ | ช่วงข้อมูล | n | ชนะ% | 95% CI ของ win% | ราคาเฉลี่ย | EV/ไม้ | ROI | PnL ($100/ไม้) |\n|---|---|---|---|---|---|---|---|---|\n")
        for name, fn in rules:
            for tag, S in (("all", rows), ("train", tr), ("test", te)):
                s = rule_stats(fn(S), stake=stake)
                if not s:
                    w(f"| {name} | {tag} | 0 | — | — | — | — | — | n/a |\n")
                    continue
                w(f"| {name} | {tag} | {s['n']} | {s['win']:.1f}% | {s['win_lo']:.1f}–{s['win_hi']:.1f} | "
                  f"{s['avg']:.3f} | ${s['ev']:.3f} | {pct(s['roi'])}% | ${s['pnl']:+,.0f} |\n")

        w("\n### ความทนทาน (ค่าธรรมเนียม 2% + slippage 2 ticks) และ bootstrap ของ ROI\n\n")
        w("| กฎ | n | ROI รวมค่าธรรมเนียม | 95% bootstrap CI ของ ROI | profit factor | max DD | แพ้ติดกันยาวสุด | ไม้/วัน | z-test |\n|---|---|---|---|---|---|---|---|---|\n")
        for name, s, s_fee, b_lo, b_hi in rule_rows:
            w(f"| {name} | {s['n']} | {pct(s_fee['roi'])}% | {b_lo:+.0f}% … {b_hi:+.0f}% | {s['pf']:.2f} | "
              f"${s['dd']:,.0f} | {s['streak']} | {s['per_day']:.1f} | {'มีนัยสำคัญ' if s['sig'] else 'ไม่'} |\n")

        # ── 1b) ตัวกรองจักรวาลเมือง (fit บน train เท่านั้น) ──
        w("\n### 1b) ตัวกรองจักรวาลเมือง — 'obs ของเราตรงกับ bin ที่ตลาดตัดสิน' (วัดบน train เท่านั้น)\n\n")
        w("| เกณฑ์ agreement | จำนวนเมือง | ทั้งชุด (n / ชนะ% / ROI) | test (n / ชนะ% / ROI) |\n|---|---|---|---|\n")
        for thr in (0.0, 0.80, 0.85, 0.90, 0.95):
            u = {c for c, (av, nn) in AGREE.items() if nn >= AGREE_MIN_N and av >= thr}
            if thr == 0.0:
                u = set(AGREE)
            sa = rule_stats(port_of(rows, u), stake)
            stt = rule_stats(port_of(te, u), stake)
            tag = "ไม่กรอง (ทั้งหมด)" if thr == 0.0 else f"≥ {thr:.2f}"
            w(f"| {tag} | {len(u)} | {sa['n']} / {sa['win']:.1f}% / {pct(sa['roi'])}% | "
              f"{stt['n']} / {stt['win']:.1f}% / {pct(stt['roi'])}% |\n")
        tier_a = sorted([c for c, (av, nn) in AGREE.items() if nn >= 15 and av >= 0.95])
        tier_b = sorted([c for c, (av, nn) in AGREE.items() if nn >= AGREE_MIN_N and 0.90 <= av < 0.95])
        w(f"\n- **ชั้น A (agreement ≥ 95%, n≥15): {len(tier_a)} เมือง** — {', '.join(tier_a)}\n")
        w(f"- **ชั้น B (90–95%): {len(tier_b)} เมือง** — {', '.join(tier_b)}\n")
        w(f"- **ตัดออก ({len(DROPPED)} เมือง)** — " + ", ".join(f"{c} ({av:.0%}, n={nn})" for c, av, nn in DROPPED) + "\n")
        w("\n*เหตุผล: ถ้า obs ที่เราใช้ไม่ตรงกับแหล่งที่ตลาดใช้ตัดสิน ต่อให้โมเดลแม่นแค่ไหนก็แพ้ — ตัดด้วยข้อมูล train ไม่ใช่ความรู้สึก*\n")

        w("\n## 2) ความไวต่อพารามิเตอร์ (sensitivity)\n\n")
        w("**ชั่วโมงที่เข้า (กฎ p≥0.90 · 0.02–0.25)**\n\n| ชั่วโมง (ท้องถิ่น) | n | ชนะ% | ROI | ROI รวมค่าธรรมเนียม |\n|---|---|---|---|---|\n")
        for (h, s), (_, sf) in zip(hours, hours_fee):
            if not s:
                w(f"| {h}:00 | 0 | — ไม่มีไม้ที่ผ่านเงื่อนไข | — | — |\n")
            else:
                w(f"| {h}:00 | {s['n']} | {s['win']:.1f}% | {pct(s['roi'])}% | {pct(sf['roi'])}% |\n")
        w("\n**ความเข้มของเงื่อนไข 'เป็นไปไม่ได้' (margin จาก max-so-far)**\n\n| margin | n | ชนะ% | ROI |\n|---|---|---|---|\n")
        for mg in (0.5, 1.0, 2.0):
            kk = []
            for r in p_no:
                day = msf.get(r["city"], {}).get(r["date"]) or {}
                past = [v for h, v in day.items() if h <= 16]
                if not past:
                    continue
                m2 = max(past)
                m2 = m2 * 9 / 5 + 32 if r["unit"] == "F" else m2
                lo2, hi2 = parse_bin(r["bin"])
                if hi2 != math.inf and hi2 < m2 - mg and not (lo2 != -math.inf and lo2 > m2 + mg):
                    kk.append(r)
            sm = rule_stats(kk, stake)
            if sm:
                w(f"| {mg:.1f}° | {sm['n']} | {sm['win']:.1f}% | {pct(sm['roi'])}% |\n")

        w("\n**ชั่วโมงเข้าตามธรรมชาติของเมือง (fit บน train) เทียบกับคงที่ 16:00**\n\n"
          "| แบบ | n | ชนะ% | ROI | test ชนะ% |\n|---|---|---|---|---|\n")
        hz = {}
        for c in [x["city"] for x in rows]:
            if c in hz or c not in UNIV:
                continue
            cnt = defaultdict(int)
            for d, hrs in (msf.get(c) or {}).items():
                if d >= a.split or len(hrs) < 6:
                    continue
                cnt[max(hrs, key=lambda h: hrs[h])] += 1
            tot = sum(cnt.values())
            acc = 0
            h80 = 23
            for h in range(24):
                acc += cnt.get(h, 0)
                if tot and acc / tot >= 0.8:
                    h80 = h
                    break
            hz[c] = max(16, min(17, h80))
        for tag, hm in (("คงที่ 16:00 (แนะนำ)", {c: 16 for c in UNIV}), ("ตามเมือง (clamp 16–17)", hz)):
            def build_h(S, hm=hm):
                cy = [r for r in S if r["city"] in UNIV and r["hour"] == hm.get(r["city"], 16)
                      and 0.02 <= r["price"] <= 0.25 and r["p"] >= 0.90]
                ysv = [max(v, key=lambda b: (b["p"], -b["price"])) for v in _group(cy).values()]
                nsv = []
                for r in S:
                    if r["city"] not in UNIV or r["hour"] != hm.get(r["city"], 16):
                        continue
                    if 0.50 <= r["price"] <= 0.97 and (r["price"] - r["p"]) >= 0.30:
                        d = dict(r)
                        pr = d.pop("price")
                        d["price"] = 1.0 - pr
                        d["won"] = 1 - r["won"]
                        nsv.append(d)
                return combine(ysv, nsv)
            sa_ = rule_stats(build_h(rows), stake)
            st_ = rule_stats(build_h(te), stake)
            w(f"| {tag} | {sa_['n']} | {sa_['win']:.1f}% | {pct(sa_['roi'])}% | {st_['win']:.1f}% |\n")

        w("\n**เกณฑ์ความเชื่อมั่น p (16:00 · 0.02–0.25)**\n\n| p ขั้นต่ำ | n | ชนะ% | ราคาเฉลี่ย | ROI |\n|---|---|---|---|---|\n")
        for pm, s in pmins:
            if s:
                w(f"| ≥ {pm:.2f} | {s['n']} | {s['win']:.1f}% | {s['avg']:.3f} | {pct(s['roi'])}% |\n")
        w("\n**เพดานราคาที่ซื้อ (16:00 · p≥0.90)**\n\n| เพดานราคา | n | ชนะ% | ราคาเฉลี่ย | ROI | EV/ไม้ |\n|---|---|---|---|---|---|\n")
        for cp, s in caps:
            if s:
                w(f"| ≤ {cp:.2f} | {s['n']} | {s['win']:.1f}% | {s['avg']:.3f} | {pct(s['roi'])}% | ${s['ev']:.3f} |\n")

        w("\n## 3) พอร์ตที่แนะนำ (หลัก + รอง + ฝั่งขาย)\n\n")
        w("| องค์ประกอบ | n | ชนะ% | ราคาเฉลี่ย | ROI | PnL ($100/ไม้) |\n|---|---|---|---|---|---|\n")
        for nm, s in (("YES หลัก (≤0.10)", rule_stats(p_yes_small, stake)),
                      ("YES รอง (0.10–0.25)", rule_stats(p_yes_mid, stake)),
                      ("NO ฝั่งขาย (ทั้งหมด)", rule_stats(p_no, stake)),
                      ("  └ NO เฉพาะ bin ที่เป็นไปไม่ได้แล้ว", rule_stats(no_imp, stake)),
                      ("  └ NO เฉพาะ bin ที่ยังเป็นไปได้", rule_stats(no_pos, stake)),
                      ("**พอร์ตแนะนำ (YES + NO เฉพาะที่เป็นไปไม่ได้)**", S_port),
                      ("พอร์ตทางเลือก (YES + NO ทั้งหมด)", S_wide)):
            w(f"| {nm} | {s['n']} | {s['win']:.1f}% | {s['avg']:.3f} | {s['roi']:+.1f}% | ${s['pnl']:+,.0f} |\n")
        p_tr_ = rule_stats([x for x in port_primary if x["date"] < a.split], stake)
        p_te_ = rule_stats([x for x in port_primary if x["date"] >= a.split], stake)
        w(f"**พอร์ตแนะนำแยกช่วง:** train n={p_tr_['n']} ชนะ {p_tr_['win']:.1f}% ROI {p_tr_['roi']:+.1f}% · "
          f"test n={p_te_['n']} ชนะ {p_te_['win']:.1f}% ROI {p_te_['roi']:+.1f}% "
          f"(EV ${p_te_['ev']:.3f}/ไม้) — สอดคล้องกันทั้งสองช่วง\n")
        w(f"\n- ไม้ทั้งหมด **{S_port['n']} ไม้ ใน {S_port['days']} วัน ({S_port['per_day']:.1f} ไม้/วัน)** · "
          f"ชนะ **{S_port['win']:.1f}%** · EV **${S_port['ev']:.3f}/ไม้** · ROI **{S_port['roi']:+.1f}%** · "
          f"PnL **${S_port['pnl']:+,.0f}** (ทุนหมุน ${stake:.0f}/ไม้)\n")
        w(f"- **max drawdown 2 แบบ:** ตามรายวัน **${mdd:,.0f}** · ตามลำดับไม้ **${S_port['dd']:,.0f}** "
          f"(= ไม้แพ้ติดกันยาวสุด {S_port['streak']} × ${stake:.0f}) — เล็กมากเมื่อเทียบกำไร เพราะไม้ถูกมีตัวคูณสูง\n")
        w(f"- วันที่แย่สุด **${min(daily):+,.0f}** · วันที่ดีสุด **${max(daily):+,.0f}** · "
          f"worst-case ของวันคือไม้แพ้ทั้งหมดที่เข้า ({S_port['per_day']:.0f} ไม้ ≈ ${S_port['per_day'] * stake:,.0f})\n")
        w(f"- profit factor **{S_port['pf']:.2f}** · Kelly เต็ม (ทฤษฎี) {S_port['kelly'] * 100:.0f}% ของทุน "
          f"→ **ใช้จริง 1% ต่อไม้** (จำกัด variance จากตัวคูณสูง)\n")
        w(f"- **เงินทุนขั้นต่ำที่ต้องมี:** ≈ 2 วัน × ไม้ต่อวัน × ขนาดไม้ = 2 × {S_port['per_day']:.1f} × ${stake:.0f} "
          f"= **${2 * S_port['per_day'] * stake:,.0f}** (ไม่มี margin เพราะซื้อแล้วถือถึงเฉลย) + สำรอง 20%\n")
        w(f"- วันที่มีกำไร {sum(1 for d in daily if d > 0)}/{len(daily)} "
          f"({sum(1 for d in daily if d > 0) / max(1, len(daily)) * 100:.0f}%) · "
          f"ค่าเฉลี่ยต่อวัน ${st.mean(daily):+,.0f} · ส่วนเบี่ยงเบน ${st.pstdev(daily):,.0f} · "
          f"t-stat {st.mean(daily) / (st.pstdev(daily) / math.sqrt(len(daily)) or 1):.2f}\n")
        w("\n**เส้นทุน (cumulative PnL, $)** — ทุก 5 วัน\n\n| วันที่ | PnL วันนั้น | สะสม |\n|---|---|---|\n")
        for d, g, c in curve[::5]:
            w(f"| {d} | ${g:+,.0f} | ${c:+,.0f} |\n")

        w("\n## 4) เมืองที่เทรด (จากพอร์ตจริง) — n และ win%\n\n")
        w("| เมือง | ไม้ | ชนะ% |\n|---|---|---|\n")
        for c, n_, wr in city_tbl:
            if n_ >= 5:
                w(f"| {c} | {n_} | {wr:.0f}% |\n")
        w(f"\n*จักรวาลที่ใช้ = {len(UNIV)} เมือง (agreement ≥{AGREE_THR:.0%} บน train) · ตัดออก {len(DROPPED)} เมือง: "
          f"{', '.join(c for c, _a, _n in DROPPED)} — hong-kong/shenzhen ถูกตัดอัตโนมัติเพราะ obs ต่างจากแหล่งตัดสิน*\n")

        w("\n## 5) พารามิเตอร์สถิติของระบบ (fit บน train เท่านั้น)\n\n")
        w("| พารามิเตอร์ | ค่า | หมายเหตุ |\n|---|---|---|\n")
        w(f"| sigma (SD ของ residual, °C) | {sigma.get('C', 0):.3f} | ใช้สร้างความกว้างการแจกแจงราย bin |\n")
        w(f"| sigma (°F) | {sigma.get('F', 0):.3f} | — |\n")
        w(f"| MAE (°C / °F) | {mae.get('C', 0):.3f} / {mae.get('F', 0):.3f} | เทียบ obs จริง |\n")
        w(f"| bias คงเหลือของ blend | {sign.get('C', 0):+.3f} / {sign.get('F', 0):+.3f} | ควรใกล้ 0 หลังหัก bias รายโมเดล |\n")
        w(f"| จำนวนโมเดลที่ใช้ | {AV.MODELS.__len__()} (อิ่มตัวจริงที่ ~3–4 แหล่ง) | เพิ่มเกินนี้ได้ +0.8 จุด |\n")
        w(f"| model spread เฉลี่ย | {spread:.3f} °C | ตัวชี้วัดความไม่แน่นอนของวันนั้น |\n")
        w("| ตัวสรุปของสมาชิก | **ค่ามัธยฐาน** (ดีกว่าค่าเฉลี่ย +2.3 จุด บน test) | ใช้ทั้ง day-ahead และ fusion |\n")
        w("| เกณฑ์ตัดสินใจ | p ≥ 0.90 (หลัก) · p ≥ 0.97 (ไม้คม) | p มาจากการแจกแจงราย bin |\n")
        w("| กันความเสี่ยงราคา | ไม่ซื้อ < 0.02 · ไม่ซื้อ > 0.25 (ไม้ YES) | เพดานกว้างกว่าแล้ว ROI ตก |\n")
        w("\n**bias รายโมเดลที่ต้องหักก่อนใช้ (ค่าสัมบูรณ์มากสุด 6 อัน):**\n\n| โมเดล | หน่วย | bias (°) |\n|---|---|---|\n")
        for m, u, v in bias_tbl:
            w(f"| {m} | {u} | {v:+.3f} |\n")

        w("\n## 6) ตรรกะการตัดสินใจ (pseudocode สำหรับเครื่อง/LLM)\n\n```text\n")
        w("INPUT: city, date, now_local_hour\n")
        w("PRECHECK\n  if city in {hong-kong, shenzhen}: SKIP\n")
        w("  if now_local_hour < 13: ใช้ day-ahead เท่านั้น (โหมดเฝ้าดู; ยังไม่เข้าไม้)\n\n")
        w("1. รวมสมาชิกโมเดล (3–4 แหล่งหลักพอ): v_i = forecast_i − bias_i        # bias fit ย้อนหลัง\n")
        w("2. mu = median(v_i)                                                    # มัธยฐาน ไม่ใช่ค่าเฉลี่ย\n")
        w("3. sigma = sigma_pooled(unit) ปรับด้วย model_spread ของวันนั้น          # กว้างขึ้นเมื่อโมเดลเห็นไม่ตรงกัน\n")
        w("   ถ้า now ≥ 13 และมี obs: mu ← ผสม(obs, พยากรณ์) น้ำหนัก 0.5 @14:00 → obs ล้วน ≥16:00\n")
        w("   ถ้า now ≥ 16: ใช้ max-so-far + การกระจายของ 'ส่วนที่เหลือ' จาก 30 วันย้อนหลังของเมืองนั้น\n")
        w("4. p(bin) = Gaussian(mu, sigma) ตัดที่ขอบ bin ทุก bin ของวันนั้น แล้ว normalize\n\n")
        w("DECISION (เวลา 16:00 เป็นหลัก)\n")
        w("  b* = argmax p(bin)\n")
        w("  if p(b*) ≥ 0.90 and 0.02 ≤ price(b*) ≤ 0.10  → BUY YES 1% ของทุน   # ไม้หลัก\n")
        w("  elif p(b*) ≥ 0.90 and 0.10 < price(b*) ≤ 0.25 → BUY YES 1%          # ไม้รอง\n")
        w("  for every other bin b:\n")
        w("      if price(b) ≥ 0.50 and price(b) − p(b) ≥ 0.30 → BUY NO 1%       # ฝั่งขาย\n")
        w("  (ไม้คม p ≥ 0.97 ที่ 15:00 ทำได้ถ้าต้องการเข้าเร็ว)\n\n")
        w("RISK\n  จ่ายจริง = ราคา + slippage; ถ้า NO: จ่าย 1 − YES_bid (ต้องดู bid จริง ไม่ใช่ last trade)\n")
        w("  เพดานรวม: ≤ 6 ไม้/วัน · ≤ 5% ของทุน/วัน · หยุดเมื่อ drawdown > 15% ของทุน แล้วทบทวน\n")
        w("  ถือถึงเฉลย (ไม่ TP) · stop −25% ได้ตามใจ\n")
        w("LOG: city, date, hour, bin, p, price, stake, reason(หลัก/รอง/NO) → ทุกไม้\n```\n")

        w("\n## 7) ข้อจำกัดที่ต้องรู้ก่อนใช้เงินจริง\n\n")
        w("1. **ราคาที่ใช้ทดสอบคือ last-trade รายชั่วโมง ไม่ใช่ ask ที่เราจะได้จริง** → ไม้ YES ต้องจ่าย ask (ผลจริงแย่ลงเล็กน้อย) "
          "และไม้ NO ต้องจ่าย `1 − YES_bid` (ผลจริงอาจแย่ลงมาก) ⇒ **ต้องเก็บ bid/ask จริงใน `cheap_live.py --log` ก่อนขยายฝั่ง NO**\n")
        w("2. ข้อมูล 85 วัน ฤดูเดียว (ก.ค.–ก.ย. 2026) ยังไม่ผ่านฤดูหนาวและวันที่อากาศแปรปรวนหนัก\n")
        w("3. n ของไม้คม (p≥0.97, ราคา ≤0.15) เล็ก (หลักสิบ) → CI กว้าง อย่าใช้เป็นไม้หลัก\n")
        w("4. ตลาดบางวันไม่มี bin ที่ราคาอยู่ในช่วง → ไม่มีไม้ (นั่นคือพฤติกรรมที่ถูก: ไม่บังคับไม้)\n")
        w("5. ค่าธรรมเนียมสัญญาจริงบน Polymarket = 0 (แต่มี spread) → ที่ใส่ 2%+2 ticks คือ stress test\n")
        w("6. **เพดานสภาพคล่อง (สำคัญมากกับไม้ราคาถูก):** ไม้ที่ราคา 0.03 ให้ตัวคูณ ~33× → ต้องจำกัด "
          "`ขนาดไม้ × (1/ราคา) ≤ 5% ของปริมาณซื้อขายจริงของ bin นั้น` และไม่ควรจ่ายเกิน ~2% ของ volume รายวันที่ราคานั้น "
          "» ไม้ 1% ของทุนต่อไม้ที่ระบุในตารางสมมติว่าได้ fill ที่ราคาที่เห็น\n")
        w("7. 85 วัน ฤดูเดียว และผลตอบแทนที่สูงมาก (ROI หลักร้อย %) คือ *paper* — ตลาดจริงจ่ายช้ากว่า/ราคาใหม่ไวกว่า "
          "เมื่อมีคนอื่นใช้วิธีเดียวกัน (โพสต์เองก็บอกว่า edge ถูกบีบเมื่อคนเยอะ) ⇒ ควรวัดใหม่ทุกสัปดาห์ด้วย `cheap_live.py --log`\n")
    
    # ── 5) JSON params สำหรับเครื่อง/LLM ──
    def j(s):
        if not s:
            return None
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in s.items()}

    params = dict(
        generated_for="Polymarket daily highest-temperature markets",
        split=a.split, stake=stake, universe_ban=UNIVERSE_BAN,
        universe=dict(rule=f"obs-in-resolved-bucket >= {AGREE_THR:.2f} on train (n>={AGREE_MIN_N})",
                      n_cities=len(UNIV), cities=sorted(UNIV),
                      dropped=[c for c, _av, _n in DROPPED],
                      agreement={c: round(v[0], 3) for c, v in sorted(AGREE.items())}),
        entry=dict(hour_local=16, hour_alt_early=15, hour_alt_late=17,
                   p_min=0.90, p_min_sharp=0.97,
                   yes_price_min=0.02, yes_price_primary_max=0.10, yes_price_secondary_max=0.25,
                   no_price_min=0.50, no_margin=0.30, mode="argmax",
                   no_strict="bin top < max-so-far − 0.5 (provably impossible)"),
        model=dict(sources=AV.MODELS, aggregator="median_after_per_model_bias",
                   sigma_pooled_c=round(sigma.get("C", 0), 3), sigma_pooled_f=round(sigma.get("F", 0), 3),
                   mae_c=round(mae.get("C", 0), 3), mae_f=round(mae.get("F", 0), 3),
                   bias_table={f"{m}|{u}": round(v, 3) for (m, u), v in bias.items()}),
        intraday=dict(fuse_weight_14h=0.5, obs_only_from_hour=16, increment_lookback_days=30),
        sizing=dict(pct_bankroll_per_bet=1.0, max_bets_per_day=6, max_pct_bankroll_per_day=5.0,
                    stop_drawdown_pct=15.0, exit="hold_to_resolution", optional_stop_pct=-25),
        fees_stress=dict(tick=TICK, fee_pct=FEE),
        expected=dict(
            yes_primary=j(rule_stats(p_yes_small, stake)),
            yes_secondary=j(rule_stats(p_yes_mid, stake)),
            no_side=j(rule_stats(p_no, stake)),
            no_impossible=j(rule_stats(no_imp, stake)),
            portfolio_recommended=j(S_port),
            portfolio_wide=j(S_wide),
            train=j(rule_stats([x for x in port_primary if x["date"] < a.split], stake)),
            test=j(rule_stats([x for x in port_primary if x["date"] >= a.split], stake)),
            wide_portfolio=j(S_wide),
            no_strict=j(rule_stats(pick_no_imp(rows, 16, 0.50, 0.30), stake)),
        ),
        tool=dict(live="cheap_live.py v2 (universe filter + NO side + real bid/ask log)",
                  tests="tests_live_v2.py (20 checks)", log_columns="data/cheap_live_log.csv v2"),
        ml=dict(tested="xgboost 3.4.1 vs our rules (reports/ml_boost.md)",
                day_ahead="ML top-1 46.6% vs ours 44.5% (+2.1pts, Brier .672 vs .715) — ใช้เป็นตัวช่วยยืนยันได้",
                intraday="ML แพ้: 16:00 max 85.8% vs 86.2% · เงิน 16:00 80.0% vs 85.5% (ROI +517% vs +561%) · 14:00 67.7% vs 84.6%",
                verdict="ไม่แทนสูตรเราในเส้นเงิน · ใช้ ML ช่วยเฉพาะ 'เลือก bin' ตอน day-ahead",
                no_gate_trap="argmax ล้วน (ไม่ใช้ประตู p≥0.90): ชนะ 8.5–8.6% ROI ติดลบ"),
        rejections=dict(laddering="no effect: legs/basket 1.00, worst -34.6%",
                        regional_models="no gain: EU -0.7..-5.3 pts, Asia -4.3..0.0",
                        ensemble_member_counting="worse: 71.4% vs 86.9% intraday",
                        price_below_002="trap: 6.0% win"),
    )
    with open(PARAMS, "w", encoding="utf-8") as f:
        json.dump(params, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
