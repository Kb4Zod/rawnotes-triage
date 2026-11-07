#!/usr/bin/env python3
"""v2.14: follow-ups from the v2.13 gate review. The dependency check's tree walk runs outside the lock; /api/new's
path keeps a folder named like a memory key; the bare `rawtriage` session survives every server failure; a read the
server fails on exits 1, not 4; a browser off this machine gets the home folder as ~ in the listings' path fields.
Every test runs on a temp notes folder (testkit) or a stub server on a free port, never the real folders."""
import json
import os
import socket
import stat
import subprocess
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

import depcheck
import server
from testkit import Fixture, cli_argv

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, "bin", "rawtriage")


class DepsOutsideTheLock(Fixture):
    def test_the_tree_walk_runs_without_holding_the_lock(self):
        """A big node_modules made the walk take seconds while every other action waited on the lock."""
        self.project("demo", notes="# Demo\n\n- status: promoted\n")
        held = []
        def walk(path, limit=None):
            held.append(server._lock._readers or server._lock._writer is not None); return False
        with mock.patch.object(server, "tree_too_big", walk):
            self.assertNotIn("undo_warning", self.get("/api/project/deps?name=demo&kind=active"))
        self.assertEqual(held, [False])
        with mock.patch.object(server, "tree_too_big", lambda p, limit=None: True):
            self.assertIn("can't be undone", self.get("/api/project/deps?name=demo&kind=active")["undo_warning"])


class NewNotePath(Fixture):
    def test_a_notes_folder_named_like_a_memory_key_comes_back_whole(self):
        """tilde() also rewrites the key spelling (-home-me-…), which mangled a path that happens to contain it."""
        odd = os.path.join(self.tmp, "Notes" + depcheck.claude_key(self.tmp) + "-x"); os.makedirs(odd)
        with open(os.path.join(odd, "_template.md"), "w") as fh: fh.write("# <topic>\n\n- date: YYYY-MM-DD\n- status: open\n- type: research\n")
        self.serve_with(notes=odd, env={"HOME": self.tmp})  # under TRIAGE_SERVER: --notes there, and that HOME from its start
        with mock.patch.object(server, "NOTES", odd), mock.patch.dict(os.environ, {"HOME": self.tmp}):
            r = self.post("/api/new", {"slug": "keyed"})
            self.assertTrue(r["path"].startswith("~/"), r["path"])
            self.assertEqual(os.path.expanduser(r["path"]), os.path.join(odd, r["file"]))

    def test_tilde_path_only_touches_a_leading_home(self):
        self.assertEqual(server.tilde_path("/h/me/x/-h-me-y", "/h/me"), "~/x/-h-me-y")
        self.assertEqual(server.tilde_path("/h/me", "/h/me"), "~")
        self.assertEqual(server.tilde_path("/h/mex/y", "/h/me"), "/h/mex/y")
        self.assertEqual(server.tilde_path("/srv/h/me/y", "/h/me"), "/srv/h/me/y")


