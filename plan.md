# plan.md — ตั้ง execution_report.py รันอัตโนมัติใน daily chain (1 ต.ค. 2026)

ตาม skill `agentic-code-workflow` (Scan → Plan → Implement → Validate → Document)
**อนุมัติโดยผู้ใช้**: "ok" (ตอบข้อเสนอต่อท้ายรายงาน execution_report.md)

## Goal

ให้ `reports/execution_report.md` (ตาราง PnL 4 แบบ: last-trade / best-ask / trade-replay / ladder-VWAP
+ fill rate + slippage) **อัปเดตเองทุกวัน** — ไม่ต้องรันมือ — เพื่อให้หลักฐานระดับ execution สดใหม่เสมอ
เมื่อไม้ใน log เพิ่มขึ้น หรือมี depth_live.jsonl เข้ามา

## ทำไมวันละครั้ง (ไม่ใช่ทุกรอบ)

- `--fetch` ยิง data-api ต่อไม้ที่ยังไม่มี trade-replay price → งานรายวันพอ (ไม้ grew แบบ slow)
- รายงานถูก stage โดย `chain_git_push_round` (stage `data reports docs` ทุกรอบ) → commit รอบถัดไปอัตโนมัติ
- กัน API หนัก: อยู่ใน daily block ที่มี marker `data/.daily_done_$(date)` กันรันซ้ำอยู่แล้ว

## Impacted Files

| ไฟล์ | การเปลี่ยน |
|---|---|
| `deploy/chain_loop.sh` | daily block: เพิ่ม `execution_report.py --fetch` ต่อจาก `edge_monitor.py` |
| `deploy/run_forward_test.sh` | daily block (สำรอง): เพิ่มบรรทัดเดียวกัน |
| `tests_chain.py` | T16a–c: hook ครบทั้ง 2 ตัวรัน · รันออฟไลน์ได้ · รายงานมีตาราง 4 แบบ + ข้อจำกัด |
| `README.md` | §31 สรุปการตั้ง autorun |
| `reports/execution_report.md` | จะถูกเขียนทับโดยระบบเอง (fresh timestamp) |

## Safety check (ยืนยันจากโค้ดจริง)

- `load_jsonl()` คืน `[]` ถ้าไฟล์ `depth_live.jsonl` ยังไม่มี → รันก่อนมีข้อมูล ladder ได้ ไม่ crash
- `--fetch` มี try/except ต่อไม้ → API ล่ม/429 ไม่ทำให้ทั้งรอบล้ม (มี `|| true` + timeout ซ้อนอีกชั้น)
- ยังไม่มีไม้ใน log → เขียน "ยังไม่มีไม้ใน log" แล้วออก rc=0

## Validation (นิยามเสร็จ)

1. `bash -n deploy/chain_loop.sh` + `deploy/run_forward_test.sh` ผ่าน
2. `python3 execution_report.py --fetch` จบ rc=0 · reports/execution_report.md อัปเดต timestamp
3. T16a–c เขียว · T1–T15 เดิมไม่แตก · `tests_live_v2.py` เขียว
4. push แล้ว remote มี commit + CI เขียว (tests.yml)
