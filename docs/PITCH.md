# RawNotes Triage

I wanted a notes sytem that would help **Drive me to DONE.** I have discovered that I am good at coming up with Ideas and  bad at deciding. Ideas pile up in a folder, and each one is a small promise I never keep. RawNotes Triage is a small local web app that turns that pile into a short list of decisions.

Point it at a folder of Markdown notes. It shows an Inbox, oldest first, split into notes that have waited two weeks or more and notes from this fortnight. Pick a note and decide:

- **Promote** it to a project, with a written "done means" and a ship-by date.
- **Reference** it: file a clean copy for later.
- **Keep** it in the pot, with a dated next step.
- **Kill** it, with a reason.

Each decision moves real files, stamps the note's status, and lands in an archive. Nothing is overwritten, changes are all-or-nothing, and the last 20 can be undone with one key.

Promoted notes show up on a Projects board sorted by deadline: Overdue, Due this week, Later, Experiments, Closed. Each card shows a countdown, the definition of done, and recent commits and pull requests (if you use git and the GitHub CLI), with one-click Mark shipped, +7 days or Kill. A Pulse page tracks your commits and how fast you clear the Inbox.

The goal: every promoted note is shipped or killed within 3 to 7 days.

It is plain Python 3 (no dependencies) plus one HTML page, runs on your own machine, works offline, is keyboard-driven, and has a phone layout(in progress). There is no login, so keep it on localhost or a private network.

Written and tested on Linux. The core should run anywhere Python does, but macOS and Windows are untested.
