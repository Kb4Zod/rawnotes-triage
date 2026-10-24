# RawNotes Triage: User Guide

RawNotes Triage is a local web app for deciding what happens to each of your raw Markdown notes, then tracking the ones you promote until they ship or die. The target: **every promoted note is shipped or killed within 3 to 7 days.**

## 1. Vocabulary

- **Inbox**: Undecided notes sitting directly in `RawNotes/`
- **Projects**: Promoted work, on a ship-by clock
- **Archive**: Decided notes, stored in `RawNotes/done/`
- **Pulse**: Statistics: commits and Inbox flow
- **Promote / Reference / Keep / Kill**: The four decisions
- **Done means**: One sentence saying what finished looks like

Note statuses: `open`, then `cooking`, then `promoted`, `dead` (killed) or `merged`. Project files can also be `shipped` or `done`.

## 2. Starting the app

Requirements: Python 3 (standard library only, nothing to install). Optional: `git` (branch and commit badges) and a signed-in `gh` (pull requests, Pulse graph, repo viewer).

**Platforms:** the app was written and tested on Linux. The core (Inbox, decisions, Projects, Archive, undo) is plain Python and should run on macOS and Windows too, but those are untested. A few extras are Linux-only: the **Open folder** button (`xdg-open`), `--restart` and `--app`, desktop notifications (`notify-send`), and the Omarchy launcher with its systemd service. Everything else works without them.

```
python3 server.py
```

This starts http://127.0.0.1:8765 and opens your browser. By default notes are read from `~/Projects/RawNotes`; to use another folder, pass `--notes /path/to/RawNotes` (on Windows, use the equivalent path).

Useful options:

- `--port N`: Use a different port
- `--no-browser`: Don't open a browser
- `--host 0.0.0.0`: Reach it from your phone (Tailscale or a network you fully trust). There is no login, so use a trusted network only
- `--notes PATH`: Use another notes folder. Promotions go to sibling folders of it
- `--restart`: Stop the old triage server on the port and start this one
- `--app`: Open in its own app window (Omarchy)

**Try it safely first:** copy a few notes to `/tmp/copy/RawNotes` and run
`python3 server.py --notes /tmp/copy/RawNotes --log /tmp/copy/triage.log --port 8766`.

The projects root is the **parent** of the notes folder: `active/`, `experiments/`, `learning/`, `templates/`, `reference/`.

After editing `index.html` or updating the app, restart the server. A tab left open shows a banner asking you to reload when versions differ.

### Omarchy launcher (optional, Linux only)
Needs Omarchy and a one-line `rawtriage-web` wrapper in `~/.local/bin` (the repo does not ship it yet, so create it yourself and `chmod +x` it):

```
#!/usr/bin/env bash
exec python3 "$HOME/Projects/active/rawnotes-triage/server.py" "$@"
```

The installer always installs and runs the checkout at `~/Projects/active/rawnotes-triage`, wherever you run it from, so clone the repo there. Then run `./install-webapp.sh`. It adds **RawNotes Triage** to the launcher (`SUPER + SPACE`) and runs the server as an always-on user service. The window opens full screen; `SUPER + F` toggles that. Use `--no-service` for the launcher entry only, and `--remove` to undo everything.

## 3. The four pages

Switch with the rail, the phone tab bar, or keys `1` to `4`.

### Inbox
- Lists `YYYY-MM-DD-<topic>.md` files, oldest first, in two groups: **Waiting 2+ weeks** and **This fortnight**. Age comes from the filename date.
- **Filters:** All, Open, Cooking, **Stale** (over 60 days and still open), **Stuck** (status says decided but the file never left the Inbox), and a type filter (question, research, issue).
- **Search** (`/`) matches title, filename and the first paragraph of the question.
- The reader shows the "What's the question / problem?" section, then the rest of the note.
- **Stacks:** three or more notes whose filenames start with the same two words collapse into one stack. Open one to tick its members for a batch action.

### Projects
A board of everything promoted, in columns: **Overdue**, **Due this week**, **Later**, **No ship date**, **Experiments**, **Closed**. Empty columns are hidden. Dated cards show a countdown ring (red = late, amber = 2 days or less). Folder cards show Git state: On GitHub, GH ↑N, Not pushed, Local git, or No git.

Select a card to open its panel.

### Archive
Everything in `done/`, newest first, filtered by Promoted, Reference, Killed or Merged. **Restore to Inbox** puts a note back as `cooking`.

### Pulse
- **Commits on GitHub:** a 53-week graph of your commits (needs `gh`).
- **Inbox flow:** notes captured vs. decided per week, last 8 weeks.
- **Median wait** of Inbox notes against the 7-day goal.
- **Where the commits went:** your ten most active repos in the last 30 days.

## 4. Triage workflow

1. Open the Inbox. Move with `j`/`k` or arrow keys.
2. Read the note. Pick a decision (button or key). The decision panel opens.
3. Fill in the form and press `Enter` (`Ctrl+Enter` inside a text box), or `Esc` to cancel.
4. Made a mistake? Press `Z`.

