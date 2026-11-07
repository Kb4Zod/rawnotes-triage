#!/usr/bin/env python3
"""Tests at the HTTP API seam: run against a real server bound to a temp projects root."""
import json
import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock
import urllib.parse
import urllib.error
import urllib.request

from testkit import Fixture
from http.server import ThreadingHTTPServer

import server
from testkit import SERVER_BIN, Switchable, isolate_git, undo_list


class ApiTestCase(Switchable, unittest.TestCase):
    def setUp(self):
        isolate_git(self)  # promote runs git: never with the real global config or hooks
        p = unittest.mock.patch.object(server, "SHIP_NEEDS_GITHUB", ()); p.start(); self.addCleanup(p.stop)  # the GitHub gate on Ship has its own tests (test_github_push.py)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.notes = os.path.join(self.tmp, "RawNotes")
        os.makedirs(self.notes)
        self.old_notes, self.old_projects, self.old_log = server.NOTES, server.PROJECTS, server.log
        self.old_log_path = server.LOG_PATH
        self.old_config_dir = server.CONFIG_DIR
        server.CONFIG_DIR = os.path.join(self.tmp, "config")  # tunables are read from config.json: never the real one
        server.NOTES = self.notes
        server.PROJECTS = self.tmp
        server.log = lambda msg: None  # never write test actions into the real triage.log
        self.addCleanup(self._restore)
        if SERVER_BIN: return self.switch_to_child(self.notes, server.CONFIG_DIR)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=lambda: self.httpd.serve_forever(poll_interval=0.02), daemon=True)
        self.thread.start()
        self.addCleanup(self.httpd.shutdown)
        self.addCleanup(self.httpd.server_close)

    def _restore(self):
        server.NOTES, server.PROJECTS = self.old_notes, self.old_projects
        server.CONFIG_DIR = self.old_config_dir
        server.log = self.old_log

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

    def write(self, project, name, text):
        with open(os.path.join(self.tmp, "active", project, name), "w", encoding="utf-8") as f: f.write(text)

    def done(self, project):
        return self.get("/api/project?name=" + project)["done"]

    def test_done_from_promote_line(self):
        self.make_project("a"); self.write("a", "NOTES.md", "# A\n\n## Plan\n\n- promoted on 2026-10-01\n- definition of done: Ships to the phone.\n- first step:\n")
        self.assertEqual(self.done("a"), {"defined": True, "text": "Ships to the phone.", "source": "NOTES.md"})
        self.assertTrue(next(p for p in self.get("/api/projects")["projects"] if p["name"] == "a")["done_defined"])

    def test_done_prose_paragraph_and_heading(self):
        self.make_project("b"); self.write("b", "NOTES.md", "## Plan\n\nDefinition of done: open it on the phone\nand use it on cell data.\n\nOther text.\n")
        self.assertEqual(self.done("b")["text"], "open it on the phone and use it on cell data.")
        self.make_project("c"); self.write("c", "README.md", "# C\n\n## What does done look like?\n\nA working demo.\n\n## Next\n\nstuff\n")
        self.assertEqual(self.done("c"), {"defined": True, "text": "A working demo.", "source": "README.md"})

    def test_done_missing_blank_or_placeholder_is_not_defined(self):
        self.make_project("none")
        self.assertEqual(self.done("none"), {"defined": False, "text": "", "source": None})
        self.make_project("ph"); self.write("ph", "NOTES.md", "## Plan\n\n- definition of done: <fill in>\n- first step:\n")
        self.assertEqual(self.done("ph"), {"defined": False, "text": "", "source": "NOTES.md"})  # tells the page which file to edit
        self.make_project("blank"); self.write("blank", "NOTES.md", "- definition of done:\n- first step: x\n")
        self.assertFalse(self.done("blank")["defined"])
        flags = {p["name"]: p["done_defined"] for p in self.get("/api/projects")["projects"]}
        self.assertEqual(flags, {"none": False, "ph": False, "blank": False})

    def test_done_mention_mid_sentence_is_not_a_definition(self):
        self.make_project("d"); self.write("d", "README.md", "| Promote | stamps dates and your definition of done |\n")
        self.assertFalse(self.done("d")["defined"])

    def post_done(self, name, text, expected=""):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/done", method="POST",
                                     headers={"Content-Type": "application/json"}, data=json.dumps({"name": name, "text": text, "expected": expected}).encode())
        try:
            with urllib.request.urlopen(req) as r: return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

    def read(self, project, name):
        with open(os.path.join(self.tmp, "active", project, name), encoding="utf-8") as f: return f.read()

    def test_set_done_replaces_promote_line_and_nothing_else(self):
        self.make_project("a"); self.write("a", "NOTES.md", "# A\n\n## Plan\n\n- promoted on 2026-10-01\n- definition of done: Old goal.\n- first step: go\n")
        code, r = self.post_done("a", "  New   goal\nline two ", "Old goal.")
        self.assertEqual((code, r["file"]), (200, "NOTES.md"))
        self.assertEqual(self.read("a", "NOTES.md"), "# A\n\n## Plan\n\n- promoted on 2026-10-01\n- definition of done: New goal line two\n- first step: go\n")
        self.assertEqual(self.done("a")["text"], "New goal line two")

    def test_set_done_replaces_whole_prose_paragraph(self):
        self.make_project("b"); self.write("b", "NOTES.md", "## Plan\n\nDefinition of done: open it on the phone\nand use it on cell data.\n\nOther text.\n")
        self.assertEqual(self.post_done("b", "Works on cell data", "open it on the phone and use it on cell data.")[0], 200)
        self.assertEqual(self.read("b", "NOTES.md"), "## Plan\n\nDefinition of done: Works on cell data\n\nOther text.\n")

    def test_set_done_replaces_heading_section(self):
        self.make_project("c"); self.write("c", "README.md", "# C\n\n## What does done look like?\n\nA demo.\n\n## Next\n\nstuff\n")
        self.assertEqual(self.post_done("c", "A shipped app", "A demo.")[0], 200)
        self.assertEqual(self.read("c", "README.md"), "# C\n\n## What does done look like?\n\nA shipped app\n\n## Next\n\nstuff\n")

    def raw(self, project, name, data=None):
        p = os.path.join(self.tmp, "active", project, name)
        if data is None:
            with open(p, "rb") as f: return f.read()
        with open(p, "wb") as f: f.write(data)

    def test_set_done_keeps_crlf_line_endings(self):
        self.make_project("w"); self.raw("w", "NOTES.md", b"## Plan\r\n\r\n- definition of done: Old.\r\n- first step: go\r\n")
        self.assertEqual(self.post_done("w", "New", "Old.")[0], 200)
        self.assertEqual(self.raw("w", "NOTES.md"), b"## Plan\r\n\r\n- definition of done: New\r\n- first step: go\r\n")

    def test_set_done_keeps_undecodable_bytes(self):
        self.make_project("u"); self.raw("u", "NOTES.md", b"caf\xe9 notes\n- definition of done: Old.\n")
        self.assertEqual(self.post_done("u", "New", "Old.")[0], 200)
        self.assertEqual(self.raw("u", "NOTES.md"), b"caf\xe9 notes\n- definition of done: New\n")

    def test_heading_definition_is_only_its_first_paragraph(self):
        self.make_project("s"); self.write("s", "README.md", "# S\n\n## Definition of done\n\nWorks on the phone.\n\nBackground that is not the definition.\n\n- a list\n")
        self.assertEqual(self.done("s")["text"], "Works on the phone.")
        self.assertEqual(self.post_done("s", "Ships", "Works on the phone.")[0], 200)
        self.assertEqual(self.read("s", "README.md"), "# S\n\n## Definition of done\n\nShips\n\nBackground that is not the definition.\n\n- a list\n")

    def request(self, path, method="GET", headers=None, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method, headers=headers or {}, data=body)
        try:
            with urllib.request.urlopen(req) as r: return r.status
        except urllib.error.HTTPError as e: return e.code

    def test_browser_guard_refuses_foreign_host_origin_and_form_posts(self):
        self.make_project("g"); self.write("g", "NOTES.md", "- definition of done:\n")
        body = json.dumps({"name": "g", "text": "Hacked", "expected": ""}).encode()
        js = {"Content-Type": "application/json"}
        self.assertEqual(self.request("/api/projects", headers={"Host": "evil.example:8765"}), 403)  # DNS rebinding
        self.assertEqual(self.request("/api/projects", headers={"Origin": "http://evil.example"}), 403)
        self.assertEqual(self.request("/api/done", "POST", {"Content-Type": "text/plain"}, body), 403)  # cross-site form post
        self.assertEqual(self.request("/api/done", "POST", {**js, "Origin": "http://evil.example"}, body), 403)
        self.assertEqual(self.read("g", "NOTES.md"), "- definition of done:\n")
        self.assertEqual(self.request("/api/projects"), 200)
        self.assertEqual(self.request("/api/projects", headers={"Host": f"localhost:{self.port}"}), 200)
        self.assertEqual(self.request("/api/done", "POST", {**js, "Origin": f"http://127.0.0.1:{self.port}"}, body), 200)

    def test_set_done_fills_placeholder_in_place(self):
        self.make_project("ph"); self.write("ph", "NOTES.md", "## Plan\n\n- definition of done: <fill in>\n- first step:\n")
        self.assertEqual(self.post_done("ph", "Ships")[0], 200)
        self.assertEqual(self.read("ph", "NOTES.md"), "## Plan\n\n- definition of done: Ships\n- first step:\n")

    def test_set_done_when_missing_goes_into_plan_or_new_notes(self):
        self.make_project("p"); self.write("p", "NOTES.md", "# P\n\n## Plan\n\n- promoted on 2026-10-01\n")
        self.post_done("p", "Ships")
        self.assertEqual(self.read("p", "NOTES.md"), "# P\n\n## Plan\n\n- definition of done: Ships\n- promoted on 2026-10-01\n")
        self.make_project("q"); self.write("q", "NOTES.md", "# Q\n\nsome notes\n")
        self.post_done("q", "Ships")
        self.assertEqual(self.read("q", "NOTES.md"), "# Q\n\nsome notes\n\n## Plan\n\n- definition of done: Ships\n")
        self.make_project("n"); self.post_done("n", "Ships")
        self.assertEqual(self.read("n", "NOTES.md"), "# n\n\n## Plan\n\n- definition of done: Ships\n")
        self.assertTrue(self.done("n")["defined"])

    def test_set_done_refuses_when_file_changed_since_opened(self):
        self.make_project("s"); self.write("s", "NOTES.md", "- definition of done: Edited elsewhere\n")
        code, r = self.post_done("s", "Mine", "What I saw earlier")
        self.assertEqual(code, 400); self.assertIn("changed on disk", r["error"])
        self.assertEqual(self.read("s", "NOTES.md"), "- definition of done: Edited elsewhere\n")

    def test_set_done_refuses_empty_placeholder_unknown_and_too_long(self):
        self.make_project("e"); self.write("e", "NOTES.md", "- definition of done: Keep\n")
        for text in ("", "   ", "<fill in>", "x" * 601): self.assertEqual(self.post_done("e", text, "Keep")[0], 400)
        self.assertEqual(self.post_done("nope", "Ships")[0], 400)
        self.assertEqual(self.post_done("../e", "Ships")[0], 400)
        self.assertEqual(self.read("e", "NOTES.md"), "- definition of done: Keep\n")

    def test_set_done_will_not_write_through_a_symlink_out_of_the_project(self):
        self.make_project("l"); outside = os.path.join(self.tmp, "outside.md")
        with open(outside, "w") as f: f.write("- definition of done: Safe\n")
        os.symlink(outside, os.path.join(self.tmp, "active", "l", "NOTES.md"))
        self.assertEqual(self.post_done("l", "Hacked", "Safe")[0], 400)
        self.assertEqual(open(outside).read(), "- definition of done: Safe\n")

    def test_experiments_are_projects_labelled_by_kind(self):
        self.make_project("ship"); os.makedirs(os.path.join(self.tmp, "experiments", "spike"))
        os.makedirs(os.path.join(self.tmp, "experiments", "ship"))  # same name in both folders stays two projects
        ps = {p["path"]: p for p in self.get("/api/projects")["projects"]}
        self.assertEqual(set(ps), {"active/ship", "experiments/spike", "experiments/ship"})
        self.assertEqual(ps["experiments/spike"]["kind"], "experiments"); self.assertEqual(ps["active/ship"]["kind"], "active")
        d = self.get("/api/project?name=spike&kind=experiments")
        self.assertEqual((d["kind"], d["path"]), ("experiments", "experiments/spike"))
        self.assertEqual(self.get("/api/project?name=ship")["path"], "active/ship")  # kind defaults to active
        with self.assertRaises(urllib.error.HTTPError): self.get("/api/project?name=spike")  # not an active project
        with self.assertRaises(urllib.error.HTTPError): self.get("/api/project?name=spike&kind=learning")

    def test_set_done_on_an_experiment(self):
        path = os.path.join(self.tmp, "experiments", "spike"); os.makedirs(path)
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/done", method="POST", headers={"Content-Type": "application/json"},
                                     data=json.dumps({"name": "spike", "kind": "experiments", "text": "We know if it works."}).encode())
        with urllib.request.urlopen(req) as r: self.assertEqual(r.status, 200)
        self.assertIn("- definition of done: We know if it works.", open(os.path.join(path, "NOTES.md")).read())
        self.assertEqual(self.post_done("spike", "Wrong folder")[0], 400)  # no kind -> active -> unknown

    def stub_rest(self, answers):
        """Replace the GitHub layer: answers maps a REST path to its JSON (or an Exception to raise)."""
        calls = []
        def fake(path):
            calls.append(path)
            r = answers[path]
            if isinstance(r, Exception): raise r
            return r
        old, old_login = server.gh_rest, server._viewer["login"]
        server.gh_rest = fake; server._viewer["login"] = "me"
        def restore(): server.gh_rest = old; server._viewer["login"] = old_login
        self.addCleanup(restore)
        return calls

    def get_err(self, path):
        try: self.get(path)
        except urllib.error.HTTPError as e: return e.code, json.loads(e.read())["error"]
        self.fail("expected an error for " + path)

    def b64(self, text): return __import__("base64").b64encode(text.encode()).decode()

    def test_repo_overview_for_a_private_repo(self):
        b = "repos/me/secret"
        self.stub_rest({b: {"full_name": "me/secret", "private": True, "description": "Hidden", "html_url": "https://github.com/me/secret",
                            "default_branch": "main", "pushed_at": "2026-10-18T01:00:00Z"},
            b + "/contents": [{"name": "b.txt", "path": "b.txt", "type": "file", "size": 3}, {"name": "docs", "path": "docs", "type": "dir", "size": 0},
                              {"name": "A.md", "path": "A.md", "type": "file", "size": 9}],
            b + "/readme": {"name": "README.md", "content": self.b64("# Hi\nthere")},
            b + "/commits?per_page=15": [{"sha": "abcdef123456", "commit": {"message": "First\n\nbody", "author": {"date": "2026-10-17T00:00:00Z", "name": "N"}}, "author": {"login": "me"}}],
            b + "/pulls?state=all&per_page=10&sort=updated&direction=desc": [{"number": 2, "title": "P", "state": "closed", "merged_at": "x", "updated_at": "2026-10-16T00:00:00Z", "user": {"login": "me"}}],
            b + "/issues?state=open&per_page=15": [{"number": 3, "title": "Bug", "updated_at": "2026-10-15T00:00:00Z", "comments": 1, "user": {"login": "me"}},
                                                    {"number": 2, "title": "P as issue", "updated_at": "2026-10-16T00:00:00Z", "pull_request": {}}]})
        j = self.get("/api/repo?name=me/secret")
        self.assertEqual((j["repo"]["private"], j["repo"]["pushed_at"], j["repo"]["default_branch"]), (True, "2026-10-18", "main"))
        self.assertEqual([e["name"] for e in j["tree"]], ["docs", "A.md", "b.txt"])  # folders first, then by name
        self.assertEqual(j["readme"], {"name": "README.md", "text": "# Hi\nthere"})
        self.assertEqual(j["commits"], [{"sha": "abcdef1", "date": "2026-10-17", "title": "First", "author": "me"}])
        self.assertEqual(j["prs"][0]["state"], "merged")
        self.assertEqual([i["number"] for i in j["issues"]], [3])  # PRs are not issues
        self.assertEqual(j["errors"], {})

    def test_repo_overview_survives_empty_repo_and_missing_readme(self):
        b = "repos/me/empty"; nf = RuntimeError("Not found, or not visible to your GitHub sign-in.")
        self.stub_rest({b: {"full_name": "me/empty", "private": False, "html_url": "https://github.com/me/empty", "default_branch": "main"},
                        b + "/contents": nf, b + "/readme": nf, b + "/commits?per_page=15": [],
                        b + "/pulls?state=all&per_page=10&sort=updated&direction=desc": [], b + "/issues?state=open&per_page=15": []})
        j = self.get("/api/repo?name=me/empty")
        self.assertEqual((j["tree"], j["readme"], j["repo"]["description"]), ([], None, ""))
        self.assertEqual(list(j["errors"]), ["tree"])  # missing README is normal; the rest is reported

    def test_repo_path_dir_file_binary_and_large(self):
        b = "repos/me/r/contents/"
        self.stub_rest({b + "notes": [{"name": "x.md", "path": "notes/x.md", "type": "file", "size": 4}],
                        b + "notes/x.md": {"type": "file", "name": "x.md", "size": 4, "content": self.b64("text"), "html_url": "https://github.com/me/r/blob/main/notes/x.md"},
                        b + "pic.png": {"type": "file", "name": "pic.png", "size": 5, "content": __import__("base64").b64encode(b"\xff\xfe\x00").decode()},
                        b + "big.json": {"type": "file", "name": "big.json", "size": 400000, "content": ""},
                        b + "link": {"type": "symlink", "name": "link", "size": 1}})
        self.assertEqual(self.get("/api/repo/path?name=me/r&path=notes")["entries"][0]["path"], "notes/x.md")
        self.assertEqual(self.get("/api/repo/path?name=me/r&path=notes/x.md")["text"], "text")
        self.assertTrue(self.get("/api/repo/path?name=me/r&path=pic.png")["binary"])
        self.assertTrue(self.get("/api/repo/path?name=me/r&path=big.json")["too_large"])
        self.assertEqual(self.get("/api/repo/path?name=me/r&path=link")["kind"], "other")

    def test_repo_viewer_refuses_other_owners_and_odd_names_and_paths(self):
        calls = self.stub_rest({})
        for q in ("name=someone/else", "name=me/..", "name=me", "name=me/a/b", "name=", "name=me/x&path=../etc", "name=me/x&path=a//b", "name=me/x&path=%5Cwin"):
            path = "/api/repo/path?" if "path=" in q else "/api/repo?"
            self.assertEqual(self.get_err(path + q)[0], 400, q)
        self.assertEqual(calls, [])  # nothing reached GitHub

    def test_repo_viewer_reports_github_errors(self):
        nf = RuntimeError("Not found, or not visible to your GitHub sign-in.")
        self.stub_rest({p: nf for p in ("repos/me/gone", "repos/me/gone/contents", "repos/me/gone/readme", "repos/me/gone/commits?per_page=15",
                                        "repos/me/gone/pulls?state=all&per_page=10&sort=updated&direction=desc", "repos/me/gone/issues?state=open&per_page=15")})
        self.assertEqual(self.get_err("/api/repo?name=me/gone"), (400, "Not found, or not visible to your GitHub sign-in."))

    def test_project_detail_names_its_github_repo(self):
        path = self.make_project("withgit")
        import subprocess
        subprocess.run(["git", "init", "-q"], cwd=path, check=True)
        subprocess.run(["git", "remote", "add", "origin", "git@github.com:me/withgit.git"], cwd=path, check=True)
        self.assertEqual(self.get("/api/project?name=withgit")["repo"], "me/withgit")
        self.make_project("nogit"); self.assertEqual(self.get("/api/project?name=nogit")["repo"], "")

    def test_github_remote_spellings(self):
        for url in ("ssh://git@ssh.github.com:443/me/r.git", "git@github.com:me/r.git", "git@ssh.github.com:me/r.git",
                    "https://github.com/me/r.git", "https://github.com/me/r", "ssh://git@github.com/me/r.git", "https://github.com/me/r/"):
            self.assertEqual(server.github_repo_of(url), "me/r", url)
        for url in ("", None, "https://gitlab.com/me/r.git", "https://github.com/me/r/extra", "https://notgithub.com/me/r",
                    "https://github.com/../..", "https://github.com/me/..", "git@github.com:./r.git"):
            self.assertEqual(server.github_repo_of(url), "", url)

    def git(self, path, *args):
        import subprocess
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com"] + list(args), cwd=path, check=True, capture_output=True, text=True).stdout

    def test_projects_report_whether_they_are_on_github(self):
        self.make_project("folder")
        loc = self.make_project("local"); self.git(loc, "init", "-q"); self.git(loc, "commit", "-q", "--allow-empty", "-m", "one")
        fresh = self.make_project("fresh"); self.git(fresh, "init", "-q"); self.git(fresh, "remote", "add", "origin", "ssh://git@ssh.github.com:443/me/fresh.git")
        gh = self.make_project("pushed"); self.git(gh, "init", "-q"); self.git(gh, "remote", "add", "origin", "ssh://git@ssh.github.com:443/me/pushed.git")
        self.git(gh, "commit", "-q", "--allow-empty", "-m", "one"); self.git(gh, "update-ref", "refs/remotes/origin/main", "HEAD")
        ahead = self.make_project("ahead"); self.git(ahead, "init", "-q"); self.git(ahead, "remote", "add", "origin", "git@github.com:me/ahead.git")
        self.git(ahead, "commit", "-q", "--allow-empty", "-m", "one"); self.git(ahead, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.git(ahead, "commit", "-q", "--allow-empty", "-m", "two"); self.git(ahead, "commit", "-q", "--allow-empty", "-m", "three")
        states = {p["name"]: p["gh"] for p in self.get("/api/projects")["projects"]}
        self.assertEqual(states["folder"], {"git": False, "origin": False, "repo": "", "pushed": False, "unpushed": 0, "after_skeleton": 0, "not_on_github": False})
        self.assertEqual(states["local"], {"git": True, "origin": False, "repo": "", "pushed": False, "unpushed": 0, "after_skeleton": 0, "not_on_github": False})  # git, never on GitHub
        self.assertEqual(states["fresh"], {"git": True, "origin": True, "repo": "me/fresh", "pushed": False, "unpushed": 0, "after_skeleton": 0, "not_on_github": False})  # remote set, nothing pushed
        self.assertEqual(states["pushed"], {"git": True, "origin": True, "repo": "me/pushed", "pushed": True, "unpushed": 0, "after_skeleton": 0, "not_on_github": False})
        self.assertEqual(states["ahead"], {"git": True, "origin": True, "repo": "me/ahead", "pushed": True, "unpushed": 2, "after_skeleton": 0, "not_on_github": False})

    def test_project_with_ssh_443_remote_gets_repo_link_and_state(self):
        path = self.make_project("viaport"); self.git(path, "init", "-q")
        self.git(path, "remote", "add", "origin", "ssh://git@ssh.github.com:443/me/viaport.git")
        d = self.get("/api/project?name=viaport")
        self.assertEqual((d["repo"], d["remote"]), ("me/viaport", "https://github.com/me/viaport"))
        self.assertEqual(d["gh"]["repo"], "me/viaport")


class DecisionBase(Switchable, unittest.TestCase):
    """The decision fixture and helpers, with no tests of its own (so subclasses don't run DecisionTestCase twice)."""
    _restore, get = ApiTestCase._restore, ApiTestCase.get

    def setUp(self):
        ApiTestCase.setUp(self)
        self.clear_undo()
        self.addCleanup(self.clear_undo)

    def note(self, name, title="A note", status="open", body="Some notes."):
        path = os.path.join(self.notes, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"# {title}\n\n- date: {name[:10]}\n- status: {status}\n- type: research\n\n"
                     f"## What's the question / problem?\n\nWhy {title}?\n\n## Notes\n\n### Detail\n\n{body}\n")
        return path

    def post(self, path, body, ok=True):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method="POST",
                                     headers={"Content-Type": "application/json"}, data=json.dumps(body).encode())
        try:
            with urllib.request.urlopen(req) as r: data = json.loads(r.read())
        except urllib.error.HTTPError as e: data = json.loads(e.read())
        if ok: self.assertNotIn("error", data, data)
        return data

    def snapshot(self, root=None):
        out = {}
        for root, _, files in os.walk(root or self.tmp):
            for f in files:
                p = os.path.join(root, f)
                with open(p, "rb") as fh: out[os.path.relpath(p, self.tmp)] = fh.read()
        return out

    record = Fixture.record


