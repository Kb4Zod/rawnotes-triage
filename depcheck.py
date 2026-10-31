"""What points at a project folder, before it moves. Read-only; standard library only; no global state of the app.

check(folder, sources, dest) -> {"blockers": [...], "todos": [...]}, each hit {kind, where, detail, fix}.

Blockers (the move would break them): systemd user units, running processes, git worktrees, ~/.local/bin links and
scripts, crontab lines. To-dos (the app can't do them itself; it never writes outside the notes and projects
folders): the agent memory folder under ~/.claude/projects keyed by the old path, and T3 Code projects (which the app
can't read, so that one is always listed).

Every scanner takes the place it looks as an argument (a home folder, a /proc folder, the crontab text), so tests run
against fake trees and never read this machine. Sources.default() is the only thing that points at the real ones.
"""
import os, re, shlex, subprocess

SCRIPT_MAX = 64 * 1024  # a ~/.local/bin file bigger than this is a program, not a wrapper script: not read
UNIT_EXT = (".service", ".timer", ".path", ".socket")
NAME_CHAR = re.compile(r"[\w.-]")  # a path followed by one of these is a different name (rawnotes-triage-flow-gaps)
GIT_ENV_DROP = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR", "GIT_CEILING_DIRECTORIES")


def _crontab_l():
    """`crontab -l` of this user ("" when there is none, or no crontab command)."""
    try: p = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired): return ""
    return p.stdout if p.returncode == 0 else ""


class Sources:
    """Where the scanners look. home: the folder holding .config/systemd/user, .local/bin and .claude/projects;
    proc: a /proc-like folder (<pid>/cwd link, <pid>/cmdline, <pid>/stat); crontab: a function returning the crontab
    text; own_pid: the app's own process, whose git/gh children are its own work, not something pointing at the folder."""
    def __init__(self, home, proc="/proc", crontab=_crontab_l, own_pid=None):
        self.home, self.proc, self.crontab, self.own_pid = os.path.abspath(home), proc, crontab, own_pid

    @classmethod
    def default(cls): return cls(os.path.expanduser("~"), own_pid=os.getpid())

    @property
    def unit_dirs(self): return [os.path.join(self.home, ".config", "systemd", "user")]
    @property
    def bin_dir(self): return os.path.join(self.home, ".local", "bin")
    @property
    def claude_projects(self): return os.path.join(self.home, ".claude", "projects")


def q(p): return shlex.quote(p)


def within(p, root): return p == root or p.startswith(root.rstrip(os.sep) + os.sep)


def path_forms(path, home):
    """The ways a file can spell `path`: absolute (and its real path), and under home as %h/…, ~/…, $HOME/…, ${HOME}/…"""
    forms = {path, os.path.realpath(path)}
    for h in {home, os.path.realpath(home)}:
        for p in list(forms):
            if within(p, h) and p != h:
                rel = p[len(h.rstrip(os.sep)) + 1:]
                forms.update({f"%h/{rel}", f"~/{rel}", f"$HOME/{rel}", f"${{HOME}}/{rel}"})
    return sorted(forms, key=len, reverse=True)


def mentions(text, path, home):
    """Does `text` name `path` (or anything inside it)? A longer name that merely starts the same way does not count."""
    for f in path_forms(path, home):
        i = text.find(f)
        while i >= 0:
            j = i + len(f)
            if j >= len(text) or not NAME_CHAR.match(text[j]): return True
            i = text.find(f, i + 1)
    return False


def _read_small(p):
    try:
        if os.path.getsize(p) > SCRIPT_MAX: return None
        with open(p, "rb") as fh: return fh.read(SCRIPT_MAX + 1).decode("utf-8", "replace")
    except OSError: return None


def hit(kind, where, detail, fix): return {"kind": kind, "where": where, "detail": detail, "fix": fix}


def _new(path, dest, p):
    """Where `p` (inside `path`) will be once `path` has moved to `dest`."""
    return dest + p[len(path):] if dest else None


def scan_units(path, unit_dirs, home, dest=None):
    """systemd user units (and their .d/*.conf drop-ins) whose text names the folder."""
    out = []
    for d in unit_dirs:
        try: names = sorted(os.listdir(d))
        except OSError: continue
        for name in names:
            p = os.path.join(d, name)
            files = []
            if name.endswith(UNIT_EXT) and os.path.isfile(p): unit, files = name, [p]
            elif name.endswith(".d") and name[:-2].endswith(UNIT_EXT) and os.path.isdir(p):
                unit = name[:-2]
                try: files = [os.path.join(p, f) for f in sorted(os.listdir(p)) if f.endswith(".conf")]
                except OSError: files = []
            for f in files:
                text = _read_small(f)
                if text is None or not mentions(text, path, home): continue
                out.append(hit("systemd", f, f"the unit {unit} runs from this folder",
                               f"systemctl --user disable --now {q(unit)}; after the move, point {q(f)} at the new folder "
                               f"(or re-run its installer from there) and run systemctl --user daemon-reload"))
    return out


