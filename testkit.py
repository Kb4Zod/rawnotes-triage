"""Shared fixture for the v2.05 regression suites: a real server on a temp projects root, never the real notes."""
import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import server


class Fixture(unittest.TestCase):
    """HTTP server bound to a temporary RawNotes + projects root. Helpers read/write only inside it."""
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.notes = os.path.join(self.tmp, "RawNotes"); os.makedirs(self.notes)
        self.saved = (server.NOTES, server.PROJECTS, server.log, server.LOG_PATH, server.CONFIG_DIR)
        server.CONFIG_DIR = os.path.join(self.tmp, "config")  # never read or write the real saved GitHub token
        server.forget_github()
        server.NOTES, server.PROJECTS = self.notes, self.tmp
        server.log = lambda msg: None  # never write test actions into the real triage.log
        server.LOG_PATH = os.path.join(self.tmp, "triage.log")
        server._undo.clear(); server._git_cache.clear()
        for c in (server._notes_cache, server._meta_cache, server._proj_cache): c.clear()
        self.addCleanup(self._restore)
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
