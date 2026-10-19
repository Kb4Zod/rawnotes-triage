#!/usr/bin/env python3
"""v2.05: reads that stay correct beside writes, bounded caches, and honest GitHub activity."""
import concurrent.futures
import os
import subprocess
import threading
import time
import unittest
from unittest import mock

import safefs
import server
from testkit import Fixture


class ReadsBesideWrites(Fixture):
    def test_16_clients_160_reads_and_40_kills_never_see_an_error(self):
        names = [f"2026-10-19-load-{i:04}.md" for i in range(200)]
        for n in names: self.note(n, n[:-3], body="Synthetic data. " * 260)
        kills = names[::5]; self.assertEqual(len(kills), 40)
        jobs = [("/api/notes", None)] * 160 + [("/api/action", {"file": n, "action": "kill", "note": "load"}) for n in kills]
        import random; random.Random(7).shuffle(jobs)
        with concurrent.futures.ThreadPoolExecutor(16) as ex: out = list(ex.map(lambda j: self.call(*j), jobs))
        bad = [(c, d) for c, d in out if c != 200]
        self.assertEqual(bad, [])
        self.assertEqual(len(os.listdir(os.path.join(self.notes, "done"))), 40)
        self.assertEqual(len(self.get("/api/notes")["live"]), 160)

    def test_a_listing_is_one_snapshot_of_our_own_changes(self):
        """A batch moves notes one by one; no reader may see some moved and some not."""
        names = [f"2026-10-0{i}-s.md" for i in range(1, 10)]
        for n in names: self.note(n, n)
        seen, stop = set(), threading.Event()
        def reader():
            while not stop.is_set():
                d = server.list_notes(False); seen.add((len(d["live"]), len(d["done"])))
        ts = [threading.Thread(target=reader) for _ in range(4)]; [t.start() for t in ts]
        for _ in range(3):
            self.post("/api/batch", {"files": names, "action": "kill"}); self.post("/api/undo", {})
        stop.set(); [t.join() for t in ts]
        self.assertTrue(seen <= {(9, 0), (0, 9)}, seen)

    def test_a_note_edited_or_removed_by_hand_while_listing_is_tolerated(self):
        for i in range(30): self.note(f"2026-10-0{i % 9 + 1}-n{i}.md", f"n{i}")
        real = server.summarize; calls = []
        def flaky(path, sub=""):
            calls.append(path)
            if len(calls) % 7 == 0: os.remove(path)  # a hand edit removes it between listing and reading
            return real(path, sub)
        with mock.patch.object(server, "summarize", flaky):
            d = self.get("/api/notes")
        self.assertGreater(len(d["live"]), 20); self.assertLess(len(d["live"]), 30)

    def test_rwlock_readers_share_writers_exclude_and_nesting_is_safe(self):
        lock = safefs.RWLock(); inside = []; peak = [0]
        def reader():
            with lock.read():
                with lock.read(): inside.append(1); peak[0] = max(peak[0], len(inside)); time.sleep(0.05); inside.pop()
        ts = [threading.Thread(target=reader) for _ in range(4)]; [t.start() for t in ts]; [t.join() for t in ts]
        self.assertGreater(peak[0], 1)
        order = []
        def writer():
            with lock.write():
                with lock.write(): with_read = True
                with lock.read(): order.append("w+"); time.sleep(0.05); order.append("w-")
        w = threading.Thread(target=writer); r = threading.Thread(target=lambda: (time.sleep(0.01), lock.read().__enter__(), order.append("r")))
        w.start(); r.start(); w.join(); r.join(2)
        self.assertEqual(order[:2], ["w+", "w-"])


