"""Read-only Launchpad PPA monitoring."""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .config import profile_for, roots
from .util import cache_dir, now_iso, read_json, write_json_atomic

API = "https://api.launchpad.net/1.0"
USER_AGENT = "release-board/0.5.1 (+local read-only monitor)"


def _fetch(url: str, timeout: int = 30) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _series(link: str) -> str:
    return link.rstrip("/").rsplit("/", 1)[-1] if link else ""


def _source_entry(entry: dict[str, Any], user: str, ppa: str) -> dict[str, Any]:
    source_id = str(entry.get("self_link", "")).rstrip("/").rsplit("/", 1)[-1]
    package = entry.get("source_package_name", "")
    version = entry.get("source_package_version", "")
    series = _series(entry.get("distro_series_link", ""))
    # +sourcepub is an API-only resource and returns 404 in a browser.
    # Point the UI at the public PPA package list and the signed .dsc instead.
    query = urllib.parse.urlencode(
        {
            "field.name_filter": package,
            "field.status_filter": "published",
            "field.series_filter": series,
        }
    )
    return {
        "id": source_id,
        "series": series,
        "package": package,
        "version": version,
        "status": entry.get("status", ""),
        "pocket": entry.get("pocket", ""),
        "created": entry.get("date_created", ""),
        "published": entry.get("date_published", ""),
        "url": f"https://launchpad.net/~{user}/+archive/ubuntu/{ppa}/+packages?{query}",
        "dscUrl": f"https://launchpad.net/~{user}/+archive/ubuntu/{ppa}/+files/{package}_{version}.dsc",
        "apiUrl": entry.get("self_link", ""),
        "uploadId": str(entry.get("packageupload_link", "")).rstrip("/").rsplit("/", 1)[-1],
    }


def _build_entry(entry: dict[str, Any], user: str, ppa: str) -> dict[str, Any]:
    build_id = str(entry.get("self_link", "")).rstrip("/").rsplit("/", 1)[-1]
    return {
        "id": build_id,
        "series": _series(entry.get("distro_series_link", "")),
        "arch": entry.get("arch_tag", ""),
        "package": entry.get("source_package_name", ""),
        "version": entry.get("source_package_version", ""),
        "state": entry.get("buildstate", ""),
        "created": entry.get("datecreated", ""),
        "built": entry.get("datebuilt", ""),
        "firstDispatched": entry.get("date_first_dispatched", ""),
        "url": entry.get("web_link", "") or f"https://launchpad.net/~{user}/+archive/ubuntu/{ppa}/+build/{build_id}",
        "logUrl": entry.get("build_log_url", ""),
        "changesUrl": entry.get("changesfile_url", ""),
        "buildinfoUrl": entry.get("buildinfo_url", ""),
    }


def _archive_url(user: str, ppa: str, operation: str) -> str:
    base = f"{API}/~{urllib.parse.quote(user)}/+archive/ubuntu/{urllib.parse.quote(ppa)}"
    return f"{base}?{urllib.parse.urlencode({'ws.op': operation, 'exact_match': 'true'})}"


def fetch_archive(user: str, ppa: str) -> dict[str, Any]:
    result: dict[str, Any] = {"ppa": ppa, "ok": False, "error": "", "sources": [], "builds": []}
    try:
        sources = _fetch(_archive_url(user, ppa, "getPublishedSources"))
        builds = _fetch(_archive_url(user, ppa, "getBuildRecords"))
        result["sources"] = [_source_entry(item, user, ppa) for item in sources.get("entries", [])]
        result["builds"] = [_build_entry(item, user, ppa) for item in builds.get("entries", [])]
        result["ok"] = True
    except Exception as exc:  # network errors are reported, never fatal
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def observe(force: bool = False, config: dict[str, Any] | None = None) -> dict[str, Any]:
    from .config import load_config

    config = config or load_config()
    user = str(config.get("launchpadUser", "toomanypatterns"))
    ppas = sorted(
        {
            str(profile_for(_detect_source(Path(entry["path"]), config), config).get("ppa"))
            for entry in roots(config)
        }
    )
    ppas = [ppa for ppa in ppas if ppa]
    for known in ("qa-dashboard", "qa-sentinels", "ubuntu-backup"):
        if known not in ppas:
            ppas.append(known)
    cache_path = cache_dir() / "launchpad.json"
    cached = read_json(cache_path, {}) or {}
    age = 10**9
    if cached.get("fetchedAt"):
        try:
            from datetime import datetime

            fetched = datetime.fromisoformat(cached["fetchedAt"])
            age = time.time() - fetched.timestamp()
        except ValueError:
            age = 10**9
    ttl = max(15, int(config.get("refreshSeconds", 60)))
    cached_archives = cached.get("archives") or {}
    if not force and cached_archives and age < ttl and all(ppa in cached_archives for ppa in ppas):
        return cached | {"cached": True, "ageSeconds": int(age)}

    archives = dict(cached.get("archives", {})) if not force else {}
    for ppa in ppas:
        if force or ppa not in archives or not archives[ppa].get("ok"):
            archives[ppa] = fetch_archive(user, ppa)
    payload = {
        "schema": 1,
        "user": user,
        "fetchedAt": now_iso(),
        "archives": archives,
        "cached": False,
        "ageSeconds": 0,
    }
    write_json_atomic(cache_path, payload)
    return payload


