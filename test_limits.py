"""Inbox limits (flow-gaps 04): stale clocks, the 30-day limit, the Keep counter, and what Keep refuses."""
import datetime as dt
import json
import os
import unittest
from unittest import mock

import server
from testkit import Fixture

TODAY = dt.date(2026, 11, 1)


def ago(n): return (TODAY - dt.timedelta(days=n)).isoformat()


class InboxLimits(Fixture):
    relax_limits = False  # the real defaults
    def setUp(self):
        super().setUp()
        p = mock.patch.object(server, "today", lambda: TODAY); p.start(); self.addCleanup(p.stop)

    def make(self, days, status="open", extra="", slug="n"):
        name = f"{ago(days)}-{slug}.md"
        with open(os.path.join(self.notes, name), "w", encoding="utf-8") as fh:
            fh.write(f"# {slug}\n\n- date: {ago(days)}\n- status: {status}\n- type: research\n{extra}\n## What's the question / problem?\n\nQ?\n\n## Notes\n")
        return name

    def row(self, name): return next(n for n in self.get("/api/notes")["live"] if n["file"] == name)

    def text(self, name): return open(os.path.join(self.notes, name), encoding="utf-8").read()

    def test_open_stale_after_14_days_unless_kept_recently(self):
        self.assertTrue(self.row(self.make(15))["stale"])
        self.assertFalse(self.row(self.make(14, slug="edge"))["stale"])
        self.assertFalse(self.row(self.make(20, extra=f"- kept: {ago(3)}\n- keeps: 1\n", slug="kept"))["stale"])

    def test_cooking_stale_7_days_after_last_keep_or_capture(self):
        self.assertTrue(self.row(self.make(20, "cooking", f"- kept: {ago(8)}\n- keeps: 1\n", "a"))["stale"])
        self.assertFalse(self.row(self.make(20, "cooking", f"- kept: {ago(6)}\n- keeps: 1\n", "b"))["stale"])
        self.assertTrue(self.row(self.make(8, "cooking", slug="c"))["stale"])
        self.assertFalse(self.row(self.make(7, "cooking", slug="d"))["stale"])

    def test_row_fields(self):
        r = self.row(self.make(10, extra="- keeps: 2\n"))
        self.assertEqual((r["expired"], r["expires_in"], r["keeps"]), (False, 20, 2))
        self.assertFalse(any(k.startswith("_") for k in r))

    def test_over_30_days_is_expired_and_keep_is_refused_and_changes_nothing(self):
        name = self.make(31); self.assertFalse(self.row(self.make(30, slug="edge"))["expired"])
        before = self.snapshot(); self.assertTrue(self.row(name)["expired"])
        r = self.post("/api/action", {"file": name, "action": "keep"}, ok=False)
        self.assertIn("30-day limit: promote, file, merge or kill it", r["error"])
        self.assertEqual((self.snapshot(), server._undo), (before, []))

    def test_three_keeps_is_expired(self):
        name = self.make(5, "cooking", f"- kept: {ago(1)}\n- keeps: 3\n")
        self.assertTrue(self.row(name)["expired"])
        self.post("/api/action", {"file": name, "action": "keep"}, ok=False)
        self.assertNotIn("- keeps: 4", self.text(name))

    def test_keep_writes_date_and_counter(self):
        name = self.make(2)
        self.post("/api/action", {"file": name, "action": "keep"})
        self.assertIn(f"- kept: {TODAY}", self.text(name)); self.assertIn("- keeps: 1", self.text(name))
        self.post("/api/action", {"file": name, "action": "keep"})
        self.assertIn("- keeps: 2", self.text(name)); self.assertEqual(self.text(name).count("- kept:"), 1)

    def test_restore_starts_the_clock_over(self):
        name = self.make(60, "dead", slug="old")
        os.makedirs(os.path.join(self.notes, "done"))
        os.rename(os.path.join(self.notes, name), os.path.join(self.notes, "done", name))
        self.post("/api/restore", {"file": "done/" + name})
        self.assertIn(f"- restored: {TODAY}", self.text(name))
        r = self.row(name); self.assertFalse(r["expired"]); self.assertEqual(r["expires_in"], 30); self.assertFalse(r["stale"])
        self.post("/api/action", {"file": name, "action": "keep"})

    def test_batch_keep_with_one_expired_note_refuses_all_and_names_it(self):
        fresh, old = self.make(2, slug="fresh"), self.make(40, slug="oldnote"); before = self.snapshot()
        r = self.post("/api/batch", {"files": [fresh, old], "action": "keep"}, ok=False)
        self.assertIn("oldnote", r["error"]); self.assertNotIn("“fresh”", r["error"])
        self.assertEqual((self.snapshot(), server._undo), (before, []))

    def test_other_decisions_still_work_on_expired_notes(self):
        for i, act in enumerate(("kill", "reference")):
            name = self.make(40 + i, slug=f"x{i}")
            self.post("/api/action", {"file": name, "action": act, "note": "why"})
        name = self.make(45, slug="prom")
        self.post("/api/action", {"file": name, "action": "promote", "dest": "experiments", "note": "Done means this."})
        a, b = self.make(41, slug="m1"), self.make(42, slug="m2")
        self.post("/api/merge", {"files": [a, b], "title": "Merged"})

    def test_thresholds_come_from_config(self):
        os.makedirs(server.CONFIG_DIR, exist_ok=True)
        with open(os.path.join(server.CONFIG_DIR, "config.json"), "w") as fh:
            json.dump({"stale_open_days": 3, "inbox_limit_days": 10, "max_keeps": 1, "stale_cooking_days": "bad"}, fh)
        self.assertEqual(server.limits(), {"stale_open_days": 3, "stale_cooking_days": 7, "inbox_limit_days": 10, "max_keeps": 1})
        self.assertTrue(self.row(self.make(4, slug="s"))["stale"])
        self.assertTrue(self.row(self.make(11, slug="e"))["expired"])
        self.assertEqual(self.get("/api/notes")["limits"]["inbox_limit_days"], 10)

    def test_one_config_reader_falls_back_to_every_default(self):
        os.makedirs(server.CONFIG_DIR, exist_ok=True)
        for junk in ("[1, 2]", "not json", '{"max_extensions": true, "closed_window_days": -1, "autocommit": "no", "max_keeps": 0, "noreply_suffix": "x"}'):
            with open(server.config_path(), "w") as fh: fh.write(junk)
            self.assertEqual(server.limits(), server.LIMIT_DEFAULTS, junk)
            self.assertEqual(server.config_int("max_extensions", server.MAX_EXTENSIONS), server.MAX_EXTENSIONS)
            self.assertEqual(server.config_int("closed_window_days", server.CLOSED_WINDOW_DAYS), server.CLOSED_WINDOW_DAYS)
            self.assertTrue(server.saved_autocommit()); self.assertEqual(server.noreply_suffix(), server.NOREPLY_SUFFIX)
            self.assertIsNone(server.saved_notes_dir())
        server.save_config(autocommit=False, max_extensions=0, notes=self.notes)  # save_config merges into the same file
        self.assertFalse(server.saved_autocommit()); self.assertEqual(server.config_int("max_extensions", 2), 0)
        self.assertEqual(server.saved_notes_dir(), self.notes)

    def test_summary_line_and_pulse_count(self):
        self.make(1, slug="a"); self.make(40, "cooking", slug="b"); self.make(20, slug="c")
        self.assertEqual(server.inbox_summary(), "2 open, 1 cooking (2 stale, 1 expired)")
        s = self.get("/api/stats"); self.assertEqual((s["expired"], s["stale"]), (1, 2))

    def test_limit_header_lines_are_not_shown_in_the_reader_body(self):
        import notemeta
        t = "# T\n\n- date: 2026-10-01\n- status: cooking\n- kept: 2026-10-02\n- keeps: 1\n- restored: 2026-10-03\n\nBody\n"
        self.assertEqual(notemeta.note_body(t), "Body")


class ConfigReadOncePerListing(Fixture):
    def test_a_listing_reads_config_json_once_not_once_per_note(self):
        for i in range(12): self.note(f"2026-10-{i + 10:02d}-n{i}.md", f"N{i}")
        for i in range(4):
            self.project(f"p{i}", notes=f"# p{i}\n\n- status: shipped\n- promoted: 2026-10-01\n- due: 2026-12-01\n- closed: 2026-10-20\n")
        real = server.read_config
        with mock.patch.object(server, "read_config", side_effect=real) as rc:
            n = self.get("/api/notes")
        self.assertEqual((len(n["live"]), len(n["inflight"])), (12, 4))
        self.assertLessEqual(rc.call_count, 1, rc.call_count)


if __name__ == "__main__": unittest.main()
