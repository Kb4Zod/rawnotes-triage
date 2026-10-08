#!/usr/bin/env python3
"""rawnotes-triage — local website for reviewing ~/Projects/RawNotes.

Usage: python3 server.py [--port 8765] [--host 127.0.0.1] [--notes ~/Projects/RawNotes] [--restart]

--host 0.0.0.0 listens on every interface (phone on home Wi-Fi / Tailscale).
There is no login: only do that on a network you trust. Firewall still applies.

If a server is already on the port, warns when its version differs from this
code; --restart stops it and starts this one.

Actions (all move real files, nothing is deleted):
  promote  -> copy note into ../experiments|active|learning|templates,
              stamp promoted/due dates, move capture to done/
  durable  -> copy note into ../reference/<slug>.md (YAML frontmatter), move capture to done/
  archive  -> status: dead, reason in ## Outcome, move to done/
  keep     -> status open|cooking, optional next-step line
"""
import argparse, json, os, re, shutil, subprocess, datetime as dt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

VERSION = "1.06"  # bump on every change; history in CHANGELOG.md
HERE = os.path.dirname(os.path.abspath(__file__))
NOTES = os.path.expanduser("~/Projects/RawNotes")
PROJECTS = None
STARTED = dt.datetime.now().isoformat(timespec="seconds")
DUE_DAYS = 7
STALE_DAYS = 60
NOTE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-.+\.md$")
DESTS = {  # promote targets: folder -> (layout)
    "experiments": "folder", "active": "folder",
    "learning": "file", "templates": "file",
}

def today(): return dt.date.today()

def field(text, key):
    m = re.search(rf"^- {key}:\s*([^\s<]+)", text, re.M)
    return m.group(1) if m else ""

def set_field(text, key, value):
    pat = re.compile(rf"^(- {key}:\s*)\S+(.*)$", re.M)
    if pat.search(text):
        return pat.sub(lambda m: f"{m.group(1)}{value}{m.group(2)}", text, count=1)
    # insert after the first "- date:" line, or after the title
    lines = text.split("\n")
    for i, l in enumerate(lines):
        if l.startswith("- date:"):
            lines.insert(i + 1, f"- {key}: {value}"); return "\n".join(lines)
    lines.insert(1, f"- {key}: {value}")
    return "\n".join(lines)

def title_of(text, fname):
    m = re.search(r"^#\s+(.+)$", text, re.M)
    return m.group(1).strip() if m else fname

def append_outcome(text, line):
    if "## Outcome" in text:
        return text.rstrip("\n") + f"\n{line}\n"
    return text.rstrip("\n") + f"\n\n## Outcome\n\n{line}\n"

def summarize(path, sub=""):
    fname = os.path.basename(path)
    text = open(path, encoding="utf-8", errors="replace").read()
    date = fname[:10]
    try: age = (today() - dt.date.fromisoformat(date)).days
    except ValueError: age = None
    body = re.search(r"## What's the question.*?\n\n(.+?)(?:\n\n|\n## )", text, re.S)
    return {
        "file": (sub + "/" if sub else "") + fname, "abs_path": path, "title": title_of(text, fname),
        "date": date, "age": age, "status": field(text, "status") or "?",
        "type": field(text, "type") or "?",
        "stale": age is not None and age > STALE_DAYS and field(text, "status") in ("open", ""),
        "question": (body.group(1).strip() if body else "")[:280],
        "size": len(text),
    }

def list_notes():
    live = sorted(f for f in os.listdir(NOTES) if NOTE_RE.match(f))
    done_dir = os.path.join(NOTES, "done")
    done = sorted(f for f in os.listdir(done_dir) if NOTE_RE.match(f)) if os.path.isdir(done_dir) else []
    return {
        "live": [summarize(os.path.join(NOTES, f)) for f in live],
        "done": [summarize(os.path.join(done_dir, f), "done") for f in reversed(done)],
        "inflight": inflight(),
        "today": today().isoformat(), "notes_dir": NOTES,
    }

