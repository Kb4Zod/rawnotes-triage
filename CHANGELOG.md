# Changelog

The version shows in the page heading ("RawNotes Triage version X.XX") and
in the startup line. Bump `VERSION` in `server.py` and add an entry here
with every change. Versions 1.00-1.03 were numbered after the fact from git history.

## 2.09 — 2026-10-23
- **GitHub sign-in status.** The left rail now says **Signed in as `<login>`** or **Not signed in to GitHub** (or
  that `gh` is missing, or GitHub is unreachable), through new `GET /api/github` (cached 60 s; `?refresh=1` bypasses).
  Click it for a dialog with the details. Nothing about the account is hardcoded: it is whoever `gh` (or the token) is.
- **Sign in with a personal access token.** The dialog saves a token, after GitHub accepts it, to
  `~/.config/rawnotes-triage/github-token` (folder `0700`, file `0600`; `RAWTRIAGE_CONFIG_DIR` overrides). It is
  passed to `gh` as `GH_TOKEN` in the environment, never on a command line, takes precedence over `gh auth login`,
  and is never returned to the page. **Remove saved token** goes back to `gh`'s own login. Changing the sign-in
  drops the cached commit graph and repo-viewer results of the old account. `POST /api/github/token` and
  `/api/github/token/remove` only answer a browser on the server's own machine: a loopback peer *and* a localhost
  `Host`, on any bind, so a DNS-rebound page can't change the sign-in under `--host 0.0.0.0` either.
- The saved token is used by every `gh` call, including the Projects page's pull-request list (one shared `gh_env`).
  An existing config folder is tightened to `0700`; a token file that isn't UTF-8 is treated as no token.
  `loadcheck.py` keeps the spawned server off a saved token too. With `gh` missing, a saved token can still be removed.
  An unwritable `triage.log` no longer makes a token save or removal that went through report as failed.
- README: new **GitHub sign-in** section saying plainly that `gh` is required for every GitHub feature and that
  `gh auth login` is how a person signs in; Pulse and Network sections point to it.
- Tests: `test_github.py` (status states, token validation/permissions/environment-only, cache reset, local-only),
  a browser test for the rail status and dialog; the shared fixture keeps tests off the real saved token.

## 2.08 — 2026-10-20
- **Standalone app in the Omarchy launcher.** New `--app` flag opens the page in its own window through
  `omarchy-launch-webapp` (falls back to a browser tab off Omarchy). `install-webapp.sh` adds a **RawNotes Triage**
  launcher entry (via `omarchy-webapp-install`, running `rawtriage-web --app`) and an always-on systemd user unit
  (`systemd/rawtriage-web.service`); `--no-service` skips the unit, `--remove` undoes both.
- Fixed before release, from the local PR review:
  - `--restart` restarts the service when it owns the server. Before, it stopped it, and the service stayed down.
  - The unit runs the new `--service` mode: if another triage server holds the port, it exits with an error and
    systemd retries every 30 s. Before, it exited cleanly and never came back.
  - The unit starts with the desktop session (`graphical-session.target`), so Open and notifications have a display.
  - `install-webapp.sh` checks everything before changing anything (Omarchy, main checkout on 2.08, no server
    started by hand on the port, a systemd user session) and installs from the main checkout only. It also confirms
    the service stayed up. `--remove` finishes without a systemd user session.
  - `--app` falls back to a browser tab if `omarchy-launch-webapp` fails (e.g. no supported browser).
  - Under the service, Open starts the editor or file manager in a scope of its own (`systemd-run --user --scope`),
    so restarting or stopping the service no longer kills it, unsaved edits and all.
- **Always opens full screen.** `--app` opens the page at `/triage` (same page as `/`), which gives the window its
  own class. `install-webapp.sh` adds a marked Hyprland rule for that class (`fullscreen_state = "2 0"`; a plain
  `fullscreen = true` is undone by Chromium), checks it with `hyprctl configerrors` and takes it back out if
  Hyprland rejects it. `--remove` removes it.
- App icon (`static/icons/`, SVG source + PNGs) served as the page favicon and as `/apple-touch-icon.png` for the
  phone home screen.
- Fixed: the whole page scrolled as well as its panes, so a second scroll bar showed at the right edge. The Inbox
  list's hidden screen-reader labels ("waiting ...") were placed against the page instead of the list, so the ones
  further down made the page taller than the window. The list now anchors them (`.list{position:relative}`).
- Fixed: while a long note scrolls under the pinned "What happens to this note?" panel, the text no longer shows
  in the strip between the top of the window and the panel. That strip is now painted with the page background
  (desktop only; the phone layout keeps its panel at the bottom).

