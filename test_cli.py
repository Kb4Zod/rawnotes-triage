#!/usr/bin/env python3
"""v2.13: bin/rawtriage decides through the server; GET /api/check and POST /api/new.

Every test runs against a disposable notes folder: either the in-process fixture server (testkit) or a server the CLI
starts itself from a temp copy of the code, on a free port. The real notes, ~/.local/bin and the service are never touched."""
import datetime as dt
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

import server
from testkit import Fixture, isolate_git

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, "bin", "rawtriage")
HOOK = os.path.join(HERE, "testdata", "check-frontmatter.sh")
CODE = ("server.py", "safefs.py", "notemeta.py", "depcheck.py", "index.html", "bin/rawtriage", "starter/_template.md")


def free_port():
    with socket.socket() as s: s.bind(("127.0.0.1", 0)); return s.getsockname()[1]


class CliBase(Fixture):
    def rt(self, *args, env=None, cli=CLI):
        e = {**os.environ, "RAWTRIAGE_PORT": str(self.port), **(env or {})}
        return subprocess.run([sys.executable, cli, *args], capture_output=True, text=True, env=e, timeout=60, cwd=self.tmp)

    def ok(self, *args, **kw):
        r = self.rt(*args, **kw); self.assertEqual(r.returncode, 0, r.stderr); return r

    def refused(self, *args, code=1):
        before = self.snapshot(); r = self.rt(*args)
        self.assertEqual(r.returncode, code, r.stdout + r.stderr); self.assertEqual(self.snapshot(), before)
        return r.stderr


class Reading(CliBase):
    def test_list_has_the_old_columns_and_the_stale_flag(self):
        t = server.today().isoformat()
        self.note("2020-01-02-old-one.md", "Old one"); self.note(f"{t}-newer.md", "Newer", status="cooking")
        r = self.ok("-l").stdout.splitlines()
        self.assertEqual(r[0].split(), ["STATUS", "TYPE", "AGE", "DATE", "TITLE"])
        self.assertRegex(r[1], r"^open\s+research\s+\d+d 2020-01-02 Old one  \(2020-01-02-old-one\.md\)  \[stale\]$")
        self.assertRegex(r[2], rf"^cooking\s+research\s+0d {t} Newer  \({t}-newer\.md\)$")
        only = self.ok("-l", "cooking").stdout.splitlines(); self.assertEqual(len(only), 2); self.assertIn("Newer", only[1])

    def test_list_shows_the_real_expired_flag_and_the_summary(self):
        t = server.today()
        with mock.patch.dict(server.LIMIT_DEFAULTS, {"inbox_limit_days": 30, "max_keeps": 3}):  # the real defaults
            self.note((t - dt.timedelta(days=31)).isoformat() + "-old.md", "Old")
            self.note(t.isoformat() + "-fresh.md", "Fresh", status="cooking")
            rows = {n["file"][11:]: n for n in self.get("/api/notes")["live"]}
            self.assertTrue(rows["old.md"]["expired"]); self.assertFalse(rows["fresh.md"]["expired"])
            out = self.ok("-l").stdout
            self.assertRegex(out, r"Old  \(\S+-old\.md\)  \[stale\]  \[expired\]\n"); self.assertNotIn("expired", out.split("Fresh")[1])
            self.assertEqual(self.ok("-l", "--summary").stdout.strip(), "1 open, 1 cooking (1 stale, 1 expired)")
            self.assertEqual(self.ok("-l", "--summary").stdout.strip(), server.inbox_summary())
            self.assertEqual(json.loads(self.ok("-l", "--summary", "--json").stdout), {"summary": server.inbox_summary()})
            self.assertIn("30-day limit", self.refused("keep", "old"))  # Keep is refused once expired

    def test_list_json_is_the_api_data(self):
        self.note("2026-10-02-a.md", "A"); self.note("2026-10-03-b.md", "B", status="cooking")
        data = json.loads(self.ok("-l", "--json").stdout)
        self.assertEqual([n["file"] for n in data], [n["file"] for n in self.get("/api/notes")["live"]])
        self.assertEqual([n["file"] for n in json.loads(self.ok("-l", "cooking", "--json").stdout)], ["2026-10-03-b.md"])

    def test_empty_inbox(self):
        self.assertIn("(no notes)", self.ok("-l").stdout)

    def test_where(self):
        r = self.ok("where").stdout.splitlines()
        self.assertEqual(r, [f"notes: {self.notes}", f"projects: {self.tmp}"])
        self.assertEqual(json.loads(self.ok("where", "--json").stdout), {"notes_dir": self.notes, "projects_dir": self.tmp})


