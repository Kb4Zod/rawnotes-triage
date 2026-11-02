# First-run setup

This walkthrough covers a fresh install and the steps to take after the app first opens. It describes version 2.13. For everyday use, see the [User Guide](USER-GUIDE.md); for command and implementation details, see the [project README](../README.md).

## 1. Choose where the app and your notes will live

The app checkout and the notes folder are separate. Keep the checkout somewhere stable if you plan to install the desktop launcher: its service runs Python from that checkout.

The notes folder's **parent becomes the projects root**. For example, choosing `~/Projects/RawNotes` means:

```text
~/Projects/
├── RawNotes/             Your Inbox
│   ├── _template.md      Template for capturing a note
│   ├── done/             Decided captures shown in Archive
│   └── YYYY-MM-DD-topic.md
├── active/               Promoted projects
├── experiments/          Open-ended experiments
├── learning/             Promoted learning projects
├── templates/            Promoted template projects
├── reference/            Filed reference copies and their index
├── maintained/           Shipped projects you will maintain
└── archive/              Finished or abandoned project folders
```

This is the eventual layout, not a list of folders created by the Welcome dialog. Setup creates only the notes folder, `done/`, and `_template.md`; destinations are created as you use them. The **Archive page** reads `RawNotes/done/`. The sibling **`archive/` folder** holds closed project folders.

Choose a full path inside another folder, such as `~/Projects/RawNotes` or `/data/projects/RawNotes`. A relative path, the disk root, or a top-level path such as `/notes` is refused by the Welcome dialog. The account running the server needs permission to write there and in the parent for later promotions and moves.

## 2. Start the app

The app is written and tested on Linux. Python 3 and its standard library are enough for basic triage; there are no Python packages to install. Git enables repository features and automatic note commits. The GitHub CLI (`gh`) enables GitHub features. The Omarchy installer additionally needs its desktop environment and systemd user services.

If you do not already have the app checkout, clone it into your chosen location:

```bash
git clone https://github.com/Kb4Zod/rawnotes-triage.git
cd rawnotes-triage
```

If it is already checked out, open a terminal in that folder instead. Then run:

```bash
python3 server.py
```

Leave that terminal running. The server listens at `http://127.0.0.1:8765` and opens the browser after it starts. `Ctrl+C` stops this manually started server. If the browser does not open, visit the address yourself; `--no-browser` deliberately suppresses opening it.

The notes path is selected in this order:

1. `--notes PATH`, if supplied.
2. The `notes` value saved in `config.json`.
3. `~/Projects/RawNotes`.

To start with a particular path:

```bash
python3 server.py --notes ~/Projects/RawNotes
```

A running triage server on the same port is reopened rather than replaced. Stop it first or choose a different port to test another folder. For a manually started server, `--restart --notes PATH` also replaces it with the requested options. If the existing server belongs to the installed systemd service, however, `--restart` restarts that service with its own configuration; it does not apply the new `--notes` flag. To change the service's notes folder, update the saved config and restart the service.

## 3. Complete the Welcome dialog

**Welcome appears only if the selected notes folder does not exist.** It is not a wizard shown on every new checkout or first browser visit.

On the machine running the server:

1. Read the missing-folder path at the top of **Welcome to RawNotes Triage**.
2. Enter the desired **Notes folder**. `~` is accepted and expanded to your home directory.
3. Check the projects-root preview below the field: project and reference destinations will be siblings of this notes folder.
4. Click **Create notes folder**.

The server creates the folder and any missing parents, makes `done/`, and copies `starter/_template.md` from the app checkout to `_template.md` if no template exists there. If you choose an existing folder in the dialog, it keeps its contents and existing template, adding the missing setup files. It then saves the absolute notes path, switches the running app to it, closes Welcome and loads the Inbox. No restart is required for this switch.

The dialog cannot be dismissed before setup is complete. From a phone or another computer, it asks you to finish setup on the server's own machine; use `http://127.0.0.1:8765` there, then reload the phone page.

If folder creation fails, check the displayed error and write permissions. If creation succeeds but saving the choice fails, the message names the config file: fix its permissions and retry. Folder creation is not undone when saving the config fails.

