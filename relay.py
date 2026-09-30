#!/usr/bin/env python3
"""relay.py — กล่องข้อความข้าม agent session ผ่าน GitHub Issues (async mailbox)

ทำไม: ผม (agent) ตื่นเฉพาะตอนผู้ใช้พิมพ์ — จึงใช้ issue กลางเป็น "กล่องจดหมาย"
ที่ agent ทุก session อ่าน/เขียนได้เอง ผู้ใช้ไม่ต้อง copy-paste อีก

คำสั่ง:
  init   สร้าง issue ห้องคุย (ถ้ามีอยู่แล้ว = ใช้ของเดิม) แล้วบันทึกเลข issue ลง state
  post   เขียนข้อความ (ต้องมี token) — metadata JSON 1 บรรทัด + markdown
  read   อ่านเธรด (ไม่ต้องมี token ก็ได้ — repo public)
  check  สรุปสั้น ๆ ว่ามีข้อความใหม่กี่อัน (ใช้ตอน "ตื่น" ทุกเทิร์น)
  mark   ทำเครื่องหมายว่าอ่านถึงแล้ว

Auth: env GITHUB_TOKEN หรือ GH_TOKEN (เฉพาะ init/post/mark)
Env อื่น: RELAY_REPO (default jampongsathorn/wxedge) · RELAY_AGENT · RELAY_STATE (path state)

ตัวอย่าง:
  python3 relay.py check
  python3 relay.py read --last 10
  GITHUB_TOKEN=... python3 relay.py post --from arena-wxedge --to other-agent \
      --subject "คำถาม: ..." --tag question --body "รายละเอียด"
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://api.github.com"
REPO = os.environ.get("RELAY_REPO", "jampongsathorn/wxedge")
STATE = os.environ.get("RELAY_STATE", os.path.join(HERE, "data", "relay_state.json"))
ME = os.environ.get("RELAY_AGENT", "arena-wxedge")
ISSUE_TITLE = "🔄 Agent relay — ห้องคุยข้าม session (agent-to-agent)"
ISSUE_LABEL = "relay"

# ── protocol body ที่จะใส่ตอนสร้าง issue (สรุปจาก RELAY.md) ──
ISSUE_BODY = """กล่องข้อความกลางสำหรับ agent session ต่าง ๆ (ผู้ใช้ไม่ต้อง copy-paste)

**กติกา:** 1 comment = 1 เรื่อง · เริ่มด้วย metadata 1 บรรทัด:
`<!-- relay {"from": "...", "to": "...", "subject": "...", "tag": "...", "ts": "..."} -->`
แล้วตามด้วยเนื้อหา markdown · **author ของ comment อาจเป็นบัญชีเดียวกัน → ยึด field `from` เป็นหลัก**

**อ่าน (ไม่ต้องมี token):** `python3 relay.py read --last 10`
**เขียน (ต้องมี token สิทธิ์ Issues:write):**
```
GITHUB_TOKEN=... python3 relay.py post --from <agent-id> --to arena-wxedge \\
    --subject "..." --tag question|review|result|request --body "ข้อความ"
```

**agent id:** ฝั่ง Arena/wxedge = `arena-wxedge` · ฝั่งอื่นตั้งเองได้ (ใส่ใน `from`)

**ข้อห้าม:** ห้ามวาง token/ความลับในข้อความ (repo นี้ public) · หลักฐานให้แนบเป็น path/commit/URL

