# เทียบ “กลยุทธ์” อย่างละเอียด: obra/superpowers vs กลยุทธ์เทรดทั้ง 4 (3 repos + wxedge)

วันที่ 25 ก.ย. 2026 · ข้อเท็จจริงของ repo ตรวจจาก clone จริง (`/tmp/superpowers_study`, commit `5bf4e78` v6.4.1, 18 ก.ย. 2026) + GitHub API

---

## 0) คำตอบสั้นที่สุด (สำคัญ — อ่านก่อน)

**`obra/superpowers` ไม่ใช่ repo กลยุทธ์เทรด/พยากรณ์อากาศ และไม่มีอะไรเกี่ยวเลย**

หลักฐาน (สแกนทั้ง repo 2.4 MB / 15 สกิล / การ์ด 4,390 บรรทัด):

| คำค้น | จำนวนไฟล์ที่เจอ | หมายเหตุ |
|---|---|---|
| polymarket | **0** | — |
| kalshi | **0** | — |
| weather | **0** | — |
| forecast | **0** | — |
| probability | **0** | — |
| backtest | **0** | — |
| temperature / trading | 1 / 1 | เป็นคำในบริบทอื่น (ข้อความบรรยาย) |
| strategy / calibration | 7 / 8 | เป็น *engineering* ล้วน — “testing strategy”, “migration strategy”, “cost calibration” |
| bet / edge (48/44 ฮิต) | — | เป็นคำอังกฤษธรรมดา `between` / `better` / `edge case` (ตรวจทีละบรรทัดแล้ว) |

มันคืออะไรจริง ๆ: **“agentic skills framework & software development methodology”** — ระเบียบวิธีทำซอฟต์แวร์สำหรับ AI coding agent (15 สกิล + hook เปิดใช้อัตโนมัติ + ติดตั้งได้บน ~15 harness: Claude Code, Codex, Cursor, Gemini CLI, Copilot CLI, Kimi, Qwen, OpenCode, Devin, Antigravity, Muse, Hermes, Pi …)
ตัวเลข ณ 25 ก.ย. 2026: ⭐ 291,133 · fork 26,046 · MIT (© 2025 Jesse Vincent) · สร้าง 2025-10-09 · push ล่าสุด 2026-09-22 · open issues 401

**ดังนั้น “เปรียบเทียบกลยุทธ์อย่างละเอียด” ที่ทำได้จริงและมีประโยชน์มี 2 ชั้น** (รายงานนี้ทำทั้งสองชั้น):

- **ชั้น A — ระเบียบวิธี (methodology):** superpowers บังคับ “วิธีทำงานของ agent” แบบไหน เทียบกับวิธีที่ (ก) โปรเจกต์เรา และ (ข) 3 repos ใช้ → ใครมีวินัย/หลักฐานแบบไหน
- **ชั้น B — การแพ็กให้ LLM ใช้:** superpowers ใช้ “skill” (markdown + trigger) ส่วนเราใช้ “CLI + README 419 บรรทัด” → **นี่คือจุดที่เราลอกของเขาได้ทันที** เพราะ deliverable ของเราคือ “tool for LLM use”

> ⚠️ สิ่งที่ superpowers **ไม่**ให้: มันไม่เพิ่ม hit rate และไม่เพิ่ม ROI ของกลยุทธ์เทรดแม้แต่นิดเดียว (คนละโดเมน) — มันเพิ่ม **ความน่าเชื่อถือของงาน** และ **ความสามารถของ LLM ที่จะใช้เครื่องมือเราถูกกติกา**

---

## 1) superpowers ทำงานยังไง (ข้อเท็จจริงจากตัว repo)

