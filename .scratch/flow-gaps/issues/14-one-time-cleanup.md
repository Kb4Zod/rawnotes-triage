# 14: One-time cleanup outside the repo (by hand or agent, after the code lands)

**What to build:** Not code. A checklist Hugh (or an agent he hands it to) runs once the tickets above are merged
and the service restarted, so existing state matches the new rules. Each step lists its exact command.

**Blocked by:** 03, 12, 13 (step 1 can run today)

**Status:** open

**Touches outside the repo:** Yes, all of it: `~/Projects/RawNotes`, `~/Projects/active/README.md`,
`~/.local/bin/`, `~/.claude/projects/`, T3 projects, `~/Projects/reference/README.md`.

- [ ] 1. Commit the pending web-app moves in RawNotes (PROPOSAL §1 commands); never push
- [ ] 2. Restart on the new version from the main checkout: `cd ~/Projects/active/rawnotes-triage && git pull && ./install-webapp.sh` (also replaces the hand-made `rawtriage-web` and `rawtriage` wrappers with links, installs the skill)
- [ ] 3. Paste `skills/rawnotes/global-rule.md` into `~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, `~/.config/opencode/AGENTS.md`
- [ ] 4. RawNotes: `TRIAGE.md` → symlink to the skill's `triage.md`; `AGENTS.md` → symlink to `CLAUDE.md`; shorten `CLAUDE.md`; README rule 5 to the new limits; commit
- [ ] 5. `~/.local/bin/rawnote`: create the note with `rawtriage new` instead of `sed` on `_template.md`
- [ ] 6. `~/Projects/active/README.md`: `_abbandoned` → `_abandoned`
- [ ] 7. Close out through the board: the three note-only folders → **Archive**; rawnotes-triage → **Next round** (new done means + due), not moved
- [ ] 8. Orphans already there: move the agent memory folder an earlier archive move left behind to its new key (`mv ~/.claude/projects/<old key> ~/.claude/projects/<new key>`); repoint the T3 project that still names a folder that no longer exists
- [ ] 9. Backfill the 21 unindexed reference notes with the Unindexed list (13)
- [ ] 10. Remove this planning worktree when done: `git -C ~/Projects/active/rawnotes-triage worktree remove ~/Projects/active/rawnotes-triage-flow-gaps`
- [ ] 11. Look at the tracked `NOTES.md` in each public repo: keep, trim or untrack
