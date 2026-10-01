# execution_report — PnL 4 แบบ เทียบกัน (หลักฐานระดับ execution)

อัปเดต 2026-10-01 16:18Z · ไม้ใน log 2 · stake $12 · หน้าต่าง 20 นาที

| แบบ | ไม้ | ชนะ | hit | PnL รวม (ที่ stake นี้) |
|---|---|---|---|---|
| 1) last-trade (วิธีเก่า) | 2 | 0 | 0.0% | $-24.00 |
| 2) best-ask ณ ตอนเข้า | 2 | 0 | 0.0% | $-24.00 |
| 3) trade-replay (ดีลจริงสะสม) | 0 | 0 | — | — |
| 4) ladder-VWAP (slippage) | 0 | 0 | — | — |

**รายไม้**

| เมือง | bin | ผล | last | ask (แหล่ง) | trade-replay | ดีล | ladder-VWAP | slippage |
|---|---|---|---|---|---|---|---|---|
| dallas | 88-89°F | แพ้ | 0.005 | 0.010 (CLOB book) | no-fill | 0 | — | — |
| panama-city | 29°C | แพ้ | 0.020 | 0.040 (CLOB book) | no-fill | 0 | — | — |

**ที่มา**: last = `yes_last` (CLOB prices-history) · ask = `book_best_ask`/`yes_ask` (Gamma) · trade-replay = ดีลจริงใน `trades_live.jsonl` (exec_price) · ladder-VWAP = `depth_live.jsonl`

## ข้อจำกัดที่ต้องเขียนให้แม่น

- **trade-replay = executed-trade path** ไม่ใช่ historical L2 order book — วัดได้ว่า "มีคนซื้อขายจริงที่ราคานี้" ไม่ใช่ "มีคำสั่งค้างให้ซื้อได้"
- **ladder-VWAP** ใช้ได้เฉพาะช่วงที่ `depth_live.jsonl` เก็บแล้ว (ตลาดอนาคต) — ย้อนหลังไม่ได้
- **book_parity** (วัด Gamma vs CLOB พร้อม timestamp) พบ diff ได้ถึง 0.08 ที่ delta_ms=0 → ราคา Gamma อาจค้าง ดังนั้นราคาเข้าให้ใช้ CLOB book เป็นหลัก (`--levels 0` เก็บทั้งเล่ม)
