"""Compare local, origin and released source trees, and open them in Zed."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .config import load_config
from .util import (
    ensure_dir,
    git,
    git_out,
    now_iso,
    parse_dpkg_changelog,
    split_version,
    state_dir,
    which,
    write_json_atomic,
)


class CompareError(RuntimeError):
    pass


def _require_ref(root: Path, ref: str) -> str:
    sha = git_out(["rev-parse", "--verify", "-q", f"{ref}^{{commit}}"], root)
    if not sha:
        raise CompareError(f"Git reference not found: {ref}")
    return sha


def _ref_info(root: Path, ref: str, label: str) -> dict[str, Any]:
    sha = _require_ref(root, ref)
    return {
        "label": label,
        "ref": ref,
        "sha": sha,
        "short": sha[:12],
        "subject": git_out(["log", "-1", "--format=%s", sha], root),
        "date": git_out(["log", "-1", "--format=%cI", sha], root),
    }


def _resolve_release_ref(root: Path, base: str, explicit: str) -> tuple[str, str]:
    if explicit:
        _require_ref(root, explicit)
        return explicit, ""
    candidate = f"v{base}" if base else ""
    if candidate and git_out(["rev-parse", "--verify", "-q", f"refs/tags/{candidate}^{{commit}}"], root):
        return candidate, ""
    tags = [tag for tag in git_out(["tag", "--list", "v*", "--sort=-creatordate"], root).splitlines() if tag]
    if tags:
        return "", (
            f"release tag {candidate or 'v<base>'} not found; showing Local → Origin only. "
            f"Available tags: {', '.join(tags[:8])}"
        )
    return "", "no release tag found; showing Local → Origin only"


def _parse_name_status(output: str) -> list[dict[str, Any]]:
    tokens = output.split("\0")
    files: list[dict[str, Any]] = []
    index = 0
    while index < len(tokens):
        status = tokens[index]
        index += 1
        if not status:
            continue
        code = status[0]
        if code in {"R", "C"}:
            if index + 1 >= len(tokens):
                break
            old_path = tokens[index]
            new_path = tokens[index + 1]
            index += 2
            files.append(
                {
                    "status": code,
                    "path": new_path,
                    "oldPath": old_path,
                    "statusDetail": status,
                }
            )
        else:
            if index >= len(tokens):
                break
            path = tokens[index]
            index += 1
            files.append({"status": code, "path": path, "statusDetail": status})
    return sorted(files, key=lambda item: item["path"])


def _tree(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    root: dict[str, Any] = {"dirs": {}, "files": []}
    for item in files:
        parts = [part for part in item["path"].split("/") if part]
        node = root
        for part in parts[:-1]:
            node = node["dirs"].setdefault(part, {"dirs": {}, "files": []})
        node["files"].append(item)

    def render(node: dict[str, Any], prefix: str = "") -> list[dict[str, Any]]:
        children: list[dict[str, Any]] = []
        for name in sorted(node["dirs"]):
            child = node["dirs"][name]
            path = f"{prefix}{name}"
            children.append(
                {
                    "type": "dir",
                    "name": name,
                    "path": path,
                    "children": render(child, f"{path}/"),
                }
            )
        for item in sorted(node["files"], key=lambda entry: entry["path"]):
            children.append(
                {
                    "type": "file",
                    "name": Path(item["path"]).name,
                    "path": item["path"],
                    "status": item["status"],
                    "statusDetail": item.get("statusDetail", item["status"]),
                    "oldPath": item.get("oldPath", ""),
                }
            )
        return children

    return render(root)


def _comparison(root: Path, old: dict[str, Any], new: dict[str, Any], label: str) -> dict[str, Any]:
    output = git_out(["diff", "--name-status", "-M", "-z", old["sha"], new["sha"]], root)
    files = _parse_name_status(output)
    counts = git_out(["rev-list", "--left-right", "--count", f"{old['sha']}...{new['sha']}"], root)
    from_only = to_only = 0
    if counts:
        parts = counts.split()
        if len(parts) == 2:
            from_only, to_only = int(parts[0]), int(parts[1])
    return {
        "label": label,
        "from": old,
        "to": new,
        "ahead": from_only,
        "behind": to_only,
        "fromOnly": from_only,
        "toOnly": to_only,
        "files": files,
        "tree": _tree(files),
        "fileCount": len(files),
        "stat": git_out(["diff", "--shortstat", old["sha"], new["sha"]], root),
    }


def compare_root(
    root: Path,
    config: dict[str, Any] | None = None,
    origin_branch: str = "main",
    release_ref: str = "",
    fetch: bool = False,
) -> dict[str, Any]:
    config = config or load_config()
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise CompareError(f"root does not exist: {root}")
    top = git_out(["rev-parse", "--show-toplevel"], root)
    if not top:
        raise CompareError(f"not a Git repository: {root}")
    root = Path(top)

    if fetch:
        proc = git(["fetch", "--prune", "--tags", "origin", origin_branch], root, timeout=120)
        if proc.returncode != 0:
            raise CompareError(f"git fetch failed: {proc.stderr.strip()}")

    changelog = parse_dpkg_changelog(root)
    base = split_version(changelog.get("version", ""))["base"] if changelog else ""
    local = _ref_info(root, "HEAD", "Local Sources")
    origin_ref = f"origin/{origin_branch}"
    origin = _ref_info(root, origin_ref, f"Origin {origin_branch}")
    release_name, release_warning = _resolve_release_ref(root, base, release_ref)
    if release_name:
        released = _ref_info(root, release_name, f"Released {release_name}")
    else:
        released = {
            "label": "Released Sources",
            "ref": "",
            "sha": "",
            "short": "",
            "subject": "No release tag",
            "date": "",
            "available": False,
            "warning": release_warning or "No release tag for this repository.",
        }

    local_origin = _comparison(root, local, origin, "Local Sources → Origin")
    comparisons = [local_origin]
    if release_name:
        comparisons.append(_comparison(root, origin, released, "Origin → Released"))
    dirty = bool(git_out(["status", "--porcelain"], root))
    warnings = []
    if dirty:
        warnings.append("working tree has uncommitted changes; Local Sources is HEAD")
    if release_warning:
        warnings.append(release_warning)
    if not changelog:
        warnings.append("no Debian changelog; released source is informational only")

    return {
        "ok": True,
        "root": str(root),
        "package": changelog.get("source", "") if changelog else "",
        "baseVersion": base,
        "originBranch": origin_branch,
        "dirty": dirty,
        "refs": {"local": local, "origin": origin, "released": released},
        "release": {"available": bool(release_name), "ref": release_name, "warning": release_warning},
        "comparisons": comparisons,
        "warnings": warnings,
        "generatedAt": now_iso(),
    }


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_") or "root"


def _show_file(root: Path, ref: str, git_path: str, destination: Path) -> None:
    proc = subprocess.run(
        ["git", "show", f"{ref}:{git_path}"],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise CompareError(
            f"git show failed for {ref}:{git_path}: {proc.stderr.decode('utf-8', 'replace').strip()}"
        )
    ensure_dir(destination.parent)
    destination.write_bytes(proc.stdout)


def _empty_file(destination: Path) -> None:
    ensure_dir(destination.parent)
    destination.write_bytes(b"")


def export_changed_trees(data: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_config()
    root = Path(data["root"])
    stamp = now_iso().replace(":", "").replace("+00:00", "Z")
    base = ensure_dir(state_dir() / "diffs" / _slug(root.name) / stamp)
    pairs: list[dict[str, Any]] = []

    for index, comparison in enumerate(data["comparisons"]):
        if not comparison["files"]:
            continue
        pair_root = base / f"{index + 1}-{_slug(comparison['label'])}"
        old_dir = ensure_dir(pair_root / "old")
        new_dir = ensure_dir(pair_root / "new")
        old_ref = comparison["from"]["sha"]
        new_ref = comparison["to"]["sha"]
        for item in comparison["files"]:
            path = item["path"]
            old_path = item.get("oldPath") or path
            if item["status"] == "A":
                _empty_file(old_dir / old_path)
            else:
                _show_file(root, old_ref, old_path, old_dir / old_path)
            if item["status"] == "D":
                _empty_file(new_dir / path)
            else:
                _show_file(root, new_ref, path, new_dir / path)
        pairs.append(
            {
                "label": comparison["label"],
                "old": str(old_dir),
                "new": str(new_dir),
                "files": comparison["fileCount"],
            }
        )

    manifest = {
        "schema": 1,
        "exportedAt": now_iso(),
        "root": str(root),
        "refs": data["refs"],
        "comparisons": [
            {
                "label": item["label"],
                "files": item["fileCount"],
                "fromOnly": item["fromOnly"],
                "toOnly": item["toOnly"],
            }
            for item in data["comparisons"]
        ],
        "pairs": pairs,
    }
    write_json_atomic(base / "manifest.json", manifest, 0o644)
    return {
        "directory": str(base),
        "manifest": str(base / "manifest.json"),
        "pairs": pairs,
    }


def open_in_zed(data: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_config()
    exported = export_changed_trees(data, config)
    zed = which("zed")
    if not zed:
        raise CompareError("zed CLI not found")
    pairs = exported["pairs"]
    if not pairs:
        return {
            "ok": True,
            "opened": False,
            "message": "No source differences to open in Zed",
            "export": exported,
        }
    commands = []
    for pair in pairs:
        command = [zed, "-n", "--diff", pair["old"], pair["new"]]
        process = subprocess.Popen(
            command,
            cwd=str(Path(pair["old"]).parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        commands.append({"command": command, "pid": process.pid, "label": pair["label"]})
        time.sleep(0.4)
    return {
        "ok": True,
        "opened": True,
        "commands": commands,
        "command": commands[0]["command"],
        "export": exported,
    }


def clean_diffs(root: Path | None = None) -> dict[str, Any]:
    base = state_dir() / "diffs"
    target = base / _slug(root.name) if root else base
    if target.exists():
        shutil.rmtree(target)
    return {"cleaned": str(target), "exists": target.exists()}


def compare_cli_payload(
    root: Path,
    config: dict[str, Any] | None = None,
    origin_branch: str = "main",
    release_ref: str = "",
    fetch: bool = False,
    open_zed: bool = False,
) -> dict[str, Any]:
    data = compare_root(root, config, origin_branch, release_ref, fetch)
    if open_zed:
        data["zed"] = open_in_zed(data, config)
    return data


def render_tree_text(nodes: list[dict[str, Any]], indent: int = 0) -> list[str]:
    lines: list[str] = []
    for node in nodes:
        prefix = "  " * indent
        if node["type"] == "dir":
            lines.append(f"{prefix}{node['name']}/")
            lines.extend(render_tree_text(node["children"], indent + 1))
        else:
            lines.append(f"{prefix}{node['status']}  {node['name']}")
    return lines
