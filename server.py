#!/usr/bin/env python3
"""rawnotes-triage — local website for reviewing ~/Projects/RawNotes.

Usage: python3 server.py [--port 8765] [--host 127.0.0.1] [--notes DIR (else the saved folder, else ~/Projects/RawNotes)] [--log triage.log] [--restart] [--app]

--host 0.0.0.0 listens on every interface (phone on home Wi-Fi / Tailscale).
There is no login: only do that on a network you trust. Firewall still applies.

If a server is already on the port, warns when its version differs from this
code; --restart stops it and starts this one.

--app opens the page in its own window (Omarchy web app) instead of a browser tab;
it is what the app-launcher entry from install-webapp.sh runs. --service is what the
systemd unit runs: no browser, and if the port is taken it exits with an error so
systemd tries again later. When the running server belongs to that unit, --restart
restarts the unit (systemctl) instead of stopping it.

Decisions (all move real files, nothing is deleted; the words match the CLI and CLAUDE.md):
  promote             -> copy note into ../experiments|active|learning|templates,
                         stamp promoted/due dates and "done means", move capture to done/;
                         a project folder gets the starter/skeletons files, git init and a first commit
  reference (durable) -> copy note into ../reference/<slug>.md (YAML frontmatter), move capture to done/
  kill (archive)      -> status: dead, reason in ## Outcome, move to done/
  keep                -> status open|cooking, optional next-step line
Also: batch (one decision for many notes), merge (many notes -> one), restore (done/ -> Inbox),
project status (mark shipped / push ship-by date), and undo of the last change made through this server.
"""
import argparse, base64, contextlib, errno, json, os, re, shlex, shutil, subprocess, sys, tempfile, threading, time, urllib.parse, datetime as dt
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import safefs
from notemeta import (field, set_field, drop_field, title_of, append_outcome, append_to_section, closed_date, question_of, question_summary, note_body,
                      eol_of, with_eol, to_lf, header_lines, title_index, HEAD_LINE)

VERSION = "2.12"  # bump on every change; history in CHANGELOG.md
HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(HERE, "triage.log")
FONT_RE = re.compile(r"^/static/fonts/[A-Za-z0-9-]+\.woff2$")
APP_PATH = "/triage"  # --app opens the page here: the path gives the window its own class, for a Hyprland rule
ICONS = {"/static/icons/icon-256.png": "icon-256.png", "/apple-touch-icon.png": "apple-touch-icon.png"}  # page favicon, phone home screen, app launcher
DEFAULT_NOTES = os.path.expanduser("~/Projects/RawNotes")
NOTES = DEFAULT_NOTES
NOTES_SOURCE = "default"  # where NOTES came from: "flag" (--notes), "config" (chosen in the first-run dialog) or "default"
PROJECTS = None
BIND_HOST = "127.0.0.1"  # set from --host; the Host/Origin guard depends on it
CLOSED = ("done", "shipped", "dead")  # a project file or folder with one of these statuses is finished
STARTED = dt.datetime.now().isoformat(timespec="seconds")
# the page is read once, so it always matches the API of the process serving it (restart to pick up edits)
with open(os.path.join(HERE, "index.html"), "rb") as _fh: INDEX_HTML = _fh.read().replace(b"__VERSION__", VERSION.encode())
DUE_DAYS = 7
MAX_EXTENSIONS = 2  # a ship-by date may be pushed this many times (config.json: max_extensions)
CLOSED_WINDOW_DAYS = 7  # the Projects board's Closed column shows projects closed this recently (config.json: closed_window_days)
CLOSED_READ = 200_000  # bytes of a closed project's notes read to find its Killed date
EXTEND_MAX_DAYS = 7
REASON_MIN = 10  # characters a reason for pushing the ship-by date has to have
ACTIVITY_TTL = 600  # seconds; GitHub contribution data barely changes minute to minute
ACTIVITY_REPO_DAYS = 30
LIMIT_DEFAULTS = {"stale_open_days": 14, "stale_cooking_days": 7, "inbox_limit_days": 30, "max_keeps": 3}  # config.json can override each
NOTE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-.+\.md$")
DESTS = {  # promote targets: folder -> (layout). "file" (a flat <slug>.md) is no longer used; old flat files still show
    "experiments": "folder", "active": "folder",
    "learning": "folder", "templates": "folder",
}

def today(): return dt.date.today()

_lock = safefs.RWLock()  # every change holds the write side; page reads share the read side, so a listing never sees half a change
_notes_cache = safefs.StatCache()  # a note's summary, until the file changes
_meta_cache = safefs.StatCache()   # due/status/title of project files found by inflight()
_proj_cache = safefs.StatCache()   # status and done-means of a project folder

def read_note(path):
    """(text, eol) of a note: undecodable bytes survive as surrogates, CRLF files come back with "\\n" and eol "\\r\\n"."""
    with open(path, "rb") as fh: raw = fh.read().decode("utf-8", "surrogateescape")
    return to_lf(raw), eol_of(raw)

def _within(path, root): return path == root or path.startswith(root.rstrip(os.sep) + os.sep)

def _summary_core(path, sub):
    fname = os.path.basename(path)
    with open(path, "rb") as fh: text = to_lf(fh.read().decode("utf-8", "replace"))
    return {
        "file": (sub + "/" if sub else "") + fname, "abs_path": path, "title": title_of(text, fname),
        "date": fname[:10], "status": field(text, "status") or "?", "type": field(text, "type") or "?",
        "question": question_summary(text), "size": len(text),
        "_kept": field(text, "kept"), "_restored": field(text, "restored"), "_keeps": field(text, "keeps"),
        **(fate_of(text) if sub == "done" else {}),
    }

def limits():
    """The four Inbox numbers: LIMIT_DEFAULTS, overridden by whole-number values >= 1 in config.json."""
    out = dict(LIMIT_DEFAULTS)
    try:
        with open(config_path(), encoding="utf-8") as fh: d = json.load(fh)
    except (OSError, ValueError): return out
    for k in out:
        v = d.get(k) if isinstance(d, dict) else None
        if isinstance(v, int) and not isinstance(v, bool) and v >= 1: out[k] = v
    return out

def _day(s):
    try: return dt.date.fromisoformat(s)
    except (TypeError, ValueError): return None

def inbox_state(date, status, kept="", restored="", keeps="", lim=None):
    """Where an Inbox note stands against the limits (all arguments are header text): keeps, stale, expired,
    expires_in, why. Stale runs from the last Keep (else the Restore date, else the capture date); the Inbox
    limit runs from the capture date, or the Restore date for a restored note."""
    lim = lim or limits(); t = today()
    start = max([d for d in (_day(date), _day(restored)) if d], default=None); k = _day(kept)
    try: n = max(0, int(keeps or 0))
    except ValueError: n = 0
    out = {"keeps": n, "stale": False, "expired": False, "expires_in": None, "why": ""}
    if start is None: return out
    if status in ("open", "cooking", "?"):
        since = (t - max(start, k or start)).days
        out["stale"] = since > lim["stale_cooking_days" if status == "cooking" else "stale_open_days"]
    out["expires_in"] = lim["inbox_limit_days"] - (t - start).days
    if out["expires_in"] < 0: out["expired"], out["why"] = True, "days"
    elif n >= lim["max_keeps"]: out["expired"], out["why"] = True, "keeps"
    return out

def expired_message(title, state, lim):
    if state["why"] == "keeps": return f"“{title}” has used all {lim['max_keeps']} Keeps: promote, file, merge or kill it"
    return f"“{title}” is over the {lim['inbox_limit_days']}-day limit: promote, file, merge or kill it"

def summarize(path, sub=""):
    core = _notes_cache.get(("note", path), [path], lambda _: _summary_core(path, sub))
    try: age = (today() - dt.date.fromisoformat(core["date"])).days
    except ValueError: age = None
    pub = {k: v for k, v in core.items() if not k.startswith("_")}
    if sub: return {**pub, "age": age, "stale": False}
    st = inbox_state(core["date"], core["status"], core["_kept"], core["_restored"], core["_keeps"])
    return {**pub, "age": age, "stale": st["stale"], "expired": st["expired"], "expires_in": st["expires_in"], "keeps": st["keeps"]}

def fate_of(text):
    """How a note in done/ ended up: promoted / reference / killed / merged, and where it went."""
    out = text.split("## Outcome", 1)[1] if "## Outcome" in text else ""
    dest = re.findall(r"->\s*`(?:\.\./)?([^`]+)`", out)
    dest = dest[-1] if dest else ""
    status = field(text, "status")
    if status == "merged" or "**Merged" in out: fate = "merged"
    elif status == "dead": fate = "killed"
    elif dest.startswith(("reference/", "maintained/")): fate = "reference"
    elif status == "promoted": fate = "promoted"
    else: fate = status or "?"
    return {"fate": fate, "dest": dest}

def _scan(d, sub):
    """Summaries of the note files in `d`. A file that vanished or can't be read since the listing (edited or moved
    by hand while we look) is skipped, not an error; a symlink pointing outside the notes folder is never read."""
    try: names = sorted(f for f in os.listdir(d) if NOTE_RE.match(f))
    except OSError: return []
    root = os.path.realpath(NOTES); out = []
    for f in names:
        p = os.path.join(d, f)
        try:
            if os.path.islink(p) and not _within(os.path.realpath(p), root): continue
            out.append(summarize(p, sub))
        except OSError: continue
    return out

def scan_notes():
    """Inbox and Archive summaries, read as one snapshot with respect to our own changes."""
    with _lock.read():
        return {"live": _scan(NOTES, ""), "done": _scan(os.path.join(NOTES, "done"), "done")[::-1]}

def inbox_summary(live=None):
    """The one-line Inbox count the weekly nudge prints: "N open, N cooking (N stale, N expired)"."""
    live = scan_notes()["live"] if live is None else live
    n = lambda s: sum(1 for x in live if x["status"] == s)
    return f"{n('open')} open, {n('cooking')} cooking ({sum(1 for x in live if x['stale'])} stale, {sum(1 for x in live if x['expired'])} expired)"

def list_notes(with_inflight=True):
    with _lock.read():
        out = scan_notes()
        return {**out, "inflight": inflight() if with_inflight else [], "limits": limits(), "today": today().isoformat(), "notes_dir": NOTES,
                "closed_window_days": tunable("closed_window_days", CLOSED_WINDOW_DAYS)}

def _count(v):
    """A header count (extensions: 2) as an int; missing or junk counts as 0."""
    return int(v) if v.isdigit() else 0

def _meta_of(path, f):
    """The part of a project file the board needs: kept until the file changes."""
    with open(path, "rb") as fh: raw = fh.read(CLOSED_READ)
    text = to_lf(raw[:4000].decode("utf-8", "replace"))
    status = field(text, "status") or "?"
    # a killed project's date can sit far down, in ## Outcome: only closed projects pay for reading more
    closed = closed_date(to_lf(raw.decode("utf-8", "replace")) if status in CLOSED else text)
    return {"title": title_of(text, f), "due": field(text, "due"), "promoted": field(text, "promoted"), "status": status,
            "extensions": _count(field(text, "extensions")), "closed": closed}

def in_closed_window(day):
    """Was `day` (YYYY-MM-DD) within the last closed_window_days days?"""
    try: return (today() - dt.date.fromisoformat(day)).days <= tunable("closed_window_days", CLOSED_WINDOW_DAYS)
    except ValueError: return False

def inflight():
    """Promoted notes with a due date (plus open-ended experiments), found in the sibling project folders."""
    out = []
    for dest in ("active", "experiments", "learning", "templates", "maintained", "archive"):
        d = os.path.join(PROJECTS, dest)
        if not os.path.isdir(d): continue
        for root, dirs, files in os.walk(d):
            if root[len(d):].count(os.sep) > 1: dirs[:] = []; continue
            for f in files:
                if not f.endswith(".md"): continue
                p = os.path.join(root, f)
                try: m = _meta_cache.get(("meta", p), [p], lambda _: _meta_of(p, f))
                except OSError: continue
                due = m["due"]; recently_closed = False
                if dest in ("maintained", "archive"):
                    # finished work that moved out of the board's folders: only the Closed column shows it, and only while recent
                    if m["closed"]:
                        if not in_closed_window(m["closed"]) or not (f.lower() == "notes.md" or root == d): continue
                        recently_closed = True
                    elif dest == "archive" or not due: continue
                # promoted with no ship-by: one per experiment (its NOTES.md, or a flat file), not every note it holds
                open_ended = (not due and dest == "experiments" and bool(m["promoted"])
                              and (f == "NOTES.md" or root == d))
                if not due and not open_ended and not recently_closed: continue
                try: left = (dt.date.fromisoformat(due) - today()).days if due else None
                except ValueError: left = None
                rel = os.path.relpath(p, PROJECTS)
                out.append({"path": rel, "title": m["title"], "project": rel.split(os.sep)[1] if rel.count(os.sep) >= 2 else "",
                            "dest": dest, "promoted": m["promoted"], "due": due,
                            "days_left": left, "open_ended": open_ended, "status": m["status"],
                            "extensions": m["extensions"], "max_extensions": tunable("max_extensions", MAX_EXTENSIONS),
                            "done": m["status"] in CLOSED or (dest in ("maintained", "archive") and bool(m["closed"])), "closed": m["closed"]})
    out.sort(key=lambda x: (x["done"], x["days_left"] if x["days_left"] is not None else 999))
    return out

