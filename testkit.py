"""Shared fixture for the v2.05 regression suites: a real server on a temp projects root, never the real notes."""
import atexit
import datetime as dt
import importlib.util
import json
import re
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

import depcheck
import server


TEST_EMAIL = "tester@users.noreply.github.com"

# The real config folder, worked out the way server.py does before anything here patches HOME. No test may touch it:
# importing testkit (every test module does) points server.CONFIG_DIR, and RAWTRIAGE_CONFIG_DIR for any server or CLI a
# test starts as a subprocess, at a throwaway folder for the whole run; fixtures then use one of their own per test.
# test_zz_isolation (run last) fails if the real folder changed during the run.
REAL_CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "rawnotes-triage")


def config_dir_state(path=REAL_CONFIG_DIR):
    """What a folder looks like (exists, and each entry's size and mtime), to tell whether anything touched it."""
    if not os.path.lexists(path): return None
    out = {".": os.lstat(path).st_mtime_ns}
    for root, dirs, files in os.walk(path):
        for f in dirs + files:
            st = os.lstat(os.path.join(root, f)); out[os.path.relpath(os.path.join(root, f), path)] = (st.st_size, st.st_mtime_ns)
    return out


REAL_CONFIG_AT_START = config_dir_state()
SESSION_DIR = tempfile.mkdtemp(prefix="rt-test-session-")
atexit.register(shutil.rmtree, SESSION_DIR, ignore_errors=True)
os.environ["RAWTRIAGE_CONFIG_DIR"] = server.CONFIG_DIR = os.path.join(SESSION_DIR, "config")

# The dependency check (close out) never looks at this machine in tests: no real systemd units, ~/.local/bin, crontab,
# ~/.claude or running processes. Every test sees an empty fake home and /proc unless it sets server.DEPS itself; a
# folder move never runs the real sync-apps either (tests that want it point server.SYNC_APPS at a stub).
FAKE_DEPS_HOME = os.path.join(SESSION_DIR, "deps-home"); FAKE_PROC = os.path.join(SESSION_DIR, "deps-proc")
os.makedirs(FAKE_DEPS_HOME); os.makedirs(FAKE_PROC)
server.DEPS = depcheck.Sources(FAKE_DEPS_HOME, proc=FAKE_PROC, crontab=lambda: "")
server.SYNC_APPS = os.path.join(SESSION_DIR, "no-sync-apps")  # an absolute path that doesn't exist: never found on PATH
# Nor does a test ever run the real gh (the runner's own GitHub sign-in): first on PATH for the whole run is a gh that
# fails at once and notes the call. Tests that want gh put their own stub (or the fake gh) ahead of it, or patch
# server._gh; one that forgets, or a request still in flight after its test's patch stopped, gets this answer.
NO_REAL_GH = os.path.join(SESSION_DIR, "no-real-gh"); os.makedirs(NO_REAL_GH)
STRAY_GH_LOG = os.path.join(SESSION_DIR, "stray-gh-calls")
with open(os.path.join(NO_REAL_GH, "gh"), "w", encoding="utf-8") as _fh:
    _fh.write(f'#!/bin/sh\necho "gh $*" >> {shlex.quote(STRAY_GH_LOG)}\necho "the real gh is off in tests" >&2\nexit 1\n')
os.chmod(os.path.join(NO_REAL_GH, "gh"), 0o755)
os.environ["PATH"] = NO_REAL_GH + os.pathsep + os.environ.get("PATH", os.defpath)


def _report_stray_gh():
    try:
        with open(STRAY_GH_LOG, encoding="utf-8") as fh: calls = fh.read().splitlines()
    except FileNotFoundError: return
    sys.stderr.write(f"testkit: {len(calls)} gh call(s) reached the session's no-real-gh stub (none reached GitHub): {calls[:5]}\n")
atexit.register(_report_stray_gh)  # registered after the rmtree of SESSION_DIR, so it runs first

