# 06: Closed date; Closed column shows the last 7 days

**What to build:** Every ship and kill writes `- closed: <date>`. The Closed column only shows projects closed
within the window (default 7 days), including ones that now live in `maintained/` or `archive/`, with a "Show all"
toggle. See `../spec.md` "Closed date (06)" and `../PROPOSAL.md` §7.1.

**Blocked by:** None

**Status:** open

**Touches outside the repo:** No

- [ ] Ship and kill (file and folder projects) write `- closed: <today>`; ship still writes `shipped:`
- [ ] Listing returns `closed` per closed project, falling back to `shipped`, then the date in `**Killed YYYY-MM-DD:**` under `## Outcome`, else none
- [ ] Folders and dated files in `maintained/` and `archive/` closed within the window appear in the Closed column only; older ones don't appear at all
- [ ] Active/experiment projects closed more than 7 days ago (or with no date) are hidden from Closed by default
- [ ] Column header "Closed (last 7 days) N" and a "Show all (M)" toggle that lists every closed project
- [ ] Window from `closed_window_days` in config
- [ ] Undo of ship/kill removes `closed` again
- [ ] README; CHANGELOG; VERSION bump; tests pass
