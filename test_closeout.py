#!/usr/bin/env python3
"""Ticket 12: close out a project folder (Next round / Maintained / Archive; Kill -> archive/<slug>_abandoned), the
"movedir" Tx step with its checked inverse, fault injection around it, and sync-apps afterwards. Every test runs on a
temp projects root with a fake dependency-check home (testkit), never the real folders."""
import errno
import os
import shutil
import subprocess
import unittest
from unittest import mock

import depcheck
import server
from testkit import Fixture

NOTES = ("# Demo\n\n- date: 2026-10-01\n- status: promoted\n- promoted: 2026-10-01\n- due: 2026-12-01\n- extensions: 1\n\n"
         "## Plan\n\n- definition of done: It ships.\n- pushed 2026-11-24 → 2026-12-01 (2026-10-20): parts late\n")


class CloseBase(Fixture):
    def setUp(self):
        super().setUp()
        self.home = os.path.join(self.tmp, "home"); os.makedirs(self.home)
        self.sources = depcheck.Sources(self.home, proc=os.path.join(self.tmp, "proc"), crontab=lambda: "")
        p = mock.patch.object(server, "DEPS", self.sources); p.start(); self.addCleanup(p.stop)
        self.demo = self.project("demo", notes=NOTES)
        with open(os.path.join(self.demo, "app.py"), "w") as fh: fh.write("print(1)\n")

    def close(self, outcome="archive", ok=True, **kw):
        return self.post("/api/project/close", {"name": "demo", "kind": "active", "outcome": outcome, **kw}, ok=ok)

    def read(self, *p):
        with open(os.path.join(self.tmp, *p), encoding="utf-8") as fh: return fh.read()

    def block(self):
        """Something in ~/.local/bin names the folder: a blocker."""
        os.makedirs(self.sources.bin_dir, exist_ok=True)
        with open(os.path.join(self.sources.bin_dir, "demo"), "w") as fh: fh.write(f"#!/bin/sh\nexec {self.demo}/app.py\n")

    def memory(self):
        d = os.path.join(self.sources.claude_projects, depcheck.claude_key(self.demo)); os.makedirs(d); return d


