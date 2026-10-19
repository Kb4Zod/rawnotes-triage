# Persistent undo

Status: open

Preserve the bounded undo history across server restarts while retaining conflict detection and path containment.
This is a follow-up enhancement; v2.07 intentionally keeps undo in memory. Implement on a separate branch after
the audit repairs are merged. See issues/01-persist-undo-history.md.