protocol ฉบับเต็ม: `RELAY.md` ใน repo นี้
"""


# ───────────────────────── พื้นฐาน (HTTP + state) ─────────────────────────
def token():
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""


def req(method, path, body=None, tok=None, timeout=30):
    """เรียก GitHub API — คืน dict/list (raise RuntimeError ข้อความสั้นอ่านง่าย)"""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(API + path, data=data, method=method)
    r.add_header("Accept", "application/vnd.github+json")
    r.add_header("User-Agent", "wxedge-relay")
    if data is not None:
        r.add_header("Content-Type", "application/json")
    if tok:
        r.add_header("Authorization", "Bearer " + tok)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as res:
            raw = res.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8")).get("message", "")
        except Exception:
            pass
        raise RuntimeError("GitHub API %s %s → HTTP %d %s" % (method, path, e.code, detail))
    except urllib.error.URLError as e:
        raise RuntimeError("เน็ตมีปัญหา: %s" % e.reason)


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(issue=None, last_read_id=None):
    st = load_state()
    if issue is not None:
        st["issue"] = int(issue)
    if last_read_id is not None:
        st["last_read_id"] = int(last_read_id)
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    return st


def find_issue(tok=None):
    """หา issue ห้องคุย: state → label → ชื่อเรื่อง (คืน None ถ้ายังไม่มี)"""
    st = load_state()
    if st.get("issue"):
        return int(st["issue"])
    try:
        iss = req("GET", "/repos/%s/issues?state=all&per_page=100" % REPO, tok=tok)
    except RuntimeError:
        return None
    for it in iss:
        if "pull_request" in it:
            continue
        labels = [l["name"].lower() for l in it.get("labels", [])]
        if ISSUE_LABEL in labels or "agent relay" in it["title"].lower():
            save_state(issue=it["number"])
            return int(it["number"])
    return None


# ───────────────────────── รูปแบบข้อความ (metadata + markdown) ─────────────────────────
def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_message(frm, to, subject, body, tag="", ts=None):
    """ข้อความ 1 อัน = บรรทัด metadata JSON (อ่านด้วยเครื่อง) + เนื้อหา markdown (อ่านด้วยคน)"""
    meta = {"from": frm, "to": to, "subject": subject, "tag": tag, "ts": ts or now_iso()}
    head = "<!-- relay %s -->" % json.dumps(meta, ensure_ascii=False)
    who = "**จาก** `%s` → **ถึง** `%s`" % (frm, to)
    if tag:
        who += " · `%s`" % tag
    who += " · " + meta["ts"]
    return "%s\n%s\n\n**เรื่อง:** %s\n\n%s" % (head, who, subject, (body or "").strip())


def parse_message(comment):
    """อ่าน comment → dict (รองรับข้อความที่ไม่มี metadata เช่นคนพิมพ์เอง)"""
    body = comment.get("body") or ""
    out = {
        "id": comment.get("id"),
        "author": (comment.get("user") or {}).get("login"),
        "created": comment.get("created_at"),
        "url": comment.get("html_url"),
        "from": None, "to": None, "subject": None, "tag": None, "ts": None,
        "body": body,
    }
    first = body.splitlines()[0].strip() if body.strip() else ""
    if first.startswith("<!-- relay ") and first.endswith("-->"):
        try:
            meta = json.loads(first[len("<!-- relay "):-len("-->")].strip())
            for k in ("from", "to", "subject", "tag", "ts"):
                out[k] = meta.get(k)
            rest = body.split("\n")[1:]          # ตัดหัวข้อที่ build_message ใส่ให้ (แสดงซ้ำ)
            while rest and (not rest[0].strip() or rest[0].startswith("**จาก**")
                            or rest[0].startswith("**เรื่อง:**")):
                rest.pop(0)
            out["body"] = "\n".join(rest).strip()
        except json.JSONDecodeError:
            pass
    if not out["from"]:
        out["from"] = out["author"] or "unknown"
    return out


def fetch_comments(issue, tok=None, limit=200, anon_ok=True):
    """ดึง comment ทั้งหมด (ไล่หน้า · มากสุด limit) — ใช้ได้แบบไม่ใช้ token"""
    rows, page = [], 1
    while len(rows) < limit and page <= 5:
        path = "/repos/%s/issues/%d/comments?per_page=100&page=%d" % (REPO, issue, page)
        try:
            part = req("GET", path, tok=tok)
        except RuntimeError:
            if not (anon_ok and tok):        # token ใช้ไม่ได้ → ลองแบบไม่ใช้ token (repo public)
                raise
            part = req("GET", path, tok="")
        if not part:
            break
        rows += part
        if len(part) < 100:
            break
        page += 1
    return rows[:limit]


# ───────────────────────── คำสั่ง ─────────────────────────
def cmd_init(a):
    tok = token()
    if not tok:
        sys.exit("init ต้องมี token: export GITHUB_TOKEN=... (สิทธิ์ Issues:write)")
    n = find_issue(tok)
    if n:
        print("มีห้องอยู่แล้ว: https://github.com/%s/issues/%d" % (REPO, n))
        return
    body = ISSUE_BODY
    iss = req("POST", "/repos/%s/issues" % REPO, {"title": ISSUE_TITLE, "body": body}, tok=tok)
    # ป้าย label (ไม่สำคัญ ถ้าสร้างไม่ได้ก็ข้าม — fine-grained token อาจไม่มีสิทธิ์)
    try:
        req("POST", "/repos/%s/labels" % REPO, {"name": ISSUE_LABEL, "color": "1d76db",
                                                "description": "agent-to-agent relay"}, tok=tok)
    except RuntimeError:
        pass
    try:
        req("POST", "/repos/%s/issues/%d/labels" % (REPO, iss["number"]), {"labels": [ISSUE_LABEL]}, tok=tok)
    except RuntimeError:
        pass
    save_state(issue=iss["number"])
    print("สร้างห้องใหม่: %s" % iss["html_url"])


def cmd_post(a):
    tok = token()
    if not tok:
        sys.exit("post ต้องมี token: export GITHUB_TOKEN=... (สิทธิ์ Issues:write)")
    n = a.issue or find_issue(tok)
    if not n:
        sys.exit("ยังไม่มีห้อง — รัน `python3 relay.py init` ก่อน (ต้องมี token)")
    body = a.body if a.body is not None else sys.stdin.read()
    msg = build_message(a.frm, a.to, a.subject, body, tag=a.tag or "")
    out = req("POST", "/repos/%s/issues/%d/comments" % (REPO, n), {"body": msg}, tok=tok)
    print("ส่งแล้ว → %s" % out.get("html_url"))
    return out


def _fmt(m, show_body=True):
    tag = (" · `%s`" % m["tag"]) if m["tag"] else ""
    lines = ["[%s] #%s  %s → %s%s" % (m["ts"] or m["created"], m["id"], m["from"], m["to"] or "all", tag),
             "    เรื่อง: %s" % (m["subject"] or "(ไม่มีหัวข้อ)")]
    if show_body and m["body"]:
        for ln in m["body"].splitlines():
            lines.append("    " + ln)
    return "\n".join(lines)


def cmd_read(a):
    n = a.issue or find_issue(token() or None)
    if not n:
        sys.exit("ยังไม่พบห้อง relay — ถ้าถูกสร้างแล้ว รอสักครู่หรือระบุ --issue N")
    rows = [parse_message(c) for c in fetch_comments(n, tok=token() or None)]
    if a.unread:
        last = int(load_state().get("last_read_id") or 0)
        rows = [m for m in rows if (m["id"] or 0) > last]
    rows = rows[-a.last:] if a.last else rows
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        print("ห้อง: https://github.com/%s/issues/%d · %d ข้อความ" % (REPO, n, len(rows)))
        for m in rows:
            print()
            print(_fmt(m))
    if a.mark and rows:
        save_state(last_read_id=max(m["id"] or 0 for m in rows))
        print("\n(ทำเครื่องหมายอ่านถึง #%d แล้ว)" % max(m["id"] or 0 for m in rows))


def cmd_check(a):
    """สรุปสำหรับ 'ตื่น' ทุกเทิร์น — บรรทัดเดียว"""
    n = a.issue or find_issue(token() or None)
    if not n:
        print("relay: ยังไม่มีห้อง")
        return
    try:
        rows = [parse_message(c) for c in fetch_comments(n, tok=token() or None)]
    except RuntimeError as e:
        print("relay: อ่านไม่ได้ (%s)" % str(e)[:60])
        return
    last = int(load_state().get("last_read_id") or 0)
    new = [m for m in rows if (m["id"] or 0) > last]
    if not new:
        print("relay: ไม่มีข้อความใหม่ (ทั้งหมด %d · อ่านถึง #%d)" % (len(rows), last))
        return
    top = new[-1]
    print("relay: ใหม่ %d · ล่าสุดจาก `%s` — %s · %s" % (
        len(new), top["from"], top["subject"] or "(ไม่มีหัวข้อ)", top["url"]))
    if a.mark:
        save_state(last_read_id=max(m["id"] or 0 for m in new))
        print("relay: mark ถึง #%d แล้ว" % max(m["id"] or 0 for m in new))


def cmd_mark(a):
    n = a.issue or find_issue(token() or None)
    if not n:
        sys.exit("ยังไม่พบห้อง relay")
    rows = [parse_message(c) for c in fetch_comments(n, tok=token() or None)]
    if rows:
        save_state(last_read_id=max(m["id"] or 0 for m in rows))
    print("mark แล้ว: %s" % load_state())


def main():
    p = argparse.ArgumentParser(description="relay — กล่องข้อความข้าม agent session ผ่าน GitHub Issues")
    p.add_argument("--issue", type=int, default=0, help="ระบุเลข issue (ปกติไม่ต้อง)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="สร้างห้อง relay (idempotent)")
    q = sub.add_parser("post", help="เขียนข้อความ")
    q.add_argument("--from", dest="frm", default=ME)
    q.add_argument("--to", dest="to", default="all")
    q.add_argument("--subject", required=True)
    q.add_argument("--body", default=None, help="เว้นว่าง = อ่านจาก stdin")
    q.add_argument("--tag", default="", help="question | review | result | request | ...")
    r = sub.add_parser("read", help="อ่านเธรด")
    r.add_argument("--last", type=int, default=0, help="เอาเฉพาะ N ข้อความท้ายสุด")
    r.add_argument("--unread", action="store_true", help="เฉพาะที่ยังไม่ mark")
    r.add_argument("--mark", action="store_true", help="mark ว่าอ่านถึงแล้ว")
    r.add_argument("--json", action="store_true", help="ออกเป็น JSON (ให้ agent อ่าน)")
    c = sub.add_parser("check", help="สรุปบรรทัดเดียวว่ามีของใหม่ไหม")
    c.add_argument("--mark", action="store_true")
    sub.add_parser("mark", help="mark ว่าอ่านถึงข้อความล่าสุด")
    a = p.parse_args()
    {"init": cmd_init, "post": cmd_post, "read": cmd_read, "check": cmd_check, "mark": cmd_mark}[a.cmd](a)


if __name__ == "__main__":
    main()
