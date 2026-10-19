# rawnotes-triage

See AGENTS.md for skills, issue tracker and versioning rules.

## Merge gate

The `merge-gate` job synthesizes the code review, the security review, and
CI into one verdict. It never re-reviews the diff. Rules:

**BLOCK** (the required check fails) if ANY of:
- Security review has a **High** finding.
- Code review left a finding that says the code will not compile/parse, or
  returns definitely wrong results regardless of input, and the comment is
  still attached to a current line (GitHub "position" not null).
- The `test` job or any other required CI check is red.
- The `secrets` job (gitleaks) is red: a credential was committed.
- A secret, token, or credential appears committed in the diff per either
  review.

**NEEDS WORK** (check passes, comment nudges) if none of the BLOCK rules hit
and ANY of:
- Security review has **Medium** findings only.
- Code review left non-fatal findings or suggestion blocks on current lines.
- A review job itself failed or was skipped (`failure` / `cancelled` /
  `skipped`), so coverage is incomplete. Say which one.

**MERGEABLE** if:
- `test` is green, and
- the security comment says no High/Medium findings (or there is none and
  the security job succeeded), and
- code review has no current-line findings (a "No issues found" comment, or
  only outdated comments).

Formatting:
- Cite the specific finding or check for every non-MERGEABLE verdict.
- Never invent findings. If a source is missing, say so and apply the
  NEEDS WORK incomplete-coverage rule.
- The last line of the comment is exactly `Verdict: MERGEABLE`,
  `Verdict: NEEDS WORK`, or `Verdict: BLOCK`.

## Code review rules

- Blocking: anything that breaks the build or returns wrong results.
- Also flag: committed secrets or `.env` files, and injection paths where
  note text or request input reaches a shell command, file path, or HTML
  without escaping (the server moves files and has no login).
- Skip: style, formatting, and anything a linter catches.
