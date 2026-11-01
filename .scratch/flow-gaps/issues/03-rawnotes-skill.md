# 03: `rawnotes` skill, one source of truth, installed for every agent

**What to build:** Move the capture and triage workflows into `skills/rawnotes/` in this repo, rewrite triage so
every decision goes through `rawtriage` (02), install the skill for Claude, Codex and OpenCode from the main
checkout, and make RawNotes' own instruction files point at it. See `../PROPOSAL.md` §0 and §2.

**Blocked by:** 02

**Status:** open

**Touches outside the repo:** Yes.
- `~/.claude/skills/rawnotes`, `~/.agents/skills/rawnotes`, `~/.codex/skills/rawnotes` (links, via installer)
- `~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, `~/.config/opencode/AGENTS.md` (paste `global-rule.md`; by hand)
- `~/Projects/RawNotes/TRIAGE.md` → symlink to the skill's `triage.md`; `RawNotes/CLAUDE.md` shortened to a pointer;
  `RawNotes/AGENTS.md` → symlink to `CLAUDE.md`; commit in RawNotes (local only, never push)
- `~/.local/bin/rawtriage` no-arg prompt now says "use the rawnotes skill" (lives in `bin/rawtriage` after 02)

- [ ] `skills/rawnotes/SKILL.md` has `name: rawnotes` and a description that triggers on "rawnote:", "capture this", "triage", "review my notes", "what's open"
- [ ] `capture.md` carries today's capture steps 1-6 from `RawNotes/CLAUDE.md`, but creates the note with `rawtriage new` and runs `rawtriage check` at the header checkpoint
- [ ] `triage.md`: backlog from `rawtriage -l`; one note at a time; decisions only through `rawtriage …`; promote asks destination, done means and ship-by (not experiments); expired notes offer no Keep; never edit a note to decide it; if the CLI exits 2, stop and tell Hugh
- [ ] No absolute paths or personal data in the skill files (it ships in a public repo); paths come from `rawtriage where`
- [ ] `global-rule.md` is at most 4 lines and says: use the skill, decide only via `rawtriage`, RawNotes is local only
- [ ] `install-webapp.sh` links the skill into the three skill folders from the main checkout (never a worktree); re-run is safe; `--remove` removes only its links; `test_install.py` covers it
- [ ] From a thread in another repo, "rawnote: test" creates a note in RawNotes and "triage" lists the backlog (manual check, noted in the PR)
- [ ] README documents the skill; CHANGELOG; VERSION bump
