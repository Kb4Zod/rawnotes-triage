#!/usr/bin/env python3
"""Auto-commit (flow gaps 01): after a change succeeds, the notes-folder paths it touched, and only those, are
committed. Runs against a disposable notes folder made into its own git repo; the user's git config and hooks are
never read (GIT_CONFIG_GLOBAL is /dev/null, hooks point at a temp folder)."""
import json
import os
import subprocess
import threading
import unittest
from unittest import mock

import server
from testkit import Fixture

try: from test_browser import BrowserCase, cdp
except ImportError: BrowserCase = cdp = None


def git(cwd, *args, check=True):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0: raise AssertionError(f"git {' '.join(args)}: {r.stderr}")
    return r.stdout


class GitFixture(Fixture):
    """The Fixture, with an isolated git: no global or system config, so nothing of the user's leaks in."""
    def setUp(self):
        env = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
        env.start(); self.addCleanup(env.stop)
        super().setUp()
        saved = server.AUTOCOMMIT; server.AUTOCOMMIT = True; self.addCleanup(setattr, server, "AUTOCOMMIT", saved)

    def init_repo(self, email="me@users.noreply.github.com"):
        self.hooks = os.path.join(self.tmp, "hooks"); os.makedirs(self.hooks)
        git(self.notes, "init", "-q", "-b", "main")
        git(self.notes, "config", "core.hooksPath", self.hooks)
        git(self.notes, "config", "user.name", "Test")
        if email: git(self.notes, "config", "user.email", email)

    def snapshot_commit(self, msg="start"):
        git(self.notes, "add", "-A"); git(self.notes, "-c", "user.email=s@x", "commit", "-q", "-m", msg)

    def count(self): return int(git(self.notes, "rev-list", "--count", "HEAD").strip())
    def subject(self): return git(self.notes, "log", "-1", "--format=%s").strip()
    def changed(self, rev="HEAD"):
        return sorted(git(self.notes, "show", "--name-status", "--format=", "--no-renames", rev).split("\n")[:-1])
    def status(self): return sorted(l for l in git(self.notes, "status", "--porcelain").split("\n") if l)


