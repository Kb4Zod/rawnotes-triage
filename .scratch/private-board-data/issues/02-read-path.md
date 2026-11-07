# Read path: record first, legacy NOTES.md fallback

Status: open
Type: task
Blocked by: 01
Spec: ../spec.md (D4, D5, D7)

## What
Status, due date, extensions, `on_board`, the GitHub gate and project facts read the record first and fall back to
the legacy fields in `NOTES.md` when there is no record.

## Acceptance criteria
- [ ] `project_facts` / `folder_status` / `folder_closed` / `inflight` / `_promoted_days` read the record first.
- [ ] Legacy projects (no record) show the same status, due date and extensions as before.
- [ ] `on_board` for `learning/` and `templates/`: a record only, a legacy `promoted:` only, and neither.
