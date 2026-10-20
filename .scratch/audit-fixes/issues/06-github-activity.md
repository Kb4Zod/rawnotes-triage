# 06: Paginate, flag truncation, honest labels

**Blocked by:** None

**Status:** done (branch fix/audit-reliability-accessibility)

**Covers findings:** 14

See ../spec.md.

- [x] Repo pages followed (cap 10×100) and stop early once repos are older than a year; history cap reports truncated repo names; `notice` + `truncated` in the payload — `test_runtime.GithubActivity`
- [x] Labelled as commits by commit date (page, rail, README); single-flight fetch with no lock held over the network
- [x] All tests mock `gh_graphql`/`_gh`; browser tests never reach GitHub
