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
```

No dependencies beyond python3. `server.py` is the API + file mover,
`index.html` is the UI (read once at startup: after editing it, `rawtriage-web --restart`), `triage.log` records every action.

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

**Undo (Z):** every change made through the page (decisions, batch, merge, restore, project status) can be
undone, newest first, while the server is running. Undo checks first and refuses, changing nothing, if any
file it would touch has been edited since.

**Stacks:** three or more Inbox notes whose names start with the same two words (e.g. `arcane-bomb-*`) collapse
into one stack. Open it, tick the notes, then **Merge into one** (a new note with each source as a section,
newest first, sources moved to `done/` pointing at it; optionally promote or file the merged note straight
away), or Keep / Kill / Reference them all as one step.

**Stuck notes:** an Inbox note whose status already says decided (e.g. `status: promoted`) but never moved gets a
"not moved" tag and shows under the **Stuck** filter. Check its destination, then move it to `done/` yourself.

**Archive:** filter by Promoted / Reference / Killed / Merged; **Restore to Inbox** brings a note back as cooking.

Keys: `j`/`k` move, `p` `r` `c` `x` decide, `Enter` apply, `Esc` cancel, `z` undo, `y` copy path, `/` search,
`Ctrl K` search everything, `1`–`4` switch pages, `?` all keys.

## Projects page

One board for everything promoted, replacing the old Active Projects tab and In flight strip. Columns:
**Overdue**, **Ship this week**, **Later**, **Experiments** (promoted experiments, open-ended), **No ship-by date** (folders in `active/` without a `due:` line),
plus **Closed** (status `shipped`, `done` or `dead`). It scans the sibling folders for markdown files with a
`due:` line. Each card shows a countdown ring (red = late, amber = 2 days or less) and its GitHub state:
**On GitHub**, **GH ↑N** (N commits not pushed), **Not pushed**, **Local git** or **No git**.

Select a card for the side panel: **done means**, the clock, recent commits and pull requests (via the optional
`gh` CLI), and a README/NOTES excerpt. Excerpts and commit labels are quoted from the source, not AI summaries.
Buttons: **Mark shipped** (sets `status: shipped` + `shipped:` date), **+7 days** (pushes `due:` from today or
the current date, whichever is later), **Browse repo**, **Kill** (asks twice). All three writes can be undone.

**Done means** is found in NOTES, PROJECT, README or SPEC: the line Promote writes, a `Definition of done:`
paragraph, or a "What does done look like" heading. Missing or `<fill in>` gets a red "Define done means" on the
card and a count in the header tiles. **Edit** (or **Fill in**) → **Review change** (before/after) → **Confirm
and save**; the write is refused if the file changed since you opened it.

Project inspection is otherwise read-only; no fetch, checkout or repository edits.

## Pulse page

- **Pushed to GitHub:** a year-long graph of commits you pushed (default branch of repos you own or collaborate on,
  so private repos count and local-only repos like RawNotes never show). Uses `gh api graphql` with your existing
  sign-in; cached 10 minutes (`ACTIVITY_TTL`); Refresh bypasses the cache. The rail shows a 16-week sparkline.
- **Inbox flow:** notes captured (by file date) vs. decided (from `triage.log`) per week, last 8 weeks, with a table
  view. Undone decisions still count as decided.
- **Median wait** of the notes in the Inbox, against the 7-day goal.
- **Where the commits went:** repos pushed to in the last 30 days; click one to browse it.

## Look

Palette and type borrowed from Rulecraft's Elven Glass: sage background, forest green, gold, Cormorant
Garamond + Lato + JetBrains Mono. Fonts are served by the app (`static/fonts`, OFL), so it works offline
and on the phone. Dark mode follows the system; the rail toggle overrides it. Under 800px wide it switches
to the phone layout: bottom tab bar, and the decision panel as a bottom sheet.

## Repo viewer

"Browse repo" (Projects panel) and the repo rows on Pulse open a read-only view of the
GitHub repo inside the page: README, files, recent commits, pull requests, open issues. The server fetches
everything through `gh`, so a private repo needs no GitHub login in the browser, including on the phone.
It only serves repos owned by the signed-in `gh` user and only issues GET requests. Because this page has no
login, serving it beyond localhost (`--host 0.0.0.0`) also makes your private repo contents readable to anyone
who can reach it; keep that to the tailnet or a trusted network.
On a localhost bind the server also refuses requests whose `Host` is not localhost, requests from another
origin, and non-JSON POSTs, so a web page you visit cannot drive it.
