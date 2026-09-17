# 02: Copy from sidebar rows and with `y`

**What to build:** Each Active Projects sidebar row gets a small copy button that copies that project's full path without selecting the row, and pressing `y` copies the selected project's path. Both reuse the copy helper and `abs_path` from ticket 01. See `../spec.md`.

**Blocked by:** 01

**Status:** done (branch feat/copy-path-rows-and-key, a83b9fe)

- [ ] Every sidebar row has a copy button that copies that row's `abs_path` and shows "Copied" for about one second
- [ ] Clicking the button, or pressing Enter/Space while it is focused, does not select the row or change the detail pane
- [ ] The button is keyboard-focusable
- [ ] `y` on the projects tab copies the selected project's `abs_path`, with feedback on the detail path line
- [ ] `y` does nothing on Backlog/Done tabs or when no project is selected; `j`/`k` and existing shortcuts unchanged
- [ ] Project names with spaces or special characters copy exactly
- [ ] Projects-tab hint text mentions `y`
- [ ] `python3 -m unittest` still passes
