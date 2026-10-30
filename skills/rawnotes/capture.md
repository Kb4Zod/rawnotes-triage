# Capture a raw note

Get it out of the user's head fast. The note may be messy; capture beats polish.

1. **Start the note.** If a note file was named in the prompt, work in that file. If not, ask what is on their mind,
   pick a short kebab-case topic, and run `rawtriage new <topic> "<their first words>"`. It creates
   `YYYY-MM-DD-<topic>.md` from the folder's `_template.md` (status: open), prints its path, and refuses a name that
   already exists. Do not create the file by hand.
2. **Interview briefly, don't interrogate.** One or two questions to sharpen "What's the question / problem?", then
   start writing.
3. **Header checkpoint.** Once the problem is sharpened, and before answering or researching, run `rawtriage check`
   and look at this note and any others it lists. A complete header has: title, date (matching the filename), status,
   type, and a filled-in "What's the question / problem?". Fill in what you can work out yourself (pick `type` on
   purpose, don't leave the default). If anything is still missing or unclear, pause and ask in one question with
   suggested values (for other notes: title from the content, date from the filename, status usually `open`, type from
   what it reads like). Fix the headers to match `_template.md`. Continue once it is complete.
4. **Write as you go.** Keep the note updated during the conversation: the user's thoughts in their words, plus
   anything you find. Don't wait until the end to write.
5. **Research on request.** If the user wants digging (web search, reading code, running commands), do it and put
   findings in `## Notes` with sources and links.
6. **Close out.** When the session winds down, fill in `## Outcome` and set the status:
   - `open` (still unanswered) or `cooking` (in progress): edit the header.
   - `promoted` or `dead`: do not edit the header. Offer to run `rawtriage promote ...` (ask destination, what done
     means, and a ship-by date unless it is an experiment) or `rawtriage kill <file> --reason "why"`, as in `triage.md`.
