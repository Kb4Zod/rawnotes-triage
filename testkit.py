"""Shared fixture for the v2.05 regression suites: a real server on a temp projects root, never the real notes."""
import atexit
import datetime as dt
import importlib.util
import json
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

# ---- the switch (spec 8.1 of the Rust port): the same tests against another server or CLI binary ----------------------
# TRIAGE_SERVER=/path/to/rawtriage-web: Fixture and test_server's ApiTestCase start that binary for each test, as its own
# process, instead of serving server.H in this one (free port, --no-browser, --notes, --log; the same temp notes folder,
# config dir, fake home and isolated git). TRIAGE_CLI=/path/to/rawtriage: the CLI tests run that binary instead of
# `python bin/rawtriage`. Unset (the normal run), nothing here changes how a test runs. Under either switch, a test the
# ledger draft (tools/parity_ledger.py) marks "ported" (it needs a Rust replacement) is skipped, and only those.
# What the binary can't be told by a flag it gets from the environment (the test hooks of spec 8.1, read only by a
# `test-hooks` build):
#   RAWTRIAGE_TODAY=YYYY-MM-DD             "today", fixed when the fixture starts (the real date unless the run sets it)
#   RAWTRIAGE_TEST_UNDO_DUMP=<file>        after every request, the undo list as JSON (see undo_list())
#   RAWTRIAGE_TEST_DEPS_HOME=<dir>         the dependency check's home (.config/systemd/user, .local/bin, .claude)
#   RAWTRIAGE_TEST_PROC=<dir>              its /proc
#   RAWTRIAGE_TEST_CRONTAB=<file>          its crontab text (a missing file: no crontab)
#   RAWTRIAGE_TEST_SYNC_APPS=<command>     what runs after a folder moves, instead of sync-apps
#   RAWTRIAGE_TEST_SHIP_NEEDS_GITHUB=<kinds>  comma-separated kinds whose Ship needs GitHub ("" = none)
#   RAWTRIAGE_TEST_OPENER=<command>        run instead of the platform opener, the path as its one argument
# and the fixture's PATH starts with a fake `gh` (FakeGh) that answers nothing until a test scripts it, so no test can
# reach the real GitHub; GH_TOKEN and GITHUB_TOKEN are not passed on. tools/shim/ has stand-ins for the two binaries
# that run this repo's own server.py and bin/rawtriage as separate processes, to exercise the switch without Rust.
HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_BIN = os.environ.get("TRIAGE_SERVER") or None
CLI_BIN = os.environ.get("TRIAGE_CLI") or None
SWITCHED = bool(SERVER_BIN or CLI_BIN)
CLI = os.path.join(HERE, "bin", "rawtriage")
FAKE_GH = os.path.join(HERE, "tools", "fake_gh.py")
NO_CRONTAB = os.path.join(SESSION_DIR, "deps-crontab")  # never created: an empty crontab
_undo_dump = None  # under TRIAGE_SERVER: the dump file of the server the current test started


def cli_argv(cli=CLI):
    """The command that runs the CLI: TRIAGE_CLI if set, else `python bin/rawtriage` (or `cli`, a copy of it)."""
    return [CLI_BIN] if CLI_BIN and cli == CLI else [sys.executable, cli]


def undo_entry(entry):
    """One server._undo entry as RAWTRIAGE_TEST_UNDO_DUMP writes it: the label, each op's kind and the paths it touched
    (never file contents), and whether it is what is left of a change that couldn't be rolled back."""
    paths = {"move": 2, "movedir": 2}
    return {"label": entry["label"], "ops": [[op[0], *op[1:1 + paths.get(op[0], 1)]] for op in entry["ops"]],
            "unfinished": entry["label"].startswith("unfinished:")}


