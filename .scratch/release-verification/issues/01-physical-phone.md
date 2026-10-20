# Verify the app on physical phones

Status: open
Type: verification
Priority: medium

## Problem
Resizing a desktop browser does not test touch input, on-screen keyboards, browser chrome, safe areas or mobile engines.

## Acceptance criteria
- [ ] Record device, OS and browser versions; test iOS Safari and Android Chrome where devices are available.
- [ ] Exercise search, note reading, all decisions, stacks, restore, undo and project editing on disposable notes.
- [ ] Verify portrait/landscape, keyboard open/closed, safe areas, scrolling and action/Undo reachability.
- [ ] Confirm drafts survive app switching; modal focus and dismissal remain usable.
- [ ] Record secure-context clipboard behavior and access method; list unavailable combinations explicitly.
- [ ] Attach results and create individual defect tickets for failures.

## Comments
2026-10-19: Desktop layouts at 1280, 390 and 320 CSS pixels were checked; physical devices were not.
