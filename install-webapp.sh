#!/usr/bin/env bash
# install-webapp.sh — make the triage site a standalone app in the Omarchy launcher (SUPER + SPACE)
#
#   ./install-webapp.sh            launcher entry + always-on server (systemd user unit)
#   ./install-webapp.sh --no-service   launcher entry only; it starts the server itself when needed
#   ./install-webapp.sh --remove   take both away again
#
# The launcher runs `rawtriage-web --app`: if a server is already up (the service) it just opens the
# window, otherwise it starts one and opens the window. Either way you never get a blank page.
set -euo pipefail

NAME="RawNotes Triage"
URL="http://127.0.0.1:8765"
REPO="$HOME/Projects/active/rawnotes-triage"   # the service and launcher always run the main checkout
UNIT="rawtriage-web.service"
UNIT_DIR="$HOME/.config/systemd/user"
HERE="$(cd "$(dirname "$0")" && pwd)"

case "${1:-}" in
  --remove)
    systemctl --user disable --now "$UNIT" 2>/dev/null || true
    rm -f "$UNIT_DIR/$UNIT"; systemctl --user daemon-reload
    # not omarchy-webapp-remove: it only lists entries that run omarchy-launch-webapp, and ours runs rawtriage-web
    rm -f "$HOME/.local/share/applications/$NAME.desktop" "$HOME/.local/share/icons/hicolor/256x256/apps/rawnotes-triage.png"
    gtk-update-icon-cache "$HOME/.local/share/icons/hicolor" &>/dev/null || true
    echo "Removed the launcher entry and the service."; exit 0 ;;
  ""|--no-service) ;;
  *) echo "usage: $0 [--no-service | --remove]" >&2; exit 2 ;;
esac

command -v omarchy-webapp-install >/dev/null || { echo "omarchy-webapp-install not found (this needs Omarchy)" >&2; exit 1; }
[[ -x "$HOME/.local/bin/rawtriage-web" ]] || { echo "~/.local/bin/rawtriage-web is missing; see README 'Run it'" >&2; exit 1; }

omarchy-webapp-install "$NAME" "$URL" "$HERE/static/icons/icon-256.png" "$HOME/.local/bin/rawtriage-web --app"
echo "Launcher entry: ~/.local/share/applications/$NAME.desktop"

if [[ "${1:-}" != --no-service ]]; then
  [[ -f "$REPO/server.py" ]] || { echo "$REPO/server.py not found" >&2; exit 1; }
  mkdir -p "$UNIT_DIR"
  cp "$HERE/systemd/$UNIT" "$UNIT_DIR/$UNIT"
  systemctl --user daemon-reload
  systemctl --user enable --now "$UNIT"
  echo "Service: $UNIT enabled and started (systemctl --user status $UNIT)"
fi
echo "Open it with SUPER + SPACE, type: $NAME"
