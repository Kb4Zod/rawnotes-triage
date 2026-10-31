#!/usr/bin/env python3
"""v2.12: install-webapp.sh installs the checkout it is in (the main one from a linked worktree) and ships the wrapper.

Runs the real script against a throwaway copy of the repo and a fake $HOME, with omarchy-webapp-install, systemctl
and curl replaced by stubs that only record what they were asked. Nothing outside the temp folder is touched."""
import os
import shutil
import subprocess
import tempfile
import unittest

from testkit import isolate_git

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = ("install-webapp.sh", "server.py", "safefs.py", "notemeta.py", "depcheck.py", "index.html", "bin/rawtriage-web", "bin/rawtriage", "skills/rawnotes/SKILL.md", "systemd/rawtriage-web.service", "static/icons/icon-256.png")
STUBS = {
    "omarchy-webapp-install": 'printf "%s\\n" "$@" >"$STUBLOG/webapp"\n',
    # is-active: up once restarted; show-environment: a user session exists
    "systemctl": 'echo "$*" >>"$STUBLOG/systemctl"\ncase "$*" in *restart*) touch "$STUBLOG/up";; *is-active*) [ -e "$STUBLOG/up" ];; esac\n',
    "curl": '[ -e "$STUBLOG/up" ] && echo \'{"version": "x", "pid": 1}\'\n',
}