class AutoCommit(GitFixture):
    def setUp(self):
        super().setUp(); self.init_repo()
        self.note("2026-10-01-alpha.md", "Alpha"); self.note("2026-10-02-beta.md", "Beta"); self.note("2026-10-03-gamma.md", "Gamma")
        with open(os.path.join(self.notes, "other.md"), "w") as fh: fh.write("other\n")
        with open(os.path.join(self.notes, "staged.md"), "w") as fh: fh.write("staged\n")
        self.snapshot_commit()

    def test_kill_commits_only_the_move_and_leaves_other_work_alone(self):
        with open(os.path.join(self.notes, "other.md"), "a") as fh: fh.write("hand edit\n")
        with open(os.path.join(self.notes, "staged.md"), "a") as fh: fh.write("staged edit\n")
        git(self.notes, "add", "staged.md")
        with open(os.path.join(self.notes, "2026-10-09-new-capture.md"), "w") as fh: fh.write("# New\n")
        before = self.count()
        r = self.post("/api/action", {"file": "2026-10-01-alpha.md", "action": "kill", "note": "dupe"})
        self.assertTrue(r["commit"]["ok"], r); self.assertTrue(r["commit"]["sha"])
        self.assertEqual(self.count(), before + 1)
        self.assertEqual(self.subject(), "triage: kill “Alpha”")
        self.assertEqual(self.changed(), ["A\tdone/2026-10-01-alpha.md", "D\t2026-10-01-alpha.md"])
        body = git(self.notes, "log", "-1", "--format=%b")
        self.assertIn("done/2026-10-01-alpha.md", body); self.assertIn(f"via rawnotes-triage {server.VERSION}", body)
        self.assertEqual(self.status(), [" M other.md", "?? 2026-10-09-new-capture.md", "M  staged.md"])

    def test_batch_merge_restore_and_undo_each_make_one_commit(self):
        n = self.count()
        self.post("/api/batch", {"files": ["2026-10-01-alpha.md", "2026-10-02-beta.md"], "action": "kill", "note": "x"})
        self.assertEqual(self.count(), n + 1); self.assertEqual(self.subject(), "triage: killed 2 notes")
        self.assertEqual(len(self.changed()), 4)
        r = self.post("/api/undo", {}); self.assertTrue(r["commit"]["ok"], r)
        self.assertEqual(self.count(), n + 2); self.assertEqual(self.subject(), "triage: undo killed 2 notes")
        self.assertEqual(self.status(), [])
        r = self.post("/api/merge", {"files": ["2026-10-01-alpha.md", "2026-10-02-beta.md"], "title": "Both"})
        self.assertTrue(r["commit"]["ok"]); self.assertEqual(self.count(), n + 3); self.assertEqual(self.subject(), "triage: merge 2 notes")
        self.assertEqual(self.status(), [])
        self.post("/api/action", {"file": "2026-10-03-gamma.md", "action": "kill"}); n = self.count()
        r = self.post("/api/restore", {"file": "done/2026-10-03-gamma.md"}); self.assertTrue(r["commit"]["ok"])
        self.assertEqual(self.count(), n + 1); self.assertEqual(self.subject(), "triage: restore “Gamma”")
        r = self.post("/api/undo", {}); self.assertTrue(self.subject().startswith("triage: undo"), self.subject())
        self.assertEqual(self.count(), n + 2); self.assertEqual(self.status(), [])

    def test_promote_commits_only_the_notes_side(self):
        r = self.post("/api/action", {"file": "2026-10-02-beta.md", "action": "promote", "dest": "active", "note": "It ships.", "due_days": 7})
        self.assertTrue(r["commit"]["ok"], r)
        self.assertEqual(self.changed(), ["A\tdone/2026-10-02-beta.md", "D\t2026-10-02-beta.md"])
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "active", "beta", "NOTES.md")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "active", "beta", ".git")))

    def test_hand_edits_in_a_touched_note_are_named_in_the_body(self):
        with open(os.path.join(self.notes, "2026-10-01-alpha.md"), "a") as fh: fh.write("typed by hand\n")
        self.post("/api/action", {"file": "2026-10-01-alpha.md", "action": "keep", "status": "cooking"})
        self.assertIn("edits made by hand before this change: 2026-10-01-alpha.md", git(self.notes, "log", "-1", "--format=%b"))

    def test_project_only_change_has_no_commit(self):
        self.project("demo", notes="# Demo\n\n- status: promoted\n- promoted: 2026-10-01\n- due: 2026-12-01\n")
        r = self.post("/api/project/status", {"path": "active/demo/NOTES.md", "action": "shipped"})
        self.assertNotIn("commit", r)

    def test_skip_reasons_keep_the_change(self):
        gd = os.path.join(self.notes, ".git")
        for name, why in (("MERGE_HEAD", "merge"), ("index.lock", "index.lock")):
            with open(os.path.join(gd, name), "w") as fh: fh.write("x\n")
            n = self.count()
            r = self.post("/api/action", {"file": "2026-10-01-alpha.md", "action": "keep", "note": name})
            self.assertFalse(r["commit"]["ok"]); self.assertIn(why, r["commit"]["skipped"])
            self.assertIn(name, open(os.path.join(self.notes, "2026-10-01-alpha.md")).read())
            os.remove(os.path.join(gd, name)); self.assertEqual(self.count(), n)
        git(self.notes, "config", "--unset", "user.email")
        r = self.post("/api/action", {"file": "2026-10-01-alpha.md", "action": "kill"})
        self.assertFalse(r["commit"]["ok"]); self.assertIn("user.email", r["commit"]["skipped"])
        self.assertTrue(os.path.isfile(os.path.join(self.notes, "done", "2026-10-01-alpha.md")))

    def test_failing_pre_commit_hook_keeps_the_change_undoable(self):
        hook = os.path.join(self.hooks, "pre-commit")
        with open(hook, "w") as fh: fh.write("#!/bin/sh\necho 'gitleaks: leak found in 2026-10-01-alpha.md' >&2\nexit 1\n")
        os.chmod(hook, 0o755); n = self.count()
        r = self.post("/api/action", {"file": "2026-10-01-alpha.md", "action": "kill"})
        self.assertFalse(r["commit"]["ok"]); self.assertIn("pre-commit", r["commit"]["error"]); self.assertIn("gitleaks", r["commit"]["error"])
        self.assertEqual(self.count(), n)
        self.assertTrue(os.path.isfile(os.path.join(self.notes, "done", "2026-10-01-alpha.md")))
        self.assertEqual(self.status(), [" D 2026-10-01-alpha.md", "?? done/"])  # nothing left staged
        self.post("/api/undo", {})
        self.assertTrue(os.path.isfile(os.path.join(self.notes, "2026-10-01-alpha.md")))

    def test_no_commit_flag_and_config_turn_it_off(self):
        n = self.count()
        server.AUTOCOMMIT = False
        r = self.post("/api/action", {"file": "2026-10-01-alpha.md", "action": "kill"})
        self.assertEqual(r["commit"], {"ok": True, "skipped": "auto-commit is off"}); self.assertEqual(self.count(), n)
        server.AUTOCOMMIT = True; server.save_config(autocommit=False)
        r = self.post("/api/action", {"file": "2026-10-02-beta.md", "action": "kill"})
        self.assertEqual(r["commit"]["skipped"], "auto-commit is off"); self.assertEqual(self.count(), n)
        server.save_config(autocommit=True)
        self.post("/api/action", {"file": "2026-10-03-gamma.md", "action": "kill"}); self.assertEqual(self.count(), n + 1)

    def test_two_changes_at_once_make_two_commits(self):
        n = self.count(); out = {}
        def kill(f): out[f] = self.call("/api/action", {"file": f, "action": "kill"})
        ts = [threading.Thread(target=kill, args=(f,)) for f in ("2026-10-01-alpha.md", "2026-10-02-beta.md")]
        for t in ts: t.start()
        for t in ts: t.join()
        for f, (code, r) in out.items(): self.assertEqual(code, 200, r); self.assertTrue(r["commit"]["ok"], r)
        self.assertEqual(self.count(), n + 2)
        self.assertEqual(self.status(), [])