class OffThisMachinePaths(Fixture):
    """--host 0.0.0.0: a phone (or a rebound DNS name) gets abs_path / notes_dir / projects_dir with the home as ~."""
    def setUp(self):
        super().setUp()
        p = mock.patch.object(server, "BIND_HOST", "0.0.0.0"); p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(server, "DEPS", depcheck.Sources(self.tmp, proc=os.path.join(self.tmp, "proc"), crontab=lambda: "")); p.start(); self.addCleanup(p.stop)
        self.serve_with(host="0.0.0.0", env=self.deps_env(server.DEPS))  # under TRIAGE_SERVER: --host 0.0.0.0, these deps sources
        self.note("2026-10-01-x.md", body=f"Kept as written: {self.tmp}/elsewhere")
        self.project("demo", notes="# Demo\n\n- status: promoted\n")

    def remote(self, path):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers={"Host": f"192.168.1.5:{self.port}"})
        with urllib.request.urlopen(req) as r: return json.loads(r.read())

    ROUTES = ("/api/notes", "/api/projects", "/api/project?name=demo&kind=active", "/api/setup")

    def fields(self, body, out=None):
        out = [] if out is None else out
        if isinstance(body, dict):
            for k, v in body.items():
                if k in server.PATH_KEYS: out.append(v)
                else: self.fields(v, out)
        elif isinstance(body, list):
            for v in body: self.fields(v, out)
        return out

    def test_the_listings_spell_the_home_as_tilde_off_this_machine_only(self):
        for route in self.ROUTES:
            there, here = self.fields(self.remote(route)), self.fields(self.get(route))
            self.assertTrue(there, route); self.assertEqual(len(there), len(here), route)
            for a, b in zip(there, here):
                self.assertTrue(a.startswith("~"), (route, a)); self.assertNotIn(self.tmp, a)
                self.assertEqual(os.path.join(self.tmp, a[2:]) if a != "~" else self.tmp, b)  # same path, home spelled ~
        notes = self.remote("/api/notes")
        self.assertEqual(notes["notes_dir"], "~/RawNotes"); self.assertEqual(notes["live"][0]["abs_path"], "~/RawNotes/2026-10-01-x.md")
        self.assertEqual(self.remote("/api/projects")["projects"][0]["path"], "active/demo")  # relative fields as they were

    def test_a_change_off_this_machine_still_works(self):
        r = self.remote("/api/notes")["live"][0]
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/action", method="POST",
                                     data=json.dumps({"file": r["file"], "action": "keep", "status": "cooking"}).encode(),
                                     headers={"Host": f"192.168.1.5:{self.port}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp: self.assertEqual(resp.status, 200)
        self.assertEqual(self.get("/api/notes")["live"][0]["status"], "cooking")


def free_port():
    with socket.socket() as s: s.bind(("127.0.0.1", 0)); return s.getsockname()[1]


class FailingServer(unittest.TestCase):
    """A rawnotes-triage that answers /api/version but fails (HTTP 500) every other read."""
    def setUp(self):
        self.port = free_port(); hits = self.hits = []
        class Broken(BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                ok = self.path == "/api/version"
                body = json.dumps({"app": "rawnotes-triage", "version": "x", "pid": 1} if ok else {"error": "disk on fire"}).encode()
                self.send_response(200 if ok else 500); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            def log_message(self, *a): pass
        httpd = HTTPServer(("127.0.0.1", self.port), Broken); httpd.daemon_threads = True; self.addCleanup(httpd.server_close)
        threading.Thread(target=httpd.serve_forever, daemon=True).start(); self.addCleanup(httpd.shutdown)
        self.bin = os.path.join(self.tmpdir(), "bin"); os.makedirs(self.bin)

    def tmpdir(self):
        import tempfile, shutil
        d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d, ignore_errors=True); return d

    def rt(self, *args):
        env = {**os.environ, "RAWTRIAGE_PORT": str(self.port), "PATH": self.bin + os.pathsep + os.environ["PATH"]}
        return subprocess.run([*cli_argv(CLI), *args], capture_output=True, text=True, timeout=60, env=env)

    def test_a_read_the_server_fails_on_exits_1_and_says_nothing_changed(self):
        for args in (["-l"], ["where"], ["check"]):
            r = self.rt(*args)
            self.assertEqual(r.returncode, 1, (args, r.stderr)); self.assertIn("disk on fire", r.stderr)
            self.assertIn("nothing was changed", r.stderr); self.assertNotIn("part-way", r.stderr)

    def test_the_session_still_starts_when_the_server_fails(self):
        """session() caught only NoServer and Refused: a 500 from /api/setup stopped it with exit 4 before claude ran."""
        claude = os.path.join(self.bin, "claude")
        with open(claude, "w") as fh: fh.write("#!/bin/sh\necho claude-started\n")
        os.chmod(claude, os.stat(claude).st_mode | stat.S_IEXEC)
        r = self.rt()
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn("claude-started", r.stdout)
        self.assertIn("starting the session anyway", r.stderr); self.assertIn("/api/setup", self.hits)


if __name__ == "__main__":
    unittest.main()