DONE_FILES = ("notes.md", "project.md", "readme.md", "spec.md")  # where a project may say what done looks like
DONE_LINE = re.compile(r"^\s*(?:[-*]\s+)?(?:\*\*)?definition of done(?:\*\*)?\s*:?\s*(?:\*\*)?\s*(.*)$", re.I)
DONE_HEAD = re.compile(r"^#{1,4}\s*(?:definition of done|what does done look like|what done looks like|done looks like)\b[^\n]*$", re.I)
DONE_MAX = 600

def done_placeholder(text):
    return not text or bool(re.search(r"<fill in|^(tbd|todo|\?+|_+|\.+)$", text, re.I))

def find_done(path):
    """Locate the definition of done in a project folder.
    Returns None if nothing looks for one, else {file, start, end, kind ('line'|'head'), prefix, text, defined}:
    lines[start:end] of `file` hold it. The first real definition wins; failing that, the first blank/placeholder one."""
    files = {f.lower(): f for f in os.listdir(path)}
    blank = None
    for name in DONE_FILES:
        if name not in files: continue
        try: lines = _read_lines(os.path.join(path, files[name]))[0]
        except OSError: continue
        for i, line in enumerate(lines):
            m = DONE_LINE.match(line); h = DONE_HEAD.match(line)
            if not (m or h): continue
            parts = [m.group(1)] if m else []; end = i + 1
            for nxt in lines[i + 1:]:  # same paragraph / same section
                if m and (not nxt.strip() or re.match(r"^\s*([-*]\s|#)", nxt)): break
                if h and re.match(r"^#{1,4}\s", nxt): break
                if h and any(parts) and not nxt.strip(): break  # first paragraph only
                parts.append(nxt.strip()); end += 1
            text = re.sub(r"<!--.*?-->", "", " ".join(x for x in parts if x), flags=re.S).strip()
            text = re.sub(r"\s+", " ", text)[:DONE_MAX].encode("utf-8", "replace").decode("utf-8")
            hit = {"file": files[name], "start": i, "end": end, "kind": "line" if m else "head",
                   "prefix": line[:m.start(1)] if m else "", "text": text, "defined": not done_placeholder(text)}
            if hit["defined"]: return hit
            blank = blank or hit  # looked for it, only found a blank/placeholder
    return blank

def definition_of_done(path):
    """What 'done' looks like for a project, as written when it was promoted (or later).
    {"defined": bool, "text": str, "source": file or None}. Missing, empty and the '<fill in>'
    placeholder from the Promote form all count as not defined (source = where it was left blank)."""
    hit = find_done(path)
    if not hit: return {"defined": False, "text": "", "source": None}
    return {"defined": hit["defined"], "text": hit["text"] if hit["defined"] else "", "source": hit["file"]}

def _read_lines(target):
    """(lines, eol): the file split on any line ending, so find_done's line numbers and set_done's agree even in a
    file with mixed endings (those are written back with the dominant one: CRLF only if every line was CRLF).
    Undecodable bytes are kept as surrogates."""
    with open(target, "rb") as fh: raw = fh.read().decode("utf-8", "surrogateescape")
    return re.split(r"\r\n|\r|\n", raw), eol_of(raw)

def set_done(name, text, expected, kind=None):
    """Write a project's definition of done in place (or into NOTES.md's Plan if it has none), as one undoable change.
    `expected` is the text the user was looking at; if the file moved on since, refuse rather than overwrite."""
    path, kind = project_dir(name, kind, write=True)
    text = re.sub(r"\s+", " ", text or "").strip()
    if done_placeholder(text): raise ValueError("Write what done looks like first.")
    if len(text) > DONE_MAX: raise ValueError(f"Keep it under {DONE_MAX} characters.")
    with mutation(f"done means for {name}") as tx:
        hit = find_done(path)  # read under the write lock: a concurrent status edit can't be lost
        now = hit["text"] if hit and hit["defined"] else ""
        if re.sub(r"\s+", " ", expected or "").strip() != now:
            raise ValueError("This definition changed on disk since you opened it. Refresh and try again.")
        eol = "\n"
        if hit:
            target = os.path.join(path, hit["file"]); lines, eol = _read_lines(target)
            if hit["kind"] == "line":
                prefix = hit["prefix"] if hit["prefix"].rstrip().endswith(":") or hit["prefix"].rstrip().endswith(":**") else hit["prefix"].rstrip() + ": "
                lines[hit["start"]:hit["end"]] = [prefix + text]
            else:
                tail = [""] if hit["end"] < len(lines) and lines[hit["end"]].strip() else []
                lines[hit["start"] + 1:hit["end"]] = ["", text] + tail
        else:
            target = notes_file(path)
            item = "- definition of done: " + text
            if os.path.exists(target):
                lines, eol = _read_lines(target)
                plan = next((i for i, l in enumerate(lines) if re.match(r"^##\s+Plan\s*$", l)), None)
                if plan is None:
                    while lines and not lines[-1].strip(): lines.pop()
                    lines += ["", "## Plan", "", item, ""]
                else: lines.insert(plan + 2 if lines[plan + 1:plan + 2] == [""] else plan + 1, item)
            else: lines = [f"# {name}", "", "## Plan", "", item, ""]
        tx.write(target, eol.join(lines))
        tx.select = None
    _log(f"done {kind}/{name} -> {os.path.basename(target)}: {text[:80]}")
    return {"ok": True, "file": os.path.basename(target), "msg": f"Saved to {os.path.basename(target)}"}

PROJECT_KINDS = ("active", "experiments", "learning", "templates")  # folders whose subfolders are projects on the board
PROMOTED_ONLY = ("learning", "templates")  # here a folder is a project only if Promote made it (its NOTES.md says promoted:)

def notes_file(path):
    """The project's NOTES.md (whatever its case), or where a new one would go."""
    names = {f.lower(): f for f in os.listdir(path)}
    return os.path.join(path, names.get("notes.md", "NOTES.md"))

def project_facts(path):
    """(status in its NOTES.md, whether done means is defined) for a project folder, kept until those files change."""
    try: names = os.listdir(path)
    except OSError: return "", False
    files = sorted(os.path.join(path, n) for n in names if n.lower() in DONE_FILES or n.lower() == "notes.md")
    def build(_):
        status = ""
        try:
            with open(notes_file(path), "rb") as fh: status = field(to_lf(fh.read(4000).decode("utf-8", "replace")), "status")
        except OSError: pass
        return status, definition_of_done(path)["defined"]
    return _proj_cache.get(("proj", path), files, build)

def folder_status(path):
    """The `- status:` line in the project's NOTES.md, if it has one."""
    return project_facts(path)[0]

def is_worktree(path):
    """A linked git worktree (its `.git` is a file saying `gitdir: ...`): a branch checkout, not a project of its own."""
    try:
        with open(os.path.join(path, ".git"), "rb") as fh: return fh.read(8) == b"gitdir: "
    except OSError: return False  # no .git, or .git is a folder (a normal repo)

def on_board(path, kind):
    """Whether a folder under a project kind counts as a project (shown on the board, reachable through the API):
    never a linked worktree; in learning/ and templates/ only a folder whose NOTES.md has a `promoted:` line, so
    hand-made study folders and template kits stay off the board."""
    if is_worktree(path): return False
    if kind not in PROMOTED_ONLY: return True
    try:
        with open(notes_file(path), "rb") as fh: return bool(field(to_lf(fh.read(4000).decode("utf-8", "replace")), "promoted"))
    except OSError: return False

def folder_closed(path, status):
    """When a project folder was closed (see closed_date), "" if it is still open or says nothing; kept until its notes change."""
    if status not in CLOSED: return ""
    def build(_):
        try:
            with open(notes_file(path), "rb") as fh: return closed_date(to_lf(fh.read(CLOSED_READ).decode("utf-8", "replace")))
        except OSError: return ""
    try: return _proj_cache.get(("closed", path), [notes_file(path)], build)
    except OSError: return ""

def project_folders():
    """Every project folder under ../active and ../experiments, plus promoted ones under ../learning and ../templates;
    `kind` says which (experiments, learning and templates are labelled on the board).
    A symlinked folder is listed (read-only: `linked`) but Triage never writes into it."""
    out = []
    for kind in PROJECT_KINDS:
        base = os.path.join(PROJECTS, kind)
        if not os.path.isdir(base): continue
        for name in sorted(os.listdir(base), key=str.casefold):
            full = os.path.join(base, name)
            if name.startswith(".") or not os.path.isdir(full) or not on_board(full, kind): continue
            status, defined = project_facts(full)
            out.append({"name": name, "kind": kind, "path": f"{kind}/{name}", "abs_path": full,
                        "repo": os.path.exists(os.path.join(full, ".git")), "status": status, "done_defined": defined,
                        "closed": folder_closed(full, status),
                        "linked": os.path.islink(full) or os.path.islink(base)})
    return out

def project_dir(name, kind=None, write=False):
    """(absolute folder, kind) of a project on the board; kind defaults to active. Anything else is refused.
    write=True is for callers that are about to change something: a symlinked project (or a symlinked
    active/ or experiments/ folder above it) is refused, so no write can leave the projects folder."""
    kind = kind or "active"
    if kind not in PROJECT_KINDS or not isinstance(name, str) or not name or name.startswith(".") or "/" in name or os.sep in name or "\0" in name:
        raise ValueError("unknown project")
    path = os.path.join(PROJECTS, kind, name)
    if not os.path.isdir(path) or not on_board(path, kind): raise ValueError("unknown project")
    if write:
        try: safefs.ensure_inside([PROJECTS], path)
        except ValueError: raise ValueError(f"“{name}” is a symlink: Triage only changes real folders inside Projects.")
    return path, kind

GITHUB_REMOTE = re.compile(r"(?:https://(?:www\.)?github\.com/|git@(?:ssh\.)?github\.com:|ssh://git@(?:ssh\.)?github\.com(?::\d+)?/)([\w.-]+/[\w.-]+?)(?:\.git)?/?")

def github_repo_of(url):
    """'owner/name' for any way git can spell a GitHub remote (https, git@, ssh://, and ssh.github.com:443), else ''."""
    m = GITHUB_REMOTE.fullmatch((url or "").strip())
    if not m or any(not seg.strip(".") for seg in m[1].split("/")): return ""  # "..", "." are not names
    return m[1]

def git_out(path, args, timeout=5):
    try:
        proc = subprocess.run(["git"] + args, cwd=path, capture_output=True, text=True, timeout=timeout, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        return proc.stdout.strip() if proc.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired): return None

GIT_TIMEOUT = 30  # seconds for a git step that writes (init, add, commit: the commit runs your hooks, e.g. gitleaks)
NOREPLY_SUFFIX = "@users.noreply.github.com"
GIT_ENV_DROP = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR", "GIT_CEILING_DIRECTORIES")

def _git(path, args, timeout=GIT_TIMEOUT):
    """(returncode, stdout, stderr) of `git args` run in `path`. Never prompts; a GIT_DIR or similar inherited from
    the environment is dropped, so the command always acts on the repo in `path`."""
    env = {k: v for k, v in os.environ.items() if k not in GIT_ENV_DROP}
    proc = subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, timeout=timeout,
                          env={**env, "GIT_TERMINAL_PROMPT": "0"})
    return proc.returncode, proc.stdout.rstrip(), proc.stderr.strip()  # rstrip: porcelain lines start with a space

def _last_line(text): return (text.strip().splitlines() or ["no output"])[-1][:300]

def git_identity(path):
    """[author email, committer email] git would use for a commit in `path` (config and GIT_* overrides included);
    "" for one git can't work out."""
    out = []
    for var in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
        try: code, ident, _ = _git(path, ["var", var], timeout=5)
        except (OSError, subprocess.TimeoutExpired): code, ident = 1, ""
        m = re.search(r"<([^<>]*)>", ident) if not code else None
        out.append(m[1] if m else "")
    return out

