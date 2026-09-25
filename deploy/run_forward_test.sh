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
WINDOW=${HOUR_WINDOW:-15-17}
STAMP=$(date -u +"%Y-%m-%dT%H:%MZ")
echo "── wxedge forward-test $STAMP ──"

if [ "${DAILY:-0}" = "1" ]; then
  echo "── รายวัน: จักรวาล + bid/ask + เติมเฉลย ──"
  $PY -c "import cheap_live as C; C.build_universe(verbose=True)"
  $PY bidask_check.py --max-bins 90 || true
  $PY forward_resolve.py || true
else
  GATE=$(PYTHONPATH=. $PY deploy/gate.py --hour-window "$WINDOW")
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
  git add -f data/cheap_live_log.csv data/universe.json data/bidask_probe.json data/forward_stats.json \
             data/snapshots reports/bidask_check.md reports/forward_report.md 2>/dev/null || true
  if ! git diff --cached --quiet; then
    git -c user.name=wxedge-bot -c user.email=wxedge-bot@users.noreply.github.com \
        commit -q -m "forward-test log $STAMP [skip ci]"
    git push -q && echo "push แล้ว" || echo "push ไม่ได้ (ตรวจ remote/สิทธิ์)"
  fi
fi
echo "เสร็จ $STAMP"
