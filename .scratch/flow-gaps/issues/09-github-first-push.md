# 09: First push to GitHub: flag, ship gate, optional button

**What to build:** Part A: the board flags a building project that isn't on GitHub, and Ship is refused for
`active` and `templates` projects with no GitHub origin. Part B (only if Hugh says yes to decision 4): a
"Create private GitHub repo" button that runs the two first-push commands. See `../spec.md` "GitHub (09)" and
`../PROPOSAL.md` §4.

**Blocked by:** 07

**Status:** open

**Touches outside the repo:** No (Part B creates a GitHub repo when clicked)

Part A
- [ ] Git chip shows **Not on GitHub** (red) when there's no GitHub origin and either a commit exists after the skeleton commit or `promoted:` is 2+ days ago; otherwise the existing chips
- [ ] Ship on an `active`/`templates` project with no GitHub origin → 400 "Push it first: shipped means it's on GitHub." and nothing changes
- [ ] Experiments and learning can ship without GitHub (chip still red)
- [ ] Projects promoted before this ticket (no skeleton commit) are judged by `promoted:` age only

Part B
- [ ] `POST /api/project/github {name, kind}` only from this machine (`_this_machine`), else 400
- [ ] Runs `gh repo create <gh login>/<folder> --private --source <path> --remote origin`, then `git push -u origin main` with the pre-push hook's override in that process's environment only; never `--public`; never `--no-verify`
- [ ] Refuses if an `origin` already exists, the tree is dirty, or `gh` isn't signed in; shows `gh`/`git` stderr on failure
- [ ] Button hidden on the phone layout and when an origin exists
- [ ] Tests stub `gh` and `git push`; nothing reaches GitHub

- [ ] README; CHANGELOG; VERSION bump; tests pass
