#!/usr/bin/env python3
"""v2.13 (ticket 09): the "Not on GitHub" flag, the GitHub gate at close out for active/templates, and the Create private GitHub repo
button. `gh` is a stub script on PATH and "GitHub" is a local bare repo (git's url.pushInsteadOf), so nothing leaves the machine."""
import json
import os
import shutil
import stat
import subprocess
import tempfile
import urllib.error
import urllib.request
from unittest import mock

import server
from testkit import Fixture, undo_list, cli_argv

GATED = ("active", "templates")
STUB = r'''#!/bin/sh
echo "gh $*" >> "$STUB_LOG"
case "$1 $2" in
  "api user") [ -f "$STUB_DIR/signed_out" ] && { echo "To get started with GitHub CLI, please run:  gh auth login" >&2; exit 4; }
              echo '{"login":"me"}' ;;
  "repo create") [ -f "$STUB_DIR/create_fails" ] && { echo "GraphQL: name already exists on this account" >&2; exit 1; }
                 [ -f "$STUB_DIR/slow" ] && sleep 1.5
                 slug=$2; shift 2; slug=$1; shift
                 while [ $# -gt 0 ]; do case "$1" in --source) src=$2; shift;; esac; shift; done
                 git -C "$src" remote add origin "https://github.com/$slug.git" ;;
  *) echo "unexpected gh call: $*" >&2; exit 9 ;;
esac
'''


def run(path, *args):
    return subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, check=True).stdout.strip()


class GithubCase(Fixture):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(server, "SHIP_NEEDS_GITHUB", GATED); p.start(); self.addCleanup(p.stop)
        outside = tempfile.mkdtemp(prefix="rt-ghstub-"); self.addCleanup(shutil.rmtree, outside, ignore_errors=True)  # not under tmp: snapshots compare tmp
        self.stub = os.path.join(outside, "stubbin"); os.makedirs(self.stub)
        self.log = os.path.join(outside, "gh.log"); open(self.log, "w").close()
        gh = os.path.join(self.stub, "gh")
        with open(gh, "w") as fh: fh.write(STUB)
        os.chmod(gh, os.stat(gh).st_mode | stat.S_IEXEC)
        p = mock.patch.dict(os.environ, {"PATH": self.stub + os.pathsep + os.environ["PATH"], "STUB_LOG": self.log, "STUB_DIR": self.stub})
        p.start(); self.addCleanup(p.stop)
        for var in ("GH_TOKEN", "GITHUB_TOKEN", "ALLOW_PUSH_MAIN"): os.environ.pop(var, None)
        self.remote = os.path.join(self.tmp, "github-me-%s.git")  # per-repo bare repos: "GitHub"

    def gh_calls(self): return open(self.log).read().splitlines()

    def bare(self, name):
        """A local bare repo standing in for github.com/me/<name>: pushes to that URL land here."""
        path = self.remote % name; subprocess.run(["git", "init", "-q", "--bare", "-b", "main", path], check=True)
        with open(self.gitconfig, "a") as fh: fh.write(f'[url "{path}"]\n\tpushInsteadOf = https://github.com/me/{name}.git\n')
        return path

    def promoted(self, name="demo", dest="active", back_days=0):
        """A project made by Promote itself (folder, skeleton, one commit)."""
        self.note("2026-10-01-" + name + ".md", title=name)
        self.post("/api/action", {"file": "2026-10-01-" + name + ".md", "action": "promote", "dest": dest, "note": "It ships.", "due_days": 5})
        path = os.path.join(self.tmp, dest, name)
        if back_days: self.set_promoted(path, back_days)
        return path

    def set_promoted(self, path, days_ago):
        """Back-date the promote in the project's board record (in RawNotes: the project's own tree stays clean)."""
        f = os.path.join(self.notes, "projects", os.path.basename(os.path.dirname(path)), os.path.basename(path) + ".md"); text = open(f).read()
        old = server.field(text, "promoted"); new = (server.today() - server.dt.timedelta(days=days_ago)).isoformat()
        open(f, "w").write(text.replace(f"promoted: {old}", f"promoted: {new}", 1))

    def state(self, name, kind="active"):
        return next(p for p in self.get("/api/projects?refresh=1")["projects"] if p["name"] == name and p["kind"] == kind)["gh"]

    def create(self, name, kind="active", ok=True): return self.post("/api/project/github", {"name": name, "kind": kind}, ok=ok)


