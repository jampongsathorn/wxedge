# ศึกษาการทำงานของ 3 repos เทรดตลาดอากาศ Polymarket

อ้างอิงจากโพสต์ [@ninedol](https://x.com/ninedol/status/2048454943196004380) · ศึกษาเมื่อ 24 ก.ย. 2026
วิธีศึกษา: `git clone` โค้ดจริงมาอ่านทั้งหมด (ไม่ใช่แค่อ่าน README) แล้ว **ทดลองนำไอเดียที่สำคัญมาใช้กับเครื่องมือของเรา** และวัดผลกับข้อมูลจริง

| # | repo | สถานะ |
|---|---|---|
| 1 | `yangyuan-zhen/PolyWeather` | โคลนได้ · commit ล่าสุด 2026-09-21 (active) · **AGPL-3.0** |
| 2 | `alteregoeth-ai/weatherbot` | **ถูกลบจาก GitHub (404)** → ศึกษาจากสาย fork ที่ยังอยู่ (`CCronos/weatherbot` ฯลฯ, MIT, Copyright alteregoeth-ai) |
| 3 | `hcharper/polyBot-Weather` | โคลนได้ · commit ล่าสุด 2026-02-03 (ไม่ active) · MIT |

---

## สรุปผู้บริหาร (อ่าน 30 วินาที)

| ประเด็น | ข้อค้นพบ |
|---|---|
| ทั้ง 3 repos แก้ปัญหาอะไร | ทาย **"อุณหภูมิสูงสุดของวัน"** ล่วงหน้า (D+0 ถึง D+3) → เทียบความน่าจะเป็นกับราคา bin บน Polymarket → เข้าไม้เมื่อ EV เป็นบวก |
| โมเดลร่วมของทั้งสาม | **Gaussian คร่อมค่าพยากรณ์** + σ จากความคลาดเคลื่อนย้อนหลัง + Kelly sizing — ไม่มีใครใช้การแจกแจงเชิงประจักษ์ (empirical) แบบเรา |
| จุดที่แต่ละตัวเก่ง | **PolyWeather**: DEB ถ่วงน้ำหนักโมเดลตาม MAE รายเมือง + calibration เชิงสถิติจริง (PIT/cov90) + dynamic intraday adjustment · **weatherbot**: self-calibration σ/bias จากตลาดที่ resolve แล้ว + กติกาออกที่รัดกุม + ตรวจ ask จริงก่อนเข้า · **polyBot**: เรียบง่าย มี risk manager + copy trading + คริปโต |
| สิ่งที่เรานำมาทดลองวันนี้ | weather suppression → **ไม่ช่วย** (45.3% เท่าเดิม) · trend rate → **แย่ลงมาก** (45.3% → 23.6%) · take-profit → **แย่ลง** (ROI 384% → 280%) · stop-loss −25% → **ดีขึ้นเล็กน้อย** (384% → 405%) · Wunderground obs → **ตรงกับ IEM และตรงตลาด 30/30** |
| ข้อแตกต่างสำคัญของเรา | ทั้ง 3 repos **ไม่มีตัวเลข hit rate ที่วัดกับผลตัดสินจริงของตลาด** เปิดเผยเลย — เรามี 4,142 ตลาด-วัน และราคาทุก bin 220,338 แถว · และไม่มีใครทำ "ทายหลัง peak" (15:00–17:00) ที่ให้ 76–88% |

---

## 1) PolyWeather (`yangyuan-zhen`) — ระบบ production ระดับ stack เต็ม

**ขนาด**: 51 เมือง · FastAPI + Next.js + Telegram bot + Redis Stream/SSE + SQLite · AGPL-3.0
**ข้อสำคัญ**: repo **ไม่เปิด** ส่วนที่ทำเงินจริง — README ระบุชัดว่าไม่รวม "internal mispricing strategy, position sizing rules, and trading bot execution code" → สิ่งที่อ่านได้คือ **ชั้นพยากรณ์ + ความน่าจะเป็น + ข้อมูล**

### 1.1 DEB (Dynamic Error Balancing) — `src/analysis/deb_algorithm.py`
หัวใจคือถ่วงน้ำหนักโมเดลด้วย **inverse error** แบบเวลา:

```
w_m ∝ 1 / ( MAE_m + |bias_m| × 0.5 + 0.1 )
```
- `MAE_m` ถ่วงด้วยเวลา: `decay = 0.85^days_ago` (1 วันก่อน = 0.85, 7 วัน = 0.32)
- **วันร้อนได้น้ำหนัก ×2**: วันที่ค่าจริง ≥ 35 °C (หรือ 95 °F) เพิ่มน้ำหนักตัวอย่างเป็น 2 เท่า — เพราะเจอว่า blend แพ้โมเดลเดี่ยวในชั้น ≥37 °C
- **ตัดโมเดลตระกูลเดียวกัน** (เช่น ECMWF หลาย variant) ไม่ให้ vote ซ้ำ
- **Bias penalty**: ใช้ bias ที่มีเครื่องหมาย (model − actual) ถ่วงเข้าไปในตัวส่วน → โมเดลที่ "เอน ciep" ทางใดทางหนึ่งถูกลดน้ำหนัก
- **Divergence fallback**: ถ้า spread ระหว่างโมเดล > 3° → ผสมกลับไปทาง equal-weight ตามสัดส่วน `trust = 3/spread`
- **Adaptive lookback**: 7–14 วันตามจำนวนข้อมูลที่มี
- ถ้าข้อมูล < 2 วัน → equal weight

### 1.2 เครื่องความน่าจะเป็น `deb_normal` — `src/analysis/deb_probability.py`
```
mu    = deb_prediction + bias(lead)
sigma = max( sigma(lead, temp_stratum), 0.5 )
P(T = τ) = Φ((τ+0.5−mu)/σ) − Φ((τ−0.5−mu)/σ)
```
- **strata ของ lead**: 0 / 1 / 2+ วัน · **strata ของอุณหภูมิ**: ≤32 / 33–36 / ≥37 °C (เจอว่าแถบ 33–36 มี warm bias)
- **James–Stein shrinkage** สำหรับ bias รายเมือง: `adj = n/(n+5) × (median_กลุ่ม − median_lead)` → กลุ่มเล็กหดเข้า 0
- **ถ่วงความใหม่**: ภายใน 14 วันใช้ `0.9^days_ago` (ตาม regime shift) เมืองที่ไม่มีข้อมูลใหม่ → ใช้ median ทั้งประวัติ
- **ด่านจำนวนตัวอย่าง**: `MIN_ADJUST_SAMPLES = 30` (เดิม 10 → เกิด adjustment −1.5 °C จากตัวอย่างไม่กี่วัน แล้วทำให้ PIT พัง 0.51 → 0.71), `MIN_SIGMA_SAMPLES = 15`
- **σ ต้องไม่แคบลงเมื่อ horizon ไกลขึ้น**: strata ที่ขาดให้ยืมจาก strata ที่ไกลกว่าเท่านั้น
- **การตรวจสอบคุณภาพ**: ใช้ **PIT (Probability Integral Transform)** mean/std, **coverage@90%**, **chi²** — ไม่ใช่ดูแค่ hit rate (เช่น ≥37 °C cov90 0.820 → 0.893 หลังแก้)
- ML calibration (LightGBM quantile) เรียน residual `actual − raw_deb` จาก city, raw deb, model median/spread, n_models, เดือน/วัน — แต่ walk-forward ตอน train เพื่อไม่ให้ target ปนการรันตัวเอง

### 1.3 การเลือกแหล่ง obs — `web/services/canonical_engine.py`
ให้คะแนนทุกแหล่งแล้วเลือกสูงสุด:
```
score = น้ำหนักแหล่ง (hko 680 / cowin 660 / madis_hfmetar 500 / metar 460)
      + 300  ถ้าเป็นแหล่งที่ใช้ตัดสิน (settlement source)
      + 1000 ถ้าตรงสถานีตัดสินเป๊ะ
      + 750  ถ้าชื่อสถานีอยู่ใน candidate list
      + ความสด (fresh 80 / expected_wait 60 / delayed 35 / unknown 20 / stale 5)
```
> ตรงกับบทเรียนของเรา: **ความสด + ความตรงสถานี สำคัญกว่าน้ำหนักแหล่ง**

### 1.4 ปรับพยากรณ์ระหว่างวัน — `src/analysis/dynamic_forecast.py`
นี่คือส่วนที่ใกล้ที่สุดกับ `intraday.py` ของเรา ทำ 3 อย่างกับ payload ความน่าจะเป็น:
1. **Hard floor** — bin ที่ต่ำกว่า `ceil(max_so_far − 0.5)` เป็นไปไม่ได้ → ตั้ง 0 แล้วกระจายมวลไป bin ที่เหลือ
2. **Trend rate** — ความชันจาก METAR 2 จุดล่าสุด (ห่าง ≤ 3 ชม.) → `mu += trend × hours_to_peak × 0.5` (หน่วงครึ่งหนึ่ง)
3. **Weather suppression** — bin ที่สูงกว่า `mu + 0.5` คูณ: TS ×0.55, RA/SHRA/FZRA/DZ ×0.70, เมฆ ≥ 6 oktas ×0.85, สัญญาณฝนใน TAF ×0.90

เสริม: เส้นพยากรณ์รายชั่วโมงแบบ DEB-weighted consensus → หา peak window · แยก TAF (FM/TEMPO/BECMG/PROB30/40) · vertical profile (upper air) · deviation monitor

### 1.5 จุดอ่อนที่เห็นจากโค้ด
- infra หนักมาก (Docker + Redis + SQLite 2 GB) สำหรับปันผลตอบแทนที่ไม่เปิดเผย
- obs หลักเป็นรายชั่วโมง/10 นาที ไม่ใช่ 5 นาทีแบบที่ตลาดสหรัฐใช้ตัดสิน (จุดที่เราวัดแล้วว่าเป็นต้นเหตุความคลาดเคลื่อน 6%)
- ไม่มี hit rate / ROI ที่วัดกับ **ผลตัดสินจริง** เปิดเผย

---

## 2) weatherbot (`alteregoeth-ai` — ถูกลบแล้ว ศึกษาจากสาย fork, MIT)

ไฟล์เดียว ~1,870 บรรทัด (`bot_v2.py`) — ตรงไปตรงมาและมี **บทเรียนที่จดไว้ในคอมเมนต์** เยอะมาก (ภาษาสเปนปนอังกฤษ) เป็น repo ที่ "ใช้งานจริงและแก้บั๊กสู้มาก่อน"

### 2.1 พยากรณ์ 3 ระดับ
| แหล่ง | รายละเอียด |
|---|---|
| ECMWF | Open-Meteo `models=ecmwf_ifs025&bias_correction=true` (ทุกเมือง) |
| **Regional รายเมือง** | US = `gfs_seamless` (HRRR blend, ใช้แค่ 48 ชม.) · London = UKMO · Paris = AROME · Munich = ICON-D2 · ยุโรปอื่น = ICON-EU · East Asia = JMA-GSM · Toronto = GEM · จีน = ICON-seamless · เมืองที่ไม่มีโมเดลระดับภูมิภาคที่ได้เปรียบจริง → ใช้ ECMWF เดี่ยว (ไม่แต่งตาราง) |
| METAR | aviationweather.gov (D+0 เท่านั้น) |

### 2.2 Self-calibration จากตลาดที่ resolve แล้ว (จุดแข็งที่สุด)
```
σ(เมือง, แหล่ง) = MAE ของ (forecast − actual)
bias(เมือง, แหล่ง) = EWMA(0.25) ของ (forecast − actual)   # บวก = โมเดลร้อนเกิน
```
**บทเรียนสำคัญในโค้ด**: ตั้ง `CALIBRATION_MIN = 30` ตัวอย่างต่อเมือง → ไม่มีเมืองไหนถึง (มีแค่ ~15 วัน/เมือง) → **calibration ไม่เคยรันเลย**, ไฟล์เป็น `{}` มาตลอด → แก้เป็น 10 + เพิ่ม **pooled fallback** รวมทุกเมืองในหน่วย+แหล่งเดียวกัน (ต้อง ≥ 40) → ค่อยตกไปใช้ค่าคงที่ 2 °F / 1.2 °C

> ตรงกับที่เราทดสอบเมื่อวาน: เลือก/ปรับรายเมืองด้วยตัวอย่างเล็ก = overfit (train 80% → test 43%) **pooled คือคำตอบ**

การรวมแหล่ง: **inverse-variance** `w = 1/σ²` และ **หัก bias เฉพาะค่าที่ใช้เทรด** — ค่าดิบใน snapshot ไม่ถูกหัก เพื่อให้การ calibration อนาคตยังวัด error จริงของโมเดล ไม่ใช่ไล่หางตัวเอง

### 2.3 กติกาเข้า–ออก (รัดกุมกว่า repo อื่น)
**เข้า**: `EV = p×(1/price − 1) − (1−p) ≥ 0.10` · ask < 0.45 · volume ≥ 500 · เหลือเวลา 2–72 ชม. · **spread จริง ≤ 0.03 (ดึง ask จาก order book ไม่ใช้ราคากลาง)** · Kelly × 0.25 · ไม้ละ ≤ $20

**ออก**:
| กติกา | ค่า | เหตุผล |
|---|---|---|
| Stop-loss | −25% | ตัดไม้ที่ตลาดบอกว่าเราผิด |
| Trailing → breakeven | เมื่อกำไร +20% | ล็อกไม่ให้ขาดทุน |
| Take-profit | +55% | เก็บกำไรก่อนตลาดผันกลับ |
| **Invalidation exit** | ถ้า obs วันนี้ > ขอบบน bin + buffer (2 °F/1 °C) → ขายทันที | daily max เพิ่มขึ้นอย่างเดียว → ไม้ "แพ้ทางคณิตศาสตร์" แล้ว |
| Emergency brake | equity −50% จากทุนเริ่ม → หยุดเปิดไม้ใหม่ | |

### 2.4 แหล่งตัดสิน: เขาเปลี่ยนจาก Visual Crossing → **Wunderground**
คอมเมนต์ระบุ: ตรวจข้อความตัดสินของหลายตลาดแล้ว Polymarket ใช้ Wunderground · และพบว่า VC ต่างจาก bucket ที่ชนะจริงถึง ~3 °C
endpoint: `api.weather.com/v1/location/{ICAO}:9:{CC}/observations/historical.json` ด้วยคีย์สาธารณะที่ฝังอยู่ใน HTML ของ wunderground.com

### 2.5 จุดอ่อน
- ไฟล์เดียว 1,870 บรรทัด ไม่มี test, ไม่มี type, คอมเมนต์สองภาษา
- σ = MAE (ใช้แทน RMSE ได้เมื่อ bias ≈ 0 เท่านั้น)
- ไม่มี hit rate/backtest ที่วัดกับผลตัดสินจริงเปิดเผย

---

## 3) polyBot-Weather (`hcharper`, MIT) — ชุดเครื่องมือหลายกลยุทธ์สำหรับเครื่องบ้าน

โครง: `polybot/` แยก connectors / strategies / core + Streamlit dashboard + pytest

### 3.1 กลยุทธ์ 3 ตัว
1. **Weather (Gaussian)**: พยากรณ์จาก `api.weather.gov` (NWS gridpoints) → σ จาก **ตาราง RMSE ตาม lead time** (24 ชม. = 2.5 °F, 48 = 3.5, 72 = 4.5, 120 = 6.0, 168 = 7.5) → `P(above) = 1 − Φ((threshold − forecast)/σ)` · ฝนใช้ตาราง calibration (เช่น NOAA บอก 10–30% → ปรับเป็น 0.85)
2. **Crypto (Black–Scholes)**: ราคา Pyth (400 ms) → `P = N(ln(S/K)/(σ√T))` ด้วย σ ตั้งต้น BTC 55% / ETH 70% / SOL 95%
3. **Binary arbitrage**: `YES + NO < $1.00` → ซื้อทั้งสองข้าง เก็บผลต่าง (ไม่ไวต่อ latency)

### 3.2 การตัดสินใจ
```
edge = |p_model − p_market| ,  z = edge / combined_std
เข้าเมื่อ  edge ≥ 10%  และ  z ≥ 1.5        (min_edge/min_zscore ตั้งได้)
confidence: HIGH เมื่อ z ≥ 3 และ edge ≥ 20% · MEDIUM เมื่อ z ≥ 2 และ edge ≥ 12%
ขนาดไม้: Kelly × 0.25 แล้ว cap ที่ 25% ของที่ Kelly แนะนำ
```

### 3.3 Risk manager (`core/risk_manager.py`)
ไม้ละ ≤ 5% ของทุน · slippage ≤ 2% · ขาดทุนรายวัน −10% → **circuit breaker** · ถือพร้อมกัน ≤ 20 ไม้ · liquidity ขั้นต่ำ $100

### 3.4 อื่น ๆ: **copy trading** (ติดตามวอลเล็ตท็อปแล้วลอกไม้), simulation mode, dashboard

### 3.5 จุดอ่อน
- σ เป็น **ตารางตายตัว** ไม่ใช่รายเมือง/ไม่เรียนรู้ — จุดที่ PolyWeather และ weatherbot ทำได้ดีกว่ามาก
- รองรับแต่คำถามแบบ yes/no ("สูงกว่า X") ไม่ได้ทำ bucket ladder แบบตลาดจริงของเรา
- commit ล่าสุด ก.พ. 2026 (ไม่อัปเดตแล้ว), ไม่มีหลักฐานผลทดสอบ

---

## 4) ตารางเปรียบเทียบ 3 repos + เครื่องมือของเรา

| | PolyWeather | weatherbot (fork) | polyBot-Weather | **wxedge (ของเรา)** |
|---|---|---|---|---|
| เมือง | 51 | ~30 | ~20 (US เป็นหลัก) | **50** |
| แหล่งพยากรณ์ | Open-Meteo suite (หลายโมเดล) | ECMWF + regional รายเมือง + METAR | NWS gridpoints | **ensemble 3 ตัว (119 members) + deterministic 12 ตัว** |
| โมเดลรวม | **DEB inverse-MAE + bias penalty + decay + heat-day ×2** | inverse-variance (1/σ²) จาก σ ที่ calibrate เอง | เลือกแหล่งเดียว (NWS) | bias-corrected blend (w1.5 best_match) |
| σ/bias | per lead × temp-stratum + shrinkage + recency | per city+source, **pooled fallback**, EWMA bias | ตาราง RMSE ตายตัว | sigma_res จาก RMSE²−spread² + tuned scale |
| การตรวจสอบคุณภาพ | **PIT / cov90 / chi²** | ไม่มี | ไม่มี | hit rate + Brier (ยังไม่มี PIT/cov90) |
| ระหว่างวัน | **hard floor + trend + weather suppression** | invalidation exit (obs > ขอบ bin) | โพสต์เฉพาะ D+ | **intraday empirical increment (ของเราใหม่)** |
| กติกาเข้า | ไม่เปิด (private) | EV ≥ 0.10 + ask จริง + spread ≤ 0.03 + vol + ช่วงเวลา | edge ≥ 10% + z ≥ 1.5 | p ≥ 0.90 / edge ≥ 0.15 + ราคา 0.02–0.25 |
| กติกาออก | ไม่เปิด | stop/trailing/TP/invalidation | ไม่มี (hold) | hold ถึงปิด (วัดแล้วว่า TP แย่ลง) |
| จริง/จำลอง | live | live + paper | live + simulation + copy trade | วัดผลย้อนหลัง + forward test (journal) |
| **hit rate กับผลตัดสินจริง** | ไม่เปิดเผย | ไม่เปิดเผย | ไม่เปิดเผย | **76–88% (intraday) · 70.5% (ไม้ถูก)** |
| License | **AGPL-3.0** | MIT | MIT | ของเราเอง |

**ช่องว่างที่เราถมแล้วและ repo อื่นไม่มี**: เราเทียบกับ **bucket ที่ตลาด resolve จริง** (4,142 ตลาด-วัน) ไม่ใช่ค่าประมาณจากสถานี; เรามีราคาทุก bin (220,338 แถว) วัดกลยุทธ์ "bin ถูก"; และเราวัด "ทายหลัง peak" เป็นระบบ

**ช่องว่างที่เขามีและเรายังไม่มี**: (1) live ask/spread gate, (2) กติกาออกแบบ invalidation/stop, (3) ตัวชี้วัด calibration เชิงสถิติ (PIT/cov90/chi²), (4) การทำ DEB-style ถ่วงโมเดลจาก MAE รายเมือง (เราถ่วงแบบคงที่/จูนรวม), (5) risk manager + journal PnL รายไม้

---

## 5) ผลทดลองจริง: นำไอเดียเขามาใช้กับ wxedge (วัดแล้ววันนี้)

### 5.1 Weather suppression + trend rate (จาก PolyWeather)
รัน `adopt_suppression.py` กับ 17 เมือง (2,668 แถว เมือง-วัน-ชั่วโมง) ดึง `wxcodes`/`skyc1` จาก IEM:

| variant | 15:00 | 16:00 |
|---|---|---|
| baseline ของเรา | **45.3%** | **52.9%** |
| ฝน ×0.85 | 45.3% | 52.9% |
| ฝน ×0.70, พายุ ×0.55 | 45.2% | 52.9% |
| ฝน ×0.70 + เมฆ OVC ×0.85 | 45.2% | 52.9% |
| + trend 0.5 | 23.6% | 26.5% |
| trend เดี่ยว 0.5 / 1.0 | 23.6% / 19.4% | 26.5% / 25.1% |

**สรุป**: suppression **ไม่ช่วย** เพราะการแจกแจงของเราสร้างจาก **ผลจริง 30 วันย้อนหลังของสถานีนั้นเอง** — ฝน/เมฆถูกสะท้อนอยู่ในนั้นแล้ว (ต่างจาก PolyWeather ที่ base เป็น Gaussian จากโมเดล จึงต้องสั่งลดด้วยมือ)
**trend rate ทำให้พัง** เพราะ double-count: เราใช้ increment จริงจากอดีตอยู่แล้ว พอ shift ตามความชันชั่วโมงล่าสุดอีก → ขยับเกิน 1 bin ทั้งหมด → **ไม่นำมาใช้ทั้งสองอย่าง** (บันทึกไว้ห้ามลองซ้ำ)

### 5.2 กติกาออก (จาก weatherbot) กับกลยุทธ์ bin ถูกของเรา (349 ไม้, เข้า 16:00)
| กลยุทธ์ | ROI |
|---|---|
| **ถือถึงปิดตลาด (เดิม)** | **+384%** |
| take-profit +55% | +280% ❌ |
| take-profit +30% / +100% | +271% / +299% ❌ |
| **stop-loss −25%** | **+405%** ✅ |
| stop-loss −50% | +412% |
| ขายทิ้งที่ 17:00 ทุกไม้ | +248% ❌ |

**สรุป**: take-profit **ทำลายกลยุทธ์ bin ถูก** เพราะไม้ถูกที่ชนะให้ผลตอบแทน +1,000% – ต่างจากตลาดวัน (แบบที่ weatherbot เล่น) ที่ TP เหมาะกว่า → **นำมาใช้เฉพาะ stop-loss เป็น "ตัวเลือก"** (ได้ ROI เพิ่ม ~21 จุด) ไม่ใช้ TP

### 5.3 Wunderground เป็นแหล่ง obs (จาก weatherbot)
ทดสอบผ่าน endpoint ที่เขาระบุ (คีย์สาธารณะ) เทียบ 30 เมือง-วัน:
- WU ให้ 24 ค่าต่อวัน (รายชั่วโมง) — **ไม่ใช่ 5 นาที**
- **WU ตรง bucket ที่ตลาดตัดสิน 30/30 = 100%** และ **IEM ก็ 30/30 = 100%**
- ผลต่าง WU − IEM เฉลี่ย +0.04 °F (เสมอกัน)

**สรุป**: WU ใช้เป็น **แหล่งสำรอง/ตรวจสอบไขว้** ได้ (และมี `wx_phrase` = ข้อความอากาศ ใช้เตือนได้) แต่ **ไม่ใช่ 5 นาที** จึงไม่ได้แก้ปัญหาหลักของเรา (METAR รายชั่วโมง vs ข้อมูล 5 นาทีของ NOAA) — และ **ยืนยันว่า IEM ที่เราใช้อยู่ถูกต้อง** (บทเรียน: เราไม่จำเป็นต้องเปลี่ยนไปใช้ WU อย่างที่ repo นั้นทำ เพราะเขาวัดกับ VC ซึ่งเพี้ยน — ของเราวัดกับผลตัดสินจริงโดยตรง)

### 5.4 บทเรียนจากบั๊กในโค้ดเขา (ไม่ต้องเจอเอง)
1. **`CALIBRATION_MIN` สูงเกินจริง → calibration ไม่เคยรัน** (ไฟล์ว่างมาตลอด) — เราเจอแบบเดียวกันตอนเลือกเมืองจาก train (overfit) → บทเรียน: ต้องเตือนเมื่อ "กลไกไม่เคยทำงาน" ไม่ใช่ปล่อยเงียบ
2. **bias รวมหลายเมืองแบบ EWMA = มั่ว** (น้ำหนักหมดไปกับเมืองสุดท้ายของ loop ไม่ใช่ข้อมูลใหม่สุด) → ต้องเก็บ (วันที่, error) แล้วเรียงตามเวลา ก่อนทำ EWMA ← ตรงกับที่เราต้องระวังถ้าทำ bias pool
3. **`outcomePrices` = [YES, NO] ไม่ใช่ bid/ask** — ราคาที่ใช้ตัดสินใจต้องดึง ask จาก book จริง
4. **ติดตามทั้ง "closed" และ "resolved"** — ตลาดที่ปิดเพราะหมดเวลา (ไม่มี position) เกือบทั้งหมดถูกทิ้งไปจากสถิติ calibration ทำให้ตัวอย่างเหลือน้อยเกิน

---

## 6) แผนนำไปใช้กับ wxedge (เรียงตามความคุ้มค่า)

| ลำดับ | งาน | ที่มา | เหตุผล/ผลที่คาด |
|---|---|---|---|
| **P0** | เพิ่ม **ask + spread จริงจาก order book** ใน `cheap_live.py` และบันทึกใน log (ตอนนี้ใช้ราคาซื้อขายล่าสุด) | weatherbot | ปิดช่องว่างข้อจำกัด #1 ของ backtest เรา; Gamma ให้ `bestAsk`/`spread`/`orderMinSize` อยู่แล้ว |
| **P0** | เพิ่ม **stop-loss −25% (option)** ในกติกาไม้ถูก + journal | weatherbot | วัดแล้ว ROI +384% → +405% |
| **P1** | **ตัวชี้วัด calibration: PIT / coverage@90 / chi²** ต่อ (lead, ย่านอุณหภูมิ) | PolyWeather | ตรวจว่าความน่าจะเป็นที่ให้ "ซื่อสัตย์" ไม่ใช่แค่ hit rate — จำเป็นก่อนอัดไม้ใหญ่ |
| **P1** | **pooled σ/bias ตามหน่วย** (แทนการจูนรวมทั้งชุด) | weatherbot + PolyWeather | ตรงกับบทเรียน overfit ของเรา; ปรับต่อเมืองแบบมี shrinkage เท่านั้น |
| **P2** | **obs canonical scoring** (ความตรงสถานี + ความสด + น้ำหนักแหล่ง) ก่อนเลือกค่าที่ใช้ตัดสิน | PolyWeather | ทำให้ขยายเมืองใหม่ได้โดยไม่ต้องเลือกมือ |
| **P2** | **DEB-style ถ่วงโมเดลจาก MAE รายเมือง** แทนน้ำหนักคงที่ (w1.3/1.0/0.9) | PolyWeather | คาดว่าได้กับเมืองที่โมเดลใดโมเดลหนึ่งเด่น (เช่น NYC icon .75 vs ecmwf 1.04) |
| **P2** | **invalidation exit** สำหรับไม้ที่ถือระหว่างวัน (ถ้า obs > ขอบบน bin + buffer → ขาย) | weatherbot | ลดการขาดทุนแบบ "แพ้ทางคณิตศาสตร์" |
| P3 | risk manager (Kelly ×0.25, ไม้ละ ≤5%, circuit breaker −10%/วัน) | polyBot | จำเป็นเมื่อต่อเงินจริง |
| P3 | TAF / upper-air (vertical profile) | PolyWeather | ยังไม่คุ้มสำหรับเรา — edge ของเราคือ "หลัง peak" ที่ obs เองบอกคำตอบแล้ว |

**ไม่นำมาใช้ (ทดลองแล้ว)**: weather suppression, trend rate, take-profit

**หมายเหตุลิขสิทธิ์**: PolyWeather = AGPL-3.0 (copyleft — ถ้าคัดลอกโค้ดเข้าเครื่องมือเราต้องเปิดซอร์สแบบเดียวกัน) และ repo ไม่ได้เปิด logic เทรดจริง · weatherbot และ polyBot = MIT (นำไอเดีย/โค้ดไปใช้ได้ แต่ควรอ้างอิง) → แนวทางที่ปลอดภัยและถูกต้อง: **นำ "ไอเดีย" มาทำเองตามที่รายงานนี้ แล้ววัดผลด้วยข้อมูลของเรา** (ตามที่ทำไปแล้วในข้อ 5)

---

## 7) คำถามที่ยังเปิด (และงานถัดไป)
1. **ask จริง vs ราคาในอดีต** — ต้องวัดว่าไม้ถูกที่ "เติมได้จริง" (มี size ที่ ask) ต่างจากตัวเลข backtest แค่ไหน → P0
2. **stop-loss ที่เหมาะกับ bin ถูก** — ลอง grid (10/15/20/25/35%) กับข้อมูล 85 วัน แล้วหาระดับที่มั่นคงทั้งสองครึ่งเวลา
3. **PIT ของเราเป็นเท่าไร** — ถ้าความน่าจะเป็น over-confident อยู่ ต้องเพิ่ม σ หรือ cap p ก่อนใช้ Kelly
4. **DEB กับ ensemble 119 members ของเรา** — ถ่วงจาก MAE รายเมืองโดยใช้ข้อมูล 85 วัน + shrinkage แล้วเทียบกับน้ำหนักคงที่ปัจจุบัน
5. **obs 5 นาทีของ NOAA** — ยังเป็น "ส่วนที่เหลือ 6%" ของความคลาดเคลื่อน; หาแหล่ง 5 นาทีที่ใช้ได้จริง (MADIS HF-METAR แบบ PolyWeather ใช้?) เพื่อปิดช่องสุดท้าย

---

*รายงานนี้เขียนจากโค้ดจริง (ยืนยันด้วยชื่อไฟล์/ฟังก์ชันทุกจุด) · ผลทดลองในข้อ 5 รันวันนี้กับข้อมูลของเราเอง*
*สคริปต์ที่เกี่ยวข้อง: `adopt_suppression.py` · `reports/adopt_suppression.md`*

*โคลน repo มาศึกษาใหม่ได้ด้วย:*
```bash
git clone --depth 1 https://github.com/yangyuan-zhen/PolyWeather.git
git clone --depth 1 https://github.com/hcharper/polyBot-Weather.git
git clone --depth 1 https://github.com/CCronos/weatherbot.git    # สำเนาของ alteregoeth-ai/weatherbot (ต้นฉบับถูกลบ)
```
*(โฟลเดอร์ที่โคลนไว้ชั่วคราวอยู่นอก workspace จึงไม่ถูกเก็บ — คำสั่งข้างบนใช้ทำซ้ำได้)*
