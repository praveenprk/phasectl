# Changelog

All notable changes to phasectl are recorded here. The format follows
[Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/), and versions
follow [Semantic Versioning](https://semver.org/) with major version 0
(eng-principles ENG-STD-001 §3.4). Entries before 2026-11-26 are backfilled
from the merged pull requests and the v0.1.0 tag.

## [Unreleased]

### Added
- `start` carries prior decisions into a new session as a verbatim,
  CLI-built PRIOR_DECISIONS block labelled historical (#11).
- Deterministic field-test runner, `scripts/field-test.sh` (#10).
- RFC-001 with SPEC-001 (`close`), SPEC-002 (`start`) and SPEC-003 (`chat`),
  with tests for their invariants (#13, #14).
- The live git block names the HEAD subject and changed paths, and a history
  conflict line names stale branches against the live one (#15).
- Governance: decisions, violations and questions registers, conformance
  statement, commit hooks, AGENTS.md, SECURITY.md (#12, #16, #21, #22, #24).
- CI on every pull request: ruff lint and format, pytest on Python 3.11 and
  3.13, hook tests, shellcheck and shfmt (#17, #18).

### Changed
- `scripts/field-test.sh` needs the repository under test named with
  `--repo` or `PHASECTL_FIELD_REPO`; it has no default path (Q-002).
- Context is lossless by default for `close`, `start` and `chat`. A model
  rewrite runs only with `--summarize` or `--compress`, warns on stderr and
  sits next to the verbatim source (DECISION-006, #15).
- Resume context is grounded in deterministic git state (#7).
- The indexer is a plain binary on `PATH`, not an MCP server (#2).

### Removed
- `docs/internal/` (pre-RFC design drafts that were out of date) and the
  parked `docs/opencode-support.md` note.

### Fixed
- Session snapshots keep the original turns (#8) and decision references (#9).

## [0.1.0] - 2026-08-02

First public release.

### Added
- Phase-gated sessions: `start`, `chat`, `switch`, `close`, `status`, with
  phase prompts bundled as package data.
- Providers behind one interface: Anthropic and any OpenAI-compatible
  endpoint (OpenAI, Ollama, OpenRouter).
- `auth` with the system keychain on macOS and Linux and an environment
  variable hierarchy.
- `index`, `query`, `graph`, `check` and `loose`, with a NetworkX session graph
  persisted as JSON.
- `chat` reads stdin as context; progress goes to stderr so stdout stays clean
  for piping.
- README, man page (docs/phasectl.1) and `--help` for every command.

[Unreleased]: https://github.com/praveenprk/phasectl/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/praveenprk/phasectl/releases/tag/v0.1.0
