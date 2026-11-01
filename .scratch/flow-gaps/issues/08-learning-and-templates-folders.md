# 08: Learning and templates promote to folders too

**What to build:** Promote to `learning` or `templates` creates a folder from its skeleton (with git, as in 07)
instead of a flat `<slug>.md`, and those folders appear on the board when they were promoted. Existing flat files
keep working. See `../PROPOSAL.md` §4.

**Blocked by:** 07

**Status:** open

**Touches outside the repo:** No

- [ ] `starter/skeletons/learning/` (`README.md`, `NOTES.md`, `notes/.gitkeep`, `AGENTS.md`, `CLAUDE.md`, `.gitignore`) and `starter/skeletons/templates/` (`README.md` saying what it scaffolds and how to copy it, `NOTES.md`, `template/.gitkeep`, `AGENTS.md`, `CLAUDE.md`, `.gitignore`)
- [ ] `DESTS` maps all four destinations to `folder`
- [ ] Board lists `learning/<x>` and `templates/<x>` folders only if their `NOTES.md` has a `promoted:` line (hand-made `learning/cert-study`, `templates/review-kit` stay off)
- [ ] Existing flat `learning/*.md` / `templates/*.md` with `due:` still show as today
- [ ] Ship/extend/kill work on the new folder kinds; `project_dir` accepts them
- [ ] README; CHANGELOG; VERSION bump; tests pass