@unittest.skipUnless(shutil.which("git") and shutil.which("bash"), "needs git and bash")
class Installer(unittest.TestCase):
    def setUp(self):
        self.gitconfig = isolate_git(self)  # git (here and in the installer) never reads the real config or runs its hooks
        self.tmp = os.path.realpath(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = os.path.join(self.tmp, "code", "my triage")  # a space, and nowhere near ~/Projects/active
        for f in FILES:
            os.makedirs(os.path.dirname(os.path.join(self.repo, f)), exist_ok=True)
            shutil.copy2(os.path.join(HERE, f), os.path.join(self.repo, f))
        git = ["git", "-C", self.repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "core.hooksPath=/dev/null"]
        for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "x"]): subprocess.run(git + cmd, check=True)
        self.worktree = os.path.join(self.tmp, "wt")
        subprocess.run(git + ["worktree", "add", "-q", "-b", "feat", self.worktree], check=True)
        self.home = os.path.join(self.tmp, "home"); os.makedirs(self.home)
        self.stubs = os.path.join(self.tmp, "stubs"); os.makedirs(self.stubs)
        self.log = os.path.join(self.tmp, "log"); os.makedirs(self.log)
        for name, body in STUBS.items():
            p = os.path.join(self.stubs, name)
            with open(p, "w") as fh: fh.write("#!/usr/bin/env bash\n" + body)
            os.chmod(p, 0o755)
        self.bin = os.path.join(self.home, ".local", "bin", "rawtriage-web")
        self.cli = os.path.join(self.home, ".local", "bin", "rawtriage")
        self.unit = os.path.join(self.home, ".config", "systemd", "user", "rawtriage-web.service")

    def run_installer(self, *args, where=None):
        env = {"HOME": self.home, "PATH": self.stubs + ":/usr/bin:/bin", "STUBLOG": self.log, "GIT_CONFIG_GLOBAL": self.gitconfig,
               "GIT_CONFIG_NOSYSTEM": "1", "RAWTRIAGE_CONFIG_DIR": os.environ["RAWTRIAGE_CONFIG_DIR"]}
        return subprocess.run(["bash", os.path.join(where or self.repo, "install-webapp.sh"), *args],
                              env=env, capture_output=True, text=True, timeout=60)

    def webapp_args(self):
        with open(os.path.join(self.log, "webapp")) as fh: return fh.read().splitlines()

    def test_launcher_only_links_the_wrapper_into_this_checkout(self):
        r = self.run_installer("--no-service"); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(os.readlink(self.bin), os.path.join(self.repo, "bin", "rawtriage-web"))
        self.assertEqual(self.webapp_args()[2:], [os.path.join(self.repo, "static/icons/icon-256.png"), self.bin + " --app"])
        self.assertFalse(os.path.exists(self.unit))

    def test_wrapper_runs_server_py_of_its_checkout(self):
        self.run_installer("--no-service")
        r = subprocess.run([self.bin, "--help"], capture_output=True, text=True, timeout=30, cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn("--notes", r.stdout)

    def test_service_from_a_worktree_runs_the_main_checkout(self):
        r = self.run_installer(where=self.worktree); self.assertEqual(r.returncode, 0, r.stderr)
        with open(self.unit) as fh: unit = fh.read()
        self.assertIn(f'ExecStart=/usr/bin/python3 "{self.repo}/server.py" --service', unit)
        self.assertNotIn("@REPO@", unit)
        self.assertEqual(os.readlink(self.bin), os.path.join(self.repo, "bin", "rawtriage-web"))
        with open(os.path.join(self.log, "systemctl")) as fh: self.assertIn("--user restart rawtriage-web.service", fh.read())

    def test_handmade_wrapper_is_backed_up_and_replaced(self):
        os.makedirs(os.path.dirname(self.bin))
        old = '#!/usr/bin/env bash\nexec python3 "$HOME/Projects/active/rawnotes-triage/server.py" "$@"\n'
        with open(self.bin, "w") as fh: fh.write(old)
        r = self.run_installer("--no-service"); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.islink(self.bin))
        with open(self.bin + ".bak.rawnotes-triage") as fh: self.assertEqual(fh.read(), old)

    def test_someone_elses_rawtriage_web_stops_the_install(self):
        os.makedirs(os.path.dirname(self.bin))
        with open(self.bin, "w") as fh: fh.write("#!/bin/sh\necho mine\n")
        r = self.run_installer("--no-service")
        self.assertNotEqual(r.returncode, 0); self.assertIn("isn't the rawnotes-triage wrapper", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.log, "webapp")))  # checked before anything changed
        r = self.run_installer("--remove"); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("left alone", r.stdout)
        with open(self.bin) as fh: self.assertEqual(fh.read(), "#!/bin/sh\necho mine\n")

    def test_remove_takes_the_link_away(self):
        self.run_installer("--no-service")
        r = self.run_installer("--remove"); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.lexists(self.bin))

    # -- ~/.local/bin/rawtriage (2.13)
    HANDMADE_CLI = ('#!/usr/bin/env bash\n# rawtriage — review ~/Projects/RawNotes and decide each note\'s fate\n'
                    'set -euo pipefail\n\nNOTES_DIR="$HOME/Projects/RawNotes"\ncd "$NOTES_DIR"\n')

    def test_rawtriage_is_linked_to_the_cli_of_the_main_checkout(self):
        r = self.run_installer("--no-service", where=self.worktree); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(os.readlink(self.cli), os.path.join(self.repo, "bin", "rawtriage"))
        r = self.run_installer("--no-service"); self.assertEqual(r.returncode, 0, r.stderr)  # a rerun is safe
        self.assertEqual(os.readlink(self.cli), os.path.join(self.repo, "bin", "rawtriage"))
        self.assertFalse(os.path.exists(self.cli + ".bak.rawnotes-triage"))
        self.assertTrue(os.access(self.cli, os.X_OK))

    def test_handmade_rawtriage_is_backed_up_and_replaced(self):
        os.makedirs(os.path.dirname(self.cli))
        with open(self.cli, "w") as fh: fh.write(self.HANDMADE_CLI)
        r = self.run_installer("--no-service"); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.islink(self.cli))
        with open(self.cli + ".bak.rawnotes-triage") as fh: self.assertEqual(fh.read(), self.HANDMADE_CLI)

    def test_someone_elses_rawtriage_stops_the_install_before_anything_changes(self):
        os.makedirs(os.path.dirname(self.cli))
        with open(self.cli, "w") as fh: fh.write("#!/bin/sh\necho mine\n")
        r = self.run_installer("--no-service")
        self.assertNotEqual(r.returncode, 0); self.assertIn("isn't the rawtriage command", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.log, "webapp"))); self.assertFalse(os.path.lexists(self.bin))
        r = self.run_installer("--remove"); self.assertEqual(r.returncode, 0, r.stderr); self.assertIn("left alone", r.stdout)
        with open(self.cli) as fh: self.assertEqual(fh.read(), "#!/bin/sh\necho mine\n")

    def test_remove_takes_only_its_own_rawtriage_link(self):
        self.run_installer("--no-service"); self.assertTrue(os.path.islink(self.cli))
        r = self.run_installer("--remove"); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.lexists(self.cli)); self.assertFalse(os.path.lexists(self.bin))
        elsewhere = os.path.join(self.tmp, "elsewhere"); open(elsewhere, "w").close()
        os.symlink(elsewhere, self.cli)  # a link to something that isn't a bin/rawtriage
        self.assertNotEqual(self.run_installer("--no-service").returncode, 0)
        self.run_installer("--remove"); self.assertEqual(os.readlink(self.cli), elsewhere)

    def test_installed_rawtriage_runs_from_its_link(self):
        self.run_installer("--no-service")
        r = subprocess.run([self.cli, "bogus"], capture_output=True, text=True, timeout=30, cwd=self.tmp)
        self.assertEqual(r.returncode, 1); self.assertIn("rawtriage:", r.stderr)

    # -- the rawnotes skill (2.13)
    def skill_links(self):
        return [os.path.join(self.home, d, "skills", "rawnotes") for d in (".claude", ".agents", ".codex")]

    def test_skill_is_linked_into_the_three_agent_folders_from_the_main_checkout(self):
        r = self.run_installer("--no-service", where=self.worktree); self.assertEqual(r.returncode, 0, r.stderr)
        for l in self.skill_links(): self.assertEqual(os.readlink(l), os.path.join(self.repo, "skills", "rawnotes"))
        r = self.run_installer("--no-service"); self.assertEqual(r.returncode, 0, r.stderr)  # a rerun is safe
        for l in self.skill_links(): self.assertEqual(os.readlink(l), os.path.join(self.repo, "skills", "rawnotes"))
        self.assertTrue(os.path.isfile(os.path.join(self.skill_links()[0], "SKILL.md")))

    def test_something_else_called_rawnotes_stops_the_install_and_survives_remove(self):
        other = self.skill_links()[1]; os.makedirs(other)
        with open(os.path.join(other, "SKILL.md"), "w") as fh: fh.write("mine")
        r = self.run_installer("--no-service")
        self.assertNotEqual(r.returncode, 0); self.assertIn("isn't a link to the rawnotes skill", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.log, "webapp")))  # checked before anything changed
        self.assertFalse(os.path.lexists(self.skill_links()[0])); self.assertFalse(os.path.lexists(self.cli))
        r = self.run_installer("--remove"); self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(other, "SKILL.md")) as fh: self.assertEqual(fh.read(), "mine")

    def test_remove_takes_only_the_skill_links_it_made(self):
        self.run_installer("--no-service")
        keep = self.skill_links()[2]; os.remove(keep); os.makedirs(keep)  # someone replaced one link with a real folder
        r = self.run_installer("--remove"); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.lexists(self.skill_links()[0])); self.assertFalse(os.path.lexists(self.skill_links()[1]))
        self.assertTrue(os.path.isdir(keep)); self.assertIn("left alone", r.stdout)