def _detect_source(root: Path, config: dict[str, Any]) -> str:
    from .util import parse_dpkg_changelog

    changelog = parse_dpkg_changelog(root)
    return changelog.get("source", root.name) if changelog else root.name


def _state_from_builds(builds: list[dict[str, Any]]) -> str:
    states = [item["state"] for item in builds]
    if not states:
        return "unknown"
    if any("Failed" in state or "Cancelled" in state or "Superseded" in state for state in states):
        return "failed"
    if any("Currently building" in state or "Uploading" in state for state in states):
        return "building"
    if any("Needs building" in state or "Dependency wait" in state for state in states):
        return "pending"
    if all("Successfully built" in state for state in states):
        return "built"
    if any("Successfully built" in state for state in states):
        return "building"
    return "unknown"


def status_for(
    data: dict[str, Any],
    ppa: str,
    package: str,
    series: str,
    expected_version: str = "",
) -> dict[str, Any]:
    archive = (data.get("archives") or {}).get(ppa, {})
    sources = [item for item in archive.get("sources", []) if item.get("package") == package and item.get("series") == series]
    sources.sort(key=lambda item: item.get("created", ""), reverse=True)
    latest_source = sources[0] if sources else None
    published_sources = [item for item in sources if item.get("status") == "Published"]
    published_source = published_sources[0] if published_sources else None
    version = (published_source or latest_source or {}).get("version", "")
    builds = [
        item
        for item in archive.get("builds", [])
        if item.get("package") == package and item.get("series") == series and (not version or item.get("version") == version)
    ]
    builds.sort(key=lambda item: item.get("created", ""), reverse=True)
    build_state = _state_from_builds(builds)
    upload_state = "uploaded" if latest_source else "missing"
    if latest_source and latest_source.get("status") == "Pending":
        upload_state = "pending"
    elif latest_source and latest_source.get("status") == "Published":
        upload_state = "published"
    elif latest_source and latest_source.get("status") in {"Superseded", "Deleted"}:
        upload_state = latest_source["status"].lower()
    state = build_state if build_state != "unknown" else upload_state
    if published_source and build_state in {"built", "unknown"}:
        state = "published"
    return {
        "ppa": ppa,
        "package": package,
        "series": series,
        "expectedVersion": expected_version,
        "latestVersion": (latest_source or {}).get("version", ""),
        "publishedVersion": (published_source or {}).get("version", ""),
        "uploadState": upload_state,
        "buildState": build_state,
        "state": state,
        "source": latest_source,
        "publishedSource": published_source,
        "builds": builds,
        "sourceUrl": (latest_source or {}).get("url", ""),
        "dscUrl": (latest_source or {}).get("dscUrl", ""),
        "buildUrl": builds[0].get("url", "") if builds else "",
        "logUrl": next((item.get("logUrl") for item in builds if item.get("logUrl")), ""),
        "error": archive.get("error", ""),
    }


def summary(data: dict[str, Any], entries: list[dict[str, str]], config: dict[str, Any]) -> dict[str, Any]:
    from .util import parse_dpkg_changelog, series_version, split_version

    packages = []
    for entry in entries:
        root = Path(entry["path"])
        changelog = parse_dpkg_changelog(root)
        if not changelog:
            continue
        source = changelog.get("source", "")
        parsed = split_version(changelog.get("version", ""))
        profile = profile_for(source, config)
        series_rows = []
        for series in profile.get("series", []):
            expected = series_version(
                parsed["base"], series, parsed["revision"], bool(profile.get("jammyNative"))
            )
            status = status_for(data, str(profile.get("ppa")), source, series, expected)
            status["expectedVersion"] = expected
            status["matchesExpected"] = (
                bool(status["publishedVersion"]) and status["publishedVersion"].startswith(expected)
            )
            series_rows.append(status)
        packages.append(
            {
                "name": entry["name"],
                "source": source,
                "path": str(root),
                "series": series_rows,
            }
        )
    return {
        "fetchedAt": data.get("fetchedAt", ""),
        "cached": data.get("cached", False),
        "packages": packages,
        "summary": {
            "published": sum(1 for pkg in packages for row in pkg["series"] if row["state"] == "published"),
            "building": sum(1 for pkg in packages for row in pkg["series"] if row["state"] in {"building", "pending"}),
            "failed": sum(1 for pkg in packages for row in pkg["series"] if row["state"] == "failed"),
            "missing": sum(1 for pkg in packages for row in pkg["series"] if row["state"] == "missing"),
        },
    }