### ลำดับงานที่ถูกบังคับ (จาก README §The Basic Workflow)
1. **brainstorming** — ก่อนเขียนโค้ด ถามให้ชัดว่าต้องการอะไรจริง ๆ เสนอทางเลือก ทำ “design doc” เป็นท่อน ๆ ให้คนอนุมัติ
2. **using-git-worktrees** — แยก workspace/branch ตรวจ baseline ว่าทดสอบผ่านสะอาดก่อนเริ่ม
3. **writing-plans** — แตกงานเป็น task ละ **2–5 นาที** ทุก task ต้องมี path ไฟล์จริง + โค้ดครบ + วิธีตรวจ
4. **subagent-driven-development** หรือ **executing-plans** — ทำงานทีละ task (ตัวเลือกแพง = subagent ใหม่ + review หลังทุก task / ตัวเลือกถูก = ทำในเซสชันเดียว + review ครั้งเดียว)
5. **test-driven-development** — RED-GREEN-REFACTOR บังคับ: เขียนเทสต์ให้ FAIL ก่อน → เขียนโค้ดน้อยสุดให้ผ่าน → commit (ถ้าเขียนโค้ดก่อนเทสต์ = **ให้ลบโค้ดนั้น**)
6. **requesting-code-review** — review เทียบกับแผน, รายงานตามระดับความรุนแรง, ระดับ critical = หยุด
7. **finishing-a-development-branch** — ยืนยันเทสต์, เสนอ merge/PR, เก็บ worktree

### “Iron Laws” (กฎเหล็กที่เขียนไว้เป็นตัวพิมพ์ใหญ่ในไฟล์สกิล)
- `verification-before-completion` → **“NO COMPLETION CLAIMS WITHOUT FRESH VERIFICATION EVIDENCE”** — ถ้าไม่ได้รันคำสั่งยืนยัน *ในข้อความนี้* ห้ามบอกว่าผ่าน · ตาราง “Common Failures” ระบุว่า *หลักฐานเดิม ๆ / คำว่า “ควรจะผ่าน”* ใช้ไม่ได้
- `systematic-debugging` → **“NO FIXES WITHOUT ROOT CAUSE INVESTIGATION FIRST”** — 4 เฟส: หา root cause ก่อนเสนอวิธีแก้; “แก้ที่อาการ = ล้มเหลว”
- ปรัชญา 4 ข้อ: TDD · Systematic over ad-hoc · Complexity reduction (YAGNI/DRY) · **Evidence over claims**

### กลไกการส่งมอบ
สกิลเป็นไฟล์ markdown ที่มี frontmatter (`name`, `description` = “Use when …” เฉพาะ *เงื่อนไขที่ควรเรียกใช้* ไม่สรุปขั้นตอน) + โฟลเดอร์ `references/` โหลดเพิ่มเมื่อจำเป็น (“progressive disclosure”) + `hooks/session-start` ยิงอัตโนมัติเมื่อเปิดเซสชัน
และเขา **ทดสอบสกิลเหมือนทดสอบโค้ด** (`writing-skills`): “ถ้าคุณไม่เคยเห็น agent ทำผิดตอนไม่มีสกิล คุณไม่รู้ว่าสกิลสอนถูกเรื่องหรือเปล่า” → baseline (RED) → เขียนสกิล (GREEN) → ปิดช่องโหว่ (REFACTOR)

---

## 2) ชั้น A — เทียบระเบียบวิธี 5 ค่าย (ละเอียดทีละมิติ)

