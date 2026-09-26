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

---

# รอบที่ 2 — บั๊กที่รอ "ไม้จริงครั้งแรก" (26 ก.ย. 2026)

ไม้จริงไม้แรกของระบบเข้าวันที่ 25 ก.ย. 2026 (dallas · panama-city) → เผยบั๊กที่หลับอยู่ 4 ตัว
เพราะทุกรอบก่อนหน้านี้ n=0 (ยังไม่เคยมีแถวที่ resolved) ทำให้โค้ดเส้นทาง "มีข้อมูลจริง" ไม่เคยถูกเรียก

## B7 — `%+,.0f` ใช้ไม่ได้ใน %-format (crash ตอนรันครั้งแรก)

- **อาการ**: `python3 forward_resolve.py` → `ValueError: unsupported format character ','`
- **สาเหตุ**: `"%+,.0f" % v` — Python ไม่รองรับ comma flag ใน %-formatting (ใช้ได้เฉพาะ `format()`/f-string)
- **แก้**: เพิ่ม `money(v) = format(v, "+,.0f")` แล้วเรียกใช้ทั้ง 4 จุด (root fix ไม่ใช่แก้ทีละจุด)
- **กันกลับ**: T8f (ไม่มี `%+,` เหลือในไฟล์) + T8g (money() ใส่ comma/เครื่องหมายถูก)

## B8 — `summarize()` KeyError 'YES'

- **อาการ**: หลัง B7 ผ่าน → `KeyError: 'YES'`
- **สาเหตุ**: คีย์ฝั่งถูกเก็บเป็น `"yes"/"no"` (ตัวเล็ก) แต่ summarize อ่าน `r["YES"]`
- **แก้**: `side = "yes" if str(r["side"]).upper() == "YES" else "no"` แล้วเช็ค `side == "yes"`
- **กันกลับ**: T8a/T8b (สรุป YES/NO ถูกต้อง + brier ถูกคำนวณ)

## B9 — `render_report()` ขาด `return L`

- **อาการ**: ฟังก์ชันคืน `None` → บรรทัดถัดไปพัง
- **สาเหตุ**: ตอน refactor แยก render_report ออกมา (เพื่อให้เทสต์ออฟไลน์ได้) ลืม `return`
- **บทเรียน**: `ast.parse` (syntax) ไม่เท่ากับเทสต์พฤติกรรม — ต้องเรียกใช้จริง
- **กันกลับ**: T8c (render_report คืน list) + T8d (ทุกบรรทัดเป็น str)

## B10 — เอา float ดิบใส่ list ที่จะ `"\n".join()`

- **อาการ**: `TypeError: sequence item ... expected str instance, float found` (เจอเพราะเทสต์ join จริง)
- **สาเหตุ**: `L += [..., o["brier"], ""]` — brier เป็น float ถูกใส่ตรง ๆ
- **แก้**: `"%…%.4f" % o["brier"]`
- **กันกลับ**: T8e (join สำเร็จ) + T8h (`--report-only` rc=0)

**หมายเหตุวิธีเจอบั๊กทั้ง 4**: จำลองข้อมูล 3 แถว (มีทั้ง YES/NO และมี resolved จริง) แล้วรันทุกเส้นทาง
— บั๊กทุกตัวรอ "ข้อมูลก้อนแรก" อยู่ การเทสต์ด้วย n=0 จึงผ่านตลอดกาลโดยไม่มีความหมาย

---

# รอบที่ 3 — บทเรียนจากไม้จริงที่แพ้ (obs ล้าช้า + ตลาดรู้ก่อน) · B11–B12

ไม้จริง 2 ไม้แรก (25 ก.ย. 2026) **แพ้ทั้งคู่** — ผลไม่เป็นทางการจนตลาดปิด แต่ราคา + ข้อมูลสถานีชี้ชัด:
dallas ไปจบที่ 92-93°F (เราเดิมพัน 88-89°F) · panama จบที่ 30°C (เราเดิมพัน 29°C)

## หลักฐานยืนยันความจริง (ground truth)

ตาราง METAR สถานี KDAL (IEM asos) วันที่ 25 ก.ย. 2026 เวลาท้องถิ่น:

| เวลา local | อุณหภูมิจริง |
|---|---|
| 14:53 | 89°F |
| **15:53** | **92°F** ← สูงสุดของวัน |
| 16:53 | 92°F |
| 17:53 | 91°F |

เราเข้าไม้ตอน 16:00 local → สถานีขึ้นไป 92°F แล้ว **แต่ระบบยังอ่านได้ 89°F**

## B11 — obs ล้าช้าได้ถึง 60 นาที เพราะ cache ของ `hourly_series` (root cause แท้)

