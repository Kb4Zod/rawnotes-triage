#!/usr/bin/env python3
"""v2.09: the GitHub sign-in status and the saved personal access token."""
import json
import os
import stat
import unittest
from unittest import mock

import server
from testkit import Fixture

TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


class GithubSignIn(Fixture):
    def write_token(self):
        with open(server.token_path(), "w") as fh: fh.write(TOKEN)

    def stub_gh(self, answers):
        """Replace the gh process: answers maps the token in use ('' for none) to a login or an Exception."""
        calls = []
        def fake(args, timeout=25, token=None):
            token = token or server.saved_token(); calls.append((tuple(args), token))
            r = answers[token]
            if isinstance(r, Exception): raise r
            return json.dumps({"login": r})
        p = mock.patch.object(server, "_gh", fake); p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(server.shutil, "which", lambda n: "/usr/bin/gh"); p.start(); self.addCleanup(p.stop)
        for var in ("GH_TOKEN", "GITHUB_TOKEN"):
            p = mock.patch.dict(os.environ); p.start(); self.addCleanup(p.stop); os.environ.pop(var, None)
        return calls

    def test_signed_in_through_gh(self):
        self.stub_gh({"": "me"})
        self.assertEqual(self.get("/api/github"), {"state": "signed_in", "login": "me", "source": "gh"})

    def test_not_signed_in(self):
        self.stub_gh({"": RuntimeError("To get started with GitHub CLI, please run:  gh auth login")})
        d = self.get("/api/github")
        self.assertEqual((d["state"], d["login"]), ("signed_out", ""))

    def test_gh_missing_and_unreachable(self):
        self.stub_gh({"": RuntimeError("GitHub took too long to answer")})
        self.assertEqual(self.get("/api/github?refresh=1")["state"], "unavailable")
        with mock.patch.object(server.shutil, "which", lambda n: None):
            self.assertEqual(self.get("/api/github?refresh=1")["state"], "no_gh")
            os.makedirs(server.CONFIG_DIR, exist_ok=True); self.write_token()  # a saved token can still be removed
            self.assertEqual(self.get("/api/github?refresh=1")["source"], "token")
        with mock.patch.object(server, "_gh", lambda *a, **k: "[]"):  # a JSON answer that isn't an object
            self.assertEqual(self.get("/api/github?refresh=1")["state"], "unavailable")

    def test_status_is_cached_until_refresh(self):
        calls = self.stub_gh({"": "me"})
        self.get("/api/github"); self.get("/api/github"); self.assertEqual(len(calls), 1)
        self.get("/api/github?refresh=1"); self.assertEqual(len(calls), 2)

    def test_token_is_checked_saved_privately_and_used(self):
        calls = self.stub_gh({"": RuntimeError("gh auth login"), TOKEN: "tokenuser"})
        self.assertEqual(self.get("/api/github")["state"], "signed_out")
        d = self.post("/api/github/token", {"token": "  " + TOKEN + " "})
        self.assertEqual(d, {"state": "signed_in", "login": "tokenuser", "source": "token"})
        self.assertNotIn(TOKEN, json.dumps(d))
        self.assertEqual(server.saved_token(), TOKEN)
        self.assertEqual(stat.S_IMODE(os.stat(server.token_path()).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(server.CONFIG_DIR).st_mode), 0o700)
        self.assertEqual(self.get("/api/github")["login"], "tokenuser")
        self.assertEqual(calls[-1][1], TOKEN)  # later GitHub calls run as the saved token

    def test_bad_token_is_refused_and_not_saved(self):
        self.stub_gh({"": "me", TOKEN: RuntimeError("gh: Bad credentials (HTTP 401)")})
        self.assertIn("rejected", self.post("/api/github/token", {"token": TOKEN}, ok=False)["error"])
        self.assertFalse(os.path.exists(server.token_path()))
        for bad in ("", "short", "has space in it 1234567890", "x" * 300, 5, None):
            self.post("/api/github/token", {"token": bad}, ok=False)

    def test_remove_goes_back_to_gh(self):
        self.stub_gh({"": "me", TOKEN: "tokenuser"})
        self.post("/api/github/token", {"token": TOKEN})
        d = self.post("/api/github/token/remove", {})
        self.assertEqual((d["login"], d["source"]), ("me", "gh"))
        self.assertFalse(os.path.exists(server.token_path()))
        self.post("/api/github/token/remove", {})  # nothing saved: still fine

    def test_changing_sign_in_drops_what_was_fetched_as_the_old_account(self):
        self.stub_gh({"": "me", TOKEN: "tokenuser"})
        server._activity.update(at=1e12, data={"login": "me"}, last={"login": "me"}); server._viewer["login"] = "me"
        server._rest_cache["repos/me/x"] = (1e12, {})
        self.post("/api/github/token", {"token": TOKEN})
        self.assertIsNone(server._activity["data"]); self.assertEqual(server._rest_cache, {})
        self.assertEqual(server._viewer["login"], "tokenuser")

    def test_token_goes_to_gh_in_the_environment_never_the_command_line(self):
        seen = {}
        def run(cmd, **kw): seen.update(cmd=cmd, env=kw["env"]); return mock.Mock(returncode=0, stdout='{"login": "u"}', stderr="")
        with mock.patch.object(server.subprocess, "run", run): server._gh(["api", "user"], token=TOKEN)
        self.assertEqual(seen["env"]["GH_TOKEN"], TOKEN); self.assertNotIn(TOKEN, " ".join(seen["cmd"]))

    def remove_as(self, peer, host):
        """POST /api/github/token/remove as if from peer with that Host header; returns (code, body)."""
        h = server.H.__new__(server.H); h.client_address = (peer, 1)
        sent = []; h._send = lambda code, body, *a: sent.append((code, body))
        h.headers = {"Host": host, "Content-Type": "application/json", "Content-Length": "2"}
        h.rfile = mock.Mock(read=lambda n: b'{}'); h.path = "/api/github/token/remove"
        h.do_POST(); return sent[0]

    def test_only_this_machine_may_change_the_sign_in(self):
        self.stub_gh({"": "me", TOKEN: "t"})
        os.makedirs(server.CONFIG_DIR, exist_ok=True); self.write_token()
        p = mock.patch.object(server, "BIND_HOST", "0.0.0.0"); p.start(); self.addCleanup(p.stop)
        for peer, host in (("192.168.1.9", "192.168.1.5:8765"),   # a phone on the network
                           ("127.0.0.1", "evil.example:8765")):   # a page whose DNS name was rebound to this machine
            code, body = self.remove_as(peer, host)
            self.assertEqual(code, 400); self.assertIn("only be changed from the machine", body["error"])
            self.assertTrue(os.path.exists(server.token_path()))
        self.assertEqual(self.remove_as("127.0.0.1", "localhost:8765")[0], 200)  # the browser on this machine may
        self.assertFalse(os.path.exists(server.token_path()))

    def test_an_unwritable_log_does_not_hide_a_change_of_sign_in(self):
        self.stub_gh({"": "me", TOKEN: "tokenuser"})
        def full(msg): raise OSError(28, "No space left on device")
        p = mock.patch.object(server, "log", full); p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(server, "_log_warned", [1]); p.start(); self.addCleanup(p.stop)  # no stderr warning
        self.assertEqual(self.post("/api/github/token", {"token": TOKEN})["login"], "tokenuser")
        self.assertEqual(self.post("/api/github/token/remove", {})["login"], "me")

    def test_unreadable_token_file_falls_back_to_gh(self):
        os.makedirs(server.CONFIG_DIR, exist_ok=True)
        with open(server.token_path(), "wb") as fh: fh.write(b"\xff\xfe not utf-8")
        self.assertEqual(server.saved_token(), "")

    def test_an_existing_loose_config_folder_is_tightened(self):
        self.stub_gh({"": "me", TOKEN: "tokenuser"})
        os.makedirs(server.CONFIG_DIR); os.chmod(server.CONFIG_DIR, 0o755)
        self.post("/api/github/token", {"token": TOKEN})
        self.assertEqual(stat.S_IMODE(os.stat(server.CONFIG_DIR).st_mode), 0o700)

    def test_project_pull_requests_use_the_saved_token(self):
        os.makedirs(os.path.join(self.project("repo"), ".git"))
        os.makedirs(server.CONFIG_DIR, exist_ok=True); self.write_token()
        envs = {}
        def run(cmd, **kw):
            envs.setdefault(cmd[0], kw.get("env") or {})
            out = "git@github.com:me/repo.git" if cmd[:3] == ["git", "remote", "get-url"] else "[]" if cmd[0] == "gh" else ""
            return mock.Mock(returncode=0, stdout=out, stderr="")
        with mock.patch.object(server.subprocess, "run", run): server.project_detail("repo", "active")
        self.assertEqual(envs["gh"].get("GH_TOKEN"), TOKEN)
        self.assertNotIn(TOKEN, envs["git"].values())  # git never sees it


if __name__ == "__main__": unittest.main()
