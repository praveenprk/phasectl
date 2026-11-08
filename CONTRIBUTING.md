# Contributing to phasectl

## Develop

```sh
git clone https://github.com/praveenprk/phasectl
cd phasectl
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

`phasectl` is now on your PATH from the local checkout; edits take effect
immediately.

## Tests

Before opening a PR, run the pytest suite and the end-to-end checks:

```sh
python -m pytest        # tests/ (tests/field is excluded; see scripts/field-test.sh)
phasectl check          # every subsystem: config, auth, db, phases, index, indexer, git
phasectl loose          # smoke-tests the offline git walker
```

## Governance

phasectl follows [eng-principles](https://github.com/praveenprk/eng-principles)
(DECISION-001). Registers: [docs/DECISIONS.md](docs/DECISIONS.md),
[docs/VIOLATIONS.md](docs/VIOLATIONS.md),
[docs/conformance/phasectl-ICS.md](docs/conformance/phasectl-ICS.md).

Install the hooks once per clone:

```sh
scripts/hooks/install   # sets core.hooksPath; scripts/hooks/test runs the hook suite
```

- Branch: `type/ANCHOR-description`, e.g. `fix/V-ENG-004-01-graph-store-errors`.
- Commit: `type(scope): description — ANCHOR`, e.g.
  `fix(graph): report graph store failures on close — V-ENG-004-01`.
- Every anchor must exist in a register. No co-author lines.
- `main` is protected: open a PR. Only the maintainer merges.

## Add a phase

Drop a TOML file at `~/.config/phasectl/phases/<name>.toml` with these keys:

```toml
[phase]
name         = "review"
temperature  = 0.2
token_budget = 8000
tools_allowed = []

system_prompt = """
You are in review mode: …
"""
```

That's it — `phasectl start --phase review` picks it up. User overrides also
shadow the six bundled phases of the same name.

## Add a provider

Implement the `LLMProvider` ABC in `src/phasectl/provider.py`:

```python
class MyProvider(LLMProvider):
    def __init__(self, config: dict): ...
    def chat(self, system, messages, tools, temperature, max_tokens):
        # return (text: str, tool_calls: list[dict])
        ...
    def compress(self, system, content, max_tokens) -> str: ...
```

Then wire the backend name into `get_provider()`. The common message shape and
tool-call format are documented at the top of `provider.py`.

## Style

- No new dependencies without a strong reason.
- No comments explaining what code does — only *why* it does it.
- Commit messages follow the format under Governance; imperative mood.
- Every user-facing string must be provider-agnostic (say "the model", not a
  brand name).

## Release

Tag `vX.Y.Z` on `main`; the tag is the release.
