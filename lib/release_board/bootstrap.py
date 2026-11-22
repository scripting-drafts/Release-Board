"""First-release scaffolding for Debian source packages.

Only Launchpad/Debian source deployments are eligible.  The scaffold creates
the minimum Debian source package metadata that the release preflight requires,
adds the matching dput target, and records the package profile.  It does not
invent a signing key.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import (
    deployment_for,
    load_config,
    root_entry,
    save_config,
)
from .packaging import _signing_key_ok
from .util import git_out, host_series, now_iso, run, write_text_atomic

SOURCE_DEPLOYMENTS = {"launchpad", "debian"}
DEFAULT_VERSION = "0.1.0"
STANDARDS_VERSION = "4.7.0"
BUILD_DEPENDS = "debhelper-compat (= 13)"


class BootstrapError(ValueError):
    """A first-release scaffold cannot be generated safely."""


def _package_name(root: Path, override: str) -> str:
    value = (override or root.name).lower()
    value = re.sub(r"[^a-z0-9+.-]+", "-", value).strip("-.")
    if not value:
        raise BootstrapError("cannot derive a Debian package name")
    return value


def _maintainer(root: Path, override: str) -> str:
    if override.strip():
        return override.strip()
    name = git_out(["config", "user.name"], root)
    email = git_out(["config", "user.email"], root)
    if not name or not email:
        raise BootstrapError("set git user.name and user.email or pass --maintainer")
    return f"{name} <{email}>"


def _homepage(root: Path) -> str:
    url = git_out(["remote", "get-url", "origin"], root)
    if url.startswith("git@github.com:"):
        url = "https://github.com/" + url.split(":", 1)[1]
    if url.endswith(".git"):
        url = url[:-4]
    return url


def _description(root: Path, package: str, override: str) -> str:
    if override.strip():
        return " ".join(override.split())
    readme = root / "README.md"
    if readme.is_file():
        for line in readme.read_text(encoding="utf-8", errors="replace").splitlines():
            text = line.strip()
            if not text or text.startswith(("#", "`", "!", "[", "|")):
                continue
            text = re.sub(r"[*_`]", "", text)
            if text:
                return " ".join(text.split())[:200]
    return f"{package} first release"


def _distribution(value: str, series: list[str]) -> str:
    distribution = (value or series[0]).strip()
    if distribution not in series:
        raise BootstrapError(
            f"distribution {distribution!r} is not in the profile series: {', '.join(series)}"
        )
    return distribution


def _changelog(package: str, version: str, distribution: str, maintainer: str) -> str:
    stamp = datetime.now().astimezone().strftime("%a, %d %b %Y %H:%M:%S %z")
    return (
        f"{package} ({version}) {distribution}; urgency=medium\n"
        "\n"
        "  * Initial release.\n"
        "\n"
        f" -- {maintainer}  {stamp}\n"
    )


def _control(package: str, maintainer: str, description: str, homepage: str) -> str:
    lines = [
        f"Source: {package}",
        "Section: utils",
        "Priority: optional",
        f"Maintainer: {maintainer}",
        f"Build-Depends: {BUILD_DEPENDS}",
        f"Standards-Version: {STANDARDS_VERSION}",
        "Rules-Requires-Root: no",
    ]
    if homepage:
        lines.extend([f"Homepage: {homepage}", f"Vcs-Git: {homepage}.git"])
    lines.extend(
        [
            "",
            f"Package: {package}",
            "Architecture: all",
            "Depends: ${misc:Depends}",
            f"Description: {description}",
            f" {description}",
        ]
    )
    return "\n".join(lines) + "\n"


def _rules() -> str:
    return "#!/usr/bin/make -f\n\n%:\n\tdh $@\n"


def _install_entries(root: Path, package: str) -> str:
    """Best-effort install map for common source layouts."""
    entries: list[str] = []
    if (root / "bin").is_dir():
        entries.append("bin/* usr/bin/")
    if (root / "lib").is_dir():
        # The common layout resolves sibling libraries from the installed
        # /usr/bin parent, i.e. /usr/lib.
        entries.append("lib/* usr/lib/")
    if (root / "systemd").is_dir():
        for pattern, target in (
            ("*.service", "lib/systemd/system/"),
            ("*.slice", "lib/systemd/system/"),
            ("*.path", "lib/systemd/system/"),
        ):
            if any((root / "systemd").glob(pattern)):
                entries.append(f"systemd/{pattern} {target}")
    if (root / "docs").is_dir():
        entries.append(f"docs/* usr/share/doc/{package}/")
    return ("\n".join(entries) + "\n") if entries else ""


def _dput_path() -> Path:
    return Path.home() / ".dput.cf"


def _dput_entry(package: str, user: str) -> str:
    return (
        f"[{package}]\n"
        "fqdn = ppa.launchpad.net\n"
        "method = ftp\n"
        f"incoming = ~{user}/ubuntu/{package}/\n"
        "login = anonymous\n"
        "allow_unsigned_uploads = 0\n"
    )


def _add_dput(package: str, user: str, dry_run: bool) -> dict[str, Any]:
    path = _dput_path()
    text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    section = re.search(rf"^\s*\[{re.escape(package)}\]\s*$", text, re.MULTILINE)
    if section:
        return {"path": str(path), "created": False, "present": True}
    entry = _dput_entry(package, user)
    if not dry_run:
        if text and not text.endswith("\n"):
            text += "\n"
        write_text_atomic(path, text + ("\n" if text else "") + entry, 0o600)
    return {"path": str(path), "created": True, "present": True}


def _series(values: list[str]) -> list[str]:
    result = [item.strip() for item in values if item.strip()]
    if not result:
        result = [host_series()]
    for item in result:
        if not re.fullmatch(r"[a-z][a-z0-9.-]*", item):
            raise BootstrapError(f"invalid Debian series: {item!r}")
    return result


def bootstrap(
    root: Path | str,
    config: dict[str, Any] | None = None,
    *,
    package: str = "",
    version: str = DEFAULT_VERSION,
    distribution: str = "",
    maintainer: str = "",
    description: str = "",
    series: list[str] | None = None,
    commit: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    config = config or load_config()
    root = Path(root).expanduser().resolve()
    if not (root / ".git").is_dir():
        raise BootstrapError(f"not a Git repository: {root}")
    entry = root_entry(root, config)
    deployment = deployment_for(entry.get("deployment"), config)
    kind = str(deployment.get("kind") or entry.get("deployment") or "launchpad")
    if kind not in SOURCE_DEPLOYMENTS:
        raise BootstrapError(
            f"{entry.get('name', root.name)} uses deployment {kind!r}; "
            "first-release scaffolding is only for Debian source deployments"
        )
    if (root / "debian" / "changelog").is_file():
        raise BootstrapError(f"{root} already has debian/changelog")

    name = _package_name(root, package)
    version = version.strip()
    if not re.fullmatch(r"[0-9][A-Za-z0-9.+:~-]*", version):
        raise BootstrapError(f"invalid Debian version: {version!r}")
    series_list = _series(series or [])
    distribution = _distribution(distribution, series_list)
    owner = _maintainer(root, maintainer)
    description = _description(root, name, description)
    homepage = _homepage(root)

    files: dict[str, str] = {
        "debian/changelog": _changelog(name, version, distribution, owner),
        "debian/control": _control(name, owner, description, homepage),
        "debian/rules": _rules(),
        "debian/source/format": "3.0 (native)\n",
    }
    install = _install_entries(root, name)
    if install:
        files[f"debian/{name}.install"] = install

    signing_key = str(config.get("signKey", ""))
    key_ok, key_message = _signing_key_ok(signing_key)
    dput = _add_dput(name, str(deployment.get("user") or config.get("launchpadUser") or ""), dry_run)

    profile = {
        "ppa": name,
        "series": series_list,
        "binaryPackage": name,
        "versionFiles": ["debian/changelog"],
    }
    if not dry_run:
        for relative, content in files.items():
            target = root / relative
            mode = 0o755 if relative == "debian/rules" else 0o644
            write_text_atomic(target, content, mode)
        config.setdefault("profiles", {})[name] = {
            **config.get("profiles", {}).get(name, {}),
            **profile,
        }
        save_config(config)

    committed = False
    if commit and not dry_run:
        add = run(["git", "add", "debian"], cwd=root, timeout=30)
        if add.returncode != 0:
            raise BootstrapError(f"git add failed: {add.stderr.strip()}")
        proc = run(
            ["git", "commit", "-s", "-m", f"Add first-release Debian packaging for {name}"],
            cwd=root,
            timeout=120,
        )
        if proc.returncode != 0:
            raise BootstrapError(f"git commit failed: {proc.stderr.strip()}")
        committed = True

    return {
        "ok": key_ok,
        "root": str(root),
        "package": name,
        "version": version,
        "distribution": distribution,
        "series": series_list,
        "files": sorted(files),
        "dput": dput,
        "profile": profile,
        "signingKey": {"present": key_ok, "message": key_message},
        "committed": committed,
        "dryRun": dry_run,
        "generatedAt": now_iso(),
    }
