#!/usr/bin/env python3
"""Flow gaps: which folders are projects on the board, and what Promote builds (skeleton + git)."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import server
from testkit import Fixture, TEST_EMAIL

ACTIVE_FILES = {".gitignore", ".scratch/.gitkeep", "AGENTS.md", "CLAUDE.md", "NOTES.md", "README.md"}
EXPERIMENT_FILES = {".gitignore", "AGENTS.md", "CLAUDE.md", "NOTES.md", "README.md"}
LEARNING_FILES = EXPERIMENT_FILES | {"notes/.gitkeep"}
TEMPLATE_FILES = EXPERIMENT_FILES | {"template/.gitkeep"}


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

    def test_files_inside_a_worktree_or_an_unpromoted_study_folder_are_not_cards(self):
        notes = "# {0}\n\n- status: promoted\n- due: 2026-12-01\n"
        for kind, name in (("active", "repo-feature"), ("experiments", "spike-wt"), ("active", "plain")):
            with open(os.path.join(self.tmp, kind, name, "NOTES.md"), "w") as fh: fh.write(notes.format(name))
        study = self.project("study", kind="learning", notes=notes.format("study"))  # no promoted: line: hand-made
        kit = self.project("kit", kind="templates", notes=notes.format("kit") + "- promoted: 2026-10-01\n")
        with open(os.path.join(self.tmp, "learning", "flat.md"), "w") as fh: fh.write(notes.format("flat"))  # an old flat file still shows
        paths = sorted(f["path"] for f in self.get("/api/notes")["inflight"])
        self.assertEqual(paths, ["active/plain/NOTES.md", "learning/flat.md", "templates/kit/NOTES.md"])
        self.assertTrue(os.path.isdir(study) and os.path.isdir(kit))
        self.post("/api/project/status", {"path": "active/repo-feature/NOTES.md", "action": "extend", "note": "Waiting on the parts."}, ok=False)

    def test_status_and_done_refuse_a_worktree(self):
        before = self.snapshot()
        self.post("/api/project/status", {"name": "repo-feature", "kind": "active", "action": "shipped"}, ok=False)
        self.post("/api/done", {"name": "repo-feature", "kind": "active", "text": "Ships."}, ok=False)
        self.assertEqual(self.snapshot(), before)



class PromoteBase(Fixture):
    """Three Inbox notes and helpers to promote them and look inside the result."""
    def setUp(self):
        super().setUp()
        self.a = self.note("2026-10-01-a.md", "Note A", question="Can it ship?")
        self.note("2026-10-02-b.md", "Note B"); self.note("2026-10-03-c.md", "Note C")
        self.before = self.snapshot()
        self.t = server.today().isoformat()

    def git(self, path, *args):
        return subprocess.run(["git", *args], cwd=path, capture_output=True, text=True).stdout.strip()

    def promote(self, file="2026-10-01-a.md", dest="active", ok=True, **extra):
        body = {"file": file, "action": "promote", "dest": dest, "note": "It runs on the phone.", **({"due_days": 3} if dest != "experiments" else {}), **extra}
        return self.post("/api/action", body, ok=ok)

    def files(self, proj):
        out = set()
        for root, dirs, fs in os.walk(proj):
            dirs[:] = [d for d in dirs if d != ".git"]
            out |= {os.path.relpath(os.path.join(root, f), proj) for f in fs}
        return out

    def read(self, proj, rel):
        with open(os.path.join(proj, rel), encoding="utf-8") as fh: return fh.read()

    def set_email(self, email):
        with open(self.gitconfig, "w") as fh: fh.write(f"[user]\n\tname = Someone\n\temail = {email}\n[core]\n\thooksPath = /dev/null\n")


class Slugs(PromoteBase):
    """A promoted folder or reference file always starts (and ends) with a letter or digit."""
    def test_a_slug_cut_to_length_never_ends_in_a_dash(self):
        for text in ("x" * 79 + "-yyyy", "x" * 79 + " yyyy", "x" * 78 + "--yy"):
            slug = server.clean_slug(text)
            self.assertLessEqual(len(slug), 80); self.assertFalse(slug.endswith("-"), slug); self.assertRegex(slug, r"^[a-z0-9]")
        self.assertEqual(self.promote(slug="a" * 79 + "-b")["target"], f"active/{'a' * 79}/NOTES.md")

    def test_promote_reference_and_merge_slugs_start_with_a_letter_or_digit(self):
        self.assertEqual(self.promote(slug="--x")["target"], "active/x/NOTES.md")
        self.assertEqual(self.promote("2026-10-02-b.md", slug="-")["target"], "active/b/NOTES.md")  # nothing left: the note's own slug
        self.note("2026-10-04--odd.md", "Odd")
        self.assertEqual(self.promote("2026-10-04--odd.md")["target"], "active/odd/NOTES.md")
        r = self.post("/api/action", {"file": "2026-10-03-c.md", "action": "reference", "slug": "-_-ref-"})
        self.assertEqual(r["target"], "reference/ref.md")
        for i in (5, 6): self.note(f"2026-10-0{i}-m{i}.md", f"M{i}")
        r = self.post("/api/merge", {"files": ["2026-10-05-m5.md", "2026-10-06-m6.md"], "title": "-- Both --", "then": "promote",
                                     "dest": "active", "note": "It ships.", "due_days": 3, "slug": "---"})
        self.assertTrue(r["file"] is None and os.path.isdir(os.path.join(self.tmp, "active", "both")), r)
        self.assertFalse([d for d in os.listdir(os.path.join(self.tmp, "active")) if not d[0].isalnum()])
        self.assertIn("kebab-case", self.post("/api/new", {"slug": "-lead"}, ok=False)["error"])


class PromoteSkeleton(PromoteBase):
    """07: Promote to active/experiments writes the skeleton, git init -b main and one commit, as one undo step."""
    def test_active_gets_the_skeleton_and_one_commit_on_main(self):
        r = self.promote(); proj = os.path.join(self.tmp, "active", "a")
        self.assertNotIn("warning", r); self.assertEqual(r["target"], "active/a/NOTES.md")
        self.assertEqual(self.files(proj), ACTIVE_FILES)
        due = (server.today() + server.dt.timedelta(days=3)).isoformat()
        for rel in ACTIVE_FILES - {"NOTES.md"}: self.assertNotIn("{{", self.read(proj, rel), rel)
        readme = self.read(proj, "README.md").splitlines()
        self.assertEqual(readme[0], "# Note A")
        self.assertEqual([l.split(":**")[0] for l in readme[2:6]], ["- **Status", "- **Stack", "- **Run it", "- **Repo"])
        self.assertEqual(readme[2], "- **Status:** building"); self.assertIn("Done means: It runs on the phone.", self.read(proj, "README.md"))
        self.assertIn(f"with a ship-by date of {due}.", self.read(proj, "AGENTS.md"))  # D2: agents need the ship-by
        for rel in ACTIVE_FILES - {"NOTES.md"}: self.assertNotIn(self.t, self.read(proj, rel), rel)  # but never the promote date
        agents = self.read(proj, "AGENTS.md")
        self.assertIn("gh repo create a --private --source . --remote origin", agents)
        self.assertIn("ALLOW_PUSH_MAIN=1 git push -u origin main", agents)
        self.assertIn(".scratch/<feature-slug>/issues/<NN>-<slug>.md", agents); self.assertIn("## First build round", agents)
        self.assertEqual(self.read(proj, "CLAUDE.md"), "@AGENTS.md\n")
        notes = self.read(proj, "NOTES.md")  # the plan only: no board data, no copy of the original note
        self.assertEqual(notes, "# Note A\n\n## Plan\n\n- definition of done: It runs on the phone.\n- first step:\n"
                                "- ship / stop check-in: on the ship-by date, either ship it or kill it\n")
        rec = self.record("active", "a")  # the board data lives in RawNotes
        self.assertEqual(rec, f"# Note A\n\n- status: promoted\n- promoted: {self.t}\n- due: {due}\n- github: private\n\n"
                              "## Capture\n\n- [Note A](<../../done/2026-10-01-a.md>)\n")
        self.assertIn("Can it ship?", self.read(self.notes, "done/2026-10-01-a.md"))
        self.assertIn("rawtriage brief a", agents)
        # the first commit, which may go public with the repo, carries no board field and no capture text
        committed = self.git(proj, "show", "HEAD", "--format=")
        for private in ("- status:", "- promoted:", "- github:", "- due:", "Can it ship?", "## Rounds", "## Moved"):
            self.assertNotIn(private, committed, private)
        # git: main, one commit with every skeleton file, clean, fixture identity
        self.assertEqual(self.git(proj, "branch", "--show-current"), "main")
        self.assertEqual(self.git(proj, "log", "--format=%s|%ae"), f"Start a (promoted from RawNotes)|{TEST_EMAIL}")
        self.assertEqual(set(self.git(proj, "ls-tree", "-r", "--name-only", "HEAD").splitlines()), ACTIVE_FILES)
        self.assertEqual(self.git(proj, "status", "--porcelain", "--untracked-files=all"), "")
        # the board sees a git repo and the same done means as before
        ps = {p["path"]: p for p in self.get("/api/projects")["projects"]}
        self.assertEqual(ps["active/a"]["gh"]["git"], True); self.assertTrue(ps["active/a"]["done_defined"])
        self.assertEqual([f["path"] for f in self.get("/api/notes")["inflight"]], ["active/a/NOTES.md"])  # README/AGENTS add no cards

    def test_notes_md_is_the_plan_while_the_old_flat_layout_still_copies_the_note(self):
        self.promote(); proj = os.path.join(self.tmp, "active", "a")
        with mock.patch.object(server, "DESTS", {**server.DESTS, "active": "file"}):  # the flat layout writes the plain promoted note
            server._undo.clear(); self.promote("2026-10-02-b.md")
        flat = self.read(self.tmp, "active/b.md")
        self.assertIn("Why Note B?", flat); self.assertEqual(server.field(flat, "status"), "promoted")
        self.assertIsNone(self.record("active", "b"))  # a flat file is its own board file
        self.assertNotIn("Can it ship?", self.read(proj, "NOTES.md"))
        self.assertTrue(self.read(proj, "NOTES.md").startswith("# Note A\n\n## Plan\n"))

    def test_experiments_get_their_skeleton_with_no_ship_by(self):
        self.promote(dest="experiments"); proj = os.path.join(self.tmp, "experiments", "a")
        self.assertEqual(self.files(proj), EXPERIMENT_FILES)
        self.assertIn("open-ended", self.read(proj, "README.md").splitlines()[2])
        self.assertEqual(server.field(self.record("experiments", "a"), "due"), "")
        self.assertEqual(server.field(self.record("experiments", "a"), "promoted"), self.t)
        self.assertEqual(set(self.git(proj, "ls-tree", "-r", "--name-only", "HEAD").splitlines()), EXPERIMENT_FILES)
        self.assertIn("gh repo create a --private", self.read(proj, "AGENTS.md"))

    def test_text_from_the_note_is_never_read_as_a_placeholder(self):
        self.note("2026-10-04-p.md", "Braces {{slug}} and {{today}}")
        self.post("/api/action", {"file": "2026-10-04-p.md", "action": "promote", "dest": "active", "note": "Shows {{title}} literally.", "due_days": 3})
        proj = os.path.join(self.tmp, "active", "p")
        self.assertEqual(self.read(proj, "README.md").splitlines()[0], "# Braces {{slug}} and {{today}}")
        self.assertIn("Done means: Shows {{title}} literally.", self.read(proj, "README.md"))

    def test_merge_then_promote_gets_the_same_skeleton(self):
        r = self.post("/api/merge", {"files": ["2026-10-02-b.md", "2026-10-03-c.md"], "title": "Both", "then": "promote",
                                     "dest": "active", "note": "Done.", "due_days": 5, "slug": "both"})
        self.assertNotIn("warning", r); proj = os.path.join(self.tmp, "active", "both")
        self.assertEqual(self.files(proj), ACTIVE_FILES)
        self.assertEqual(self.git(proj, "log", "--format=%s"), "Start both (promoted from RawNotes)")
        self.post("/api/undo", {}); self.assertEqual(self.snapshot(), self.before)

    def test_a_taken_name_gets_a_suffix_and_its_own_repo(self):
        self.promote(); self.promote("2026-10-02-b.md", slug="a")
        proj = os.path.join(self.tmp, "active", "a-2")
        self.assertEqual(self.git(proj, "log", "--format=%s"), "Start a-2 (promoted from RawNotes)")
        self.assertIn("gh repo create a-2 --private", self.read(proj, "AGENTS.md"))

    # ---- identity: the app checks, never sets
    def test_a_non_noreply_identity_gets_a_repo_but_no_commit_and_a_warning(self):
        self.set_email("me@example.com")
        r = self.promote(); proj = os.path.join(self.tmp, "active", "a")
        self.assertIn("no commit", r["warning"]); self.assertIn("@users.noreply.github.com", r["warning"])
        # the warning reaches a phone on a --host 0.0.0.0 bind and triage.log: no email address, no absolute path
        self.assertNotIn("me@example.com", r["warning"]); self.assertNotIn(self.tmp, r["warning"])
        self.assertIn("in active/a: git add -A && git commit -m", r["warning"])
        self.assertTrue(os.path.isdir(os.path.join(proj, ".git")))
        self.assertEqual(self.git(proj, "rev-parse", "-q", "--verify", "HEAD"), "")
        self.assertEqual(self.git(proj, "config", "--local", "user.email"), "")  # never set an identity
        with open(self.gitconfig) as fh: self.assertIn("me@example.com", fh.read())
        self.assertEqual(self.files(proj), ACTIVE_FILES)
        self.post("/api/undo", {}); self.assertEqual(self.snapshot(), self.before)

    def test_a_warning_is_logged_but_pulse_counts_one_decision(self):
        server.log = self.saved[2]; server.LOG_PATH = os.path.join(self.tmp, "triage.log")
        self.set_email("me@example.com"); self.promote()
        lines = open(server.LOG_PATH).read().splitlines()
        self.assertNotIn("me@example.com", "\n".join(lines))
        self.assertEqual([l.split(" ", 2)[1] for l in lines], ["promote", "warning"])
        self.assertEqual(sum(w["decided"] for w in self.get("/api/stats")["weeks"]), 1)

    def test_no_identity_at_all_is_a_warning_too(self):
        with open(self.gitconfig, "w") as fh: fh.write("[core]\n\thooksPath = /dev/null\n[user]\n\tuseConfigOnly = true\n")
        r = self.promote()
        self.assertIn("no commit", r["warning"]); self.assertIn("git has no email set", r["warning"])

    def test_an_inherited_identity_or_config_override_never_reaches_git(self):
        """GIT_AUTHOR_* / GIT_COMMITTER_* (git exports them to its hooks), GIT_CONFIG_COUNT + KEY/VALUE and
        GIT_CONFIG_PARAMETERS (`git -c`) are dropped: the identity checked and used is the one in git's config."""
        for i, env in enumerate(({"GIT_AUTHOR_EMAIL": "leak@example.com", "GIT_COMMITTER_EMAIL": "leak@example.com"},
                                 {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.email", "GIT_CONFIG_VALUE_0": "leak@example.com"},
                                 {"GIT_CONFIG_PARAMETERS": "'user.email'='leak@example.com'"})):
            name = f"2026-10-0{i + 1}-{'abc'[i]}.md"
            if i: self.note(name, title="x")
            with mock.patch.dict(os.environ, env): r = self.promote(name)
            self.assertNotIn("warning", r, env)
            self.assertEqual(self.git(os.path.join(self.tmp, "active", "abc"[i]), "log", "--format=%ae %ce"), f"{TEST_EMAIL} {TEST_EMAIL}")

    def test_one_list_of_dropped_git_variables_for_every_git_and_gh(self):
        import depcheck, importlib.machinery, importlib.util
        bad = {v: "x" for v in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                                "GIT_COMMON_DIR", "GIT_NAMESPACE", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0",
                                "GIT_CONFIG_VALUE_0", "GIT_AUTHOR_NAME", "GIT_AUTHOR_DATE", "GIT_COMMITTER_EMAIL")}
        loader = importlib.machinery.SourceFileLoader("rawtriage_cli", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", "rawtriage"))
        cli = importlib.util.module_from_spec(importlib.util.spec_from_loader("rawtriage_cli", loader)); loader.exec_module(cli)
        with mock.patch.dict(os.environ, {**bad, "GIT_CONFIG_GLOBAL_KEEP": "y"}):
            for env in (server.git_env(self.tmp), server.gh_env(), depcheck.without_git_env(), cli.server_env()):
                self.assertEqual(set(env) & set(bad), set()); self.assertIn("GIT_CONFIG_GLOBAL", env); self.assertIn("GIT_CONFIG_GLOBAL_KEEP", env)

    def test_the_suffix_is_configurable(self):
        os.makedirs(server.CONFIG_DIR, exist_ok=True)
        with open(server.config_path(), "w") as fh: json.dump({"noreply_suffix": "@corp.example"}, fh)
        self.assertIn("@corp.example", self.promote()["warning"])  # the fixture's GitHub noreply no longer passes
        self.set_email("me@corp.example"); r = self.promote("2026-10-02-b.md")
        self.assertNotIn("warning", r); self.assertEqual(self.git(os.path.join(self.tmp, "active", "b"), "log", "--format=%ae"), "me@corp.example")

    def test_a_refused_commit_warns_and_keeps_the_promote(self):
        hooks = os.path.join(self.tmp, "hooks"); os.makedirs(hooks)
        with open(os.path.join(hooks, "pre-commit"), "w") as fh: fh.write("#!/bin/sh\necho 'secret found' >&2\nexit 1\n")
        os.chmod(os.path.join(hooks, "pre-commit"), 0o755)
        with open(self.gitconfig, "a") as fh: fh.write(f"[core]\n\thooksPath = {hooks}\n")
        r = self.promote(); proj = os.path.join(self.tmp, "active", "a")
        self.assertIn("first commit failed", r["warning"]); self.assertIn("secret found", r["warning"]); self.assertNotIn(self.tmp, r["warning"])
        self.assertEqual(self.git(proj, "rev-parse", "-q", "--verify", "HEAD"), "")
        self.assertFalse(os.path.exists(self.a))
        self.post("/api/undo", {}); self.assertFalse(os.path.exists(proj)); self.assertTrue(os.path.exists(self.a))

    def test_no_git_still_promotes_with_a_warning(self):
        real = shutil.which
        with mock.patch.object(server.shutil, "which", lambda name, *a, **k: None if name == "git" else real(name, *a, **k)):
            r = self.promote()
        proj = os.path.join(self.tmp, "active", "a")
        self.assertIn("git is not installed", r["warning"])
        self.assertEqual(self.files(proj), ACTIVE_FILES); self.assertFalse(os.path.exists(os.path.join(proj, ".git")))
        self.post("/api/undo", {}); self.assertEqual(self.snapshot(), self.before)

    # ---- undo
    def test_undo_removes_the_folder_and_its_git(self):
        self.promote()
        self.post("/api/undo", {})
        self.assertEqual(self.snapshot(), self.before); self.assertFalse(os.path.exists(os.path.join(self.tmp, "active")))

    def test_undo_refuses_after_a_second_commit_extra_or_edited_file(self):
        proj = os.path.join(self.tmp, "active", "a")
        def second_commit():
            with open(os.path.join(proj, "README.md"), "a") as fh: fh.write("more\n")
            self.git(proj, "commit", "-qam", "more")
        def amended():  # same files as before, but HEAD is no longer the skeleton commit
            self.git(proj, "commit", "-q", "--amend", "-m", "renamed")
        def edited_tracked(): open(os.path.join(proj, "AGENTS.md"), "a").write("x\n")
        def untracked_in_scratch(): open(os.path.join(proj, ".scratch", "todo.md"), "w").write("x\n")
        def ignored_file(): open(os.path.join(proj, ".env"), "w").write("TOKEN=x\n")
        def new_branch(): self.git(proj, "checkout", "-qb", "feat"); open(os.path.join(proj, "f.txt"), "w").write("x"); self.git(proj, "add", "f.txt"); self.git(proj, "commit", "-qm", "f"); self.git(proj, "checkout", "-q", "main")
        def staged_only(): open(os.path.join(proj, "s.txt"), "w").write("x"); self.git(proj, "add", "s.txt"); os.remove(os.path.join(proj, "s.txt"))
        for change in (second_commit, amended, edited_tracked, untracked_in_scratch, ignored_file, new_branch, staged_only):
            with self.subTest(change.__name__):
                if os.path.exists(proj): shutil.rmtree(proj)
                for f in os.listdir(self.notes): os.path.isdir(os.path.join(self.notes, f)) or os.remove(os.path.join(self.notes, f))
                shutil.rmtree(os.path.join(self.notes, "done"), ignore_errors=True); shutil.rmtree(os.path.join(self.tmp, "active"), ignore_errors=True)
                shutil.rmtree(os.path.join(self.notes, "projects"), ignore_errors=True)
                server._undo.clear(); self.note("2026-10-01-a.md", "Note A", question="Can it ship?")
                self.promote(); change(); mid = self.snapshot()
                r = self.post("/api/undo", {}, ok=False)
                self.assertIn("changed since", r["error"])
                self.assertEqual(self.snapshot(), mid); self.assertEqual(len(server._undo), 1)

    def test_undo_with_no_commit_refuses_once_a_commit_exists(self):
        self.set_email("me@example.com"); self.promote(); proj = os.path.join(self.tmp, "active", "a")
        self.set_email(TEST_EMAIL); self.git(proj, "add", "-A"); self.git(proj, "commit", "-qm", "by hand"); mid = self.snapshot()
        self.assertIn("changed since", self.post("/api/undo", {}, ok=False)["error"]); self.assertEqual(self.snapshot(), mid)

    def test_undo_refuses_when_git_is_gone_and_changes_nothing(self):
        self.promote(); mid = self.snapshot(); real = shutil.which
        with mock.patch.object(server.shutil, "which", lambda name, *a, **k: None if name == "git" else real(name, *a, **k)):
            self.assertIn("git is not installed", self.post("/api/undo", {}, ok=False)["error"])
        self.assertEqual(self.snapshot(), mid)

    def test_undo_refuses_when_dot_git_became_a_symlink(self):
        self.promote(); proj = os.path.join(self.tmp, "active", "a")
        os.rename(os.path.join(proj, ".git"), os.path.join(self.tmp, "moved.git")); os.symlink(os.path.join(self.tmp, "moved.git"), os.path.join(proj, ".git"))
        mid = self.snapshot()
        self.post("/api/undo", {}, ok=False); self.assertEqual(self.snapshot(), mid)
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "moved.git", "objects")))

    # ---- fault injection
    def fail_git(self, sub, *, after=None):
        """Make the server's `git <sub>` fail (after running `after` first, e.g. a partial init)."""
        real = server._git
        def fake(path, args, timeout=server.GIT_TIMEOUT):
            if args[0] == sub:
                if after: after(path)
                return 1, "", f"fatal: simulated {sub} failure"
            return real(path, args, timeout)
        p = mock.patch.object(server, "_git", fake); p.start(); self.addCleanup(p.stop)

    def test_a_failure_after_git_init_rolls_everything_back_git_included(self):
        real = os.rename; calls = []
        def fail_first(a, b):  # the capture's move to done/ comes after git init and the commit
            calls.append(a); raise OSError(5, "simulated disk error")
        with mock.patch("os.rename", fail_first):
            r = self.promote(ok=False)
        self.assertIn("simulated", r["error"]); self.assertEqual(len(calls), 1)
        self.assertEqual((self.snapshot(), server._undo), (self.before, []))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "active")))

    def test_a_failed_git_init_rolls_back_a_half_made_git_folder(self):
        self.fail_git("init", after=lambda path: os.makedirs(os.path.join(path, ".git", "objects")))
        r = self.promote(ok=False)
        self.assertIn("git init failed", r["error"])
        self.assertEqual((self.snapshot(), server._undo), (self.before, []))

    def test_a_half_made_git_inside_an_outer_repo_never_reads_the_outer_one(self):
        """Projects inside an outer repo (a dotfiles or ~/Projects repo with a remote): a .git that git can't read must not
        send git searching upward, where the outer repo's remote would refuse the rollback and strand the folder."""
        self.git(self.tmp, "init", "-q", "-b", "main"); self.git(self.tmp, "remote", "add", "origin", "https://github.com/me/outer.git")
        self.git(self.tmp, "commit", "-q", "--allow-empty", "-m", "outer")
        before = self.snapshot()
        self.fail_git("init", after=lambda path: os.makedirs(os.path.join(path, ".git", "objects")))
        r = self.promote(ok=False)
        self.assertIn("git init failed", r["error"]); self.assertNotIn("remote", r["error"])
        self.assertEqual((self.snapshot(), server._undo), (before, []))
        self.assertIsNone(server.git_out(os.path.join(self.tmp, "active"), ["rev-parse", "--show-toplevel"]))  # no upward search

    def test_git_init_and_first_commit_errors_spell_home_as_tilde(self):
        home = tempfile.mkdtemp(prefix="rt-home-"); self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        real = server._git
        def fake(path, args, timeout=server.GIT_TIMEOUT):
            if args[0] == "init": return 1, "", f"fatal: cannot copy {home}/.git-templates/hooks: Permission denied"
            return real(path, args, timeout)
        with mock.patch.dict(os.environ, {"HOME": home}):
            with mock.patch.object(server, "_git", fake): err = self.promote(ok=False)["error"]
            self.assertIn("git init failed", err); self.assertIn("~/.git-templates/hooks", err); self.assertNotIn(home, err)
            hooks = os.path.join(self.tmp, "hooks"); os.makedirs(hooks)
            with open(os.path.join(hooks, "pre-commit"), "w") as fh: fh.write(f"#!/bin/sh\necho 'key in {home}/.ssh/id' >&2\nexit 1\n")
            os.chmod(os.path.join(hooks, "pre-commit"), 0o755)
            with open(self.gitconfig, "a") as fh: fh.write(f"[core]\n\thooksPath = {hooks}\n")
            w = self.promote()["warning"]
        self.assertIn("first commit failed", w); self.assertIn("key in ~/.ssh/id", w); self.assertNotIn(home, w)

    def test_the_git_ceiling_is_the_real_parent_and_never_a_split_path(self):
        real = os.path.join(self.tmp, "real"); os.makedirs(os.path.join(real, "proj"))
        link = os.path.join(self.tmp, "link"); os.symlink(real, link)
        self.assertEqual(server.git_env(os.path.join(link, "proj"))["GIT_CEILING_DIRECTORIES"], os.path.realpath(real))
        odd = os.path.join(self.tmp, "a:b"); os.makedirs(os.path.join(odd, "proj"))
        self.assertNotIn("GIT_CEILING_DIRECTORIES", server.git_env(os.path.join(odd, "proj")))  # ':' splits the list

    def test_a_failed_git_add_is_a_warning_not_a_failure(self):
        self.fail_git("add")
        r = self.promote(); self.assertIn("first commit failed", r["warning"]); self.assertIn("simulated add failure", r["warning"])
        self.post("/api/undo", {}); self.assertEqual(self.snapshot(), self.before)

    def test_a_git_timeout_rolls_back_cleanly(self):
        real = server._git
        def slow(path, args, timeout=server.GIT_TIMEOUT):
            if args[0] == "init": raise subprocess.TimeoutExpired("git", timeout)
            return real(path, args, timeout)
        with mock.patch.object(server, "_git", slow): r = self.promote(ok=False)
        self.assertIn("git init failed", r["error"]); self.assertEqual((self.snapshot(), server._undo), (self.before, []))

    def test_undo_that_cannot_remove_git_changes_nothing(self):
        self.promote(); after = self.snapshot()
        with mock.patch.object(server.shutil, "rmtree", side_effect=OSError(5, "simulated")):
            r = self.post("/api/undo", {}, ok=False)
        self.assertIn("nothing was changed", r["error"])
        self.assertEqual((self.snapshot(), len(server._undo)), (after, 1))
        self.post("/api/undo", {}); self.assertEqual(self.snapshot(), self.before)

    def test_rollback_that_cannot_remove_git_leaves_an_unfinished_undo_entry(self):
        with mock.patch("os.rename", side_effect=OSError(5, "simulated")), mock.patch.object(server.shutil, "rmtree", side_effect=OSError(5, "stuck")):
            r = self.promote(ok=False)
        self.assertIn("could not be rolled back", r["error"])
        self.assertIn("unfinished", server._undo[-1]["label"])
        self.post("/api/undo", {}); self.assertEqual((self.snapshot(), server._undo), (self.before, []))

    def test_a_dot_git_already_in_the_new_folder_is_refused(self):
        tx = server.Tx(); proj = os.path.join(self.tmp, "active", "x"); os.makedirs(os.path.join(proj, ".git"))
        with self.assertRaises(FileExistsError): tx.gitinit(proj, "m")
        self.assertEqual(tx.ops, [])

    def test_git_never_follows_an_inherited_git_dir(self):
        other = os.path.join(self.tmp, "other"); os.makedirs(other); subprocess.run(["git", "init", "-q", other], check=True)
        with mock.patch.dict(os.environ, {"GIT_DIR": os.path.join(other, ".git"), "GIT_WORK_TREE": other}): self.promote()
        self.assertEqual(self.git(os.path.join(self.tmp, "active", "a"), "log", "--format=%s"), "Start a (promoted from RawNotes)")
        self.assertEqual(self.git(other, "rev-parse", "-q", "--verify", "HEAD"), "")