# ---- the switch (spec 8.1 of the Rust port): the same tests against another server or CLI binary ----------------------
# TRIAGE_SERVER=/path/to/rawtriage-web: Fixture and test_server's ApiTestCase start that binary for each test, as its own
# process, instead of serving server.H in this one (free port, --no-browser, --notes, --log; the same temp notes folder,
# config dir, fake home and isolated git). TRIAGE_CLI=/path/to/rawtriage: the CLI tests run that binary instead of
# `python bin/rawtriage`. Unset (the normal run), nothing here changes how a test runs. Under either switch, a test the
# ledger draft (tools/parity_ledger.py) marks "ported" (it needs a Rust replacement) is skipped, and only those.
# TRIAGE_EXPECT_VERSION=<version> (optional): the version the binary must report (/api/version, the page, commit bodies);
# unset, whatever it reports must look like N.NN or N.NN-tag (3.00-dev). In-process the tests expect server.VERSION.
# What the binary can't be told by a flag it gets from the environment (the test hooks of spec 8.1, read only by a
# `test-hooks` build):
#   RAWTRIAGE_TODAY=YYYY-MM-DD             "today", fixed when the fixture starts (the real date unless the run sets it)
#   RAWTRIAGE_TEST_UNDO_DUMP=<file>        after every request, the undo list as JSON (see undo_list())
#   RAWTRIAGE_TEST_DEPS_HOME=<dir>         the dependency check's home (.config/systemd/user, .local/bin, .claude)
#   RAWTRIAGE_TEST_PROC=<dir>              its /proc
#   RAWTRIAGE_TEST_CRONTAB=<file>          its crontab text (a missing file: no crontab)
#   RAWTRIAGE_TEST_SYNC_APPS=<path>        the program run after a folder moves, instead of sync-apps (one path, no arguments)
#   RAWTRIAGE_TEST_SHIP_NEEDS_GITHUB=<kinds>  comma-separated kinds whose Ship needs GitHub ("" = none)
#   RAWTRIAGE_TEST_OPENER=<command>        run instead of the platform opener (shell words, so quote a path with spaces),
#                                          the path to open as its last argument
#   RAWTRIAGE_TEST_TREE_LIMIT=<n>          the entry count over which a folder is too big for undo to check (TREE_LIMIT)
#   RAWTRIAGE_TEST_DEPS_FAIL=<message>     the dependency lookup (GET /api/project/deps) fails with that message
#   RAWTRIAGE_TEST_INFLIGHT_DUE=<project>=<text>  that project's `due` in the board listing, after metadata extraction
#                                          (so a test's hostile string reaches the page whole)
# and the fixture's PATH starts with a fake `gh` (FakeGh) that answers nothing until a test scripts it, so no test can
# reach the real GitHub; GH_TOKEN and GITHUB_TOKEN are not passed on. tools/shim/ has stand-ins for the two binaries
# that run this repo's own server.py and bin/rawtriage as separate processes, to exercise the switch without Rust.
# A test that patches the server in-process says the same to the binary through Switchable.serve_with() (its
# environment, --host, --notes, --log, flags; before the first request, or restart=True for a new process), deps_env(),
# path_without(), set_limits() and fake_gh; each does nothing in-process (tools/patch_sites.tsv says which test does what).
HERE = os.path.dirname(os.path.abspath(__file__))
def _binary(var):
    path = os.environ.get(var) or None  # a path with a slash is made absolute: tests run commands from other folders
    return os.path.abspath(path) if path and os.sep in path else path


SERVER_BIN, CLI_BIN = _binary("TRIAGE_SERVER"), _binary("TRIAGE_CLI")
SWITCHED = bool(SERVER_BIN or CLI_BIN)
CLI = os.path.join(HERE, "bin", "rawtriage")
FAKE_GH = os.path.join(HERE, "tools", "fake_gh.py")
NO_CRONTAB = os.path.join(SESSION_DIR, "deps-crontab")  # never created: an empty crontab
_undo_dump = None  # under TRIAGE_SERVER: the dump file of the server the current test started
# The GitHub sign-in of whoever runs the suite never reaches a server under test (a test may still set its own).
OWN_GH_ENV = {k: os.environ[k] for k in ("GH_TOKEN", "GITHUB_TOKEN") if k in os.environ}


