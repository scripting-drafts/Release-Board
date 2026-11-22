"""Small, dependency-free helpers shared by the release board."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

APP_ROOT = Path(__file__).resolve().parents[2]
PROFILES_FILE = APP_ROOT / "config" / "profiles.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def host_series(default: str = "resolute") -> str:
    """Return the current Ubuntu series codename."""
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if line.startswith("VERSION_CODENAME="):
                value = line.split("=", 1)[1].strip().strip('"')
                if value:
                    return value
    except OSError:
        pass
    return default


def config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def cache_home() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))


def state_home() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))


def config_path() -> Path:
    return config_home() / "release-board" / "config.json"


def cache_dir() -> Path:
    return cache_home() / "release-board"


def state_dir() -> Path:
    return state_home() / "release-board"


def ensure_dir(path: Path, mode: int = 0o700) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(mode)
    except OSError:
        pass
    return path


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json_atomic(path: Path, payload: Any, mode: int = 0o600) -> None:
    ensure_dir(path.parent)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_text_atomic(path: Path, text: str, mode: int = 0o644) -> None:
    ensure_dir(path.parent)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(
    args: Sequence[str],
    cwd: Path | None = None,
    timeout: int | None = 120,
    env: dict[str, str] | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a fixed argv with no shell and return text output."""
    merged = os.environ.copy()
    if env:
        merged.update(env)
    proc = subprocess.run(
        list(args),
        cwd=str(cwd) if cwd else None,
        env=merged,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(args)}\n{proc.stderr.strip()}"
        )
    return proc


def git(args: Sequence[str], cwd: Path, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], cwd=cwd, timeout=timeout)


def git_out(args: Sequence[str], cwd: Path, default: str = "") -> str:
    proc = git(args, cwd)
    if proc.returncode != 0:
        return default
    return proc.stdout.strip()


def parse_dpkg_changelog(root: Path) -> dict[str, str] | None:
    fields = ("Source", "Version", "Distribution", "Urgency", "Maintainer", "Date", "Changes")
    values: dict[str, str] = {}
    if not root.is_dir():
        return None
    for field in fields:
        try:
            proc = run(["dpkg-parsechangelog", "-S", field], cwd=root, timeout=20)
        except OSError:
            return None
        if proc.returncode != 0:
            return None
        values[field.lower()] = proc.stdout.strip()
    return values


def split_version(version: str) -> dict[str, Any]:
    base = version.split("~", 1)[0]
    revision = 1
    if "-0ubuntu" in base:
        left, right = base.rsplit("-0ubuntu", 1)
        base = left
        digits = "".join(ch for ch in right if ch.isdigit())
        revision = int(digits) if digits else 1
    return {"version": version, "base": base, "revision": revision}


def series_version(base: str, series: str, revision: int, jammy_native: bool = False) -> str:
    if jammy_native and series == "jammy":
        return base
    if "-" in base:
        return f"{base}+{series}"
    return f"{base}-0ubuntu{revision}~{series}1"


def version_matches(published: str, base: str, series: str, revision: int, jammy_native: bool) -> bool:
    if jammy_native and series == "jammy":
        return published == base
    return published.startswith(f"{base}-0ubuntu") and published.endswith(f"~{series}1")


def which(name: str) -> str | None:
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def flatten(items: Iterable[Iterable[Any]]) -> list[Any]:
    return [value for group in items for value in group]


def compact(text: str, limit: int = 400) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
