"""Append-only release ledger and commit provenance verification."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import ledger_file, load_config, roots
from .util import git_out, now_iso, read_json, run, sha256_file, write_text_atomic


def read_ledger(config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    config = config or load_config()
    path = ledger_file(config)
    entries: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                entries.append(item)
    except OSError:
        pass
    return entries


def append_event(event: dict[str, Any], config: dict[str, Any] | None = None, commit: bool = False) -> Path:
    config = config or load_config()
    path = ledger_file(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema": 1, "recordedAt": now_iso(), **event}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
        handle.flush()
    if commit:
        commit_ledger(config, payload)
    return path


def commit_ledger(config: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    path = ledger_file(config)
    repo = Path(config.get("ledgerRepo", path.parent)).expanduser()
    if not (repo / ".git").exists():
        return {"committed": False, "reason": "ledger repo is not a Git repository"}
    relative = path
    try:
        relative = path.relative_to(repo)
    except ValueError:
        return {"committed": False, "reason": "ledger is outside the ledger repo"}
    add = run(["git", "add", "--", str(relative)], cwd=repo, timeout=30)
    if add.returncode != 0:
        return {"committed": False, "reason": add.stderr.strip()}
    message = f"release: record {event.get('package', '?')} {event.get('version', '?')} {event.get('event', 'event')}"
    commit = run(["git", "commit", "-m", message, "--", str(relative)], cwd=repo, timeout=60)
    return {
        "committed": commit.returncode == 0,
        "reason": "" if commit.returncode == 0 else (commit.stderr or commit.stdout).strip(),
        "sha": git_out(["rev-parse", "HEAD"], repo) if commit.returncode == 0 else "",
        "message": message,
    }


def _latest(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[tuple[str, str, str], dict[str, Any]] = {}
    rank = {"built": 1, "uploaded": 2, "published": 3, "verified": 4}
    for entry in entries:
        key = (
            str(entry.get("package", "")),
            str(entry.get("version", "")),
            str(entry.get("series", "")),
        )
        current = latest.get(key)
        if current is None or rank.get(str(entry.get("event")), 0) >= rank.get(str(current.get("event")), 0):
            latest[key] = entry
    return sorted(latest.values(), key=lambda item: str(item.get("recordedAt", "")), reverse=True)


def _find_root(package: str, config: dict[str, Any], recorded: str = "") -> Path | None:
    if recorded:
        path = Path(recorded)
        if path.is_dir():
            return path
    for entry in roots(config):
        path = Path(entry["path"])
        changelog = path / "debian" / "changelog"
        if changelog.is_file():
            try:
                first = changelog.read_text(encoding="utf-8", errors="replace").splitlines()[0]
                if first.startswith(f"{package} "):
                    return path
            except OSError:
                continue
    return None


def verify_entry(entry: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    package = str(entry.get("package", ""))
    version = str(entry.get("version", ""))
    base = str(entry.get("baseVersion", ""))
    series = str(entry.get("series", ""))
    root = _find_root(package, config, str(entry.get("root", "")))
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    check("root", root is not None, str(root) if root else "not found")
    if root:
        tag = f"v{base}" if base else ""
        tag_commit = git_out(["rev-parse", f"refs/tags/{tag}^{{commit}}"], root) if tag else ""
        check("tag", bool(tag_commit), tag or "no base version")
        check(
            "tag-commit",
            bool(tag_commit) and tag_commit == str(entry.get("commit", "")),
            f"{tag_commit[:12]} vs {str(entry.get('commit', ''))[:12]}",
        )
        if tag:
            verify = run(["git", "tag", "-v", tag], cwd=root, timeout=20)
            check("tag-signature", verify.returncode == 0, (verify.stderr or verify.stdout).strip()[:200])
        check(
            "worktree",
            not git_out(["status", "--porcelain"], root),
            "clean" if not git_out(["status", "--porcelain"], root) else "dirty",
        )

    for label in ("changes", "dsc", "buildinfo"):
        path_text = str(entry.get(label, ""))
        expected = str(entry.get(f"{label}Sha256", ""))
        if not path_text:
            continue
        path = Path(path_text)
        if not path.is_file():
            check(f"{label}-file", False, "not present on this host")
            continue
        actual = sha256_file(path)
        check(f"{label}-sha256", actual == expected, f"{actual[:16]}… vs {expected[:16]}…")

    from .config import profile_for
    from .launchpad import observe, status_for

    data = observe(force=False, config=config)
    profile = profile_for(package, config)
    status = status_for(data, str(profile.get("ppa", package)), package, series, version)
    check(
        "launchpad-source",
        bool(status.get("publishedVersion")),
        status.get("publishedVersion", "") or status.get("error", "no published source"),
    )
    check(
        "launchpad-build",
        status.get("buildState") in {"built", "unknown"},
        status.get("buildState", "") or "no build record",
    )
    check(
        "launchpad-version",
        status.get("publishedVersion", "").startswith(version.split("~")[0]) if version else False,
        status.get("publishedVersion", ""),
    )

    provenance = Path(f"/usr/share/doc/{package}/provenance.json")
    if provenance.is_file():
        installed = read_json(provenance, {}) or {}
        check("installed-provenance", installed.get("commit") == entry.get("commit"), str(provenance))
    else:
        checks.append({"name": "installed-provenance", "ok": True, "detail": "package not installed; skipped"})

    return {
        "package": package,
        "version": version,
        "series": series,
        "event": entry.get("event", ""),
        "recordedAt": entry.get("recordedAt", ""),
        "ok": all(item["ok"] for item in checks),
        "checks": checks,
    }


def verify_all(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_config()
    entries = read_ledger(config)
    latest = _latest(entries)
    results = [verify_entry(entry, config) for entry in latest]
    return {
        "entries": len(entries),
        "latest": len(latest),
        "ok": all(item["ok"] for item in results) if results else True,
        "results": results,
    }


def record_build(payload: dict[str, Any], config: dict[str, Any], commit: bool = False) -> Path:
    return append_event({"event": "built", **payload}, config=config, commit=commit)


def record_upload(payload: dict[str, Any], config: dict[str, Any], commit: bool = True) -> Path:
    return append_event({"event": "uploaded", **payload}, config=config, commit=commit)


def record_published(payload: dict[str, Any], config: dict[str, Any], commit: bool = True) -> Path:
    return append_event({"event": "published", **payload}, config=config, commit=commit)


def write_board_markdown(text: str, config: dict[str, Any] | None = None) -> Path:
    config = config or load_config()
    path = ledger_file(config).parent / "BOARD.md"
    write_text_atomic(path, text)
    return path
