# {{title}}: notes for agents

`{{slug}}` was promoted from RawNotes on {{today}} as an open-ended experiment (no ship-by date). Read `NOTES.md`
first: it is the original note plus the plan and what "done" means for this experiment. Keep it up to date; it is
tracked in git.

## Issue tracker

Issues and specs live as markdown files in `.scratch/`, not on GitHub:

- One feature per directory: `.scratch/<feature-slug>/`
- The spec is `.scratch/<feature-slug>/spec.md`
- One file per ticket: `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01`, never one combined file
- A `Status:` line near the top of each ticket records its state (`open`, `claimed`, `done`)
- Comments and history go at the bottom of the ticket under `## Comments`

## First build round

Promote made this folder a local git repo with one commit. It is **not on GitHub yet**. Do this as part of the
first ticket, before any other commit is pushed anywhere:

- [ ] Check the identity: `git config user.email` must end in `@users.noreply.github.com`. Never commit with a
      personal address.
- [ ] Create the GitHub repo, **private**, and push `main`:

  ```
  gh repo create {{slug}} --private --source . --remote origin
  ALLOW_PUSH_MAIN=1 git push -u origin main
  ```

  (`ALLOW_PUSH_MAIN=1` because the pre-push hook refuses `main` otherwise.)
- [ ] Fill in the `Stack`, `Run it` and `Repo` lines in `README.md`.

Never make the repo public from here. Going public is `gopublic` only (secret scan over the full history, review,
licence).
