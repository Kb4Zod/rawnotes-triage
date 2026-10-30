#!/usr/bin/env python3
"""Flow gaps: which folders are projects on the board, and what Promote builds (skeleton + git)."""
import os
import unittest
from unittest import mock

import server
from testkit import Fixture


class Worktrees(Fixture):
    """A linked git worktree (`.git` is a `gitdir:` file) is a branch checkout, not a project."""
    def setUp(self):
        super().setUp()
        self.opened = []
        p = mock.patch.object(server, "open_path", side_effect=self.opened.append); p.start(); self.addCleanup(p.stop)
        self.project("plain")
        os.makedirs(os.path.join(self.project("repo"), ".git"))
        wt = self.project("repo-feature")
        with open(os.path.join(wt, ".git"), "w") as fh: fh.write("gitdir: /somewhere/repo/.git/worktrees/repo-feature\n")
        exp = self.project("spike-wt", kind="experiments")
        with open(os.path.join(exp, ".git"), "w") as fh: fh.write("gitdir: /x/.git/worktrees/spike-wt\n")
        odd = self.project("odd")  # a .git file that is not a worktree pointer stays a project
        with open(os.path.join(odd, ".git"), "w") as fh: fh.write("not a pointer\n")
        os.makedirs(os.path.join(self.tmp, "elsewhere", "linked"))
        os.symlink(os.path.join(self.tmp, "elsewhere", "linked"), os.path.join(self.tmp, "active", "linked"))

    def test_board_skips_worktrees_and_keeps_everything_else(self):
        ps = {p["path"]: p for p in self.get("/api/projects")["projects"]}
        self.assertEqual(set(ps), {"active/plain", "active/repo", "active/odd", "active/linked"})
        self.assertTrue(ps["active/repo"]["repo"]); self.assertFalse(ps["active/plain"]["repo"])
        self.assertTrue(ps["active/linked"]["linked"])

    def test_project_detail_and_open_refuse_a_worktree(self):
        for name, kind in (("repo-feature", "active"), ("spike-wt", "experiments")):
            code, data = self.call(f"/api/project?name={name}&kind={kind}")
            self.assertEqual((code, data["error"]), (400, "unknown project"))
            self.assertEqual(self.post("/api/open", {"name": name, "kind": kind}, ok=False)["error"], "unknown project")
        self.post("/api/open", {"name": "repo", "kind": "active"})
        self.assertEqual(self.opened, [os.path.join(self.tmp, "active", "repo")])

    def test_status_and_done_refuse_a_worktree(self):
        before = self.snapshot()
        self.post("/api/project/status", {"name": "repo-feature", "kind": "active", "action": "shipped"}, ok=False)
        self.post("/api/done", {"name": "repo-feature", "kind": "active", "text": "Ships."}, ok=False)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
