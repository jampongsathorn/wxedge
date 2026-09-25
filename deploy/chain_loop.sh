#!/usr/bin/env bash
# chain_loop.sh — วนเก็บข้อมูลทุก N นาทีใน job เดียว (ไม่ต้องพึ่งความแม่นของ cron ของ GitHub)
#
# หลักคิด: GitHub cron เลื่อนได้ 15-60 นาที และงานที่ "ต้องเก็บทุก 10 นาที" ต้องการความแน่นอน
#          จุดนี้จึงวนอยู่ใน job เดียว (job ละไม่เกิน 6 ชม.) แล้วสั่งรันตัวเองใหม่ต่อจากรอบถัดไป
#
# ตัวแปร: CHAIN_HOURS (ค่าเริ่มต้น 5.5) · INTERVAL_MIN (10) · STATE (data/chain_state.json)
#         WORKERS (4) · ENTRY/CTX windows · DRY=1 = ไม่ push (ทดสอบ) · PUSH=0 = ไม่ push
set -uo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-python3}
INTERVAL_MIN=${INTERVAL_MIN:-10}
CHAIN_HOURS=${CHAIN_HOURS:-5.5}
WORKERS=${WORKERS:-4}
ENTRY_WIN=${ENTRY_WIN:-15:30-16:30}
ENTRY_GAP=${ENTRY_GAP:-10}
CTX_WIN=${CTX_WIN:-14:00-18:00}
CTX_GAP=${CTX_GAP:-30}
PUSH=${PUSH:-1}
STATE=${STATE:-data/chain_state.json}
HEARTBEAT_STALE_MIN=${HEARTBEAT_STALE_MIN:-25}

now_iso() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
now_epoch() { date -u +%s; }
log() { echo "[$(date -u +%H:%M:%S)] $*"; }

# ── กันโซ่ซ้อน: ถ้ามีโซ่อื่นยังเต้นอยู่ (heartbeat สด) ให้ออกทันที ──
END=$(( $(now_epoch) + $(python3 -c "print(int(float('$CHAIN_HOURS')*3600))") ))
prev_end=0; prev_beat=0
if [ -f "$STATE" ]; then
  read -r prev_end prev_beat < <(python3 - "$STATE" <<'PY'
import json, sys, datetime as dt
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
    end = int(dt.datetime.strptime(d["ends_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp())
    beat = int(dt.datetime.strptime(d["beat_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp())
except Exception:
    end = beat = 0
print(end, beat)
PY
)
  if [ "$prev_end" -gt "$(now_epoch)" ] && [ $(( $(now_epoch) - prev_beat )) -lt $(( HEARTBEAT_STALE_MIN * 60 )) ]; then
    log "มีโซ่อื่นทำงานอยู่ (heartbeat อีก $(( (prev_end - $(now_epoch)) / 60 )) นาที) — ออกเพื่อไม่ให้ซ้อน"
    exit 0
  fi
fi

write_state() {
  python3 - "$STATE" "$END" "$(now_iso)" <<'PY'
import json, sys, datetime as dt
path, end, beat = sys.argv[1], int(sys.argv[2]), sys.argv[3]
json.dump({"ends_at": dt.datetime.fromtimestamp(end, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "beat_at": beat}, open(path, "w", encoding="utf-8"), indent=1)
PY
}

git_pull_push() {
  [ "$PUSH" = "1" ] && [ -d .git ] || return 0
  git add -f data reports docs 2>/dev/null || true
  git diff --cached --quiet && { log "ไม่มีข้อมูลใหม่"; return 0; }
  git -c user.name=wxedge-bot -c user.email=wxedge-bot@users.noreply.github.com \
      commit -q -m "forward-test log $(now_iso) [skip ci]" || return 0
  for i in 1 2 3; do
    git pull --rebase -q origin main 2>/dev/null || true
    git push -q origin main 2>/dev/null && { log "push แล้ว (ครั้งที่ $i)"; return 0; }
    sleep 5
  done
  log "⚠ push ไม่สำเร็จ 3 ครั้ง — ข้อมูลยังอยู่ในเครื่อง runner"
}

write_state
if [ "$PUSH" = "1" ] && [ -d .git ]; then
  git add -f "$STATE" 2>/dev/null || true
  git -c user.name=wxedge-bot -c user.email=wxedge-bot@users.noreply.github.com \
      commit -q -m "chain: เริ่มโซ่ถึง $(date -u -d "@$END" +%Y-%m-%dT%H:%MZ) [skip ci]" 2>/dev/null || true
  git push -q origin main 2>/dev/null || true
fi
log "เริ่มโซ่ · จะวนถึง $(date -u -d "@$END" +%Y-%m-%dT%H:%MZ) · ทุก $INTERVAL_MIN นาที · workers $WORKERS"

rounds=0; scans=0; empty=0
while [ "$(now_epoch)" -lt "$END" ]; do
  # ── งานรายวัน (03:20–03:39 UTC) ──
  H=$(date -u +%-H); M=$(date -u +%-M)
  if [ "$H" = "3" ] && [ "$M" -ge 20 ] && [ "$M" -lt 40 ]; then
    if [ ! -f data/.daily_done_$(date -u +%F) ]; then
      log "── งานรายวัน: จักรวาล + bid/ask + เติมเฉลย ──"
      $PY -c "import cheap_live as C; C.build_universe(verbose=True)" || true
      $PY bidask_check.py --max-bins 90 || true
      $PY forward_resolve.py || true
      touch data/.daily_done_$(date -u +%F)
    fi
  fi

  # ── เลือกเมืองที่ควรสแกนรอบนี้ ──
  GATE=$( $PY deploy/gate.py --entry "$ENTRY_WIN" --entry-gap "$ENTRY_GAP" --ctx "$CTX_WIN" --ctx-gap "$CTX_GAP" 2>/dev/null || true )
  RUN=$(echo "$GATE" | sed -n 's/^run=//p')
  CITIES=$(echo "$GATE" | sed -n 's/^cities=//p')
  MODE=$(echo "$GATE" | sed -n 's/^mode=//p')
  if [ "$RUN" = "true" ]; then
    rounds=$((rounds + 1)); scans=$((scans + $(echo "$CITIES" | tr ',' '\n' | grep -c .)))
    log "รอบที่ $rounds · โหมด $MODE · $CITIES"
    $PY cheap_live.py --log --workers "$WORKERS" --cities "$CITIES" 2>&1 | tail -3
    # หน้า monitor อัปเดตเมื่อ KPI เปลี่ยน (log/จักรวาล/bid-ask/เฉลย/รายงาน) — snapshot เปล่า ๆ ไม่ต้อง rebuild Pages
    git add -f data/cheap_live_log.csv data/universe.json data/bidask_probe.json data/forward_stats.json reports 2>/dev/null || true
    if ! git diff --cached --quiet; then
      $PY build_monitor.py --quiet --mode "${MODE_MONITOR:-full}" --skip-if-same --out docs/index.html --json-out docs/monitor.json || true
    fi
  else
    empty=$((empty + 1))
  fi
  write_state
  git_pull_push

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
