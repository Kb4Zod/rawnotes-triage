# 01: Copy full path from the detail pane

**What to build:** On the Active Projects tab, the detail pane shows the selected project's full absolute path instead of the relative one, and clicking that path copies it to the clipboard with brief "Copied" feedback. The projects list and project detail API responses gain an `abs_path` field (projects root joined with `active/<name>`, absolute, not symlink-resolved); the existing relative `path` is unchanged. Adds the repo's first tests at the HTTP API seam. See `../spec.md`.

**Blocked by:** None (can start immediately)

**Status:** done (branch feat/copy-path-detail-pane, d4ff145)

- [ ] Projects list and project detail responses include `abs_path` = `<projects root>/active/<name>`; relative `path` unchanged
- [ ] `abs_path` is absolute, honours a non-default notes directory, and is not realpath-resolved (symlinked project keeps its literal path)
- [ ] stdlib `unittest` tests cover the above through the HTTP API against a temporary projects root; `python3 -m unittest` passes; no new dependencies
- [ ] Detail pane meta line shows `abs_path` (branch still shown) with a title hint that it is click-to-copy
- [ ] Clicking it writes exactly `abs_path` (no `file://`, no `cd`) to the clipboard via a single reusable copy helper
- [ ] Feedback reads "Copied" for about one second, then reverts; no toast
- [ ] Sidebar rows still show the short `active/<name>`
