#!/usr/bin/env python3
"""Guard: the suite never touches the real ~/.config/rawnotes-triage (config.json, the saved GitHub token).

Named test_zz_ so unittest discovery (which sorts module names) runs it after every other module: it compares the
real folder with how it looked when testkit was first imported. Run on its own it still checks that the redirect is
in place."""
import os
import unittest

import server
import testkit


class RealConfigUntouched(unittest.TestCase):
    def test_config_dir_points_at_a_throwaway_folder(self):
        for d in (server.CONFIG_DIR, os.environ.get("RAWTRIAGE_CONFIG_DIR", "")):
            self.assertTrue(d.startswith(testkit.SESSION_DIR), d)
            self.assertNotEqual(os.path.realpath(d), os.path.realpath(testkit.REAL_CONFIG_DIR))

    def test_the_dependency_check_never_looks_at_this_machine(self):
        self.assertTrue(server.DEPS.home.startswith(testkit.SESSION_DIR), server.DEPS.home)
        self.assertTrue(server.DEPS.proc.startswith(testkit.SESSION_DIR), server.DEPS.proc)
        self.assertEqual(server.DEPS.crontab(), "")
        self.assertTrue(server.SYNC_APPS.startswith(testkit.SESSION_DIR))

    def test_isolated_git_never_runs_background_maintenance(self):
        """Auto gc / maintenance can drop .git/objects/maintenance.lock (and pack files) into a repo between a test's
        before and after snapshots, which made "nothing changed" checks flaky."""
        import subprocess
        testkit.isolate_git(self)
        get = lambda key: subprocess.run(["git", "config", "--global", key], capture_output=True, text=True).stdout.strip()
        self.assertEqual((get("gc.auto"), get("maintenance.auto"), get("gc.autoDetach")), ("0", "false", "false"))

    def test_the_real_config_folder_is_as_it_was_before_the_run(self):
        self.assertEqual(testkit.config_dir_state(), testkit.REAL_CONFIG_AT_START,
                         f"a test created or changed {testkit.REAL_CONFIG_DIR}; isolate it (testkit.Fixture / server.CONFIG_DIR)")


if __name__ == "__main__":
    unittest.main()
