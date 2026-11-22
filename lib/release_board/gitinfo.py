"""Read-only Git inspection for the configured project roots."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .util import git, git_out, parse_dpkg_changelog, split_version


def _branch_records(root: Path, prefix: str) -> dict[str, dict[str, Any]]:
    fmt = "%(refname:short)%00%(objectname:short)%00%(upstream:short)%00%(upstream:track)"
    out = git_out(["for-each-ref", f"refs/{prefix}", f"--format={fmt}"], root)
    records: dict[str, dict[str, Any]] = {}
    for line in out.splitlines():
        parts = line.split("\0")
        if len(parts) < 3:
            continue
        name = parts[0]
        if prefix == "remotes":
            if name in {"origin", "origin/HEAD"} or not name.startswith("origin/"):
                continue
            name = name[len("origin/") :]
        records[name] = {
            "name": name,
            "sha": parts[1],
            "upstream": parts[2],
            "track": parts[3] if len(parts) > 3 else "",
        }
    return records


def inspect_root(root: Path, fetch: bool = False) -> dict[str, Any]:
    root = root.expanduser().resolve()
    result: dict[str, Any] = {
        "path": str(root),
        "name": root.name,
        "exists": root.is_dir(),
        "isGit": False,
        "error": "",
    }
    if not root.is_dir():
        result["error"] = "path does not exist"
        return result

    top = git_out(["rev-parse", "--show-toplevel"], root)
    if not top:
        result["error"] = "not a Git repository"
        return result

    repo = Path(top)
    result["isGit"] = True
    result["repo"] = str(repo)

    if fetch:
        fetched = git(["fetch", "--all", "--prune", "--tags"], repo, timeout=120)
        result["fetched"] = fetched.returncode == 0
        if fetched.returncode != 0:
            result["fetchError"] = fetched.stderr.strip()

    status = git(["status", "--porcelain=v2", "--branch"], repo, timeout=30)
    branch = ""
    upstream = ""
    ahead = 0
    behind = 0
    dirty = 0
    untracked = 0
    if status.returncode == 0:
        for line in status.stdout.splitlines():
            if line.startswith("# branch.head "):
                branch = line.split(" ", 2)[2]
            elif line.startswith("# branch.upstream "):
                upstream = line.split(" ", 2)[2]
            elif line.startswith("# branch.ab "):
                values = line.split()
                if len(values) >= 4:
                    ahead = int(values[2].lstrip("+"))
                    behind = int(values[3].lstrip("-"))
            elif line.startswith("? "):
                untracked += 1
                dirty += 1
            elif line.startswith(("1 ", "2 ", "u ")):
                dirty += 1

    result.update(
        {
            "branch": branch,
            "upstream": upstream,
            "ahead": ahead,
            "behind": behind,
            "dirty": dirty,
            "untracked": untracked,
            "clean": dirty == 0,
            "remote": git_out(["remote", "get-url", "origin"], repo),
        }
    )

    head = git_out(["log", "-1", "--format=%H%x00%h%x00%cI%x00%s"], repo)
    if head:
        parts = head.split("\0", 3)
        result["head"] = {
            "sha": parts[0] if len(parts) > 0 else "",
            "short": parts[1] if len(parts) > 1 else "",
            "date": parts[2] if len(parts) > 2 else "",
            "subject": parts[3] if len(parts) > 3 else "",
        }
    else:
        result["head"] = {}

    result["headTags"] = [tag for tag in git_out(["tag", "--points-at", "HEAD"], repo).splitlines() if tag]
    result["tags"] = [tag for tag in git_out(["tag", "--list", "--sort=-creatordate"], repo).splitlines() if tag][:20]

    local = _branch_records(repo, "heads")
    remote = _branch_records(repo, "remotes")
    result["branches"] = {
        "local": list(local.values()),
        "remote": list(remote.values()),
        "localOnly": sorted(set(local) - set(remote)),
        "remoteOnly": sorted(set(remote) - set(local)),
        "diverged": sorted(
            name for name in set(local) & set(remote) if local[name]["sha"] != remote[name]["sha"]
        ),
    }

    branch_details = []
    for name in sorted(set(local) | set(remote)):
        local_ref = local.get(name, {})
        remote_ref = remote.get(name, {})
        detail = {
            "name": name,
            "localSha": local_ref.get("sha", ""),
            "remoteSha": remote_ref.get("sha", ""),
            "status": "in-sync" if local_ref.get("sha") and local_ref.get("sha") == remote_ref.get("sha") else "",
        }
        if not local_ref:
            detail["status"] = "remote-only"
        elif not remote_ref:
            detail["status"] = "local-only"
        elif local_ref.get("sha") != remote_ref.get("sha"):
            counts = git_out(
                ["rev-list", "--left-right", "--count", f"{remote_ref['sha']}...{local_ref['sha']}"],
                repo,
            )
            if counts:
                parts = counts.split()
                if len(parts) == 2:
                    detail["behind"] = int(parts[0])
                    detail["ahead"] = int(parts[1])
            detail["status"] = "diverged" if detail.get("ahead") and detail.get("behind") else "out-of-sync"
        branch_details.append(detail)
    result["branches"]["details"] = branch_details
    if branch and branch in local and branch in remote:
        counts = git_out(["rev-list", "--left-right", "--count", f"{remote[branch]['sha']}...{local[branch]['sha']}"], repo)
        if counts:
            parts = counts.split()
            if len(parts) == 2:
                result["branches"]["remoteBehind"], result["branches"]["remoteAhead"] = int(parts[0]), int(parts[1])

    changelog = parse_dpkg_changelog(repo)
    if changelog:
        version = split_version(changelog.get("version", ""))
        result["package"] = {
            "source": changelog.get("source", ""),
            "version": version["version"],
            "base": version["base"],
            "revision": version["revision"],
            "distribution": changelog.get("distribution", ""),
        }
    return result


def inspect_roots(roots: list[dict[str, str]], fetch: bool = False) -> dict[str, Any]:
    entries = []
    for entry in roots:
        info = inspect_root(Path(entry["path"]), fetch=fetch)
        info["configuredName"] = entry["name"]
        entries.append(info)
    return {
        "roots": entries,
        "summary": {
            "total": len(entries),
            "clean": sum(1 for item in entries if item.get("clean")),
            "dirty": sum(1 for item in entries if item.get("isGit") and not item.get("clean")),
            "unreachable": sum(1 for item in entries if not item.get("isGit")),
            "diverged": sum(1 for item in entries if item.get("branches", {}).get("diverged")),
        },
    }