class Outcomes(CloseBase):
    def test_archive_moves_the_folder_and_writes_shipped_closed_and_moved(self):
        before = self.snapshot(); mem = self.memory(); t = server.today().isoformat()
        r = self.close()
        self.assertEqual((r["moved_to"], r["msg"]), ("archive/demo", "Shipped demo 🎉 → archive/demo"))
        self.assertFalse(os.path.exists(self.demo))
        text = self.read("archive", "demo", "NOTES.md")
        self.assertEqual((server.field(text, "status"), server.field(text, "closed"), server.field(text, "shipped")), ("shipped", t, t))
        new_mem = os.path.join(self.sources.claude_projects, depcheck.claude_key(os.path.join(self.tmp, "archive", "demo")))
        moved = text.split("## Moved", 1)[1]
        self.assertIn(f"- {t}: moved from `active/demo/` to `archive/demo/`. Left to do by hand:", moved)
        self.assertIn(f"`mv {mem} {new_mem}`", moved)
        self.assertIn(f"  - Update any T3 Code project whose folder is {self.demo} to {os.path.join(self.tmp, 'archive', 'demo')}", moved)
        self.assertEqual([x["kind"] for x in r["todos"]], ["agent-memory", "t3"])
        self.assertTrue(os.path.isdir(mem))  # the app never moves memory folders itself
        [f] = [x for x in self.get("/api/notes")["inflight"] if x["path"] == "archive/demo/NOTES.md"]
        self.assertEqual((f["done"], f["closed"]), (True, t))  # shown in the Closed column for the window
        self.assertEqual(self.get("/api/projects")["projects"], [])
        self.post("/api/undo", {})
        after = self.snapshot(); self.assertEqual({k: v for k, v in after.items() if not k.startswith("home/")},
                                                  {k: v for k, v in before.items() if not k.startswith("home/")})

    def test_maintained_is_suggested_for_a_github_origin_and_moves_there(self):
        subprocess.run(["git", "init", "-q", "-b", "main", self.demo], check=True)
        subprocess.run(["git", "-C", self.demo, "remote", "add", "origin", "https://github.com/me/demo.git"], check=True)
        self.assertIn("suggested: maintained", self.close(outcome=None, ok=False)["error"])
        self.assertIn("suggested: maintained", self.post("/api/project/status", {"name": "demo", "action": "shipped"}, ok=False)["error"])
        r = self.close("maintained"); self.assertEqual(r["moved_to"], "maintained/demo")
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "maintained", "demo", ".git")))

    def test_a_taken_name_gets_a_suffix(self):
        os.makedirs(os.path.join(self.tmp, "archive", "demo")); os.makedirs(os.path.join(self.tmp, "archive", "demo-2"))
        self.assertEqual(self.close()["moved_to"], "archive/demo-3")

    def test_kill_needs_a_reason_and_moves_to_abandoned(self):
        before = self.snapshot()
        for body in ({}, {"note": "  "}):
            self.post("/api/project/status", {"name": "demo", "action": "kill", **body}, ok=False)
        self.post("/api/project/close", {"name": "demo", "action": "kill"}, ok=False)
        self.assertEqual(self.snapshot(), before)
        os.makedirs(os.path.join(self.tmp, "archive", "demo"))  # the plain name being taken does not matter
        r = self.post("/api/project/status", {"name": "demo", "action": "kill", "note": "Nobody needs it."})
        self.assertEqual(r["moved_to"], "archive/demo_abandoned"); self.assertIn("Killed demo → archive/demo_abandoned", r["msg"])
        text = self.read("archive", "demo_abandoned", "NOTES.md")
        self.assertEqual(server.field(text, "status"), "dead"); self.assertIn("Nobody needs it.", text.split("## Outcome", 1)[1])
        self.post("/api/undo", {}); self.assertEqual(self.read("active", "demo", "NOTES.md"), NOTES)

    def test_next_round_stays_and_records_the_finished_round(self):
        t = server.today().isoformat()
        r = self.close("next", done="Round two: it runs on the phone.", due_days=10)
        due = (server.today() + server.dt.timedelta(days=10)).isoformat()
        self.assertNotIn("moved_to", r); self.assertEqual(r["msg"], f"Shipped demo 🎉 Next round ships by {due}")
        text = self.read("active", "demo", "NOTES.md")
        self.assertEqual([server.field(text, k) for k in ("status", "promoted", "due", "extensions", "closed", "shipped")],
                         ["promoted", t, due, "0", "", ""])
        self.assertIn("- definition of done: Round two: it runs on the phone.", text); self.assertNotIn("definition of done: It ships.", text)
        self.assertIn(f"## Rounds\n\n- 2026-10-01 → shipped {t} (ship by 2026-12-01, pushed 1×). Done means: It ships.", text)
        self.assertEqual(self.get("/api/project?name=demo&kind=active")["done"]["text"], "Round two: it runs on the phone.")
        self.post("/api/undo", {}); self.assertEqual(self.read("active", "demo", "NOTES.md"), NOTES)

    def test_next_round_rules(self):
        before = self.snapshot()
        self.assertIn("done means", self.close("next", ok=False, due_days=5)["error"])
        self.assertIn("ship-by", self.close("next", ok=False, done="More.")["error"])
        self.assertIn("ship-by", self.close("next", ok=False, done="More.", due="2020-01-01")["error"])
        self.post("/api/project/status", {"name": "demo", "action": "extend", "outcome": "next"}, ok=False)
        self.assertEqual(self.snapshot(), before)
        exp = self.project("spike", "experiments", "# Spike\n\n- status: promoted\n- promoted: 2026-10-01\n\n- definition of done: We know.\n")
        r = self.post("/api/project/close", {"name": "spike", "kind": "experiments", "outcome": "next", "done": "We know more."})
        self.assertIn("open-ended", r["msg"]); self.assertEqual(server.field(open(exp + "/NOTES.md").read(), "due"), "")

    def test_next_round_puts_done_means_where_it_was_found(self):
        with open(os.path.join(self.demo, "README.md"), "w") as fh: fh.write("# Demo\n\n## Definition of done\n\nThe old README goal.\n")
        os.remove(os.path.join(self.demo, "NOTES.md"))
        self.close("next", done="The new goal.", due_days=3)
        self.assertIn("The new goal.", self.read("active", "demo", "README.md"))
        self.assertIn("Done means: The old README goal.", self.read("active", "demo", "NOTES.md"))
        self.post("/api/undo", {}); self.assertFalse(os.path.exists(os.path.join(self.demo, "NOTES.md")))
        self.assertIn("The old README goal.", self.read("active", "demo", "README.md"))

    def test_a_single_file_project_cannot_be_closed_out(self):
        os.makedirs(os.path.join(self.tmp, "experiments"))
        with open(os.path.join(self.tmp, "experiments", "flat.md"), "w") as fh: fh.write("# Flat\n\n- status: promoted\n- due: 2026-12-01\n")
        self.post("/api/project/status", {"path": "experiments/flat.md", "action": "shipped", "outcome": "archive"}, ok=False)
        self.post("/api/project/status", {"path": "experiments/flat.md", "action": "shipped"})  # shipped in place, as before
        self.post("/api/project/close", {"path": "experiments/flat.md", "outcome": "archive"}, ok=False)


