# rawnotes-triage

Local website for reviewing `~/Projects/RawNotes` and pushing each note to
its next step. Goal: every promoted note is shipped or killed within
3-7 days.

```
rawtriage-web            # starts http://127.0.0.1:8765 and opens the browser
rawtriage-web --port N   # different port
rawtriage-web --no-browser
```

No dependencies beyond python3. `server.py` is the API + file mover,
`index.html` is the UI, `triage.log` records every action.

## Decisions per note

| Button   | What happens |
|----------|--------------|
| Promote  | Copy to `../active/<slug>/NOTES.md` or `../experiments/<slug>/NOTES.md` (learning/templates get a flat `<slug>.md`). Stamps `promoted:` and `due:` dates and a `## Plan` block with your definition of done. Capture is marked promoted and moved to `done/`. |
| Durable  | Copy to `../maintained/<slug>.md` as a reference note. Capture to `done/`. |
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
shown explicitly. Refresh reloads the project list and selected project's activity.
All project inspection is read-only; no fetch, checkout, or repository edits occur.