- **อาการ**: โมเดลประเมิน bin 88-89°F ที่ p=0.967 · edge 0.927 (สูงผิดปกติ) · ตลาดตั้งราคาไว้เพียง 0.04
- **สาเหตุ**: `intraday.hourly_series()` เรียก `http_get(..., ttl=3600)` และ `cheap_live.model_dist()`
  เรียกโดยไม่ปิด cache → ทุกรอบใน 1 ชั่วโมงใช้ข้อมูลชุดเดิม (สูงสุดเห็นแค่ obs 14:53 = 89°F)
- **แก้**: แยกเป็น 2 คำขอ — **ประวัติ (≤ เมื่อวาน) cache 6 ชม.** (ข้อมูลนิ่ง) + **"วันนี้" ดึงสดเสมอ** (`no_cache=True`)
  เพราะการดึงสดทั้งก้อน 32 วัน/ครั้ง ทำให้ IEM ตอบ 429 (วัดจริง: 12 เมือง 75 วิ · 1 เมืองล้ม)
  → หลังแก้: 50 สถานี 319 วิ · **0 error** (รอบแรกที่ history ยังเย็น 570 วิ) · มี fallback ไปใช้ cache ถ้าโดน 429
- **พิสูจน์หลังแก้**: Dallas obs สูงสุด = **33.3°C = 92°F** (ตรงกับสถานีเป๊ะ) · Panama = 30.0°C
  → ระบบจะไม่เลือก 88-89°F ตั้งแต่แรก (เพราะรู้แล้วว่า max อยู่ที่ 92) **ไม้ที่แพ้ทั้ง 2 ไม้จะไม่เกิดขึ้น**
- **กันกลับ**: T9f (`model_dist` มี `no_cache=True`)

## B12 — เกต P_exceed: ราคารวมของ bin ที่สูงกว่าเรา (ด่านกัน obs ล้าช้า)

แนวคิด: ถ้าตลาดยังให้ราคา bin ที่ **สูงกว่า** bin ที่เราจะเลือก รวมกันมาก แปลว่าตลาดรู้ว่าอุณหภูมิ
ขึ้นไปกว่านั้นแล้ว → เรากำลังเข้าโดยข้อมูลล้าช้า

**หลักฐานจาก backtest** (165 ไม้ @16:00 · ราคา 0.02–0.25 · p ≥ 0.90):

| P_exceed | จำนวนไม้ | hit |
|---|---|---|
| < 0.05 | 87 | **96.6%** |
| 0.05–0.15 | 21 | 95.2% |
| ≥ 0.60 | 52 | **71.2%** |
| ฐานเดิม (ไม่มีเกต) | 165 | 87.9% |

เกณฑ์ที่เลือก: **P_exceed < 0.10** → n=103 · hit **96.1%** · ROI +993% (จาก 87.9% / +817%)

**ยืนยันกับเคสจริง**: ทั้ง dallas และ panama ที่แพ้ มี P_exceed = **1.00** (ตลาดให้ราคา bin ที่สูงกว่าเกือบเต็ม)
ข้อมูลนี้อยู่ใน snapshot ของเราอยู่แล้วตอนเข้าไม้ — แต่กติกายังไม่ได้ดู

- **แก้**: `compute_p_exceed(bins)` (ระดับโมดูล เทสต์ได้) + เกต `p_exceed < max_exceed` ใน `scan_city` + CLI `--max-exceed` + คอลัมน์ `p_exceed` ใน log
- **กันกลับ**: T9a–T9e (ตรรกะผลรวม/cap/or-above/ป้ายพัง) · T9g–T9j (เกต + CLI + schema ตรงกัน)

### สิ่งที่ *ไม่* เปลี่ยน (เพราะข้อมูลไม่สนับสนุน)

- ยังเข้าเวลา 16:00 local (ช่วง 14–19) — ตาราง hit ตามชั่วโมงยังชี้ว่า 16:00 คุ้มสุด
- ไม่ใส่ฟิลเตอร์ตามราคา ask 0.02–0.04 (hit 68.8% จากกลุ่มเล็ก n=16 — ไม่พอจะสรุป)
- ไม่ใส่ฟิลเตอร์ "ราคาตก" — กลับกัน กลุ่มนี้ hit 91.8% (ไม้ที่แพ้ของเราอยู่ในกลุ่มที่ hit สูง)
- ขนาดไม้เท่าเดิม (ยอดแรก 0.25–0.5%)

## แถว log เปลี่ยน schema (37 คอลัมน์)

เพิ่ม `p_exceed` เป็นคอลัมน์สุดท้าย · header ของ `data/cheap_live_log.csv` ถูก migrate แล้ว
(`forward_resolve.py` อ่าน fieldnames จาก header เดิม จึงไม่พัง) — T9j บังคับให้ header ตรงกับ LOG_COLS เสมอ
