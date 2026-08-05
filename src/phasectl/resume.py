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


KNOWN_AGENT_DIRS: list[str] = [
    "~/.claude/projects",
    "~/.codex/sessions",
    "~/.cursor/sessions",
    "~/.continue/sessions",
]


def _roots_to_scan(configured: str = "") -> list[Path]:
    env = os.environ.get("PHASECTL_AGENT_DIR")
    if configured:
        p = Path(os.path.expanduser(configured))
        return [p] if p.is_dir() else []
    if env:
        p = Path(os.path.expanduser(env))
        return [p] if p.is_dir() else []
    roots: list[Path] = []
    for raw in KNOWN_AGENT_DIRS:
        p = Path(os.path.expanduser(raw))
        if p.is_dir():
            roots.append(p)
    return roots


def find_agent_session(repo_path: str, projects_dir: str = "") -> str | None:
    if not repo_path:
        return None
    roots = _roots_to_scan(projects_dir)
    if not roots:
        return None

    abs_repo = os.path.realpath(repo_path)
    slug_dash = abs_repo.replace("/", "-")
    slug_dash_lead = "-" + abs_repo.lstrip("/").replace("/", "-")
    basename = os.path.basename(abs_repo.rstrip("/"))

    best: tuple[float, Path] | None = None
    for root in roots:
        try:
            subs = list(root.iterdir())
        except OSError:
            continue
        for sub in subs:
            if not sub.is_dir():
                continue
            name = sub.name
            if not (
                name == slug_dash
                or name == slug_dash_lead
                or (basename and basename in name)
            ):
                continue
            try:
                jsonls = list(sub.glob("*.jsonl"))
            except OSError:
                continue
            for jf in jsonls:
                try:
                    mtime = jf.stat().st_mtime
                except OSError:
                    continue
                if best is None or mtime > best[0]:
                    best = (mtime, jf)

    return str(best[1]) if best else None


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
    "Fuse: git working state, the last session summary, and the last "
    "external coding-agent transcript context. Output, in order: "
    "(1) 2-4 line 'Resume here:' — the precise last checkpoint, what they were "
    "mid-way through, and the exact next step. "
    "(2) 'State:' one line each for branch, uncommitted changes, last commit, "
    "current blocker. "
    "Prefer the newest concrete signal (uncommitted diff and coding-agent "
    "transcript context are newer than the last closed session). Be specific "
    "with symbol/file/line when present. No preamble, no padding."
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
    agent_context: str,
    app_config: dict,
) -> str:
    sections: list[str] = []
    git_block = _format_git_for_prompt(git)
    if git_block:
        sections.append(git_block)
    if session_summary and session_summary.strip():
        sections.append(f"## Last phasectl session summary\n{session_summary.strip()}")
    if agent_context and agent_context.strip():
        sections.append(f"## Last coding-agent context (tail)\n{agent_context.strip()}")

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
