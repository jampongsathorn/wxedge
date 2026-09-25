# Bugfix systematic: ระบบโซ่เก็บข้อมูล (25 ก.ย. 2026)

Reproduce: `/tmp/reproduce.sh` · Regression guard: `tests_chain.py` (รันใน CI ทุก push)
วิธี: Reproduce → Root cause tree → Dependency search → Fix root → Fix all impacted → Validate → Regression guard

## สรุปบั๊ก 4 ตัว (เรียงตามความรุนแรง)

| # | อาการ | Root cause | หลักฐานก่อนแก้ | หลังแก้ |
|---|---|---|---|---|
| **B1** | เก็บข้อมูลได้ทุก **10 นาที** แทนที่จะเป็น 5 ในช่วงตัดสินใจ | gate เทียบ "เว้นขั้นต่ำ ≥ gap" แต่รอบตื่นห่างจริง **4 น. 45 วิ** → gap=5 ทำให้รอบเว้นรอบถูกข้าม | `reproduce.sh`: nyc ถูกข้ามที่ gap=5 | `ENTRY_GAP=4` · entry 15:00–16:30 · `CTX_GAP=11` → T1 ผ่าน |
| **B2** | โซ่หยุดเงียบ **25 นาที** หลังโซ่ถูกยกเลิก (state ค้างว่า "ยังมีโซ่") + watchdog ไม่รู้ | (ก) guard ตัดสินจาก state อย่างเดียว (ข) watchdog ดูแค่ heartbeat สด — ไม่รู้ว่า "ไม่มี run active" = ตาย | `reproduce.sh`: chain_loop ออกทันที · watchdog "ยังเต้นปกติ" | watchdog ตัดสินจาก **beat + จำนวน run active** และ**ปลด state เอง** → T3/T5/T6 ผ่าน |
| **B3** | push ชนกัน → rebase ค้าง → **push ไม่ได้อีกเลย** (ข้อมูลหาย 75 นาที) | `pull --rebase` เปราะเมื่อไฟล์ generate (docs/snapshots) ชนกัน · abort ไม่ถูกเรียก | T4a: ตรรกะเดิม push ไม่ผ่านทั้ง 3 ครั้ง | `chain_git_push_round`: abort rebase/merge ที่ค้าง + `merge -X ours` + stage ทีละ path → T4b/T4c ผ่าน |
| **B4** | `run_forward_test.sh` (cron VPS/Pi) **ตายทันที** ที่บรรทัด 28 | gate v2 เปลี่ยน interface (ลบ `--hour-window`) แต่ผู้เรียกเดิมไม่ถูกอัปเดต · `2>/dev/null \|\| true` กลืน error | `reproduce.sh`: `gate.py: error: unrecognized arguments` | เรียกด้วย flag v2 + ใช้ lib กลาง → T2/T7e ผ่าน |

## บั๊กที่เจอเพิ่มระหว่างทำ (ซ่อนอยู่ ไม่มีใครรู้)

**B5 — ข้อมูลหายเงียบเมื่อโฟลเดอร์หายไป 1 ตัว**
`git add -f data reports docs` ถ้า `reports/` ไม่มีอยู่ → **คำสั่งล้มทั้งคำสั่ง** (ถูกกลืนด้วย `2>/dev/null || true`) → ไม่ stage อะไรเลย → ขึ้นว่า "ไม่มีข้อมูลใหม่" → **ไม่ push ทั้งที่มีข้อมูลใหม่** = ข้อมูลหายโดยไม่มีสัญญาณ
แก้: `chain_git_stage_paths` — stage ทีละ path, path ที่ไม่มีถูกข้าม (T4b/T4c พิสูจน์ใน repo ที่ไม่มี reports/)

**B6 — gate error ถูกกลืนในลูป** (`2>/dev/null || true`)
ถ้า gate พัง (เช่น flag ผิด) ลูปจะเดินต่อโดย "ไม่มีเมืองถึงเวลา" เงียบ ๆ ทุกวัน — แก้: จับ stderr แล้ว log `⚠ gate ไม่ตอบ — สาเหตุ: ...` (T7g)

## Dependency & files ที่แก้ (ทั้งหมด ไม่ใช่ไฟล์เดียว)

| ไฟล์ | แก้อะไร |
|---|---|
| `deploy/chain_lib.sh` **(ใหม่)** | ชุดเดียวของ guard · state · stage · push · watchdog decision |
| `deploy/chain_loop.sh` | ใช้ lib (guard/push/state) · gate error ไม่กลืน · stage ผ่านตัวกลาง |
| `deploy/chain_watchdog.sh` | ตัดสินจาก beat + run active · ปลด state · โหมด `LOCAL=1` สำหรับทดสอบ |
| `deploy/run_forward_test.sh` | gate v2 flags + push ผ่าน lib (B4) |
| `deploy/github-actions/forward-test.yml` | template ค่าเก่า 15:30–16:30/10 นาที → ค่าใหม่ |
| `.github/workflows/forward-chain.yml` | `ENTRY_GAP=4` · `ENTRY_WIN=15:00-16:30` · `CTX_GAP=11` |
| `.github/workflows/chain-watchdog.yml` | inputs `stale_min`/`dry` สำหรับทดสอบ E2E · `contents: write` |
| `.github/workflows/tests.yml` | รัน `tests_chain.py` ด้วย |
| `tests_chain.py` **(ใหม่)** | T1–T7 regression guard |
| `reports/chain_bugfix.md` **(ใหม่)** | เอกสารนี้ |

## ตรวจสอบแล้ว (Validate)

- `python3 tests_chain.py` → ผ่านทั้งหมด (T1–T7: gap · flag callers · guard · push ชนกัน · watchdog matrix · chain_loop E2E offline · ความสอดคล้อง workflow)
- `python3 tests_live_v2.py` → ผ่านทั้งหมด (ไม่ถูกกระทบจากการ refactor)
- `bash -n` ทุกสคริปต์ · `yaml.safe_load` ทุก workflow
- ทดสอบ E2E บน GitHub Actions: dispatch watchdog ด้วย `stale_min=0` → ยกเลิก run ที่ค้าง + ปลด state + dispatch โซ่ใหม่ (ดู log ของ run)

## บทเรียนที่ฝังในโค้ด (กันกลับมา)

1. **ตรรกะเดียวต้องมีชุดเดียว** — คัดลอกแล้วลืม = "แก้ไฟล์เดียว พังอีกไฟล์" (B4 เกิดจากสิ่งนี้)
2. **ห้ามกลืน error** — `|| true` + `2>/dev/null` ทำให้ระบบหยุดเงียบ (B4/B6)
3. **"สด" ไม่ได้แปลว่า "อยู่"** — heartbeat ต้องอ่านคู่กับ run ที่ active (B2)
4. **รอบตื่นกับเกณฑ์เว้นระยะต้องสอดคล้องกัน** — ถ้า gap ≥ รอบตื่น จะถูกข้ามเป็นรอบคู่ (B1, T7d บังคับไว้)
5. **stage ทีละ path** — path หายต้องไม่ทำให้ข้อมูลหายทั้งชุด (B5)
