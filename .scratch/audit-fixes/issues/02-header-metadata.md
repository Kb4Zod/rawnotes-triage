# 02: Header-only metadata fields

**Blocked by:** None

**Status:** done (branch fix/audit-reliability-accessibility)

**Covers findings:** 2

See ../spec.md.

- [x] `# X\n- status:\n- type: research\n` + set status cooking → `- status: cooking` on its own line — `test_meta.Header.test_blank_status_*`
- [x] Template trailing comments kept; blank value with a comment filled in
- [x] Missing header gets one under the title; body/quoted/indented/fenced/merged-section metadata never read or edited — `test_body_metadata_is_never_read_or_edited`, `HeaderThroughTheApp.test_merged_note_*`
- [x] Real-world shape (subtitle paragraph before the header) still works — `test_header_after_a_subtitle_paragraph_still_counts`
