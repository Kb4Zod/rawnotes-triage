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
# Everything is installed from, and runs, the checkout this script is in. Run from a linked git worktree (a feature
# branch), it installs the main checkout of the same repo instead, so the always-on service never runs a branch.
# ~/.local/bin/rawtriage-web becomes a link to bin/rawtriage-web in that checkout.
set -euo pipefail

NAME="RawNotes Triage"
URL="http://127.0.0.1:8765"
APP_URL="$URL/triage"   # what --app opens; the path gives the window its own class (see server.py APP_PATH)
HYPR="$HOME/.config/hypr/hyprland.lua"
BEGIN="-- >>> rawnotes-triage (install-webapp.sh) >>>"
END="-- <<< rawnotes-triage <<<"
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
REPO=$HERE
if common=$(git -C "$HERE" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) \
   && gitdir=$(git -C "$HERE" rev-parse --path-format=absolute --git-dir 2>/dev/null) && [[ $common != "$gitdir" ]]; then
  REPO=$(dirname "$common")   # a linked worktree: its common dir is <main checkout>/.git
fi
UNIT="rawtriage-web.service"
UNIT_DIR="$HOME/.config/systemd/user"
BIN="$HOME/.local/bin/rawtriage-web"
WRAPPER="$REPO/bin/rawtriage-web"

fail() { echo "install-webapp: $*" >&2; exit 1; }

warn() { echo "install-webapp: warning: $*" >&2; }

# The window rule lives between two marker lines. Touch it only when each marker is there exactly once (or both are
# absent); anything else means the block was edited by hand, and is left for you to fix.
hypr_markers() {  # prints: none | ok | damaged
  [[ -f $HYPR ]] || { echo none; return; }
  local b e; b=$(grep -nxF -- "$BEGIN" "$HYPR" | cut -d: -f1 || true); e=$(grep -nxF -- "$END" "$HYPR" | cut -d: -f1 || true)
  if [[ -z $b && -z $e ]]; then echo none
  elif [[ $b =~ ^[0-9]+$ && $e =~ ^[0-9]+$ ]] && (( b < e )); then echo ok   # exactly one of each, in order
  else echo damaged; fi
}
# What is at $BIN: ours (a link to some checkout's bin/rawtriage-web: replaced or removed freely), the hand-made one-line
# wrapper the README asked for before 2.12 (backed up, then replaced), nothing, or someone else's (never touched).
bin_kind() {  # prints: none | link | handmade | other
  if [[ -L $BIN ]]; then [[ $(readlink "$BIN") == */bin/rawtriage-web ]] && echo link || echo other
  elif [[ ! -e $BIN ]]; then echo none
  elif [[ -f $BIN ]] && grep -q 'rawnotes-triage/server.py' "$BIN" && (( $(wc -l <"$BIN") <= 5 )); then echo handmade
  else echo other; fi
}

hypr_up() { command -v hyprctl &>/dev/null && hyprctl version &>/dev/null; }
hypr_errors() { local out; out=$(hyprctl configerrors 2>/dev/null) || return 0; printf '%s\n' "$out" | sed '/^[[:space:]]*$/d' | sort -u; }

remove_hypr_rule() {  # remove_hypr_rule [--no-backup]
  case $(hypr_markers) in
    none) return 0 ;;
    damaged) warn "the rawnotes-triage block in $HYPR has a missing, repeated or out-of-order marker line; remove it by hand"; return 0 ;;
  esac
  [[ ${1:-} == --no-backup ]] || cp "$HYPR" "$HYPR.bak.rawnotes-triage"
  sed -i "\|^$BEGIN\$|,\|^$END\$|d" "$HYPR"
  hypr_up && hyprctl reload &>/dev/null || true
}

add_hypr_rule() {
  [[ -f $HYPR ]] || { warn "no $HYPR; the window won't open full screen"; return 0; }
  case $(hypr_markers) in
    ok) echo "Hyprland rule: already there"; return 0 ;;
    damaged) warn "the rawnotes-triage block in $HYPR has a missing, repeated or out-of-order marker line; fix it by hand, then rerun"; return 0 ;;
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
  local new; new=$(comm -13 <(printf '%s\n' "$before") <(hypr_errors) | sed '/^$/d')
  if [[ -n $new ]]; then
    remove_hypr_rule --no-backup   # keep the backup from before the rule went in
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
    case $(bin_kind) in
      link) rm -f "$BIN"; echo "Removed the launcher entry, the service, the Hyprland rule and $BIN." ;;
      none) echo "Removed the launcher entry, the service and the Hyprland rule." ;;
      *) echo "Removed the launcher entry, the service and the Hyprland rule. $BIN was not made by this script, so it was left alone." ;;
    esac
    exit 0 ;;
  ""|--no-service) ;;
  *) echo "usage: $0 [--no-service | --remove]" >&2; exit 2 ;;
