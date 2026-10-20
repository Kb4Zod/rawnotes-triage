# Spec: v2.04 audit fixes (reliability, accessibility, load)

Status: implemented on `fix/audit-reliability-accessibility` (awaiting review)

## Problem Statement

A code review plus a browser, accessibility (axe-core 4.10.3) and load audit of v2.04 found 16 groups of
problems: writes that can half-finish and leave no undo entry, header edits that corrupt notes, a page that
merges notes on one Enter, read errors under concurrent writes, symlink escapes, a `--restart` that can kill an
unrelated service, stale/overwritten screens from late network replies, WCAG failures, silent GitHub truncation,
and an `/api/open` that hands arbitrary strings to `xdg-open`.

## Solution

Fix every finding without a framework rewrite and without new runtime dependencies (Python stdlib server,
one `index.html`). Small stdlib helper modules are allowed where they make the fixes safer:

- `safefs.py`: path containment, atomic replace, a readers/writer lock, a stat-keyed cache.
- `notemeta.py`: header (metadata) parsing/editing and the question/body split, pure functions.

## Implementation Decisions

- **One mutation path.** Every mutation (decide, batch, merge, restore, project status, done means) runs inside
  `mutation()`: write lock, a `Tx` that applies each step atomically and records it, automatic rollback if a later
  step fails, and an undo entry committed *before* anything is logged. Logging is best effort.
- **Inputs are validated before the first write**, including duplicate sources.
- **Header = the block of `- key: value` bullets right under the title.** Nothing below it is metadata.
- **Containment:** writes may only touch real (non-symlink) paths under the notes dir or the projects root;
  symlinked project folders stay visible read-only (`linked: true`).
- **Reads and writes are synchronized** with a readers/writer lock; listings tolerate files that vanish.
- **Page:** request generations/abort per screen, draft store for editors, explicit activate-vs-submit
  separation, dialog semantics for overlays, native controls for rows/checkboxes.
- **`--restart` verifies identity** (triage page title and `/proc/<pid>/cmdline`) before signalling.
- **`/api/open`** accepts only `{name, kind}` of a project folder or a promoted-file path the board lists, and
  only from a loopback client.
- **GitHub activity** paginates repos, reports truncation, and is labelled as commits (by commit date on the
  default branch), not pushes.

## Testing Decisions

stdlib `unittest` only. Fault injection through monkeypatched `os.replace` / `os.rename` / `log`. Browser
regressions drive Chromium through a small stdlib CDP client (`test_browser.py`, skipped when Chromium is
absent); axe-core is used when `AXE_JS` points at a local copy (never a product dependency).

## Out of Scope

Framework rewrite, new runtime dependencies, authentication, changing real notes or real sibling projects.