def undo_list():
    """The server's undo list, oldest first: [{"label", "ops": [[kind, path, ...]], "unfinished"}]. In-process it is
    server._undo; under TRIAGE_SERVER it is the file the binary rewrites after every request (RAWTRIAGE_TEST_UNDO_DUMP)."""
    if _undo_dump is None: return [undo_entry(e) for e in server._undo]
    with open(_undo_dump, encoding="utf-8") as fh: return json.load(fh)


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
            fh.write(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(FAKE_GH)} {shlex.quote(self.dir)} "$@"\n')
        os.chmod(gh, 0o755)

    def answer(self, argv=None, prefix=None, graphql=None, stdout="", stderr="", exit=0, json_out=None, once=False):
        """Answer a call whose argv is `argv` exactly, starts with `prefix`, or is `gh api graphql` with a query
        containing `graphql` (a substring of the -f query=... argument). json_out: stdout as JSON. once: used for one
        call only, so answers for the same call given in order are a sequence (the last one that isn't `once` repeats)."""
        self.answers.append({"argv": argv, "prefix": prefix, "graphql": graphql, "exit": exit, "once": once, "stderr": stderr,
                             "stdout": json.dumps(json_out) if json_out is not None else stdout})
        self._save(); return self

    def _save(self):
        tmp = self.answers_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh: json.dump({"answers": self.answers}, fh)
        os.replace(tmp, self.answers_file)

    def calls(self):
        """Every call so far, oldest first: {"argv", "env", "cwd", "answered"}."""
        try:
            with open(self.log, encoding="utf-8") as fh: return [json.loads(l) for l in fh if l.strip()]
        except FileNotFoundError: return []


class ChildServer:
    """TRIAGE_SERVER, run for one test. Started on first use of the test's port (so whatever setUp still changes in the
    environment, after the fixture's own setUp, reaches it), stopped by the test's cleanup. Its log, undo dump, output
    and fake gh live in a folder of their own outside the fixture's tree (snapshots compare that tree)."""
    def __init__(self, case, notes, config_dir, relax_limits=False):
        self.case, self.notes, self.config_dir = case, notes, config_dir
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
        env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN", "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
        opener = os.path.join(self.side, "opened"); env.update({
            "RAWTRIAGE_CONFIG_DIR": self.config_dir, "RAWTRIAGE_TEST_UNDO_DUMP": self.dump,
            "RAWTRIAGE_TEST_DEPS_HOME": FAKE_DEPS_HOME, "RAWTRIAGE_TEST_PROC": FAKE_PROC, "RAWTRIAGE_TEST_CRONTAB": NO_CRONTAB,
            "RAWTRIAGE_TEST_SYNC_APPS": server.SYNC_APPS, "RAWTRIAGE_TEST_SHIP_NEEDS_GITHUB": "",
            "RAWTRIAGE_TEST_OPENER": os.path.join(HERE, "tools", "shim", "record-open"), "RAWTRIAGE_TEST_OPENED": opener,
            "PATH": self.gh.bin + os.pathsep + env.get("PATH", os.defpath)})
        return env

    def start(self):
        global _undo_dump
        for attempt in range(3):
            port = free_port()
            with open(self.output, "ab") as out:
                self.proc = subprocess.Popen([SERVER_BIN, "--port", str(port), "--host", "127.0.0.1", "--no-browser",
                                              "--notes", self.notes, "--log", self.log], cwd=self.side, env=self.env(),
                                             stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and self.proc.poll() is None:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/version", timeout=2) as r: r.read()
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

    @property
    def port(self):
        if self._port is None and self.child is not None: self._port = self.child.start()
        return self._port

    @port.setter
    def port(self, value): self._port = value

    def switch_to_child(self, notes, config_dir, relax_limits=False):
        """Under TRIAGE_SERVER: serve this test from the binary, logging to a file outside the fixture's tree (which
        server.LOG_PATH then names, for tests that read the log), with "today" fixed now."""
        p = mock.patch.dict(os.environ, {"RAWTRIAGE_TODAY": os.environ.get("RAWTRIAGE_TODAY") or dt.date.today().isoformat()})
        p.start(); self.addCleanup(p.stop)
        self.child = ChildServer(self, notes, config_dir, relax_limits)
        p = mock.patch.object(server, "LOG_PATH", self.child.log); p.start(); self.addCleanup(p.stop)

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
    spec = importlib.util.spec_from_file_location("parity_ledger", os.path.join(HERE, "tools", "parity_ledger.py"))
    ledger = importlib.util.module_from_spec(spec); spec.loader.exec_module(ledger)
    ported = {t.id: t.reason for t in ledger.build().tests if t.outcome == "ported"}
    run = unittest.TestCase.run

    def run_unless_ported(self, result=None):
        why = ported.get(self.id())
        if why is not None:
            method = getattr(self, self._testMethodName)
            setattr(self, self._testMethodName, unittest.skip(f"ported (ledger draft): {why}")(method))
        return run(self, result)
    unittest.TestCase.run = run_unless_ported


if SWITCHED: _skip_what_the_ledger_ports()
