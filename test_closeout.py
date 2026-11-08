#!/usr/bin/env python3
"""Ticket 12: close out a project folder (Next round / Maintained / Archive; Kill -> archive/<slug>_abandoned), the
"movedir" Tx step with its checked inverse, fault injection around it, and sync-apps afterwards. Every test runs on a
temp projects root with a fake dependency-check home (testkit), never the real folders."""
import errno
import json
import os
import shutil
import subprocess
import unittest
from unittest import mock

import depcheck
import server
from testkit import Fixture, undo_list

NOTES = ("# Demo\n\n- date: 2026-10-01\n- status: promoted\n- promoted: 2026-10-01\n- due: 2026-12-01\n- extensions: 1\n\n"
         "## Plan\n\n- definition of done: It ships.\n- pushed 2026-11-24 → 2026-12-01 (2026-10-20): parts late\n")


class CloseBase(Fixture):
    def setUp(self):
        super().setUp()
        self.home = os.path.join(self.tmp, "home"); os.makedirs(self.home)
        self.sources = depcheck.Sources(self.home, proc=os.path.join(self.tmp, "proc"), crontab=lambda: "")
        p = mock.patch.object(server, "DEPS", self.sources); p.start(); self.addCleanup(p.stop)
        self.serve_with(env=self.deps_env(self.sources))  # under TRIAGE_SERVER: the same sources, through the deps hooks
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

    def memory(self): return self.memory_of(self.demo)

    def memory_of(self, folder):
        d = os.path.join(self.sources.claude_projects, depcheck.claude_key(folder)); os.makedirs(d); return d

    def remote(self, path, body=None, host="192.168.1.5"):
        """A request from a browser that is not on this machine (needs BIND_HOST 0.0.0.0 for its Host name to pass)."""
        import urllib.error, urllib.request
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method="GET" if body is None else "POST",
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Host": f"{host}:{self.port}", **({} if body is None else {"Content-Type": "application/json"})})
        try:
            with urllib.request.urlopen(req) as r: return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e: return e.code, json.loads(e.read())