# One header per line of this table; the same files go through RawNotes' own hook and through /api/check.
HEADER_CASES = {
    "2026-10-01-good.md": "# Good\n\n- date: 2026-10-01\n- status: open\n- type: question\n\n## What's the question / problem?\n\nx\n",
    "2026-10-02-no-title.md": "- date: 2026-10-02\n- status: open\n- type: issue\n\n## What's the question / problem?\n",
    "2026-10-03-topic.md": "# <topic>\n\n- date: 2026-10-03\n- status: open\n- type: issue\n\n## What's the question / problem?\n",
    "2026-10-04-wrong-date.md": "# T\n\n- date: 2026-10-05\n- status: cooking\n- type: research\n\n## What's the question / problem?\n",
    "2026-10-05-bad-status.md": "# T\n\n- date: 2026-10-05\n- status: merged\n- type: research\n\n## What's the question / problem?\n",
    "2026-10-06-no-status-type.md": "# T\n\n- date: 2026-10-06\n\n## What's the question / problem?\n",
    "2026-10-07-bad-type.md": "# T\n\n- date: 2026-10-07\n- status: dead\n- type: idea\n\n## What's the question / problem?\n",
    "2026-10-08-no-section.md": "# T\n\n- date: 2026-10-08\n- status: promoted\n- type: question\n\n## Notes\n",
    "2026-10-09-no-date.md": "# T\n\n- status: open\n- type: question\n\n## What's the question / problem?\n",
    "Bad_Name.md": "# T\n\n- date: 2026-10-09\n- status: open\n- type: question\n\n## What's the question / problem?\n",
    "pasted-from-phone.md": "just some words\n",
    "2026-10-10-empty.md": "",
    "README.md": "nothing here\n", "CLAUDE.md": "x\n", "AGENTS.md": "x\n", "TRIAGE.md": "x\n", "_template.md": "# <topic>\n",
}


@unittest.skipUnless(shutil.which("bash"), "needs bash")
class HeaderCheck(CliBase):
    def hook_lines(self):
        hooks = os.path.join(self.notes, ".claude", "hooks"); os.makedirs(hooks)
        shutil.copy(HOOK, hooks)
        out = subprocess.run(["bash", os.path.join(hooks, "check-frontmatter.sh")], capture_output=True, text=True, timeout=30).stdout
        return sorted(l for l in out.splitlines() if l.startswith("- "))

    def test_same_verdicts_as_the_session_start_hook(self):
        for name, text in HEADER_CASES.items():
            with open(os.path.join(self.notes, name), "w", encoding="utf-8") as fh: fh.write(text)
        os.makedirs(os.path.join(self.notes, "done")); self.note("2020-01-01-archived-bad.md", status="merged", sub="done")
        want = self.hook_lines(); self.assertGreaterEqual(len(want), 9)
        data = self.get("/api/check")
        got = sorted(f"- {p['file']} — missing/invalid: {', '.join(p['issues'])}" for p in data["problems"])
        self.assertEqual(got, want)
        self.assertEqual(data["checked"], len(HEADER_CASES) - 5)  # the five names the hook never looks at
        out = self.ok("check").stdout
        for line in want: self.assertIn(line, out)
        self.assertIn(f"{len(want)} note(s) don't have a complete header", out)

    def test_clean_folder_says_so(self):
        self.note("2026-10-01-a.md")
        self.assertIn("All 1 notes have a complete header.", self.ok("check").stdout)
        self.assertEqual(json.loads(self.ok("check", "--json").stdout), {"checked": 1, "problems": []})


