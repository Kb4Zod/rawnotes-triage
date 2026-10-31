#!/usr/bin/env python3
"""rawnotes-triage — local website for reviewing ~/Projects/RawNotes.

Usage: python3 server.py [--port 8765] [--host 127.0.0.1] [--notes DIR (else the saved folder, else ~/Projects/RawNotes)] [--log triage.log] [--restart] [--app] [--no-commit]

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
project status (mark shipped / push ship-by date), close out of a project folder (next round in place, or a move to
maintained/ or archive/; kill moves it to archive/<name>_abandoned) after a read-only dependency check (depcheck.py),
and undo of the last change made through this server.
After each change (undo included) the notes-folder files it touched are committed, if the notes folder is a git
repo: only those paths, message "triage: <what>". A commit that can't happen warns; the change stands (--no-commit).
"""
import argparse, base64, contextlib, errno, json, os, re, shlex, shutil, stat, subprocess, sys, tempfile, threading, time, urllib.parse, datetime as dt
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import depcheck, safefs
from notemeta import (field, set_field, drop_field, title_of, append_outcome, append_to_section, closed_date, question_of, question_summary, note_body,
                      eol_of, with_eol, to_lf, header_lines, title_index, HEAD_LINE)

VERSION = "2.13"  # bump on every change; history in CHANGELOG.md
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

def limits(conf=None):
    """The four Inbox numbers: LIMIT_DEFAULTS, overridden by whole-number values >= 1 in config.json (`conf`: an
    already-read config, so a listing reads the file once, not once per note)."""
    d = read_config() if conf is None else conf
    return {k: config_int(k, v, minimum=1, conf=d) for k, v in LIMIT_DEFAULTS.items()}

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

def summarize(path, sub="", lim=None):
    core = _notes_cache.get(("note", path), [path], lambda _: _summary_core(path, sub))
    try: age = (today() - dt.date.fromisoformat(core["date"])).days
    except ValueError: age = None
    pub = {k: v for k, v in core.items() if not k.startswith("_")}
    if sub: return {**pub, "age": age, "stale": False}
    st = inbox_state(core["date"], core["status"], core["_kept"], core["_restored"], core["_keeps"], lim)
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

def _scan(d, sub, lim=None):
    """Summaries of the note files in `d`. A file that vanished or can't be read since the listing (edited or moved
    by hand while we look) is skipped, not an error; a symlink pointing outside the notes folder is never read."""
    try: names = sorted(f for f in os.listdir(d) if NOTE_RE.match(f))
    except OSError: return []
    root = os.path.realpath(NOTES); out = []
    for f in names:
        p = os.path.join(d, f)
        try:
            if os.path.islink(p) and not _within(os.path.realpath(p), root): continue
            out.append(summarize(p, sub, lim))
        except OSError: continue
    return out

def scan_notes(conf=None):
    """Inbox and Archive summaries, read as one snapshot with respect to our own changes. config.json is read once."""
    lim = limits(conf)
    with _lock.read():
        return {"live": _scan(NOTES, "", lim), "done": _scan(os.path.join(NOTES, "done"), "done")[::-1]}

def inbox_summary(live=None):
    """The one-line Inbox count the weekly nudge prints: "N open, N cooking (N stale, N expired)"."""
    live = scan_notes()["live"] if live is None else live
    n = lambda s: sum(1 for x in live if x["status"] == s)
    return f"{n('open')} open, {n('cooking')} cooking ({sum(1 for x in live if x['stale'])} stale, {sum(1 for x in live if x['expired'])} expired)"

def list_notes(with_inflight=True):
    with _lock.read():
        conf = read_config()  # once per listing: every note and project below uses this copy
        out = scan_notes(conf)
        return {**out, "inflight": inflight(conf) if with_inflight else [], "limits": limits(conf), "summary": inbox_summary(out["live"]),
                "today": today().isoformat(), "notes_dir": NOTES,
                "closed_window_days": config_int("closed_window_days", CLOSED_WINDOW_DAYS, conf=conf)}

def _count(v):
    """A header count (extensions: 2) as an int; missing or junk counts as 0."""
    return int(v) if v.isdigit() else 0

ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")

def iso_day(s):
    """`s` if it is a real YYYY-MM-DD date, else "": header text goes to the page, so anything else is dropped here."""
    return s if isinstance(s, str) and ISO_DAY.fullmatch(s) and _day(s) else ""

def _meta_of(path, f):
    """The part of a project file the board needs: kept until the file changes. A `due:` that isn't a real
    YYYY-MM-DD date counts as no ship-by date."""
    with open(path, "rb") as fh: raw = fh.read(CLOSED_READ)
    text = to_lf(raw[:4000].decode("utf-8", "replace"))
    status = field(text, "status") or "?"
    # a killed project's date can sit far down, in ## Outcome: only closed projects pay for reading more
    closed = closed_date(to_lf(raw.decode("utf-8", "replace")) if status in CLOSED else text)
    return {"title": title_of(text, f), "due": iso_day(field(text, "due")), "promoted": field(text, "promoted"), "status": status,
            "extensions": _count(field(text, "extensions")), "closed": closed}

def in_closed_window(day, window=None):
    """Was `day` (YYYY-MM-DD) within the last closed_window_days days (`window`, else read from config.json)?"""
    if window is None: window = config_int("closed_window_days", CLOSED_WINDOW_DAYS)
    try: return (today() - dt.date.fromisoformat(day)).days <= window
    except ValueError: return False

def inflight(conf=None):
    """Promoted notes with a due date (plus open-ended experiments), found in the sibling project folders."""
    conf = read_config() if conf is None else conf; out = []
    window = config_int("closed_window_days", CLOSED_WINDOW_DAYS, conf=conf); max_ext = config_int("max_extensions", MAX_EXTENSIONS, conf=conf)
    for dest in ("active", "experiments", "learning", "templates", "maintained", "archive"):
        d = os.path.join(PROJECTS, dest)
        if not os.path.isdir(d): continue
        for root, dirs, files in os.walk(d):
            if root[len(d):].count(os.sep) > 1: dirs[:] = []; continue
            if root == d and dest in PROJECT_KINDS:  # the same folders the board lists: no linked worktree, no hand-made study folder
                dirs[:] = [x for x in dirs if on_board(os.path.join(d, x), dest)]
            for f in files:
                if not f.endswith(".md"): continue
                p = os.path.join(root, f)
                try: m = _meta_cache.get(("meta", p), [p], lambda _: _meta_of(p, f))
                except OSError: continue
                due = m["due"]; recently_closed = False
                if dest in ("maintained", "archive"):
                    # finished work that moved out of the board's folders: only the Closed column shows it, and only while recent
                    if m["closed"]:
                        if not in_closed_window(m["closed"], window) or not (f.lower() == "notes.md" or root == d): continue
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
                            "extensions": m["extensions"], "max_extensions": max_ext,
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
        target, lines, eol = _with_done(path, name, text, hit)
        tx.write(target, eol.join(lines))
        tx.select = None
    _log(f"done {kind}/{name} -> {os.path.basename(target)}: {text[:80]}")
    return {"ok": True, "file": os.path.basename(target), "msg": f"Saved to {os.path.basename(target)}"}

def _with_done(path, name, text, hit):
    """(file, lines, eol): `text` put in as the definition of done where find_done found one (`hit`), else as a
    `- definition of done:` line in the Plan of NOTES.md (made if missing). Nothing is written here."""
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
    return target, lines, eol

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

# ---- close out: where a finished project folder goes, and what points at it first (depcheck, read-only) ----
DEPS = None  # where the dependency check looks: a depcheck.Sources (tests give fake roots); None = this machine
SYNC_APPS = "sync-apps"  # run after a folder moves (or moves back) so Obsidian's Apps/ links follow it; best effort
CLOSE_DEST = {"maintained": ("maintained", ""), "archive": ("archive", ""), "abandoned": ("archive", "_abandoned")}

def dep_sources(): return DEPS or depcheck.Sources.default()

def unique_dir(path):
    """`path`, or `path`-2, -3 … if the name is taken (no extension splitting: folder names may contain dots)."""
    n = 2; out = path
    while os.path.lexists(out): out = f"{path}-{n}"; n += 1
    return out

def close_suggestion(path):
    """Maintained for a folder with a GitHub origin (a live tool that still gets fixes), else Archive (a one-off,
    often just a NOTES.md)."""
    return "maintained" if github_state(path)["repo"] else "archive"

def close_target(path, outcome):
    base, suffix = CLOSE_DEST[outcome]
    return unique_dir(os.path.join(PROJECTS, base, os.path.basename(path) + suffix))