class Outcomes(CloseBase):
    def test_undo_lists_the_reverse_to_dos(self):
        """Close out lists the memory-folder mv and the T3 path as to-dos; its undo lists the way back."""
        mem = self.memory(); self.close(); moved = os.path.join(self.tmp, "archive", "demo")
        new_mem = os.path.join(self.sources.claude_projects, depcheck.claude_key(moved))
        os.rename(mem, new_mem)  # the user did the to-do
        r = self.post("/api/undo", {})
        self.assertEqual([x["kind"] for x in r["todos"]], ["agent-memory", "t3"])
        self.assertIn(f"mv {new_mem} {mem}", r["todos"][0]["fix"])
        self.assertIn(f"{moved} to {self.demo}", r["todos"][1]["fix"])
        self.note("2026-10-01-x.md"); self.post("/api/action", {"file": "2026-10-01-x.md", "action": "kill", "note": "Not needed now."})
        self.assertNotIn("todos", self.post("/api/undo", {}))  # a note's undo has none

    def test_archive_moves_the_folder_and_writes_shipped_closed_and_moved(self):
        before = self.snapshot(); mem = self.memory(); t = server.today().isoformat()
        r = self.close()
        self.assertEqual((r["moved_to"], r["msg"]), ("archive/demo", "Shipped demo 🎉 → archive/demo. Its board data moved out of "
                                                     "NOTES.md into RawNotes/projects/: commit the trimmed archive/demo/NOTES.md yourself."))
        self.assertFalse(os.path.exists(self.demo))
        text = self.record("archive", "demo")  # the board record moved with the folder
        self.assertIsNone(self.record("active", "demo"))
        self.assertEqual((server.field(text, "status"), server.field(text, "closed"), server.field(text, "shipped")), ("shipped", t, t))
        notes = self.read("archive", "demo", "NOTES.md")  # migrated on this first write: the board data left it
        self.assertEqual(notes, "# Demo\n\n## Plan\n\n- definition of done: It ships.\n")
        self.assertIn("- pushed 2026-11-24 → 2026-12-01 (2026-10-20): parts late", text.split("## Extensions", 1)[1])
        new_mem = os.path.join(self.sources.claude_projects, depcheck.claude_key(os.path.join(self.tmp, "archive", "demo")))
        moved = text.split("## Moved", 1)[1]
        self.assertIn(f"- {t}: moved from `active/demo/` to `archive/demo/`. Left to do by hand:", moved)
        self.assertIn("keyed by the old path: move it in `~/.claude/projects/` to the key of its new path", moved)
        self.assertIn("  - Point the T3 Code project for this folder at its new place", moved)
        self.assertEqual([x["kind"] for x in r["todos"]], ["agent-memory", "t3"])
        self.assertEqual(r["todos"][0]["fix"], f"mv {mem} {new_mem}")  # the toast keeps the exact commands
        self.assertTrue(os.path.isdir(mem))  # the app never moves memory folders itself
        [f] = [x for x in self.get("/api/notes")["inflight"] if x["path"] == "archive/demo/NOTES.md"]
        self.assertEqual((f["done"], f["closed"]), (True, t))  # shown in the Closed column for the window
        self.assertEqual(self.get("/api/projects")["projects"], [])
        self.post("/api/undo", {})
        after = self.snapshot(); self.assertEqual({k: v for k, v in after.items() if not k.startswith("home/")},
                                                  {k: v for k, v in before.items() if not k.startswith("home/")})

    def test_moved_in_notes_names_no_absolute_path_but_the_toast_does(self):
        """NOTES.md is tracked in the project's own (maybe public) repo: ## Moved says what to do without the home
        folder, the username or the projects root; the answer (toast and panel, this machine only) has the commands."""
        g = ["git", "-c", "user.name=T", "-c", "user.email=t@example.com", "-C", self.demo]
        subprocess.run(["git", "init", "-q", "-b", "main", self.demo], check=True)
        subprocess.run(g + ["add", "-A"], check=True); subprocess.run(g + ["commit", "-qm", "x"], check=True)
        wt = os.path.join(self.tmp, "wt"); subprocess.run(g + ["worktree", "add", "-q", wt], check=True)
        shutil.rmtree(wt)  # a stale worktree record: a to-do, not a blocker
        mem = self.memory(); os.makedirs(os.path.join(self.demo, "sub")); self.memory_of(os.path.join(self.demo, "sub"))
        r = self.close(); moved = self.record("archive", "demo").split("## Moved", 1)[1]
        self.assertEqual(sorted(x["kind"] for x in r["todos"]), ["agent-memory", "agent-memory", "t3", "worktree"])
        self.assertEqual(moved.count("\n  - "), 4)
        for secret in (self.tmp, self.home, depcheck.claude_key(self.tmp), depcheck.claude_key(self.home),
                       os.path.realpath(self.tmp), os.path.expanduser("~"), os.path.basename(os.path.expanduser("~"))):
            self.assertNotIn(secret, moved)
        self.assertIn("run `git worktree prune` in the moved folder", moved)
        fixes = [x["fix"] for x in r["todos"]]; dest = os.path.join(self.tmp, "archive", "demo")
        self.assertIn(f"mv {mem} {os.path.join(self.sources.claude_projects, depcheck.claude_key(dest))}", fixes)
        self.assertIn(f"git -C {dest} worktree prune", fixes)
        self.assertIn(f"Update any T3 Code project whose folder is {self.demo} to {dest}", fixes)
        self.post("/api/undo", {}); self.assertTrue(os.path.isdir(self.demo))
        self.assertEqual(self.read("active", "demo", "NOTES.md"), NOTES)
        self.assertEqual((self.record("active", "demo"), self.record("archive", "demo")), (None, None))

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
        text = self.record("archive", "demo_abandoned")
        self.assertEqual(server.field(text, "status"), "dead"); self.assertIn("Nobody needs it.", text.split("## Outcome", 1)[1])
        self.assertNotIn("Nobody needs it.", self.read("archive", "demo_abandoned", "NOTES.md"))
        self.post("/api/undo", {}); self.assertEqual(self.read("active", "demo", "NOTES.md"), NOTES)

    def test_next_round_stays_and_records_the_finished_round(self):
        t = server.today().isoformat()
        r = self.close("next", done="Round two: it runs on the phone.", due_days=10)
        due = (server.today() + server.dt.timedelta(days=10)).isoformat()
        self.assertNotIn("moved_to", r); self.assertTrue(r["msg"].startswith(f"Shipped demo 🎉 Next round ships by {due}. Its board data moved"))
        text = self.record("active", "demo")
        self.assertEqual([server.field(text, k) for k in ("status", "promoted", "due", "extensions", "closed", "shipped")],
                         ["promoted", t, due, "0", "", ""])
        self.assertIn(f"## Rounds\n\n- 2026-10-01 → shipped {t} (ship by 2026-12-01, pushed 1×). Done means: It ships.", text)
        notes = self.read("active", "demo", "NOTES.md")  # done means stays in the project: it is the plan
        self.assertEqual(notes, "# Demo\n\n## Plan\n\n- definition of done: Round two: it runs on the phone.\n")
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
        self.assertIn("open-ended", r["msg"]); self.assertEqual(server.field(self.record("experiments", "spike"), "due"), "")
        self.assertEqual(server.field(open(exp + "/NOTES.md").read(), "status"), "")

    def test_next_round_puts_done_means_where_it_was_found(self):
        with open(os.path.join(self.demo, "README.md"), "w") as fh: fh.write("# Demo\n\n## Definition of done\n\nThe old README goal.\n")
        os.remove(os.path.join(self.demo, "NOTES.md"))
        self.close("next", done="The new goal.", due_days=3)
        self.assertIn("The new goal.", self.read("active", "demo", "README.md"))
        self.assertIn("Done means: The old README goal.", self.record("active", "demo"))
        self.assertFalse(os.path.exists(os.path.join(self.demo, "NOTES.md")))  # nothing board-side is written into the folder
        self.post("/api/undo", {}); self.assertIsNone(self.record("active", "demo"))
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
        self.assertEqual((self.snapshot(), undo_list()), (before, []))
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
        self.assertEqual((self.snapshot(), os.listdir(outside), undo_list()), (before, [], []))

    def test_a_worktree_is_not_a_project_to_close(self):
        wt = self.project("demo-wt")
        with open(os.path.join(wt, ".git"), "w") as fh: fh.write("gitdir: /x/.git/worktrees/demo-wt\n")
        self.post("/api/project/close", {"name": "demo-wt", "outcome": "archive"}, ok=False)