class Blockers(CloseBase):
    def test_any_blocker_refuses_with_the_list_and_changes_nothing(self):
        self.block(); before = self.snapshot()
        code, r = self.call("/api/project/close", {"name": "demo", "outcome": "archive"})
        self.assertEqual(code, 400)
        self.assertIn("Can't move demo: 1 thing points at it", r["error"]); self.assertIn("/.local/bin/demo", r["error"])
        self.assertEqual([b["kind"] for b in r["blockers"]], ["bin"]); self.assertIn("fix", r["blockers"][0])
        self.assertEqual((self.snapshot(), server._undo), (before, []))
        code, r = self.call("/api/project/status", {"name": "demo", "action": "kill", "note": "Not needed."})
        self.assertEqual((code, self.snapshot()), (400, before)); self.assertIn("Can't move demo", r["error"])
        self.close("next", done="Again.", due_days=3)  # a next round moves nothing, so nothing blocks it

    def test_a_process_running_inside_blocks(self):
        os.makedirs(os.path.join(self.tmp, "proc", "4242"))
        os.symlink(self.demo, os.path.join(self.tmp, "proc", "4242", "cwd"))
        with open(os.path.join(self.tmp, "proc", "4242", "cmdline"), "wb") as fh: fh.write(b"python3\0app.py\0")
        self.assertIn("pid 4242", self.close(ok=False)["error"])

    def test_a_symlinked_folder_or_destination_root_is_refused(self):
        real = os.path.join(self.tmp, "elsewhere", "linked"); os.makedirs(real)
        os.symlink(real, os.path.join(self.tmp, "active", "linked"))
        self.assertIn("symlink", self.post("/api/project/close", {"name": "linked", "outcome": "archive"}, ok=False)["error"])
        outside = os.path.realpath(self.tmp + "-out"); os.makedirs(outside); self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        os.symlink(outside, os.path.join(self.tmp, "archive"))
        before = self.snapshot()
        self.assertIn("symlink", self.close(ok=False)["error"])
        self.assertEqual((self.snapshot(), os.listdir(outside), server._undo), (before, [], []))

    def test_a_worktree_is_not_a_project_to_close(self):
        wt = self.project("demo-wt")
        with open(os.path.join(wt, ".git"), "w") as fh: fh.write("gitdir: /x/.git/worktrees/demo-wt\n")
        self.post("/api/project/close", {"name": "demo-wt", "outcome": "archive"}, ok=False)


