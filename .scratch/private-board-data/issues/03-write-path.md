# Write path: board actions write the record, not the project

Status: done
Type: task
Blocked by: 01, 02
Spec: ../spec.md (D2, D6)

## What
Ship, kill, extend, Next round, close-out and the definition-of-done edit write the record. The definition of done
itself stays in `NOTES.md`. Projects that were never promoted get a record on Mark shipped / Kill and nothing is
written into the project.

## Acceptance criteria
- [ ] Every board action leaves the project tree byte-identical (except the definition of done, which lives in `NOTES.md`).
- [ ] Undo of each action restores the record (and any legacy `NOTES.md` it stripped).
- [ ] Mark shipped / Kill of a hand-made folder creates a record and no `NOTES.md`.
