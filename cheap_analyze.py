#!/usr/bin/env python3
"""
cheap_analyze.py — วิเคราะห์ผล backtest "ซื้อ bin ถูก (<$0.25) แล้วชนะตอนปิด"
อ่าน data/cheap_bets.csv (จาก cheap_bets.py) แล้วสรุปเป็นตาราง + bootstrap CI + เขียน reports/cheap_bets.md

รัน: python3 cheap_analyze.py --pmax 0.25
"""
import argparse, collections, csv, os, random, statistics as st, sys, json
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))


def load(path):
    rows = []
    for r in csv.DictReader(open(path, encoding="utf-8")):
        rows.append(dict(city=r["city"], date=r["date"], hour=int(r["hour"]), unit=r["unit"], bin=r["bin"],
                         model_p=float(r["model_p"]), price=float(r["price"]), won=int(r["won"])))
    return rows


def stats(rs):
    n = len(rs)
    if not n:
        return dict(n=0, hit=0.0, avg=0.0, ev=0.0, roi=0.0, stake=0.0, ret=0.0)
    wins = sum(r["won"] for r in rs)
    stake = sum(r["price"] for r in rs)
    return dict(n=n, hit=wins / n, avg=stake / n, ev=wins / n - stake / n,
                roi=(wins - stake) / stake if stake else 0.0, stake=stake, ret=wins)


