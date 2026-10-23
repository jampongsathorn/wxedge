# execution_report — PnL 4 แบบ เทียบกัน (หลักฐานระดับ execution)

อัปเดต 2026-10-23 03:21Z · ไม้ใน log 24 · stake $12 · หน้าต่าง 20 นาที

| แบบ | ไม้ | ชนะ | hit | PnL รวม (ที่ stake นี้) |
|---|---|---|---|---|
| 1) last-trade (วิธีเก่า) | 24 | 3 | 12.5% | $+11745.37 |
| 2) best-ask ณ ตอนเข้า | 24 | 3 | 12.5% | $+11741.77 |
| 3) trade-replay (ดีลจริงสะสม) | 10 | 2 | 20.0% | $-88.30 |
| 4) ladder-VWAP (slippage) | 24 | 3 | 12.5% | $+245.67 |

**รายไม้**

| เมือง | bin | ผล | last | ask (แหล่ง) | trade-replay | ดีล | ladder-VWAP | slippage |
|---|---|---|---|---|---|---|---|---|
| dallas | 88-89°F | แพ้ | 0.005 | 0.010 (CLOB book) | no-fill | 0 | 0.4422 | +0.4322 |
| panama-city | 29°C | แพ้ | 0.020 | 0.040 (CLOB book) | no-fill | 0 | 0.9750 | +0.9350 |
| wellington | 19°C | แพ้ | 0.110 | 0.130 (CLOB book) | 0.193 | 1 | 0.3304 | +0.2004 |
| wellington | 17°C | ชนะ | 0.707 | 0.789 (CLOB book) | 0.757 | 3 | 0.6885 | -0.1005 |
| wellington | 17°C | ชนะ | 0.732 | 0.824 (CLOB book) | 0.757 | 3 | 0.6885 | -0.1355 |
| denver | 80-81°F | ชนะ | 0.001 | 0.001 (CLOB book) | no-fill | 0 | 0.0241 | +0.0231 |
| lucknow | 32°C | แพ้ | 0.638 | 0.868 (CLOB book) | 0.967 | 2 | 0.1689 | -0.6991 |
| lucknow | 32°C | แพ้ | 0.788 | 0.836 (CLOB book) | 0.967 | 2 | 0.1689 | -0.6671 |
| lucknow | 32°C | แพ้ | 0.949 | 0.966 (CLOB book) | 0.967 | 2 | 0.1689 | -0.7971 |
| lucknow | 33°C | แพ้ | 0.069 | 0.090 (CLOB book) | 0.761 | 5 | 0.2654 | +0.1754 |
| lucknow | 33°C | แพ้ | 0.021 | 0.033 (CLOB book) | 0.761 | 5 | 0.2654 | +0.2324 |
| lucknow | 32°C | แพ้ | 0.946 | 0.979 (CLOB book) | 0.967 | 2 | 0.1689 | -0.8101 |
| istanbul | 13°C or below | แพ้ | 0.605 | 0.659 (CLOB book) | no-fill | 0 | 0.3020 | -0.3570 |
| istanbul | 13°C or below | แพ้ | 0.621 | 0.640 (CLOB book) | no-fill | 0 | 0.3020 | -0.3380 |
| istanbul | 15°C or below | แพ้ | 0.542 | 0.564 (CLOB book) | no-fill | 0 | 0.6880 | +0.1240 |
| istanbul | 15°C or below | แพ้ | 0.523 | 0.529 (CLOB book) | no-fill | 0 | 0.6880 | +0.1590 |
| istanbul | 15°C or below | แพ้ | 0.560 | 0.599 (CLOB book) | no-fill | 0 | 0.6880 | +0.0890 |
| istanbul | 15°C or below | แพ้ | 0.606 | 0.687 (CLOB book) | no-fill | 0 | 0.6880 | +0.0010 |
| istanbul | 15°C or below | แพ้ | 0.609 | 0.695 (CLOB book) | no-fill | 0 | 0.6880 | -0.0070 |
| istanbul | 15°C or below | แพ้ | 0.684 | 0.768 (CLOB book) | no-fill | 0 | 0.7390 | -0.0290 |
| istanbul | 15°C or below | แพ้ | 0.624 | 0.713 (CLOB book) | no-fill | 0 | 0.7390 | +0.0260 |
| istanbul | 15°C or below | แพ้ | 0.702 | 0.739 (CLOB book) | no-fill | 0 | 0.7390 | +0.0000 |
| istanbul | 24°C | แพ้ | 0.042 | 0.052 (CLOB book) | 0.330 | 2 | 0.3300 | +0.2780 |
| istanbul | 15°C or below | แพ้ | 0.978 | 0.983 (CLOB book) | no-fill | 0 | 0.7390 | -0.2440 |

**ที่มา**: last = `yes_last` (CLOB prices-history) · ask = `book_best_ask`/`yes_ask` (Gamma) · trade-replay = ดีลจริงใน `trades_live.jsonl` (exec_price) · ladder-VWAP = `depth_live.jsonl`

## ข้อจำกัดที่ต้องเขียนให้แม่น

- **trade-replay = executed-trade path** ไม่ใช่ historical L2 order book — วัดได้ว่า "มีคนซื้อขายจริงที่ราคานี้" ไม่ใช่ "มีคำสั่งค้างให้ซื้อได้"
- **ladder-VWAP** ใช้ได้เฉพาะช่วงที่ `depth_live.jsonl` เก็บแล้ว (ตลาดอนาคต) — ย้อนหลังไม่ได้
- **book_parity** (วัด Gamma vs CLOB พร้อม timestamp) พบ diff ได้ถึง 0.08 ที่ delta_ms=0 → ราคา Gamma อาจค้าง ดังนั้นราคาเข้าให้ใช้ CLOB book เป็นหลัก (`--levels 0` เก็บทั้งเล่ม)