def cli_argv(cli=CLI):
    """The command that runs the CLI: TRIAGE_CLI if set, else `python bin/rawtriage` (or `cli`, a copy of it)."""
    return [CLI_BIN] if CLI_BIN and cli == CLI else [sys.executable, cli]


def _tool(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, "tools", name + ".py"))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod


undo_entry = _tool("undo_dump").entry  # the dump's format, shared with tools/shim/rawtriage-web
_graphql_query = _tool("fake_gh").graphql_query  # how the fake gh finds a GraphQL query in its argv


def undo_list():
    """The server's undo list, oldest first: [{"label", "ops": [[kind, path, ...]], "unfinished"}]. In-process it is
    server._undo; under TRIAGE_SERVER it is the file the binary rewrites after every request (RAWTRIAGE_TEST_UNDO_DUMP)."""
    if not SERVER_BIN: return [undo_entry(e) for e in server._undo]
    if _undo_dump is None: return []  # the test's server hasn't started yet (or was just restarted): nothing to undo
    try:
        with open(_undo_dump, encoding="utf-8") as fh: return json.load(fh)
    except FileNotFoundError: return []  # not written yet: no request has changed anything


def free_port():
    with socket.socket() as s: s.bind(("127.0.0.1", 0)); return s.getsockname()[1]


class FakeGh:
    """A fake `gh` on a PATH of its own (tools/fake_gh.py). It answers from a JSON file the test writes through
    answer(), in order where a test needs a sequence, and logs every call (argv, environment, working folder) to a file
    calls() reads. A call nothing answers exits 1 with "fake gh: no answer for …" on stderr (and is logged too)."""
    def __init__(self, case):
        self.dir = tempfile.mkdtemp(prefix="rt-fakegh-"); case.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.bin = os.path.join(self.dir, "bin"); os.makedirs(self.bin)
        self.answers_file, self.log = os.path.join(self.dir, "answers.json"), os.path.join(self.dir, "calls.jsonl")
        self.answers = []; self._save()
        gh = os.path.join(self.bin, "gh")
        with open(gh, "w", encoding="utf-8") as fh:
            fh.write(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} -I -S {shlex.quote(FAKE_GH)} {shlex.quote(self.dir)} "$@"\n')
        os.chmod(gh, 0o755)

    def answer(self, argv=None, prefix=None, graphql=None, stdout="", stderr="", exit=0, json_out=None, once=False, env=None):
        """Answer a call whose argv is `argv` exactly, starts with `prefix`, or is `gh api graphql` with a query
        containing `graphql` (a substring of the -f query=... argument). json_out: stdout as JSON. once: used for one
        call only, so answers for the same call given in order are a sequence (the last one that isn't `once` repeats).
        env: {name: value} the call's environment must have as well (None: unset or empty), e.g. the GH_TOKEN in use."""
        self.answers.append({"argv": argv, "prefix": prefix, "graphql": graphql, "env": env, "exit": exit, "once": once, "stderr": stderr,
                             "stdout": json.dumps(json_out) if json_out is not None else stdout})
        self._save(); return self

    def reset(self):
        """Forget every answer (the calls stay logged)."""
        self.answers = []; self._save()
        try: os.remove(os.path.join(self.dir, "used.json"))
        except FileNotFoundError: pass
        return self

    def _save(self):
        tmp = self.answers_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh: json.dump({"answers": self.answers}, fh)
        os.replace(tmp, self.answers_file)

    def queries(self):
        """The query of every `gh api graphql` call so far, oldest first."""
        return [q for q in (_graphql_query(c["argv"]) for c in self.calls()) if q is not None]

    def calls(self):
        """Every call so far, oldest first: {"argv", "env", "cwd", "answered"}."""
        try:
            with open(self.log, encoding="utf-8") as fh: return [json.loads(l) for l in fh if l.strip()]
        except FileNotFoundError: return []


