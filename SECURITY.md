# Security policy

## Supported versions

Only the latest `main` is supported. Releases (v0.1.0 and later) are not
patched separately; a fix lands on `main` and in the next release.

## Reporting a vulnerability

Please report privately. Do not open a public issue.

Use GitHub's private vulnerability reporting:
**[Report a vulnerability](https://github.com/praveenprk/phasectl/security/advisories/new)**
(the repository's Security tab → "Report a vulnerability").

If you cannot use GitHub, email the maintainer at
**justpraveenhere@protonmail.com**, with "phasectl security" in the subject.

Include:

- what is affected: the CLI, its stored state, the indexer call, a provider
  backend, or a workflow;
- what an attacker can do, and the steps or input that show it;
- any fix you suggest.

You will get an acknowledgement within 7 days, in the advisory thread or by
email. Once a fix is merged, the advisory is published with credit to you,
unless you ask not to be named.

## In scope

- **Credentials.** API keys set with `phasectl auth set` or read from the
  environment: a way to leak them to logs, stored sessions, another
  provider, or another user.
- **Local state.** Session and graph files phasectl writes: a way for
  another local user or a crafted repository to read or tamper with them.
- **The indexer call.** phasectl runs the configured indexer binary with
  arguments: argument or command injection, or running a binary other than
  the one configured.
- **The user's codebase.** phasectl never writes to it (DECISION-002); any
  path by which it does is a vulnerability.
- **The workflows** in .github/workflows: a way to run code with the
  workflow token, or to get secrets.

## Not a vulnerability

- Context phasectl sends to the provider the user configured. That is its
  purpose; choose a local provider (Ollama) to keep context on the machine.
- A model's answers being wrong. Open an ordinary issue.
- Vulnerabilities in a provider's service or in a third-party indexer.
  Report them to that project.
