# 07: Reader shows the entire question

**Blocked by:** 02

**Status:** done (branch fix/audit-reliability-accessibility)

**Covers findings:** 4

See ../spec.md.

- [x] `/api/note` returns `question` (whole section) and `body`; list row keeps ≤280 chars — `test_meta.Question`
- [x] Reader renders every paragraph/list — `test_browser.Structure.test_full_question_is_in_the_reader`
