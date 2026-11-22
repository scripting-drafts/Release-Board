"""Aggregate the release board from Git, Debian, Launchpad and the ledger."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import ledger_file, load_config, profile_for, roots
from .gitinfo import inspect_roots
from .launchpad import observe, status_for
from .packaging import inspect_packages
from .release import _next_revision, _published_base
from .trace import read_ledger, write_board_markdown
from .util import now_iso, series_version, split_version


def _latest_trace(entries: list[dict[str, Any]], package: str, base: str, series: str) -> dict[str, Any]:
    matches = [
        item
        for item in entries
        if item.get("package") == package
        and item.get("series") == series
        and str(item.get("version", "")).startswith(base)
    ]
    rank = {"built": 1, "uploaded": 2, "published": 3, "verified": 4}
    matches.sort(key=lambda item: (rank.get(str(item.get("event")), 0), str(item.get("recordedAt", ""))))
    return matches[-1] if matches else {}


def build_board(config: dict[str, Any] | None = None, refresh_launchpad: bool = False) -> dict[str, Any]:
    config = config or load_config()
    entries = roots(config)
    git_data = inspect_roots(entries, fetch=False)
    package_data = inspect_packages(entries, config)
    launchpad_data = observe(force=refresh_launchpad, config=config)
    ledger = read_ledger(config)

    git_by_path = {item.get("path"): item for item in git_data["roots"]}
    rows: list[dict[str, Any]] = []
    packages: list[dict[str, Any]] = []
    for package in package_data["packages"]:
        source = package.get("source", "")
        if not source:
            continue
        parsed = split_version(package.get("version", ""))
        profile = profile_for(source, config)
        git = git_by_path.get(str(Path(package.get("path", "")).resolve()), {})
        series_rows = []
        for series in profile.get("series", []):
            probe = status_for(launchpad_data, str(profile.get("ppa")), source, series, "")
            published_base = _published_base(
                probe.get("publishedVersion", ""), series, bool(profile.get("jammyNative"))
            )
            revision = parsed["revision"]
            if published_base == parsed["base"]:
                revision = _next_revision(
                    probe.get("publishedVersion", ""), parsed["base"], series, bool(profile.get("jammyNative"))
                )
            expected = series_version(parsed["base"], series, revision, bool(profile.get("jammyNative")))
            status = status_for(launchpad_data, str(profile.get("ppa")), source, series, expected)
            trace = _latest_trace(ledger, source, parsed["base"], series)
            row = {
                "package": source,
                "configuredName": package.get("configuredName", ""),
                "series": series,
                "untested": series in profile.get("untested", []),
                "expectedVersion": expected,
                "publishedVersion": status.get("publishedVersion", ""),
                "latestVersion": status.get("latestVersion", ""),
                "state": status.get("state", "unknown"),
                "buildState": status.get("buildState", ""),
                "uploadState": status.get("uploadState", ""),
                "sourceUrl": status.get("sourceUrl", ""),
                "dscUrl": status.get("dscUrl", ""),
                "buildUrl": status.get("buildUrl", ""),
                "logUrl": status.get("logUrl", ""),
                "commit": trace.get("commit") or "",
                "commitShort": str(trace.get("commit", ""))[:12],
                "headShort": str(git.get("head", {}).get("sha", ""))[:12],
                "tag": trace.get("tag") or f"v{parsed['base']}",
                "traceEvent": trace.get("event", ""),
                "traceAt": trace.get("recordedAt", ""),
                "traceVersion": trace.get("version", ""),
                "traceMatchesExpected": trace.get("version", "") == expected,
                "traceOk": bool(trace.get("commit")),
                "branch": git.get("branch", ""),
                "dirty": git.get("dirty", 0),
                "ahead": git.get("ahead", 0),
                "behind": git.get("behind", 0),
            }
            series_rows.append(row)
            rows.append(row)
        packages.append(
            {
                "name": package.get("configuredName", ""),
                "source": source,
                "path": package.get("path", ""),
                "version": package.get("version", ""),
                "baseVersion": parsed["base"],
                "distribution": package.get("distribution", ""),
                "ok": package.get("ok", False),
                "errors": package.get("errors", []),
                "warnings": package.get("warnings", []),
                "tag": package.get("tag", {}),
                "git": git,
                "ppa": package.get("ppa", ""),
                "profile": package.get("profile", {}),
                "control": package.get("control", {}),
                "sourceFormat": package.get("sourceFormat", ""),
                "versionFiles": package.get("versionFiles", []),
                "signingKey": package.get("signingKey", {}),
                "dput": package.get("dput", {}),
                "buildDeps": package.get("buildDeps", {}),
                "series": series_rows,
            }
        )

    summary = {
        "roots": len(entries),
        "packages": len(packages),
        "series": len(rows),
        "published": sum(1 for row in rows if row["state"] == "published"),
        "building": sum(1 for row in rows if row["state"] in {"building", "pending"}),
        "failed": sum(1 for row in rows if row["state"] == "failed"),
        "missing": sum(1 for row in rows if row["state"] == "missing"),
        "dirtyRoots": sum(1 for item in git_data["roots"] if item.get("isGit") and not item.get("clean")),
        "packagingErrors": sum(len(pkg.get("errors", [])) for pkg in packages),
        "traceRows": sum(1 for row in rows if row["traceOk"]),
        "launchpadFetchedAt": launchpad_data.get("fetchedAt", ""),
        "launchpadCached": bool(launchpad_data.get("cached")),
    }
    return {
        "generatedAt": now_iso(),
        "config": {
            "roots": entries,
            "ledger": str(ledger_file(config)),
            "launchpadUser": config.get("launchpadUser", ""),
        },
        "roots": git_data["roots"],
        "packages": packages,
        "rows": rows,
        "launchpad": launchpad_data,
        "ledger": {
            "entries": len(ledger),
            "latest": ledger[-10:],
        },
        "summary": summary,
    }


def render_markdown(board: dict[str, Any]) -> str:
    summary = board.get("summary", {})
    lines = [
        "# Release Board",
        "",
        f"Generated: {board.get('generatedAt', '')}",
        f"Roots: {summary.get('roots', 0)} · Packages: {summary.get('packages', 0)} · "
        f"Published: {summary.get('published', 0)} · Building: {summary.get('building', 0)} · "
        f"Failed: {summary.get('failed', 0)} · Missing: {summary.get('missing', 0)} · "
        f"Trace-backed: {summary.get('traceRows', 0)}",
        "",
        "## Roots",
        "",
        "| Root | Branch | State | Remote sync | HEAD |",
        "| --- | --- | --- | --- | --- |",
    ]
    for root in board.get("roots", []):
        if not root.get("isGit"):
            lines.append(f"| {root.get('configuredName') or root.get('name')} | — | missing | — | — |")
            continue
        sync = "clean" if root.get("clean") else f"{root.get('dirty', 0)} dirty"
        remote = f"+{root.get('ahead', 0)} / -{root.get('behind', 0)}"
        head = root.get("head", {}).get("short", "")
        lines.append(
            f"| {root.get('configuredName') or root.get('name')} | {root.get('branch', '')} | {sync} | {remote} | {head} |"
        )

    lines.extend(
        [
            "",
            "## Packages",
            "",
            "| Package | Series | Expected | Published | Upload | Build | Commit | Tag |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in board.get("rows", []):
        series = row.get("series", "")
        if row.get("untested"):
            series = f"{series} (untested)"
        lines.append(
            "| {package} | {series} | {expected} | {published} | {upload} | {build} | {commit} | {tag} |".format(
                package=row.get("package", ""),
                series=series,
                expected=row.get("expectedVersion", ""),
                published=row.get("publishedVersion", "") or "—",
                upload=row.get("uploadState", ""),
                build=row.get("buildState", "") or row.get("state", ""),
                commit=row.get("commitShort", "") or "—",
                tag=row.get("tag", ""),
            )
        )

    problems = []
    for package in board.get("packages", []):
        for error in package.get("errors", []):
            problems.append(f"- {package.get('source')}: {error}")
        for warning in package.get("warnings", []):
            problems.append(f"- {package.get('source')}: warning: {warning}")
    if problems:
        lines.extend(["", "## Preflight", ""])
        lines.extend(problems)

    lines.extend(
        [
            "",
            "## Evidence",
            "",
            f"Ledger: `{board.get('config', {}).get('ledger', '')}`",
            f"Launchpad snapshot: {summary.get('launchpadFetchedAt', '')}"
            f"{' (cached)' if summary.get('launchpadCached') else ''}",
            "",
        ]
    )
    return "\n".join(lines)


def write_board(board: dict[str, Any], config: dict[str, Any] | None = None) -> Path:
    config = config or load_config()
    return write_board_markdown(render_markdown(board), config)
