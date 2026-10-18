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
import argparse, base64, json, os, re, shutil, subprocess, threading, time, urllib.parse, datetime as dt
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

VERSION = "1.14"  # bump on every change; history in CHANGELOG.md
HERE = os.path.dirname(os.path.abspath(__file__))
NOTES = os.path.expanduser("~/Projects/RawNotes")
PROJECTS = None
BIND_HOST = "127.0.0.1"  # set from --host; the Host/Origin guard depends on it
_done_lock = threading.Lock()
STARTED = dt.datetime.now().isoformat(timespec="seconds")
DUE_DAYS = 7
ACTIVITY_TTL = 600  # seconds; GitHub contribution data barely changes minute to minute
ACTIVITY_REPO_DAYS = 30
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
        try:
            with open(os.path.join(path, files[name]), encoding="utf-8", errors="replace") as fh: lines = fh.read(1000000).split("\n")
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
            text = re.sub(r"\s+", " ", text)[:DONE_MAX]
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

def set_done(name, text, expected):
    with _done_lock: return _set_done(name, text, expected)

def _read_lines(target):
    """(lines, eol): the file split on its own line ending, undecodable bytes kept as surrogates."""
    with open(target, encoding="utf-8", errors="surrogateescape", newline="") as fh: raw = fh.read()
    eol = "\r\n" if "\r\n" in raw else "\n"
    return raw.split(eol), eol

def _set_done(name, text, expected):
    """Write a project's definition of done in place (or into NOTES.md's Plan if it has none).
    `expected` is the text the user was looking at; if the file moved on since, refuse rather than overwrite."""
    if name not in {p["name"] for p in active_projects()}: raise ValueError("unknown project")
    text = re.sub(r"\s+", " ", text or "").strip()
    if done_placeholder(text): raise ValueError("Write what done looks like first.")
    if len(text) > DONE_MAX: raise ValueError(f"Keep it under {DONE_MAX} characters.")
    path = os.path.join(PROJECTS, "active", name); hit = find_done(path)
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
        names = {f.lower(): f for f in os.listdir(path)}; target = os.path.join(path, names.get("notes.md", "NOTES.md"))
        item = "- definition of done: " + text
        if os.path.exists(target):
            lines, eol = _read_lines(target)
            plan = next((i for i, l in enumerate(lines) if re.match(r"^##\s+Plan\s*$", l)), None)
            if plan is None:
                while lines and not lines[-1].strip(): lines.pop()
                lines += ["", "## Plan", "", item, ""]
            else: lines.insert(plan + 2 if lines[plan + 1:plan + 2] == [""] else plan + 1, item)
        else: lines = [f"# {name}", "", "## Plan", "", item, ""]
    if os.path.realpath(target) != os.path.join(os.path.realpath(path), os.path.basename(os.path.realpath(target))):
        raise ValueError("refusing to write outside the project folder")
    tmp = target + ".triage-tmp"
    with open(tmp, "w", encoding="utf-8", errors="surrogateescape", newline="") as fh: fh.write(eol.join(lines))
    if os.path.exists(target): shutil.copymode(target, tmp)
    os.replace(tmp, target)
    log(f"done {name} -> {os.path.basename(target)}: {text[:80]}")
    return {"ok": True, "file": os.path.basename(target), "msg": f"Saved to {os.path.basename(target)}"}

def active_projects():
    base = os.path.join(PROJECTS, "active")
    if not os.path.isdir(base): return []
    return [{"name": name, "path": "active/" + name, "abs_path": os.path.join(base, name),
             "repo": os.path.exists(os.path.join(base, name, ".git")),
             "done_defined": definition_of_done(os.path.join(base, name))["defined"]}
            for name in sorted(os.listdir(base), key=str.casefold)
            if not name.startswith(".") and os.path.isdir(os.path.join(base, name))]