class NewNote(CliBase):
    def template(self):
        with open(os.path.join(self.notes, "_template.md"), "w", encoding="utf-8") as fh:
            fh.write(open(os.path.join(HERE, "starter", "_template.md"), encoding="utf-8").read())

    def test_creates_todays_note_from_the_template(self):
        self.template()
        r = self.ok("new", "dns-slow"); today = server.today().isoformat()
        path = os.path.join(self.notes, f"{today}-dns-slow.md")
        self.assertEqual(r.stdout.splitlines(), [f"Created {today}-dns-slow.md", path])
        with open(path, encoding="utf-8") as fh: text = fh.read()
        self.assertTrue(text.startswith("# dns slow\n")); self.assertIn(f"- date: {today}\n", text)
        self.assertNotIn("YYYY-MM-DD", text); self.assertNotIn("<topic>", text)
        self.assertEqual(self.get("/api/check")["problems"], [])

    def test_thought_goes_under_the_question(self):
        self.template()
        self.ok("new", "dns-slow", "why does dns", "take 5s?")
        with open(os.path.join(self.notes, f"{server.today().isoformat()}-dns-slow.md"), encoding="utf-8") as fh: text = fh.read()
        self.assertIn("## What's the question / problem?\n\nwhy does dns take 5s?\n\n## Notes", text)
        self.assertNotIn("<one or two", text)

    def test_falls_back_to_the_starter_template(self):
        self.ok("new", "no-template")
        with open(os.path.join(self.notes, f"{server.today().isoformat()}-no-template.md"), encoding="utf-8") as fh:
            self.assertTrue(fh.read().startswith("# no template\n"))

    def test_refuses_an_existing_name_and_a_bad_slug(self):
        self.ok("new", "again")
        self.assertIn("already exists", self.refused("new", "again"))
        for bad in ("Has Space", "x/y", "-lead", "a--b", "../up"): self.assertIn("kebab-case", self.refused("new", "--", bad))

    def test_is_undoable_and_shows_in_the_undo_list(self):
        before = self.snapshot(); self.ok("new", "oops")
        self.assertEqual(self.get("/api/stats")["undo"], [f"new note {server.today().isoformat()}-oops.md"])
        self.ok("undo"); self.assertEqual(self.snapshot(), before)


