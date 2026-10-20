# Filesystem hardening

Status: open

Close the remaining race between path validation and filesystem mutation. Current checks reject links present before
an operation, including Undo; they do not prevent another process replacing a directory immediately after validation.
Implement on a separate branch after the audit repairs are merged. See issues/01-anchor-writes-to-directory-handles.md.
