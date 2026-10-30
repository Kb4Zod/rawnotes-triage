# Flow gaps: proposal

Status: draft for Hugh (2026-10-30). Nothing is built yet. Spec: `spec.md`. Tickets: `issues/`.

Source: a RawNotes note (gaps 1–8 and your answers 0–8).
Line numbers are from `origin/main` at 31a12d7 (version 2.12).

How to read this: each section has **Today** (what the code does, with file:line), **Recommend** (what to
build), and, where I disagree with your version, **Pushback**. The last section lists the decisions still open.

---

## 0. One rawnotes skill for every agent

**Feasible: yes.** The same setup already works for another skill: one folder, linked into every agent's skill
folder, plus a short rule in each global instructions file.

**Today**

- Capture rules live in `RawNotes/CLAUDE.md`; triage rules in `RawNotes/TRIAGE.md`.
- `RawNotes/AGENTS.md` is a separate *copy* of `CLAUDE.md` (identical today, `diff` is empty). Copies drift.
- They only load when a session's folder is `RawNotes/`. A thread in any other repo can't capture or triage.
- That skill shows the pattern: canonical in one folder under the projects root, symlinked from
  `~/.claude/skills/`, `~/.agents/skills/` and `~/.codex/skills/`; a rule pasted into `~/.claude/CLAUDE.md`,
  `~/.codex/AGENTS.md` and `~/.config/opencode/AGENTS.md`.

**Recommend**

1. The skill lives in the app repo: `skills/rawnotes/SKILL.md` (short: when to use, the two workflows) plus
   `skills/rawnotes/capture.md` and `skills/rawnotes/triage.md`. It is written without your paths: it asks the
   CLI from section 2 where the notes are (`rawtriage where`), so it is safe in the public repo.
2. **One source of truth = the skill.** In `RawNotes/`:
   - `TRIAGE.md` → becomes a symlink to `<main checkout>/skills/rawnotes/triage.md`.
   - `CLAUDE.md` → shrinks to three lines: "Use the rawnotes skill (capture or triage). This folder is the notes
     folder. Never reorganize it." Folder-only bits (the SessionStart header hook) stay.
   - `AGENTS.md` → a symlink to `CLAUDE.md` (no more copy).
3. `install-webapp.sh` gets a `--skill` step (also run by default) that links the skill into
   `~/.claude/skills/rawnotes`, `~/.agents/skills/rawnotes` and `~/.codex/skills/rawnotes`, always from the
   **main checkout**, never a worktree (same rule the installer already uses for the service, README:107-109).
   `--remove` takes the links out, only if they are its links.