## 2.07 — 2026-10-19
- Replace the search and keyboard-help dialog backdrop blur with the existing plain dimmed overlay. This avoids
  mirrored/repeated background imagery observed in the preview browser. Modal focus handling is unchanged.
- Track persistent undo, race-resistant filesystem operations, and physical-phone, screen-reader and authenticated
  GitHub verification as separate follow-up tickets under `.scratch/`.

## 2.06 — 2026-10-19
Two defects found by independent verification of 2.05, fixed before release (2.05 was never released).
- **Open folder works again.** 2.05 ran `xdg-open -- <path>`, but the installed `xdg-open` rejects `--` ("unexpected
  option '--'") and exits without opening anything, while the page reported success. The command is now
  `xdg-open <absolute path>`: the path was already validated and absolute, so it always starts with `/` and can never
  be read as an option (a relative path is refused). An opener that fails at once is now reported on the page instead of
  swallowed. A new test runs the real `/usr/bin/xdg-open` on the exact command line the app builds, in a clean
  environment with no display and a stub handler, so nothing is ever opened; the old `--` form is asserted to be refused.
- **Undo, rollback and redo can't write through a symlink.** 2.05 checked containment when a change was made but not
  when it was reversed, so renaming a project folder away and putting a symlink in its place between requests let Undo
  edit the real files behind the link. Every path an undo entry recorded (the file, a move's source *and* destination, a
  created folder) is now checked again, for all steps, before the first one is touched; Undo then refuses with "Nothing
  was changed", keeps the entry on the undo list and works again once the folder is put right. The same check runs
  immediately before each reverse step, each rollback step and each "put it back" step after a half-failed undo, which
  then reports the step as not put back rather than writing through the link. Covered: folder swapped for a link to a
  renamed folder inside or outside the projects root, the file itself swapped for a link with identical content, a
  move's destination folder or source name replaced, a promotion's new folder, rollback and redo recovery.
- Still not covered (same as 2.05): a swap in the instant *between* a check and the write inside one request; Triage
  assumes the folders are not being rearranged under it by another local process at that moment.
- Tests: 206 → 220.

## 2.05 — 2026-10-19
Reliability, accessibility and load fixes from the v2.04 code review and browser audit (no new features, no new
dependencies; `safefs.py` and `notemeta.py` are small stdlib helpers next to `server.py`).
- **Changes are all-or-nothing and always undoable.** Every change (decide, batch, merge, restore, project status,
  done means) now checks all its notes first (a note listed twice, a missing or already-archived one, a bad status),
  replaces files atomically (permissions, CRLF and odd bytes survive), rolls back automatically if a later step
  fails, and records its undo entry *before* anything is logged. A full disk or unwritable `triage.log` no longer
  fails (or strands) a change that already happened. If a rollback can't finish, an "unfinished" entry stays on the
  undo list so Z completes it; a half-failed undo puts back what it had already undone. Saving **done means** is now
  undoable and shares the same lock, so a status change and a definition edit can't overwrite each other.
- **Note headers are read and edited only where they are.** `set_field` used `\s*` across lines and searched the whole
  document: `- status:` (blank) followed by `- type: research` became `- status:\ncooking type: research`. The header is
  now the first run of `- key: value` lines under the title, before the first `##`; blank values are filled in place,
  a missing header is added under the title, and `- status:`/`- due:` lines in a body, quote or merged section are
  left alone.
- **The reader shows the whole question** (every paragraph, list, code block); the list row keeps the 280-character
  summary. `/api/note` returns `question` and `body` alongside `text`.
- **Enter on a stack only opens it.** One Enter used to open the stack *and* merge it (the row's handler and the page
  handler both acted). Rows now mark the key handled; a batch is submitted by its button, or by Enter after you chose
  the action on purpose. **Space** opens rows like Enter.
- **Late replies can't overwrite the wrong screen.** Reader, Archive, project drawer, Projects, Pulse and repo
  requests are cancelled when superseded and ignored (success *and* failure) if their element is gone.
- **Unsaved typing survives** a refresh on returning to the tab, a re-render, and a trip to another note or page
  (done means editor, decision forms); closing the tab with an unsaved edit asks first.
- **Projects/Pulse never show stale data after a change:** promotion, batch, merge, restore, project actions, done
  means and undo all drop the cached lists (a promoted note's card now has its done-means editor straight away).
- **Symlinks can't redirect a write.** (Undo and rollback were not covered until 2.06.) Writes need real paths inside `RawNotes/` or the projects folders with no
  symlink on the way (the old check only looked at the final file). Symlinked project folders are still listed,
  read-only, with a note.
- **`--restart` verifies what it stops:** the port must serve the triage page *and* its owner must be a triage
  `server.py` (command line checked); an HTTP error from some other service is no longer taken for an old triage.
- **`/api/open` only launches a project folder or promoted file the board lists** (`{name, kind}` or `{path}`), only for a
  client on the server's own machine, with `xdg-open` and no shell (2.05 passed `--`, which the installed `xdg-open` rejects; fixed in 2.06). URLs and arbitrary paths are refused.
- **Reads no longer fail beside writes.** Listings take a shared lock that changes hold exclusively (and skip a file
  that vanished or can't be read), so 16 clients doing 160 listings + 40 kills got 116/200 HTTP 400s before and 0 now.
  Per-note summaries are cached by file stat (not trusted within 2 s of an edit); git inspection is cached for 20 s
  on one shared bounded pool; Pulse stats no longer build the whole Projects board; GitHub is asked without holding a
  lock (one caller fetches, others wait for its answer). The listen backlog is 128 (it was 5).
  1000 notes, 24 clients: p95 2001 ms → ~590 ms; 8 clients: 557 → ~165 ms.
- **GitHub activity is honest:** repos are paged (up to 1000) and per-repo history up to 2000 commits; when GitHub
  has more the page says so. The graph counts commits by commit date on the default branch, so it is labelled
  "commits", not "pushes".
- **Accessibility (axe-core 4.10.3 clean):** contrast fixed in both themes (muted text, selected-segment hints,
  dimmed action cards, the dark Undo button); `Ctrl K` and `?` are real modal dialogs (inert background, focus trap,
  focus returns, listbox/option semantics); stack members are a native checkbox plus a button (no nested
  interactive controls, unique names); `main`/`nav` landmarks, one `h1` per page, no skipped heading levels, named
  search boxes, keyboard-reachable scrolling regions; the mobile toast floats above the tab bar or sheet with a 44 px
  Undo; 44 px touch targets; no page-wide overflow at 1280, 390 or 320 px. The promote form's preview no longer clips at
  desktop widths.
- Found by an independent review before release and fixed: a held or doubled Enter could submit a decision twice; a rolled-back
  change had already written its log lines (they now wait for the commit); `done means` in a file with mixed line endings
  could edit the wrong line; Keep dropped the next step when `## Notes` was the last line; notes with YAML frontmatter
  got their status written above it; a fenced `##` line cut the question short; an Outcome line went to the end of the
  note instead of its section; armed stack + Enter on the focused row silently disarmed; a stale Projects reload could
  repopulate a dropped cache; Enter on "No matches" closed the palette.
- Tests: 62 → 206 (engine fault injection, headers, safety, caches/concurrency, GitHub, a real-browser suite with
  optional axe sweeps); `loadcheck.py` for the load scenario.

## 2.04 — 2026-10-18
- **Projects board, reorganized.** One column per group, left to right: Overdue, Due this week, Later, No ship date,
  Experiments, then Closed. Empty columns are hidden, and the board scrolls sideways when it doesn't fit.
- **The project panel opens beside the card.** Selecting a card opens its panel as a full column right next to that
  card's column; selecting it again (or `Esc`) closes it. Nothing is selected when the page opens. On a phone, a tap
  still opens the full-screen detail view.

## 2.03 — 2026-10-18
- **Archive filter is readable again.** The five outcome segments (All / Promoted / Reference / Killed / Merged)
  overlapped in the 400px list pane; each segment now stacks its count under the label and wider labels get more
  room. The group is labelled "Outcome" for screen readers. Inbox filters unchanged.

## 2.02 — 2026-10-18
- **Projects board shows experiments.** Every folder in `../experiments` is now a project card, not just ones with a
  promoted note in them, labelled with a teal **Experiment** chip (also in the drawer). Folder-only experiments sit
  in the Experiments column with an open-ended clock; the column header is teal to match. Done means, recent
  activity and GitHub state work for experiments too (`kind` on `/api/projects`, `/api/project` and `/api/done`;
  defaults to `active`, so a folder with the same name in both places stays two projects).
- **Close any project from the board.** A project folder with no promoted note (most experiments) now has
  Mark shipped and Kill in its drawer; they write `- status:` into its NOTES.md (created if missing, undoable).
  A folder whose NOTES.md says `done`, `shipped` or `dead` moves to Closed.
- **Tiles count active projects only:** experiments no longer add to "No done means" or the On GitHub total.
- Review fixes: project names are checked without rescanning every folder; the done-means log line names
  `kind/name`.

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
