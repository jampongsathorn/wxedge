#!/usr/bin/env bash
# deploy/setup_github.sh — ตั้งค่า repo GitHub สำหรับ forward-test ในคำสั่งเดียว
# รันได้ทั้งในแซนด์บ็อกซ์นี้และบนเครื่องของคุณเอง
#
# วิธีใช้:
#   bash deploy/setup_github.sh https://github.com/<user>/wxedge.git
#   TOKEN=ghp_xxx bash deploy/setup_github.sh https://github.com/<user>/wxedge.git   # ไม่ต้องพิมพ์รหัส
#   bash deploy/setup_github.sh            # ถ้าติดตั้ง gh CLI ไว้ จะสร้าง repo ให้อัตโนมัติ
#
# สิ่งที่ทำ: วาง workflow → .gitignore → git init/commit → ตั้ง remote → push
# ปลอดภัย: ถ้าใช้ TOKEN ตัวสคริปต์จะลบ token ออกจาก .git/config ทันทีหลัง push
set -euo pipefail
cd "$(dirname "$0")/.."

URL="${1:-}"

echo "① วาง workflow"
mkdir -p .github/workflows
cp -f deploy/github-actions/forward-test.yml .github/workflows/forward-test.yml
[ -f .github/workflows/tests.yml ] || echo "(ไม่มี tests.yml — ข้าม)"
[ -f .gitignore ] || printf '__pycache__/\n*.pyc\n.venv/\n.cache/\n*.log\ndeploy/*.tar.gz\n' > .gitignore

echo "② เตรียม git repo"
[ -d .git ] || git init -q -b main
GIT_NAME="${GIT_NAME:-wxedge}"; GIT_EMAIL="${GIT_EMAIL:-wxedge@users.noreply.github.com}"
git add -A
if git diff --cached --quiet; then
  echo "   ไม่มีอะไรใหม่ให้ commit"
else
  git -c user.name="$GIT_NAME" -c user.email="$GIT_EMAIL" \
      commit -q -m "wxedge: research toolkit + forward-test runner" || true
  echo "   commit แล้ว: $(git log --oneline -1)"
fi
git branch -M main 2>/dev/null || true

echo "③ ตั้ง remote + push"
if [ -z "$URL" ]; then
  if command -v gh >/dev/null 2>&1; then
    echo "   พบ gh CLI → สร้าง repo ใหม่ให้"
    gh repo create wxedge --private --source=. --remote=origin --push
    echo "✅ เสร็จ — เปิด Actions เพื่อกด Run workflow"
    exit 0
  fi
  echo "⚠ ไม่ได้ใส่ URL ของ repo และไม่พบ gh CLI"
  echo "  ทำต่อ: สร้าง repo เปล่าบน GitHub แล้วรัน:"
  echo "     bash deploy/setup_github.sh https://github.com/<user>/<repo>.git"
  exit 0
fi

git remote remove origin 2>/dev/null || true
if [ -n "${TOKEN:-}" ]; then
  AUTH_URL=$(printf '%s' "$URL" | sed -E "s#^https://#https://x-access-token:${TOKEN}@#")
  git remote add origin "$AUTH_URL"
  git push -u origin main
  git remote set-url origin "$URL"          # ลบ token ออกจาก config ทันที
  echo "   (ลบ token ออกจาก .git/config แล้ว)"
else
  git remote add origin "$URL"
  git push -u origin main
fi

echo "✅ เสร็จ — ไปที่หน้า repo → Actions → wxedge-forward-test → Run workflow เพื่อทดสอบทันที"
echo "   ตั้งเวลาไว้แล้ว: ทุกชั่วโมงนาทีที่ 5 (UTC) + รายวัน 03:00 UTC"
