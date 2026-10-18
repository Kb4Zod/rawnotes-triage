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

    def test_version_endpoint_and_running_server(self):
        info = self.get("/api/version")
        self.assertEqual(info["version"], server.VERSION)
        self.assertEqual(info["pid"], os.getpid())
        self.assertEqual(server.running_server(self.port)["version"], server.VERSION)

    def test_running_server_none_when_port_free(self):
        import socket
        s = socket.socket(); s.bind(("127.0.0.1", 0)); free = s.getsockname()[1]; s.close()
        self.assertIsNone(server.running_server(free))

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

    def stub_gh(self, *results):
        calls = []
        def fake(query):
            calls.append(query)
            r = results[min(len(calls), len(results)) - 1]
            if isinstance(r, Exception): raise r
            return r
        old_gh, old_cache = server.gh_graphql, dict(server._activity)
        server.gh_graphql = fake; server._activity.update(at=0.0, data=None)
        def restore(): server.gh_graphql = old_gh; server._activity.update(old_cache)
        self.addCleanup(restore)
        return calls

    def repos_answer(self):
        repo = lambda n, priv: {"id": "ID_" + n, "nameWithOwner": "me/" + n, "isPrivate": priv, "url": "https://github.com/me/" + n,
                                "pushedAt": server.today().isoformat() + "T00:00:00Z"}
        return {"viewer": {"id": "U1", "login": "me", "repositories": {"nodes": [
            repo("pub", False), repo("secret", True),
            {**repo("old", False), "pushedAt": "2001-01-01T00:00:00Z"}]}}}  # not pushed this year: never queried

    def history_answer(self, pub_days, secret_days):
        node = lambda days: {"defaultBranchRef": {"target": {"history": {"pageInfo": {"hasNextPage": False},
            "nodes": [{"committedDate": f"{d.isoformat()}T12:00:00Z"} for d in days]}}}}
        return {"r0": node(pub_days), "r1": node(secret_days)}

    def test_activity_counts_pushed_commits_including_private_repos(self):
        t = server.today(); day = lambda n: t - server.dt.timedelta(days=n)
        calls = self.stub_gh(self.repos_answer(), self.history_answer([day(1), day(1), day(2)], [day(1), day(40)]))
        a = self.get("/api/activity")
        self.assertEqual((a["login"], a["total"], a["active_days"], a["streak"]), ("me", 5, 3, 2))
        self.assertEqual([(r["name"], r["count"], r["private"]) for r in a["repos"]], [("me/pub", 3, False), ("me/secret", 1, True)])
        self.assertNotIn("ID_old", calls[1])  # repo with no push this year isn't asked for commits
        self.assertIn('author: {id: "U1"}', calls[1])  # only your commits
        days = {d["date"]: d for w in a["weeks"] for d in w}
        self.assertEqual(days[day(1).isoformat()]["count"], 3); self.assertEqual(days[day(1).isoformat()]["level"], 4)
        self.assertEqual(len(a["weeks"]), 53)

    def test_activity_follows_pagination(self):
        t = server.today()
        page1 = self.history_answer([t], [])
        page1["r0"]["defaultBranchRef"]["target"]["history"]["pageInfo"] = {"hasNextPage": True, "endCursor": "CUR"}
        calls = self.stub_gh(self.repos_answer(), page1, {"r0": self.history_answer([t - server.dt.timedelta(days=3)], [])["r0"]})
        a = self.get("/api/activity")
        self.assertEqual(a["total"], 2)
        self.assertIn('after: "CUR"', calls[2]); self.assertNotIn("ID_secret", calls[2])

    def test_activity_empty_when_nothing_pushed(self):
        self.stub_gh({"viewer": {"id": "U1", "login": "me", "repositories": {"nodes": []}}})
        a = self.get("/api/activity")
        self.assertEqual((a["total"], a["repos"], a["streak"]), (0, [], 0))

    def test_activity_is_cached_until_refresh(self):
        t = server.today()
        hist = self.history_answer([t], [])
        calls = self.stub_gh(self.repos_answer(), hist, self.repos_answer(), hist)
        self.get("/api/activity"); self.get("/api/activity")
        self.assertEqual(len(calls), 2)  # one fetch = repo list + history
        self.get("/api/activity?refresh=1")
        self.assertEqual(len(calls), 4)

    def test_activity_error_without_cache(self):
        self.stub_gh(RuntimeError("gh is not installed"))
        self.assertEqual(self.get("/api/activity"), {"error": "gh is not installed"})

    def test_activity_failure_keeps_last_good_data_marked_stale(self):
        self.stub_gh(self.repos_answer(), self.history_answer([server.today()], []), RuntimeError("network down"))
        self.get("/api/activity")
        a = self.get("/api/activity?refresh=1")
        self.assertTrue(a["stale"]); self.assertEqual(a["error"], "network down"); self.assertEqual(a["total"], 1)


if __name__ == "__main__":
    unittest.main()
