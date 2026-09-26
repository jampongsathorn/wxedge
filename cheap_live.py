#!/usr/bin/env python3
"""
cheap_live.py v2 — ใช้จริงตาม "กลยุทธ์ฉบับสมบูรณ์" (reports/strategy_full.md)

หลักการ (ทั้งหมดวัดจากข้อมูลจริงแล้ว):
  • YES หลัก: ask ≤ $0.10 · p ≥ 0.90 · หลัง 16:00 local        (backtest: ชนะ 83%, ROI +1177%)
  • YES รอง : ask 0.10–0.25 · p ≥ 0.90                          (backtest: ชนะ 88%, ROI +389%)
  • NO (ขาย): bin ที่ "เป็นไปไม่ได้แล้ว" (bin top < max-so-far − 0.5°) แต่ตลาดยังตั้ง YES bid ≥0.50
              และ bid − p ≥ 0.30 → ซื้อ NO ด้วยต้นทุน (1 − YES bid)   (backtest: ชนะ 100% ใน 215 ไม้)
  • จักรวาลเมือง: เฉพาะเมืองที่ obs ของเราตรงกับ bin ที่ตลาดตัดสิน ≥90% (ตัด hong-kong, shenzhen ฯลฯ)
  • กันความเสี่ยง: ไม่ซื้อ <0.02 · spread ≤0.04 · ถือถึงเฉลย (ไม่ TP)

รัน:
  python3 cheap_live.py                      # สแกนเมืองที่ local time อยู่ในช่วง 14-19 (ในจักรวาล)
  python3 cheap_live.py --cities denver,seattle --json
  python3 cheap_live.py --hour 15 --cities paris   # ทดสอบ as-of
  python3 cheap_live.py --log                # บันทึกคำแนะนำ + snapshot ลง data/ (v3: bid/ask จริง · depth · สถานะ obs · token id · เฉลย)
  python3 cheap_live.py --log --hour-window 15-17   # โหมด cron: สแกนเฉพาะเมืองที่ใกล้เวลาเข้าไม้
  python3 cheap_live.py --no-universe-filter # ปิดตัวกรองเมือง (ไม่แนะนำ)
  python3 cheap_live.py --workers 4 --cities a,b,c   # สแกนขนาน (CI ใช้ให้รอบเร็วขึ้น)

หมายเหตุ: การสแกนขนานยังเคารพ rate limit ต่อ host ใน wxedge.py (IEM เว้น 2 วิ/คำขอ)
"""
import argparse, collections, csv, json, math, os, re, statistics as _st, sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wxedge as W, intraday as I
import cheap_bets as CB

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "data", "cheap_live_log.csv")
# v3 (2026-09-25): ครบทุก datapoint ที่บอทต้องใช้ — สถานะ obs ตอนเข้าไม้ · depth จริง · token id · สองฝั่งของสมุด · เฉลย
LOG_COLS = ["logged_at", "city", "target", "local_time", "hour", "side", "tier", "bin", "unit",
            "yes_bid", "yes_ask", "yes_last", "spread", "no_ask_implied", "no_bid_implied",
            "ask_depth_usd", "bid_depth_usd", "book_best_ask", "book_best_bid",
            "model_p", "edge", "model_mu_c", "model_sigma_c", "obs_so_far_c", "n_hist", "n_bins",
            "vol", "liquidity", "min_size", "token_id", "slug", "stop_price",
            "won", "resolved_bin", "resolved_max_c", "resolved_at",
            "p_exceed"]
SNAP_DIR = os.path.join(HERE, "data", "snapshots")
UNIVERSE_JSON = os.path.join(HERE, "data", "universe.json")
AGREE_THR = 0.90


def book_snap(token_id):
    """(best_ask, ask_depth_usd, best_bid, bid_depth_usd) จาก CLOB order book ของ token นั้น
    depth = มูลค่า ณ ราคานั้น (price × size) → ใช้ตอบคำถาม 'ไม้ที่แนะนำ fill ได้จริงไหม' """
    if not token_id:
        return None
    try:
        j = W.get_json("https://clob.polymarket.com/book?token_id=%s" % token_id, ttl=0, no_cache=True)
    except Exception:
        return None
    if not isinstance(j, dict):
        return None
    asks = [(float(a["price"]), float(a["size"])) for a in (j.get("asks") or [])]
    bids = [(float(b["price"]), float(b["size"])) for b in (j.get("bids") or [])]
    if not asks and not bids:
        return None
    ba = min(asks) if asks else (None, None)
    bb = max(bids) if bids else (None, None)
    return dict(best_ask=ba[0], ask_depth_usd=round(ba[0] * ba[1], 1) if ba[0] is not None else None,
                best_bid=bb[0], bid_depth_usd=round(bb[0] * bb[1], 1) if bb[0] is not None else None)


