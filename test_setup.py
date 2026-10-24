#!/usr/bin/env python3
"""v2.12: first run, when the notes folder doesn't exist yet, the page asks where to create it."""
import json
import os
import shutil
import unittest
from unittest import mock

import server
from testkit import Fixture


class FirstRun(Fixture):
    def setUp(self):
        super().setUp()
        shutil.rmtree(self.notes)  # a fresh install: nothing at the default place yet
        p = mock.patch.object(server, "NOTES_SOURCE", "default"); p.start(); self.addCleanup(p.stop)

    def config(self):
        with open(server.config_path(), encoding="utf-8") as fh: return json.load(fh)

    def test_setup_needed_only_while_the_folder_is_missing(self):
        st = self.get("/api/setup")
        self.assertTrue(st["needed"]); self.assertEqual(st["notes_dir"], self.notes)
        os.makedirs(self.notes)
        self.assertFalse(self.get("/api/setup")["needed"])

    def test_creates_the_folder_with_done_and_template(self):
        target = os.path.join(self.tmp, "home", "Notes")
        r = self.post("/api/setup", {"path": target})
        self.assertEqual((r["needed"], r["notes_dir"], r["projects_dir"], r["existed"]), (False, target, os.path.dirname(target), False))
        self.assertTrue(os.path.isdir(os.path.join(target, "done")))
        with open(os.path.join(target, "_template.md"), encoding="utf-8") as fh: self.assertIn("## What's the question / problem?", fh.read())
        self.assertEqual(self.config(), {"notes": target})
        self.assertEqual((server.NOTES, server.PROJECTS), (target, os.path.dirname(target)))
        self.assertEqual(self.get("/api/notes")["notes_dir"], target)  # the running server switched over

    def test_existing_folder_is_adopted_untouched(self):
        target = os.path.join(self.tmp, "Mine"); os.makedirs(target)
        with open(os.path.join(target, "_template.md"), "w") as fh: fh.write("my own\n")
        r = self.post("/api/setup", {"path": target})
        self.assertTrue(r["existed"])
        with open(os.path.join(target, "_template.md")) as fh: self.assertEqual(fh.read(), "my own\n")

    def test_tilde_and_trailing_slash(self):
        with mock.patch.dict(os.environ, {"HOME": self.tmp, "USERPROFILE": self.tmp}):  # ~ is USERPROFILE on Windows
            r = self.post("/api/setup", {"path": "~/Projects/RawNotes/"})
        self.assertEqual(r["notes_dir"], os.path.join(self.tmp, "Projects", "RawNotes"))

    def test_bad_paths_are_refused_and_nothing_is_saved(self):
        afile = os.path.join(self.tmp, "afile"); open(afile, "w").close()
        for bad in ("", "   ", "relative/notes", "/", "/top", "/top/", afile, "/x\0y", 7, None):
            self.post("/api/setup", {"path": bad}, ok=False)
        self.assertFalse(os.path.exists(server.config_path()))
        self.assertEqual(server.NOTES, self.notes)

    def test_refused_once_set_up(self):
        os.makedirs(self.notes)
        d = self.post("/api/setup", {"path": os.path.join(self.tmp, "Other")}, ok=False)
        self.assertIn("already set up", d["error"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "Other")))

    def test_config_keeps_other_keys(self):
        os.makedirs(server.CONFIG_DIR)
        with open(server.config_path(), "w") as fh: json.dump({"other": 1}, fh)
        target = os.path.join(self.tmp, "N")
        self.post("/api/setup", {"path": target})
        self.assertEqual(self.config(), {"other": 1, "notes": target})

    def test_saved_notes_dir(self):
        self.assertIsNone(server.saved_notes_dir())
        os.makedirs(server.CONFIG_DIR)
        ab = os.path.abspath("/a/b")  # /a/b, or X:\a\b on Windows
        for content, want in ((json.dumps({"notes": ab}), ab), ('{"notes": "rel"}', None), ("[1]", None), ("not json", None)):
            with open(server.config_path(), "w") as fh: fh.write(content)
            self.assertEqual(server.saved_notes_dir(), want, content)

    def setup_as(self, peer, host):
        """POST /api/setup as if from peer with that Host header; returns (code, body)."""
        body = json.dumps({"path": os.path.join(self.tmp, "Phone")}).encode()
        h = server.H.__new__(server.H); h.client_address = (peer, 1)
        sent = []; h._send = lambda code, body, *a: sent.append((code, body))
        h.headers = {"Host": host, "Content-Type": "application/json", "Content-Length": str(len(body))}
        h.rfile = mock.Mock(read=lambda n: body); h.path = "/api/setup"
        h.do_POST(); return sent[0]

    def test_only_this_machine_may_set_up(self):
        p = mock.patch.object(server, "BIND_HOST", "0.0.0.0"); p.start(); self.addCleanup(p.stop)
        for peer, host in (("192.168.1.9", "192.168.1.5:8765"), ("127.0.0.1", "evil.example:8765")):
            code, body = self.setup_as(peer, host)
            self.assertEqual(code, 400); self.assertIn("machine running the server", body["error"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "Phone")))
        self.assertEqual(self.setup_as("127.0.0.1", "127.0.0.1:8765")[0], 200)

    def test_scan_is_empty_not_an_error_before_setup(self):
        d = self.get("/api/notes")
        self.assertEqual((d["live"], d["done"]), ([], []))


if __name__ == "__main__":
    unittest.main()
