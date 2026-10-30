# 13: Reference updates the `reference/README.md` index

**What to build:** Filing a note as Reference can add `- [Title](slug.md): summary` to a chosen `###` section
under `## Index` in `reference/README.md`, in the same undoable step. An Unindexed list shows reference notes the
index doesn't link, with Add to index. See `../spec.md` "Reference index (13)" and `../PROPOSAL.md` §8.

**Blocked by:** None

**Status:** open

**Touches outside the repo:** Writes `~/Projects/reference/README.md` (inside the projects root, through Tx) when
used. The one-time backfill of the 21 unindexed notes is in 14.

- [ ] Pure `index_insert(text, section, line)`: adds the line at the end of that section's list; creates `### <section>` at the end of the index if new; touches nothing outside `## Index`…next `## `; returns None if there's no `## Index` or the `(slug.md)` link already exists
- [ ] `[`, `]` and newlines in title/summary are escaped or removed
- [ ] `/api/action` reference accepts optional `section` and `blurb`; with them, README and note are written in one Tx; `section:` is added to the note's frontmatter
- [ ] "Don't index" (no section) behaves as today
- [ ] No README or no `## Index` → note still filed, response warns, README untouched
- [ ] Undo removes the index line and the note; refuses if the README changed since
- [ ] Batch Reference all takes one section; summaries from each question
- [ ] `GET /api/reference/unindexed` lists `reference/*.md` (not README) with no `(<file>)` link in the index; `POST /api/reference/index {file, section, blurb}` adds one (undoable)
- [ ] Page: Reference form has Index section (existing `###` headings + New section… + Don't index) and One-line summary (prefilled); Archive page shows "N reference notes aren't in the index" with Add to index
- [ ] `rawtriage reference --unindexed` once 02 lands (not blocking)
- [ ] README Reference row (no longer "does not touch"); CHANGELOG; VERSION bump; tests pass
