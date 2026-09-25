# Playbook tables (wxedge-temp-market reference)

## A. Timing by city — hit rate at 15:00 local (n=4,010 city-days, real settlement)

| Group | n | Cities | Hit @15:00 |
|---|---|---|---|
| **A — bet at 15:00** | 23 | los-angeles 95.2 · san-francisco 94.6 · manila 93.5 · miami 93.4 · qingdao 93.2 · panama-city 92.8 · tel-aviv 92.3 · busan 91.7 · houston 89.2 · wellington 88.7 · denver 87.0 · chicago 86.4 · shanghai 85.7 · nyc 84.0 · dallas 82.5 · buenos-aires 80.4 | ≥80% |
| **B — bet at 15:00** | 12 | karachi 78.9 · amsterdam 78.6 · singapore 78.6 · tokyo 78.3 · kuala-lumpur 78.0 · wuhan 77.1 · atlanta 76.5 · toronto 74.1 · seoul 74.1 · seattle 72.0 · jeddah 71.7 · ankara 70.5 | 70–80% |
| **C — wait to 16:00–17:00** | 9 | london 68.7 · beijing 68.5 · chongqing 68.2 · milan 67.5 · warsaw 66.6 · munich 65.1 · guangzhou 64.9 · chengdu 63.4 · moscow 60.4 | 60–69% |
| **D — wait to 17:00–18:00** | 4 | madrid 49.7→94.7 @18:00 · paris 49.4 · shenzhen 49.7 · (late-peak EU) | <50% @15:00 |

Adjacent-bin fallback: top-2 average 91.4% (worst city 71.7%).

## B. Intraday hit rate vs real settlement (n=4,010, 48 cities × 85 days)

| Hour (station-local) | top-1 | top-2 | Winner-bin price (avg) |
|---|---|---|---|
| 14:00 | 61.4% | 87.6% | — |
| **15:00** | **76.0%** | 92.9% | 0.627 |
| **16:00** | **85.3%** | 93.1% | — |
| 17:00 | 88.5% | 91.9% | 0.845 (edge priced in) |

Day-ahead (D-1): 52.2% top-1 (65.0% at p≥0.65), 82.4% top-2.

## C. Money — cheap bins (220,338 rows of real CLOB prices, 48 cities × 85 days)

| Rule | n | Win | Avg price | ROI |
|---|---|---|---|---|
| ask 0.02–0.25 & p≥0.90 @16:00 | 246 | **78.9%** | 0.133 | **+495%** |
| ask 0.02–0.25 & p≥0.90 @17:00 | 140 | 84.3% | 0.135 | +524% |
| ask 0.02–0.25 & p≥0.90 @15:00 | 150 | 74.7% | 0.146 | +411% |
| ask 0.02–0.25 & p≥0.35 @16:00 | 349 | 70.5% | 0.135 | +422% |
| ask 0.02–0.25 & p≥0.35 @15:00 | 621 | 46.5% | — | +226% |
| all bins <0.25, no model @16:00 | 38,740 | 1% | 0.013 | **−20%** |
| bins <0.02 (trap) | 182 | 6.0% | — | — |

### Win-rate-focused rules at price ≤ 0.30 (bootstrap CI, train/test confirmed, n=220,338 rows)
| Rule (hour · model gate · price band) | n | win% (95% CI) | avg | EV/share | ROI | test win% | bets/day | days with ≥1 bet |
|---|---|---|---|---|---|---|---|---|
| 16:00 · p≥0.90 · 0.02–0.25 (base) | 249 | 78.7 (73.1–83.5) | 0.134 | +0.653 | +488% | 76.4% | 3.0 | 83/85 |
| 16:00 · p≥0.97 · 0.02–0.25 (sharp) | 99 | **90.9** (84.8–96.0) | 0.136 | +0.773 | +570% | 90.5% | 1.7 | 59/85 |
| 16:00 · p≥0.97 · 0.02–0.15 (cheapest) | 55 | 87.3 (78.2–94.5) | 0.082 | +0.791 | +967% | 87.5% | 1.5 | 36/85 |
| 16:00 · p≥0.90 · 0.02–0.30 | 321 | 78.2 (73.5–82.6) | 0.166 | +0.616 | +371% | 77.3% | 3.9 | 83/85 |
| 17:00 · p≥0.95 · 0.02–0.30 | 185 | 86.5 (81.1–91.4) | 0.173 | +0.692 | +401% | 86.8% | 2.5 | 74/85 |
| 15:00 · p≥0.97 · 0.02–0.30 | 55 | 89.1 (80.0–96.4) | 0.170 | +0.721 | +425% | 85.7% | 1.3 | 41/85 |

leave-one-city-out worst case: p≥0.97 still ≥ 86.3% win; p≥0.90 still ≥ 77.4%. Fee 2% + 2 ticks: p≥0.90 → +401%, p≥0.97 → +471%, p≥0.97/≤0.15 → +739%.

Stability (85-day halves): @16:00 p≥0.35 → 70.4% / 70.7%. Frequency: ~4.1 bets/day at 16:00, ~2.1 at 17:00 across all 48 cities.
Exits (349 tickets @16:00, 17:00 prices): hold +384% · stop −25% **+405%** · TP+55% +280% · TP+30% +271% · sell@17:00 +248% → hold or stop-loss only.

## D. Head-to-head (identical inputs, train < 2026-08-15)

| Strategy | Day-ahead top-1 | Intraday 16:00 | ROI |
|---|---|---|---|
| wxedge (ours) | **50.9%** | **88.8%** | **+493%** |
| PolyWeather | 37.6% → 77.3% (with its own hard floor) | 77.3% | +24% |
| weatherbot | 45.5% (2-source design) | — | −12.8% (own EV gate); own paper −22% |
| polyBot-Weather | 34.7% | — | +0.3% (noise) |

Ablation: same mu, swap σ formula across all repos → 50.8–50.9% (σ irrelevant). Remove per-model bias correction → 33.5% (−17.4 pts).

## E. Sources & resolution cheatsheet

| Item | Use | Note |
|---|---|---|
| Obs (hourly METAR) | IEM asos.py `data=tmpc&report_type=3&tz=<tz>` | end date exclusive (+1); matches settlement 94% |
| Obs (never) | NWS 5-min api.weather.gov | 56% match, runs hot |
| Resolution source (US) | NOAA weather.gov/wrh/timeseries?site=<ICAO> or Wunderground | whole-degree; WU `units=e` is already whole °F |
| Historical WU | api.weather.com `.../observations/historical.json?units=e&startDate=YYYYMMDD` | oracle-grade cross-check vs IEM (30/30, mean +0.04°F) |
| Market labels | Gamma API events by slug `highest-temperature-in-<city>-on-<mon>-<day>-<year>` | winner = market with outcomePrices[0] > 0.5; always send a User-Agent |
| Prices (recent) | clob.polymarket.com/prices-history `interval=max` | covers only ~1 month |
| Prices (older) | `startTs/endTs` per market day | required for July/older dates |

## F. Install & run

```bash
cp -r skills/wxedge-temp-market ~/.claude/skills/     # Claude Code
cp -r skills/wxedge-temp-market ~/.agents/skills/     # Codex / Copilot CLI / Gemini CLI
python3 /home/user/wxedge/cheap_live.py               # live picks (15:00–17:00 local)
python3 /home/user/wxedge/headtohead.py --split 2026-08-15   # reproduce the comparison
```
