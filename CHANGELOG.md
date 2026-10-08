# Changelog

The version shows in the page heading ("RawNotes Triage version X.XX") and
in the startup line. Bump `VERSION` in `server.py` and add an entry here
with every change. Versions 1.00-1.03 were numbered after the fact from git history.

## 1.06 — 2026-10-07
- `--host` flag (default `127.0.0.1`, unchanged). `--host 0.0.0.0` listens on every
  interface so a phone on home Wi-Fi or the tailnet can open the page. No login,
  so only on trusted networks; the startup line says so.

## 1.05 — 2026-10-06
- Launcher checks for a server already on the port. If its version differs,
  it warns in the terminal and with a desktop notification, then opens the running one.
  `--restart` stops the old server and starts the new code. New `/api/version` endpoint.
- Browser opens only after the server is listening. A port taken by another program gives a clear error.

## 1.04 — 2026-10-06
- Version number shown in the page heading, browser tab title, and startup line.

## 1.03 — 2026-10-02
- Durable now copies to `../reference/<slug>.md` (was `../maintained/`) and adds
  YAML frontmatter so Obsidian shows Properties.

## 1.02 — 2026-09-25
- Copy note path: row Copy button, detail line, `y` key.

## 1.01 — 2026-09-17
- Active Projects: click-to-copy project path, per-row copy buttons, `y` key.

## 1.00 — 2026-09-17
- First version: triage site with Promote / Durable / Archive / Keep, the in-flight strip, and the Active Projects tab.
