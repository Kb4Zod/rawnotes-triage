#!/usr/bin/env python3
"""v2.05: transactional recovery. Fault injection through monkeypatched os calls and the log; every case checks
the files byte for byte (and the undo list) against what the user had before."""
import os
import threading
import unittest
from unittest import mock

import server
from testkit import Fixture, link


class Faults(Fixture):
    def setUp(self):
        super().setUp()
        self.a = self.note("2026-10-01-a.md", "Note A"); self.b = self.note("2026-10-02-b.md", "Note B")
        self.c = self.note("2026-10-03-c.md", "Note C")
        self.before = self.snapshot()

    def fail_nth(self, target, n, exc=None):
        """Patch `target` (e.g. 'os.rename') so its n-th call (1-based) raises; the rest run for real."""
        real = eval(target, {"os": os, "server": server}); calls = []
        def wrapper(*a, **k):
            calls.append(a)
            if len(calls) == n: raise exc or OSError(5, "simulated disk error")
            return real(*a, **k)
        p = mock.patch(target, wrapper); p.start(); self.addCleanup(p.stop)
        return calls

    # ---- 1: logging can't take a change down
    def test_log_oserror_does_not_fail_or_strand_a_kill(self):
        with mock.patch.object(server, "log", side_effect=OSError(28, "No space left on device")):
            r = self.post("/api/action", {"file": "2026-10-01-a.md", "action": "kill", "note": "meh"})
        self.assertIn("Killed", r["msg"])
        self.assertTrue(os.path.exists(os.path.join(self.notes, "done", "2026-10-01-a.md")))
        self.assertEqual(len(server._undo), 1)
        self.post("/api/undo", {})
        self.assertEqual(self.snapshot(), self.before)

    def test_unwritable_log_file_does_not_fail_changes(self):
        server.log = self.saved[2]; server.LOG_PATH = os.path.join(self.tmp, "no", "such", "dir", "triage.log")
        self.post("/api/action", {"file": "2026-10-01-a.md", "action": "kill"})
        self.assertEqual(len(server._undo), 1)

    def test_a_rolled_back_change_writes_no_log_lines(self):
        server.log = self.saved[2]; server.LOG_PATH = os.path.join(self.tmp, "triage.log")
        self.fail_nth("os.rename", 3)
        self.post("/api/batch", {"files": ["2026-10-01-a.md", "2026-10-02-b.md", "2026-10-03-c.md"], "action": "kill", "note": "x"}, ok=False)
        self.assertFalse(os.path.exists(server.LOG_PATH) and open(server.LOG_PATH).read().strip())  # nothing decided, nothing logged
        mock.patch.stopall()
        self.post("/api/batch", {"files": ["2026-10-01-a.md", "2026-10-02-b.md"], "action": "kill", "note": "x"})
        self.assertEqual(len(open(server.LOG_PATH).read().strip().splitlines()), 2)  # the successful one logs once per note

    # ---- 1: prevalidation
    def test_merge_with_a_duplicate_source_changes_nothing(self):
        r = self.post("/api/merge", {"files": ["2026-10-01-a.md", "2026-10-01-a.md"], "title": "Dup"}, ok=False)
        self.assertIn("twice", r["error"])
        self.assertEqual((self.snapshot(), server._undo), (self.before, []))

    def test_merge_and_batch_with_missing_or_bad_sources_change_nothing(self):
        for bad in (["2026-10-01-a.md", "nope.md"], ["2026-10-01-a.md", "../escape.md"], ["2026-10-01-a.md", 7], "a.md", None):
            self.post("/api/merge", {"files": bad, "title": "T"}, ok=False)
            self.post("/api/batch", {"files": bad, "action": "kill"}, ok=False)
        self.post("/api/batch", {"files": ["2026-10-01-a.md", "2026-10-01-a.md"], "action": "kill"}, ok=False)
        self.post("/api/batch", {"files": ["2026-10-01-a.md", "2026-10-02-b.md"], "action": "keep", "status": "dead"}, ok=False)
        self.post("/api/batch", {"files": ["2026-10-01-a.md", "2026-10-02-b.md"], "action": "promote"}, ok=False)
        self.assertEqual((self.snapshot(), server._undo), (self.before, []))

    def test_batch_with_one_note_already_in_the_archive_changes_nothing(self):
        self.post("/api/action", {"file": "2026-10-03-c.md", "action": "kill"}); server._undo.clear()
        mid = self.snapshot()
        self.post("/api/batch", {"files": ["2026-10-01-a.md", "done/2026-10-03-c.md"], "action": "kill"}, ok=False)
        self.assertEqual((self.snapshot(), server._undo), (mid, []))

    def test_symlink_alias_of_the_same_note_counts_as_a_duplicate(self):
        link(self.a, os.path.join(self.notes, "2026-10-09-alias.md"))
        self.post("/api/batch", {"files": ["2026-10-01-a.md", "2026-10-09-alias.md"], "action": "kill"}, ok=False)
        self.assertEqual(server._undo, [])

    # ---- 1: rollback
    def test_failed_move_in_a_kill_rolls_the_edit_back(self):
        self.fail_nth("os.rename", 1)
        r = self.post("/api/action", {"file": "2026-10-01-a.md", "action": "kill", "note": "x"}, ok=False)
        self.assertIn("simulated", r["error"])
        self.assertEqual((self.snapshot(), server._undo, self.tmp_leftovers()), (self.before, [], []))

    def test_failed_final_step_of_a_promote_removes_the_new_project(self):
        self.fail_nth("os.rename", 1)  # the move of the capture to done/ is the last step
        self.post("/api/action", {"file": "2026-10-01-a.md", "action": "promote", "dest": "active", "note": "Ships.", "due_days": 3}, ok=False)
        self.assertEqual((self.snapshot(), server._undo), (self.before, []))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "active")))

    def test_batch_that_fails_on_the_third_note_puts_all_back(self):
        self.fail_nth("os.rename", 3)
        self.post("/api/batch", {"files": ["2026-10-01-a.md", "2026-10-02-b.md", "2026-10-03-c.md"], "action": "kill", "note": "x"}, ok=False)
        self.assertEqual((self.snapshot(), server._undo, self.tmp_leftovers()), (self.before, [], []))

    def test_merge_that_fails_midway_puts_everything_back(self):
        self.fail_nth("os.rename", 2)
        self.post("/api/merge", {"files": ["2026-10-01-a.md", "2026-10-02-b.md", "2026-10-03-c.md"], "title": "All", "then": "promote",
                                 "dest": "active", "note": "Done.", "slug": "all"}, ok=False)
        self.assertEqual((self.snapshot(), server._undo), (self.before, []))

    def test_failed_atomic_write_leaves_the_old_file_and_no_temp_file(self):
        self.fail_nth("os.replace", 1)
        self.post("/api/action", {"file": "2026-10-01-a.md", "action": "keep", "note": "next"}, ok=False)
        self.assertEqual((self.snapshot(), self.tmp_leftovers(), server._undo), (self.before, [], []))

    def test_restore_and_project_writes_roll_back_too(self):
        self.post("/api/action", {"file": "2026-10-01-a.md", "action": "kill"}); server._undo.clear(); mid = self.snapshot()
        with mock.patch("os.rename", side_effect=OSError(5, "simulated")):
            self.post("/api/restore", {"file": "done/2026-10-01-a.md"}, ok=False)
        self.assertEqual((self.snapshot(), server._undo), (mid, []))
        self.post("/api/action", {"file": "2026-10-02-b.md", "action": "promote", "dest": "active", "note": "x", "due_days": 3}); server._undo.clear()
        mid = self.snapshot()
        self.fail_nth("os.replace", 1)
        self.post("/api/project/status", {"path": "active/b/NOTES.md", "action": "shipped"}, ok=False)
        self.assertEqual((self.snapshot(), server._undo), (mid, []))

    # ---- 1: partial failure stays recoverable
    def test_rollback_that_cannot_finish_leaves_an_undo_entry_that_finishes_the_job(self):
        real_replace = os.replace
        state = {"broken": False}
        def replace(a, b):
            if state["broken"]: raise OSError(5, "disk went away")
            return real_replace(a, b)
        # kill = write note, then move: the move fails and from then on nothing works, so the rollback can't undo the write
        def rename_then_break(a, b):
            state["broken"] = True; raise OSError(5, "disk went away")
        with mock.patch("os.rename", rename_then_break), mock.patch("os.replace", replace):
            r = self.post("/api/action", {"file": "2026-10-01-a.md", "action": "kill", "note": "x"}, ok=False)
        self.assertIn("could not be rolled back", r["error"])
        self.assertEqual(len(server._undo), 1); self.assertIn("unfinished", server._undo[0]["label"])
        self.assertNotEqual(self.snapshot(), self.before)  # the edit is still there...
        state["broken"] = False
        self.post("/api/undo", {})  # ...and Undo finishes the job
        self.assertEqual((self.snapshot(), server._undo), (self.before, []))

    def test_undo_that_fails_midway_keeps_the_entry_whole(self):
        self.post("/api/action", {"file": "2026-10-01-a.md", "action": "promote", "dest": "active", "note": "Ships.", "due_days": 3})
        after = self.snapshot()
        real = os.rename; n = []
        def flaky(a, b):
            n.append(1)
            if len(n) == 1: raise OSError(5, "simulated")
            return real(a, b)
        with mock.patch("os.rename", flaky): self.post("/api/undo", {}, ok=False)
        self.assertEqual((self.snapshot(), len(server._undo)), (after, 1))
        self.post("/api/undo", {}); self.assertEqual(self.snapshot(), self.before)

    def test_undo_whose_redo_also_fails_keeps_only_what_still_stands(self):
        self.post("/api/action", {"file": "2026-10-01-a.md", "action": "promote", "dest": "active", "note": "Ships.", "due_days": 3})
        real_rename = os.rename; calls = []
        def broken_after_first(a, b):
            calls.append(1)
            if len(calls) == 1: return real_rename(a, b)  # the capture moves back...
            raise OSError(5, "disk went away")           # ...then the disk dies, so it can't be put back either
        with mock.patch("os.rename", broken_after_first), mock.patch("os.replace", side_effect=OSError(5, "disk went away")):
            r = self.post("/api/undo", {}, ok=False)
        self.assertIn("could not be put back", r["error"])
        # the capture is back in the Inbox; the entry no longer claims it moved
        self.assertTrue(os.path.exists(self.a))
        self.assertNotIn(("move", self.a), [(o[0], o[1]) for o in server._undo[0]["ops"]])

    # ---- 1: undo is still conflict-aware
    def test_undo_refuses_after_an_outside_edit_even_to_a_created_folder(self):
        self.post("/api/action", {"file": "2026-10-01-a.md", "action": "promote", "dest": "active", "note": "Ships.", "due_days": 3})
        with open(os.path.join(self.tmp, "active", "a", "extra.txt"), "w") as fh: fh.write("mine")
        r = self.post("/api/undo", {}, ok=False); self.assertIn("changed since", r["error"])
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "active", "a", "NOTES.md")))

    # ---- bytes, modes and line endings survive
    def test_write_keeps_mode_crlf_and_undecodable_bytes(self):
        raw = b"# Cafe \xe9\r\n\r\n- date: 2026-10-04\r\n- status: open\r\n- type: research\r\n\r\n## Notes\r\n\r\nline\r\n"
        path = os.path.join(self.notes, "2026-10-04-crlf.md")
        with open(path, "wb") as fh: fh.write(raw)
        os.chmod(path, 0o640); mode = os.stat(path).st_mode & 0o7777  # 0o640, or 0o666 on Windows (no Unix bits there)
        self.post("/api/action", {"file": "2026-10-04-crlf.md", "action": "keep", "note": "go"})
        with open(path, "rb") as fh: got = fh.read()
        self.assertIn(b"- status: cooking\r\n", got); self.assertIn(b"Cafe \xe9\r\n", got)
        self.assertNotIn(b"\n", got.replace(b"\r\n", b""))  # no lone LF crept in
        self.assertEqual(os.stat(path).st_mode & 0o7777, mode)
        self.post("/api/undo", {})
        with open(path, "rb") as fh: self.assertEqual(fh.read(), raw)
        self.assertEqual(os.stat(path).st_mode & 0o7777, mode)

    def test_killed_crlf_note_keeps_its_line_endings_in_done(self):
        path = os.path.join(self.notes, "2026-10-04-crlf.md")
        with open(path, "wb") as fh: fh.write(b"# T\r\n\r\n- status: open\r\n")
        self.post("/api/action", {"file": "2026-10-04-crlf.md", "action": "kill", "note": "no"})
        with open(os.path.join(self.notes, "done", "2026-10-04-crlf.md"), "rb") as fh: got = fh.read()
        self.assertTrue(got.startswith(b"# T\r\n\r\n- status: dead\r\n")); self.assertNotIn(b"\n", got.replace(b"\r\n", b""))

    # ---- 3: done means shares the one path (undo, conflicts, lost updates)
    def test_done_means_edit_is_undoable(self):
        self.project("p", notes="# P\n\n- status: promoted\n\n## Plan\n\n- definition of done: Old.\n")
        mid = self.snapshot()
        self.post("/api/done", {"name": "p", "text": "New goal", "expected": "Old."})
        self.assertIn("New goal", open(os.path.join(self.tmp, "active", "p", "NOTES.md")).read())
        self.assertEqual(len(server._undo), 1)
        self.post("/api/undo", {}); self.assertEqual(self.snapshot(), mid)

    def test_done_means_and_status_edits_racing_lose_nothing(self):
        for i in range(12):
            self.project(f"r{i}", notes=f"# R{i}\n\n- date: 2026-10-01\n- status: promoted\n- due: 2026-12-01\n\n## Plan\n\n- definition of done: Old.\n")
        errors = []
        def edit(i):
            try: self.post("/api/done", {"name": f"r{i}", "text": f"New {i}", "expected": "Old."})
            except Exception as e: errors.append(e)
        def ship(i):
            try: self.post("/api/project/status", {"name": f"r{i}", "action": "shipped"})
            except Exception as e: errors.append(e)
        ts = [threading.Thread(target=f, args=(i,)) for i in range(12) for f in (edit, ship)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(errors, [])
        for i in range(12):
            text = open(os.path.join(self.tmp, "active", f"r{i}", "NOTES.md")).read()
            self.assertIn(f"definition of done: New {i}", text); self.assertIn("- status: shipped", text)
        self.assertEqual(len(server._undo), server.UNDO_MAX)  # 24 changes, the newest UNDO_MAX kept

    def test_done_means_refuses_a_stale_edit_after_a_status_change(self):
        self.project("s", notes="# S\n\n- status: promoted\n\n- definition of done: Old.\n")
        self.post("/api/project/status", {"name": "s", "action": "shipped"})
        self.post("/api/done", {"name": "s", "text": "Mine", "expected": "Something else"}, ok=False)
        self.assertIn("- status: shipped", open(os.path.join(self.tmp, "active", "s", "NOTES.md")).read())

    # ---- every page mutation can be undone
    def test_every_mutation_endpoint_pushes_an_undo_step(self):
        self.project("p", notes="# P\n\n- status: promoted\n- due: 2026-12-01\n\n- definition of done: Old.\n")
        steps = [("/api/action", {"file": "2026-10-01-a.md", "action": "keep", "note": "n"}),
                 ("/api/batch", {"files": ["2026-10-02-b.md", "2026-10-03-c.md"], "action": "kill"}),
                 ("/api/restore", {"file": "done/2026-10-02-b.md"}),
                 ("/api/project/status", {"name": "p", "action": "shipped"}),
                 ("/api/done", {"name": "p", "text": "New", "expected": "Old."})]
        for i, (path, body) in enumerate(steps, 1):
            self.post(path, body); self.assertEqual(len(server._undo), i, path)
        for _ in steps: self.post("/api/undo", {})
        self.assertEqual(self.snapshot().keys() - self.before.keys(), {"active/", "active/p/", "active/p/NOTES.md"})


class UndoContainment(Fixture):
    """v2.06: undo, rollback and redo recovery check every recorded path again, right before touching it. A folder renamed
    away and replaced by a symlink between requests (or a file swapped for a link) makes them refuse - changing nothing
    and keeping the history - instead of writing through the link."""
    def setUp(self):
        super().setUp()
        self.outside = os.path.realpath(self.tmp + "-outside"); os.makedirs(self.outside)
        self.addCleanup(__import__("shutil").rmtree, self.outside, ignore_errors=True)

    def swap_for_link(self, path, target):
        """Rename the real folder/file `path` to `target` (outside the roots) and leave a symlink with its old name."""
        os.rename(path, target); link(target, path)

    def assertRefused(self, snapshot, msg="symlink"):
        n = len(server._undo)
        r = self.post("/api/undo", {}, ok=False)
        self.assertIn(msg, r["error"]); self.assertIn("Nothing was changed", r["error"])
        self.assertEqual((self.snapshot(self.tmp), len(server._undo)), (snapshot, n))  # files and history untouched

    def shipped_demo(self):
        p = self.project("demo", notes="# Demo\n\n- status: promoted\n- due: 2026-12-01\n")
        self.post("/api/project/status", {"path": "active/demo/NOTES.md", "action": "shipped"})
        return p

    def test_undo_refuses_when_the_project_folder_became_a_symlink_to_the_renamed_folder_inside_the_projects_root(self):
        p = self.shipped_demo(); ext = os.path.join(self.tmp, "external")
        self.swap_for_link(p, ext); before = self.snapshot(); outside_text = open(ext + "/NOTES.md").read()
        self.assertRefused(before)
        self.assertEqual(open(ext + "/NOTES.md").read(), outside_text); self.assertIn("shipped", outside_text)

    def test_undo_refuses_when_the_project_folder_became_a_symlink_to_a_folder_outside_the_roots(self):
        p = self.shipped_demo(); ext = os.path.join(self.outside, "demo")
        self.swap_for_link(p, ext); before = self.snapshot(self.outside); n = len(server._undo)
        r = self.post("/api/undo", {}, ok=False); self.assertIn("symlink", r["error"])
        self.assertEqual((self.snapshot(self.outside), len(server._undo)), (before, n)); self.assertIn("shipped", open(ext + "/NOTES.md").read())

    def test_undo_refuses_when_the_file_itself_was_swapped_for_a_link_with_identical_content(self):
        p = self.shipped_demo(); copy = os.path.join(self.outside, "NOTES.md")
        os.replace(p + "/NOTES.md", copy); link(copy, p + "/NOTES.md")
        before = self.snapshot(self.outside)
        r = self.post("/api/undo", {}, ok=False); self.assertIn("symlink", r["error"])
        self.assertEqual((self.snapshot(self.outside), len(server._undo)), (before, 1)); self.assertTrue(os.path.islink(p + "/NOTES.md"))

    def test_undo_of_a_move_refuses_a_destination_folder_replaced_by_a_link(self):
        self.note("2026-10-01-a.md", "A"); self.post("/api/action", {"file": "2026-10-01-a.md", "action": "kill", "note": "x"})
        self.swap_for_link(os.path.join(self.notes, "done"), os.path.join(self.outside, "done")); before = self.snapshot(self.outside)
        r = self.post("/api/undo", {}, ok=False); self.assertIn("symlink", r["error"])
        self.assertEqual((self.snapshot(self.outside), len(server._undo), os.path.exists(os.path.join(self.notes, "2026-10-01-a.md"))), (before, 1, False))

    def test_undo_of_a_move_refuses_a_dangling_link_where_the_source_was(self):
        a = self.note("2026-10-01-a.md", "A"); self.post("/api/action", {"file": "2026-10-01-a.md", "action": "kill", "note": "x"})
        link(os.path.join(self.outside, "nowhere"), a); before = self.snapshot()
        self.assertRefused(before)
        self.assertTrue(os.path.islink(a)); self.assertEqual(os.listdir(self.outside), [])

    def test_undo_of_a_promotion_refuses_when_the_new_projects_folder_became_a_link(self):
        self.note("2026-10-01-a.md", "A")
        self.post("/api/action", {"file": "2026-10-01-a.md", "action": "promote", "dest": "active", "note": "Ships.", "due_days": 3})
        self.swap_for_link(os.path.join(self.tmp, "active"), os.path.join(self.outside, "active")); before = self.snapshot(self.outside)
        r = self.post("/api/undo", {}, ok=False); self.assertIn("symlink", r["error"])
        self.assertEqual((self.snapshot(self.outside), len(server._undo)), (before, 1))

    def test_a_refused_undo_works_again_once_the_folder_is_put_right(self):
        p = self.shipped_demo(); ext = os.path.join(self.outside, "demo"); self.swap_for_link(p, ext)
        self.post("/api/undo", {}, ok=False)
        os.unlink(p); os.rename(ext, p)
        self.post("/api/undo", {}); self.assertIn("- status: promoted", open(p + "/NOTES.md").read()); self.assertEqual(server._undo, [])

    def test_undo_of_the_newest_change_checks_every_step_before_changing_any(self):
        """Two steps in two folders; only the OLDER step's folder is a link. Nothing may change, not even the newer step."""
        d1, d2 = self.project("one"), self.project("two")
        for d in (d1, d2): open(d + "/f.txt", "w").write("v0")
        with server.mutation("two files") as tx: tx.write(d1 + "/f.txt", "v1"); tx.write(d2 + "/f.txt", "v1")
        self.swap_for_link(d1, os.path.join(self.outside, "one")); before = self.snapshot()
        self.assertRefused(before)
        self.assertEqual(open(d2 + "/f.txt").read(), "v1"); self.assertEqual(open(self.outside + "/one/f.txt").read(), "v1")

    def test_rollback_does_not_write_through_a_link_and_leaves_an_unfinished_entry_that_finishes_later(self):
        d1 = self.project("one"); open(d1 + "/f.txt", "w").write("v0"); ext = os.path.join(self.outside, "one")
        with self.assertRaises(server.MutationFailed):
            with server.mutation("edit then fail") as tx:
                tx.write(d1 + "/f.txt", "v1")
                self.swap_for_link(d1, ext)  # the folder is swapped for a link, then a later step fails
                raise OSError(5, "simulated disk error")
        self.assertEqual(open(ext + "/f.txt").read(), "v1")  # not rolled back through the link
        self.assertEqual([os.listdir(ext), len(server._undo), "unfinished" in server._undo[0]["label"]], [["f.txt"], 1, True])
        os.unlink(d1); os.rename(ext, d1)
        self.post("/api/undo", {}); self.assertEqual((open(d1 + "/f.txt").read(), server._undo), ("v0", []))

    def test_rollback_does_not_move_back_through_a_link(self):
        a = self.note("2026-10-01-a.md", "A"); done = os.path.join(self.notes, "done"); ext = os.path.join(self.outside, "done")
        with self.assertRaises(server.MutationFailed):
            with server.mutation("move then fail") as tx:
                tx.ensure_dir(done); tx.move(a, done + "/2026-10-01-a.md")
                self.swap_for_link(done, ext)
                raise OSError(5, "simulated")
        self.assertEqual(os.listdir(ext), ["2026-10-01-a.md"]); self.assertFalse(os.path.exists(a))

    def test_redo_after_a_half_failed_undo_does_not_write_through_a_link(self):
        d1, d2 = self.project("one"), self.project("two")
        for d in (d1, d2): open(d + "/f.txt", "w").write("v0")
        with server.mutation("two files") as tx: tx.write(d1 + "/f.txt", "v1"); tx.write(d2 + "/f.txt", "v1")
        decoy = os.path.join(self.outside, "two"); os.makedirs(decoy); open(decoy + "/f.txt", "w").write("decoy")
        real = server._apply_inverse; calls = []
        def inverse(op):
            calls.append(op)
            if len(calls) == 2:  # d2 was put back; now d2 is swapped for a link and the next step fails
                os.rename(d2, d2 + ".real"); link(decoy, d2); raise OSError(5, "simulated")
            return real(op)
        with mock.patch.object(server, "_apply_inverse", inverse):
            r = self.post("/api/undo", {}, ok=False)
        self.assertIn("could not be put back", r["error"])
        self.assertEqual(open(decoy + "/f.txt").read(), "decoy"); self.assertEqual(os.listdir(decoy), ["f.txt"])
        self.assertEqual(open(d2 + ".real/f.txt").read(), "v0")


if __name__ == "__main__":
    unittest.main()
