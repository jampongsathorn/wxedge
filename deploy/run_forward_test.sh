#!/usr/bin/env bash
# run_forward_test.sh — ตัวรัน forward-test สำหรับ cron/VPS/Raspberry Pi/เครื่องตัวเอง
#
# ติดตั้ง:
#   crontab -e
#   */30 * * * *  cd /path/to/wxedge && bash deploy/run_forward_test.sh >> /tmp/wxedge.log 2>&1
#   20 3 * * *    cd /path/to/wxedge && DAILY=1 bash deploy/run_forward_test.sh >> /tmp/wxedge.log 2>&1
#
# ทำอะไร: gate เช็คช่วงเวลา → สแกนเมืองที่ใกล้เวลาเข้าไม้ (15:00-17:00 local)
#         → บันทึก bid/ask จริง + depth + snapshot ลง data/ (กันซ้ำอัตโนมัติ)
#         DAILY=1 → อัปเดตจักรวาล + วัด bid/ask + เติมเฉลย/รายงาน forward
# ตั้งค่า: PY=python3 · HOUR_WINDOW=15-17 · PUSH=0 ปิดการ git push · DAILY=1 โหมดรายวัน
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-python3}
WINDOW=${HOUR_WINDOW:-15-17}                     # ใช้กับ cheap_live.py --hour-window (ยังรองรับ)
ENTRY_WIN=${ENTRY_WIN:-15:00-16:30}              # ใช้กับ gate.py v2 — gate v2 ไม่รับ flag ของ v1 แล้ว (ใส่ flag ของ v1 = สคริปต์ตายใต้ set -e)
ENTRY_GAP=${ENTRY_GAP:-4}
CTX_WIN=${CTX_WIN:-12:30-18:30}
CTX_GAP=${CTX_GAP:-11}
# ตรรกะ guard/state/push ใช้ชุดกลางเดียวกับ chain_loop.sh (root fix B3)
. "$(cd "$(dirname "$0")" && pwd)/chain_lib.sh"
STAMP=$(date -u +"%Y-%m-%dT%H:%MZ")
echo "── wxedge forward-test $STAMP ──"

if [ "${DAILY:-0}" = "1" ]; then
  echo "── รายวัน: จักรวาล + bid/ask + เติมเฉลย ──"
  $PY -c "import cheap_live as C; C.build_universe(verbose=True)"
  $PY bidask_check.py --max-bins 90 || true
  $PY forward_resolve.py || true
  $PY edge_monitor.py || true                          # วัด edge จากราคา ask จริง (กันกลับไปเชื่อราคา stale)
  $PY build_monitor.py --quiet || true                 # สร้างหน้า monitor ให้สดใหม่
  [ -n "${MONITOR_PAT:-}" ] && bash deploy/publish_monitor.sh || true
else
  GATE=$(PYTHONPATH=. $PY deploy/gate.py --entry "$ENTRY_WIN" --entry-gap "$ENTRY_GAP" \
                                        --ctx "$CTX_WIN" --ctx-gap "$CTX_GAP")
  echo "$GATE" | sed 's/^/   /'
  if echo "$GATE" | grep -q '^run=true'; then
    CITIES=$(echo "$GATE" | sed -n 's/^cities=//p')
    $PY cheap_live.py --log --hour-window "$WINDOW" --cities "$CITIES"
  else
    echo "   ไม่มีเมืองถึงเวลาเข้าไม้ — ข้ามรอบนี้"
  fi
fi

# ถ้ามี git remote ตั้งไว้ จะ commit/push ให้อัตโนมัติ (ปิดได้ด้วย PUSH=0)
if [ "${PUSH:-1}" = "1" ] && [ -d .git ]; then
  chain_git_push_round "forward-test log" && echo "push แล้ว" || echo "push ไม่ได้ (ตรวจ remote/สิทธิ์)"
fi
echo "เสร็จ $STAMP"
