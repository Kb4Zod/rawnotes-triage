#!/usr/bin/env python3
"""Ticket 11: the dependency check (what points at a project folder before it moves). Read-only, and every scanner
runs against fake roots: no test reads this machine's systemd units, ~/.local/bin, crontab, ~/.claude or /proc."""
import os
import subprocess
import unittest
from unittest import mock

import depcheck
import server
from testkit import Fixture


def write(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh: fh.write(text)
    if mode: os.chmod(path, mode)
    return path


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def fake_proc(root, procs):
    """A /proc-like folder: {pid: (cwd or None, [argv])}."""
    os.makedirs(root, exist_ok=True)
    for pid, (cwd, argv) in procs.items():
        d = os.path.join(root, str(pid)); os.makedirs(d)
        if cwd: os.symlink(cwd, os.path.join(d, "cwd"))
        with open(os.path.join(d, "cmdline"), "wb") as fh: fh.write(b"\0".join(a.encode() for a in argv) + b"\0")
    os.makedirs(os.path.join(root, "self"), exist_ok=True)  # non-numeric entries are not processes
    return root


class Situation(Fixture):
    """A copy of today's rawnotes-triage situation under a fake home: unit, hand-made wrapper, linked worktree, memory."""
    def setUp(self):
        super().setUp()
        self.home = os.path.join(self.tmp, "home"); projects = os.path.join(self.home, "Projects")
        server.PROJECTS = projects
        self.folder = os.path.join(projects, "active", "rawnotes-triage"); os.makedirs(self.folder)
        write(os.path.join(self.folder, "server.py"), "print('hi')\n"); write(os.path.join(self.folder, "triage.log"), "")
        write(os.path.join(self.folder, "NOTES.md"), "# rawnotes-triage\n\n- status: shipped\n")
        git(self.folder, "init", "-q", "-b", "main"); git(self.folder, "add", "-A"); git(self.folder, "commit", "-qm", "start")
        self.wt = os.path.join(projects, "active", "rawnotes-triage-flow-gaps")
        git(self.folder, "worktree", "add", "-q", "-b", "feat", self.wt)
        units = os.path.join(self.home, ".config", "systemd", "user")
        write(os.path.join(units, "rawtriage-web.service"),
              "[Service]\nExecStart=/usr/bin/python3 %h/Projects/active/rawnotes-triage/server.py --service\n")
        os.makedirs(os.path.join(units, "default.target.wants"))
        os.symlink("../rawtriage-web.service", os.path.join(units, "default.target.wants", "rawtriage-web.service"))
        write(os.path.join(units, "other.service"), "[Service]\nExecStart=%h/Projects/active/rawnotes-triage-flow-gaps/x\n")
        bindir = os.path.join(self.home, ".local", "bin")
        write(os.path.join(bindir, "rawtriage-web"), f"#!/bin/sh\nexec python3 {self.folder}/server.py --app \"$@\"\n", 0o755)
        write(os.path.join(bindir, "unrelated"), "#!/bin/sh\necho hi\n", 0o755)
        claude = os.path.join(self.home, ".claude", "projects")
        self.memory = os.path.join(claude, depcheck.claude_key(self.folder)); os.makedirs(self.memory)
        os.makedirs(os.path.join(claude, depcheck.claude_key(self.wt)))  # the sibling worktree's own memory: not ours
        self.proc = fake_proc(os.path.join(self.tmp, "proc"), {})
        self.crontab = ""
        self.sources = depcheck.Sources(self.home, proc=self.proc, crontab=lambda: self.crontab)
        p = mock.patch.object(server, "DEPS", self.sources); p.start(); self.addCleanup(p.stop)

    def test_todays_rawnotes_triage_has_three_blockers_and_two_todos(self):
        dest = os.path.join(server.PROJECTS, "maintained", "rawnotes-triage")
        r = depcheck.check(self.folder, self.sources, dest)
        self.assertEqual(sorted(b["kind"] for b in r["blockers"]), ["bin", "systemd", "worktree"], r["blockers"])
        self.assertEqual(sorted(t["kind"] for t in r["todos"]), ["agent-memory", "t3"], r["todos"])
        by = {b["kind"]: b for b in r["blockers"]}
        self.assertIn("systemctl --user disable --now rawtriage-web.service", by["systemd"]["fix"])
        self.assertTrue(by["systemd"]["where"].endswith("/rawtriage-web.service"))
        self.assertTrue(by["bin"]["where"].endswith("/.local/bin/rawtriage-web"))
        self.assertEqual(by["worktree"]["where"], self.wt)
        self.assertIn("git worktree list", by["worktree"]["detail"]); self.assertIn("worktree remove", by["worktree"]["fix"])
        self.assertIn("worktree repair", by["worktree"]["fix"])
        mem = next(t for t in r["todos"] if t["kind"] == "agent-memory")
        new = os.path.join(self.home, ".claude", "projects", depcheck.claude_key(dest))
        self.assertEqual(mem["fix"], f"mv {self.memory} {new}")
        t3 = next(t for t in r["todos"] if t["kind"] == "t3")
        self.assertEqual(t3["fix"], f"Update any T3 Code project whose folder is {self.folder} to {dest}")

    def test_the_api_reports_the_same_and_changes_nothing(self):
        before = self.snapshot()
        r = self.get("/api/project/deps?name=rawnotes-triage&kind=active")
        self.assertEqual((len(r["blockers"]), len(r["todos"])), (3, 2))
        self.assertEqual((r["suggest"], r["to"], r["dest"]), ("archive", "archive", "archive/rawnotes-triage"))  # no GitHub origin
        r = self.get("/api/project/deps?name=rawnotes-triage&kind=active&to=abandoned")
        self.assertEqual(r["dest"], "archive/rawnotes-triage_abandoned")
        self.assertIn(depcheck.claude_key(os.path.join(server.PROJECTS, "archive", "rawnotes-triage_abandoned")),
                      next(t for t in r["todos"] if t["kind"] == "agent-memory")["fix"])
        self.assertEqual(self.snapshot(), before)
        for bad in ("name=nope&kind=active", "name=rawnotes-triage&kind=active&to=elsewhere", "name=../x&kind=active",
                    "name=rawnotes-triage-flow-gaps&kind=active"):  # a worktree is not a project
            code, _ = self.call("/api/project/deps?" + bad); self.assertEqual(code, 400, bad)

    def test_a_github_origin_suggests_maintained(self):
        git(self.folder, "remote", "add", "origin", "git@github.com:me/rawnotes-triage.git")
        r = self.get("/api/project/deps?name=rawnotes-triage&kind=active")
        self.assertEqual((r["suggest"], r["dest"]), ("maintained", "maintained/rawnotes-triage"))

    def test_the_worktree_itself_is_a_blocker_when_checked(self):
        r = depcheck.check(self.wt, self.sources)
        self.assertEqual([b["kind"] for b in r["blockers"]], ["systemd", "worktree"])  # other.service runs from it
        self.assertIn("linked git worktree", r["blockers"][1]["detail"]); self.assertIn("worktree move", r["blockers"][1]["fix"])

    def test_a_removed_worktree_folder_is_only_a_prune_todo(self):
        import shutil; shutil.rmtree(self.wt)
        r = depcheck.check(self.folder, self.sources, "/new/place")
        self.assertNotIn("worktree", [b["kind"] for b in r["blockers"]])
        self.assertIn("git -C /new/place worktree prune", [t["fix"] for t in r["todos"] if t["kind"] == "worktree"])

    def test_processes_inside_the_folder_block(self):
        fake_proc(self.proc, {
            100: (self.folder, ["python3", "server.py", "--service"]),             # working folder inside
            200: ("/", ["/usr/bin/python3", f"{self.folder}/server.py"]),           # command line names it
            300: (os.path.join(self.folder, "sub (deleted)"), ["vim"]),             # a deleted working folder still counts
            400: ("/", ["python3", f"{self.wt}/server.py"]),                         # the sibling: not ours
            500: (None, ["kthread"]),                                               # unreadable cwd: skipped
        })
        r = depcheck.scan_processes(self.folder, self.proc, self.home)
        self.assertEqual([h["where"] for h in r], ["pid 100", "pid 200", "pid 300"])
        self.assertIn("python3 server.py --service", r[0]["detail"]); self.assertIn("kill 100", r[0]["fix"])
        self.assertEqual(depcheck.scan_processes(self.folder, os.path.join(self.tmp, "no-proc"), self.home), [])

    def test_the_apps_own_git_and_gh_children_are_not_blockers(self):
        fake_proc(self.proc, {
            700: ("/", ["python3", "/elsewhere/server.py"]),                         # the app itself, running elsewhere
            701: (self.folder, ["git", "push", "-u", "origin", "main"]),            # its git push, in the folder
            702: (self.folder, ["/usr/lib/git-core/git-remote-https", "origin"]),   # started by that git
            703: (self.folder, ["gh", "repo", "create"]),                         # its gh
            704: (self.folder, ["git", "status"]),                                 # your own git in a terminal: blocks
            705: (self.folder, ["python3", "app.py"]),                             # something the app started that isn't git: blocks
        })
        for pid, ppid in ((700, 1), (701, 700), (702, 701), (703, 700), (704, 900), (705, 700)):
            with open(os.path.join(self.proc, str(pid), "stat"), "w") as fh: fh.write(f"{pid} (x (y)) S {ppid} 0 0\n")
        r = depcheck.scan_processes(self.folder, self.proc, self.home, own_pid=700)
        self.assertEqual([h["where"] for h in r], ["pid 704", "pid 705"])
        self.assertEqual(len(depcheck.scan_processes(self.folder, self.proc, self.home)), 6 - 1)  # no own pid: every one in the folder
        self.assertEqual([h["where"] for h in depcheck.check(self.folder, depcheck.Sources(self.home, self.proc, lambda: "", own_pid=700))["blockers"]
                          if h["kind"] == "process"], ["pid 704", "pid 705"])
        self.assertEqual(depcheck.Sources.default().own_pid, os.getpid())

    def test_the_rawtriage_command_asking_for_the_move_is_not_a_blocker(self):
        """`rawtriage project close` run from inside the folder: the command itself is the request, not something that
        points at the folder. Its parent shell (or agent) still blocks: triage.md says not to close out from inside."""
        fake_proc(self.proc, {
            800: (self.folder, ["/usr/bin/python3", "/somewhere/bin/rawtriage", "project", "close", "rawnotes-triage", "--outcome", "archive"]),
            801: (self.folder, ["/home/x/.local/bin/rawtriage", "project", "kill", "rawnotes-triage", "--reason", "r"]),
            802: (self.folder, ["python3", "-I", "rawtriage", "project", "close", "x"]),
            803: (self.folder, ["bash"]),                                           # the shell it runs in: blocks
            804: (self.folder, ["/usr/bin/python3", f"{self.folder}/bin/rawtriage-web", "--service"]),  # the web server: blocks
            805: ("/", ["vim", f"{self.folder}/rawtriage"]),                        # editing a file called rawtriage in it: blocks
        })
        r = depcheck.scan_processes(self.folder, self.proc, self.home)
        self.assertEqual([h["where"] for h in r], ["pid 803", "pid 804", "pid 805"])

    def test_crontab_lines_block_but_comments_do_not(self):
        self.crontab = f"# {self.folder}/old\n0 * * * * {self.folder}/run.sh\n5 * * * * ~/Projects/active/rawnotes-triage/x\n0 1 * * * {self.wt}/y\n"
        r = depcheck.check(self.folder, self.sources)
        self.assertEqual([b["where"] for b in r["blockers"] if b["kind"] == "crontab"], ["crontab line 2", "crontab line 3"])

    def test_links_into_the_folder_block_with_the_new_target(self):
        bindir = self.sources.bin_dir
        os.symlink(os.path.join(self.folder, "server.py"), os.path.join(bindir, "rt-link"))
        os.symlink(os.path.join(self.wt, "server.py"), os.path.join(bindir, "rt-sibling"))
        os.symlink("../../Projects/active/rawnotes-triage/bin", os.path.join(bindir, "rt-rel"))  # relative, dangling: still counts
        write(os.path.join(bindir, "big"), "x" * (depcheck.SCRIPT_MAX + 1) + self.folder)  # a big file is a program: not read
        r = depcheck.scan_bin(self.folder, bindir, self.home, "/new/rt")
        self.assertEqual(sorted(os.path.basename(h["where"]) for h in r), ["rawtriage-web", "rt-link", "rt-rel"])
        link = next(h for h in r if h["where"].endswith("rt-link"))
        self.assertIn("ln -s /new/rt/server.py", link["fix"])

    def test_unit_drop_ins_and_home_spellings_count(self):
        units = self.sources.unit_dirs[0]
        write(os.path.join(units, "nightly.timer.d", "override.conf"), "[Timer]\n# $HOME/Projects/active/rawnotes-triage\n")
        write(os.path.join(units, "notes.txt"), self.folder)  # not a unit
        r = depcheck.scan_units(self.folder, [units], self.home)
        self.assertEqual(sorted(os.path.basename(h["where"]) for h in r), ["override.conf", "rawtriage-web.service"])
        self.assertIn("disable --now nightly.timer", next(h for h in r if h["where"].endswith("override.conf"))["fix"])

    def test_memory_for_subfolders_follows_and_siblings_do_not(self):
        os.makedirs(os.path.join(self.folder, ".claude", "worktrees", "fix-1"))
        claude = self.sources.claude_projects
        sub = depcheck.claude_key(self.folder) + "--claude-worktrees-fix-1"; os.makedirs(os.path.join(claude, sub))
        os.makedirs(os.path.join(claude, "-somewhere-else"))
        r = depcheck.scan_claude(self.folder, claude, "/home/me/Projects/archive/rt")
        self.assertEqual(sorted(os.path.basename(h["where"]) for h in r), sorted([depcheck.claude_key(self.folder), sub]))
        fixes = [h["fix"] for h in r]
        self.assertIn(f"mv {os.path.join(claude, sub)} {os.path.join(claude, '-home-me-Projects-archive-rt--claude-worktrees-fix-1')}", fixes)
        os.makedirs(os.path.join(claude, "-home-me-Projects-archive-rt"))  # the new key is already there: say merge, never clobber
        self.assertIn("merge them by hand", depcheck.scan_claude(self.folder, claude, "/home/me/Projects/archive/rt")[0]["fix"])

    def test_nothing_points_at_a_plain_folder(self):
        plain = os.path.join(server.PROJECTS, "active", "plain"); os.makedirs(plain)
        r = depcheck.check(plain, self.sources, os.path.join(server.PROJECTS, "archive", "plain"))
        self.assertEqual(r["blockers"], []); self.assertEqual([t["kind"] for t in r["todos"]], ["t3"])


class Helpers(unittest.TestCase):
    def test_claude_key_and_mentions(self):
        self.assertEqual(depcheck.claude_key("/home/me/Projects/active/rawnotes-triage"), "-home-me-Projects-active-rawnotes-triage")
        self.assertEqual(depcheck.claude_key("/h/.worktrees/rt_x"), "-h--worktrees-rt-x")
        home, p = "/home/u", "/home/u/Projects/active/x"
        for text in (p, p + "/server.py", f'"{p}"', "%h/Projects/active/x/a", "~/Projects/active/x", "${HOME}/Projects/active/x;"):
            self.assertTrue(depcheck.mentions(text, p, home), text)
        for text in (p + "-flow-gaps", p + ".bak", p + "_old/a", "/home/u/Projects/active/xy", ""):
            self.assertFalse(depcheck.mentions(text, p, home), text)

    def test_default_sources_are_this_users_home(self):
        with mock.patch.dict(os.environ, {"HOME": "/nowhere/home"}):
            s = depcheck.Sources.default()
        self.assertEqual((s.home, s.proc, s.bin_dir), ("/nowhere/home", "/proc", "/nowhere/home/.local/bin"))
        self.assertEqual(s.unit_dirs, ["/nowhere/home/.config/systemd/user"])


if __name__ == "__main__":
    unittest.main()