def inflight():
    """Promoted notes with a due date, found in the sibling project folders."""
    out = []
    for dest in ("active", "experiments", "learning", "templates", "maintained"):
        d = os.path.join(PROJECTS, dest)
        if not os.path.isdir(d): continue
        for root, dirs, files in os.walk(d):
            if root[len(d):].count(os.sep) > 1: dirs[:] = []; continue
            for f in files:
                if not f.endswith(".md"): continue
                p = os.path.join(root, f)
                try: text = open(p, encoding="utf-8", errors="replace").read(4000)
                except OSError: continue
                due = field(text, "due")
                if not due: continue
                try: left = (dt.date.fromisoformat(due) - today()).days
                except ValueError: left = None
                out.append({"path": os.path.relpath(p, PROJECTS), "title": title_of(text, f),
                            "dest": dest, "promoted": field(text, "promoted"), "due": due,
                            "days_left": left, "status": field(text, "status") or "?",
                            "done": field(text, "status") in ("done", "shipped", "dead")})
    out.sort(key=lambda x: (x["done"], x["days_left"] if x["days_left"] is not None else 999))
    return out

def active_projects():
    base = os.path.join(PROJECTS, "active")
    if not os.path.isdir(base): return []
    return [{"name": name, "path": "active/" + name, "abs_path": os.path.join(base, name),
             "repo": os.path.exists(os.path.join(base, name, ".git"))}
            for name in sorted(os.listdir(base), key=str.casefold)
            if not name.startswith(".") and os.path.isdir(os.path.join(base, name))]

def project_detail(name):
    if name not in {p["name"] for p in active_projects()}: raise ValueError("unknown project")
    path = os.path.join(PROJECTS, "active", name)
    result = {"name": name, "path": "active/" + name, "abs_path": path, "summary": "No README or project notes yet.",
              "summary_source": None, "commits": [], "features": [], "prs": [],
              "pr_status": "No Git repository in this project.", "branch": "", "remote": ""}
    files = {f.lower(): f for f in sorted(os.listdir(path))}
    for candidate in ("readme.md", "readme.rst", "readme.txt", "readme", "notes.md"):
        if candidate not in files: continue
        try:
            with open(os.path.join(path, files[candidate]), encoding="utf-8", errors="replace") as f:
                source = f.read(12000)
            # Use a source excerpt rather than inventing a repository description.
            paragraphs = re.split(r"\n\s*\n", source)
            prose = [x.strip() for x in paragraphs if x.strip() and not x.lstrip().startswith(("#", "```", "..", "- date:", "- status:"))]
            result.update(summary=(prose[0][:1200] if prose else source[:1200]), summary_source=files[candidate])
            break
        except OSError: continue
    if not os.path.exists(os.path.join(path, ".git")): return result
    def run(args, timeout=5):
        try:
            proc = subprocess.run(args, cwd=path, capture_output=True, text=True, timeout=timeout,
                                  env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1"})
            return proc.stdout.strip() if proc.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired): return None
    result["branch"] = run(["git", "branch", "--show-current"]) or "Detached HEAD / no commits"
    remote = run(["git", "remote", "get-url", "origin"]) or ""
    match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([\w.-]+/[\w.-]+?)(?:\.git)?/?", remote)
    if match: result["remote"] = "https://github.com/" + match[1]
    history = run(["git", "log", "-100", "--format=%h%x09%cs%x09%s"])
    commits = []
    for line in (history or "").splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3: commits.append(dict(zip(("sha", "date", "title"), parts)))
    result["commits"] = commits[:10]
    result["features"] = [c for c in commits if re.match(r"^feat(?:ure)?(?:\([^)]*\))?!?:", c["title"], re.I)][:10]
    result["pr_status"] = "PRs require a GitHub origin remote."
    if match:
        prs = run(["gh", "pr", "list", "--repo", match[1], "--state", "all", "--limit", "10",
                   "--json", "number,title,state,url,updatedAt,mergedAt", "--search", "sort:updated-desc"], timeout=12)
        result["pr_status"] = "GitHub PRs unavailable. Check gh installation, sign-in, and network access."
        if prs is not None:
            try:
                result["prs"] = json.loads(prs)
                result["pr_status"] = "" if result["prs"] else "No pull requests found."
            except ValueError: pass
    return result

def reference_copy(text, fname, why=""):
    """Turn the '- key: value' header into YAML frontmatter, so Obsidian shows it as Properties."""
    lines = text.split("\n"); meta = {}; i = 1
    while i < len(lines) and (not lines[i].strip() or re.match(r"^- (date|status|type|promoted|due):", lines[i])):
        m = re.match(r"^- (\w+):\s*([^\s<]+)", lines[i])
        if m: meta[m.group(1)] = m.group(2)
        i += 1
    fm = ["---", f"created: {meta.get('date', '')}", f"promoted: {meta.get('promoted', '')}",
          "status: promoted", f"type: {meta.get('type', 'research')}", f"source: RawNotes/done/{fname}"]
    if why: fm.append("why: " + json.dumps(why))
    fm += ["tags:", "  - reference", "---", ""]
    return "\n".join(fm + [lines[0], ""] + lines[i:])

