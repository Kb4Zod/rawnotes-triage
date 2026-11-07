# Record store: per-project board record in RawNotes

Status: open
Type: task
Spec: ../spec.md (D1)

## What
A per-project board record at `RawNotes/projects/<kind>/<slug>.md`: a markdown file with the same `- field: value`
header the app already parses, plus sections (`## Rounds`, `## Moved`, `## Capture`, `## Extensions`, `## Outcome`).

## Acceptance criteria
- [ ] Path scheme `projects/<kind>/<slug>.md` for every project kind plus `maintained` and `archive`.
- [ ] Read and written only through `Tx`, so undo, the lock and auto-commit work unchanged.
- [ ] Auto-commit (`_commit_paths`) picks up the record paths.
- [ ] A close-out move renames the record in the same transaction; undo renames it back.
