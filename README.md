# rawnotes-triage

Local website for reviewing the Markdown notes in `~/Projects/RawNotes` and
pushing each one to its next step: a project, the reference folder, back
in the pot, or the archive.

- **Status:** active (version shown in the rail footer and the browser tab)
- **Stack:** Python 3 standard library (`server.py`, `safefs.py`, `notemeta.py`) + one `index.html`, no dependencies
- **Run it:** `python3 server.py` → http://127.0.0.1:8765
- **Repo:** github.com/Kb4Zod/rawnotes-triage (public)

## Why it exists

Notes pile up. Goal: every promoted note is shipped or killed within 3–7 days.

## Run

```
python3 server.py                     # starts http://127.0.0.1:8765 and opens the browser
python3 server.py --port N            # different port
python3 server.py --no-browser
python3 server.py --host 0.0.0.0      # reachable from the phone (home Wi-Fi / Tailscale); no login
python3 server.py --notes /tmp/copy/RawNotes --log /tmp/copy/triage.log --port 8766   # try it on a copy
python3 server.py --restart           # stop the server already on the port (e.g. an old version), start this one
python3 server.py --app               # same, but in its own app window instead of a browser tab (Omarchy)
python3 server.py --service           # for the systemd unit: no browser; exits with an error if the port is taken, so systemd retries
```

`rawtriage-web` is a one-line wrapper around `python3 server.py` that lives in
`~/.local/bin` on the author's machines. It is not part of this repo yet; if you
make one, every flag above works through it. The Omarchy launcher app (below)
needs it.

Defaults: port `8765`, host `127.0.0.1`, notes in `~/Projects/RawNotes`,
`triage.log` next to `server.py`. The **projects root is the parent of the
notes folder**: with `--notes /tmp/copy/RawNotes`, promotions land in
`/tmp/copy/active/`, `/tmp/copy/experiments/` and so on.

If a triage server is already on the port, startup just opens it (and warns,
in the terminal and with `notify-send`, when its version differs). `--restart`
stops it first, but only after two checks: the port must serve the triage
page or `/api/version`, and the process that owns the port (found with `ss`
and `/proc`) must be a `server.py` of this app. Anything else on the port is
left alone and the command exits.

`index.html` is read once at startup; after editing it, restart. A tab left
open across a restart shows a banner asking to reload when the page and the
server versions differ.

Optional tools (the app works for note triage without them):

