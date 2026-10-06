## Agent skills

### Orchestration

Use the global `$orchestrate` skill for planning-first feature orchestration, worker dispatch, and verification. Its instructions are at `/home/kb4zod/.agents/skills/orchestrate/SKILL.md`. Invoke it explicitly in T3 Code with `$orchestrate`.

### Issue tracker

Issues and specs live as local markdown files under `.scratch/<feature-slug>/`. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context: one `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### Versioning

Every change to the app bumps `VERSION` in `server.py` by 0.01 (e.g. 1.04 → 1.05) and adds a dated entry to `CHANGELOG.md`. The version shows in the page heading.
