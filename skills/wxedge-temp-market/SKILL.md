---
name: wxedge-temp-market
description: Use when asked to predict, explain, or act on Polymarket "Highest temperature in <city> on <date>" markets — which temperature bucket will resolve, hit-rate questions, cheap-bin (under $0.25) opportunities, intraday timing rules, or when comparing/validating a temperature-market strategy.
---

# wxedge — Temperature Market Edge

## Overview

Predicting the resolved bucket of a Polymarket daily-temperature market is an **intraday** problem, not a forecasting problem. The daily max happens between 13:00–17:00 **station-local** time, so at/after 15:00 the running max is almost locked while the market still prices the winning bin cheaply (avg ~0.13 while it resolves ~79% of the time). Everything below is measured against real market settlement, not model output.

Project: `/home/user/wxedge/` · full rules: `STRATEGY.md` · evidence: `reports/*.md`

## When to Use

- "Which bucket will resolve for <city> on <date>?" / "ควรซื้อ bin ไหน"
- Anything about hit rate, cheap bins (<$0.25), intraday (14:00–18:00 local) predictions
- Validating or comparing a temperature-market strategy against alternatives

Do NOT use for: general weather questions, other market types, or any day-ahead-only bet (day-ahead top-1 is only ~50%).

## The Three Facts (check before any prediction)

1. **Resolution is station-specific, local-calendar-day, whole-degree.** Each market names its station (nyc=KLGA, london=EGLC, hong kong=HKO, moscow=UUWW, taipei=RCSS …). Never use city-center coordinates, never use a different day boundary, never re-convert °F/°C (US bins are whole °F; Wunderground `units=e` is already whole °F).
2. **Observations must be IEM hourly METAR** (`mesonet.agron.iastate.edu`, `data=tmpc`, `report_type=3`, `tz=<city tz>`) — it matches the resolving source 94%. Do NOT use NWS 5-minute observations (api.weather.gov): only 56% match, runs hot ~+0.3°F. IEM end date is exclusive → request `day+1`.
3. **Timing decides everything.** Predict at/after **15:00 station-local**, best at **16:00**. Local-time errors are the single most common way to lose money here.

## Workflow

1. **Check the station's local time now** — it must be 15:00–17:00 (a late-peak city: 17:00–18:00; see `references/playbook.md`). If it is earlier, say so and wait; do not bet.
2. **Run the tool** (never hand-compute probabilities):
   ```bash
   python3 /home/user/wxedge/cheap_live.py                  # scans cities currently 14:00–19:00 local, default rule p>=0.90
   python3 /home/user/wxedge/cheap_live.py --cities denver,seattle --pmin 0.35 --json
   python3 /home/user/wxedge/cheap_live.py --log            # log recommendations for forward test
   ```
   For analysis/backtest instead of today's bet: `intraday.py --days 120 --hours 14,15,16,17 --cities all`.
3. **Apply the gates** (below) to each candidate. If nothing passes, the correct answer is "no bet today" — opportunities are ~4/day at 16:00 across all cities, not every city every day.
4. **Report with the evidence line**: state the rule used, the model probability, the ask, and that ROI figures are gross (last-trade prices, no fee/spread). Never claim a result you did not just compute in this session.

## Gates (all must hold)

| Gate | Rule | Why (measured) |
|---|---|---|
| Time | 15:00–17:00 station-local (16:00 best) | 14:00 = 61.4% hit, 16:00 = 85.3%, 17:00 = 88.5% |
| Price | ask **0.02–0.25** | below 0.02 wins only 6.0% (n=182) — the market sees finer 5-min data than we do |
| Model | `p ≥ 0.90` (wide base) · `p ≥ 0.97` (sharp) · `p ≥ 0.35` (max volume) | 16:00: p≥0.90 → 78.7% @ 0.134 (+488%); p≥0.97 → **90.9%** @ 0.136 (+570%, n=99); p≥0.35 → 70.5% (+422%) |
| Size | 1–2% of capital per bet, cap ~10% total | 70–79% win rate still loses runs of bets |

## Never Do (each was measured and failed)

1. Never buy bins priced **under 0.02** — 6.0% win rate.
2. Never predict **before 14:00** (~20–30% win) and never bet day-ahead without `p ≥ 0.65` (top-1 is only 50.9%).
3. Never use **take-profit** (+30/+55/+100%) or a forced 17:00 sale — both cut ROI. Hold to resolution; an optional **−25% stop-loss** is the only exit that helped.
4. Never use **NWS 5-minute** observations (56% match) — IEM METAR only.
5. Never port a repo's **σ formula** as if it were the edge — swapping σ formulas changes results ≤0.1 pt; the real drivers are (a) removing **per-model bias** before blending (+17.4 pts) and (b) using **post-peak observations** (+52 pts).
6. Never select cities using past results (train-picked subset: 80% → 43% out-of-sample).
7. Never use "adjacent to the favourite bin" as a rule — negative ROI at 16:00–17:00.
8. Never quote ROI without saying **gross**: hourly last-trade prices, no fee/spread/slippage, 85 days of a single season.

## Quick Reference (headline numbers, test period, real settlement)

| Question | Answer |
|---|---|
| Day-ahead top-1 / top-2 | 50.9% / 80.2% (n=1,892, Brier 0.608) |
| 15:00 / 16:00 / 17:00 top-1 | 76.0% / 85.3% / 88.5% (n=4,010) |
| Cheapest profitable rule | ask 0.02–0.25 & p≥0.90 @16:00 → 78.7% win @ 0.134 → +488% ROI (gross) |
| Best win-rate rule (price ≤ 0.30) | ask 0.02–0.25 & **p≥0.97** @16:00 → **90.9%** win @ 0.136 → +570% (n=99, test 90.5%) |
| Raise price cap to 0.30? | No — widening 0.25→0.30 *lowers* ROI (488%→371%): pay more, win the same |
| Fee/spread stress | with 2% fee on winnings **and** paying 2 ticks more: p≥0.90 → +401%, p≥0.97 → +471% |
| vs. alternatives | ours 88.8% intraday > PolyWeather+hard-floor 77.3% > weatherbot 45.5% day-ahead (own paper −22%) > polyBot 34.7% |
| Best single borrowed idea | hard floor: any bin below (max_so_far − 0.5°C) is impossible (+39.7 pts) |

More tables (per-city timing groups, ROI by rule, traps): `references/playbook.md`.

## Common Mistakes

- Treating a model's max-probability bin as the answer without checking price → wins 77% but ROI only +24% (PolyWeather's pattern).
- Using the city's timezone guess instead of the station's actual local time.
- Reading `prices-history?interval=max` (empty for markets older than ~1 month) instead of `startTs/endTs`.
- Reporting a number from a stale report file instead of re-running the command.

## Evidence

All figures above come from `reports/headtohead.md` (head-to-head of 4 strategies on identical inputs), `reports/cheap_bets.md` (220,338 bin×day×hour prices), `reports/hitrate70.md` (4,010 city-days vs real settlement), `reports/superpowers_vs_strategy.md` (methodology comparison). Reproduce with `python3 headtohead.py --split 2026-08-15`.
