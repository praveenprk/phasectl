import os
import re
import subprocess
from pathlib import Path

from .api import compress
from .config import get_index_path


def _run_git(repo: str, args: list[str], timeout: float = 5.0) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", repo, *args],
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


def is_git_repo(repo: str) -> bool:
    if not repo or not os.path.isdir(repo):
        return False
    out = _run_git(repo, ["rev-parse", "--is-inside-work-tree"])
    return bool(out) and out.strip() == "true"


def resolve_base(repo: str, requested: str = "main") -> str | None:
    """Return the base ref to compare against, with fallbacks."""
    for candidate in (requested, "master"):
        if _run_git(repo, ["rev-parse", "--verify", "--quiet", candidate]) is not None:
            if candidate:
                verify = _run_git(repo, ["rev-parse", "--verify", "--quiet", candidate])
                if verify and verify.strip():
                    return candidate
    origin_head = _run_git(repo, ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"])
    if origin_head and origin_head.strip():
        return origin_head.strip()
    return None


def get_current_branch(repo: str) -> str | None:
    out = _run_git(repo, ["rev-parse", "--abbrev-ref", "HEAD"])
    if not out:
        return None
    b = out.strip()
    return b if b and b != "HEAD" else None


def get_uncommitted(repo: str) -> dict:
    result: dict = {"dirty": False, "files": [], "count": 0}
    porcelain = _run_git(repo, ["status", "--porcelain"])
    if porcelain is None:
        return result
    lines = [line for line in porcelain.splitlines() if line.strip()]
    if not lines:
        return result

    file_paths: list[str] = []
    for line in lines:
        path = line[3:] if len(line) > 3 else line
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        file_paths.append(path.strip())

    numstat = _run_git(repo, ["diff", "--numstat", "HEAD"])
    line_counts: dict[str, tuple[int, int]] = {}
    if numstat:
        for row in numstat.splitlines():
            parts = row.split("\t")
            if len(parts) < 3:
                continue
            added_s, removed_s, path = parts[0], parts[1], parts[2].strip()
            try:
                added = int(added_s) if added_s != "-" else 0
                removed = int(removed_s) if removed_s != "-" else 0
            except ValueError:
                continue
            line_counts[path] = (added, removed)

    files = []
    for p in file_paths:
        added, removed = line_counts.get(p, (0, 0))
        files.append({"path": p, "added": added, "removed": removed})

    result["dirty"] = True
    result["files"] = files
    result["count"] = len(files)
    return result


def get_unmerged_branches(repo: str, base: str) -> list[dict]:
    if not base:
        return []
    out = _run_git(repo, ["branch", "--no-merged", base, "--format=%(refname:short)"])
    if out is None:
        return []
    branches = [b.strip() for b in out.splitlines() if b.strip()]

    result = []
    for br in branches:
        if br == base:
            continue
        ahead_raw = _run_git(repo, ["rev-list", "--count", f"{base}..{br}"])
        try:
            ahead = int(ahead_raw.strip()) if ahead_raw else 0
        except ValueError:
            ahead = 0
        last = _run_git(repo, ["log", "-1", "--format=%cr|%h|%s", br])
        last_rel = last_hash = last_subject = ""
        if last and last.strip():
            parts = last.strip().split("|", 2)
            if len(parts) == 3:
                last_rel, last_hash, last_subject = parts
        result.append({
            "branch": br,
            "ahead": ahead,
            "last_rel": last_rel,
            "last_hash": last_hash,
            "last_subject": last_subject,
        })
    return result


_AGE_UNITS = {
    "second": 1, "seconds": 1,
    "minute": 60, "minutes": 60,
    "hour": 3600, "hours": 3600,
    "day": 86400, "days": 86400,
    "week": 604800, "weeks": 604800,
    "month": 2592000, "months": 2592000,
    "year": 31536000, "years": 31536000,
}


def _age_seconds(rel: str) -> int:
    if not rel:
        return 0
    m = re.match(r"(\d+)\s+(\w+)\s+ago", rel.strip())
    if not m:
        return 0
    n, unit = int(m.group(1)), m.group(2).lower()
    return n * _AGE_UNITS.get(unit, 0)


def get_stashes(repo: str) -> list[dict]:
    out = _run_git(repo, ["stash", "list", "--format=%gd|%cr|%s"])
    if out is None:
        return []
    result = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue
        result.append({"ref": parts[0], "age_rel": parts[1], "subject": parts[2]})
    return result


def get_unpushed(repo: str) -> list[dict]:
    out = _run_git(
        repo,
        ["for-each-ref", "--format=%(refname:short)\t%(upstream:short)", "refs/heads"],
    )
    if out is None:
        return []
    result = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        branch = parts[0].strip()
        upstream = parts[1].strip() if len(parts) > 1 else ""
        if not upstream:
            result.append({"branch": branch, "ahead": 0, "has_upstream": False})
            continue
        ahead_raw = _run_git(repo, ["rev-list", "--count", f"{upstream}..{branch}"])
        try:
            ahead = int(ahead_raw.strip()) if ahead_raw else 0
        except ValueError:
            ahead = 0
        if ahead > 0:
            result.append({"branch": branch, "ahead": ahead, "has_upstream": True})
    return result


def get_worktrees(repo: str) -> list[dict]:
    out = _run_git(repo, ["worktree", "list", "--porcelain"])
    if out is None:
        return []
    entries: list[dict] = []
    current: dict = {}
    for line in out.splitlines():
        if not line.strip():
            if current:
                entries.append(current)
                current = {}
            continue
        if line.startswith("worktree "):
            current["path"] = line[len("worktree "):].strip()
        elif line.startswith("branch "):
            ref = line[len("branch "):].strip()
            current["branch"] = ref.replace("refs/heads/", "", 1)
        elif line.startswith("bare"):
            current["bare"] = True
        elif line.startswith("detached"):
            current["detached"] = True
    if current:
        entries.append(current)

    main_repo_real = os.path.realpath(repo)
    for e in entries:
        e["is_current"] = os.path.realpath(e.get("path", "")) == main_repo_real
    return entries


def resolve_repo(
    path: str | None,
    project: str | None,
    active_project: str,
    config: dict,
) -> str | None:
    if path:
        expanded = os.path.abspath(os.path.expanduser(path))
        return expanded
    if project:
        p = get_index_path(project, config)
        if p:
            return str(p)
        return None
    if active_project:
        p = get_index_path(active_project, config)
        if p:
            return str(p)
    return None


def collect(repo: str, base: str = "main", progress=None) -> dict:
    def _step(msg: str) -> None:
        if progress is not None:
            progress.step(msg)

    _step("resolving base branch...")
    resolved_base = resolve_base(repo, base)
    current = get_current_branch(repo)
    _step("scanning uncommitted changes...")
    uncommitted = get_uncommitted(repo)
    _step("scanning branches...")
    unmerged = get_unmerged_branches(repo, resolved_base) if resolved_base else []
    _step("scanning stashes...")
    stashes = get_stashes(repo)
    _step("scanning unpushed commits...")
    unpushed = get_unpushed(repo)
    _step("scanning worktrees...")
    worktrees = get_worktrees(repo)
    return {
        "repo": repo,
        "current_branch": current,
        "base": resolved_base,
        "uncommitted": uncommitted,
        "unmerged": unmerged,
        "stashes": stashes,
        "unpushed": unpushed,
        "worktrees": worktrees,
    }


def _summarize_uncommitted(u: dict) -> str:
    if not u.get("dirty"):
        return "clean"
    total = sum(f.get("added", 0) + f.get("removed", 0) for f in u.get("files", []))
    if total:
        return f"{u['count']} files, {total} lines uncommitted"
    return f"{u['count']} files uncommitted"


def _sort_unmerged_coldest_first(unmerged: list[dict]) -> list[dict]:
    return sorted(unmerged, key=lambda b: _age_seconds(b.get("last_rel", "")), reverse=True)


def render_human(data: dict, project_label: str) -> str:
    lines: list[str] = []
    current = data.get("current_branch")
    base = data.get("base") or "?"
    uncommitted = data.get("uncommitted") or {}
    unmerged = data.get("unmerged") or []
    stashes = data.get("stashes") or []
    unpushed = data.get("unpushed") or []
    worktrees = data.get("worktrees") or []

    no_upstream_set = {u["branch"] for u in unpushed if not u.get("has_upstream")}
    ahead_upstream = [u for u in unpushed if u.get("has_upstream") and u.get("ahead", 0) > 0]

    truly_loose = (
        (uncommitted.get("dirty")) or unmerged or stashes or ahead_upstream
        or len([w for w in worktrees if not w.get("is_current")]) > 0
    )
    if not truly_loose:
        return f"nothing loose in {project_label} — all merged and pushed."

    lines.append(f"In flight across {project_label}:")

    if current:
        summary = _summarize_uncommitted(uncommitted)
        current_no_remote = current in no_upstream_set
        suffix = " · no remote" if current_no_remote else ""
        lines.append(f"  ● {current}  ← current · {summary}{suffix}")

    for br in _sort_unmerged_coldest_first(unmerged):
        if br["branch"] == current:
            continue
        cold = br.get("last_rel", "?")
        ahead = br.get("ahead", 0)
        no_remote = " · no remote" if br["branch"] in no_upstream_set else ""
        lines.append(
            f"  ○ {br['branch']}  {ahead} commits ahead of {base} · {cold} cold{no_remote}"
        )

    for s in stashes:
        lines.append(f"  📦 {s['ref']}  \"{s['subject']}\" · {s['age_rel']}")

    for u in ahead_upstream:
        lines.append(f"  ⬆ {u['branch']}  {u['ahead']} commits unpushed")

    extra_wts = [w for w in worktrees if not w.get("is_current")]
    for w in extra_wts:
        br = w.get("branch", "detached")
        lines.append(f"  🌿 worktree {w.get('path', '?')} ({br})")

    return "\n".join(lines)


def synthesize_top_line(data: dict, config: dict) -> str:
    payload_parts: list[str] = []
    unmerged = _sort_unmerged_coldest_first(data.get("unmerged") or [])
    if unmerged:
        payload_parts.append("Unmerged branches (coldest first):")
        for br in unmerged[:8]:
            payload_parts.append(
                f"  {br['branch']} — {br.get('ahead', 0)} ahead, last {br.get('last_rel', '?')}"
            )
    stashes = data.get("stashes") or []
    if stashes:
        payload_parts.append("Stashes:")
        for s in stashes:
            payload_parts.append(f"  {s['ref']} — {s['age_rel']}: {s['subject']}")
    unpushed = [u for u in (data.get("unpushed") or []) if u.get("ahead", 0) > 0]
    if unpushed:
        payload_parts.append("Unpushed:")
        for u in unpushed:
            payload_parts.append(f"  {u['branch']} — {u['ahead']} commits")

    if not payload_parts:
        return ""

    prompt = (
        "You produce ONE short line naming the single most stale/forgotten loose "
        "end in a git repo. Format: 'Most stale: <branch or stash>, <age> untouched — "
        "<one-clause context>'. No preamble, no extra lines."
    )
    try:
        line = compress("\n".join(payload_parts), config, system_prompt=prompt).strip()
        return line.splitlines()[0] if line else ""
    except Exception:
        return ""
