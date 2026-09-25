#!/usr/bin/env bash
# chain_lib.sh — ฟังก์ชันกลางของระบบโซ่เก็บข้อมูล (root fix ของบั๊ก B1–B3)
#
# ทำไมต้องมี: ตรรกะเดียวกันนี้เคยถูกคัดลอกไว้หลายที่ (chain_loop.sh · run_forward_test.sh · watchdog)
#            พอแก้ที่เดียว ที่อื่นไม่ตาม → เกิดบั๊ก "แก้ไฟล์เดียว พังอีกไฟล์" (เคสจริง: gate.py เปลี่ยน flag
#            แต่ run_forward_test.sh ยังเรียก --hour-window อยู่ → cron พังเงียบ)
# ใช้: . deploy/chain_lib.sh   (ไม่มี side effect ตอน source · stdlib + git เท่านั้น)
#
# ฟังก์ชัน:
#   chain_state_fields <state>                     → "end_epoch beat_epoch" (0 0 ถ้าอ่านไม่ได้)
#   chain_guard_should_exit <end> <beat> <now> <stale_min> → rc 0 = มีโซ่อื่นจริง ควรออก
#   chain_state_write <state> <end_epoch> <iso_beat>
#   chain_state_clear_stale <state> [state_file_only]      → เขียน ends/beat เป็นอดีต (ปลดล็อก)
#   chain_git_push_round <msg_tag>                 → rc 0 = push สำเร็จ · เซ็ต CHAIN_PUSH_FAIL
#   chain_watchdog_decision <beat_age_min> <active_runs> <stale_min> → "healthy" | "dead"
#
# หมายเหตุการออกแบบ (บทเรียนจากของจริง):
#   • รอบตื่นทุก 5 นาที ห่างจริง ~4 น. 45 วิ → เกณฑ์ "เว้นขั้นต่ำ" ต้องน้อยกว่ารอบตื่น ไม่งั้นถูกข้าม
#   • push ชนกันให้ใช้ merge -X ours + เคลียร์ rebase/merge ที่ค้าง (rebase เปราะเมื่อไฟล์ generate ชนกัน)
#   • "beat สด" ไม่ได้แปลว่าโซ่ยังอยู่ → watchdog ต้องดู run ที่ active ประกอบด้วย ไม่งั้นตายเงียบ 25 นาที

chain_log() {
  if declare -F log >/dev/null 2>&1; then log "$@"; else echo "[$(date -u +%H:%M:%S)] $*"; fi
}

chain_state_fields() {
  python3 - "$1" <<'PY'
import json, sys, datetime as dt
end = beat = 0
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
    end = int(dt.datetime.strptime(d["ends_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp())
    beat = int(dt.datetime.strptime(d["beat_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp())
except Exception:
    pass
print(end, beat)
PY
}

chain_guard_should_exit() {   # <end> <beat> <now> <stale_min> → rc 0 = ควรออก
  local end=${1:-0} beat=${2:-0} now=${3:-0} stale=${4:-25}
  if [ "$end" -gt "$now" ] && [ $(( now - beat )) -lt $(( stale * 60 )) ]; then
    return 0
  fi
  return 1
}

chain_state_write() {         # <state> <end_epoch> <iso_beat>
  python3 - "$1" "$2" "$3" <<'PY'
import json, sys, datetime as dt
path, end, beat = sys.argv[1], int(sys.argv[2]), sys.argv[3]
json.dump({"ends_at": dt.datetime.fromtimestamp(end, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "beat_at": beat}, open(path, "w", encoding="utf-8"), indent=1)
PY
}

chain_state_clear_stale() {   # <state> [hours_back=1] → ends/beat เป็นอดีต (โซ่ใหม่เริ่มได้ทันที)
  python3 - "$1" "${2:-1}" <<'PY'
import json, sys, datetime as dt
old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=float(sys.argv[2]))).strftime("%Y-%m-%dT%H:%M:%SZ")
json.dump({"ends_at": old, "beat_at": old}, open(sys.argv[1], "w", encoding="utf-8"), indent=1)
PY
}

chain_git_stage_paths() {   # <paths...> — stage ทีละ path (path ที่ไม่มีต้องไม่ทำให้ทั้งคำสั่งล้ม)
  local p
  for p in "$@"; do
    if [ -e "$p" ]; then git add -f -- "$p" >/dev/null 2>&1 || true; fi
  done
}

chain_git_push_round() {      # <msg_tag> · ใช้ env: PUSH (1=push) · เซ็ต CHAIN_PUSH_FAIL
  local tag=${1:-"forward-test log"}
  local i
  CHAIN_PUSH_FAIL=${CHAIN_PUSH_FAIL:-0}
  [ "${PUSH:-1}" = "1" ] && [ -d .git ] || return 0
  # ── add ทีละ path: ถ้า path ใดหายไป ห้ามให้ทั้งคำสั่งล้ม (เคยเป็นบั๊ก: git add data reports docs
  #    ล้มเพราะ reports/ ไม่มี → ไม่ stage อะไรเลย → "ไม่มีข้อมูลใหม่" → ข้อมูลหายเงียบ) ──
  chain_git_stage_paths data reports docs
  if git diff --cached --quiet; then chain_log "ไม่มีข้อมูลใหม่"; CHAIN_PUSH_FAIL=0; return 0; fi
  git -c user.name=wxedge-bot -c user.email=wxedge-bot@users.noreply.github.com \
      commit -q -m "$tag $(date -u +%Y-%m-%dT%H:%M:%SZ) [skip ci]" || return 0
  for i in 1 2 3; do
    # เคลียร์ rebase/merge ที่ค้างจากรอบก่อน (สาเหตุค้างถาวรในเคสจริง)
    git rebase --abort >/dev/null 2>&1 || true
    git merge  --abort >/dev/null 2>&1 || true
    git fetch -q origin main 2>/dev/null || { sleep 5; continue; }
    # ชนกันเมื่อไรให้ยึดข้อมูลฝั่งเรา (ข้อมูลฝั่งเราสดกว่าเสมอ)
    if ! git -c user.name=wxedge-bot -c user.email=wxedge-bot@users.noreply.github.com \
         merge -q --no-edit -X ours origin/main >/dev/null 2>&1; then
      git rebase --abort >/dev/null 2>&1 || true
      git merge  --abort >/dev/null 2>&1 || true
      git checkout --ours -q -- data docs reports 2>/dev/null || true
      git add -A 2>/dev/null || true
      git -c user.name=wxedge-bot -c user.email=wxedge-bot@users.noreply.github.com \
          commit -q -m "chain: merge (ours) $(date -u +%Y-%m-%dT%H:%M:%SZ) [skip ci]" >/dev/null 2>&1 || true
    fi
    if git push -q origin main 2>/dev/null; then
      chain_log "push แล้ว (ครั้งที่ $i)"; CHAIN_PUSH_FAIL=0; return 0
    fi
    sleep 5
  done
  CHAIN_PUSH_FAIL=$(( CHAIN_PUSH_FAIL + 1 ))
  chain_log "⚠ push ไม่สำเร็จ (ต่อเนื่อง $CHAIN_PUSH_FAIL รอบ) — ข้อมูลยังอยู่ในเครื่อง runner"
  return 1
}

chain_watchdog_decision() {   # <beat_age_min> <active_runs> <stale_min> → healthy|dead
  local age=${1:-99999} runs=${2:-0} stale=${3:-25}
  if [ "$runs" -gt 0 ] && [ "$age" -lt "$stale" ]; then echo healthy; else echo dead; fi
}
