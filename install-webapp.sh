#!/usr/bin/env bash
# install-webapp.sh — make the triage site a standalone app in the Omarchy launcher (SUPER + SPACE)
#
#   ./install-webapp.sh                launcher entry + always-on server (systemd user unit)
#   ./install-webapp.sh --no-service   launcher entry only; it starts the server itself when needed
#   ./install-webapp.sh --remove       take both away again
#
# The launcher runs `rawtriage-web --app`: if a server is already up (the service) it just opens the
# window, otherwise it starts one and opens the window. Either way you never get a blank page.
# Everything is installed from, and runs, the main checkout below, wherever you run this script from.
set -euo pipefail

NAME="RawNotes Triage"
URL="http://127.0.0.1:8765"
REPO="$HOME/Projects/active/rawnotes-triage"
UNIT="rawtriage-web.service"
UNIT_DIR="$HOME/.config/systemd/user"
BIN="$HOME/.local/bin/rawtriage-web"

case "${1:-}" in
  --remove)
    systemctl --user disable --now "$UNIT" 2>/dev/null || true
    rm -f "$UNIT_DIR/$UNIT"
    systemctl --user daemon-reload 2>/dev/null || true
    # not omarchy-webapp-remove: it only lists entries that run omarchy-launch-webapp, and ours runs rawtriage-web
    rm -f "$HOME/.local/share/applications/$NAME.desktop" "$HOME/.local/share/icons/hicolor/256x256/apps/rawnotes-triage.png"
    gtk-update-icon-cache "$HOME/.local/share/icons/hicolor" &>/dev/null || true
    echo "Removed the launcher entry and the service."; exit 0 ;;
  ""|--no-service) ;;
  *) echo "usage: $0 [--no-service | --remove]" >&2; exit 2 ;;
esac

# Check everything before changing anything.
fail() { echo "install-webapp: $*" >&2; exit 1; }
command -v omarchy-webapp-install >/dev/null || fail "omarchy-webapp-install not found (this needs Omarchy)"
[[ -x $BIN ]] || fail "$BIN is missing; see README 'Run it'"
[[ -f $REPO/server.py ]] || fail "$REPO/server.py not found"
grep -q -- '"--service"' "$REPO/server.py" || fail "$REPO is older than 2.08 (no --app/--service); git pull there first"
[[ -f $REPO/static/icons/icon-256.png && -f $REPO/systemd/$UNIT ]] || fail "icon or unit file missing in $REPO"
if [[ ${1:-} != --no-service ]]; then
  systemctl --user show-environment &>/dev/null || fail "no systemd user session here; run this from your desktop"
  # A triage server started by hand holds the port; the service would just keep retrying behind it.
  if ver=$(curl -fsS --max-time 2 "$URL/api/version" 2>/dev/null) && ! systemctl --user -q is-active "$UNIT"; then
    pid=$(sed -n 's/.*"pid": *\([0-9]*\).*/\1/p' <<<"$ver")
    fail "a triage server started by hand is running (pid ${pid:-?}); stop it first (Ctrl-C in its terminal, or kill ${pid:-<pid>}), then rerun"
  fi
fi

omarchy-webapp-install "$NAME" "$URL" "$REPO/static/icons/icon-256.png" "$BIN --app"
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
echo "Open it with SUPER + SPACE, type: $NAME"