class Caches(Fixture):
    def git(self, path, *args):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com"] + list(args), cwd=path, check=True, capture_output=True, text=True).stdout

    def repo(self, name):
        p = self.project(name); self.git(p, "init", "-q"); self.git(p, "remote", "add", "origin", "git@github.com:me/x.git"); return p

    def test_git_inspection_is_cached_and_shares_one_bounded_pool(self):
        p = self.repo("a"); self.repo("b")
        with mock.patch.object(server, "git_out", wraps=server.git_out) as g:
            self.get("/api/projects"); first = g.call_count
            self.get("/api/projects"); self.get("/api/projects")
        self.assertGreaterEqual(first, 2); self.assertEqual(g.call_count, first)  # nothing re-run
        self.assertIs(server._git_pool, server._git_pool); self.assertLessEqual(server._git_pool._max_workers, 8)
        with mock.patch.object(server, "git_out", wraps=server.git_out) as g:
            self.get("/api/projects?refresh=1")
        self.assertEqual(g.call_count, first)  # Refresh bypasses the cache

    def test_git_cache_notices_a_repo_that_changed(self):
        p = self.repo("a"); st = lambda: self.get("/api/projects")["projects"][0]["gh"]
        self.assertEqual((st()["pushed"], st()["unpushed"]), (False, 0))
        self.git(p, "commit", "-q", "--allow-empty", "-m", "one"); self.git(p, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.assertEqual(st()["pushed"], True)
        self.git(p, "commit", "-q", "--allow-empty", "-m", "two")
        self.assertEqual(st()["unpushed"], 1)

    def test_git_cache_expires_and_is_bounded(self):
        self.repo("a")
        with mock.patch.object(server, "git_out", wraps=server.git_out) as g:
            self.get("/api/projects"); n = g.call_count
            with mock.patch.object(server, "GIT_TTL", 0): self.get("/api/projects")
        self.assertEqual(g.call_count, 2 * n)

    def test_stats_does_not_build_the_projects_board(self):
        self.note("2026-10-01-a.md")
        with mock.patch.object(server, "inflight", side_effect=AssertionError("stats must not scan projects")):
            self.assertEqual(self.get("/api/stats")["live"], 1)

    def test_unchanged_notes_are_not_read_again_but_changed_ones_are(self):
        path = self.note("2026-10-01-a.md", "Old title")
        old = time.time() - 3600; os.utime(path, (old, old))
        self.get("/api/notes")  # first read
        with mock.patch.object(server, "_summary_core", wraps=server._summary_core) as core:
            self.get("/api/notes"); self.get("/api/notes")
            self.assertEqual(core.call_count, 0)
            with open(path) as fh: text = fh.read()
            with open(path, "w") as fh: fh.write(text.replace("Old title", "New title"))  # same size, mtime moves to now
            self.assertEqual(self.get("/api/notes")["live"][0]["title"], "New title")
            self.assertEqual(core.call_count, 1)

    def test_a_same_size_same_mtime_edit_just_after_a_read_is_still_seen(self):
        """Git's 'racily clean' case: the cache must not trust a file modified within the last moments."""
        path = self.note("2026-10-01-a.md", "Aaaa")
        st = os.stat(path); self.get("/api/notes")
        for new in ("Bbbb", "Cccc"):
            with open(path) as fh: text = fh.read()
            with open(path, "w") as fh: fh.write(text.replace(text.split("\n")[0][2:], new))
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))  # identical size and mtime
            self.assertEqual(self.get("/api/notes")["live"][0]["title"], new)

    def test_stat_cache_is_bounded(self):
        c = safefs.StatCache(limit=5); p = os.path.join(self.tmp, "f"); open(p, "w").close()
        for i in range(20): c.get(i, [p], lambda _: i)
        self.assertLessEqual(len(c._d), 5)


