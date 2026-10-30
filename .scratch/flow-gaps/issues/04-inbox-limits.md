# 04: Inbox limits, keep counter, forced decision

**What to build:** Stale for open (14 d) and cooking (7 d since last Keep); a 30-day Inbox limit and a 3-Keep limit
after which Keep is refused everywhere; Keep and Restore record dates and a counter; the page and the weekly nudge
show the same numbers. See `../spec.md` "Inbox limits (04)" and `../PROPOSAL.md` §3.

**Blocked by:** None for the server and page; the nudge criterion needs 02

**Status:** open

**Touches outside the repo:** Yes. `~/.local/bin/rawtriage-nudge` (use `rawtriage -l --summary`) and the
description line of `~/.config/systemd/user/rawtriage-nudge.timer` ("Fridays" vs `Sat 09:00`). RawNotes
`README.md` rule 5 text.

- [ ] Keep writes `- kept: <today>` and `- keeps: N+1`; Restore writes `- restored: <today>`
- [ ] `/api/notes` rows carry `stale`, `expired`, `expires_in`, `keeps`
- [ ] Open note 15 days old (never kept) is stale; kept 3 days ago is not
- [ ] Cooking note kept 8 days ago is stale; kept 6 days ago is not; a cooking note never kept uses its capture date
- [ ] Note 31 days old → expired; Keep via `/api/action` returns 400 "…30-day limit: promote, file, merge or kill it" and changes nothing
- [ ] Note with `keeps: 3` → expired the same way
- [ ] Restored note's clock starts at `restored`
- [ ] Batch Keep with one expired note refuses the whole batch and names it
- [ ] Promote, Reference, Merge and Kill still work on expired notes
- [ ] Thresholds come from config with the defaults above
- [ ] Page: Stale chip counts open+cooking; red Expired chip; row badges `kept N×` and `expires in Nd`; the Keep option is replaced by the message when expired; Pulse shows the expired count
- [ ] `rawtriage -l --summary` prints `N open, N cooking (N stale, N expired)`; `rawtriage-nudge` uses it (after 02)
- [ ] README Inbox section and rule text updated; CHANGELOG; VERSION bump; tests pass
