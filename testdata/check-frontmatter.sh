#!/usr/bin/env bash
# check-frontmatter — find RawNotes notes with a missing or invalid header.
#
# Runs as a Claude Code SessionStart hook (see ../settings.json); whatever it
# prints is handed to Claude, which asks you to fill the gaps before anything
# else. Silent when every note is fine. Also runnable by hand:
#   .claude/hooks/check-frontmatter.sh
#
# Required header (same as _template.md):
#   # <title>
#   - date: YYYY-MM-DD        (matches the filename date)
#   - status: open|cooking|promoted|dead
#   - type: question|research|issue
set -uo pipefail

cd "$(dirname "$0")/../.." || exit 0

problems=()

for f in *.md; do
  [[ -e $f ]] || continue
  case $f in README.md|SETUP.md|CLAUDE.md|AGENTS.md|TRIAGE.md|_template.md) continue ;; esac

  issues=()
  if [[ ! $f =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}-[a-z0-9-]+\.md$ ]]; then
    issues+=("filename is not YYYY-MM-DD-kebab-topic.md")
  fi

  title=$(sed -n 's/^# *//p' "$f" | head -1)
  date=$(sed -n 's/^- date: *\([0-9-]*\).*/\1/p' "$f" | head -1)
  status=$(sed -n 's/^- status: *\([a-z]*\).*/\1/p' "$f" | head -1)
  type=$(sed -n 's/^- type: *\([a-z]*\).*/\1/p' "$f" | head -1)

  [[ -z $title || $title == "<topic>" ]] && issues+=("title")
  if [[ -z $date ]]; then
    issues+=("date")
  elif [[ $f =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}- && $date != "${f:0:10}" ]]; then
    issues+=("date ($date) doesn't match filename (${f:0:10})")
  fi
  case $status in
    open|cooking|promoted|dead) ;;
    "") issues+=("status") ;;
    *)  issues+=("status '$status' not open|cooking|promoted|dead") ;;
  esac
  case $type in
    question|research|issue) ;;
    "") issues+=("type") ;;
    *)  issues+=("type '$type' not question|research|issue") ;;
  esac
  grep -q "^## What's the question / problem?" "$f" || issues+=("'What's the question / problem?' section")

  if (( ${#issues[@]} )); then
    joined=$(printf '%s, ' "${issues[@]}"); problems+=("- $f — missing/invalid: ${joined%, }")
  fi
done

(( ${#problems[@]} )) || exit 0

cat <<MSG
RawNotes header check: ${#problems[@]} note(s) don't have a complete header.
Don't interrupt the user with this up front. In a capture session, raise
it at the header checkpoint (after sharpening the problem, together with
any gaps in the current note); in triage, raise it at the start. Ask in one
short question, suggesting a value for each field guessed from the note
(title from the content, date from the filename or file mtime, status
usually "open", type from what it reads like) so they can just confirm.
Then fix the header to match _template.md (rename the file to
YYYY-MM-DD-kebab-topic.md if needed).

$(printf '%s\n' "${problems[@]}")
MSG
