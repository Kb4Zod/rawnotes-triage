#!/usr/bin/env python3
"""v2.05: note headers (only the header carries metadata) and the reader's full question."""
import os
import unittest

import notemeta
import server
from notemeta import field, set_field
from testkit import Fixture


class Header(unittest.TestCase):
    def test_blank_status_is_filled_in_place_not_across_lines(self):
        got = set_field("# X\n- status:\n- type: research\n", "status", "cooking")
        self.assertEqual(got, "# X\n- status: cooking\n- type: research\n")
        self.assertEqual(field(got, "type"), "research")

    def test_template_comment_survives_and_blank_with_comment_is_filled(self):
        self.assertEqual(set_field("# T\n- status: open        <!-- a | b -->\n", "status", "dead"), "# T\n- status: dead        <!-- a | b -->\n")
        self.assertEqual(field("# T\n- status:        <!-- a | b -->\n", "status"), "")
        self.assertIn("dead", set_field("# T\n- status:        <!-- a | b -->\n", "status", "dead"))

    def test_missing_header_adds_one_under_the_title_and_touches_nothing_else(self):
        body = "# T\n\nSome prose.\nMore prose.\nEven more.\nAnd more.\n\n- status: quoted in the body\n"
        out = set_field(body, "status", "dead")
        self.assertEqual(field(body, "status"), "")  # not metadata: it is below prose
        self.assertEqual(out, "# T\n\n- status: dead\n\nSome prose.\nMore prose.\nEven more.\nAnd more.\n\n- status: quoted in the body\n")

    def test_no_title_and_empty_text(self):
        self.assertEqual(set_field("just words\n", "status", "dead"), "- status: dead\n\njust words\n")
        self.assertEqual(set_field("", "status", "dead"), "- status: dead\n\n")
        self.assertEqual(set_field("# T\n", "status", "dead"), "# T\n\n- status: dead\n")

    def test_new_key_goes_to_the_end_of_the_header(self):
        self.assertEqual(set_field("# T\n\n- date: 2026-10-01\n- status: open\n\n## Notes\n", "due", "2026-11-01"),
                         "# T\n\n- date: 2026-10-01\n- status: open\n- due: 2026-11-01\n\n## Notes\n")

    def test_body_metadata_is_never_read_or_edited(self):
        text = ("# Merged\n\n- date: 2026-10-05\n- status: cooking\n- type: research\n\n## What's the question / problem?\n\nQ\n\n## Notes\n\n"
                "## Source one (2026-10-01)\n\n- status: open\n- due: 2026-10-30\n\n> - status: quoted\n\n    - status: indented\n\n```\n- status: fenced\n```\n")
        self.assertEqual((field(text, "status"), field(text, "due")), ("cooking", ""))
        out = set_field(set_field(text, "status", "dead"), "due", "2027-01-01")
        self.assertEqual(out.count("- status: dead"), 1); self.assertEqual(out.count("- status: open"), 1)
        self.assertEqual(out.count("- status: quoted"), 1); self.assertIn("- due: 2026-10-30", out)
        self.assertLess(out.index("- due: 2027-01-01"), out.index("## What's"))

    def test_header_after_a_subtitle_paragraph_still_counts(self):  # real notes look like this (eight-rings handoff)
        t = "# The Eight Rings\n\n*A subtitle.*\n\n- date: 2026-10-17\n- due: 2026-10-25\n- status: promoted\n\n## Q\n"
        self.assertEqual((field(t, "due"), field(t, "status")), ("2026-10-25", "promoted"))

    def test_heading_before_any_bullet_means_no_header(self):
        t = "# T\n\n## Plan\n\n- due: 2026-10-25\n- status: x\n"
        self.assertEqual(field(t, "due"), ""); self.assertEqual(field(t, "status"), "")

    def test_other_words_are_not_header_keys(self):
        t = "# T\n- status: open\n- next step (2026-10-01): do it\n- definition of done: ship\n"
        self.assertEqual(field(t, "status"), "open")
        self.assertEqual(set_field(t, "due", "x").split("\n")[2], "- due: x")

    def test_duplicate_keys_use_the_first(self):
        t = "# T\n- status: open\n- status: dead\n"
        self.assertEqual(field(t, "status"), "open"); self.assertEqual(set_field(t, "status", "x"), "# T\n- status: x\n- status: dead\n")

    def test_crlf_helpers(self):
        raw = "# T\r\n- status: open\r\n"
        self.assertEqual(notemeta.eol_of(raw), "\r\n"); self.assertEqual(notemeta.eol_of("a\r\nb\n"), "\n"); self.assertEqual(notemeta.eol_of("a\rb\r\n"), "\n")
        self.assertEqual(notemeta.with_eol(set_field(notemeta.to_lf(raw), "status", "x"), "\r\n"), "# T\r\n- status: x\r\n")


