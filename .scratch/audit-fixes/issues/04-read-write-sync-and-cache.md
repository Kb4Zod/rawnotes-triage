# 04: Consistent reads under writes; bounded caches

**Blocked by:** 01

**Status:** done (branch fix/audit-reliability-accessibility)

**Covers findings:** 11, 13

See ../spec.md.

- [x] 16 clients, 160 GET /api/notes + 40 distinct kills: all 200 — `test_runtime.ReadsBesideWrites`; `loadcheck.py` 3 rounds × (200 requests) all 200, 40 archived each
- [x] Listing is one snapshot of our own changes; vanished files skipped — `test_a_listing_is_one_snapshot_*`, `test_a_note_edited_or_removed_by_hand_*`
- [x] Git inspection cached (TTL 20 s + repo fingerprint, `?refresh=1` bypass) on one bounded pool; stats doesn't build the board; stat-keyed note cache with racy-clean guard — `test_runtime.Caches`
