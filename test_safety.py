#!/usr/bin/env python3
"""v2.05: where the app may write, what /api/open may launch, and what --restart may stop."""
import http.server
import os
import signal
import subprocess
import sys
import threading
import unittest
from unittest import mock

import safefs
import server
from testkit import Fixture, WINDOWS, link


class Containment(Fixture):
    def setUp(self):
        super().setUp()
        self.outside = os.path.join(self.tmp, "..", os.path.basename(self.tmp) + "-outside")
        self.outside = os.path.realpath(self.outside); os.makedirs(self.outside)
        self.addCleanup(__import__("shutil").rmtree, self.outside, ignore_errors=True)
        with open(os.path.join(self.outside, "NOTES.md"), "w") as fh: fh.write("# Out\n\n- status: promoted\n- due: 2026-12-01\n\n- definition of done: Safe.\n")
        os.makedirs(os.path.join(self.tmp, "active"))
        self.link = os.path.join(self.tmp, "active", "linked"); link(self.outside, self.link)
        self.before_outside = self.snapshot(self.outside)

    def assertOutsideUntouched(self):
        self.assertEqual(self.snapshot(self.outside), self.before_outside)
        self.assertEqual(sorted(os.listdir(self.outside)), ["NOTES.md"])

    def test_every_project_write_refuses_a_symlinked_folder(self):
        for body in ({"name": "linked", "action": "shipped"}, {"name": "linked", "action": "kill", "note": "x"}):
            r = self.post("/api/project/status", body, ok=False); self.assertIn("symlink", r["error"])
        r = self.post("/api/done", {"name": "linked", "text": "Hacked", "expected": "Safe."}, ok=False); self.assertIn("symlink", r["error"])
        self.assertOutsideUntouched(); self.assertEqual(server._undo, [])

    def test_symlinked_project_is_still_listed_and_readable(self):
        p = next(x for x in self.get("/api/projects")["projects"] if x["name"] == "linked")
        self.assertEqual((p["abs_path"], p["linked"], p["done_defined"]), (self.link, True, True))
        d = self.get("/api/project?name=linked")
        self.assertEqual((d["abs_path"], d["linked"], d["done"]["text"]), (self.link, True, "Safe."))

    def test_symlinked_project_file_inside_a_real_folder_is_refused(self):
        real = self.project("real"); link(os.path.join(self.outside, "NOTES.md"), os.path.join(real, "NOTES.md"))
        self.post("/api/project/status", {"name": "real", "action": "shipped"}, ok=False)
        self.post("/api/done", {"name": "real", "text": "Hacked", "expected": "Safe."}, ok=False)
        self.assertOutsideUntouched()

    def test_symlinked_active_folder_blocks_promotion_and_rolls_back(self):
        real_active = os.path.join(self.tmp, "active"); moved = os.path.join(self.outside, "elsewhere"); os.makedirs(moved)
        os.rename(real_active, moved + "/active-real"); link(moved, real_active)
        self.note("2026-10-01-a.md", "A"); before = self.snapshot()
        r = self.post("/api/action", {"file": "2026-10-01-a.md", "action": "promote", "dest": "active", "note": "Ships.", "due_days": 3}, ok=False)
        self.assertIn("symlink", r["error"])
        self.assertEqual(self.snapshot(), before); self.assertEqual(server._undo, [])
        self.assertEqual(os.listdir(moved), ["active-real"])

    def test_symlinked_notes_cannot_be_decided_or_listed_when_they_point_outside(self):
        link(os.path.join(self.outside, "NOTES.md"), os.path.join(self.notes, "2026-10-01-link.md"))
        self.assertEqual([n["file"] for n in self.get("/api/notes")["live"]], [])  # not read, not shown
        self.post("/api/action", {"file": "2026-10-01-link.md", "action": "kill"}, ok=False)
        self.post("/api/batch", {"files": ["2026-10-01-link.md"], "action": "kill"}, ok=False)
        self.assertEqual(self.get_err("/api/note?f=2026-10-01-link.md")[0], 400)
        self.assertOutsideUntouched()

    def test_symlinked_note_to_another_note_inside_is_readable_but_not_writable(self):
        self.note("2026-10-01-real.md", "Real"); link("2026-10-01-real.md", os.path.join(self.notes, "2026-10-02-alias.md"))
        self.assertEqual(self.get("/api/note?f=2026-10-02-alias.md")["text"][:6], "# Real")
        self.post("/api/action", {"file": "2026-10-02-alias.md", "action": "kill"}, ok=False)

    def test_symlinked_done_folder_is_refused(self):
        self.note("2026-10-01-a.md", "A"); link(self.outside, os.path.join(self.notes, "done"))
        self.post("/api/action", {"file": "2026-10-01-a.md", "action": "kill"}, ok=False)
        self.assertOutsideUntouched(); self.assertTrue(os.path.exists(os.path.join(self.notes, "2026-10-01-a.md")))

    def get_err(self, path):
        code, data = self.call(path); return code, data.get("error")

    def test_ensure_inside_rules(self):
        root = os.path.join(self.tmp, "root"); os.makedirs(root + "/a")
        link(self.outside, root + "/out")
        ok = lambda p: safefs.ensure_inside([root], p); n = os.path.normpath  # `\` on Windows
        self.assertEqual(ok(root + "/a/new.md"), n(root + "/a/new.md"))
        self.assertEqual(ok(root + "/a/../a/x.md"), n(root + "/a/x.md"))
        for bad in (root + "/out/x.md", root + "/out", root + "/../x", self.tmp, "/etc/passwd", root + "/a/../../x"):
            with self.assertRaises(ValueError, msg=bad): ok(bad)
        link_root = os.path.join(self.tmp, "linkroot"); link(root, link_root)
        self.assertEqual(safefs.ensure_inside([link_root], link_root + "/a/x.md"), n(link_root + "/a/x.md"))  # the root itself may be a link


