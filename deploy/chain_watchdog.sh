#!/usr/bin/env bash
# chain_watchdog.sh — เฝ้าโซ่เก็บข้อมูล: ถ้าโซ่ตาย/ค้าง ให้ยกเลิก run ที่ค้าง + ปลด state + เริ่มโซ่ใหม่
#
# ทำไมต้องมี 2 เงื่อนไข (บทเรียนเคสจริง 25 ก.ย. 2026):
#   ① heartbeat ค้าง  → โซ่ยัง "รันอยู่" แต่ push ไม่ได้ (rebase ค้าง)  → ต้องฆ่า + เริ่มใหม่
#   ② heartbeat สด แต่ "ไม่มี run ที่ active" → โซ่เพิ่งถูกยกเลิก/ตาย แต่ state ยังบอกว่ามีโซ่
#      → โซ่ใหม่จะถูก guard สั่งออกทันที (บั๊ก B2) ถ้า watchdog ดูแค่ heartbeat จะไม่รู้เลย 25 นาที
#
# ใช้: GH_TOKEN=<token ที่มี actions:write + contents:write> bash deploy/chain_watchdog.sh
#      DRY=1     → พิมพ์ว่าจะทำอะไร ไม่ยิง API
#      LOCAL=1   → โหมดทดสอบ: ปลด state ที่ไฟล์ในเครื่อง (ไม่ยิง API) ไม่ cancel/dispatch
# ตัวแปร: STALE_MIN (25) · REPO (GITHUB_REPOSITORY) · WF (forward-chain.yml) · STATE
set -uo pipefail
_LIB="$(cd "$(dirname "$0")" && pwd)/chain_lib.sh"
[ -f "$_LIB" ] || { echo "ไม่พบ $_LIB"; exit 1; }
. "$_LIB"
cd "$(dirname "$0")/.."

REPO=${REPO:-${GITHUB_REPOSITORY:-jampongsathorn/wxedge}}
WF=${WF:-forward-chain.yml}
STALE_MIN=${STALE_MIN:-25}
STATE=${STATE:-data/chain_state.json}
DRY=${DRY:-0}
LOCAL=${LOCAL:-0}
TOK=${GH_TOKEN:-}
API="https://api.github.com/repos/$REPO"

# ── ① อ่าน heartbeat ──
beat=""; stale=99999
if [ -f "$STATE" ]; then
  read -r _end beat_epoch < <(chain_state_fields "$STATE")
  beat=$(python3 - "$STATE" <<'PY'
import json, sys
try:
    print(json.load(open(sys.argv[1], encoding="utf-8")).get("beat_at", ""))
except Exception:
    print("")
PY
)
  stale=$(python3 - "$beat_epoch" <<'PY'
import sys, datetime as dt, time
try:
    print(int((time.time() - float(sys.argv[1])) // 60))
except Exception:
    print(99999)
PY
)
fi

# ── ② นับ run ที่ active (in_progress/queued/pending/…) ──
runs=""
if [ "$LOCAL" = "1" ]; then
  runs=""                                  # โหมดทดสอบ: ไม่ยิง API
  n_active_override=${ACTIVE_RUNS:-0}      # ...ใช้ค่านี้จำลองจำนวน run ที่ active แทน
else
  for st in in_progress queued pending requested waiting; do
    ids=$(curl -s -H "Authorization: Bearer $TOK" -H "User-Agent: wxedge-watchdog" \
            -H "Accept: application/vnd.github+json" "$API/actions/workflows/$WF/runs?status=$st&per_page=10" \
          | python3 -c "import sys,json
try: print(' '.join(str(r['id']) for r in json.load(sys.stdin).get('workflow_runs',[])))
except Exception: print('')")
    runs="$runs $ids"
  done
fi
n_active=${n_active_override:-$(echo $runs | wc -w | tr -d ' ')}

decision=$(chain_watchdog_decision "$stale" "$n_active" "$STALE_MIN")
echo "[watchdog] heartbeat=${beat:-ไม่มี} · ห่าง ${stale} นาที · run active ${n_active} · เกณฑ์ ${STALE_MIN} นาที → $decision"

if [ "$decision" = "healthy" ]; then
  echo "[watchdog] โซ่ยังเต้นปกติ (มี run active + heartbeat สด) — ไม่ทำอะไร"
  exit 0
fi

# ── ③ โซ่ตาย: ยกเลิก run ที่ค้าง (ถ้ามี) ──
if [ "$LOCAL" != "1" ]; then
  for id in $runs; do
    if [ "$DRY" = "1" ]; then echo "  (dry) จะยกเลิก run $id"; else
      code=$(curl -s -o /dev/null -w "%{http_code}" -X POST -H "Authorization: Bearer $TOK" \
              -H "User-Agent: wxedge-watchdog" -H "Accept: application/vnd.github+json" \
              "$API/actions/runs/$id/cancel")
      echo "  ยกเลิก run $id → HTTP $code"
    fi
  done
  [ -n "$runs" ] && sleep 10
fi

# ── ④ ปลด state ที่ค้าง (ถ้า state ยังบอกว่ามีโซ่ถึงอนาคต) ──
if [ "$LOCAL" = "1" ]; then
  chain_state_clear_stale "$STATE" 1
  echo "  (local) ปลด state ที่ไฟล์แล้ว → $(cat "$STATE" | tr -d '\n')"
else
  if [ "$DRY" = "1" ]; then
    echo "  (dry) จะปลด state (ตั้ง ends_at/beat_at เป็น 1 ชม.ที่แล้ว)"
  else
    sha=$(curl -s -H "Authorization: Bearer $TOK" -H "User-Agent: wxedge-watchdog" \
            "$API/contents/data/chain_state.json" \
          | python3 -c "import sys,json
try: print(json.load(sys.stdin).get('sha',''))
except Exception: print('')")
    if [ -n "$sha" ]; then
      body=$(python3 - "$sha" <<'PY2'
import base64, json, sys, datetime as dt
old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
print(json.dumps({"message": "chain: watchdog ปลด state ที่ค้าง [skip ci]", "sha": sys.argv[1],
                  "content": base64.b64encode(json.dumps({"ends_at": old, "beat_at": old}, indent=1).encode()).decode()}))
PY2
)
      code=$(curl -s -o /dev/null -w "%{http_code}" -X PUT -H "Authorization: Bearer $TOK" \
              -H "User-Agent: wxedge-watchdog" -H "Accept: application/vnd.github+json" \
              -d "$body" "$API/contents/data/chain_state.json")
      echo "  ปลด state → HTTP $code"
    else
      echo "  อ่าน state sha ไม่ได้ — ข้ามการปลด (โซ่ใหม่จะเริ่มเองเมื่อ heartbeat ครบเกณฑ์)"
    fi
  fi
fi

# ── ⑤ เริ่มโซ่ใหม่ ──
if [ "$LOCAL" = "1" ]; then
  echo "  (local) จบ — ไม่ dispatch"
elif [ "$DRY" = "1" ]; then
  echo "  (dry) จะ dispatch โซ่ใหม่"
else
  code=$(curl -s -o /dev/null -w "%{http_code}" -X POST -H "Authorization: Bearer $TOK" \
          -H "User-Agent: wxedge-watchdog" -H "Accept: application/vnd.github+json" \
          -d '{"ref":"main"}' "$API/actions/workflows/$WF/dispatches")
  echo "  dispatch โซ่ใหม่ → HTTP $code"
fi
echo "[watchdog] จบ"