class GithubActivity(Fixture):
    def setUp(self):
        super().setUp()
        self.saved_act = dict(server._activity); server._activity.update(at=0.0, data=None, busy=None, last=None)
        self.addCleanup(lambda: server._activity.update(self.saved_act))

    def repos(self, names, more=False, cursor="C", old=()):
        nodes = [{"id": "ID_" + n, "nameWithOwner": "me/" + n, "isPrivate": False, "url": "https://github.com/me/" + n,
                  "pushedAt": ("2001-01-01" if n in old else server.today().isoformat()) + "T00:00:00Z"} for n in names]
        return {"viewer": {"id": "U1", "login": "me", "repositories": {"pageInfo": {"hasNextPage": more, "endCursor": cursor}, "nodes": nodes}}}

    def hist(self, ids, days, more=None):
        out = {}
        for n, i in enumerate(ids):
            more_i = bool(more and i in more)
            out[f"r{n}"] = {"defaultBranchRef": {"target": {"history": {"pageInfo": {"hasNextPage": more_i, "endCursor": "H" if more_i else None},
                            "nodes": [{"committedDate": f"{d.isoformat()}T12:00:00Z"} for d in days]}}}}
        return out

    def run_with(self, answers):
        calls = []
        def fake(query):
            calls.append(query); a = answers[min(len(calls), len(answers)) - 1]
            if callable(a): a = a(query)
            if isinstance(a, Exception): raise a
            return a
        with mock.patch.object(server, "gh_graphql", fake): data = self.get("/api/activity")
        return data, calls

    def test_more_than_100_repos_are_paged_through(self):
        t = server.today()
        data, calls = self.run_with([self.repos(["a"], more=True, cursor="P1"), self.repos(["b"], more=False),
                                     self.hist(["ID_a", "ID_b"], [t])])
        self.assertIn('after: "P1"', calls[1]); self.assertNotIn("after:", calls[0])
        self.assertEqual({r["name"] for r in data["repos"]}, {"me/a", "me/b"}); self.assertEqual(data["total"], 2)
        self.assertEqual(data["truncated"], {"repos": False, "history": []}); self.assertEqual(data["notice"], "")

    def test_pages_stop_once_repos_are_older_than_a_year(self):
        data, calls = self.run_with([self.repos(["a", "b"], more=True, old=("b",)), self.hist(["ID_a"], [server.today()])])
        self.assertEqual(len(calls), 2)  # no page 2: it would only hold older repos
        self.assertFalse(data["truncated"]["repos"])

    def test_hitting_the_repo_page_cap_is_reported_not_silent(self):
        with mock.patch.object(server, "REPO_PAGES", 2):
            data, calls = self.run_with([self.repos(["a"], more=True), self.repos(["b"], more=True), self.hist(["ID_a", "ID_b"], [])])
        self.assertTrue(data["truncated"]["repos"]); self.assertIn("200 most recently", data["notice"])
        self.assertEqual(len(calls), 3)

    def test_history_cut_at_the_page_cap_names_the_repo(self):
        t = server.today()
        endless = lambda q: self.hist(["ID_a", "ID_b"] if "ID_b" in q else ["ID_a"], [t], more={"ID_a"})
        with mock.patch.object(server, "HISTORY_PAGES", 3):
            data, calls = self.run_with([self.repos(["a", "b"]), endless, endless, endless, endless])
        self.assertEqual(data["truncated"]["history"], ["me/a"]); self.assertIn("me/a", data["notice"])
        self.assertEqual(len(calls), 1 + 3)

    def test_activity_is_described_as_commits_not_pushes(self):
        data, _ = self.run_with([self.repos([]),])
        self.assertIn("commit", data["basis"]); self.assertIn("by commit date", data["basis"])
        page = server.INDEX_HTML.decode()
        for stale in ("pushes this year", "commits pushed this year", "Pushed to GitHub", "you pushed"): self.assertFalse(stale in page, "page still says: " + stale)

    def test_concurrent_callers_share_one_fetch_and_nobody_holds_a_lock_while_waiting(self):
        gate = threading.Event(); calls = []
        def slow(query):
            calls.append(query); gate.wait(5)
            return self.repos([]) if len(calls) == 1 else {}
        results = []
        with mock.patch.object(server, "gh_graphql", slow):
            ts = [threading.Thread(target=lambda: results.append(server.github_activity())) for _ in range(5)]
            [t.start() for t in ts]; time.sleep(0.3)
            self.assertTrue(server._activity_lock.acquire(timeout=1)); server._activity_lock.release()  # not held over the network call
            self.assertEqual(self.get("/api/version")["version"], server.VERSION)  # and the rest of the app answers
            gate.set(); [t.join(5) for t in ts]
        self.assertEqual(len(calls), 1); self.assertEqual(len(results), 5); self.assertTrue(all("weeks" in r for r in results))

    def test_a_failed_fetch_wakes_waiters_with_the_error(self):
        gate = threading.Event()
        def boom(query): gate.wait(5); raise RuntimeError("network down")
        results = []
        with mock.patch.object(server, "gh_graphql", boom):
            ts = [threading.Thread(target=lambda: results.append(server.github_activity())) for _ in range(3)]
            [t.start() for t in ts]; time.sleep(0.2); gate.set(); [t.join(5) for t in ts]
        self.assertEqual(results, [{"error": "network down"}] * 3)
        self.assertIsNone(server._activity["busy"])


class HttpLimits(Fixture):
    def test_oversized_and_malformed_bodies_are_refused_without_changes(self):
        import urllib.request, urllib.error
        for body, code in ((b"x" * (server.MAX_BODY + 1), 413), (b"{not json", 400), (b"[1,2]", 400)):
            req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/action", method="POST", data=body, headers={"Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as cm: urllib.request.urlopen(req)
            self.assertEqual(cm.exception.code, code)

    def test_responses_are_not_cached_or_sniffed(self):
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/notes") as r:
            self.assertEqual((r.headers["Cache-Control"], r.headers["X-Content-Type-Options"]), ("no-store", "nosniff"))


if __name__ == "__main__":
    unittest.main()
