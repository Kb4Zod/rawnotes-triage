# 03: Symlink containment and a safe /api/open

**Blocked by:** 01

**Status:** done (branch fix/audit-reliability-accessibility)

**Covers findings:** 7, 15

See ../spec.md.

- [x] Writes need real paths inside notes/projects with no symlink on the way (parents included); symlinked project folders listed read-only with `linked: true` — `test_safety.Containment`
- [x] Reproduced `active/linked -> outside` + `project_status`/`done` writes nothing outside — `test_every_project_write_refuses_a_symlinked_folder`
- [x] `/api/open` accepts only `{name,kind}` or a board-listed `{path}`, loopback clients only, `xdg-open <absolute path>` (not `--`: the installed xdg-open rejects it), no shell; URLs/absolute paths/`..` refused — `test_safety.OpenEndpoint`

Verification fixes (2.06):

- [x] The real `/usr/bin/xdg-open` accepts the exact command the app builds (clean env, no display, stub `run-mailcap`; nothing is opened) and refuses the old `--` form — `test_safety.XdgOpenContract`; an opener that fails at once is reported — `test_an_opener_that_fails_at_once_*`
- [x] Undo/rollback/redo recheck containment of every recorded path (file, move source and destination, created folder) before changing anything; refused undo keeps files and history — `test_engine.UndoContainment` (11 tests: folder swapped for a link inside/outside the root, file swapped for a same-content link, move destination/source, promotion folder, all-steps-before-any, recovery after fixing, rollback, move rollback, redo recovery)
- Limitation: a swap between a check and the write inside one request is not prevented (no `openat`/`O_NOFOLLOW` directory walk).