def last_trade(token_id):
    """ราคาซื้อขายล่าสุด (ใช้เทียบว่า bid/ask เบี้ยวจากราคาซื้อขายจริงแค่ไหน)"""
    if not token_id:
        return None
    try:
        j = W.get_json("https://clob.polymarket.com/prices-history?market=%s&interval=1d&fidelity=60" % token_id,
                       ttl=0, no_cache=True)
    except Exception:
        return None
    h = (j or {}).get("history") or []
    return round(float(h[-1]["p"]), 3) if h else None


def build_universe(thr=AGREE_THR, min_n=10, verbose=False):
    """จักรวาลเมือง = เมืองที่ 'obs ของเราตกใน bin ที่ตลาดตัดสิน' ≥ thr (จากข้อมูลจริงทั้งหมดถึงวันนี้)
    เหตุผล: เมืองที่ obs ไม่ตรงกับแหล่งตัดสินของตลาด ต่อให้โมเดลแม่นแค่ไหนก็แพ้ (hong-kong 51%, shenzhen 52%)"""
    ds = os.path.join(HERE, "data", "dataset_full.csv")
    lb = os.path.join(HERE, "data", "market_labels.csv")
    if not (os.path.exists(ds) and os.path.exists(lb)):
        return None
    labels = {}
    for r in csv.DictReader(open(lb, encoding="utf-8")):
        try:
            labels[(r["city"], r["date"])] = (float(r["lo"]), float(r["hi"]), r["unit"])
        except ValueError:
            continue
    acc = collections.defaultdict(lambda: [0, 0])
    for r in csv.DictReader(open(ds, encoding="utf-8")):
        k = (r["city"], r["date"])
        if k not in labels or r.get("obs_c") in (None, ""):
            continue
        try:
            o = float(r["obs_c"])
        except ValueError:
            continue
        lo, hi, unit = labels[k]
        o = o * 9 / 5 + 32 if unit == "F" else o
        acc[r["city"]][1] += 1
        if lo - 0.5 <= o <= hi + 0.5:
            acc[r["city"]][0] += 1
    keep = sorted(c for c, (k, n) in acc.items() if n >= min_n and k / n >= thr)
    drop = sorted(((c, k / n, n) for c, (k, n) in acc.items() if not (n >= min_n and k / n >= thr)),
                  key=lambda x: x[1])
    out = dict(threshold=thr, min_n=min_n, updated=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               cities=keep, dropped=[c for c, _a, _n in drop],
               agreement={c: round(k / n, 3) for c, (k, n) in acc.items()})
    try:
        with open(UNIVERSE_JSON, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    except OSError:
        pass
    if verbose:
        print("จักรวาล: %d เมือง (ตัด %d)" % (len(keep), len(drop)))
    return set(keep)


def parse_bin_num(label):
    """'23°C' / '23°C or below' / '92-93°F' → (lo, hi) ในหน่วยของตลาด (ระวัง '-' ต้องไม่ถูกอ่านเป็นลบ)"""
    t = str(label).replace("°C", " ").replace("°F", " ").replace("–", "-").strip()
    neg = t.startswith("-")
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", t)]
    if neg and nums:
        nums[0] = -nums[0]
    if not nums:
        return (-math.inf, math.inf)
    if "or below" in t or "or lower" in t:          # ตลาดจริงใช้ "or below" / "or higher"
        return (-math.inf, nums[0])
    if "or above" in t or "or higher" in t:
        return (nums[0], math.inf)
    if len(nums) == 1:
        return (nums[0], nums[0])
    return (nums[0], nums[1])


def model_dist(cfg, city, day, hour, lookback=30):
    """การแจกแจงความน่าจะเป็นของค่าสูงสุดสุดท้าย (หน่วย °C) ณ ชั่วโมง hour

    ⚠ no_cache=True เสมอ: บทเรียน 26 ก.ย. 2026 — cache 1 ชม. ของ hourly_series ทำให้ max-so-far
    เก่าได้ถึง 60 นาที (Dallas: จริง 92°F @15:53 แต่ระบบเห็น 89°F @16:00 → โมเดล p=0.97 ผิด bin)
    """
    # ── สดแต่ประหยัด: ประวัติ (≤ เมื่อวาน) ใช้ cache ได้ · "วันนี้" ดึงสดเสมอ (คำขอเล็ก = ไม่โดน 429) ──
    #    บทเรียน 26 ก.ย. 2026: ถ้า cache ทั้งก้อน (เดิม ttl=3600) obs จะเก่าได้ถึง 60 นาที
    #    แต่ถ้าดึงสดทั้งก้อน (32 วัน/ครั้ง) จะโดน IEM 429 → แยกเป็น 2 คำขอ
    ser = I.hourly_series(cfg, day - timedelta(days=lookback + 2), day - timedelta(days=1),
                          ttl=6 * 3600)                      # ประวัติ = ข้อมูลนิ่ง → cache 6 ชม.
    try:
        _fresh = I.hourly_series(cfg, day, day, no_cache=True, ttl=60)   # วันนี้ = ดึงสด (คำขอเล็ก)
    except Exception:                                        # 429/เน็ตสะดุด → ถอยไปใช้ของเดิมใน cache
        _fresh = I.hourly_series(cfg, day, day, ttl=3600)
    for _d, _hh in (_fresh or {}).items():
        ser[_d].update(_hh)
    key = day.isoformat()
    hist = sorted(d for d in ser if d < key)
    cur = {hh: v for hh, v in (ser.get(key) or {}).items() if hh <= hour}
    if len(cur) < 3 or len(hist) < 10:
        return None
    mx = max(cur.values())
    incs = []
    for hd in hist[-lookback:]:
        p2 = [v for hh, v in ser[hd].items() if hh <= hour]
        if len(p2) < 3:
            continue
        incs.append(max(ser[hd].values()) - max(p2))
    if len(incs) < 8:
        return None
    return mx, incs



def compute_p_exceed(bins):
    """P_exceed ของแต่ละ bin = ราคา (ask) รวมของทุก bin ที่ 'สูงกว่า' bin นั้น (มุมมองตลาด).

    ใช้เป็นเกตกันไม้ obs ล้าช้า: ถ้าตลาดยังให้ราคา bin ที่สูงกว่ารวมกันมาก แปลว่า
    อุณหภูมิจริงอาจขึ้นไปแล้วแต่ข้อมูลที่เรามีล่าช้า (Dallas 26 ก.ย. 2026: P_exceed=1.00)
    """
    def _lo(o):
        try:
            lo_b, _hi = parse_bin_num(o["bin"])
        except Exception:
            return None
        return lo_b
    for o in bins:
        n = _lo(o)
        o["p_exceed"] = (round(min(sum(x["ask"] for x in bins if _lo(x) > n), 1.0), 3)
                         if n is not None else None)
    return bins


def scan_city(city, cfg, hour, pmax, min_edge, pmin, target=None, max_spread=0.04, min_size=0.0,
              want_depth=False, max_exceed=0.10):
    tz = ZoneInfo(cfg["tz"])
    now = datetime.now(tz)
    day = datetime.strptime(target, "%Y-%m-%d").date() if target else now.date()
    md = model_dist(cfg, city, day, hour)
    if not md:
        return {"city": city, "error": "obs ไม่พอสำหรับทำนาย"}
    mx_c, incs = md
    ev = W.polymarket_event(city, day.isoformat(), no_cache=True)
    if not ev:
        return {"city": city, "error": "ไม่พบตลาด"}
    mb = CB.market_bins_of(ev)
    if not mb:
        return {"city": city, "error": "ตลาดไม่มี bin"}
    samples = [mx_c + x for x in incs]
    vals = [s * 9 / 5 + 32 if cfg["unit"] == "F" else s for s in samples]
    mp = CB.model_probs(vals, cfg["unit"], [(lab, b) for lab, b, _ in mb])
    out = []
    for m in (ev.get("markets") or []):
        lab = (m.get("groupItemTitle") or "").strip()
        ask = m.get("bestAsk")
        if ask in (None, ""):
            op = m.get("outcomePrices")
            try:
                ask = float(json.loads(op)[0])
            except Exception:
                ask = None
        if ask is None:
            continue
        ask = float(ask)
        bid = m.get("bestBid")
        try:
            bid = float(bid) if bid not in (None, "") else None
        except (TypeError, ValueError):
            bid = None
        spread = float(m.get("spread")) if m.get("spread") not in (None, "") else (round(ask - bid, 4) if bid is not None else None)
        oms = float(m.get("orderMinSize") or 0)
        p_mod = mp.get(lab, 0.0)
        edge = p_mod - ask
        try:
            _toks = json.loads(m.get("clobTokenIds") or "[]")
        except Exception:
            _toks = []
        out.append(dict(bin=lab, ask=round(ask, 3), bid=round(bid, 3) if bid is not None else None,
                        spread=round(spread, 4) if spread is not None else None,
                        model_p=round(p_mod, 3), edge=round(edge, 3),
                        vol=round(float(m.get("volumeNum") or m.get("volume") or 0), 0),
                        liquidity=round(float(m.get("liquidityNum") or 0), 0),
                        min_size=oms, token_id=(_toks[0] if _toks else None),
                        slug=m.get("slug"), condition_id=m.get("conditionId")))
    # ── P_exceed: ราคา (ask) รวมของ bin ที่ "สูงกว่าของเรา" = ตลาดให้โอกาสที่ร้อนต่ออีกเท่าไร ──
    #    หลักฐาน 26 ก.ย. 2026 (backtest 165 ไม้ @16:00): P_exceed <0.10 → hit 96.1% · ≥0.60 → hit 71.2%
    #    เคสจริงที่แพ้ทั้ง 2 ไม้: P_exceed = 1.00 (ตลาดรู้ว่าอุณหภูมิขึ้นไปแล้ว แต่ obs เราล่าช้า)
    out = compute_p_exceed(out)

    picks = [o for o in out if 0.02 <= o["ask"] < pmax and o["model_p"] >= pmin and o["edge"] >= min_edge]
    picks = [o for o in picks if (o["spread"] is None or o["spread"] <= max_spread)
             and (min_size <= 0 or o["min_size"] <= min_size)]
    # กติกากันไม้ "obs ล้าช้า": ถ้าตลาดยังให้ราคา bin ที่สูงกว่ารวมกัน ≥ max_exceed → ข้าม
    picks = [o for o in picks if o["p_exceed"] is None or o["p_exceed"] < max_exceed]
    picks.sort(key=lambda o: (-o["model_p"], o["ask"]))
    picks = picks[:1]                                   # argmax bin เดียว (กฎที่ทดสอบแล้ว)
    for o in picks:
        # ชั้นตามราคา (ผลทดสอบ: ≤0.10 = ไม้หลัก ROI สูงสุด · 0.10–0.25 = ไม้รอง)
        o["side"] = "YES"
        o["tier"] = "หลัก (ask ≤0.10)" if o["ask"] <= 0.10 else "รอง (0.10–0.25)"

    # ── ฝั่งขาย (NO): bin ที่ "เป็นไปไม่ได้แล้ว" (bin top < max-so-far − 0.5°) แต่ตลาดยังตั้ง YES bid แพง ──
    #    เงื่อนไขที่ทดสอบแล้ว (215 ไม้ ชนะ 100% · train 125/test 90): YES bid ≥0.50 และ bid − p ≥ 0.30
    mx_u = mx_c * 9 / 5 + 32 if cfg["unit"] == "F" else mx_c
    no_picks = []
    for o in out:
        bid = o.get("bid")
        if bid is None or bid < 0.50:
            continue
        if (bid - o["model_p"]) < min_edge:
            continue
        lo_b, hi_b = parse_bin_num(o["bin"])
        if not (hi_b != math.inf and hi_b < mx_u - 0.5):
            continue
        no_cost = round(1.0 - bid, 3)                   # จ่ายจริง = 1 − YES bid
        if not (0.03 <= no_cost <= 0.50):
            continue
        no_picks.append(dict(side="NO", tier="ขาย (พิสูจน์ได้)", bin=o["bin"], ask=round(bid, 3),
                             entry_cost=no_cost, no_ask_implied=no_cost, bid=bid, spread=o.get("spread"),
                             model_p=o["model_p"], edge=round(bid - o["model_p"], 3),
                             vol=o["vol"], liquidity=o.get("liquidity"), min_size=o.get("min_size"),
                             max_so_far=round(mx_u, 2), token_id=o.get("token_id"), slug=o.get("slug")))
    if want_depth:
        for p in list(picks) + list(no_picks):
            snap = book_snap(p.get("token_id"))
            if snap:
                p["book_best_ask"] = snap["best_ask"]
                p["book_best_bid"] = snap["best_bid"]
                p["ask_depth_usd"] = snap["ask_depth_usd"]
                p["bid_depth_usd"] = snap["bid_depth_usd"]
            p["yes_last"] = last_trade(p.get("token_id"))
    screened = sorted(out, key=lambda o: (-o["model_p"], o["ask"]))[:3]
    return dict(city=city, station=cfg["icao"], unit=cfg["unit"], target=day.isoformat(),
                screened=[dict(bin=o["bin"], ask=o["ask"], bid=o.get("bid"), p=o["model_p"]) for o in screened],
                local_time=now.strftime("%Y-%m-%d %H:%M"), hour=hour,
                obs_so_far_c=round(mx_c, 2), n_hist=len(incs),
                picks=picks, no_picks=no_picks, bins=out, mu_c=round(mx_c, 3),
                sigma_c=(round(_st.stdev(incs), 3) if len(incs) > 1 else None),
                best_ask=min((o["ask"] for o in picks), default=None),
                note="YES: ราคา 0.02–%.2f · p ≥ %.2f · edge ≥ %.2f · spread ≤ %.3f | NO: bin ที่ต่ำกว่า max-so-far >0.5° (ถือถึงเฉลย)"
                     % (pmax, pmin, min_edge, max_spread))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cities", default="auto", help="auto = เมืองที่ local time อยู่ในช่วง --hour-window (ในจักรวาล)")
    ap.add_argument("--hour-window", default="14-19",
                    help="ช่วง local hour สำหรับ --cities auto (ค่าเริ่มต้น 14-19 · โหมด cron ใช้ 15-17 เพื่อประหยัดนาที CI)")
    ap.add_argument("--hour", type=int, default=0, help="ใช้ชั่วโมงนี้แทนชั่วโมงปัจจุบัน (สำหรับทดสอบ)")
    ap.add_argument("--day", default="", help="วันเป้าหมาย YYYY-MM-DD (ค่าเริ่มต้น=วันนี้)")
    ap.add_argument("--pmax", type=float, default=0.25)
    ap.add_argument("--pmin", type=float, default=0.90,
                    help="ธรณี p ของโมเดล (ค่าเริ่มต้น 0.90 = กฎไม้หลัก; ใช้ 0.35 = กว้างกว่า ชนะน้อยกว่า)")
    ap.add_argument("--min-edge", type=float, default=0.15)
    ap.add_argument("--max-exceed", type=float, default=0.10,
                    help="เพดานราคารวมของ bin ที่สูงกว่าเรา (P_exceed) — กันไม้ obs ล้าช้า (หลักฐาน 26 ก.ย.)")
    ap.add_argument("--max-spread", type=float, default=0.04,
                    help="spread สูงสุดที่ยอมรับ (ask-bid) — บทเรียนจาก repo weatherbot (0.03)")
    ap.add_argument("--min-size", type=float, default=0, help="ขนาดไม้ขั้นต่ำของตลาด (orderMinSize) ที่ยอมรับ")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--log", action="store_true", help="บันทึกคำแนะนำลง data/cheap_live_log.csv (v2)")
    ap.add_argument("--force-log", action="store_true",
                    help="บันทึกทับได้แม้ซ้ำ (ค่าเริ่มต้น: กันซ้ำด้วย city+target+hour+side+bin) — "
                         "สำคัญเมื่อรันเป็น cron ทุกชั่วโมง เพราะอยากได้ 'ราคาแรก' ของชั่วโมงนั้น")
    ap.add_argument("--workers", type=int, default=1,
                    help="สแกนหลายเมืองพร้อมกัน (ค่าเริ่มต้น 1 = เรียงลำดับ · ใช้ 3-4 ใน CI ให้รอบเร็วขึ้น)")
    ap.add_argument("--no-universe-filter", action="store_true",
                    help="ปิดตัวกรองจักรวาลเมือง (ค่าเริ่มต้น: เฉพาะเมืองที่ obs ตรงกับ bin ที่ตลาดตัดสิน)")
    a = ap.parse_args()
    cfgs = json.load(open(W.STATIONS))

    UNIV = None
    if not a.no_universe_filter:
        UNIV = build_universe()
        if UNIV is None:
            print("⚠ ยังไม่มี data/dataset_full.csv หรือ market_labels.csv — ข้ามตัวกรองจักรวาล")

    hw_lo, hw_hi = [int(x) for x in a.hour_window.split("-")]

    if a.cities == "auto":
        cities = []
        for c, v in cfgs.items():
            if not v.get("icao"):
                continue
            t = datetime.now(ZoneInfo(v["tz"]))
            if hw_lo <= t.hour <= hw_hi and (UNIV is None or c in UNIV):
                cities.append(c)
    else:
        cities = [c.strip() for c in a.cities.split(",") if c.strip()]
        if not cities:                                  # ส่ง --cities ว่างมา (เช่น workflow_dispatch นอกช่วงเวลา) → ถอยไปใช้ auto
            cities = [c for c, v in cfgs.items()
                      if v.get("icao") and hw_lo <= datetime.now(ZoneInfo(v["tz"])).hour <= hw_hi
                      and (UNIV is None or c in UNIV)]
        if UNIV is not None:
            skipped = [c for c in cities if c not in UNIV]
            if skipped:
                print("⚠ ข้ามเมืองที่ obs ไม่ตรงกับแหล่งตัดสินของตลาด: %s" % ", ".join(skipped))
            cities = [c for c in cities if c in UNIV]

    def _scan(c):
        try:
            return scan_city(c, cfgs[c], a.hour or datetime.now(ZoneInfo(cfgs[c]["tz"])).hour,
                             a.pmax, a.min_edge, a.pmin, a.day or None,
                             max_spread=a.max_spread, min_size=a.min_size, want_depth=a.log,
                             max_exceed=a.max_exceed)
        except Exception as e:                       # เมืองเดียวล้ม ต้องไม่ล้มทั้งรอบ (เช่น IEM ตอบ 429)
            return {"city": c, "error": "สแกนไม่สำเร็จ: %s" % str(e)[:120]}

    if a.workers > 1 and len(cities) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            res = list(ex.map(_scan, cities))          # คงลำดับเดิม (map เรียงตามอินพุต)
    else:
        res = [_scan(c) for c in cities]

    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print("เมืองที่สแกน: %s" % ", ".join(cities))
        for r in res:
            if r.get("error"):
                print("  %-14s ✖ %s" % (r["city"], r["error"]))
                continue
            if not r["picks"] and not r.get("no_picks"):
                print("  %-14s — ไม่มีไม้เข้าเกณฑ์ (%s)" % (r["city"], r["note"]))
                continue
            print("  %-14s local %s | obs สูงสุด %.1f°C" % (r["city"], r["local_time"], r["obs_so_far_c"]))
            for p in r["picks"]:
                print("      ▸ BUY YES [%s] %-12s ask=%.3f (spread %.3f) | โมเดล=%.2f | edge=%+.2f | vol=$%s | stop %.3f" % (
                    p["tier"], p["bin"], p["ask"], p["spread"] or 0, p["model_p"], p["edge"],
                    int(p["vol"]), p["ask"] * 0.75))
            for p in r.get("no_picks", []):
                print("      ▸ BUY NO  [ขาย bin เป็นไปไม่ได้] %-12s YES bid=%.3f → จ่าย NO=%.3f | max-so-far=%.1f | โมเดล=%.2f | vol=$%s" % (
                    p["bin"], p["bid"], p["no_ask_implied"], p["max_so_far"], p["model_p"], int(p["vol"])))

    if a.log and res:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        # อัปเกรดฟอร์แมต log (v1/v2 → v3) โดยไม่ทำข้อมูลเดิมหาย
        if os.path.exists(LOG):
            with open(LOG, newline="", encoding="utf-8") as f:
                head = next(csv.reader(f), [])
            if [h.strip() for h in head] != LOG_COLS:
                arc = LOG.replace(".csv", "_prev.csv")
                n = 2
                while os.path.exists(arc):
                    arc = LOG.replace(".csv", "_prev%d.csv" % n)
                    n += 1
                os.replace(LOG, arc)
                print("เก็บ log ฟอร์แมตเก่าไว้ที่ %s แล้วเริ่มไฟล์ v3 ใหม่" % arc)
        new = not os.path.exists(LOG)
        seen = set()
        if not (a.force_log or new):
            for r0 in csv.DictReader(open(LOG, encoding="utf-8")):
                seen.add((r0.get("city"), r0.get("target"), str(r0.get("hour")), r0.get("side"), r0.get("bin")))
        added = skipped = 0
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        def _log_row(r, p, side):
            """แถวเดียวต่อ (เมือง, วัน, ชั่วโมง, ฝั่ง, bin) — เก็บทุก datapoint ที่บอทต้องใช้ตัดสินใจ"""
            yb = p.get("bid")
            ya = p.get("ask") if side == "YES" else p.get("book_best_ask")
            if side == "YES":
                no_ask, no_bid = (round(1 - yb, 3) if yb is not None else None), (round(1 - ya, 3) if ya is not None else None)
            else:
                no_ask = p.get("no_ask_implied")
                no_bid = round(1 - ya, 3) if ya is not None else None
            return [ts, r["city"], r["target"], r.get("local_time"), r["hour"], side, p["tier"], p["bin"], r.get("unit"),
                    yb, ya, p.get("yes_last"), p.get("spread"), no_ask, no_bid,
                    p.get("ask_depth_usd"), p.get("bid_depth_usd"), p.get("book_best_ask"), p.get("book_best_bid"),
                    p["model_p"], p["edge"], r.get("mu_c"), r.get("sigma_c"), r.get("obs_so_far_c"),
                    r.get("n_hist"), len(r.get("bins") or []),
                    p.get("vol"), p.get("liquidity"), p.get("min_size"), p.get("token_id"), p.get("slug"),
                    (round(p["ask"] * 0.75, 4) if side == "YES" else None),
                    "", "", "", "",                        # won, resolved_bin, resolved_max_c, resolved_at → forward_resolve.py เติมทีหลัง
                    p.get("p_exceed")]

        with open(LOG, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(LOG_COLS)
            for r in res:
                if r.get("error"):
                    continue
                for p in r.get("picks", []):
                    key = (r["city"], r["target"], str(r["hour"]), "YES", p["bin"])
                    if key in seen:
                        skipped += 1
                        continue
                    seen.add(key)
                    w.writerow(_log_row(r, p, "YES"))
                    added += 1
                for p in r.get("no_picks", []):
                    key = (r["city"], r["target"], str(r["hour"]), "NO", p["bin"])
                    if key in seen:
                        skipped += 1
                        continue
                    seen.add(key)
                    w.writerow(_log_row(r, p, "NO"))
                    added += 1
        print("บันทึก %s: เพิ่ม %d แถว · ข้ามซ้ำ %d แถว (กันซ้ำตาม city+วัน+ชั่วโมง+ฝั่ง+bin)" % (LOG, added, skipped))

        # ── snapshot: การแจกแจงเต็ม + ราคาทุก bin (ไม่ dedupe — ต้องได้ time series ของราคาช่วงเข้าไม้) ──
        os.makedirs(SNAP_DIR, exist_ok=True)
        snap_path = os.path.join(SNAP_DIR, datetime.now(timezone.utc).strftime("%Y-%m-%d") + ".jsonl")
        done = set()
        if os.path.exists(snap_path):
            for ln in open(snap_path, encoding="utf-8"):
                try:
                    d0 = json.loads(ln)
                    done.add((d0["city"], d0["target"], d0["local_time"]))
                except Exception:
                    continue
        n_snap = 0
        with open(snap_path, "a", encoding="utf-8") as f:
            for r in res:
                if r.get("error") or not r.get("bins"):
                    continue
                k = (r["city"], r["target"], r.get("local_time"))
                if k in done:
                    continue
                done.add(k)
                rec = dict(ts=ts, city=r["city"], target=r["target"], local_time=r.get("local_time"), hour=r.get("hour"),
                           obs_so_far_c=r.get("obs_so_far_c"), mu_c=r.get("mu_c"), sigma_c=r.get("sigma_c"),
                           n_hist=r.get("n_hist"), max_so_far_c=r.get("obs_so_far_c"),
                           bins=[[b["bin"], b["model_p"], b["ask"], b["bid"], int(b["vol"] or 0)] for b in r["bins"]])
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n_snap += 1
        if n_snap:
            print("snapshot: +%d เมือง → %s" % (n_snap, snap_path))


if __name__ == "__main__":
    main()
