"""Configuration and Debian package profiles."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .util import (
    APP_ROOT,
    PROFILES_FILE,
    config_path,
    ensure_dir,
    host_series,
    now_iso,
    read_json,
    write_json_atomic,
)

DEFAULT_USER = "toomanypatterns"
DEFAULT_SIGN_KEY = "F104A3F1CE9A23540AEB8F24F31978E49E85DDCA"

DEFAULT_ROOTS = [
    {"name": "QA Dashboard", "path": str(Path.home() / "QA-Dashboard")},
    {"name": "QA Sentinels", "path": str(Path.home() / "QA-Sentinels")},
    {"name": "Ubuntu Backup", "path": str(Path.home() / "Ubuntu-Backup-src")},
]

DEFAULT_DEPLOYMENTS = {
    "launchpad": {
        "kind": "launchpad",
        "user": DEFAULT_USER,
        "api": "https://api.launchpad.net/1.0",
        "archive": "https://ppa.launchpadcontent.net/{user}",
        "web": "https://launchpad.net/~{user}/+archive/ubuntu",
    }
}

DEFAULT_CONFIG: dict[str, Any] = {
    "schema": 1,
    "roots": DEFAULT_ROOTS,
    "ledgerRepo": str(APP_ROOT),
    "ledgerPath": "release/ledger.jsonl",
    "launchpadUser": DEFAULT_USER,
    "deployments": copy.deepcopy(DEFAULT_DEPLOYMENTS),
    "repoHosts": {"github": "https://github.com"},
    "signKey": DEFAULT_SIGN_KEY,
    "refreshSeconds": 60,
    "profiles": {},
}


def default_profiles() -> dict[str, Any]:
    return read_json(PROFILES_FILE, {}) or {}


def load_config() -> dict[str, Any]:
    config = copy.deepcopy(DEFAULT_CONFIG)
    stored = read_json(config_path(), None)
    if isinstance(stored, dict):
        for key, value in stored.items():
            if key == "profiles" and isinstance(value, dict):
                config["profiles"].update(value)
            elif key in {"deployments", "repoHosts"} and isinstance(value, dict):
                # The UI edits these as complete lists. Storing them as a
                # replacement lets an environment or host be removed again.
                config[key] = copy.deepcopy(value)
            else:
                config[key] = value
    return config


def save_config(config: dict[str, Any]) -> Path:
    path = config_path()
    ensure_dir(path.parent)
    config = copy.deepcopy(config)
    config["updatedAt"] = now_iso()
    write_json_atomic(path, config, 0o600)
    return path


def profile_for(source: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_config()
    defaults = default_profiles()
    profile = copy.deepcopy(defaults.get(source, {}))
    profile.setdefault("ppa", source)
    profile.setdefault("series", [host_series()])
    profile.setdefault("untested", [])
    profile.setdefault("jammyNative", False)
    profile.setdefault("binaryPackage", source)
    profile.setdefault("test", [])
    profile.setdefault("versionFiles", ["debian/changelog"])
    profile.setdefault("sourceNeeds", [])
    profile.setdefault("docs", "")
    overrides = config.get("profiles", {}).get(source, {})
    if isinstance(overrides, dict):
        profile.update(overrides)
    return profile


def roots(config: dict[str, Any] | None = None) -> list[dict[str, str]]:
    config = config or load_config()
    result: list[dict[str, str]] = []
    for entry in config.get("roots", []):
        if isinstance(entry, str):
            result.append({"name": Path(entry).name, "path": entry})
        elif isinstance(entry, dict) and entry.get("path"):
            result.append(
                {
                    "name": str(entry.get("name") or Path(entry["path"]).name),
                    "path": str(Path(entry["path"]).expanduser()),
                    "remote": str(entry.get("remote") or "origin"),
                    "host": str(entry.get("host") or "github"),
                    "deployment": str(entry.get("deployment") or "launchpad"),
                }
            )
    return result


def ledger_file(config: dict[str, Any] | None = None) -> Path:
    config = config or load_config()
    path = Path(config.get("ledgerPath", "release/ledger.jsonl"))
    if path.is_absolute():
        return path
    repo = Path(config.get("ledgerRepo", APP_ROOT)).expanduser()
    return repo / path


def config_summary(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_config()
    return {
        "path": str(config_path()),
        "roots": roots(config),
        "ledger": str(ledger_file(config)),
        "launchpadUser": config.get("launchpadUser"),
        "deployments": config.get("deployments", {}),
        "repoHosts": config.get("repoHosts", {}),
        "signKey": config.get("signKey"),
        "profiles": sorted(default_profiles().keys()),
        "refreshSeconds": config.get("refreshSeconds", 60),
    }


def init_config(force: bool = False) -> dict[str, Any]:
    path = config_path()
    if path.exists() and not force:
        return load_config()
    config = copy.deepcopy(DEFAULT_CONFIG)
    save_config(config)
    return config


def set_roots(entries: list[dict[str, str]], config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_config()
    clean: list[dict[str, str]] = []
    for entry in entries:
        path = Path(str(entry.get("path", ""))).expanduser()
        if not path:
            continue
        clean.append(
            {
                "name": str(entry.get("name") or path.name),
                "path": str(path),
                "remote": str(entry.get("remote") or "origin"),
                "host": str(entry.get("host") or "github"),
                "deployment": str(entry.get("deployment") or "launchpad"),
            }
        )
    config["roots"] = clean
    save_config(config)
    return config


def root_entry(path: Path | str, config: dict[str, Any] | None = None) -> dict[str, str]:
    config = config or load_config()
    resolved = str(Path(path).expanduser().resolve())
    for entry in roots(config):
        if str(Path(entry["path"]).resolve()) == resolved or Path(entry["path"]).name == str(path):
            return entry
    return {"name": Path(resolved).name, "path": resolved, "remote": "origin", "host": "github", "deployment": "launchpad"}


def deployment_for(name: str | None, config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_config()
    deployments = config.get("deployments", {})
    selected = deployments.get(name or "launchpad", {}) if isinstance(deployments, dict) else {}
    merged = copy.deepcopy(DEFAULT_DEPLOYMENTS["launchpad"])
    if isinstance(selected, dict):
        merged.update(selected)
    if not merged.get("user"):
        merged["user"] = config.get("launchpadUser", DEFAULT_USER)
    return merged


def package_root(root: Path) -> Path:
    """Return the directory that owns debian/changelog for a project root."""
    if (root / "debian" / "changelog").is_file():
        return root
    candidates = []
    for marker in ("debian/changelog", "*/debian/changelog"):
        candidates.extend(root.glob(marker))
    candidates = [path.parent.parent for path in candidates if path.is_file()]
    if len(candidates) == 1:
        return candidates[0]
    return root


def read_profiles_file() -> dict[str, Any]:
    return read_json(PROFILES_FILE, {}) or {}


def profiles_path() -> Path:
    return PROFILES_FILE


def app_root() -> Path:
    return APP_ROOT


def json_dumps(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)
