"""Install the offline provider boundary for field-test CLI subprocesses."""

import json
import os
from pathlib import Path
import socket


def _network_disabled(*args, **kwargs):
    raise RuntimeError("network access is disabled during the phasectl field test")


socket.create_connection = _network_disabled
socket.socket.connect = _network_disabled
socket.socket.connect_ex = _network_disabled

import phasectl
import phasectl.api as api


_SOURCE_ROOT = Path(os.environ["PHASECTL_FIELD_SOURCE_ROOT"]).resolve()
_SOURCE_FILE = Path(phasectl.__file__).resolve()
if _SOURCE_ROOT not in _SOURCE_FILE.parents:
    raise RuntimeError(f"field test loaded non-source phasectl: {_SOURCE_FILE}")

_EVENTS = Path(os.environ["PHASECTL_FIELD_EVENTS"])


def _record(kind, **payload):
    event = {"kind": kind, "phasectl_source": str(_SOURCE_FILE), **payload}
    with _EVENTS.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, sort_keys=True) + "\n")


class FieldProvider:
    def chat(self, system, messages, tools, temperature, max_tokens):
        _record("chat", system=system, messages=messages, tools=tools)
        return "FIELD ASSISTANT RESPONSE", []

    def compress(self, system, content, max_tokens):
        _record("compress", system=system, content=content)
        if "RESUME-HERE" in system:
            return "Resume here: FIELD RESUME\nState: deterministic"
        if "snapshot mode" in system:
            return "FIELD SNAPSHOT WITHOUT IDENTIFIER"
        return "FIELD COMPRESSION WITHOUT IDENTIFIER"


api.get_provider = lambda config: FieldProvider()
