# `rawtriage brief <slug>`

Status: open
Type: task
Blocked by: 04
Spec: ../spec.md (D3)

## What
Prints a project's original capture and its plan through the server (no paths), for agents in a worktree.

## Acceptance criteria
- [ ] `GET /api/brief?name=<slug>[&kind=]` returns the capture text and the plan.
- [ ] `rawtriage brief <slug> [--kind K] [--json]`.
- [ ] No absolute paths in the answer.
