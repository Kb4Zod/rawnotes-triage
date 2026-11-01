# 05: "+7 days" needs a reason, max two; Kill needs a reason

**What to build:** Extending a ship-by date requires a written reason, is capped at two per project, and is recorded
in the project file. After two, only Ship and Kill are offered. Killing a project asks why. See `../spec.md`
"Ship clock (05)" and `../PROPOSAL.md` §5.

**Blocked by:** None

**Status:** open

**Touches outside the repo:** No

- [ ] `project/status` extend without `note`, or with fewer than 10 characters, returns 400 and changes nothing
- [ ] Extend with a reason moves `due` by `days` (1-7 only; 8+ refused) from max(today, due), sets `- extensions: N+1`, and adds `- pushed <old> → <new> (<today>): <reason>` under `## Plan` (creating the section if missing)
- [ ] Third extend returns 400 "Pushed twice already: ship it or kill it." and changes nothing
- [ ] Missing `extensions:` counts as 0
- [ ] Undo of an extend restores `due`, `extensions` and the Plan section exactly
- [ ] Project kill without a reason returns 400; with one, the reason is under `## Outcome`
- [ ] Page: `+7 days` opens a reason form; after two extensions the button is replaced by "No more extensions"; overdue + none left shows a **Decide** badge on the card; Kill asks for a reason
- [ ] The CLI (`rawtriage project extend --reason`) works once 02 lands (not blocking)
- [ ] README Projects section; CHANGELOG; VERSION bump; tests pass