class NotOnGithubFlag(GithubCase):
    def test_a_fresh_promote_is_not_flagged_until_it_has_a_commit_or_is_two_days_old(self):
        path = self.promoted()
        self.assertEqual((self.state("demo")["not_on_github"], self.state("demo")["after_skeleton"]), (False, 0))
        open(os.path.join(path, "x.txt"), "w").write("x"); run(path, "add", "-A"); run(path, "commit", "-qm", "work")
        s = self.state("demo"); self.assertEqual((s["not_on_github"], s["after_skeleton"]), (True, 1))

    def test_age_alone_flags_it_from_the_second_day(self):
        path = self.promoted(); self.set_promoted(path, 1); self.assertFalse(self.state("demo")["not_on_github"])
        self.set_promoted(path, 2); self.assertTrue(self.state("demo")["not_on_github"])

    def test_a_project_from_before_the_skeleton_is_judged_by_age_only(self):
        for name, days in (("new", 0), ("old", 3)):
            path = self.project(name, notes=f"# {name}\n\n- status: promoted\n- promoted: {(server.today() - server.dt.timedelta(days=days)).isoformat()}\n")
            run(path, "init", "-q", "-b", "main"); run(path, "add", "-A"); run(path, "commit", "-qm", "initial")
            open(os.path.join(path, "more"), "w").write("1"); run(path, "add", "-A"); run(path, "commit", "-qm", "second")
        s = {n: self.state(n) for n in ("new", "old")}
        self.assertEqual((s["new"]["after_skeleton"], s["new"]["not_on_github"], s["old"]["not_on_github"]), (0, False, True))

    def test_a_folder_with_no_git_is_flagged_by_age_and_an_origin_clears_it(self):
        path = self.project("bare", notes=f"# b\n\n- status: promoted\n- promoted: {(server.today() - server.dt.timedelta(days=5)).isoformat()}\n")
        self.assertTrue(self.state("bare")["not_on_github"])
        run(path, "init", "-q", "-b", "main"); run(path, "remote", "add", "origin", "https://github.com/me/bare.git")
        self.assertEqual((self.state("bare")["not_on_github"], self.state("bare")["repo"]), (False, "me/bare"))

    def test_an_inherited_git_dir_does_not_redirect_the_git_facts(self):
        """A server started from a git hook or a `git -C` alias inherits GIT_DIR: every read must still be of the project's own repo."""
        path = self.promoted(); other = os.path.join(self.tmp, "other"); os.makedirs(other); run(other, "init", "-q", "-b", "main")
        run(other, "remote", "add", "origin", "https://github.com/me/other.git")
        with mock.patch.dict(os.environ, {"GIT_DIR": os.path.join(other, ".git"), "GIT_WORK_TREE": other}):
            self.assertEqual(server.git_out(path, ["rev-parse", "--show-toplevel"]), path)
            s = server.github_state(path, fresh=True)
        self.assertEqual((s["repo"], s["after_skeleton"]), ("", 0))
        self.assertEqual(server.work_commits(path), 0)

    def test_an_inherited_git_dir_does_not_reach_gh_or_the_project_panel(self):
        """gh runs git itself (gh repo create --source adds the remote), and the panel's own git reads (branch, log): an
        inherited GIT_DIR must reach neither."""
        path = self.promoted(); other = os.path.join(self.tmp, "other"); os.makedirs(other)
        run(other, "init", "-q", "-b", "elsewhere"); run(other, "commit", "-q", "--allow-empty", "-m", "not this project")
        drop = {"GIT_DIR": os.path.join(other, ".git"), "GIT_WORK_TREE": other, "GIT_INDEX_FILE": os.path.join(other, "idx")}
        with mock.patch.dict(os.environ, drop):
            self.assertFalse(set(server.gh_env()) & set(drop))
            d = self.get("/api/project?name=demo&kind=active")
        self.assertEqual(d["branch"], "main"); self.assertEqual([c["title"] for c in d["commits"]], ["Start demo (promoted from RawNotes)"])

    def test_a_project_with_no_promoted_line_is_never_flagged_by_age(self):
        self.project("plain", notes="# plain\n\n- status: promoted\n")
        self.assertFalse(self.state("plain")["not_on_github"])


