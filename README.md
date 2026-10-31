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
python3 server.py --no-commit         # don't auto-commit the notes folder (see "Auto-commit" below)
```

`bin/rawtriage-web` is a small wrapper around `python3 server.py`; every flag
above works through it. The Omarchy launcher installer (below) links
`~/.local/bin/rawtriage-web` to it, so after installing you can type
`rawtriage-web` from anywhere.

Defaults: port `8765`, host `127.0.0.1`, notes in the folder chosen on first run (see [First run](#first-run)), else `~/Projects/RawNotes`,
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

## First run

If the notes folder doesn't exist yet (a fresh install), the page opens with a **Welcome** dialog asking where your
notes should live, suggesting `~/Projects/RawNotes`. It can't be dismissed: there is nothing to show until it's done.
**Create notes folder** makes the folder (an existing folder is used as it is) with a `done/` folder for the Archive
and a `_template.md` for new notes (copied from `starter/_template.md`, never over one you already have). The parent
of the folder becomes the projects root, so it must be a full path at least two levels deep (not `/notes`). The
choice is saved in `~/.config/rawnotes-triage/config.json` (`$XDG_CONFIG_HOME` and `RAWTRIAGE_CONFIG_DIR` are
honoured) and used on every later start, including by the systemd service. `--notes` still wins over it.

The dialog only appears while the folder is missing, and setup only works from the machine running the server: a
phone on the network is told to do it there. To move to another folder later, start with `--notes`, or edit
`config.json` and restart.

**Settings in `config.json`.** Every key is optional; a missing file, bad JSON or a value of the wrong kind means the
default. Restart after editing.

| Key | Default | What it does |
|-----|---------|--------------|
| `notes` | (first run) | the notes folder, a full path |
| `autocommit` | `true` | `false` turns off committing the notes folder (see **Auto-commit**); so does `--no-commit` |
| `noreply_suffix` | `@users.noreply.github.com` | the email ending a promoted project's first commit needs (see **Project skeleton**) |
| `stale_open_days`, `stale_cooking_days` | 14, 7 | when an Inbox note counts as stale (see **Inbox limits**; 1 or more) |
| `inbox_limit_days`, `max_keeps` | 30, 3 | when an Inbox note expires and Keep is refused (1 or more) |
| `max_extensions` | 2 | how many times a ship-by date can be pushed (see **Projects page**) |
| `closed_window_days` | 7 | how far back the Projects board's **Closed** column looks |

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

Needs Omarchy. Clone the repo anywhere you like; the installer works out where it is.

One-time setup, run from the repo:

1. `./install-webapp.sh` — adds **RawNotes Triage** (with its icon) to the app launcher and starts the server as an
   always-on systemd user service (`rawtriage-web.service`, starts with your desktop session). It checks everything
   first and changes nothing if a check fails, e.g. the checkout is older than the installer (`git pull` there), or a
   server you started by hand is still running (stop it first). It installs and runs the checkout the script is in;
   run from a linked git worktree (a feature branch), it uses the main checkout of that repo instead, so the
   always-on service never runs a branch. It also links `~/.local/bin/rawtriage-web` to `bin/rawtriage-web`
   there. A hand-made wrapper from before 2.12 is backed up (`rawtriage-web.bak.rawnotes-triage`) and replaced;
   any other file with that name stops the install, untouched. `~/.local/bin/rawtriage` is linked to `bin/rawtriage`
   the same way (a hand-made bash `rawtriage` is backed up as `rawtriage.bak.rawnotes-triage`); see **Commands**.
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
- Launcher entry only, no service: `./install-webapp.sh --no-service`. Undo everything: `./install-webapp.sh --remove`
  (it removes `~/.local/bin/rawtriage-web` and `~/.local/bin/rawtriage` only if they are the installer's links).
- **Moved the checkout?** Run `./install-webapp.sh` again from the new place: the service file and the link are
  rewritten to point there.
- The launcher entry runs `rawtriage-web --app`, not `omarchy-launch-webapp`, so it doesn't show up in Omarchy's
  *Remove Web App* menu. Use `--remove` instead.

## Commands

`rawtriage` (`bin/rawtriage`, installed to `~/.local/bin/rawtriage` by `install-webapp.sh`) triages from a terminal or
an agent. It never edits notes itself: every command goes through the running server, so a decision made here has the
same rules, lock, undo list and `triage.log` line as one made in the page (and shows up in Pulse's "decided").

```
rawtriage                          # a Claude triage session (uses the rawnotes skill)
rawtriage -l [status] [--json]     # the Inbox: status, type, age, date, title, [stale] / [expired] flags
rawtriage -l --summary             # one line: "N open, N cooking (N stale, N expired)" (see Inbox limits)
rawtriage where                    # the notes folder and the projects root
rawtriage check                    # notes with a missing or invalid header (same rules as check-frontmatter.sh)
rawtriage new <slug> [thought...]  # create today's note from _template.md; refuses a name that exists
rawtriage keep <file> [--status open|cooking] [--next "step"]   # refused once the note is expired
rawtriage kill <file> --reason "why"
rawtriage reference <file> [--why "..."] [--section "Index section" [--blurb "one line"]]
rawtriage reference --unindexed    # reference notes reference/README.md's index doesn't link, and its sections
rawtriage index <name.md> --section "Index section" [--blurb "one line"]   # index an already-filed reference note
rawtriage promote <file> --dest active|experiments|learning|templates --done "done means" \
                  [--due-days 7 | --due YYYY-MM-DD] [--slug name]
