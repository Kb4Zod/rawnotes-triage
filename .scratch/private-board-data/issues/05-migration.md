# Migration: on first write, and in bulk with a report

Status: open
Type: task
Blocked by: 02, 03
Spec: ../spec.md (D5)

## What
The first board action that writes to a project with no record creates the record from the legacy `NOTES.md` fields
and strips them, in one undoable transaction. `rawtriage migrate-board-data` and a Pulse link do the same for every
project at once and list what changed. The app never commits in project repos.

## Acceptance criteria
- [ ] First write migrates; undo restores the legacy `NOTES.md` and removes the record.
- [ ] Bulk migration: one undo step; report lists each project, what moved, says the old data stays in git
      history and which repos are public.
- [ ] CLI command and a Pulse page link.
