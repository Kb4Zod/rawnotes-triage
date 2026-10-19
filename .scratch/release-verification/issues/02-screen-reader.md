# Verify screen-reader and keyboard workflows

Status: open
Type: verification
Priority: medium

## Problem
Automated axe checks cannot verify that announcements, reading order and interaction make sense to a screen-reader user.

## Acceptance criteria
- [ ] Record screen reader, OS and browser versions; run at least one real supported combination.
- [ ] Navigate landmarks/headings, search, choose a note and read its entire question without a mouse.
- [ ] Verify stack checkbox names/states, form labels, validation errors and success/Undo announcements.
- [ ] Verify palette/help dialog entry, result selection, focus containment, Escape and returned focus.
- [ ] Opening a stack with Enter/Space never submits a decision; explicit decisions and Undo remain operable.
- [ ] Exercise project definition editing/review and document any confusing or missing announcements.
- [ ] Attach results and create individual defect tickets for failures; note untested combinations.

## Comments
2026-10-19: Automated accessibility sweeps passed; real assistive-technology verification remains outstanding.