def project_deps(name, kind=None, to=None, local=True):
    """GET /api/project/deps: what points at a project folder (blockers and to-dos, each with its fix), for a move to
    `to` (maintained, archive or abandoned; default the suggestion). Reads only. local=False (a browser that is not
    on this machine, e.g. a phone on a --host 0.0.0.0 bind, or a rebound DNS name): the same hits, redacted."""
    path, kind = project_dir(name, kind)
    suggest = close_suggestion(path); to = to or suggest
    if to not in CLOSE_DEST: raise ValueError("to must be maintained, archive or abandoned")
    with _lock.read():
        dest = close_target(path, to)
        sources = dep_sources(); found = depcheck.check(path, sources, dest)
        too_big = tree_too_big(path)
    if not local: found = redact_deps(found, sources.home)
    old, new = f"{kind}/{name}", os.path.relpath(dest, PROJECTS)
    warn = ({"undo_warning": f"{name} is too big (over {TREE_LIMIT} files and folders) or partly unreadable for the app to check "
                             f"later, so this move can't be undone from here: you'd move it back by hand (from the projects folder, "
                             f"mv {shlex.quote(new)} {shlex.quote(old)})."} if too_big else {})
    return {"name": name, "kind": kind, "path": old, "suggest": suggest, "to": to, "dest": new, **found, **warn}

PRIVATE_HITS = ("process", "crontab")  # their detail is a command line or a crontab line: never sent off this machine

def redact_deps(found, home):
    """The dependency check as a browser off this machine sees it: every hit keeps its kind and fix, but a running
    process's command line and a crontab line (`detail`) are dropped, and the home folder is spelled ~ everywhere, in
    both of its forms: the path (/home/me/…) and Claude Code's memory-folder key (-home-me-…, see depcheck.claude_key)."""
    homes = sorted({h.rstrip(os.sep) for h in (home, os.path.realpath(home)) if h and h != os.sep}, key=len, reverse=True)
    keys = sorted({depcheck.claude_key(h) for h in homes}, key=len, reverse=True)
    def scrub(text):
        for h in homes: text = re.sub(re.escape(h) + r"(?=/|$|[^\w.-])", "~", text)
        for k in keys: text = re.sub(re.escape(k) + r"(?![A-Za-z0-9])", "~", text)
        return text
    def one(hit):
        return {**hit, "where": scrub(hit["where"]), "fix": scrub(hit["fix"]),
                "detail": "" if hit["kind"] in PRIVATE_HITS else scrub(hit["detail"])}
    return {"blockers": [one(h) for h in found["blockers"]], "todos": [one(h) for h in found["todos"]], "redacted": True}

GITHUB_REMOTE = re.compile(r"(?:https://(?:www\.)?github\.com/|git@(?:ssh\.)?github\.com:|ssh://git@(?:ssh\.)?github\.com(?::\d+)?/)([\w.-]+/[\w.-]+?)(?:\.git)?/?")

def github_repo_of(url):
    """'owner/name' for any way git can spell a GitHub remote (https, git@, ssh://, and ssh.github.com:443), else ''."""
    m = GITHUB_REMOTE.fullmatch((url or "").strip())
    if not m or any(not seg.strip(".") for seg in m[1].split("/")): return ""  # "..", "." are not names
    return m[1]

def git_env(path):
    """The environment for git run in a project folder `path`: never prompts; a GIT_DIR or similar inherited from the
    environment is dropped; and GIT_CEILING_DIRECTORIES is the folder's parent, so git only ever reads `path`'s own
    .git: one it can't read (a half-made one) is "not a repo", never the repo of a folder above (projects kept inside
    a dotfiles or ~/Projects repo)."""
    env = {k: v for k, v in os.environ.items() if k not in GIT_ENV_DROP}
    return {**env, "GIT_TERMINAL_PROMPT": "0", "GIT_CEILING_DIRECTORIES": os.path.dirname(os.path.abspath(path))}

def git_out(path, args, timeout=5):
    """stdout of a read-only `git args` in `path` (None if it failed). Like _git: never prompts and reads only the repo
    in `path` (git_env)."""
    try:
        proc = subprocess.run(["git"] + args, cwd=path, capture_output=True, text=True, timeout=timeout, env=git_env(path))
        return proc.stdout.strip() if proc.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired): return None

GIT_TIMEOUT = 30  # seconds for a git step that writes (init, add, commit: the commit runs your hooks, e.g. gitleaks)
NOREPLY_SUFFIX = "@users.noreply.github.com"
GIT_ENV_DROP = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR", "GIT_CEILING_DIRECTORIES")

def _git(path, args, timeout=GIT_TIMEOUT):
    """(returncode, stdout, stderr) of `git args` run in `path`. Never prompts, and always acts on the repo in `path`
    itself, never one inherited from the environment or found in a folder above (git_env)."""
    proc = subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, timeout=timeout, env=git_env(path))
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
    return config_value("noreply_suffix", NOREPLY_SUFFIX, lambda v: isinstance(v, str) and v.startswith("@") and len(v) > 2)

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

NOT_ON_GITHUB_DAYS = 2  # a project with no GitHub origin is flagged this long after Promote, or at its first commit after the skeleton
SKELETON_SUBJECT = re.compile(r"Start .+ \(promoted from RawNotes\)")  # the one commit Promote makes (Tx.gitinit)

def has_skeleton_root(roots):
    """Is one of these root-commit subjects Promote's skeleton commit? The one rule for both after_skeleton (the chip) and
    work_commits (the gate): every other commit, another root (an orphan branch) included, is work after it."""
    return any(SKELETON_SUBJECT.fullmatch(r) for r in roots)

def _promoted_days(path):
    """Days since the project's NOTES.md says it was promoted, None if it doesn't say (or the date is unreadable)."""
    try:
        with open(notes_file(path), "rb") as fh: d = field(to_lf(fh.read(4000).decode("utf-8", "replace")), "promoted")
        return (today() - dt.date.fromisoformat(d)).days
    except (OSError, ValueError): return None

def github_state(path, fresh=False):
    """Is this project on GitHub? git: has a repo; origin: it has an `origin` remote (GitHub or not); repo: 'owner/name' of a GitHub origin; pushed: that remote has
    received something (a remote-tracking ref exists); unpushed: commits on any local branch that no origin ref contains;
    after_skeleton: commits (on any local branch) made after Promote's own first commit (0 when the repo has none, i.e. it predates it);
    not_on_github: no GitHub origin and (after_skeleton > 0 or promoted 2+ days ago): the red chip and the Ship gate.
    The git facts are cached for GIT_TTL seconds (and until the repo's own files change): a few git processes per project add up."""
    state = _git_facts(path, fresh)
    if state["repo"]: state["not_on_github"] = False
    else:
        age = _promoted_days(path)
        state["not_on_github"] = state["after_skeleton"] > 0 or (age is not None and age >= NOT_ON_GITHUB_DAYS)
    return state

def local_revs(path):
    """What "the work in this repo" means to git: every local branch, plus HEAD when it points at a commit (a detached
    HEAD's commits are on no branch). An unborn HEAD with commits on other branches still counts those."""
    return ["--branches"] + (["HEAD"] if git_out(path, ["rev-parse", "--verify", "-q", "HEAD"]) is not None else [])

def current_branch(path):
    """The checked-out branch's name ("" when HEAD is detached or git fails). From the full ref, never --short:
    with a tag of the same name git shortens refs/heads/main to "heads/main"."""
    ref = git_out(path, ["symbolic-ref", "-q", "HEAD"]) or ""
    return ref.removeprefix("refs/heads/") if ref.startswith("refs/heads/") else ""

def _git_facts(path, fresh):
    if not os.path.exists(os.path.join(path, ".git")): return {"git": False, "origin": False, "repo": "", "pushed": False, "unpushed": 0, "after_skeleton": 0}
    fp = _git_fingerprint(path)
    with _git_lock: hit = _git_cache.get(path)
    if hit and not fresh and hit[0] == fp and time.time() - hit[1] < GIT_TTL: return dict(hit[2])
    url = git_out(path, ["remote", "get-url", "origin"]); repo = github_repo_of(url)
    pushed = bool(repo and git_out(path, ["for-each-ref", "--count=1", "refs/remotes/origin"]))
    local = local_revs(path)
    ahead = git_out(path, ["rev-list", "--count", *local, "--not", "--remotes=origin"]) if pushed else None
    after = 0
    if not repo:
        total = git_out(path, ["rev-list", "--count", *local]); roots = (git_out(path, ["log", "--max-parents=0", "--format=%s", *local]) or "").splitlines()
        if total and total.isdigit() and has_skeleton_root(roots): after = int(total) - 1
    state = {"git": True, "origin": url is not None, "repo": repo, "pushed": pushed, "unpushed": int(ahead) if ahead and ahead.isdigit() else 0, "after_skeleton": after}
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
              "pr_status": "No Git repository in this project.", "branch": "", "remote": "", "repo": "", "gh": {"git": False, "origin": False, "repo": "", "pushed": False, "unpushed": 0, "after_skeleton": 0, "not_on_github": False},
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
                                  env={**(gh_env() if args[0] == "gh" else git_env(path)), "GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1"})
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
    env = {k: v for k, v in os.environ.items() if k not in GIT_ENV_DROP}  # gh runs git (repo create --source adds the remote)
    return {**env, "GH_PROMPT_DISABLED": "1", **({"GH_TOKEN": token} if token else {})}

