# Verify authenticated GitHub activity and repository browsing

Status: open
Type: verification
Priority: medium

## Problem
Automated tests stub GitHub and the preview disables credentials. Real permissions, rate limits and network responses
may differ from those fixtures.

## Acceptance criteria
- [ ] Use existing authorized gh credentials and approved public/private test repositories; never print tokens.
- [ ] Verify activity counts/commit-date labels, caching, refresh and visible partial/truncated-result notices.
- [ ] Browse README, directories, text files, commits, PRs and issues; verify private-repo access and owner restrictions.
- [ ] Verify graceful handling of unavailable auth, inaccessible/empty repos, network failure and rate-limit responses.
- [ ] Keep calls read-only and bounded; use mocks for rate-limit/pagination extremes rather than exhausting live quotas.
- [ ] Record gh version, account permissions, tested cases and sanitized evidence; file defects separately.

## Comments
2026-10-19: No live authenticated GitHub checks were performed during the disposable preview/load audit.
