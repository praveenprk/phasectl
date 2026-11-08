import os
from typing import Callable

from .api import compress


SEED_SYSTEM_PROMPT = (
    "You extract the SIGNAL from a document for an engineer's working context. "
    "Preserve: concrete decisions, arguments made, references cited, open questions, "
    "next steps, specific numbers/names/paths. "
    "Drop: pleasantries, small talk, cuss words, tangents that led nowhere, "
    "restatements, filler. "
    "Output the gist as plain prose (or short bulleted structure if the source is "
    "list-shaped). No preamble like 'This document...'. Terse, dense, load-bearing."
)

SUPPORTED_EXTS = {".md", ".txt", ".json"}
MAX_BYTES = 200_000


def _diag_noop(msg: str) -> None:
    pass


def load_seed(path: str) -> tuple[str, bool]:
    """Return (text, truncated). Empty string if unreadable/unsupported/empty."""
    if not path or not os.path.isfile(path):
        return "", False
    ext = os.path.splitext(path)[1].lower()
    if ext not in SUPPORTED_EXTS:
        return "", False
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            raw = fh.read(MAX_BYTES)
    except OSError:
        return "", False
    truncated = size > MAX_BYTES
    try:
        text = raw.decode("utf-8", errors="replace").strip()
    except Exception:
        return "", False
    return text, truncated


def summarize_seed(path: str, text: str, truncated: bool, config: dict) -> str:
    if not text:
        return ""
    header = f"[source: {os.path.basename(path)}"
    if truncated:
        header += f"; TRUNCATED to first {MAX_BYTES // 1000}KB of larger file"
    header += "]"
    payload = f"{header}\n\n{text}"
    # Opt-in only (`start --summarize`); errors propagate to the caller.
    return compress(payload, config, system_prompt=SEED_SYSTEM_PROMPT).strip()


def load_seed_files(
    paths: list[str],
    diag: Callable[[str], None] = _diag_noop,
) -> list[tuple[str, str, str, bool]]:
    """Usable seed files as (path, basename, text, truncated). Unusable ones are reported and skipped."""
    loaded = []
    for p in paths or []:
        if not os.path.isfile(p):
            diag(f"[seed] missing: {p}")
            continue
        ext = os.path.splitext(p)[1].lower()
        if ext not in SUPPORTED_EXTS:
            diag(f"[seed] unsupported ext {ext}: {p} (supported: {sorted(SUPPORTED_EXTS)})")
            continue
        text, truncated = load_seed(p)
        if not text:
            diag(f"[seed] empty or unreadable: {p}")
            continue
        loaded.append((p, os.path.basename(p), text, truncated))
    return loaded


def seed_turn_text(basename: str, text: str, truncated: bool) -> str:
    """SPEC-002 `seed` turn: the file verbatim, with any size cut stated."""
    header = f"SEED {basename} (supplied, verbatim; not verified"
    if truncated:
        header += f"; TRUNCATED to first {MAX_BYTES // 1000}KB"
    return f"{header}):\n{text}"
