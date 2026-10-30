# Triage raw notes

Your job is to make decisions happen, not to do more research. Every decision goes through `rawtriage`.

1. **Check headers, then show the backlog.** Run `rawtriage check`; if any note has a bad header, raise it in one
   question with suggested values and fix the headers to match `_template.md` (a header fix is not a decision). Then run
   `rawtriage -l` and present a short table: status, age in days, title. The command marks `[stale]` and `[expired]`
   notes; repeat those marks, don't work them out yourself.
2. **One note at a time, oldest first.** Find the note with `rawtriage where` and read it. Give a 2-3 line recap of the
   question and where it got to, then ask for exactly one decision:
   - **promote** (it became real work). Ask the destination: `experiments`, `active`, `learning` or `templates`.
     Ask **what done means** (one concrete sentence). Unless the destination is `experiments`, ask the **ship-by**
     too (a number of days or a date). Then run
     `rawtriage promote <file> --dest <dest> --done "<done means>" --due-days <N>` (or `--due YYYY-MM-DD`; add
     `--slug <name>` to name the project). Never promote without an answer to each question.
   - **reference** (permanent knowledge, not work): `rawtriage reference <file> --why "<one line>"`.
   - **keep** (still alive): `rawtriage keep <file> [--status cooking] [--next "<one line next step>"]`. If the note is
     `[expired]`, do not offer keep: the choices are promote, reference or kill.
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
   is still open. Projects that are due can be closed with `rawtriage project ship|kill <name>`.
