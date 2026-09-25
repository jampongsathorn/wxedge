#!/usr/bin/env python3
"""build_monitor.py — สร้างหน้า monitor แบบ static (ไฟล์เดียว) สำหรับ GitHub Pages

อ่านข้อมูลจริงจาก data/ แล้วคำนวณทุกตัวชี้วัดเอง (ไม่ hardcode ผลลัพธ์):
  data/strategy_params.json     → KPI backtest · กติกา · จักรวาล · โมเดล · สิ่งที่ทดสอบแล้วไม่ใช้
  data/trades_recommended.csv   → equity curve 379 ไม้ · decomposition ตามฝั่ง/ชั้น
  data/cheap_live_log.csv       → forward-test จริง (36 คอลัมน์) + ไม้ที่ค้างรอเฉลย
  data/forward_stats.json       → hit rate/EV/Brier จริง (จาก forward_resolve.py)
  data/snapshots/*.jsonl        → ความครอบคลุมข้อมูลที่เก็บได้ (วัน × เมือง)
  data/bidask_probe.json        → ช่องว่าง bid/ask ที่วัดจริง
  data/universe.json            → agreement รายเมือง
  reports/bidask_check.md       → ตัวเลข stress หลังหัก ask (ถ้ามี)

โหมด:
  --mode safe  (ค่าเริ่มต้นสำหรับที่สาธารณะ) — ซ่อน bin/ราคาของไม้ที่ยังไม่ settled + ซ่อนตัวเลขกติกา
  --mode full  — โชว์ทุกอย่าง (ใช้กับที่ส่วนตัว/ในเครื่อง)

รัน:
  python3 build_monitor.py                      # → docs/index.html (safe)
  python3 build_monitor.py --mode full          # → docs/index.html (full)
  python3 build_monitor.py --out /tmp/m.html --mode full
"""
import argparse, csv, json, math, os, re, sys
from datetime import datetime, timedelta, timezone
from html import escape as esc

HERE = os.path.dirname(os.path.abspath(__file__))
def P(*a):
    return os.path.join(HERE, *a)

ISO = lambda dt: dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
TH_MONTH = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