OWN_TOOLS = re.compile(r"git(-[\w-]+)?|gh")  # what the app itself runs in a project folder (git, its helpers, gh)


def _ppid(proc, pid):
    """The parent pid from <proc>/<pid>/stat ("pid (comm) state ppid ..."; comm may hold spaces or parens), else None."""
    try:
        with open(os.path.join(proc, str(pid), "stat"), "rb") as fh: rest = fh.read(4096).decode("utf-8", "replace").rsplit(")", 1)[1].split()
        return int(rest[1])
    except (OSError, IndexError, ValueError): return None


def _own_tool(proc, pid, argv, own_pid):
    """A git or gh process started (directly or through git) by the app itself: its own short-lived work, not a blocker."""
    if not own_pid or not argv or not OWN_TOOLS.fullmatch(os.path.basename(argv[0])): return False
    cur = pid
    for _ in range(16):
        cur = _ppid(proc, cur)
        if cur is None or cur <= 1: return False
        if cur == own_pid: return True
    return False


def scan_processes(path, proc, home, own_pid=None):
    """Running processes whose working folder or command line is inside the folder (the app's own git/gh children aside)."""
    out = []; real = {path, os.path.realpath(path)}
    try: pids = sorted((n for n in os.listdir(proc) if n.isdigit()), key=int)
    except OSError: return out
    for pid in pids:
        base = os.path.join(proc, pid)
        try: cwd = os.readlink(os.path.join(base, "cwd"))
        except OSError: cwd = ""
        cwd = cwd[:-len(" (deleted)")] if cwd.endswith(" (deleted)") else cwd
        try:
            with open(os.path.join(base, "cmdline"), "rb") as fh: argv = [a.decode("utf-8", "replace") for a in fh.read(65536).split(b"\0") if a]
        except OSError: argv = []
        in_cwd = bool(cwd) and any(within(cwd, r) for r in real)
        if not in_cwd and not mentions("\0".join(argv), path, home): continue
        if _own_tool(proc, pid, argv, own_pid): continue
        cmd = " ".join(argv)[:200] or "(no command line)"
        out.append(hit("process", f"pid {pid}", f"{cmd}" + (f" (working folder {cwd})" if in_cwd else ""),
                       f"stop it first: kill {pid} (or stop the service or terminal that runs it)"))
    return out


def _git(path, args):
    env = {k: v for k, v in os.environ.items() if k not in GIT_ENV_DROP}
    try: p = subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, timeout=10, env={**env, "GIT_TERMINAL_PROMPT": "0"})
    except (OSError, subprocess.TimeoutExpired): return None
    return p.stdout if p.returncode == 0 else None


def scan_worktrees(path, dest=None):
    """(blockers, todos): the folder is a linked worktree, or it has linked worktrees (their .git files point at it by
    absolute path). A worktree whose folder is already gone (prunable) is only a to-do."""
    blockers, todos = [], []
    g = os.path.join(path, ".git")
    if os.path.isfile(g) and not os.path.islink(g):
        try:
            with open(g, encoding="utf-8", errors="replace") as fh: first = fh.readline().strip()
        except OSError: first = ""
        if first.startswith("gitdir:"):
            blockers.append(hit("worktree", path, f"this folder is a linked git worktree ({first})",
                                f"move it with git instead: git -C {q(path)} worktree move . <new place>"))
        return blockers, todos
    if not os.path.isdir(g): return blockers, todos
    listing = _git(path, ["worktree", "list", "--porcelain"])
    if listing is None: return blockers, todos
    entries = []
    for block in listing.strip().split("\n\n"):
        e = {}
        for line in block.splitlines():
            k, _, v = line.partition(" "); e[k] = v
        if e.get("worktree"): entries.append(e)
    main = os.path.realpath(path)
    shown = _git(path, ["worktree", "list"]) or ""
    for e in entries:
        wt = e["worktree"]
        if os.path.realpath(wt) == main or e.get("bare") is not None: continue
        if "prunable" in e:
            todos.append(hit("worktree", wt, "a stale worktree record (its folder is gone)", f"git -C {q(dest or path)} worktree prune"))
            continue
        blockers.append(hit("worktree", wt, "a linked worktree of this repo (git worktree list):\n" + shown.strip(),
                            f"git -C {q(path)} worktree remove {q(wt)}  (or keep it: after the move, "
                            f"git -C {q(dest or '<new place>')} worktree repair {q(wt)})"))
    return blockers, todos


