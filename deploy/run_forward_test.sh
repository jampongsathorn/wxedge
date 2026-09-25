#!/usr/bin/env bash
# run_forward_test.sh — ตัวรัน forward-test สำหรับ cron/VPS/Raspberry Pi/เครื่องตัวเอง
#
# ใช้:
#   crontab -e
#   5 * * * *  cd /path/to/wxedge && bash deploy/run_forward_test.sh >> /tmp/wxedge.log 2>&1
#   0 3 * * *  cd /path/to/wxedge && DAILY=1 bash deploy/run_forward_test.sh >> /tmp/wxedge.log 2>&1
#
# ทำอะไร: สแกนเมืองที่ local 14:00–19:00 → บันทึก bid/ask จริงลง data/cheap_live_log.csv (กันซ้ำอัตโนมัติ)
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-python3}
STAMP=$(date -u +"%Y-%m-%dT%H:%MZ")
echo "── wxedge forward-test $STAMP ──"
$PY cheap_live.py --log
if [ "${DAILY:-0}" = "1" ]; then
  echo "── รายวัน: อัปเดตจักรวาล + วัด bid/ask ──"
  $PY -c "import cheap_live as C; C.build_universe(verbose=True)"
  $PY bidask_check.py --max-bins 90 || true
fi
# ถ้ามี git และตั้ง remote ไว้แล้ว จะ commit ให้อัตโนมัติ (ปิดได้ด้วย PUSH=0)
if [ "${PUSH:-1}" = "1" ] && [ -d .git ]; then
  git add -f data/cheap_live_log.csv data/universe.json data/bidask_probe.json || true
  if ! git diff --cached --quiet; then
    git -c user.name=wxedge-bot -c user.email=wxedge-bot@users.noreply.github.com \
        commit -m "forward-test log $STAMP [skip ci]" >/dev/null
    git push -q && echo "push แล้ว" || echo "push ไม่ได้ (ตรวจ remote/สิทธิ์)"
  fi
fi
echo "เสร็จ $STAMP"