esac

# Check everything before changing anything.
command -v omarchy-webapp-install >/dev/null || fail "omarchy-webapp-install not found (this needs Omarchy)"
[[ -f $REPO/server.py ]] || fail "$REPO/server.py not found"
grep -q -- 'APP_PATH = "/triage"' "$REPO/server.py" || fail "$REPO is older than this installer (2.08); git pull there first"
[[ -x $WRAPPER ]] || fail "$WRAPPER not found (the checkout is older than 2.12); git pull there first"
[[ -f $REPO/static/icons/icon-256.png && -f $REPO/systemd/$UNIT ]] || fail "icon or unit file missing in $REPO"
grep -qF '@REPO@' "$REPO/systemd/$UNIT" || fail "$REPO/systemd/$UNIT is older than this installer (2.12); git pull there first"
[[ $REPO != *[\"\\\$$'\n']* ]] || fail "the checkout's path ($REPO) has a quote, backslash, \$ or newline in it, which the service file can't hold; move it"
[[ $(bin_kind) != other ]] || fail "$BIN exists and isn't the rawnotes-triage wrapper; move it away, then rerun"
if [[ ${1:-} != --no-service ]]; then
  systemctl --user show-environment &>/dev/null || fail "no systemd user session here; run this from your desktop"
  # A triage server started by hand holds the port; the service would just keep retrying behind it.
  if ver=$(curl -fsS --max-time 2 "$URL/api/version" 2>/dev/null) && ! systemctl --user -q is-active "$UNIT"; then
    pid=$(sed -n 's/.*"pid": *\([0-9]*\).*/\1/p' <<<"$ver")
    fail "a triage server started by hand is running (pid ${pid:-?}); stop it first (Ctrl-C in its terminal, or kill ${pid:-<pid>}), then rerun"
  fi
fi

mkdir -p "$(dirname "$BIN")"
if [[ $(bin_kind) == handmade ]]; then cp -p "$BIN" "$BIN.bak.rawnotes-triage"; echo "Old wrapper: backed up to $BIN.bak.rawnotes-triage"; fi
ln -sfn "$WRAPPER" "$BIN"
echo "Wrapper: $BIN -> $WRAPPER"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) warn "$HOME/.local/bin is not on your PATH, so type the full path to run rawtriage-web by hand (the launcher doesn't need it)" ;; esac

omarchy-webapp-install "$NAME" "$APP_URL" "$REPO/static/icons/icon-256.png" "$BIN --app"
echo "Launcher entry: ~/.local/share/applications/$NAME.desktop"

if [[ ${1:-} != --no-service ]]; then
  mkdir -p "$UNIT_DIR"
  # @REPO@ -> the checkout; % is doubled for systemd, and | & \ are escaped for sed
  repo_sub=$(printf '%s' "${REPO//%/%%}" | sed 's/[|&\\]/\\&/g')
  sed "s|@REPO@|$repo_sub|g" "$REPO/systemd/$UNIT" >"$UNIT_DIR/$UNIT"
  echo "Service file: $UNIT_DIR/$UNIT runs $REPO/server.py"
  systemctl --user daemon-reload
  systemctl --user enable "$UNIT"
  systemctl --user restart "$UNIT"   # restart, not start: picks up a new version if it was already running
  for _ in $(seq 20); do curl -fsS --max-time 1 "$URL/api/version" &>/dev/null && break; sleep 0.25; done
  systemctl --user -q is-active "$UNIT" || fail "$UNIT didn't stay up; see: journalctl --user -u $UNIT"
  echo "Service: $UNIT running (systemctl --user status $UNIT)"
fi
add_hypr_rule   # last: a problem here is only a warning, and everything else is already in place
echo "Open it with SUPER + SPACE, type: $NAME"