class UndoMove(CloseBase):
    def setUp(self):
        super().setUp(); self.original = self.snapshot(); self.close()
        self.moved = os.path.join(self.tmp, "archive", "demo")

    def refused(self, msg):
        before = self.snapshot(); n = len(server._undo)
        r = self.post("/api/undo", {}, ok=False); self.assertIn(msg, r["error"])
        self.assertEqual((self.snapshot(), len(server._undo)), (before, n))
        return r

    def test_undo_refuses_a_file_added_edited_or_removed_since(self):
        p = os.path.join(self.moved, "new.txt"); open(p, "w").close()
        self.assertIn("mv ", self.refused("changed since it moved")["error"]); os.remove(p)
        with open(os.path.join(self.moved, "app.py"), "a") as fh: fh.write("# edited\n")
        self.refused("changed since it moved")
        with open(os.path.join(self.moved, "app.py"), "w") as fh: fh.write("print(1)\n")
        os.utime(os.path.join(self.moved, "app.py"), ns=(1, 1)); self.refused("changed since it moved")

    def test_undo_refuses_when_the_old_name_is_taken_then_works_once_free(self):
        os.makedirs(self.demo); self.refused("exists again")
        os.rmdir(self.demo); self.post("/api/undo", {})
        self.assertEqual(self.snapshot(), self.original)

    def test_undo_refuses_a_moved_folder_swapped_for_a_symlink(self):
        out = os.path.realpath(self.tmp + "-out"); self.addCleanup(shutil.rmtree, out, ignore_errors=True)
        os.rename(self.moved, out); os.symlink(out, self.moved)
        self.refused("symlink")

    def test_undo_refuses_when_the_moved_folder_is_gone(self):
        os.rename(self.moved, os.path.join(self.tmp, "archive", "renamed")); self.refused("no longer in archive/")

    def test_git_activity_inside_the_moved_repo_does_not_block_undo(self):
        server._undo.clear(); shutil.rmtree(os.path.join(self.tmp, "archive"))
        shutil.rmtree(self.demo, ignore_errors=True); self.demo = self.project("demo", notes=NOTES)
        subprocess.run(["git", "init", "-q", "-b", "main", self.demo], check=True)
        subprocess.run(["git", "-C", self.demo, "add", "-A"], check=True); subprocess.run(["git", "-C", self.demo, "commit", "-qm", "x"], check=True)
        self.close()
        subprocess.run(["git", "-C", self.moved, "status"], capture_output=True, check=True)
        subprocess.run(["git", "-C", self.moved, "commit", "-q", "--allow-empty", "-m", "later"], check=True)
        self.post("/api/undo", {}); self.assertTrue(os.path.isdir(os.path.join(self.demo, ".git")))


class SyncApps(CloseBase):
    def stub(self, code=0):
        bindir = os.path.join(self.tmp, "stubbin"); os.makedirs(bindir, exist_ok=True)
        self.marker = os.path.join(self.tmp, "sync-ran"); p = os.path.join(bindir, "sync-apps")
        with open(p, "w") as fh: fh.write(f"#!/bin/sh\necho run >> {self.marker}\necho synced; exit {code}\n")
        os.chmod(p, 0o755)
        m = mock.patch.object(server, "SYNC_APPS", "sync-apps"); m.start(); self.addCleanup(m.stop)
        m = mock.patch.dict(os.environ, {"PATH": bindir + os.pathsep + "/usr/bin:/bin"}); m.start(); self.addCleanup(m.stop)

    def runs(self):
        try: return len(open(self.marker).read().split())
        except FileNotFoundError: return 0

    def test_runs_after_a_move_and_its_undo_but_not_a_next_round(self):
        self.stub()
        with mock.patch("sys.stderr"):
            self.assertEqual(self.close()["sync_apps"], "ran"); self.assertEqual(self.runs(), 1)
            self.assertEqual(self.post("/api/undo", {})["sync_apps"], "ran"); self.assertEqual(self.runs(), 2)
            r = self.close("next", done="Again.", due_days=3); self.assertNotIn("sync_apps", r); self.assertEqual(self.runs(), 2)

    def test_a_failing_sync_apps_never_fails_the_move(self):
        self.stub(code=3)
        with mock.patch("sys.stderr"): r = self.close()
        self.assertEqual((r["sync_apps"], r["moved_to"]), ("failed", "archive/demo")); self.assertEqual(self.runs(), 1)

    def test_not_installed_is_fine(self):
        self.assertEqual(self.close()["sync_apps"], "not installed")


