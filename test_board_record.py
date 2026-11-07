#!/usr/bin/env python3
"""2.15: a project's board data lives in a record in RawNotes (RawNotes/projects/<kind>/<folder>.md), never in the
project's own NOTES.md, which its repo tracks and could publish. Spec and tickets: .scratch/private-board-data/.

Every test runs on a temp notes folder and projects root (testkit): never the real RawNotes, config or GitHub."""
import json
import os
import shutil
import subprocess
import unittest
from unittest import mock

import server
from notemeta import field
from test_cli import CliBase
from testkit import Fixture

LEGACY = ("# Demo\n\n- date: 2026-10-01\n- status: promoted\n- type: research\n- promoted: 2026-10-01\n- due: 2026-12-01\n"
          "- extensions: 1\n- github: private\n\n## What's the question / problem?\n\nCan it ship?\n\n## Notes\n\nMy own notes, edited since.\n\n"
          "## Plan\n\n- promoted from RawNotes on 2026-10-01, due 2026-11-24\n- definition of done: It ships.\n- first step: draw it\n"
          "- pushed 2026-11-24 → 2026-12-01 (2026-10-20): parts late\n\n## Rounds\n\n- 2026-09-01 → shipped 2026-09-20. Done means: v0\n")
CAPTURE = ("# Demo\n\n- date: 2026-10-01\n- status: promoted\n- type: research\n\n## What's the question / problem?\n\nCan it ship?\n\n"
           "## Notes\n\nMy own notes.\n\n## Outcome\n\n**Promoted 2026-10-01** -> `../active/demo/NOTES.md`\n")


def git(path, *args):
    return subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, check=True).stdout


class SplitLegacy(unittest.TestCase):
    """The split by audience, as a pure function."""
    def test_board_fields_sections_and_plan_lines_move_and_the_rest_stays(self):
        notes, fields, sections, moved = server.split_legacy(LEGACY)
        self.assertEqual(notes, "# Demo\n\n## What's the question / problem?\n\nCan it ship?\n\n## Notes\n\nMy own notes, edited since.\n\n"
                                "## Plan\n\n- definition of done: It ships.\n- first step: draw it\n")
        self.assertEqual(fields, [("date", "2026-10-01"), ("status", "promoted"), ("type", "research"), ("promoted", "2026-10-01"),
                                  ("due", "2026-12-01"), ("extensions", "1"), ("github", "private")])
        self.assertEqual(sections, {"Rounds": "- 2026-09-01 → shipped 2026-09-20. Done means: v0",
                                    "Extensions": "- pushed 2026-11-24 → 2026-12-01 (2026-10-20): parts late"})
        self.assertIn("## Rounds", moved); self.assertIn("the promoted line (## Plan)", moved)

    def test_with_the_capture_only_the_unedited_copy_of_it_goes(self):
        notes = server.split_legacy(LEGACY, CAPTURE)[0]
        self.assertNotIn("Can it ship?", notes)  # identical to the original: Promote's copy
        self.assertIn("My own notes, edited since.", notes)  # edited since: the user's now, stays

    def test_a_hand_made_notes_md_keeps_its_capture_like_header(self):
        """date/type are the capture's header only in a promoted copy: a hand-made NOTES.md keeps them."""
        notes, fields, _, _ = server.split_legacy("# Mine\n\n- date: 2026-01-01\n- status: building\n\nText.\n")
        self.assertEqual((notes, fields), ("# Mine\n\n- date: 2026-01-01\n\nText.\n", [("status", "building")]))

    def test_has_legacy(self):
        for text, want in ((LEGACY, True), ("# P\n\n## Plan\n\n- definition of done: x\n", False), ("# P\n\nNo header.\n", False),
                           ("# P\n\n## Moved\n\n- x\n", True), ("# P\n\n## Plan\n\n- pushed a → b (2026-10-01): why\n", True)):
            self.assertEqual(server.has_legacy(text), want, text)

    def test_record_text_and_capture_link_round_trip(self):
        rec = server.record_text("T", [("status", "promoted")], {"Outcome": "x", "Capture": server.capture_line("T", "done/2026-10-01-a b.md")})
        self.assertEqual(rec, "# T\n\n- status: promoted\n\n## Capture\n\n- [T](<../../done/2026-10-01-a b.md>)\n\n## Outcome\n\nx\n")
        self.assertEqual(server.capture_of(rec), "done/2026-10-01-a b.md")