class CloseOutGate(GithubCase):
    """The GitHub gate sits in close out (close_project), for active/templates folders: maintained needs an origin;
    archive needs one only when the repo has work beyond Promote's skeleton commit; next and kill are never gated."""
    def close(self, name="demo", kind="active", outcome="archive", ok=True, **kw):
        return self.post("/api/project/close", {"name": name, "kind": kind, "outcome": outcome, **kw}, ok=ok)

    def work(self, path, n=1):
        for i in range(n):
            self.made = getattr(self, "made", 0) + 1
            with open(os.path.join(path, f"work{self.made}.txt"), "w") as fh: fh.write(str(i))
            run(path, "add", "-A"); run(path, "commit", "-qm", f"work {i}")

    def assert_refused(self, contains="Push it first", **kw):
        before = self.snapshot(); n = len(undo_list())
        err = self.close(ok=False, **kw)["error"]
        self.assertIn(contains, err)
        self.assertEqual((self.snapshot(), len(undo_list())), (before, n))  # nothing moved, nothing written, no undo step
        return err

    def test_maintained_needs_an_origin_in_active_and_templates(self):
        for dest, name in (("active", "a1"), ("templates", "t1")):
            self.promoted(name, dest)
            err = self.assert_refused(name=name, kind=dest, outcome="maintained")
            self.assertIn("maintained means it's on GitHub", err); self.assertIn(f"rawtriage project github {name}", err)

    def test_maintained_with_no_git_at_all_is_refused_too(self):
        self.project("plain", notes="# p\n\n- status: promoted\n")
        self.assert_refused(name="plain", outcome="maintained")

    def origin(self, path, name="demo", push=True):
        self.bare(name); run(path, "remote", "add", "origin", f"https://github.com/me/{name}.git")
        if push: run(path, "push", "-q", "-u", "origin", "main")

    def test_with_its_work_pushed_maintained_moves(self):
        path = self.promoted(); self.work(path); self.origin(path)
        self.assertEqual(self.close(outcome="maintained")["moved_to"], "maintained/demo")
        self.assertIn("- status: shipped", self.record("maintained", "demo"))

    def test_archive_with_only_the_skeleton_commit_moves_without_github(self):
        self.promoted()
        self.assertEqual(self.close()["moved_to"], "archive/demo")

    def test_archive_of_a_folder_with_no_git_moves_without_github(self):
        self.project("plain", notes="# p\n\n- status: promoted\n")
        self.assertEqual(self.close("plain")["moved_to"], "archive/plain")

    def test_archive_of_a_repo_with_no_commits_moves_without_github(self):
        path = self.project("empty", notes="# e\n\n- status: promoted\n"); run(path, "init", "-q", "-b", "main")
        self.assertEqual(self.close("empty")["moved_to"], "archive/empty")

    def test_archive_with_work_after_the_skeleton_needs_an_origin(self):
        for dest, name in (("active", "a1"), ("templates", "t1")):
            path = self.promoted(name, dest); self.work(path, 2)
            self.assertIn("has 2 commits that are not on GitHub", self.assert_refused(name=name, kind=dest))
            self.origin(path, name)
            self.assertEqual(self.close(name, dest)["moved_to"], f"archive/{name}")

    def test_an_origin_that_was_never_pushed_to_is_not_on_github(self):
        path = self.promoted(); self.work(path); self.origin(path, push=False)
        for outcome in ("maintained", "archive"):
            self.assertIn("nothing was ever pushed", self.assert_refused(outcome=outcome))

    def test_commits_missing_from_github_on_any_branch_are_refused(self):
        path = self.promoted(); self.work(path); self.origin(path)
        self.work(path, 2)  # two more on main, not pushed
        for outcome in ("maintained", "archive"):
            self.assertIn("2 commits on demo's local branches are not on GitHub", self.assert_refused(outcome=outcome))
        run(path, "push", "-q", "origin", "main")
        run(path, "switch", "-q", "-c", "side"); self.work(path); run(path, "switch", "-q", "main")
        self.assertIn("1 commit on demo's local branches is not on GitHub", self.assert_refused(outcome="maintained"))
        run(path, "push", "-q", "origin", "side")
        self.assertEqual(self.close(outcome="maintained")["moved_to"], "maintained/demo")

    def test_the_refusal_names_each_unpushed_branch_with_both_ways_out(self):
        path = self.promoted(); self.work(path); self.origin(path)
        run(path, "switch", "-q", "-c", "feat/merged-on-github"); self.work(path, 2); run(path, "switch", "-q", "main")
        err = self.assert_refused(outcome="maintained")
        self.assertIn("2 commits on demo's local branches are not on GitHub", err)
        self.assertIn("feat/merged-on-github (2)", err)
        self.assertIn("`git push origin refs/heads/feat/merged-on-github`", err)
        self.assertIn("`git branch -D -- feat/merged-on-github`", err)
        self.assertNotIn("git branch -D main", err)

    def test_a_tag_named_like_a_branch_does_not_break_the_gate(self):
        """--short/refname:short turn refs/heads/main into "heads/main" when a tag "main" exists too: the gate then
        couldn't count and refused a fully pushed repo. It uses full ref names for revs and lstrip=2 for display."""
        path = self.promoted(); self.work(path); self.origin(path)
        run(path, "tag", "main"); run(path, "switch", "-q", "-c", "side"); self.work(path); run(path, "tag", "side")
        run(path, "switch", "-q", "main")
        err = self.assert_refused(outcome="maintained")
        self.assertIn("side (1)", err); self.assertNotRegex(err, r"(?<!refs/)heads/"); self.assertNotIn("couldn't check", err)
        self.assertIn("`git branch -D -- side`", err); self.assertNotIn("git branch -D -- main", err)
        self.assertIn("`git push origin refs/heads/side`", err)  # plain `side` would fail: it matches the tag too
        run(path, "push", "-q", "origin", "refs/heads/side")
        self.assertEqual(self.close(outcome="maintained")["moved_to"], "maintained/demo")

    def test_the_suggested_commands_quote_the_branch_name(self):
        path = self.promoted(); self.work(path); self.origin(path)
        run(path, "switch", "-q", "-c", "x$(id)"); self.work(path); run(path, "switch", "-q", "main")
        err = self.assert_refused(outcome="maintained")
        self.assertIn("`git push origin 'refs/heads/x$(id)'`", err)
        self.assertIn("`git branch -D -- 'x$(id)'`", err)

    def test_the_suggested_commands_work_for_a_branch_starting_with_a_dash(self):
        """git won't make such a branch, but update-ref (or another tool) can: `git push origin -x` would be an option."""
        path = self.promoted(); self.work(path); self.origin(path)
        run(path, "switch", "-q", "-c", "side"); self.work(path); run(path, "update-ref", "refs/heads/-x", "HEAD")
        run(path, "switch", "-q", "main"); run(path, "branch", "-q", "-D", "side")
        err = self.assert_refused(outcome="maintained")
        self.assertIn("`git push origin refs/heads/-x`", err); self.assertIn("`git branch -D -- -x`", err)
        run(path, "branch", "-D", "--", "-x")  # the suggested delete works as written
        self.assertEqual(self.close(outcome="maintained")["moved_to"], "maintained/demo")

    def test_a_git_check_that_fails_refuses_rather_than_lets_it_through(self):
        """The unpushed count comes from git rev-list: if that fails (or times out) the gate fails closed."""
        path = self.promoted(); self.work(path); self.origin(path)
        real = server.git_out
        def broken(p, args, timeout=5):
            if args[0] == "rev-list" and "--not" in args: return None
            return real(p, args, timeout)
        with mock.patch.object(server, "git_out", broken):
            for outcome in ("maintained", "archive"):
                err = self.assert_refused(outcome=outcome); self.assertIn("couldn't check", err)

    def test_archive_of_a_repo_without_a_skeleton_commit_counts_every_commit(self):
        path = self.project("old", notes="# old\n\n- status: promoted\n")
        run(path, "init", "-q", "-b", "main"); run(path, "add", "-A"); run(path, "commit", "-qm", "initial")
        self.assertIn("has 1 commit that is not on GitHub", self.assert_refused(name="old"))

    def test_the_promoted_file_route_and_the_status_route_are_gated_too(self):
        path = self.promoted("a1"); self.work(path)
        for body in ({"path": "active/a1/NOTES.md", "action": "shipped", "outcome": "maintained"},
                     {"path": "active/a1/NOTES.md", "action": "shipped", "outcome": "archive"},
                     {"name": "a1", "kind": "active", "action": "shipped", "outcome": "maintained"}):
            before = self.snapshot()
            self.assertIn("Push it first", self.post("/api/project/status", body, ok=False)["error"])
            self.assertEqual(self.snapshot(), before)

    def test_next_round_is_not_gated(self):
        path = self.promoted(); self.work(path)
        self.close(outcome="next", done="It ships again.", due_days=7)
        self.assertTrue(os.path.isdir(path)); self.assertIn("## Rounds", self.record("active", "demo"))

    def test_kill_is_not_gated(self):
        path = self.promoted("k1"); self.work(path)
        r = self.post("/api/project/status", {"name": "k1", "kind": "active", "action": "kill", "note": "no time"})
        self.assertEqual(r["moved_to"], "archive/k1_abandoned")

    def test_experiments_and_learning_close_out_without_github(self):
        for dest in ("experiments", "learning"):
            path = self.promoted("e-" + dest, dest); self.set_promoted(path, 5); self.work(path)
            self.assertTrue(self.state("e-" + dest, dest)["not_on_github"])  # the chip stays red
            self.assertEqual(self.close("e-" + dest, dest, "maintained")["moved_to"], f"maintained/e-{dest}")

    def test_close_out_is_the_only_gate(self):
        """No second, older Ship gate is left beside close out's (it was dead code that could drift from it)."""
        self.assertFalse(hasattr(server, "require_github_to_ship"))
        import inspect
        self.assertNotIn("require_github", inspect.getsource(server.project_status))
        self.assertFalse(hasattr(server, "folder_project_status"))  # its ship/kill went to close out; what was left only refused

    def test_a_bare_folder_says_it_has_no_ship_by_to_push_and_changes_nothing(self):
        self.project("plain", notes="# p\n\n- status: promoted\n"); before = self.snapshot()
        err = self.post("/api/project/status", {"name": "plain", "kind": "active", "action": "extend", "note": "Waiting on the parts."}, ok=False)["error"]
        self.assertIn("no ship-by date", err)
        self.assertEqual(self.post("/api/project/status", {"name": "plain", "kind": "active", "action": "bogus"}, ok=False)["error"], "unknown project action")
        self.assertEqual(self.post("/api/project/status", {"name": "ghost", "kind": "active", "action": "extend"}, ok=False)["error"], "unknown project")
        self.assertEqual((self.snapshot(), undo_list()), (before, []))

    def test_the_gate_off_lets_everything_through(self):
        path = self.promoted(); self.work(path)
        with mock.patch.object(server, "SHIP_NEEDS_GITHUB", ()):
            self.assertEqual(self.close(outcome="maintained")["moved_to"], "maintained/demo")