def noreply_suffix():
    """The ending a first commit's email must have: `noreply_suffix` in config.json, else GitHub's noreply domain."""
    try:
        with open(config_path(), encoding="utf-8") as fh: d = json.load(fh)
        v = d.get("noreply_suffix") if isinstance(d, dict) else None
    except (OSError, ValueError): v = None
    return v if isinstance(v, str) and v.startswith("@") and len(v) > 2 else NOREPLY_SUFFIX

GIT_TTL = 20  # seconds a project's git facts are reused; Refresh on the Projects page asks for fresh ones
_git_cache = {}  # path -> (fingerprint, taken, state)
_git_lock = threading.Lock()
_git_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="git")  # shared and bounded: a listing never spawns its own pool

def _git_fingerprint(path):
    """Cheap stat of the files git rewrites when a repo gains a commit, a remote or a remote-tracking ref."""
    git = os.path.join(path, ".git"); out = []
    for rel in ("", "HEAD", "config", "packed-refs", "refs/heads", "refs/remotes/origin", "logs/HEAD"):
        try: st = os.stat(os.path.join(git, rel)); out.append((st.st_mtime_ns, st.st_size))
        except OSError: out.append(None)
    return tuple(out)

def github_state(path, fresh=False):
    """Is this project on GitHub? git: has a repo; repo: 'owner/name' of a GitHub origin; pushed: that remote has
    received something (a remote-tracking ref exists); unpushed: commits on this checkout that no origin ref contains.
    Cached for GIT_TTL seconds (and until the repo's own files change): three git processes per project add up."""
    if not os.path.exists(os.path.join(path, ".git")): return {"git": False, "repo": "", "pushed": False, "unpushed": 0}
    fp = _git_fingerprint(path)
    with _git_lock: hit = _git_cache.get(path)
    if hit and not fresh and hit[0] == fp and time.time() - hit[1] < GIT_TTL: return dict(hit[2])
    repo = github_repo_of(git_out(path, ["remote", "get-url", "origin"]))
    pushed = bool(repo and git_out(path, ["for-each-ref", "--count=1", "refs/remotes/origin"]))
    ahead = git_out(path, ["rev-list", "--count", "HEAD", "--not", "--remotes=origin"]) if pushed else None
    state = {"git": True, "repo": repo, "pushed": pushed, "unpushed": int(ahead) if ahead and ahead.isdigit() else 0}
    with _git_lock:
        if len(_git_cache) > 500: _git_cache.clear()
        _git_cache[path] = (fp, time.time(), state)
    return dict(state)

def projects_listing(fresh=False):
    projects = project_folders()
    for p, state in zip(projects, _git_pool.map(lambda p: github_state(p["abs_path"], fresh), projects)): p["gh"] = state
    return projects

def project_detail(name, kind=None):
    path, kind = project_dir(name, kind)
    result = {"name": name, "kind": kind, "path": f"{kind}/{name}", "abs_path": path, "summary": "No README or project notes yet.",
              "summary_source": None, "commits": [], "features": [], "prs": [],
              "pr_status": "No Git repository in this project.", "branch": "", "remote": "", "repo": "", "gh": {"git": False, "repo": "", "pushed": False, "unpushed": 0},
              "linked": os.path.islink(path), "done": definition_of_done(path)}
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
                                  env={**(gh_env() if args[0] == "gh" else os.environ), "GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1"})
            return proc.stdout.strip() if proc.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired): return None
    result["gh"] = github_state(path)
    result["branch"] = run(["git", "branch", "--show-current"]) or "Detached HEAD / no commits"
    remote = run(["git", "remote", "get-url", "origin"]) or ""
    match = github_repo_of(remote)
    if match: result["remote"] = "https://github.com/" + match; result["repo"] = match
    history = run(["git", "log", "-100", "--format=%h%x09%cs%x09%s"])
    commits = []
    for line in (history or "").splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3: commits.append(dict(zip(("sha", "date", "title"), parts)))
    result["commits"] = commits[:10]
    result["features"] = [c for c in commits if re.match(r"^feat(?:ure)?(?:\([^)]*\))?!?:", c["title"], re.I)][:10]
    result["pr_status"] = "PRs require a GitHub origin remote."
    if match:
        prs = run(["gh", "pr", "list", "--repo", match, "--state", "all", "--limit", "10",
                   "--json", "number,title,state,url,updatedAt,mergedAt", "--search", "sort:updated-desc"], timeout=12)
        result["pr_status"] = "GitHub PRs unavailable. Check gh installation, sign-in, and network access."
        if prs is not None:
            try:
                result["prs"] = json.loads(prs)
                result["pr_status"] = "" if result["prs"] else "No pull requests found."
            except ValueError: pass
    return result

# The graph is built from commits on the default branch of repos you own or collaborate on (by commit date),
# not from GitHub's profile calendar (which hides private repos unless a profile setting is on) and not from
# push events: a commit counts on the day it was committed, however late it was pushed.
REPOS_QUERY = """{ viewer { id login repositories(first: 100__AFTER__, ownerAffiliations: [OWNER, COLLABORATOR], orderBy: {field: PUSHED_AT, direction: DESC}) {
  pageInfo { hasNextPage endCursor } nodes { id nameWithOwner isPrivate url pushedAt } } } }"""
REPO_PAGES = 10  # x100 repos; beyond that the graph says so instead of silently stopping
HISTORY_PAGES = 20  # x100 commits per repo for the year; beyond that the repo is named as truncated
HISTORY_FIELDS = "id defaultBranchRef { target { ... on Commit { history(first: 100, since: %s, author: {id: %s}%s) { pageInfo { hasNextPage endCursor } nodes { committedDate } } } } }"
_activity = {"at": 0.0, "data": None, "busy": None, "last": None}
_activity_lock = threading.Lock()  # guards the dict only: never held while waiting for GitHub

# ---- the GitHub sign-in: gh's own login, or a personal access token saved here and handed to gh as GH_TOKEN ----
CONFIG_DIR = os.environ.get("RAWTRIAGE_CONFIG_DIR") or os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "rawnotes-triage")
TOKEN_RE = re.compile(r"[A-Za-z0-9_]{20,255}")  # classic (ghp_...), fine-grained (github_pat_...) and older 40-hex tokens
STATUS_TTL = 60
_gh_status = {"at": 0.0, "data": None}
_gh_status_lock = threading.Lock()

def token_path(): return os.path.join(CONFIG_DIR, "github-token")

def saved_token():
    """The token saved through the page, or '' (none saved, or unreadable)."""
    try:
        with open(token_path(), encoding="utf-8") as fh: return fh.read().strip()
    except (OSError, ValueError): return ""  # ValueError: not UTF-8

def gh_env(token=None):
    """The environment every gh process gets: never prompts, and signed in as the saved token if there is one
    (GH_TOKEN, never on the command line)."""
    token = token or saved_token()
    return {**os.environ, "GH_PROMPT_DISABLED": "1", **({"GH_TOKEN": token} if token else {})}

def _gh(args, timeout=25, token=None):
    """Run the gh CLI and return stdout. Signed in as the token saved through the page if there is one (given to gh in
    its environment, never on the command line), otherwise as gh itself. Read-only use: callers only issue GETs and queries."""
    try:
        proc = subprocess.run(["gh"] + args, capture_output=True, text=True, timeout=timeout, env=gh_env(token))
    except FileNotFoundError: raise RuntimeError("gh is not installed")
    except subprocess.TimeoutExpired: raise RuntimeError("GitHub took too long to answer")
    if proc.returncode != 0:
        err = proc.stderr.strip() + proc.stdout.strip()
        if "HTTP 404" in err: raise RuntimeError("Not found, or not visible to your GitHub sign-in.")
        raise RuntimeError((err.splitlines() or ["gh failed; check `gh auth status` and the network"])[-1])
    return proc.stdout

def forget_github():
    """The sign-in changed: whatever was fetched as the old account is wrong."""
    with _gh_status_lock: _gh_status.update(at=0.0, data=None)
    with _activity_lock: _activity.update(at=0.0, data=None, last=None)
    with _rest_lock: _rest_cache.clear()
    _viewer["login"] = None

def github_status(refresh=False):
    """Who GitHub features act as: {state: signed_in | signed_out | no_gh | unavailable, login, source: token | environment | gh, error}.
    The token itself is never returned."""
    with _gh_status_lock:
        if _gh_status["data"] and not refresh and time.time() - _gh_status["at"] < STATUS_TTL: return _gh_status["data"]
    source = "token" if saved_token() else "environment" if (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")) else "gh"
    out = {"state": "signed_in", "login": "", "source": source}
    if not shutil.which("gh"): out.update(state="no_gh", source="token" if source == "token" else "", error="The GitHub CLI (gh) is not installed.")
    else:
        try:
            out["login"] = json.loads(_gh(["api", "user"], timeout=15))["login"]; _viewer["login"] = out["login"]
        except (ValueError, KeyError, TypeError): out.update(state="unavailable", error="unexpected answer from GitHub")
        except RuntimeError as e:
            msg = str(e); low = msg.lower()
            bad = any(k in low for k in ("gh auth login", "not logged", "http 401", "bad credentials", "authentication", "gh_token"))
            out.update(state="signed_out" if bad else "unavailable", error="" if bad and source == "gh" else msg)
            if bad and source == "token": out["error"] = "GitHub rejected the saved token."
    with _gh_status_lock: _gh_status.update(at=time.time(), data=out)
    return out

def set_github_token(req):
    """Save a personal access token: checked against GitHub first, kept in a file only you can read."""
    token = req.get("token")
    if not isinstance(token, str) or not TOKEN_RE.fullmatch(token.strip()): raise ValueError("That does not look like a GitHub token (letters, digits and underscores, 20+ characters).")
    token = token.strip()
    if not shutil.which("gh"): raise ValueError("The GitHub CLI (gh) is not installed; install it first.")
    try: login = json.loads(_gh(["api", "user"], timeout=15, token=token))["login"]
    except (ValueError, KeyError, TypeError): raise ValueError("unexpected answer from GitHub")
    except RuntimeError as e:
        raise ValueError("GitHub rejected that token." if any(k in str(e).lower() for k in ("401", "bad credentials", "gh_token")) else str(e))
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True); os.chmod(CONFIG_DIR, 0o700)  # makedirs leaves an existing folder as it was
    fd, tmp = tempfile.mkstemp(dir=CONFIG_DIR, prefix=".github-token.")  # 0600, its own name: concurrent saves don't collide
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh: fh.write(token + "\n")
        os.replace(tmp, token_path())
    except BaseException:
        with contextlib.suppress(OSError): os.unlink(tmp)
        raise
    forget_github(); _log(f"github token saved ({login})")
    return github_status(True)

def remove_github_token():
    """Forget the saved token; GitHub features go back to gh's own sign-in."""
    with contextlib.suppress(FileNotFoundError): os.unlink(token_path())
    forget_github(); _log("github token removed")
    return github_status(True)

# ---- first run: the notes folder. Chosen in the page when it doesn't exist yet, then remembered in config.json ----
TEMPLATE = os.path.join(HERE, "starter", "_template.md")  # copied into a new notes folder

def config_path(): return os.path.join(CONFIG_DIR, "config.json")

def saved_notes_dir():
    """The notes folder chosen in the first-run dialog, or None (nothing saved, or the file is unreadable)."""
    try:
        with open(config_path(), encoding="utf-8") as fh: d = json.load(fh)
    except (OSError, ValueError): return None
    p = d.get("notes") if isinstance(d, dict) else None
    return p if isinstance(p, str) and os.path.isabs(p) else None

def tunable(key, default):
    """A whole-number setting from config.json (`default` if missing, unreadable or not a positive/zero integer)."""
    try:
        with open(config_path(), encoding="utf-8") as fh: v = json.load(fh).get(key)
    except (OSError, ValueError, AttributeError): return default
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else default

