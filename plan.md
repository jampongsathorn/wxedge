# plan.md — Agent relay ผ่าน GitHub Issues (1 ต.ค. 2026)

ตาม skill `agentic-code-workflow` (Scan → Plan → Implement → Validate → Document)
**ผู้ใช้เลือก**: ทาง A (GitHub Issues relay) จากตัวเลือก A–D

## Goal

ให้ agent session อื่น (และตัวผมเอง) คุยกันได้ผ่าน **issue เดียวใน repo นี้** โดยผู้ใช้ไม่ต้อง copy-paste:
- ใครเสร็จงาน/มีคำถาม → เขียน comment (ข้อความรูปแบบกลาง อ่านด้วยเครื่องได้)
- อีกฝ่ายอ่านตอน "ตื่น" ของตัวเอง → ตอบกลับในเธรดเดิม

## ข้อจำกัดที่กำหนดดีไซน์ (ตรวจแล้ว)

- ผมตื่นเฉพาะเมื่อผู้ใช้พิมพ์ → relay เป็น **async mailbox** ไม่ใช่ live chat
- repo public → **ห้ามมี token/ความลับในข้อความ** · ข้อความทุกอันเป็นสาธารณะ
- ฝั่งอ่านต้องทำงานได้ **ไม่ต้องมี token** (anonymous) เพื่อให้ agent ไหนก็อ่านได้
- ฝั่งเขียนต้องมี token ที่มี Issues:write (PAT ผู้ใช้มี admin ✓ · Issues API 200 ✓)

## Impacted Files

| ไฟล์ | บทบาท |
|---|---|
| `relay.py` **(ใหม่)** | CLI stdlib ล้วน: `init` / `post` / `read` / `check` / `mark` · ข้อความมี metadata JSON 1 บรรทัด (`<!-- relay {...} -->`) |
| `data/relay_state.json` | issue number + last_read_id (tracked → persist ข้าม rehydration) |
| `RELAY.md` **(ใหม่)** | protocol ให้ agent อื่นอ่าน (raw URL) |
| `tests_chain.py` | T17a–e (ออฟไลน์ล้วน) |
| `README.md` | §32 วิธีใช้ + standing rule "เช็ค relay ก่อนตอบ" |

## กติกากลาง (สรุป)

- comment 1 อัน = 1 เรื่อง · บรรทัดแรก `<!-- relay {"from","to","subject","tag","ts"} -->` · เนื้อหาถัดลงมาเป็น markdown
- agent id: ฝั่งผม = `arena-wxedge` · อีกฝั่งตั้งเองได้ (ระบุใน `from`)
- author ของ GitHub comment อาจเป็นบัญชีเดียวกัน (PAT เดียว) → **ยึด field `from` เป็นหลัก**
- ห้ามใส่ token ในข้อความ · แนบหลักฐานเป็น path/commit/URL ไม่ใช่ข้อความยาว

## Validation (นิยามเสร็จ)

1. `python3 relay.py init` สร้าง issue จริง + `post` ข้อความแรกสำเร็จ (มี token)
2. `python3 relay.py read` อ่านได้ **แบบไม่ใช้ token** (anonymous) — พิสูจน์ว่า agent อื่นอ่านได้แม้ไม่มีสิทธิ์เขียน
3. `check`/`mark` ทำงานตาม state (unread → 0 หลัง mark)
4. T17a–e เขียว · T1–T16 เดิมไม่แตก
5. push แล้ว remote มี relay.py/RELAY.md/issue URL ใช้ได้จริง
