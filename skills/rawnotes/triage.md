# Triage raw notes

Your job is to make decisions happen, not to do more research. Every decision goes through `rawtriage`.

1. **Check headers, then show the backlog.** Run `rawtriage check`; if any note has a bad header, raise it in one
   question with suggested values and fix the headers to match `_template.md` (a header fix is not a decision). Then run
   `rawtriage -l` and present a short table: status, age in days, title. The command marks `[stale]` and `[expired]`
   notes; repeat those marks, don't work them out yourself (`rawtriage -l --summary` gives the one-line counts). Stale:
   `open` untouched for 14 days or `cooking` for 7. Expired: in the Inbox over 30 days, or kept 3 times already; the
   server's numbers win if they were changed.
2. **One note at a time, oldest first.** Find the note with `rawtriage where` and read it. Give a 2-3 line recap of the
   question and where it got to, then ask for exactly one decision:
   - **promote** (it became real work). Ask the destination: `experiments`, `active`, `learning` or `templates`.
     Ask **what done means** (one concrete sentence). Unless the destination is `experiments`, ask the **ship-by**
     too (a number of days or a date). Then run
     `rawtriage promote <file> --dest <dest> --done "<done means>" --due-days <N>` (or `--due YYYY-MM-DD`; add
     `--slug <name>` to name the project). Never promote without an answer to each question.
   - **reference** (permanent knowledge, not work): ask which **index section** of `reference/README.md` it belongs in
     (run `rawtriage reference --unindexed` once to see the section names; a new name adds a section; "don't index" is
     allowed) and a one-line summary, then
     `rawtriage reference <file> --why "<one line>" --section "<section>" [--blurb "<summary>"]` (leave out
     `--section` only if the user says not to index it). Notes filed without an index line show up in
     `rawtriage reference --unindexed`; add one with `rawtriage index <name.md> --section "<section>"`.
   - **keep** (still alive): `rawtriage keep <file> [--status cooking] [--next "<one line next step>"]`. If the note is
     `[expired]`, do not offer keep: the choices are promote, reference, merge or kill (the server refuses keep anyway).
     A note restored from the Archive starts over: a fresh 30 days and 3 Keeps.
   - **kill**: ask why in a few words, then `rawtriage kill <file> --reason "<why>"`.
   - **merge** (several notes on one topic): `rawtriage merge <file> <file> ... --title "<title>"`, optionally
     `--then promote ...` with the promote options above.
   - **skip**: move on, touch nothing.
   `<file>` is the note's filename (a unique part of it works).
3. **Be decisive on their behalf when asked.** If the user says "you pick", recommend one option with a
   one-sentence reason and act on it once they confirm.
4. **Mistakes.** `rawtriage undo` reverses the newest change (the page's undo list is the same list). Say what it undid.
5. **Never edit a note to decide it.** No status edits, no moving files, no copying into project folders. If a command
   exits 1, read its message and ask the user; if it exits 2, stop and tell the user; if it exits 3, check with
   `rawtriage -l` before retrying (see `SKILL.md`).
6. **Finish with a summary.** Run `rawtriage -l` again. Report how many were promoted, kept, killed and merged, and what
   is still open.
7. **Projects that are due.** When done means is met, a project folder is **closed out**: ask the user what happens
   now, offering the suggestion from `rawtriage project deps <name>` (maintained when it has a GitHub origin, else
   archive):
   - **Next round** (still being built, stays where it is): ask for the new done means and ship-by, then
     `rawtriage project close <name> --outcome next --done "<done means>" --due-days N` (or `--due YYYY-MM-DD`).
   - **Maintained** or **Archive** (the folder moves to `maintained/<name>` or `archive/<name>`):
     `rawtriage project close <name> --outcome maintained|archive`.
   Killing needs a reason and moves the folder to `archive/<name>_abandoned`:
   `rawtriage project kill <name> --reason "<why>"`. Pushing the ship-by needs a reason too (at least 10 characters)
   and is allowed a limited number of times (twice unless the server's `max_extensions` says otherwise):
   `rawtriage project extend <name> --reason "<why more time>" [--days N]`. Once they are used up the server refuses
   ("Pushed … already"): the only choices left are ship or kill, so ask the user which.
8. **Moves.** A move is refused while anything points at the folder (systemd unit, running process, git worktree,
   a link or script in the local bin folder, crontab line): show the user each blocker and its fix from `rawtriage project deps`,
   and let them fix it; never fix it silently. Never run a blocker's fix yourself (`kill <pid>`, `systemctl … disable`,
   `rm`, `crontab -e`): show it to the user, who runs it. Don't run a close out (or a project kill) from inside the
   project folder: your own session and shell count as processes working there and block the move (the `rawtriage`
   command itself doesn't); run it from the notes folder or anywhere else. Never move a project folder yourself, never on a timer, only when the
   user decides. After a move, the command prints what is left to do by hand (also written under `## Moved` in the
   project's NOTES.md): the `mv` of its Claude Code agent memory folder (under `.claude/projects` in the home folder), and updating any T3 Code project
   that points at the old folder. Offer to do those (with T3's own project tools for T3), with the user's OK.
9. **GitHub before close out (`active` and `templates` only).** Closing out to **maintained** is refused until the
   folder's work is on GitHub: a GitHub origin that has been pushed to, with no local branch ahead of it ("Push it
   first"). **Archive** is refused too when the folder is a git repo with work in it, until that work is pushed (any commit after Promote's skeleton commit, or any commit at all if there is no skeleton commit); a folder
   with no git repo, or only the skeleton commit, archives without GitHub. **Next round** and **kill** are never
   gated. A refusal for unpushed commits names each branch: push it (`git push origin <branch>`), or, if the user says
   it was already merged on GitHub, delete it (`git branch -D <branch>`); show the user those commands, don't run them
   unasked. When refused for no GitHub repo, tell the user and offer `rawtriage project github <name>` (creates a **private** repo and
   pushes `main`; only on the server's own machine; it never makes anything public, that is the user's `gopublic`).
   Run it only when they say yes, then retry the close out.
