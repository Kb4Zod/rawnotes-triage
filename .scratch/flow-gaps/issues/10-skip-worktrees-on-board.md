# 10: Keep git worktrees off the Projects board

**What to build:** A linked git worktree inside `active/` or `experiments/` (its `.git` is a file with
`gitdir:`) is not a project; the board skips it. Today `active/rawnotes-triage-flow-gaps` shows as a hand-made
project. See `../PROPOSAL.md` §6.

**Blocked by:** None

**Status:** open

**Touches outside the repo:** No (optional, separate: `obsidian-sync-apps/bin/sync-apps` could skip worktrees the
same way; different repo, not part of this ticket)

- [ ] `project_folders()` omits folders whose `.git` is a regular file starting with `gitdir:`
- [ ] A normal repo, a folder with no git, and a symlinked folder are listed as before
- [ ] `/api/project` and `/api/open` refuse a worktree folder like any unknown project
- [ ] CHANGELOG; VERSION bump; tests pass
