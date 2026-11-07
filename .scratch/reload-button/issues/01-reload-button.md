# 01: Reload button that does a fresh Ctrl-R

**What to build:** See `.scratch/reload-button/spec.md`. One `reloadAll()` in `index.html` used by a rail button,
a phone-header button and the palette's "Refresh everything" command; a one-shot `sessionStorage` flag makes the
reloaded page fetch projects, GitHub status and activity with `refresh=1`.

**Blocked by:** None

**Status:** done (v2.15)

- [ ] Rail bottom row has a Reload button; phone header has a labelled reload icon button
- [ ] Pressing it reloads the page; the new page requests `/api/projects?refresh=1` (when on or opening Projects), `/api/github?refresh=1`, `/api/activity?refresh=1`
- [ ] The flag is used once: a later Ctrl-R behaves as before
- [ ] Typed-in decision form: asks first; Cancel changes nothing
- [ ] Palette "Refresh everything" runs the same reload
- [ ] Keys dialog mentions Ctrl R
- [ ] VERSION 2.15, CHANGELOG entry, USER-GUIDE mention
- [ ] Browser tests; `python3 -m unittest` passes