rawtriage merge <file> <file>... --title "..." [--then inbox|promote|reference] [promote options]
rawtriage undo                     # the newest change, whoever made it (the page's undo list is the same list)
rawtriage project ship <name> [--kind active|experiments|learning|templates]   # a project folder needs --outcome
rawtriage project extend <name> --reason "why more time" [--days N] [--kind ...]   # at most twice
rawtriage project kill <name> --reason "why" [--kind ...]       # a folder moves to archive/<name>_abandoned
rawtriage project close <name> --outcome next --done "done means" (--due-days N | --due YYYY-MM-DD)
rawtriage project close <name> --outcome maintained|archive     # the folder moves; refused while anything blocks it
rawtriage project deps <name> [--to maintained|archive|abandoned]   # what points at the folder (read-only)
rawtriage project github <name> [--kind ...]       # first push: private GitHub repo, then push main (this machine only)
```

- `<file>` is a name in the Inbox, an absolute path, or any part of a name that matches one note (`rawtriage kill dns-slow --reason ...`).
- `promote` needs `--done`, and a ship-by (`--due-days` or `--due`) for everything except `experiments`; without them
  it exits 1 and changes nothing. `project extend` and `project kill` need `--reason` (`--note` is the old name);
  an extension's reason must be at least 10 characters, and a third extension is refused (see **Projects page**).
- `reference --section` adds the index line in the same undoable step as the page's Reference form (see Reference
  under **Decisions per note**); without it the note is filed but not indexed, and `reference --unindexed` lists it.
- `--json` prints the server's answer instead of a sentence. `RAWTRIAGE_PORT` picks another port (default 8765).
- **Exit codes:** 0 done; 1 refused (the reason is printed; bad usage too); 2 no usable server (nothing was changed);
  3 the connection was lost after a change was sent: the server may have finished it, so check `rawtriage -l` or the
  page before retrying. Promote, merge, close out, ship/kill, `project github` and undo wait up to 300 s for an answer,
  everything else 60 s (`RAWTRIAGE_TIMEOUT` sets both, in seconds).
- **Server down:** `systemctl --user start rawtriage-web` if the unit is installed, otherwise `server.py --no-browser`
  is started detached; it waits up to 5 seconds. If that fails, or something that isn't rawnotes-triage answers on the
  port, it exits 2 and says nothing was changed. Do not fall back to editing notes by hand.
- `GET /api/check` and `POST /api/new {slug, thought}` are the two endpoints added for it.
- `project close` (and `project ship --outcome …`) prints what is left to do by hand after a move. `--kind` is only
  needed when two project folders share a name.

## The rawnotes skill

`skills/rawnotes/` teaches an agent (Claude, Codex, OpenCode) to capture and triage notes from a thread in any folder,
not just inside the notes folder: `SKILL.md` (when to use it, the rules), `capture.md`, `triage.md`, and
`global-rule.md` (the few lines to paste into your global instructions). It is path-free: the agent asks
`rawtriage where` for the notes folder, creates notes with `rawtriage new`, checks headers with `rawtriage check`, and
decides only through `rawtriage ...` (see **Commands**); if `rawtriage` exits 2 it stops and tells you.

- `./install-webapp.sh` links it into `~/.claude/skills/rawnotes`, `~/.agents/skills/rawnotes` and
  `~/.codex/skills/rawnotes`, always from the main checkout (never a worktree). A rerun is safe; `--remove` removes
  only those links; anything else already called `rawnotes` there stops the install, untouched.
- Paste `skills/rawnotes/global-rule.md` into `~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md` and
  `~/.config/opencode/AGENTS.md` (by hand; the installer doesn't edit your instruction files).
- **One source of truth.** In your notes folder, point the instruction files at the skill instead of copying it:
  link `TRIAGE.md` to `skills/rawnotes/triage.md`, make `CLAUDE.md` a few lines ("Use the rawnotes skill (capture or
  triage). This folder is the notes folder. Never reorganize it."), and make `AGENTS.md` a link to `CLAUDE.md`.

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

Filters: All / Open / Cooking / **Stale** (open and cooking, see below) / red
**Expired** (past the limit below; the chip appears only when there is one) /
**Stuck** (the status says decided, e.g. `promoted`,
but the file never left the Inbox; the chip appears only when there is one),
plus a type filter (question / research / issue). Search matches title,
filename and the first paragraph of the question.

**Inbox limits.** A note should not sit in the Inbox for ever:

| Rule | Default | Clock starts at |
|------|---------|-----------------|
| `open` (or no status) is **stale** | 14 days | capture date, or the last Keep |
| `cooking` is **stale** | 7 days | the last Keep (capture date if never kept) |
| **Expired**: Keep is refused | 30 days | capture date (Restore date for a restored note) |
| **Expired**: Keep is refused | 3 Keeps | counted in the header (Restore starts the count over) |

Keep writes `- kept: <today>` and `- keeps: N` into the note's header; Restore
writes `- restored: <today>` and removes `kept:`/`keeps:`, so a restored note
gets a fresh 30 days and 3 Keeps. An expired note cannot be kept, on the page, in a
batch (one expired note refuses the whole batch and names it) or through the
API (400, "...30-day limit: promote, file, merge or kill it"). Promote,
Reference, Merge and Kill still work. Rows show `kept 2×` and `expires in 3d`;
Pulse shows the expired count. Change the four numbers in `config.json`
(`stale_open_days`, `stale_cooking_days`, `inbox_limit_days`, `max_keeps`;
whole numbers of 1 or more) and restart. The weekly nudge should print the
same numbers (`server.inbox_summary()`: "N open, N cooking (N stale, N expired)").

The reader shows the whole "What's the question / problem?" section, then
the rest of the note with the title and header lines removed.

**Stacks:** three or more notes in the current filter whose slugs start with
the same two words (`arcane-bomb-*`) collapse into one stack. Searching turns
stacks off. Opening a stack (click, `Enter` or `Space`) changes nothing; it
ticks all members and shows the batch form. Untick what should stay.

## Decisions per note

| Button (key) | What happens |
|--------------|--------------|
| Promote (P)   | Creates `../<dest>/<slug>/` (`active`, `experiments`, `learning` or `templates`) from the skeleton in `starter/skeletons/<dest>/` (see **Project skeleton** below), with the note as its `NOTES.md`. Stamps `status: promoted`, `promoted: <today>` and a `## Plan` block with your **done means** (required for every destination). Active / Learning / Template also get `due:` (3, 7, 14 or 30 days, or an exact date; default 7). **Experiments are open-ended:** no `due:`, and any old one is removed. The capture is marked promoted, gets the destination under `## Outcome`, and moves to `done/`. |
| Reference (R) | Copies the note to `../reference/<slug>.md` with YAML frontmatter (created, promoted, status, type, source, optional why, tag `reference`) so Obsidian shows it as properties. Capture marked promoted and moved to `done/`. Pick an **Index section** (an existing `###` heading, a new one, or Don't index) and a one-line summary, and the same undoable step adds `- [Title](slug.md): summary` to `## Index` in `reference/README.md` and records `section:` in the note. Nothing outside `## Index` is touched; with no README or no `## Index` the note is still filed and you get a warning. Reference notes the index doesn't link are listed on the Archive page with **Add to index** (`GET /api/reference/unindexed`, `POST /api/reference/index`). |
| Keep (C)      | Sets `open` or `cooking` (the form defaults to the note's current one), stamps `kept:` / `keeps:` in the header and optionally adds a dated `next step` line under `## Notes`. Stays in the Inbox. Refused once the note is expired (see Inbox limits). |
| Kill (X)      | `status: dead`, your reason (optional; "no longer useful." if blank) under `## Outcome`, moved to `done/`. |

If the destination name is taken, a `-2`, `-3`… suffix is added; nothing is
overwritten. The API also accepts the 1.x names `durable` (Reference) and
`archive` (Kill).

**Project skeleton.** A project folder made by Promote gets `README.md` (title,
then `- **Status:**`, `- **Stack:**`, `- **Run it:**`, `- **Repo:**` lines and
`## Why it exists` with the done means), `NOTES.md` (the promoted note, plus
`- github: private` in its header), `AGENTS.md` (the `.scratch/` issue-tracker
convention and a **First build round** checklist with the exact `gh repo create
<slug> --private …` and `ALLOW_PUSH_MAIN=1 git push -u origin main` commands),
`CLAUDE.md` (`@AGENTS.md`), `.gitignore`, and an empty `.scratch/` (active),
`notes/` for study notes (learning) or `template/` for the files to copy
(templates, whose README says what it scaffolds and how to copy it).
Learning and templates used to get a flat `<slug>.md`; those files stay where
they are and still show on the board. `{{title}}`, `{{slug}}`, `{{done}}`, `{{due}}` and `{{today}}` in
the skeleton files are filled in one pass (text from the note is never
expanded). Then, in the same undoable step, it runs `git init -b main` and
makes one commit, `Start <slug> (promoted from RawNotes)`, with your normal
git hooks. The app never sets a git identity: if the email git would use
(config or `GIT_AUTHOR_EMAIL`/`GIT_COMMITTER_EMAIL`) does not end in
`@users.noreply.github.com` (change it with `"noreply_suffix"` in
`config.json`), the repo is made with **no commit** and the page shows a
warning with the command to commit by hand (it names the folder relative to
the projects root and never shows the address itself: the warning also goes
to `triage.log` and to a phone on a `--host 0.0.0.0` bind). A refused commit (a hook said no)
is a warning too, with the files staged. No `git` on the PATH: the folder and
files are made, with a warning. Promote never creates a GitHub repo or
pushes. **Undo** removes the folder including `.git` only while the repo
still holds just that commit (every ref on it) and nothing is modified,
staged or untracked; after a second commit, an amend, a new branch or an
added or edited file it refuses, changing nothing. If a step after `git init`
fails, the whole promote is rolled back, `.git` included.

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
is no redo (each undo is committed like any other change; see Auto-commit). Undo checks every file it would touch first and refuses, changing
nothing, if one has been edited since or now sits behind a symlink or outside
the notes and projects folders.

**Auto-commit:** if the notes folder is a git repo, every change made through
the server (each decision, batch, merge, restore, new note, and each undo) is committed
right after it succeeds: only the notes-folder files that change touched
(both sides of a move), with the message `triage: <what>` (e.g.
`triage: kill “Chrome remove profiles”`, `triage: killed 3 notes`,
`triage: undo kill “…”`) and the paths plus `via rawnotes-triage <version>` in
the body. Anything else you have edited or staged is left as it was; hand
edits already in a touched note go into the commit and the body names them.
Undo is a commit of its own; history is never rewritten. Files in project
folders (the promoted copy, project status) and in `../reference` (the filed
note, its index line) are not committed; a promoted project's own first commit
is made in its own repo (see **Project skeleton**). If the commit
can't happen (not a git repo, a merge or rebase in progress, `index.lock`
present, no `user.email`, a pre-commit hook such as gitleaks refusing it) the
change still stands and stays undoable, and the toast and stderr say why it
wasn't committed. Hooks always run (never `--no-verify`). Turn it off with
`--no-commit` or `"autocommit": false` in `~/.config/rawnotes-triage/config.json`.

Changes are all-or-nothing: notes are checked before anything is written (a
note listed twice, a missing one, a bad date), each file is replaced
atomically (permissions and undecodable bytes survive; a consistently CRLF
file stays CRLF, mixed endings become LF), and if a step fails half-way the
steps already taken are rolled back. If a rollback cannot finish, an
"unfinished" entry stays on the undo list so `Z` can complete it.

## Archive

Everything in `done/`, newest first, filtered by Promoted / Reference /
Killed / Merged (worked out from the status and the `## Outcome` line).
**Restore to Inbox** sets the note to `cooking`, stamps `- restored: <today>`
(its Inbox limit starts again from that date), clears its Keep count (`kept:`,
`keeps:`) and moves it back; it refuses
if a note with that filename is already in the Inbox, and it does not remove
a project or reference copy made earlier.

## Projects page

One board for everything promoted. Cards come from two scans of the sibling
folders:

- every folder directly under `active/` and `experiments/` (hidden ones
  skipped), whether or not it was promoted through this app. A linked git
  worktree (its `.git` is a `gitdir:` file, i.e. a branch checkout) is not a
  project: it is left off the board, and the project routes refuse it;
- every folder directly under `learning/` and `templates/` whose `NOTES.md`
  has a `promoted:` line (made by Promote). Hand-made folders there, such as a
  study folder or a template kit, stay off the board;
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
never from GitHub. A project with no GitHub origin shows a red **Not on
GitHub** instead, once it has a commit after Promote's own first commit or
was promoted 2 or more days ago (a project made before the skeleton existed is
judged by the `promoted:` date alone).

**Shipped means it's on GitHub.** For an `active` or `templates` project
folder, the gate sits in **close out** (below), so no route around it skips
it. "On GitHub" means the work is there, not just a remote: a GitHub `origin`
that has received a push, and no commit on any local branch that `origin`
doesn't have. It is checked fresh, under the same lock as the move; a refusal
is a 400 starting "Push it first" and nothing changes:

| Close-out outcome | Needs the work on GitHub? |
|---|---|
| **Maintained** | always |
| **Archive** | only if the folder is a git repo with work in it (on any local branch): any commit after Promote's skeleton commit, or any commit at all when the repo has no skeleton commit. No `.git`, no commits, or only the skeleton commit: archives without GitHub |
| **Next round** | never |
| **Kill** (`archive/<name>_abandoned`) | never |

Experiments and learning are never gated (the chip stays red). The panel of a
project that has a local repo and no origin has a **Create private GitHub repo** button (also
`rawtriage project github <name>`; `POST /api/project/github {name, kind}`). It
only works from the machine running the server (the button is hidden on a
phone or any non-`localhost` page) and refuses if an `origin` exists, the tree
isn't clean, the branch isn't `main`, or `gh` isn't signed in. It runs exactly
`gh repo create <your login>/<folder> --private --source <folder> --remote
origin`, then `git push -u origin main` with `ALLOW_PUSH_MAIN=1` in that one
process's environment (never `--no-verify`). The repo is always private; going
public is `gopublic`'s job. If the push fails after the repo was created, the
error says so and what to run by hand. Header tiles count overdue, due this week, active folders
without "done means", and active folders on GitHub.

Select a card to open its panel in a column beside it (select it again, or
`Esc`, to close it): **done means**, the clock, the last five commits (from
`git log`), the last five pull requests (from `gh`), and an excerpt from the
README or NOTES. Excerpts and commit titles are quoted from the source, not
summarised. Buttons: **Mark shipped** (`status: shipped` + `shipped:` date),
**+7 days** (pushes `due:` from today or the current due date, whichever is
later; not offered for experiments or undated folders; asks why, at least 10
characters, and records `- extensions: N` plus a `- pushed <old> → <new>
(<date>): <reason>` line under `## Plan`; after two pushes (`max_extensions` in
`config.json`) the button reads "No more extensions", and an overdue project
with none left shows a **Decide** badge: ship it or kill it), **Browse repo**
when the project has a GitHub origin, otherwise **Open folder** (launches
`xdg-open` on the server's machine; only for a browser on that machine, and
hidden on the phone layout), and **Kill** (asks why; the reason goes under
`## Outcome`). Shipping or killing a folder that has no dated file writes into its `NOTES.md`,
creating it if needed. Shipping or killing also writes `- closed: <date>`. On a project folder, Mark shipped and Kill
are a **close out** (below): the folder may move.
The **Closed** column shows only projects closed in the last 7 days
(`closed_window_days` in `config.json`), counting folders and dated files that
have since moved to `maintained/` or `archive/`; **Show all (N)** lists every
closed project still under `active/` or `experiments/`. A project's closed date
is its `closed:` line, else `shipped:`, else the date in `**Killed
YYYY-MM-DD:**` under `## Outcome`. All these writes can be undone. Git facts are cached
for 20 seconds; **Refresh** re-reads them.

**What points here** (the panel of an open project folder) is a read-only dependency check, the same one a move
runs (see **Close out** below; `GET /api/project/deps?name=&kind=[&to=maintained|archive|abandoned]`). It looks for:

| Looks at | Finds | Result |
|---|---|---|
| `~/.config/systemd/user/*.service`, `.timer`, `.path`, `.socket` (and `.d/*.conf` drop-ins) | the folder's path, also spelled `%h/…`, `~/…` or `$HOME/…` | **blocks** |
| running processes (`/proc/<pid>/cwd`, `/proc/<pid>/cmdline`) | anything running from inside the folder | **blocks** |
| `git worktree list` in the folder; a `gitdir:` `.git` file | linked worktrees, or the folder is one | **blocks** (a worktree whose folder is gone: a `git worktree prune` to-do) |
| `~/.local/bin/*` | links into the folder; scripts under 64 KB naming it | **blocks** |
| `crontab -l` | uncommented lines naming it | **blocks** |
| `~/.claude/projects/<path with every non-alphanumeric as ->` (and its subfolders') | agent memory for the old path | to-do: the exact `mv` |
| T3 Code projects | the app can't read them | to-do, always listed |

Each hit comes with the command that fixes it. A path that only starts the same way (`rawnotes-triage-flow-gaps`
next to `rawnotes-triage`) is not a hit. The app never moves memory folders or edits T3 itself.

**Close out.** Mark shipped on a project folder asks *What happens to it now?*, with a suggestion preselected:

- **Next round** (stays where it is): a new done means and ship-by (required; experiments may stay open-ended). The
  header goes back to `status: promoted` with today's `promoted:`, the new `due:`, `extensions: 0`, and no `closed:` or
  `shipped:`; the finished round is written under `## Rounds` (`- <promoted> → shipped <date> (ship by <due>, pushed
  N×). Done means: <old>`), and the new done means replaces the old one where it was found. Use it for something still
  being built (rawnotes-triage itself).
- **Maintained**: `status: shipped`, `shipped:`, `closed:`, then the folder moves to `maintained/<name>`. Suggested when
  the folder has a GitHub origin.
- **Archive**: the same, into `archive/<name>`. Suggested otherwise (a one-off, often just a `NOTES.md`). In
  `active/` and `templates/` both Maintained and Archive pass the GitHub gate above first.
- **Kill** (reason required) always moves the folder to `archive/<name>_abandoned`.

A folder already marked shipped (or `done` by hand) before close out existed, and still in its project folder, shows
**Close out…** instead, with the same three choices. A taken name gets `-2`, `-3`…. A move runs the dependency check (**What points here**) inside the change: any blocker
refuses it with the list and each fix (HTTP 400 with `blockers` and `todos`), and nothing changes. To-dos don't stop
it: they are written under `## Moved` in the moved `NOTES.md` (`- <date>: moved from <old>/ to <new>/. Left to do by
hand:` and one line per command) and shown in the toast. The status edit and the move are one undo step: **Undo**
moves the folder back and restores `NOTES.md`, but refuses (changing nothing, and saying the `mv` to do it by hand) if
anything in the moved folder was added, removed or edited since (git's own `.git` contents don't count), the old name
is taken again, or the folder became a symlink. The move is a `rename` on the same filesystem, never a copy. After a
move or its undo, `sync-apps` (from `maintained/obsidian-sync-apps`) runs if it is on `PATH`, so Obsidian's `Apps/`
link follows the folder; its output goes to stderr and a failure never fails the change (`sync_apps` in the answer
says `ran`, `failed` or `not installed`). Moves happen only when you choose one, never on a timer. A single-file
project (an old flat `<slug>.md`) is still just marked shipped in place. API: `POST /api/project/close {name, kind,
outcome: next|maintained|archive, done?, due?|due_days?}` (or `action: "kill", note`); `/api/project/status` with
`shipped` or `kill` on a folder does the same and needs `outcome` for `shipped`.

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
`- promoted:`, `- due:`, `- shipped:`, and the app's own `- kept:`, `- keeps:`,
`- restored:`, `- extensions:`, `- closed:`, `- github:`, which the reader
hides) is the first run of `- key: value`
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
  `triage.log`; Keep, Restore, new notes and warnings do not count) per week, last 8 weeks, with a
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
`/api/open` only answers a browser on the server's own machine with a localhost
`Host` (so a DNS-rebound page can't use it under `--host 0.0.0.0`), and only for
project folders or dated files the board lists. Saving or removing the GitHub
token (`/api/github/token`) is likewise limited to the server's own machine,
and also needs a localhost `Host` on any bind (so a DNS-rebound page can't do it
under `--host 0.0.0.0`).

Project folders get the same rule. Moving one (close out to `maintained/` or
`archive/`, or Kill to `archive/<name>_abandoned`, through `/api/project/close`
or `/api/project/status`) and **Create private GitHub repo** only work from a
browser on the server's own machine with a localhost `Host`; a phone (or a
rebound DNS name) gets a 400 and nothing moves. So does **Undo** of a change
that moved a project folder or made a project's repo (a close out, a Kill, a
Promote): undo of anything else still works from anywhere. Undo of a move is
checked like the move: anything pointing at the folder in its new place blocks
it, with the list. A **Next round** moves nothing
and works from anywhere. **What points here** (`/api/project/deps`) still
answers off this machine, redacted: every hit keeps its kind and fix, but the
command line of a running process and the text of a crontab line are left out,
and your home folder is shown as `~` (also inside the Claude Code memory
folder names, where it is spelled `-home-<you>`).

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
No test reads your git config or runs your git hooks (`testkit.isolate_git`), and none reads or writes your
`~/.config/rawnotes-triage` (testkit points the config folder at a temp one for the whole run;
`test_zz_isolation.py` runs last and fails if the real folder changed). The dependency check never looks at this
machine in tests: testkit points it at an empty fake home and `/proc` with no crontab, and a folder move never runs the
real `sync-apps`.
`TRIAGE_INDEX=/path/to/old/index.html` runs the same checks against another
build of the page.