class Base(Fixture):
    def promote(self, name="a", dest="active", title="Note A", **extra):
        self.note(f"2026-10-01-{name}.md", title, question="Can it ship?", body="Private first thoughts.")
        body = {"file": f"2026-10-01-{name}.md", "action": "promote", "dest": dest, "note": "It runs on the phone.", **extra}
        if dest != "experiments": body.setdefault("due_days", 5)
        self.post("/api/action", body); server._undo.clear()
        return os.path.join(self.tmp, dest, name)

    def tree(self, proj):
        """Every file under the project (its .git included), byte for byte."""
        return self.snapshot(proj)

    def clean(self, proj): self.assertEqual(git(proj, "status", "--porcelain", "--untracked-files=all"), "")


class BoardActionsLeaveTheProjectAlone(Base):
    """Story 4: Mark shipped, Kill, +7 days, Next round and close-out never change the project's working tree (Next round
    writes only the new done means, which is the plan)."""
    def test_extend_writes_only_the_record_and_undo_restores_it(self):
        proj = self.promote(); before = self.tree(proj); rec = self.record("active", "a")
        self.post("/api/project/status", {"path": "active/a/NOTES.md", "action": "extend", "days": 7, "note": "Waiting on the parts."})
        self.assertEqual(self.tree(proj), before); self.clean(proj)
        self.assertEqual(field(self.record("active", "a"), "extensions"), "1")
        [f] = self.get("/api/notes")["inflight"]; self.assertEqual((f["extensions"], f["path"]), (1, "active/a/NOTES.md"))
        self.post("/api/undo", {}); self.assertEqual(self.record("active", "a"), rec)

    def test_close_out_and_kill_move_the_record_with_the_folder(self):
        for outcome, dest in (("archive", "archive/a"), ("maintained", "maintained/a"), (None, "archive/a_abandoned")):
            with self.subTest(outcome):
                proj = self.promote(); before = self.tree(proj); rec = self.record("active", "a")
                body = {"name": "a", "kind": "active", "outcome": outcome} if outcome else {"name": "a", "kind": "active", "action": "kill", "note": "Not needed."}
                r = self.post("/api/project/close", body); self.assertEqual(r["moved_to"], dest)
                moved = os.path.join(self.tmp, dest)
                self.assertEqual({k.replace(dest, "active/a", 1): v for k, v in self.tree(moved).items()}, before); self.clean(moved)
                self.assertIsNone(self.record("active", "a"))
                kind, name = dest.split("/"); text = self.record(kind, name)
                self.assertEqual(field(text, "status"), "dead" if not outcome else "shipped"); self.assertIn("## Moved", text)
                self.assertIn("## Capture", text)  # the link to the original note goes along
                self.post("/api/undo", {})
                self.assertEqual((self.record("active", "a"), self.record(kind, name)), (rec, None))
                self.assertEqual(self.tree(proj), before)
                # the next outcome starts from a fresh promote
                server._undo.clear(); shutil.rmtree(proj); os.remove(os.path.join(self.notes, "projects", "active", "a.md"))
                os.remove(os.path.join(self.notes, "done", "2026-10-01-a.md"))

    def test_next_round_writes_only_done_means_into_the_project(self):
        proj = self.promote(); before = self.tree(proj)
        self.post("/api/project/close", {"name": "a", "kind": "active", "outcome": "next", "done": "Round two.", "due_days": 9})
        after = self.tree(proj)
        self.assertEqual({k for k in after if after[k] != before.get(k)}, {"active/a/NOTES.md"})
        self.assertIn("- definition of done: Round two.", open(os.path.join(proj, "NOTES.md")).read())
        self.assertIn("## Rounds", self.record("active", "a")); self.assertNotIn("Rounds", open(os.path.join(proj, "NOTES.md")).read())
        self.post("/api/undo", {}); self.assertEqual(self.tree(proj), before)

    def test_a_move_refuses_when_the_records_name_is_taken(self):
        """A board record left behind under the target's name (its folder deleted by hand) is never overwritten."""
        self.promote(); os.makedirs(os.path.join(self.notes, "projects", "archive"))
        with open(os.path.join(self.notes, "projects", "archive", "a.md"), "w") as fh: fh.write("# stale\n")
        self.assertEqual(self.post("/api/project/close", {"name": "a", "outcome": "archive"})["moved_to"], "archive/a-2")
        self.assertEqual(self.record("archive", "a"), "# stale\n")
        self.assertIn("## Moved", self.record("archive", "a-2"))