class UndoMove(CloseBase):
    def setUp(self):
        super().setUp(); self.original = self.snapshot(); self.close()
        self.moved = os.path.join(self.tmp, "archive", "demo")

    def refused(self, msg):
        before = self.snapshot(); n = len(undo_list())
        r = self.post("/api/undo", {}, ok=False); self.assertIn(msg, r["error"])
        self.assertEqual((self.snapshot(), len(undo_list())), (before, n))
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
        self.clear_undo(); shutil.rmtree(os.path.join(self.tmp, "archive")); shutil.rmtree(os.path.join(self.notes, "projects"))
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
        self.serve_with(env={"RAWTRIAGE_TEST_SYNC_APPS": "sync-apps"})  # under TRIAGE_SERVER: the hook names it the same way
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
        self.assertEqual((self.snapshot(), undo_list(), self.tmp_leftovers()), (self.before, [], []))

    def test_a_failure_after_the_move_moves_it_back(self):
        real = server.Tx.movedir
        def movedir(tx, src, dst): real(tx, src, dst); raise OSError(5, "simulated failure after the move")
        with mock.patch.object(server.Tx, "movedir", movedir): self.close(ok=False)
        self.assertEqual((self.snapshot(), undo_list()), (self.before, []))

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
        [entry] = undo_list(); self.assertTrue(entry["label"].startswith("unfinished:"))
        # the folder couldn't go back, so neither could its NOTES.md edit nor archive/ (not empty): all three wait for Undo
        self.assertEqual([op[0] for op in entry["ops"]], ["write", "mkdir", "movedir"])
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "archive", "demo")))
        state["stuck"] = False
        self.post("/api/undo", {})
        self.assertEqual((self.snapshot(), undo_list()), (self.before, []))

    def test_an_undo_that_fails_half_way_moves_the_folder_forward_again(self):
        real = server.safefs.atomic_write
        def atomic_write(path, data, mode=None):
            if path.endswith(os.path.join("demo", "NOTES.md")): raise OSError(28, "No space left on device")
            return real(path, data, mode)
        self.close(); mid = self.snapshot()
        with mock.patch.object(server.safefs, "atomic_write", atomic_write):
            code, r = self.call("/api/undo", {})
        self.assertEqual(code, 500); self.assertIn("nothing was changed", r["error"])
        self.assertEqual((self.snapshot(), len(undo_list())), (mid, 1))
        self.post("/api/undo", {}); self.assertEqual(self.snapshot(), self.before)

    def test_a_folder_too_big_to_check_still_moves_but_its_undo_refuses(self):
        self.serve_with(env={"RAWTRIAGE_TEST_TREE_LIMIT": "1"})  # under TRIAGE_SERVER: for the whole test (undo refuses either way)
        with mock.patch.object(server, "TREE_LIMIT", 1): self.close()
        mid = self.snapshot()
        r = self.post("/api/undo", {}, ok=False); self.assertIn("too big", r["error"]); self.assertIn("mv ", r["error"])
        self.assertEqual((self.snapshot(), len(undo_list())), (mid, 1))

    def test_the_deps_answer_warns_before_a_move_that_undo_cannot_check(self):
        self.assertNotIn("undo_warning", self.get("/api/project/deps?name=demo&kind=active"))
        self.serve_with(env={"RAWTRIAGE_TEST_TREE_LIMIT": "1"}, restart=True)  # under TRIAGE_SERVER: a server with the limit
        with mock.patch.object(server, "TREE_LIMIT", 1):
            d = self.get("/api/project/deps?name=demo&kind=active")
        self.assertIn("can't be undone", d["undo_warning"]); self.assertIn("mv ", d["undo_warning"])
        self.assertNotIn(self.tmp, d["undo_warning"])  # it may go to a phone: no absolute path

    def test_tree_too_big_agrees_with_tree_state_on_entries_it_cannot_stat(self):
        """A folder that can be listed but not entered (r, no x): its names show, lstat of them fails. tree_state gives
        up, so the warning must say undo can't check it."""
        d = os.path.join(self.tmp, "t"); sub = os.path.join(d, "locked"); os.makedirs(sub); open(os.path.join(sub, "f"), "w").close()
        os.chmod(sub, 0o644); self.addCleanup(os.chmod, sub, 0o755)
        self.assertIsNone(server.tree_state(d))
        self.assertTrue(server.tree_too_big(d))
        os.chmod(sub, 0o755)
        self.assertIsNotNone(server.tree_state(d)); self.assertFalse(server.tree_too_big(d))

    def test_tree_state_ignores_git_internals_and_gives_up_on_huge_folders(self):
        d = os.path.join(self.tmp, "t"); os.makedirs(os.path.join(d, ".git", "objects")); open(os.path.join(d, "f"), "w").close()
        s1 = server.tree_state(d); open(os.path.join(d, ".git", "index"), "w").close()
        self.assertEqual(server.tree_state(d), s1); self.assertEqual(s1[".git"], "git")
        os.symlink("f", os.path.join(d, "l")); self.assertEqual(server.tree_state(d)["l"], ("link", "f"))
        self.assertIsNone(server.tree_state(d, limit=1)); self.assertIsNone(server.tree_state(os.path.join(d, "missing")))


