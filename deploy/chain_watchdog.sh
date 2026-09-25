#!/usr/bin/env bash
# chain_watchdog.sh — เฝ้า heartbeat ของโซ่เก็บข้อมูล ถ้าค้างเกิน N นาที ให้ยกเลิก run ที่ค้างแล้วเริ่มโซ่ใหม่
#
# ใช้: GH_TOKEN=<token ที่มี actions:write> bash deploy/chain_watchdog.sh     (ใน GitHub Actions ใช้ github.token)
#      DRY=1  → แค่พิมพ์ว่า "จะทำอะไร" ไม่ยิง API
# ตัวแปร: STALE_MIN (25) · REPO (GITHUB_REPOSITORY) · WF (forward-chain.yml)
set -uo pipefail
cd "$(dirname "$0")/.."
REPO=${REPO:-${GITHUB_REPOSITORY:-jampongsathorn/wxedge}}
WF=${WF:-forward-chain.yml}
STALE_MIN=${STALE_MIN:-25}
STATE=${STATE:-data/chain_state.json}
DRY=${DRY:-0}
TOK=${GH_TOKEN:-}

beat=$(python3 - "$STATE" <<'PY'
import json, sys
try:
    print(json.load(open(sys.argv[1], encoding="utf-8")).get("beat_at", ""))
except Exception:
    print("")
PY
)
stale=$(python3 - "$beat" <<'PY'
import sys, datetime as dt
try:
    b = dt.datetime.strptime(sys.argv[1], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    print(int((dt.datetime.now(dt.timezone.utc) - b).total_seconds() // 60))
except Exception:
    print(99999)
PY
)
echo "[watchdog] heartbeat=${beat:-ไม่มี} · ห่าง ${stale} นาที · เกณฑ์ ${STALE_MIN} นาที"
if [ "$stale" -lt "$STALE_MIN" ]; then
  echo "[watchdog] โซ่ยังเต้นปกติ — ไม่ทำอะไร"
  exit 0
fi

runs=$(curl -s -H "Authorization: Bearer $TOK" -H "User-Agent: wxedge-watchdog" \
        -H "Accept: application/vnd.github+json" \
        "https://api.github.com/repos/$REPO/actions/workflows/$WF/runs?status=in_progress&per_page=10" \
      | python3 -c "import sys,json;print(' '.join(str(r['id']) for r in json.load(sys.stdin).get('workflow_runs',[])))")
echo "[watchdog] run ที่ค้างอยู่: ${runs:-ไม่มี}"

for id in $runs; do
  if [ "$DRY" = "1" ]; then echo "  (dry) จะยกเลิก run $id"; else
    code=$(curl -s -o /dev/null -w "%{http_code}" -X POST -H "Authorization: Bearer $TOK" \
            -H "User-Agent: wxedge-watchdog" -H "Accept: application/vnd.github+json" \
            "https://api.github.com/repos/$REPO/actions/runs/$id/cancel")
    echo "  ยกเลิก run $id → HTTP $code"
  fi
done

[ -n "$runs" ] && sleep 10
# ── ปลด state ที่ค้าง: โซ่ที่ถูกยกเลิกทิ้ง state ว่า "ยังมีโซ่ถึง ..." ไว้ → โซ่ใหม่จะออกทันทีจาก guard ──
if [ "$DRY" = "1" ]; then
  echo "  (dry) จะปลด state (ตั้ง ends_at/beat_at เป็น 1 ชม.ที่แล้ว)"
else
  sha=$(curl -s -H "Authorization: Bearer $TOK" -H "User-Agent: wxedge-watchdog" \
          "https://api.github.com/repos/$REPO/contents/data/chain_state.json" \
        | python3 -c "import sys,json;print(json.load(sys.stdin).get('sha',''))")
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
            -d "$body" "https://api.github.com/repos/$REPO/contents/data/chain_state.json")
    echo "  ปลด state → HTTP $code"
  fi
fi
if [ "$DRY" = "1" ]; then
  echo "  (dry) จะ dispatch โซ่ใหม่"
else
  code=$(curl -s -o /dev/null -w "%{http_code}" -X POST -H "Authorization: Bearer $TOK" \
          -H "User-Agent: wxedge-watchdog" -H "Accept: application/vnd.github+json" \
          -d '{"ref":"main"}' \
          "https://api.github.com/repos/$REPO/actions/workflows/$WF/dispatches")
  echo "  dispatch โซ่ใหม่ → HTTP $code"
fi
echo "[watchdog] จบ"