## Files

- `server.py`: HTTP API, decisions, undo engine, project scan, GitHub access, startup and `--restart`
- `notemeta.py`: note header and section parsing
- `safefs.py`: symlink-safe path checks, atomic writes, read/write lock, stat cache
- `depcheck.py`: the read-only dependency check run before a project folder moves (scanners take their roots as arguments)
- `index.html` + `static/fonts/`: the whole UI
- `starter/_template.md`: the note template copied into a new notes folder on first run
- `starter/skeletons/<dest>/`: the files Promote writes into a new project folder (see **Project skeleton**)
- `static/icons/`: app icon (SVG source + PNGs), the favicon and the phone home-screen icon
- `install-webapp.sh`, `systemd/rawtriage-web.service`: the Omarchy launcher app and its always-on service (the
  installer fills in the checkout's path in the service file)
- `bin/rawtriage-web`: the command-line wrapper the launcher runs
- `bin/rawtriage`: the triage command (stdlib client of the server's API)
- `skills/rawnotes/`: the capture and triage skill for agents (installed by `install-webapp.sh`)
- `testdata/check-frontmatter.sh`: a copy of RawNotes' header hook, so `test_cli.py` can check `/api/check` gives the same verdicts
- `test_*.py`, `testkit.py`, `cdp.py`, `loadcheck.py`: tests and load check
- `.scratch/`: specs and tickets for past and pending work (see `AGENTS.md`)

**Version:** `VERSION` in `server.py`, shown in the rail footer, the tab
title and `/api/version`. When you change the app, bump it by 0.01 and add a
dated entry to `CHANGELOG.md`.