def _gh(args, timeout=25, token=None):
    """Run the gh CLI and return stdout. Signed in as the token saved through the page if there is one (given to gh in
    its environment, never on the command line), otherwise as gh itself. Mostly reads (api GETs and GraphQL queries);
    the one write is create_github_repo's `gh repo create --private`, which only runs from this machine."""
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

# config.json is read in exactly one place (read_config); every setting goes through config_value / config_int, so a
# missing, unreadable or malformed file, or a value of the wrong kind, always means "use the default". Keys:
#   notes, autocommit, noreply_suffix, max_extensions, closed_window_days, stale_open_days, stale_cooking_days,
#   inbox_limit_days, max_keeps.
def read_config():
    """config.json as a dict; {} when it is missing, unreadable, not JSON or not an object."""
    try:
        with open(config_path(), encoding="utf-8") as fh: d = json.load(fh)
    except (OSError, ValueError): return {}
    return d if isinstance(d, dict) else {}

def config_value(key, default, valid=lambda v: True, conf=None):
    """config.json's `key` if it is set and passes `valid`, else `default`. `conf`: an already-read config."""
    v = (read_config() if conf is None else conf).get(key)
    return v if v is not None and valid(v) else default

def config_int(key, default, minimum=0, conf=None):
    """A whole-number setting (not a bool) of at least `minimum`, else `default`."""
    return config_value(key, default, lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= minimum, conf)

def saved_notes_dir():
    """The notes folder chosen in the first-run dialog, or None (nothing saved, or the file is unreadable)."""
    return config_value("notes", None, lambda p: isinstance(p, str) and os.path.isabs(p))

def save_config(**changes):
    """Merge `changes` into config.json (folder 0700, written through a private temp file and renamed into place)."""
    d = read_config()
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


INDEX_HEAD = re.compile(r"^##[ \t]+Index[ \t]*$", re.M)
INDEX_LINK = re.compile(r"\]\(([^)\s]+\.md)\)")

def index_clean(s):
    """Text for a markdown link label or summary: no brackets, no line breaks, single spaces."""
    return re.sub(r"\s+", " ", re.sub(r"[\[\]]", "", str(s or ""))).strip()

def index_section_name(s): return index_clean(s).lstrip("#").strip()

def index_region(lines):
    """(start, end) line numbers of what sits between `## Index` and the next `## ` heading (or the end), or None."""
    start = next((i for i, l in enumerate(lines) if INDEX_HEAD.match(l)), None)
    if start is None: return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return start + 1, end

def index_sections(text):
    """The `### ` headings under `## Index`, in order (empty if there is no index)."""
    lines = text.split("\n"); r = index_region(lines)
    return [l[4:].strip() for l in lines[r[0]:r[1]] if l.startswith("### ")] if r else []

def index_links(text):
    """Every file named as `(file.md)` inside the index."""
    lines = text.split("\n"); r = index_region(lines)
    return set(INDEX_LINK.findall("\n".join(lines[r[0]:r[1]]))) if r else set()

def index_insert(text, section, line):
    """Add `line` (a '- [Title](slug.md): summary' list item) at the end of `### section` under `## Index`, adding the
    section at the end of the index if it is new. Touches nothing outside the index. None if there is no `## Index`,
    or the index already links that file."""
    lines = text.split("\n"); r = index_region(lines)
    m = INDEX_LINK.search(line); section = index_section_name(section)
    if not r or not section or (m and m.group(1) in index_links(text)): return None
    start, end = r
    head = next((i for i in range(start, end) if lines[i].startswith("### ") and lines[i][4:].strip() == section), None)
    if head is None:
        last = max((i for i in range(start, end) if lines[i].strip()), default=start - 1)
        lines[last + 1:last + 1] = [""] * (last >= start) + ["### " + section, line]
    else:
        stop = next((i for i in range(head + 1, end) if lines[i].startswith("### ")), end)
        last = max(i for i in range(head, stop) if i == head or lines[i].strip())
        lines.insert(last + 1, line)
    return "\n".join(lines)

def index_line(title, fname, blurb):
    b = index_clean(blurb)
    return f"- [{index_clean(title) or fname}]({fname})" + (f": {b}" if b else "")

def index_blurb(text, why=""):
    """A default one-line summary: the 'why', else the first sentence of the question."""
    if index_clean(why): return index_clean(why)[:200]
    q = index_clean(question_summary(text))
    return re.split(r"(?<=[.?!])\s", q, 1)[0][:200]

def _frontmatter_free(text): return re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S)

def _readme_ok():
    p = os.path.join(PROJECTS, "reference", "README.md")
    return p if os.path.isfile(p) and not os.path.islink(p) else None

def reference_index(tx, fname, title, text, section, blurb, why=""):
    """Add the index line for reference/<fname> through `tx`. Returns (done, warning); on a warning nothing was changed."""
    p = _readme_ok()
    if not p: return False, "No reference/README.md, so the note was filed but not indexed."
    body, eol = read_note(p)
    new = index_insert(body, section, index_line(title, fname, blurb or index_blurb(text, why)))
    if new is None:
        return False, (f"reference/README.md already links {fname}" if index_region(body.split("\n")) else "reference/README.md has no ## Index") + ", so it was not indexed."
    tx.write(p, new, eol)
    return True, None

def reference_unindexed():
    """Reference notes the index doesn't link, plus the index's section headings."""
    d = os.path.join(PROJECTS, "reference")
    with _lock.read():
        p = _readme_ok(); text = read_note(p)[0] if p else ""
        has = bool(p and index_region(text.split("\n"))); linked = index_links(text); out = []
        for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            fp = os.path.join(d, f)
            if not f.endswith(".md") or f == "README.md" or f in linked or os.path.islink(fp) or not os.path.isfile(fp): continue
            body = read_note(fp)[0]; m = re.search(r"^why:[ \t]*(.+)$", body, re.M); why = ""
            if m:
                try: why = json.loads(m.group(1))
                except ValueError: why = m.group(1)
            out.append({"file": f, "title": title_of(_frontmatter_free(body), f), "blurb": index_blurb(body, why if isinstance(why, str) else "")})
    return {"has_index": has, "sections": index_sections(text), "files": out if has else []}

def reference_index_add(req):
    """Index an already-filed reference note: one undoable step."""
    f = req.get("file"); section = index_section_name(_text(req, "section"))
    if not isinstance(f, str) or "/" in f or "\0" in f or not f.endswith(".md") or f == "README.md": raise ValueError("bad file name")
    if not section: raise ValueError("pick a section")
    fp = os.path.join(PROJECTS, "reference", f)
    if not os.path.isfile(fp) or os.path.islink(fp): raise ValueError("no such reference note")
    with mutation() as tx:
        text, eol = read_note(fp); title = title_of(_frontmatter_free(text), f)
        ok, warn = reference_index(tx, f, title, text, section, _text(req, "blurb"))
        if not ok: raise ValueError(warn)
        if text.startswith("---\n") and "\n---\n" in text:
            fm, rest = text.split("\n---\n", 1)
            if not re.search(r"^section:", fm, re.M): tx.write(fp, f"{fm}\nsection: {json.dumps(section)}\n---\n{rest}", eol)
        tx.label = f"index “{title}”"; tx.log(f"index {f} in {section}")
    return {"ok": True, "msg": f"Added “{title}” to the index under {section}"}

def reference_copy(text, fname, why="", section=""):
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
    if section: fm.append("section: " + json.dumps(section))
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

def clean_slug(text):
    """A folder or file name for Promote/Reference: lowercase letters, digits and single dashes, starting and ending with
    a letter or digit (a leading dash reads as an option to every command that is later given the path). "" if nothing is left."""
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]", "", (text or "").lower())).strip("-")[:80].rstrip("-")  # cut, then no trailing dash

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

TREE_LIMIT = 200_000  # entries; a bigger folder can still move, but its undo can't be checked, so it refuses

def tree_state(path, limit=None):
    """What a folder holds, to tell later whether anything in it changed: every entry's relative path with its size and
    mtime (a link: its target), folders by name only, `.git` contents left out (git rewrites its index on a mere
    `git status`). None if it can't be read or has more than `limit` entries. Never raises."""
    out = {}; limit = TREE_LIMIT if limit is None else limit
    try:
        for root, dirs, files in os.walk(path, onerror=_raise):
            if ".git" in dirs and os.path.isdir(os.path.join(root, ".git")) and not os.path.islink(os.path.join(root, ".git")):
                dirs.remove(".git"); out[os.path.relpath(os.path.join(root, ".git"), path)] = "git"
            for n in dirs + files:
                p = os.path.join(root, n); st = os.lstat(p)
                out[os.path.relpath(p, path)] = (("link", os.readlink(p)) if stat.S_ISLNK(st.st_mode) else "dir" if stat.S_ISDIR(st.st_mode)
                                                 else (st.st_size, st.st_mtime_ns))
                if len(out) > limit: return None
    except OSError: return None
    return out

