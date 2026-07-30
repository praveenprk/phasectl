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
    try:
        gist = compress(payload, config, system_prompt=SEED_SYSTEM_PROMPT).strip()
    except Exception:
        return ""
    return gist


def build_seed_block(
    paths: list[str],
    config: dict,
    diag: Callable[[str], None] = _diag_noop,
) -> str:
    if not paths:
        return ""

    sections: list[str] = []
    used = 0
    skipped = 0
    for p in paths:
        if not os.path.isfile(p):
            diag(f"[seed] missing: {p}")
            skipped += 1
            continue
        ext = os.path.splitext(p)[1].lower()
        if ext not in SUPPORTED_EXTS:
            diag(f"[seed] unsupported ext {ext}: {p} (supported: {sorted(SUPPORTED_EXTS)})")
            skipped += 1
            continue

        text, truncated = load_seed(p)
        if not text:
            diag(f"[seed] empty or unreadable: {p}")
            skipped += 1
            continue

        gist = summarize_seed(p, text, truncated, config)
        if not gist:
            diag(f"[seed] compression failed: {p}")
            skipped += 1
            continue

        header = f"## {os.path.basename(p)}"
        if truncated:
            header += "  (truncated)"
        sections.append(f"{header}\n{gist}")
        used += 1

    if not sections:
        if paths:
            diag(f"[seed] no seed files usable ({skipped} skipped)")
        return ""

    diag(f"seeded: {used} file{'s' if used != 1 else ''}"
         + (f" ({skipped} skipped)" if skipped else ""))
    return "# Seeded context\n\n" + "\n\n".join(sections)