def save_config(**changes):
    """Merge `changes` into config.json (folder 0700, written through a private temp file and renamed into place)."""
    try:
        with open(config_path(), encoding="utf-8") as fh: d = json.load(fh)
        if not isinstance(d, dict): d = {}
    except (OSError, ValueError): d = {}
    d.update(changes)
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True); os.chmod(CONFIG_DIR, 0o700)
    fd, tmp = tempfile.mkstemp(dir=CONFIG_DIR, prefix=".config.json.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh: json.dump(d, fh, indent=2); fh.write("\n")
        os.replace(tmp, config_path())
    except BaseException:
        with contextlib.suppress(OSError): os.unlink(tmp)
        raise

def setup_state():
    """Whether the page must ask where the notes live: only while the notes folder doesn't exist."""
    return {"needed": not os.path.isdir(NOTES), "notes_dir": NOTES, "projects_dir": PROJECTS, "source": NOTES_SOURCE,
            "default": DEFAULT_NOTES}

def _setup_target(raw):
    """The folder the user typed, checked: absolute (~ allowed), at least two levels deep (its parent becomes the
    projects root, so never /), and not an existing file."""
    if not isinstance(raw, str) or not raw.strip(): raise ValueError("Type the folder where your notes should live.")
    raw = raw.strip()
    if "\0" in raw or len(raw) > 4096: raise ValueError("That is not a usable folder path.")
    p = os.path.expanduser(raw)
    if not os.path.isabs(p): raise ValueError("Use a full path, for example ~/Projects/RawNotes (or C:\\Users\\you\\RawNotes on Windows).")
    p = os.path.normpath(p); parent = os.path.dirname(p)
    if parent == p or os.path.dirname(parent) == parent: raise ValueError("Pick a folder inside another folder (for example ~/Projects/RawNotes): "
                                                       "projects are created next to the notes folder, so it can't sit at the top of the disk.")
    if os.path.exists(p) and not os.path.isdir(p): raise ValueError(f"{p} is a file, not a folder.")
    return p

def setup_notes(req):
    """First run: create (or adopt) the notes folder, with done/ and _template.md, remember it, and switch to it."""
    global NOTES, PROJECTS
    with _lock.write():
        if os.path.isdir(NOTES): raise ValueError(f"The notes folder is already set up: {NOTES}")
        p = _setup_target(req.get("path"))
        existed = os.path.isdir(p)
        try:
            os.makedirs(os.path.join(p, "done"), exist_ok=True)
            t = os.path.join(p, "_template.md")
            if not os.path.exists(t): shutil.copyfile(TEMPLATE, t)
        except OSError as e: raise ValueError(f"Couldn't create {p}: {e.strerror or e}")
        try: save_config(notes=p)
        except OSError as e: raise ValueError(f"Created {p}, but couldn't save the choice in {config_path()}: {e.strerror or e}")
        NOTES, PROJECTS = p, os.path.dirname(p)
        for c in (_notes_cache, _meta_cache, _proj_cache): c.clear()
    _log(f"notes folder set up: {p}" + (" (existing folder)" if existed else ""))
    return {**setup_state(), "existed": existed}

def gh_graphql(query):
    """Run a GraphQL query through gh. Returns the parsed 'data'."""
    try: return json.loads(_gh(["api", "graphql", "-f", "query=" + query]))["data"]
    except (ValueError, KeyError): raise RuntimeError("unexpected answer from GitHub")

def commit_days(repo_ids, uid, since, max_pages=None, truncated=None):
    """{repo id: [local dates of your commits since `since`]} for each repo's default branch.
    Repos still having more history after `max_pages` pages are added to `truncated` (a set), not silently cut."""
    out = {i: [] for i in repo_ids}; pending = {i: "" for i in repo_ids}
    for _ in range(max_pages or HISTORY_PAGES):
        if not pending: break
        nodes = "".join(f"r{n}: node(id: {json.dumps(i)}) {{ ... on Repository {{ {HISTORY_FIELDS % (json.dumps(since), json.dumps(uid), ', after: ' + json.dumps(c) if c else '')} }} }} "
                        for n, (i, c) in enumerate(pending.items()))
        data = gh_graphql("{ " + nodes + "}"); ids = list(pending); pending = {}
        for n, i in enumerate(ids):
            repo = data.get(f"r{n}") or {}
            hist = (((repo.get("defaultBranchRef") or {}).get("target") or {}).get("history")) or {}
            for c in hist.get("nodes", []):
                out[i].append(dt.datetime.fromisoformat(c["committedDate"].replace("Z", "+00:00")).astimezone().date())
            if hist.get("pageInfo", {}).get("hasNextPage"): pending[i] = hist["pageInfo"]["endCursor"]
    if truncated is not None: truncated.update(pending)
    return out

def current_streak(days):
    """Consecutive days with a push, ending today (or yesterday: today may not have started yet)."""
    counts = [d["count"] for d in days]
    if counts and counts[-1] == 0: counts = counts[:-1]
    n = 0
    for c in reversed(counts):
        if c == 0: break
        n += 1
    return n

def build_activity(login, repos, by_repo, today_, truncated=None):
    """Calendar weeks (Sunday first, 53 of them like GitHub) plus last-30-day repo counts from commit dates.
    `truncated`: {"repos": bool, "history": [repo names]} when GitHub had more than we asked for."""
    start = today_ - dt.timedelta(days=(today_.weekday() + 1) % 7 + 52 * 7)
    per_day = {}; recent = {}; cutoff = today_ - dt.timedelta(days=ACTIVITY_REPO_DAYS)
    for r in repos:
        for d in by_repo.get(r["id"], []):
            if start <= d <= today_: per_day[d] = per_day.get(d, 0) + 1
            if d > cutoff: recent[r["id"]] = recent.get(r["id"], 0) + 1
    top = max(per_day.values(), default=0)
    level = lambda n: 0 if not n else min(4, -(-4 * n // top))  # quartiles of your busiest day, like GitHub
    days = [start + dt.timedelta(days=i) for i in range((today_ - start).days + 1)]
    weeks = [[{"date": d.isoformat(), "count": per_day.get(d, 0), "level": level(per_day.get(d, 0))} for d in days[i:i + 7]]
             for i in range(0, len(days), 7)]
    flat = [d for w in weeks for d in w]
    chips = sorted(({"name": r["nameWithOwner"], "url": r["url"], "private": r["isPrivate"], "count": recent[r["id"]]}
                    for r in repos if r["id"] in recent), key=lambda r: -r["count"])
    truncated = truncated or {"repos": False, "history": []}
    notice = []
    if truncated["repos"]: notice.append(f"Only the {REPO_PAGES * 100} most recently pushed repositories were checked.")
    if truncated["history"]: notice.append("Commit history was cut short for " + ", ".join(truncated["history"][:5])
                                           + (f" and {len(truncated['history']) - 5} more" if len(truncated["history"]) > 5 else "") + ".")
    return {"login": login, "total": sum(per_day.values()), "weeks": weeks,
            "active_days": sum(1 for d in flat if d["count"]), "streak": current_streak(flat),
            "repos": chips, "repo_days": ACTIVITY_REPO_DAYS, "basis": "commits on the default branch, by commit date",
            "truncated": truncated, "notice": " ".join(notice),
            "fetched": dt.datetime.now().isoformat(timespec="seconds")}

def fetch_activity():
    today_ = today(); year_ago = (today_ - dt.timedelta(days=366)).isoformat()
    repos, cursor, viewer, more = [], "", None, False
    for _ in range(REPO_PAGES):
        v = gh_graphql(REPOS_QUERY.replace("__AFTER__", f", after: {json.dumps(cursor)}" if cursor else ""))["viewer"]
        viewer = viewer or v; page = v["repositories"]; info = page.get("pageInfo") or {}
        repos += [r for r in page["nodes"] if r["pushedAt"][:10] >= year_ago]  # never pushed this year = no commits to count
        # newest-pushed first: once a repo is older than a year, every later page is too
        more = bool(info.get("hasNextPage")) and all(r["pushedAt"][:10] >= year_ago for r in page["nodes"][-1:])
        cursor = info.get("endCursor") or ""
        if not more or not cursor: break
    since = dt.datetime.combine(today_ - dt.timedelta(days=(today_.weekday() + 1) % 7 + 52 * 7), dt.time.min).astimezone().astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cut = set()
    by_repo = commit_days([r["id"] for r in repos], viewer["id"], since, truncated=cut) if repos else {}
    names = {r["id"]: r["nameWithOwner"] for r in repos}
    return build_activity(viewer["login"], repos, by_repo, today_,
                          {"repos": bool(more), "history": sorted(names[i] for i in cut)})

def github_activity(refresh=False):
    """Your commit graph, cached. Failures come back as {'error': ...} (with the last good data, marked stale,
    when there is some) so the page can still render. One caller at a time talks to GitHub, and none of them
    holds a lock while it waits: the others wait for that answer instead of asking again."""
    with _activity_lock:
        fresh = _activity["data"] and time.time() - _activity["at"] < ACTIVITY_TTL
        if fresh and not refresh: return _activity["data"]
        busy = _activity.get("busy")
        if busy is None: _activity["busy"] = mine = threading.Event()
    if busy is not None:  # someone else is already asking GitHub
        busy.wait(60)
        with _activity_lock: return _activity.get("last") or {"error": "GitHub took too long to answer"}
    try:
        data = fetch_activity()
        with _activity_lock: _activity.update(data=data, at=time.time(), last=data)
        return data
    except RuntimeError as e:
        with _activity_lock:
            out = {**_activity["data"], "stale": True, "error": str(e)} if _activity["data"] else {"error": str(e)}
            _activity["last"] = out
        return out
    finally:
        with _activity_lock: _activity["busy"] = None
        mine.set()

# ---- read-only repo viewer: lets a private repo be reviewed inside the page, through gh, with no browser login ----
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
REPO_TTL = 60
FILE_MAX = 300000
_viewer = {"login": None}
_rest_cache = {}
_rest_lock = threading.Lock()

def gh_rest(path):
    """GET a GitHub REST path through gh. Cached for a minute so browsing around the repo is quick."""
    with _rest_lock:
        hit = _rest_cache.get(path)
        if hit and time.time() - hit[0] < REPO_TTL: return hit[1]
    try: data = json.loads(_gh(["api", path], timeout=20))
    except ValueError: raise RuntimeError("unexpected answer from GitHub")
    with _rest_lock:
        if len(_rest_cache) > 300: _rest_cache.clear()
        _rest_cache[path] = (time.time(), data)
    return data

def check_repo(name):
    """Only repos owned by the signed-in gh user: the page has no login of its own, so keep it to your own work."""
    if not REPO_RE.match(name or "") or any(not part.strip(".") for part in name.split("/")): raise ValueError("bad repository name")
    if _viewer["login"] is None: _viewer["login"] = json.loads(_gh(["api", "user"]))["login"]
    if name.split("/")[0].lower() != _viewer["login"].lower(): raise ValueError("Only your own repositories can be viewed here.")

def repo_path_ok(path):
    path = (path or "").strip("/")
    if not path: return ""
    if len(path) > 300 or "\\" in path or "\0" in path or any(seg in ("", ".", "..") for seg in path.split("/")):
        raise ValueError("bad path")
    return path

def _entries(items):
    out = [{"name": e["name"], "path": e["path"], "type": "dir" if e["type"] == "dir" else "file", "size": e.get("size", 0)} for e in items]
    return sorted(out, key=lambda e: (e["type"] != "dir", e["name"].casefold()))

def _b64text(d):
    try: return base64.b64decode(d.get("content") or "").decode("utf-8")
    except UnicodeDecodeError: return None

def repo_overview(name):
    check_repo(name); base = "repos/" + name
    calls = {"repo": base, "tree": base + "/contents", "readme": base + "/readme", "commits": base + "/commits?per_page=15",
             "prs": base + "/pulls?state=all&per_page=10&sort=updated&direction=desc", "issues": base + "/issues?state=open&per_page=15"}
    with ThreadPoolExecutor(6) as ex: futs = {k: ex.submit(gh_rest, v) for k, v in calls.items()}
    res, errs = {}, {}
    for k, f in futs.items():
        try: res[k] = f.result()
        except RuntimeError as e: errs[k] = str(e)
    if "repo" in errs: raise RuntimeError(errs["repo"])
    d = res["repo"]; readme = res.get("readme")
    text = _b64text(readme)[:200000] if readme and _b64text(readme) is not None else None
    return {
        "repo": {"name": d["full_name"], "private": d["private"], "description": d.get("description") or "", "url": d["html_url"],
                 "default_branch": d["default_branch"], "pushed_at": (d.get("pushed_at") or "")[:10], "archived": d.get("archived", False)},
        "tree": _entries(res.get("tree") or []),
        "readme": {"name": readme["name"], "text": text} if text is not None else None,
        "commits": [{"sha": c["sha"][:7], "date": c["commit"]["author"]["date"][:10], "title": c["commit"]["message"].split("\n")[0],
                     "author": (c.get("author") or {}).get("login") or c["commit"]["author"]["name"]} for c in res.get("commits") or []],
        "prs": [{"number": x["number"], "title": x["title"], "state": "merged" if x.get("merged_at") else x["state"],
                 "updated": x["updated_at"][:10], "author": (x.get("user") or {}).get("login", "")} for x in res.get("prs") or []],
        "issues": [{"number": x["number"], "title": x["title"], "updated": x["updated_at"][:10], "comments": x.get("comments", 0),
                    "author": (x.get("user") or {}).get("login", "")} for x in res.get("issues") or [] if "pull_request" not in x],
        "errors": {k: v for k, v in errs.items() if k != "readme"},  # a missing README is normal; the rest is worth showing
    }

def repo_path(name, path):
    check_repo(name); path = repo_path_ok(path)
    d = gh_rest(f"repos/{name}/contents/{urllib.parse.quote(path, safe='/')}")
    if isinstance(d, list): return {"kind": "dir", "path": path, "entries": _entries(d)}
    out = {"kind": "file", "path": path, "name": d["name"], "size": d.get("size", 0), "url": d.get("html_url", "")}
    if d.get("type") != "file": return {**out, "kind": "other", "type": d.get("type")}
    if out["size"] > FILE_MAX or not d.get("content"): return {**out, "too_large": True}
    text = _b64text(d)
    return {**out, "binary": True} if text is None else {**out, "text": text}


def reference_copy(text, fname, why=""):
    """Turn the '- key: value' header into YAML frontmatter, so Obsidian shows it as Properties."""
    lines = text.split("\n"); head = header_lines(lines); meta = {}
    for i in head:
        m = HEAD_LINE.match(lines[i])
        if m.group(1) in ("date", "status", "type", "promoted", "due") and m.group(3).strip() and not m.group(3).startswith("<"):
            meta[m.group(1)] = m.group(3).split()[0]
    t = title_index(lines)
    drop = {i for i in head if HEAD_LINE.match(lines[i]).group(1) in ("date", "status", "type", "promoted", "due", "kept", "keeps", "restored")} | ({t} if t is not None else set())
    rest = [l for i, l in enumerate(lines) if i not in drop]
    while rest and not rest[0].strip(): rest.pop(0)
    fm = ["---", f"created: {meta.get('date', '')}", f"promoted: {meta.get('promoted', '')}",
          "status: promoted", f"type: {meta.get('type', 'research')}", f"source: RawNotes/done/{fname}"]
    if why: fm.append("why: " + json.dumps(why))
    fm += ["tags:", "  - reference", "---", ""]
    return "\n".join(fm + [lines[t] if t is not None else "# " + title_of(text, fname), ""] + rest)

def safe_note(rel, write=False):
    """Resolve a note path from the API and refuse anything outside NOTES.
    Reading follows a symlink only to somewhere inside NOTES. write=True (a note about to be changed or moved)
    refuses any symlink on the way, so a change can never land outside the notes folder."""
    if not isinstance(rel, str) or not rel or "\0" in rel: raise ValueError("bad path")
    root = os.path.normpath(NOTES); p = os.path.normpath(os.path.join(root, rel))
    if write:
        if not p.endswith(".md") or not p.startswith(root + os.sep): raise ValueError("bad path")
        safefs.ensure_inside([NOTES], p)
    else:
        p = os.path.realpath(p)
        if not p.startswith(os.path.realpath(NOTES) + os.sep) or not p.endswith(".md"): raise ValueError("bad path")
    if not os.path.isfile(p): raise ValueError("no such note")
    return p

def slug_of(fname): return re.sub(r"^\d{4}-\d{2}-\d{2}-", "", fname)[:-3]

def unique(path):
    base, ext = os.path.splitext(path); n = 2
    while os.path.lexists(path):
        path = f"{base}-{n}{ext}"; n += 1
    return path

# ---- every change goes through mutation(): applied step by step, rolled back if a step fails, undoable after ----
UNDO_MAX = 20
_undo = []  # newest last: {"label", "ops", "select"}
MISSING = object()

class MutationFailed(RuntimeError):
    """A change failed part-way. The message says what state the files were left in."""

def _read(path):
    with open(path, encoding="utf-8", errors="surrogateescape", newline="") as fh: return fh.read()

def _stored(path): return safefs.ensure_inside([NOTES, PROJECTS], path)

class Tx:
    """One change to the files, applied step by step. Each step that succeeds is recorded, so the whole change can be
    rolled back if a later step fails, or undone later. Every path must be a real (non-symlink) path inside the notes
    folder or the projects root; a write replaces the file atomically and keeps its permissions."""
    def __init__(self, label=""): self.label, self.ops, self.select, self.logs = label, [], None, []
    def log(self, msg): self.logs.append(msg)  # written to triage.log only once the whole change has gone through
    def write(self, path, text, eol="\n"):
        path = _stored(path)
        try:
            with open(path, "rb") as fh: before = fh.read()
            mode = os.stat(path).st_mode & 0o7777
        except FileNotFoundError: before = mode = None
        data = with_eol(text, eol).encode("utf-8", "surrogateescape")
        safefs.atomic_write(path, data, mode)
        self.ops.append(("write", path, before, data, mode))
    def move(self, src, dst):
        src, dst = _stored(src), _stored(dst)
        if os.path.lexists(dst): raise FileExistsError(f"{os.path.basename(dst)} already exists")
        try: os.rename(src, dst)
        except OSError as e:
            if e.errno != errno.EXDEV: raise
            shutil.move(src, dst)
        self.ops.append(("move", src, dst))
    def mkdir(self, path):
        path = _stored(path); os.mkdir(path); self.ops.append(("mkdir", path))
    def ensure_dir(self, path):
        """Create `path` and any missing parents, each one recorded (so undo removes the empty ones it made)."""
        path = _stored(path); missing = []
        while not os.path.isdir(path): missing.append(path); path = os.path.dirname(path)
        for d in reversed(missing): self.mkdir(d)
    def gitinit(self, path, message):
        """`git init -b main` in `path` (a folder this change made), then one commit of everything in it, but only
        if the identity git will use is a GitHub noreply address; the app never sets an identity. Recorded as
        ("gitinit", path, sha or None): undo and rollback remove the `.git` only while it still holds just that.
        Returns "" when the commit was made, else a warning for the page (no git, wrong identity, commit refused)."""
        path = _stored(path); gitdir = os.path.join(path, ".git")
        if os.path.lexists(gitdir): raise FileExistsError(f"{os.path.basename(path)}/.git already exists")
        if not shutil.which("git"): return "git is not installed, so the folder has no repo yet."
        try: code, _, err = _git(path, ["init", "-q", "-b", "main"])
        except (OSError, subprocess.TimeoutExpired) as e: code, err = 1, str(e)
        if os.path.isdir(gitdir) and not os.path.islink(gitdir): self.ops.append(("gitinit", path, None))
        if code: raise OSError(f"git init failed: {_last_line(err)}")
        i = len(self.ops) - 1; name = os.path.basename(path)
        how = f"commit by hand: git -C {shlex.quote(path)} add -A && git -C {shlex.quote(path)} commit -m {shlex.quote(message)}"
        suffix = noreply_suffix(); bad = [e or "(not set)" for e in git_identity(path) if not (e or "").endswith(suffix)]
        if bad: return f"{name}: git repo made, but no commit: the email git would use is {bad[0]}, not a {suffix} address. Set it in git yourself, then {how}"
        try:
            code, _, err = _git(path, ["add", "-A"])
            if not code: code, _, err = _git(path, ["commit", "-q", "-m", message])
        except (OSError, subprocess.TimeoutExpired) as e: code, err = 1, str(e)
        if code: return f"{name}: git repo made, but the first commit failed ({_last_line(err)}). The files are staged; {how}"
        sha = _git(path, ["rev-parse", "HEAD"])[1]
        self.ops[i] = ("gitinit", path, sha)
        return ""
    def rollback(self):
        """Reverse every step, newest first. Returns the steps that could not be reversed (usually none)."""
        left = _revert(self.ops); self.ops = left; return left
    def commit(self):
        if self.ops: _push_undo({"label": self.label, "ops": self.ops, "select": self.select})

def _push_undo(entry):
    _undo.append(entry); del _undo[:-UNDO_MAX]

def _changed(path): return f"Can't undo: {os.path.basename(path)} changed since. Fix it by hand."

def _recheck_contained(op):
    """Every path a recorded step touches must still be a real path inside the notes/projects folders, now: a folder
    renamed away and replaced by a symlink (or the file itself swapped for one) since the step ran makes undo,
    rollback and redo refuse rather than write through the link. Raises ValueError, changing nothing."""
    for p in ((op[1], op[2]) if op[0] == "move" else (op[1],)):
        try: _stored(p)
        except ValueError as e: raise ValueError(f"Can't undo: {e}. Nothing was changed; fix {os.path.basename(p)} by hand.") from e

def _plan_inverse(ops, vfs=None):
    """Dry run of reversing `ops` newest first, against the files as they are now: raises ValueError (changing
    nothing) if any file a step touched has been edited, moved or added to since, or no longer lies safely inside the
    notes and projects folders."""
    vfs = {} if vfs is None else vfs
    for op in ops: _recheck_contained(op)
    def cur(p):
        if p in vfs: return vfs[p]
        if os.path.isfile(p):
            with open(p, "rb") as fh: return fh.read()
        return MISSING
    for op in reversed(ops):
        if op[0] == "write":
            _, path, before, after, _mode = op
            if cur(path) != after: raise ValueError(_changed(path))
            vfs[path] = MISSING if before is None else before
        elif op[0] == "move":
            _, src, dst = op
            if cur(dst) is MISSING or cur(src) is not MISSING: raise ValueError(_changed(dst))
            vfs[src] = cur(dst); vfs[dst] = MISSING
        elif op[0] == "mkdir":
            path = op[1]
            if not os.path.isdir(path): raise ValueError(_changed(path + "/"))
            if [f for f in os.listdir(path) if vfs.get(os.path.join(path, f)) is not MISSING]: raise ValueError(_changed(path + "/"))
            vfs[path] = MISSING
        elif op[0] == "gitinit":
            _check_gitinit(op[1], op[2])
            vfs[os.path.join(op[1], ".git")] = MISSING
    return vfs

def _check_gitinit(path, sha):
    """Raise ValueError unless `path`/.git is still exactly what promote made: HEAD at its one commit, every ref on
    that commit, nothing modified or untracked (or, if no commit was made, still no commit at all)."""
    gitdir = os.path.join(path, ".git")
    bad = ValueError(f"Can't undo: {os.path.basename(path)}/.git changed since (a new commit, or files added or edited). Fix it by hand.")
    if os.path.islink(gitdir) or not os.path.isdir(gitdir): raise bad
    if not shutil.which("git"): raise ValueError(f"Can't undo: git is not installed, so {os.path.basename(path)}/.git can't be checked. Fix it by hand.")
    try:
        head = _git(path, ["rev-parse", "-q", "--verify", "HEAD"])
        refs = _git(path, ["for-each-ref", "--format=%(objectname)"])
        status = _git(path, ["status", "--porcelain", "--untracked-files=all"]) if sha else (0, "", "")
    except (OSError, subprocess.TimeoutExpired): raise bad
    if sha:
        # " D": a committed skeleton file already removed from disk (an earlier rollback step got that far). Nothing is
        # lost with it: its content is the skeleton commit. Anything else (modified, staged, untracked) refuses.
        dirty = [l for l in status[1].splitlines() if not l.startswith(" D ")]
        if head[0] or head[1] != sha or refs[0] or set(refs[1].split()) != {sha} or status[0] or dirty: raise bad
    elif not head[0] or (not refs[0] and refs[1]): raise bad  # a commit (or any ref) appeared since

def _apply_inverse(op):
    _recheck_contained(op)
    if op[0] == "write":
        if op[2] is None: os.remove(op[1])
        else: safefs.atomic_write(op[1], op[2], op[4])
    elif op[0] == "move": os.rename(op[2], op[1])
    elif op[0] == "mkdir": os.rmdir(op[1])
    elif op[0] == "gitinit":
        gitdir = _stored(os.path.join(op[1], ".git"))
        if os.path.islink(gitdir) or not os.path.isdir(gitdir): raise OSError(f"{gitdir} is not a folder")
        shutil.rmtree(gitdir)

def _apply_forward(op):
    _recheck_contained(op)
    if op[0] == "write": safefs.atomic_write(op[1], op[3], op[4])
    elif op[0] == "move": os.rename(op[1], op[2])
    elif op[0] == "mkdir": os.mkdir(op[1])
    elif op[0] == "gitinit": raise ValueError("a removed .git can't be put back")  # undo reports it; the files are intact

def _revert(ops):
    """Reverse `ops` newest first, one at a time, each only if its own result is still as we left it.
    Returns what could not be reversed, in the original order."""
    left = []
    for op in reversed(ops):
        try: _plan_inverse([op]); _apply_inverse(op)
        except (OSError, ValueError): left.append(op)
    return left[::-1]

@contextlib.contextmanager
def mutation(label=""):
    """The only way files are changed: one change at a time, all or nothing. If anything fails the steps already
    taken are reversed; if some cannot be, they stay on the undo list so Undo can finish the job. On success the
    undo entry is recorded at once, before the caller logs anything."""
    with _lock.write():
        tx = Tx(label)
        try: yield tx
        except BaseException as e:
            if not tx.ops: raise
            left = tx.rollback()
            if not left: raise
            _push_undo({"label": f"unfinished: {tx.label or label}", "ops": left, "select": None})
            raise MutationFailed(f"{e}. {len(left)} change(s) could not be rolled back; press Undo to finish putting them back.") from e
        tx.commit()
    for line in tx.logs: _log(line)  # after the undo entry exists and the lock is released: a log failure can't matter

def undo():
    """Reverse the newest change. Checked on a dry run first: refuses (and changes nothing) if any file it
    touched has been edited, moved or added to since. If a step fails part-way, what it already reversed is put
    back, so the change stays whole and undoable."""
    with _lock.write():
        if not _undo: raise ValueError("Nothing to undo.")
        entry = _undo[-1]; ops = entry["ops"]
        _plan_inverse(ops)
        taken = []  # reversed so far, newest first
        try:
            for op in reversed(ops): _apply_inverse(op); taken.append(op)
        except BaseException as e:
            stuck = []
            for op in reversed(taken):  # oldest-reversed last: redo in the original order
                try: _apply_forward(op)
                except (OSError, ValueError): stuck.append(op)
            if stuck:
                entry["ops"] = [o for o in ops if not any(o is s for s in stuck)]
                if not entry["ops"]: _undo.pop()
                raise MutationFailed(f"Undo stopped half-way ({e}) and {len(stuck)} step(s) could not be put back; check {os.path.basename(stuck[0][1])}.") from e
            raise MutationFailed(f"Undo failed ({e}); nothing was changed.") from e
        _undo.pop(); _log(f"undo {entry['label']}")
        return {"ok": True, "msg": f"Undone: {entry['label']}", "select": entry["select"], "left": len(_undo)}

def to_done(src, tx):
    done_dir = os.path.join(NOTES, "done"); tx.ensure_dir(done_dir)
    dst = unique(os.path.join(done_dir, os.path.basename(src)))
    tx.move(src, dst); return dst

def log(msg):
    with open(LOG_PATH, "a", encoding="utf-8", errors="replace") as fh:
        fh.write(f"{dt.datetime.now().isoformat(timespec='seconds')} {msg}\n")

_log_warned = []
def _log(msg):
    """Best-effort log line. A full disk or unwritable log must never undo or hide a change that already happened."""
    try: log(msg)
    except Exception as e:
        if not _log_warned: _log_warned.append(1); print(f"rawtriage-web: can't write the log ({e}); changes still work", file=sys.stderr)

ALIASES = {"archive": "kill", "durable": "reference"}  # 1.x names still accepted

def _text(req, key):
    v = req.get(key)
    if v is None: return ""
    if not isinstance(v, (str, int, float)): raise ValueError(f"{key} must be text")
    return str(v).strip()

def _plan(req):
    """Everything about one decision that can be checked before any file changes: (source path, action, ship-by)."""
    src = safe_note(req.get("file"), write=True)
    if os.path.dirname(src) != os.path.normpath(NOTES): raise ValueError("only Inbox notes can be decided")
    action = ALIASES.get(req.get("action"), req.get("action"))
    if action not in ("keep", "kill", "promote", "reference"): raise ValueError("unknown action")
    if action == "keep" and req.get("status", "cooking") not in ("open", "cooking"): raise ValueError("bad status")
    _text(req, "note"); _text(req, "slug")
    return src, action, (check_promote(req) if action == "promote" else None)

def act(req, tx=None):
    """One decision on one note. With no tx, it is its own undo step."""
    if tx is not None: return _act(req, tx)
    with mutation() as own:
        res = _act(req, own); own.label = res["label"]; own.select = req["file"]
    return res

def _act(req, tx):
    src, action, due = _plan(req); fname = os.path.basename(src); note = _text(req, "note")
    text, eol = read_note(src); title = title_of(text, fname)
    t = today().isoformat()

    if action == "keep":
        status = req.get("status", "cooking")
        lim = limits(); st = inbox_state(fname[:10], field(text, "status") or "?", field(text, "kept"), field(text, "restored"), field(text, "keeps"), lim)
        if st["expired"]: raise ValueError(expired_message(title, st, lim))
        text = set_field(set_field(set_field(text, "status", status), "kept", t), "keeps", str(st["keeps"] + 1))
        if note:
            if not text.endswith("\n"): text += "\n"
            if re.search(r"^## Notes[ \t]*$", text, re.M):
                text = re.sub(r"(^## Notes[ \t]*\n)", lambda m: f"{m.group(1)}\n- next step ({t}): {note}\n", text, count=1, flags=re.M)
            else: text = text.rstrip("\n") + f"\n\n- next step ({t}): {note}\n"
        tx.write(src, text, eol)
        tx.log(f"keep {fname} status={status}"); return {"ok": True, "msg": f"Kept “{title}” ({status})", "label": f"keep “{title}”"}

    if action == "kill":
        text = set_field(text, "status", "dead")
        text = append_outcome(text, f"**Killed {t}:** {note or 'no longer useful.'}")
        tx.write(src, text, eol)
        dst = to_done(src, tx); tx.log(f"kill {fname} -> {dst}")
        return {"ok": True, "msg": f"Killed “{title}” → Archive", "label": f"kill “{title}”"}

    dest = "reference" if action == "reference" else req.get("dest")
    slug = re.sub(r"[^a-z0-9-]", "", (_text(req, "slug") or slug_of(fname)).lower()) or slug_of(fname)
    dest_dir = os.path.join(PROJECTS, dest)
    tx.ensure_dir(dest_dir); proj = None
    if action == "promote" and DESTS[dest] == "folder":
        proj = unique(os.path.join(dest_dir, slug)); tx.mkdir(proj)
        target = os.path.join(proj, "NOTES.md")
    else:
        target = unique(os.path.join(dest_dir, slug + ".md"))
    copy = set_field(text, "status", "promoted")
    copy = set_field(copy, "promoted", t)
    if action == "reference": copy = reference_copy(copy, fname, note)
    if action == "promote":
        copy = set_field(copy, "due", due) if due else drop_field(copy, "due")  # open-ended: drop any old ship-by
        copy = copy.rstrip("\n") + (
            f"\n\n## Plan\n\n- promoted from RawNotes on {t}, " + (f"due {due}\n" if due else "open-ended experiment (no ship-by date)\n") +
            f"- definition of done: {note}\n"
            "- first step:\n" + ("- ship / stop check-in: on the due date, either ship it or kill it\n" if due else
                                 "- check-in: when done means is met, mark it shipped; if it stalls, kill it\n"))
    warning = ""
    if proj:
        copy = set_field(copy, "github", "private")  # intended visibility; going public is gopublic's job
        pslug = os.path.basename(proj)
        write_skeleton(tx, dest, proj, {"title": title, "slug": pslug, "done": note, "due": due or "", "today": t, "note": copy}, eol)
        warning = tx.gitinit(proj, f"Start {pslug} (promoted from RawNotes)")
    else: tx.write(target, copy, eol)
    rel = os.path.relpath(target, PROJECTS)
    # original capture: mark promoted, record destination, move to done/
    text = set_field(text, "status", "promoted")
    text = append_outcome(text, f"**Promoted {t}** -> `../{rel}`" + (f" ({note})" if note and action == 'reference' else ""))
    tx.write(src, text, eol)
    dst = to_done(src, tx); tx.log(f"{'durable' if action == 'reference' else action} {fname} -> {rel}; capture -> {dst}")
    verb = "Promoted" if action == "promote" else "Filed"
    if warning: tx.log(f"warning {fname}: {warning}")  # not "promote …": Pulse counts those lines as decisions
    return {"ok": True, "msg": f"{verb} “{title}” → {rel}", "target": rel, "label": f"{action} “{title}”", **({"warning": warning} if warning else {})}

SKELETONS = os.path.join(HERE, "starter", "skeletons")  # starter/skeletons/<dest>/: the files Promote writes into a new project folder
SKELETON_VAR = re.compile(r"\{\{(title|slug|done|due|today|note)\}\}")

def skeleton_files(dest):
    """[(relative path, template text)] of a destination's skeleton, folders before what they hold."""
    base = os.path.join(SKELETONS, dest); out = []
    for root, dirs, files in os.walk(base):
        dirs.sort()
        for f in sorted(files):
            with open(os.path.join(root, f), encoding="utf-8") as fh: out.append((os.path.relpath(os.path.join(root, f), base), fh.read()))
    if not out: raise ValueError(f"no project skeleton for {dest} (starter/skeletons/{dest} is missing)")
    return out

def write_skeleton(tx, dest, proj, values, eol="\n"):
    """Write the skeleton into `proj` (already made by tx). {{title}} {{slug}} {{done}} {{due}} {{today}} {{note}} are
    filled in one pass, so text that came from the note is never itself treated as a placeholder. NOTES.md keeps
    the note's own line endings; every other file is LF."""
    for rel, template in skeleton_files(dest):
        if os.path.dirname(rel): tx.ensure_dir(os.path.join(proj, os.path.dirname(rel)))
        text = SKELETON_VAR.sub(lambda m: values[m[1]], template)
        tx.write(os.path.join(proj, rel), text, eol if rel == "NOTES.md" else "\n")

def check_promote(req):
    """Validate a promotion before anything is written. Returns the ship-by date, or None for an experiment."""
    dest = req.get("dest")
    if dest not in DESTS: raise ValueError("bad destination")
    if done_placeholder(_text(req, "note")): raise ValueError("Say what done means first.")
    return None if dest == "experiments" else ship_by(req)  # experiments are open-ended

def ship_by(req):
    """The ship-by date for a promotion: an exact date (`due`) if given, else today + `due_days`."""
    if req.get("due"):
        try: due = dt.date.fromisoformat(str(req["due"]))
        except ValueError: raise ValueError("ship-by date must be YYYY-MM-DD")
        if not 1 <= (due - today()).days <= 365: raise ValueError("ship-by date must be within the next year")
        return due.isoformat()
    try: days = int(req.get("due_days") or DUE_DAYS)
    except (TypeError, ValueError): raise ValueError("ship-by must be a number of days")
    if not 1 <= days <= 90: raise ValueError("ship-by must be 1 to 90 days")
    return (today() + dt.timedelta(days=days)).isoformat()

def _file_list(req, minimum, maximum=200):
    """The `files` of a batch or merge request, checked before anything is touched: a list of distinct names."""
    files = req.get("files")
    if not isinstance(files, list) or not all(isinstance(f, str) and f for f in files): raise ValueError("files must be a list of note names")
    if len(files) < minimum: raise ValueError("pick at least two notes to merge" if minimum > 1 else "pick at least one note")
    if len(files) > maximum: raise ValueError(f"too many notes (limit {maximum})")
    return files

def _distinct_sources(files, resolve):
    """Resolve and check every source up front; the same note listed twice (under any spelling) is refused."""
    srcs = []
    for f in files:
        p = resolve(f)
        if p in srcs: raise ValueError(f"“{os.path.basename(p)}” is listed twice")
        srcs.append(p)
    return srcs

def _inbox_source(f):
    src = safe_note(f, write=True)
    if os.path.dirname(src) != os.path.normpath(NOTES): raise ValueError("only Inbox notes can be merged")
    return src

def batch(req):
    """The same decision for several notes, as one undo step: every note is checked first, and if a step fails
    part-way the ones already done are put back."""
    files = _file_list(req, 1)
    if ALIASES.get(req.get("action"), req.get("action")) not in ("keep", "kill", "reference"): raise ValueError("batch can keep, kill or file as reference")
    with mutation() as tx:
        srcs = _distinct_sources(files, lambda f: _plan({**req, "file": f, "slug": ""})[0])
        if ALIASES.get(req["action"], req["action"]) == "keep":  # one expired note refuses the whole batch, and names it
            lim = limits(); bad = []
            for p in srcs:
                text, _ = read_note(p); name = os.path.basename(p)
                st = inbox_state(name[:10], field(text, "status") or "?", field(text, "kept"), field(text, "restored"), field(text, "keeps"), lim)
                if st["expired"]: bad.append(expired_message(title_of(text, name), st, lim))
            if bad: raise ValueError("Nothing was kept. " + "; ".join(bad))
        for f in files: _act({**req, "file": f, "slug": ""}, tx)
        verb = {"keep": "Kept", "kill": "Killed", "reference": "Filed"}[ALIASES.get(req["action"], req["action"])]
        tx.label = f"{verb.lower()} {len(files)} notes"; tx.select = files[0]
    return {"ok": True, "msg": f"{verb} {len(files)} notes"}

def merge(req):
    """Combine several Inbox notes into one new note (newest first), move the sources to done/ pointing at it,
    then optionally promote or file the merged note. One undo step; nothing is written unless every source checks out."""
    files = _file_list(req, 2)
    title = re.sub(r"\s+", " ", _text(req, "title")).strip()
    if not title: raise ValueError("give the merged note a title")
    then = req.get("then", "inbox")
    if then not in ("inbox", "promote", "reference"): raise ValueError("bad follow-up")
    if then == "promote": check_promote(req)  # fail before the merge writes anything
    t = today().isoformat(); warning = ""
    with mutation(f"merge {len(files)} notes") as tx:
        srcs = _distinct_sources(files, _inbox_source)
        srcs.sort(key=os.path.basename, reverse=True)
        notes = {p: read_note(p) for p in srcs}
        slug = re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", title.lower())).strip("-")[:60] or "merged"
        target = unique(os.path.join(NOTES, f"{t}-{slug}.md")); tname = os.path.basename(target)
        types = [field(notes[p][0], "type") for p in srcs]
        parts = [f"# {title}", "", f"- date: {t}", "- status: cooking",
                 f"- type: {max(set(types), key=types.count) or 'research'}", "",
                 "## What's the question / problem?", "",
                 _text(req, "question") or f"Merged from {len(srcs)} notes on {t}; newest first below.", "", "## Notes", ""]
        sources = []
        for p in srcs:
            text = notes[p][0]; name = os.path.basename(p)
            body = note_body_for_merge(text)
            body = re.sub(r"^(#{1,5})(\s)", r"#\1\2", body, flags=re.M)  # its headings sit one level under its section
            parts += [f"## {title_of(text, name)} ({name[:10]})", "", body.strip(), ""]
            sources.append(name)
        parts += ["## Sources", ""] + [f"- `done/{n}`" for n in sources] + [""]
        tx.write(target, "\n".join(parts))
        for p in srcs:
            text, eol = notes[p]
            text = set_field(text, "status", "merged")
            text = append_outcome(text, f"**Merged {t}** -> `RawNotes/{tname}`")
            tx.write(p, text, eol); to_done(p, tx); tx.log(f"merge {os.path.basename(p)} -> {tname}")
        msg = f"Merged {len(srcs)} notes into “{title}”"; tx.select = tname
        if then != "inbox":
            res = _act({**req, "file": tname, "action": then, "slug": req.get("slug") or ""}, tx)
            msg += "; " + res["msg"][0].lower() + res["msg"][1:]
            tx.select = None; warning = res.get("warning", "")
    return {"ok": True, "msg": msg, "file": tname if then == "inbox" else None, **({"warning": warning} if warning else {})}

def note_body_for_merge(text):
    """A source's content for the merged note: everything but its title and date/status/type/promoted/due lines
    (the question stays, as a section of its own)."""
    lines = text.split("\n"); head = header_lines(lines); t = title_index(lines)
    drop = {i for i in head if HEAD_LINE.match(lines[i]).group(1) in ("date", "status", "type", "promoted", "due")} | ({t} if t is not None else set())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(l for i, l in enumerate(lines) if i not in drop)).strip()

def restore(req):
    """Bring a note back from done/ into the Inbox (status cooking)."""
    with mutation() as tx:
        src = safe_note(req.get("file", ""), write=True)
        if os.path.dirname(src) != os.path.join(os.path.normpath(NOTES), "done"): raise ValueError("only Archive notes can be restored")
        dst = os.path.join(NOTES, os.path.basename(src))
        if os.path.lexists(dst): raise ValueError("a note with that name is already in the Inbox")
        text, eol = read_note(src); title = title_of(text, os.path.basename(src))
        text = append_outcome(set_field(set_field(text, "status", "cooking"), "restored", today().isoformat()), f"**Restored {today().isoformat()}** to the Inbox")
        tx.write(src, text, eol); tx.move(src, dst)
        tx.label = f"restore “{title}”"; tx.select = "done/" + os.path.basename(src)
    _log(f"restore {os.path.basename(src)}")
    return {"ok": True, "msg": f"Restored “{title}” to the Inbox", "file": os.path.basename(dst)}

def _status_edit(text, req, name):
    """The new text, label and message for a project status action (shipped / extend / kill)."""
    t = today().isoformat(); action = req.get("action")
    if action == "shipped":
        return set_field(set_field(set_field(text, "status", "shipped"), "shipped", t), "closed", t), f"ship {name}", f"Shipped {name} 🎉"
    if action == "extend":
        if not field(text, "due"): raise ValueError("open-ended experiments have no ship-by date to push")
        why = _text(req, "note")
        if len(why) < REASON_MIN: raise ValueError(f"Say why you need more time (at least {REASON_MIN} characters).")
        done = _count(field(text, "extensions"))
        if done >= tunable("max_extensions", MAX_EXTENSIONS): raise ValueError("Pushed twice already: ship it or kill it.")
        try: days = int(7 if req.get("days") is None else req.get("days"))
        except (TypeError, ValueError): raise ValueError("days must be a number")
        if not 1 <= days <= EXTEND_MAX_DAYS: raise ValueError(f"days must be 1 to {EXTEND_MAX_DAYS}")
        old = field(text, "due")
        try: base = max(dt.date.fromisoformat(old), today())
        except ValueError: base = today()
        due = (base + dt.timedelta(days=days)).isoformat()
        text = append_to_section(set_field(set_field(text, "due", due), "extensions", str(done + 1)), "Plan",
                                 f"- pushed {old} → {due} ({t}): {' '.join(why.split())}")
        return text, f"push {name} to {due}", f"{name}: ship by {due}"
    if action == "kill":
        why = _text(req, "note")
        if not why: raise ValueError("Say why you are killing it.")
        text = append_outcome(set_field(set_field(text, "status", "dead"), "closed", t), f"**Killed {t}:** {why}")
        return text, f"kill {name}", f"Killed {name}"
    raise ValueError("unknown project action")

def project_status(req):
    """On a promoted project file (a due: line, or an open-ended experiment): mark it shipped, push the ship-by
    date (not on open-ended experiments), or kill it.
    A project folder with no promoted file ({name, kind} instead of path) can be shipped or killed through its NOTES.md."""
    if not req.get("path"): return folder_project_status(req)
    rel = req.get("path", "")
    with mutation() as tx:
        if not isinstance(rel, str) or rel not in {x["path"] for x in inflight()}: raise ValueError("unknown project file")
        path = os.path.join(PROJECTS, rel)
        text, eol = read_note(path)
        name = rel.split("/")[1] if rel.count("/") >= 2 else os.path.basename(rel)[:-3]
        text, tx.label, msg = _status_edit(text, req, name)
        tx.write(path, text, eol)
    _log(f"project {req.get('action')} {rel}")
    return {"ok": True, "msg": msg}

def folder_project_status(req):
    path, kind = project_dir(req.get("name", ""), req.get("kind"), write=True); name = os.path.basename(path)
    if req.get("action") == "extend": raise ValueError("unknown project action")  # no due: line to push in a bare folder
    with mutation() as tx:
        target = notes_file(path)
        text, eol = read_note(target) if os.path.exists(target) else (f"# {name}\n", "\n")
        text, tx.label, msg = _status_edit(text, req, name)
        tx.write(target, text, eol)
    _log(f"project {req.get('action')} {kind}/{name}")
    return {"ok": True, "msg": msg}

def stats(weeks=8):
    """Inbox flow for the Pulse page: notes captured vs. decided per week, and how long the Inbox waits."""
    notes = scan_notes(); t = today()
    start = t - dt.timedelta(days=t.weekday())  # Monday of this week
    bins = [{"week": (start - dt.timedelta(weeks=weeks - 1 - i)).isoformat(), "captured": 0, "decided": 0} for i in range(weeks)]
    def slot(d):
        i = weeks - 1 - (start - (d - dt.timedelta(days=d.weekday()))).days // 7
        return bins[i] if 0 <= i < weeks else None
    for n in notes["live"] + notes["done"]:
        try: b = slot(dt.date.fromisoformat(n["date"]))
        except ValueError: continue
        if b: b["captured"] += 1
    try:
        with open(LOG_PATH, encoding="utf-8", errors="replace") as fh: lines = fh.read().splitlines()
    except OSError: lines = []
    for line in lines:
        bits = line.split(" ", 2)
        if len(bits) < 2 or bits[1] not in ("promote", "durable", "reference", "archive", "kill", "merge"): continue
        try: b = slot(dt.date.fromisoformat(bits[0][:10]))
        except ValueError: continue
        if b: b["decided"] += 1
    ages = sorted(n["age"] for n in notes["live"] if n["age"] is not None)
    mid = len(ages) // 2
    median = (ages[mid] if len(ages) % 2 else (ages[mid - 1] + ages[mid]) / 2) if ages else None
    return {"weeks": bins, "median_wait": median, "oldest": ages[-1] if ages else None, "live": len(notes["live"]),
            "expired": sum(1 for n in notes["live"] if n["expired"]), "stale": sum(1 for n in notes["live"] if n["stale"]),
            "undo": [u["label"] for u in reversed(list(_undo))]}



def open_target(req):
    """The one thing /api/open may launch: a project folder on the board ({name, kind}) or a promoted file the board
    lists ({path}), as a real path inside the projects folder. Anything else (a URL, an absolute path, ..) is refused."""
    if req.get("path") is not None:
        rel = req.get("path")
        if not isinstance(rel, str) or rel not in {x["path"] for x in inflight()}: raise ValueError("that is not a project the board knows")
        return safefs.ensure_inside([PROJECTS], os.path.join(PROJECTS, rel))
    path, _ = project_dir(req.get("name"), req.get("kind"))
    return safefs.ensure_inside([PROJECTS], path)

def open_argv(path):
    """The opener's command line. `path` is already validated and absolute, so it starts with `/` and can never be read as an
    option. No `--`: the installed xdg-open rejects it as an unexpected option and opens nothing."""
    if not os.path.isabs(path): raise ValueError("only an absolute path can be opened")
    return ["xdg-open", path]

def in_own_scope(argv):
    """Under the systemd unit, run the opener in a scope of its own: otherwise the editor or file manager it starts
    lives in the unit's cgroup and is killed whenever the unit stops or restarts. systemd-run --scope keeps the
    opener in the foreground, so its exit code (and the failure report below) is unchanged."""
    if service_owns(os.getpid()) and shutil.which("systemd-run"):
        return ["systemd-run", "--user", "--scope", "--quiet", "--collect", *argv]
    return argv

def open_path(path):
    """Hand a validated path to the desktop's opener. No shell, one absolute operand. If the opener fails at once
    (it can't parse its arguments, or finds no handler) that is reported instead of swallowed; one that keeps
    running (it launched something) is left alone."""
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(in_own_scope(open_argv(path)), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err, start_new_session=True)
        try: code = proc.wait(timeout=1.5)
        except subprocess.TimeoutExpired: return
        if code:
            err.seek(0); why = err.read(300).decode("utf-8", "replace").strip()
            raise OSError(f"it exited with status {code}" + (f": {why}" if why else ""))

def note_view(rel):
    """A note for the reader: the raw text, the whole question, and the body without what the page shows itself."""
    with _lock.read():
        p = safe_note(rel)
        with open(p, "rb") as fh: text = to_lf(fh.read().decode("utf-8", "replace"))
    return {"text": text, "question": question_of(text), "body": note_body(text)}

MAX_BODY = 1 << 20

class Server(ThreadingHTTPServer):
    request_queue_size = 128  # the default backlog of 5 drops connections when a page fires many requests at once
    daemon_threads = True

class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", ctype if ctype.startswith(("font/", "image/")) else ctype + "; charset=utf-8")
        if ctype.startswith(("font/", "image/")): self.send_header("Cache-Control", "max-age=604800")
        else: self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(data)))
        try: self.end_headers(); self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError): pass  # the page cancelled this request (it moved on); nothing to report
    def _fail(self, e):
        """Bad requests are the caller's (400); a file that vanished is 404; anything else is ours (500)."""
        code = (500 if isinstance(e, MutationFailed) else 400 if isinstance(e, (ValueError, KeyError, TypeError, AttributeError, RuntimeError))
                else 404 if isinstance(e, FileNotFoundError) else 500)
        self._send(code, {"error": str(e.args[0]) if isinstance(e, KeyError) and e.args else str(e) or type(e).__name__})
    def log_message(self, *a): pass
    def _refuse(self, post):
        """Browser-attack guard (no login here, so the page you are on must not be able to drive it).
        Loopback bind: only localhost Host names (stops DNS rebinding). Any bind: a request carrying an
        Origin must come from this same host (stops cross-site forms), and POST bodies must be JSON."""
        host = self.headers.get("Host", "")
        if BIND_HOST in ("127.0.0.1", "localhost", "::1") and urlparse("//" + host).hostname not in ("127.0.0.1", "localhost", "::1"):
            return "unexpected Host header"
        origin = self.headers.get("Origin")
        if origin is not None and urlparse(origin).netloc != host: return "cross-origin request refused"
        if post and self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            return "Content-Type must be application/json"
        return None
    def _this_machine(self):
        """A browser on the server's own machine: the peer and the Host name (on a --host 0.0.0.0 bind a rebound DNS
        name would otherwise pass as this machine)."""
        return (self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")
                and urlparse("//" + self.headers.get("Host", "")).hostname in ("127.0.0.1", "localhost", "::1"))
    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        bad = self._refuse(False)
        if bad: return self._send(403, {"error": bad})
        try:
            if u.path in ("/", APP_PATH): return self._send(200, INDEX_HTML, "text/html")
            if u.path == "/api/version": return self._send(200, {"version": VERSION, "started": STARTED, "pid": os.getpid(), "app": "rawnotes-triage"})
            if u.path == "/api/projects": return self._send(200, {"projects": projects_listing(q.get("refresh") == ["1"])})
            if u.path == "/api/project": return self._send(200, project_detail(q.get("name", [""])[0], q.get("kind", [""])[0]))
            if u.path == "/api/github": return self._send(200, github_status(q.get("refresh") == ["1"]))
            if u.path == "/api/activity": return self._send(200, github_activity(q.get("refresh") == ["1"]))
            if u.path == "/api/repo": return self._send(200, repo_overview(q.get("name", [""])[0]))
            if u.path == "/api/repo/path": return self._send(200, repo_path(q.get("name", [""])[0], q.get("path", [""])[0]))
            if u.path == "/api/setup": return self._send(200, setup_state())
            if u.path == "/api/notes": return self._send(200, list_notes())
            if u.path == "/api/stats": return self._send(200, stats())
            if FONT_RE.match(u.path):
                p = os.path.join(HERE, u.path.lstrip("/"))
                if not os.path.isfile(p): return self._send(404, {"error": "not found"})
                with open(p, "rb") as fh: return self._send(200, fh.read(), "font/woff2")
            if u.path in ICONS:
                with open(os.path.join(HERE, "static", "icons", ICONS[u.path]), "rb") as fh: return self._send(200, fh.read(), "image/png")
            if u.path == "/api/note": return self._send(200, note_view(q["f"][0]))
            self._send(404, {"error": "not found"})
        except Exception as e: self._fail(e)
    def do_POST(self):
        bad = self._refuse(True)
        if bad: return self._send(403, {"error": bad})
        try:
            n = int(self.headers.get("Content-Length", 0))
            if not 0 <= n <= MAX_BODY: return self._send(413, {"error": "request too large"})
            req = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(req, dict): raise ValueError("the request must be a JSON object")
            path = urlparse(self.path).path
            if path == "/api/action": return self._send(200, act(req))
            if path == "/api/batch": return self._send(200, batch(req))
            if path == "/api/merge": return self._send(200, merge(req))
            if path == "/api/restore": return self._send(200, restore(req))
            if path == "/api/project/status": return self._send(200, project_status(req))
            if path in ("/api/github/token", "/api/github/token/remove"):
                if not self._this_machine(): raise ValueError("The GitHub sign-in can only be changed from the machine running the server.")
                return self._send(200, set_github_token(req) if path == "/api/github/token" else remove_github_token())
            if path == "/api/setup":
                if not self._this_machine(): raise ValueError("The notes folder can only be set up from the machine running the server.")
                return self._send(200, setup_notes(req))
            if path == "/api/undo": return self._send(200, undo())
            if path == "/api/done": return self._send(200, set_done(req.get("name", ""), req.get("text", ""), req.get("expected", ""), req.get("kind")))
            if path == "/api/open":
                if self.client_address[0] not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"): raise ValueError("Open only works from the machine running the server.")
                target = open_target(req)
                try: open_path(target)
                except OSError as e: raise OSError(f"couldn't launch xdg-open: {e}")
                return self._send(200, {"ok": True})
            self._send(404, {"error": "not found"})
        except Exception as e: self._fail(e)

