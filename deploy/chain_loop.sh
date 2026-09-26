#!/usr/bin/env bash
# chain_loop.sh — วนเก็บข้อมูลทุก N นาทีใน job เดียว (ไม่ต้องพึ่งความแม่นของ cron ของ GitHub)
#
# หลักคิด: GitHub cron เลื่อนได้ 15-60 นาที และงานที่ "ต้องเก็บทุก 10 นาที" ต้องการความแน่นอน
#          จุดนี้จึงวนอยู่ใน job เดียว (job ละไม่เกิน 6 ชม.) แล้วสั่งรันตัวเองใหม่ต่อจากรอบถัดไป
#
# ตัวแปร: CHAIN_HOURS (5.5) · INTERVAL_MIN (5) · ENTRY_WIN/GAP · CTX_WIN/GAP · STATE (data/chain_state.json)
#         WORKERS (4) · ENTRY/CTX windows · DRY=1 = ไม่ push (ทดสอบ) · PUSH=0 = ไม่ push
set -uo pipefail
# ใช้ฟังก์ชันกลาง (root fix B1–B3: guard/state/push ต้องมีชุดเดียว ไม่คัดลอก)
_LIB="$(cd "$(dirname "$0")" && pwd)/chain_lib.sh"
[ -f "$_LIB" ] || { echo "ไม่พบ $_LIB"; exit 1; }
. "$_LIB"
cd "$(dirname "$0")/.."
PY=${PY:-python3}
INTERVAL_MIN=${INTERVAL_MIN:-5}
CHAIN_HOURS=${CHAIN_HOURS:-5.5}
WORKERS=${WORKERS:-4}
ENTRY_WIN=${ENTRY_WIN:-15:00-16:30}
ENTRY_GAP=${ENTRY_GAP:-4}
CTX_WIN=${CTX_WIN:-12:30-18:30}
CTX_GAP=${CTX_GAP:-11}
PUSH=${PUSH:-1}
STATE=${STATE:-data/chain_state.json}
HEARTBEAT_STALE_MIN=${HEARTBEAT_STALE_MIN:-25}

now_iso() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
now_epoch() { date -u +%s; }
log() { echo "[$(date -u +%H:%M:%S)] $*"; }

# ── กันโซ่ซ้อน: ถ้ามีโซ่อื่นยังเต้นอยู่จริง (state สด) ให้ออกทันที ──
END=$(( $(now_epoch) + $(python3 -c "print(int(float('$CHAIN_HOURS')*3600))") ))
prev_end=0; prev_beat=0
if [ -f "$STATE" ]; then
  read -r prev_end prev_beat < <(chain_state_fields "$STATE")
  if chain_guard_should_exit "$prev_end" "$prev_beat" "$(now_epoch)" "$HEARTBEAT_STALE_MIN"; then
    log "มีโซ่อื่นทำงานอยู่ (heartbeat อีก $(( (prev_end - $(now_epoch)) / 60 )) นาที) — ออกเพื่อไม่ให้ซ้อน"
    exit 0
  fi
fi

write_state() { chain_state_write "$STATE" "$END" "$(now_iso)"; }

git_pull_push() { chain_git_push_round "forward-test log" || true; PUSH_FAIL=${CHAIN_PUSH_FAIL:-0}; }

write_state
# push state เริ่มโซ่ผ่านตรรกะกลาง (fetch + merge -X ours + abort ที่ค้าง) — ห้ามใช้ git push ตรง ๆ
chain_git_push_round "chain: เริ่มโซ่ถึง $(date -u -d "@$END" +%Y-%m-%dT%H:%MZ)" || true
log "เริ่มโซ่ · จะวนถึง $(date -u -d "@$END" +%Y-%m-%dT%H:%MZ) · ทุก $INTERVAL_MIN นาที · workers $WORKERS"

