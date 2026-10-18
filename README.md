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
                         # the page works on a phone: tap a row, decide from the bottom bar
rawtriage-web --restart   # stop the server already running (e.g. old version), start this one
```

No dependencies beyond python3. `server.py` is the API + file mover,
`index.html` is the UI, `triage.log` records every action.

**Version:** shown in the page heading. When you change the app, bump
`VERSION` in `server.py` and add an entry to `CHANGELOG.md`.

## Decisions per note

| Button   | What happens |
|----------|--------------|
| Promote  | Copy to `../active/<slug>/NOTES.md` or `../experiments/<slug>/NOTES.md` (learning/templates get a flat `<slug>.md`). Stamps `promoted:` and `due:` dates and a `## Plan` block with your definition of done. Capture is marked promoted and moved to `done/`. |
| Durable  | Copy to `../reference/<slug>.md` as a reference note, with YAML frontmatter so Obsidian can read it. Capture to `done/`. Add it to `../reference/README.md`. |
| Archive  | `status: dead`, your one-line reason under `## Outcome`, moved to `done/`. |
| Keep     | Set `open` or `cooking`, optionally add a next-step line. Stays in the backlog. |

Nothing is ever deleted; every move is logged.

## In flight strip

Scans the sibling project folders for markdown files with a `due:` line and
shows days left. Red = late, amber = due within 2 days. Set that file's
`status:` to `done`, `shipped`, or `dead` to clear it.

Keys: `j`/`k` move, `p` promote, `d` durable, `a` archive, `c` keep,
`Enter` apply.

## Active Projects tab

Lists folders in the sibling `active/` directory, including projects without Git.
Select a project to see a README (or NOTES) excerpt, its current branch, ten
recent commits, and feature commits (`feat:` / `feat(scope):`) from the latest
100 commits on that branch. These are source excerpts and commit labels, not
AI-generated summaries or a guarantee that features have been released.

For repositories with a GitHub `origin`, the tab also loads the ten most recently
updated pull requests across all states using the optional `gh` CLI and its
existing authentication. Missing authentication, network access, or history is
shown explicitly. Each row starts with a GitHub label: green **GH** = pushed, amber **GH ↑N** = N commits not pushed yet,
red **No GH** = not on GitHub (hover for why).

Each project also shows its **definition of done** at the top (the line Promote writes into
`NOTES.md`, a `Definition of done:` paragraph, or a "What does done look like" heading in NOTES, PROJECT,
README or SPEC). If it is missing or still `<fill in>`, the row gets a red badge, the detail pane a red
callout, and the tab header counts how many projects need one. **Edit** (or **Fill in**) opens a text box,
**Review change** shows the file plus before/after, and only **Confirm and save** writes anything. The write
is refused if the file changed since you opened it.

Refresh reloads the project list and selected project's activity.
All project inspection is read-only; no fetch, checkout, or repository edits occur.

## GitHub activity strip

Always visible under the heading: a year-long graph of commits you have pushed to GitHub, totals
and streak, and the repos you pushed to in the last 30 days (🔒 = private). It counts commits on
the default branch of repos you own or collaborate on, so private repos are included and local-only
repos (like RawNotes) never show up. Commits on side branches count once merged. Uses `gh api graphql`
and the same sign-in as the PR list, so no new setup. Cached 10 minutes (`ACTIVITY_TTL`); the
Refresh button bypasses the cache.

## Repo viewer

"Review repository" (project detail) and the repo chips in the activity strip open a read-only view of the
GitHub repo inside the page: README, files, recent commits, pull requests, open issues. The server fetches
everything through `gh`, so a private repo needs no GitHub login in the browser, including on the phone.
It only serves repos owned by the signed-in `gh` user and only issues GET requests. Because this page has no
login, serving it beyond localhost (`--host 0.0.0.0`) also makes your private repo contents readable to anyone
who can reach it; keep that to the tailnet or a trusted network.
On a localhost bind the server also refuses requests whose `Host` is not localhost, requests from another
origin, and non-JSON POSTs, so a web page you visit cannot drive it.
