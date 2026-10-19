# Anchor filesystem mutations to validated directory handles

Status: open
Type: hardening
Priority: medium

## Problem
An external local process can replace a path component with a symlink between validation and a filesystem operation.
The v2.06 checks close the between-request gap, but a check followed by a path-based write still has a narrow race.

## Scope
Use directory descriptors and no-follow traversal for filesystem operations, including temporary writes, rename,
delete, mkdir, rollback, undo and recovery. Define the trusted-root policy and behavior on unsupported platforms.
Keep the standard-library deployment model where feasible; document any unavoidable platform constraints.

## Acceptance criteria
- [ ] Deterministic tests swap parent directories and final components at the validation/operation boundary.
- [ ] No bytes or entries outside approved roots are changed by normal writes, undo, rollback or redo recovery.
- [ ] Atomic replacement, modes, supported line endings, conflict detection and recoverable history are preserved.
- [ ] Directory handles are closed on success and every failure path; cross-filesystem moves fail or recover safely.
- [ ] Current symlink/read-only-project behavior and the complete safety suite remain covered.

## Comments
2026-10-19: Follow-up hardening against concurrent local filesystem changes, not a claim of remote exploitation.
