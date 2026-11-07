# RawNotes Triage: User Guide

RawNotes Triage is a local web app for deciding what happens to each raw Markdown note, then tracking promoted work until it ships or dies. The target is **ship or kill promoted work within 3–7 days**; experiments can remain open-ended.

This guide describes version 2.13. Start with the detailed [First-run Setup](SETUP.md) for folder selection, the Welcome dialog, configuration, your first note, git, GitHub and the optional desktop install. See the [docs index](README.md) for other documentation.

## Vocabulary

- **Inbox:** undecided dated notes directly in the notes folder (usually `RawNotes/`).
- **Projects:** work tracked on the ship-by board, including existing active and experiment folders.
- **Archive:** decided captures in `RawNotes/done/`.
- **Pulse:** commits and Inbox-flow statistics.
- **Promote / Reference / Keep / Kill:** the four decisions.
- **Done means:** one sentence saying what finished looks like.

A note starts `open` or `cooking`; decided captures become `promoted`, `dead` (killed) or `merged`. Reference captures are marked promoted, with their fate recorded in the outcome. Projects can be `shipped` or `done`. The sibling `archive/` folder holds closed project folders; it is separate from the Archive page's captures.

## Starting the app

Requirements: Python 3, standard library only. Written and tested on Linux; macOS and Windows are untested. Desktop extras depend on Linux tools: `xdg-open`, `ss` and `/proc` for restart checks, `notify-send`, and Omarchy/systemd for the launcher and service.

```bash
python3 server.py
```

Open `http://127.0.0.1:8765` if the browser does not open automatically. Leave the terminal running; `Ctrl+C` stops the server.

| Option | Purpose |
| --- | --- |
| `--notes PATH` | Override the saved/default notes folder; its parent is the projects root |
| `--port N` | Use another port |
| `--no-browser` | Start without opening the browser |
| `--host 0.0.0.0` | Listen for other devices; trusted networks only, no login |
| `--restart` | Replace an existing triage server, or restart its service |
| `--app` | Open an Omarchy app window |
| `--service` | Run without a browser; fail if another server holds the port, allowing systemd to retry |
| `--log PATH` | Choose the action log (default: `triage.log` in the app checkout) |
| `--no-commit` | Disable automatic notes commits |

To try it on copies:

```bash
python3 server.py --notes /tmp/copy/RawNotes --log /tmp/copy/triage.log --port 8766
```

Promotions and reference copies go beside that copied notes folder. An existing triage server on the chosen port is reopened, so stop it or use another port when changing folders. If `--restart` restarts the installed service, that service uses its own saved configuration rather than new flags such as `--notes`. Restart after updating the app; a tab with a different version asks you to reload.

### First launch and desktop installation

If the selected notes folder is missing, Welcome asks for its path and creates the folder, `done/` and `_template.md`, then remembers the choice. If the folder already exists, Welcome is skipped and no setup files or saved setting are added automatically. The [setup walkthrough](SETUP.md) explains both cases and what to configure afterward.

On Omarchy, `./install-webapp.sh` adds the launcher, an always-on user service, `rawtriage-web` and `rawtriage` terminal commands, and the bundled agent skill links. Stop a manually started server before installing. From a linked worktree it uses the main checkout. `--no-service` installs the launcher without a service; `--remove` removes the integration. If you move the checkout, rerun the installer. `rawtriage-web --restart` picks up updates. The launcher opens full screen; `SUPER + F` toggles it.

## The four pages

Switch with the rail, phone tab bar, or keys `1` to `4`.

### Inbox

Dated `YYYY-MM-DD-topic.md` files are shown oldest first, grouped as **Waiting 2+ weeks** and **This fortnight**. Age comes from the filename date. Search (`/`) matches title, filename and the question summary. Filters include All, Open, Cooking, Stale, Expired and Stuck, plus note type. Expired and Stuck appear when there are matching notes. Stuck means a decided status whose file never left the Inbox.

**Stale** and **expired** have different effects:

| Default limit | Meaning |
| --- | --- |
| Open for more than 14 days | Stale; time runs from the last Keep, else Restore/capture |
| Cooking for more than 7 days | Stale; same clock |
| More than 30 days in the Inbox | Expired; Keep is refused |
| 3 Keeps used | Expired; Keep is refused |