class NotARepo(GitFixture):
    def test_change_succeeds_without_a_repo(self):
        self.note("2026-10-01-alpha.md", "Alpha")
        r = self.post("/api/action", {"file": "2026-10-01-alpha.md", "action": "kill"})
        self.assertFalse(r["commit"]["ok"]); self.assertIn("not a git repo", r["commit"]["skipped"])
        self.assertTrue(os.path.isfile(os.path.join(self.notes, "done", "2026-10-01-alpha.md")))

    def test_untracked_note_moved_commits_its_new_place(self):
        self.init_repo(); self.note("2026-10-01-alpha.md", "Alpha")
        r = self.post("/api/action", {"file": "2026-10-01-alpha.md", "action": "kill"})
        self.assertTrue(r["commit"]["ok"], r); self.assertEqual(self.changed(), ["A\tdone/2026-10-01-alpha.md"])


if BrowserCase is not None:
    @unittest.skipUnless(cdp.chromium_path(), "no Chromium/Chrome found")
    class CommitToast(BrowserCase):
        def test_toast_warns_when_not_committed(self):
            self.page.click("#list .item[data-f='2026-10-15-recent.md']"); self.page.key("x"); self.page.wait("document.querySelector('#form.kill')")
            self.page.key("Enter"); self.page.wait("document.querySelector('#toast.show')")
            self.assertIn("Not committed to git", self.text("#toast .cwarn")); self.assertIn("not a git repo", self.text("#toast .cwarn"))
            self.page.click("#tUndo"); self.page.wait("document.querySelector('#toast.show') && !document.querySelector('#toast .cwarn')")  # said once per page load

        def test_hook_failure_warning_is_shown(self):
            with mock.patch.object(server, "autocommit", lambda label, ops: {"ok": False, "error": "the pre-commit hook (x) refused the commit"}):
                self.page.click("#list .item[data-f='2026-10-15-recent.md']"); self.page.key("x"); self.page.wait("document.querySelector('#form.kill')")
                self.page.key("Enter"); self.page.wait("document.querySelector('#toast.show .cwarn')")
                self.assertIn("pre-commit hook", self.text("#toast .cwarn"))


if __name__ == "__main__":
    unittest.main()