class Deciding(CliBase):
    def setUp(self):
        super().setUp()
        server.log = self.saved[2]  # these tests read the real log writer, pointed at the temp log
        self.note("2026-10-01-a.md", "Alpha"); self.note("2026-10-02-b.md", "Bravo"); self.note("2026-10-03-c.md", "Charlie")

    def inbox(self): return sorted(n["file"] for n in self.get("/api/notes")["live"])

    def test_keep(self):
        r = self.ok("keep", "2026-10-01-a.md", "--status", "open", "--next", "ask Sam")
        self.assertEqual(r.stdout.strip(), "Kept “Alpha” (open)")
        with open(os.path.join(self.notes, "2026-10-01-a.md")) as fh: self.assertIn("next step", fh.read())
        self.assertIn("Kept “Alpha” (cooking)", self.ok("keep", "2026-10-01-a.md").stdout)

    def test_kill_needs_a_reason(self):
        self.assertIn("--reason", self.refused("kill", "2026-10-01-a.md"))
        r = self.ok("kill", "2026-10-01-a.md", "--reason", "dup of b")
        self.assertEqual(r.stdout.strip(), "Killed “Alpha” → Archive")
        with open(os.path.join(self.notes, "done", "2026-10-01-a.md")) as fh: self.assertIn("dup of b", fh.read())

    def test_reference(self):
        r = self.ok("reference", "2026-10-01-a.md", "--why", "worth keeping")
        self.assertIn("Filed “Alpha” → reference/", r.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "reference", "a.md")))

    def test_reference_section_unindexed_and_index(self):
        readme = os.path.join(self.tmp, "reference", "README.md"); os.makedirs(os.path.dirname(readme))
        with open(readme, "w") as fh: fh.write("# reference\n\n## Index\n\n### Tools\n- [x](x.md): x\n")
        self.ok("reference", "2026-10-01-a.md", "--why", "worth keeping", "--section", "Tools", "--blurb", "the alpha one")
        self.ok("reference", "2026-10-02-b.md")  # no --section: filed, not indexed
        with open(readme) as fh: text = fh.read()
        self.assertIn("- [Alpha](a.md): the alpha one", text); self.assertNotIn("b.md", text)
        out = self.ok("reference", "--unindexed").stdout
        self.assertIn("1 reference note(s) aren't in the index", out); self.assertIn("- b.md  Bravo", out); self.assertIn("Sections: Tools", out)
        self.assertEqual([f["file"] for f in json.loads(self.ok("reference", "--unindexed", "--json").stdout)["files"]], ["b.md"])
        self.assertIn("Added “Bravo” to the index under Tools", self.ok("index", "b.md", "--section", "Tools").stdout)
        self.assertIn("0 reference note(s)", self.ok("reference", "--unindexed").stdout)
        self.assertEqual(self.rt("reference").returncode, 1)  # neither a note nor --unindexed

    def test_promote(self):
        r = self.ok("promote", "2026-10-01-a.md", "--dest", "active", "--done", "it ships", "--due-days", "5", "--slug", "alpha")
        self.assertIn("Promoted “Alpha” → active/alpha/NOTES.md", r.stdout)
        self.ok("promote", "2026-10-02-b.md", "--dest", "experiments", "--done", "we know")  # open-ended: no due needed
        self.ok("promote", "2026-10-03-c.md", "--dest", "learning", "--done", "learned", "--due", (server.today() + dt.timedelta(days=3)).isoformat())
        self.assertEqual(self.inbox(), [])

    def test_promote_without_done_or_due_changes_nothing(self):
        self.assertIn("Say what done means", self.refused("promote", "2026-10-01-a.md", "--dest", "active", "--due-days", "5"))
        self.assertIn("Say what done means", self.refused("promote", "2026-10-01-a.md", "--dest", "experiments"))
        self.assertIn("ship-by", self.refused("promote", "2026-10-01-a.md", "--dest", "active", "--done", "ships"))
        self.assertIn("ship-by", self.refused("promote", "2026-10-01-a.md", "--dest", "active", "--done", "ships", "--due-days", "99"))
        self.assertEqual(self.get("/api/stats")["undo"], [])

    def test_merge(self):
        r = self.ok("merge", "2026-10-01-a.md", "2026-10-02-b.md", "--title", "AB")
        self.assertIn("Merged 2 notes into “AB”", r.stdout); self.assertEqual(len(self.inbox()), 2)
        ab = [f for f in self.inbox() if f.endswith("-ab.md")][0]
        r = self.ok("merge", "c.md", ab, "--title", "Shipped", "--then", "promote", "--dest", "active", "--done", "ships", "--due-days", "4", "--slug", "sh")
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "active", "sh", "NOTES.md")))

    def test_undo_reverses_the_newest_change(self):
        notes = lambda: {k: v for k, v in self.snapshot().items() if k != "triage.log"}
        before = notes(); self.ok("kill", "2026-10-01-a.md", "--reason", "x")
        self.assertIn("Undone: kill “Alpha”", self.ok("undo").stdout); self.assertEqual(notes(), before)
        self.assertIn("Nothing to undo", self.refused("undo"))

    def test_a_name_can_be_a_slug_or_an_absolute_path_but_never_a_guess(self):
        self.assertIn("Kept “Bravo”", self.ok("keep", os.path.join(self.notes, "2026-10-02-b.md")).stdout)
        self.assertIn("Kept “Charlie”", self.ok("keep", "03-c").stdout)
        self.note("2026-10-04-c2.md", "C2"); self.note("2026-10-05-c3.md", "C3")
        self.assertIn("matches 3 notes", self.refused("keep", "c"))
        self.assertIn("no such note", self.refused("keep", "nothing-like-it.md"))

    def test_rawtriage_decisions_are_logged_listed_for_undo_and_counted_in_pulse(self):
        self.ok("kill", "2026-10-01-a.md", "--reason", "x"); self.ok("keep", "2026-10-02-b.md")
        with open(server.LOG_PATH) as fh: log = fh.read()
        self.assertIn("kill 2026-10-01-a.md", log); self.assertIn("keep 2026-10-02-b.md", log)
        st = self.get("/api/stats")
        self.assertEqual(st["undo"], ["keep “Bravo”", "kill “Alpha”"])
        self.assertEqual(sum(w["decided"] for w in st["weeks"]), 1)  # keep isn't a "decided" line in Pulse, kill is
        self.post("/api/undo", {})  # the page's undo reverses the CLI's newest decision
        self.assertEqual(self.get("/api/stats")["undo"], ["kill “Alpha”"])