class AllLocalBranches(GithubCase):
    """Work counts on every local branch, not just the checked-out one (and with an unborn HEAD)."""
    def side_work(self, path, n=1, back=True):
        run(path, "switch", "-q", "-c", "side")
        for i in range(n):
            with open(os.path.join(path, f"side{i}.txt"), "w") as fh: fh.write(str(i))
            run(path, "add", "-A"); run(path, "commit", "-qm", f"side {i}")
        if back: run(path, "switch", "-q", "main")

    def test_work_on_another_branch_flags_the_chip_and_gates_archive(self):
        path = self.promoted(); self.side_work(path, 2)
        s = self.state("demo"); self.assertEqual((s["after_skeleton"], s["not_on_github"]), (2, True))
        self.assertEqual(server.work_commits(path), 2)
        before = self.snapshot()
        self.assertIn("has 2 commits", self.post("/api/project/close", {"name": "demo", "kind": "active", "outcome": "archive"}, ok=False)["error"])
        self.assertEqual(self.snapshot(), before)

    def test_an_unborn_head_with_commits_on_other_branches_still_counts_them(self):
        path = self.promoted(); self.side_work(path, 1, back=False); run(path, "switch", "-q", "--orphan", "fresh")
        self.assertEqual(server.work_commits(path), 1)  # side's work commit; main has only the skeleton
        self.assertEqual(self.state("demo")["after_skeleton"], 1)

    def test_a_second_root_commit_counts_the_same_for_the_chip_and_the_gate(self):
        """An orphan branch adds a root commit of its own: the chip (after_skeleton) and the gate (work_commits) must agree
        that it is work after the skeleton."""
        path = self.promoted(); run(path, "switch", "-q", "--orphan", "other")
        with open(os.path.join(path, "o.txt"), "w") as fh: fh.write("o")
        run(path, "add", "o.txt"); run(path, "commit", "-qm", "unrelated start"); run(path, "switch", "-q", "main")
        s = self.state("demo")
        self.assertEqual((server.work_commits(path), s["after_skeleton"], s["not_on_github"]), (1, 1, True))

    def test_unpushed_counts_every_local_branch(self):
        path = self.promoted(); self.bare("demo"); run(path, "remote", "add", "origin", "https://github.com/me/demo.git")
        run(path, "push", "-q", "-u", "origin", "main"); self.side_work(path, 2)
        s = self.state("demo"); self.assertEqual((s["pushed"], s["unpushed"]), (True, 2))


