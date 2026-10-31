#!/usr/bin/env python3
"""v2.13 (ticket 09): the "Not on GitHub" flag, the Ship gate for active/templates, and the Create private GitHub repo
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
from testkit import Fixture

GATED = ("active", "templates")
STUB = r'''#!/bin/sh
echo "gh $*" >> "$STUB_LOG"
case "$1 $2" in
  "api user") [ -f "$STUB_DIR/signed_out" ] && { echo "To get started with GitHub CLI, please run:  gh auth login" >&2; exit 4; }
              echo '{"login":"me"}' ;;
  "repo create") [ -f "$STUB_DIR/create_fails" ] && { echo "GraphQL: name already exists on this account" >&2; exit 1; }
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

    def set_promoted(self, path, days_ago, commit=False):
        f = os.path.join(path, "NOTES.md"); text = open(f).read()
        old = server.field(text, "promoted"); new = (server.today() - server.dt.timedelta(days=days_ago)).isoformat()
        open(f, "w").write(text.replace(f"promoted: {old}", f"promoted: {new}", 1))
        if commit: run(path, "commit", "-qam", "back-date")

    def state(self, name, kind="active"):
        return next(p for p in self.get("/api/projects?refresh=1")["projects"] if p["name"] == name and p["kind"] == kind)["gh"]

    def ship(self, name, kind="active", ok=True): return self.post("/api/project/status", {"name": name, "kind": kind, "action": "shipped"}, ok=ok)
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

    def test_a_project_with_no_promoted_line_is_never_flagged_by_age(self):
        self.project("plain", notes="# plain\n\n- status: promoted\n")
        self.assertFalse(self.state("plain")["not_on_github"])


class ShipGate(GithubCase):
    def test_active_and_templates_cannot_ship_without_an_origin_and_nothing_changes(self):
        for dest, name in (("active", "a1"), ("templates", "t1")):
            self.promoted(name, dest)
            before = self.snapshot(); n = len(server._undo)
            err = self.ship(name, dest, ok=False)["error"]
            self.assertEqual(err, "Push it first: shipped means it's on GitHub.")
            self.assertEqual((self.snapshot(), len(server._undo)), (before, n))

    def test_the_gate_applies_to_the_promoted_file_route_too(self):
        self.promoted("a1")
        err = self.post("/api/project/status", {"path": "active/a1/NOTES.md", "action": "shipped"}, ok=False)["error"]
        self.assertIn("Push it first", err)

    def test_experiments_and_learning_ship_without_github(self):
        for dest in ("experiments", "learning"):
            path = self.promoted("e-" + dest, dest); self.set_promoted(path, 5, commit=True)
            self.assertTrue(self.state("e-" + dest, dest)["not_on_github"])  # the chip stays red
            self.ship("e-" + dest, dest)

    def test_with_an_origin_ship_works(self):
        path = self.promoted(); run(path, "remote", "add", "origin", "https://github.com/me/demo.git")
        self.ship("demo")
        self.assertIn("- status: shipped", open(os.path.join(path, "NOTES.md")).read())

    def test_kill_and_extend_are_not_gated(self):
        self.promoted("k1"); self.post("/api/project/status", {"name": "k1", "kind": "active", "action": "kill", "note": "no time"})


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
        self.ship("demo")

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

    def test_refuses_when_an_origin_already_exists(self):
        path = self.promoted(); run(path, "remote", "add", "origin", "https://github.com/me/demo.git")
        self.refused(contains="already has an origin")

    def test_refuses_a_dirty_tree(self):
        path = self.promoted(); open(os.path.join(path, "new.txt"), "w").write("x")
        self.refused(contains="isn't clean")

    def test_refuses_when_not_on_main(self):
        path = self.promoted(); run(path, "switch", "-q", "-c", "side")
        self.refused(contains="main branch")

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
        err = self.create("demo", ok=False)["error"]
        self.assertIn("Created private repo me/demo, but the push failed", err); self.assertIn("git push -u origin main", err)
        self.assertEqual(run(path, "remote"), "origin")
        self.assertIn("already has an origin", self.create("demo", ok=False)["error"])  # no second create

    def test_only_a_board_project_folder_is_accepted(self):
        for bad in ({"name": "../x"}, {"name": "ghost"}, {"name": "demo", "kind": "nope"}):
            self.post("/api/project/github", bad, ok=False)


class Cli(GithubCase):
    def test_project_github_goes_through_the_server(self):
        import sys
        path = self.promoted(); self.bare("demo")
        cli = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", "rawtriage")
        r = subprocess.run([sys.executable, cli, "project", "github", "demo"], capture_output=True, text=True, timeout=60, cwd=self.tmp,
                           env={**os.environ, "RAWTRIAGE_PORT": str(self.port)})
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn("Created private repo me/demo", r.stdout)
        self.assertEqual(run(path, "remote"), "origin")
        r = subprocess.run([sys.executable, cli, "project", "github", "demo"], capture_output=True, text=True, timeout=60, cwd=self.tmp,
                           env={**os.environ, "RAWTRIAGE_PORT": str(self.port)})
        self.assertEqual(r.returncode, 1); self.assertIn("already has an origin", r.stderr + r.stdout)
