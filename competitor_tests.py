#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
competitor_tests.py — ทดลองไอเดียจากโพสต์คู่แข่ง กับข้อมูลจริงของเรา (85 วัน, 48 เมือง, เฉลยจริง)

ไอเดียที่ทดสอบ:
  A) [@AlterEgo_eth · stations] ตรวจว่าสถานี/วันของเราตรงกับที่ตลาดใช้ตัดสินจริงทุกเมืองไหม (ต่อเมือง)
  B) [@polydao · laddering]      ซื้อ "บันได" bin ราคาถูกหลายใบพร้อมกัน ดีกว่าซื้อใบเดียวที่มั่นใจสุดไหม
  C) [@polydao · sell overpriced] ขาย bin ที่ตลาดตั้งราคาแพงเกิน (buy NO) ได้ edge ไหม
ข้อมูล: data/cheap_bets.csv (220,338 แถว = ทุก bin × ชั่วโมง × วัน), data/market_labels.csv, data/dataset_full.csv
รัน: python3 competitor_tests.py --split 2026-08-15
"""
import argparse, csv, os, statistics as st
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "reports", "competitor_tests.md")
HOURS = [12, 14, 15, 16, 17]

# สถานีตามโพสต์ของ @AlterEgo_eth (2057434097577079064) + แก้ไขในคอมเมนต์
TWEET_STATIONS = {
    "los-angeles": "KLAX", "nyc": "KLGA", "chicago": "KORD", "dallas": "KDAL", "miami": "KMIA",
    "san-francisco": "KSFO", "atlanta": "KATL", "austin": "KAUS", "denver": "KBKF", "seattle": "KSEA",
    "mexico-city": "MMMX", "panama-city": "MPMG", "buenos-aires": "SAEZ", "sao-paulo": "SBGR",
    "london": "EGLC", "paris": "LFPB", "madrid": "LEMD", "milan": "LIMC", "amsterdam": "EHAM",
    "munich": "EDDM", "warsaw": "EPWA", "helsinki": "EFHK", "istanbul": "LTFM", "ankara": "LTAC",
    "moscow": "UUWW", "jeddah": "OEJN", "tel-aviv": "LLBG", "cape-town": "FACT", "karachi": "OPKC",
    "beijing": "ZBAA", "shanghai": "ZSPD", "guangzhou": "ZGGG", "shenzhen": "ZGSZ", "chengdu": "ZUUU",
    "chongqing": "ZUCK", "wuhan": "ZHHH", "qingdao": "ZSQD", "hong-kong": "HKO", "taipei": "RCSS",
    "singapore": "WSSS", "jakarta": "WIHH", "manila": "RPLL", "lucknow": "VILK", "seoul": "RKSI",
    "tokyo": "RJTT", "kuala-lumpur": "WMKK", "istanbul2": "", "wellington": "NZWN", "busan": "RKPK",
}


def load_bets():
    rows = []
    with open(os.path.join(HERE, "data", "cheap_bets.csv"), newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                rows.append(dict(city=r["city"], date=r["date"], hour=int(r["hour"]), bin=r["bin"],
                                 unit=r["unit"], p=float(r["model_p"]), price=float(r["price"]),
                                 won=int(r["won"])))
            except (KeyError, ValueError):
                continue
    return rows


def markets(rows):
    m = defaultdict(list)
    for r in rows:
        m[(r["city"], r["date"], r["hour"])].append(r)
    return m


# ─────────────────────────── A) Station audit ───────────────────────────
def exp_station_audit():
    import json
    st_cfg = json.load(open(os.path.join(HERE, "stations.json"), encoding="utf-8"))
    labels = {(r["city"], r["date"]): (float(r["lo"]), float(r["hi"]), r["unit"])
              for r in csv.DictReader(open(os.path.join(HERE, "data", "market_labels.csv"), encoding="utf-8"))}
    per_city = defaultdict(lambda: [0, 0])
    station_diff = []
    for r in csv.DictReader(open(os.path.join(HERE, "data", "dataset_full.csv"), encoding="utf-8")):
        k = (r["city"], r["date"])
        if k not in labels or not r.get("obs_c"):
            continue
        lo, hi, unit = labels[k]
        v = float(r["obs_c"])
        v = v * 9 / 5 + 32 if unit == "F" else v
        per_city[r["city"]][0] += 1
        if lo - 0.5 <= v <= hi + 0.5:
            per_city[r["city"]][1] += 1
    for c, cfg in st_cfg.items():
        t = TWEET_STATIONS.get(c)
        if t and t != cfg["icao"]:
            station_diff.append((c, cfg["icao"], t))
    rows = sorted((c, n, h, h / n * 100) for c, (n, h) in per_city.items())
    print("\n[A] สถานี/วัน ตรงกับที่ตลาด resolve จริงแค่ไหน (ต่อเมือง):")
    bad = [r for r in rows if r[3] < 85]
    for c, n, h, p in bad:
        print(f"    ⚠ {c:16s} {p:5.1f}% (n={n})   icao={st_cfg[c]['icao']}")
    print(f"    รวม: {sum(r[2] for r in rows)}/{sum(r[1] for r in rows)} = "
          f"{sum(r[2] for r in rows)/sum(r[1] for r in rows)*100:.1f}% · เมืองที่ต่ำกว่า 85%: {len(bad)}/{len(rows)}")
    if station_diff:
        print("    สถานีที่ต่างจากโพสต์:")
        for c, ours, theirs in station_diff:
            print(f"      {c:16s} เรา={ours}  โพสต์={theirs}")
    else:
        print("    สถานีตรงกับโพสต์ทุกเมืองที่เทียบได้")
    return rows, station_diff


# ─────────────────────────── B) Laddering ───────────────────────────
def basket_stats(baskets):
    """baskets = list of dict(cost=..., payout=...)"""
    if not baskets:
        return None
    n = len(baskets)
    tot_cost = sum(b["cost"] for b in baskets)
    tot_pay = sum(b["payout"] for b in baskets)
    roi = (tot_pay - tot_cost) / tot_cost * 100 if tot_cost else 0
    won_any = sum(1 for b in baskets if b["payout"] > 0) / n * 100
    legs = sum(b["legs"] for b in baskets) / n
    avg_cost = tot_cost / n
    return dict(n=n, roi=roi, won_any=won_any, legs=legs, cost=avg_cost,
                payoff=(tot_pay / tot_cost if tot_cost else 0))


def build_baskets(mk, hour, selector):
    out = []
    for (c, d, h), bins in mk.items():
        if h != hour:
            continue
        legs = [b for b in bins if selector(b)]
        if not legs:
            continue
        cost = sum(b["price"] for b in legs)
        payout = 1.0 if any(b["won"] for b in legs) else 0.0
        out.append(dict(cost=cost, payout=payout, legs=len(legs),
                        edge=sum(max(0.0, b["p"] - b["price"]) for b in legs)))
    return out


def exp_ladder(mk):
    print("\n[B] Laddering (ซื้อหลายใบพร้อมกัน) เทียบกับซื้อใบเดียว:")
    res = {}
    for hour in (14, 15, 16, 17):
        rows = []
        # ใบเดียว: argmax p ที่ผ่านเกตราคา (กฎที่เราใช้จริง)
        rows.append(("champion: p≥0.90 · ราคา 0.02–0.25 · argmax",
                     basket_stats(build_baskets(mk, hour, lambda b: b["p"] >= 0.90 and 0.02 <= b["price"] <= 0.25))))
        for k in (2, 3, 4):
            def sel_k(b, k=k):
                return b["p"] >= 0.90 and b["price"] <= 0.10
            bs = []
            for (c, d, h), bins in mk.items():
                if h != hour:
                    continue
                cand = sorted([b for b in bins if b["p"] >= 0.90 and b["price"] <= 0.10],
                              key=lambda b: -b["p"])[:k]
                if not cand:
                    continue
                bs.append(dict(cost=sum(b["price"] for b in cand), payout=1.0 if any(b["won"] for b in cand) else 0.0,
                               legs=len(cand), edge=0))
            rows.append((f"ladder: {k} ใบ (p≥0.90 · ราคา ≤0.10 · เรียงตาม p)",
                         basket_stats(bs)))
        # edge-weighted: bin ที่ p − price มากสุด 3 ใบ ราคา ≤0.10
        bs = []
        for (c, d, h), bins in mk.items():
            if h != hour:
                continue
            cand = sorted([b for b in bins if b["price"] <= 0.10 and b["p"] - b["price"] >= 0.05],
                          key=lambda b: -(b["p"] - b["price"]))[:3]
            if not cand:
                continue
            bs.append(dict(cost=sum(b["price"] for b in cand), payout=1.0 if any(b["won"] for b in cand) else 0.0,
                           legs=len(cand), edge=0))
        rows.append(("ladder: 3 ใบ ที่ edge สูงสุด (ราคา ≤0.10)", basket_stats(bs)))
        # ถูกสุดล้วน ๆ: ทุก bin ราคา 0.02–0.05 (ไม่มีโมเดล) — กลุ่มควบคุม
        rows.append(("กลุ่มควบคุม: ทุก bin ราคา 0.02–0.05 (ไม่ใช้โมเดล)",
                     basket_stats(build_baskets(mk, hour, lambda b: 0.02 <= b["price"] <= 0.05))))
        res[hour] = rows
        print(f"  ── {hour}:00 local ──")
        for name, s in rows:
            if not s:
                continue
            print(f"     {name:52s} n={s['n']:4d} ROI={s['roi']:+8.1f}% ต้นทุน/ตะกร้า={s['cost']:.3f} "
                  f"ถูก≥1ใบ={s['won_any']:5.1f}% ใบ/ตะกร้า={s['legs']:.1f}")
    return res


# ─────────────────────────── C) Sell overpriced (buy NO) ───────────────────────────
def exp_sell(mk):
    print("\n[C] ขาย bin ที่แพงเกิน (buy NO) — ใช้โมเดลจับ overprice:")
    res = {}
    for hour in (12, 14, 16):
        rows = []
        for margin in (0.15, 0.30):
            for lo, hi in ((0.30, 0.97), (0.50, 0.97), (0.70, 0.97)):
                legs = []
                for (c, d, h), bins in mk.items():
                    if h != hour:
                        continue
                    for b in bins:
                        if lo <= b["price"] <= hi and (b["price"] - b["p"]) >= margin:
                            legs.append(b)
                if len(legs) < 30:
                    continue
                cost = sum(1 - b["price"] for b in legs)     # จ่ายเพื่อถือ NO
                pay = sum(1 - b["won"] for b in legs)        # NO จ่าย 1 เมื่อ bin นั้นไม่ชนะ
                n = len(legs)
                rows.append((f"buy NO: ราคา {lo:.2f}–{hi:.2f} & price−p ≥ {margin:.2f}",
                             n, st.mean(1 - b["won"] for b in legs) * 100, cost / n,
                             (pay - cost) / cost * 100))
        res[hour] = rows
        print(f"  ── {hour}:00 local ──")
        for name, n, win, cost, roi in rows:
            print(f"     {name:46s} n={n:5d} ชนะ(NO)={win:5.1f}% จ่ายเฉลี่ย={cost:.3f} ROI={roi:+7.1f}%")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="2026-08-15")
    a = ap.parse_args()
    rows = load_bets()
    print(f"cheap_bets rows={len(rows):,}")
    audit, sdiff = exp_station_audit()
    mk_all = markets(rows)
    mk_test = markets([r for r in rows if r["date"] >= a.split])
    lad = exp_ladder(mk_all)
    sell = exp_sell(mk_test)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("# ทดลองไอเดียคู่แข่ง กับข้อมูลจริงของเรา\n\n"
                f"> `competitor_tests.py --split {a.split}` · 220,338 แถว bin×วัน×ชั่วโมง · 48 เมือง × 85 วัน · เฉลย = bucket ที่ตลาด resolve จริง\n"
                f"> (B) ใช้ทั้ง 85 วัน · (C) ใช้ช่วง test (≥{a.split}) เพื่อความเข้มงวด\n\n")
        f.write(f"## A) สถานี/วันตรงกับที่ตลาดใช้ตัดสิน (ต่อเมือง)\n\n"
                f"รวม **{sum(r[2] for r in audit)}/{sum(r[1] for r in audit)} = "
                f"{sum(r[2] for r in audit)/sum(r[1] for r in audit)*100:.1f}%** · เมืองที่ต่ำกว่า 85%: "
                f"{sum(1 for r in audit if r[3] < 85)}/{len(audit)}\n\n| เมือง | ตรง% | n | icao ที่เราใช้ |\n|---|---|---|---|\n")
        import json
        st_cfg = json.load(open(os.path.join(HERE, "stations.json"), encoding="utf-8"))
        for c, n, h, p in audit:
            if p < 85:
                f.write(f"| {c} | **{p:.1f}** | {n} | {st_cfg[c]['icao']} |\n")
        f.write("\n## B) Laddering (ซื้อบันไดหลายใบ) เทียบซื้อใบเดียว\n\n")
        for hour, rr in lad.items():
            f.write(f"### {hour}:00 local\n\n| กลยุทธ์ | n ตะกร้า | ROI | ต้นทุน/ตะกร้า | ถูก ≥1 ใบ | ใบ/ตะกร้า |\n|---|---|---|---|---|---|\n")
            for name, s in rr:
                if s:
                    f.write(f"| {name} | {s['n']} | **{s['roi']:+.1f}%** | {s['cost']:.3f} | {s['won_any']:.1f}% | {s['legs']:.2f} |\n")
            f.write("\n")
        f.write("## C) ขาย bin ที่แพงเกิน (buy NO) — ช่วง test\n\n")
        for hour, rr in sell.items():
            f.write(f"### {hour}:00 local\n\n| กฎ | n ใบ | ชนะ(NO)% | จ่ายเฉลี่ย | ROI |\n|---|---|---|---|---|\n")
            for name, n, win, cost, roi in rr:
                f.write(f"| {name} | {n} | {win:.1f}% | {cost:.3f} | **{roi:+.1f}%** |\n")
            f.write("\n")
    print(f"\nเขียน {OUT}")


if __name__ == "__main__":
    main()
