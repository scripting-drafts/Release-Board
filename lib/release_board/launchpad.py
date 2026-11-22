"""Read-only Launchpad PPA monitoring."""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .config import deployment_for, profile_for, roots
from .util import cache_dir, now_iso, read_json, write_json_atomic

API = "https://api.launchpad.net/1.0"
USER_AGENT = "release-board/0.9.17 (+local read-only monitor)"


def _fetch(url: str, timeout: int = 30) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _series(link: str) -> str:
    return link.rstrip("/").rsplit("/", 1)[-1] if link else ""


def _source_entry(entry: dict[str, Any], user: str, ppa: str, web_base: str = "") -> dict[str, Any]:
    source_id = str(entry.get("self_link", "")).rstrip("/").rsplit("/", 1)[-1]
    package = entry.get("source_package_name", "")
    version = entry.get("source_package_version", "")
    series = _series(entry.get("distro_series_link", ""))
    # +sourcepub is an API-only resource and returns 404 in a browser.
    # Point the UI at the public PPA package list and the signed .dsc instead.
    if not web_base:
        web_base = f"https://launchpad.net/~{user}/+archive/ubuntu"
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
        "url": f"{web_base}/{ppa}/+packages?{query}",
        "dscUrl": f"{web_base}/{ppa}/+files/{package}_{version}.dsc",
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


def _archive_url(user: str, ppa: str, operation: str, api_base: str = API) -> str:
    base = f"{api_base.rstrip('/')}/~{urllib.parse.quote(user)}/+archive/ubuntu/{urllib.parse.quote(ppa)}"
    return f"{base}?{urllib.parse.urlencode({'ws.op': operation, 'exact_match': 'true'})}"


def fetch_archive(user: str, ppa: str, api_base: str, web_base: str) -> dict[str, Any]:
    result: dict[str, Any] = {"ppa": ppa, "ok": False, "error": "", "sources": [], "builds": []}
    try:
        sources = _fetch(_archive_url(user, ppa, "getPublishedSources", api_base))
        builds = _fetch(_archive_url(user, ppa, "getBuildRecords", api_base))
        result["sources"] = [_source_entry(item, user, ppa, web_base) for item in sources.get("entries", [])]
        result["builds"] = [_build_entry(item, user, ppa) for item in builds.get("entries", [])]
        result["ok"] = True
    except Exception as exc:  # network errors are reported, never fatal
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _expand_base(template: str, user: str) -> str:
    try:
        return template.format(user=user)
    except (KeyError, ValueError):
        return template


def _deployment_target(name: str | None, config: dict[str, Any]) -> dict[str, str]:
    deployment = deployment_for(name, config)
    environment = str(name or "launchpad")
    user = str(deployment.get("user") or config.get("launchpadUser", "toomanypatterns"))
    api_base = str(deployment.get("api") or API)
    archive_base = _expand_base(
        str(deployment.get("archive") or "https://ppa.launchpadcontent.net/{user}"), user
    )
    web_base = _expand_base(
        str(deployment.get("web") or "https://launchpad.net/~{user}/+archive/ubuntu"), user
    ).rstrip("/")
    return {
        "environment": environment,
        "user": user,
        "api": api_base,
        "archive": archive_base,
        "web": web_base,
    }


def _targets(config: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Return the deployment target for every monitored PPA."""
    targets: dict[str, dict[str, str]] = {}
    for entry in roots(config):
        source = _detect_source(Path(entry["path"]), config)
        ppa = str(profile_for(source, config).get("ppa") or "")
        if ppa:
            targets.setdefault(ppa, _deployment_target(entry.get("deployment"), config))
    default_target = _deployment_target("launchpad", config)
    for known in ("qa-dashboard", "qa-sentinels", "ubuntu-backup"):
        targets.setdefault(known, default_target)
    return targets


def observe(force: bool = False, config: dict[str, Any] | None = None) -> dict[str, Any]:
    from .config import load_config

    config = config or load_config()
    targets = _targets(config)
    ppas = sorted(targets)
    default_target = _deployment_target("launchpad", config)
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
    target_signature = {ppa: targets[ppa] for ppa in ppas}
    same_targets = cached.get("targets") == target_signature
    if (
        not force
        and same_targets
        and cached_archives
        and age < ttl
        and all(ppa in cached_archives for ppa in ppas)
    ):
        return cached | {"cached": True, "ageSeconds": int(age)}

    stale_all = force or not same_targets or age >= ttl
    archives = (
        {ppa: cached_archives[ppa] for ppa in ppas if ppa in cached_archives}
        if not force
        else {}
    )
    for ppa in ppas:
        target = targets[ppa]
        if stale_all or ppa not in archives or not archives[ppa].get("ok"):
            archive = fetch_archive(target["user"], ppa, target["api"], target["web"])
            archive["environment"] = target["environment"]
            archive["user"] = target["user"]
            archives[ppa] = archive
    payload = {
        "schema": 2,
        "user": default_target["user"],
        "deployment": {
            "environment": default_target["environment"],
            "user": default_target["user"],
            "api": default_target["api"],
            "archive": default_target["archive"],
            "web": default_target["web"],
        },
        "targets": target_signature,
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
            status["untested"] = series in profile.get("untested", [])
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