class Legacy(Base):
    """D5: no record means the legacy fields in NOTES.md are read exactly as before; the first write migrates."""
    def setUp(self):
        super().setUp()
        self.proj = self.project("demo", notes=LEGACY)
        os.makedirs(os.path.join(self.notes, "done"))
        with open(os.path.join(self.notes, "done", "2026-10-01-demo.md"), "w") as fh: fh.write(CAPTURE)

    def test_read_as_before(self):
        [f] = self.get("/api/notes")["inflight"]
        self.assertEqual((f["path"], f["due"], f["extensions"], f["status"], f["promoted"]), ("active/demo/NOTES.md", "2026-12-01", 1, "promoted", "2026-10-01"))
        [p] = self.get("/api/projects")["projects"]; self.assertEqual((p["status"], p["done_defined"]), ("promoted", True))
        self.assertEqual(server._promoted_days(self.proj), (server.today() - server.dt.date(2026, 10, 1)).days)

    def test_first_write_migrates_in_one_undoable_step(self):
        before = self.snapshot()
        self.post("/api/project/status", {"path": "active/demo/NOTES.md", "action": "extend", "note": "Waiting on the parts."})
        notes = open(os.path.join(self.proj, "NOTES.md")).read(); rec = self.record("active", "demo")
        self.assertEqual(notes, "# Demo\n\n## Notes\n\nMy own notes, edited since.\n\n## Plan\n\n- definition of done: It ships.\n- first step: draw it\n")
        self.assertEqual([field(rec, k) for k in ("status", "promoted", "extensions", "github", "date")], ["promoted", "2026-10-01", "2", "private", "2026-10-01"])
        self.assertEqual(server.capture_of(rec), "done/2026-10-01-demo.md")  # found from the original's Outcome line
        self.assertEqual(rec.split("## Extensions\n\n", 1)[1].count("- pushed "), 2)
        self.assertEqual(len(server._undo), 1)
        self.post("/api/undo", {}); self.assertEqual(self.snapshot(), before)

    def test_done_means_edit_migrates_too(self):
        before = self.snapshot()
        self.post("/api/done", {"name": "demo", "text": "It ships twice.", "expected": "It ships."})
        self.assertIn("- definition of done: It ships twice.", open(os.path.join(self.proj, "NOTES.md")).read())
        self.assertEqual(field(open(os.path.join(self.proj, "NOTES.md")).read(), "status"), "")
        self.assertEqual(field(self.record("active", "demo"), "due"), "2026-12-01")
        self.post("/api/undo", {}); self.assertEqual(self.snapshot(), before)

    def test_a_stale_done_means_edit_migrates_nothing(self):
        before = self.snapshot()
        self.post("/api/done", {"name": "demo", "text": "Mine", "expected": "Something else"}, ok=False)
        self.assertEqual((self.snapshot(), server._undo), (before, []))


class BulkMigration(Base):
    def setUp(self):
        super().setUp()
        self.demo = self.project("demo", notes=LEGACY)
        os.makedirs(os.path.join(self.notes, "done"))
        with open(os.path.join(self.notes, "done", "2026-10-01-demo.md"), "w") as fh: fh.write(CAPTURE)
        self.old = self.project("old", "maintained", "# Old\n\n- status: shipped\n- closed: 2026-09-01\n\n## Moved\n\n- 2026-09-01: moved from `active/old/` to `maintained/old/`.\n")
        self.plain = self.project("plain", notes="# Plain\n\n## Plan\n\n- definition of done: x\n")  # nothing to move
        self.kit = self.project("kit", "templates", "# Kit\n\n- status: maintained\n")  # hand-made, off the board: left alone
        self.fresh = self.promote("fresh")  # already has a record

    def test_pulse_lists_them_and_one_step_moves_them_all(self):
        self.assertEqual(self.get("/api/stats")["legacy_board"], ["active/demo", "maintained/old"])
        before = self.snapshot()
        r = self.post("/api/migrate-board-data", {})
        self.assertEqual([m["path"] for m in r["migrated"]], ["active/demo", "maintained/old"])
        self.assertIn("git history", r["msg"]); self.assertIn("commit each trimmed NOTES.md yourself", r["msg"])
        self.assertEqual((r["migrated"][0]["repo"], r["migrated"][0]["public"]), ("", False))
        self.assertEqual(open(os.path.join(self.old, "NOTES.md")).read(), "# Old\n")
        self.assertIn("moved from `active/old/`", self.record("maintained", "old"))
        self.assertNotIn("Can it ship?", open(os.path.join(self.demo, "NOTES.md")).read())
        self.assertEqual(open(os.path.join(self.kit, "NOTES.md")).read(), "# Kit\n\n- status: maintained\n")
        self.assertEqual(self.get("/api/stats")["legacy_board"], [])
        self.assertEqual(self.post("/api/migrate-board-data", {})["migrated"], [])  # again: nothing left, no undo step
        self.assertEqual(server._undo[-1]["label"], "move board data out of 2 projects")
        self.post("/api/undo", {}); self.assertEqual(self.snapshot(), before)

    def test_the_report_names_public_repos(self):
        subprocess.run(["git", "init", "-q", "-b", "main", self.demo], check=True)
        subprocess.run(["git", "-C", self.demo, "remote", "add", "origin", "https://github.com/me/demo.git"], check=True)
        with mock.patch.object(server, "repo_public", lambda repo: repo == "me/demo"):
            r = self.post("/api/migrate-board-data", {})
        self.assertEqual(r["public"], ["me/demo"]); self.assertIn("Public on GitHub", r["msg"]); self.assertIn("me/demo", r["msg"])

    def test_repo_public_never_guesses(self):
        p = mock.patch.object(server, "github_status", return_value={"state": "signed_in"}); p.start(); self.addCleanup(p.stop)
        with mock.patch.object(server, "gh_rest", side_effect=RuntimeError("offline")): self.assertIsNone(server.repo_public("me/x"))
        with mock.patch.object(server, "gh_rest", return_value={"private": False}): self.assertTrue(server.repo_public("me/x"))
        with mock.patch.object(server, "gh_rest", return_value={}): self.assertIsNone(server.repo_public("me/x"))
        with mock.patch.object(server, "github_status", return_value={"state": "signed_out"}), \
             mock.patch.object(server, "gh_rest", side_effect=AssertionError("asked GitHub while signed out")):
            self.assertIsNone(server.repo_public("me/x"))


