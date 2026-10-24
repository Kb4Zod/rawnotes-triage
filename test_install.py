#!/usr/bin/env python3
"""v2.12: install-webapp.sh installs the checkout it is in (the main one from a linked worktree) and ships the wrapper.

Runs the real script against a throwaway copy of the repo and a fake $HOME, with omarchy-webapp-install, systemctl
and curl replaced by stubs that only record what they were asked. Nothing outside the temp folder is touched."""
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = ("install-webapp.sh", "server.py", "safefs.py", "notemeta.py", "index.html", "bin/rawtriage-web", "systemd/rawtriage-web.service", "static/icons/icon-256.png")
STUBS = {
    "omarchy-webapp-install": 'printf "%s\\n" "$@" >"$STUBLOG/webapp"\n',
    # is-active: up once restarted; show-environment: a user session exists
    "systemctl": 'echo "$*" >>"$STUBLOG/systemctl"\ncase "$*" in *restart*) touch "$STUBLOG/up";; *is-active*) [ -e "$STUBLOG/up" ];; esac\n',
    "curl": '[ -e "$STUBLOG/up" ] && echo \'{"version": "x", "pid": 1}\'\n',
}


@unittest.skipUnless(shutil.which("git") and shutil.which("bash"), "needs git and bash")
class Installer(unittest.TestCase):
    def setUp(self):
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
        self.unit = os.path.join(self.home, ".config", "systemd", "user", "rawtriage-web.service")

    def run_installer(self, *args, where=None):
        env = {"HOME": self.home, "PATH": self.stubs + ":/usr/bin:/bin", "STUBLOG": self.log}
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


if __name__ == "__main__":
    unittest.main()
