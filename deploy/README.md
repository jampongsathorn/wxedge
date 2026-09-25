# deploy — รัน forward-test 24/7 ฟรี (life hack ฝั่ง dev)

เป้าหมาย: เก็บ `data/cheap_live_log.csv` (bid/ask จริง + ฝั่ง NO) ให้ได้ 1–2 สัปดาห์
เพื่อปิด 3 ช่องที่ยังพิสูจน์ไม่ได้ (ask ตอน 16:00 local · YES bestBid จริง · depth)
โดย **ไม่ต้องเช่าเซิร์ฟเวอร์** และ **ไม่ต้องผูกบัตรเครดิต**

โปรแกรมต้องใช้แค่ `python3` + `numpy` (ตัวเดียว) · อ่าน API สาธารณะ ไม่ต้องมี API key

---

## ตัวเลือกฟรี — เทียบให้เลือก

| บริการ | ฟรีจริงไหม | รัน Python ได้ | cron/ตั้งเวลา | เก็บข้อมูลถาวร | ต้องมีบัตร | เหมาะกับ |
|---|---|---|---|---|---|---|
| **GitHub Actions** ⭐ แนะนำ | ✅ public ไม่จำกัดนาที · private 2,000 นาที/เดือน (งานนี้ ~720 นาที/เดือน) | ✅ | ✅ cron ทุก 5 นาทีขึ้นไป (UTC) | commit กลับเข้า repo | ❌ ไม่ต้อง | ทุกคนที่มี GitHub — ไฟล์พร้อมใช้ใน `deploy/github-actions/` |
| **เครื่องตัวเอง / Raspberry Pi** ⭐ ง่ายสุด | ✅ ฟรี 100% | ✅ | ✅ crontab | ดิสก์ตัวเอง | ❌ | เปิดคอมทิ้งไว้ หรือ Pi กินไฟ ~3W |
| **Oracle Cloud Always Free** | ✅ ARM 4 core/24GB "always free" | ✅ | ✅ crontab | ดิสก์ถาวร | ⚠️ ต้องบัตร (ยืนยันตัวตน ไม่ตัดเงิน) | อยากได้ VPS จริงถาวร |
| **Google Apps Script** | ✅ 20,000 ครั้ง/วัน | ❌ (JS แต่เรียก API ได้) | ✅ time-driven trigger ทุก 1 ชม. | Google Sheet | ❌ ไม่ต้อง | ไม่มี GitHub/ไม่อยากแตะ terminal — ใช้ `deploy/appsscript.gs` |
| **Hugging Face Spaces (CPU)** | ✅ | ✅ | ❌ ไม่มี cron ตรง ๆ (ใช้ loop + ping) | ephemeral (ต้อง push กลับ) | ❌ | รันสคริปต์ยาว ๆ แต่ข้อมูลไม่ค่อยถาวร |
| **Render** | ⚠️ มี free cron job แต่ instance ฟรีหลับเมื่อ idle + ต้องผูกบัญชี | ✅ | ✅ (free plan มี cron) | ephemeral | ⚠️ | พอใช้ได้ แต่ต้องกด deploy จาก GitHub อยู่ดี |
| **Railway / Fly.io** | ⚠️ เครดิต $5 หมดแล้วจ่าย · Fly มี free shared VM | ✅ | ⚠️ | ❌ | ✅ | ไม่แนะนำสำหรับงานนี้ |
| **Vercel / Netlify functions** | ⚠️ cron จำกัด (Vercel Hobby 2 ครั้ง/วัน) | ⚠️ ต้องปรับเป็น serverless | ⚠️ | ❌ | ✅ | ไม่พอสำหรับเก็บทุกชั่วโมง |
| **PythonAnywhere** | ✅ แต่งานตั้งเวลาได้ **1 งาน/วัน** | ✅ | ⚠️ daily เท่านั้น | ดิสก์ถาวร | ❌ | ไม่พอสำหรับเก็บทุกชั่วโมง |
| **cron-job.org + UptimeRobot** | ✅ | ❌ (แค่ยิง URL) | ✅ | — | ❌ | ใช้เป็น "ตัวปลุก" ให้บริการที่หลับ (เช่น ping HF Space ทุก 10 นาที) |
| **Cloudflare Workers / Deno Deploy / Val Town** | ✅ | ⚠️ JS/TS เป็นหลัก | ✅ | KV/Blob | ❌ | ต้องเขียนใหม่เป็น JS — ไม่คุ้มกับโค้ด Python ที่มี |

ข้อเท็จจริงที่ตรวจสอบแล้ว (ก.ย. 2026): GitHub Actions — public repo ไม่จำกัดนาที, private free 2,000 นาที/เดือน + 500 MB
· cron สั้นสุดทุก 5 นาที · งานอาจเลื่อน 15–60 นาทีตอนโหลดสูง · schedule ถูกปิดอัตโนมัติถ้า repo ไม่มี commit 60 วัน
· งานนี้ใช้ ~30–40 นาที/เดือน (public) หรือ ~720 นาที/เดือน (private) → อยู่ในโควตาฟรีสบาย

