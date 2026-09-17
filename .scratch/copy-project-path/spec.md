# Spec: Copy project path

Status: ready-for-agent

## Problem Statement

On the Active Projects tab, Hugh can see each project but only as a relative path (`active/<name>`). To use a project elsewhere (terminal, editor, agent prompt) he has to retype or reconstruct the full path by hand.

## Solution

Every active project exposes its full absolute path, and that path can be copied to the clipboard with one click (sidebar row or detail pane) or one keypress.

## User Stories

1. As Hugh, I want a copy button on each active project row, so that I can grab a project's path without selecting it first.
2. As Hugh, I want clicking the row's copy button to not change the selected project, so that copying doesn't disturb what I'm viewing.
3. As Hugh, I want the detail pane to show the project's full absolute path, so that I can see exactly what will be copied.
4. As Hugh, I want to click the path in the detail pane to copy it, so that the obvious target works.
5. As Hugh, I want to press `y` to copy the selected project's path, so that I can stay on the keyboard alongside `j`/`k`.
6. As Hugh, I want the copied text to be the plain absolute path (no `file://`, no `cd`), so that it pastes cleanly anywhere.
7. As Hugh, I want the path to be the literal location under my Projects folder, not a symlink-resolved one, so that it matches the path I know and type.
8. As Hugh, I want the control to briefly read "Copied" (about one second), so that I know the copy worked.
9. As Hugh, I want sidebar rows to keep showing the short `active/<name>` text, so that the narrow list stays readable.
10. As Hugh, I want the path to be correct when the server is started with a non-default notes directory, so that the copied path always points at the real folder.
11. As Hugh, I want project names with spaces or special characters to copy exactly, so that the path is always usable.
12. As Hugh, I want `y` to do nothing on the Backlog and Done tabs or when no project is selected, so that existing shortcuts are unaffected.

## Implementation Decisions

- The projects list response and the project detail response each gain an `abs_path` field per project: the projects root joined with `active/<name>`, absolute, not realpath-resolved. The existing relative `path` field is unchanged.
- The frontend never constructs the absolute path itself; it copies `abs_path` verbatim.
- One shared copy helper in the page handles clipboard write plus the "Copied" feedback, used by the row button, the detail path line, and the `y` key.
- Clipboard uses the browser's async clipboard API (the site is served on localhost, which is a secure context).
- The row copy button stops event propagation so the row's select handler does not fire; it is keyboard-focusable and Enter/Space on it must not trigger row selection either.
- The detail pane's meta line shows `abs_path` (plus branch as today) and is click-to-copy with a title hint.
- `y` is handled only in the projects-tab branch of the existing key handler; for the keypress, feedback appears on the detail path line.
- The hint text for the projects tab mentions `y`.

## Testing Decisions

- Good tests assert external behavior only: what the HTTP API returns, not how it is computed.
- Single seam: the HTTP API. Tests start the server handler against a temporary projects root and assert that the projects list and project detail responses include `abs_path` equal to `<temp root>/active/<name>`, that it is absolute, that it is not symlink-resolved, and that the relative `path` is unchanged.
- There is no existing test suite; this adds the first one using Python's standard-library `unittest` (no new dependencies), runnable with `python3 -m unittest`.
- The frontend has no test harness and none is added; UI behavior is verified manually against the user stories.

## Out of Scope

- `file://` links, "open in editor/terminal" actions, or copying shell commands.
- Copy controls for notes, the Backlog/Done tabs, or projects outside `active/`.
- Toast/notification system.
- A frontend test harness.

## Further Notes

Decisions come from the 2026-09-17 grill; Hugh accepted all recommendations.
