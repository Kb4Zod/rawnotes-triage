# Reload button

Status: done (v2.16)

## Problem

The installed app (`install-webapp.sh`) opens in a full-screen app window with no browser toolbar, so there is
no visible way to reload the page. Ctrl-R works if you know it, and only on a keyboard (not on the phone). After
a server upgrade, or when something looks stale, Hugh wants one button that does what Ctrl-R does.

## What it does

A **Reload** button that reloads the whole page (`location.reload()`), the same as Ctrl-R, and on the way back
asks the server for fresh data everywhere it would otherwise reuse a cache:

- notes and reference index: always re-read on load (no change);
- Projects: git facts fetched fresh (`/api/projects?refresh=1`, skips the 20 s git cache) when the reload lands on
  Projects (anywhere else, Projects loads later, past the cache's useful life anyway);
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

- Anything typed and not saved: a decision draft (the form of a note still in the Inbox that now differs
  from how it opened, `S.forms`), a stack's batch form changed the same way, or an unsaved done-means edit. The button asks once, itself:
  `confirm("Reload the page? What you typed and haven't saved will be lost.")`. Cancel does nothing. OK reloads
  without the browser's leave-page prompt on top (so there is no second prompt, and no way to cancel after the
  fresh flag is set).
- Nothing typed (including a form just opened, text typed and then deleted again, a stack's prefilled form, or a stale draft of a note already
  decided): reloads straight away.
- Plain Ctrl-R keeps the existing behaviour (leave-page prompt for an unsaved done-means edit only).

## Out of scope

Restarting the server, clearing server caches for other clients, keeping selection across the reload.

## Tests

Browser tests (`test_browser.py`): the rail button and the phone-header button exist and are labelled; pressing
it reloads the page and the reloaded page asks for `refresh=1` on projects, github and activity; with a decision
form open and typed in, cancelling the confirm leaves the page and the text alone; the palette command reloads.