REAL_OPEN = server.open_path


class OpenEndpoint(Fixture):
    def setUp(self):
        super().setUp()
        self.opened = []
        p = mock.patch.object(server, "open_path", side_effect=self.opened.append); p.start(); self.addCleanup(p.stop)
        self.project("demo", notes="# D\n\n- status: promoted\n- due: 2026-12-01\n")
        self.project("spike", kind="experiments")

    def test_opens_a_listed_project_folder_and_a_listed_project_file(self):
        self.post("/api/open", {"name": "demo", "kind": "active"})
        self.post("/api/open", {"name": "spike", "kind": "experiments"})
        self.post("/api/open", {"path": "active/demo/NOTES.md"})
        self.assertEqual(self.opened, [os.path.join(self.tmp, "active", "demo"), os.path.join(self.tmp, "experiments", "spike"),
                                       os.path.join(self.tmp, "active", "demo", "NOTES.md")])

    def test_refuses_urls_arbitrary_paths_and_unknown_things(self):
        bad = [{"path": "https://evil.example/x"}, {"path": "file:///etc/passwd"}, {"path": "/etc/passwd"}, {"path": "../RawNotes/x.md"},
               {"path": "active/demo/../../../etc/passwd"}, {"path": "active/demo/NOTES.md/../../x"}, {"path": "-n"}, {"path": 7}, {"path": ["a"]},
               {"name": "../demo", "kind": "active"}, {"name": "demo", "kind": "learning"}, {"name": "nope"}, {"name": "https://evil.example", "kind": "active"},
               {"name": ".hidden"}, {"name": "demo/x"}, {}, {"name": None}]
        for b in bad: self.post("/api/open", b, ok=False)
        self.assertEqual(self.opened, [])

    def test_refuses_a_symlinked_project_folder(self):
        link(self.tmp, os.path.join(self.tmp, "active", "ln"))
        self.post("/api/open", {"name": "ln", "kind": "active"}, ok=False); self.assertEqual(self.opened, [])

    @unittest.skipIf(WINDOWS, "xdg-open is the Linux opener; Windows uses os.startfile")
    def test_the_launcher_command_is_one_absolute_operand_without_a_shell(self):
        weird = "/tmp/x y;rm -rf $(id) -n"
        self.assertEqual(server.open_argv(weird), ["xdg-open", weird])
        for bad in ("-n", "--", "--help", "relative/dir", ""):
            with self.assertRaises(ValueError, msg=bad): server.open_argv(bad)
        proc = mock.MagicMock(); proc.wait.return_value = 0
        with mock.patch.object(subprocess, "Popen", return_value=proc) as popen:
            REAL_OPEN(weird)
        args, kw = popen.call_args
        self.assertEqual(args[0], ["xdg-open", weird]); self.assertFalse(kw.get("shell"))

    @unittest.skipIf(WINDOWS, "a #!/bin/sh stub can't run on Windows")
    def test_an_opener_that_fails_at_once_is_reported_not_swallowed(self):
        stub = os.path.join(self.tmp, "failing-opener"); fh = open(stub, "w"); fh.write("#!/bin/sh\necho 'no handler' >&2\nexit 3\n"); fh.close(); os.chmod(stub, 0o755)
        with mock.patch.object(server, "open_argv", return_value=[stub, "/tmp"]):
            with self.assertRaises(OSError) as cm: REAL_OPEN("/tmp")
        self.assertIn("status 3", str(cm.exception)); self.assertIn("no handler", str(cm.exception))

    def test_a_failed_open_names_the_opener(self):
        with mock.patch.object(server, "open_path", side_effect=OSError("it exited with status 3")):
            self.assertIn(f"couldn't launch {server.OPENER}", self.post("/api/open", {"name": "demo", "kind": "active"}, ok=False)["error"])

    @unittest.skipUnless(WINDOWS, "the Windows opener")
    def test_windows_hands_one_absolute_path_to_the_shell_and_reports_a_failure(self):
        with mock.patch.object(os, "startfile") as startfile: REAL_OPEN(self.tmp)
        startfile.assert_called_once_with(self.tmp)
        for bad in ("-n", "relative\\dir", ""):
            with self.assertRaises(ValueError, msg=bad): REAL_OPEN(bad)
        with mock.patch.object(os, "startfile", side_effect=OSError("No application is associated")):
            with self.assertRaises(OSError): REAL_OPEN(self.tmp)