def safe_note(rel):
    """Resolve a note path from the API and refuse anything outside NOTES."""
    p = os.path.realpath(os.path.join(NOTES, rel))
    if not p.startswith(os.path.realpath(NOTES) + os.sep) or not p.endswith(".md"):
        raise ValueError("bad path")
    if not os.path.isfile(p): raise ValueError("no such note")
    return p

def slug_of(fname): return re.sub(r"^\d{4}-\d{2}-\d{2}-", "", fname)[:-3]

def unique(path):
    base, ext = os.path.splitext(path); n = 2
    while os.path.exists(path):
        path = f"{base}-{n}{ext}"; n += 1
    return path

def to_done(src):
    done_dir = os.path.join(NOTES, "done"); os.makedirs(done_dir, exist_ok=True)
    dst = unique(os.path.join(done_dir, os.path.basename(src)))
    shutil.move(src, dst); return dst

def log(msg):
    with open(os.path.join(HERE, "triage.log"), "a") as fh:
        fh.write(f"{dt.datetime.now().isoformat(timespec='seconds')} {msg}\n")

def act(req):
    src = safe_note(req["file"]); fname = os.path.basename(src)
    action = req.get("action"); note = req.get("note", "").strip()
    text = open(src, encoding="utf-8").read()
    t = today().isoformat()

    if action == "keep":
        status = req.get("status", "cooking")
        if status not in ("open", "cooking"): raise ValueError("bad status")
        text = set_field(text, "status", status)
        if note: text = text.rstrip("\n") + f"\n\n- next step ({t}): {note}\n" if "## Notes" not in text else \
            re.sub(r"(## Notes\n)", rf"\1\n- next step ({t}): {note}\n", text, count=1)
        open(src, "w", encoding="utf-8").write(text)
        log(f"keep {fname} status={status}"); return {"ok": True, "msg": f"Kept ({status})"}

    if action == "archive":
        text = set_field(text, "status", "dead")
        text = append_outcome(text, f"**Killed {t}:** {note or 'no longer useful.'}")
        open(src, "w", encoding="utf-8").write(text)
        dst = to_done(src); log(f"archive {fname} -> {dst}")
        return {"ok": True, "msg": "Archived to done/"}

    if action in ("promote", "durable"):
        dest = "reference" if action == "durable" else req.get("dest")
        if action == "promote" and dest not in DESTS: raise ValueError("bad destination")
        slug = re.sub(r"[^a-z0-9-]", "", (req.get("slug") or slug_of(fname)).lower()) or slug_of(fname)
        dest_dir = os.path.join(PROJECTS, dest)
        os.makedirs(dest_dir, exist_ok=True)
        if action == "promote" and DESTS[dest] == "folder":
            proj = unique(os.path.join(dest_dir, slug)); os.makedirs(proj)
            target = os.path.join(proj, "NOTES.md")
        else:
            target = unique(os.path.join(dest_dir, slug + ".md"))
        copy = set_field(text, "status", "promoted")
        copy = set_field(copy, "promoted", t)
        if action == "durable": copy = reference_copy(copy, fname, note)
        if action == "promote":
            due = (today() + dt.timedelta(days=int(req.get("due_days") or DUE_DAYS))).isoformat()
            copy = set_field(copy, "due", due)
            copy = copy.rstrip("\n") + (
                f"\n\n## Plan\n\n- promoted from RawNotes on {t}, due {due}\n"
                f"- definition of done: {note or '<fill in>'}\n"
                "- first step:\n- ship / stop check-in: on the due date, either ship it or kill it\n")
        open(target, "w", encoding="utf-8").write(copy)
        rel = os.path.relpath(target, PROJECTS)
        # original capture: mark promoted, record destination, move to done/
        text = set_field(text, "status", "promoted")
        text = append_outcome(text, f"**Promoted {t}** -> `../{rel}`" + (f" ({note})" if note and action == 'durable' else ""))
        open(src, "w", encoding="utf-8").write(text)
        dst = to_done(src); log(f"{action} {fname} -> {rel}; capture -> {dst}")
        return {"ok": True, "msg": f"Copied to ../{rel}, capture moved to done/", "target": rel}

    raise ValueError("unknown action")