class CreateRepo(GithubCase):
    def hook(self, path):
        """A pre-push hook that records the environment the push saw."""
        hooks = os.path.join(path, ".git", "myhooks"); os.makedirs(hooks)
        out = os.path.join(os.path.dirname(self.log), "push-env")
        with open(os.path.join(hooks, "pre-push"), "w") as fh: fh.write(f'#!/bin/sh\necho "ALLOW=$ALLOW_PUSH_MAIN" >> "{out}"\n')
        os.chmod(os.path.join(hooks, "pre-push"), 0o755); run(path, "config", "core.hooksPath", hooks)
        return out

    def test_creates_a_private_repo_pushes_main_and_unlocks_ship(self):
        path = self.promoted(); bare = self.bare("demo"); seen = self.hook(path)
        r = self.create("demo")
        self.assertEqual(r["repo"], "me/demo"); self.assertIn("private", r["msg"])
        calls = self.gh_calls()
        create = [c for c in calls if c.startswith("gh repo create")]
        self.assertEqual(create, [f"gh repo create me/demo --private --source {path} --remote origin"])
        self.assertFalse(any("--public" in c or "--no-verify" in c for c in calls))
        self.assertEqual(open(seen).read().strip(), "ALLOW=1")  # the push had it
        self.assertNotIn("ALLOW_PUSH_MAIN", os.environ)  # and only the push did
        self.assertEqual(run(bare, "rev-parse", "main"), run(path, "rev-parse", "HEAD"))
        self.assertEqual(run(path, "rev-parse", "--abbrev-ref", "main@{upstream}"), "origin/main")
        s = self.state("demo"); self.assertEqual((s["repo"], s["pushed"], s["not_on_github"]), ("me/demo", True, False))
        self.assertEqual(self.post("/api/project/close", {"name": "demo", "kind": "active", "outcome": "maintained"})["moved_to"], "maintained/demo")

    def test_a_tag_named_main_does_not_hide_the_main_branch(self):
        path = self.promoted(); bare = self.bare("demo"); run(path, "tag", "main")
        self.assertEqual(self.create("demo")["repo"], "me/demo")
        self.assertEqual(run(bare, "rev-parse", "refs/heads/main"), run(path, "rev-parse", "refs/heads/main"))

    def test_undo_of_the_promote_is_refused_once_the_repo_has_a_remote(self):
        path = self.promoted(); self.bare("demo"); self.create("demo")
        before = self.snapshot(); n = len(undo_list())
        err = self.post("/api/undo", {}, ok=False)["error"]
        self.assertIn("Delete the GitHub repo first", err); self.assertIn("origin", err)
        self.assertEqual((self.snapshot(), len(undo_list())), (before, n))
        self.assertTrue(os.path.isdir(os.path.join(path, ".git")))
        run(path, "remote", "remove", "origin")  # GitHub repo deleted and the remote taken out by hand: undo works again
        self.post("/api/undo", {}); self.assertFalse(os.path.exists(path))

    def test_an_undo_sent_while_the_repo_is_being_created_waits_and_is_refused(self):
        """gh repo create runs under the lock: an undo of the promote (or a close out) sent meanwhile waits for it, then
        sees the new remote and refuses, instead of deleting the folder under gh and orphaning the GitHub repo."""
        import threading, time
        path = self.promoted(); self.bare("demo"); open(os.path.join(self.stub, "slow"), "w").close()
        made = {}
        t = threading.Thread(target=lambda: made.update(r=self.call("/api/project/github", {"name": "demo", "kind": "active"}))); t.start()
        for _ in range(200):
            if any(c.startswith("gh repo create") for c in self.gh_calls()): break
            time.sleep(0.02)
        else: self.fail("gh repo create never started")
        code, r = self.call("/api/undo", {})
        t.join(30)
        self.assertEqual(made["r"][0], 200, made["r"])
        self.assertEqual(code, 400, r); self.assertIn("Delete the GitHub repo first", r["error"])
        self.assertTrue(os.path.isdir(os.path.join(path, ".git"))); self.assertEqual(run(path, "remote"), "origin")
        self.assertEqual(len(undo_list()), 1)

    def test_the_sign_in_check_runs_without_the_lock(self):
        """github_status(True) can wait on the network for a minute: holding the read lock meanwhile froze every change."""
        self.promoted(); self.bare("demo"); real = server.github_status; seen = []
        def status(refresh=False):
            seen.append(server._lock._readers); return real(refresh)
        with mock.patch.object(server, "github_status", status): self.create("demo")
        self.assertEqual(seen, [0])

    def test_refuses_off_this_machine(self):
        self.promoted(); self.bare("demo")
        p = mock.patch.object(server, "BIND_HOST", "0.0.0.0"); p.start(); self.addCleanup(p.stop)  # a phone on the LAN: its Host name is accepted, but it isn't this machine
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/project/github", method="POST", data=json.dumps({"name": "demo", "kind": "active"}).encode(),
                                     headers={"Content-Type": "application/json", "Host": f"192.168.1.5:{self.port}"})
        with self.assertRaises(urllib.error.HTTPError) as cm: urllib.request.urlopen(req)
        self.assertIn("machine running the server", json.loads(cm.exception.read())["error"])
        self.assertFalse(any(c.startswith("gh repo create") for c in self.gh_calls()))

    def refused(self, name="demo", kind="active", contains=""):
        before = self.snapshot(); err = self.create(name, kind, ok=False)["error"]
        self.assertIn(contains, err); self.assertEqual(self.snapshot(), before)
        self.assertFalse(any(c.startswith("gh repo create") for c in self.gh_calls()))
        return err

    def test_any_origin_counts_not_only_a_github_one(self):
        """The page offers Create private GitHub repo only with no origin at all (the server refuses any origin)."""
        path = self.promoted(); self.assertFalse(self.state("demo")["origin"])
        run(path, "remote", "add", "origin", "https://git.example.com/me/demo.git")
        s = self.state("demo"); self.assertEqual((s["origin"], s["repo"]), (True, ""))
        self.refused(contains="already has an origin")
        self.assertEqual(self.get("/api/project?name=demo&kind=active")["gh"]["origin"], True)
        self.project("plain", notes="# p\n\n- status: promoted\n"); self.assertFalse(self.state("plain")["origin"])

    def test_refuses_when_an_origin_already_exists(self):
        path = self.promoted(); run(path, "remote", "add", "origin", "https://github.com/me/demo.git")
        self.refused(contains="already has an origin")

    def test_refuses_a_dirty_tree(self):
        path = self.promoted(); open(os.path.join(path, "new.txt"), "w").write("x")
        self.refused(contains="isn't clean")

    def test_refuses_when_not_on_main(self):
        path = self.promoted(); run(path, "switch", "-q", "-c", "side")
        self.refused(contains="main branch")

    def test_refuses_a_folder_name_github_does_not_accept(self):
        """GitHub repo names are ASCII letters, digits, `-`, `_` and `.`: a non-ASCII folder name is refused up front."""
        path = self.project("café", notes="# c\n\n- status: promoted\n")
        run(path, "init", "-q", "-b", "main"); run(path, "add", "-A"); run(path, "commit", "-qm", "start")
        self.refused("café", contains="not a name GitHub accepts")

    def test_refuses_without_a_repo(self):
        self.project("plain", notes="# p\n\n- status: promoted\n")
        self.refused("plain", contains="no git repo")

    def test_refuses_when_gh_is_not_signed_in(self):
        self.promoted(); open(os.path.join(self.stub, "signed_out"), "w").close()
        self.refused(contains="Not signed in")

    def test_refuses_when_gh_is_missing(self):
        self.promoted()
        with mock.patch.object(server.shutil, "which", lambda n: None if n == "gh" else "/usr/bin/" + n):
            self.refused(contains="gh")

    def test_a_failing_gh_shows_its_error_and_leaves_no_origin(self):
        path = self.promoted(); open(os.path.join(self.stub, "create_fails"), "w").close()
        err = self.create("demo", ok=False)["error"]
        self.assertIn("name already exists", err); self.assertEqual(run(path, "remote"), "")

    def test_a_failing_push_says_the_repo_exists_and_how_to_finish(self):
        path = self.promoted()
        with open(self.gitconfig, "a") as fh: fh.write(f'[url "{self.tmp}/nowhere.git"]\n\tpushInsteadOf = https://github.com/me/demo.git\n')
        code, body = self.call("/api/project/github", {"name": "demo", "kind": "active"}); err = body["error"]
        self.assertEqual(code, 500)  # the repo exists now: a change that stopped part-way, not "refused, nothing changed"
        self.assertIn("Created private repo me/demo, but the push failed", err); self.assertIn("git push -u origin refs/heads/main", err)
        self.assertEqual(run(path, "remote"), "origin")
        self.assertIn("already has an origin", self.create("demo", ok=False)["error"])  # no second create
        before = self.snapshot()  # the repo exists on GitHub but holds nothing: maintained still says push it first
        err = self.post("/api/project/close", {"name": "demo", "kind": "active", "outcome": "maintained"}, ok=False)["error"]
        self.assertIn("nothing was ever pushed", err); self.assertEqual(self.snapshot(), before)
        open(os.path.join(path, "w.txt"), "w").write("w"); run(path, "add", "-A"); run(path, "commit", "-qm", "work")
        before = self.snapshot()  # and with work in it, so does archive
        err = self.post("/api/project/close", {"name": "demo", "kind": "active", "outcome": "archive"}, ok=False)["error"]
        self.assertIn("nothing was ever pushed", err); self.assertEqual(self.snapshot(), before)

    def test_only_a_board_project_folder_is_accepted(self):
        for bad in ({"name": "../x"}, {"name": "ghost"}, {"name": "demo", "kind": "nope"}):
            self.post("/api/project/github", bad, ok=False)