def _raise(e): raise e

def tree_too_big(path, limit=None):
    """Would tree_state(path) give up (more than `limit` entries, or unreadable)? Counted without a stat per entry, so
    the close-out form can warn, before a move, that its undo won't be possible."""
    n = 0; limit = TREE_LIMIT if limit is None else limit
    try:
        for root, dirs, files in os.walk(path, onerror=_raise):
            if ".git" in dirs and os.path.isdir(os.path.join(root, ".git")) and not os.path.islink(os.path.join(root, ".git")):
                dirs.remove(".git"); n += 1
            n += len(dirs) + len(files)
            if n > limit: return True
    except OSError: return True
    return False

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
    def movedir(self, src, dst):
        """Rename a whole folder (a project closing out). Recorded as ("movedir", src, dst, state): `state` is what the
        folder held right after the move (tree_state), so undo and rollback move it back only while nothing in it was
        added, removed or edited since, and only while the old name is still free. Same filesystem only (a rename,
        never a copy); a symlink, or a symlink on the way, is refused like every other write."""
        src, dst = _stored(src), _stored(dst)
        if os.path.islink(src) or not os.path.isdir(src): raise ValueError(f"{os.path.basename(src)} is not a folder")
        if os.path.lexists(dst): raise FileExistsError(f"{os.path.basename(dst)} already exists")
        os.rename(src, dst)  # EXDEV (another filesystem) fails here, before anything moved
        self.ops.append(("movedir", src, dst, tree_state(dst)))
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
        # The warning goes to whichever browser asked (a phone, on a --host 0.0.0.0 bind) and into triage.log: it names
        # the folder relative to the projects root and never the email address git would use.
        rel = os.path.relpath(path, PROJECTS) if PROJECTS and _within(path, PROJECTS) else name
        how = f"commit by hand in {rel}: git add -A && git commit -m {shlex.quote(message)}"
        suffix = noreply_suffix(); emails = git_identity(path); bad = [e for e in emails if not (e or "").endswith(suffix)]
        if bad:
            why = "git has no email set" if not all(emails) else f"the email git would use is not a {suffix} address"
            return f"{name}: git repo made, but no commit: {why}. Set a {suffix} email in git yourself, then {how}"
        try:
            code, _, err = _git(path, ["add", "-A"])
            if not code: code, _, err = _git(path, ["commit", "-q", "-m", message])
        except (OSError, subprocess.TimeoutExpired) as e: code, err = 1, str(e)
        if code:
            err = _last_line(err).replace(path, rel)
            if PROJECTS: err = err.replace(PROJECTS.rstrip(os.sep) + os.sep, "")
            return f"{name}: git repo made, but the first commit failed ({err}). The files are staged; {how}"
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
    for p in ((op[1], op[2]) if op[0] in ("move", "movedir") else (op[1],)):
        try: _stored(p)
        except ValueError as e: raise ValueError(f"Can't undo: {e}. Nothing was changed; fix {os.path.basename(p)} by hand.") from e

def _plan_inverse(ops, vfs=None):
    """Dry run of reversing `ops` newest first, against the files as they are now: raises ValueError (changing
    nothing) if any file a step touched has been edited, moved or added to since, or no longer lies safely inside the
    notes and projects folders."""
    vfs = {} if vfs is None else vfs; moved = []  # folders a later step moved: their files are read at the new place
    for op in ops: _recheck_contained(op)
    def cur(p):
        if p in vfs: return vfs[p]
        for src, dst in reversed(moved):
            if _within(p, src):
                p = dst + p[len(src):]
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
        elif op[0] == "movedir":
            _check_movedir(op, vfs)
            moved.append((op[1], op[2])); vfs[op[2]] = MISSING
    return vfs

def _check_movedir(op, vfs=None):
    """Raise ValueError unless a moved folder can go back: still a real folder at the new place, holding exactly what
    it held right after the move, and the old name still free."""
    _, src, dst, state = op; name = os.path.basename(dst)
    back = f"move it back by hand if you still want to: mv {shlex.quote(dst)} {shlex.quote(src)}"
    if os.path.islink(dst) or not os.path.isdir(dst): raise ValueError(f"Can't undo: {name} is no longer in {os.path.basename(os.path.dirname(dst))}/. Fix it by hand.")
    if os.path.lexists(src) and (vfs or {}).get(src) is not MISSING:
        raise ValueError(f"Can't undo: {os.path.basename(os.path.dirname(src))}/{os.path.basename(src)} exists again. Nothing was changed; {back}")
    if state is None: raise ValueError(f"Can't undo: {name} is too big (or partly unreadable) to check for changes. Nothing was changed; {back}")
    if tree_state(dst) != state:
        raise ValueError(f"Can't undo: {name} changed since it moved (files added, removed or edited). Nothing was changed; {back}")

def _check_gitinit(path, sha):
    """Raise ValueError unless `path`/.git is still exactly what promote made: no remote (a GitHub repo made since would
    be orphaned), HEAD at its one commit, every ref on that commit, nothing modified or untracked (or, if no commit was
    made, still no commit at all)."""
    gitdir = os.path.join(path, ".git")
    bad = ValueError(f"Can't undo: {os.path.basename(path)}/.git changed since (a new commit, or files added or edited). Fix it by hand.")
    if os.path.islink(gitdir) or not os.path.isdir(gitdir): raise bad
    if not shutil.which("git"): raise ValueError(f"Can't undo: git is not installed, so {os.path.basename(path)}/.git can't be checked. Fix it by hand.")
    try:
        remotes = _git(path, ["remote"])  # a half-made .git (git init failed) can't answer: the checks below decide then
        if not remotes[0] and remotes[1].strip():
            raise ValueError(f"Can't undo: {os.path.basename(path)} has a remote now ({', '.join(remotes[1].split())}), "
                             f"so its repo may be on GitHub. Delete the GitHub repo first (then remove the remote), or keep the project.")
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
    elif op[0] == "movedir":
        if os.path.lexists(op[1]): raise FileExistsError(f"{os.path.basename(op[1])} already exists")
        os.rename(op[2], op[1])
    elif op[0] == "mkdir": os.rmdir(op[1])
    elif op[0] == "gitinit":
        gitdir = _stored(os.path.join(op[1], ".git"))
        if os.path.islink(gitdir) or not os.path.isdir(gitdir): raise OSError(f"{gitdir} is not a folder")
        shutil.rmtree(gitdir)

def _apply_forward(op):
    _recheck_contained(op)
    if op[0] == "write": safefs.atomic_write(op[1], op[3], op[4])
    elif op[0] == "move": os.rename(op[1], op[2])
    elif op[0] == "movedir":
        if os.path.lexists(op[2]): raise FileExistsError(f"{os.path.basename(op[2])} already exists")
        os.rename(op[1], op[2])
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
    _note_commit(autocommit(tx.label or label, tx.ops))  # likewise: a commit that can't happen never fails the change

UNDO_LOCAL_ONLY = ("movedir", "gitinit")  # undoing these moves a project folder back or deletes a repo: this machine only

def undo(local=True):
    """Reverse the newest change. Checked on a dry run first: refuses (and changes nothing) if any file it
    touched has been edited, moved or added to since. If a step fails part-way, what it already reversed is put
    back, so the change stays whole and undoable.
    local=False (a browser not on this machine): a change that moved a project folder or made a repo is refused, like
    the move itself. A folder move back is checked like close out's move: anything pointing at the folder in its new
    place (depcheck) refuses it, with the list."""
    with _lock.write():
        if not _undo: raise ValueError("Nothing to undo.")
        entry = _undo[-1]; ops = entry["ops"]
        if not local and any(op[0] in UNDO_LOCAL_ONLY for op in ops):
            raise ValueError(f"Undoing “{entry['label']}” only works from the machine running the server (it moves a project "
                             f"folder back or removes a repo). Nothing was changed.")
        _plan_inverse(ops)
        for op in reversed(ops):
            if op[0] == "movedir":
                found = depcheck.check(op[2], dep_sources(), op[1])
                if found["blockers"]: raise Blocked(blocked_message(f"Can't move {os.path.basename(op[2])} back", found["blockers"]), found)
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
        res = {"ok": True, "msg": f"Undone: {entry['label']}", "select": entry["select"], "left": len(_undo)}
    _note_commit(autocommit(f"undo {entry['label']}", inverse_ops(ops)))  # undo is a new change with its own commit; history is never rewritten
    if any(op[0] == "movedir" for op in ops): res["sync_apps"] = run_sync_apps()  # a folder moved back: Obsidian links follow
    return res

