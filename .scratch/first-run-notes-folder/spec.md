# First run: ask where the notes folder lives

Status: implemented in 2.12 (branch feat/first-run-notes-folder)

## Problem

A new person who clones the repo and runs the server gets an empty app pointed at
`~/Projects/RawNotes`, a folder that probably doesn't exist. Nothing asks where their
notes should live or creates the structure the app expects.

## Behaviour

- On load the page asks `GET /api/setup`. If the notes folder is missing, it shows a
  non-dismissable Welcome dialog with the path prefilled (default `~/Projects/RawNotes`).
- `POST /api/setup {path}` creates the folder + `done/` + `_template.md` (from
  `starter/_template.md`, never overwriting), saves `{"notes": path}` to
  `~/.config/rawnotes-triage/config.json`, and switches the running server.
- Precedence at startup: `--notes` > saved config > `~/Projects/RawNotes`.
- Refused: relative paths, files, `/x` (parent would be `/`), requests from another
  machine, and any request once the folder exists.

## Launcher on a fresh clone (same PR)

- `install-webapp.sh` installs the checkout it is in; from a linked worktree, the main checkout.
- `bin/rawtriage-web` ships in the repo; the installer links `~/.local/bin/rawtriage-web` to it (a hand-made
  pre-2.12 wrapper is backed up and replaced; anything else stops the install). `--remove` deletes only its link.
- `systemd/rawtriage-web.service` has an `@REPO@` placeholder the installer fills in.

## Not in scope (follow-ups)

- Changing the notes folder later from the page (today: `--notes` or edit config.json).
