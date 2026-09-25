#!/usr/bin/env bash
# publish_monitor.sh — สร้างหน้า monitor แล้วเผยแพร่ขึ้น GitHub Pages (repo สาธารณะแยก)
#
# ใช้:
#   MONITOR_PAT=github_pat_xxx bash deploy/publish_monitor.sh              # เผยแพร่โหมด safe (ค่าเริ่มต้น)
#   MONITOR_PAT=... MODE=full bash deploy/publish_monitor.sh               # โหมดเต็ม (โชว์ bin/ราคา/กติกา) — ระวัง: หน้าเว็บเป็นสาธารณะ
#   bash deploy/publish_monitor.sh --dry-run                               # สร้างหน้า + พรีวิวเป็น zip ไม่ push
#
# ต้องมี: repo ปลายทาง (ค่าเริ่มต้น jampongsathorn/wxedge-monitor) และ token ที่เขียน repo นั้นได้ (contents: write)
set -euo pipefail
cd "$(dirname "$0")/.."

DEST_REPO="${DEST_REPO:-jampongsathorn/wxedge-monitor}"
# DEST_URL = URL ปลายทาง (ใช้ตอนทดสอบกับ repo ท้องถิ่นได้ เช่น file:///tmp/monitor-bare.git)
DEST_URL="${DEST_URL:-https://x-access-token:${MONITOR_PAT:-x}@github.com/$DEST_REPO.git}"
MODE="${MODE:-safe}"
PY=${PY:-python3}
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

echo "① สร้างหน้า monitor (โหมด $MODE)"
PYTHONPATH=. $PY build_monitor.py --mode "$MODE" --out docs/index.html --json-out docs/monitor.json

if [ "$DRY" = "1" ]; then
  (cd docs && zip -q -r ../deploy/monitor-preview.zip index.html monitor.json)
  echo "   --dry-run: เขียน deploy/monitor-preview.zip (ไม่ push)"
  exit 0
fi

if [ -z "${MONITOR_PAT:-}" ]; then
  echo "   ⚠ ไม่มี MONITOR_PAT — ข้ามการเผยแพร่ (หน้าเว็บยังอยู่ที่ docs/index.html)"
  exit 0
fi

# ลายนิ้วมือข้อมูล (ตัด timestamp ออก) — กัน commit ซ้ำทุก 30 นาทีทั้งที่ข้อมูลไม่เปลี่ยน
FP=$(python3 - <<'FPY'
import hashlib, re
s = open('docs/index.html', encoding='utf-8').read()
s = re.sub(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', 'TS', s)
print(hashlib.sha256(s.encode()).hexdigest()[:16])
FPY
)

echo "② เตรียมโฟลเดอร์ปลายทาง"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
git -c init.defaultBranch=main init -q "$TMP"
git -C "$TMP" remote add origin "$DEST_URL"
if git -C "$TMP" fetch -q --depth 1 origin main 2>/dev/null; then
  git -C "$TMP" checkout -q -b main FETCH_HEAD
else
  git -C "$TMP" checkout -q -b main
fi

if [ -f "$TMP/.fingerprint" ] && [ "$(cat "$TMP/.fingerprint")" = "$FP" ] && [ "${FORCE:-0}" != "1" ]; then
  echo "   ข้อมูลไม่เปลี่ยน (fingerprint $FP) — ข้ามการ push · บังคับได้ด้วย FORCE=1"
  exit 0
fi
echo "   fingerprint: $FP"

echo "③ คัดลอก + commit"
cp docs/index.html docs/monitor.json "$TMP"/
printf '%s' "$FP" > "$TMP/.fingerprint"
touch "$TMP/.nojekyll"
[ -f "$TMP/README.md" ] || cat > "$TMP/README.md" <<'MD'
# wxedge-monitor

หน้าเว็บติดตามกลยุทธ์ตลาด “อุณหภูมิสูงสุดของวัน” บน Polymarket — **สร้างอัตโนมัติ** จาก repo ต้นทาง (ส่วนตัว)

- `index.html` — หน้า monitor (static · ไม่มี dependency ภายนอก · ไม่มี tracking)
- `monitor.json` — KPI แบบ machine-readable

อย่าแก้ไฟล์ในนี้ด้วยมือ (จะถูกเขียนทับรอบถัดไป) · สร้างด้วย `build_monitor.py`
MD
git -C "$TMP" add -A
if git -C "$TMP" diff --cached --quiet; then
  echo "   ไม่มีการเปลี่ยนแปลง — ไม่ต้อง push"
else
  git -C "$TMP" -c user.name=wxedge-bot -c user.email=wxedge-bot@users.noreply.github.com \
      commit -q -m "monitor: อัปเดต $(date -u +'%Y-%m-%dT%H:%MZ') (โหมด $MODE)"
  git -C "$TMP" push -q origin main
  echo "   push แล้ว → https://${DEST_REPO%%/*}.github.io/${DEST_REPO##*/}/"
fi