class Projects(CliBase):
    def test_ship_and_kill(self):
        self.project("alpha", notes="# Alpha\n\n- status: promoted\n- promoted: 2026-10-01\n- due: 2099-01-01\n")
        self.project("beta", notes="# Beta\n\n- status: promoted\n")
        self.assertIn("suggested: archive", self.refused("project", "ship", "alpha"))  # a folder needs an outcome
        self.assertIn("Shipped alpha", self.ok("project", "ship", "alpha", "--outcome", "archive").stdout)
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "archive", "alpha")))
        self.assertIn("--reason", self.refused("project", "kill", "beta"))
        out = self.ok("project", "kill", "beta", "--reason", "no time").stdout
        self.assertIn("Killed beta → archive/beta_abandoned", out); self.assertIn("Update any T3 Code project", out)
        with open(os.path.join(self.tmp, "archive", "beta_abandoned", "NOTES.md")) as fh: self.assertIn("no time", fh.read())
        self.assertIn("unknown project", self.refused("project", "ship", "ghost"))

    def test_close_out_next_round_deps_and_a_blocked_move(self):
        import depcheck
        from unittest import mock
        self.project("alpha", notes="# Alpha\n\n- status: promoted\n- promoted: 2026-10-01\n- due: 2099-01-01\n\n- definition of done: One.\n")
        self.project("spike", kind="experiments", notes="# Spike\n\n- status: promoted\n")
        self.assertIn("--outcome", self.refused("project", "close", "alpha"))
        self.assertIn("--done", self.refused("project", "close", "alpha", "--outcome", "next"))
        r = self.ok("project", "close", "alpha", "--outcome", "next", "--done", "Two.", "--due-days", "5").stdout
        self.assertIn("Next round ships by", r)
        with open(os.path.join(self.tmp, "active", "alpha", "NOTES.md")) as fh: self.assertIn("## Rounds", fh.read())
        r = self.ok("project", "deps", "spike").stdout  # the kind is found from the board
        self.assertIn("experiments/spike → archive/spike (suggested: archive)", r); self.assertIn("Nothing blocks the move.", r)
        home = os.path.join(self.tmp, "home"); os.makedirs(os.path.join(home, ".local", "bin"))
        with open(os.path.join(home, ".local", "bin", "spike"), "w") as fh: fh.write(f"#!/bin/sh\n{self.tmp}/experiments/spike/run\n")
        with mock.patch.object(server, "DEPS", depcheck.Sources(home, proc=os.path.join(self.tmp, "proc"), crontab=lambda: "")):
            self.assertIn("BLOCKED: 1 thing(s)", self.ok("project", "deps", "spike", "--to", "abandoned").stdout)
            self.assertIn("Can't move spike", self.refused("project", "ship", "spike", "--outcome", "archive"))
        r = self.ok("project", "ship", "spike", "--outcome", "archive").stdout
        self.assertIn("→ archive/spike", r); self.assertIn("Left to do by hand", r)
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "archive", "spike")))
        self.ok("undo"); self.assertTrue(os.path.isdir(os.path.join(self.tmp, "experiments", "spike")))

    def test_extend_needs_a_ship_by(self):
        self.project("alpha", notes="# Alpha\n\n- status: promoted\n- promoted: 2026-10-01\n- due: 2099-01-01\n")
        self.project("beta", notes="# Beta\n\n- status: promoted\n")
        self.assertIn("--reason", self.refused("project", "extend", "alpha", "--days", "3"))
        self.assertIn("at least 10", self.refused("project", "extend", "alpha", "--reason", "busy"))
        for _ in range(2): self.assertIn("ship by", self.ok("project", "extend", "alpha", "--days", "3", "--reason", "waiting on parts").stdout)
        self.assertIn("ship it or kill it", self.refused("project", "extend", "alpha", "--reason", "waiting on parts"))
        self.assertIn("no ship-by", self.refused("project", "extend", "beta", "--reason", "waiting on parts"))


class Usage(CliBase):
    def test_bad_usage_exits_1_not_2(self):
        for args in (["bogus"], ["kill"], ["keep", "x", "--status", "dead"], ["promote"]):
            self.assertEqual(self.rt(*args).returncode, 1, args)

    def test_no_arguments_starts_claude_in_the_notes_folder(self):
        stubs = os.path.join(self.tmp, "stubs"); os.makedirs(stubs)
        with open(os.path.join(stubs, "claude"), "w") as fh: fh.write('#!/bin/sh\npwd > "$STUBOUT"\nprintf "%s\\n" "$@" >> "$STUBOUT"\n')
        os.chmod(os.path.join(stubs, "claude"), 0o755)
        out = os.path.join(self.tmp, "claude.out")
        self.ok(env={"PATH": stubs + ":" + os.environ["PATH"], "STUBOUT": out})
        lines = open(out).read().splitlines()
        self.assertEqual(lines[0], self.notes); self.assertIn("haiku", lines); self.assertIn("rawnotes skill", lines[-1])


