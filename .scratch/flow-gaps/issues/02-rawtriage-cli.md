# 02: `rawtriage` CLI that decides through the server

**What to build:** A stdlib client, `bin/rawtriage`, that does every triage action through the running server's
HTTP API, starts the server if it is down, and refuses (changing nothing) if it can't. It replaces the hand-made
`~/.local/bin/rawtriage`, keeping its no-argument (Claude session) and `-l` behaviour. Adds `GET /api/check` and
`POST /api/new`. See `../spec.md` "CLI (02)" and `../PROPOSAL.md` §2.

**Blocked by:** None (01 recommended first so CLI decisions are committed too)

**Status:** open

**Touches outside the repo:** Yes. `install-webapp.sh` replaces `~/.local/bin/rawtriage` with a link (backup
`rawtriage.bak.rawnotes-triage`). `~/.local/bin/rawnote` should call `rawtriage new` instead of `sed` on the template
(hand edit, listed in 14).

- [ ] `rawtriage -l [status]` prints the same columns as today, plus `stale`/`expired` flags from the server; `--json` prints the API data
- [ ] `rawtriage where` prints the notes folder and projects root
- [ ] `rawtriage check` lists notes with bad headers using the same rules as `RawNotes/.claude/hooks/check-frontmatter.sh` (shared test cases)
- [ ] `rawtriage new <slug> [thought]` creates today's note from `_template.md` through `POST /api/new`; undoable; refuses an existing name
- [ ] `keep`, `kill --reason`, `reference`, `promote --dest --done [--due-days|--due] [--slug]`, `merge … --title`, `undo` each hit the existing endpoints and print the server's message
- [ ] `promote` without `--done`, or without a due for non-experiments, exits non-zero with the server's error and changes nothing
- [ ] A CLI decision appears in `triage.log`, in the page's undo list, and in Pulse "decided"
- [ ] Server down + `rawtriage-web.service` installed → `systemctl --user start` is used (tested with a stub `systemctl`)
- [ ] Server down, no unit → `server.py --no-browser` is spawned detached and the command proceeds
- [ ] Server can't be started, or something that isn't rawnotes-triage answers on the port → exit 2, message says nothing was changed
- [ ] No args still launches the Claude triage session (prompt updated in 03)
- [ ] Installer links `~/.local/bin/rawtriage`, backs up a hand-made one, stops on any other file; `--remove` removes only its link (`test_install.py`)
- [ ] README "Commands" section; CHANGELOG; VERSION bump; tests pass
