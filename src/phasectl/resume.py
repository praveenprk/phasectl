import json
import os
import subprocess
from pathlib import Path

from .api import compress


def _run_git(repo_path: str, args: list[str], timeout: float = 3.0) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def get_git_state(repo_path: str) -> dict:
    if not repo_path or not os.path.isdir(repo_path):
        return {}
    inside = _run_git(repo_path, ["rev-parse", "--is-inside-work-tree"])
    if not inside or inside.strip() != "true":
        return {}

    state: dict = {}
    branch = _run_git(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    if branch:
        state["branch"] = branch.strip()

    porcelain = _run_git(repo_path, ["status", "--porcelain"])
    if porcelain is not None:
        lines = [line for line in porcelain.splitlines() if line.strip()]
        state["dirty"] = bool(lines)
        state["dirty_count"] = len(lines)
        files: list[str] = []
        for line in lines[:10]:
            parts = line[3:].split(" -> ")
            files.append(parts[-1].strip())
        state["dirty_files"] = files

    diffstat = _run_git(repo_path, ["diff", "--stat", "HEAD"])
    if diffstat:
        stat_lines = diffstat.splitlines()
        if len(stat_lines) > 12:
            stat_lines = stat_lines[:12] + [f"... ({len(stat_lines) - 12} more)"]
        state["diffstat"] = "\n".join(stat_lines)

    last = _run_git(repo_path, ["log", "-1", "--format=%h|%s|%cr"])
    if last and last.strip():
        parts = last.strip().split("|", 2)
        if len(parts) == 3:
            state["last_commit"] = {
                "hash": parts[0],
                "subject": parts[1],
                "when": parts[2],
            }

    return state


def _candidate_projects_dirs() -> list[Path]:
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    candidates: list[Path] = []
    if env:
        candidates.append(Path(env) / "projects")
    candidates.append(Path.home() / ".claude" / "projects")
    candidates.append(Path.home() / ".config" / "claude" / "projects")
    return candidates


def _resolve_projects_dir(configured: str = "") -> Path | None:
    if configured:
        p = Path(os.path.expanduser(configured))
        return p if p.is_dir() else None
    for p in _candidate_projects_dirs():
        if p.is_dir():
            return p
    return None


def find_claude_code_session(repo_path: str, projects_dir: str = "") -> str | None:
    if not repo_path:
        return None
    root = _resolve_projects_dir(projects_dir)
    if root is None:
        return None

    abs_repo = os.path.realpath(repo_path)
    slug_dash = abs_repo.replace("/", "-")
    slug_dash_lead = "-" + abs_repo.lstrip("/").replace("/", "-")
    basename = os.path.basename(abs_repo.rstrip("/"))

    matches: list[Path] = []
    for sub in root.iterdir():
        if not sub.is_dir():
            continue
        name = sub.name
        if name == slug_dash or name == slug_dash_lead:
            matches.append(sub)
        elif basename and basename in name:
            matches.append(sub)

    if not matches:
        return None

    matches.sort(key=lambda p: (p.name != slug_dash_lead, p.name != slug_dash))
    target = matches[0]

    jsonls = sorted(
        target.glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not jsonls:
        return None
    return str(jsonls[0])


def _extract_text_from_content(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text = block.get("text", "")
            if text:
                parts.append(text)
        elif btype == "thinking":
            pass
        elif btype == "tool_use":
            name = block.get("name", "tool")
            inp = block.get("input", {})
            try:
                inp_str = json.dumps(inp)[:200]
            except (TypeError, ValueError):
                inp_str = str(inp)[:200]
            parts.append(f"[tool_use: {name} {inp_str}]")
        elif btype == "tool_result":
            r = block.get("content", "")
            if isinstance(r, list):
                r = " ".join(
                    b.get("text", "")
                    for b in r
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            r_str = str(r)[:200]
            if r_str:
                parts.append(f"[tool_result: {r_str}]")
    return "\n".join(parts).strip()


def _is_low_signal(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    markers = (
        "<local-command-stdout>",
        "<local-command-caveat>",
        "<command-name>",
        "<command-message>",
        "<command-args>",
    )
    if stripped.startswith(markers) and len(stripped) < 400:
        return True
    return False


def extract_last_context(jsonl_path: str, max_chars: int = 4000) -> str:
    if not jsonl_path or not os.path.isfile(jsonl_path):
        return ""

    entries: list[tuple[str, str, str]] = []
    try:
        with open(jsonl_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = d.get("type")
                if t not in ("user", "assistant"):
                    continue
                msg = d.get("message") or {}
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role") or t
                text = _extract_text_from_content(msg.get("content"))
                if not text or _is_low_signal(text):
                    continue
                ts = d.get("timestamp", "")
                entries.append((ts, role, text))
    except OSError:
        return ""

    if not entries:
        return ""

    tail: list[tuple[str, str, str]] = []
    total = 0
    for ts, role, text in reversed(entries):
        snippet = text[:1200]
        chunk_len = len(snippet) + len(role) + 4
        if total + chunk_len > max_chars and tail:
            break
        tail.append((ts, role, snippet))
        total += chunk_len

    tail.reverse()
    lines = [f"[{ts[:19]}] {role}:\n{text}" for ts, role, text in tail]
    return "\n\n".join(lines)


_SYNTHESIS_PROMPT = (
    "You produce a RESUME-HERE briefing for an engineer returning to work. "
    "Fuse: git working state, the last session summary, and the last Claude Code context. "
    "Output, in order: "
    "(1) 2-4 line 'Resume here:' — the precise last checkpoint, what they were "
    "mid-way through, and the exact next step. "
    "(2) 'State:' one line each for branch, uncommitted changes, last commit, "
    "current blocker. "
    "Prefer the newest concrete signal (uncommitted diff and CC context are newer "
    "than the last closed session). Be specific with symbol/file/line when present. "
    "No preamble, no padding."
)


def _format_git_for_prompt(git: dict) -> str:
    if not git:
        return ""
    lines = ["## Git state"]
    if "branch" in git:
        lines.append(f"branch: {git['branch']}")
    if git.get("dirty"):
        lines.append(f"uncommitted: {git.get('dirty_count', 0)} files")
        files = git.get("dirty_files") or []
        if files:
            lines.append("dirty files:")
            for f in files:
                lines.append(f"  - {f}")
    else:
        lines.append("uncommitted: clean")
    if "diffstat" in git:
        lines.append("diffstat:")
        lines.append(git["diffstat"])
    lc = git.get("last_commit")
    if lc:
        lines.append(f"last commit: {lc['hash']} {lc['subject']} ({lc['when']})")
    return "\n".join(lines)


def build_resume_block(
    git: dict,
    session_summary: str,
    cc_context: str,
    app_config: dict,
) -> str:
    sections: list[str] = []
    git_block = _format_git_for_prompt(git)
    if git_block:
        sections.append(git_block)
    if session_summary and session_summary.strip():
        sections.append(f"## Last phasectl session summary\n{session_summary.strip()}")
    if cc_context and cc_context.strip():
        sections.append(f"## Last Claude Code context (tail)\n{cc_context.strip()}")

    if not sections:
        return ""

    payload = "\n\n".join(sections)
    try:
        return compress(payload, app_config, system_prompt=_SYNTHESIS_PROMPT).strip()
    except Exception as e:
        fallback = ["Resume here: (synthesis unavailable — raw signals below)"]
        if git.get("branch"):
            fallback.append(f"State: branch={git['branch']} dirty={git.get('dirty_count', 0)}")
        fallback.append(f"[synthesis error: {e}]")
        fallback.append(payload)
        return "\n".join(fallback)