class SkillFiles(unittest.TestCase):
    """The skill ships in a public repo and is read by every agent: keep it complete, short and free of personal paths."""
    DIR = os.path.join(HERE, "skills", "rawnotes")

    def read(self, name):
        with open(os.path.join(self.DIR, name), encoding="utf-8") as fh: return fh.read()

    def test_skill_md_names_itself_and_its_triggers(self):
        head = self.read("SKILL.md").split("---")[1]
        self.assertIn("name: rawnotes", head)
        for trigger in ('"rawnote:"', '"capture this"', '"triage"', '"review my notes"', '"what\'s open"'): self.assertIn(trigger, head)

    def test_no_absolute_paths_or_personal_data(self):
        import getpass, re
        me = re.escape(getpass.getuser())  # whoever runs the tests: their login must not appear either
        for name in os.listdir(self.DIR):
            text = self.read(name)
            self.assertIsNone(re.search(r"/home/|/Users/|~/|\bHugh\b|@\w+\.com|C:\\\\|\b" + me + r"\b", text), name)

    def test_global_rule_is_at_most_four_lines_and_says_the_three_things(self):
        rule = self.read("global-rule.md"); self.assertLessEqual(len(rule.strip().splitlines()), 4)
        for words in ("`rawnotes` skill", "`rawtriage` command", "never by editing note files", "local only", "never add a remote or push"): self.assertIn(words, rule)

    def test_capture_creates_with_the_cli_and_checks_headers(self):
        cap = self.read("capture.md")
        self.assertIn("rawtriage new", cap); self.assertIn("rawtriage check", cap)
        for step in ("Interview briefly", "Header checkpoint", "Write as you go", "Research on request", "Close out"): self.assertIn(step, cap)

    def test_triage_decides_only_through_the_cli(self):
        tri = self.read("triage.md")
        for cmd in ("rawtriage -l", "rawtriage promote", "rawtriage keep", "rawtriage kill", "rawtriage merge", "rawtriage undo", "rawtriage reference"): self.assertIn(cmd, tri)
        for rule in ("what done means", "ship-by", "`experiments`", "`[expired]`", "do not offer keep", "Never edit a note to decide it", "exits 2, stop"): self.assertIn(rule, tri)


if __name__ == "__main__":
    unittest.main()