class OffThisMachine(CloseBase):
    """--host 0.0.0.0 (or a DNS name rebound to it): a browser that isn't on this machine may look, redacted, but may
    not move a folder. Its Host name passes the guard; _this_machine() says no."""
    def setUp(self):
        super().setUp()
        p = mock.patch.object(server, "BIND_HOST", "0.0.0.0"); p.start(); self.addCleanup(p.stop)
        self.sources = depcheck.Sources(self.tmp, proc=os.path.join(self.tmp, "proc"), crontab=lambda: f"0 * * * * {self.demo}/app.py --secret-flag\n")
        p = mock.patch.object(server, "DEPS", self.sources); p.start(); self.addCleanup(p.stop)
        self.serve_with(env=self.deps_env(self.sources), host="0.0.0.0")  # under TRIAGE_SERVER: these sources, --host 0.0.0.0
        pid = os.path.join(self.tmp, "proc", "4242"); os.makedirs(pid)
        with open(os.path.join(pid, "cmdline"), "wb") as fh: fh.write(f"python3\0{self.demo}/app.py\0--token=hunter2\0".encode())

    def test_deps_off_this_machine_hide_command_lines_crontab_lines_and_the_home_path(self):
        os.makedirs(os.path.join(self.sources.claude_projects, depcheck.claude_key(self.demo)))  # memory dir: the home path in key form
        here = self.get("/api/project/deps?name=demo&kind=active")
        self.assertIn("--token=hunter2", str(here)); self.assertIn("--secret-flag", str(here))
        code, there = self.remote("/api/project/deps?name=demo&kind=active")
        self.assertEqual(code, 200, there); self.assertTrue(there["redacted"])
        self.assertEqual(sorted(h["kind"] for h in there["blockers"]), ["crontab", "process"])
        for h in there["blockers"]: self.assertEqual(h["detail"], ""); self.assertTrue(h["fix"])
        text = json.dumps(there)
        for secret in ("hunter2", "--secret-flag", self.tmp, depcheck.claude_key(self.tmp)): self.assertNotIn(secret, text)
        self.assertIn("~/active/demo", text)  # the T3 to-do still names the folder, home-relative
        [mem] = [h for h in there["todos"] if h["kind"] == "agent-memory"]
        self.assertEqual(mem["where"], "~/.claude/projects/~-active-demo")  # the memory dir's key, home-relative too
        self.assertIn("~/.claude/projects/~-archive-demo", mem["fix"])

    def test_a_move_is_refused_off_this_machine_and_nothing_changes(self):
        self.sources = depcheck.Sources(self.home, proc=os.path.join(self.tmp, "noproc"), crontab=lambda: "")
        self.serve_with(env=self.deps_env(self.sources))  # under TRIAGE_SERVER: no blockers, through the deps hooks
        with mock.patch.object(server, "DEPS", self.sources):
            before = self.snapshot(); n = len(undo_list())
            for path, body in (("/api/project/close", {"name": "demo", "kind": "active", "outcome": "archive"}),
                               ("/api/project/close", {"name": "demo", "kind": "active", "outcome": "maintained"}),
                               ("/api/project/status", {"name": "demo", "kind": "active", "action": "shipped", "outcome": "archive"}),
                               ("/api/project/status", {"path": "active/demo/NOTES.md", "action": "shipped", "outcome": "archive"}),
                               ("/api/project/status", {"name": "demo", "kind": "active", "action": "kill", "note": "not needed"})):
                code, r = self.remote(path, body)
                self.assertEqual(code, 400, (path, body, r)); self.assertIn("machine running the server", r["error"]); self.assertNotIn("blockers", r)
            self.assertEqual((self.snapshot(), len(undo_list())), (before, n))
            code, r = self.remote("/api/project/close", {"name": "demo", "kind": "active", "outcome": "next", "done": "Round two.", "due_days": 7})
            self.assertEqual(code, 200, r)  # a next round moves nothing: fine from the phone
            self.assertEqual(self.close()["moved_to"], "archive/demo")  # and from this machine a move still works


