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
   - **kill**: ask why in a few words, then `rawtriage kill <file> --reason "<why>"`.
   - **merge** (several notes on one topic): `rawtriage merge <file> <file> ... --title "<title>"`, optionally
     `--then promote ...` with the promote options above.
   - **skip**: move on, touch nothing.
   `<file>` is the note's filename (a unique part of it works).
3. **Be decisive on their behalf when asked.** If the user says "you pick", recommend one option with a
   one-sentence reason and act on it once they confirm.
4. **Mistakes.** `rawtriage undo` reverses the newest change (the page's undo list is the same list). Say what it undid.
5. **Never edit a note to decide it.** No status edits, no moving files, no copying into project folders. If a command
   exits 1, read its message and ask the user; if it exits 2, stop and tell the user (see `SKILL.md`).
6. **Finish with a summary.** Run `rawtriage -l` again. Report how many were promoted, kept, killed and merged, and what
   is still open.
7. **Projects that are due.** `rawtriage project ship <name>` when done means is met. Killing needs a reason:
   `rawtriage project kill <name> --reason "<why>"`. Pushing the ship-by needs a reason too (at least 10 characters)
   and is allowed at most twice: `rawtriage project extend <name> --reason "<why more time>" [--days N]`. After two
   extensions the server refuses: the only choices left are ship or kill, so ask the user which.
   Shipping an `active` or `templates` project is refused until it is on GitHub ("Push it first"). Tell the user, and
   offer `rawtriage project github <name>` (creates a **private** repo and pushes `main`; only on the server's own
   machine; it never makes anything public, that is the user's `gopublic`). Run it only when they say yes.
