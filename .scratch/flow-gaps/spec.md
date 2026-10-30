# Spec: Flow gaps (capture → triage → project → closed)

Status: draft, waiting for Hugh's approval of the breakdown (see `PROPOSAL.md`, "Decisions Hugh still needs to make").
The recommended answer to every open decision is assumed below; a different answer changes the named ticket only.

## Problem Statement

A 2026-10-24 flow review (a RawNotes note) found that the system leaks at
every hand-off. Web-app decisions are never committed to the RawNotes git repo. The three ways to triage (web app,
`rawtriage` CLI session, T3 "triage") behave differently: only the web app logs, enforces done means and a ship-by
date. Keep costs nothing and `cooking` never goes stale. Promote stops at an empty folder with no git. The ship
clock can be pushed forever with one click and no reason. Closed projects never leave `active/`, and moving them by
hand orphans systemd units, agent memory dirs and T3 projects. Filing a reference note never updates the index (21
of 37 reference notes are missing from it). Capture and triage only work in sessions started inside `RawNotes/`.

## Solution

The server becomes the only thing that changes notes and project status. A small CLI (`bin/rawtriage`) and a
`rawnotes` skill route every agent through it, so all three doors share one lock, one undo list, one log, one set of
rules, and one auto-commit. On top of that the server gains: Inbox limits with a keep counter, a reasoned and capped
ship-clock extension, a standard git-initialised skeleton at promote, a GitHub ship gate, a closed date with a
7-day Closed column, a close-out step that moves projects to `maintained/` or `archive/` after a dependency check,
and automatic `reference/README.md` index lines.

## User Stories

1. As Hugh, I want every decision the app makes to be committed in RawNotes git, so that I have history and no pile of uncommitted moves.
2. As Hugh, I want the auto-commit to include only the files that decision touched, so that my unrelated edits are never swept into it.
3. As Hugh, I want a failed or blocked commit (gitleaks, merge in progress) to warn me but keep the decision, so that git trouble never loses work.
4. As Hugh, I want Undo to make its own commit, so that history is never rewritten.
5. As Hugh, I want to turn auto-commit off with a flag or config, so that I can try the app on a copy.
6. As an agent in any repo, I want a `rawtriage` command that lists, keeps, kills, files, promotes, merges and undoes through the server, so that my decisions match the web app exactly.
7. As an agent, I want promote to refuse without done means and (except experiments) a ship-by date, so that I must ask Hugh for both.
8. As an agent, I want the CLI to start the server when it is down and to fail without changing anything if it can't, so that I never edit notes by hand.
9. As Hugh, I want CLI decisions to appear in the page's Undo and in Pulse, so that every door counts.
10. As Hugh, I want one `rawnotes` skill available to Claude, Codex and OpenCode in every repo, so that "rawnote: …" and "triage" work anywhere.
11. As Hugh, I want the capture and triage instructions to exist in one place, so that RawNotes `CLAUDE.md`, `AGENTS.md` and `TRIAGE.md` can't drift.
12. As Hugh, I want open notes stale after 14 days and cooking notes stale 7 days after the last Keep, so that nothing simmers unseen.
13. As Hugh, I want a note older than 30 days, or kept 3 times, to lose the Keep option, so that it gets a real decision.
14. As Hugh, I want each row to show how many times it was kept and when it expires, so that I see the pressure coming.
15. As Hugh, I want the weekly nudge to use the same rules as the page, so that the numbers agree.
16. As Hugh, I want "+7 days" to require a reason that is saved in the project, so that pushing the date is a conscious choice.
17. As Hugh, I want at most two extensions per project, then only Ship or Kill, so that the clock means something.
18. As Hugh, I want Kill on a project to ask why, so that the archive explains itself.
19. As Hugh, I want Promote to create a standard folder for each category (active, experiments, learning, templates), git-initialised with a first commit, so that building can start at once.
20. As Hugh, I want the first commit to use my GitHub noreply email and never my personal address, so that nothing private leaks.
21. As Hugh, I want new repos to start private and become public only through `gopublic`, so that nothing goes public by accident.
22. As Hugh, I want a project that is building but not on GitHub to be flagged, and Ship refused for active and template projects until it is pushed, so that the first push happens in the first build round.
23. As Hugh, I want (optionally) a "Create private GitHub repo" button, so that I can do the first push from the board.
24. As Hugh, I want every close to record a closed date, and the Closed column to show only the last 7 days with a "Show all" link, so that the board stays short without hiding history.
25. As Hugh, I want shipping to ask "Next round, Maintained or Archive?" with a sensible suggestion, so that finished one-offs and live tools each land in the right place.
26. As Hugh, I want killed projects moved to `archive/<slug>_abandoned`, so that the archive shows what was abandoned.
27. As Hugh, I want a move refused when a systemd unit, running process, worktree, script or cron entry points at the folder, with the command to fix each, so that moving never silently breaks a service.
28. As Hugh, I want the follow-ups the app can't do itself (agent memory dir, T3 project) listed with exact commands and saved in `NOTES.md`, so that nothing is orphaned.
29. As Hugh, I want a move to be undoable and the Obsidian Apps links refreshed afterwards, so that mistakes are cheap and Obsidian stays correct.
30. As Hugh, I want git worktrees left off the Projects board, so that a feature branch checkout isn't mistaken for a project.
31. As Hugh, I want Reference to add a line to the right section of `reference/README.md` in the same undoable step, so that the index stays complete.
32. As Hugh, I want a list of reference notes missing from the index with an "Add to index" action, so that I can backfill the 21 missing ones.

