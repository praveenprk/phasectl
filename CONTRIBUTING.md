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

```sh
pytest
```

The suite is intentionally minimal — most of the value is exercised by
`phasectl check` against a real config.

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