class Cli(GithubCase):
    def test_project_github_goes_through_the_server(self):
        import sys
        path = self.promoted(); self.bare("demo")
        cli = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", "rawtriage")
        r = subprocess.run([*cli_argv(cli), "project", "github", "demo"], capture_output=True, text=True, timeout=60, cwd=self.tmp,
                           env={**os.environ, "RAWTRIAGE_PORT": str(self.port)})
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn("Created private repo me/demo", r.stdout)
        self.assertEqual(run(path, "remote"), "origin")
        r = subprocess.run([*cli_argv(cli), "project", "github", "demo"], capture_output=True, text=True, timeout=60, cwd=self.tmp,
                           env={**os.environ, "RAWTRIAGE_PORT": str(self.port)})
        self.assertEqual(r.returncode, 1); self.assertIn("already has an origin", r.stderr + r.stdout)

    def test_a_failed_push_after_the_repo_was_made_exits_4_not_1(self):
        """Exit 1 means "refused, nothing changed"; by then the GitHub repo and the origin exist."""
        import sys
        path = self.promoted()
        with open(self.gitconfig, "a") as fh: fh.write(f'[url "{self.tmp}/nowhere.git"]\n\tpushInsteadOf = https://github.com/me/demo.git\n')
        cli = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", "rawtriage")
        r = subprocess.run([*cli_argv(cli), "project", "github", "demo"], capture_output=True, text=True, timeout=60, cwd=self.tmp,
                           env={**os.environ, "RAWTRIAGE_PORT": str(self.port)})
        self.assertEqual(r.returncode, 4, r.stderr); self.assertIn("Created private repo me/demo, but the push failed", r.stderr)
        self.assertNotIn("Nothing was changed", r.stderr); self.assertEqual(run(path, "remote"), "origin")