def boot(rs, iters=2000, seed=7):
    """bootstrap 95% CI ของ hit rate และ ROI (resample ไม้)"""
    if len(rs) < 5:
        return (None, None), (None, None)
    rnd = random.Random(seed)
    hits, rois = [], []
    for _ in range(iters):
        s = [rs[rnd.randrange(len(rs))] for _ in rs]
        d = stats(s)
        hits.append(d["hit"])
        rois.append(d["roi"])
    hits.sort(); rois.sort()
    lo, hi = int(0.025 * iters), int(0.975 * iters)
    return (hits[lo], hits[hi]), (rois[lo], rois[hi])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(HERE, "data", "cheap_bets.csv"))
    ap.add_argument("--pmax", type=float, default=0.25)
    ap.add_argument("--report", default=os.path.join(HERE, "reports", "cheap_bets.md"))
    ap.add_argument("--pmin", type=float, default=0.35)
    a = ap.parse_args()
    rows = load(a.data)
    hours = sorted({r["hour"] for r in rows})
    cities = {r["city"] for r in rows}
    L = []

    def w(s=""):
        L.append(s)

    w("# ซื้อ bin ราคาถูก (≤ $%.2f) แล้วชนะตอนปิด — ผลวัดจริงจากราคาทุก bin" % a.pmax)
    w()
    w("วันที่ %s · ข้อมูล **%d แถว** (bin × วัน × ชั่วโมง) จาก **%d เมือง** · "
      "ราคาอ่านจาก CLOB prices-history (ราคาซื้อขายจริงรายชั่วโมง)" % (
          datetime.now().strftime("%Y-%m-%d %H:%M"), len(rows), len(cities)))
    w()
    w("> ที่มา: `cheap_bets.py` สร้างด้วยโมเดล intraday (ค่าที่วัดได้ถึงชั่วโมงนั้น + การกระจายตัวของส่วนที่เพิ่มอีก)")
    w("> แล้ววัดกับ **bucket ที่ตลาด resolve จริง** (`data/market_labels.csv`)")

    # ── 1. ตารางหลัก: กฎการเลือก × ชั่วโมง ──
    w("\n## 1) กฎการเลือกไม้ (entry ตามเวลาที่ระบุ, ราคา ณ ชั่วโมงนั้น)\n")
    w("| กฎ | 12:00 | 14:00 | 15:00 | 16:00 | 17:00 |")
    w("|---|---|---|---|---|---|")
    rules = [
        ("ซื้อทุก bin ที่ราคา < %.2f (ไม่มีโมเดล)" % a.pmax, lambda r: r["price"] < a.pmax),
        ("ราคา < %.2f + โมเดล p ≥ %.2f" % (a.pmax, a.pmin), lambda r: r["price"] < a.pmax and r["model_p"] >= a.pmin),
        ("ราคา < %.2f + p ≥ 0.50" % a.pmax, lambda r: r["price"] < a.pmax and r["model_p"] >= 0.50),
        ("ราคา < %.2f + p ≥ 0.65" % a.pmax, lambda r: r["price"] < a.pmax and r["model_p"] >= 0.65),
        ("ราคา 0.02–0.25 + p ≥ %.2f" % a.pmin, lambda r: 0.02 <= r["price"] < a.pmax and r["model_p"] >= a.pmin),
        ("ราคา 0.05–0.25 + p ≥ %.2f" % a.pmin, lambda r: 0.05 <= r["price"] < a.pmax and r["model_p"] >= a.pmin),
        ("edge (p−ราคา) ≥ 0.20", lambda r: r["model_p"] - r["price"] >= 0.20),
        ("edge ≥ 0.20 และราคา ≥ 0.02", lambda r: r["model_p"] - r["price"] >= 0.20 and r["price"] >= 0.02),
    ]
    for name, fn in rules:
        cells = []
        for h in [12, 14, 15, 16, 17]:
            rs = [r for r in rows if r["hour"] == h and fn(r)]
            d = stats(rs)
            cells.append("–" if not d["n"] else "%d ไม้ · %.0f%% · ROI %+.0f%%" % (d["n"], 100 * d["hit"], 100 * d["roi"]))
        w("| %s | %s |" % (name, " | ".join(cells)))
    w()
    w("อ่านตาราง: `n ไม้ · อัตราชนะ · ROI` (ROI คิดจากทุนที่จ่ายจริงต่อไม้ ไม่หัก fee/spread)")

    # ── 2. ถ้าใช้ 16:00 (ค่าที่ดีที่สุด) แยกช่วงราคา ──
    hh = 16 if 16 in hours else hours[-1]
    w("\n## 2) ช่วงราคา × ผลลัพธ์ (ชั่วโมง %02d:00, โมเดล p ≥ %.2f)\n" % (hh, a.pmin))
    w("| ช่วงราคา | n ไม้ | ชนะ% | ราคาเฉลี่ย | ROI |")
    w("|---|---|---|---|---|")
    for lo, hi in [(0.0, 0.02), (0.02, 0.05), (0.05, 0.10), (0.10, 0.15), (0.15, 0.25), (0.25, 0.50)]:
        rs = [r for r in rows if r["hour"] == hh and lo <= r["price"] < hi and r["model_p"] >= a.pmin]
        d = stats(rs)
        if d["n"]:
            roi_s = "%+.1f%%" % (100 * d["roi"]) if d["avg"] >= 0.01 else "n/a (ราคาเกือบ 0)"
            w("| %.2f–%.2f | %d | %.1f%% | %.3f | %s |" % (lo, hi, d["n"], 100 * d["hit"], d["avg"], roi_s))

    # ── 2.5 กฎเพิ่ม: bin ที่ "ติดกับตัวเต็ง" ของโมเดล และ bin ที่เป็นตัวเลือกอันดับ 2 ──
    import math as _m
    def num_bin(lab):
        m = _m.floor(0)
        import re
        mm = re.match(r"^(-?\d+)-(-?\d+)°([FC])$", lab)
        if mm:
            return float(mm.group(1))
        mm = re.match(r"^(-?\d+)°([FC])$", lab)
        if mm:
            return float(mm.group(1))
        mm = re.match(r"^(-?\d+)°[FC] or below$", lab)
        if mm:
            return float(mm.group(1))
        mm = re.match(r"^(-?\d+)°[FC] or higher$", lab)
        if mm:
            return float(mm.group(1))
        return None

    key = lambda r: (r["city"], r["date"], r["hour"])
    grp = collections.defaultdict(list)
    for r in rows:
        grp[key(r)].append(r)
    info = {}
    for k, rs in grp.items():
        top = max(rs, key=lambda r: r["model_p"])
        vals = sorted(((num_bin(r["bin"]), r) for r in rs if num_bin(r["bin"]) is not None))
        # หา bin ที่ติดกับตัวเต็ง
        adj = []
        vb = num_bin(top["bin"])
        if vb is not None:
            for v, r in vals:
                if r["bin"] != top["bin"] and abs(v - vb) <= 2.5:
                    adj.append(r["bin"])
        srt = sorted(rs, key=lambda r: -r["model_p"])
        info[k] = dict(top=top["bin"], p_top=top["model_p"],
                       second=srt[1]["bin"] if len(srt) > 1 else None,
                       p_second=srt[1]["model_p"] if len(srt) > 1 else 0.0,
                       adj=set(adj))
    def rule_adj(r):
        d = info.get(key(r))
        return bool(d) and r["bin"] in d["adj"] and r["model_p"] >= 0.20
    def rule_second(r):
        d = info.get(key(r))
        return bool(d) and r["bin"] == d["second"] and d["p_second"] >= 0.30
    def rule_adj_or_second(r):
        return rule_adj(r) or rule_second(r)
    w("\n**กฎเสริม** (ใช้กับตารางด้านบนได้): bin ที่ติดกับตัวเต็งของโมเดล หรือเป็นตัวเลือกอันดับ 2\n")
    w("| กฎ | " + " | ".join("%02d:00" % h for h in [12, 14, 15, 16, 17]) + " |")
    w("|---|" + "---|" * 5)
    for nm, fn, cond in [
        ("bin ติดตัวเต็ง + ราคา < %.2f" % a.pmax, rule_adj, lambda r: r["price"] < a.pmax),
        ("ตัวเลือกอันดับ 2 + ราคา < %.2f" % a.pmax, rule_second, lambda r: r["price"] < a.pmax),
        ("bin ติดตัวเต็ง/อันดับ2 + p ≥ 0.35 + ราคา < %.2f" % a.pmax, rule_adj_or_second,
         lambda r: r["price"] < a.pmax and r["model_p"] >= 0.35),
    ]:
        cells = []
        for h in [12, 14, 15, 16, 17]:
            rs = [r for r in rows if r["hour"] == h and fn(r) and cond(r)]
            d = stats(rs)
            cells.append("–" if not d["n"] else "%d ไม้ · %.0f%% · ROI %+.0f%%" % (d["n"], 100 * d["hit"], 100 * d["roi"]))
        w("| %s | %s |" % (nm, " | ".join(cells)))

    # ── 2.6 ความมั่นใจของโมเดล → อัตราชนะ (ที่ราคา 0.02–0.25) ──
    w("\n## 2.6) ความมั่นใจของโมเดล → อัตราชนะ (ที่ราคา 0.02–%.2f)\n" % a.pmax)
    w("| p ของโมเดล | " + " | ".join("%02d:00" % h for h in [14, 15, 16, 17]) + " |")
    w("|---|" + "---|" * 4)
    for lo, hi in [(0.02, 0.35), (0.35, 0.50), (0.50, 0.70), (0.70, 0.90), (0.90, 1.01)]:
        cells = []
        for h in [14, 15, 16, 17]:
            rs = [r for r in rows if r["hour"] == h and 0.02 <= r["price"] < a.pmax and lo <= r["model_p"] < hi]
            d = stats(rs)
            cells.append("–" if not d["n"] else "%d ไม้ · %.0f%%" % (d["n"], 100 * d["hit"]))
        w("| p %.2f–%.2f | %s |" % (lo, hi, " | ".join(cells)))

    # ── 3. กับดัก: bin ราคาเกือบศูนย์ ──
    w("\n## 3) กับดักสำคัญ: bin ราคา < 0.02 (ตลาด 'รู้' มากกว่าเรา)\n")
    for lo, hi in [(0.0, 0.02), (0.02, 0.05), (0.05, 1.01)]:
        rs = [r for r in rows if r["hour"] == hh and lo <= r["price"] < hi and r["model_p"] >= 0.5]
        d = stats(rs)
        if d["n"]:
            w("- ราคา %.2f–%.2f (p ≥ 0.5): **%d ไม้ ชนะ %.1f%%** → %s" % (
                lo, hi, d["n"], 100 * d["hit"],
                "⛔ กับดัก: ตลาดเห็นข้อมูล 5 นาทีที่สูงกว่าสถานีของเรา" if d["hit"] < 0.15 else "✅ ใช้ได้"))
    w()
    w("สาเหตุ (ตรวจแล้ว): ตลาดตัดสินจากคอลัมน์ Temp ของ NOAA/WRH ซึ่งเป็นข้อมูล **5 นาที** — "
      "มักสูงกว่า METAR รายชั่วโมง 0.3–1.0 หน่วย มีวันที่สถานีของเราสูงสุด 21°C แต่ตลาดตัดสิน 22°C "
      "ทำให้ bin ที่เรามั่นใจกลายเป็น bin ที่แพ้ ถ้าราคาตลาดบอก < 0.02 → เชื่อตลาด")

    # ── 4. สรุปไม้ที่ควรเล่นจริง + CI ──
    w("\n## 4) กฎที่แนะนำ (พร้อม bootstrap 95%% CI)\n")
    rec = [r for r in rows if r["hour"] == hh and r["price"] < a.pmax and r["model_p"] >= a.pmin and r["price"] >= 0.02]
    d = stats(rec)
    ci_hit, ci_roi = boot(rec)
    w("**กฎ:** ณ ชั่วโมง %02d:00 (เวลาท้องถิ่น) ซื้อ bin ที่ `ราคา < %.2f`, `โมเดล p ≥ %.2f`, `ราคา ≥ 0.02`\n" % (hh, a.pmax, a.pmin))
    w("- n = **%d ไม้** · ชนะ **%.1f%%** (95%% CI %.1f–%.1f%%)" % (d["n"], 100 * d["hit"], 100 * ci_hit[0], 100 * ci_hit[1]) if ci_hit[0] is not None else "- n = %d ไม้" % d["n"])
    w("- ราคาเฉลี่ยที่จ่าย **%.3f** · EV **%+.3f/หุ้น** · ROI **%+.0f%%** (95%% CI %+.0f%% ถึง %+.0f%%)" % (
        d["avg"], d["ev"], 100 * d["roi"], 100 * ci_roi[0], 100 * ci_roi[1]) if ci_roi[0] is not None else "")
    w("- ที่ hit rate นี้ จุดคุ้มทุน = ราคา %.3f → ที่ราคา ≤ %.2f ยังมี margin" % (d["hit"], d["hit"]))
    w()
    w("แยกตามชั่วโมง (กฎเดียวกัน):\n")
    w("| ชั่วโมง | n | ชนะ% | ราคาเฉลี่ย | ROI |")
    w("|---|---|---|---|---|")
    for h in hours:
        rs = [r for r in rows if r["hour"] == h and r["price"] < a.pmax and r["model_p"] >= a.pmin and r["price"] >= 0.02]
        dd = stats(rs)
        if dd["n"]:
            w("| %02d:00 | %d | %.1f%% | %.3f | %+.1f%% |" % (h, dd["n"], 100 * dd["hit"], dd["avg"], 100 * dd["roi"]))

    # ── 4.5 แบ่งครึ่งเวลา (train/test) + ความถี่ของโอกาส ──
    w("\n## 4.5) ทดสอบความเสถียร: แบ่งครึ่งเวลา\n")
    rules = {
        "ราคา < 0.25 · p ≥ 0.35 · ราคา ≥ 0.02": lambda r: r["price"] < a.pmax and r["model_p"] >= a.pmin and r["price"] >= 0.02,
        "ราคา < 0.25 · p ≥ 0.50 · ราคา ≥ 0.02": lambda r: r["price"] < a.pmax and r["model_p"] >= 0.50 and r["price"] >= 0.02,
        "ราคา < 0.15 · p ≥ 0.35 · ราคา ≥ 0.02": lambda r: r["price"] < 0.15 and r["model_p"] >= a.pmin and r["price"] >= 0.02,
        "ราคา 0.02–0.25 · edge ≥ 0.20": lambda r: 0.02 <= r["price"] < a.pmax and (r["model_p"] - r["price"]) >= 0.20,
    }
    dates = sorted({r["date"] for r in rows})
    split = dates[len(dates) // 2]
    for h in [15, 16]:
        w("\n**ชั่วโมง %02d:00** (แบ่งที่ %s)\n" % (h, split))
        w("| กฎ | ครึ่งแรก (ถึง %s) | ครึ่งหลัง |" % split)
        w("|---|---|---|")
        for nm, fn in rules.items():
            a1 = stats([r for r in rows if r["hour"] == h and fn(r) and r["date"] < split])
            b1 = stats([r for r in rows if r["hour"] == h and fn(r) and r["date"] >= split])
            f = lambda d: "–" if not d["n"] else "n=%d · %.1f%% · ROI %+.0f%%" % (d["n"], 100 * d["hit"], 100 * d["roi"])
            w("| %s | %s | %s |" % (nm, f(a1), f(b1)))
    w("\n**ความถี่ของโอกาส** (ทั้งชุดข้อมูล %d เมือง × %d วัน)\n" % (len(cities), len(dates)))
    w("| ชั่วโมง | ไม้ที่เข้าเกณฑ์ | วันที่มีไม้ | เฉลี่ยต่อวัน |" )
    w("|---|---|---|---|")
    for h in hours:
        rs = [r for r in rows if r["hour"] == h and r["price"] < a.pmax and r["model_p"] >= a.pmin and r["price"] >= 0.02]
        if rs:
            w("| %02d:00 | %d | %d | %.1f |" % (h, len(rs), len({r["date"] for r in rs}), len(rs) / max(len(dates), 1)))

    # ── 4.6 กฎความมั่นใจสูง p ≥ 0.90 ──
    w("\n## 4.6) ⭐ กฎที่คมที่สุด: ราคา 0.02–%.2f และโมเดลมั่นใจ p ≥ 0.90\n" % a.pmax)
    w("| ชั่วโมง | n ไม้ | ชนะ% | ราคาเฉลี่ย | ROI | ครึ่งแรก | ครึ่งหลัง |")
    w("|---|---|---|---|---|---|---|")
    dates2 = sorted({r["date"] for r in rows})
    split2 = dates2[len(dates2) // 2]
    for h in hours:
        rs = [r for r in rows if r["hour"] == h and 0.02 <= r["price"] < a.pmax and r["model_p"] >= 0.90]
        if not rs:
            continue
        d = stats(rs)
        a1 = stats([r for r in rs if r["date"] < split2])
        b1 = stats([r for r in rs if r["date"] >= split2])
        w("| %02d:00 | %d | %.1f%% | %.3f | %+.0f%% | %s | %s |" % (
            h, d["n"], 100 * d["hit"], d["avg"], 100 * d["roi"],
            "–" if not a1["n"] else "n=%d · %.0f%%" % (a1["n"], 100 * a1["hit"]),
            "–" if not b1["n"] else "n=%d · %.0f%%" % (b1["n"], 100 * b1["hit"])))
    fs = [r for r in rows if r["hour"] in (15, 16) and 0.02 <= r["price"] < a.pmax and r["model_p"] >= 0.90]
    if fs:
        d = stats(fs)
        w("\n**รวม 15:00–16:00: n = %d ไม้ · ชนะ %.1f%% · ราคาเฉลี่ย %.3f · ROI %+.0f%%**" % (
            d["n"], 100 * d["hit"], d["avg"], 100 * d["roi"]))
        w("\n> ข้อควรระวัง: เมื่อราคาตลาด < 0.25 แต่นายตลาดมั่นใจว่า bin อื่นจะชนะ (ราคาของ bin อื่น > 0.75) "
          "ให้ตรวจ `description`/`bestAsk` ของ bin คู่แข่งก่อน — อาจเป็นวันที่ข้อมูล 5 นาทีของ NOAA ต่างจาก METAR ของเรา")

    # ── 5. ตะกร้าต่อเมือง-วัน ──
    if rec:
        w("\n## 5) ถ้าซื้อเป็น 'ตะกร้า' ต่อเมือง-วัน (ซื้อทุก bin ที่เข้าเกณฑ์)\n")
        b = collections.defaultdict(list)
        for r in rec:
            b[(r["city"], r["date"])].append(r)
        tot_cost = sum(sum(x["price"] for x in v) for v in b.values())
        tot_ret = sum(1 for v in b.values() if any(x["won"] for x in v))
        w("- ตะกร้า %d ชุด · จ่ายรวม %.2f · ได้คืน %.2f → **ROI %+.1f%%** · อัตราที่ตะกร้ามี bin ชนะ = %.1f%%" % (
            len(b), tot_cost, tot_ret, 100 * (tot_ret - tot_cost) / tot_cost, 100 * tot_ret / len(b)))

    # ── 6. รายเมือง ──
    w("\n## 6) แยกตามเมือง (กฎที่แนะนำ)\n")
    w("| city | n ไม้ | ชนะ% | ROI |")
    w("|---|---|---|---|")
    per = collections.defaultdict(list)
    for r in rec:
        per[r["city"]].append(r)
    for c, rs in sorted(per.items(), key=lambda kv: -stats(kv[1])["hit"]):
        dd = stats(rs)
        w("| %s | %d | %.1f%% | %+.1f%% |" % (c, dd["n"], 100 * dd["hit"], 100 * dd["roi"]))

    # ── 7. ตัวอย่างไม้ ──
    w("\n## 7) ตัวอย่างไม้ (เรียงตามกำไร/ขาดทุน)\n")
    w("| city | date | ชั่วโมง | bin | p | ราคา | ผล | P&L/หุ้น |")
    w("|---|---|---|---|---|---|---|---|")
    for r in sorted(rec, key=lambda r: (r["won"] - r["price"]), reverse=True)[:12]:
        w("| %s | %s | %02d:00 | %s | %.2f | %.3f | %s | %+.3f |" % (
            r["city"], r["date"], r["hour"], r["bin"], r["model_p"], r["price"], "ชนะ ✅" if r["won"] else "แพ้ ❌",
            r["won"] - r["price"]))
    w("| ... | | | | | | | |")
    for r in sorted(rec, key=lambda r: (r["won"] - r["price"]))[:6]:
        w("| %s | %s | %02d:00 | %s | %.2f | %.3f | %s | %+.3f |" % (
            r["city"], r["date"], r["hour"], r["bin"], r["model_p"], r["price"], "ชนะ ✅" if r["won"] else "แพ้ ❌",
            r["won"] - r["price"]))

    # ── 8. ข้อจำกัด ──
    w("\n## 8) ข้อจำกัดที่ต้องรู้ก่อนใช้จริง\n")
    w("1. **ราคาที่บันทึกคือราคาซื้อขายล่าสุดรายชั่วโมง** จาก CLOB ไม่ใช่ ask ของสมุดคำสั่ง — bin ถูกมักบาง "
      "(orderMinSize / spread) ของจริงอาจเติมไม่เต็มจำนวน ต้องเช็ค `bestAsk` สดก่อนกด")
    w("2. ROI ยังไม่หัก fee ของ Polymarket, spread, และ slippage")
    w("3. ตัวอย่างไม้ที่เข้าเกณฑ์มีจำนวนจำกัด (ดู n ในตาราง) — ตัวเลข ROI ผันผวนสูง ใช้ `%d%%` ของ Kelly หรือไม้เล็ก"
      % 25)
    w("4. กับดัก bin ราคา < 0.02: หลีกเลี่ยง (ตลาดเห็นข้อมูล 5 นาทีของ NOAA ซึ่งละเอียดกว่า METAR ของเรา)")
    w("5. ใช้ได้เฉพาะช่วง **หลัง 14:00–15:00 น. ท้องถิ่น** ของเมืองนั้น — ก่อนหน้านั้นคือการเดา")
    w("6. ต้องเช็คผลจริงหลังปิดตลาด: `python3 market_labels.py --mode page ...` แล้วหรือ `journal.py score`")

    os.makedirs(os.path.dirname(a.report), exist_ok=True)
    open(a.report, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L[:0]))
    print("เขียนรายงาน: %s (%d บรรทัด)" % (a.report, len(L)))
    base = stats([r for r in rows if r["hour"] == hh and r["price"] < a.pmax and r["model_p"] >= a.pmin and r["price"] >= 0.02])
    hi = stats([r for r in rows if r["hour"] in (15, 16) and 0.02 <= r["price"] < a.pmax and r["model_p"] >= 0.90])
    if base["n"]:
        print("กฎกว้าง  @%02d:00 ราคา<%.2f p≥%.2f → n=%d ชนะ %.1f%% ROI %+.0f%%"
              % (hh, a.pmax, a.pmin, base["n"], 100 * base["hit"], 100 * base["roi"]))
    if hi["n"]:
        print("กฎคม     @15-16:00 ราคา %.02f-%.2f p≥0.90 → n=%d ชนะ %.1f%% ROI %+.0f%%"
              % (0.02, a.pmax, hi["n"], 100 * hi["hit"], 100 * hi["roi"]))


if __name__ == "__main__":
    main()