class Faults(CloseBase):
    """The movedir step under failure: all or nothing, and what can't be put back stays on the undo list."""
    def setUp(self):
        super().setUp(); self.before = self.snapshot()

    def test_a_failed_rename_changes_nothing(self):
        real = os.rename
        def rename(a, b):
            if a == self.demo: raise OSError(errno.EXDEV, "Invalid cross-device link")  # another filesystem: never copied
            return real(a, b)
        with mock.patch("os.rename", side_effect=rename): self.close(ok=False)
        self.assertEqual((self.snapshot(), server._undo, self.tmp_leftovers()), (self.before, [], []))

    def test_a_failure_after_the_move_moves_it_back(self):
        real = server.Tx.movedir
        def movedir(tx, src, dst): real(tx, src, dst); raise OSError(5, "simulated failure after the move")
        with mock.patch.object(server.Tx, "movedir", movedir): self.close(ok=False)
        self.assertEqual((self.snapshot(), server._undo), (self.before, []))

    def test_a_rollback_that_cannot_move_back_leaves_an_unfinished_undo_that_finishes(self):
        real_movedir, real_rename = server.Tx.movedir, os.rename
        state = {"stuck": True}
        def movedir(tx, src, dst): real_movedir(tx, src, dst); raise OSError(5, "simulated")
        def rename(a, b):
            if state["stuck"] and b == self.demo: raise OSError(5, "disk went away")
            return real_rename(a, b)
        with mock.patch.object(server.Tx, "movedir", movedir), mock.patch("os.rename", side_effect=rename):
            code, r = self.call("/api/project/close", {"name": "demo", "outcome": "archive"})
        self.assertEqual(code, 500); self.assertIn("could not be rolled back", r["error"])
        [entry] = server._undo; self.assertTrue(entry["label"].startswith("unfinished:"))
        # the folder couldn't go back, so neither could its NOTES.md edit nor archive/ (not empty): all three wait for Undo
        self.assertEqual([op[0] for op in entry["ops"]], ["write", "mkdir", "movedir"])
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "archive", "demo")))
        state["stuck"] = False
        self.post("/api/undo", {})
        self.assertEqual((self.snapshot(), server._undo), (self.before, []))

    def test_an_undo_that_fails_half_way_moves_the_folder_forward_again(self):
        real = server.safefs.atomic_write
        def atomic_write(path, data, mode=None):
            if path.endswith(os.path.join("demo", "NOTES.md")): raise OSError(28, "No space left on device")
            return real(path, data, mode)
        self.close(); mid = self.snapshot()
        with mock.patch.object(server.safefs, "atomic_write", atomic_write):
            code, r = self.call("/api/undo", {})
        self.assertEqual(code, 500); self.assertIn("nothing was changed", r["error"])
        self.assertEqual((self.snapshot(), len(server._undo)), (mid, 1))
        self.post("/api/undo", {}); self.assertEqual(self.snapshot(), self.before)

    def test_a_folder_too_big_to_check_still_moves_but_its_undo_refuses(self):
        with mock.patch.object(server, "TREE_LIMIT", 1): self.close()
        mid = self.snapshot()
        r = self.post("/api/undo", {}, ok=False); self.assertIn("too big", r["error"]); self.assertIn("mv ", r["error"])
        self.assertEqual((self.snapshot(), len(server._undo)), (mid, 1))

    def test_tree_state_ignores_git_internals_and_gives_up_on_huge_folders(self):
        d = os.path.join(self.tmp, "t"); os.makedirs(os.path.join(d, ".git", "objects")); open(os.path.join(d, "f"), "w").close()
        s1 = server.tree_state(d); open(os.path.join(d, ".git", "index"), "w").close()
        self.assertEqual(server.tree_state(d), s1); self.assertEqual(s1[".git"], "git")
        os.symlink("f", os.path.join(d, "l")); self.assertEqual(server.tree_state(d)["l"], ("link", "f"))
        self.assertIsNone(server.tree_state(d, limit=1)); self.assertIsNone(server.tree_state(os.path.join(d, "missing")))


if __name__ == "__main__":
    unittest.main()
