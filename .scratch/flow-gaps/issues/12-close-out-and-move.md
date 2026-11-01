# 12: Close out: Next round, Maintained or Archive (and Kill → archive/_abandoned)

**What to build:** Shipping a folder project asks what happens next: **Next round** (stays, new done means and
due), **Maintained** (move to `maintained/`) or **Archive** (move to `archive/`), with a suggestion. Killing a
folder project moves it to `archive/<slug>_abandoned`. Moves run the dependency check (11) inside the change and
refuse on any blocker; to-dos are written to `NOTES.md`. Undoable; `sync-apps` re-run after. See `../spec.md`
"Close out and move (12)" and `../PROPOSAL.md` §7.

**Blocked by:** 06, 11

**Status:** done (feat/fg-11-closeout)

**Touches outside the repo:** Runs `sync-apps` (from `maintained/obsidian-sync-apps`) after a move, best effort,
which rewrites links in the Obsidian vault's `Apps/` folder. `~/Projects/active/README.md` typo fix is in 14.

- [x] Ship on a folder project returns/needs an `outcome`; suggestion is `maintained` when the folder has a GitHub origin, else `archive`; the page preselects it
- [x] Next round: writes new `due` (required), new done means (required), `extensions: 0`, `status: promoted`, removes `closed`, appends the finished round (dates, done means, shipped date) under `## Rounds`; folder stays
- [x] Maintained/Archive: status `shipped`, `closed`, then the folder moves to `maintained/<slug>` or `archive/<slug>` (`unique()` suffix if taken)
- [x] Kill on a folder project: reason required (05), status `dead`, `closed`, folder moves to `archive/<slug>_abandoned`
- [x] Any blocker from 11 → 400 listing them with fixes; nothing changed
- [x] To-dos → move proceeds; `## Moved` section in the moved `NOTES.md` lists them with exact commands; toast shows them
- [x] New Tx directory-move op; Undo moves the folder back and restores `NOTES.md`; refuses if the moved folder was changed since (any file added/edited) or the old name is taken
- [x] After a successful move or its undo, `sync-apps` runs if on PATH; failure only logged
- [x] Moved project appears in the Closed column (06) for 7 days
- [x] Symlinked folders refused as today
- [x] `rawtriage project close <name> --outcome next|maintained|archive …` once 02 lands (not blocking)
- [x] README Projects section; CHANGELOG (2.13 entry; VERSION not bumped, by instruction); tests pass