| มิติ | **superpowers** | **wxedge (เรา)** | **PolyWeather** | **weatherbot** | **polyBot-Weather** |
|---|---|---|---|---|---|
| เป้าหมาย | ให้ agent เขียนซอฟต์แวร์ถูกต้อง เชื่อถือได้ | ทาย bucket ที่ **ตลาด resolve จริง** ให้ถูก + มี edge พิสูจน์ได้ | พยากรณ์อุณหภูมิ + calibrate ความน่าจะเป็น (ขายเป็น Pro/ยังไม่เปิด execution) | บอทเทรด.day-ahead EV/Kelly | บอทเทรดสายเร็ว (latency-tolerant) |
| นิยาม “สำเร็จ” | **เทสต์เขียว + หลักฐานสด** จากคำสั่งที่รันเอง | **hit rate เทียบเฉลยตลาด** (4,142 ตลาด-วัน) + ROI (gross) | PIT/chi²/cov90 บน 1,450 เคส (ไม่มี PnL) | state.json (paper) | ไม่มี artifact เลย |
| วิธีตัดสินใจเลือกกลยุทธ์ | brainstorming + design doc + ขออนุมัติเป็นท่อน | วัดกับข้อมูลจริง แบ่ง train/test ตามเวลา (split 2026-08-15) | ออกแบบทฤษฎี (DEB, LightGBM, strata) แล้วมี self-check | ตั้งค่าพารามิเตอร์ตายตัว (σ ตาม lead, gate edge ≥10%) | ตั้งค่าตายตัว (RMSE-by-lead) |
| หลักฐานที่ต้องมีก่อนอ้างว่าดี | **ไฟล์ output ของคำสั่งยืนยัน** | `reports/*.md` ที่รันซ้ำได้ (`headtohead.py --split`) | `deb_normal_calibration_compare.json.bak` | `data/state.json` | **ไม่มี** |
| การทดสอบ | **TDD บังคับ + มี tests/ ใน repo เอง** (มีเทสต์ของทุกสกิล) | **ไม่เคยรันเทสต์** ในโฟลเดอร์นี้ — ใช้ “empirical harness” แทน | มี self-check ในโปรดักชัน (chi²/PIT/cov90) | ไม่มีเทสต์ | ไม่มีเทสต์ |
| เจอ bug ทำยังไง | 4 เฟส root cause → แก้ → ยืนยัน; “ห้ามแก้ที่อาการ” | แก้ทีละตัว + **จดบันทึกที่ผิดพลาด** (README §9, Errors) — *ใกล้เคียง แต่ไม่เป็นระบบ/ไม่บังคับ* | — | — | — |
| เอกสาร | design doc + แผนต่อ task + review prompt | README 419 บรรทัด + reports 15 ไฟล์ | README + spec ในเครื่องที่ขาย | README | README |
| การขนานงาน | `dispatching-parallel-agents`, `subagent-driven-development` (มีวินัย) | ทำคล้ายกันแบบ ad-hoc (chunked runs, background job, repo reading) | — | — | — |
| ความซับซ้อน | ลดความซับซ้อนเป็นเป้าหมาย (YAGNI/DRY) | เรียบ, ablation พิสูจน์ว่าความซับซ้อนเกินจำเป็นไม่ช่วย (σ ต่าง ≤0.1 จุด) | **ซับซ้อนสุด** (DEB + LightGBM + strata + TAF/JMA/HKO) แต่ได้ 37.6% day-ahead | กลาง | ต่ำ |
| แพ็กให้ LLM ใช้ | **สกิล markdown + trigger + hooks + ทดสอบสกิล** | CLI + README (agent ต้องอ่านเอง ไม่มี trigger, ไม่มี compliance test) | ไม่เปิด | ไม่เปิด | ไม่เปิด |

### ข้อสรุปเชิงเปรียบเทียบ (ชั้น A)
1. **superpowers เก่งเรื่อง “ความน่าเชื่อถือของกระบวนการ”** — แต่ไม่มีอะไรเกี่ยวกับตลาด/สถิติเลย มันตอบคำถามว่า “โค้ดนี้ทำงานถูกไหม” ไม่ใช่ “กลยุทธ์นี้ทำเงินไหม”
2. **จุดอ่อนร่วมของทั้ง 3 repos และของเรา = ไม่มี “เทสต์ที่พิสูจน์ว่ากลยุทธ์ทำเงิน”** — เราเป็นเจ้าเดียวที่สร้าง *empirical harness* (วัดกับเฉลยจริง + ราคาจริง + แบ่ง train/test) ซึ่งจริง ๆ คือ “TDD ของกลยุทธ์” นั่นเอง: **RED** = กลยุทธ์ยังไม่ผ่านเกณฑ์ตอนวัด → **GREEN** = ปรับจนผ่านบน train → **REFACTOR** = วัดซ้ำบน test ที่ไม่เคยเห็น
3. **ความต่างทางญาณทัศนะเรื่อง “หลักฐาน”**: superpowers ใช้ *output ของเทสต์* เป็นความจริง; เราใช้ *การ resolve ของตลาด* เป็นความจริง · ทั้งคู่เหมือนกันในแก่น: **“ห้ามอ้างก่อนมีหลักฐานสด”** → กฎนี้คือสิ่งที่ weatherbot ขาด (โพสต์/อวดได้ แต่ state.json คือ −22%)
4. **เราเดินถูกทางตามปรัชญาของเขาโดยบังเอิญ**: evidence over claims (caveat ROI gross), complexity reduction (ablation ตัด σ ออก), systematic (Errors & Dead Ends), verification (re-run headtohead ก่อนอ้างเลข) — แต่ทำแบบ **ไม่ได้บังคับ** → พลาดได้ถ้าลืม (เช่นเคยรัน `--uplift auto` ทับรายงาน, เคยปล่อย `reports/intraday.md` ค้างเก่า)