### The decisions

**Promote (P)** turns the note into work.
- Choose a destination: Active, Experiment, Learning or Template.
- Write the **done means** (required for every destination).
- Active, Learning and Template also get a **due** date: 3, 7, 14 or 30 days, or an exact date (default 7). Experiments are open-ended and get no due date.
- Result: a copy is created at `active/<slug>/NOTES.md` or `experiments/<slug>/NOTES.md` (Learning and Template get a flat `<slug>.md`) with `status: promoted` and a `## Plan` block. The original is marked promoted, records the destination under `## Outcome`, and moves to `done/`.
- Promote creates files and folders only. It does not run `git init` or create a GitHub repo.

**Reference (R)** files it for later: a copy goes to `reference/<slug>.md` with frontmatter that Obsidian shows as properties. The original moves to `done/`. It does not update `reference/README.md`; add the entry by hand if you want it indexed.

**Keep (C)** leaves it in the Inbox. Set the status to `open` or `cooking` and optionally add a dated next step under `## Notes`.

**Kill (X)** sets `status: dead`, records your reason (optional) under `## Outcome`, and moves it to `done/`.

If a destination name is taken, `-2`, `-3` and so on is added. Nothing is overwritten.

### Batch and merge
Open a stack and tick the notes. You can **Keep all**, **Kill all** or **Reference all** as one undoable step. Batch Promote is not offered. **Merge into one** makes a new `cooking` note dated today with one section per source (newest first), marks the sources `merged`, and moves them to `done/`. You can promote or reference the merged note in the same step. Up to 200 notes at a time. In a stack, `Enter` only submits after you have deliberately chosen an action.

### Undo
`Z` undoes the newest of the last 20 changes (decisions, batch, merge, restore, project status, done means), newest first. The list lives in memory and is cleared when the server restarts. There is no redo. Undo refuses, changing nothing, if a file was edited since or now sits behind a symlink.

All changes are all-or-nothing: they are checked before anything is written and rolled back if a step fails.

## 5. Working with projects

Click a card (select it again or press `Esc` to close). The panel shows:
- **Done means**, the clock, the last five commits, the last five pull requests, and an excerpt from the README or NOTES.
- **Mark shipped**: sets `status: shipped` and a `shipped:` date.
- **+7 days**: pushes the due date a week (not for experiments or undated folders).
- **Browse repo**: opens the read-only repo viewer (if the project has a GitHub origin). Otherwise **Open folder** opens it with `xdg-open` (only from a browser on the server's own machine).
- **Kill**: asks for a second click to confirm.

**Editing done means:** Edit (or Fill in), then Review change (before and after), then Confirm and save. Maximum 600 characters. A red "Define done means" on a card means none is set. The save is refused if the file changed since you opened it.

**Giving a project a ship-by date:** add `- due: YYYY-MM-DD` to the note's header lines (the `- key: value` lines under the title, before the first `##` heading).

Git facts refresh every 20 seconds; use **Refresh** to re-read them.

## 6. Repo viewer
From **Browse repo** or a Pulse repo row: README, files, last 15 commits, 10 pull requests, 15 open issues. It is read-only and fetched through `gh` on the server, so private repos work on your phone too. Only repos owned by the signed-in `gh` user can be opened. Text files up to 300 KB are shown.

## 7. Keyboard shortcuts

- `j` / `k`, arrows: Move
- `h` / `l`, `←` / `→`: Scroll the Projects board left and right
- `p` `r` `c` `x`: Promote, Reference, Keep, Kill
- `Enter`: Apply (`Ctrl+Enter` in a text box)
- `Esc`: Cancel or close
- `z`: Undo
- `y`: Copy the note's full path
- `/`: Search
- `Ctrl K`: Search everything and commands
- `1` to `4`: Switch pages
- `?`: Show all keys

Drafts in decision forms and the done-means editor survive switching notes or pages, but not a browser reload.

## 8. Appearance and phone use
Dark mode follows your system; the rail toggle overrides it. Below 800px wide the app uses a phone layout with a bottom tab bar and the decision panel as a bottom sheet. Fonts are bundled, so it works offline.

## 9. Safety notes
- **No login.** With `--host 0.0.0.0`, anyone who can reach the port can read and change your notes and read your private repos through the viewer. Keep it on a trusted network.
- Every action is appended to `triage.log` (next to `server.py`). Pulse's "decided" counts come from it.
- Writes outside the notes and projects folders, or through symlinks, are refused. Symlinked project folders are listed read-only.

## 10. Troubleshooting

- Port in use: Use `--port N`, or `--restart` if it is an old triage server
- Banner asks to reload: The server was updated. Reload the tab
- No PRs, Pulse graph or repo viewer: Sign in with `gh auth login`
- Open folder does nothing on the phone: It only works from a browser on the server's machine
- A decided note is still in the Inbox: Check the **Stuck** filter
- Launcher shows a blank window: Run `rawtriage-web --restart` (the wrapper from the launcher section) or restart the `rawtriage-web` systemd user service