GITHUB_REMOTE = re.compile(r"(?:https://(?:www\.)?github\.com/|git@(?:ssh\.)?github\.com:|ssh://git@(?:ssh\.)?github\.com(?::\d+)?/)([\w.-]+/[\w.-]+?)(?:\.git)?/?")

def github_repo_of(url):
    """'owner/name' for any way git can spell a GitHub remote (https, git@, ssh://, and ssh.github.com:443), else ''."""
    m = GITHUB_REMOTE.fullmatch((url or "").strip())
    return m[1] if m else ""

def git_out(path, args, timeout=5):
    try:
        proc = subprocess.run(["git"] + args, cwd=path, capture_output=True, text=True, timeout=timeout, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        return proc.stdout.strip() if proc.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired): return None

def github_state(path):
    """Is this project on GitHub? git: has a repo; repo: 'owner/name' of a GitHub origin; pushed: that remote has
    received something (a remote-tracking ref exists); unpushed: commits on this checkout that no origin ref contains."""
    if not os.path.exists(os.path.join(path, ".git")): return {"git": False, "repo": "", "pushed": False, "unpushed": 0}
    repo = github_repo_of(git_out(path, ["remote", "get-url", "origin"]))
    pushed = bool(repo and git_out(path, ["for-each-ref", "--count=1", "refs/remotes/origin"]))
    ahead = git_out(path, ["rev-list", "--count", "HEAD", "--not", "--remotes=origin"]) if pushed else None
    return {"git": True, "repo": repo, "pushed": pushed, "unpushed": int(ahead) if ahead and ahead.isdigit() else 0}

def projects_listing():
    projects = active_projects()
    for p in projects: p["gh"] = github_state(p["abs_path"])
    return projects

def project_detail(name):
    if name not in {p["name"] for p in active_projects()}: raise ValueError("unknown project")
    path = os.path.join(PROJECTS, "active", name)
    result = {"name": name, "path": "active/" + name, "abs_path": path, "summary": "No README or project notes yet.",
              "summary_source": None, "commits": [], "features": [], "prs": [],
              "pr_status": "No Git repository in this project.", "branch": "", "remote": "", "repo": "", "gh": {"git": False, "repo": "", "pushed": False, "unpushed": 0},
              "done": definition_of_done(path)}
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

# The graph is built from commits that reached GitHub (default branch of repos you own or collaborate on),
# not from GitHub's profile calendar: that calendar hides private repos unless a profile setting is on.
REPOS_QUERY = """{ viewer { id login repositories(first: 100, ownerAffiliations: [OWNER, COLLABORATOR], orderBy: {field: PUSHED_AT, direction: DESC}) {
  nodes { id nameWithOwner isPrivate url pushedAt } } } }"""
HISTORY_FIELDS = "id defaultBranchRef { target { ... on Commit { history(first: 100, since: %s, author: {id: %s}%s) { pageInfo { hasNextPage endCursor } nodes { committedDate } } } } }"
_activity = {"at": 0.0, "data": None}
_activity_lock = threading.Lock()

def _gh(args, timeout=25):
    """Run the gh CLI (your existing sign-in) and return stdout. Read-only use: callers only issue GETs and queries."""
    try:
        proc = subprocess.run(["gh"] + args, capture_output=True, text=True, timeout=timeout,
                              env={**os.environ, "GH_PROMPT_DISABLED": "1"})
    except FileNotFoundError: raise RuntimeError("gh is not installed")
    except subprocess.TimeoutExpired: raise RuntimeError("GitHub took too long to answer")
    if proc.returncode != 0:
        err = proc.stderr.strip() + proc.stdout.strip()
        if "HTTP 404" in err: raise RuntimeError("Not found, or not visible to your GitHub sign-in.")
        raise RuntimeError((err.splitlines() or ["gh failed; check `gh auth status` and the network"])[-1])
    return proc.stdout

def gh_graphql(query):
    """Run a GraphQL query through gh. Returns the parsed 'data'."""
    try: return json.loads(_gh(["api", "graphql", "-f", "query=" + query]))["data"]
    except (ValueError, KeyError): raise RuntimeError("unexpected answer from GitHub")