def scan_bin(path, bin_dir, home, dest=None):
    """~/.local/bin entries: links resolving into the folder, and small scripts naming it."""
    out = []
    try: names = sorted(os.listdir(bin_dir))
    except OSError: return out
    real = {path, os.path.realpath(path)}
    for name in names:
        p = os.path.join(bin_dir, name)
        if os.path.islink(p):
            try: raw = os.readlink(p)
            except OSError: continue
            target = os.path.normpath(os.path.join(bin_dir, raw)); resolved = os.path.realpath(p)
            inside = next((t for t in (target, resolved) for r in real if within(t, r)), None)
            if not inside: continue
            root = next(r for r in real if within(inside, r))
            new = _new(root, dest, inside)
            out.append(hit("bin", p, f"a link to {inside}",
                           f"rm {q(p)}; after the move: ln -s {q(new) if new else '<new place>/…'} {q(p)} (or re-run the project's installer from there)"))
            continue
        if not os.path.isfile(p): continue
        text = _read_small(p)
        if text is not None and mentions(text, path, home):
            out.append(hit("bin", p, "a script that names this folder",
                           f"edit {q(p)} to use the new folder" + (f" ({dest})" if dest else "") + " (or re-run the project's installer from there)"))
    return out


def scan_crontab(path, text, home):
    out = []
    for n, line in enumerate((text or "").splitlines(), 1):
        if line.lstrip().startswith("#") or not mentions(line, path, home): continue
        out.append(hit("crontab", f"crontab line {n}", line.strip()[:200], "crontab -e: change or remove that line"))
    return out


def claude_key(path):
    """The folder name Claude Code keeps a project's sessions and memory under: every character that isn't a letter or
    a digit becomes '-' (/home/me/Projects/x -> -home-me-Projects-x)."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def _resolves(base, rest, depth=6):
    """Is `rest` ('-sub-dir' in key spelling) the key spelling of a folder that exists under `base`?"""
    if not rest: return True
    if depth == 0 or not rest.startswith("-"): return False
    rest = rest[1:]
    try: names = os.listdir(base)
    except OSError: return False
    for n in names:
        k = claude_key(n); p = os.path.join(base, n)
        if (rest == k or rest.startswith(k + "-")) and os.path.isdir(p) and not os.path.islink(p):
            if _resolves(p, rest[len(k):], depth - 1): return True
    return False


def scan_claude(path, projects_dir, dest=None):
    """Agent memory folders for the folder and anything inside it (to-dos, each with the exact mv for `dest`)."""
    out = []
    try: names = sorted(os.listdir(projects_dir))
    except OSError: return out
    key = claude_key(path); parent = os.path.dirname(path)
    try: siblings = [s for s in os.listdir(parent) if s != os.path.basename(path)]
    except OSError: siblings = []
    for name in names:
        if name != key and not name.startswith(key + "-"): continue
        rest = name[len(key):]
        if rest and not _resolves(path, rest):
            # a longer key that is really a sibling folder (rawnotes-triage-flow-gaps next to rawnotes-triage): not ours
            if any(name == claude_key(os.path.join(parent, s)) or name.startswith(claude_key(os.path.join(parent, s)) + "-") for s in siblings): continue
        old = os.path.join(projects_dir, name)
        if dest:
            new = os.path.join(projects_dir, claude_key(dest) + rest)
            fix = (f"mv {q(old)} {q(new)}" if not os.path.lexists(new)
                   else f"both {q(old)} and {q(new)} exist: merge them by hand")
        else: fix = f"mv {q(old)} {q(os.path.join(projects_dir, '<key of the new folder>'))}"
        what = "this folder" if not rest else "a folder inside it"
        out.append(hit("agent-memory", old, f"Claude Code sessions and memory for {what}, keyed by the old path", fix))
    return out


def t3_todo(path, dest=None):
    return hit("t3", "T3 Code", "T3 Code keeps its own list of project folders, which the app can't read",
               f"Update any T3 Code project whose folder is {path}" + (f" to {dest}" if dest else ""))


def check(path, sources, dest=None):
    """Everything that points at `path` (a project folder about to move to `dest`). Read-only."""
    path = os.path.normpath(os.path.abspath(path)); home = sources.home
    blockers = scan_units(path, sources.unit_dirs, home, dest)
    blockers += scan_processes(path, sources.proc, home, getattr(sources, "own_pid", None))
    wt_block, wt_todo = scan_worktrees(path, dest)
    blockers += wt_block
    blockers += scan_bin(path, sources.bin_dir, home, dest)
    blockers += scan_crontab(path, sources.crontab(), home)
    todos = wt_todo + scan_claude(path, sources.claude_projects, dest) + [t3_todo(path, dest)]
    return {"blockers": blockers, "todos": todos}