- `git`: branch, commits and GitHub-remote badges on the Projects page
- the GitHub CLI (`gh`), signed in: pull requests, the Pulse commit graph, the repo viewer. See [GitHub sign-in](#github-sign-in)
- `xdg-open`: the **Open folder** button
- `notify-send`: the old-server warning as a desktop notification

## GitHub sign-in

The app has no GitHub login of its own, and no account is built in. Every GitHub feature (pull requests, the Pulse
commit graph, the repo viewer) works by running the **GitHub CLI, `gh`**, on the machine that runs the server. So
**`gh` is required for those features, and signing in with it is how a person logs in.** Without `gh`, notes triage
and the git-only parts (branch, commits, the **On GitHub** badges, which only read each project's `origin` remote)
still work; the GitHub parts say why they are unavailable.

1. Install `gh` ([cli.github.com](https://cli.github.com)).
2. Sign in once: `gh auth login`.
3. Start (or restart) the server. The bottom of the left rail shows **Signed in as `<your login>`**, or **Not signed in
   to GitHub**, with a link to the fix.

Whichever account `gh` is signed in as is the account the app acts as: the commit graph counts that account's
commits, and the repo viewer opens only that account's own repositories.

**Signing in with a token instead.** Click the status line in the rail to open the GitHub dialog and paste a personal
access token (best fine-grained, read-only Contents, Pull requests and Issues; a classic token needs `repo`, which also
allows writes). The server checks it
with GitHub, and only if it works saves it to `~/.config/rawnotes-triage/github-token` (`$XDG_CONFIG_HOME` is
honoured; folder `0700`, file `0600`). From then on it is handed to `gh` as `GH_TOKEN` in the process environment (never on a
command line) and takes precedence over `gh auth login`. The token is never sent back to the page. **Remove saved
token** deletes the file and returns to `gh`'s own login. The `gh` CLI is still needed: a token replaces the login,
not the CLI. A `GH_TOKEN` / `GITHUB_TOKEN` already in the server's environment is also honoured, and shown as the
source in the dialog. Saving or removing a token only works from the machine running the server, not from a phone
on the network; there the dialog offers no token field, so a token is never typed into a page served over the LAN.

## Omarchy launcher app

Needs Omarchy and the `rawtriage-web` wrapper in `~/.local/bin` (the installer checks for both and stops if either is
missing). The wrapper is one line; make it executable (`chmod +x ~/.local/bin/rawtriage-web`):

```
#!/usr/bin/env bash
exec python3 "$HOME/Projects/active/rawnotes-triage/server.py" "$@"
```

> **To do before installing this on another computer:** the wrapper is not in the repo yet, so a fresh machine needs
> the manual step above. Planned fix: ship `bin/rawtriage-web` in the repo and have `install-webapp.sh` put it in
> `~/.local/bin` (and `--remove` take it out). Do that first, then deploy.

One-time setup, run from the repo:

1. `./install-webapp.sh` — adds **RawNotes Triage** (with its icon) to the app launcher and starts the server as an
   always-on systemd user service (`rawtriage-web.service`, starts with your desktop session). It checks everything
   first and changes nothing if a check fails, e.g. the main checkout isn't on 2.08 yet (`git pull` there), or a
   server you started by hand is still running (stop it first). It always installs and runs the main checkout
   (`~/Projects/active/rawnotes-triage`), wherever you run the script from.
2. Press `SUPER + SPACE`, type `RawNotes Triage`, Enter. The page opens in its own window, not a browser tab,
   and always full screen. `SUPER + F` leaves full screen (and goes back), as for any window.

- **Full screen** comes from a Hyprland window rule the installer appends to `~/.config/hypr/hyprland.lua`, between
  `-- >>> rawnotes-triage` markers (backup: `hyprland.lua.bak.rawnotes-triage`). Problems with the rule are only
  warnings: the app still installs, just not full screen. Over SSH (no Hyprland to ask) the rule is added and
  applies at next login. It matches only this app: `--app` opens
  `/triage`, which gives the window its own class (`chrome-127.0.0.1__triage-Default`). It uses
  `fullscreen_state = "2 0"` because Chromium undoes a plain `fullscreen = true` as soon as the window opens.
- Opening another window on the same workspace takes the app out of full screen (Hyprland's normal behaviour).
- If the service is down, the launcher starts a server itself, so you never get a blank window.
- **After updating the app:** `rawtriage-web --restart` (or `systemctl --user restart rawtriage-web`). When the
  running server belongs to the service, `--restart` restarts the service instead of stopping it.
- If a server you started by hand holds the port, the service waits and tries again every 30 s.
- Launcher entry only, no service: `./install-webapp.sh --no-service`. Undo everything: `./install-webapp.sh --remove`.
- The launcher entry runs `rawtriage-web --app`, not `omarchy-launch-webapp`, so it doesn't show up in Omarchy's
  *Remove Web App* menu. Use `--remove` instead.

## Words (same in the web app and RawNotes/CLAUDE.md)

Places: **Inbox** (undecided notes in `RawNotes/`), **Projects** (promoted
work on a ship-by clock), **Archive** (`RawNotes/done/`), **Pulse** (stats).
Decisions: **Promote**, **Reference**, **Keep**, **Kill**.

Statuses on disk: `open` → `cooking` → then `promoted` (both for a project
and for a reference copy; the Archive tells them apart by where the note
went), `dead` (killed), or `merged`. Project files add `shipped` and `done`.
Every project has a **done means** and, unless it is an experiment, a **ship
by** date.

## Inbox

Lists `YYYY-MM-DD-<topic>.md` files directly in `RawNotes/` (not in
subfolders), oldest first, split into **Waiting 2+ weeks** and **This
fortnight**. A note's date and age come from its filename.

Filters: All / Open / Cooking / **Stale** (older than 60 days and still `open`
or without a status) / **Stuck** (the status says decided, e.g. `promoted`,
but the file never left the Inbox; the chip appears only when there is one),
plus a type filter (question / research / issue). Search matches title,
filename and the first paragraph of the question.

The reader shows the whole "What's the question / problem?" section, then
the rest of the note with the title and header lines removed.

**Stacks:** three or more notes in the current filter whose slugs start with
the same two words (`arcane-bomb-*`) collapse into one stack. Searching turns
stacks off. Opening a stack (click, `Enter` or `Space`) changes nothing; it
ticks all members and shows the batch form. Untick what should stay.

## Decisions per note

| Button (key) | What happens |
|--------------|--------------|
| Promote (P)   | Copies the note to `../active/<slug>/NOTES.md` or `../experiments/<slug>/NOTES.md` (`learning/` and `templates/` get a flat `<slug>.md`). Stamps `status: promoted`, `promoted: <today>` and a `## Plan` block with your **done means** (required for every destination). Active / Learning / Template also get `due:` (3, 7, 14 or 30 days, or an exact date; default 7). **Experiments are open-ended:** no `due:`, and any old one is removed. The capture is marked promoted, gets the destination under `## Outcome`, and moves to `done/`. |
| Reference (R) | Copies the note to `../reference/<slug>.md` with YAML frontmatter (created, promoted, status, type, source, optional why, tag `reference`) so Obsidian shows it as properties. Capture marked promoted and moved to `done/`. **Does not** touch `reference/README.md`; the form reminds you to add it by hand if it belongs in the index. |
| Keep (C)      | Sets `open` or `cooking` (the form defaults to the note's current one) and optionally adds a dated `next step` line under `## Notes`. Stays in the Inbox. |
| Kill (X)      | `status: dead`, your reason (optional; "no longer useful." if blank) under `## Outcome`, moved to `done/`. |

If the destination name is taken, a `-2`, `-3`… suffix is added; nothing is
overwritten. The API also accepts the 1.x names `durable` (Reference) and
`archive` (Kill). Promote creates files and folders only: no `git init`, no
GitHub repo.

**Batch:** with a stack open, tick notes and **Keep all**, **Kill all** or
**Reference all** as one undoable step (batch Promote is not offered), or
**Merge into one**: a new `cooking` note dated today, one section per source
newest first, each source marked `merged`, pointed at the merge and moved to
`done/`. The merged note can be promoted or filed as reference in the same
step. Batch and merge take up to 200 notes. On a stack, `Enter` only submits
after you chose the action on purpose (a click or `p` `r` `c` `x`).

**Undo (Z):** the newest 20 changes made through the page (decisions, batch,
merge, restore, project status, done means) can be undone, newest first,
while the server runs. The list is in memory: a restart empties it, and there
is no redo. Undo checks every file it would touch first and refuses, changing
nothing, if one has been edited since or now sits behind a symlink or outside
the notes and projects folders.

Changes are all-or-nothing: notes are checked before anything is written (a
note listed twice, a missing one, a bad date), each file is replaced
atomically (permissions and undecodable bytes survive; a consistently CRLF
file stays CRLF, mixed endings become LF), and if a step fails half-way the
steps already taken are rolled back. If a rollback cannot finish, an
"unfinished" entry stays on the undo list so `Z` can complete it.

## Archive

Everything in `done/`, newest first, filtered by Promoted / Reference /
Killed / Merged (worked out from the status and the `## Outcome` line).
**Restore to Inbox** sets the note to `cooking` and moves it back; it refuses
if a note with that filename is already in the Inbox, and it does not remove
a project or reference copy made earlier.

## Projects page

One board for everything promoted. Cards come from two scans of the sibling
folders:

- every folder directly under `active/` and `experiments/` (hidden ones
  skipped), whether or not it was promoted through this app;
- every `.md` file directly in, or one level below, `active/`, `experiments/`,
  `learning/`, `templates/` and `maintained/` whose header has a `due:` line
  (first 4,000 bytes are read), plus experiment files with a `promoted:` line
  and no `due:`.

Columns, left to right: **Overdue**, **Due this week** (0–7 days left),
**Later**, **No ship date** (an `active/` folder with no dated file),
**Experiments** (open-ended), **Closed** (status `shipped`, `done` or
`dead`). Empty columns are hidden; on a narrow screen the board scrolls
sideways. Each dated card shows a countdown ring (red = late, amber = 2 days
or less). Folder cards show Git state: **On GitHub** (an `origin` tracking
ref exists), **GH ↑N** (commits on `HEAD` not on any `origin` ref), **Not
pushed**, **Local git** or **No git**. These are read from the local clone,
never from GitHub. Header tiles count overdue, due this week, active folders
without "done means", and active folders on GitHub.

Select a card to open its panel in a column beside it (select it again, or
`Esc`, to close it): **done means**, the clock, the last five commits (from
`git log`), the last five pull requests (from `gh`), and an excerpt from the
README or NOTES. Excerpts and commit titles are quoted from the source, not
summarised. Buttons: **Mark shipped** (`status: shipped` + `shipped:` date),
**+7 days** (pushes `due:` from today or the current due date, whichever is
later; not offered for experiments or undated folders), **Browse repo** when
the project has a GitHub origin, otherwise **Open folder** (launches
`xdg-open` on the server's machine; only for a browser on that machine, and
hidden on the phone layout), and **Kill** (asks for a second click). Shipping
or killing a folder that has no dated file writes into its `NOTES.md`,
creating it if needed. All these writes can be undone. Git facts are cached
for 20 seconds; **Refresh** re-reads them.

**Symlinked project folders** are listed read-only with a note. Every write
must land on a real path inside `RawNotes/` or the projects folders with no
symlink on the way, or it is refused. Undo, rollback and redo check the same
rule again before touching anything. Not covered: another local process
swapping a folder for a link between a check and the write inside one request.

**Done means** is looked for in NOTES, PROJECT, README then SPEC (any case):
the `- definition of done:` line Promote writes, a `Definition of done:`
paragraph, or a "Definition of done" / "What does done look like" heading.
The first real definition wins; empty, `<fill in>`, `TBD` and the like count
as missing and give the card a red "Define done means". **Edit** (or **Fill
in**) → **Review change** (before/after) → **Confirm and save**. Up to 600
characters. If the project has no definition yet, one is added to the `## Plan`
of its `NOTES.md`. The save is refused if the definition on disk no longer
matches the one you were shown.

**Note headers.** A note's metadata (`- date:`, `- status:`, `- type:`,
`- promoted:`, `- due:`, `- shipped:`) is the first run of `- key: value`
lines under the title (after at most three lines of subtitle, before the
first `##` heading; a leading YAML frontmatter block is skipped over). Only
that block is read or edited: a `- status:` line in the body, a quote or a
merged section is just text. Values are read up to the first space, and a
`<placeholder>` counts as empty. To give a project a ship-by date, add
`- due: YYYY-MM-DD` to that header.

Project inspection is otherwise read-only: no fetch, checkout or repository edits.

## Pulse page

- **Commits on GitHub:** a 53-week graph of your commits on the default
  branch of repos you own or collaborate on, counted by *commit date* in the
  server's timezone (not by when they were pushed), so private repos count
  and local-only repos never show. Uses `gh api graphql` with the account
  shown in the rail ([GitHub sign-in](#github-sign-in)); cached 10 minutes; **Refresh** bypasses the cache; a failed
  refresh shows the last good data marked stale. Up to 1,000 repos (most
  recently pushed first) and 2,000 commits per repo are read; if GitHub had
  more, the page says so. The rail shows a 16-week sparkline, total and streak.
- **Inbox flow:** notes captured (by filename date, counting Inbox and
  Archive) vs. decided (promote, reference, kill and merge lines in
  `triage.log`; Keep and Restore do not count) per week, last 8 weeks, with a
  table view. Undone decisions still count as decided.
- **Median wait** of the notes in the Inbox, against the 7-day goal.
- **Where the commits went:** the ten repos you committed to most in the
  last 30 days; click one to browse it.

## Repo viewer

**Browse repo** (Projects panel) and the repo rows on Pulse open a read-only
view inside the page: README, files, the last 15 commits, 10 pull requests
and 15 open issues. The server fetches everything through `gh` with GET
requests only, so a private repo needs no GitHub login in the browser,
including on the phone. It serves only repos owned by the signed-in `gh`
user: a collaborator's repo can appear on Pulse but cannot be opened here.
Results are cached for a minute. Files up to 300 KB of UTF-8 text are shown;
binary or larger files get a notice and a link.

## Network and safety

There is **no login**. Serving beyond localhost (`--host 0.0.0.0`) lets
anyone who can reach the port read and change notes, undo, and read your
private repos through the viewer; keep that to the tailnet or a trusted
network. Guards against a web page you visit driving the server: on a
localhost bind, requests whose `Host` is not localhost are refused; on any
bind, a request from another origin and a non-JSON `POST` are refused.
`/api/open` only answers a browser on the server's own machine and only for
project folders or dated files the board lists. Saving or removing the GitHub
token (`/api/github/token`) is likewise limited to the server's own machine,
and also needs a localhost `Host` on any bind (so a DNS-rebound page can't do it
under `--host 0.0.0.0`).

`triage.log` records every action after it has gone through, best-effort: if
the log cannot be written, the change still happens and the Pulse decided
count is lower than reality.

## Keys

`j`/`k` (or arrows) move · `p` `r` `c` `x` decide · `Enter` apply
(`Ctrl+Enter` in a text box) · `Esc` cancel / close · `z` undo · `y` copy the
note's full path · `/` search · `Ctrl K` search everything and commands ·
`1`–`4` switch pages · `?` all keys. `Enter` and `Space` open a focused row.

What you type in a decision form or the done-means editor survives a
re-render, a look at another note or page, and the data refresh that runs
when you come back to the tab. It does **not** survive a browser reload, and
only an unsaved done-means edit makes the browser ask before closing the tab.
Cancel clears a decision form's draft.

## Look

Palette and type borrowed from Rulecraft's Elven Glass: sage background,
forest green, gold, Cormorant Garamond + Lato + JetBrains Mono. Fonts are
served by the app (`static/fonts`, OFL), so it works offline and on the
phone. Dark mode follows the system; the rail toggle overrides it (stored in
localStorage). Under 800px wide it switches to the phone layout: bottom tab
bar, and the decision panel as a bottom sheet.

**Accessibility.** Text meets WCAG AA contrast in both themes; `main`/`nav`
landmarks, one `h1` per page and no skipped heading levels; stack members
are real checkboxes plus buttons; `Ctrl K` and `?` are modal dialogs (the
page behind is inert, Tab stays inside, focus returns where it was); results
announce through the status region; touch targets are 44px on the phone.
Checked with axe-core 4.10.3 in the browser tests; not yet checked with a
screen reader or on a physical phone.

## Tests

```
python3 -W ignore::ResourceWarning -m unittest                 # API, engine/fault-injection, headers, safety, caches (stdlib only)
AXE_JS=/path/to/axe.min.js python3 -m unittest test_browser    # + accessibility sweeps (axe-core 4.10.3; not part of the app)
python3 loadcheck.py                                           # disposable 100/1000-note load, reads beside writes
```

`test_browser.py` drives Chromium through `cdp.py` (a tiny stdlib DevTools
client) against a disposable server; it is skipped when no Chromium/Chrome is
found (`CHROMIUM=/path` to pick one) and never touches GitHub or your notes.
`TRIAGE_INDEX=/path/to/old/index.html` runs the same checks against another
build of the page.

## Files

- `server.py`: HTTP API, decisions, undo engine, project scan, GitHub access, startup and `--restart`
- `notemeta.py`: note header and section parsing
- `safefs.py`: symlink-safe path checks, atomic writes, read/write lock, stat cache
- `index.html` + `static/fonts/`: the whole UI
- `static/icons/`: app icon (SVG source + PNGs), the favicon and the phone home-screen icon
- `install-webapp.sh`, `systemd/rawtriage-web.service`: the Omarchy launcher app and its always-on service
- `test_*.py`, `testkit.py`, `cdp.py`, `loadcheck.py`: tests and load check
- `.scratch/`: specs and tickets for past and pending work (see `AGENTS.md`)

**Version:** `VERSION` in `server.py`, shown in the rail footer, the tab
title and `/api/version`. When you change the app, bump it by 0.01 and add a
dated entry to `CHANGELOG.md`.