class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def log_message(self, *a): pass
    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        try:
            if u.path == "/": return self._send(200, open(os.path.join(HERE, "index.html"), "rb").read().replace(b"__VERSION__", VERSION.encode()), "text/html")
            if u.path == "/api/version": return self._send(200, {"version": VERSION, "started": STARTED, "pid": os.getpid()})
            if u.path == "/api/projects": return self._send(200, {"projects": active_projects()})
            if u.path == "/api/project": return self._send(200, project_detail(q.get("name", [""])[0]))
            if u.path == "/api/notes": return self._send(200, list_notes())
            if u.path == "/api/note":
                p = safe_note(q["f"][0]); return self._send(200, {"text": open(p, encoding="utf-8", errors="replace").read()})
            self._send(404, {"error": "not found"})
        except Exception as e: self._send(400, {"error": str(e)})
    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0)); req = json.loads(self.rfile.read(n) or b"{}")
            if urlparse(self.path).path == "/api/action": return self._send(200, act(req))
            if urlparse(self.path).path == "/api/open":
                import subprocess; subprocess.Popen(["xdg-open", req["path"]]); return self._send(200, {"ok": True})
            self._send(404, {"error": "not found"})
        except Exception as e: self._send(400, {"error": str(e)})

def running_server(port):
    """Ask whatever is on the port what it is. None = nothing there.
    Servers older than 1.05 have no /api/version, so they report version None."""
    import urllib.request, urllib.error
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/version", timeout=2) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError: return {"version": None, "started": None, "pid": None}
    except (OSError, ValueError): return None

def port_pid(port):
    out = subprocess.run(["ss", "-ltnpH", f"sport = :{port}"], capture_output=True, text=True).stdout
    m = re.search(r"pid=(\d+)", out); return int(m.group(1)) if m else None

def stop_server(info, port):
    import signal, time
    pid = info.get("pid") or port_pid(port)
    if not pid: raise SystemExit(f"rawtriage-web: can't find the process on port {port}; stop it by hand.")
    os.kill(pid, signal.SIGTERM)
    for _ in range(50):
        if running_server(port) is None: return pid
        time.sleep(0.1)
    raise SystemExit(f"rawtriage-web: pid {pid} didn't stop; try kill {pid}")

def notify(msg):
    try: subprocess.Popen(["notify-send", "RawNotes Triage", msg], stderr=subprocess.DEVNULL)
    except OSError: pass

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1", help="interface to listen on; 0.0.0.0 = all (no auth, trusted networks only)")
    ap.add_argument("--notes", default=NOTES); ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--restart", action="store_true", help="stop a server already on the port, then start this one")
    a = ap.parse_args(); NOTES = os.path.abspath(os.path.expanduser(a.notes)); PROJECTS = os.path.dirname(NOTES)
    url = f"http://127.0.0.1:{a.port}/"
    info = running_server(a.port)
    if info and a.restart:
        pid = stop_server(info, a.port)
        print(f"rawtriage-web: stopped version {info['version'] or 'older than 1.05'} (pid {pid})")
    elif info:
        if info["version"] == VERSION:
            print(f"rawtriage-web: version {VERSION} already running (started {info['started']}), opening it")
        else:
            msg = (f"An old server is already running on port {a.port}\n"
                   f"  running:   version {info['version'] or 'older than 1.05'}"
                   + (f"   (started {info['started']})" if info["started"] else "") + "\n"
                   f"  this code: version {VERSION}\n"
                   f"  -> run: rawtriage-web --restart")
            print("rawtriage-web: " + msg); notify(msg)
        if not a.no_browser:
            import webbrowser; webbrowser.open(url)
        raise SystemExit(0)
    try: httpd = ThreadingHTTPServer((a.host, a.port), H)
    except OSError as e: raise SystemExit(f"rawtriage-web: port {a.port} is in use by something else ({e}); try --port N")
    print(f"rawnotes-triage v{VERSION}: {NOTES}\n{url}  (Ctrl-C to stop)")
    if a.host != "127.0.0.1": print(f"listening on {a.host}:{a.port} for other devices (no login; trusted networks only)")
    if not a.no_browser:
        import webbrowser; webbrowser.open(url)
    try: httpd.serve_forever()
    except KeyboardInterrupt: pass
