# RawNotes Triage

I wanted a notes system that would help **Drive me to DONE.** I have discovered that I am good at coming up with ideas and bad at deciding. Ideas pile up in a folder, and each one is a small promise I never keep. RawNotes Triage is a local web app that turns that pile into decisions and tracks the work afterward.

Point it at a folder of Markdown notes. On a fresh setup, Welcome asks where they should live and remembers the choice. The Inbox shows the oldest notes first. Pick a note and decide:

- **Promote** it into a project folder with a written “done means,” a local git repo and a ship-by date. Experiments can stay open-ended.
- **Reference** it: file a copy for later, optionally adding it to a reference index in the same step.
- **Keep** it with a next step, within a limit: by default, three Keeps or 30 days in the Inbox.
- **Kill** it and move the capture to the Archive.

Decisions update real files. The app checks changes and attempts rollback on failure; the newest 20 tracked changes can be undone while the server runs, provided their files have not changed since. If the notes folder is in git, touched notes are committed automatically with your normal hooks. The terminal command and agent skill use the same server rules and undo list.

A Projects board tracks deadlines, done means, commits and pull requests. Extensions need a reason and are limited to two by default. When a round ships, choose another round, move the project to Maintained, or archive it; killing moves it to an abandoned archive folder. Before a move, the app checks what points at the folder and reports blockers and follow-up tasks. Active and template projects must have their committed work pushed to GitHub before closing into Maintained, or Archive when they have work beyond the starter commit. A localhost action can create a private GitHub repo and make the first push.

The goal is to ship or kill promoted work within **3–7 days**. Pulse shows Inbox flow and GitHub commits so you can see whether ideas are turning into finished work.

It is Python 3 with no third-party Python dependencies and one HTML page. It runs locally, supports keyboard and phone use, and works offline for core triage. GitHub features need `gh` and a connection. Folder moves and other machine-specific actions require localhost. There is no app login, so keep it on localhost or a trusted network.

Written and tested on Linux; macOS and Windows are untested. Omarchy users can install a desktop launcher and an always-on user service.

Start with [First-run Setup](SETUP.md), then the [User Guide](USER-GUIDE.md).