class LearningAndTemplates(PromoteBase):
    """08: learning and templates promote to git-initialised folders too; only promoted ones join the board."""
    def test_learning_promotes_to_a_folder_with_a_repo(self):
        r = self.promote(dest="learning"); proj = os.path.join(self.tmp, "learning", "a")
        self.assertNotIn("warning", r); self.assertEqual(r["target"], "learning/a/NOTES.md")
        self.assertEqual(self.files(proj), LEARNING_FILES)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "learning", "a.md")))
        self.assertEqual(set(self.git(proj, "ls-tree", "-r", "--name-only", "HEAD").splitlines()), LEARNING_FILES)
        self.assertEqual(self.git(proj, "log", "--format=%s"), "Start a (promoted from RawNotes)")
        for rel in LEARNING_FILES - {"NOTES.md"}: self.assertNotIn("{{", self.read(proj, rel), rel)
        self.assertIn("gh repo create a --private", self.read(proj, "AGENTS.md"))
        self.assertEqual(server.field(self.record("learning", "a"), "github"), "private")
        self.assertEqual(server.field(self.read(proj, "NOTES.md"), "github"), "")
        self.post("/api/undo", {}); self.assertEqual(self.snapshot(), self.before)

    def test_templates_promote_to_a_folder_that_says_how_to_copy_it(self):
        self.promote(dest="templates"); proj = os.path.join(self.tmp, "templates", "a")
        self.assertEqual(self.files(proj), TEMPLATE_FILES)
        readme = self.read(proj, "README.md")
        self.assertEqual(readme.splitlines()[0], "# Note A"); self.assertIn("## What it scaffolds", readme)
        self.assertIn("cp -r template/. ", readme)
        self.assertEqual(set(self.git(proj, "ls-tree", "-r", "--name-only", "HEAD").splitlines()), TEMPLATE_FILES)

    def test_promoted_folders_show_on_the_board_hand_made_ones_do_not(self):
        self.promote(dest="learning"); self.promote("2026-10-02-b.md", dest="templates")
        self.project("cert-study", kind="learning")  # hand-made, no NOTES.md
        self.project("review-kit", kind="templates", notes="# Review kit\n\n- status: maintained\n")  # NOTES.md, never promoted
        self.project("half", kind="learning", notes="# Half\n\nNotes, but promoted: only in prose.\n")
        self.project("legacy", kind="templates", notes="# Legacy\n\n- promoted: 2026-09-01\n")  # promoted before 2.15: no record
        ps = {p["path"]: p for p in self.get("/api/projects")["projects"]}
        self.assertEqual(set(ps), {"learning/a", "templates/b", "templates/legacy"})
        self.assertIsNotNone(self.record("learning", "a"))  # on the board through its record: its NOTES.md has no promoted: line
        self.assertEqual(server.field(self.read(os.path.join(self.tmp, "learning", "a"), "NOTES.md"), "promoted"), "")
        self.assertEqual((ps["learning/a"]["kind"], ps["templates/b"]["kind"]), ("learning", "templates"))
        self.assertTrue(ps["learning/a"]["gh"]["git"]); self.assertTrue(ps["learning/a"]["done_defined"])
        inflight = {f["path"]: f for f in self.get("/api/notes")["inflight"]}
        self.assertEqual(set(inflight), {"learning/a/NOTES.md", "templates/b/NOTES.md"})
        self.assertEqual((inflight["learning/a/NOTES.md"]["project"], inflight["learning/a/NOTES.md"]["dest"]), ("a", "learning"))
        self.assertEqual(self.get("/api/project?name=a&kind=learning")["path"], "learning/a")
        self.assertEqual(self.get("/api/project?name=b&kind=templates")["summary_source"], "README.md")
        for name, kind in (("cert-study", "learning"), ("review-kit", "templates"), ("half", "learning"), ("a", "templates")):
            self.assertEqual(self.call(f"/api/project?name={name}&kind={kind}")[0], 400, name)
            self.post("/api/project/status", {"name": name, "kind": kind, "action": "shipped"}, ok=False)

    def test_old_flat_files_still_show_and_can_be_shipped(self):
        due = (server.today() + server.dt.timedelta(days=4)).isoformat()
        for kind in ("learning", "templates"):
            os.makedirs(os.path.join(self.tmp, kind), exist_ok=True)
            with open(os.path.join(self.tmp, kind, "old.md"), "w") as fh: fh.write(f"# Old {kind}\n\n- status: promoted\n- promoted: 2026-09-01\n- due: {due}\n")
        inflight = {f["path"]: f for f in self.get("/api/notes")["inflight"]}
        self.assertEqual(set(inflight), {"learning/old.md", "templates/old.md"})
        self.assertEqual(inflight["learning/old.md"]["days_left"], 4)
        self.assertEqual(self.get("/api/projects")["projects"], [])  # a flat file is a card from inflight, not a folder
        self.post("/api/project/status", {"path": "templates/old.md", "action": "shipped"})
        self.assertIn("- status: shipped", self.read(self.tmp, "templates/old.md"))

    def test_ship_extend_kill_and_done_means_work_on_the_new_folders(self):
        self.promote(dest="learning"); self.promote("2026-10-02-b.md", dest="templates"); self.promote("2026-10-03-c.md", dest="learning")
        server._undo.clear()
        lp, tp = os.path.join(self.tmp, "learning", "a"), os.path.join(self.tmp, "templates", "b")
        old_due = server.field(self.record("learning", "a"), "due")
        self.post("/api/project/status", {"path": "learning/a/NOTES.md", "action": "extend", "days": 7, "note": "Waiting on the exam date"})
        self.assertNotEqual(server.field(self.record("learning", "a"), "due"), old_due)
        self.post("/api/project/status", {"path": "templates/b/NOTES.md", "action": "shipped", "outcome": "next", "done": "Copies cleanly.", "due_days": 5})
        self.assertIn("## Rounds", self.record("templates", "b")); self.assertNotIn("## Rounds", self.read(tp, "NOTES.md"))
        self.post("/api/project/status", {"name": "c", "kind": "learning", "action": "kill", "note": "lost interest"})
        self.assertIn("**Killed", self.record("archive", "c_abandoned"))
        self.post("/api/done", {"name": "a", "kind": "learning", "text": "I can pass the exam.", "expected": "It runs on the phone."})
        self.assertIn("- definition of done: I can pass the exam.", self.read(lp, "NOTES.md"))
        ps = {p["path"]: p["status"] for p in self.get("/api/projects")["projects"]}
        self.assertEqual((ps["templates/b"], "learning/c" in ps), ("promoted", False))
        for _ in range(4): self.post("/api/undo", {})
        self.assertEqual(server.field(self.record("learning", "a"), "due"), old_due)


if __name__ == "__main__":
    unittest.main()
