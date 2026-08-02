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

There is no pytest suite yet — the tool's surface is exercised end-to-end
by `phasectl check` against a real config. Before opening a PR, at
minimum run:

```sh
phasectl check          # every subsystem: config, auth, db, phases, index, mcp, git
phasectl loose          # smoke-tests the offline git walker
```

Contributions that add a `tests/` directory with pytest cases for pure
functions (config, resume-block building, loose walker) are welcome.

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
- Terse commit messages; imperative mood.
- Every user-facing string must be provider-agnostic (say "the model", not a
  brand name).

## Release

Tag `vX.Y.Z` on `main`; the tag is the release.
