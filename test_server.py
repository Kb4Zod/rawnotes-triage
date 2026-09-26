#!/usr/bin/env python3
"""Tests at the HTTP API seam: run against a real server bound to a temp projects root."""
import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

import server


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.notes = os.path.join(self.tmp, "RawNotes")
        os.makedirs(self.notes)
        self.old_notes, self.old_projects = server.NOTES, server.PROJECTS
        server.NOTES = self.notes
        server.PROJECTS = self.tmp
        self.addCleanup(self._restore)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.httpd.shutdown)
        self.addCleanup(self.httpd.server_close)

    def _restore(self):
        server.NOTES, server.PROJECTS = self.old_notes, self.old_projects

    def make_project(self, name):
        path = os.path.join(self.tmp, "active", name)
        os.makedirs(path)
        return path

    def get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as r:
            return json.loads(r.read())

    def test_projects_list_includes_abs_path(self):
        self.make_project("demo")
        projects = self.get("/api/projects")["projects"]
        self.assertEqual(len(projects), 1)
        p = projects[0]
        self.assertEqual(p["path"], "active/demo")
        self.assertEqual(p["abs_path"], os.path.join(self.tmp, "active", "demo"))
        self.assertTrue(os.path.isabs(p["abs_path"]))

    def test_project_detail_includes_abs_path(self):
        self.make_project("demo")
        detail = self.get("/api/project?name=demo")
        self.assertEqual(detail["path"], "active/demo")
        self.assertEqual(detail["abs_path"], os.path.join(self.tmp, "active", "demo"))
        self.assertTrue(os.path.isabs(detail["abs_path"]))

    def test_abs_path_not_realpath_resolved(self):
        real_target = os.path.join(self.tmp, "elsewhere")
        os.makedirs(real_target)
        active_dir = os.path.join(self.tmp, "active")
        os.makedirs(active_dir)
        link = os.path.join(active_dir, "linked")
        os.symlink(real_target, link)

        projects = self.get("/api/projects")["projects"]
        p = next(x for x in projects if x["name"] == "linked")
        self.assertEqual(p["abs_path"], link)
        self.assertNotEqual(p["abs_path"], os.path.realpath(link))

        detail = self.get("/api/project?name=linked")
        self.assertEqual(detail["abs_path"], link)
        self.assertNotEqual(detail["abs_path"], os.path.realpath(link))

    def test_abs_path_honours_non_default_projects_root(self):
        other_root = os.path.join(self.tmp, "other-root")
        other_notes = os.path.join(other_root, "RawNotes")
        os.makedirs(other_notes)
        os.makedirs(os.path.join(other_root, "active", "proj"))
        server.NOTES, server.PROJECTS = other_notes, other_root

        detail = self.get("/api/project?name=proj")
        self.assertEqual(detail["abs_path"], os.path.join(other_root, "active", "proj"))

    def test_abs_path_with_spaces_and_special_chars(self):
        name = "my project (v2)"
        self.make_project(name)
        detail = self.get("/api/project?name=" + urllib.parse.quote(name))
        self.assertEqual(detail["abs_path"], os.path.join(self.tmp, "active", name))

    def make_note(self, name, sub=""):
        d = os.path.join(self.notes, sub) if sub else self.notes
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            f.write("# A note\n\n- status: open\n")

    def test_live_note_includes_abs_path(self):
        self.make_note("2026-01-02-idea.md")
        note = self.get("/api/notes")["live"][0]
        self.assertEqual(note["file"], "2026-01-02-idea.md")
        self.assertEqual(note["abs_path"], os.path.join(self.notes, "2026-01-02-idea.md"))
        self.assertTrue(os.path.isabs(note["abs_path"]))

    def test_done_note_includes_abs_path_under_done(self):
        self.make_note("2026-01-03-old.md", "done")
        note = self.get("/api/notes")["done"][0]
        self.assertEqual(note["file"], "done/2026-01-03-old.md")
        self.assertEqual(note["abs_path"], os.path.join(self.notes, "done", "2026-01-03-old.md"))

    def test_note_abs_path_with_spaces_and_special_chars(self):
        name = "2026-01-04-my note (v2) & more.md"
        self.make_note(name)
        note = self.get("/api/notes")["live"][0]
        self.assertEqual(note["abs_path"], os.path.join(self.notes, name))


if __name__ == "__main__":
    unittest.main()
