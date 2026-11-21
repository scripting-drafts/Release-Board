"""Debian metadata inspection and release preflight checks."""

from __future__ import annotations

import os
import re
from email.parser import Parser
from pathlib import Path
from typing import Any

from .config import profile_for
from .util import git_out, parse_dpkg_changelog, run, split_version


def parse_control(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace")
    source = Parser().parsestr(text)
    source_fields = {key.lower(): str(value).strip() for key, value in source.items()}
    binaries = []
    for block in re.split(r"\n\s*\n", text):
        if not re.search(r"^Package:", block, re.MULTILINE):
            continue
        message = Parser().parsestr(block, headersonly=True)
        fields = {key.lower(): str(value).strip() for key, value in message.items()}
        binaries.append(
            {
                "package": fields.get("package", ""),
                "architecture": fields.get("architecture", ""),
                "depends": fields.get("depends", ""),
                "recommends": fields.get("recommends", ""),
                "description": fields.get("description", "").splitlines()[0] if fields.get("description") else "",
            }
        )
    return {
        "source": source_fields.get("source", ""),
        "section": source_fields.get("section", ""),
        "priority": source_fields.get("priority", ""),
        "maintainer": source_fields.get("maintainer", ""),
        "buildDepends": source_fields.get("build-depends", ""),
        "standardsVersion": source_fields.get("standards-version", ""),
        "homepage": source_fields.get("homepage", ""),
        "binaries": binaries,
    }


def _dput_sections() -> set[str]:
    sections: set[str] = set()
    paths = [Path("/etc/dput.cf"), Path.home() / ".dput.cf"]
    for path in paths:
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                match = re.match(r"^\[([^\]]+)\]", line.strip())
                if match:
                    sections.add(match.group(1))
        except OSError:
            continue
    return sections


def _supported_series() -> set[str]:
    proc = run(["ubuntu-distro-info", "--supported"], timeout=20)
    if proc.returncode != 0:
        return set()
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def _signing_key_ok(key: str) -> tuple[bool, str]:
    if not key:
        return False, "no signing key configured"
    proc = run(["gpg", "--batch", "--list-secret-keys", "--with-colons", key], timeout=20)
    if proc.returncode != 0:
        return False, "secret key not found"
    has_secret = any(line.startswith("sec:") for line in proc.stdout.splitlines())
    return has_secret, "secret key present" if has_secret else "no secret key"


def _version_file_status(root: Path, files: list[str], base: str) -> list[dict[str, Any]]:
    result = []
    for name in files:
        path = root / name
        if not path.is_file():
            result.append({"file": name, "exists": False, "matches": False})
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        result.append({"file": name, "exists": True, "matches": base in text})
    return result


def inspect_package(root: Path, config: dict[str, Any]) -> dict[str, Any]:
    root = root.expanduser().resolve()
    result: dict[str, Any] = {
        "path": str(root),
        "name": root.name,
        "errors": [],
        "warnings": [],
        "ok": False,
    }
    changelog = parse_dpkg_changelog(root)
    if not changelog:
        result["errors"].append("debian/changelog is missing or unreadable")
        return result

    source = changelog.get("source", "")
    version = split_version(changelog.get("version", ""))
    profile = profile_for(source, config)
    control = parse_control(root / "debian" / "control")
    source_format = ""
    format_path = root / "debian" / "source" / "format"
    if format_path.is_file():
        source_format = format_path.read_text(encoding="utf-8").strip()

    result.update(
        {
            "source": source,
            "version": version["version"],
            "baseVersion": version["base"],
            "revision": version["revision"],
            "distribution": changelog.get("distribution", ""),
            "urgency": changelog.get("urgency", ""),
            "maintainer": changelog.get("maintainer", ""),
            "date": changelog.get("date", ""),
            "changes": changelog.get("changes", ""),
            "control": control,
            "sourceFormat": source_format,
            "profile": profile,
            "profileName": source,
            "ppa": profile.get("ppa", source),
            "series": profile.get("series", []),
            "jammyNative": bool(profile.get("jammyNative")),
            "versionFiles": _version_file_status(root, list(profile.get("versionFiles", [])), version["base"]),
        }
    )

    if not source:
        result["errors"].append("changelog has no Source field")
    if not control:
        result["errors"].append("debian/control is missing or unreadable")
    if control.get("source") and control["source"] != source:
        result["errors"].append(f"control Source {control['source']} does not match changelog {source}")
    if control.get("maintainer") and changelog.get("maintainer") and control["maintainer"] != changelog["maintainer"]:
        result["warnings"].append("control Maintainer differs from changelog Maintainer")

    if source_format:
        if source_format not in {"3.0 (quilt)", "3.0 (native)"}:
            result["warnings"].append(f"unexpected source format: {source_format}")
    else:
        result["warnings"].append("debian/source/format is missing")

    rules = root / "debian" / "rules"
    if not rules.is_file():
        result["errors"].append("debian/rules is missing")
    elif not os.access(rules, os.X_OK):
        result["warnings"].append("debian/rules is not executable")

    series = list(profile.get("series", []))
    supported = _supported_series()
    if supported:
        unknown = [item for item in series if item not in supported]
        if unknown:
            result["warnings"].append(f"series not currently supported by ubuntu-distro-info: {', '.join(unknown)}")
    if changelog.get("distribution") and changelog["distribution"] not in series:
        result["warnings"].append(
            f"changelog distribution {changelog['distribution']} is not in the profile series"
        )

    dput_sections = _dput_sections()
    result["dput"] = {
        "configured": bool(dput_sections) and profile.get("ppa") in dput_sections,
        "sections": sorted(dput_sections),
        "target": profile.get("ppa", ""),
    }
    if dput_sections and profile.get("ppa") not in dput_sections:
        result["warnings"].append(f"dput target {profile.get('ppa')} is not configured in dput.cf")
    elif not dput_sections:
        result["warnings"].append("no dput.cf found; upload target cannot be verified")

    key_ok, key_message = _signing_key_ok(str(config.get("signKey", "")))
    result["signingKey"] = {"present": key_ok, "message": key_message}
    if not key_ok:
        result["warnings"].append(f"signing key: {key_message}")

    for item in result["versionFiles"]:
        if not item["exists"]:
            result["warnings"].append(f"version file missing: {item['file']}")
        elif not item["matches"]:
            result["errors"].append(f"version file does not mention {version['base']}: {item['file']}")

    for need in profile.get("sourceNeeds", []):
        if not (root / need).exists():
            result["warnings"].append(f"source build input missing: {need}")

    tag_name = f"v{version['base']}"
    tag_sha = git_out(["rev-parse", "-q", "--verify", f"refs/tags/{tag_name}"], root)
    tag_head = git_out(["rev-parse", "HEAD"], root)
    tag_commit = git_out(["rev-parse", f"{tag_name}^{{commit}}"], root) if tag_sha else ""
    tag_verified = False
    tag_message = ""
    if tag_sha:
        verify = run(["git", "tag", "-v", tag_name], cwd=root, timeout=20)
        tag_verified = verify.returncode == 0
        tag_message = (verify.stderr or verify.stdout).strip().splitlines()[0] if verify.returncode != 0 else "signed"
    result["tag"] = {
        "name": tag_name,
        "present": bool(tag_sha),
        "signed": tag_verified,
        "pointsAtHead": bool(tag_commit and tag_head and tag_commit == tag_head),
        "commit": tag_commit,
        "message": tag_message,
    }
    if not tag_sha:
        result["warnings"].append(f"release tag {tag_name} is missing")
    elif not tag_verified:
        result["warnings"].append(f"release tag {tag_name} is not verified: {tag_message}")
    elif tag_commit != tag_head:
        result["warnings"].append(f"release tag {tag_name} does not point at HEAD")

    deps = run(["dpkg-checkbuilddeps"], cwd=root, timeout=60)
    result["buildDeps"] = {
        "ok": deps.returncode == 0,
        "message": (deps.stderr or deps.stdout).strip(),
    }
    if deps.returncode != 0:
        result["warnings"].append(f"build dependencies: {result['buildDeps']['message']}")

    result["ok"] = not result["errors"]
    return result


def inspect_packages(entries: list[dict[str, str]], config: dict[str, Any]) -> dict[str, Any]:
    packages = []
    for entry in entries:
        info = inspect_package(Path(entry["path"]), config)
        info["configuredName"] = entry["name"]
        packages.append(info)
    return {
        "packages": packages,
        "summary": {
            "total": len(packages),
            "ok": sum(1 for item in packages if item.get("ok")),
            "errors": sum(len(item.get("errors", [])) for item in packages),
            "warnings": sum(len(item.get("warnings", [])) for item in packages),
        },
    }
