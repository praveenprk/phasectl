"""Opt-in end-to-end field test; excluded from the default unit-test command."""

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src"
HARNESS = ROOT / "tests" / "field"
PROJECT = "fieldtest"
DECISION = "DECISION-FIELD-901"


def _git(repo: Path, *args: str) -> bytes:
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    result = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        env=env,
        timeout=30,
    )
    return result.stdout


def _git_text(repo: Path, *args: str) -> str:
    return _git(repo, *args).decode("utf-8").strip()


def _repository_state(repo: Path) -> dict:
    status = _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    diff = _git(repo, "diff", "--no-ext-diff", "--binary", "HEAD", "--")
    untracked = {}
    for raw_path in _git(repo, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0"):
        if not raw_path:
            continue
        relative = os.fsdecode(raw_path)
        path = repo / relative
        if path.is_symlink():
            digest = hashlib.sha256(os.readlink(path).encode()).hexdigest()
        else:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        untracked[relative] = digest
    return {
        "head": _git_text(repo, "rev-parse", "HEAD"),
        "status_sha256": hashlib.sha256(status).hexdigest(),
        "diff_sha256": hashlib.sha256(diff).hexdigest(),
        "untracked_sha256": untracked,
    }


def _events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _message_text(event: dict) -> str:
    return "\n".join(str(message.get("content", "")) for message in event["messages"])


def test_field_runner() -> None:
    started = time.monotonic()
    repo = Path(os.environ.get("PHASECTL_FIELD_REPO", "~/Desktop/contextos")).expanduser().resolve()
    output = Path(os.environ.get("PHASECTL_FIELD_OUTPUT", ROOT / ".field-test")).expanduser().resolve()
    if output == repo or repo in output.parents:
        raise ValueError("field-test output must not be inside the external repository")
    output.mkdir(parents=True, exist_ok=True)

    import phasectl

    source_file = Path(phasectl.__file__).resolve()
    report = {
        "schema_version": 1,
        "status": "running",
        "repository": str(repo),
        "phasectl_source": str(source_file),
        "checks": [],
        "commands": [],
    }

    def check(name: str, condition: bool, details=None) -> None:
        report["checks"].append({"name": name, "passed": bool(condition), "details": details})
        if not condition:
            raise AssertionError(f"{name}: {details}")

    try:
        check("source-backed CLI", SOURCE / "phasectl" in source_file.parents, str(source_file))
        check("external repository exists", repo.is_dir(), str(repo))
        check("external repository is Git", _git_text(repo, "rev-parse", "--is-inside-work-tree") == "true")
        before = _repository_state(repo)
        report["repository_before"] = before
        branch = _git_text(repo, "rev-parse", "--abbrev-ref", "HEAD")
        short_head = _git_text(repo, "rev-parse", "--short", "HEAD")
        porcelain = _git_text(repo, "status", "--porcelain")
        dirty_count = len([line for line in porcelain.splitlines() if line.strip()])

        with tempfile.TemporaryDirectory(prefix="phasectl-field-") as temporary:
            temp = Path(temporary)
            home = temp / "home"
            xdg_config = temp / "xdg" / "config"
            xdg_data = temp / "xdg" / "data"
            xdg_cache = temp / "xdg" / "cache"
            xdg_state = temp / "xdg" / "state"
            agent = temp / "agent"
            state = temp / "state"
            work = temp / "work"
            for directory in (home, xdg_config, xdg_data, xdg_cache, xdg_state, agent, state, work):
                directory.mkdir(parents=True, exist_ok=True)

            config_dir = xdg_config / "phasectl"
            phases_dir = config_dir / "phases"
            phases_dir.mkdir(parents=True)
            db_path = state / "sessions.db"
            graph_path = state / "graph.json"
            events_path = state / "provider-events.jsonl"
            events_path.touch()
            config_dir.joinpath("config.toml").write_text(
                "[provider]\n"
                'backend = "anthropic"\n'
                'model = "field-mock"\n'
                'compression_model = "field-mock"\n\n'
                "[defaults]\n"
                f'project = "{PROJECT}"\n\n'
                "[storage]\n"
                f"db_path = {json.dumps(str(db_path))}\n\n"
                "[graph]\n"
                f"path = {json.dumps(str(graph_path))}\n\n"
                "[coding_agent]\n"
                f"projects_dir = {json.dumps(str(agent))}\n\n"
                f"[projects.{PROJECT}]\n"
                f"index_path = {json.dumps(str(repo))}\n",
                encoding="utf-8",
            )
            phases_dir.joinpath("design.toml").write_text(
                "[phase]\n"
                'name = "design"\n'
                "temperature = 0.0\n"
                "token_budget = 1\n"
                "tools_allowed = []\n"
                'system_prompt = "Deterministic field-test design phase."\n',
                encoding="utf-8",
            )

            child_env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": str(home),
                "XDG_CONFIG_HOME": str(xdg_config),
                "XDG_DATA_HOME": str(xdg_data),
                "XDG_CACHE_HOME": str(xdg_cache),
                "XDG_STATE_HOME": str(xdg_state),
                "PHASECTL_AGENT_DIR": str(agent),
                "PHASECTL_FIELD_EVENTS": str(events_path),
                "PHASECTL_FIELD_SOURCE_ROOT": str(SOURCE / "phasectl"),
                "PYTHONPATH": os.pathsep.join((str(HARNESS), str(SOURCE))),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONHASHSEED": "0",
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_TERMINAL_PROMPT": "0",
                "LC_ALL": "C",
                "NO_COLOR": "1",
            }

            def cli(*args: str) -> dict:
                command = [sys.executable, "-m", "phasectl.cli", *args]
                try:
                    completed = subprocess.run(
                        command,
                        cwd=work,
                        env=child_env,
                        stdin=subprocess.DEVNULL,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    record = {
                        "argv": ["python", "-m", "phasectl.cli", *args],
                        "returncode": completed.returncode,
                        "stdout": completed.stdout,
                        "stderr": completed.stderr,
                    }
                except subprocess.TimeoutExpired as error:
                    record = {
                        "argv": ["python", "-m", "phasectl.cli", *args],
                        "returncode": None,
                        "stdout": error.stdout or "",
                        "stderr": error.stderr or "",
                        "error": "timed out after 30 seconds",
                    }
                    report["commands"].append(record)
                    raise
                report["commands"].append(record)
                return record

            initial = cli("status", "--json")
            check("initial status command", initial["returncode"] == 0, initial)
            check("lifecycle starts without active session", json.loads(initial["stdout"])["session_id"] is None)

            opened = cli("start", "--phase", "orient")
            check("start command", opened["returncode"] == 0, opened)
            expected_git = (
                f"CURRENT_GIT_STATE: branch={branch} dirty={bool(dirty_count)} "
                f"dirty_count={dirty_count} head={short_head}"
            )
            check("start uses live Git grounding", expected_git in opened["stdout"], opened["stdout"])

            active = cli("status", "--json")
            check("active status command", active["returncode"] == 0, active)
            check("lifecycle opens orient session", json.loads(active["stdout"])["phase"] == "orient")

            duplicate = cli("start", "--phase", "orient")
            check("duplicate start is rejected", duplicate["returncode"] == 1, duplicate)

            switched = cli("switch", "--phase", "design", "--quiet")
            check("phase switch command", switched["returncode"] == 0, switched)
            design_status = cli("status", "--json")
            check("phase switch is persistent", json.loads(design_status["stdout"])["phase"] == "design")

            first_chat = cli("chat", f"Choose {DECISION} for append-only history.")
            check("first design chat", first_chat["returncode"] == 0, first_chat)
            check("chat uses live Git grounding", expected_git in first_chat["stdout"], first_chat["stdout"])
            second_chat = cli("chat", "Continue the deterministic design.")
            check("second design chat", second_chat["returncode"] == 0, second_chat)
            third_chat = cli("chat", "Finalize the deterministic design.")
            check("third design chat", third_chat["returncode"] == 0, third_chat)

            first_close = cli("close")
            check("first close command", first_close["returncode"] == 0, first_close)
            check(
                "close reports deterministic reference recovery",
                f"Recovered decision references missing from generated summary: {DECISION}" in first_close["stderr"],
                first_close["stderr"],
            )
            closed_status = cli("status", "--json")
            closed_json = json.loads(closed_status["stdout"])
            check(
                "first close deactivates session",
                closed_json["phase"] is None and closed_json["last_session"] is not None,
                closed_json,
            )

            with closing(sqlite3.connect(db_path)) as connection:
                connection.row_factory = sqlite3.Row
                sessions = [dict(row) for row in connection.execute("SELECT * FROM sessions ORDER BY created_at")]
                turns = [dict(row) for row in connection.execute("SELECT * FROM turns ORDER BY id")]
            check("first session persisted", len(sessions) == 1, sessions)
            session = sessions[0]
            original = [turn for turn in turns if DECISION in turn["content"] and turn["phase"] == "design"]
            compressed = [turn for turn in turns if turn["phase"] == "_compression"]
            check("original decision turn survives compression", len(original) == 1 and original[0]["compressed"] == 1, original)
            check("compression produced provenance rows", bool(compressed) and all(turn["origin"] == "compression" for turn in compressed))
            expected_summary = (
                "FIELD SNAPSHOT WITHOUT IDENTIFIER\n\n"
                "Decision references recovered from original turns (historical occurrence only; "
                "not acceptance, implementation, or verification):\n"
                f"- {DECISION}"
            )
            check("close stores recovered summary verbatim", session["final_summary"] == expected_summary, session["final_summary"])
            check("close records final phase", session["closing_phase"] == "design" and session["closed_at"])

            graph = json.loads(graph_path.read_text(encoding="utf-8"))
            decision_nodes = [node for node in graph["nodes"] if node.get("id") == DECISION]
            check("decision reference reaches isolated graph", len(decision_nodes) == 1, decision_nodes)

            second_open = cli("start", "--phase", "orient")
            check("second lifecycle start", second_open["returncode"] == 0, second_open)
            history_chat = cli("chat", "Inspect historical provenance.", "--prior-session-memory")
            check("historical provenance chat", history_chat["returncode"] == 0, history_chat)
            second_close = cli("close")
            check("second close command", second_close["returncode"] == 0, second_close)
            repeated_close = cli("close")
            check("close without active session returns 3", repeated_close["returncode"] == 3, repeated_close)

            events = _events(events_path)
            chats = [event for event in events if event["kind"] == "chat"]
            snapshots = [event for event in events if event["kind"] == "compress" and "snapshot mode" in event["system"]]
            resumes = [event for event in events if event["kind"] == "compress" and "RESUME-HERE" in event["system"]]
            tail_compressions = [
                event for event in events
                if event["kind"] == "compress" and event not in snapshots and event not in resumes
            ]
            check("all model calls use source-backed mock", len(chats) == 4 and all(SOURCE / "phasectl" in Path(event["phasectl_source"]).parents for event in events))
            check("tool and MCP execution remains disabled", all(not event["tools"] for event in chats))
            check("resume and compression boundaries exercised", len(resumes) == 2 and len(tail_compressions) == 2 and len(snapshots) == 2)
            check("resume provenance is wrapped", "[MODEL-DERIVED RESUME SYNTHESIS (non-authoritative)]" in _message_text(chats[0]))
            check("compression provenance is wrapped", any("[MODEL-DERIVED COMPRESSED SUMMARY (non-authoritative)]" in _message_text(event) for event in chats))
            check("prior-session provenance is wrapped", "[HISTORICAL CONTEXT: prior session (non-authoritative)]" in _message_text(chats[-1]))
            check("close receives compressed original", DECISION in snapshots[0]["content"])
            check("close excludes synthetic compression rows", "[COMPRESSED SUMMARY:" not in snapshots[0]["content"])
            report["provider_event_counts"] = {
                "chat": len(chats),
                "resume": len(resumes),
                "tail_compression": len(tail_compressions),
                "snapshot": len(snapshots),
            }

            check("SQLite remains in isolated state", db_path.is_file() and state in db_path.parents)
            check("agent path remains isolated", child_env["PHASECTL_AGENT_DIR"] == str(agent) and temp in agent.parents)
            check("HOME and XDG paths remain isolated", all(temp in Path(child_env[name]).parents for name in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME")))

        after = _repository_state(repo)
        report["repository_after"] = after
        check("external HEAD and working tree are unchanged", after == before, {"before": before, "after": after})
        report["status"] = "passed"
    except Exception as error:
        report["status"] = "failed"
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        report["duration_seconds"] = round(time.monotonic() - started, 3)
        report["check_counts"] = {
            "passed": sum(check["passed"] for check in report["checks"]),
            "failed": sum(not check["passed"] for check in report["checks"]),
        }
        output.joinpath("results.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