def inverse_ops(ops):
    """`ops` as the change their undo made, for its commit: a write now went from the bytes the change wrote (op[3]) back
    to the ones it replaced, and a move went the other way. So hand-edit detection compares the committed note with
    what undo started from, not with what the original change started from (which every undo differs from)."""
    out = []
    for op in reversed(ops):
        if op[0] == "write": out.append(("write", op[1], op[3], op[2], op[4]))
        elif op[0] == "move": out.append(("move", op[2], op[1]))
        else: out.append(op)
    return out

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

# ---- auto-commit: after a change succeeds, commit exactly the notes-folder paths it touched (never anything else) ----
AUTOCOMMIT = True  # --no-commit turns it off; so does "autocommit": false in config.json
COMMIT_TIMEOUT = 20  # seconds, for the commit lock and for each git command
_commit_lock = threading.Lock()  # one commit at a time: two changes fired at once never meet on index.lock
_commit_warned = set()  # skip reasons already printed to stderr
_commit_result = threading.local()  # the commit of the change this request thread just made, for the API response

def saved_autocommit():
    """False only when config.json says "autocommit": false."""
    return config_value("autocommit", True) is not False

def _note_commit(res):
    if res is not None: _commit_result.value = res

def _notes_git(args, timeout=COMMIT_TIMEOUT, stdin=None):
    """Run git in the notes folder (literal pathspecs, no prompts, never another repo's GIT_DIR). CompletedProcess."""
    env = {k: v for k, v in os.environ.items() if k not in GIT_ENV_DROP}
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(["git", "--literal-pathspecs"] + args, cwd=NOTES, capture_output=True, timeout=timeout, env=env,
                          input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL)

def _commit_paths(ops):
    """Paths inside the notes folder that `ops` wrote or moved (both sides of a move), relative to it, in order.
    Folders it made are left out: git tracks files, and adding done/ would sweep in everything else there. So is a
    project's own repo ("gitinit": promote makes its first commit itself) and anything outside the notes folder:
    project folders and ../reference (its note and README.md index line) are never committed here."""
    root = os.path.realpath(NOTES); out = []; first = {}
    for op in ops:
        if op[0] not in ("write", "move"): continue  # mkdir, gitinit
        for p in (op[1], op[2]) if op[0] == "move" else (op[1],):
            p = os.path.realpath(p)  # Tx paths keep the notes folder's own spelling (a symlink in it, or above it): compare like with like
            if not _within(p, root) or p == root: continue
            rel = os.path.relpath(p, root)
            if rel not in out: out.append(rel); first[rel] = op if op[0] == "write" else None
    return out, first

def _hand_edited(rel, op):
    """True if a tracked note already differed from its last commit before this change wrote it."""
    if op is None or op[2] is None: return False
    head = _notes_git(["rev-parse", "-q", "--verify", f"HEAD:./{rel}"])  # ./: from the notes folder, which may sit inside a bigger repo
    if head.returncode != 0: return False
    mine = _notes_git(["hash-object", "--stdin"], stdin=op[2])
    return mine.returncode == 0 and mine.stdout.strip() != head.stdout.strip()

def _hook_name():
    """The pre-commit hook git will run here (honours core.hooksPath), or ''."""
    r = _notes_git(["rev-parse", "--git-path", "hooks/pre-commit"])
    if r.returncode != 0: return ""
    p = r.stdout.decode(errors="replace").strip()
    p = p if os.path.isabs(p) else os.path.join(NOTES, p)
    return p if os.path.isfile(p) and os.access(p, os.X_OK) else ""

def autocommit(label, ops):
    """Commit the notes-folder paths `ops` touched, with message "triage: <label>". Returns None when the change
    touched nothing in the notes folder, else {"ok", "sha"?, "skipped"?, "error"?}. Never raises: the change has
    already happened and stays, whatever git says (and it stays undoable). Never --no-verify, never amends."""
    paths, first = _commit_paths(ops)
    if not paths: return None
    if not AUTOCOMMIT or not saved_autocommit(): return {"ok": True, "skipped": "auto-commit is off"}
    try: res = _autocommit(label, paths, first)
    except subprocess.TimeoutExpired: res = {"ok": False, "error": f"git took longer than {COMMIT_TIMEOUT} s"}
    except FileNotFoundError: res = {"ok": False, "skipped": "git is not installed"}
    except Exception as e: res = {"ok": False, "error": str(e) or type(e).__name__}
    if not res["ok"] and (res.get("error") or res["skipped"] not in _commit_warned):  # a skip reason is said once per run
        _commit_warned.add(res.get("skipped"))
        print(f"rawtriage-web: not committed ({res.get('error') or res.get('skipped')}); the change itself stands", file=sys.stderr)
    return res

def _autocommit(label, paths, first):
    if not _commit_lock.acquire(timeout=COMMIT_TIMEOUT): return {"ok": False, "skipped": "another commit is still running"}
    try:
        r = _notes_git(["rev-parse", "--absolute-git-dir"])
        if r.returncode != 0: return {"ok": False, "skipped": "the notes folder is not a git repo"}
        gd = r.stdout.decode(errors="replace").strip()
        for name, why in (("MERGE_HEAD", "a merge is in progress"), ("rebase-merge", "a rebase is in progress"),
                          ("rebase-apply", "a rebase is in progress"), ("CHERRY_PICK_HEAD", "a cherry-pick is in progress"),
                          ("index.lock", "git's index.lock is present (another git command running, or a stale lock)")):
            if os.path.exists(os.path.join(gd, name)): return {"ok": False, "skipped": why}
        if not _notes_git(["config", "user.email"]).stdout.strip(): return {"ok": False, "skipped": "git user.email is not set"}
        # a path that is gone from disk and was never tracked (an untracked note moved to done/) has nothing to record
        tracked = set(_notes_git(["ls-files", "-z", "--"] + paths).stdout.decode(errors="surrogateescape").split("\0"))
        paths = [p for p in paths if p in tracked or os.path.lexists(os.path.join(NOTES, p))]
        if not paths: return {"ok": True, "skipped": "nothing to commit"}
        edited = [p for p in paths if _hand_edited(p, first.get(p))]
        r = _notes_git(["add", "-A", "--"] + paths)
        if r.returncode != 0: return {"ok": False, "error": "git add failed: " + _tail(r.stderr)}
        if _notes_git(["rev-parse", "-q", "--verify", "HEAD"]).returncode == 0 and _notes_git(["diff", "--cached", "--quiet", "HEAD", "--"] + paths).returncode == 0:
            return {"ok": True, "skipped": "nothing to commit"}
        body = "\n".join(paths)
        if edited: body += "\n\nAlso includes edits made by hand before this change: " + ", ".join(edited)
        body += f"\n\nvia rawnotes-triage {VERSION}"
        r = _notes_git(["commit", "-q", "-m", f"triage: {label}", "-m", body, "--"] + paths)
        if r.returncode != 0:
            _notes_git(["reset", "-q", "--"] + paths)  # leave the index as it was; the files keep the change
            hook = _hook_name(); out = _tail(r.stderr + r.stdout)
            return {"ok": False, "error": (f"the pre-commit hook ({hook}) refused the commit" if hook else "git commit failed") + (f": {out}" if out else "")}
        sha = _notes_git(["rev-parse", "--short", "HEAD"]).stdout.decode().strip()
        return {"ok": True, "sha": sha}
    finally: _commit_lock.release()