class UndoOffThisMachine(CloseBase):
    """Undo of a close out moves a folder back (and runs sync-apps); undo of a promote removes a .git. Neither may be
    driven by a browser that isn't on this machine (a --host 0.0.0.0 bind, or a rebound DNS name)."""
    def setUp(self):
        super().setUp()
        p = mock.patch.object(server, "BIND_HOST", "0.0.0.0"); p.start(); self.addCleanup(p.stop)
        self.serve_with(host="0.0.0.0")  # under TRIAGE_SERVER: --host 0.0.0.0

    def refused_remotely(self):
        before = self.snapshot(); n = len(undo_list())
        code, r = self.remote("/api/undo", {})
        self.assertEqual(code, 400, r); self.assertIn("machine running the server", r["error"])
        self.assertEqual((self.snapshot(), len(undo_list())), (before, n))

    def test_off_machine_undo_of_a_close_out_is_refused_and_the_folder_stays(self):
        original = self.snapshot(); self.close()
        self.refused_remotely()
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "archive", "demo"))); self.assertFalse(os.path.exists(self.demo))
        self.post("/api/undo", {})  # from this machine it works
        self.assertEqual(self.snapshot(), original)

    def test_off_machine_undo_of_a_promote_is_refused(self):
        self.note("2026-10-01-p.md", title="P")
        self.post("/api/action", {"file": "2026-10-01-p.md", "action": "promote", "dest": "experiments", "note": "We know."})
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "experiments", "p", ".git")))
        self.refused_remotely()
        self.post("/api/undo", {}); self.assertFalse(os.path.exists(os.path.join(self.tmp, "experiments", "p")))

    def test_off_machine_undo_of_a_note_change_still_works(self):
        self.note("2026-10-01-k.md", title="K")
        self.post("/api/action", {"file": "2026-10-01-k.md", "action": "keep"})
        code, r = self.remote("/api/undo", {}); self.assertEqual(code, 200, r)


