# 01: Auto-commit RawNotes after every change

**What to build:** After any change the server makes succeeds (decide, batch, merge, restore, undo, and every later
write kind), commit exactly the RawNotes paths that change touched, with a `triage: <label>` message. A commit that
can't happen warns and never fails the change. See `../spec.md` "Auto-commit (01)" and `../PROPOSAL.md` §1.

**Blocked by:** None

**Status:** open

**Touches outside the repo:** No (code). Before merging, Hugh runs the one-time catch-up commit in PROPOSAL §1.

- [ ] A kill through `/api/action` in a fixture whose notes folder is a git repo makes one commit containing the deletion of the Inbox file and the new `done/` file, message `triage: kill “<title>”`
- [ ] An unrelated modified or staged file in RawNotes is not in that commit and is still modified/staged afterwards
- [ ] Batch, merge, restore and undo each make exactly one commit; undo's message starts `triage: undo`
- [ ] Promote commits only the RawNotes side (capture edit + move); nothing in the project folder is committed by this ticket
- [ ] Notes folder not a git repo, merge/rebase in progress, `index.lock` present, or no `user.email` → change succeeds, response `commit.ok` false with a `skipped` reason, no exception
- [ ] A pre-commit hook that exits 1 → change succeeds, `commit.ok` false, `error` names the hook; the change is still on disk and undoable
- [ ] `--no-commit` and `"autocommit": false` in config both turn it off
- [ ] Page toast shows a warning when `commit.ok` is false
- [ ] Two changes fired at once produce two commits, no `index.lock` errors (commit lock)
- [ ] README "Decisions per note" / "Undo" mention the commit; CHANGELOG entry; VERSION bump
- [ ] `python3 -W ignore::ResourceWarning -m unittest` passes