---

## 3) ชั้น B — จุดที่เราลอกได้จริง: แพ็ก wxedge เป็น “skill”

| ของ superpowers | สถานะของเรา (ก่อน) | สิ่งที่ทำแล้วในงานนี้ |
|---|---|---|
| `SKILL.md` + frontmatter (`description` = “Use when …” เฉพาะเงื่อนไขเรียกใช้) | ไม่มี — LLM ต้องอ่าน README 419 บรรทัด | **สร้างแล้ว**: `skills/wxedge-temp-market/SKILL.md` |
| `references/` โหลดเมื่อจำเป็น (progressive disclosure) | ความรู้กระจายใน reports/ 15 ไฟล์ | **สร้างแล้ว**: `references/playbook.md` (ตารางเมือง/เวลา/ROI/trap) |
| กฎที่ agent ต้องทำตามแบบ “ห้ามต่อรอง” | อยู่ในหัวคนอ่าน + README | ใน SKILL.md ใช้รูปแบบเดียวกับ Iron Law: **กติกา 5 ข้อ + “ห้ามทำ” 8 ข้อ (พร้อมตัวเลขที่วัดได้เป็นเหตุผล)** |
| ทดสอบสกิลด้วย subagent (RED-GREEN) | ไม่มี | **ขั้นต่อไป**: ยิงงาน “วันนี้ควรซื้อ bin ไหน” ใส่ agent ที่ *ไม่เห็น* สกิล → จดว่าแหกกติกาอะไร (คาดว่าแหก: ซื้อ bin <0.02 / ทายก่อน 15:00 / ใช้ take-profit) → แล้ววัดซ้ำตอนมีสกิล |

**แผนติดตั้ง (เลือกอย่างใดอย่างหนึ่ง)**
```bash
# Claude Code (personal skills)
cp -r /home/user/wxedge/skills/wxedge-temp-market ~/.claude/skills/

# Cross-runtime (Codex / Copilot CLI / Gemini CLI)
cp -r /home/user/wxedge/skills/wxedge-temp-market ~/.agents/skills/

# หรือใช้แบบ superpowers เต็มระบบ
/plugin install superpowers@claude-plugins-official      # ใน Claude Code
```

---

## 4) แมป “สกิลของ superpowers” → งานจริงที่เราค้างอยู่ (และความผิดพลาดที่เคยเกิด)