class UndoReverseMoveBlockers(CloseBase):
    """Undo of a move is a move too: what points at the folder in its new place blocks it, like close out."""
    def test_a_blocker_on_the_new_place_refuses_the_undo_with_the_list(self):
        original = self.snapshot(); self.close(); moved = os.path.join(self.tmp, "archive", "demo")
        os.makedirs(self.sources.bin_dir, exist_ok=True)
        with open(os.path.join(self.sources.bin_dir, "demo"), "w") as fh: fh.write(f"#!/bin/sh\nexec {moved}/app.py\n")
        before = self.snapshot(); n = len(undo_list())
        code, r = self.call("/api/undo", {})
        self.assertEqual(code, 400, r); self.assertIn("Can't move demo back", r["error"]); self.assertIn("/.local/bin/demo", r["error"])
        self.assertEqual([b["kind"] for b in r["blockers"]], ["bin"])
        self.assertEqual((self.snapshot(), len(undo_list())), (before, n))
        os.remove(os.path.join(self.sources.bin_dir, "demo"))
        self.post("/api/undo", {})
        self.assertEqual({k: v for k, v in self.snapshot().items() if not k.startswith("home/")},
                         {k: v for k, v in original.items() if not k.startswith("home/")})


if __name__ == "__main__":
    unittest.main()