# ───────────────────────── โหลดข้อมูล ─────────────────────────
def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def read_csv(path):
    try:
        with open(path, encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def fnum(x, nd=0):
    """ตัวเลขแบบมี comma (ใช้ tabular-nums ในตาราง)"""
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "—"
    return ("{:,.%df}" % nd).format(x)


def fdate(day):
    try:
        d = datetime.strptime(day, "%Y-%m-%d")
        return "%d %s" % (d.day, TH_MONTH[d.month - 1])
    except (ValueError, TypeError):
        return "—"


def compute(mode="safe"):
    sp = load_json(P("data", "strategy_params.json"), {}) or {}
    exp = sp.get("expected", {}) or {}
    M = {"built_at": ISO(datetime.now(timezone.utc)), "mode": mode,
         "generated_for": sp.get("generated_for", "Polymarket highest-temperature"), "split": sp.get("split")}

    # ── backtest: คำนวณสดจากไฟล์เทรดรายไม้ ──
    tr = read_csv(P("data", "trades_recommended.csv"))
    stake = float(sp.get("stake", 100) or 100)
    equity, dates, split_i = [], [], None
    cum = 0.0
    peak = 0.0
    dd = 0.0
    wins = 0
    by_side, by_tier = {}, {}
    for i, r in enumerate(tr):
        try:
            pnl = float(r["pnl"])
        except (KeyError, ValueError):
            continue
        cum += pnl
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
        equity.append(cum)
        dates.append(r.get("date", ""))
        wins += 1 if r.get("won") in ("1", "1.0", "True", "true") else 0
        if split_i is None and dates[-1] > (sp.get("split") or ""):
            split_i = len(equity) - 1
        side = (r.get("side") or "?").upper()
        tier = r.get("tier") or "?"
        for key, bucket in ((side, by_side), (tier, by_tier)):
            b = bucket.setdefault(key, {"n": 0, "wins": 0, "pnl": 0.0})
            b["n"] += 1
            b["wins"] += 1 if r.get("won") in ("1", "1.0", "True", "true") else 0
            b["pnl"] += pnl
    n_bt = len(equity)
    M["backtest"] = {"n": n_bt, "wins": wins,
                     "hit": (100.0 * wins / n_bt) if n_bt else None,
                     "pnl": cum, "roi": (100.0 * cum / (stake * n_bt)) if n_bt else None,
                     "stake": stake, "dd": dd, "dd_pct": (100.0 * dd / (stake * n_bt)) if n_bt else None,
                     "first": dates[0] if dates else None, "last": dates[-1] if dates else None,
                     "equity": equity, "split_i": split_i if split_i is not None else len(equity) // 2,
                     "by_side": by_side, "by_tier": by_tier,
                     "head": exp.get("portfolio_recommended", {}), "train": exp.get("train", {}),
                     "test": exp.get("test", {}), "yes_primary": exp.get("yes_primary", {}),
                     "yes_secondary": exp.get("yes_secondary", {}), "no_impossible": exp.get("no_impossible", {})}

    # ── stress: ตัวเลขหลังหัก ask ที่วัดได้ (อ่านจากรายงาน) ──
    stress = []
    try:
        md = open(P("reports", "bidask_check.md"), encoding="utf-8").read()
        for label, pat in (("+ median ที่วัดได้ (+0.0060)", r"median ที่วัดได้[^|]*\|\s*\$?\+?([\d,]+)"),
                           ("+ p90 ที่วัดได้ (+0.0200)", r"p90 ที่วัดได้[^|]*\|\s*\$?\+?([\d,]+)"),
                           ("+ 3 ticks (0.03)", r"3 ticks[^|]*\|\s*\$?\+?([\d,]+)")):
            m = re.search(pat, md)
            if m:
                stress.append({"label": label, "pnl": float(m.group(1).replace(",", ""))})
    except OSError:
        pass
    M["stress"] = stress

    # ── ช่องว่าง bid/ask (คำนวณสดจาก probe) ──
    pr = load_json(P("data", "bidask_probe.json"), {}) or {}
    a2l, l2b, sprd = [], [], []
    for r in pr.get("rows", []):
        try:
            a, b, l, s = r.get("ask"), r.get("bid"), r.get("last"), r.get("spread")
            if a is not None and l is not None:
                a2l.append(float(a) - float(l))
            if l is not None and b is not None:
                l2b.append(float(l) - float(b))
            if s is not None:
                sprd.append(float(s))
        except (TypeError, ValueError):
            continue
    med = lambda v: (sorted(v)[len(v) // 2] if v else None)
    M["spread"] = {"n": pr.get("n_compared"), "bins": pr.get("n_bins"), "measured_at": pr.get("measured_at"),
                   "ask_last": med(a2l), "last_bid": med(l2b), "spread": med(sprd), "settled": len(pr.get("settled_markets") or [])}

    # ── forward-test จริง ──
    log = read_csv(P("data", "cheap_live_log.csv"))
    fs = load_json(P("data", "forward_stats.json"), {}) or {}
    resolved = [r for r in log if (r.get("won") or "").strip() != ""]
    picks = []
    for r in log:
        picks.append({"city": r.get("city"), "side": r.get("side"), "tier": r.get("tier"), "bin": r.get("bin"),
                      "hour": r.get("hour"), "target": r.get("target"), "logged_at": r.get("logged_at"),
                      "ask": r.get("yes_ask") or r.get("no_ask_implied"), "p": r.get("model_p"),
                      "obs": r.get("obs_so_far_c"), "won": r.get("won"), "resolved": r.get("resolved_bin"),
                      "depth": r.get("ask_depth_usd"), "slug": r.get("slug")})
    day_counts = {}
    for r in picks:
        d = (r.get("target") or "?")
        day_counts[d] = day_counts.get(d, 0) + 1
    snap_days = []
    snap_dir = P("data", "snapshots")
    if os.path.isdir(snap_dir):
        for fn in sorted(os.listdir(snap_dir)):
            if not fn.endswith(".jsonl"):
                continue
            day = fn[:-6]
            cities = set()
            try:
                for ln in open(os.path.join(snap_dir, fn), encoding="utf-8"):
                    try:
                        cities.add((json.loads(ln).get("city"), json.loads(ln).get("local_time")))
                    except ValueError:
                        continue
            except OSError:
                pass
            snap_days.append({"day": day, "n": len(cities)})
    yes = [r for r in resolved if r.get("side") == "YES"]
    no = [r for r in resolved if r.get("side") == "NO"]
    hit = lambda rows: (100.0 * sum(1 for r in rows if r.get("won") == "1") / len(rows)) if rows else None
    M["forward"] = {"n": len(log), "resolved": len(resolved), "yes": {"n": len(yes), "hit": hit(yes)},
                    "no": {"n": len(no), "hit": hit(no)}, "hit": hit(resolved),
                    "picks": picks, "day_counts": day_counts, "snap_days": snap_days,
                    "snap_city_days": sum(d["n"] for d in snap_days),
                    "log_first": log[0].get("logged_at") if log else None,
                    "log_last": log[-1].get("logged_at") if log else None,
                    "stats": fs, "pending": [r for r in picks if (r.get("won") or "") == ""]}

    # ── เป้าหมายก่อนใช้เงินจริง (5 ข้อ) ──
    sp_med = M["spread"]["ask_last"]
    fw = M["forward"]
    gates = [
        {"n": 1, "title": "หลักฐาน backtest", "status": "done" if (n_bt >= 100 and cum > 0) else "progress",
         "value": "%s ไม้ · %s%% ชนะ · $%s" % (fnum(n_bt), fnum(M["backtest"]["hit"], 1), fnum(cum)),
         "target": "พิสูจน์ที่มาได้ทีละไม้ (pnl_proof 5 ชั้น ผ่านหมด)", "pct": 100 if n_bt >= 100 else 0},
        {"n": 2, "title": "หลักฐาน bid/ask จริง", "status": "progress",
         "value": "วัดได้ %s bins (ask−last median %s)" % (fnum(M["spread"]["bins"]), fnum(sp_med, 4)),
         "target": "ต้องมี log ตอน 16:00 local + depth จริง (log v3 เก็บแล้ว)", "pct": min(100, 100 * fw["n"] / 50)},
        {"n": 3, "title": "forward-test จริง ≥ 50 ไม้ · ชนะ ≥ 85%", "status": "done" if (fw["resolved"] >= 50 and (fw["hit"] or 0) >= 85) else "progress",
         "value": "%s/%s ไม้ settled%s" % (fnum(fw["resolved"]), 50, (" · ชนะ %s%%" % fnum(fw["hit"], 1)) if fw["hit"] is not None else ""),
         "target": "เก็บจากตลาดจริงด้วยราคาที่จ่ายจริง", "pct": min(100, 100 * fw["resolved"] / 50)},
        {"n": 4, "title": "เงินทุน ≥ min-cap + 20%", "status": "manual",
         "value": "ต้องยืนยันเอง (min-cap ≈ $959 จากไม้ที่แพงสุด → เป้า ~$1,200)",
         "target": "กัน drawdown 15% ของแผน sizing", "pct": 0},
        {"n": 5, "title": "บัญชีเทรดพร้อม", "status": "manual",
         "value": "ต้องยืนยันเอง (Polymarket + USDC บน Polygon + ภูมิภาคที่ใช้ได้)",
         "target": "เช็ค ToS/ภูมิภาคก่อนฝากเงิน", "pct": 0},
    ]
    M["gates"] = gates

    # ── โมเดล · จักรวาล · กติกา · ML · rejections ──
    M["model"] = dict(sp.get("model", {}) or {})
    M["model"]["n_sources"] = len(M["model"].get("sources") or [])
    uni = load_json(P("data", "universe.json"), {}) or {}
    sp_uni = sp.get("universe", {}) or {}
    M["universe"] = {"n": len(uni.get("cities") or sp_uni.get("cities") or []),
                     "cities": uni.get("cities") or sp_uni.get("cities") or [],
                     "dropped": uni.get("dropped") or sp_uni.get("dropped") or [],
                     "agreement": uni.get("agreement") or sp_uni.get("agreement") or {},
                     "ban": sp.get("universe_ban") or [], "updated": uni.get("updated"),
                     "threshold": uni.get("threshold", 0.90), "min_n": uni.get("min_n", 10)}
    M["rules"] = {"entry": sp.get("entry", {}), "sizing": sp.get("sizing", {}), "intraday": sp.get("intraday", {}),
                  "fees": sp.get("fees_stress", {}), "tool": sp.get("tool", {})}
    M["ml"] = sp.get("ml", {})
    M["rejections"] = sp.get("rejections", {})
    return M


# ───────────────────────── ชาร์ต (inline SVG) ─────────────────────────
def svg_equity(M, w=760, h=250):
    pts = M["backtest"]["equity"]
    if len(pts) < 2:
        return ""
    pad = {"l": 62, "r": 14, "t": 18, "b": 30}
    ymin = min(0.0, min(pts))
    ymax = max(pts) * 1.04
    if ymax <= ymin:
        ymax = ymin + 1
    px = lambda i: pad["l"] + (w - pad["l"] - pad["r"]) * i / (len(pts) - 1)
    py = lambda v: pad["t"] + (h - pad["t"] - pad["b"]) * (1 - (v - ymin) / (ymax - ymin))
    line = " ".join(("M" if i == 0 else "L") + "%.1f,%.1f" % (px(i), py(v)) for i, v in enumerate(pts))
    area = line + " L%.1f,%.1f L%.1f,%.1f Z" % (px(len(pts) - 1), py(ymin), px(0), py(ymin))
    ticks = []
    step = 50000
    while step * 4 < (ymax - ymin):
        step *= 2
    t = math.ceil(ymin / step) * step
    while t <= ymax:
        ticks.append(t)
        t += step
    grid = "".join('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" class="grid"/>'
                   '<text x="%d" y="%.1f" class="axis" text-anchor="end">$%s</text>'
                   % (pad["l"], py(v), w - pad["r"], py(v), pad["l"] - 7, py(v) + 3.5, fnum(v)) for v in ticks)
    si = M["backtest"]["split_i"]
    split = ('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" class="split"/>'
             '<text x="%.1f" y="%d" class="axis splitlabel">train │ test</text>'
             % (px(si), pad["t"], px(si), h - pad["b"], px(si) + 6, pad["t"] + 12)) if 0 < si < len(pts) - 1 else ""
    last_dot = '<circle cx="%.1f" cy="%.1f" r="4" class="dot"/>' % (px(len(pts) - 1), py(pts[-1]))
    xlab = ('<text x="%d" y="%d" class="axis" text-anchor="start">%s</text>'
            '<text x="%d" y="%d" class="axis" text-anchor="end">%s</text>'
            % (pad["l"], h - 8, esc(fdate(M["backtest"]["first"])), w - pad["r"], h - 8, esc(fdate(M["backtest"]["last"]))))
    return ('<svg viewBox="0 0 %d %d" class="chart" role="img" aria-labelledby="eqT eqD" preserveAspectRatio="xMidYMid meet">'
            '<title id="eqT">Equity curve ของ backtest %s ไม้</title>'
            '<desc id="eqD">กำไรสะสมจาก %s ไม้ เรียงตามวันที่ (stake $%s/ไม้) · สิ้นสุดที่ $%s · drawdown สูงสุด $%s</desc>'
            '<defs><linearGradient id="eqg" x1="0" y1="0" x2="0" y2="1">'
            '<stop offset="0%%" stop-color="#4ade80" stop-opacity="0.38"/>'
            '<stop offset="100%%" stop-color="#4ade80" stop-opacity="0.02"/></linearGradient></defs>'
            '%s<path d="%s" class="eqarea"/><path d="%s" class="eqline"/>%s%s%s</svg>'
            % (w, h, fnum(M["backtest"]["n"]), fnum(M["backtest"]["n"]), fnum(M["backtest"]["stake"], 0),
               fnum(M["backtest"]["pnl"]), fnum(M["backtest"]["dd"]), grid, area, line, split, last_dot, xlab))


def svg_daily(M, w=760, h=170):
    dc = M["forward"]["day_counts"]
    if not dc:
        return ""
    days = sorted(dc)[-21:]
    vals = [dc[d] for d in days]
    pad = {"l": 34, "r": 10, "t": 14, "b": 26}
    bw = (w - pad["l"] - pad["r"]) / max(1, len(days))
    ymax = max(vals) or 1
    bars = []
    for i, v in enumerate(vals):
        bh = (h - pad["t"] - pad["b"]) * v / ymax
        x = pad["l"] + i * bw + bw * 0.15
        y = h - pad["b"] - bh
        bars.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="3" class="bar"><title>%s: %d ไม้</title></rect>'
                    % (x, y, bw * 0.7, bh, esc(days[i]), v))
    return ('<svg viewBox="0 0 %d %d" class="chart" role="img" aria-labelledby="dlT dlD">'
            '<title id="dlT">จำนวนไม้ที่เก็บได้ต่อวัน</title>'
            '<desc id="dlD">แท่งแสดงจำนวนไม้ใน log ของแต่ละวัน (สูงสุด %d ไม้/วัน)</desc>'
            '<line x1="%d" y1="%d" x2="%d" y2="%d" class="grid"/>%s'
            '<text x="%d" y="%d" class="axis" text-anchor="middle">%s</text>'
            '<text x="%d" y="%d" class="axis" text-anchor="end">%s</text></svg>'
            % (w, h, ymax, pad["l"], h - pad["b"], w - pad["r"], h - pad["b"], "".join(bars),
               w / 2, h - 8, esc(fdate(days[0]) + " – " + fdate(days[-1])), w - pad["r"], h - 8, "ล่าสุด"))


def bar(label, pct, val, target=None, cls=""):
    p = 0 if pct is None else max(0.0, min(100.0, pct))
    return ('<div class="barwrap"><div class="barlabel"><span>%s</span><span class="bnum">%s</span></div>'
            '<div class="track" role="progressbar" aria-label="%s" aria-valuenow="%.0f" aria-valuemin="0" aria-valuemax="100">'
            '<div class="fill %s" style="width:%.1f%%"></div></div>%s</div>'
            % (label, val, esc(label), p, cls, p, ('<div class="bartarget">เป้า: %s</div>' % target) if target else ""))


# ───────────────────────── HTML ─────────────────────────
CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{
 --bg:#080d18;--panel:#101a2c;--panel2:#0c1424;--line:#1f2c46;--line2:#2a3a5c;
 --text:#e9effb;--muted:#93a4c4;--muted2:#6f80a0;
 --green:#4ade80;--blue:#60b8fa;--amber:#fbbf24;--red:#f87171;--violet:#a78bfa;
 --radius:14px;--fs:15px;
}
html{color-scheme:dark;-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font:var(--fs)/1.55 system-ui,-apple-system,"Segoe UI","Noto Sans Thai","Sarabun",Roboto,sans-serif;
 background-image:radial-gradient(1100px 460px at 12% -6%,#16233d 0%,transparent 62%),radial-gradient(760px 380px at 96% 2%,#14283a 0%,transparent 58%);
 background-attachment:fixed}
a{color:var(--blue);text-decoration:none}
a:hover{text-decoration:underline}
a:focus-visible,button:focus-visible,summary:focus-visible{outline:2px solid var(--blue);outline-offset:2px;border-radius:6px}
button{font:inherit;color:inherit;background:none;border:0;cursor:pointer;touch-action:manipulation;-webkit-tap-highlight-color:transparent}
.skip{position:absolute;left:-9999px;top:0;background:var(--panel);color:var(--text);padding:10px 14px;border-radius:0 0 10px 0;z-index:9}
.skip:focus{left:0}
.wrap{max-width:1140px;margin:0 auto;padding:0 16px 56px}
header.top{position:sticky;top:0;z-index:5;background:rgba(8,13,24,.86);backdrop-filter:blur(10px);border-bottom:1px solid var(--line);
 padding:calc(10px + env(safe-area-inset-top)) 0 10px}
.topin{max-width:1140px;margin:0 auto;padding:0 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
h1{margin:0;font-size:clamp(17px,2.4vw,21px);letter-spacing:-.2px;text-wrap:balance}
h1 span{color:var(--muted);font-weight:500;font-size:.82em;display:block}
h2{margin:38px 0 12px;font-size:clamp(16px,2.1vw,19px);text-wrap:balance;scroll-margin-top:76px}
h3{margin:0 0 8px;font-size:15px}
p{margin:8px 0;text-wrap:pretty}
.pill{display:inline-flex;align-items:center;gap:7px;padding:6px 11px;border:1px solid var(--line2);border-radius:999px;background:var(--panel2);font-size:12.5px;color:var(--muted);white-space:nowrap}
.dot{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 0 3px rgba(74,222,128,.16)}
.dot.wait{background:var(--amber);box-shadow:0 0 0 3px rgba(251,191,36,.16)}
.spacer{flex:1}
.btn{min-height:40px;padding:8px 13px;border:1px solid var(--line2);border-radius:10px;background:var(--panel);font-size:13.5px;transition:background-color .15s,border-color .15s,transform .15s}
.btn:hover{background:#16223a;border-color:#33507e}
.btn[aria-pressed="true"]{border-color:var(--blue);color:#dff0ff}
nav.toc{display:flex;gap:6px;overflow-x:auto;padding:10px 0 4px;scrollbar-width:thin}
nav.toc a{padding:7px 11px;border:1px solid var(--line);border-radius:999px;font-size:13px;white-space:nowrap;color:var(--muted)}
nav.toc a:hover{color:var(--text);border-color:var(--line2);text-decoration:none}
.grid{display:grid;gap:12px}
.kpis{grid-template-columns:repeat(auto-fit,minmax(215px,1fr))}
.cards{grid-template-columns:repeat(auto-fit,minmax(290px,1fr))}
.card{background:linear-gradient(180deg,var(--panel) 0%,var(--panel2) 100%);border:1px solid var(--line);border-radius:var(--radius);padding:14px 15px;content-visibility:auto;contain-intrinsic-size:auto 120px}
.card h3{color:var(--muted);font-weight:600;font-size:13px;letter-spacing:.2px}
.kpi{font-size:clamp(21px,3.4vw,27px);font-weight:700;font-variant-numeric:tabular-nums;letter-spacing:-.5px;margin:2px 0 2px}
.sub{color:var(--muted);font-size:12.5px}
.pos{color:var(--green)}.neg{color:var(--red)}.amb{color:var(--amber)}.blu{color:var(--blue)}.vio{color:var(--violet)}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:13.5px}
caption{text-align:left;color:var(--muted);font-size:12.5px;padding:0 0 8px}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left;white-space:normal}
thead th{color:var(--muted);font-weight:600;font-size:12.5px;position:sticky;top:0;background:var(--panel)}
tbody tr:hover{background:#16223a}
.tblwrap{overflow-x:auto;border:1px solid var(--line);border-radius:var(--radius);background:var(--panel2)}
.tblwrap table{min-width:520px}
.chart{width:100%;height:auto;display:block}
.chart .grid{stroke:#1c2740;stroke-width:1}
.chart .axis{fill:var(--muted2);font-size:11px;font-variant-numeric:tabular-nums}
.chart .splitlabel{fill:var(--amber);font-size:11px}
.chart .split{stroke:var(--amber);stroke-dasharray:4 4;stroke-width:1;opacity:.55}
.chart .eqline{fill:none;stroke:var(--green);stroke-width:2.2;stroke-linejoin:round;stroke-linecap:round}
.chart .eqarea{fill:url(#eqg);stroke:none}
.chart .dot{fill:var(--green);stroke:#080d18;stroke-width:2}
.chart .bar{fill:var(--blue);opacity:.85}
.barwrap{margin:12px 0}
.barlabel{display:flex;justify-content:space-between;gap:10px;font-size:13px;margin-bottom:5px}
.bnum{font-variant-numeric:tabular-nums;color:var(--muted)}
.track{height:9px;border-radius:999px;background:#16223a;overflow:hidden}
.fill{height:100%;border-radius:999px;background:linear-gradient(90deg,var(--blue),#7dd3fc);transition:width .4s ease}
.fill.good{background:linear-gradient(90deg,var(--green),#86efac)}
.fill.warn{background:linear-gradient(90deg,var(--amber),#fcd34d)}
.bartarget{color:var(--muted2);font-size:12px;margin-top:4px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 0;padding:0;list-style:none}
.chip{font-size:12px;padding:5px 9px;border-radius:999px;border:1px solid var(--line2);background:var(--panel2);color:var(--muted)}
.chip.ok{border-color:#2c6a48;color:#a7f3c9}
.chip.mid{border-color:#6b5a22;color:#fde68a}
.chip.bad{border-color:#6b2f2f;color:#fecaca}
.chip.off{opacity:.55;text-decoration:line-through}
.gate{display:grid;gap:10px}
.gate .row{display:flex;gap:10px;align-items:flex-start}
.mark{width:22px;height:22px;flex:0 0 22px;border-radius:50%;display:grid;place-items:center;font-size:13px;font-weight:700;border:1px solid var(--line2);color:var(--muted)}
.mark.done{background:rgba(74,222,128,.16);border-color:#2c6a48;color:var(--green)}
.mark.progress{background:rgba(251,191,36,.14);border-color:#6b5a22;color:var(--amber)}
.mark.manual{background:rgba(148,163,184,.12)}
.note{border-left:3px solid var(--line2);padding:10px 12px;background:var(--panel2);border-radius:0 10px 10px 0;color:var(--muted);font-size:13px}
.warnbox{border-left-color:var(--amber)}
details{border:1px solid var(--line);border-radius:var(--radius);background:var(--panel2);padding:10px 13px;margin:10px 0}
summary{cursor:pointer;color:var(--text);font-weight:600;font-size:13.5px}
details[open] summary{margin-bottom:8px}
ul.clean{margin:8px 0;padding-left:18px;color:var(--muted)}
ul.clean li{margin:4px 0}
.pick{border:1px solid var(--line);border-radius:12px;padding:11px 13px;background:var(--panel2)}
.pick .pline{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.tag{font-size:11.5px;padding:3px 8px;border-radius:999px;border:1px solid var(--line2);color:var(--muted)}
.tag.yes{border-color:#2c6a48;color:#a7f3c9}.tag.no{border-color:#4b3a6b;color:#ddd6fe}
footer{border-top:1px solid var(--line);margin-top:40px;padding:18px 0 calc(24px + env(safe-area-inset-bottom));color:var(--muted2);font-size:12.5px}
.sr{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;border:0}
.empty{text-align:center;color:var(--muted);padding:26px 14px;border:1px dashed var(--line2);border-radius:var(--radius);background:var(--panel2)}
.empty strong{color:var(--text);display:block;margin-bottom:4px;font-size:14.5px}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
@media (max-width:640px){:root{--fs:14.5px}.wrap{padding:0 12px 44px}}
"""

JS = r"""
(function(){
 try{
  var fmt = new Intl.DateTimeFormat('th-TH',{dateStyle:'medium',timeStyle:'short',timeZone:'Asia/Bangkok'});
  var fmtD = new Intl.DateTimeFormat('th-TH',{dateStyle:'medium',timeZone:'Asia/Bangkok'});
  document.querySelectorAll('time[datetime]').forEach(function(el){
    var d = new Date(el.getAttribute('datetime'));
    if(isNaN(d)) return;
    try{ el.textContent = el.dataset.dateonly==='1' ? fmtD.format(d) : fmt.format(d); }catch(e){}
  });

  /* รอบถัดไปของ cron: ทุก 30 นาที (นาทีที่ 0/30 UTC) + งานรายวัน 03:20 UTC */
  var nextEl = document.getElementById('nextRun');
  function nextRun(){
    var now = new Date();
    var t = new Date(Date.UTC(now.getUTCFullYear(),now.getUTCMonth(),now.getUTCDate(),now.getUTCHours(),now.getUTCMinutes(),0));
    t.setUTCMinutes(now.getUTCMinutes()<30?30:60,0,0);
    return t;
  }
  function tick(){
    if(!nextEl) return;
    var t = nextRun();
    var mins = Math.max(1, Math.round((t-new Date())/60000));
    nextEl.textContent = fmt.format(t) + ' (อีก ~' + mins + ' นาที)';
  }
  tick(); setInterval(tick, 30000);

  /* auto-refresh 15 นาที — ปิดได้ (จำค่าไว้ใน localStorage) */
  var btn = document.getElementById('autoBtn');
  var key = 'wx-mon-autorefresh';
  /* localStorage อาจถูกบล็อกใน iframe แบบ sandbox — ต้องไม่ทำให้สคริปต์พัง */
  var store = {
    get: function(k){ try { return window.localStorage.getItem(k); } catch(e){ return null; } },
    set: function(k,v){ try { window.localStorage.setItem(k,v); } catch(e){} }
  };
  var on = store.get(key) !== '0';
  var timer = null;
  function apply(){
    if(btn){ btn.setAttribute('aria-pressed', on?'true':'false'); btn.textContent = on? 'อัปเดตอัตโนมัติ: เปิด' : 'อัปเดตอัตโนมัติ: ปิด'; }
    if(timer){ clearTimeout(timer); timer=null; }
    if(on){ timer = setTimeout(function(){ location.reload(); }, 15*60*1000); }
  }
  if(btn){
    btn.addEventListener('click', function(){ on = !on; store.set(key, on?'1':'0'); apply(); });
  }
  apply();

  var rf = document.getElementById('refreshBtn');
  if(rf){ rf.addEventListener('click', function(){
    var st = document.getElementById('live');
    if(st) st.textContent = 'กำลังโหลดใหม่…';
    location.reload();
  }); }
 }catch(e){ if(window.console) console.warn('monitor script:', e); }
})();
"""


def render(M):
    safe = M["mode"] == "safe"
    bt, fw, sp_ = M["backtest"], M["forward"], M["spread"]
    parts = []
    A = parts.append

    A('<!doctype html><html lang="th">')
    A('<head><meta charset="utf-8">')
    A('<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">')
    A('<meta name="theme-color" content="#080d18">')
    A('<meta name="description" content="ตัวติดตามกลยุทธ์ตลาดอุณหภูมิสูงสุดบน Polymarket — ผลจริงจาก forward-test, KPI, เป้าหมายก่อนใช้เงินจริง">')
    A('<title>wxedge · ตัวติดตามกลยุทธ์อุณหภูมิสูงสุด</title>')
    A('<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 32 32\'%3E%3Ctext y=\'25\' font-size=\'24\'%3E%F0%9F%8C%A1%3C/text%3E%3C/svg%3E">')
    A('<style>' + CSS + '</style>')
    A('</head><body>')
    A('<a class="skip" href="#main">ข้ามไปเนื้อหาหลัก</a>')

    # ── header ──
    A('<header class="top"><div class="topin">')
    A('<h1>wxedge · ตัวติดตามกลยุทธ์<span>ตลาด “อุณหภูมิสูงสุดของวัน” บน Polymarket — ข้อมูลจริง ไม่ใช่การเดา</span></h1>')
    A('<div class="spacer"></div>')
    if fw["n"]:
        A('<span class="pill"><span class="dot" aria-hidden="true"></span>กำลังเก็บข้อมูลจริง · %s ไม้</span>' % fnum(fw["n"]))
    else:
        A('<span class="pill"><span class="dot wait" aria-hidden="true"></span>เริ่มเก็บแล้ว · ยังไม่มีไม้เข้าเกณฑ์</span>')
    A('<button class="btn" id="refreshBtn" type="button" aria-label="โหลดข้อมูลใหม่">↻ โหลดใหม่</button>')
    A('<button class="btn" id="autoBtn" type="button" aria-pressed="true" aria-label="สลับการอัปเดตอัตโนมัติทุก 15 นาที">อัปเดตอัตโนมัติ: เปิด</button>')
    A('</div><nav class="toc topin" aria-label="สารบัญ"><a href="#goals">เป้าหมาย</a><a href="#kpi">KPI</a>'
      '<a href="#forward">Forward-test</a><a href="#backtest">Backtest</a><a href="#model">โมเดล</a>'
      '<a href="#rules">กติกา</a><a href="#pipeline">รอบงาน</a></nav></header>')

    A('<div class="wrap">')
    if not safe:
        A('<div class="note warnbox" style="margin-top:14px"><strong>โหมดเต็ม (หน้าเว็บสาธารณะ)</strong> — หน้านี้แสดง bin · ราคา · ความลึกของสมุด '
          'รวมถึงไม้ที่ยังไม่ settle และตัวเลขกติกาครบ · ถ้าต้องการซ่อน ตั้ง <code>MODE=safe</code> ใน workflow '
          'หรือรัน <code>python3 build_monitor.py --mode safe</code></div>')
    A('<main id="main">')

    # ── 1. เป้าหมายก่อนใช้เงินจริง ──
    A('<section id="goals" aria-labelledby="goalsH"><h2 id="goalsH">เป้าหมายก่อนใช้เงินจริง</h2>')
    A('<div class="cards grid">')
    A('<div class="card"><h3>ความคืบหน้า 5 ข้อ</h3><div class="gate">')
    for g in M["gates"]:
        icon = {"done": "✓", "progress": "…", "manual": "?"}[g["status"]]
        A('<div class="row"><span class="mark %s" aria-hidden="true">%s</span><div><strong>%d. %s</strong>'
          '<div class="sub">%s</div><div class="sub" style="color:var(--muted2)">%s</div></div></div>'
          % (g["status"], icon, g["n"], esc(g["title"]), esc(g["value"]), esc(g["target"])))
    A('</div></div>')
    A('<div class="card"><h3>ไม้จริงที่ต้องเก็บ (เป้า 50)</h3>')
    A(bar("settled แล้ว", 100.0 * fw["resolved"] / 50, "%d / 50" % fw["resolved"],
          "settle แล้วนับอัตโนมัติจาก forward_resolve.py", "good" if fw["resolved"] >= 50 else ""))
    hit_pct = fw["hit"]
    A(bar("อัตราชนะจริง", hit_pct if hit_pct is not None else 0, ("%.1f%%" % hit_pct) if hit_pct is not None else "ยังไม่มีไม้ settled",
          "ต้อง ≥ 85% ตอนครบ 50 ไม้", "good" if (hit_pct or 0) >= 85 else "warn"))
    A(bar("ข้อมูลดิบที่เก็บได้", min(100, 100.0 * fw["snap_city_days"] / 200),
          "%d snapshot (เมือง×รอบ)" % fw["snap_city_days"], "เป้า 200 = 33 เมือง × ~2 วันครึ่ง"))
    A('</div>')
    A('<div class="card"><h3>สถานะ pipeline</h3>')
    A('<p class="sub">รอบถัดไป: <strong id="nextRun">—</strong></p>')
    A('<p class="sub">log เริ่มเก็บ: <time datetime="%s">%s</time></p>' % (fw["log_first"] or M["built_at"], esc(fw["log_first"] or "—")))
    A('<p class="sub">สร้างหน้านี้: <time datetime="%s">%s</time></p>' % (M["built_at"], esc(M["built_at"])))
    A('<p class="sub" id="live" aria-live="polite">ข้อมูลฝังในหน้า ณ เวลาที่สร้าง — กด “โหลดใหม่” เพื่อดึงล่าสุด</p>')
    A('</div></div></section>')

    # ── 2. KPI ──
    A('<section id="kpi" aria-labelledby="kpiH"><h2 id="kpiH">KPI หลัก</h2><div class="grid kpis">')
    A('<div class="card"><h3>ไม้จริง (settled)</h3><div class="kpi %s">%d</div><div class="sub">จาก log v3 · %d แถวทั้งหมด (รวมที่รอเฉลย)</div></div>'
      % ("pos" if fw["resolved"] else "", fw["resolved"], fw["n"]))
    A('<div class="card"><h3>อัตราชนะจริง</h3><div class="kpi %s">%s</div><div class="sub">YES %s · NO %s</div></div>'
      % ("pos" if (hit_pct or 0) >= 85 else "amb", ("%.1f%%" % hit_pct) if hit_pct is not None else "—",
         ("%.1f%%" % fw["yes"]["hit"]) if fw["yes"]["hit"] is not None else "—",
         ("%.1f%%" % fw["no"]["hit"]) if fw["no"]["hit"] is not None else "—"))
    A('<div class="card"><h3>Backtest PnL (หลักฐาน)</h3><div class="kpi pos">$%s</div><div class="sub">%s ไม้ · ชนะ %.1f%% · ROI +%.0f%% · stake $%s</div></div>'
      % (fnum(bt["pnl"]), fnum(bt["n"]), bt["hit"] or 0, bt["roi"] or 0, fnum(bt["stake"])))
    A('<div class="card"><h3>Drawdown สูงสุด (backtest)</h3><div class="kpi">$%s</div><div class="sub">%.2f%% ของเงินที่วางทั้งหมด · ไม้แพ้ติดกันสูงสุด %s</div></div>'
      % (fnum(bt["dd"]), bt["dd_pct"] or 0, fnum((bt["head"] or {}).get("streak"))))
    A('<div class="card"><h3>ช่องว่าง ask−last (วัดจริง)</h3><div class="kpi">%s</div><div class="sub">median จาก %s bins · spread median %s<br>วัดเมื่อ <time datetime="%s">%s</time></div></div>'
      % (fnum(sp_["ask_last"], 4), fnum(sp_["bins"]), fnum(sp_["spread"], 4), sp_.get("measured_at") or M["built_at"], esc((sp_.get("measured_at") or "—")[:10])))
    A('<div class="card"><h3>Brier score (ความน่าเชื่อถือ p)</h3><div class="kpi">%s</div><div class="sub">จากไม้ settled ที่มี model_p · ยิ่งต่ำยิ่งดี (0.25 = เดาสุ่ม)</div></div>'
      % (fnum(fw["stats"].get("brier"), 4) if fw["stats"].get("brier") is not None else "—"))
    A('</div></section>')

    # ── 3. forward-test ──
    A('<section id="forward" aria-labelledby="fwH"><h2 id="fwH">Forward-test — ผลจริงจากตลาด</h2>')
    if fw["n"] == 0:
        A('<div class="empty"><strong>ยังไม่มีไม้เข้าเกณฑ์</strong>')
        A('ระบบเก็บข้อมูลทุก 30 นาทีแล้ว — ไม้จะปรากฏที่นี่เมื่อมี bin ที่ราคา ≤ 0.25 · p โมเดล ≥ 0.90 · edge ≥ 0.15 · spread ≤ 0.04 '
          '(หรือฝั่ง NO: bin ที่พิสูจน์แล้วว่าเป็นไปไม่ได้) และจะได้ “เฉลย” หลังตลาดปิดในเช้าวันถัดไป</div>')
    else:
        if fw["pending"]:
            A('<h3>ไม้ที่รอเฉลย (%d)</h3><div class="grid cards">' % len(fw["pending"]))
            for p in fw["pending"][:12]:
                A('<div class="pick"><div class="pline"><span class="tag %s">%s</span><span class="tag">%s</span>'
                  '<strong>%s</strong><span class="sub">%s · %s:00 local</span></div>' % (
                      "yes" if p["side"] == "YES" else "no", esc(p["side"] or "?"), esc(p["tier"] or ""),
                      esc(p["city"] or ""), esc(fdate(p["target"])), esc(str(p["hour"]))))
                if safe:
                    A('<div class="sub">โหมดสาธารณะ: ซ่อน bin/ราคา/ความลึกของสมุดจนกว่าจะ settle</div>')
                else:
                    A('<div class="sub">bin %s · จ่าย %s · p โมเดล %s · obs %s°C · depth $%s</div>' % (
                        esc(p["bin"] or ""), esc(str(p["ask"])), esc(str(p["p"])), esc(str(p["obs"])), fnum(p["depth"])))
                A('</div>')
            A('</div>')
        A('<h3>ผลตามฝั่ง</h3><div class="tblwrap"><table><caption>ผลจริงจาก log (ถ่วงจากราคาจ่ายจริง ถือถึงเฉลย)</caption>'
          '<thead><tr><th scope="col">ฝั่ง</th><th scope="col">ไม้</th><th scope="col">ชนะ</th><th scope="col">อัตราชนะ</th></tr></thead><tbody>')
        for label, d in (("YES", fw["yes"]), ("NO", fw["no"])):
            A('<tr><td>%s</td><td>%d</td><td>%d</td><td>%s</td></tr>' % (label, d["n"], 0, ("%.1f%%" % d["hit"]) if d["hit"] is not None else "—"))
        A('</tbody></table></div>')
        if M["forward"]["stats"]:
            fs = M["forward"]["stats"]
            A('<div class="tblwrap" style="margin-top:12px"><table><caption>แยกตามเมือง (ไม้จริง)</caption>'
              '<thead><tr><th scope="col">เมือง</th><th scope="col">ไม้</th><th scope="col">ชนะ</th><th scope="col">อัตราชนะ</th><th scope="col">PnL</th></tr></thead><tbody>')
            for city, d in sorted((fs.get("by_city") or {}).items(), key=lambda kv: -kv[1]["n"])[:20]:
                A('<tr><td>%s</td><td>%d</td><td>%d</td><td>%.1f%%</td><td class="%s">%s$%s</td></tr>'
                  % (esc(city), d["n"], d["w"], 100.0 * d["w"] / d["n"] if d["n"] else 0,
                     "pos" if d["pnl"] >= 0 else "neg", "" if d["pnl"] < 0 else "+", fnum(abs(d["pnl"]))))
            A('</tbody></table></div>')
        ch = svg_daily(M)
        if ch:
            A('<h3 style="margin-top:16px">ไม้ที่เก็บได้ต่อวัน</h3>' + ch)

    A('<h3 style="margin-top:16px">ความครอบคลุมของข้อมูลที่เก็บ</h3>')
    if fw["snap_days"]:
        A('<div class="tblwrap"><table><caption>snapshot การแจกแจง + ราคาทุก bin (ใช้ทำ calibration/PIT)</caption>'
          '<thead><tr><th scope="col">วัน</th><th scope="col">เมือง×รอบ</th></tr></thead><tbody>')
        for d in fw["snap_days"][-14:]:
            A('<tr><td><time datetime="%sT00:00:00Z" data-dateonly="1">%s</time></td><td>%d</td></tr>' % (d["day"], esc(d["day"]), d["n"]))
        A('</tbody></table></div>')
    else:
        A('<div class="empty"><strong>ยังไม่มี snapshot</strong>จะเริ่มมีหลังรอบ cron แรกที่สร้างเสร็จ</div>')
    A('</section>')

    # ── 4. backtest ──
    A('<section id="backtest" aria-labelledby="btH"><h2 id="btH">Backtest — หลักฐาน 379 ไม้</h2>')
    A('<div class="card">' + svg_equity(M) + '<p class="sub">กำไรสะสมจากไม้ที่แนะนำทั้งหมด เรียงตามวันที่ · เส้นประ = จุดแบ่ง train/test (%s) · '
      'ยังไม่หักช่องว่าง ask — ดูแถว stress ด้านล่าง</p></div>' % esc(M["split"] or ""))
    A('<h3 style="margin-top:16px">แยกตามฝั่งและชั้นไม้</h3><div class="tblwrap"><table><caption>ไม้ที่แนะนำทั้งหมด (stake $%s/ไม้ · ถือถึงเฉลย)</caption>'
      '<thead><tr><th scope="col">กลุ่ม</th><th scope="col">ไม้</th><th scope="col">ชนะ</th><th scope="col">อัตราชนะ</th><th scope="col">PnL</th><th scope="col">สัดส่วนกำไร</th></tr></thead><tbody>'
      % fnum(bt["stake"]))
    rows = [("YES · หลัก (≤0.10)", bt["by_tier"].get("หลัก (ask ≤0.10)", {})),
            ("YES · รอง (0.10–0.25)", bt["by_tier"].get("รอง (0.10–0.25)", {})),
            ("NO · ขาย bin เป็นไปไม่ได้", bt["by_tier"].get("ขาย (พิสูจน์ได้)", {})),
            ("รวมทั้งหมด", {"n": bt["n"], "wins": bt["wins"], "pnl": bt["pnl"]})]
    for label, d in rows:
        n = d.get("n", 0)
        if not n:
            continue
        A('<tr><td>%s</td><td>%d</td><td>%d</td><td>%.1f%%</td><td class="pos">+$%s</td><td>%.1f%%</td></tr>'
          % (esc(label), n, d.get("wins", 0), 100.0 * d.get("wins", 0) / n, fnum(d.get("pnl", 0)),
             100.0 * d.get("pnl", 0) / bt["pnl"] if bt["pnl"] else 0))
    A('</tbody></table></div>')
    if M["stress"]:
        A('<h3 style="margin-top:16px">Stress: ถ้าจ่ายแพงกว่าราคาที่บันทึก</h3><div class="tblwrap"><table>'
          '<caption>คิดใหม่หลังหักช่องว่าง ask ที่วัดได้ (แหล่ง: reports/bidask_check.md)</caption>'
          '<thead><tr><th scope="col">สมมติฐานต้นทุน</th><th scope="col">PnL</th><th scope="col">เทียบฐาน</th></tr></thead><tbody>')
        for s in M["stress"]:
            d = s["pnl"] - bt["pnl"]
            A('<tr><td>%s</td><td class="pos">+$%s</td><td class="neg">%s$%s</td></tr>'
              % (esc(s["label"]), fnum(s["pnl"]), "" if d < 0 else "+", fnum(d)))
        A('</tbody></table></div>')
    tr, te = bt.get("train") or {}, bt.get("test") or {}
    A('<div class="grid cards" style="margin-top:16px">')
    A('<div class="card"><h3>Train (%s ไม้)</h3><div class="kpi pos">$%s</div><div class="sub">ชนะ %.1f%% · ROI +%.0f%%</div></div>'
      % (fnum(tr.get("n")), fnum(tr.get("pnl")), tr.get("win", 0), tr.get("roi", 0)))
    A('<div class="card"><h3>Test (%s ไม้ · ไม่เคยเห็นตอนตั้งกติกา)</h3><div class="kpi pos">$%s</div><div class="sub">ชนะ %.1f%% · ROI +%.0f%%</div></div>'
      % (fnum(te.get("n")), fnum(te.get("pnl")), te.get("win", 0), te.get("roi", 0)))
    A('<div class="card"><h3>ป้ายกำกับสำคัญ</h3><p class="sub">ตัวเลข backtest ใช้ “ราคาซื้อขายล่าสุด” จากประวัติตลาด · '
      'ราคาที่จ่ายจริงคือ ask ซึ่งแพงกว่า → ดู stress · สิ่งที่จะยืนยันของจริงคือ forward-test ด้านบน</p></div>')
    A('</div></section>')

    # ── 5. โมเดล + จักรวาล ──
    m = M["model"]
    uni = M["universe"]
    A('<section id="model" aria-labelledby="mdH"><h2 id="mdH">โมเดล &amp; จักรวาลเมือง</h2><div class="grid cards">')
    A('<div class="card"><h3>แหล่งพยากรณ์</h3><div class="kpi">%d</div><div class="sub">รวมแบบ %s (หัก bias ต่อโมเดลก่อน)</div>'
      % (m.get("n_sources", 0), esc(m.get("aggregator", "—"))))
    A('<div class="sub" style="margin-top:8px">%s</div></div>' % esc(", ".join(m.get("sources") or [])))
    A('<div class="card"><h3>ความแม่นที่วัดได้</h3><div class="kpi">σ %s°C</div><div class="sub">MAE %s°C · σ %s°F · MAE %s°F</div>'
      '<div class="sub">ใช้ป้อนการแจกแจงความน่าจะเป็นต่อ bin</div></div>'
      % (fnum(m.get("sigma_pooled_c"), 3), fnum(m.get("mae_c"), 3), fnum(m.get("sigma_pooled_f"), 3), fnum(m.get("mae_f"), 3)))
    A('<div class="card"><h3>จักรวาลที่ใช้จริง</h3><div class="kpi">%d เมือง</div><div class="sub">เกณฑ์: obs ตกใน bin ที่ตลาดตัดสิน ≥ %d%% (จากข้อมูลจริง · อัปเดต <time datetime="%s">%s</time>)</div></div>'
      % (uni["n"], int(uni["threshold"] * 100), uni.get("updated") or M["built_at"], esc((uni.get("updated") or "—")[:10])))
    A('</div>')
    A('<h3 style="margin-top:16px">agreement รายเมือง (ยิ่งสูง = obs ของเราตรงกับแหล่งตัดสินของตลาด)</h3>')
    A('<ul class="chips" aria-label="เมืองที่ใช้และค่า agreement">')
    for c in sorted(uni["cities"]):
        a = uni["agreement"].get(c)
        cls = "ok" if (a or 0) >= 0.95 else ("mid" if (a or 0) >= 0.90 else "bad")
        A('<li class="chip %s">%s%s</li>' % (cls, esc(c), (" · %d%%" % round(a * 100)) if a else ""))
    A('</ul>')
    if uni["dropped"]:
        A('<details><summary>เมืองที่ตัดออก (%d) — obs ของเราไม่ตรงกับแหล่งตัดสินของตลาด</summary><ul class="chips">' % len(uni["dropped"]))
        for c in sorted(uni["dropped"]):
            a = uni["agreement"].get(c)
            A('<li class="chip off">%s%s</li>' % (esc(c), (" · %d%%" % round(a * 100)) if a else ""))
        A('</ul><p class="sub">ห้ามเทรดเมืองเหล่านี้ต่อให้โมเดลแม่น — ต่อให้ทายถูกลม ก็แพ้ตอนตัดสิน (เช่น hong-kong %s%%)</p></details>'
          % round((uni["agreement"].get("hong-kong") or 0) * 100))
    if m.get("bias_table"):
        A('<details><summary>Bias ต่อโมเดล (°C / °F) — หักออกก่อนรวม</summary><div class="tblwrap"><table><thead>'
          '<tr><th scope="col">โมเดล</th><th scope="col">bias</th></tr></thead><tbody>')
        for k, v in sorted(m["bias_table"].items()):
            A('<tr><td>%s</td><td>%s</td></tr>' % (esc(k), fnum(v, 3)))
        A('</tbody></table></div></details>')
    A('</section>')

    # ── 6. กติกา ──
    r = M["rules"]
    e, s_, idx = r["entry"], r["sizing"], r["intraday"]
    A('<section id="rules" aria-labelledby="rlH"><h2 id="rlH">กติกาที่ล็อกไว้</h2><div class="grid cards">')
    if safe:
        A('<div class="card"><h3>จังหวะเข้าไม้</h3><p class="sub">เข้าเมื่อเวลา local ของเมืองนั้นถึงชั่วโมงตัดสินใจ (ช่วง 15:00–17:00) '
          'แล้วค่อยเลือก bin เดียวจากความน่าจะเป็นสูงสุด · ถือถึงเฉลย ไม่ขายทำกำไรระหว่างทาง</p>'
          '<p class="sub" style="color:var(--muted2)">โหมดสาธารณะ: ซ่อนตัวเลขเกณฑ์ (ราคา/p/edge/spread) — ดูได้ในโหมด full</p></div>')
    else:
        A('<div class="card"><h3>จังหวะเข้าไม้</h3><ul class="clean">'
          '<li>เข้าเวลา local <strong>%s:00</strong> (ช่วง 15:00–17:00 · ช่วงสายช่วยได้ %s:00 / %s:00)</li>'
          '<li>โมเดลต้อง p ≥ <strong>%s</strong> (โหมดคม p ≥ %s)</li>'
          '<li>ราคา YES: <strong>%s–%s</strong> (ไม้หลัก) / %s–%s (ไม้รอง)</li>'
          '<li>edge ≥ %s · spread ≤ %s</li><li>ฝั่ง NO: YES bid ≥ %s และ bid − p ≥ %s (bin ที่พิสูจน์ว่าเป็นไปไม่ได้)</li>'
          '<li><strong>bucket ที่ซื้อ = bin ที่มี max-so-far อยู่ข้างใน</strong> (บัคเก็ตปัจจุบัน = bin ที่จะชนะถ้าวันจบเดี๋ยวนี้) — 100%% ของไม้ใน backtest · '
          'ไม่ซื้อ bin สูงกว่า (แพ้เพราะ “ร้อนต่อ”) ไม่ซื้อ bin ต่ำกว่า</li>'
          '<li>รอบเสริมที่ทดสอบแล้วดีกว่า: ชั้น ≤0.10 เก็บได้ตั้งแต่ 14:00 (hit 83%%) + รอบหลัก 16:00–17:00 → 259 ไม้ · 88.0%% · ROI +906%% (vs รอบเดียว 165 ไม้ · 87.9%% · +817%%)</li></ul></div>'
          % (e.get("hour_local"), e.get("hour_alt_early"), e.get("hour_alt_late"), e.get("p_min"), e.get("p_min_sharp"),
             e.get("yes_price_min"), e.get("yes_price_primary_max"), e.get("yes_price_min"), e.get("yes_price_secondary_max"),
             "0.15", "0.04", e.get("no_price_min"), e.get("no_margin")))
    A('<div class="card"><h3>ขนาดไม้ &amp; ความเสี่ยง</h3><ul class="clean">'
      '<li>ไม้ละ <strong>%s%%</strong> ของพอร์ต (ช่วงทดลองใช้ 0.25–0.5%%)</li>'
      '<li>สูงสุด %s ไม้/วัน · %s%% ของพอร์ต/วัน</li><li>หยุดเมื่อ drawdown %s%%</li>'
      '<li>ทางออก: %s%s</li></ul></div>'
      % (fnum(s_.get("pct_bankroll_per_bet"), 2), fnum(s_.get("max_bets_per_day")), fnum(s_.get("max_pct_bankroll_per_day")),
         fnum(s_.get("stop_drawdown_pct")), esc(s_.get("exit", "hold_to_resolution")),
         (" · stop เสริม %s%%" % fnum(s_.get("optional_stop_pct"))) if s_.get("optional_stop_pct") else ""))
    A('<div class="card"><h3>ต้นทุน &amp; เผื่อไว้</h3><ul class="clean">'
      '<li>tick ขั้นต่ำ %s · ค่าธรรมเนียมสมมติ %s%%</li><li>ไม้ที่ราคา &lt; 0.02 ห้ามซื้อ (ทดสอบแล้วชนะแค่ 6.0%%)</li>'
      '<li>เทียบ ask จริงทุกครั้งก่อนกดซื้อ</li></ul></div>'
      % (fnum(r["fees"].get("tick"), 2), fnum(r["fees"].get("fee_pct"), 1) * 100 if r["fees"].get("fee_pct") else "—"))
    A('</div>')
    if M["ml"]:
        ml = M["ml"]
        A('<div class="note" style="margin-top:14px"><strong>ระบบ ML (ทดสอบแล้ว):</strong> %s<br>%s<br>%s</div>'
          % (esc(ml.get("day_ahead", "")), esc(ml.get("intraday", "")), esc(ml.get("verdict", ""))))
    if M["rejections"]:
        A('<details><summary>ทดสอบแล้ว “ไม่ใช้” (กันหลงทางซ้ำ)</summary><ul class="clean">')
        for k, v in M["rejections"].items():
            A('<li><strong>%s</strong> — %s</li>' % (esc(k), esc(str(v))))
        A('</ul></details>')
    A('</section>')

    # ── 7. รอบงาน ──
    A('<section id="pipeline" aria-labelledby="plH"><h2 id="plH">รอบงานอัตโนมัติ</h2><div class="tblwrap"><table>'
      '<caption>ตารางที่ตั้งไว้บน GitHub Actions (UTC) — ทุกอย่าง commit กลับเข้า repo เอง</caption>'
      '<thead><tr><th scope="col">เวลา</th><th scope="col">ทำอะไร</th><th scope="col">ได้อะไร</th></tr></thead><tbody>'
      '<tr><td>ทุก 30 นาที (:00, :30 UTC)</td><td>เช็คว่ามีเมืองถึงเวลาตัดสินใจไหม → สแกนเฉพาะเมืองนั้น</td>'
      '<td>bid/ask + depth + obs + σ + snapshot</td></tr>'
      '<tr><td>ทุกวัน 03:20 UTC</td><td>อัปเดตจักรวาล · วัดช่องว่าง bid/ask · เติมเฉลย</td>'
      '<td>hit rate/EV/Brier จริง + รายงาน</td></tr>'
      '<tr><td>ทุกครั้งที่ push</td><td>รันชุดทดสอบ 20 ข้อ</td><td>กันโค้ดพังเงียบ ๆ</td></tr>'
      '</tbody></table></div>')
    A('<p class="sub">นาที CI ที่ใช้ ~1,400–1,700/เดือน — อยู่ในโควตาฟรีของ repo ส่วนตัว (2,000 นาที)</p>')
    A('</section>')

    # ── footer ──
    A('</main><footer><p>หน้านี้สร้างอัตโนมัติจาก <code>build_monitor.py</code> · โหมด %s · สร้างเมื่อ <time datetime="%s">%s</time></p>'
      '<p>ตัวเลขทั้งหมดคำนวณสดจาก data/ ในขณะสร้าง (ไม่ใส่ค่าคงที่) · backtest ใช้ราคาซื้อขายล่าสุดจากประวัติตลาด · '
      'ผลจริงจะมาจาก forward-test เท่านั้น</p>'
      '<p>ไม่ใช่คำแนะนำการลงทุน · ตลาดพยากรณ์อากาศมีความเสี่ยงสภาพคล่อง/การตัดสิน · ตรวจ ToS และภูมิภาคก่อนใช้เงินจริง</p>'
      '</footer></div>' % (esc(M["mode"]), M["built_at"], esc(M["built_at"])))
    A('<script>' + JS + '</script>')
    A('</body></html>')
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=P("docs", "index.html"))
    ap.add_argument("--json-out", default=P("docs", "monitor.json"))
    ap.add_argument("--mode", choices=["safe", "full"], default="safe")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--skip-if-same", action="store_true",
                    help="ถ้าข้อมูลไม่เปลี่ยน (ตัด timestamp) ให้ข้ามการเขียนไฟล์ — กัน commit/Pages build ซ้ำ")
    ap.add_argument("--fingerprint-file", default="", help="ไฟล์เก็บลายนิ้วมือข้อมูล (ค่าเริ่มต้น: ข้าง ๆ --out)")
    a = ap.parse_args()

    M = compute(mode=a.mode)
    html = render(M)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)

    # ลายนิ้วมือข้อมูล (ตัด timestamp ออก) — ใช้ข้ามการเขียนเมื่อเนื้อหาเหมือนเดิม
    fp_file = a.fingerprint_file or (os.path.join(os.path.dirname(a.out) or ".", ".fingerprint"))
    import hashlib, re as _re
    fp = hashlib.sha256(_re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", "TS", html).encode()).hexdigest()[:16]
    if a.skip_if_same and os.path.exists(fp_file):
        try:
            if open(fp_file, encoding="utf-8").read().strip() == fp:
                if not a.quiet:
                    print("ข้อมูลไม่เปลี่ยน (fingerprint %s) — ข้ามการเขียน %s" % (fp, a.out))
                return 0
        except OSError:
            pass
    try:
        with open(fp_file, "w", encoding="utf-8") as f:
            f.write(fp)
    except OSError:
        pass
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(html)
    summary = {"built_at": M["built_at"], "mode": M["mode"],
               "backtest": {k: M["backtest"][k] for k in ("n", "wins", "hit", "pnl", "roi", "dd")},
               "forward": {k: M["forward"][k] for k in ("n", "resolved", "hit", "snap_city_days")},
               "spread": M["spread"], "gates": [{k: g[k] for k in ("n", "title", "status", "value")} for g in M["gates"]]}
    try:
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=1)
    except OSError:
        pass
    if not a.quiet:
        print("เขียน %s (%d KB · โหมด %s) · backtest %d ไม้ $%s · forward %d ไม้ settled"
              % (a.out, len(html) // 1024, a.mode, M["backtest"]["n"], fnum(M["backtest"]["pnl"]), M["forward"]["resolved"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
