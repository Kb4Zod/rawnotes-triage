# Changelog

The version shows in the page heading ("RawNotes Triage version X.XX") and
in the startup line. Bump `VERSION` in `server.py` and add an entry here
with every change. Versions 1.00-1.03 were numbered after the fact from git history.

## 2.01 — 2026-10-18
Bug fixes after the 2.00 redesign.
- **Fix: page and server out of step.** The server read `index.html` from disk on every request, so a 1.14 server
  still running after the 2.00 merge served the 2.00 page; Merge and the Pulse median wait then failed against
  missing endpoints. The page is now read once at startup, and a tab that outlives a restart shows a banner
  ("This page is vX but the server is vY") with a Reload button.
- **Promote by destination:** Active (and Learning/Template) still need a ship-by date: the 3/7/14/30-day buttons
  or an exact date from the new date picker (`due` in the API, within a year). **Experiments are open-ended:**
  no `due:` line, still need done means. Same in Merge → Promote it.
- **Projects board:** new **Experiments** column for promoted experiments (no countdown ring, "since" date;
  Mark shipped and Kill, no +7 days). "No ship-by date" sits under it.
- **Inbox filters** are one solid segmented bar (All / Open / Cooking / Stale / Stuck) plus a type bar with
  **Any type**. Archive's fate filter uses the same bar.
- **Stuck notes:** Inbox notes whose status already says decided (e.g. `status: promoted`) but were never moved get a
  "not moved" tag, a **Stuck** filter, and an explanation in the reader. Nothing is moved automatically.
- **Pulse median wait:** true median (mean of the middle two for an even count), "Inbox is empty" instead of 0d,
  note count shown, and the load error if the server can't answer.
- **Merge preview** shows the merged note's size and warns past 200 KB.
- Review fixes (folded into 2.01 before merge): Merge → Promote checks destination, done means and ship-by before
  writing anything (a bad date used to leave a half-done merge with no undo); the Merge form's date picker is no
  longer overwritten by the form's input handler; promoting to Experiment drops any old `due:` line; the server
  refuses +7 days on open-ended experiments; only an experiment's NOTES.md (or a flat file) makes a card, not
  every note in its folder; Stuck only matches decided statuses (promoted, dead, merged, shipped…) in any case;
  clearing the date goes back to the day count you picked; on a phone the filter counts sit under their labels.

## 2.00 — 2026-10-18
Redesign: one look, one set of words, and the features that make triage faster. Proposal and mockups in
`.scratch/ui-redesign/PROPOSAL.md` (kept out of git; it has screenshots of private notes).
- **Words** match the CLI and CLAUDE.md: Inbox / Projects / Archive / Pulse; Promote / Reference / Keep / Kill;
  Open → Cooking → Promoted / Reference / Killed; "done means" and "ship by". Keys: P R C X, Z undo.
- **Look:** Rulecraft's Elven Glass palette and type, self-hosted fonts (`static/fonts`, served at
  `/static/fonts/*.woff2`), tuned dark mode with a toggle, left rail with counts and a push sparkline.
- **Inbox:** search, status and type filters, grouped by age, age bar per row, the question pulled out as a
  callout, frontmatter shown as chips. Decision cards say what each one does; the form shows exactly which files
  will be written. **Done means is now required to Promote** (server refuses without it). Decide → next note.
- **Undo** (`POST /api/undo`): every change through the page can be undone, newest first, while the server runs.
  It dry-runs first and refuses if any file changed since.
- **Stacks:** 3+ notes sharing a two-word name prefix group together. **Merge** (`POST /api/merge`) combines them
  into one note (sources to `done/`, status `merged`) and can promote or file it in the same step;
  **batch** (`POST /api/batch`) keeps, kills or files several notes as one undo step.
- **Projects** page replaces the Active Projects tab and In flight strip: board by ship-by date, countdown rings,
  side panel with Mark shipped / +7 days / Kill (`POST /api/project/status`), the done-means editor, repo browser.
- **Archive:** filter by fate (promoted / reference / killed / merged; `fate` and `dest` added to done notes in
  `/api/notes`), **Restore to Inbox** (`POST /api/restore`).
- **Pulse:** the activity graph moves here from the header, plus Inbox flow (captured vs. decided per week,
  `GET /api/stats`), median Inbox wait, and commits by repo.
- **Ctrl K** palette across notes, projects, archive and commands; `?` shows every key.
- Phone layout: tab bar, decision bottom sheet with 44px targets.
- Markdown tables render. `--log PATH` option (handy for trying the app on a copy of your notes).
- `archive` / `durable` API action names still accepted.

## 1.14 — 2026-10-18
- Fix: projects whose remote is written `ssh://git@ssh.github.com:443/...` (GitHub's SSH-over-443
  address) were not recognised as GitHub repos, so they had no "Review repository" button, no
  GitHub link and no pull-request list. All GitHub remote spellings are now accepted.
- Active Projects rows start with a GitHub label: green **GH** (pushed), amber **GH ↑N** (N commits
  not pushed yet), red **No GH** (not a Git repo, no GitHub remote, or remote set but nothing pushed).
  Hover for the reason; the detail pane repeats it.
- Review fixes (folded into 1.14 before merge): the GH state of each project is now checked in parallel, so
  one slow or hung repo no longer stalls the whole list; remotes like `github.com/../..` are no longer
  accepted as a repo name.

## 1.13 — 2026-10-18
- In-app repo viewer, so private repos can be reviewed without a GitHub login in the browser (or on the
  phone). "Review repository" in a project's detail, and every repo chip in the activity strip, open a
  read-only view: README, file browser (folders, text files, markdown rendered), recent commits, pull
  requests and open issues. The server reads it all through `gh` (same sign-in as before); the browser
  never talks to GitHub. Limited to repos owned by the signed-in gh user. New `GET /api/repo` and
  `GET /api/repo/path`; responses cached 60 s. Files over 300 KB and binaries link out to GitHub instead.
- Review fixes (folded into 1.13 before merge):
  - Browser-attack guard on every request: on a localhost bind the `Host` header must be localhost/127.0.0.1
    (stops DNS rebinding reading private repos), a request with an `Origin` must come from the same host, and
    POSTs must be `application/json` (stops cross-site form posts writing to your notes). The page now sends
    that header.
  - Saving a definition of done keeps CRLF line endings and non-UTF-8 bytes in the file, and two saves at
    once no longer share a temp file.
  - A `## Definition of done` heading now means its first paragraph only, so saving no longer replaces the
    whole section (bullets and background text below it stay).

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
