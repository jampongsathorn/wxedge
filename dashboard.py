#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dashboard.py — เว็บแดชบอร์ดสำหรับพอร์ต "อุณหภูมิสูงสุด" (Polymarket)
  • โชว์ไม้สดของวันนี้ (YES หลัก/รอง + NO ที่พิสูจน์ได้) จาก cheap_live v2
  • โชว์หลักฐานที่พิสูจน์แล้ว (winrate / EV / ROI / PnL / maxDD) + เส้นทุนสะสม (SVG inline)
  • โชว์ log จริง (data/cheap_live_log.csv) และ "ไม้ใกล้เข้าเกณฑ์" เมื่อยังไม่มีไม้
  • สแกนอัตโนมัติทุก 20 นาที + ปุ่มสแกนเอง (มีโหมดสาธิตย้อนหลัง 1 วัน)
รัน: python3 dashboard.py --port 8080        → http://0.0.0.0:8080
stdlib ล้วน ไม่ใช้ dependency ภายนอก · ไม่มีไฟล์ภายนอก (inline CSS/JS/SVG ทั้งหมด)
"""
import argparse, csv, json, os, sys, threading, time, traceback
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wxedge as W
import cheap_live as CL

PARAMS = os.path.join(HERE, "data", "strategy_params.json")
TRADES = os.path.join(HERE, "data", "trades_recommended.csv")
HOUR_WINDOW = (14, 19)
REFRESH_SEC = 1200

STATE = dict(results=[], scanned_at=None, scanning=False, mode=None, err="", universe=None,
             next_refresh=None, progress=None)
LOCK = threading.Lock()


def load_params():
    try:
        return json.load(open(PARAMS, encoding="utf-8"))
    except Exception:
        return {}


def universe_set():
    if STATE["universe"]:
        return STATE["universe"]
    u = CL.build_universe()
    STATE["universe"] = u or set()
    return STATE["universe"]


def equity_svg(width=560, height=120):
    """SVG เส้นทุนสะสมจากไฟล์ไม้จริง (inline ไม่พึ่ง library)"""
    try:
        rows = list(csv.DictReader(open(TRADES, encoding="utf-8")))
    except Exception:
        return ""
    byday = defaultdict(float)
    for r in rows:
        byday[r["date"]] += float(r["pnl"])
    if not byday:
        return ""
    days = sorted(byday)
    cum, vals = 0.0, []
    for d in days:
        cum += byday[d]
        vals.append(cum)
    lo, hi = min(0, min(vals)), max(vals) or 1
    span = (hi - lo) or 1
    pts = []
    for i, v in enumerate(vals):
        x = 4 + i * (width - 8) / max(1, len(vals) - 1)
        y = height - 6 - (v - lo) / span * (height - 16)
        pts.append("%.1f,%.1f" % (x, y))
    zero_y = height - 6 - (0 - lo) / span * (height - 16)
    return ("<svg viewBox='0 0 %d %d' width='100%%' height='%d' preserveAspectRatio='none'>"
            "<line x1='0' y1='%.1f' x2='%d' y2='%.1f' stroke='#2a3550' stroke-width='1'/>"
            "<polyline fill='none' stroke='#37d67a' stroke-width='2' points='%s'/></svg>"
            % (width, height, height, zero_y, width, zero_y, " ".join(pts)))


def scan(day_mode="today", max_workers=2):
    cfgs = json.load(open(W.STATIONS, encoding="utf-8"))
    univ = universe_set()
    now_utc = datetime.now(timezone.utc)
    if day_mode == "demo":
        day = (now_utc - timedelta(days=1)).date().isoformat()
        hour_override = 16
        cities = sorted(univ)[:8]
    else:
        day = now_utc.date().isoformat()
        hour_override = None
        cities = []
        for c in sorted(univ):
            t = datetime.now(ZoneInfo(cfgs[c]["tz"]))
            if HOUR_WINDOW[0] <= t.hour <= HOUR_WINDOW[1]:
                cities.append(c)
    out, lock = [], threading.Lock()
    total = len(cities)
    with LOCK:
        STATE["progress"] = "0/%d" % total

    def work(c):
        hour = hour_override or datetime.now(ZoneInfo(cfgs[c]["tz"])).hour
        r = None
        for attempt in range(3):
            try:
                r = CL.scan_city(c, cfgs[c], hour, 0.25, 0.15, 0.90, target=day)
                break
            except Exception as e:
                msg = str(e)
                if "429" in msg:
                    time.sleep(4 + 6 * attempt)
                    continue
                r = {"city": c, "error": "%s: %s" % (type(e).__name__, msg[:70])}
                break
        if r is None:
            r = {"city": c, "error": "429 Too Many Requests (ลอง 3 ครั้งแล้ว)"}
        with lock:
            out.append(r)
            with LOCK:
                STATE["progress"] = "%d/%d" % (len(out), total)

    queue, threads = list(cities), []
    while queue or any(t.is_alive() for t in threads):
        while queue and len([t for t in threads if t.is_alive()]) < max_workers:
            t = threading.Thread(target=work, args=(queue.pop(0),), daemon=True)
            t.start()
            threads.append(t)
        time.sleep(0.1)

    def sortkey(r):
        return (0 if (r.get("picks") or r.get("no_picks")) else 1, r.get("city", ""))
    return sorted(out, key=sortkey)


def refresh(day_mode="today"):
    with LOCK:
        if STATE["scanning"]:
            return
        STATE["scanning"] = True
    try:
        res = scan(day_mode)
        with LOCK:
            STATE["results"] = res
            STATE["scanned_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            STATE["mode"] = day_mode
            STATE["err"] = ""
            STATE["next_refresh"] = (datetime.now(timezone.utc) + timedelta(seconds=REFRESH_SEC)).strftime("%H:%M UTC")
    except Exception:
        with LOCK:
            STATE["err"] = traceback.format_exc(limit=2)
    finally:
        with LOCK:
            STATE["scanning"] = False
            STATE["progress"] = None


def bg_loop():
    refresh("today")
    while True:
        time.sleep(REFRESH_SEC)
        refresh("today")


def status_json():
    with LOCK:
        st = dict(scanned_at=STATE["scanned_at"], scanning=STATE["scanning"], mode=STATE["mode"],
                  err=STATE["err"], next_refresh=STATE["next_refresh"], progress=STATE["progress"],
                  results=list(STATE["results"]))
    st["universe"] = sorted(universe_set())
    st["trades"] = len(list(csv.DictReader(open(TRADES, encoding="utf-8")))) if os.path.exists(TRADES) else 0
    return st


def trades_tail(n=25):
    """ไม้ย้อนหลังจากไฟล์ที่พิสูจน์แล้ว (trades_recommended.csv) — ล่าสุดก่อน"""
    if not os.path.exists(TRADES):
        return []
    rows = list(csv.DictReader(open(TRADES, encoding="utf-8")))
    rows.sort(key=lambda r: (r["date"], r["city"]), reverse=True)
    out = []
    for r in rows[:n]:
        out.append(dict(date=r["date"], city=r["city"], side=r["side"], tier=r["tier"], bin=r["bin"],
                        entry=float(r["entry"]), won=int(r["won"]), pnl=float(r["pnl"])))
    return out


def log_tail(n=40):
    if not os.path.exists(CL.LOG):
        return []
    rows = list(csv.DictReader(open(CL.LOG, encoding="utf-8")))
    return rows[-n:][::-1]


CSS = """
*{box-sizing:border-box}body{margin:0;background:#0b1020;color:#e6ecff;
font:14px/1.5 -apple-system,'Segoe UI',Roboto,'Noto Sans Thai',sans-serif}
.wrap{max-width:1120px;margin:0 auto;padding:20px}
h1{font-size:20px;margin:0 0 2px}.sub{color:#8ea0c9;font-size:12px;margin-bottom:16px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:18px}
.card{background:#121a33;border:1px solid #1f2a4a;border-radius:12px;padding:12px}
.card .k{color:#8ea0c9;font-size:11px;letter-spacing:.03em}
.card .v{font-size:19px;font-weight:700;margin-top:4px}
.card .v.g{color:#37d67a}.card .v.b{color:#63a4ff}.card .v.y{color:#ffcf5c}
.panel{background:#121a33;border:1px solid #1f2a4a;border-radius:12px;padding:14px;margin-bottom:16px}
.panel h2{font-size:14px;margin:0 0 10px;color:#c7d4f5}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:7px 8px;text-align:left;border-bottom:1px solid #1c2742;white-space:nowrap}
th{color:#8ea0c9;font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.04em}
tr:hover td{background:#16203c}
.pill{display:inline-block;padding:1px 7px;border-radius:999px;font-size:11px;font-weight:600}
.pill.yes{background:#12351f;color:#54e08a;border:1px solid #1e5233}
.pill.no{background:#3a1e12;color:#ffa96b;border:1px solid #5a3220}
.pill.main{background:#123047;color:#7cc4ff;border:1px solid #1f4a6b}
.pill.watch{background:#2a2a3a;color:#c7c7d8;border:1px solid #3a3a52}
.muted{color:#8ea0c9}.err{color:#ff8b8b}.ok{color:#54e08a}
button{background:#1c3a6b;color:#e6ecff;border:1px solid #2b5aa5;border-radius:8px;
padding:7px 12px;font-size:13px;cursor:pointer}button:hover{background:#24508f}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
ul{margin:6px 0 0 18px;padding:0}li{margin:3px 0}
.foot{color:#6f80a8;font-size:11px;margin-top:14px}
"""

JS = r"""
function esc(s){return (s==null?'':String(s)).replace(/[&<>]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c]})}
function render(st){
  document.getElementById('when').textContent = st.scanned_at ? (st.scanned_at + ' · โหมด ' + st.mode) : 'ยังไม่สแกน';
  var rows = [], errs = 0;
  (st.results||[]).forEach(function(r){
    if (r.error){ errs++; return; }
    (r.picks||[]).forEach(function(p){
      rows.push([r.city, r.local_time, p.obs_so_far_c,
        '<span class="pill yes">YES</span>',
        '<span class="pill ' + (p.tier.indexOf('หลัก')>=0 ? 'main':'watch') + '">' + esc(p.tier) + '</span>',
        esc(p.bin), Number(p.ask).toFixed(3), Number(p.model_p).toFixed(2),
        (p.edge>=0?'+':'') + Number(p.edge).toFixed(2), '$' + Math.round(p.vol).toLocaleString(),
        '<button onclick="copyLine(\'YES\',\'' + esc(r.city) + '\',\'' + esc(r.target||'') + '\',\'' + esc(p.bin) + '\',' + p.ask + ')">คัดลอก</button>']);
    });
    (r.no_picks||[]).forEach(function(p){
      rows.push([r.city, r.local_time, p.max_so_far,
        '<span class="pill no">NO</span>', '<span class="pill no">ขาย bin เป็นไปไม่ได้</span>',
        esc(p.bin), Number(p.no_ask_implied).toFixed(3), Number(p.model_p).toFixed(2),
        (p.edge>=0?'+':'') + Number(p.edge).toFixed(2), '$' + Math.round(p.vol).toLocaleString(),
        '<button onclick="copyLine(\'NO\',\'' + esc(r.city) + '\',\'' + esc(r.target||'') + '\',\'' + esc(p.bin) + '\',' + p.no_ask_implied + ')">คัดลอก</button>']);
    });
    if ((r.picks||[]).length===0 && (r.no_picks||[]).length===0 && (r.screened||[]).length){
      var s0 = r.screened[0];
      rows.push([r.city, r.local_time, r.obs_so_far_c, '<span class="pill watch">เฝ้าดู</span>',
        '<span class="pill watch">bin ที่ p สูงสุด</span>', esc(s0.bin), Number(s0.ask).toFixed(3),
        Number(s0.p).toFixed(2), (s0.p < 0.90 ? 'p<0.90' : 'ราคานอกช่วง'), '—', '']);
    }
  });
  var h = '<table><tr><th>เมือง</th><th>เวลา local</th><th>obs สูงสุด</th><th>ฝั่ง</th><th>ชั้น</th><th>bin</th><th>ราคาที่จ่าย</th><th>p โมเดล</th><th>edge</th><th>vol</th><th></th></tr>';
  if (rows.length){ rows.forEach(function(r){ h += '<tr>' + r.map(function(c){ return '<td>' + c + '</td>'; }).join('') + '</tr>'; }); }
  else { h += '<tr><td colspan=11 class="muted">ยังไม่มีไม้เข้าเกณฑ์' + (errs ? ' (บางเมืองดึงข้อมูลไม่ได้: ' + errs + ' เมือง)' : '') + '</td></tr>'; }
  h += '</table>';
  document.getElementById('picks').innerHTML = h;
  document.getElementById('stat').textContent = st.scanning ? ('กำลังสแกน… ' + (st.progress||'')) :
    (st.next_refresh ? ('รีเฟรชถัดไป ' + st.next_refresh) : '');
}
function renderLog(rows){
  var h = '<table><tr><th>เวลา UTC</th><th>เมือง</th><th>วัน</th><th>ฝั่ง</th><th>bin</th><th>ราคา</th><th>p</th></tr>';
  if (rows.length){
    rows.forEach(function(r){
      h += '<tr><td>' + esc(r.logged_at) + '</td><td>' + esc(r.city) + '</td><td>' + esc(r.target) + '</td><td>' +
           esc(r.side||'YES') + '</td><td>' + esc(r.bin) + '</td><td>' +
           esc(r.no_ask_implied || r.yes_ask || r.ask || '') + '</td><td>' + esc(r.model_p) + '</td></tr>';
    });
  } else {
    h += '<tr><td colspan=7 class="muted">ยังไม่มี log — รัน python3 cheap_live.py --log เพื่อเก็บราคาจริง (ฝั่ง NO + bid/ask)</td></tr>';
  }
  h += '</table>';
  document.getElementById('log').innerHTML = h;
}
function copyLine(side, city, day, bin, price){
  var txt = side + ' ' + city + ' ' + day + ' | bin ' + bin + ' | จ่าย ' + price;
  if (navigator.clipboard) { navigator.clipboard.writeText(txt); }
  var el = document.getElementById('copied');
  el.textContent = 'คัดลอก: ' + txt;
  setTimeout(function(){ el.textContent = ''; }, 4000);
}
function renderTrades(rows){
  var cum = 0, h = '<table><tr><th>วันที่</th><th>เมือง</th><th>ฝั่ง</th><th>bin</th><th>จ่าย</th><th>ผล</th><th>PnL</th></tr>';
  if (rows.length){
    rows.forEach(function(r){
      var ok = r.won === 1 || r.won === '1';
      h += '<tr><td>' + esc(r.date) + '</td><td>' + esc(r.city) + '</td><td>' + esc(r.side) + '</td><td>' +
           esc(r.bin) + '</td><td>' + Number(r.entry).toFixed(3) + '</td><td class="' + (ok ? 'ok' : 'err') + '">' +
           (ok ? 'ชนะ' : 'แพ้') + '</td><td>' + (r.pnl >= 0 ? '+' : '') + Math.round(r.pnl).toLocaleString() + '</td></tr>';
    });
  } else { h += '<tr><td colspan=7 class="muted">ยังไม่มีไฟล์ไม้ — รัน python3 pnl_proof.py</td></tr>'; }
  h += '</table>';
  document.getElementById('trades').innerHTML = h;
}
function load(){
  fetch('/api/status').then(function(r){return r.json();}).then(render).catch(function(e){});
  fetch('/api/log').then(function(r){return r.json();}).then(renderLog).catch(function(e){});
  fetch('/api/trades?n=25').then(function(r){return r.json();}).then(renderTrades).catch(function(e){});
}
function loadTrades(){ fetch('/api/trades?n=25').then(function(r){return r.json();}).then(renderTrades).catch(function(e){}); }
function scan(mode){ fetch('/api/scan?mode=' + mode).then(load); }
load(); setInterval(load, 30000);
"""


def render_html():
    p = load_params()
    port = p.get("expected", {}).get("portfolio_recommended", {})
    n_tr = len(list(csv.DictReader(open(TRADES, encoding="utf-8")))) if os.path.exists(TRADES) else 0
    head = [
        "<!doctype html><html lang='th'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>WXEdge · พอร์ตอุณหภูมิสูงสุด</title><style>%s</style></head><body><div class='wrap'>" % CSS,
        "<h1>WXEdge — พอร์ต “อุณหภูมิสูงสุด” (Polymarket)</h1>",
        "<div class='sub'>หลักฐานจากข้อมูลจริง 220k แถว × เฉลยตลาด 4,142 รายการ · <b>โหมด paper</b> "
        "(ยังไม่ส่งคำสั่งซื้อจริง) · จักรวาล %d เมือง</div>" % len(universe_set()),
        "<div class='cards'>",
        "<div class='card'><div class='k'>WIN RATE (พอร์ตที่พิสูจน์)</div><div class='v g'>%.1f%%</div></div>" % port.get("win", 0),
        "<div class='card'><div class='k'>EV / ไม้</div><div class='v b'>$%.3f</div></div>" % port.get("ev", 0),
        "<div class='card'><div class='k'>ROI</div><div class='v g'>+%.1f%%</div></div>" % port.get("roi", 0),
        "<div class='card'><div class='k'>PNL (100/ไม้)</div><div class='v'>$%s</div></div>" % "{:,.0f}".format(port.get("pnl", 0)),
        "<div class='card'><div class='k'>MAX DRAWDOWN</div><div class='v y'>$%s</div></div>" % "{:,.0f}".format(port.get("dd", 0)),
        "<div class='card'><div class='k'>ไม้ / วัน</div><div class='v'>%.1f</div></div>" % port.get("per_day", 0),
        "</div>",
        "<div class='panel'><h2>เส้นทุนสะสม — จากไฟล์ไม้จริง %d ไม้ (คำนวณสดจาก trades_recommended.csv)</h2>%s</div>"
        % (n_tr, equity_svg()),
        "<div class='panel'><h2>ไม้สด — สแกนล่าสุด <span id='when' class='muted'>…</span></h2>",
        "<div class='row' style='margin-bottom:10px'><button onclick='scan(\"today\")'>สแกนใหม่ (วันนี้)</button>",
        "<button onclick='loadTrades()'>อัปเดตไม้ย้อนหลัง</button>",
        "<span id='stat' class='muted'></span></div>",
        "<div id='picks' class='muted'>กำลังโหลด…</div><div id='copied' class='ok' style='margin-top:8px'></div></div>",
        "<div class='panel'><h2>กำไรมาจากไหน (379 ไม้ · $201,884)</h2>",
        "<table><tr><th>แหล่งกำไร</th><th>ไม้</th><th>ชนะ%</th><th>ROI</th><th>กำไร/ไม้</th><th>ส่วนแบ่งกำไร</th><th></th></tr>",
        "<tr><td><b>YES หลัก</b> (ราคา 0.02–0.10)</td><td>60</td><td>83.3%</td><td>+1257%</td><td>$1,439</td><td>42.8%</td>"
        "<td><div style='background:#37d67a;height:9px;width:43%;border-radius:5px'></div></td></tr>",
        "<tr><td><b>YES รอง</b> (0.10–0.25)</td><td>104</td><td>87.5%</td><td>+389%</td><td>$420</td><td>21.6%</td>"
        "<td><div style='background:#63a4ff;height:9px;width:22%;border-radius:5px'></div></td></tr>",
        "<tr><td><b>NO</b> (bin พิสูจน์ได้ว่าเป็นไปไม่ได้)</td><td>215</td><td>100%</td><td>+203%</td><td>$334</td><td>35.6%</td>"
        "<td><div style='background:#ffa96b;height:9px;width:36%;border-radius:5px'></div></td></tr>",
        "</table><div class='muted' style='margin-top:8px'>YES = 64.4% ของกำไร (เครื่องยนต์หลัก) · NO = 35.6% แต่กินไม้ 57% ของทั้งหมด และเป็นส่วนที่ "
        "<b>ยังพิสูจน์ราคาไม่ได้</b> ⇒ ถ้าตัด NO ออกทั้งหมด ยังได้ $129,974 · ถ้า NO แพงขึ้น 0.10 ยังได้ $166,153</div></div>",
        "<div class='panel'><h2>ไม้ย้อนหลัง 25 รายการ — จากไฟล์ที่พิสูจน์แล้ว (trades_recommended.csv)</h2>"
        "<div id='trades' class='muted'>กำลังโหลด…</div></div>",
        "<div class='panel'><h2>เริ่มเทรดเมื่อไหร่ — ตารางเวลา (เวลาท้องถิ่นของเมืองนั้น)</h2>",
        "<table><tr><th>เวลา local</th><th>ยอดสูงสุดของวัน 'เกิดแล้ว'</th><th>สิ่งที่ทำ</th><th>ผลย้อนหลัง (จักรวาล 33 เมือง)</th></tr>",
        "<tr><td>12:00</td><td>29.1%</td><td>day-ahead check — <b>โหมดเฝ้าดู ยังไม่เข้าไม้</b></td><td class='muted'>ไม่เทรด</td></tr>",
        "<tr><td>14:00</td><td>63.5%</td><td>ผสม obs+พยากรณ์ 50/50 · เริ่มเข้าไม้ได้</td><td>ชนะ 85.0% · ROI +496% (n=60)</td></tr>",
        "<tr><td>15:00</td><td>81.6%</td><td>ไม้คม p≥0.97 ได้ (เข้าเร็ว)</td><td>ชนะ 84.5% · ROI +447% (n=97)</td></tr>",
        "<tr><td><b>16:00</b></td><td><b>93.2%</b></td><td><b>ไพรม์ไทม์</b> — YES หลัก/รอง + NO ที่พิสูจน์ได้</td><td><b>ชนะ 86.0% · ROI +532% (n=164)</b></td></tr>",
        "<tr><td>17:00</td><td>97.9%</td><td>ไม้สาย (มั่นใจสุด แต่น้อยไม้)</td><td>ชนะ 93.9% · ROI +535% (n=99)</td></tr>",
        "<tr><td>≥18:00</td><td>~99%</td><td>ราคาปรับไปแล้ว/ตลาดใกล้ปิด — หยุด</td><td class='muted'>ไม่เทรด</td></tr>",
        "</table><div class='muted' style='margin-top:8px'>หลักการ: ยิ่งสาย ยิ่งรู้ว่ายอดสูงสุดเกิดแล้ว (ความมั่นใจสูงขึ้น) "
        "แต่ราคาก็แพงขึ้นเช่นกัน → จุดสมดุลที่วัดแล้วดีที่สุดคือ <b>16:00</b> · ถือถึงเฉลย (ไม่ TP)</div></div>",
        "<div class='panel'><h2>กฎที่ใช้ (ล็อกไว้ ไม่ปรับตามอารมณ์)</h2><ul>",
        "<li><b>YES หลัก</b>: 16:00 น. ท้องถิ่น · p ≥ 0.90 · ราคา 0.02–0.10 · เลือก bin ที่ p สูงสุด</li>",
        "<li><b>YES รอง</b>: ราคา 0.10–0.25 (ไม้เสริม)</li>",
        "<li><b>NO</b>: bin ที่ต่ำกว่า max-so-far แล้ว &gt;0.5° (พิสูจน์ได้ว่าเป็นไปไม่ได้) · YES bid ≥0.50 · bid−p ≥0.30</li>",
        "<li><b>ห้าม</b>: ราคา &lt;0.02 · p &lt;0.90 · ไม้นอกจักรวาล · ถือถึงเฉลย (ไม่ TP)</li>",
        "<li><b>ขนาด</b>: 1% ของทุน/ไม้ · ≤6 ไม้/วัน · หยุดเมื่อ DD &gt; 15%</li></ul></div>",
        "<div class='panel'><h2>บันทึกจริง (cheap_live.py --log)</h2><div id='log' class='muted'>…</div></div>",
        "<div class='foot'>ข้อจำกัด: ราคาที่โชว์คือ quote/last-trade ที่ดึงได้ (ไม่ใช่ ask ที่ได้จริง 100%) · "
        "ฝั่ง NO ใช้ต้นทุน 1 − YES bid ซึ่งต้องยืนยันด้วย log จริง · ผลตอบแทนเป็น paper PnL · obs จาก IEM อาจล่าช้า ~1 วัน<br>"
        "ผลความทนทาน: +3 ticks → ROI +240% · ต้นทุน NO แพงขึ้น 0.10 → ฝั่ง NO ยัง +133%</div>",
        "<script>%s</script></div></body></html>" % JS,
    ]
    return "".join(head)


class Handler(BaseHTTPRequestHandler):
    server_version = "wxedge-dashboard"

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        try:
            if u.path in ("/", "/index.html"):
                return self._send(200, render_html(), "text/html; charset=utf-8")
            if u.path == "/api/status":
                return self._send(200, json.dumps(status_json(), ensure_ascii=False, default=str))
            if u.path == "/api/trades":
                q = parse_qs(u.query)
                try:
                    n = max(1, min(200, int((q.get("n") or ["25"])[0])))
                except ValueError:
                    n = 25
                return self._send(200, json.dumps(trades_tail(n), ensure_ascii=False, default=str))
            if u.path == "/api/log":
                return self._send(200, json.dumps(log_tail(), ensure_ascii=False, default=str))
            if u.path == "/api/scan":
                q = parse_qs(u.query)
                mode = (q.get("mode") or ["today"])[0]
                if mode not in ("today", "demo"):
                    mode = "today"
                threading.Thread(target=refresh, args=(mode,), daemon=True).start()
                return self._send(200, json.dumps(dict(started=True, mode=mode)))
            if u.path == "/healthz":
                return self._send(200, json.dumps(dict(ok=True)))
            return self._send(404, json.dumps(dict(error="not found")))
        except BrokenPipeError:
            pass
        except Exception:
            return self._send(500, json.dumps(dict(error=traceback.format_exc(limit=3)), ensure_ascii=False))

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="0.0.0.0")
    a = ap.parse_args()
    universe_set()
    threading.Thread(target=bg_loop, daemon=True).start()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print("แดชบอร์ดพร้อมที่ http://%s:%d  (จักรวาล %d เมือง · refresh ทุก %d นาที)"
          % (a.host, a.port, len(universe_set()), REFRESH_SEC // 60), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