@unittest.skipUnless(os.path.exists("/usr/bin/xdg-open"), "the installed xdg-open is the contract")
class XdgOpenContract(Fixture):
    """Run the real /usr/bin/xdg-open on the exact command line the app builds. Safe: a clean environment with no display
    and no desktop, and a stub `run-mailcap` first on PATH that records its arguments and succeeds, so xdg-open stops
    there; nothing is ever opened. This is the check that caught `--` (rejected by the installed xdg-open as an option)."""
    def run_opener(self, argv):
        stubs = os.path.join(self.tmp, "stubs"); os.makedirs(stubs, exist_ok=True); calls = os.path.join(self.tmp, "calls")
        stub = os.path.join(stubs, "run-mailcap")
        with open(stub, "w") as fh: fh.write(f'#!/bin/sh\nprintf "%s\\n" "$@" >> "{calls}"\nexit 0\n')
        os.chmod(stub, 0o755)
        env = {"PATH": stubs + ":/usr/bin:/bin", "HOME": self.tmp}
        r = subprocess.run(["/usr/bin/xdg-open"] + argv[1:], env=env, capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
        got = open(calls).read().splitlines() if os.path.exists(calls) else []
        return r, got

    def test_the_command_the_app_builds_is_accepted_and_reaches_the_handler_with_the_exact_path(self):
        d = os.path.join(self.tmp, "active", "x y;rm -rf -n"); os.makedirs(d)
        r, got = self.run_opener(server.open_argv(d))
        self.assertEqual((r.returncode, r.stderr), (0, "")); self.assertEqual(got, ["--action=view", d])

    def test_the_old_double_dash_form_is_what_the_installed_opener_refuses(self):
        r, got = self.run_opener(["xdg-open", "--", self.tmp])
        self.assertEqual(r.returncode, 1); self.assertIn("unexpected option '--'", r.stderr); self.assertEqual(got, [])


class Restart(Fixture):
    def serve(self, handler_cls):
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close); self.addCleanup(httpd.shutdown)
        return httpd.server_address[1]

    def handler(self, status, body, ctype="text/plain"):
        class Hn(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(status); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            def log_message(self, *a): pass
        return Hn

    def test_a_foreign_service_answering_404_is_not_an_old_triage(self):
        port = self.serve(self.handler(404, b"nginx says no"))
        self.assertTrue(server.running_server(port)["foreign"])
        port = self.serve(self.handler(401, b"<title>Some Router</title>", "text/html"))
        self.assertTrue(server.running_server(port)["foreign"])

    def test_json_that_is_not_ours_is_foreign(self):
        port = self.serve(self.handler(200, b'{"hello": 1}', "application/json"))
        self.assertTrue(server.running_server(port)["foreign"])

    def test_an_old_triage_without_api_version_is_still_recognised(self):
        page = "<!doctype html><title>RawNotes · Triage 1.04</title>".encode()
        class Old(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/api/version": self.send_response(404); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"{}")
                else: self.send_response(200); self.send_header("Content-Length", str(len(page))); self.end_headers(); self.wfile.write(page)
            def log_message(self, *a): pass
        info = server.running_server(self.serve(Old))
        self.assertEqual((info["version"], info["foreign"]), (None, False))

    def test_this_server_is_recognised(self):
        info = server.running_server(self.port)
        self.assertEqual((info["version"], info["foreign"], info["pid"]), (server.VERSION, False, os.getpid()))

    def test_stop_server_never_signals_a_foreign_service(self):
        with mock.patch.object(os, "kill") as kill, mock.patch.object(server, "port_pid", return_value=4242):
            with self.assertRaises(SystemExit) as cm: server.stop_server({"version": None, "pid": None, "foreign": True}, 8765)
        self.assertIn("isn't rawnotes-triage", str(cm.exception)); kill.assert_not_called()

    def test_stop_server_refuses_a_pid_that_is_not_a_triage_server(self):
        sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]); self.addCleanup(sleeper.kill)
        self.assertFalse(server.is_triage_process(sleeper.pid))
        for info in ({"version": None, "pid": sleeper.pid, "foreign": False}, {"version": None, "pid": None, "foreign": False}):
            with mock.patch.object(os, "kill") as kill, mock.patch.object(server, "port_pid", return_value=sleeper.pid):
                with self.assertRaises(SystemExit) as cm: server.stop_server(info, 8765)
            self.assertIn("doesn't look like", str(cm.exception)); kill.assert_not_called()
        self.assertIsNone(sleeper.poll())  # and we really didn't touch it

    def test_stop_server_refuses_when_the_reported_pid_does_not_own_the_port(self):
        with mock.patch.object(os, "kill") as kill, mock.patch.object(server, "port_pid", return_value=777):
            with self.assertRaises(SystemExit): server.stop_server({"version": "2.04", "pid": 1, "foreign": False}, 8765)
        kill.assert_not_called()

    def test_stop_server_signals_a_real_triage_process_with_term_only(self):
        d = os.path.join(self.tmp, "app"); os.makedirs(d)
        with open(os.path.join(d, "server.py"), "w", encoding="utf-8") as fh: fh.write('"""rawnotes-triage — local website"""\nimport time; time.sleep(60)\n')
        child = subprocess.Popen([sys.executable, "server.py"], cwd=d); self.addCleanup(child.kill)
        for _ in range(50):
            if server.is_triage_process(child.pid): break
            __import__("time").sleep(0.05)
        self.assertTrue(server.is_triage_process(child.pid))
        with mock.patch.object(os, "kill") as kill, mock.patch.object(server, "port_pid", return_value=child.pid), \
             mock.patch.object(server, "running_server", return_value=None):
            self.assertEqual(server.stop_server({"version": "2.04", "pid": child.pid, "foreign": False}, 8765), child.pid)
        kill.assert_called_once_with(child.pid, signal.SIGTERM)
        self.assertIsNone(child.poll())  # the mock means no real signal went out


if __name__ == "__main__":
    unittest.main()