class Brief(Base):
    def test_brief_prints_the_original_and_the_plan_with_no_path(self):
        self.promote()
        b = self.get("/api/brief?name=a")
        self.assertEqual((b["kind"], b["title"], b["status"], b["done"]), ("active", "Note A", "promoted", "It runs on the phone."))
        self.assertIn("Private first thoughts.", b["capture"]); self.assertIn("- definition of done: It runs on the phone.", b["plan"])
        self.assertNotIn(self.tmp, json.dumps(b))

    def test_brief_refuses_unknown_and_ambiguous_names(self):
        self.promote(); self.promote("a", dest="experiments")
        self.assertIn("say which", self.call("/api/brief?name=a")[1]["error"])
        self.assertEqual(self.get("/api/brief?name=a&kind=experiments")["kind"], "experiments")
        for bad in ("nope", "../a", ".hidden", ""): self.assertEqual(self.call(f"/api/brief?name={bad}")[0], 400, bad)

    def test_a_legacy_project_finds_its_original_through_done(self):
        self.project("demo", notes=LEGACY); os.makedirs(os.path.join(self.notes, "done"))
        with open(os.path.join(self.notes, "done", "2026-10-01-demo.md"), "w") as fh: fh.write(CAPTURE)
        self.assertIn("My own notes.", self.get("/api/brief?name=demo")["capture"])

    def test_on_board_by_record_alone(self):
        """D4: in learning/ and templates/, a record puts a folder on the board even with no NOTES.md at all."""
        study = self.project("study", "learning")
        self.assertEqual(self.get("/api/projects")["projects"], [])
        os.makedirs(os.path.join(self.notes, "projects", "learning"))
        with open(os.path.join(self.notes, "projects", "learning", "study.md"), "w") as fh: fh.write("# Study\n\n- status: promoted\n- due: 2099-01-01\n")
        [p] = self.get("/api/projects")["projects"]; self.assertEqual((p["path"], p["status"]), ("learning/study", "promoted"))
        [f] = self.get("/api/notes")["inflight"]; self.assertEqual((f["path"], f["due"]), ("learning/study/NOTES.md", "2099-01-01"))
        self.assertFalse(os.path.exists(os.path.join(study, "NOTES.md")))


class Cli(CliBase):
    def test_brief_and_migrate_board_data(self):
        self.note("2026-10-01-a.md", "Note A", question="Can it ship?")
        self.post("/api/action", {"file": "2026-10-01-a.md", "action": "promote", "dest": "active", "note": "It ships.", "due_days": 5})
        out = self.ok("brief", "a").stdout
        self.assertIn("# Note A (active/a)", out); self.assertIn("## The original note", out); self.assertIn("Can it ship?", out)
        self.assertEqual(json.loads(self.ok("brief", "a", "--json").stdout)["kind"], "active")
        self.assertIn("no project", self.rt("brief", "nope").stderr)
        self.project("demo", notes=LEGACY)
        out = self.ok("migrate-board-data").stdout
        self.assertIn("- active/demo: date, status", out); self.assertIn("git history", out)
        self.assertIn("nothing to move", self.ok("migrate-board-data").stdout)


if __name__ == "__main__":
    unittest.main()