## Implementation Decisions

### Shared

- Every new write goes through `mutation()` / `Tx` (`server.py:901-915`): checked first, all-or-nothing, one undo
  step, logged after the undo entry exists. New write kinds (git init, folder move) get recorded Tx ops with
  conflict-checked inverses.
- New header keys, all in the `- key: value` header block read by `notemeta.field`/`set_field`: `kept`, `keeps`,
  `restored` (notes); `extensions`, `closed`, `github` (projects). Add them to `notemeta.SHOWN_KEYS`.
- New tunables in `config.json` (read with the existing `saved_*`/`save_config` helpers, `server.py:502-526`):
  `autocommit`, `stale_open_days` (14), `stale_cooking_days` (7), `inbox_limit_days` (30), `max_keeps` (3),
  `max_extensions` (2), `closed_window_days` (7).
- Routes that start external side effects outside the notes/projects roots (GitHub repo creation) require
  `_this_machine()` (`server.py:1302-1306`). Moves and git commits stay inside the roots and follow the normal guards.
- Versioning: each ticket bumps `VERSION` by 0.01 and adds a CHANGELOG entry at merge time.

### Auto-commit (01)

- After `mutation()` succeeds and the lock is released, if `NOTES` is a git work tree and autocommit is on: collect
  the Tx op paths under `NOTES` (both sides of moves), then `git -C NOTES add -A -- <paths>` and
  `git -C NOTES commit -m "triage: <label>" -m "<paths>\n\nvia rawnotes-triage <VERSION>" -- <paths>`.
- Serialised by a module-level commit lock; 20 s timeout; never `--no-verify`.
- Skip (with reason) when: not a repo, `MERGE_HEAD`/`rebase-*` present, `index.lock` present, no `user.email`.
- The API response gains `commit: {"ok": bool, "sha"?: str, "skipped"?: str, "error"?: str}`; the page toast shows
  a warning when not ok. A failed commit never fails the request.
- `--no-commit` CLI flag on `server.py`; `autocommit: false` in config.

### CLI (02)

- `bin/rawtriage`: Python stdlib (`urllib`), followed back to its checkout like `bin/rawtriage-web`. Talks to
  `http://127.0.0.1:<port>` (port from `RAWTRIAGE_PORT` or 8765) with `Content-Type: application/json`.