def _tail(b, n=300):
    s = " ".join((b.decode("utf-8", "replace") if isinstance(b, bytes) else b or "").split())
    return s if len(s) <= n else "…" + s[-n:]

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
    slug = clean_slug(_text(req, "slug")) or clean_slug(slug_of(fname)) or "project"
    dest_dir = os.path.join(PROJECTS, dest)
    tx.ensure_dir(dest_dir); proj = None
    if action == "promote" and DESTS[dest] == "folder":
        proj = unique(os.path.join(dest_dir, slug)); tx.mkdir(proj)
        target = os.path.join(proj, "NOTES.md")
    else:
        target = unique(os.path.join(dest_dir, slug + ".md"))
    copy = set_field(text, "status", "promoted")
    copy = set_field(copy, "promoted", t)
    warning = ""
    if action == "reference":
        section = index_section_name(_text(req, "section"))
        if section:
            done_idx, warning = reference_index(tx, os.path.basename(target), title, text, section, _text(req, "blurb"), note)
            if not done_idx: section = ""
        copy = reference_copy(copy, fname, note, section)
    if action == "promote":
        copy = set_field(copy, "due", due) if due else drop_field(copy, "due")  # open-ended: drop any old ship-by
        copy = copy.rstrip("\n") + (
            f"\n\n## Plan\n\n- promoted from RawNotes on {t}, " + (f"due {due}\n" if due else "open-ended experiment (no ship-by date)\n") +
            f"- definition of done: {note}\n"
            "- first step:\n" + ("- ship / stop check-in: on the due date, either ship it or kill it\n" if due else
                                 "- check-in: when done means is met, mark it shipped; if it stalls, kill it\n"))
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
        warns = [w for w in (_act({**req, "file": f, "slug": ""}, tx).get("warning") for f in files) if w]
        verb = {"keep": "Kept", "kill": "Killed", "reference": "Filed"}[ALIASES.get(req["action"], req["action"])]
        tx.label = f"{verb.lower()} {len(files)} notes"; tx.select = files[0]
    return {"ok": True, "msg": f"{verb} {len(files)} notes", **({"warning": warns[0]} if warns else {})}

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
        slug = re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", title.lower())).strip("-")[:60].rstrip("-") or "merged"
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
    """Bring a note back from done/ into the Inbox (status cooking) with a fresh start: `restored: <today>` restarts the
    Inbox limit, and its Keep count (`keeps:`, `kept:`) is cleared, so it gets the full 30 days and 3 Keeps again."""
    with mutation() as tx:
        src = safe_note(req.get("file", ""), write=True)
        if os.path.dirname(src) != os.path.join(os.path.normpath(NOTES), "done"): raise ValueError("only Archive notes can be restored")
        dst = os.path.join(NOTES, os.path.basename(src))
        if os.path.lexists(dst): raise ValueError("a note with that name is already in the Inbox")
        text, eol = read_note(src); title = title_of(text, os.path.basename(src))
        text = drop_field(drop_field(set_field(set_field(text, "status", "cooking"), "restored", today().isoformat()), "keeps"), "kept")
        text = append_outcome(text, f"**Restored {today().isoformat()}** to the Inbox")
        tx.write(src, text, eol); tx.move(src, dst)
        tx.label = f"restore “{title}”"; tx.select = "done/" + os.path.basename(src)
    _log(f"restore {os.path.basename(src)}")
    return {"ok": True, "msg": f"Restored “{title}” to the Inbox", "file": os.path.basename(dst)}

SHIP_NEEDS_GITHUB = ("active", "templates")  # shipped means it's on GitHub (experiments and learning may ship without)

def work_commits(path):
    """Commits in a folder's repo beyond Promote's skeleton commit (every commit when no root commit is the skeleton),
    across every local branch (local_revs). 0 with no repo or no commits yet. A repo git can't read counts as work (1),
    so the gate fails closed."""
    if not os.path.exists(os.path.join(path, ".git")): return 0
    local = local_revs(path)
    total = git_out(path, ["rev-list", "--count", *local])
    if not total or not total.isdigit(): return 1
    if total == "0": return 0  # an unborn branch and no other: nothing committed
    roots = git_out(path, ["log", "--max-parents=0", "--format=%s", *local])
    if roots is None: return 1
    return int(total) - (1 if has_skeleton_root(roots.splitlines()) else 0)

def require_github_to_close(path, kind, outcome):
    """The GitHub gate at close out, for active/templates folders (git facts read fresh). The work has to be on GitHub,
    not just have an origin: maintained needs a GitHub origin that has received a push (`pushed`) and no commit on any
    local branch missing from it (`unpushed` 0). archive is gated only when the repo has work in it (work_commits > 0),
    and then needs the same. No git, or only the skeleton commit, archives freely; next and kill (abandoned) are never
    gated."""
    if kind not in SHIP_NEEDS_GITHUB or outcome not in ("maintained", "archive"): return
    if outcome == "archive":
        n = work_commits(path)
        if not n: return
    st = github_state(path, True); name = os.path.basename(path)
    how = f"(Create private GitHub repo, or `rawtriage project github {name}`)"
    if not st["repo"]:
        if outcome == "maintained":
            raise ValueError(f"Push it first: maintained means it's on GitHub. Create a private repo for {name} {how}, then close it out again.")
        raise ValueError(f"Push it first: {name} has {n} commit{'s' if n > 1 else ''} that {'are' if n > 1 else 'is'} not on GitHub, "
                         f"and archive must not strand them. Create a private repo {how}, then archive it, or kill it instead.")
    if not st["pushed"]:
        raise ValueError(f"Push it first: {name} has a GitHub origin ({st['repo']}) but nothing was ever pushed to it. "
                         f"Run `git push -u origin main` in the folder, then close it out again.")
    u, branches = unpushed_work(path)
    if u is None:
        raise ValueError(f"Push it first: couldn't check with git whether every local branch of {name} is on GitHub ({st['repo']}), so "
                         f"nothing was moved. Look with `git log --branches --not --remotes=origin` in the folder, then close it out again.")
    if u:
        current = current_branch(path)
        others = [b for b, _ in branches if b != current and b != "HEAD"]
        raise ValueError(f"Push it first: {u} commit{'s' if u > 1 else ''} on {name}'s local branches {'are' if u > 1 else 'is'} not on "
                         f"GitHub ({st['repo']}): " + ", ".join(f"{b} ({n})" for b, n in branches) + ". Push each branch ("
                         + ", ".join(f"`git push origin {b}`" for b, _ in branches if b != "HEAD")
                         + ("; a detached HEAD needs a branch first: `git switch -c <name>`" if any(b == "HEAD" for b, _ in branches) else "")
                         + ")" + (", or, for a branch already merged on GitHub (a squash or rebase merge leaves its own commits "
                                  "behind), delete it instead (" + ", ".join(f"`git branch -D {b}`" for b in others) + ")" if others else "")
                         + ", then close it out again.")

def unpushed_work(path):
    """(commits on local branches, or a detached HEAD, that no origin ref contains; [(branch, count), ...]) for the close
    out gate. (None, None) when git can't say (fails, times out): the gate then refuses, it never guesses 0."""
    heads = git_out(path, ["for-each-ref", "--format=%(refname)", "refs/heads"])
    total = git_out(path, ["rev-list", "--count", *local_revs(path), "--not", "--remotes=origin"])
    if heads is None or total is None or not total.isdigit(): return None, None
    revs = [(ref.removeprefix("refs/heads/"), ref) for ref in heads.splitlines() if ref]  # full ref as the rev: a tag
    # with a branch's name makes the short form "heads/<b>", and refs/heads/heads/<b> doesn't exist
    if git_out(path, ["symbolic-ref", "-q", "HEAD"]) is None and git_out(path, ["rev-parse", "--verify", "-q", "HEAD"]) is not None:
        revs.append(("HEAD", "HEAD"))  # detached
    out = []
    for b, rev in revs:
        n = git_out(path, ["rev-list", "--count", rev, "--not", "--remotes=origin"])
        if n is None or not n.isdigit(): return None, None
        if int(n): out.append((b, int(n)))
    return int(total), out

def times(n): return {1: "once", 2: "twice"}.get(n, f"{n} times")

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
        limit = config_int("max_extensions", MAX_EXTENSIONS)
        if done >= limit: raise ValueError(f"Pushed {times(limit)} already: ship it or kill it.")
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

def project_status(req, local=True):
    """On a promoted project file (a due: line, or an open-ended experiment): mark it shipped, push the ship-by
    date (not on open-ended experiments), or kill it.
    Shipping or killing a project folder on the board ({name, kind}, or a path inside it) is a close out (close_project):
    it needs an outcome, and may move the folder. A folder has no ship-by to push unless it has a promoted file."""
    folder = _folder_of(req) if req.get("action") in ("shipped", "kill") else None
    if folder: return close_project(req, folder, local)
    if req.get("outcome"): raise ValueError("Only a project folder can be closed out; this project is a single file.")
    if not req.get("path"):  # a folder with no promoted file: shipped and kill went to close out above; nothing else applies
        project_dir(req.get("name", ""), req.get("kind"))
        if req.get("action") == "extend": raise ValueError("This project folder has no ship-by date (no due: line) to push.")
        raise ValueError("unknown project action")
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

# ---- close out: ship a project folder into another round, maintained/ or archive/, or kill it into archive/ ----
OUTCOMES = ("next", "maintained", "archive")
SYNC_TIMEOUT = 60  # seconds sync-apps may take

class Blocked(ValueError):
    """A move refused because something points at the folder. `payload` ({blockers, todos}) goes into the 400 answer."""
    def __init__(self, msg, payload): super().__init__(msg); self.payload = payload

def _folder_of(req):
    """(folder, kind, project file or None) when a status request is about a project folder on the board: {name, kind},
    or a path `<kind>/<folder>/<file>.md`. None for a single-file project, or one outside the board's folders."""
    rel = req.get("path")
    if not rel: return (*project_dir(req.get("name", ""), req.get("kind")), None)
    parts = rel.split("/") if isinstance(rel, str) else []
    if len(parts) != 3 or parts[0] not in PROJECT_KINDS: return None
    try: path, kind = project_dir(parts[1], parts[0])
    except ValueError: return None
    return path, kind, os.path.join(path, parts[2])

