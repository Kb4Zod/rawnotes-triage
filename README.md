# rawnotes-triage

Local website for reviewing `~/Projects/RawNotes` and pushing each note to its next step.

- **Status:** active (version shown in the page heading)
- **Stack:** Python 3 standard library (`server.py`) + one `index.html`, no dependencies
- **Run it:** `rawtriage-web` → http://127.0.0.1:8765
- **Repo:** github.com/Kb4Zod/rawnotes-triage (public)

## Why it exists
Notes pile up. Goal: every promoted note is shipped or killed within
3-7 days.

```
rawtriage-web            # starts http://127.0.0.1:8765 and opens the browser
rawtriage-web --port N   # different port
rawtriage-web --no-browser
rawtriage-web --host 0.0.0.0   # reachable from the phone (home Wi-Fi / Tailscale); no login
                         # the page works on a phone: tap a row, decide from the bottom sheet
rawtriage-web --notes /tmp/copy/RawNotes --log /tmp/copy/triage.log --port 8766   # try it on a copy
rawtriage-web --restart   # stop the server already running (e.g. old version), start this one
rawtriage-web --app       # same, but in its own app window instead of a browser tab (Omarchy)
```

### As an app in the Omarchy launcher

One-time setup, run from the repo:

1. `./install-webapp.sh` — adds **RawNotes Triage** (with its icon) to the app launcher and starts the server as an
   always-on systemd user service (`rawtriage-web.service`, starts at login).
2. Press `SUPER + SPACE`, type `RawNotes Triage`, Enter. The page opens in its own window, not a browser tab.

- If the service is down, the launcher starts a server itself, so you never get a blank window.
- **After updating the app:** run `systemctl --user restart rawtriage-web`, not `rawtriage-web --restart`. The
  `--restart` flag stops the service's server and starts one in your terminal, and the service stays stopped.
- Launcher entry only, no service: `./install-webapp.sh --no-service`. Undo everything: `./install-webapp.sh --remove`.
- The launcher entry runs `rawtriage-web --app`, not `omarchy-launch-webapp`, so it doesn't show up in Omarchy's
  *Remove Web App* menu. Use `--remove` instead.