| สกิล | ใช้กับอะไรในโปรเจกต์เรา | หลักฐานว่าจำเป็น (เหตุการณ์จริง) | ระดับ |
|---|---|---|---|
| **verification-before-completion** | ก่อนอ้าง “hit rate X%” / “ROI Y%” ทุกครั้ง ต้องรันคำสั่งสด ๆ ในข้อความนั้น | เคยมีรายงานเก่าค้าง (`reports/intraday.md` ยัง stale), เคยรัน `--uplift auto` ทับรายงาน | ⭐ P0 |
| **writing-plans** (task 2–5 นาที + วิธีตรวจ) | งานค้าง: regenerate `intraday.md`, forward test 14:00–19:00, floor+empirical fusion, winter backtest | แผน P0–P3 ใน `three_repos_study.md §6` ยังไม่ถูกแปลงเป็น task ที่ตรวจได้ | ⭐ P0 |
| **systematic-debugging** (4 เฟส + root cause) | data bugs: IEM end-date-exclusive, `prices-history?interval=max` = 0 จุด, NWS 5-min ไม่ตรงตลาด 56% | README §9 “ข้อผิดพลาดที่ห้ามทำซ้ำ” มี 5 ข้อ — เป็นผลของ root-cause แล้ว แต่ยังไม่เป็นเทมเพลต | P1 |
| **test-driven-development** | ทำให้ “empirical harness” เป็นวินัย: **ทุกข้ออ้างใหม่ต้องมีเทสต์วัดกับเฉลยจริงก่อน** (แบบ headtohead) | headtohead/adopt/cheap_analyze ทำแบบนี้อยู่แล้วโดยไม่เรียกชื่อ | P1 |
| **dispatching-parallel-agents** | งานหนักที่ทำอยู่บ่อย: ดึงราคาย้อนหลังหลายพันตลาด, อ่าน repo หลายตัว, backtest หลายชุด | เราแบ่ง chunk ด้วยมือ (`cheap_bets` 17 เมือง/งาน) เพราะเจอ timeout | P2 |
| **brainstorming** | ก่อนสร้างกลยุทธ์ใหม่ (เช่น floor+empirical fusion) — ถามให้ชัดว่าจะวัด “ชนะ” ยังไงก่อนเขียนโค้ด | เราเกือบทำ “0.25 + ชนะ” กว้างเกินไป จนต้องตีความใหม่ | P2 |
| **requesting/receiving-code-review** | review สคริปต์ก่อนใช้เงินจริง (เช็ค ask/size, fee, stop) | ยังไม่เคยมี review รอบจริง | P2 |
| **diagnosing-superpowers** | ไม่เกี่ยวกับโดเมนเรา — ใช้เมื่อสกิล/ฮาร์เนสเองมีปัญหา | — | — |

---

## 5) จะกันความผิดพลาดที่เคยเกิดขึ้นได้จริงไหม (ประเมินตรง ๆ)

| เหตุการณ์จริงในโปรเจกต์เรา | สกิลที่จะช่วย | ช่วยได้จริงแค่ไหน |
|---|---|---|
| `--uplift auto` ทับ `reports/intraday.md` (ข้อมูลเสียหาย) | verification-before-completion + writing-plans | **ได้เต็ม ๆ** — แผนต้องบอก “ไฟล์ที่จะเขียนทับ + คำสั่งตรวจว่าถูก” ก่อนรัน |
| รายงานหัวข้อ “95%%” ค้าง, `intraday.md` เก่า | verification-before-completion | **ได้** — บังคับอ่าน output จริงก่อนถือว่าจบ |
| ใช้ NWS 5-min เป็น obs แล้ว hit rate ตก | systematic-debugging (root cause) | **ได้** — 4 เฟสบังคับหาสาเหตุ (การวัดเวลา/ความละเอียด) แทนการเดา |
| เข้าใจผิดว่า “ทายถูก = ทำเงิน” | ไม่มีสกิลไหนช่วย | ❌ **ต้องใช้หลักฐานโดเมนของเราเอง** (ชั้นเงินใน headtohead) |
| weatherbot ขาดทุน −22% ทั้งที่โค้ด “น่าจะ” ผ่านเทสต์ | — | ❌ TDD ป้องกันไม่ได้: **เทสต์เขียวทั้งหมดแต่กลยุทธ์ขาดทุนได้** — ต้องมีหลักฐานระดับตลาด (ของเรา) |
| cheap-bin trap <0.02 ชนะ 6% | ไม่มีสกิลไหนช่วย | ❌ ต้องเป็น **กติกาที่เขียนไว้** (อยู่ใน SKILL.md ตอนนี้) เพื่อไม่ให้ LLM เผลอซื้อ |

