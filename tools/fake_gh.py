#!/usr/bin/env python3
"""Test-only fake `gh` (testkit.FakeGh writes the wrapper that runs it): fake_gh.py <state dir> <gh arguments...>.

<state dir>/answers.json is {"answers": [{argv | prefix | graphql, stdout, stderr, exit, once}]}. The first answer
that matches and isn't used up wins: `argv` is the whole argument list, `prefix` its start, `graphql` a substring of
the query of `gh api graphql ... -f query=<query>`; `env`, if given, {name: value} the call's environment must also have
(null: unset or empty). An answer with "once": true is used up after one call, so answers
given in order for the same call are a sequence. Every call is appended to <state dir>/calls.jsonl as
{"argv", "env", "cwd", "answered"} (answered: the index of the answer used, or null). A call nothing answers exits 1."""
import fcntl
import json
import os
import sys


def graphql_query(argv):
    if argv[:2] != ["api", "graphql"]: return None
    for i, a in enumerate(argv):
        if a.startswith("query="): return a[len("query="):]
        if a in ("-f", "-F", "--raw-field", "--field") and i + 1 < len(argv) and argv[i + 1].startswith("query="):
            return argv[i + 1][len("query="):]
    return None


def env_matches(answer):
    return all((os.environ.get(k) or None) == (v or None) for k, v in (answer.get("env") or {}).items())


def matches(answer, argv):
    if not env_matches(answer): return False
    if answer.get("argv") is not None: return argv == answer["argv"]
    if answer.get("prefix") is not None: return argv[:len(answer["prefix"])] == answer["prefix"]
    if answer.get("graphql") is not None:
        q = graphql_query(argv); return q is not None and answer["graphql"] in q
    return False


def main(state, argv):
    with open(os.path.join(state, "lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # gh runs in parallel (the git and GitHub pools): one call at a time here
        with open(os.path.join(state, "answers.json"), encoding="utf-8") as fh: answers = json.load(fh)["answers"]
        used_file = os.path.join(state, "used.json")
        try:
            with open(used_file, encoding="utf-8") as fh: used = set(json.load(fh))
        except FileNotFoundError: used = set()
        hit = next((i for i, a in enumerate(answers) if i not in used and matches(a, argv)), None)
        if hit is not None and answers[hit].get("once"):
            used.add(hit)
            with open(used_file, "w", encoding="utf-8") as fh: json.dump(sorted(used), fh)
        with open(os.path.join(state, "calls.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"argv": argv, "env": dict(os.environ), "cwd": os.getcwd(), "answered": hit}) + "\n")
    if hit is None:
        sys.stderr.write(f"fake gh: no answer for: gh {' '.join(argv)}\n"); return 1
    a = answers[hit]
    sys.stdout.write(a.get("stdout") or ""); sys.stderr.write(a.get("stderr") or "")
    return int(a.get("exit") or 0)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2:]))