### If the notes folder already existed before startup

The app goes straight to the Inbox. It **does not run the setup routine**, save that path to config, copy a template or create `done/` just because this is your first launch.

Existing dated Markdown notes can be used immediately. Decisions create `done/` when needed, and `rawtriage new` can use the bundled template if `_template.md` is missing. To add your own capture template, copy `starter/_template.md` into the notes folder and edit that copy.

If this is a custom path supplied by `--notes`, continue passing it, or save the absolute path under `notes` in config before switching to the default service. An existing default `~/Projects/RawNotes` folder needs no saved setting.

## 4. Check the saved configuration

By default, setup writes:

```text
~/.config/rawnotes-triage/config.json
```

For example, with your actual home path in place of `/home/you`:

```json
{
  "notes": "/home/you/Projects/RawNotes"
}
```

The config directory is chosen from `RAWTRIAGE_CONFIG_DIR`, otherwise `$XDG_CONFIG_HOME/rawnotes-triage`, otherwise `~/.config/rawnotes-triage`. These are environment settings of the **server process**; an installed service must receive the same settings if you use them.

Setup preserves other valid keys in the JSON object. Optional settings are listed in the [User Guide](USER-GUIDE.md#settings). If editing an existing config, merge keys into it rather than replacing its contents. Missing or malformed config, or values of the wrong kind, fall back to defaults. Restart after manual edits. `--notes` always takes precedence over the saved value on startup.

To change folders later, stop and restart with `--notes /new/path/RawNotes`, or change `notes` in config and restart. An existing folder opens directly; a missing folder triggers Welcome. **Changing the setting does not move existing notes or projects.** Plan any file migration separately.

## 5. Add and verify your first note

From the checkout, use the bundled CLI even before installing terminal shortcuts:

```bash
bin/rawtriage where
bin/rawtriage new first-note "What should I work on next?"
bin/rawtriage -l
bin/rawtriage check
```

`where` should show the intended notes folder and its parent. `new` creates a file named `YYYY-MM-DD-first-note.md` using today's date. `-l` should list it in the Inbox, and `check` reports any invalid or missing headers. If testing on another port, prefix commands with `RAWTRIAGE_PORT=8766` (using your port).

You can also create a note in your editor from `_template.md`. Name it `YYYY-MM-DD-topic.md`, fill in the title, date and question, and keep the header's `status` (`open` or `cooking`) and `type` (`question`, `research` or `issue`). `_template.md` itself is not an Inbox note. An empty Inbox is normal until a dated note exists.

Open the note in the page. Try **Keep** with a next step, then **Z** to undo it. This checks writes and the shared undo list without creating a project. Keep counts toward the default three-Keep limit; undoing it restores the previous count and date. Undo lives in memory and is cleared by a server restart.

## 6. Decide whether to version your notes

Welcome **does not initialize a git repository**, set your git identity, install hooks, or configure backups. If the notes folder is already in a git repo, successful changes to its files are auto-committed by default. If it is not, triage still works and reports that the commit was skipped.

To opt into note history, initialize and make a first commit yourself. For the example layout:

```bash
cd ~/Projects/RawNotes
git init -b main
git add .
git commit -m "Start RawNotes"
```

Review the folder contents before staging. Set your preferred git identity beforehand if it is not configured. The app uses your git configuration and hooks; it never sets an identity for you. To disable automatic commits, use `--no-commit` or set `"autocommit": false` in config.

Notes history and project history are separate. Promotion creates each project's own git repo and attempts its first commit. That commit requires an email ending in `@users.noreply.github.com` by default (configurable with `noreply_suffix`). A missing git installation, unsuitable identity or refusing hook produces a warning; the project files still exist. Reference copies and their index are not included in the notes auto-commit. A local git repo is not a remote backup.

## 7. Connect GitHub if you want GitHub features

There is no app account or app login. On the server machine, install `gh`, then run:

```bash
gh auth login
gh auth status
```

Restart the app, or wait a minute for the cached status to expire and reload the page, then check the rail's **Signed in as** line. GitHub features use that account. The repo viewer opens its owned repos; Pulse can also count commits in collaborator repos. Without GitHub, basic triage continues to work.

Alternatively, click the rail's GitHub status on a localhost page to save a personal access token. The app validates it and stores it as `github-token` in the config directory (directory `0700`, file `0600`); `gh` is still required. A saved token takes precedence over environment tokens and the CLI's own login. **Remove saved token** returns to the environment/CLI credentials. Token entry and removal are unavailable from a phone on the network.

Read access is enough for the viewer and statistics. **Create private GitHub repo** also needs permission to create a repository and push; a token limited to read-only access cannot do that. Promotion itself creates only a local repo, never a GitHub repo.

## 8. Prepare the reference index if you want one

Setup does not create `reference/README.md`. The Reference decision can file a note without an index, but adding an index entry requires that file to contain a `## Index` section. Create it next to the notes folder, for example:

```markdown
# Reference

## Index

### Tools

### Research
```

The Reference form can choose an existing section or create a new one beneath `## Index`. It adds a Markdown link and summary in the same undoable step as filing. Missing README or index: the copy is still filed, with a warning. **Archive → Add to index** handles reference notes filed earlier.

## 9. Install the Omarchy launcher and service (optional)

After verifying the notes path, stop any manually started server with `Ctrl+C`. From the app checkout, run:

```bash
./install-webapp.sh
```

The installer checks requirements and conflicts first. It installs:

- **RawNotes Triage** in the desktop launcher, with its icon and a full-screen window rule.
- An enabled, running systemd user service, `rawtriage-web.service`, for future desktop sessions.
- `~/.local/bin/rawtriage-web` and `~/.local/bin/rawtriage` links to the checkout's wrappers.
- The bundled `rawnotes` agent skill as links under `~/.claude/skills`, `~/.agents/skills` and `~/.codex/skills`.

From a linked git worktree, it targets the main checkout so the service does not run a feature branch. Existing custom files that conflict with its links can block installation; legacy hand-made wrappers have specific backup handling described in the [README](../README.md#omarchy-launcher-app).

Check the service and CLI:

```bash
systemctl --user status rawtriage-web
rawtriage where
```

The service uses the saved/default notes path and port 8765. A `--notes` flag used in an earlier manual run is not automatically added to it. Confirm `where` still reports the folder you intended. If `rawtriage` is not found, check that `~/.local/bin` is on your `PATH`; the checkout's `bin/rawtriage` still works.

Open **RawNotes Triage** with `SUPER + SPACE`; `SUPER + F` toggles full screen. After app updates, run `rawtriage-web --restart` or `systemctl --user restart rawtriage-web`, then reload a stale tab. Moving the app checkout requires rerunning the installer. `--no-service` installs the launcher without the service; `--remove` removes the installer's integration, not your notes or project folders.

## 10. Confirm setup is complete

You are ready when the page opens without Welcome, `rawtriage where` shows the right paths, a dated note appears in the Inbox, and a Keep/Undo cycle succeeds. GitHub and auto-commit may remain optional; read any warnings so you know whether commits and remote features are available.

For a phone, a server must listen on a LAN/Tailscale address. The installed service binds localhost on port 8765. If it is running, use a separate free port for a manual session, from the app checkout:

```bash
python3 server.py --host 0.0.0.0 --port 8766
```

Leave that terminal open and visit `http://<server-LAN-or-Tailscale-address>:8766` on the phone. To send CLI commands to this session, prefix them with `RAWTRIAGE_PORT=8766`; otherwise the CLI still uses the service on 8765. Each server has its own in-memory undo list. Stop the manual session with `Ctrl+C` when finished.

Alternatively, stop the service with `systemctl --user stop rawtriage-web` before starting a manual server on 8765. Restarting the installed service with `--restart --host 0.0.0.0` does not apply the host flag. Resume the normal localhost service afterward with `systemctl --user start rawtriage-web`.

There is no login: anyone able to reach the listening port can read and change notes and use your repo viewer. Use a trusted network. Folder moves, GitHub repo creation, token changes and certain undo operations require localhost; see [phone restrictions](USER-GUIDE.md#appearance-and-phone-use).
