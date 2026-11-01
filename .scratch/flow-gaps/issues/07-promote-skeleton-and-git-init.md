# 07: Promote creates the standard skeleton and a git repo (active, experiments)

**What to build:** Tracer bullet for item 4. Promoting to `active` or `experiments` writes the category skeleton
from `starter/skeletons/<category>/`, runs `git init -b main`, checks the commit identity, and makes the first
commit, all as one undoable step. See `../spec.md` "Skeleton + git init (07, 08)" and `../PROPOSAL.md` §4.

**Blocked by:** None

**Status:** open

**Touches outside the repo:** No

- [ ] `starter/skeletons/active/` and `starter/skeletons/experiments/` exist with the files in PROPOSAL §4; placeholders `{{title}}`, `{{slug}}`, `{{done}}`, `{{due}}`, `{{today}}` are filled
- [ ] `README.md` starts with the title and `- **Status:**`, `- **Stack:**`, `- **Run it:**`, `- **Repo:**` lines (the shape `obsidian-sync-apps` and `project_detail` read)
- [ ] `NOTES.md` is today's promoted note (same content as now, `server.py:1014-1024`) plus `- github: private`
- [ ] `AGENTS.md` contains the issue-tracker convention and the "First build round" checklist with the exact `gh repo create … --private` and `git push -u origin main` commands; `CLAUDE.md` is `@AGENTS.md`
- [ ] The new folder is a git repo on `main` with one commit `Start <slug> (promoted from RawNotes)` containing every skeleton file
- [ ] Effective `user.email` not ending in `@users.noreply.github.com` (suffix configurable) → repo initialised, no commit, warning in the response; the app never sets an identity
- [ ] `git` missing → folder and files made, warning, promote still succeeds
- [ ] Undo removes the folder including `.git` when the repo still has only the skeleton commit and a clean tree
- [ ] Undo refuses ("changed since") when there is a second commit or an extra/edited file, changing nothing
- [ ] A failure after `git init` rolls everything back, `.git` included
- [ ] Merge-then-promote gets the same skeleton
- [ ] Tests use a fixture-local identity and `core.hooksPath=/dev/null`
- [ ] README Promote row updated (no longer "no git init"); CHANGELOG; VERSION bump; tests pass
