# 08: Stale replies, drafts, caches, Enter/Space

**Blocked by:** 01

**Status:** done (branch fix/audit-reliability-accessibility)

**Covers findings:** 5, 6, 9, 10

See ../spec.md.

- [x] `scope()` cancels/ignores late replies (success and failure) for reader, archive, drawer, projects, pulse, repo — `test_browser.StaleReplies`
- [x] Done-means and decision-form drafts survive refresh/navigation; beforeunload guard — `test_browser.Drafts`
- [x] `changed()` drops cached Projects/Pulse data after every mutation and undo — `test_browser.Caches`
- [x] One Enter/Space on a stack only opens it; merge needs the button or Enter after a deliberate choice — `test_browser.Keyboard`
- [x] Everyday flows still work end to end — `test_browser.Flows`
