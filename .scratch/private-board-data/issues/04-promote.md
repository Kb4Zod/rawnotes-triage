# Promote: trimmed NOTES.md, capture only in RawNotes, record created

Status: done
Type: task
Blocked by: 01
Spec: ../spec.md (D2, D3)

## What
Promote writes a trimmed `NOTES.md` (title, `## Plan` with definition of done, first step and check-in line, no
dates), keeps the original capture only in `RawNotes/done/`, and creates the record (status, promoted, due,
`github: private`, a link to the capture).

## Acceptance criteria
- [ ] `NOTES.md` has the plan and no capture text, status, dates or `github:`.
- [ ] The record has all of them.
- [ ] The first commit in the new repo contains no private field.
- [ ] Skeleton `AGENTS.md` still gets `{{due}}` and points agents at `rawtriage brief <slug>`.
