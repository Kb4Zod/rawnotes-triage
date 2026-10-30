# 11: Dependency check before a project folder moves (read-only)

**What to build:** `GET /api/project/deps?name=&kind=` reports what points at a project folder: blockers
(systemd user units, running processes, git worktrees, `~/.local/bin` links/scripts, crontab) and to-dos (agent
memory dir under `~/.claude/projects`, T3 projects), each with a fix command. Shown in the project panel. Read-only.
See `../spec.md` "Dependency check (11)" and `../PROPOSAL.md` §7 step 3.

**Blocked by:** None

**Status:** open

**Touches outside the repo:** Reads only: `~/.config/systemd/user/`, `~/.local/bin/`, `~/.claude/projects/`,
`/proc`, `crontab -l`. Writes nothing.

- [ ] Each scanner is a function taking its root as an argument (home, `/proc`), so tests use fake trees
- [ ] A unit file mentioning the absolute folder path (or `%h/<path under home>`) → blocker with `systemctl --user disable --now <unit>` and "edit/reinstall the unit" as fix
- [ ] A process whose cwd or argv is inside the folder → blocker naming pid and command
- [ ] The folder has linked worktrees, or is one → blocker with `git worktree list` output and `git worktree remove`/`repair` hint
- [ ] A link in `~/.local/bin` resolving into the folder, or a script under 64 KB containing the path → blocker
- [ ] A crontab line containing the path → blocker
- [ ] `~/.claude/projects/<abs path with / → ->` (and subfolder variants) → to-do with the exact `mv` command for a given destination
- [ ] T3 → always one to-do: "Update any T3 Code project whose folder is <path>"
- [ ] Against a copy of today's `rawnotes-triage` situation (unit + wrapper + worktree + memory dir) it reports 3 blockers and 2 to-dos
- [ ] The panel shows "What points here" for the selected folder project
- [ ] CHANGELOG; VERSION bump; tests pass
