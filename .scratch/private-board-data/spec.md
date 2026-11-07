# Spec: Keep board data out of project repos

Status: approved 2026-11-06. Hugh asked for this to be built. The recommended answer is assumed for each decision
below; if one turns out not to hold while building, stop and ask Hugh before changing it.

## Problem Statement

Every project folder's `NOTES.md` is tracked in that project's own git repo. The app writes private board data
into it: status, promoted/shipped/closed dates, the ship-by date, extension counts and reasons, kill reasons,
`## Rounds`, `## Moved`, `github: private`, and (at Promote) a full copy of the original RawNotes capture. Any repo
can become public later, or be shared while private. When it does, all of that goes with it, including the full
git history of `NOTES.md`. v2.14 already had to scrub home-folder paths out of `## Moved` for this reason.

Making `NOTES.md` local-only (ignored by git) was considered and rejected. Agents in T3 worktrees would lose it,
because a worktree is a fresh checkout and every starter `AGENTS.md` says "Read `NOTES.md` first". It would also
lose the backup and the history.

## Solution

Split the data by audience:

- **Project-facing, safe to publish, stays in the project's `NOTES.md` (tracked):** the plan and definition of
  done, the first step, and anything the user writes there by hand. This is what agents building the project need.
- **Board-private, moves to a per-project record in the RawNotes folder:** every field and section the app writes
  for its own bookkeeping, plus the original capture. RawNotes is already private, and the app auto-commits it on
  every change, so the record gets a backup, history and undo.

The app reads board data from the private record. During migration it falls back to the legacy fields in
`NOTES.md`.

## User Stories

1. As Hugh, I want a project's status, dates, extension and kill reasons, rounds and move notes kept out of the
   project's repo, so that making the repo public (or sharing it) never exposes them.
2. As Hugh, I want my original capture to stay in RawNotes, not copied into the project, so that my unedited first
   thoughts are never published with the code.
3. As an agent working in a worktree, I want `NOTES.md` to still hold the plan and definition of done, so that I know
   what I'm building.
4. As Hugh, I want board actions (Mark shipped, Kill, +7 days, Next round, close-out) to leave the project's working
   tree untouched, so that they never leave a change to commit and never block the "clean tree" checks.
5. As Hugh, I want the private record committed in RawNotes git, so that I have a backup and history.
6. As Hugh, I want every board action that writes the record to stay undoable, as it is today.
7. As Hugh, I want existing projects to keep working without me doing anything, and a one-step migration that moves
   their board fields out of `NOTES.md`, so that I can clean them up when I choose.
8. As Hugh, I want a move (close-out to `maintained/` or `archive/`, or its undo) to keep the record attached to
   the project.

## Implementation Decisions

D1. **Where the record lives.** `RawNotes/projects/<kind>/<slug>.md` (recommended), a markdown file with the same
    `- field: value` header style the app already parses, plus the sections (`## Rounds`, `## Moved`,
    `## Capture`, extension and kill reasons). It is written through `Tx`, so undo, the lock and auto-commit work
    unchanged. A move renames the record in the same transaction, and undo renames it back.
    Rejected alternatives: a single JSON file (merge conflicts, harder to read by hand), or `.git/` inside the
    project (not backed up, and lost on re-clone).

D2. **What stays in `NOTES.md`.** The title, `## Plan` (definition of done, first step, check-in line, without
    dates), and any text the user added. Promote writes this trimmed version. Agents also need the ship-by date, so
    the skeleton `AGENTS.md` keeps getting `{{due}}`, as it does today. The date is no more private than the plan.

D3. **The original capture.** It stays only in RawNotes (`done/<file>`, as today), and the record links to it.
    `NOTES.md` gets no copy. Agents that need it can run `rawtriage brief <slug>`, which prints the capture and
    the plan via the server (no paths). Recommended: include `rawtriage brief` in this PR; if it grows, split it
    into a follow-up ticket.

D4. **Which projects are on the board.** `learning/` and `templates/` folders are on the board only if a record
    exists, or `NOTES.md` has a legacy `promoted:` line.

D5. **Legacy fallback and migration.** If a project has no record, the app reads the legacy fields from
    `NOTES.md`, exactly as today. The first board action that writes to such a project creates the record from
    those fields and strips them from `NOTES.md`, in one undoable transaction. A migration step (`rawtriage
    migrate-board-data`, plus a Pulse/board link) does the same for every project at once, and lists what it
    changed. The app never commits in project repos; the stripped `NOTES.md` is left for Hugh to commit. The old
    data stays in git history; the migration report says so, and says which repos are public, using the existing
    GitHub status.

D6. **Projects that aren't promoted** (folders under `active/` or `experiments/` made by hand, like this repo).
    Mark shipped and Kill create a record and write nothing into the project. Today they create `NOTES.md` if it
    is missing.

D7. **The GitHub gate and `github: private`** move to the record. The gate's checks don't change.

D8. **Off-machine (phone).** Nothing changes. The record lives on the server's machine, like everything else.

## Testing Decisions

- Every board action writes only under RawNotes and leaves the project tree byte-identical (assert with
  `git status --porcelain` and file hashes).
- Promote: `NOTES.md` contains the plan and no capture text, status, dates or `github:`; the record contains all of
  them; the first commit in the new repo contains no private field.
- Undo of each action restores both the record and any legacy `NOTES.md` it stripped.
- A close-out move, and its undo, rename the record.
- Legacy projects, with no record, show the same status, due date and extensions as before. The first write
  migrates them, and so does the bulk migration. Both are undoable.
- `on_board` for `learning/` and `templates/` covers a record only, a legacy `promoted:` only, and neither.
- Auto-commit picks up the record paths.
- The isolation test still holds: no test touches the real RawNotes folder or the real config.

## Out of Scope

- Rewriting git history in existing project repos. The report says how, if Hugh wants to.
- Making repos public (`gopublic`).
- This repo's own `NOTES.md`: once its data has been migrated into a record, Hugh can delete the file and remove
  the `/NOTES.md` line from `.git/info/exclude`.

## Tickets

Split into `issues/NN-<slug>.md` per `docs/agents/issue-tracker.md` before building:

1. Record store: path scheme, read/write through `Tx`, auto-commit paths, rename on move (D1).
2. Read path: status, due date, extensions, `on_board`, the GitHub gate and project facts read the record first
   and fall back to `NOTES.md` (D4, D5, D7).
3. Write path: ship, kill, extend, Next round, close-out and the definition-of-done edit write the record. The
   definition of done itself stays in `NOTES.md` (D2, D6).
4. Promote: trimmed `NOTES.md` and skeleton, capture kept only in RawNotes, record created (D2, D3).
5. Migration: migrate on first write plus bulk migration (CLI and page), with a report (D5).
6. `rawtriage brief <slug>` (D3).
7. Docs: README, USER-GUIDE, CHANGELOG, `skills/rawnotes/triage.md`, and the version bump.
