"""Resumable release pipeline with Git conflict detection."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import load_config, root_entry
from .release import (
    build_source,
    check_release,
    push_release,
    run_tests,
    upload_built,
    watch,
)
from .util import (
    git_out,
    now_iso,
    run,
    split_version,
    state_dir,
    write_json_atomic,
)


class PipelineBlocked(RuntimeError):
    pass


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "root"


def state_path(root: Path, config: dict[str, Any]) -> Path:
    entry = root_entry(root, config)
    return state_dir() / "pipelines" / f"{_slug(entry['name'])}.json"


def load_state(root: Path, config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    config = config or load_config()
    path = state_path(root, config)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save_state(root: Path, config: dict[str, Any], state: dict[str, Any]) -> Path:
    path = state_path(root, config)
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = now_iso()
    write_json_atomic(path, state, 0o600)
    return path


def _in_rebase(root: Path) -> bool:
    return (root / ".git" / "rebase-merge").exists() or (root / ".git" / "rebase-apply").exists()


def _conflicts(root: Path) -> list[str]:
    out = git_out(["diff", "--name-only", "--diff-filter=U"], root)
    return [line for line in out.splitlines() if line]


def sync_repository(root: Path, config: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    entry = root_entry(root, config)
    remote = entry.get("remote", "origin")
    branch = git_out(["rev-parse", "--abbrev-ref", "HEAD"], root)
    if branch != "main":
        raise PipelineBlocked(f"sync requires main, not {branch or 'detached HEAD'}")
    if _in_rebase(root):
        raise PipelineBlocked(
            "a Git rebase is in progress; resolve conflicts, run git rebase --continue, then resume"
        )
    fetch = run(["git", "fetch", "--prune", remote, branch], cwd=root, timeout=120)
    if fetch.returncode != 0:
        raise PipelineBlocked(f"git fetch failed: {fetch.stderr.strip()}")
    upstream = f"{remote}/{branch}"
    counts = git_out(["rev-list", "--left-right", "--count", f"{upstream}...HEAD"], root)
    behind = ahead = 0
    if counts:
        parts = counts.split()
        if len(parts) == 2:
            behind, ahead = int(parts[0]), int(parts[1])
    state["conflicts"] = []
    if behind:
        rebase = run(["git", "pull", "--rebase", remote, branch], cwd=root, timeout=300)
        if rebase.returncode != 0:
            conflicts = _conflicts(root)
            state["conflicts"] = conflicts
            raise PipelineBlocked(
                "rebase conflicts" + (": " + ", ".join(conflicts) if conflicts else "")
            )
    return {"remote": remote, "branch": branch, "behind": behind, "ahead": ahead, "rebased": bool(behind)}


def _clear_stale_local_tag(root: Path, config: dict[str, Any]) -> str:
    from .util import parse_dpkg_changelog

    changelog = parse_dpkg_changelog(root)
    if not changelog:
        return ""
    base = split_version(changelog.get("version", ""))["base"]
    tag = f"v{base}"
    tag_commit = git_out(["rev-parse", f"refs/tags/{tag}^{{commit}}"], root)
    head = git_out(["rev-parse", "HEAD"], root)
    if not tag_commit or tag_commit == head:
        return ""
    entry = root_entry(root, config)
    remote = entry.get("remote", "origin")
    remote_tag = run(["git", "ls-remote", "--tags", remote, f"refs/tags/{tag}"], cwd=root, timeout=30)
    if remote_tag.returncode == 0 and remote_tag.stdout.strip():
        raise PipelineBlocked(f"release tag {tag} already exists on {remote} and points elsewhere")
    delete = run(["git", "tag", "-d", tag], cwd=root, timeout=30)
    if delete.returncode != 0:
        raise PipelineBlocked(f"cannot replace stale local tag {tag}: {delete.stderr.strip()}")
    return tag


def release_pipeline(
    root: Path,
    config: dict[str, Any] | None = None,
    series_list: list[str] | None = None,
    revision: int | None = None,
    confirm: bool = False,
    check_publication: bool = True,
    resume: bool = False,
    log: Any = None,
) -> dict[str, Any]:
    config = config or load_config()
    root = root.expanduser().resolve()
    emit = log or (lambda _message: None)
    if not confirm:
        return {"ok": False, "error": "pipeline requires explicit confirmation", "steps": {}}

    from .util import parse_dpkg_changelog

    changelog = parse_dpkg_changelog(root)
    package = changelog.get("source", "") if changelog else ""
    version = changelog.get("version", "") if changelog else ""
    existing = load_state(root, config) if resume else None
    if existing and (
        existing.get("package") != package
        or existing.get("baseVersion") != split_version(version)["base"]
    ):
        existing = None
    state = existing or {
        "schema": 1,
        "status": "running",
        "package": package,
        "version": version,
        "root": str(root),
        "startedAt": now_iso(),
        "steps": {},
        "conflicts": [],
    }
    state["status"] = "running"
    state["options"] = {
        "series": series_list or [],
        "revision": revision,
        "checkPublication": check_publication,
    }
    save_state(root, config, state)

    def completed(name: str) -> bool:
        return bool(existing and existing.get("steps", {}).get(name, {}).get("ok"))

    def step(name: str, action: Any) -> dict[str, Any]:
        if completed(name):
            emit(f"== {name} (already complete)")
            return {"ok": True}
        emit(f"== {name}")
        try:
            result = action()
        except PipelineBlocked as exc:
            state["status"] = "blocked"
            state["steps"][name] = {"ok": False, "error": str(exc), "at": now_iso()}
            save_state(root, config, state)
            raise
        except Exception as exc:
            state["status"] = "failed"
            state["steps"][name] = {"ok": False, "error": str(exc), "at": now_iso()}
            save_state(root, config, state)
            raise
        ok = bool(result.get("ok", True)) if isinstance(result, dict) else bool(result)
        state["steps"][name] = {
            "ok": ok,
            "at": now_iso(),
            "error": str(result.get("error", "")) if isinstance(result, dict) else "",
        }
        if not ok:
            state["status"] = "failed"
        save_state(root, config, state)
        if not ok:
            raise PipelineBlocked(f"{name} failed")
        return result

    try:
        preflight = step(
            "preflight",
            lambda: check_release(root, config, require_tag=False, require_sync=False),
        )
        if not preflight.get("ok"):
            raise PipelineBlocked("preflight failed")
        step("sync", lambda: sync_repository(root, config, state) | {"ok": True})
        step("tests", lambda: run_tests(root, config))
        _clear_stale_local_tag(root, config)
        step("build", lambda: build_source(root, series_list, revision, config, tag=True))
        step("push", lambda: push_release(root, config))
        uploads = step("upload", lambda: upload_built(root, config, confirm=True))
        if check_publication:
            step("publication", lambda: watch(root, series_list, config, once=True))
        state["status"] = "completed"
        state["completedAt"] = now_iso()
        state["conflicts"] = []
        save_state(root, config, state)
        return {"ok": True, "state": state, "steps": state["steps"]}
    except PipelineBlocked as exc:
        return {"ok": False, "error": str(exc), "state": state, "steps": state["steps"]}
    except KeyboardInterrupt:
        state["status"] = "stopped"
        save_state(root, config, state)
        return {"ok": False, "error": "pipeline stopped", "state": state, "steps": state["steps"]}


def pipeline_status(root: Path, config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_config()
    state = load_state(root, config)
    if state is None:
        return {"ok": True, "state": None, "message": "no pipeline state"}
    return {"ok": True, "state": state}
