#!/usr/bin/env python3
"""tests_chain.py — regression guard ของระบบโซ่เก็บข้อมูล (chain_loop / gate / watchdog / push)

ครอบคลุมบั๊กจริงที่เกิดวันที่ 25 ก.ย. 2026:
  B1 gate gap 5 นาที > รอบตื่นจริง 4น.45วิ → ถูกข้าม ต้องรอ 10 นาที
  B2 state ค้าง "beat สด" แต่โซ่ตาย → โซ่ใหม่ถูก guard สั่งออก + watchdog ไม่ปลด → หยุดเงียบ 25 นาที
  B3 push ชนกันกับ commit ที่เข้ามา → rebase ค้าง → push ไม่ได้อีกเลย (โซ่ค้าง 75 นาที)
  B4 run_forward_test.sh (cron VPS/Pi) เรียก gate.py --hour-window ที่ถูกลบไปแล้ว → ตายเงียบ

รัน: python3 tests_chain.py   (offline · ไม่แตะ data/ จริง · ไม่ยิง API)
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(ROOT, "deploy", "chain_lib.sh")
fails = []


def check(name, cond, detail=""):
    mark = "✓" if cond else "✗"
    print("%s %s%s" % (mark, name, (" — %s" % detail) if detail and not cond else ""))
    if not cond:
        fails.append(name)


def run(cmd, env=None, cwd=None, timeout=120):
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True,
                          cwd=cwd or ROOT, env=e, timeout=timeout)


def iso(delta_seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_state(path, end_delta_s, beat_delta_s):
    json.dump({"ends_at": iso(end_delta_s), "beat_at": iso(beat_delta_s)}, open(path, "w", encoding="utf-8"))


tmp = tempfile.mkdtemp(prefix="wxedge_tests_chain_")
try:
    print("═══ T1: gate gap ต้องตรงรอบตื่น (B1) ═══")
    state = os.path.join(tmp, "snap.jsonl")
    with open(state, "w", encoding="utf-8") as fh:
        for c in ("nyc", "buenos-aires"):
            fh.write(json.dumps({"ts": iso(-285), "city": c, "target": "2026-09-25", "hour": 15, "unit": "F",
                                 "obs_so_far_c": 20.0, "mu_c": 22.0, "sigma_c": 1.2, "n_hist": 10,
                                 "max_so_far_c": 20.0, "bins": []}) + "\n")

    def entry_cities(gap):
        out = run(["python3", "deploy/gate.py", "--entry", "15:00-16:30", "--entry-gap", str(gap),
                   "--ctx", "12:30-18:30", "--ctx-gap", "11", "--state-file", state]).stdout
        for line in out.splitlines():
            if line.startswith("entry_cities="):
                return set(x for x in line.split("=", 1)[1].split(",") if x)
        return set()

    e5, e4 = entry_cities(5), entry_cities(4)
    check("T1a สแกนล่าสุด 285 วิ + gap=4 → เก็บ (ค่า production)", "nyc" in e4, str(sorted(e4)))
    check("T1b สแกนล่าสุด 285 วิ + gap=5 → ข้าม (บั๊กเดิมถูกจับได้)", "nyc" not in e5, str(sorted(e5)))

    print()
    print("═══ T2: ตัวเรียก gate.py ทุกตัวต้องใช้ flag ที่มีจริง (B4) ═══")
    supported = set()
    h = run(["python3", "deploy/gate.py", "--help"]).stdout
    supported = set(re.findall(r"(--[a-z-]+)", h))
    check("T2a อ่าน flag ที่ gate.py รองรับได้", "--entry" in supported and "--entry-gap" in supported,
          str(sorted(supported)))
    callers = []
    for dirpath, _dirs, files in os.walk(ROOT):
        if ".git" in dirpath:
            continue
        for fn in files:
            if fn.endswith((".sh", ".yml", ".yaml")):
                p = os.path.join(dirpath, fn)
                raw = open(p, encoding="utf-8", errors="ignore").read()
                # ต่อบรรทัดที่ขึ้นบรรทัดใหม่ด้วย backslash แล้วตัดบรรทัดที่เป็นคอมเมนต์ออก
                txt = raw.replace("\\\n", " ")
                for line in txt.splitlines():
                    if line.lstrip().startswith("#"):
                        continue
                    line = line.split("#", 1)[0]        # ตัดคอมเมนต์ท้ายบรรทัด (เช่น "# ใช้กับ gate.py v2 ...")
                    for m in re.finditer(r"gate\.py([^\n]*)", line):
                        callers.append((os.path.relpath(p, ROOT), m.group(1)))
    bad = []
    for path, args in callers:
        for flag in re.findall(r"(--[a-z-]+)", args):
            if flag.startswith("--") and flag not in supported and flag not in ("--max-bins",):
                bad.append("%s: %s" % (path, flag))
    check("T2b ไม่มีใครเรียก flag ที่ gate.py ไม่รู้จัก (เช่น --hour-window)", not bad, "; ".join(bad))
    check("T2c พบตัวเรียก gate.py อย่างน้อย 3 ที่ (loop/watchdog/runner)",
          len({p for p, _ in callers}) >= 3, str(sorted({p for p, _ in callers})))

    print()
    print("═══ T3: guard ของโซ่ (B2 ฝั่งโซ่) ═══")
    cases = [
        ("T3a state ค้าง (ends อนาคต · beat เก่า 40 นาที) → เริ่มได้", 18000, -2400, 1),
        ("T3b state สด (ends อนาคต · beat 2 นาที) → ออก", 18000, -120, 0),
        ("T3c state หมดอายุ (ends ในอดีต · beat เก่า) → เริ่มได้", -600, -3600, 1),
    ]
    for name, e_d, b_d, want in cases:
        st = os.path.join(tmp, "guard.json")
        write_state(st, e_d, b_d)
        code = ("set -uo pipefail; . '%s'; read -r E B < <(chain_state_fields '%s'); "
                "if chain_guard_should_exit \"$E\" \"$B\" \"$(date -u +%%s)\" 25; then exit 0; else exit 1; fi") % (LIB, st)
        rc = run(["bash", "-c", code]).returncode
        check(name, rc != 0 if want else rc == 0, "rc=%d" % rc)

    print()
    print("═══ T4: push ชนกัน → ต้องฟื้นได้ (B3) ═══")
    bare = os.path.join(tmp, "origin.git")
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", bare], check=True)
    env_git = dict(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@x")
    chain_dir = os.path.join(tmp, "chain")
    other_dir = os.path.join(tmp, "other")
    for d in (chain_dir, other_dir):
        subprocess.run(["git", "clone", "-q", bare, d], check=True, env=dict(os.environ, **env_git))
    os.makedirs(os.path.join(chain_dir, "data", "snapshots"), exist_ok=True)
    os.makedirs(os.path.join(chain_dir, "docs"), exist_ok=True)
    open(os.path.join(chain_dir, "data", "snapshots", "d.jsonl"), "w").write("chain-v0\n")
    open(os.path.join(chain_dir, "docs", "index.html"), "w").write("page-v0\n")
    subprocess.run(["git", "add", "-A"], cwd=chain_dir, check=True, env=dict(os.environ, **env_git))
    subprocess.run(["git", "commit", "-qm", "init"], cwd=chain_dir, check=True, env=dict(os.environ, **env_git))
    subprocess.run(["git", "push", "-q", "-u", "origin", "main"], cwd=chain_dir, check=True)

    # ฝั่งอื่นแก้ "ไฟล์เดียวกัน" แล้ว push (จำลอง commit ที่เข้ามาชนกันจริง)
    subprocess.run(["git", "pull", "-q", "origin", "main"], cwd=other_dir, check=True,
                   env=dict(os.environ, **env_git))
    os.makedirs(os.path.join(other_dir, "data", "snapshots"), exist_ok=True)
    os.makedirs(os.path.join(other_dir, "docs"), exist_ok=True)
    open(os.path.join(other_dir, "data", "snapshots", "d.jsonl"), "w").write("other-v1\n")
    open(os.path.join(other_dir, "docs", "index.html"), "w").write("page-v1-other\n")
    subprocess.run(["git", "add", "-A"], cwd=other_dir, check=True, env=dict(os.environ, **env_git))
    subprocess.run(["git", "commit", "-qm", "other: แก้ไฟล์เดียวกัน"], cwd=other_dir, check=True,
                   env=dict(os.environ, **env_git))
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=other_dir, check=True)

    # ฝั่งโซ่มีข้อมูลใหม่ไฟล์เดียวกัน
    open(os.path.join(chain_dir, "data", "snapshots", "d.jsonl"), "w").write("chain-v1\n")
    subprocess.run(["git", "add", "-A"], cwd=chain_dir, check=True, env=dict(os.environ, **env_git))
    subprocess.run(["git", "commit", "-qm", "chain: ข้อมูลใหม่"], cwd=chain_dir, check=True,
                   env=dict(os.environ, **env_git))

    # (a) ตรรกะเดิม: pull --rebase || true + push ×3 → ต้องล้มเหลว (พิสูจน์บั๊ก)
    old = ("for i in 1 2 3; do git pull --rebase -q origin main 2>/dev/null || true; "
           "git push -q origin main 2>/dev/null && exit 0; sleep 1; done; exit 1")
    rc_old = subprocess.run(["bash", "-c", old], cwd=chain_dir, capture_output=True).returncode
    check("T4a ตรรกะเดิม (pull --rebase) push ไม่ผ่าน = บั๊กเดิม", rc_old != 0, "rc=%d" % rc_old)
    subprocess.run(["git", "rebase", "--abort"], cwd=chain_dir, capture_output=True)

    # (b) ตรรกะใหม่จาก chain_lib.sh → ต้อง push ผ่าน และข้อมูลฝั่งเราชนะ
    #     (จำลองรอบใหม่: มีข้อมูลเพิ่มจริง เหมือนทุกรอบของโซ่)
    open(os.path.join(chain_dir, "data", "snapshots", "d.jsonl"), "w").write("chain-v2\n")
    subprocess.run(["git", "remote", "set-url", "origin", bare], cwd=chain_dir, check=True)
    code = ("set -uo pipefail; . '%s'; git remote set-url origin '%s'; PUSH=1 chain_git_push_round 'test'; "
            "echo \"RC=$? FAIL=$CHAIN_PUSH_FAIL\"") % (LIB, bare)
    out = subprocess.run(["bash", "-c", code], cwd=chain_dir, capture_output=True, text=True,
                         env=dict(os.environ, **env_git)).stdout
    check("T4b ตรรกะใหม่ push ผ่าน (RC=0)", "RC=0" in out, out.strip())
    subprocess.run(["git", "fetch", "-q", "origin", "main"], cwd=chain_dir, check=True)
    remote_data = subprocess.run(["git", "show", "origin/main:data/snapshots/d.jsonl"], cwd=chain_dir,
                                 capture_output=True, text=True).stdout.strip()
    check("T4c ข้อมูลฝั่งโซ่ชนะหลัง merge (chain-v2)", remote_data == "chain-v2", remote_data)

    print()
    print("═══ T5: watchdog ตัดสินใจ + ปลด state (B2 ฝั่ง watchdog) ═══")
    wd = os.path.join(ROOT, "deploy", "chain_watchdog.sh")
    for name, beat_d, runs, want in (
            ("T5a beat สด + มี run active → healthy", -60, "1", "healthy"),
            ("T5b beat สด + ไม่มี run → dead (บั๊กเดิมมองไม่เห็น)", -60, "0", "dead"),
            ("T5c beat ค้าง 71 นาที + มี run → dead (เคสโซ่ค้างจริง)", -4260, "1", "dead")):
        st = os.path.join(tmp, "wd.json")
        write_state(st, 18000, beat_d)
        out = run(["bash", wd], env={"STATE": st, "LOCAL": "1", "ACTIVE_RUNS": runs}).stdout
        got = "healthy" if "→ healthy" in out else ("dead" if "→ dead" in out else "?")
        check(name, got == want, "got=%s" % got)

    st = os.path.join(tmp, "wd_clear.json")
    write_state(st, 18000, -60)
    run(["bash", wd], env={"STATE": st, "LOCAL": "1", "ACTIVE_RUNS": "0"})
    after = json.load(open(st, encoding="utf-8"))
    age_min = (datetime.now(timezone.utc)
               - datetime.strptime(after["beat_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
               ).total_seconds() / 60
    check("T5d ปลด state แล้ว (beat กลายเป็นอดีต → โซ่ใหม่เริ่มได้ทันที)", age_min > 30, after["beat_at"])

    print()
    print("═══ T6: chain_loop.sh end-to-end (offline · PUSH=0) ═══")
    for name, beat_d, expect_start in (
            ("T6a state สด → โซ่ออกทันที (กันซ้อน)", -60, False),
            ("T6b state ค้าง → โซ่เริ่มทำงาน", -2400, True)):
        st = os.path.join(tmp, "loop_state.json")
        write_state(st, 18000, beat_d)
        out = run(["bash", "deploy/chain_loop.sh"],
                  env={"STATE": st, "PUSH": "0", "CHAIN_HOURS": "0.005", "INTERVAL_MIN": "5",
                       "ENTRY_WIN": "00:00-00:01", "CTX_WIN": "00:00-00:01",
                       "CHAIN_PAT": ""}, timeout=180).stdout
        started = "เริ่มโซ่" in out
        check(name, started == expect_start, "out=%s" % out.strip().replace("\n", " | ")[:120])

    print()
    print("═══ T7: ไฟล์ที่ต้องมี + อ้างอิงถูกต้อง ═══")
    check("T7a มี deploy/chain_lib.sh", os.path.exists(LIB))
    libtxt = open(LIB, encoding="utf-8").read()
    check("T7a2 lib: stage ทีละ path (path หายต้องไม่ทำให้ข้อมูลหาย)",
          "chain_git_stage_paths" in libtxt and "for p in data reports docs; do" not in libtxt)
    for f in ("deploy/chain_loop.sh", "deploy/chain_watchdog.sh"):
        txt = open(os.path.join(ROOT, f), encoding="utf-8").read()
        check("T7b %s ใช้ chain_lib (ไม่คัดลอกตรรกะ)" % os.path.basename(f), "chain_lib.sh" in txt)
    wf = open(os.path.join(ROOT, ".github", "workflows", "forward-chain.yml"), encoding="utf-8").read()
    for key, want in (("ENTRY_GAP", '"4"'), ("ENTRY_WIN", '"15:00-16:30"'), ("CTX_GAP", '"11"')):
        m = re.search(key + r':\s*"([^"]+)"', wf)
        check("T7c workflow: %s=%s" % (key, want), bool(m) and ('"' + m.group(1) + '"') == want,
              m.group(1) if m else "ไม่พบ")
    gp = re.search(r"ENTRY_GAP", wf) and re.search(r'INTERVAL_MIN:\s*"(\d+)"', wf)
    if gp:
        ival = int(gp.group(1))
        egap = int(re.search(r'ENTRY_GAP:\s*"(\d+)"', wf).group(1))
        check("T7d entry gap < รอบตื่น (ไม่งั้นถูกข้ามเป็นรอบคู่)", egap < ival, "gap=%d interval=%d" % (egap, ival))
    runner = open(os.path.join(ROOT, "deploy", "run_forward_test.sh"), encoding="utf-8").read()
    gate_calls = " ".join(a for _f, a in callers if _f.endswith("run_forward_test.sh"))
    check("T7e run_forward_test.sh เรียก gate ด้วย flag v2 (ไม่มี --hour-window)",
          "--entry" in gate_calls and "--hour-window" not in gate_calls, gate_calls[:80])
    check("T7f run_forward_test.sh ใช้ chain_lib ในการ push (ไม่ push ตรง)", "chain_git_push_round" in runner)
    loop = open(os.path.join(ROOT, "deploy", "chain_loop.sh"), encoding="utf-8").read()
    check("T7g chain_loop ไม่กลืน error ของ gate (ไม่มี 2>/dev/null บนบรรทัด gate)",
          "gate.py --entry" in loop and "2>/dev/null" not in [l for l in loop.splitlines() if "gate.py" in l][0])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("ผลรวม: %s" % ("ผ่านทั้งหมด ✓" if not fails else "ไม่ผ่าน %d รายการ: %s" % (len(fails), fails)))
sys.exit(1 if fails else 0)
