"""Lifecycle operations: check, test, build source, upload, watch."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from .config import load_config, profile_for
from .gitinfo import inspect_root
from .launchpad import observe, status_for
from .packaging import inspect_package
from . import __version__
from .slsa import create_attestation, verify_attestation
from .trace import read_ledger, record_build, record_published, record_upload
from .util import (
    APP_ROOT,
    ensure_dir,
    git_out,
    now_iso,
    run,
    series_version,
    sha256_file,
    split_version,
    state_dir,
    write_json_atomic,
)


def _series_list(profile: dict[str, Any], selected: list[str] | None = None) -> list[str]:
    if selected:
        result = []
        for item in selected:
            result.extend(part for part in re.split(r"[,\s]+", item) if part)
        return result
    return list(profile.get("series", []))


def _ledger_entries(config: dict[str, Any]) -> list[dict[str, Any]]:
    return read_ledger(config)


def _published_matches(published: str, base: str, series: str, jammy_native: bool) -> bool:
    if not published:
        return False
    if jammy_native and series == "jammy":
        return published == base
    return published.startswith(f"{base}-0ubuntu") and published.endswith(f"~{series}1")


def _published_base(published: str, series: str, jammy_native: bool) -> str:
    if not published:
        return ""
    if jammy_native and series == "jammy":
        return published
    match = re.match(r"^(.*)-0ubuntu\d+~" + re.escape(series) + r"1$", published)
    return match.group(1) if match else published


def _published_revision(published: str) -> int:
    match = re.search(r"-0ubuntu(\d+)~", published or "")
    return int(match.group(1)) if match else 1


def _next_revision(published: str, base: str, series: str, jammy_native: bool) -> int:
    published_base = _published_base(published, series, jammy_native)
    if published_base != base:
        return 1
    return _published_revision(published) + 1


def _dpkg_compare(left: str, right: str) -> int | None:
    if not left or not right:
        return None
    if run(["dpkg", "--compare-versions", left, "eq", right], timeout=10).returncode == 0:
        return 0
    if run(["dpkg", "--compare-versions", left, "lt", right], timeout=10).returncode == 0:
        return -1
    return 1


def check_release(root: Path, config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_config()
    root = root.expanduser().resolve()
    git = inspect_root(root)
    package = inspect_package(root, config)
    blockers: list[str] = []
    notes: list[str] = []

    if not git.get("isGit"):
        blockers.append("not a Git repository")
    else:
        if git.get("dirty"):
            blockers.append(f"working tree is dirty ({git['dirty']} file(s))")
        if git.get("branch") != "main":
            blockers.append(f"release from main, not {git.get('branch') or 'detached HEAD'}")
        if git.get("ahead") or git.get("behind"):
            blockers.append(f"branch is not in sync with {git.get('upstream') or 'upstream'} (behind {git.get('behind')}, ahead {git.get('ahead')})")

    if package.get("errors"):
        blockers.extend(package["errors"])
    if package.get("warnings"):
        notes.extend(
            warning
            for warning in package["warnings"]
            if not warning.startswith("release tag") and not warning.startswith("signing key")
        )
    if not package.get("dput", {}).get("configured"):
        blockers.append(f"dput target {package.get('ppa', '?')} is not configured")
    if not package.get("signingKey", {}).get("present"):
        blockers.append(f"signing key: {package.get('signingKey', {}).get('message', 'not available')}")
    tag_name = f"v{package.get('baseVersion', '?')}"
    if not package.get("tag", {}).get("present"):
        blockers.append(f"release tag {tag_name} is missing")
    elif not package.get("tag", {}).get("signed"):
        blockers.append(f"release tag {tag_name} is not signed")
    elif not package.get("tag", {}).get("pointsAtHead"):
        blockers.append(f"release tag {tag_name} does not point at HEAD")
    elif git.get("isGit"):
        remote_tag = run(["git", "ls-remote", "--tags", "origin", f"refs/tags/{tag_name}"], cwd=root, timeout=30)
        if remote_tag.returncode != 0 or not remote_tag.stdout.strip():
            blockers.append(f"release tag {tag_name} is not pushed to origin")

    profile = profile_for(package.get("source", root.name), config)
    parsed = split_version(package.get("version", ""))
    launchpad_rows = []
    try:
        data = observe(config=config)
    except Exception as exc:
        data = {"archives": {}, "error": str(exc)}
        notes.append(f"Launchpad unavailable: {exc}")
    for series in profile.get("series", []):
        jammy_native = bool(profile.get("jammyNative"))
        published = status_for(data, str(profile.get("ppa")), package.get("source", ""), series, "").get(
            "publishedVersion", ""
        )
        published_base = _published_base(published, series, jammy_native)
        comparison = _dpkg_compare(published_base, parsed["base"])
        revision = parsed["revision"]
        if comparison == 0:
            revision = _next_revision(published, parsed["base"], series, jammy_native)
        expected = series_version(parsed["base"], series, revision, jammy_native)
        status = status_for(data, str(profile.get("ppa")), package.get("source", ""), series, expected)
        status["expectedVersion"] = expected
        status["publishedBase"] = published_base
        status["comparison"] = comparison
        if comparison is not None and comparison > 0:
            blockers.append(
                f"{series}: published {published} is newer than local base {parsed['base']}"
            )
        elif comparison == 0:
            status["state"] = "published"
            if jammy_native and series == "jammy":
                notes.append(f"{series}: already published {published}; bump the base version to release again")
            else:
                notes.append(f"{series}: already published {published}; next local revision is {revision}")
        elif status.get("state") == "failed" and status.get("latestVersion", "").startswith(parsed["base"]):
            blockers.append(f"{series}: Launchpad build failed for {status.get('latestVersion')}")
        launchpad_rows.append(status)

    return {
        "ok": not blockers,
        "root": str(root),
        "package": package,
        "git": git,
        "launchpad": launchpad_rows,
        "blockers": blockers,
        "notes": notes,
        "checkedAt": now_iso(),
    }


def run_tests(root: Path, config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_config()
    root = root.expanduser().resolve()
    package = inspect_package(root, config)
    profile = profile_for(package.get("source", root.name), config)
    command = list(profile.get("test", []))
    if not command:
        return {"ok": True, "command": [], "output": "no test command configured", "returncode": 0}
    proc = run(command, cwd=root, timeout=1800)
    return {
        "ok": proc.returncode == 0,
        "command": command,
        "output": (proc.stdout + proc.stderr).strip(),
        "returncode": proc.returncode,
    }


def ensure_tag(root: Path, base: str, config: dict[str, Any], message: str = "") -> dict[str, Any]:
    root = root.expanduser().resolve()
    tag = f"v{base}"
    existing = git_out(["rev-parse", "-q", "--verify", f"refs/tags/{tag}"], root)
    if existing:
        verify = run(["git", "tag", "-v", tag], cwd=root, timeout=20)
        commit = git_out(["rev-parse", f"{tag}^{{commit}}"], root)
        head = git_out(["rev-parse", "HEAD"], root)
        return {
            "ok": verify.returncode == 0 and commit == head,
            "tag": tag,
            "created": False,
            "signed": verify.returncode == 0,
            "pointsAtHead": commit == head,
            "message": (verify.stderr or verify.stdout).strip()[:300],
        }
    key = str(config.get("signKey", ""))
    args = ["git", "tag", "-s", "-a", tag, "-m", message or f"Release {tag}"]
    if key:
        args.extend(["-u", key])
    proc = run(args, cwd=root, timeout=120)
    commit = git_out(["rev-parse", f"{tag}^{{commit}}"], root)
    head = git_out(["rev-parse", "HEAD"], root)
    return {
        "ok": proc.returncode == 0,
        "tag": tag,
        "created": proc.returncode == 0,
        "signed": proc.returncode == 0,
        "pointsAtHead": commit == head,
        "message": (proc.stderr or proc.stdout).strip()[:300],
    }


def _rewrite_changelog(path: Path, package: str, version: str, series: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise RuntimeError(f"empty changelog: {path}")
    first = lines[0]
    first = re.sub(r"^(\S+) \([^)]*\)", f"{package} ({version})", first, count=1)
    first = re.sub(r"\)\s+\S+;", f") {series};", first, count=1)
    lines[0] = first
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _prepare_tree(root: Path, series: str, revision: int, config: dict[str, Any]) -> tuple[Path, Path, dict[str, Any]]:
    package = inspect_package(root, config)
    if package.get("errors"):
        raise RuntimeError("; ".join(package["errors"]))
    source = package["source"]
    base = package["baseVersion"]
    profile = profile_for(source, config)
    version = series_version(base, series, revision, bool(profile.get("jammyNative")))
    work = ensure_dir(state_dir() / "builds" / source / version / series)
    src = work / "src"
    if src.exists():
        shutil.rmtree(src)
    ensure_dir(src)

    prefix = git_out(["rev-parse", "--show-prefix"], root).strip()
    archive_ref = f"HEAD:{prefix.rstrip('/')}" if prefix else "HEAD"
    tar_path = work / "source.tar"
    archive = run(["git", "archive", "--format=tar", "-o", str(tar_path), archive_ref], cwd=root, timeout=120)
    if archive.returncode != 0:
        raise RuntimeError(f"git archive failed: {archive.stderr.strip()}")
    extract = run(["tar", "-x", "-C", str(src), "-f", str(tar_path)], timeout=120)
    if extract.returncode != 0:
        raise RuntimeError(f"tar extraction failed: {extract.stderr.strip()}")

    for need in profile.get("sourceNeeds", []):
        source_path = root / need
        target_path = src / need
        if source_path.exists():
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if source_path.is_dir():
                shutil.copytree(source_path, target_path, symlinks=True)
            else:
                shutil.copy2(source_path, target_path)

    _rewrite_changelog(src / "debian" / "changelog", source, version, series)

    head = git_out(["rev-parse", "HEAD"], root)
    tree = git_out(["rev-parse", "HEAD^{tree}"], root)
    tag = f"v{base}"
    tag_object = git_out(["rev-parse", tag], root)
    tag_commit = git_out(["rev-parse", f"{tag}^{{commit}}"], root) if tag_object else ""
    provenance = {
        "schema": 1,
        "package": source,
        "version": version,
        "baseVersion": base,
        "revision": revision,
        "series": series,
        "commit": head,
        "tree": tree,
        "tag": tag,
        "tagObject": tag_object,
        "tagCommit": tag_commit,
        "tagVerified": bool(tag_commit and tag_commit == head and run(["git", "tag", "-v", tag], cwd=root, timeout=20).returncode == 0),
        "branch": git_out(["rev-parse", "--abbrev-ref", "HEAD"], root),
        "remote": git_out(["remote", "get-url", "origin"], root),
        "clean": not git_out(["status", "--porcelain"], root),
        "builtAt": now_iso(),
        "builder": f"release-board/{__version__}",
    }
    write_json_atomic(src / "debian" / "provenance.json", provenance, 0o644)

    binary = str(profile.get("binaryPackage", source))
    install_file = src / "debian" / f"{binary}.install"
    provenance_line = f"debian/provenance.json usr/share/doc/{binary}/"
    if install_file.exists():
        existing = install_file.read_text(encoding="utf-8", errors="replace")
        if provenance_line not in existing:
            install_file.write_text(existing.rstrip() + "\n" + provenance_line + "\n", encoding="utf-8")
    else:
        install_file.write_text(provenance_line + "\n", encoding="utf-8")
    return src, work, provenance


def _revision_for(
    series: str,
    base: str,
    jammy_native: bool,
    data: dict[str, Any] | None,
    ppa: str,
    package: str,
    explicit: int | None,
) -> int:
    if explicit is not None:
        return explicit
    if not data:
        return 1
    status = status_for(data, ppa, package, series, "")
    published = status.get("publishedVersion", "")
    if not published:
        return 1
    return _next_revision(published, base, series, jammy_native)


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.~-]+", "_", value).strip("_") or "unknown"


def store_attestation(attestation: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    repo = Path(config.get("ledgerRepo", APP_ROOT)).expanduser()
    if not (repo / ".git").exists():
        return attestation
    relative = (
        Path("release")
        / "attestations"
        / _safe_component(str(attestation.get("package", "")))
        / _safe_component(str(attestation.get("version", "")))
        / _safe_component(str(attestation.get("series", "")))
    )
    destination = repo / relative
    ensure_dir(destination)
    stored = dict(attestation)
    for key, suffix in (("attestation", ".intoto.jsonl"), ("statement", ".slsa.json")):
        source = Path(str(attestation.get(key, "")))
        if source.is_file():
            target = destination / source.name
            shutil.copy2(source, target)
            stored[key] = str(target)
            stored[f"{key}Sha256"] = sha256_file(target)
    stored["stored"] = True
    return stored


def build_source(
    root: Path,
    series_list: list[str] | None = None,
    revision: int | None = None,
    config: dict[str, Any] | None = None,
    tag: bool = False,
) -> dict[str, Any]:
    config = config or load_config()
    root = root.expanduser().resolve()
    package = inspect_package(root, config)
    profile = profile_for(package.get("source", root.name), config)
    series_values = _series_list(profile, series_list)
    if not series_values:
        return {"ok": False, "error": "no series selected", "builds": []}
    if not str(config.get("signKey", "")).strip() and not config.get("allowUnsignedBuilds"):
        return {"ok": False, "error": "no signing key configured; set signKey or allowUnsignedBuilds for local tests", "builds": []}
    if not config.get("allowDirtyBuilds"):
        dirty = git_out(["status", "--porcelain"], root)
        if dirty:
            return {"ok": False, "error": "working tree is dirty; commit or stash before building a release", "builds": []}
    branch = git_out(["rev-parse", "--abbrev-ref", "HEAD"], root)
    if branch != "main":
        return {"ok": False, "error": f"release builds must run from main, not {branch or 'detached HEAD'}", "builds": []}

    if tag:
        tag_result = ensure_tag(root, package.get("baseVersion", ""), config)
        if not tag_result.get("ok"):
            return {"ok": False, "error": f"tag failed: {tag_result.get('message', '')}", "tag": tag_result, "builds": []}
    else:
        existing = package.get("tag", {})
        if not existing.get("present") or not existing.get("signed") or not existing.get("pointsAtHead"):
            return {
                "ok": False,
                "error": f"release tag v{package.get('baseVersion', '?')} must exist, be signed and point at HEAD; run tag or build with --tag",
                "builds": [],
            }

    try:
        launchpad_data = observe(force=True, config=config)
    except Exception:
        launchpad_data = None
    results = []
    for series in series_values:
        try:
            series_revision = _revision_for(
                series,
                package.get("baseVersion", ""),
                bool(profile.get("jammyNative")),
                launchpad_data,
                str(profile.get("ppa")),
                str(package.get("source", "")),
                revision,
            )
            src, work, provenance = _prepare_tree(root, series, series_revision, config)
            env = {"XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")}
            key = str(config.get("signKey", "")).strip()
            command = ["dpkg-buildpackage", "-S", "-sa"]
            command.extend([f"-k{key}"] if key else ["-us", "-uc"])
            started_on = now_iso()
            proc = run(command, cwd=src, timeout=3600, env=env)
            finished_on = now_iso()
            version = provenance["version"]
            changes = work / f"{provenance['package']}_{version}_source.changes"
            dsc = work / f"{provenance['package']}_{version}.dsc"
            buildinfo = work / f"{provenance['package']}_{version}_source.buildinfo"
            artifacts = sorted(
                item
                for item in work.iterdir()
                if item.is_file()
                and item.name.startswith(f"{provenance['package']}_{version}")
                and not item.name.endswith((".intoto.jsonl", ".slsa.json"))
            )
            record = {
                "root": str(root),
                "package": provenance["package"],
                "version": version,
                "baseVersion": provenance["baseVersion"],
                "series": series,
                "revision": series_revision,
                "commit": provenance["commit"],
                "tree": provenance["tree"],
                "tag": provenance["tag"],
                "tagObject": provenance["tagObject"],
                "tagVerified": provenance["tagVerified"],
                "changes": str(changes) if changes.is_file() else "",
                "changesSha256": sha256_file(changes) if changes.is_file() else "",
                "dsc": str(dsc) if dsc.is_file() else "",
                "dscSha256": sha256_file(dsc) if dsc.is_file() else "",
                "buildinfo": str(buildinfo) if buildinfo.is_file() else "",
                "buildinfoSha256": sha256_file(buildinfo) if buildinfo.is_file() else "",
                "provenance": provenance,
                "artifacts": [str(item) for item in artifacts],
            }
            if proc.returncode == 0 and changes.is_file():
                attestation = create_attestation(
                    package=provenance["package"],
                    version=version,
                    base_version=provenance["baseVersion"],
                    series=series,
                    revision=series_revision,
                    source_uri=str(provenance.get("remote") or f"git+{root}"),
                    commit=provenance["commit"],
                    tree=provenance["tree"],
                    tag=provenance["tag"],
                    tag_object=provenance["tagObject"],
                    tag_verified=provenance["tagVerified"],
                    artifacts=[item for item in artifacts if item.is_file()],
                    command=command,
                    started_on=started_on,
                    finished_on=finished_on,
                    builder_version=__version__,
                    output_dir=work,
                    key=key,
                )
                if key and not attestation.get("signed"):
                    raise RuntimeError("SLSA attestation signing failed")
                record["slsa"] = store_attestation(attestation, config)
                record_build(record, config, commit=False)
                results.append({"ok": True, "series": series, "record": record, "output": (proc.stdout + proc.stderr).strip()})
            else:
                results.append(
                    {
                        "ok": False,
                        "series": series,
                        "record": record,
                        "output": (proc.stdout + proc.stderr).strip(),
                        "error": f"dpkg-buildpackage exited {proc.returncode}",
                    }
                )
        except Exception as exc:
            results.append({"ok": False, "series": series, "error": str(exc)})
    return {
        "ok": all(item.get("ok") for item in results),
        "root": str(root),
        "package": package.get("source", ""),
        "baseVersion": package.get("baseVersion", ""),
        "builds": results,
    }


def push_release(root: Path, config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_config()
    root = root.expanduser().resolve()
    package = inspect_package(root, config)
    tag = f"v{package.get('baseVersion', '')}"
    if package.get("errors"):
        return {"ok": False, "error": "packaging preflight failed", "details": package["errors"]}
    if not package.get("tag", {}).get("present") or not package.get("tag", {}).get("signed"):
        return {"ok": False, "error": f"signed release tag {tag} is required"}
    if not package.get("tag", {}).get("pointsAtHead"):
        return {"ok": False, "error": f"release tag {tag} does not point at HEAD"}
    branch = git_out(["rev-parse", "--abbrev-ref", "HEAD"], root)
    if branch != "main":
        return {"ok": False, "error": f"push from main, not {branch or 'detached HEAD'}"}
    results = []
    for ref in (branch, tag):
        proc = run(["git", "push", "origin", ref], cwd=root, timeout=120)
        results.append({"ref": ref, "ok": proc.returncode == 0, "output": (proc.stdout + proc.stderr).strip()})
        if proc.returncode != 0:
            break
    return {"ok": all(item["ok"] for item in results), "tag": tag, "pushes": results}


def build_binary(
    root: Path,
    series: str | None = None,
    revision: int | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = config or load_config()
    root = root.expanduser().resolve()
    package = inspect_package(root, config)
    profile = profile_for(package.get("source", root.name), config)
    target_series = series or (profile.get("series") or ["jammy"])[0]
    try:
        launchpad_data = observe(force=True, config=config)
    except Exception:
        launchpad_data = None
    series_revision = _revision_for(
        target_series,
        package.get("baseVersion", ""),
        bool(profile.get("jammyNative")),
        launchpad_data,
        str(profile.get("ppa")),
        str(package.get("source", "")),
        revision,
    )
    try:
        src, work, provenance = _prepare_tree(root, target_series, series_revision, config)
        started_on = now_iso()
        proc = run(["dpkg-buildpackage", "-b", "-us", "-uc"], cwd=src, timeout=3600)
        finished_on = now_iso()
        deb_paths = sorted(item for item in work.glob("*.deb") if item.is_file())
        slsa = create_attestation(
            package=provenance["package"],
            version=provenance["version"],
            base_version=provenance["baseVersion"],
            series=target_series,
            revision=series_revision,
            source_uri=str(provenance.get("remote") or f"git+{root}"),
            commit=provenance["commit"],
            tree=provenance["tree"],
            tag=provenance["tag"],
            tag_object=provenance["tagObject"],
            tag_verified=provenance["tagVerified"],
            artifacts=deb_paths,
            command=["dpkg-buildpackage", "-b", "-us", "-uc"],
            started_on=started_on,
            finished_on=finished_on,
            builder_version=__version__,
            output_dir=work,
            key="",
            build_type="https://github.com/scripting-drafts/Release-Board/slsa/debian-binary/v1",
        )
        return {
            "ok": proc.returncode == 0,
            "package": provenance["package"],
            "version": provenance["version"],
            "series": target_series,
            "provenance": provenance,
            "slsa": slsa,
            "debs": [str(item) for item in deb_paths],
            "output": (proc.stdout + proc.stderr).strip(),
            "returncode": proc.returncode,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def upload(
    root: Path,
    changes: Path,
    config: dict[str, Any] | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    config = config or load_config()
    root = root.expanduser().resolve()
    changes = changes.expanduser().resolve()
    if not confirm:
        return {"ok": False, "error": "upload requires explicit confirmation"}
    if not changes.is_file():
        return {"ok": False, "error": f"changes file not found: {changes}"}
    check = check_release(root, config)
    if not check.get("ok"):
        return {"ok": False, "error": "release preflight failed", "check": check}
    package = check["package"]
    profile = profile_for(package.get("source", root.name), config)
    match = re.match(r"^(?P<package>.+)_(?P<version>.+)_source\.changes$", changes.name)
    if not match:
        return {"ok": False, "error": f"not a source changes file: {changes.name}"}
    upload_package = match.group("package")
    upload_version = match.group("version")
    if upload_package != package["source"]:
        return {"ok": False, "error": f"changes package {upload_package} does not match {package['source']}"}
    if not upload_version.startswith(package["baseVersion"]):
        return {"ok": False, "error": f"changes version {upload_version} does not match base {package['baseVersion']}"}
    series_match = re.search(r"~([a-z]+)1$", upload_version)
    upload_series = series_match.group(1) if series_match else package.get("distribution", "")
    if upload_series not in profile.get("series", []):
        return {"ok": False, "error": f"changes series {upload_series} is not configured for {upload_package}"}
    revision_match = re.search(r"-0ubuntu(\d+)~", upload_version)
    upload_revision = int(revision_match.group(1)) if revision_match else 1
    verify = run(["gpg", "--batch", "--verify", str(changes)], timeout=30)
    if verify.returncode != 0:
        return {"ok": False, "error": "changes file is not signed", "output": (verify.stderr or verify.stdout).strip()}
    target = str(profile.get("ppa", package["source"]))
    proc = run(["dput", target, str(changes)], cwd=root, timeout=900)
    ok = proc.returncode == 0
    result = {
        "ok": ok,
        "target": target,
        "changes": str(changes),
        "changesSha256": sha256_file(changes),
        "output": (proc.stdout + proc.stderr).strip(),
        "returncode": proc.returncode,
    }
    if ok:
        build_entry = next(
            (
                item
                for item in reversed(_ledger_entries(config))
                if item.get("event") == "built"
                and item.get("package") == upload_package
                and item.get("version") == upload_version
                and item.get("changesSha256") == result["changesSha256"]
            ),
            {},
        )
        slsa = build_entry.get("slsa") or {}
        if not slsa.get("attestation"):
            return {
                "ok": False,
                "error": "SLSA provenance is missing for this build; rebuild before uploading",
                "build": build_entry.get("version", ""),
            }
        attestation_path = Path(str(slsa.get("attestation", "")))
        if slsa.get("attestationSha256") and attestation_path.is_file():
            if sha256_file(attestation_path) != slsa["attestationSha256"]:
                return {"ok": False, "error": "SLSA attestation hash mismatch"}
        subject_paths = [Path(str(item)) for item in build_entry.get("artifacts", [])]
        for key in ("changes", "dsc", "buildinfo"):
            value = build_entry.get(key)
            if value:
                subject_paths.append(Path(str(value)))
        verification = verify_attestation(attestation_path, subject_paths)
        if not verification.get("ok") or not verification.get("signed"):
            return {
                "ok": False,
                "error": "SLSA provenance verification failed",
                "checks": verification.get("checks", []),
            }
        record_upload(
            {
                "root": str(root),
                "package": upload_package,
                "version": upload_version,
                "baseVersion": package["baseVersion"],
                "series": upload_series,
                "revision": upload_revision,
                "commit": build_entry.get("commit") or check["git"].get("head", {}).get("sha", ""),
                "tag": build_entry.get("tag", ""),
                "changes": str(changes),
                "changesSha256": result["changesSha256"],
                "dsc": build_entry.get("dsc", ""),
                "dscSha256": build_entry.get("dscSha256", ""),
                "buildinfo": build_entry.get("buildinfo", ""),
                "buildinfoSha256": build_entry.get("buildinfoSha256", ""),
                "artifacts": build_entry.get("artifacts", []),
                "dputTarget": target,
                "slsa": slsa,
            },
            config,
            commit=True,
        )
    return result


def watch(
    root: Path,
    series_list: list[str] | None = None,
    config: dict[str, Any] | None = None,
    timeout: int = 10800,
    interval: int = 60,
) -> dict[str, Any]:
    config = config or load_config()
    root = root.expanduser().resolve()
    package = inspect_package(root, config)
    profile = profile_for(package.get("source", root.name), config)
    series_values = _series_list(profile, series_list)
    parsed = split_version(package.get("version", ""))
    rows = []
    for series in series_values:
        expected = series_version(parsed["base"], series, parsed["revision"], bool(profile.get("jammyNative")))
        waited = 0
        final = None
        recorded = False
        while True:
            data = observe(force=True, config=config)
            final = status_for(data, str(profile.get("ppa")), package.get("source", ""), series, expected)
            final["expectedVersion"] = expected
            if _published_matches(
                final.get("publishedVersion", ""),
                parsed["base"],
                series,
                bool(profile.get("jammyNative")),
            ):
                evidence = next(
                    (
                        item
                        for item in reversed(read_ledger(config))
                        if item.get("event") in {"built", "uploaded"}
                        and item.get("package") == package["source"]
                        and item.get("series") == series
                        and str(item.get("version", "")).startswith(parsed["base"])
                    ),
                    None,
                )
                if not evidence:
                    final["error"] = "no local build evidence for this publication; ledger not updated"
                    break
                record_published(
                    {
                        "root": str(root),
                        "package": package["source"],
                        "version": final.get("publishedVersion", expected),
                        "baseVersion": parsed["base"],
                        "series": series,
                        "revision": evidence.get("revision", parsed["revision"]),
                        "commit": evidence.get("commit", ""),
                        "tag": evidence.get("tag", ""),
                        "changes": evidence.get("changes", ""),
                        "changesSha256": evidence.get("changesSha256", ""),
                        "dsc": evidence.get("dsc", ""),
                        "dscSha256": evidence.get("dscSha256", ""),
                        "buildinfo": evidence.get("buildinfo", ""),
                        "buildinfoSha256": evidence.get("buildinfoSha256", ""),
                        "artifacts": evidence.get("artifacts", []),
                        "slsa": evidence.get("slsa", {}),
                        "sourceId": (final.get("publishedSource") or {}).get("id", ""),
                        "buildIds": [item.get("id") for item in final.get("builds", [])],
                        "publishedVersion": final.get("publishedVersion", ""),
                    },
                    config,
                    commit=True,
                )
                recorded = True
                break
            if final.get("state") == "failed":
                break
            if waited >= timeout:
                break
            time.sleep(interval)
            waited += interval
        rows.append(
            {
                "series": series,
                "expectedVersion": expected,
                "final": final,
                "ok": bool(
                    recorded
                    and final
                    and _published_matches(
                        final.get("publishedVersion", ""),
                        parsed["base"],
                        series,
                        bool(profile.get("jammyNative")),
                    )
                ),
            }
        )
    return {"ok": all(item["ok"] for item in rows), "package": package.get("source", ""), "series": rows}
