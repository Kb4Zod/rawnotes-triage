# Reload button

Status: done (v2.15)

## Problem

The installed app (`install-webapp.sh`) opens in a full-screen app window with no browser toolbar, so there is
no visible way to reload the page. Ctrl-R works if you know it, and only on a keyboard (not on the phone). After
a server upgrade, or when something looks stale, Hugh wants one button that does what Ctrl-R does.

## What it does

A **Reload** button that reloads the whole page (`location.reload()`), the same as Ctrl-R, and on the way back
asks the server for fresh data everywhere it would otherwise reuse a cache:

- notes and reference index: always re-read on load (no change);
- Projects: git facts fetched fresh (`/api/projects?refresh=1`, skips the 20 s git cache);
- GitHub sign-in state (`/api/github?refresh=1`) and the activity graph (`/api/activity?refresh=1`).

The page is rebuilt from scratch, so a new page version from the server is picked up too (this is what the
version-mismatch banner's "Reload the page" does; that banner stays).

What survives, as with Ctrl-R: the current page (`#/inbox`, `#/projects`, …, kept in the URL) and the theme.
What doesn't: the selected note/project, filters, and decision or done-means drafts.

## Where

- **Desktop rail**, bottom row, between the dark-mode toggle and the version: `↻ Reload`.
- **Phone header** (`.mtop`), an icon button next to search, `aria-label="Reload the page"`.
- **Command palette**: the existing "Refresh everything" command becomes this reload (one way to do it, not two
  that differ). Its label keeps "Refresh everything" as a search word: "Reload — refresh everything".
- The Projects page's own **Refresh** button stays as it is (a quick in-place refresh of the board only).

No new single-key shortcut: Ctrl-R already is one, and the keys dialog lists it ("Ctrl R · reload the page and
fetch everything fresh"; a plain Ctrl-R still uses the server's short caches, the button does not).

## Unsaved work

- An unsaved done-means edit: the browser's existing leave-page prompt (`beforeunload`) asks, as it does for Ctrl-R.
- A decision form open with text in it, or a stack being decided (`dirty()` but no done-means edit): the button
  asks first with `confirm("Reload the page? What you typed in the open form will be lost.")`. Cancel does nothing.
- Nothing typed: reloads straight away.

## Out of scope

Restarting the server, clearing server caches for other clients, keeping selection across the reload.

## Tests

Browser tests (`test_browser.py`): the rail button and the phone-header button exist and are labelled; pressing
it reloads the page and the reloaded page asks for `refresh=1` on projects, github and activity; with a decision
form open and typed in, cancelling the confirm leaves the page and the text alone; the palette command reloads.
