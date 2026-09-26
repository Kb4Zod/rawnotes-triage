# 01: Copy a note's absolute path from Backlog and Done

**What to build:** Mirror the project copy-path feature for notes. Every note in the Backlog and Done lists gets a small Copy button that copies the note's absolute `.md` path without selecting the note; the selected note's detail view shows its absolute path as a click-to-copy line; pressing `y` on the Backlog/Done tabs copies the selected note's path. All reuse the existing `copyPath(path, el)` helper and its one-second "Copied" feedback. The notes API adds `abs_path` per note (absolute path to the file, `done/` subfolder respected, not realpath-resolved); the existing relative `file` field is unchanged. See `.scratch/copy-project-path/spec.md` for the original decisions.

**Blocked by:** None (can start immediately)

**Status:** done (PR #4)

- [ ] `/api/notes` returns `abs_path` for every live and done note = `<notes dir>/<file>` (done notes under `<notes dir>/done/`); `file` unchanged
- [ ] Each Backlog/Done row has a Copy button (same look as the project rows) that copies `abs_path`, shows "Copied" ~1 s, and does not select the row on click or Enter/Space; keyboard-focusable
- [ ] The note detail view shows `abs_path` as a click-to-copy line (title hint, Enter/Space works)
- [ ] `y` on Backlog/Done copies the selected note's `abs_path` with feedback on the detail path line; no-op when nothing is selected; p/d/a/c/Enter/j/k unchanged; `y` on the Projects tab unchanged
- [ ] Hint text for Backlog/Done mentions `y copy path`
- [ ] Filenames with spaces/special characters copy exactly
- [ ] `test_server.py` gains HTTP-seam tests for `abs_path` on live and done notes against a temp notes dir; `python3 -m unittest` passes; no new dependencies
