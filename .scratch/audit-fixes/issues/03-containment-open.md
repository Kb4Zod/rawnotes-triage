# 03: Symlink containment and a safe /api/open

**Blocked by:** 01

**Status:** done (branch fix/audit-reliability-accessibility)

**Covers findings:** 7, 15

See ../spec.md.

- [x] Writes need real paths inside notes/projects with no symlink on the way (parents included); symlinked project folders listed read-only with `linked: true` — `test_safety.Containment`
- [x] Reproduced `active/linked -> outside` + `project_status`/`done` writes nothing outside — `test_every_project_write_refuses_a_symlinked_folder`
- [x] `/api/open` accepts only `{name,kind}` or a board-listed `{path}`, loopback clients only, `xdg-open --`, no shell; URLs/absolute paths/`..` refused — `test_safety.OpenEndpoint`
