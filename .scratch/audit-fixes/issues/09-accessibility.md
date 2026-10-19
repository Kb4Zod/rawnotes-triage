# 09: axe-clean in both themes, dialogs, native semantics

**Blocked by:** 08

**Status:** done (branch fix/audit-reliability-accessibility)

**Covers findings:** 12

See ../spec.md.

- [x] axe-core 4.10.3: 0 violations on desktop Keep/Projects/Merge, mobile Reference 390×844, dark Inbox, Pulse, Archive, palette/keys dialogs, mobile stack/projects/pulse/toast in both themes — `test_browser.Accessibility` (15 sweeps)
- [x] Palette/keys are modal dialogs (inert background, trap, returned focus, listbox) — `test_browser.Overlays`
- [x] Native checkbox + button in stack rows, unique names, landmarks, heading order, named inputs — `test_browser.Structure`
- [x] Mobile toast clear of nav/sheet with 44 px Undo; 44 px targets; no overflow at 1280/390/320 — `test_browser.Responsive`