def close_project(req, folder=None, local=True):
    """POST /api/project/close (and ship/kill of a project folder through /api/project/status). One undo step.
    outcome next: another round in place (new done means and ship-by, the finished round under ## Rounds).
    maintained / archive: shipped, then the folder moves to maintained/<slug> or archive/<slug>.
    action kill (reason required): dead, then the folder moves to archive/<slug>_abandoned.
    A move first runs the dependency check inside the change: any blocker refuses it (400, nothing changed); to-dos
    are written under ## Moved in its notes and returned. After a move, sync-apps runs (best effort).
    local=False (a browser not on this machine): a move is refused before anything is read; next round still works."""
    path, kind, file = folder or _folder_of(req) or (None, None, None)
    if not path: raise ValueError("unknown project")
    path, kind = project_dir(os.path.basename(path), kind, write=True)  # a symlinked folder is refused here
    name = os.path.basename(path); action = req.get("action") or "shipped"
    if action not in ("shipped", "kill"): raise ValueError("unknown project action")
    outcome = "abandoned" if action == "kill" else req.get("outcome")
    if outcome not in OUTCOMES + ("abandoned",):
        raise ValueError(f"What happens to {name} now? Choose next (another round, stays in {kind}/), maintained or archive "
                         f"(suggested: {close_suggestion(path)}).")
    if outcome != "next" and not local:
        raise ValueError(f"Moving {name} out of {kind}/ only works from the machine running the server (a move can stop "
                         f"services and leave things behind; check What points here there).")
    done = due = None
    if outcome == "next":
        done = " ".join(_text(req, "done").split())
        if done_placeholder(done): raise ValueError("Say what done means for the next round.")
        if len(done) > DONE_MAX: raise ValueError(f"Keep done means under {DONE_MAX} characters.")
        due = ship_by(req) if req.get("due") or req.get("due_days") else None
        if not due and kind != "experiments": raise ValueError("Give the next round a ship-by date.")
    res = {"ok": True}
    with mutation() as tx:
        path, kind = project_dir(name, kind, write=True)  # again under the lock: it may have moved since
        if file and os.path.relpath(file, PROJECTS) not in {x["path"] for x in inflight()}: raise ValueError("unknown project file")
        target = file or notes_file(path)
        require_github_to_close(path, kind, outcome)  # here, under the lock: every ship/close of a folder passes this way
        if outcome == "next": res["msg"] = _next_round(tx, path, kind, name, target, done, due)
        else: res.update(_close_and_move(tx, req, path, kind, name, target, action, outcome))
    if res.get("moved_to"): res["sync_apps"] = run_sync_apps()
    return res

def _next_round(tx, path, kind, name, target, done, due):
    t = today().isoformat(); old_done = definition_of_done(path)["text"]
    dfile, dlines, deol = _with_done(path, name, done, find_done(path))
    if dfile == target: text, eol = "\n".join(dlines), deol
    else:
        tx.write(dfile, deol.join(dlines))
        text, eol = read_note(target) if os.path.exists(target) else (f"# {name}\n", "\n")
    promoted, old_due, ext = field(text, "promoted"), field(text, "due"), _count(field(text, "extensions"))
    text = _status_edit(text, {"action": "shipped"}, name)[0]  # the same checks and fields as any ship, then reopened
    text = set_field(set_field(set_field(text, "status", "promoted"), "promoted", t), "extensions", "0")
    text = drop_field(drop_field(text, "closed"), "shipped")
    text = set_field(text, "due", due) if due else drop_field(text, "due")
    clock = f" (ship by {old_due}" + (f", pushed {ext}×" if ext else "") + ")" if old_due else ""
    text = append_to_section(text, "Rounds", f"- {promoted or '?'} → shipped {t}{clock}. Done means: {old_done or '(none written)'}")
    tx.write(target, text, eol)
    tx.label = f"next round of {name}"; tx.log(f"project next {kind}/{name} due {due or 'open-ended'}")
    return f"Shipped {name} 🎉 Next round" + (f" ships by {due}" if due else " is open-ended")

def _moved_lines(t, old, new, todos):
    out = [f"- {t}: moved from `{old}/` to `{new}/`" + (". Left to do by hand:" if todos else ".")]
    for x in todos: out.append(f"  - {x['fix']}" if x["kind"] == "t3" else f"  - {x['detail']}: `{x['fix']}`")
    return "\n".join(out)

def blocked_message(head, blockers):
    n = len(blockers)
    return (f"{head}: {n} {'things point' if n > 1 else 'thing points'} at it. Fix {'each' if n > 1 else 'it'}, then try again:\n"
            + "\n".join(f"- {b['where']}: {(b['detail'].splitlines() or [''])[0]} → {b['fix']}" for b in blockers))

def _close_and_move(tx, req, path, kind, name, target, action, outcome):
    text, eol = read_note(target) if os.path.exists(target) else (f"# {name}\n", "\n")
    text, label, msg = _status_edit(text, {**req, "action": action}, name)
    dest = close_target(path, outcome)
    found = depcheck.check(path, dep_sources(), dest)
    blockers, todos = found["blockers"], found["todos"]
    if blockers: raise Blocked(blocked_message(f"Can't move {name}", blockers), found)
    old, new = f"{kind}/{name}", os.path.relpath(dest, PROJECTS)
    text = append_to_section(text, "Moved", _moved_lines(today().isoformat(), old, new, todos))
    tx.write(target, text, eol)
    tx.ensure_dir(os.path.dirname(dest))
    tx.movedir(path, dest)
    tx.label = f"{'kill' if action == 'kill' else outcome} {name}"
    tx.log(f"project close {old} -> {new}")
    return {"msg": f"{msg} → {new}", "moved_to": new, "todos": todos}

def run_sync_apps():
    """Re-run obsidian-sync-apps after a folder moved (or moved back), so Obsidian's Apps/ links follow it. Best effort:
    "ran", "failed" or "not installed"; its output goes to stderr only, and nothing here can fail the change."""
    exe = shutil.which(SYNC_APPS) if SYNC_APPS else None
    if not exe: return "not installed"
    try: p = subprocess.run([exe], capture_output=True, text=True, timeout=SYNC_TIMEOUT, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"rawtriage-web: sync-apps failed ({e}); the move itself stands", file=sys.stderr); return "failed"
    out = (p.stdout + p.stderr).strip()
    if out: print(f"rawtriage-web: sync-apps: {out[-2000:]}", file=sys.stderr)
    if p.returncode:
        print(f"rawtriage-web: sync-apps exited {p.returncode}; the move itself stands", file=sys.stderr); return "failed"
    return "ran"

_gh_create_lock = threading.Lock()

def create_github_repo(req):
    """First push, for a project folder with a local repo and no origin: `gh repo create <login>/<folder> --private
    --source <path> --remote origin`, then `git push -u origin main`. Always private (going public is gopublic's job);
    the push has ALLOW_PUSH_MAIN=1 in its own environment only (the global hook allows a first push to main with it);
    never --no-verify. Refuses an existing origin, a dirty tree, a branch other than main or no gh sign-in.
    The checks and `gh repo create` (which adds the remote) hold the read side of the lock: an undo of the promote or a
    close out sent meanwhile waits, then finds the remote (undo refuses; close out wants the push). The push runs after."""
    path, kind = project_dir(req.get("name", ""), req.get("kind"), write=True); name = os.path.basename(path)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or not name.strip("."): raise ValueError(f"“{name}” is not a name GitHub accepts for a repo.")
    if not shutil.which("git"): raise ValueError("git is not installed.")
    if not _gh_create_lock.acquire(blocking=False): raise ValueError("A GitHub repo is already being created.")
    try:
        with _lock.read():
            path, kind = project_dir(name, kind, write=True)  # again under the lock: it may have moved or been undone since
            if not os.path.exists(os.path.join(path, ".git")): raise ValueError("This project has no git repo yet.")
            if "origin" in _git(path, ["remote"])[1].split(): raise ValueError("It already has an origin remote.")
            if current_branch(path) != "main": raise ValueError("Switch to the main branch first: the first push is `main`.")
            if _git(path, ["rev-parse", "--verify", "-q", "HEAD"])[0]: raise ValueError("Make a first commit before creating the repo.")
            if _git(path, ["status", "--porcelain"])[1]: raise ValueError("Commit or stash your changes first: the working tree isn't clean.")
            st = github_status(True)
            if st["state"] == "signed_out": raise ValueError("Not signed in to GitHub: run `gh auth login`." + (f" ({st['error']})" if st.get("error") else ""))
            if st["state"] != "signed_in": raise ValueError(st.get("error") or "GitHub isn't reachable through gh right now.")
            slug = f"{st['login']}/{name}"
            try: _gh(["repo", "create", slug, "--private", "--source", path, "--remote", "origin"], timeout=60)
            except RuntimeError as e: raise ValueError(f"gh repo create failed: {e}")
        try:
            proc = subprocess.run(["git", "push", "-u", "origin", "refs/heads/main"], cwd=path, capture_output=True, text=True, timeout=120,
                                  env={**git_env(path), "ALLOW_PUSH_MAIN": "1"})
            code, err = proc.returncode, proc.stderr or proc.stdout
        except (OSError, subprocess.TimeoutExpired) as e: code, err = 1, str(e)
        if code:
            raise ValueError(f"Created private repo {slug}, but the push failed: {_last_line(err)}. Fix that, then run `git push -u origin main` in the folder.")
    finally: _gh_create_lock.release()
    _git_cache.pop(path, None)
    _log(f"github {kind}/{name} -> {slug} (private)")
    return {"ok": True, "repo": slug, "msg": f"Created private repo {slug} and pushed main"}

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