def commit_days(repo_ids, uid, since, max_pages=20):
    """{repo id: [local dates of your commits since `since`]} for each repo's default branch."""
    out = {i: [] for i in repo_ids}; pending = {i: "" for i in repo_ids}
    for _ in range(max_pages):
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

def build_activity(login, repos, by_repo, today_):
    """Calendar weeks (Sunday first, 53 of them like GitHub) plus last-30-day repo counts from commit dates."""
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
    return {"login": login, "total": sum(per_day.values()), "weeks": weeks,
            "active_days": sum(1 for d in flat if d["count"]), "streak": current_streak(flat),
            "repos": chips, "repo_days": ACTIVITY_REPO_DAYS,
            "fetched": dt.datetime.now().isoformat(timespec="seconds")}

def fetch_activity():
    v = gh_graphql(REPOS_QUERY)["viewer"]; today_ = today()
    year_ago = (today_ - dt.timedelta(days=366)).isoformat()
    repos = [r for r in v["repositories"]["nodes"] if r["pushedAt"][:10] >= year_ago]  # never pushed this year = no commits to count
    since = dt.datetime.combine(today_ - dt.timedelta(days=(today_.weekday() + 1) % 7 + 52 * 7), dt.time.min).astimezone().astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return build_activity(v["login"], repos, commit_days([r["id"] for r in repos], v["id"], since) if repos else {}, today_)

def github_activity(refresh=False):
    """Your pushed-commit graph, cached. Failures come back as {'error': ...}
    (with the last good data, marked stale, when there is some) so the page can still render."""
    with _activity_lock:
        fresh = _activity["data"] and time.time() - _activity["at"] < ACTIVITY_TTL
        if fresh and not refresh: return _activity["data"]
        try:
            _activity["data"] = fetch_activity(); _activity["at"] = time.time()
            return _activity["data"]
        except RuntimeError as e:
            if _activity["data"]: return {**_activity["data"], "stale": True, "error": str(e)}
            return {"error": str(e)}

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
    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        bad = self._refuse(False)
        if bad: return self._send(403, {"error": bad})
        try:
            if u.path == "/": return self._send(200, open(os.path.join(HERE, "index.html"), "rb").read().replace(b"__VERSION__", VERSION.encode()), "text/html")
            if u.path == "/api/version": return self._send(200, {"version": VERSION, "started": STARTED, "pid": os.getpid()})
            if u.path == "/api/projects": return self._send(200, {"projects": projects_listing()})
            if u.path == "/api/project": return self._send(200, project_detail(q.get("name", [""])[0]))
            if u.path == "/api/activity": return self._send(200, github_activity(q.get("refresh") == ["1"]))
            if u.path == "/api/repo": return self._send(200, repo_overview(q.get("name", [""])[0]))
            if u.path == "/api/repo/path": return self._send(200, repo_path(q.get("name", [""])[0], q.get("path", [""])[0]))
            if u.path == "/api/notes": return self._send(200, list_notes())
            if u.path == "/api/note":
                p = safe_note(q["f"][0]); return self._send(200, {"text": open(p, encoding="utf-8", errors="replace").read()})
            self._send(404, {"error": "not found"})
        except Exception as e: self._send(400, {"error": str(e)})
    def do_POST(self):
        bad = self._refuse(True)
        if bad: return self._send(403, {"error": bad})
        try:
            n = int(self.headers.get("Content-Length", 0)); req = json.loads(self.rfile.read(n) or b"{}")
            if urlparse(self.path).path == "/api/action": return self._send(200, act(req))
            if urlparse(self.path).path == "/api/done": return self._send(200, set_done(req.get("name", ""), req.get("text", ""), req.get("expected", "")))
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
    a = ap.parse_args(); BIND_HOST = a.host; NOTES = os.path.abspath(os.path.expanduser(a.notes)); PROJECTS = os.path.dirname(NOTES)
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
