# 01: Atomic, rolled-back, always-undoable mutations

**Blocked by:** None

**Status:** done (branch fix/audit-reliability-accessibility)

**Covers findings:** 1, 3 (server side)

See ../spec.md.

- [x] Every mutation runs in `mutation()` (write lock, `Tx`, rollback on failure, undo entry committed before logging) — `test_engine.Faults`
- [x] Logging failure (`server.log` raising OSError, unwritable log path) neither fails nor strands a change — `test_log_oserror_*`, `test_unwritable_log_file_*`
- [x] All sources validated first, including duplicates (and symlink aliases); merge/batch with a bad source change nothing — `test_merge_with_a_duplicate_source_*`, `test_merge_and_batch_with_missing_*`, `test_batch_with_one_note_already_in_the_archive_*`
- [x] Injected failure in act(kill)/promote/batch/merge/restore/project status/atomic write restores bytes exactly, leaves no undo entry and no temp file — `test_failed_*`, `test_batch_that_fails_*`, `test_merge_that_fails_*`, `test_restore_and_project_writes_*`
- [x] Rollback that cannot finish leaves an "unfinished" undo entry that Undo completes — `test_rollback_that_cannot_finish_*`
- [x] Undo stays conflict-aware; a half-failed undo keeps the entry whole (or only what still stands) — `test_undo_refuses_*`, `test_undo_that_fails_midway_*`, `test_undo_whose_redo_also_fails_*`
- [x] Atomic replacement keeps mode, CRLF and undecodable bytes — `test_write_keeps_mode_crlf_*`, `test_killed_crlf_note_*`
- [x] Done means goes through the same path: undoable, conflict-checked, no lost update against status edits — `test_done_means_*`, `test_every_mutation_endpoint_pushes_an_undo_step`
