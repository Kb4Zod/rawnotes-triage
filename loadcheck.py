#!/usr/bin/env python3
"""Load check for rawnotes-triage: starts THIS checkout's server on a disposable notes folder (never your real one),
fires concurrent reads and then reads mixed with writes, and prints one JSON line per scenario.

    python3 loadcheck.py                 # 100 and 1000 notes, 1/8/24 clients, then 16 clients of reads + kills
    python3 loadcheck.py --sizes 100 --mixed-rounds 3

It uses a private port, 20 empty local git repos and no GitHub sign-in (GH_* variables are blanked), so nothing
leaves the machine. Exit status is 1 if any request failed. Bounded: a few thousand local requests."""
import argparse, collections, concurrent.futures, json, os, pathlib, random, statistics, subprocess, sys, tempfile, time, urllib.request

HERE = pathlib.Path(__file__).resolve().parent


def start(root, port):
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "GH_CONFIG_DIR": str(root / "gh"), "GH_TOKEN": "", "GITHUB_TOKEN": ""}
    p = subprocess.Popen([sys.executable, str(HERE / "server.py"), "--notes", str(root / "RawNotes"), "--log", str(root / "actions.log"),
                          "--port", str(port), "--no-browser"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try: urllib.request.urlopen(f"http://127.0.0.1:{port}/api/version", timeout=1).close(); return p
        except Exception: time.sleep(0.1)
    p.terminate(); raise SystemExit("server did not start")


def req(base, path, body=None):
    t = time.perf_counter()
    try:
        data = None if body is None else json.dumps(body).encode()
        q = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(q, timeout=30) as f: f.read(); code = f.status
        return (time.perf_counter() - t) * 1000, code, ""
    except Exception as e:
        detail = ""
        if hasattr(e, "read"):
            try: detail = json.loads(e.read()).get("error", "")
            except Exception: pass
        return (time.perf_counter() - t) * 1000, getattr(e, "code", 0), detail or str(e)


def pct(lat, q): return round(lat[int(q * (len(lat) - 1))], 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="+", default=[100, 1000]); ap.add_argument("--clients", type=int, nargs="+", default=[1, 8, 24])
    ap.add_argument("--requests", type=int, default=120); ap.add_argument("--mixed-rounds", type=int, default=3); ap.add_argument("--port", type=int, default=18766)
    a = ap.parse_args()
    root = pathlib.Path(tempfile.mkdtemp(prefix="rawtriage-load-")); notes = root / "RawNotes"; (notes / "done").mkdir(parents=True)
    for i in range(20):
        p = root / "active" / f"project-{i}"; p.mkdir(parents=True)
        (p / "NOTES.md").write_text("# Project\n\n- status: promoted\n- due: 2026-10-25\n\n- definition of done: Test complete\n")
        subprocess.run(["git", "init", "-q", str(p)], check=True)
    proc = start(root, a.port); base = f"http://127.0.0.1:{a.port}"; failed = 0
    try:
        for size in a.sizes:
            for i in range(size):
                f = notes / f"2026-10-19-load-{i:04}.md"
                if not f.exists(): f.write_text(f"# Load {i}\n\n- date: 2026-10-19\n- status: open\n- type: research\n\n## Notes\n\n" + "Synthetic data. " * 270)
            for c in a.clients:
                paths = ["/api/notes", "/api/stats", "/api/projects"]; start_t = time.perf_counter()
                with concurrent.futures.ThreadPoolExecutor(c) as ex: out = list(ex.map(lambda i: req(base, paths[i % 3]), range(a.requests)))
                lat = sorted(x[0] for x in out); counts = dict(collections.Counter(x[1] for x in out)); failed += sum(v for k, v in counts.items() if k != 200)
                print(json.dumps({"notes": size, "clients": c, "requests": a.requests, "seconds": round(time.perf_counter() - start_t, 2),
                                  "p50_ms": pct(lat, .5), "p95_ms": pct(lat, .95), "max_ms": round(max(lat), 1), "status": counts}), flush=True)
        # reads beside writes: 160 listings + 40 distinct kills, 16 clients, several rounds on fresh notes
        for r in range(a.mixed_rounds):
            names = [f"2026-10-19-mix{r}-{i:04}.md" for i in range(200)]
            for n in names: (notes / n).write_text(f"# Mixed {n}\n\n- date: 2026-10-19\n- status: open\n- type: research\n\n## Notes\n\n" + "Synthetic data. " * 270)
            jobs = [("/api/notes", None)] * 160 + [("/api/action", {"file": n, "action": "kill", "note": "load check"}) for n in names[::5]]
            random.Random(r).shuffle(jobs)
            before = len(list((notes / "done").glob(f"*mix{r}-*.md")))
            with concurrent.futures.ThreadPoolExecutor(16) as ex: out = list(ex.map(lambda j: req(base, *j), jobs))
            counts = dict(collections.Counter(x[1] for x in out)); errs = sorted({x[2] for x in out if x[2]})
            archived = len(list((notes / "done").glob(f"*mix{r}-*.md"))) - before; failed += sum(v for k, v in counts.items() if k != 200) + (archived != 40)
            print(json.dumps({"mixed_round": r, "requests": len(jobs), "status": counts, "errors": errs[:3], "archived": archived}), flush=True)
    finally:
        proc.terminate(); proc.wait(timeout=10)
    print(json.dumps({"failed_requests_or_checks": failed, "scratch": str(root)}))
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