**สรุปเร็ว:** มี GitHub → ใช้ **GitHub Actions** (0 บาท ไม่ต้องมีบัตร ข้อมูลอยู่ใน git อ่านย้อนได้)
ไม่มี GitHub → ใช้ **เครื่องตัวเอง + cron** หรือ **Google Apps Script**

---

## วิธี 0: คำสั่งเดียว (เตรียมไว้ให้แล้ว)

repo นี้ **พร้อม push แล้ว** — มี `.gitignore`, commit แรก, `.github/workflows/forward-test.yml` (เก็บ log ทุกชั่วโมง) และ
`.github/workflows/tests.yml` (รันชุดทดสอบเมื่อ push) อยู่ในนั้น เหลือแค่ชี้ remote:

```bash
# บนเครื่องคุณ (หรือในแซนด์บ็อกซ์) — เลือกทางใดทางหนึ่ง
bash deploy/setup_github.sh https://github.com/<user>/wxedge.git
TOKEN=ghp_xxx bash deploy/setup_github.sh https://github.com/<user>/wxedge.git   # ไม่ต้องพิมพ์รหัส (สคริปต์ลบ token ออกจาก config ให้)
bash deploy/setup_github.sh          # ถ้ามี gh CLI → สร้าง repo private + push ให้เลย
```

ถ้ายังไม่มีไฟล์ในเครื่อง: ดาวน์โหลด `deploy/wxedge-repo-bundle.tar.gz` (4.4 MB) แล้ว
`tar -xzf wxedge-repo-bundle.tar.gz -C wxedge && cd wxedge && bash deploy/setup_github.sh <repo-url>`

## วิธี 1: GitHub Actions (แนะนำ)

```bash
# 1) เอาโฟลเดอร์ wxedge ขึ้น GitHub (public = ฟรีไม่จำกัดนาที, private = 2,000 นาที/เดือน ก็พอ)
cd wxedge
git init -b main
printf '__pycache__/\n*.pyc\n.venv/\n' > .gitignore
git add -A && git commit -m "wxedge: weather-market research + tooling"
git remote add origin https://github.com/<your-user>/wxedge.git
git push -u origin main

# 2) เปิดใช้ workflow (ถ้ายังไม่ได้คัดลอก)
mkdir -p .github/workflows
cp deploy/github-actions/forward-test.yml .github/workflows/forward-test.yml
git add .github/workflows/forward-test.yml && git commit -m "ci: forward-test" && git push
```

จากนั้นที่หน้า repo → **Actions → wxedge-forward-test → Run workflow** เพื่อทดสอบทันที
- ตั้งเวลา: ทุกชั่วโมง นาทีที่ 5 (UTC) — พอสำหรับทุก timezone ของ 33 เมือง
- กันซ้ำในตัว: 1 เมือง/วัน/ชั่วโมง/ฝั่ง/bin เก็บแค่ครั้งแรก (ราคาที่ใกล้เวลาตัดสินใจที่สุด)
- ข้อมูลจะถูก commit กลับเป็น `data/cheap_live_log.csv` — เปิดดูได้จากมือถือเลย

**ข้อควรรู้:** GitHub อาจเลื่อน cron ได้ ±5–15 นาทีตอนคนใช้เยอะ · ถ้า repo ไม่มี commit 60 วัน schedule จะถูกปิด (แก้: กด Enable ใหม่ หรือให้บอท commit ทุกวัน ซึ่งงานนี้ commit เองอยู่แล้ว)

## วิธี 2: เครื่องตัวเอง / Raspberry Pi / VPS

```bash
cd wxedge
pip install -r deploy/requirements.txt
chmod +x deploy/run_forward_test.sh
# ทดสอบรันหนึ่งครั้ง
bash deploy/run_forward_test.sh

# ตั้ง cron
crontab -e
5 * * * *  cd /path/to/wxedge && bash deploy/run_forward_test.sh >> /tmp/wxedge.log 2>&1
0 3 * * *  cd /path/to/wxedge && DAILY=1 bash deploy/run_forward_test.sh >> /tmp/wxedge.log 2>&1
```

## วิธี 3: Google Apps Script (ไม่มี GitHub / ไม่อยากใช้ terminal)

1. เปิด [script.google.com](https://script.google.com) → New project → วางโค้ดจาก `deploy/appsscript.gs`
2. Triggers → Add trigger → `logQuotes` → Time-driven → Hour timer → ทุกชั่วโมง
3. ข้อมูลจะเขียนลง Google Sheet ที่สร้างให้อัตโนมัติ (export CSV ได้)

---

## เช็คว่าเก็บครบหรือยัง (หลังรัน 1–2 สัปดาห์)

```bash
python3 forward_report.py        # (ถ้ามี) สรุป hit rate จริง + เทียบราคาที่จ่ายจริง vs last-trade
python3 bidask_check.py          # วัดช่องว่าง ask−last ใหม่จากข้อมูลปัจจุบัน
```

เกณฑ์ผ่านก่อนใช้เงินจริง (ตามเช็คลิสต์ในรายงาน): log ≥ 50 ไม้ · hit rate จริง ≥ 85% · ต้นทุนจริงเฉลี่ยไม่แย่กว่า stress +0.03