Keep updates `kept:` and increments `keeps:`. It resets the stale clock, **not** the 30-day limit. Expired notes can still be promoted, filed, merged or killed. One expired member refuses an entire batch Keep. Rows show Keep counts and days left; Pulse shows stale and expired counts. Limits are configurable under [Settings](#settings).

The reader pulls out the question/problem section and hides metadata headers. **Stacks** group three or more notes sharing a two-word slug prefix in the current filter; searching disables stacks. Open a stack to select its members for batch actions; opening it changes no files.

### Projects

Columns are **Overdue**, **Due this week** (0–7 days), **Later**, **No ship date**, **Experiments** and **Closed**. Empty columns disappear; narrow screens scroll sideways. Countdown rings turn amber with 2 days or less and red when late.

The board includes active and experiment folders, promoted learning/template folders, and eligible dated Markdown project files (including older flat-file promotions). Linked git worktrees are excluded; symlinked project folders are read-only. Select a card for its done means, clock, recent commits/PRs and README/NOTES excerpt. See [Working with projects](#working-with-projects).

### Archive

Captures in `done/`, newest first, filtered by Promoted, Reference, Killed or Merged. **Restore to Inbox** moves a capture back as `cooking`, stamps `restored:`, and clears its Keep count: it gets a fresh Inbox clock and three Keeps by default. It refuses an Inbox filename collision. It leaves any project or reference copy in place.

Unindexed reference notes also appear here with **Add to index**, so you can add a section and summary after filing.

### Pulse

- **Commits on GitHub:** a 53-week grid of commits on default branches, including private repos (requires `gh`). Commit date, rather than push date, determines the day. Local-only repos do not appear. Results cache for 10 minutes; Refresh bypasses it.
- **Inbox flow:** captured vs. decided per week for eight weeks. Promote, Reference, Kill and Merge count as decisions; Keep, Restore and new-note creation do not. Undo does not subtract a decision from this log-based count.
- **Median wait:** current Inbox age against the 7-day goal, plus stale/expired counts.
- **Where the commits went:** the ten most active repos in the last 30 days; select one to browse it.

If activity limits truncate the data, the page says so. A failed refresh can show the last good data marked stale.

## Triage workflow

1. Move through the Inbox with `j`/`k` or arrow keys.
2. Read a note and choose a decision by button or key.
3. Complete the form; `Enter` applies it (`Ctrl+Enter` inside a text box), `Esc` cancels.
4. Press `Z` to undo a mistake while its undo step is still available.

### Promote (P)

Choose Active, Experiment, Learning or Template, and write **done means** (required). Active, Learning and Template need a ship-by date: 3, 7, 14 or 30 days, or an exact date, defaulting to 7 days in the page. Experiments get no due date; any old due line is removed.

The app makes `<destination>/<slug>/` beside the notes folder using `starter/skeletons/`: README, AGENTS.md, CLAUDE.md, .gitignore and a `NOTES.md` holding only the plan (done means, first step, check-in), plus the destination's working folder (`.scratch/`, `notes/` or `template/`). The project's repo may go public one day, so nothing private goes into it: the status, dates, `github: private` and a link to your original note go to the project's [board record](#board-records-private-to-rawnotes) in RawNotes. The original records the destination and moves to `done/`, where it stays; agents working in the project read it with `rawtriage brief <slug>`.

It also initializes git on `main` and attempts a first commit, `Start <slug> (promoted from RawNotes)`, with your normal hooks. The default requires a git-config email ending in `@users.noreply.github.com`. No git, an unsuitable identity or a refused commit produces a warning; the project still exists. The app never changes your identity. **Promotion does not create a GitHub repo or push it.**

Undo can remove the project repo only while it still matches the initial state: later commits, added refs, changed/untracked files or an added remote can prevent it. Undo of a promotion that initialized git requires localhost.

### Reference (R)

Files a copy in `reference/<slug>.md` with Obsidian-compatible YAML properties, marks the capture promoted and moves it to `done/`.

Pick an **Index section** (existing or new) and a one-line summary to add `- [Title](slug.md): summary` beneath `## Index` in `reference/README.md` in the same undoable step. **Don't index** files without adding the link. Missing README or `## Index` means the note is still filed, with a warning. Setup does not create the reference index; see [preparing it](SETUP.md#8-prepare-the-reference-index-if-you-want-one).

### Keep (C)

Leaves the note in the Inbox as `open` or `cooking`, optionally with a dated next step under `## Notes`. It updates the Keep count and date; once expired, Keep is refused.

### Kill (X)

Sets `status: dead`, writes the reason under `## Outcome`, and moves the capture to `done/`. The page permits an empty reason (using a default); the CLI requires `--reason`. Killing a **project** requires a reason.

Taken destination names get `-2`, `-3` and so on; existing files are not overwritten.

### Batch and merge

Open a stack, then tick the notes to change. **Keep all**, **Kill all** and **Reference all** are one undo step; batch Reference supports an index section. Batch Promote is not offered.

**Merge into one** creates a `cooking` note dated today, with one section per source, newest first. Sources are marked `merged`, linked to the result and moved to `done/`. You can promote or file the result in the same step. Up to 200 notes can be processed at once. In a stack, `Enter` submits only after you deliberately choose an action.

### Undo and automatic commits

`Z` reverses the newest of the last 20 tracked changes, including changes made through the CLI. Undo is shared across clients, lives in server memory and disappears on restart. There is no redo. Files are checked first: edits since the action, symlinks or an unsafe path cause refusal. Moves have additional checks described below. GitHub repo creation and its push are external actions and have no undo step.

Changes are validated and rolled back if a step fails. If rollback cannot finish, an unfinished entry stays on the undo list; follow the error's instructions instead of blindly repeating the change.

When the notes folder belongs to a git repo, successful changes auto-commit only touched notes-folder paths, including both sides of a move. Other staged/edited files stay alone; hand edits in a touched note are included and named in the commit body. Undo creates another commit; it never rewrites history. Hooks run normally. A skipped/failed commit does not reverse the action or remove its undo step: read the toast for the reason.

Project-folder changes and reference copies/index edits are not included in the notes auto-commit. Board records are: they live in RawNotes. Promotion's first project commit is separate. Disable auto-commit with `--no-commit` or `"autocommit": false` in config.

## Working with projects

Select a card again or press `Esc` to close its panel. Git facts cache for 20 seconds; **Refresh** rereads them.

**Done means:** Edit/Fill in → Review change (before/after) → Confirm and save. Maximum 600 characters. Empty placeholders count as missing and show **Define done means**. Saving is refused if the definition changed on disk. To give an undated project a ship-by date, add `- due: YYYY-MM-DD` to the header of its board record, `RawNotes/projects/<kind>/<folder>.md` (make the file if there is none: `# <folder>`, a blank line, then that line), never to its `NOTES.md`.

**+7 days:** asks for a reason of at least 10 characters, records it under `## Extensions` in the board record, and increments `extensions:` there. The new due date is seven days after today or the existing deadline, whichever is later. Not offered for open-ended experiments or undated folders. After two extensions by default, the button reads **No more extensions**; an overdue project with no extensions left shows **Decide**.

### Board records (private to RawNotes)

Every board action (Mark shipped, Kill, +7 days, Next round, close out) writes the project's **board record**, `RawNotes/projects/<kind>/<folder>.md`, and leaves the project folder itself untouched, so it never leaves a change to commit or trips a "clean tree" check. The one exception is the new done means of a Next round: that is the plan, so it goes in the project. The record holds the status, dates, `github:`, extensions with their reasons, rounds, moves, the kill reason and a link to the original note. It is undoable and auto-committed like any note, and a close-out move renames it with the folder. A folder that was never promoted gets one the first time you ship or kill it.

Projects promoted before 2.15 keep their board data in `NOTES.md` until something moves it: the first board action on such a project does, in the same undo step, and **Pulse → Board data in project repos → Move it to RawNotes** (or `rawtriage migrate-board-data`) does it for all of them at once. Your own text in `NOTES.md` stays; sections still exactly as in the original note are dropped, since the original stays in `done/`, and the report names each `NOTES.md` that still has sections besides the plan, so you can read it before committing. The bulk move works only from the machine running the server. The app never commits in a project's repo: commit each trimmed `NOTES.md` yourself. The old data stays in the repo's git history; the report says so and names the repos that are public on GitHub.

### Close out a round or a project

**Mark shipped** on a project folder asks what happens next:

| Choice | Result |
| --- | --- |
| **Next round** | Keeps the folder, records the finished round under `## Rounds` in the board record, sets a new done means (in the project) and ship-by date (experiments may stay open-ended), resets extensions |
| **Maintained** | Marks shipped/closed and moves to `maintained/<name>/` |
| **Archive** | Marks shipped/closed and moves to `archive/<name>/` |
| **Kill** | Requires a reason, marks dead/closed and moves to `archive/<name>_abandoned/` |

Maintained is suggested when there is a GitHub origin; otherwise Archive is suggested. Older folders already marked shipped/done show **Close out…**. A single-file project is marked shipped in place. Closed shows the last seven days by default, including recent moves; **Show all (N)** expands the older closed projects still in active/experiments.

**What points here** checks systemd user units, running processes, git worktrees, `~/.local/bin` links/scripts and crontab entries. Any blocker refuses a move and gives a fix. Agent-memory moves and updating the T3 Code project path are to-dos: they do not block, are listed in the result and recorded under `## Moved` in the board record. You do them yourself. If available, `sync-apps` runs after a move or its undo to refresh Obsidian links; failure does not reverse the move.

Moves use a rename on the same filesystem. Undo checks the folder for changed/added/removed contents (excluding `.git` internals), a reused old name, and new dependencies. It lists reverse to-dos too. Folders over 200,000 entries or partly unreadable may move but cannot be undone by the app; the close-out form warns before you submit. Folder moves and their undo require localhost.

### Put work on GitHub

Git badges come from the local clone. **On GitHub** means origin tracking refs exist; **GH ↑N** counts commits on any local branch (plus a detached HEAD) that are not on those refs. **Not on GitHub** appears for a promoted project with no GitHub origin once it has work beyond the skeleton commit or is at least two days old.

A local repo with **no origin** offers **Create private GitHub repo** on the server's own desktop page. It requires signed-in `gh`, a clean tree, a first commit and the `main` branch. It creates a private repo under your signed-in account, adds origin and pushes main with hooks enabled. If creation succeeds but the push fails, the message explains how to finish manually; do not try to create the repo again.

For **active** and **templates** folders, close out enforces these rules:

- **Maintained:** requires a GitHub origin that has received a push, with no commits on any local branch missing from origin's tracking refs.
- **Archive:** the same requirement if any work commit exists beyond the promotion skeleton (or any commit when there is no skeleton). No repo, no commits or only the skeleton can archive without GitHub.
- **Next round** and **Kill:** no GitHub gate.

Learning and experiments are not gated. This checks committed history and pushes; uncommitted/untracked files move with the folder, so commit and push work you want preserved remotely first. A refusal says **Push it first** and changes nothing.

## Terminal and agent use

Use `bin/rawtriage` from the checkout, or `rawtriage` after launcher installation. Commands go through the same server rules, undo list, log and auto-commit as the page. If no server is running, the CLI tries the installed service, otherwise a detached server.

```bash
rawtriage where
rawtriage new dns-check "Why is DNS slow?"
rawtriage -l
rawtriage -l --summary
rawtriage check
rawtriage keep dns-check --next "Compare resolver timings"
rawtriage promote dns-check --dest active --done "Resolver delay measured and fixed" --due-days 7
rawtriage reference --unindexed
rawtriage index resolver-notes.md --section "Networking" --blurb "Resolver timing checklist"
rawtriage project deps dns-check --to maintained
rawtriage project github dns-check
rawtriage project extend dns-check --reason "Need another measured sample"
rawtriage project close dns-check --outcome next --done "Verify the fix on Wi-Fi" --due-days 7
rawtriage project close dns-check --outcome maintained
rawtriage project kill dns-check --reason "No longer needed"
rawtriage brief dns-check                 # the original note and the plan, for an agent in the project
rawtriage migrate-board-data              # move pre-2.15 board data out of every NOTES.md
rawtriage undo
```

These are examples of separate choices, not a script to run in sequence. File arguments accept a filename, absolute path or unique partial name. Promote needs `--done` and an explicit `--due-days`/`--due` except for experiments. Use `--kind` when project names collide. `--json` prints the server response; `RAWTRIAGE_PORT` selects a nondefault port. See the [full command reference](../README.md#commands) and `bin/rawtriage --help`.

Exit codes: **0** success; **1** refused/bad usage (or a read-only command the server failed on: nothing changed); **2** no usable server, no change; **3** lost connection after sending a change, which may have completed; **4** server failure (HTTP 500), potentially part-way through. For 3 or 4, inspect the page/list and message before retrying. Promote, merge, project status/close/GitHub and undo wait up to 300 seconds; other requests wait 60 (`RAWTRIAGE_TIMEOUT` overrides both).

With no arguments, `rawtriage` launches a Claude triage session. The bundled `rawnotes` skill also supports capture and triage from other agents; the installer links it into Claude, Codex and shared agent skill directories.

## GitHub sign-in and repo viewer

Run `gh auth login` on the server machine, or save a token through the rail's GitHub dialog on localhost. `gh` is required in both cases. The dialog shows the account and credential source; saved token takes precedence over environment/CLI login. Removing it returns to the remaining credentials. Read access supports browsing; creating/pushing repos needs write permissions too. See [GitHub setup](SETUP.md#7-connect-github-if-you-want-github-features).

**Browse repo** (Projects) or a Pulse repo row opens README, files, the last 15 commits, 10 PRs and 15 open issues. This viewer is read-only and fetched through the server's `gh`, so private repos work on your phone without a browser login. Only repos owned by the signed-in account can be opened, even though collaborator repos can appear on Pulse. Text files up to 300 KB are shown; binary/larger files link out. Results cache for one minute.

## Settings

Edit `~/.config/rawnotes-triage/config.json`, preserving existing keys, then restart. `RAWTRIAGE_CONFIG_DIR` overrides the directory; otherwise `$XDG_CONFIG_HOME/rawnotes-triage` is used when set. Missing/invalid values fall back to defaults.

| Key | Default | Meaning |
| --- | --- | --- |
| `notes` | `~/Projects/RawNotes` when no choice is saved | Absolute notes path; `--notes` wins |
| `autocommit` | `true` | Commit touched notes; `--no-commit` also disables it |
| `noreply_suffix` | `@users.noreply.github.com` | Required email suffix for promotion's first project commit |
| `stale_open_days` | 14 | Stale open-note threshold |
| `stale_cooking_days` | 7 | Stale cooking-note threshold |
| `inbox_limit_days` | 30 | Inbox age limit for Keep |
| `max_keeps` | 3 | Keep count limit |
| `max_extensions` | 2 | Allowed deadline extensions |
| `closed_window_days` | 7 | Recent Closed-column window |

Inbox limits must be whole numbers at least 1. `max_extensions` and `closed_window_days` allow 0. The notes path alone is remembered by Welcome; it does not write a full set of default settings.

## Keyboard shortcuts

- `j` / `k`, arrows: move.
- `h` / `l`, left/right arrows: scroll the Projects board.
- `p` `r` `c` `x`: Promote, Reference, Keep, Kill.
- `Enter`: apply; `Ctrl+Enter` inside a text box.
- `Esc`: cancel/close.
- `z`: undo; `y`: copy note path.
- `/`: search; `Ctrl K`: search everything and commands.
- `1`–`4`: switch pages; `?`: show all keys.

Decision and done-means drafts survive switching notes/pages and data refreshes, but not browser reloads. Cancel clears a decision draft. Only an unsaved done-means edit prompts before closing the tab.

## Appearance and phone use

Dark mode follows the system; the rail toggle overrides and remembers it. Fonts are bundled. Under 800px wide, the page uses bottom tabs and a decision bottom sheet.

Basic triage and repo viewing work on a trusted network. Setup, token changes, opening a folder, creating a GitHub repo and moving project folders require a browser on the server machine using a localhost address. **Next round** works remotely because nothing moves. Undo of a folder move or a promotion that made a git repo also requires localhost. Since undo is newest first, such an entry can block undo from your phone until handled on the server machine.

## Safety and troubleshooting

There is **no app login**. Anyone who can reach the listening port can read/change notes and view private repos through your server credentials. Use localhost or a trusted network. Writes through symlinks or outside the configured roots are refused. `triage.log` records actions best-effort; an unwritable log does not reverse the change and can undercount Pulse activity.

| Symptom | What to check |
| --- | --- |
| Welcome on every restart | Config could not be saved, or the server/service uses another config directory; see [setup](SETUP.md) |
| No Welcome on first launch | The selected notes folder already exists; this is expected |
| Empty Inbox | Use dated `YYYY-MM-DD-topic.md` names directly in the notes folder; run `rawtriage where` and `rawtriage check` |
| Port in use | Use another port, or `--restart` for an existing triage server |
| New flags seem ignored | An existing server was reopened. Stop it or use another port for new command-line flags. For the installed service, change saved config and restart, or stop the service before a manual run |
| Reload banner | Reload after the server update |
| Keep refused | The note exceeded the Inbox age/count limit; promote, file, merge or kill it |
| Auto-commit warning | The decision still happened; check git identity, hook output, locks or an in-progress merge/rebase |
| Missing PRs/graph/viewer | Check `gh auth status` and the rail's credential source; a saved token can override CLI login |
| Close out blocked | Resolve **What points here**, or **Push it first**; rerun the action |
| Undo refused | Read the changed-file/dependency reason; for a move or initialized repo, use localhost |
| Phone cannot open folder/move project | These operations require localhost; the Open folder button also needs `xdg-open` |
| Launcher fails to load | Run `rawtriage-web --restart`, then check `systemctl --user status rawtriage-web` |
