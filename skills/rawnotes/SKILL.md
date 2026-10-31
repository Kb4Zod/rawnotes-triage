---
name: rawnotes
description: Capture and triage RawNotes, the user's scratch folder of raw questions, research and issues. Use when the user says "rawnote:", "capture this", "note this down", "triage", "review my notes", or "what's open", or asks to add, review, promote, keep or kill a raw note, from any folder or repo.
---

# rawnotes

RawNotes is a flat folder of dated notes (`YYYY-MM-DD-topic.md`) with a `done/` archive. The `rawtriage` command is the
only way to create a note or decide its fate; it talks to the rawnotes-triage server, which keeps the rules, the undo
list, the log and the git history for every agent and for the web page alike.

Find the folder with `rawtriage where` (it prints the notes folder and the projects root). Never guess a path.

## Which workflow

- **Capture** ("rawnote: ...", "capture this", a new thought to write down): read `capture.md` in this skill's folder.
- **Triage** ("triage", "review my notes", "what's open", deciding what to do with notes): read `triage.md` in this
  skill's folder.

## Rules for both

1. **Decisions only through `rawtriage`.** Promote, keep, kill, reference, merge, undo, project status and close out (moves) are
   `rawtriage` commands. Never decide by editing a note's status, moving it to `done/`, or copying it to a project folder.
2. **If `rawtriage` exits 2, stop.** It means there is no working triage server and nothing was changed. Tell the user.
   Do not fall back to editing files. Exit 1 means the server refused: read its message, ask the user, do not work
   around it.
3. **RawNotes is local only.** Never add a remote and never push it.
4. **Never reorganize the folder.** Flat root, dated filenames, `done/` for finished notes, no topic subfolders.
5. Writing the user's own words and findings into a note is fine; that is capture, not a decision.