- Keeps today's behaviour for no args (launch Claude triage session) and `-l [status]` (now from `/api/notes`).
- Adds `where`, `check`, `new`, `keep`, `kill`, `reference`, `promote`, `merge`, `undo`, `project …`; `--json` on all.
- New read endpoint `GET /api/check` (header problems, same rules as `RawNotes/.claude/hooks/check-frontmatter.sh`),
  and `POST /api/new` (create today's note from `_template.md`, undoable).
- Server down: `systemctl --user start rawtriage-web` if the unit exists, else spawn `server.py --no-browser`
  detached; wait ≤5 s for `/api/version` with `app == "rawnotes-triage"`; else exit 2, nothing changed.
- `install-webapp.sh` links `~/.local/bin/rawtriage` to it, backing up a hand-made one
  (`rawtriage.bak.rawnotes-triage`), same rules as for `rawtriage-web` (`install-webapp.sh:48-52`, `:133-134`).

### Skill (03)

- `skills/rawnotes/{SKILL.md,capture.md,triage.md,global-rule.md}` in the repo. Path-free; uses `rawtriage where`.
- Triage instructions: list with `rawtriage -l`, one note at a time, decisions only via the CLI; promote asks
  destination, done means, ship-by (not experiments); keep is refused when expired; summary at the end.
- Installer links the skill into `~/.claude/skills/`, `~/.agents/skills/`, `~/.codex/skills/` from the main checkout.

### Inbox limits (04)

- `summarize()` (`server.py:85-89`) computes `stale`, `expired`, `expires_in`, `keeps` from date, `kept`,
  `restored`, `keeps` and status. Keep refuses when expired (400, fixed message); batch keep refuses all and names them.
- Keep writes `kept` and `keeps`; Restore writes `restored`.
- Page: Stale chip covers open+cooking, new Expired chip, row badges, Keep replaced by a message when expired, Pulse
  "expired" count.

### Ship clock (05)

- `_status_edit` extend (`server.py:1155-1163`): requires `note` ≥10 chars, `days` 1–7, `extensions` <
  `max_extensions`; writes `extensions`, `due`, and a `## Plan` line. Kill requires a reason (page and CLI).
- Page: extension form; "No more extensions" state; "Decide" badge when overdue with none left.

### Closed date (06)

- Ship, kill and close-out write `closed`. `inflight()` and `project_folders()` return `closed` (fallback
  `shipped`, then the `**Killed YYYY-MM-DD:**` Outcome date). For the Closed column only, also scan `maintained/`
  and `archive/` folders and files closed within the window.
- Page: Closed column filtered to the window, header "Closed (last 7 days)", "Show all (N)" toggle.

### Skeleton + git init (07, 08)

- `starter/skeletons/{active,experiments,learning,templates}/` with `{{title}}`, `{{slug}}`, `{{done}}`, `{{due}}`,
  `{{today}}` placeholders (plain `str.replace`, no template engine).
- `DESTS` (`server.py:56-59`): all four become `folder`. `PROJECT_KINDS` (`server.py:248`) gains `learning` and
  `templates`, but folders there show on the board only if their `NOTES.md` has a `promoted:` line.
- New Tx op `gitinit(path)`: runs `git init -b main`, checks `user.email` ends with `@users.noreply.github.com`
  (configurable suffix), commits the skeleton. Inverse: remove `.git` only if `HEAD` is still that commit and
  `git status --porcelain` is empty; else refuse like other conflicts. No git on PATH → folder made, warning.

### GitHub (09)

- `github_state()` (`server.py:329-344`) adds `commits_after_skeleton` and `promoted` age; chip **Not on GitHub**
  when no origin and (commits after skeleton > 0 or promoted ≥ 2 days ago).
- Ship refused for `active`/`templates` with no GitHub origin.
- Part B (optional): `POST /api/project/github` (this machine only) runs
  `gh repo create <login>/<slug> --private --source <path> --remote origin` then
  `git push -u origin main` with the pre-push hook's override in its environment only. Never public.

### Worktrees off the board (10)

- `project_folders()` skips a folder whose `.git` is a file containing `gitdir:` (a linked worktree).

### Dependency check (11)

- `GET /api/project/deps?name=&kind=` returns `{blockers: [...], todos: [...]}`, each `{kind, where, detail, fix}`.
  Read-only. Checks listed in PROPOSAL §7 step 3. Same function used by the move in 12.

### Close out and move (12)

- `POST /api/project/close {name, kind, outcome: "next"|"maintained"|"archive", ...}` and kill → archive with
  `_abandoned`. Runs the dependency check inside the mutation; any blocker → 400 with the list, nothing changed.
- New Tx op for a directory move (`os.rename`), with an inverse that checks the folder, not a file
  (`_plan_inverse` today checks with `os.path.isfile`, `server.py:858`, `:868`).
- "Next round": new `due`, done means, `extensions: 0`, status `promoted`, old round appended to `## Rounds`.
- After the mutation and its commit: run `sync-apps` if on PATH, best effort, output to stderr only.
- Destination name taken → `unique()` suffix (`server.py:782-787`).

### Reference index (13)

- In `_act` reference branch (`server.py:1005-1031`): optional `section` and `blurb`; edit `reference/README.md`
  with a pure function `index_insert(text, section, line) -> text | None` (None = no `## Index`, or link already
  present). Tx write, so it's undone with the note.
- `GET /api/reference/unindexed`; Archive page lists them with Add to index (`POST /api/reference/index`).

## Testing Decisions

- Test external behaviour through the HTTP API with the existing `testkit.Fixture` (temp RawNotes + projects
  root, never the real notes). Pure helpers (`index_insert`, stale/expiry maths, dependency scanners with injected
  roots) get direct unit tests.
- Git tests create a throwaway repo in the fixture with a local `user.email` and `core.hooksPath=/dev/null`
  so the global gitleaks hook does not run in tests; one test asserts a failing pre-commit hook leaves the
  change in place and returns `commit.ok == false`.
- Dependency-check tests inject fake roots for systemd units, `~/.local/bin`, `~/.claude/projects` and a fake
  `/proc` reader; they never read the real home folder.
- CLI tests start the fixture server and run `bin/rawtriage` with `RAWTRIAGE_PORT`; the auto-start path is
  tested with a stub `systemctl` on PATH.
- Installer changes extend `test_install.py` (fake `$HOME`).
- Browser checks for new forms/chips extend `test_browser.py` where cheap; otherwise verified by hand against the
  user stories.
- `python3 -W ignore::ResourceWarning -m unittest` must pass after every ticket.

## Out of Scope

- Adopting the 8 existing hand-made folders onto the clock (item 6, skipped).
- Persistent undo across restarts (`.scratch/persistent-undo/`).
- Pushing RawNotes anywhere, or any off-machine backup.
- Making a repo public from the app (stays `gopublic`).
- Editing T3 Code's project registry from the app (the skill may do it with T3's own tools).
- Moving `~/.claude/projects/*` memory dirs from the app (listed as commands only).
- Regenerating the whole reference index from frontmatter.

## Further Notes

- Files outside this repo touched by tickets 02, 03, 04, 12 and 14 are marked in each ticket under
  **Touches outside the repo**.
- `rawnotes-triage` itself is the main dependency-check case: its unit, wrapper, log, memory dir, T3 project and
  worktrees all key on `active/rawnotes-triage`. Recommended outcome: Next round, not a move.