**สรุป:** สกิลของ superpowers กันความผิดพลาด *ระดับวิศวกรรม* ได้ดี (ไฟล์ทับ, รายงานเก่า, แก้ที่อาการ) แต่ **ไม่กันความผิดพลาดระดับตลาด** (เทรดขาดทุน, ราคาไม่ fill, trap) → งานตลาดต้องมี “หลักฐานโดเมน” ของเราเอง ซึ่งเรามีแล้วและตอนนี้ถูกย่อลงเป็นกติกาในสกิล

---

## 6) ตารางตัดสิน (รวมทุกชั้น)

| ประเด็น | คำตอบ |
|---|---|
| `obra/superpowers` เป็นกลยุทธ์เทรดไหม | **ไม่** — 0 ไฟล์เกี่ยวกับ weather/market/probability |
| เทียบกับ 3 repos แล้วใคร “ดีสุด” ในเชิงระเบียบวิธี | **superpowers** (วินัยสูงสุด: TDD บังคับ + เทสต์จริง + กฎ “ห้ามอ้างก่อนมีหลักฐาน”) — แต่ **คนละโดเมน** เอามาเทียบกับกลยุทธ์เทรดตรง ๆ ไม่ได้ |
| ในบรรดากลยุทธ์เทรด ใครดีสุด | **wxedge (เรา)** 50.9% day-ahead / 88.8% intraday / ROI +493% (gross) · รองลงมา PolyWeather (ไอเดีย hard floor +39.7 จุด) · weatherbot (ดีไซน์ day-ahead 45.5% แต่ paper −22%) · polyBot (34.7%, ไม่มีหลักฐาน) |
| เราควรทำอะไรกับ superpowers | **ไม่ต้องติดตั้งทั้งระบบ** ถ้าเป้าหมายคือเทรด — เอา 3 สกิลมาใช้ (verification-before-completion, writing-plans, systematic-debugging) และ **ลอกรูปแบบการแพ็ก skill** มาใช้ทำเครื่องมือของเรา (ทำแล้วในงานนี้) |
| ได้ edge เพิ่มไหมจาก superpowers | **ไม่** — มันไม่แตะข้อมูล/สถิติ/ราคา · สิ่งที่ได้คือความน่าเชื่อถือ + ลด human error + ทำให้ LLM ใช้เครื่องมือเราถูกกติกา (ซึ่งลดความเสี่ยงที่ ROI จะกลายเป็นลบเพราะใช้ผิด) |

---

## 7) ข้อจำกัดของบทเปรียบเทียบนี้ (พูดตรง)

1. **เทียบข้ามโดเมน** — superpowers วัดผลด้วย “เทสต์ผ่าน/ไม่ผ่าน” ส่วนกลยุทธ์เทรดวัดด้วย “ตลาด resolve ถูกไหม + เงิน” → ตารางนี้เทียบ *ระเบียบวิธี* ไม่ใช่ *ผลงาน*
2. **ไม่ได้รัน superpowers จริง** ในสภาพแวดล้อมนี้ (มันต้องติดตั้งเป็น plugin ใน harness ของ coding agent) — ข้อความจาก README/สกิลเป็นแหล่งปฐมภูมิ (ตรวจจากไฟล์จริง)
3. **ตัวเลขของเรา (ROI) ยังเป็นขอบเขตบน** — ราคาซื้อขายล่าสุด ไม่หัก fee/spread, 85 วันฤดูเดียว (เหมือนรายงาน `headtohead.md`)
4. **สกิลที่สร้างให้ (`skills/wxedge-temp-market`) ยังไม่ผ่านการทดสอบกับ subagent** — ตามหลักของ `writing-skills` เอง: “สกิลที่ยังไม่เคยเห็น agent ทำผิดตอนไม่มีมัน = ยังไม่รู้ว่าสอนถูกเรื่อง” → งานถัดไปคือ RED test (ดู §3)
5. สถิติ repo (ดาว/fork/วันที่) ตรวจจาก GitHub API ณ 25 ก.ย. 2026 และจะเปลี่ยนเมื่อเวลาเปลี่ยน
