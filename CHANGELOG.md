# Changelog

The version shows in the page heading ("RawNotes Triage version X.XX") and
in the startup line. Bump `VERSION` in `server.py` and add an entry here
with every change. Versions 1.00-1.03 were numbered after the fact from git history.

## 1.13 — 2026-10-18
- In-app repo viewer, so private repos can be reviewed without a GitHub login in the browser (or on the
  phone). "Review repository" in a project's detail, and every repo chip in the activity strip, open a
  read-only view: README, file browser (folders, text files, markdown rendered), recent commits, pull
  requests and open issues. The server reads it all through `gh` (same sign-in as before); the browser
  never talks to GitHub. Limited to repos owned by the signed-in gh user. New `GET /api/repo` and
  `GET /api/repo/path`; responses cached 60 s. Files over 300 KB and binaries link out to GitHub instead.

## 1.12 — 2026-10-18
- Definition of done is editable from Active Projects: Edit (or Fill in) -> Review change -> a confirm
  step showing the file and BEFORE/AFTER -> Confirm and save. Writes the line in place (or into
  NOTES.md's `## Plan`, creating NOTES.md if the project has none). Refuses if the text changed on
  disk since you opened it, if it is empty or `<fill in>`, or if the file is a symlink out of the
  project. New `POST /api/done`; each save is logged in triage.log.

## 1.11 — 2026-10-18
- Active Projects shows each project's definition of done (the line written at Promote, a
  "Definition of done:" paragraph, or a "What does done look like" section; checked in NOTES.md,
  PROJECT.md, README.md, SPEC.md). Projects without one, or with the `<fill in>` placeholder, get a
  red "No done defined" badge in the list, a red callout in the detail pane saying which line to
  add, and a count in the tab header.

## 1.10 — 2026-10-18
- Activity strip text 15% larger (12px → 13.8px).

## 1.09 — 2026-10-18
- Activity strip now counts commits that reached GitHub, private repos included. GitHub's profile
  calendar hides private-repo commits unless a profile setting is on, so the graph is built from
  your own commits on the default branch of every repo you own or collaborate on (pushed in the
  last year). Local-only repos never appear. Private repos show a lock on their chip.
  Graph shading is quartiles of your busiest day, like GitHub's.

## 1.08 — 2026-10-18
- GitHub activity strip under the page heading, always visible: a contribution-style graph for the
  last year, total, active days, current streak, and chips for the repos you committed to in the
  last 30 days (click opens the repo). New `/api/activity` endpoint uses `gh api graphql` with your
  existing sign-in, cached 10 minutes; Refresh button forces a fetch. If GitHub can't be reached
  the strip says why (or keeps the last good data marked as saved). Page layout is now flex instead
  of a fixed `100vh - 92px`, so the extra strip doesn't push the triage panes off the screen.

## 1.07 — 2026-10-07
- Phone layout (<=800px): backlog list and note are two pages — tap a row to open
  the note, "← Backlog" to return. Decide buttons sit in a bar fixed to the bottom
  with 44px tap targets; forms go single-column with 16px inputs (no iOS zoom);
  keyboard hints hidden; long paths and code wrap inside the screen. In-flight chips
  are read-only on phones (opening a file happens on the host machine). Desktop unchanged.

## 1.06 — 2026-10-07
- `--host` flag (default `127.0.0.1`, unchanged). `--host 0.0.0.0` listens on every
  interface so a phone on home Wi-Fi or the tailnet can open the page. No login,
  so only on trusted networks; the startup line says so.

## 1.05 — 2026-10-06
- Launcher checks for a server already on the port. If its version differs,
  it warns in the terminal and with a desktop notification, then opens the running one.
  `--restart` stops the old server and starts the new code. New `/api/version` endpoint.
- Browser opens only after the server is listening. A port taken by another program gives a clear error.

## 1.04 — 2026-10-06
- Version number shown in the page heading, browser tab title, and startup line.

## 1.03 — 2026-10-02
- Durable now copies to `../reference/<slug>.md` (was `../maintained/`) and adds
  YAML frontmatter so Obsidian shows Properties.

## 1.02 — 2026-09-25
- Copy note path: row Copy button, detail line, `y` key.

## 1.01 — 2026-09-17
- Active Projects: click-to-copy project path, per-row copy buttons, `y` key.

## 1.00 — 2026-09-17
- First version: triage site with Promote / Durable / Archive / Keep, the in-flight strip, and the Active Projects tab.
