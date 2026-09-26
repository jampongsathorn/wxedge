# plan.md — rebuild backtest ด้วยราคาที่ซื้อได้จริง (26 ก.ย. 2026)

ตาม skill `agentic-code-workflow` (Scan → **Grill ✅ (ผู้ใช้ยืนยัน ก/ก/ก)** → **Plan** → Implement → Validate → Document)

## Goal

แทนที่ "ราคา stale" ใน backtest ด้วย **ราคาที่ซื้อได้จริงจากดีล (fills) จริง** เพื่อวัด hit rate/ROI ที่เทรดได้จริง
— ตอบว่า "ถ้าใช้กติกาเดิมของเรา (16:00 local · p≥0.90 · ask 0.02–0.25) เราจะได้ผลเท่าไรจริง ๆ"

## Root cause hypothesis

ราคาใน `cheap_bets.csv` มาจาก last-trade ที่ **ขอบชั่วโมง (stale)** — ตลาด reprice ตอนต้นชั่วโมง
(istanbul 0.095→0.975 · warsaw 0.095→0.960) และดีลจริงของ dallas แสดงว่า **หลัง 16:00 ไม่มีดีลเลยที่ราคานั้น**
→ hit/ROI เดิม (87.9% / +817%) มาจากราคาที่ซื้อไม่ได้

## Impacted Files (rg แล้ว)

| ไฟล์ | สถานะ |
|---|---|
| `exec_backtest.py` **(ใหม่)** | เครื่องมือ rebuild (ออฟไลน์หลัง cache · อ่าน/เขียนเฉพาะ data/exec_cache + reports/) |
| `cheap_live.py` | เพิ่ม `token_id` เป็นธาตุที่ 6 ของ bins ใน snapshot (เพิ่มข้อมูล ไม่ลบ · reader เดิมกันด้วย `len(b)<4` อยู่แล้ว) |
| `trades_probe.py` **(ใหม่)** | เก็บดีลจริงรอบหน้าต่างเข้าไม้เข้า `data/trades_live.jsonl` (ตอบ Q3) |
| `deploy/chain_loop.sh` | เรียก `trades_probe.py` หลัง `cheap_live.py --log` + stage path ใหม่ |
| `tests_chain.py` | T11 (คณิต executable) · T12 (probe: window guard + dedupe) |
| `README.md` | §28 สรุปผล rebuild |

## Steps

1. `exec_backtest.py`: cohort 249 ไม้ (h=16 · p≥0.90 · 0.02–0.25) → conditionId/YES-token → ดึงดีลจริง (แคช) →
   **executable price** = ราคาดีล BUY ที่ทำให้ notional สะสมครบ `--stake` ($12) ภายใน `--window-min` (20 นาที)
   - ไม่ครบ = **no-fill** (ไม่มีไม้ — ไม่นับเป็นแพ้)
   - ราคา executable > pmax (0.25) = **ไม่เข้า** (กติกาจริงจะไม่ซื้อ)
2. `trades_probe.py` + hook เข้าโซ่ (เฉพาะเมืองในหน้าต่าง 14:30–17:30 local)
3. Tests ปิดช่องถอยหลัง
4. รัน rebuild → เขียน `reports/exec_backtest.md`

## Validation (นิยามเสร็จ)

- `python3 exec_backtest.py` จบได้ + ได้ CSV/MD ที่ n ครบและบวกกันได้ (traded + expensive + nofill + no-market = cohort)
- เทสต์: T11/T12 เขียว + `tests_live_v2.py` เขียว + `tests_chain.py` เขียว
- ตัวเลขในรายงานมาจากดีลจริงเท่านั้น (มีไฟล์ cache ยืนยัน) และแสดงเทียบกับ baseline stale ให้เห็นชัด
