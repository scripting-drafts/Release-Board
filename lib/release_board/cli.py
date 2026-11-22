"""Command-line entry point for the Release Board."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .board import build_board, render_markdown, write_board
from .compare import (
    clean_diffs,
    compare_cli_payload,
    render_tree_text,
)
from .config import (
    config_summary,
    init_config,
    ledger_file,
    load_config,
    package_root,
    profile_for,
    roots,
    save_config,
    set_roots,
)
from .gitinfo import inspect_roots
from .launchpad import observe, summary as launchpad_summary
from .packaging import inspect_packages
from .release import (
    build_binary,
    build_source,
    check_release,
    ensure_tag,
    push_release,
    run_tests,
    upload,
    watch,
)
from .trace import read_ledger, verify_all
from .util import git_out, now_iso, parse_dpkg_changelog, series_version, split_version, state_dir


def _resolve_root(value: str | None, config: dict[str, Any]) -> Path:
    if not value or value in {".", "current"}:
        return package_root(Path.cwd())
    for entry in roots(config):
        if value in {entry["name"], entry["path"], Path(entry["path"]).name}:
            return Path(entry["path"])
    path = Path(value).expanduser()
    return package_root(path) if path.is_dir() else path


def _print(payload: Any, as_json: bool, human: str = "") -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(human or json.dumps(payload, indent=2, sort_keys=True))


def _root_payload(config: dict[str, Any]) -> list[dict[str, str]]:
    return roots(config)


def cmd_config(args: argparse.Namespace) -> int:
    config = init_config(force=False) if args.init else load_config()
    if args.roots_json:
        payload = json.loads(args.roots_json)
        if not isinstance(payload, list):
            raise SystemExit("--roots-json must be a JSON list")
        config = set_roots(payload, config)
    if args.patch_json:
        patch = json.loads(args.patch_json)
        if not isinstance(patch, dict):
            raise SystemExit("--patch-json must be a JSON object")
        allowed = {
            "roots",
            "launchpadUser",
            "signKey",
            "refreshSeconds",
            "ledgerRepo",
            "ledgerPath",
            "profiles",
            "allowUnsignedBuilds",
        }
        unknown = sorted(set(patch) - allowed)
        if unknown:
            raise SystemExit(f"unsupported configuration keys: {', '.join(unknown)}")
        if "roots" in patch:
            if not isinstance(patch["roots"], list):
                raise SystemExit("roots must be a list")
            config = set_roots(patch["roots"], config)
        for key in ("launchpadUser", "signKey", "ledgerRepo", "ledgerPath"):
            if key in patch:
                config[key] = str(patch[key])
        if "refreshSeconds" in patch:
            config["refreshSeconds"] = max(15, int(patch["refreshSeconds"]))
        if "profiles" in patch and isinstance(patch["profiles"], dict):
            config.setdefault("profiles", {}).update(patch["profiles"])
        save_config(config)
    payload = config_summary(config)
    payload["version"] = __version__
    _print(payload, args.json)
    return 0


def cmd_roots(args: argparse.Namespace) -> int:
    config = load_config()
    _print(_root_payload(config), args.json)
    return 0


def cmd_git(args: argparse.Namespace) -> int:
    config = load_config()
    entries = roots(config)
    if args.root:
        root = _resolve_root(args.root, config)
        entries = [{"name": root.name, "path": str(root)}]
    data = inspect_roots(entries, fetch=args.fetch)
    if args.json:
        _print(data, True)
    else:
        for item in data["roots"]:
            if not item.get("isGit"):
                print(f"{item.get('configuredName')}: not a Git repository ({item.get('error')})")
                continue
            print(
                f"{item.get('configuredName')}: {item.get('branch')} "
                f"{'clean' if item.get('clean') else f'{item.get('dirty')} dirty'} "
                f"ahead={item.get('ahead')} behind={item.get('behind')} "
                f"head={item.get('head', {}).get('short', '')}"
            )
            branches = item.get("branches", {})
            if branches.get("localOnly"):
                print(f"  local-only: {', '.join(branches['localOnly'])}")
            if branches.get("remoteOnly"):
                print(f"  remote-only: {', '.join(branches['remoteOnly'])}")
            if branches.get("diverged"):
                print(f"  diverged: {', '.join(branches['diverged'])}")
    return 0


def cmd_pkg(args: argparse.Namespace) -> int:
    config = load_config()
    entries = roots(config)
    if args.root:
        root = _resolve_root(args.root, config)
        entries = [{"name": root.name, "path": str(root)}]
    data = inspect_packages(entries, config)
    if args.json:
        _print(data, True)
    else:
        for pkg in data["packages"]:
            print(f"{pkg.get('source')} {pkg.get('version')} ({pkg.get('distribution')})")
            print(f"  root: {pkg.get('path')}")
            print(f"  ppa: {pkg.get('ppa')} series: {', '.join(pkg.get('series', []))}")
            tag = pkg.get("tag", {})
            print(f"  tag: {tag.get('name')} present={tag.get('present')} signed={tag.get('signed')} head={tag.get('pointsAtHead')}")
            for error in pkg.get("errors", []):
                print(f"  ERROR: {error}")
            for warning in pkg.get("warnings", []):
                print(f"  warn:  {warning}")
    return 0


def cmd_launchpad(args: argparse.Namespace) -> int:
    config = load_config()
    data = observe(force=args.refresh, config=config)
    payload = launchpad_summary(data, roots(config), config) if args.summary else data
    if args.json:
        _print(payload, True)
        return 0
    print(f"snapshot: {payload.get('fetchedAt', '')}")
    for package in payload.get("packages", []):
        print(f"{package.get('source')}:")
        for row in package.get("series", []):
            print(
                f"  {row.get('series')}: published={row.get('publishedVersion') or '—'} "
                f"upload={row.get('uploadState')} build={row.get('buildState')} state={row.get('state')}"
            )
    return 0


def cmd_trace(args: argparse.Namespace) -> int:
    config = load_config()
    if args.verify:
        payload = verify_all(config)
    else:
        entries = read_ledger(config)
        payload = {"entries": len(entries), "latest": entries[-20:], "ledger": str(ledger_file(config))}
    if args.json:
        _print(payload, True)
    elif args.verify:
        print(f"traceability: {'pass' if payload.get('ok') else 'fail'} ({payload.get('latest', 0)} latest records)")
        for result in payload.get("results", []):
            print(f"{result.get('package')} {result.get('version')} {result.get('series')}: {'pass' if result.get('ok') else 'fail'}")
            for check in result.get("checks", []):
                print(f"  {'PASS' if check.get('ok') else 'FAIL'} {check.get('name')}: {check.get('detail', '')}")
    else:
        print(f"ledger: {payload.get('ledger')} ({payload.get('entries', 0)} entries)")
        for entry in payload.get("latest", []):
            print(
                f"  {entry.get('recordedAt')} {entry.get('event')} {entry.get('package')} "
                f"{entry.get('version')} {entry.get('series')} {str(entry.get('commit', ''))[:12]}"
            )
    return 0 if (not args.verify or payload.get("ok")) else 1


def cmd_board(args: argparse.Namespace) -> int:
    config = load_config()
    payload = build_board(config, refresh_launchpad=args.refresh)
    if args.write:
        path = write_board(payload, config)
        payload["boardPath"] = str(path)
    if args.json:
        _print(payload, True)
    else:
        print(render_markdown(payload))
        if args.write:
            print(f"\nwritten: {payload['boardPath']}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    config = load_config()
    if args.clean:
        payload = clean_diffs(_resolve_root(args.root, config) if args.root else None)
        _print(payload, args.json)
        return 0

    root = _resolve_root(args.root, config)
    payload = compare_cli_payload(
        root,
        config,
        origin_branch=args.origin,
        release_ref=args.release,
        fetch=args.fetch,
        open_zed=args.open,
        open_sources=args.open_sources,
        open_comparison=args.open_comparison,
        open_file=args.open_file,
    )
    if args.json:
        _print(payload, True)
    else:
        print(f"root:     {payload['root']}")
        for key in ("local", "origin", "released"):
            ref = payload["refs"][key]
            if ref.get("available") is False:
                print(f"{ref['label']:<18} unavailable")
            else:
                print(f"{ref['label']:<18} {ref['short']}  {ref['subject']}")
        for comparison in payload["comparisons"]:
            print(
                f"\n{comparison['label']} · {comparison['fileCount']} file(s) · "
                f"{comparison['from']['ref']} is {comparison['ahead']} ahead / "
                f"{comparison['behind']} behind {comparison['to']['ref']}"
            )
            if not comparison["files"]:
                print("  no differences")
            else:
                for line in render_tree_text(comparison["tree"]):
                    print(f"  {line}")
        for warning in payload.get("warnings", []):
            print(f"\nwarning: {warning}")
        if args.open or args.open_sources or args.open_comparison is not None or args.open_file:
            zed = payload.get("zed", {})
            if zed.get("opened"):
                print(f"\nopened Zed: {zed.get('export', {}).get('directory', '')}")
            else:
                print(f"\n{zed.get('message', 'no Zed window opened')}")
    return 0 if payload.get("ok") else 1


def cmd_check(args: argparse.Namespace) -> int:
    config = load_config()
    root = _resolve_root(args.root, config)
    payload = check_release(root, config)
    if args.json:
        _print(payload, True)
    else:
        pkg = payload.get("package", {})
        print(f"package: {pkg.get('source')} {pkg.get('version')} ({pkg.get('distribution')})")
        print(f"root:    {payload.get('root')}")
        print(f"status:  {'ready' if payload.get('ok') else 'blocked'}")
        for blocker in payload.get("blockers", []):
            print(f"  BLOCK: {blocker}")
        for note in payload.get("notes", []):
            print(f"  note:  {note}")
        for row in payload.get("launchpad", []):
            print(
                f"  {row.get('series')}: expected={row.get('expectedVersion')} "
                f"published={row.get('publishedVersion') or '—'} state={row.get('state')}"
            )
    return 0 if payload.get("ok") else 1


def cmd_test(args: argparse.Namespace) -> int:
    config = load_config()
    root = _resolve_root(args.root, config)
    payload = run_tests(root, config)
    _print(payload, args.json, payload.get("output", ""))
    return 0 if payload.get("ok") else 1


def cmd_tag(args: argparse.Namespace) -> int:
    config = load_config()
    root = _resolve_root(args.root, config)
    changelog = parse_dpkg_changelog(root)
    if not changelog:
        print("debian/changelog is missing", file=sys.stderr)
        return 1
    base = split_version(changelog.get("version", ""))["base"]
    payload = ensure_tag(root, base, config, args.message)
    _print(payload, args.json, json.dumps(payload, indent=2))
    return 0 if payload.get("ok") else 1


def cmd_build(args: argparse.Namespace) -> int:
    config = load_config()
    root = _resolve_root(args.root, config)
    payload = build_source(root, args.series, args.revision, config, tag=args.tag)
    _print(payload, args.json, json.dumps(payload, indent=2))
    return 0 if payload.get("ok") else 1


def cmd_push_release(args: argparse.Namespace) -> int:
    config = load_config()
    root = _resolve_root(args.root, config)
    payload = push_release(root, config)
    _print(payload, args.json, json.dumps(payload, indent=2))
    return 0 if payload.get("ok") else 1


def cmd_build_binary(args: argparse.Namespace) -> int:
    config = load_config()
    root = _resolve_root(args.root, config)
    payload = build_binary(root, args.series, args.revision, config)
    _print(payload, args.json, json.dumps(payload, indent=2))
    return 0 if payload.get("ok") else 1


def cmd_upload(args: argparse.Namespace) -> int:
    config = load_config()
    root = _resolve_root(args.root, config)
    payload = upload(root, Path(args.changes), config, confirm=args.confirm)
    _print(payload, args.json, json.dumps(payload, indent=2))
    return 0 if payload.get("ok") else 1


def _built_changes(config: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    changelog = parse_dpkg_changelog(root)
    if not changelog:
        return []
    source = changelog.get("source", "")
    parsed = split_version(changelog.get("version", ""))
    entries = read_ledger(config)
    uploaded = {
        str(item.get("changesSha256", ""))
        for item in entries
        if item.get("event") in {"uploaded", "published"} and item.get("changesSha256")
    }
    builds: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in entries:
        if entry.get("event") != "built":
            continue
        if entry.get("package") != source:
            continue
        if not str(entry.get("version", "")).startswith(parsed["base"]):
            continue
        if str(entry.get("changesSha256", "")) in uploaded:
            continue
        changes = Path(str(entry.get("changes", "")))
        if not changes.is_file():
            continue
        builds[(str(entry.get("series")), str(entry.get("version")))] = entry
    return list(builds.values())


def cmd_upload_latest(args: argparse.Namespace) -> int:
    config = load_config()
    root = _resolve_root(args.root, config)
    entries = _built_changes(config, root)
    if not entries:
        payload = {"ok": False, "error": "no built source packages for the local version"}
        _print(payload, args.json)
        return 1
    if not args.confirm:
        print("About to upload:")
        for entry in entries:
            print(f"  {entry.get('package')} {entry.get('version')} -> {profile_for(entry.get('package'), config).get('ppa')}")
        answer = input("Upload these source packages? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("aborted")
            return 1
    results = []
    for entry in entries:
        result = upload(root, Path(entry["changes"]), config, confirm=True)
        results.append(result)
        if not result.get("ok"):
            break
    payload = {"ok": all(item.get("ok") for item in results), "uploads": results}
    _print(payload, args.json, json.dumps(payload, indent=2))
    return 0 if payload.get("ok") else 1


def cmd_watch(args: argparse.Namespace) -> int:
    config = load_config()
    root = _resolve_root(args.root, config)
    payload = watch(root, args.series, config, timeout=args.timeout, interval=args.interval)
    _print(payload, args.json, json.dumps(payload, indent=2))
    return 0 if payload.get("ok") else 1


def cmd_workspace(args: argparse.Namespace) -> int:
    config = load_config()
    entries = roots(config)
    if args.dry_run:
        _print(
            {
                "roots": entries,
                "command": "zed -n <first>; zed <next>  (each opens as a project in the threads sidebar)",
            },
            args.json,
        )
        return 0
    zed = "zed"
    from .util import which

    if not which(zed):
        print("zed CLI not found", file=sys.stderr)
        return 1
    if not entries:
        print("no roots configured", file=sys.stderr)
        return 1
    import subprocess
    import time

    subprocess.Popen([zed, "-n", entries[0]["path"]])
    time.sleep(0.8)
    for entry in entries[1:]:
        subprocess.Popen([zed, entry["path"]])
        time.sleep(0.3)
    print(f"opened {len(entries)} project(s) in one Zed window")
    return 0


def _zed_tasks(cli: Path) -> list[dict[str, Any]]:
    root = "$ZED_WORKTREE_ROOT"
    python = "python3"
    return [
        {
            "label": "Release: board",
            "command": f'{python} {cli} board --write && zed -e {cli.parents[1]}/release/BOARD.md',
            "reveal": "no_focus",
        },
        {"label": "Release: preflight", "command": f"{python} {cli} check --root {root}"},
        {"label": "Release: test", "command": f"{python} {cli} test --root {root}"},
        {"label": "Release: git fetch", "command": f"{python} {cli} git --root {root} --fetch"},
        {"label": "Release: git inspect", "command": f"{python} {cli} git --root {root}", "reveal": "no_focus"},
        {
            "label": "Release: compare in Zed",
            "command": f"{python} {cli} compare --root {root} --open",
        },
        {"label": "Release: packaging inspect", "command": f"{python} {cli} pkg --root {root}", "reveal": "no_focus"},
        {"label": "Release: launchpad", "command": f"{python} {cli} launchpad --refresh", "reveal": "no_focus"},
        {"label": "Release: trace verify", "command": f"{python} {cli} trace --verify", "reveal": "no_focus"},
        {"label": "Release: sign tag", "command": f"{python} {cli} tag --root {root}"},
        {"label": "Release: push main and tag", "command": f"{python} {cli} push-release --root {root}"},
        {"label": "Release: build local .deb", "command": f"{python} {cli} build-binary --root {root}"},
        {"label": "Release: build source (all series)", "command": f"{python} {cli} build-source --root {root} --tag"},
        {"label": "Release: upload latest", "command": f"{python} {cli} upload-latest --root {root}"},
        {"label": "Release: watch publication", "command": f"{python} {cli} watch --root {root}"},
        {"label": "Release: open board app", "command": f"npm --prefix {cli.parents[1]} start"},
    ]


def cmd_zed(args: argparse.Namespace) -> int:
    path = Path.home() / ".config" / "zed" / "tasks.json"
    tasks = _zed_tasks(Path(__file__).resolve().parents[2] / "bin" / "release-board")
    if args.dry_run:
        _print({"path": str(path), "tasks": tasks}, args.json)
        return 0
    existing: list[dict[str, Any]] = []
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        try:
            existing = json.loads(text)
            if not isinstance(existing, list):
                raise ValueError("tasks.json is not a list")
        except Exception as exc:
            backup = path.with_suffix(".json.bak")
            backup.write_text(text, encoding="utf-8")
            print(
                f"cannot parse {path}: {exc}; backup written to {backup}; no tasks were changed",
                file=sys.stderr,
            )
            return 1
    labels = {item.get("label") for item in tasks}
    merged = [item for item in existing if item.get("label") not in labels] + tasks
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    print(f"installed {len(tasks)} tasks in {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="release-board", description="Release Board")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, func: Any, help_text: str) -> argparse.ArgumentParser:
        item = sub.add_parser(name, help=help_text)
        item.set_defaults(func=func)
        item.add_argument("--json", action="store_true")
        return item

    p = add("config", cmd_config, "show or initialize configuration")
    p.add_argument("--init", action="store_true")
    p.add_argument("--roots-json", default="")
    p.add_argument("--patch-json", default="")

    add("roots", cmd_roots, "list configured roots")

    p = add("git", cmd_git, "inspect local and remote Git state")
    p.add_argument("--root", default="")
    p.add_argument("--fetch", action="store_true")

    p = add("pkg", cmd_pkg, "inspect Debian metadata and versions")
    p.add_argument("--root", default="")

    p = add("launchpad", cmd_launchpad, "read Launchpad PPA state")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--summary", action="store_true", default=True)

    p = add("trace", cmd_trace, "show or verify release traceability")
    p.add_argument("--verify", action="store_true")

    p = add("board", cmd_board, "render the release board")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--write", action="store_true")

    p = add("compare", cmd_compare, "compare local, origin and released trees")
    p.add_argument("--root", default="")
    p.add_argument("--origin", default="main", help="origin branch to compare (default: main)")
    p.add_argument("--release", default="", help="release tag (default: v<package version>)")
    p.add_argument("--fetch", action="store_true", help="fetch origin and tags first")
    p.add_argument("--open", action="store_true", help="open changed files in Zed")
    p.add_argument("--open-sources", action="store_true", help="open the three source trees in Zed")
    p.add_argument("--open-comparison", type=int, default=None, help="open one comparison by index")
    p.add_argument("--open-file", default="", help="open one changed file in Zed")
    p.add_argument("--clean", action="store_true", help="remove exported diff trees")

    p = add("check", cmd_check, "run release preflight")
    p.add_argument("--root", default="")

    p = add("test", cmd_test, "run the package test command")
    p.add_argument("--root", default="")

    p = add("tag", cmd_tag, "create or verify the signed release tag")
    p.add_argument("--root", default="")
    p.add_argument("--message", default="")

    p = add("build-source", cmd_build, "build signed source packages")
    p.add_argument("--root", default="")
    p.add_argument("--series", action="append", default=[])
    p.add_argument("--revision", type=int, default=None)
    p.add_argument("--tag", action="store_true")

    p = add("push-release", cmd_push_release, "push main and the signed release tag")
    p.add_argument("--root", default="")

    p = add("build-binary", cmd_build_binary, "build a local .deb for testing")
    p.add_argument("--root", default="")
    p.add_argument("--series", default="")
    p.add_argument("--revision", type=int, default=None)

    p = add("upload", cmd_upload, "upload one source changes file")
    p.add_argument("--root", default="")
    p.add_argument("--changes", required=True)
    p.add_argument("--confirm", action="store_true")

    p = add("upload-latest", cmd_upload_latest, "upload built source packages for the local version")
    p.add_argument("--root", default="")
    p.add_argument("--confirm", action="store_true")

    p = add("watch", cmd_watch, "wait for Launchpad publication")
    p.add_argument("--root", default="")
    p.add_argument("--series", action="append", default=[])
    p.add_argument("--timeout", type=int, default=10800)
    p.add_argument("--interval", type=int, default=60)

    p = add("workspace", cmd_workspace, "open configured roots as Zed projects")
    p.add_argument("--dry-run", action="store_true")

    p = add("zed-tasks", cmd_zed, "install global Zed lifecycle tasks")
    p.add_argument("--dry-run", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, indent=2))
        else:
            print(f"release-board: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