4. One short rule (kept in the repo as `skills/rawnotes/global-rule.md`, like that skill's own global rule) is
   pasted into `~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, `~/.config/opencode/AGENTS.md`:
   > RawNotes: when Hugh says "rawnote: …", "capture this", "triage", or "what's open", use the `rawnotes`
   > skill. Decisions go through the `rawtriage` command, never by editing note files. RawNotes is local only:
   > never add a remote or push.
   Kept to 3 lines on purpose (short, to keep prefill context small).
5. The header check (`RawNotes/.claude/hooks/check-frontmatter.sh`) only fires in sessions started in
   `RawNotes/`. The skill runs `rawtriage check` instead, so any thread sees bad headers too.

**Check it worked**

```
ls -l ~/.claude/skills/rawnotes ~/.agents/skills/rawnotes ~/.codex/skills/rawnotes
readlink ~/Projects/RawNotes/TRIAGE.md ~/Projects/RawNotes/AGENTS.md
cd ~/Projects/active/project-a && claude "rawnote: test capture from another repo"
```

Ticket: 03.

---

## 1. Should the app commit RawNotes?

**What not committing costs (today: 32 lines in `git -C ~/Projects/RawNotes status --short`)**

1. **No history of decisions.** The web app moves 11 notes to `done/`; git sees 11 deletions and 11 new files.
   If a note is later edited or lost, there is no version to go back to.
2. **Undo is short-lived.** The app's undo holds 20 steps in memory and empties on restart
   (`server.py:789-790`, README:187-192). Git is the only lasting undo.
3. **Unrelated work gets swept up.** The next agent that commits runs something like `git add -A` and folds
   web-app moves into an unrelated commit (it has already happened once).
4. **Noise hides real changes.** A dirty tree makes it hard to see what an agent just changed in your notes.

**Does `done/` belong in git? Yes.** It is the Archive page, Restore reads from it (`server.py:1136-1148`),
and Pulse counts captures from it (`server.py:1213`). 92 of 104 files there are already tracked; `.git` is 16 MB.

**Why RawNotes has a git repo at all:** agents rewrite notes; `git diff` shows what they changed, `git log`
is the history, and `git checkout` gets back a note an agent broke.

**Local only: confirmed.** Notes hold private material, and your rule says never add a remote. If you want an off-machine copy,
use an encrypted backup of `~/Projects`, not a git remote (out of scope here).

**Recommend: the app commits RawNotes after every change it makes.**

| Question | Answer |
|---|---|
| What is committed | Only the paths that change touched inside `RawNotes/` (both sides of a move), with a pathspec: `git add -A -- <paths>` then `git commit -m … -- <paths>`. Nothing else you have staged or edited is included. |
| Message | `triage: <undo label>`, e.g. `triage: kill “Chrome remove profiles”`, `triage: killed 3 notes`, `triage: undo kill “…”`. Body: the paths, then `via rawnotes-triage 2.13`. |
| Repo dirty with other changes | Left alone (pathspec). If a touched note already had hand edits, those edits go into this commit; the body says so. |
| Merge/rebase in progress, `index.lock` present, not a git repo, no `user.email` | Skip the commit, keep the change, warn in the toast and on stderr. Never fails the decision (same rule as `triage.log`, `server.py:951-955`). |
| gitleaks pre-commit hook blocks it (global hook `~/.config/git/hooks/pre-commit`) | Same as above: change stands, toast says "not committed: gitleaks flagged <file>". Never `--no-verify`. |
| Undo | Undo is a new change, so it gets its own commit. History is never rewritten (no reset, no amend). |
| Opt out | `--no-commit` flag, or `"autocommit": false` in `~/.config/rawnotes-triage/config.json`. |
| Project folders | Not auto-committed. The only commit the app makes in a project repo is the skeleton commit at promote (section 4). |

**Do this now, by hand (catches up the 11 moves without touching new captures):**

```
cd ~/Projects/RawNotes
git add -A -- done/
git add -u -- '2026-*.md'          # deletions + the one Keep edit (tracked files only)
git status --short                 # only untracked new captures should be left
git commit -m "triage: catch up web-app decisions since 2026-10-24"
```

Ticket: 01.

---

## 2. One behaviour for all three ways to triage

**Today**

- Web app: every decision goes through `mutation()` (`server.py:901-915`), writes `triage.log`
  (`server.py:946-955`), asks done means and ship-by on promote (`server.py:1034-1051`).
- `rawtriage` CLI and T3 "triage": Claude edits files by hand following `TRIAGE.md`. No log, so Pulse
  undercounts (`server.py:1196-1225` reads only `triage.log`). `TRIAGE.md` step 2 never asks for done means or a
  due date. `rawtriage -l` and `rawtriage-nudge` each re-implement the 60-day stale rule in bash.

**Recommend: agents call the app; they never edit notes to make a decision.**

1. Add a small stdlib client to the repo, `bin/rawtriage`, which talks to the running server over HTTP
   (`127.0.0.1:8765`, JSON, same guards as the page: `server.py:1290-1301`). It replaces the hand-made
   `~/.local/bin/rawtriage` (installer links it, backing up the old one, same as it does for `rawtriage-web`,
   `install-webapp.sh:133-134`).
2. Commands (every write is one undo step, logged, and committed per section 1):
   ```
   rawtriage                        # unchanged: starts a Claude triage session (now via the skill)
   rawtriage -l [status]            # list, from /api/notes (same stale/expired flags as the page)
   rawtriage where                  # notes folder and projects root
   rawtriage check                  # notes with bad headers (same rules as check-frontmatter.sh)
   rawtriage new <slug> [thought]   # create today's note from _template.md (used by rawnote)
   rawtriage keep <file> [--status open|cooking] [--next "step"]
   rawtriage kill <file> --reason "why"
   rawtriage reference <file> [--why "…"] [--section "Azure"] [--blurb "…"]
   rawtriage promote <file> --dest active|experiments|learning|templates --done "done means" [--due-days 7 | --due YYYY-MM-DD] [--slug x]
   rawtriage merge <file> <file>… --title "…" [--then inbox|promote|reference …]
   rawtriage undo
   rawtriage project ship|extend|kill|close …   # sections 5 and 7
   ```
   `--done` is required for promote, and `--due…` for every destination except experiments: the server already
   refuses otherwise (`server.py:1034-1038`), so the agent has to ask you.
3. `--json` on every command for agents; plain text for you.
4. **Server not running:** the client tries `systemctl --user start rawtriage-web` (the always-on unit), then
   waits up to 5 s. If there is no unit it starts `rawtriage-web --no-browser` detached. If that still fails, it
   exits 2 with "Triage server isn't running and couldn't be started", and **changes nothing**. The skill tells
   agents to stop and tell you, never to fall back to editing files. Reason: one writer keeps one lock
   (`server.py:63`), one undo list and one log; a second in-process writer would bypass all three.
5. The page shows CLI changes in its undo list (they are server changes), so `Z` in the page undoes an agent's
   decision too.

Side benefit: the HTTP API becomes the contract the Rust port must keep (the Rust-port note in RawNotes).

Tickets: 02 (client), 03 (skill and TRIAGE.md rewrite).

---

## 3. A limit on time in the Inbox

**Today:** `STALE_DAYS = 60` (`server.py:54`); stale means age > 60 *and* status `open` or missing
(`server.py:89`). `cooking` never goes stale. Keep writes no counter (`server.py:987-996`). The same 60-day
open-only rule is copied in `~/.local/bin/rawtriage-nudge` and `TRIAGE.md` step 1. Oldest live note today: 24 days.

**Recommend**

| Rule | Value | Clock starts at |
|---|---|---|
| `open` is stale | 14 days | capture date, or last Keep |
| `cooking` is stale | 7 days | last Keep (or capture date if never kept) |
| **Inbox limit** (forced decision) | 30 days | capture date (or the Restore date for a restored note) |
| Max Keeps | 3 | counted in the header |

1. Keep writes two header lines: `- kept: YYYY-MM-DD` and `- keeps: N` (N+1). Restore writes `- restored: YYYY-MM-DD`.
2. **Expired** = past the Inbox limit, or `keeps` ≥ 3. An expired note cannot be kept: the Keep button is
   replaced by "Over the 30-day limit: promote, file, merge or kill", and the API refuses keep with a 400 and the
   same words (web, batch and CLI alike, since they all hit the server). Batch Keep refuses the whole batch and
   names the expired notes.
3. The page: the Stale chip covers open and cooking; a new red **Expired** chip (like Stuck,
   `index.html:548`); each row shows `kept 2×` and `expires in 3d`; Pulse adds an "expired" count.
4. All four numbers are settable in `config.json`; the defaults are above.
5. `rawtriage-nudge` stops counting by itself and prints `rawtriage -l --summary` (so the weekly notification uses
   the same rules). Its timer says "Fridays" in the description but fires `Sat 09:00`; fix the text while there.
6. README rule 5 ("older than a couple of months … kill it") and `TRIAGE.md` step 1 are rewritten to the
   new numbers (TRIAGE.md through the skill, section 0).

**No grandfathering needed:** nothing in the Inbox is over 30 days today; the first note to expire would be the
2026-10-06 one, on 2026-11-05.

Ticket: 04.

---

## 4. Promote builds a real project

**Today:** promote makes one folder with one `NOTES.md` for active/experiments, or a flat `<slug>.md` for
learning/templates (`server.py:56-59`, `server.py:1008-1013`). No git (README:176-177). Board folders come only
from `active/` and `experiments/` (`server.py:248`). Your repos disagree on `NOTES.md`: tracked in
a public project, tracked in a private app, untracked in `rawnotes-triage`
(public; the app wrote it there when you marked it shipped, `server.py:1185-1194`).

**Recommend: a standard skeleton per category, shipped in the repo as `starter/skeletons/<category>/`.**

```
active/<slug>/                 experiments/<slug>/         learning/<slug>/            templates/<slug>/
  README.md   (public face)      README.md                   README.md                   README.md  (what it scaffolds, how to copy it)
  NOTES.md    (the promoted note,  NOTES.md                    NOTES.md                    NOTES.md
               Plan, due, clock)   .gitignore                  notes/       (study notes)  template/  (the files to copy)
  AGENTS.md   (issue tracker,      AGENTS.md                   .gitignore                  AGENTS.md
               first-push steps)   CLAUDE.md  (@AGENTS.md)     AGENTS.md                   CLAUDE.md
  CLAUDE.md   (one line: @AGENTS.md)                           CLAUDE.md                   .gitignore
  .gitignore
  .scratch/.gitkeep
```

- `README.md` follows the shape `obsidian-sync-apps` reads (title, then `- **Status:**`, `- **Stack:**`,
  `- **Run it:**`, `- **Repo:**`, then `## Why it exists` with the done means), so it shows up in Obsidian
  `Apps/` straight away.
- `AGENTS.md` carries the issue-tracker convention (`.scratch/<feature>/…`, like this repo's
  `docs/agents/issue-tracker.md`) and the **first build round checklist** (below).
- No `LICENSE` at promote: new repos start private. `gopublic` already adds MIT when a repo goes public
  (`~/.local/bin/gopublic` step 3).
- **Learning and templates become folders** (today they are flat files). Old flat files stay where they are and
  stay on the board as they do now.

**What promote does, in order (one undo step):**

1. Make the folder and write the skeleton (placeholders filled: title, slug, done means, due, today).
2. `git init -b main`.
3. Check the identity git will use (`git config user.email` in the new repo). It must end in
   `@users.noreply.github.com`. If not, skip the commit and
   say so; never set an identity for you.
4. First commit: `Start <slug> (promoted from RawNotes)`. The global gitleaks hook runs as usual.
5. Write `- github: private` (intended visibility) into the `NOTES.md` header.

Undo of a promote also removes `.git`, but only if the repo still has just that one commit and no other files;
otherwise Undo refuses, as it does today for edited files (`server.py:850-884`).

**First push to GitHub = first build round, not promote.** The app does not create GitHub repos by itself.

- Skeleton `AGENTS.md`, "First build round" (the agent does this as part of the first ticket):
  ```
  gh repo create <your-login>/<slug> --private --source . --remote origin
  git push -u origin main
  ```
- Enforcement on the board:
  - The card's git chip (`index.html:783`) turns red, **Not on GitHub**, once the repo has any commit after the
    skeleton commit, or 2 days after promote, whichever comes first.
  - **Mark shipped is refused** for active and templates projects with no `origin` on GitHub: "Push it first:
    shipped means it's on GitHub." Experiments and learning get the red chip but can ship without it.
- Public: only through `gopublic` (gitleaks over full history, security review, MIT, release branch). Push
  protection is already on for public repos.
- Optional (ticket 09, part B, decision 4): a **Create private GitHub repo** button in the project panel, only from this
  machine (same check as the token routes, `server.py:1302-1306`), running exactly the two commands above.

**Pushback**

- You said "the first push to GH should be part of the first round of building". Agreed, and that is why the app
  should not push at promote: an empty skeleton on GitHub is noise, and an automatic `gh repo create` is how a
  repo ends up public by accident. The ship gate is the enforcement.
- `NOTES.md` holds your raw capture. Recommend: track it (it is the plan and the clock, and it needs history),
  and add a `gopublic` step that shows `NOTES.md` and asks before a repo goes public. One public project
  already has its `NOTES.md` tracked: worth a look.

Tickets: 07 (active + experiments skeleton + git init), 08 (learning + templates as folders), 09 (GitHub chip,
ship gate, optional button).

---

## 5. "+7 days" needs a reason, and only twice

**Today:** `+7 days` is one click with no reason (`index.html:826`, `index.html:847-851`); the API takes 1–90
days with no limit and no record (`server.py:1155-1163`). The log shows six pushes, on six different projects, in under a minute. Project Kill also sends an empty reason (`index.html:848`).

**Recommend**

1. `+7 days` opens a small form: **Why does it need more time?** (required, at least 10 characters).
2. On save: `due` moves 7 days (from today or the old due date, whichever is later, as now), the header gets
   `- extensions: N`, and `## Plan` gets `- pushed 2026-11-06 → 2026-11-13 (2026-10-30): <reason>`.
3. The API takes `days` 1–7 only, and refuses a third extension: "Pushed twice already: ship it or kill it."
4. After two, the panel shows only **Mark shipped** and **Kill**, with "No more extensions". An overdue card with
   no extensions left gets a **Decide** badge.
5. Project **Kill** asks for a reason too (required), written under `## Outcome` (already supported:
   `server.py:1164-1166`, the page just never sends one).
6. **Grandfathering:** a missing `extensions:` line counts as 0. Every project starts with two from the day this
   ships. (Alternative: count past `project extend` lines in `triage.log`; I don't recommend it: the log is
   best-effort and one project would be out of extensions on day one.)

Ticket: 05.

---

## 6. Hand-made folders (skipped)

"Folders are no longer made by hand" means **Promote is the only way into `active/`, `experiments/`,
`learning/` and `templates/`**. For what is already there:

- Leave the 8 folders with no ship date as they are (seven private or note-only folders, plus rawnotes-triage). They stay in **No ship
  date**. No adopt button now.
- One cheap fix worth doing now (ticket 10): the board and `sync-apps` treat **git worktrees** as projects. The
  planning worktree for this proposal (`active/rawnotes-triage-flow-gaps`, which you asked for) is on the board
  right now as a hand-made folder. The board should skip a folder whose `.git` is a file (a linked worktree).
  Better still, make worktrees outside the stage folders (T3 already uses `~/.t3/worktrees`).

---

## 7. Closed projects leave `active/`

**Today:** Ship writes `status: shipped` + `shipped:`; Kill writes `status: dead` and an Outcome line
(`server.py:1150-1167`). Nothing moves. 4 of 18 are closed and still in `active/`: rawnotes-triage (shipped) and three
note-only folders (two shipped, one `status: done` set by hand). The last three are a single `NOTES.md` each.

**Pushback: "shipped → maintained" is right for some projects, not all.** Three problems with doing it
automatically:

1. **A one-off isn't "maintained".** `maintained/README.md` says "done and working, but still get dependency
   bumps, small fixes". A folder that is only a `NOTES.md` (3 of the 4 today) is finished, so it belongs in
   `archive/` ("finished or abandoned", `archive/README.md`).
2. **"Shipped" isn't "quiet".** rawnotes-triage is marked shipped (2026-10-19) but got 2.12 on 2026-10-24, has
   this plan in flight, and a Rust port cooking. `maintained/README.md` itself says to move a project back to
   `active/` "during a real push of new work". It should stay in `active/` with a new round.
3. **Moving a folder breaks things that point at it.** For rawnotes-triage today:
   - the `rawtriage-web` systemd user unit (its `ExecStart` runs `server.py` from the `active/` path)
   - `~/.local/bin/rawtriage-web`: still the hand-made wrapper with the `active/` path (2.12's installer, which
     would replace it with a link, has not been re-run)
   - `triage.log` sits next to `server.py` (`server.py:38`), so Pulse history moves with the folder
   - the agent memory folder under `~/.claude/projects/` (keyed by the folder's path)
   - T3 project `rawnotes-triage` (`workspaceRoot` = the `active/` path)
   - the linked worktree `active/rawnotes-triage-flow-gaps` (its `.git` file points at the main repo by absolute path)
   It has already happened twice: one project's agent memory folder was left behind when the project
   moved to `archive/`, and a T3 project still points at a folder that no longer exists.

**Recommended workflow: ship/kill, then "close out" (one step, three choices).**

1. **Mark shipped** asks: *What happens to it now?*
   - **Next round** (stays in `active/`): new done means + new due, `extensions` reset to 0, status back to
     `promoted`, the old round written to `## Rounds` in `NOTES.md`. Use for rawnotes-triage.
   - **Maintained**: moves to `maintained/<slug>`. Suggested when the folder has git with a GitHub origin.
   - **Archive**: moves to `archive/<slug>`. Suggested when the folder has no git or only `NOTES.md`.
2. **Kill** always goes to **Archive** as `archive/<slug>_abandoned` (fix the typo in `active/README.md`:
   `_abbandoned` → `_abandoned`). Experiments are killed the same way.
3. **Before any move: a dependency check** (read-only, same scan for the CLI and the page):

   | Looks at | Finds | Result |
   |---|---|---|
   | `~/.config/systemd/user/*.service|.timer|.path` | the folder path in the file | **blocks** |
   | running processes (`/proc/*/cwd`, `/proc/*/cmdline`) | anything running from inside the folder | **blocks** |
   | `git worktree list` in the folder; a `.git` file in the folder | linked worktrees, or the folder is one | **blocks** |
   | `~/.local/bin/*` | links into the folder; scripts (<64 KB) containing the path | **blocks** |
   | `crontab -l` | the path | **blocks** |
   | `~/.claude/projects/<path with / as ->` | agent memory for the old path | to-do (command given) |
   | T3 projects | can't be read by the app | to-do, always listed: "update T3 projects pointing at <path>" |

   Blocked → nothing moves; the panel lists each hit with the command that fixes it (for example
   `systemctl --user disable --now <name>.timer`, re-run `./install-webapp.sh` from the new place,
   `git worktree remove …`). To-dos → the move goes ahead and the list is written to `NOTES.md` under
   `## Moved` and shown in the toast, with the exact `mv ~/.claude/projects/… …` line. The app never writes
   outside the notes and projects folders (`server.py:799`), so it does not move memory dirs itself; the skill
   (section 0) can, and can update T3 with its project tools.
4. **Undo** moves the folder back (one undo step with the status change). The engine needs a small addition: it
   checks files, not folders, when it plans an undo (`server.py:858`, `server.py:868`).
5. After a move or its undo: run `sync-apps` if it is on PATH (best effort, output to the log), so the
   Obsidian `Apps/` link follows the folder (`maintained/obsidian-sync-apps`, README "Move a repo… rerun").
6. Moves happen when you decide, never on a timer: a move hours later, with no one watching, is when a hidden
   dependency breaks something.

**Where I disagree with your version, plainly:** not every shipped project goes to maintained (one-offs go to
archive); shipped-and-still-being-worked-on stays in active as a new round; and nothing moves while something
still points at it.

Tickets: 06 (closed date), 11 (dependency check), 12 (close out and move).

### 7.1 Closed column: last 7 days only

**Today:** Closed = every card with status `done`, `shipped` or `dead` (`index.html:791`, `index.html:799`).
Kill writes no date in the header.

**Recommend**

1. Every close writes `- closed: YYYY-MM-DD` (ship, kill, and close out). `shipped:` stays for old readers.
2. Closed shows cards with `closed` (else `shipped`, else the date in a `**Killed YYYY-MM-DD:**` Outcome line)
   in the last 7 days. No date at all = older than 7 days.
3. Because closed projects now leave `active/`, the board also scans `maintained/` and `archive/` for the
   Closed column only (folders closed in the last 7 days).
4. The column header reads `Closed (last 7 days) 2` and has **Show all (9)**, so nothing disappears without a
   trace.

Ticket: 06.

---

## 8. Reference keeps `reference/README.md` up to date

**Today:** Reference copies the note and only reminds you (`index.html:631`, README:170). Result: 21 of 37
reference notes are not in the index. `reference/` is not a git repo, so a bad edit there has no history.

**Recommend: Reference adds the index line in the same undoable step.**

1. The Reference form gets two fields: **Index section** (a list of the `### ` headings under `## Index` in
   `reference/README.md`, plus "New section…" and "Don't index") and **One-line summary** (prefilled from the
   "why", else the first sentence of the question).
2. The server adds `- [<Title>](<slug>.md): <summary>` as the last line of that section (or a new
   `### <Section>` at the end of the index). It records `section:` in the note's frontmatter too.
3. Safety rules:
   - only lines between `## Index` and the next `## ` heading (or end of file) are touched; nothing is reordered
     or rewritten;
   - no `## Index` heading, or no `README.md` → the note is still filed, the index step is skipped with a
     warning;
   - a link to the same `<slug>.md` already present → no second line;
   - `]`, `[` and newlines in the title/summary are escaped or stripped;
   - it is one Tx write, so it is undone with the rest and refused if the README changed since
     (`server.py:850-884`).
4. **Unindexed list:** the Archive page (Reference filter) shows "21 reference notes aren't in the index", each
   with **Add to index** (same form). `rawtriage reference --unindexed` lists them for agents. Backfill the 21
   once with that.
5. Batch **Reference all** uses one section + per-note summary from the question.

Alternative I don't recommend: regenerate the whole index from frontmatter (like `sync-apps` does for
`_Apps index.md`). One source of truth, but it overwrites your hand-written blurbs and headings unless they are all
migrated into frontmatter first.

Ticket: 13.

---

## Build order

```
01 auto-commit ─────────────┐
02 rawtriage CLI ── 03 skill ┤
04 inbox limits (nudge: 02)  │
05 extension limit           │
06 closed date + 7 days ─────┼── 12 close out + move ── 14 cleanup (by hand / agent)
11 dependency check ─────────┘
07 skeleton + git init ── 08 learning/templates folders
                       └─ 09 GitHub chip + ship gate
10 skip worktrees on the board
13 reference index
```

Every ticket bumps `VERSION` by 0.01 and adds a CHANGELOG entry (`AGENTS.md`). Parallel branches will clash on
that line; bump at merge time.

---

## Decisions

**Approved 2026-10-30:** Hugh chose the recommended answer for all 13 decisions below.

| # | Decision | Recommended (approved 2026-10-30) |
|---|---|---|
| 1 | Auto-commit RawNotes on every web/CLI change? | **Yes**, pathspec-only, opt-out flag. |
| 2 | Server down: agents start it, or fall back to editing files? | **Start it; if that fails, stop and tell you.** No hand edits. |
| 3 | Inbox numbers: open stale / cooking stale / Inbox limit / max keeps | **14 / 7 / 30 days / 3 keeps.** |
| 4 | App button to create a private GitHub repo? | **Yes, as an explicit button (ticket 09 part B)**, private only, never at promote. |
| 5 | Ship gate "must be on GitHub" for which categories? | **Active and templates.** Experiments and learning: red chip only. |
| 6 | Track `NOTES.md` in project repos? | **Track**, and add a NOTES.md review step to `gopublic`. Check the tracked NOTES.md in each public repo. |
| 7 | Skeletons in the app repo or in `~/Projects/templates/`? | **App repo** (`starter/skeletons/`): versioned and tested with the app. |
| 8 | Extensions for existing projects: start at 0 or count the log? | **Start at 0.** |
| 9 | Shipped → maintained always? | **No: choose Next round / Maintained / Archive**, with a suggestion. |
| 10 | Killed folders renamed `<slug>_abandoned`? | **Yes** (your rule), and fix the `_abbandoned` typo. |
| 11 | rawnotes-triage now | **Next round in `active/`** (not maintained): it is still being built. |
| 12 | Reference index: add-a-line or regenerate? | **Add a line**, plus an Unindexed list for the 21 missing. |
| 13 | Where the skill text lives | **App repo `skills/rawnotes/`**; RawNotes `TRIAGE.md` and `AGENTS.md` become symlinks. |