class NoServerRunning(unittest.TestCase):
    """The CLI on a port where nothing runs: it starts a server from a temp copy of the code, or refuses."""
    def setUp(self):
        isolate_git(self)  # the server the CLI spawns runs git in the notes folder: never with the real config or hooks
        self.tmp = os.path.realpath(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.code = os.path.join(self.tmp, "code")
        for f in CODE:
            os.makedirs(os.path.dirname(os.path.join(self.code, f)), exist_ok=True); shutil.copy2(os.path.join(HERE, f), os.path.join(self.code, f))
        self.cli = os.path.join(self.code, "bin", "rawtriage")
        self.notes = os.path.join(self.tmp, "Projects", "RawNotes"); os.makedirs(os.path.join(self.notes, "done"))
        conf = os.path.join(self.tmp, "config"); os.makedirs(conf)
        with open(os.path.join(conf, "config.json"), "w") as fh: json.dump({"notes": self.notes}, fh)
        self.port = free_port()
        self.env = {**os.environ, "RAWTRIAGE_PORT": str(self.port), "RAWTRIAGE_CONFIG_DIR": conf, "HOME": self.tmp, "STUBLOG": self.tmp}
        self.addCleanup(self.stop_server)

    def stop_server(self):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/version", timeout=1) as r: os.kill(json.loads(r.read())["pid"], signal.SIGTERM)
        except OSError: pass

    def rt(self, *args, env=None):
        return subprocess.run([sys.executable, self.cli, *args], capture_output=True, text=True, env={**self.env, **(env or {})}, timeout=60, cwd=self.tmp)

    def stub(self, name, body):
        d = os.path.join(self.tmp, "stubs"); os.makedirs(d, exist_ok=True); p = os.path.join(d, name)
        with open(p, "w") as fh: fh.write("#!/bin/sh\n" + body)
        os.chmod(p, 0o755); return d + ":" + os.environ["PATH"]

    def listing(self):
        return sorted(os.listdir(self.notes))

    def test_with_no_unit_a_detached_server_is_spawned_and_the_command_goes_through(self):
        r = self.rt("new", "from-nothing"); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.listing()), 2)  # done/ and the new note
        self.assertEqual(self.rt("where").returncode, 0)  # the server it started is still there

    def test_the_spawned_server_does_not_inherit_a_git_dir(self):
        """An agent's shell inside a git hook (or `git -C` alias) has GIT_DIR set: the server the CLI starts must not
        inherit it, or every git and gh child it runs (gh repo create adds the remote with git) acts on that repo."""
        other = os.path.join(self.tmp, "other"); os.makedirs(other); subprocess.run(["git", "init", "-q", other], check=True)
        r = self.rt("new", "with-git-dir", env={"GIT_DIR": os.path.join(other, ".git"), "GIT_WORK_TREE": other, "GIT_INDEX_FILE": "/tmp/x"})
        self.assertEqual(r.returncode, 0, r.stderr)
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/version", timeout=5) as resp: pid = json.loads(resp.read())["pid"]
        with open(f"/proc/{pid}/environ", "rb") as fh: names = {e.split(b"=", 1)[0].decode() for e in fh.read().split(b"\0") if e}
        self.assertEqual(names & {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"}, set())
        self.assertIn("RAWTRIAGE_CONFIG_DIR", names)  # the rest of the environment is passed on

    def test_with_the_unit_installed_systemctl_start_is_used(self):
        start = f'{sys.executable} "{self.code}/server.py" --no-browser --port {self.port} >/dev/null 2>&1 </dev/null &'
        path = self.stub("systemctl", f'echo "$*" >> "$STUBLOG/systemctl"\ncase "$*" in *start*) setsid {start} ;; esac\nexit 0\n')
        r = self.rt("new", "via-unit", env={"PATH": path, "RAWTRIAGE_UNIT": "stub.service"}); self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(self.tmp, "systemctl")) as fh: calls = fh.read().splitlines()
        self.assertEqual(calls, ["--user cat stub.service", "--user start stub.service"])
        self.assertEqual(len(self.listing()), 2)

    def test_a_unit_that_fails_to_start_means_exit_2_and_nothing_changed(self):
        path = self.stub("systemctl", 'case "$*" in *start*) exit 1;; esac\nexit 0\n')
        r = self.rt("new", "never", env={"PATH": path, "RAWTRIAGE_UNIT": "stub.service"})
        self.assertEqual(r.returncode, 2); self.assertIn("Nothing was changed", r.stderr); self.assertEqual(self.listing(), ["done"])

    def test_a_server_that_cannot_start_means_exit_2_and_nothing_changed(self):
        with open(os.path.join(self.code, "server.py"), "w") as fh: fh.write("raise SystemExit(1)\n")
        r = self.rt("new", "never")
        self.assertEqual(r.returncode, 2); self.assertIn("couldn't be started", r.stderr); self.assertIn("Nothing was changed", r.stderr)
        self.assertEqual(self.listing(), ["done"])

    def test_something_else_on_the_port_means_exit_2_and_nothing_changed(self):
        hits = []
        class Other(BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append((self.command, self.path)); body = b'{"hello": "world"}'
                self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            do_POST = do_GET
            def log_message(self, *a): pass
        httpd = HTTPServer(("127.0.0.1", self.port), Other); self.addCleanup(httpd.server_close)
        threading.Thread(target=httpd.serve_forever, daemon=True).start(); self.addCleanup(httpd.shutdown)
        for args in (["new", "x"], ["kill", "2026-10-01-a.md", "--reason", "r"], ["-l"]):
            r = self.rt(*args)
            self.assertEqual(r.returncode, 2, args); self.assertIn("isn't rawnotes-triage", r.stderr); self.assertIn("Nothing was changed", r.stderr)
        # asked who it was, then stopped: no decision was ever posted. (Only /api/ requests count: something else on the
        # machine, such as a desktop widget that lists local servers, may probe a fresh port with GET /.)
        self.assertEqual({h for h in hits if h[0] != "GET" or h[1].startswith("/api/")}, {("GET", "/api/version")})
        self.assertEqual(self.listing(), ["done"])


class LostAfterSending(unittest.TestCase):
    """A server that takes the change and never answers: exit 3 ("may have finished"), never exit 2 / "nothing changed"."""
    def setUp(self):
        self.port = free_port(); hits = self.hits = []
        class Slow(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({"app": "rawnotes-triage", "version": "x", "pid": 1}).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            def do_POST(self):
                hits.append(self.path); self.rfile.read(int(self.headers.get("Content-Length", 0))); threading.Event().wait(5)
            def log_message(self, *a): pass
        httpd = HTTPServer(("127.0.0.1", self.port), Slow); httpd.daemon_threads = True; self.addCleanup(httpd.server_close)
        threading.Thread(target=httpd.serve_forever, daemon=True).start(); self.addCleanup(httpd.shutdown)

    def test_a_timeout_after_sending_exits_3_and_says_check(self):
        r = subprocess.run([sys.executable, CLI, "new", "slow-one"], capture_output=True, text=True, timeout=60,
                           env={**os.environ, "RAWTRIAGE_PORT": str(self.port), "RAWTRIAGE_TIMEOUT": "1"})
        self.assertEqual(r.returncode, 3, r.stderr); self.assertEqual(self.hits, ["/api/new"])
        self.assertIn("may have finished", r.stderr); self.assertIn("rawtriage -l", r.stderr); self.assertNotIn("Nothing was changed", r.stderr)

    def test_slow_routes_wait_longer(self):
        from importlib.machinery import SourceFileLoader
        import importlib.util
        spec = importlib.util.spec_from_loader("rawtriage_cli", SourceFileLoader("rawtriage_cli", CLI))
        cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
        t = cli.timeout_for
        with mock.patch.dict(os.environ):
            os.environ.pop("RAWTRIAGE_TIMEOUT", None)
            for path, body in (("/api/project/github", {}), ("/api/project/close", {}), ("/api/merge", {}), ("/api/action", {"action": "promote"}),
                               ("/api/undo", {})):  # undo of a move: tree check, sync-apps (up to 60 s) and a commit
                self.assertEqual(t(path, body), 300, path)
            for path, body in (("/api/action", {"action": "kill"}), ("/api/notes", None)):
                self.assertEqual(t(path, body), 60, path)


if __name__ == "__main__":
    unittest.main()