rounds=0; scans=0; empty=0; PUSH_FAIL=0
while [ "$(now_epoch)" -lt "$END" ]; do
  # ── งานรายวัน (03:20–03:39 UTC) ──
  H=$(date -u +%-H); M=$(date -u +%-M)
  if [ "$H" = "3" ] && [ "$M" -ge 20 ] && [ "$M" -lt 40 ]; then
    if [ ! -f data/.daily_done_$(date -u +%F) ]; then
      log "── งานรายวัน: จักรวาล + bid/ask + เติมเฉลย ──"
      timeout 900 $PY -c "import cheap_live as C; C.build_universe(verbose=True)" || true
      timeout 600 $PY bidask_check.py --max-bins 90 || true
      timeout 600 $PY forward_resolve.py || true
      touch data/.daily_done_$(date -u +%F)
    fi
  fi

  # ── เลือกเมืองที่ควรสแกนรอบนี้ ──
  GATE=$( $PY deploy/gate.py --entry "$ENTRY_WIN" --entry-gap "$ENTRY_GAP" --ctx "$CTX_WIN" --ctx-gap "$CTX_GAP" 2>&1 ) || true
  if [ -z "$GATE" ]; then GATE_ERR=$( $PY deploy/gate.py --entry "$ENTRY_WIN" --entry-gap "$ENTRY_GAP" --ctx "$CTX_WIN" --ctx-gap "$CTX_GAP" 2>&1 | tail -1 )
    log "⚠ gate ไม่ตอบ — ข้ามรอบนี้ · สาเหตุ: $GATE_ERR"; fi
  RUN=$(echo "$GATE" | sed -n 's/^run=//p')
  CITIES=$(echo "$GATE" | sed -n 's/^cities=//p')
  MODE=$(echo "$GATE" | sed -n 's/^mode=//p')
  if [ "$RUN" = "true" ]; then
    rounds=$((rounds + 1)); scans=$((scans + $(echo "$CITIES" | tr ',' '\n' | grep -c .)))
    log "รอบที่ $rounds · โหมด $MODE · $CITIES"
    timeout 900 $PY cheap_live.py --log --workers "$WORKERS" --cities "$CITIES" 2>&1 | tail -3
    # ดีลจริง (fills) ของ bin ที่สนใจ ในหน้าต่างเข้าไม้ — หลักฐานว่า "ซื้อได้จริง" (snapshot = quote อาจค้าง)
    timeout 300 $PY trades_probe.py --log 2>&1 | tail -2 || true
    # หน้า monitor อัปเดตเมื่อ KPI เปลี่ยน (log/จักรวาล/bid-ask/เฉลย/รายงาน) — snapshot เปล่า ๆ ไม่ต้อง rebuild Pages
    chain_git_stage_paths data/cheap_live_log.csv data/universe.json data/bidask_probe.json \
                         data/forward_stats.json data/trades_live.jsonl reports
    if ! git diff --cached --quiet; then
      timeout 300 $PY build_monitor.py --quiet --mode "${MODE_MONITOR:-full}" --skip-if-same --out docs/index.html --json-out docs/monitor.json || true
    fi
  else
    empty=$((empty + 1))
  fi
  write_state
  git_pull_push
  if [ "$PUSH_FAIL" -ge 3 ]; then
    log "หยุดโซ่: push ไม่ติดต่อกัน 3 รอบ → ปล่อยให้โซ่ใหม่ (cron สำรอง/watchdog) เริ่มแทน"
    break
  fi

  # ── นอนให้ตรงรอบถัดไป (นาทีที่หารด้วย INTERVAL_min ลงตัว) ──
  cur=$(date -u +%s); mod=$(( $(date -u +%-M) % INTERVAL_MIN ))
  wait_s=$(( (INTERVAL_MIN - mod) * 60 - $(date -u +%-S) ))
  [ "$wait_s" -lt 20 ] && wait_s=$(( wait_s + INTERVAL_MIN * 60 ))
  [ $(( cur + wait_s )) -gt "$END" ] && break
  sleep "$wait_s"
done

log "จบโซ่ · รอบที่สแกน $rounds · เมืองรวม $scans · รอบเปล่า $empty"
# ── สั่งรันโซ่ถัดไป (ตัวสำรองคือ cron ใน workflow) ──
if [ -n "${CHAIN_PAT:-}" ] && [ "${DISPATCH_NEXT:-1}" = "1" ]; then
  for i in 1 2 3; do
    code=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
      -H "Authorization: Bearer $CHAIN_PAT" -H "User-Agent: wxedge-chain" \
      -H "Accept: application/vnd.github+json" \
      -d '{"ref":"main"}' \
      "https://api.github.com/repos/${GITHUB_REPOSITORY:-jampongsathorn/wxedge}/actions/workflows/forward-chain.yml/dispatches")
    if [ "$code" = "204" ]; then log "ต่อโซ่ถัดไปแล้ว (HTTP $code)"; break; fi
    log "⚠ dispatch ไม่สำเร็จ (HTTP $code) — ลองใหม่"; sleep 15
  done
else
  log "ไม่มี CHAIN_PAT — พึ่ง cron สำรองของ workflow ในการเริ่มโซ่ถัดไป"
fi