class HeaderThroughTheApp(Fixture):
    def test_keep_on_a_blank_status_note_does_not_corrupt_it(self):
        path = os.path.join(self.notes, "2026-10-01-blank.md")
        with open(path, "w") as fh: fh.write("# X\n- status:\n- type: research\n")
        self.post("/api/action", {"file": "2026-10-01-blank.md", "action": "keep"})
        self.assertEqual(open(path).read(), "# X\n- status: cooking\n- type: research\n")

    def test_note_without_a_header_gets_one(self):
        path = os.path.join(self.notes, "2026-10-01-bare.md")
        with open(path, "w") as fh: fh.write("# Bare\n\nJust thoughts.\n")
        self.post("/api/action", {"file": "2026-10-01-bare.md", "action": "kill", "note": "no"})
        text = open(os.path.join(self.notes, "done", "2026-10-01-bare.md")).read()
        self.assertTrue(text.startswith("# Bare\n\n- status: dead\n\nJust thoughts.")); self.assertIn("**Killed", text)

    def test_merged_note_keeps_source_metadata_out_of_the_header(self):
        self.note("2026-10-01-bomb-a.md", "Bomb A", body="- status: stays in a\n- due: 2026-10-30")
        self.note("2026-10-02-bomb-b.md", "Bomb B", body="beta")
        r = self.post("/api/merge", {"files": ["2026-10-01-bomb-a.md", "2026-10-02-bomb-b.md"], "title": "Bomb"})
        merged = os.path.join(self.notes, r["file"])
        text = open(merged).read()
        self.assertEqual((field(text, "status"), field(text, "due")), ("cooking", ""))  # sources' lines are body text
        self.post("/api/action", {"file": r["file"], "action": "kill", "note": "x"})
        done = open(os.path.join(self.notes, "done", r["file"])).read()
        self.assertIn("- status: stays in a", done); self.assertIn("- due: 2026-10-30", done)
        self.assertEqual(field(done, "status"), "dead")

    def test_promote_keeps_a_subtitle_note_header_in_order(self):
        path = os.path.join(self.notes, "2026-10-01-sub.md")
        with open(path, "w") as fh: fh.write("# Sub\n\n*tagline*\n\n- date: 2026-10-01\n- status: open\n- type: research\n\n## Notes\n\nx\n")
        self.post("/api/action", {"file": "2026-10-01-sub.md", "action": "promote", "dest": "active", "note": "It ships.", "due_days": 5})
        text = open(os.path.join(self.tmp, "active", "sub", "NOTES.md")).read()
        self.assertEqual((field(text, "status"), field(text, "promoted") != "", field(text, "due") != ""), ("promoted", True, True))
        self.assertEqual(text.count("- status:"), 1)


class Question(Fixture):
    LONG = ("First paragraph. " * 30).strip()  # past the 280 chars the list row shows

    def test_list_row_keeps_a_short_summary_but_the_reader_gets_everything(self):
        q = f"{self.LONG}\n\nSecond paragraph with `code`.\n\n- a list item\n- another\n\n### sub heading\n\nLast paragraph."
        self.note("2026-10-01-long.md", "Long", question=q)
        row = self.get("/api/notes")["live"][0]
        self.assertEqual(row["question"], self.LONG[:280]); self.assertLessEqual(len(row["question"]), 280)
        j = self.get("/api/note?f=2026-10-01-long.md")
        self.assertEqual(j["question"], q.strip())
        for part in ("Second paragraph", "another", "sub heading", "Last paragraph.", self.LONG[-20:]): self.assertIn(part, j["question"])
        self.assertNotIn("Last paragraph", j["body"]); self.assertNotIn("What's the question", j["body"])
        self.assertIn("Detail", j["body"])  # the rest of the note is still shown
        self.assertNotIn("- status:", j["body"]); self.assertFalse(j["body"].startswith("# "))

    def test_note_without_a_question_section(self):
        path = os.path.join(self.notes, "2026-10-01-q.md")
        with open(path, "w") as fh: fh.write("# Q\n\n- status: open\n\njust text\n")
        j = self.get("/api/note?f=2026-10-01-q.md")
        self.assertEqual((j["question"], j["body"]), ("", "just text"))
        self.assertEqual(self.get("/api/notes")["live"][0]["question"], "")

    def test_archive_notes_show_the_whole_question_too(self):
        self.note("2026-10-01-old.md", "Old", sub="done", question="One.\n\nTwo.")
        self.assertEqual(self.get("/api/note?f=done/2026-10-01-old.md")["question"], "One.\n\nTwo.")


if __name__ == "__main__":
    unittest.main()
