"""Progress reporter for user-facing operations.

Writes to stderr so stdout stays clean for piping. In a tty, each step
overwrites the previous line in place (git-style); when stderr is piped,
each step falls back to its own newline. Elapsed time is appended on
finalize when a step took at least 2 seconds.
"""

from __future__ import annotations

import sys
import time
from typing import IO


class Progress:
    def __init__(self, quiet: bool = False, stream: IO[str] | None = None):
        self.quiet = quiet
        self._stream = stream or sys.stderr
        self._tty = self._stream.isatty()
        self._pending: str | None = None
        self._pending_start: float = 0.0
        self._last_len: int = 0

    def step(self, msg: str) -> None:
        if self.quiet:
            return
        self._finalize_pending()
        self._pending = msg
        self._pending_start = time.monotonic()
        if self._tty:
            self._write_transient(msg)
        else:
            self._write_final(msg, elapsed=0.0)

    def event(self, msg: str) -> None:
        """A one-shot line that never carries elapsed time.

        Used for events that happen inside a longer step (e.g. tool
        calls during a chat round). In tty mode this finalizes any
        pending step, prints the event on its own line, and then
        re-emits the pending step so it stays visible on the bottom row.
        """
        if self.quiet:
            return
        if self._tty:
            resume = self._pending
            resume_start = self._pending_start
            self._finalize_pending()
            self._write_final(msg, elapsed=0.0)
            if resume is not None:
                self._pending = resume
                self._pending_start = resume_start
                self._write_transient(resume)
        else:
            self._write_final(msg, elapsed=0.0)

    def done(self) -> None:
        if self.quiet:
            return
        self._finalize_pending()

    def _finalize_pending(self) -> None:
        if self._pending is None:
            return
        elapsed = time.monotonic() - self._pending_start
        if self._tty:
            self._wipe()
            self._write_final(self._pending, elapsed=elapsed)
        # non-tty already wrote the line on step(); we skip elapsed there
        self._pending = None

    def _write_transient(self, msg: str) -> None:
        # tty-only path: overwrite prior transient line and leave cursor at end
        self._wipe()
        self._stream.write(msg)
        self._stream.flush()
        self._last_len = _visible_len(msg)

    def _write_final(self, msg: str, elapsed: float) -> None:
        suffix = f" ({elapsed:.0f}s)" if elapsed >= 2 else ""
        line = msg + suffix
        if self._tty:
            self._wipe()
        self._stream.write(line + "\n")
        self._stream.flush()
        self._last_len = 0

    def _wipe(self) -> None:
        if not self._tty or self._last_len == 0:
            return
        self._stream.write("\r" + " " * self._last_len + "\r")
        self._stream.flush()
        self._last_len = 0


def _visible_len(s: str) -> int:
    return len(s)