No dependencies beyond python3. `server.py` is the API + file mover, `safefs.py` and `notemeta.py` are its two small
helpers (safe file writes; note headers), `index.html` is the UI (read once at startup: after editing it,
`rawtriage-web --restart`), `triage.log` records every action (if the log can't be written, changes still go through).

`--restart` only stops a server that is really rawnotes-triage: it checks the page the port serves and the process
that owns it, and refuses (leaving the other service alone) if either isn't.

**Version:** shown in the page heading. When you change the app, bump
`VERSION` in `server.py` and add an entry to `CHANGELOG.md`.

## Words (same in the web app, the CLI and RawNotes/CLAUDE.md)

Places: **Inbox** (undecided notes), **Projects** (promoted work on a ship-by clock), **Archive** (`done/`),
**Pulse** (stats). Decisions: **Promote**, **Reference**, **Keep**, **Kill**. Statuses:
Open → Cooking → Promoted / Reference / Killed (`status: dead` on disk). Every project has a **done means**
and a **ship by** date.

## Decisions per note

| Button (key) | What happens |
|--------------|--------------|
| Promote (P)   | Copy to `../active/<slug>/NOTES.md` or `../experiments/<slug>/NOTES.md` (learning/templates get a flat `<slug>.md`). Stamps `promoted:` and a `## Plan` block with your **done means** (required for every destination). Active/Learning/Template also get `due:` (ship by: 3/7/14/30 days or an exact date); **experiments are open-ended**, no `due:`. Capture is marked promoted and moved to `done/`. |
| Reference (R) | Copy to `../reference/<slug>.md` with YAML frontmatter so Obsidian can read it. Capture to `done/`. Add it to `../reference/README.md`. |
| Keep (C)      | Set `open` or `cooking`, optionally add a next-step line. Stays in the Inbox. |
| Kill (X)      | `status: dead`, your one-line reason under `## Outcome`, moved to `done/`. |

The old API names `durable` and `archive` still work. Nothing is ever deleted; every move is logged.

**Undo (Z):** every change made through the page (decisions, batch, merge, restore, project status, done means) can be
undone, newest first, while the server is running. Undo checks first and refuses, changing nothing, if any
file it would touch has been edited since, or is now behind a symlink or outside the notes and projects folders. Changes are all-or-nothing: notes are checked before anything is
written (a note listed twice, a missing one, a bad date), each file is replaced atomically (permissions, line
endings and odd bytes survive), and if a step fails half-way the steps already taken are rolled back. In the rare
case a rollback can't finish, an "unfinished" step stays on the undo list so Z can finish it.

**Stacks:** three or more Inbox notes whose names start with the same two words (e.g. `arcane-bomb-*`) collapse
into one stack. Open it, tick the notes, then **Merge into one** (a new note with each source as a section,
newest first, sources moved to `done/` pointing at it; optionally promote or file the merged note straight
away), or Keep / Kill / Reference them all as one step.

**Stuck notes:** an Inbox note whose status already says decided (e.g. `status: promoted`) but never moved gets a
"not moved" tag and shows under the **Stuck** filter. Check its destination, then move it to `done/` yourself.

**Archive:** filter by Promoted / Reference / Killed / Merged; **Restore to Inbox** brings a note back as cooking.

**Opening a stack never changes anything.** `Enter` or `Space` on a stack row only opens it. A batch is applied by
the Merge / Kill all / … button, or by `Enter` after you chose that action on purpose (a click or `p` `r` `c` `x`).

Keys: `j`/`k` move, `p` `r` `c` `x` decide, `Enter` apply, `Esc` cancel, `z` undo, `y` copy path, `/` search,
`Ctrl K` search everything, `1`–`4` switch pages, `?` all keys. `Enter` and `Space` open a focused row.
What you've typed in a decision form or the "done means" editor is kept when the page refreshes, when you look at
another note or page and come back, and the browser asks before you close the tab with an unsaved edit.

## Projects page

One board for everything promoted, replacing the old Active Projects tab and In flight strip. Columns, left to
right: **Overdue**, **Due this week**, **Later**, **No ship date** (folders in `active/` without a `due:` line),
**Experiments** (open-ended), then **Closed** (status `shipped`, `done` or `dead`). Empty columns are hidden; on a
narrow screen the board scrolls sideways. It scans the sibling folders for markdown files with a
`due:` line. Each card shows a countdown ring (red = late, amber = 2 days or less) and its GitHub state:
**On GitHub**, **GH ↑N** (N commits not pushed), **Not pushed**, **Local git** or **No git**.

Select a card to open its panel as a column right beside it (select it again, or `Esc`, to close it): **done means**, the clock, recent commits and pull requests (via the optional
`gh` CLI), and a README/NOTES excerpt. Excerpts and commit labels are quoted from the source, not AI summaries.
Buttons: **Mark shipped** (sets `status: shipped` + `shipped:` date), **+7 days** (pushes `due:` from today or
the current date, whichever is later), **Browse repo** / **Open folder** (opens a project folder or a promoted file the
board lists, only from the machine running the server), **Kill** (asks twice). All of these writes can be undone.
Git facts per project (on GitHub? unpushed commits?) are cached for 20 seconds; **Refresh** re-reads them.

**Symlinked project folders** are shown (read-only) with a note, but Triage never writes into one: a symlink
could point anywhere. Every write must land on real files inside `RawNotes/` or the projects folders, with no symlink
on the way, or it is refused. Undo, rollback and redo check the same rule again, for every file they would touch, before
changing anything. Not covered: another local process swapping a folder for a link in the instant between a check and
the write inside one request.

**Done means** is found in NOTES, PROJECT, README or SPEC: the line Promote writes, a `Definition of done:`
paragraph, or a "What does done look like" heading. Missing or `<fill in>` gets a red "Define done means" on the
card and a count in the header tiles. **Edit** (or **Fill in**) → **Review change** (before/after) → **Confirm
and save**; the write is refused if the file changed since you opened it, and it can be undone.

**Note headers.** A note's metadata (`- date:`, `- status:`, `- type:`, `- promoted:`, `- due:`, `- shipped:`) is the first run
of `- key: value` lines under the title (up to three lines of subtitle may come first) and before the first `##`
heading. Only that block is read or edited: a `- status:` line in the body, a quote or a merged section is just text.
To give a project a ship-by date, add `- due: YYYY-MM-DD` to that header.

Project inspection is otherwise read-only; no fetch, checkout or repository edits.

## Pulse page

- **Commits on GitHub:** a year-long graph of your commits on the default branch of repos you own or collaborate on,
  counted by *commit date* (not by when they were pushed), so private repos count and local-only repos like RawNotes
  never show. Uses `gh api graphql` with your existing sign-in; cached 10 minutes (`ACTIVITY_TTL`); Refresh bypasses
  the cache. Up to 1000 repos (most recently pushed first) and 2000 commits per repo are read; if GitHub had more, the
  page says so instead of silently stopping. The rail shows a 16-week sparkline.
- **Inbox flow:** notes captured (by file date) vs. decided (from `triage.log`) per week, last 8 weeks, with a table
  view. Undone decisions still count as decided.
- **Median wait** of the notes in the Inbox, against the 7-day goal.
- **Where the commits went:** repos you committed to in the last 30 days; click one to browse it.

## Look

Palette and type borrowed from Rulecraft's Elven Glass: sage background, forest green, gold, Cormorant
Garamond + Lato + JetBrains Mono. Fonts are served by the app (`static/fonts`, OFL), so it works offline
and on the phone. Dark mode follows the system; the rail toggle overrides it. Under 800px wide it switches
to the phone layout: bottom tab bar, and the decision panel as a bottom sheet.

**Accessibility.** Text meets WCAG AA contrast in both themes; `main`/`nav` landmarks, one `h1` per page and no skipped
heading levels; stack members are real checkboxes plus buttons; `Ctrl K` and `?` are modal dialogs (the page behind is
inert, Tab stays inside, focus returns where it was); results announce through the status region; touch targets are
44px on the phone. Checked with axe-core 4.10.3 (see Tests).

## Tests

```
python3 -W ignore::ResourceWarning -m unittest                 # API, engine/fault-injection, headers, safety, caches (stdlib only)
AXE_JS=/path/to/axe.min.js python3 -m unittest test_browser    # + accessibility sweeps (axe-core 4.10.3; not part of the app)
python3 loadcheck.py                                           # disposable 100/1000-note load, reads beside writes
```

`test_browser.py` drives Chromium through `cdp.py` (a tiny stdlib DevTools client) against a disposable server; it is
skipped when no Chromium/Chrome is found (`CHROMIUM=/path` to pick one) and never touches GitHub or your notes. Set
`TRIAGE_INDEX=/path/to/old/index.html` to run the same checks against another build of the page.

## Repo viewer

"Browse repo" (Projects panel) and the repo rows on Pulse open a read-only view of the
GitHub repo inside the page: README, files, recent commits, pull requests, open issues. The server fetches
everything through `gh`, so a private repo needs no GitHub login in the browser, including on the phone.
It only serves repos owned by the signed-in `gh` user and only issues GET requests. Because this page has no
login, serving it beyond localhost (`--host 0.0.0.0`) also makes your private repo contents readable to anyone
who can reach it; keep that to the tailnet or a trusted network.
On a localhost bind the server also refuses requests whose `Host` is not localhost, requests from another
origin, and non-JSON POSTs, so a web page you visit cannot drive it. "Open folder" only works for a browser on the
server's own machine and only for project folders / promoted files the board lists.
