# Windows support: what broke on a fresh Windows clone

Status: implemented in 2.13 (branch fix/windows-support)

## How it was found

A fresh clone on Windows 11 (Python 3.13.5, Git Bash) started with `python server.py` and served the Welcome dialog,
so it looked fine. The stdlib test suite run on that machine showed what doesn't work: 40 failures and errors in six
of the eight test files before the fix.

## Findings

App bugs (a user hits these):

1. **`python3` is the Microsoft Store stub** on Windows. The README and `bin/rawtriage-web` (bash) say `python3`.
   → `bin\rawtriage-web.cmd` (py launcher, else `python`) and a README Windows section.
2. **Project paths came back with `\`.** `inflight()` and promotions used `os.path.relpath`, so the board got
   `active\demo\NOTES.md`; the page sends that back, but every check compares against `/`-joined names, and the
   promotion's Outcome line / log recorded `\`. Effect: ship / extend / kill / open of a promoted file failed with
   "unknown project file"; Pulse misread a reference filing as a promotion. → `api_rel()` (always `/`); `fate_of`
   also accepts old `\` lines.
3. **Junctions bypassed the link guard.** `os.path.islink` is False for a junction, which needs no privilege to
   make. `ensure_inside` let writes go through a junctioned folder pointing outside the roots. Confirmed: with the
   junction-based tests, 15 fail without the fix. → `safefs.is_link()`.
4. **Open folder** ran `xdg-open`. → `os.startfile` on Windows.
5. **`--restart`** used `ss` and `/proc`. → `netstat -ano` + `Get-CimInstance Win32_Process` command line. A relative
   `server.py` can't be checked against its file (another process's cwd isn't readable), so it is accepted by name;
   the other two checks (the page / `/api/version` says rawnotes-triage, the reported pid owns the port) still apply.

Test-only problems: symlink creation needs privilege (WinError 1314); no Unix mode bits; `expanduser` reads
`USERPROFILE`, not `HOME`; `/a/b` isn't absolute; `snapshot()` keys used `\`; a stub file written without
`encoding=` came out cp1252.

## Not changed (documented instead)

- `--app`, `--service`, systemd, `install-webapp.sh`, `notify-send`: Linux/Omarchy only. `--app` already falls back
  to a browser tab; `notify` already ignores a missing `notify-send`.
- Config stays in `~/.config/rawnotes-triage/` (not `%APPDATA%`), so one rule holds everywhere and the README stays
  true. The token file's `0600` is a no-op on Windows; the user profile's ACL keeps it private.
- `test_browser.py` was not run on Windows (Chromium lookup in `cdp.py` not checked there).
- Path checks (`ensure_inside`, `safe_note`) compare case-sensitively; on Windows a differently-cased spelling of
  the same root would be refused, never allowed (fails safe), so it was left alone.