# ---- for the rawtriage command (bin/rawtriage): header check and "new note" ----
HEADER_SKIP = ("README.md", "SETUP.md", "CLAUDE.md", "AGENTS.md", "TRIAGE.md", "_template.md")  # same list as RawNotes/.claude/hooks/check-frontmatter.sh
KEBAB_NOTE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}-[a-z0-9-]+\.md")
SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")

def header_issues(fname, text):
    """What is missing or invalid in one Inbox note's header: the same rules, in the same words, as check-frontmatter.sh
    (which reads the first line that looks like each field, anywhere in the file; so does this)."""
    def first(pattern):
        m = re.search(pattern, text, re.M)
        return m.group(1) if m else ""
    issues = []
    if not KEBAB_NOTE.fullmatch(fname): issues.append("filename is not YYYY-MM-DD-kebab-topic.md")
    title = first(r"^# *(.*)$"); date = first(r"^- date: *([0-9-]*)"); status = first(r"^- status: *([a-z]*)"); typ = first(r"^- type: *([a-z]*)")
    if not title or title == "<topic>": issues.append("title")
    if not date: issues.append("date")
    elif re.match(r"[0-9]{4}-[0-9]{2}-[0-9]{2}-", fname) and date != fname[:10]: issues.append(f"date ({date}) doesn't match filename ({fname[:10]})")
    if status not in ("open", "cooking", "promoted", "dead"): issues.append(f"status '{status}' not open|cooking|promoted|dead" if status else "status")
    if typ not in ("question", "research", "issue"): issues.append(f"type '{typ}' not question|research|issue" if typ else "type")
    if not re.search(r"^## What's the question / problem\?", text, re.M): issues.append("'What's the question / problem?' section")
    return issues

def check_headers():
    """Inbox notes whose header is missing or invalid (GET /api/check)."""
    with _lock.read():
        try: names = sorted(f for f in os.listdir(NOTES) if f.endswith(".md") and not f.startswith(".") and f not in HEADER_SKIP)
        except OSError: names = []
        root = os.path.realpath(NOTES); checked = 0; problems = []
        for f in names:
            p = os.path.join(NOTES, f)
            try:
                if not os.path.isfile(p) or (os.path.islink(p) and not _within(os.path.realpath(p), root)): continue
                with open(p, "rb") as fh: text = to_lf(fh.read().decode("utf-8", "replace"))
            except OSError: continue
            checked += 1; issues = header_issues(f, text)
            if issues: problems.append({"file": f, "issues": issues})
    return {"checked": checked, "problems": problems}

def new_note(req):
    """Create today's note from the folder's _template.md (POST /api/new), as one undoable step. `thought` goes under
    "What's the question / problem?". Refuses a name that already exists."""
    slug = _text(req, "slug").lower(); thought = _text(req, "thought")
    if not slug or len(slug) > 60 or not SLUG_RE.fullmatch(slug): raise ValueError("slug must be short kebab-case: lowercase letters, digits and dashes")
    if not os.path.isdir(NOTES): raise ValueError("the notes folder doesn't exist yet; open the page to set it up")
    t = today().isoformat(); fname = f"{t}-{slug}.md"; path = os.path.join(NOTES, fname)
    with mutation(f"new note {fname}") as tx:
        if os.path.lexists(path): raise ValueError(f"{fname} already exists")
        tpl = os.path.join(NOTES, "_template.md")
        text, eol = read_note(tpl) if os.path.isfile(tpl) else read_note(TEMPLATE)
        text = "\n".join(re.sub("YYYY-MM-DD", t, re.sub("<topic>", slug.replace("-", " "), l, count=1), count=1) for l in text.split("\n"))
        if thought:
            ph = re.compile(r"^<one or two sentences[^\n]*>[ \t]*$", re.M)
            if ph.search(text): text = ph.sub(lambda m: thought, text, count=1)
            elif re.search(r"^## What's the question / problem\?[ \t]*$", text, re.M):
                text = re.sub(r"(^## What's the question / problem\?[ \t]*\n)", lambda m: m.group(1) + "\n" + thought + "\n", text, count=1, flags=re.M)
            else: text = text.rstrip("\n") + "\n\n" + thought + "\n"
        tx.write(path, text, eol); tx.select = fname; tx.log(f"new {fname}")
    return {"ok": True, "msg": f"Created {fname}", "file": fname, "path": path}

MAX_BODY = 1 << 20

class Server(ThreadingHTTPServer):
    request_queue_size = 128  # the default backlog of 5 drops connections when a page fires many requests at once
    daemon_threads = True

class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        made = _commit_result.__dict__.pop("value", None)  # the auto-commit of the change this request just made
        if made is not None and code == 200 and isinstance(body, dict): body = {**body, "commit": made}
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
        self._send(code, {"error": str(e.args[0]) if isinstance(e, KeyError) and e.args else str(e) or type(e).__name__, **getattr(e, "payload", {})})
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
            if u.path == "/api/project/deps": return self._send(200, project_deps(q.get("name", [""])[0], q.get("kind", [""])[0], q.get("to", [""])[0], self._this_machine()))
            if u.path == "/api/github": return self._send(200, github_status(q.get("refresh") == ["1"]))
            if u.path == "/api/activity": return self._send(200, github_activity(q.get("refresh") == ["1"]))
            if u.path == "/api/repo": return self._send(200, repo_overview(q.get("name", [""])[0]))
            if u.path == "/api/repo/path": return self._send(200, repo_path(q.get("name", [""])[0], q.get("path", [""])[0]))
            if u.path == "/api/setup": return self._send(200, setup_state())
            if u.path == "/api/notes": return self._send(200, list_notes())
            if u.path == "/api/reference/unindexed": return self._send(200, reference_unindexed())
            if u.path == "/api/stats": return self._send(200, stats())
            if u.path == "/api/check": return self._send(200, check_headers())
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
        _commit_result.__dict__.pop("value", None)
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
            if path == "/api/reference/index": return self._send(200, reference_index_add(req))
            if path == "/api/merge": return self._send(200, merge(req))
            if path == "/api/restore": return self._send(200, restore(req))
            if path == "/api/new": return self._send(200, new_note(req))
            if path == "/api/project/status": return self._send(200, project_status(req, self._this_machine()))
            if path == "/api/project/close": return self._send(200, close_project(req, local=self._this_machine()))
            if path == "/api/project/github":
                if not self._this_machine(): raise ValueError("The GitHub repo can only be created from the machine running the server.")
                return self._send(200, create_github_repo(req))
            if path in ("/api/github/token", "/api/github/token/remove"):
                if not self._this_machine(): raise ValueError("The GitHub sign-in can only be changed from the machine running the server.")
                return self._send(200, set_github_token(req) if path == "/api/github/token" else remove_github_token())
            if path == "/api/setup":
                if not self._this_machine(): raise ValueError("The notes folder can only be set up from the machine running the server.")
                return self._send(200, setup_notes(req))
            if path == "/api/undo": return self._send(200, undo(self._this_machine()))
            if path == "/api/done": return self._send(200, set_done(req.get("name", ""), req.get("text", ""), req.get("expected", ""), req.get("kind")))
            if path == "/api/open":
                if not self._this_machine(): raise ValueError("Open only works from the machine running the server.")
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
    ap.add_argument("--no-commit", action="store_true", help="don't commit the notes folder after each change (also: \"autocommit\": false in config.json)")
    a = ap.parse_args(); a.no_browser = a.no_browser or a.service; BIND_HOST = a.host; NOTES_SOURCE = "flag" if a.notes else "config" if saved_notes_dir() else "default"
    NOTES = os.path.abspath(os.path.expanduser(a.notes or saved_notes_dir() or DEFAULT_NOTES)); PROJECTS = os.path.dirname(NOTES)
    LOG_PATH = os.path.abspath(os.path.expanduser(a.log))
    AUTOCOMMIT = not a.no_commit
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
