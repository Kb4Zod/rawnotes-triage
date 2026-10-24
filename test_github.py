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
        for target, val in (("_gh", fake), ):
            p = mock.patch.object(server, target, val); p.start(); self.addCleanup(p.stop)
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

    def test_only_this_machine_may_change_the_sign_in(self):
        self.stub_gh({"": "me", TOKEN: "t"})
        h = server.H.__new__(server.H); h.client_address = ("192.168.1.9", 1)
        sent = []; h._send = lambda code, body, *a: sent.append((code, body))
        h.headers = {"Host": "127.0.0.1", "Content-Type": "application/json", "Content-Length": "2"}
        h.rfile = mock.Mock(read=lambda n: b'{}'); h.path = "/api/github/token/remove"
        os.makedirs(server.CONFIG_DIR, exist_ok=True); self.write_token()
        h.do_POST()
        self.assertEqual(sent[0][0], 400); self.assertTrue(os.path.exists(server.token_path()))


if __name__ == "__main__": unittest.main()
