# Preserve undo history across server restarts

Status: open
Type: enhancement
Priority: medium

## Problem
Restarting the server discards undo history even though the file changes remain. Users cannot reverse a recent
decision through the app after restarting.

## Scope
Design a versioned, bounded local journal scoped to the configured notes/projects roots. Keep note contents private,
write journal updates atomically, and define recovery for interruption between file changes and journal updates.
Never replay operations automatically on startup. Preserve existing content-conflict and containment checks.

## Acceptance criteria
- [ ] A decision, restart, and explicit Undo restore the original files, including multi-file promotion and merge.
- [ ] Externally edited files are refused without partial changes; malformed or incompatible journals fail safely.
- [ ] Journal roots cannot be reused against a different notes workspace; stored paths are revalidated.
- [ ] Interrupted-write recovery, retention, file permissions and journal cleanup are tested and documented.
- [ ] Tests use disposable fixtures; existing rollback and undo behavior still passes.

## Comments
2026-10-19: Captured after the v2.04 audit repair. Not a blocker for merging those repairs.
