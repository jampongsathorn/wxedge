#!/usr/bin/env python3
"""edge_monitor.py — วัด "edge ที่เทรดได้จริง" จาก snapshot ของเราเอง (bid/ask จริงจาก CLOB book)

ทำไมต้องมี: backtest เดิมใช้ราคา last-trade ซึ่ง stale ณ ขอบชั่วโมง (ดู reports/live_reality_check.md)
ตัวนี้อ่าน data/snapshots/*.jsonl (ราคา ask/bid จริงที่บันทึกทุก 5 นาที) แล้วตอบคำถามเดียว:
    "มีจังหวะไหนที่โมเดลของเราบอกว่าถูกกว่าราคาที่ตลาดขายจริง (p > ask) บ้างไหม"

เอาต์พุต: reports/edge_monitor.md + data/edge_monitor.json
รัน: python3 edge_monitor.py            (อ่านไฟล์ในเครื่อง ไม่ยิงเน็ต · ปลอดภัยกับโซ่)
"""
import argparse
import collections
import glob
import json
import math
import os
import statistics as st
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP = os.path.join(HERE, "data", "snapshots")
REPORT = os.path.join(HERE, "reports", "edge_monitor.md")
OUT = os.path.join(HERE, "data", "edge_monitor.json")


def load(days=14):
    rows = []
    files = sorted(glob.glob(os.path.join(SNAP, "*.jsonl")))[-days:]
    for f in files:
        for line in open(f, encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def pairs(rows):
    """[(p-ask, ask, p, meta)] เฉพาะคู่ที่ราคาสมเหตุสมผล"""
    out = []
    for r in rows:
        for b in (r.get("bins") or []):
            if len(b) < 4 or b[3] is None or b[2] is None:
                continue
            try:
                p, ask = float(b[3]), float(b[2])
            except (TypeError, ValueError):
                continue
            if not (0 < ask < 1):
                continue
            out.append(dict(delta=round(p - ask, 4), ask=ask, p=p, city=r.get("city"),
                            hour=r.get("hour"), bin=b[0], ts=r.get("ts")))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rows = load(a.days)
    ps = pairs(rows)
    pos = [x for x in ps if x["delta"] > 0]
    by_day = collections.defaultdict(list)
    for x in ps:
        by_day[str(x["ts"])[:10]].append(x)

    o = dict(
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        snapshots=len(rows),
        pairs=len(ps),
        positive=len(pos),
        best_delta=max([x["delta"] for x in ps], default=None),
        median_delta=round(st.median([x["delta"] for x in ps]), 4) if ps else None,
        thresholds={str(t): sum(1 for x in ps if x["delta"] >= t) for t in (0.05, 0.10, 0.15, 0.20, 0.30)},
        tradable=[x for x in ps if 0.02 <= x["ask"] <= 0.25 and x["p"] >= 0.60 and x["delta"] >= 0.15],
        by_day={d: dict(n=len(v), pos=sum(1 for x in v if x["delta"] > 0),
                        best=max(x["delta"] for x in v)) for d, v in sorted(by_day.items())},
        top=sorted(ps, key=lambda x: -x["delta"])[:10],
    )
    if a.json:
        print(json.dumps(o, ensure_ascii=False, indent=1))
    L = ["# Edge monitor — จังหวะที่โมเดลถูกกว่าราคาจริง (p > ask)", "",
         "อัปเดต %s · snapshot %d จุด · คู่ bin×เวลา %d คู่ (ราคา ask จริงจาก CLOB book)" % (o["generated_at"], o["snapshots"], o["pairs"]), ""]
    if not ps:
        L += ["**ยังไม่มีข้อมูลพอ** — รอ snapshot เพิ่ม", ""]
    else:
        L += ["| ตัวชี้วัด | ค่า |", "|---|---|",
              "| จังหวะที่ p > ask | **%d** จาก %d คู่ (%.2f%%) |" % (o["positive"], o["pairs"], 100.0 * o["positive"] / o["pairs"]),
              "| ส่วนต่าง p−ask ดีที่สุด | %+.4f |" % o["best_delta"],
              "| มัธยฐานส่วนต่าง | %+.4f |" % o["median_delta"],
              "| p−ask ≥ 0.05 / 0.10 / 0.15 / 0.20 / 0.30 | %d / %d / %d / %d / %d |" % tuple(o["thresholds"][str(t)] for t in (0.05, 0.10, 0.15, 0.20, 0.30)),
              "| จังหวะที่เข้าเกณฑ์เทรด (ask 0.02–0.25 · p≥0.60 · p−ask≥0.15) | **%d** |" % len(o["tradable"]), ""]
        L += ["**รายวัน**", "", "| วัน | คู่ | p>ask | ดีสุด |", "|---|---|---|---|"]
        for d, v in o["by_day"].items():
            L += ["| %s | %d | %d | %+.4f |" % (d, v["n"], v["pos"], v["best"])]
        L += ["", "**10 อันดับที่ส่วนต่างดีที่สุด (ยังไม่ถึงเกณฑ์เทรดก็ได้ — ดูแนวโน้ม)**", "",
              "| p−ask | เมือง | ชม. | bin | ask | p | เวลา |", "|---|---|---|---|---|---|---|"]
        for x in o["top"]:
            L += ["| %+.4f | %s | %s | %s | %.3f | %.3f | %s |" % (x["delta"], x["city"], x["hour"], x["bin"], x["ask"], x["p"], x["ts"])]
    L += ["", "---", "",
          "**เกณฑ์ตีความ**: ถ้าคอลัมน์ `p > ask` ยังเป็น 0 ต่อเนื่องเป็นสัปดาห์ → โมเดลไม่มี edge เหนือราคาตลาด",
          "ที่ซื้อขายได้จริง และไม่ควรใช้เงินจริง (ดู `reports/live_reality_check.md`)", ""]
    open(REPORT, "w", encoding="utf-8").write("\n".join(L) + "\n")
    json.dump(o, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("เขียน %s + %s" % (REPORT, OUT))
    print("p>ask: %d/%d · ดีสุด %s" % (o["positive"], o["pairs"], o["best_delta"]))


if __name__ == "__main__":
    main()
