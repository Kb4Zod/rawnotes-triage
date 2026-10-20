#!/usr/bin/env bash
# install-webapp.sh — make the triage site a standalone app in the Omarchy launcher (SUPER + SPACE)
#
#   ./install-webapp.sh                launcher entry + always-on server (systemd user unit)
#   ./install-webapp.sh --no-service   launcher entry only; it starts the server itself when needed
#   ./install-webapp.sh --remove       take both away again
#
# Both installs also add a Hyprland window rule (a marked block at the end of ~/.config/hypr/hyprland.lua)
# so the app window always opens full screen. SUPER + F leaves full screen, as for any window.
#
# The launcher runs `rawtriage-web --app`: if a server is already up (the service) it just opens the
# window, otherwise it starts one and opens the window. Either way you never get a blank page.
# Everything is installed from, and runs, the main checkout below, wherever you run this script from.
set -euo pipefail

NAME="RawNotes Triage"
URL="http://127.0.0.1:8765"
APP_URL="$URL/triage"   # what --app opens; the path gives the window its own class (see server.py APP_PATH)
HYPR="$HOME/.config/hypr/hyprland.lua"
BEGIN="-- >>> rawnotes-triage (install-webapp.sh) >>>"
END="-- <<< rawnotes-triage <<<"
REPO="$HOME/Projects/active/rawnotes-triage"
UNIT="rawtriage-web.service"
UNIT_DIR="$HOME/.config/systemd/user"
BIN="$HOME/.local/bin/rawtriage-web"

fail() { echo "install-webapp: $*" >&2; exit 1; }

warn() { echo "install-webapp: warning: $*" >&2; }

# The window rule lives between two marker lines. Touch it only when each marker is there exactly once (or both are
# absent); anything else means the block was edited by hand, and is left for you to fix.
hypr_markers() {  # prints: none | ok | damaged
  [[ -f $HYPR ]] || { echo none; return; }
  local b e; b=$(grep -cxF -- "$BEGIN" "$HYPR" || true); e=$(grep -cxF -- "$END" "$HYPR" || true)
  if [[ $b == 0 && $e == 0 ]]; then echo none; elif [[ $b == 1 && $e == 1 ]]; then echo ok; else echo damaged; fi
}
hypr_up() { command -v hyprctl &>/dev/null && hyprctl version &>/dev/null; }
hypr_errors() { local out; out=$(hyprctl configerrors 2>/dev/null) || return 0; printf '%s\n' "$out" | sed '/^[[:space:]]*$/d'; }

remove_hypr_rule() {
  case $(hypr_markers) in
    none) return 0 ;;
    damaged) warn "the rawnotes-triage block in $HYPR has a missing or repeated marker line; remove it by hand"; return 0 ;;
  esac
  cp "$HYPR" "$HYPR.bak.rawnotes-triage"
  sed -i "\|^$BEGIN\$|,\|^$END\$|d" "$HYPR"
  hypr_up && hyprctl reload &>/dev/null || true
}

add_hypr_rule() {
  [[ -f $HYPR ]] || { warn "no $HYPR; the window won't open full screen"; return 0; }
  case $(hypr_markers) in
    ok) echo "Hyprland rule: already there"; return 0 ;;
    damaged) warn "the rawnotes-triage block in $HYPR has a missing or repeated marker line; fix it by hand, then rerun"; return 0 ;;
  esac
  local before=""; hypr_up && before=$(hypr_errors)
  cp "$HYPR" "$HYPR.bak.rawnotes-triage"
  sed -i -e :a -e '/^\n*$/{$d;N;ba' -e '}' "$HYPR"   # drop trailing blank lines, so add/remove cycles don't pile them up
  printf '\n%s\n%s\n%s\n%s\n%s\n' "$BEGIN" \
    "-- The RawNotes Triage app window (rawtriage-web --app opens /triage) always opens full screen." \
    '-- fullscreen_state "2 0": full screen for Hyprland, not for Chromium (it undoes a plain fullscreen = true).' \
    'o.window([[^chrome-127\.0\.0\.1__triage-.*$]], { fullscreen_state = "2 0" })' \
    "$END" >>"$HYPR"
  if ! hypr_up; then echo "Hyprland rule: added; Hyprland isn't reachable from here, so it applies at next login"; return 0; fi
  hyprctl reload &>/dev/null || true
  local new; new=$(comm -13 <(sort <<<"$before") <(hypr_errors | sort) | sed '/^$/d')
  if [[ -n $new ]]; then
    remove_hypr_rule
    warn "Hyprland rejected the window rule, so it was taken out again (the app still works, just not full screen): $new"
    return 0
  fi
  echo "Hyprland rule: app window opens full screen ($HYPR; backup in $HYPR.bak.rawnotes-triage)"
}

