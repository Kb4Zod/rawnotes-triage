# 05: --restart verifies what it kills

**Blocked by:** None

**Status:** done (branch fix/audit-reliability-accessibility)

**Covers findings:** 8

See ../spec.md.

- [x] HTTP error from another service is `foreign`, never an old triage; old triage recognised by its page title — `test_safety.Restart`
- [x] `stop_server` checks the page, the port owner and the process command line before SIGTERM; all signals mocked