TRIAGE_TITLE = "<title>RawNotes · Triage"

def running_server(port):
    """Ask whatever is on the port what it is. None = nothing there.
    {"version", "started", "pid", "foreign"}: foreign=True means something answered that is not rawnotes-triage
    (never stop that one). Servers older than 1.05 have no /api/version but do serve this page, so they report version None."""
    import urllib.request, urllib.error
    base = f"http://127.0.0.1:{port}"
    try:
        with urllib.request.urlopen(base + "/api/version", timeout=2) as r:
            info = json.loads(r.read())
            if not isinstance(info, dict) or "version" not in info: return {"version": None, "started": None, "pid": None, "foreign": True}
            return {"version": info["version"], "started": info.get("started"), "pid": info.get("pid"), "foreign": False}
    except urllib.error.HTTPError:  # answers, but has no /api/version: an old triage page, or some other service
        try:
            with urllib.request.urlopen(base + "/", timeout=2) as r: old = TRIAGE_TITLE in r.read(4096).decode("utf-8", "replace")
        except (OSError, ValueError): old = False
        return {"version": None, "started": None, "pid": None, "foreign": not old}
    except (OSError, ValueError): return None

def port_pid(port):
    try: out = subprocess.run(["ss", "-ltnpH", f"sport = :{port}"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired): return None
    m = re.search(r"pid=(\d+)", out); return int(m.group(1)) if m else None

def is_triage_process(pid):
    """Is `pid` a rawnotes-triage server? Looks at its command line (a server.py whose first lines name this app);
    unreadable or anything else counts as no."""
    try:
        with open(f"/proc/{int(pid)}/cmdline", "rb") as fh: args = [a.decode("utf-8", "replace") for a in fh.read().split(b"\0") if a]
        cwd = os.readlink(f"/proc/{int(pid)}/cwd")
    except (OSError, ValueError): return False
    for a in args[1:4]:
        if os.path.basename(a) != "server.py": continue
        try:
            with open(os.path.join(cwd, a), "rb") as fh: return b"rawnotes-triage" in fh.read(400)
        except OSError: return False
    return False

def stop_server(info, port):
    """Stop the triage server on `port`, after checking it is one: the page it serves says so, and the process that
    owns the port is a server.py of this app. Anything else is left alone."""
    import signal
    if info.get("foreign"): raise SystemExit(f"rawtriage-web: port {port} is used by something that isn't rawnotes-triage; not touching it.")
    owner = port_pid(port); pid = info.get("pid") or owner
    if not pid: raise SystemExit(f"rawtriage-web: can't find the process on port {port}; stop it by hand.")
    if owner and pid != owner: raise SystemExit(f"rawtriage-web: the server on port {port} says pid {pid} but pid {owner} owns the port; stop it by hand.")
    if not is_triage_process(pid): raise SystemExit(f"rawtriage-web: pid {pid} on port {port} doesn't look like rawnotes-triage; not stopping it.")
    os.kill(pid, signal.SIGTERM)
    for _ in range(50):
        if running_server(port) is None: return pid
        time.sleep(0.1)
    raise SystemExit(f"rawtriage-web: pid {pid} didn't stop; try kill {pid}")

SERVICE = "rawtriage-web.service"  # the systemd user unit install-webapp.sh installs

def service_owns(pid):
    """Is `pid` running inside the rawtriage-web systemd user unit? (Its cgroup path names the unit.)"""
    try:
        with open(f"/proc/{int(pid)}/cgroup") as fh: return f"/{SERVICE}" in fh.read()
    except (OSError, ValueError, TypeError): return False

def restart_service(port):
    """Restart the unit rather than kill its server: a SIGTERM counts as a clean stop, so systemd would leave it down."""
    r = subprocess.run(["systemctl", "--user", "restart", SERVICE], capture_output=True, text=True)
    if r.returncode: raise SystemExit(f"rawtriage-web: systemctl --user restart {SERVICE} failed: {r.stderr.strip()}")
    for _ in range(50):
        info = running_server(port)
        if info and not info["foreign"]: return info
        time.sleep(0.1)
    raise SystemExit(f"rawtriage-web: {SERVICE} restarted but nothing answers on port {port}; see: journalctl --user -u {SERVICE}")

def open_page(url, app):
    """Open the page: in its own window with --app (Omarchy's web-app launcher), else a browser tab.
    Without omarchy-launch-webapp (another desktop), or if it fails at once (no supported browser), --app
    falls back to the tab. Waits up to 2 s to see that; call it from a thread when that would hold things up.
    The app window opens at APP_PATH, so its class is chrome-127.0.0.1__triage-Default (the port isn't in it)."""
    if app: url = url.rstrip("/") + APP_PATH
    if app and shutil.which("omarchy-launch-webapp"):
        try:
            p = subprocess.Popen(["omarchy-launch-webapp", url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                if p.wait(timeout=2) == 0: return
            except subprocess.TimeoutExpired: return  # still running: the window is up
        except OSError: pass
    import webbrowser; webbrowser.open(url)

def notify(msg):
    try: subprocess.Popen(["notify-send", "RawNotes Triage", msg], stderr=subprocess.DEVNULL)
    except OSError: pass

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1", help="interface to listen on; 0.0.0.0 = all (no auth, trusted networks only)")
    ap.add_argument("--notes", help="the notes folder (default: the one chosen on first run, else ~/Projects/RawNotes)"); ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--restart", action="store_true", help="stop a server already on the port, then start this one")
    ap.add_argument("--app", action="store_true", help="open the page in its own app window (Omarchy web app), not a browser tab")
    ap.add_argument("--service", action="store_true", help="run under systemd: no browser; exit with an error if the port is taken, so systemd retries")
    ap.add_argument("--log", default=LOG_PATH, help="where actions are logged (and Pulse reads decisions from)")
    a = ap.parse_args(); a.no_browser = a.no_browser or a.service; BIND_HOST = a.host; NOTES_SOURCE = "flag" if a.notes else "config" if saved_notes_dir() else "default"
    NOTES = os.path.abspath(os.path.expanduser(a.notes or saved_notes_dir() or DEFAULT_NOTES)); PROJECTS = os.path.dirname(NOTES)
    LOG_PATH = os.path.abspath(os.path.expanduser(a.log))
    url = f"http://127.0.0.1:{a.port}/"
    info = running_server(a.port)
    if info and info["foreign"]:
        raise SystemExit(f"rawtriage-web: port {a.port} is in use by something else (not rawnotes-triage); try --port N")
    if info and a.restart and service_owns(info.get("pid") or port_pid(a.port)):
        new = restart_service(a.port)
        print(f"rawtriage-web: restarted {SERVICE}; it now runs version {new['version']} "
              f"(the copy the unit points at, not necessarily this one)")
        if not a.no_browser: open_page(url, a.app)
        raise SystemExit(0)
    if info and a.service:
        raise SystemExit(f"rawtriage-web: port {a.port} is held by another triage server (pid {info.get('pid')}); "
                         f"{SERVICE} will try again")
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
        if not a.no_browser: open_page(url, a.app)
        raise SystemExit(0)
    try: httpd = Server((a.host, a.port), H)
    except OSError as e: raise SystemExit(f"rawtriage-web: port {a.port} is in use by something else ({e}); try --port N")
    print(f"rawnotes-triage v{VERSION}: {NOTES}\n{url}  (Ctrl-C to stop)")
    if not os.path.isdir(NOTES): print(f"no notes folder at {NOTES} yet: the page will ask where to create it")
    if a.host != "127.0.0.1": print(f"listening on {a.host}:{a.port} for other devices (no login; trusted networks only)")
    if not a.no_browser: threading.Thread(target=open_page, args=(url, a.app), daemon=True).start()
    try: httpd.serve_forever()
    except KeyboardInterrupt: pass