case "${1:-}" in
  --remove)
    remove_hypr_rule
    systemctl --user disable --now "$UNIT" 2>/dev/null || true
    rm -f "$UNIT_DIR/$UNIT"
    systemctl --user daemon-reload 2>/dev/null || true
    # not omarchy-webapp-remove: it only lists entries that run omarchy-launch-webapp, and ours runs rawtriage-web
    rm -f "$HOME/.local/share/applications/$NAME.desktop" "$HOME/.local/share/icons/hicolor/256x256/apps/rawnotes-triage.png"
    gtk-update-icon-cache "$HOME/.local/share/icons/hicolor" &>/dev/null || true
    echo "Removed the launcher entry, the service and the Hyprland rule."; exit 0 ;;
  ""|--no-service) ;;
  *) echo "usage: $0 [--no-service | --remove]" >&2; exit 2 ;;
esac

# Check everything before changing anything.
command -v omarchy-webapp-install >/dev/null || fail "omarchy-webapp-install not found (this needs Omarchy)"
[[ -x $BIN ]] || fail "$BIN is missing; see README 'Run it'"
[[ -f $REPO/server.py ]] || fail "$REPO/server.py not found"
grep -q -- 'APP_PATH = "/triage"' "$REPO/server.py" || fail "$REPO is older than this installer (2.08); git pull there first"
[[ -f $REPO/static/icons/icon-256.png && -f $REPO/systemd/$UNIT ]] || fail "icon or unit file missing in $REPO"
if [[ ${1:-} != --no-service ]]; then
  systemctl --user show-environment &>/dev/null || fail "no systemd user session here; run this from your desktop"
  # A triage server started by hand holds the port; the service would just keep retrying behind it.
  if ver=$(curl -fsS --max-time 2 "$URL/api/version" 2>/dev/null) && ! systemctl --user -q is-active "$UNIT"; then
    pid=$(sed -n 's/.*"pid": *\([0-9]*\).*/\1/p' <<<"$ver")
    fail "a triage server started by hand is running (pid ${pid:-?}); stop it first (Ctrl-C in its terminal, or kill ${pid:-<pid>}), then rerun"
  fi
fi

omarchy-webapp-install "$NAME" "$APP_URL" "$REPO/static/icons/icon-256.png" "$BIN --app"
echo "Launcher entry: ~/.local/share/applications/$NAME.desktop"

if [[ ${1:-} != --no-service ]]; then
  mkdir -p "$UNIT_DIR"
  cp "$REPO/systemd/$UNIT" "$UNIT_DIR/$UNIT"
  systemctl --user daemon-reload
  systemctl --user enable "$UNIT"
  systemctl --user restart "$UNIT"   # restart, not start: picks up a new version if it was already running
  for _ in $(seq 20); do curl -fsS --max-time 1 "$URL/api/version" &>/dev/null && break; sleep 0.25; done
  systemctl --user -q is-active "$UNIT" || fail "$UNIT didn't stay up; see: journalctl --user -u $UNIT"
  echo "Service: $UNIT running (systemctl --user status $UNIT)"
fi
add_hypr_rule   # last: a problem here is only a warning, and everything else is already in place
echo "Open it with SUPER + SPACE, type: $NAME"