class LiveList:
    """A list read again on every use (len, index, ==, iteration): a fake gh's calls as a test's stub would collect them."""
    def __init__(self, read): self.read = read
    def __len__(self): return len(self.read())
    def __getitem__(self, i): return self.read()[i]
    def __iter__(self): return iter(self.read())
    def __eq__(self, other): return self.read() == other
    def __repr__(self): return repr(self.read())


class ChildServer:
    """TRIAGE_SERVER, run for one test. Started on first use of the test's port (so whatever setUp still changes in the
    environment, after the fixture's own setUp, reaches it), stopped by the test's cleanup. Its log, undo dump, output
    and fake gh live in a folder of their own outside the fixture's tree (snapshots compare that tree)."""
    def __init__(self, case, notes, config_dir, relax_limits=False):
        self.case, self.notes, self.config_dir = case, notes, config_dir
        self.host, self.args, self.extra_env, self.gh_first = "127.0.0.1", [], {}, True  # serve_with changes them before a start
        self.side = tempfile.mkdtemp(prefix="rt-child-"); case.addCleanup(shutil.rmtree, self.side, ignore_errors=True)
        self.log = os.path.join(self.side, "triage.log"); self.dump = os.path.join(self.side, "undo.json")
        self.output = os.path.join(self.side, "server-output.txt")
        self.gh = FakeGh(case); self.proc = None
        if relax_limits:  # what Fixture's patch of server.LIMIT_DEFAULTS does in-process: the fixture's config.json
            os.makedirs(config_dir, exist_ok=True)
            with open(os.path.join(config_dir, "config.json"), "w", encoding="utf-8") as fh:
                json.dump({"inbox_limit_days": 36500, "max_keeps": 1000}, fh)
        case.addCleanup(self.stop)

    def env(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE") and not (k in OWN_GH_ENV and OWN_GH_ENV[k] == v)}
        opener = os.path.join(self.side, "opened"); env.update({
            "RAWTRIAGE_CONFIG_DIR": self.config_dir, "RAWTRIAGE_TEST_UNDO_DUMP": self.dump,
            "RAWTRIAGE_TEST_DEPS_HOME": FAKE_DEPS_HOME, "RAWTRIAGE_TEST_PROC": FAKE_PROC, "RAWTRIAGE_TEST_CRONTAB": NO_CRONTAB,
            "RAWTRIAGE_TEST_SYNC_APPS": server.SYNC_APPS, "RAWTRIAGE_TEST_SHIP_NEEDS_GITHUB": "",
            "RAWTRIAGE_TEST_OPENER": shlex.quote(os.path.join(HERE, "tools", "shim", "record-open")), "RAWTRIAGE_TEST_OPENED": opener})
        for k, v in self.extra_env.items():  # serve_with: None removes a variable
            if v is None: env.pop(k, None)
            else: env[k] = v
        path = env.get("PATH", os.defpath)  # serve_with's PATH is the base; the fake gh goes first unless the test brings its own
        env["PATH"] = self.gh.bin + os.pathsep + path if self.gh_first else path
        return env

    def start(self):
        global _undo_dump
        for attempt in range(3):
            port = free_port()
            with open(self.output, "ab") as out:
                self.proc = subprocess.Popen([SERVER_BIN, "--port", str(port), "--host", self.host, "--no-browser",
                                              *(["--notes", self.notes] if self.notes is not None else []), "--log", self.log,
                                              *self.args], cwd=self.side, env=self.env(),
                                             stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and self.proc.poll() is None:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/version", timeout=2) as r: r.read()
                    if self.proc.poll() is not None: break  # something else answered on the port and ours exited: retry
                    _undo_dump = self.dump; return port
                except (OSError, ValueError): time.sleep(0.05)
            self.stop()
        raise RuntimeError(f"{SERVER_BIN} did not start:\n{self.output_text()}")

    def stop(self):
        global _undo_dump
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try: self.proc.wait(10)
            except subprocess.TimeoutExpired: self.proc.kill(); self.proc.wait()
        self.proc = None
        if _undo_dump == self.dump: _undo_dump = None

    def restart(self):
        """A new process on the same folders: an empty undo list and cold caches. Used where a test cleared server._undo."""
        self.stop()
        try: os.remove(self.dump)
        except FileNotFoundError: pass

    def output_text(self):
        try:
            with open(self.output, encoding="utf-8", errors="replace") as fh: return fh.read()
        except FileNotFoundError: return ""


class Switchable:
    """`port` that starts the TRIAGE_SERVER child on first use, for Fixture and test_server's ApiTestCase."""
    _port = child = None
    _starting = threading.Lock()  # a test's threads may all reach for the port at once: start one server, not several

    @property
    def port(self):
        if self._port is None and self.child is not None:
            with self._starting:
                if self._port is None: self._port = self.child.start()
        return self._port

    @port.setter
    def port(self, value): self._port = value

    def switch_to_child(self, notes, config_dir, relax_limits=False):
        """Under TRIAGE_SERVER: serve this test from the binary, logging to a file outside the fixture's tree (which
        server.LOG_PATH then names, for tests that read the log), with "today" fixed now."""
        today = os.environ.get("RAWTRIAGE_TODAY") or dt.date.today().isoformat()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", today): raise ValueError(f"RAWTRIAGE_TODAY must be YYYY-MM-DD, not {today!r}")
        dt.date.fromisoformat(today)  # a typo fails here; the server would quietly use the real date
        p = mock.patch.dict(os.environ, {"RAWTRIAGE_TODAY": today})
        p.start(); self.addCleanup(p.stop)
        self.child = ChildServer(self, notes, config_dir, relax_limits)
        p = mock.patch.object(server, "LOG_PATH", self.child.log); p.start(); self.addCleanup(p.stop)

    def server_pid(self):
        """The process serving this test: this one in-process, the child under TRIAGE_SERVER."""
        return os.getpid() if self.child is None else (self.port and self.child.proc.pid)

    _KEEP = object()

    def serve_with(self, env=None, host=None, notes=_KEEP, args=None, log=None, gh=None, restart=False, reset=()):
        """Under TRIAGE_SERVER, how this test's server is started: env, variables for it (the test hooks; None removes
        one); host, its --host; notes, its --notes (None: started without it); args, more flags (--no-commit). The
        server starts on the test's first request, so this comes before it, or passes restart=True: a new process,
        so an empty undo list and cold caches. reset: names of variables to give back the fixture's own value.
        In-process it does nothing; the test patches server.* as before. log: its --log (default: a file
        beside the child's own output; server.LOG_PATH names whichever it is). env's PATH is the base of the server's
        PATH: testkit's fake gh still goes first, unless gh=False (the test's PATH has its own gh, or none at all)."""
        if self.child is None: return
        if self.child.proc is not None:
            if not restart: raise RuntimeError("serve_with() after the server started: pass restart=True (a new process)")
            self.clear_undo()
        for k in reset: self.child.extra_env.pop(k, None)
        self.child.extra_env.update(env or {})
        if host is not None: self.child.host = host
        if notes is not self._KEEP: self.child.notes = notes
        if args is not None: self.child.args = list(args)
        if gh is not None: self.child.gh_first = gh
        if log is not None: self.child.log = server.LOG_PATH = log  # switch_to_child's patch of LOG_PATH puts it back

    def deps_env(self, sources):
        """The test hooks that give a server under TRIAGE_SERVER the dependency check's sources (a depcheck.Sources):
        RAWTRIAGE_TEST_DEPS_HOME, _PROC and _CRONTAB (the crontab function's text, written to a file now)."""
        crontab = os.path.join(tempfile.mkdtemp(prefix="rt-crontab-"), "crontab")
        self.addCleanup(shutil.rmtree, os.path.dirname(crontab), ignore_errors=True)
        with open(crontab, "w", encoding="utf-8") as fh: fh.write(sources.crontab() or "")
        return {"RAWTRIAGE_TEST_DEPS_HOME": sources.home, "RAWTRIAGE_TEST_PROC": sources.proc, "RAWTRIAGE_TEST_CRONTAB": crontab}

    _version = None

    def expected_version(self):
        """The version the server under test must report. In-process server.VERSION. Under TRIAGE_SERVER,
        TRIAGE_EXPECT_VERSION if set; else what its /api/version says (read once), which must at least look like a
        version (N.NN, or N.NN-tag such as 3.00-dev), so a server answering "" or junk fails every version check."""
        if self.child is None: return server.VERSION
        want = os.environ.get("TRIAGE_EXPECT_VERSION")
        if want: return want
        if self._version is None:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/version", timeout=10) as r: self._version = json.loads(r.read())["version"]
        if not isinstance(self._version, str) or not re.fullmatch(r"\d+\.\d{2}(-[a-z]+)?", self._version):
            self.fail(f"the server under test reports version {self._version!r}, not N.NN[-tag] (set TRIAGE_EXPECT_VERSION)")
        return self._version

    def served_page(self):
        """The page the server under test must serve: server.INDEX_HTML in-process; under TRIAGE_SERVER index.html with
        expected_version() put in (the Rust port's normalisation rule R2: only the version differs)."""
        if self.child is None: return server.INDEX_HTML
        with open(os.path.join(HERE, "index.html"), "rb") as fh: return fh.read().replace(b"__VERSION__", self.expected_version().encode())

    def path_without(self, *names):
        """This process's PATH with no `names` on it (e.g. "gh", "git"), for a server started without them: folders that
        hold one are swapped for a folder of links to their other programs (at the first swapped folder's place, so the
        search order holds), and python3, git and the rest still run. Empty entries are dropped."""
        return self._path_without(names)[0]

    def _path_without(self, names):
        keep, links = [], tempfile.mkdtemp(prefix="rt-path-"); self.addCleanup(shutil.rmtree, links, ignore_errors=True)
        for d in os.environ.get("PATH", os.defpath).split(os.pathsep):
            if not d: continue
            if not any(os.path.lexists(os.path.join(d, n)) for n in names): keep.append(d); continue
            if links not in keep: keep.append(links)
            try: entries = os.listdir(d)
            except OSError: continue
            for e in entries:
                src, dst = os.path.join(d, e), os.path.join(links, e)
                if e not in names and not os.path.lexists(dst) and os.path.isfile(src) and os.access(src, os.X_OK): os.symlink(src, dst)
        if links not in keep: keep.append(links)
        return os.pathsep.join(keep), links

    def set_limits(self, **limits):
        """Under TRIAGE_SERVER, the Inbox limits the test patches into server.LIMIT_DEFAULTS, written to the fixture's
        config.json (merged with what is there) before the server starts. In-process it does nothing."""
        if self.child is None: return
        if self.child.proc is not None: raise RuntimeError("set_limits() after the server started: serve_with(restart=True) first")
        self.write_config(**limits)

    def saved_token(self):
        """The GitHub token the server under test saved, from its file in the fixture's config folder ("" if none)."""
        try:
            with open(server.token_path(), encoding="utf-8") as fh: return fh.read().strip()
        except FileNotFoundError: return ""

    def opened_paths(self):
        """Under TRIAGE_SERVER, the paths the server handed to its opener (RAWTRIAGE_TEST_OPENER, tools/shim/record-open),
        oldest first, as LiveList: what in-process tests collect by patching server.open_path."""
        def read():
            try:
                with open(os.path.join(self.child.side, "opened"), encoding="utf-8") as fh: return fh.read().splitlines()
            except FileNotFoundError: return []
        return LiveList(read)

    def vanishing(self, name):
        """Under TRIAGE_SERVER, a PATH on which `name` (e.g. "git") is found only through a link that the returned
        function removes: a server started with it loses the program mid-test, keeping its undo list."""
        path, links = self._path_without((name,))
        real = shutil.which(name); link = os.path.join(links, name); os.symlink(real, link)
        return path, lambda: os.remove(link)

    def write_config(self, **values):
        """Merge values into the fixture's config.json (in-process the file starts absent, so this is a plain write; under
        TRIAGE_SERVER it keeps the relaxed limits Fixture wrote there)."""
        conf = self.saved_config() or {}; conf.update(values)
        os.makedirs(self.config_dir(), exist_ok=True)
        with open(os.path.join(self.config_dir(), "config.json"), "w", encoding="utf-8") as fh: json.dump(conf, fh)

    def saved_config(self):
        """The fixture's config.json as the server under test left it (None if there is none)."""
        try:
            with open(os.path.join(self.config_dir(), "config.json"), encoding="utf-8") as fh: return json.load(fh)
        except FileNotFoundError: return None

    @property
    def fake_gh(self):
        """Under TRIAGE_SERVER, the FakeGh on the server's PATH (None in-process, where the tests patch server._gh)."""
        return self.child.gh if self.child is not None else None

    def config_dir(self):
        """The config folder the server under test reads (token, config.json)."""
        return self.child.config_dir if self.child is not None else server.CONFIG_DIR

    def clear_undo(self):
        """Empty the server's undo list: in-process server._undo.clear(); under TRIAGE_SERVER a fresh process."""
        if self.child is None: server._undo.clear()
        else: self.child.restart(); self._port = None



def isolate_git(case, email=TEST_EMAIL):
    """For the rest of `case`, git sees a throwaway HOME and global config (a fixture identity, hooks off), never the
    real ~/.gitconfig, its hooks (gitleaks) or the system config, and never runs auto gc or maintenance. Returns the config file, for tests that change it."""
    home = os.path.realpath(tempfile.mkdtemp(prefix="rt-githome-")); case.addCleanup(shutil.rmtree, home, ignore_errors=True)
    cfg = os.path.join(home, ".gitconfig")
    with open(cfg, "w", encoding="utf-8") as fh:
        # no auto gc / maintenance: it could write .git/objects/maintenance.lock (or packs) between a test's snapshots
        fh.write(f"[user]\n\tname = Test User\n\temail = {email}\n[core]\n\thooksPath = /dev/null\n"
                 "[gc]\n\tauto = 0\n\tautoDetach = false\n[maintenance]\n\tauto = false\n")
    p = mock.patch.dict(os.environ, {"HOME": home, "XDG_CONFIG_HOME": os.path.join(home, ".config"), "GIT_CONFIG_GLOBAL": cfg,
                                     "GIT_CONFIG_NOSYSTEM": "1"})
    p.start(); case.addCleanup(p.stop)
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL", "GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME", "EMAIL"): os.environ.pop(var, None)
    return cfg


class Fixture(Switchable, unittest.TestCase):
    """HTTP server bound to a temporary RawNotes + projects root. Helpers read/write only inside it."""
    relax_limits = True

    def setUp(self):
        self.gitconfig = isolate_git(self)
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.notes = os.path.join(self.tmp, "RawNotes"); os.makedirs(self.notes)
        self.saved = (server.NOTES, server.PROJECTS, server.log, server.LOG_PATH, server.CONFIG_DIR)
        server.CONFIG_DIR = os.path.join(self.tmp, "config")  # never read or write the real config.json or saved GitHub token
        p = mock.patch.dict(os.environ, {"RAWTRIAGE_CONFIG_DIR": server.CONFIG_DIR}); p.start(); self.addCleanup(p.stop)  # nor a server a test spawns
        server.forget_github()
        p = mock.patch.object(server, "SHIP_NEEDS_GITHUB", ()); p.start(); self.addCleanup(p.stop)  # the GitHub gate on Ship has its own tests (test_github_push.py)
        if self.relax_limits:  # fixtures use fixed 2026-10 dates: keep the Inbox limits from expiring them as the real date moves on
            p = unittest.mock.patch.dict(server.LIMIT_DEFAULTS, {"inbox_limit_days": 36500, "max_keeps": 1000}); p.start(); self.addCleanup(p.stop)
        server.NOTES, server.PROJECTS = self.notes, self.tmp
        server.log = lambda msg: None  # never write test actions into the real triage.log
        server.LOG_PATH = os.path.join(self.tmp, "triage.log")
        server._undo.clear(); server._git_cache.clear()
        for c in (server._notes_cache, server._meta_cache, server._proj_cache): c.clear()
        self.addCleanup(self._restore)
        if SERVER_BIN: return self.switch_to_child(self.notes, server.CONFIG_DIR, self.relax_limits)
        self.httpd = server.Server(("127.0.0.1", 0), server.H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=lambda: self.httpd.serve_forever(poll_interval=0.02), daemon=True).start()
        self.addCleanup(self.httpd.server_close); self.addCleanup(self.httpd.shutdown)

    def _restore(self):
        server.NOTES, server.PROJECTS, server.log, server.LOG_PATH, server.CONFIG_DIR = self.saved
        server.forget_github()
        server._undo.clear()

    # -- http
    def call(self, path, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method="GET" if body is None else "POST",
                                     headers={} if body is None else {"Content-Type": "application/json"},
                                     data=None if body is None else json.dumps(body).encode())
        try:
            with urllib.request.urlopen(req) as r: return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

    def get(self, path):
        code, data = self.call(path); self.assertEqual(code, 200, data); return data

    def post(self, path, body, ok=True):
        code, data = self.call(path, body)
        if ok: self.assertEqual(code, 200, data)
        else: self.assertNotEqual(code, 200, data); self.assertIn("error", data)
        return data

    # -- files
    def note(self, name, title="A note", status="open", body="Some notes.", question=None, sub=""):
        d = os.path.join(self.notes, sub) if sub else self.notes
        os.makedirs(d, exist_ok=True); path = os.path.join(d, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"# {title}\n\n- date: {name[:10]}\n- status: {status}\n- type: research\n\n"
                     f"## What's the question / problem?\n\n{question or 'Why ' + title + '?'}\n\n## Notes\n\n### Detail\n\n{body}\n")
        return path

    def project(self, name, kind="active", notes=None):
        path = os.path.join(self.tmp, kind, name); os.makedirs(path)
        if notes is not None:
            with open(os.path.join(path, "NOTES.md"), "w", encoding="utf-8") as fh: fh.write(notes)
        return path

    def record(self, kind, name):
        """A project folder's board record (RawNotes/projects/<kind>/<name>.md) as text, None if it has none."""
        p = os.path.join(self.notes, "projects", kind, name + ".md")
        if not os.path.exists(p): return None
        with open(p, encoding="utf-8") as fh: return fh.read()

    def snapshot(self, root=None):
        out = {}
        for r, dirs, files in os.walk(root or self.tmp):
            for d in dirs: out[os.path.relpath(os.path.join(r, d), self.tmp) + "/"] = ("dir",)
            for f in files:
                p = os.path.join(r, f)
                if os.path.islink(p): out[os.path.relpath(p, self.tmp)] = ("link", os.readlink(p)); continue
                with open(p, "rb") as fh: out[os.path.relpath(p, self.tmp)] = (fh.read(), os.stat(p).st_mode & 0o7777)
        return out

    def tmp_leftovers(self):
        return [os.path.join(r, f) for r, _, fs in os.walk(self.tmp) for f in fs if f.endswith(".tmp")]


def _skip_what_the_ledger_ports():
    """Under a switch, skip exactly the tests the ledger draft marks "ported" (a Rust test replaces them, or they call
    no route and no CLI, so they would still test Python), each with its reason; every other test runs."""
    ledger = _tool("parity_ledger")
    ported = {t.id: t.reason for t in ledger.build().tests if t.outcome == "ported"}
    run = unittest.TestCase.run

    def run_unless_ported(self, result=None):
        tid = self.id()
        if tid.startswith("__main__."):  # `python3 test_x.py`: the module's own name, as the ledger has it
            tid = os.path.splitext(os.path.basename(sys.modules["__main__"].__file__))[0] + tid[len("__main__"):]
        why = ported.get(tid)
        if why is not None:
            method = getattr(self, self._testMethodName)
            setattr(self, self._testMethodName, unittest.skip(f"ported (ledger draft): {why}")(method))
        return run(self, result)
    unittest.TestCase.run = run_unless_ported


if SWITCHED: _skip_what_the_ledger_ports()