class DecisionTestCase(DecisionBase):
    """2.00: decisions, batch, merge, restore, project status and undo, all through the HTTP API."""

    def test_kill_then_undo_puts_everything_back(self):
        self.note("2026-10-01-a.md"); before = self.snapshot()
        r = self.post("/api/action", {"file": "2026-10-01-a.md", "action": "kill", "note": "meh"})
        self.assertIn("Killed", r["msg"])
        self.assertTrue(os.path.exists(os.path.join(self.notes, "done", "2026-10-01-a.md")))
        u = self.post("/api/undo", {})
        self.assertEqual(u["select"], "2026-10-01-a.md")
        self.assertEqual(self.snapshot(), before)
        self.assertIn("Nothing to undo", self.post("/api/undo", {}, ok=False)["error"])

    def test_old_action_names_still_work(self):
        self.note("2026-10-01-a.md"); self.note("2026-10-01-b.md")
        self.post("/api/action", {"file": "2026-10-01-a.md", "action": "archive"})
        self.post("/api/action", {"file": "2026-10-01-b.md", "action": "durable"})
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "reference", "b.md")))
        fates = {n["file"]: n["fate"] for n in self.get("/api/notes")["done"]}
        self.assertEqual(fates, {"done/2026-10-01-a.md": "killed", "done/2026-10-01-b.md": "reference"})

    def test_promote_needs_done_means_and_undo_removes_the_project(self):
        self.note("2026-10-01-a.md"); before = self.snapshot()
        err = self.post("/api/action", {"file": "2026-10-01-a.md", "action": "promote", "dest": "active", "note": ""}, ok=False)
        self.assertIn("done means", err["error"])
        self.post("/api/action", {"file": "2026-10-01-a.md", "action": "promote", "dest": "active", "note": "It ships.", "due_days": 3})
        notes = open(os.path.join(self.tmp, "active", "a", "NOTES.md")).read()
        self.assertIn("- definition of done: It ships.", notes)
        self.post("/api/undo", {})
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "active", "a")))
        self.assertEqual(self.snapshot(), before)

    def test_undo_refuses_when_a_file_changed_since(self):
        self.note("2026-10-01-a.md")
        self.post("/api/action", {"file": "2026-10-01-a.md", "action": "kill"})
        with open(os.path.join(self.notes, "done", "2026-10-01-a.md"), "a") as fh: fh.write("edited by hand\n")
        self.assertIn("changed since", self.post("/api/undo", {}, ok=False)["error"])
        self.assertFalse(os.path.exists(os.path.join(self.notes, "2026-10-01-a.md")))  # nothing half-undone

    def test_batch_kill_is_one_undo_step(self):
        for n in "abc": self.note(f"2026-10-0{'abc'.index(n) + 1}-{n}.md")
        before = self.snapshot()
        r = self.post("/api/batch", {"files": ["2026-10-01-a.md", "2026-10-02-b.md"], "action": "kill", "note": "dupes"})
        self.assertEqual(r["msg"], "Killed 2 notes")
        self.assertEqual([n["file"] for n in self.get("/api/notes")["live"]], ["2026-10-03-c.md"])
        self.post("/api/undo", {})
        self.assertEqual(self.snapshot(), before)
        self.assertIn("batch can", self.post("/api/batch", {"files": ["2026-10-03-c.md"], "action": "promote"}, ok=False)["error"])

    def test_merge_newest_first_then_promote_and_undo(self):
        self.note("2026-10-01-bomb-a.md", "Bomb A", body="alpha"); self.note("2026-10-02-bomb-b.md", "Bomb B", body="beta")
        before = self.snapshot()
        r = self.post("/api/merge", {"files": ["2026-10-01-bomb-a.md", "2026-10-02-bomb-b.md"], "title": "Bomb: all of it", "then": "inbox"})
        merged = open(os.path.join(self.notes, r["file"])).read()
        self.assertLess(merged.index("## Bomb B"), merged.index("## Bomb A"))
        self.assertIn("#### Detail", merged)  # source headings pushed one level down
        self.assertIn("- `done/2026-10-01-bomb-a.md`", merged)
        done = {n["file"]: n["fate"] for n in self.get("/api/notes")["done"]}
        self.assertEqual(set(done.values()), {"merged"})
        self.post("/api/undo", {}); self.assertEqual(self.snapshot(), before)
        r = self.post("/api/merge", {"files": ["2026-10-01-bomb-a.md", "2026-10-02-bomb-b.md"], "title": "Bomb", "then": "promote",
                                     "dest": "active", "note": "Runbook printed.", "slug": "bomb"})
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "active", "bomb", "NOTES.md")))
        self.assertEqual(self.get("/api/notes")["live"], [])
        self.post("/api/undo", {}); self.assertEqual(self.snapshot(), before)

    def test_restore_brings_a_note_back_cooking(self):
        self.note("2026-10-01-a.md"); self.post("/api/action", {"file": "2026-10-01-a.md", "action": "kill"})
        self.post("/api/restore", {"file": "done/2026-10-01-a.md"})
        live = self.get("/api/notes")["live"]
        self.assertEqual([(n["file"], n["status"]) for n in live], [("2026-10-01-a.md", "cooking")])
        self.assertIn("only Archive", self.post("/api/restore", {"file": "2026-10-01-a.md"}, ok=False)["error"])

    def test_project_ship_extend_and_refuse_unknown(self):
        self.note("2026-10-01-a.md")
        self.post("/api/action", {"file": "2026-10-01-a.md", "action": "promote", "dest": "active", "note": "x", "due_days": 2})
        f = self.get("/api/notes")["inflight"][0]
        self.assertEqual((f["path"], f["project"]), ("active/a/NOTES.md", "a"))
        self.post("/api/project/status", {"path": "active/a/NOTES.md", "action": "extend", "days": 7, "note": "Waiting on the parts to arrive."})
        self.assertEqual(self.get("/api/notes")["inflight"][0]["days_left"], 9)
        err = self.post("/api/project/status", {"path": "active/a/NOTES.md", "action": "shipped"}, ok=False)["error"]
        self.assertIn("What happens to a now?", err); self.assertIn("suggested: archive", err)  # a folder needs an outcome
        self.post("/api/project/status", {"path": "active/a/NOTES.md", "action": "shipped", "outcome": "archive"})
        self.assertTrue(self.get("/api/notes")["inflight"][0]["done"])
        self.post("/api/undo", {}); self.assertFalse(self.get("/api/notes")["inflight"][0]["done"])
        self.assertIn("unknown", self.post("/api/project/status", {"path": "../etc/passwd", "action": "shipped"}, ok=False)["error"])

    def test_folder_project_without_promoted_file_can_be_shipped_or_killed(self):
        spike = os.path.join(self.tmp, "experiments", "spike"); os.makedirs(spike)
        old = os.path.join(self.tmp, "experiments", "old"); os.makedirs(old)
        with open(os.path.join(old, "notes.md"), "w") as fh: fh.write("# old\n\nSome notes.\n")
        status = lambda: {p["path"]: p["status"] for p in self.get("/api/projects")["projects"]}
        self.assertEqual(status(), {"experiments/spike": "", "experiments/old": ""})
        self.post("/api/project/status", {"name": "spike", "kind": "experiments", "action": "shipped", "outcome": "archive"})
        self.assertNotIn("experiments/spike", status())
        self.assertIn("- status: shipped", self.record("archive", "spike"))  # a board record; nothing is written into the folder
        self.assertEqual(os.listdir(os.path.join(self.tmp, "archive", "spike")), [])
        self.post("/api/undo", {})
        self.assertEqual(status()["experiments/spike"], "")
        self.assertEqual((os.listdir(spike), self.record("archive", "spike"), self.record("experiments", "spike")), ([], None, None))
        self.post("/api/project/status", {"name": "spike", "kind": "experiments", "action": "kill"}, ok=False)  # a reason is required
        self.post("/api/project/status", {"name": "old", "kind": "experiments", "action": "kill", "note": "Went nowhere."})
        self.assertEqual(open(os.path.join(self.tmp, "archive", "old_abandoned", "notes.md")).read(), "# old\n\nSome notes.\n")  # untouched
        text = self.record("archive", "old_abandoned")
        self.assertIn("- status: dead", text); self.assertIn("Went nowhere.", text)
        for bad in ({"name": "spike", "kind": "experiments", "action": "extend"}, {"name": "spike", "action": "shipped", "outcome": "archive"},
                    {"name": "../experiments/spike", "kind": "active", "action": "shipped", "outcome": "archive"},
                    {"name": "spike", "kind": "learning", "action": "shipped", "outcome": "archive"},
                    {"name": "spike", "kind": "experiments", "action": "shipped", "outcome": "elsewhere"}):
            self.post("/api/project/status", bad, ok=False)

    def _promoted(self, extra=""):
        self.note("2026-10-01-a.md")
        self.post("/api/action", {"file": "2026-10-01-a.md", "action": "promote", "dest": "active", "note": "x", "due_days": 2})
        path = os.path.join(self.notes, "projects", "active", "a.md")  # its board record
        if extra:
            with open(path) as fh: text = fh.read()
            with open(path, "w") as fh: fh.write(server.set_field(text, "extensions", extra))
        return path

    def _push(self, **kw):
        return {"path": "active/a/NOTES.md", "action": "extend", "days": 7, "note": "Waiting on the parts to arrive.", **kw}

    def test_extend_needs_a_reason_and_changes_nothing_without_one(self):
        self._promoted(); before = self.snapshot()
        for bad in ({"note": ""}, {"note": "too short"}, {"note": "          "}, {"note": None}):
            self.post("/api/project/status", self._push(**bad), ok=False); self.assertEqual(self.snapshot(), before)
        for days in (0, 8, 30, "x"):
            self.post("/api/project/status", self._push(days=days), ok=False); self.assertEqual(self.snapshot(), before)

    def test_extend_records_count_and_plan_line_then_stops_after_two(self):
        path = self._promoted(); t = server.today()
        due0 = server.field(open(path).read(), "due")
        self.post("/api/project/status", self._push(days=3))
        text = open(path).read(); due1 = (max(t, server.dt.date.fromisoformat(due0)) + server.dt.timedelta(days=3)).isoformat()
        self.assertEqual((server.field(text, "due"), server.field(text, "extensions")), (due1, "1"))
        plan = text.split("## Extensions", 1)[1]
        self.assertIn(f"- pushed {due0} → {due1} ({t.isoformat()}): Waiting on the parts to arrive.", plan)
        [f] = self.get("/api/notes")["inflight"]; self.assertEqual((f["extensions"], f["max_extensions"]), (1, 2))
        self.post("/api/project/status", self._push())
        before = self.snapshot()
        err = self.post("/api/project/status", self._push(), ok=False)["error"]
        self.assertEqual(err, "Pushed twice already: ship it or kill it."); self.assertEqual(self.snapshot(), before)
        self.post("/api/project/status", {"path": "active/a/NOTES.md", "action": "shipped", "outcome": "archive"})  # shipping is still allowed

    def test_the_refusal_says_the_configured_number_of_pushes(self):
        os.makedirs(server.CONFIG_DIR, exist_ok=True)
        for limit, words in ((3, "Pushed 3 times already"), (1, "Pushed once already")):
            with self.subTest(limit=limit):
                with open(server.config_path(), "w") as fh: json.dump({"max_extensions": limit}, fh)
                path = self._promoted() if limit == 3 else path
                with open(path) as fh: text = fh.read()
                with open(path, "w") as fh: fh.write(server.set_field(text, "extensions", str(limit)))
                self.assertEqual(self.post("/api/project/status", self._push(), ok=False)["error"], f"{words}: ship it or kill it.")

    def test_missing_extensions_counts_as_zero_and_undo_restores_exactly(self):
        path = self._promoted(); original = open(path, "rb").read()
        self.assertNotIn(b"extensions", original)
        self.post("/api/project/status", self._push()); self.assertNotEqual(open(path, "rb").read(), original)
        self.post("/api/undo", {}); self.assertEqual(open(path, "rb").read(), original)

    def test_extend_adds_an_extensions_section_to_the_record_and_leaves_the_project_alone(self):
        path = self._promoted(); proj = os.path.join(self.tmp, "active", "a"); before = self.snapshot(proj)
        original = open(path, "rb").read(); self.assertNotIn(b"## Extensions", original)
        self.post("/api/project/status", self._push())
        self.assertIn("\n## Extensions\n\n- pushed ", open(path).read())
        self.assertEqual(self.snapshot(proj), before)
        self.post("/api/undo", {}); self.assertEqual(open(path, "rb").read(), original)

    def test_extension_limit_comes_from_config(self):
        self._promoted(); os.makedirs(server.CONFIG_DIR, exist_ok=True)
        with open(os.path.join(server.CONFIG_DIR, "config.json"), "w") as fh: fh.write('{"max_extensions": 1}')
        self.post("/api/project/status", self._push())
        self.post("/api/project/status", self._push(), ok=False)

    def test_project_kill_needs_a_reason_and_files_it_under_outcome(self):
        path = self._promoted(); before = self.snapshot()
        self.post("/api/project/status", {"path": "active/a/NOTES.md", "action": "kill"}, ok=False)
        self.post("/api/project/status", {"path": "active/a/NOTES.md", "action": "kill", "note": "  "}, ok=False)
        self.assertEqual(self.snapshot(), before)
        self.post("/api/project/status", {"path": "active/a/NOTES.md", "action": "kill", "note": "Not worth it."})
        text = self.record("archive", "a_abandoned")
        self.assertIn("**Killed", text.split("## Outcome", 1)[1]); self.assertIn("Not worth it.", text)
        self.assertNotIn("Not worth it.", open(os.path.join(self.tmp, "archive", "a_abandoned", "NOTES.md")).read())

    def project(self, name, kind="active", notes=None):
        path = os.path.join(self.tmp, kind, name); os.makedirs(path)
        if notes is not None:
            with open(os.path.join(path, "NOTES.md"), "w", encoding="utf-8") as fh: fh.write(notes)
        return path

    def _days_ago(self, n): return (server.today() - server.dt.timedelta(days=n)).isoformat()

    def test_ship_and_kill_write_closed_and_undo_removes_it(self):
        path = self._promoted(); original = open(path, "rb").read(); t = server.today().isoformat()
        notes = open(os.path.join(self.tmp, "active", "a", "NOTES.md"), "rb").read()
        self.post("/api/project/status", {"path": "active/a/NOTES.md", "action": "shipped", "outcome": "maintained"})
        text = self.record("maintained", "a")
        self.assertEqual((server.field(text, "closed"), server.field(text, "shipped")), (t, t))
        self.assertEqual(open(os.path.join(self.tmp, "maintained", "a", "NOTES.md"), "rb").read(), notes)
        [f] = self.get("/api/notes")["inflight"]; self.assertEqual((f["path"], f["closed"]), ("maintained/a/NOTES.md", t))
        self.post("/api/undo", {}); self.assertEqual(open(path, "rb").read(), original)
        self.post("/api/project/status", {"path": "active/a/NOTES.md", "action": "kill", "note": "Not worth it."})
        self.assertEqual(server.field(self.record("archive", "a_abandoned"), "closed"), t)
        self.post("/api/undo", {}); self.assertEqual(open(path, "rb").read(), original)
        self.assertEqual(open(os.path.join(self.tmp, "active", "a", "NOTES.md"), "rb").read(), notes)

    def test_folder_ship_and_kill_write_closed_and_listing_reports_it(self):
        for name in ("s", "k"): os.makedirs(os.path.join(self.tmp, "experiments", name))
        t = server.today().isoformat()
        self.post("/api/project/status", {"name": "s", "kind": "experiments", "action": "shipped", "outcome": "archive"})
        self.post("/api/project/status", {"name": "k", "kind": "experiments", "action": "kill", "note": "No."})
        self.assertEqual(self.get("/api/projects")["projects"], [])  # both moved out of experiments/
        closed = {f["path"]: f["closed"] for f in self.get("/api/notes")["inflight"]}  # and show as recently closed
        self.assertEqual(closed, {"archive/s/NOTES.md": t, "archive/k_abandoned/NOTES.md": t})
        self.post("/api/undo", {}); self.assertEqual({p["name"]: p["closed"] for p in self.get("/api/projects")["projects"]}, {"k": ""})

    def test_closed_date_falls_back_to_shipped_then_the_killed_date(self):
        from notemeta import closed_date
        self.assertEqual(closed_date("# x\n\n- shipped: 2026-10-01\n- closed: 2026-10-05\n"), "2026-10-05")
        self.assertEqual(closed_date("# x\n\n- shipped: 2026-10-01\n"), "2026-10-01")
        self.assertEqual(closed_date("# x\n\n- status: dead\n\n## Outcome\n\n**Killed 2026-09-30:** no.\n"), "2026-09-30")
        self.assertEqual(closed_date("# x\n\n- status: dead\n"), "")
        self.assertEqual(closed_date("# x\n\n- closed: soon\n"), "")
        self.assertEqual(closed_date("# x\n\n**Killed 2026-09-30:** in prose, not Outcome\n"), "")
        # an old project killed before 06: the date is far down the file, past the header
        d = self.project("old", "experiments", "# old\n\n- status: dead\n\n" + "filler\n" * 2000 + "\n## Outcome\n\n**Killed 2026-09-30:** no.\n")
        self.assertEqual({p["name"]: p["closed"] for p in self.get("/api/projects")["projects"]}["old"], "2026-09-30")

    def test_closed_window_covers_maintained_and_archive_and_comes_from_config(self):
        mk = lambda kind, name, closed: self.project(name, kind, f"# {name}\n\n- status: shipped\n- closed: {closed}\n")
        mk("maintained", "m-new", self._days_ago(2)); mk("maintained", "m-old", self._days_ago(30))
        mk("archive", "a-new", self._days_ago(7)); mk("archive", "a-old", self._days_ago(8))
        os.makedirs(os.path.join(self.tmp, "archive", "open")); mk("archive", "no-date", "n/a")
        with open(os.path.join(self.tmp, "archive", "2026-10-01-note.md"), "w") as fh:  # a dated file at the top of archive/
            fh.write(f"# Dated\n\n- status: dead\n- closed: {self._days_ago(1)}\n")
        n = self.get("/api/notes"); self.assertEqual(n["closed_window_days"], 7)
        self.assertEqual(sorted(x["path"] for x in n["inflight"]),
                         ["archive/2026-10-01-note.md", "archive/a-new/NOTES.md", "maintained/m-new/NOTES.md"])
        self.assertTrue(all(x["done"] for x in n["inflight"]))
        os.makedirs(server.CONFIG_DIR, exist_ok=True)
        with open(os.path.join(server.CONFIG_DIR, "config.json"), "w") as fh: fh.write('{"closed_window_days": 1}')
        n = self.get("/api/notes"); self.assertEqual(n["closed_window_days"], 1)
        self.assertEqual(sorted(x["path"] for x in n["inflight"]), ["archive/2026-10-01-note.md"])

    def test_old_closed_active_projects_stay_listed_for_show_all(self):
        self.project("done-long-ago", "active", f"# d\n\n- status: shipped\n- closed: {self._days_ago(40)}\n")
        [p] = self.get("/api/projects")["projects"]; self.assertEqual((p["status"], p["closed"]), ("shipped", self._days_ago(40)))

    def test_stats_counts_captures_and_decisions_by_week(self):
        server.LOG_PATH = os.path.join(self.tmp, "triage.log"); self.addCleanup(setattr, server, "LOG_PATH", self.old_log_path)
        today = server.today().isoformat()
        self.note(f"{today}-a.md"); self.note(f"{today}-b.md")
        with open(server.LOG_PATH, "w") as fh: fh.write(f"{today}T10:00:00 kill x.md -> y\n{today}T10:00:01 keep z.md status=open\n")
        s = self.get("/api/stats")
        self.assertEqual((s["weeks"][-1]["captured"], s["weeks"][-1]["decided"]), (2, 1))
        self.assertEqual(len(s["weeks"]), 8)

    def test_stats_median_wait_and_empty_inbox(self):
        self.assertEqual((self.get("/api/stats")["median_wait"], self.get("/api/stats")["oldest"]), (None, None))
        for days, n in ((1, "a"), (3, "b"), (10, "c"), (20, "d")):
            self.note(f"{(server.today() - server.dt.timedelta(days=days)).isoformat()}-{n}.md")
        s = self.get("/api/stats")
        self.assertEqual((s["median_wait"], s["oldest"], s["live"]), (6.5, 20, 4))  # even count: mean of the middle two

    def test_promote_experiment_is_open_ended_but_needs_done_means(self):
        self.note("2026-10-01-spike.md")
        err = self.post("/api/action", {"file": "2026-10-01-spike.md", "action": "promote", "dest": "experiments", "note": ""}, ok=False)
        self.assertIn("done means", err["error"])
        self.post("/api/action", {"file": "2026-10-01-spike.md", "action": "promote", "dest": "experiments", "note": "We know if it works."})
        notes = open(os.path.join(self.tmp, "experiments", "spike", "NOTES.md")).read()
        self.assertEqual(notes, "# A note\n\n## Plan\n\n- definition of done: We know if it works.\n- first step:\n"
                                "- check-in: when done means is met, mark it shipped; if it stalls, kill it\n")
        rec = self.record("experiments", "spike")
        self.assertNotIn("- due:", rec); self.assertIn(f"- promoted: {server.today().isoformat()}", rec)
        [f] = self.get("/api/notes")["inflight"]
        self.assertEqual((f["dest"], f["due"], f["days_left"], f["open_ended"], f["done"]), ("experiments", "", None, True, False))
        self.post("/api/project/status", {"path": f["path"], "action": "shipped", "outcome": "archive"})
        [f] = self.get("/api/notes")["inflight"]; self.assertEqual((f["path"], f["done"]), ("archive/spike/NOTES.md", True))

    def test_promote_active_to_an_exact_ship_by_date(self):
        self.note("2026-10-01-a.md"); self.note("2026-10-02-b.md")
        when = (server.today() + server.dt.timedelta(days=45)).isoformat()
        self.post("/api/action", {"file": "2026-10-01-a.md", "action": "promote", "dest": "active", "note": "x", "due": when, "due_days": 3})
        self.assertIn(f"- due: {when}", self.record("active", "a"))
        self.assertNotIn("due", open(os.path.join(self.tmp, "active", "a", "NOTES.md")).read())
        for bad in (server.today().isoformat(), "2026-13-01", (server.today() + server.dt.timedelta(days=400)).isoformat()):
            self.assertIn("ship-by", self.post("/api/action", {"file": "2026-10-02-b.md", "action": "promote", "dest": "active",
                                                               "note": "x", "due": bad}, ok=False)["error"])

    def test_merge_then_promote_to_experiment_has_no_ship_by(self):
        self.note("2026-10-01-bomb-a.md", "Bomb A"); self.note("2026-10-02-bomb-b.md", "Bomb B")
        self.post("/api/merge", {"files": ["2026-10-01-bomb-a.md", "2026-10-02-bomb-b.md"], "title": "Bomb", "then": "promote",
                                 "dest": "experiments", "note": "Playtested.", "slug": "bomb"})
        self.assertNotIn("- due:", open(os.path.join(self.tmp, "experiments", "bomb", "NOTES.md")).read())

    def test_merge_with_a_bad_ship_by_changes_nothing(self):
        self.note("2026-10-01-bomb-a.md", "Bomb A"); self.note("2026-10-02-bomb-b.md", "Bomb B")
        before = self.snapshot()
        for bad in ({"dest": "active", "note": "x", "due": "2026-13-01"}, {"dest": "active", "note": ""}, {"dest": "nowhere", "note": "x"}):
            self.post("/api/merge", {"files": ["2026-10-01-bomb-a.md", "2026-10-02-bomb-b.md"], "title": "Bomb", "then": "promote", **bad}, ok=False)
            self.assertEqual(self.snapshot(), before)

    def test_experiment_drops_an_old_due_line_and_cannot_be_extended(self):
        path = self.note("2026-10-01-spike.md")
        with open(path) as fh: text = fh.read()
        with open(path, "w") as fh: fh.write(text.replace("- type: research", "- type: research\n- due: 2026-10-05"))
        self.post("/api/action", {"file": "2026-10-01-spike.md", "action": "promote", "dest": "experiments", "note": "Known."})
        self.assertNotIn("- due:", open(os.path.join(self.tmp, "experiments", "spike", "NOTES.md")).read())
        [f] = self.get("/api/notes")["inflight"]
        self.assertTrue(f["open_ended"])
        self.assertIn("open-ended", self.post("/api/project/status", {"path": f["path"], "action": "extend"}, ok=False)["error"])

    def test_one_card_per_experiment_folder(self):
        d = os.path.join(self.tmp, "experiments", "spike"); os.makedirs(d)
        for name in ("NOTES.md", "old-capture.md"):
            with open(os.path.join(d, name), "w") as fh: fh.write("# Spike\n\n- promoted: 2026-10-01\n")
        self.assertEqual([f["path"] for f in self.get("/api/notes")["inflight"]], ["experiments/spike/NOTES.md"])

    def test_page_is_the_one_loaded_at_startup(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/") as r: page = r.read().decode()
        self.assertIn(f"const VERSION='{server.VERSION}'", page)
        self.assertEqual(page.encode(), server.INDEX_HTML)

    def test_fonts_are_served_and_nothing_else_under_static(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/static/fonts/Lato-400.woff2") as r:
            self.assertEqual(r.headers["Content-Type"], "font/woff2")
        for bad in ("/static/fonts/../server.py", "/static/fonts/x.ttf", "/static/fonts/README.md"):
            with self.assertRaises(urllib.error.HTTPError): urllib.request.urlopen(f"http://127.0.0.1:{self.port}{bad}")

    def test_the_app_window_path_serves_the_same_page(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/triage") as r: self.assertEqual(r.read(), server.INDEX_HTML)
        for bad in ("/triage/", "/triagex", "/triage/api/notes"):
            with self.assertRaises(urllib.error.HTTPError): urllib.request.urlopen(f"http://127.0.0.1:{self.port}{bad}")

    def test_app_icons_are_served_as_png_and_only_by_name(self):
        for path in ("/static/icons/icon-256.png", "/apple-touch-icon.png"):
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as r:
                self.assertEqual(r.headers["Content-Type"], "image/png")
                self.assertTrue(r.read().startswith(b"\x89PNG"))
        for bad in ("/static/icons/icon.svg", "/static/icons/../../server.py", "/static/icons/"):
            with self.assertRaises(urllib.error.HTTPError): urllib.request.urlopen(f"http://127.0.0.1:{self.port}{bad}")


class OpenPageTestCase(unittest.TestCase):
    """--app opens Omarchy's app window; without it, or off Omarchy, a browser tab."""
    def launch(self, wait):
        """open_page with --app on Omarchy, where the launcher's wait() does `wait` (a return code or an exception)."""
        with mock.patch.object(server.shutil, "which", return_value="/usr/bin/omarchy-launch-webapp"), \
             mock.patch.object(server.subprocess, "Popen") as popen, mock.patch("webbrowser.open") as tab:
            popen.return_value.wait.side_effect = [wait]
            server.open_page("http://127.0.0.1:8765/", True)
        self.assertEqual(popen.call_args.args[0], ["omarchy-launch-webapp", "http://127.0.0.1:8765/triage"])
        return tab

    def test_app_uses_the_omarchy_launcher(self):
        self.launch(0).assert_not_called()
        self.launch(server.subprocess.TimeoutExpired("omarchy-launch-webapp", 2)).assert_not_called()  # window still up

    def test_app_falls_back_to_a_tab_when_the_launcher_fails(self):
        self.launch(1).assert_called_once_with("http://127.0.0.1:8765/triage")

    def test_app_falls_back_to_a_tab_without_omarchy(self):
        with mock.patch.object(server.shutil, "which", return_value=None), \
             mock.patch.object(server.subprocess, "Popen") as popen, mock.patch("webbrowser.open") as tab:
            server.open_page("http://127.0.0.1:8765/", True)
        popen.assert_not_called(); tab.assert_called_once_with("http://127.0.0.1:8765/triage")

    def test_without_app_it_is_a_browser_tab(self):
        with mock.patch.object(server.subprocess, "Popen") as popen, mock.patch("webbrowser.open") as tab:
            server.open_page("http://127.0.0.1:8765/", False)
        popen.assert_not_called(); tab.assert_called_once_with("http://127.0.0.1:8765/")



class ServiceTestCase(unittest.TestCase):
    """The systemd side: --service retries when the port is taken; the unit's server is told apart by its cgroup."""
    def test_service_mode_exits_with_an_error_when_a_triage_server_holds_the_port(self):
        import subprocess, sys
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.H); port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close); self.addCleanup(httpd.shutdown)
        tmp = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        r = subprocess.run([sys.executable, server.__file__, "--service", "--port", str(port), "--notes", tmp,
                            "--log", os.path.join(tmp, "t.log")], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 1)  # an error, so Restart=on-failure tries again
        self.assertIn("will try again", r.stderr)

    def test_service_owns_reads_the_unit_from_the_cgroup(self):
        unit = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/rawtriage-web.service\n"
        other = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/app-foot.scope\n"
        for text, want in ((unit, True), (other, False)):
            with mock.patch("builtins.open", mock.mock_open(read_data=text)): self.assertIs(server.service_owns(123), want)
        self.assertFalse(server.service_owns(None))
        self.assertFalse(server.service_owns(2 ** 30))  # no such process

    def test_under_the_service_the_opener_gets_its_own_scope(self):
        argv = ["xdg-open", "/tmp/x.md"]
        with mock.patch.object(server, "service_owns", return_value=True), \
             mock.patch.object(server.shutil, "which", return_value="/usr/bin/systemd-run"):
            self.assertEqual(server.in_own_scope(argv), ["systemd-run", "--user", "--scope", "--quiet", "--collect", *argv])
        with mock.patch.object(server, "service_owns", return_value=False):
            self.assertEqual(server.in_own_scope(argv), argv)  # from a terminal or the launcher: unchanged


INDEX = """# reference

## Index

### Azure
- [A](x1.md): first
- [B](x2.md): second

### Hardware
- [H](h.md): hw

## Other

- [Z](z.md): not the index
"""

class IndexInsertTest(unittest.TestCase):
    def test_adds_at_end_of_existing_section_and_touches_nothing_else(self):
        out = server.index_insert(INDEX, "Azure", "- [N](n.md): new")
        self.assertEqual(out, INDEX.replace("- [B](x2.md): second\n", "- [B](x2.md): second\n- [N](n.md): new\n"))

    def test_last_section_stays_above_next_heading(self):
        out = server.index_insert(INDEX, "Hardware", "- [N](n.md): new")
        self.assertIn("- [H](h.md): hw\n- [N](n.md): new\n\n## Other", out)

    def test_new_section_goes_at_end_of_index(self):
        out = server.index_insert(INDEX, "Cooking", "- [N](n.md): new")
        self.assertIn("- [H](h.md): hw\n\n### Cooking\n- [N](n.md): new\n\n## Other", out)

    def test_refuses_without_index_or_when_already_linked(self):
        self.assertIsNone(server.index_insert("# x\n\n## Notes\n", "A", "- [N](n.md)"))
        self.assertIsNone(server.index_insert(INDEX, "Azure", "- [Again](h.md): dup"))
        self.assertIsNone(server.index_insert(INDEX, "Azure", "- [Y](x1.md)"))

    def test_link_elsewhere_in_readme_does_not_count(self):
        self.assertIsNotNone(server.index_insert(INDEX, "Azure", "- [Z](z.md): ok"))

    def test_index_at_end_of_file_without_trailing_newline(self):
        out = server.index_insert("## Index\n\n### A\n- [x](x.md)", "A", "- [y](y.md)")
        self.assertEqual(out, "## Index\n\n### A\n- [x](x.md)\n- [y](y.md)")

    def test_brackets_and_newlines_removed(self):
        self.assertEqual(server.index_line("A [b]\nc", "n.md", "see [x]\n\nnow"), "- [A b c](n.md): see x now")


class ReferenceIndexTestCase(DecisionBase):
    def readme(self, text=INDEX):
        os.makedirs(os.path.join(self.tmp, "reference"), exist_ok=True)
        p = os.path.join(self.tmp, "reference", "README.md")
        with open(p, "w") as fh: fh.write(text)
        return p

    def read(self, p):
        with open(p) as fh: return fh.read()

    def test_reference_with_section_indexes_and_undo_reverts_both(self):
        self.note("2026-10-01-a.md", title="My [odd] note"); p = self.readme(); before = self.snapshot()
        r = self.post("/api/action", {"file": "2026-10-01-a.md", "action": "reference", "note": "why", "section": "Azure", "blurb": "does a thing"})
        self.assertNotIn("warning", r)
        self.assertIn("- [My odd note](a.md): does a thing\n\n### Hardware", self.read(p))
        self.assertIn('section: "Azure"', self.read(os.path.join(self.tmp, "reference", "a.md")))
        self.post("/api/undo", {})
        self.assertEqual(self.snapshot(), before)

    def test_default_blurb_is_first_sentence_of_question(self):
        self.note("2026-10-01-a.md", title="T"); p = self.readme()
        self.post("/api/action", {"file": "2026-10-01-a.md", "action": "reference", "section": "Azure"})
        self.assertIn("- [T](a.md): Why T?", self.read(p))

    def test_no_section_leaves_readme_alone(self):
        self.note("2026-10-01-a.md"); p = self.readme()
        self.post("/api/action", {"file": "2026-10-01-a.md", "action": "reference"})
        self.assertEqual(self.read(p), INDEX)
        self.assertNotIn("section:", self.read(os.path.join(self.tmp, "reference", "a.md")))

    def test_missing_readme_or_index_still_files_with_warning(self):
        self.note("2026-10-01-a.md"); self.note("2026-10-01-b.md")
        r = self.post("/api/action", {"file": "2026-10-01-a.md", "action": "reference", "section": "Azure"})
        self.assertIn("No reference/README.md", r["warning"])
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "reference", "a.md")))
        p = self.readme("# reference\n\nno index here\n")
        r = self.post("/api/action", {"file": "2026-10-01-b.md", "action": "reference", "section": "Azure"})
        self.assertIn("no ## Index", r["warning"])
        self.assertEqual(self.read(p), "# reference\n\nno index here\n")
        self.assertNotIn("section:", self.read(os.path.join(self.tmp, "reference", "b.md")))

    def test_undo_refuses_when_readme_changed_since(self):
        self.note("2026-10-01-a.md"); p = self.readme()
        self.post("/api/action", {"file": "2026-10-01-a.md", "action": "reference", "section": "Azure"})
        with open(p, "a") as fh: fh.write("edited\n")
        self.assertIn("changed", self.post("/api/undo", {}, ok=False)["error"])
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "reference", "a.md")))

    def test_batch_reference_all_one_section_one_undo(self):
        self.note("2026-10-01-a.md", title="A1"); self.note("2026-10-01-b.md", title="B1"); p = self.readme(); before = self.snapshot()
        self.post("/api/batch", {"files": ["2026-10-01-a.md", "2026-10-01-b.md"], "action": "reference", "section": "New one"})
        t = self.read(p)
        self.assertIn("### New one\n- [A1](a.md): Why A1?\n- [B1](b.md): Why B1?\n", t)
        self.post("/api/undo", {})
        self.assertEqual(self.snapshot(), before)

    def test_unindexed_list_and_add(self):
        p = self.readme(); d = os.path.join(self.tmp, "reference")
        for n in ("x1.md", "new.md"):
            with open(os.path.join(d, n), "w") as fh: fh.write(f"---\ncreated: x\n---\n\n# Title {n}\n\n## What's the question / problem?\n\nHow does it work? More.\n")
        j = self.get("/api/reference/unindexed")
        self.assertEqual(j["sections"], ["Azure", "Hardware"])
        self.assertEqual([f["file"] for f in j["files"]], ["new.md"])
        self.assertEqual(j["files"][0]["blurb"], "How does it work?")
        before = self.snapshot()
        r = self.post("/api/reference/index", {"file": "new.md", "section": "Hardware", "blurb": "mine"})
        self.assertIn("- [Title new.md](new.md): mine", self.read(p))
        self.assertIn('section: "Hardware"', self.read(os.path.join(d, "new.md")))
        self.assertEqual(self.get("/api/reference/unindexed")["files"], [])
        self.post("/api/undo", {})
        self.assertEqual(self.snapshot(), before)

    def test_index_add_rejects_bad_input(self):
        self.readme()
        for body in ({"file": "../x.md", "section": "A"}, {"file": "README.md", "section": "A"}, {"file": "nope.md", "section": "A"}, {"file": "nope.md", "section": ""}):
            self.assertIn("error", self.post("/api/reference/index", body, ok=False))


if __name__ == "__main__":
    unittest.main()
