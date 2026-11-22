from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from release_board.board import render_markdown  # noqa: E402
from release_board.bootstrap import BootstrapError, bootstrap, ensure_first_release  # noqa: E402
from release_board.config import deployment_for, load_config, roots, save_config  # noqa: E402
from release_board.compare import (  # noqa: E402
    _anomaly_paths,
    _make_anomaly_repo,
    _parse_name_status,
    _tree,
    compare_root,
)
from release_board.launchpad import _source_entry, _targets, status_for  # noqa: E402
from release_board.packaging import inspect_package, parse_control  # noqa: E402
from release_board.pipeline import release_pipeline  # noqa: E402
from release_board.release import (  # noqa: E402
    _changelog_entry,
    _next_base_version,
    _next_revision,
    _published_base,
    _published_matches,
    _release_records,
    _series_list,
)
from release_board.trace import append_event, read_ledger  # noqa: E402
from release_board.util import git_out, parse_dpkg_changelog, run, series_version, split_version  # noqa: E402


class VersionTests(unittest.TestCase):
    def test_plain_version(self) -> None:
        parsed = split_version("0.5.9")
        self.assertEqual(parsed["base"], "0.5.9")
        self.assertEqual(parsed["revision"], 1)

    def test_series_version(self) -> None:
        parsed = split_version("0.5.9-0ubuntu16~jammy1")
        self.assertEqual(parsed["base"], "0.5.9")
        self.assertEqual(parsed["revision"], 16)

    def test_series_suffix(self) -> None:
        self.assertEqual(series_version("0.5.9", "noble", 1, False), "0.5.9-0ubuntu1~noble1")
        self.assertEqual(series_version("1.3.31", "jammy", 1, True), "1.3.31")
        self.assertEqual(series_version("1.3.31", "noble", 1, True), "1.3.31-0ubuntu1~noble1")

    def test_published_helpers(self) -> None:
        published = "0.5.9-0ubuntu16~noble1"
        self.assertEqual(_published_base(published, "noble", False), "0.5.9")
        self.assertEqual(_next_revision(published, "0.5.9", "noble", False), 17)
        self.assertTrue(_published_matches(published, "0.5.9", "noble", False))
        self.assertFalse(_published_matches(published, "0.5.10", "noble", False))

    def test_native_jammy(self) -> None:
        self.assertEqual(_published_base("1.3.31", "jammy", True), "1.3.31")
        self.assertEqual(_next_revision("1.3.31", "1.3.31", "jammy", True), 2)

    def test_untested_series_is_opt_in(self) -> None:
        profile = {
            "series": ["jammy", "noble", "resolute", "stonking"],
            "untested": ["stonking"],
        }
        self.assertEqual(_series_list(profile), ["jammy", "noble", "resolute"])
        self.assertEqual(_series_list(profile, ["stonking"]), ["stonking"])


class RepairTests(unittest.TestCase):
    def test_next_base_version(self) -> None:
        self.assertEqual(_next_base_version("0.5.10"), "0.5.11")
        self.assertEqual(_next_base_version("1.4.3"), "1.4.4")
        with self.assertRaises(ValueError):
            _next_base_version("main")

    def test_changelog_entry_lists_commits(self) -> None:
        package = {
            "source": "demo",
            "distribution": "noble",
            "urgency": "medium",
            "maintainer": "Test <test@example.invalid>",
        }
        entry = _changelog_entry(package, "1.2.4", ["Fix one", "Fix two"])
        self.assertTrue(entry.startswith("demo (1.2.4) noble; urgency=medium\n"))
        self.assertIn("    - Fix one", entry)
        self.assertIn("    - Fix two", entry)
        self.assertIn(" -- Test <test@example.invalid>  ", entry)

    def test_release_records_filter_package_base_and_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.jsonl"
            ledger.write_text(
                "\n".join(
                    [
                        json.dumps({"package": "demo", "event": "built", "baseVersion": "1.0.0"}),
                        json.dumps({"package": "other", "event": "built", "baseVersion": "1.0.0"}),
                        json.dumps({"package": "demo", "event": "uploaded", "baseVersion": "1.0.1"}),
                        json.dumps({
                            "package": "demo",
                            "event": "published",
                            "provenance": {"baseVersion": "1.0.0"},
                        }),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            records = _release_records({"ledgerPath": str(ledger)}, "demo", "1.0.0")
            self.assertEqual([item["event"] for item in records], ["built", "published"])


class PipelineTests(unittest.TestCase):
    def test_preflight_failure_records_error_in_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            (root / "debian").mkdir(parents=True)
            (root / "debian" / "changelog").write_text(
                """demo (1.0.0) jammy; urgency=medium

  * Initial release.

 -- Test <test@example.invalid>  Sun, 22 Nov 2026 12:00:00 +0100
""",
                encoding="utf-8",
            )
            config = {"roots": [], "profiles": {}, "ledgerPath": str(Path(tmp) / "ledger.jsonl")}
            blocker = "release tag v1.0.0 does not point at HEAD"
            with patch.dict(os.environ, {"XDG_STATE_HOME": tmp}):
                with patch(
                    "release_board.pipeline.check_release",
                    return_value={"ok": False, "error": blocker},
                ):
                    result = release_pipeline(root, config, confirm=True)
            self.assertFalse(result["ok"])
            self.assertIn(blocker, result["error"])
            self.assertEqual(result["state"]["status"], "failed")
            self.assertIn(blocker, result["state"]["error"])
            state_file = Path(tmp) / "release-board" / "pipelines" / "repo.json"
            self.assertTrue(state_file.is_file())
            self.assertIn(blocker, state_file.read_text(encoding="utf-8"))


class PackagingTests(unittest.TestCase):
    def test_missing_changelog_is_not_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist"
            self.assertIsNone(parse_dpkg_changelog(missing))

    def test_parse_control(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "control"
            path.write_text(
                """Source: demo
Section: utils
Maintainer: Test <test@example.invalid>
Build-Depends: debhelper-compat (= 13)
Standards-Version: 4.6.2
Homepage: https://example.invalid

Package: demo
Architecture: all
Depends: ${misc:Depends}
Recommends: other
Description: demo package
 Long description.
""",
                encoding="utf-8",
            )
            data = parse_control(path)
            self.assertEqual(data["source"], "demo")
            self.assertEqual(data["binaries"][0]["package"], "demo")
            self.assertEqual(data["binaries"][0]["architecture"], "all")
            self.assertEqual(data["binaries"][0]["recommends"], "other")


class LaunchpadTests(unittest.TestCase):
    def test_published_state(self) -> None:
        data = {
            "archives": {
                "demo": {
                    "ok": True,
                    "error": "",
                    "sources": [
                        {
                            "id": "1",
                            "package": "demo",
                            "series": "noble",
                            "version": "1.0-0ubuntu2~noble1",
                            "status": "Published",
                            "created": "2026-11-20T10:00:00+00:00",
                            "url": "https://launchpad.net/source",
                        }
                    ],
                    "builds": [
                        {
                            "id": "2",
                            "package": "demo",
                            "series": "noble",
                            "arch": "amd64",
                            "version": "1.0-0ubuntu2~noble1",
                            "state": "Successfully built",
                            "created": "2026-11-20T10:05:00+00:00",
                            "url": "https://launchpad.net/build",
                            "logUrl": "",
                        }
                    ],
                }
            }
        }
        status = status_for(data, "demo", "demo", "noble", "1.0-0ubuntu2~noble1")
        self.assertEqual(status["publishedVersion"], "1.0-0ubuntu2~noble1")
        self.assertEqual(status["state"], "published")
        self.assertEqual(status["buildState"], "built")

    def test_mixed_arch_failure_is_failed(self) -> None:
        data = {
            "archives": {
                "demo": {
                    "ok": True,
                    "sources": [
                        {
                            "id": "1",
                            "package": "demo",
                            "series": "noble",
                            "version": "1.0-0ubuntu1~noble1",
                            "status": "Published",
                            "created": "2026-11-20T10:00:00+00:00",
                        }
                    ],
                    "builds": [
                        {
                            "id": "2",
                            "package": "demo",
                            "series": "noble",
                            "arch": "amd64",
                            "version": "1.0-0ubuntu1~noble1",
                            "state": "Successfully built",
                            "created": "2026-11-20T10:05:00+00:00",
                        },
                        {
                            "id": "3",
                            "package": "demo",
                            "series": "noble",
                            "arch": "arm64",
                            "version": "1.0-0ubuntu1~noble1",
                            "state": "Failed to build",
                            "created": "2026-11-20T10:06:00+00:00",
                        },
                    ],
                }
            }
        }
        status = status_for(data, "demo", "demo", "noble", "1.0-0ubuntu1~noble1")
        self.assertEqual(status["state"], "failed")

    def test_public_source_links(self) -> None:
        entry = _source_entry(
            {
                "self_link": "https://api.launchpad.net/1.0/~u/+archive/ubuntu/demo/+sourcepub/42",
                "distro_series_link": "https://api.launchpad.net/1.0/ubuntu/noble",
                "source_package_name": "demo",
                "source_package_version": "1.0-0ubuntu1~noble1",
                "status": "Published",
                "date_created": "2026-11-20T10:00:00+00:00",
                "date_published": "2026-11-20T10:30:00+00:00",
                "packageupload_link": "https://api.launchpad.net/1.0/ubuntu/noble/+upload/7",
            },
            "u",
            "demo",
        )
        self.assertNotIn("+sourcepub", entry["url"])
        self.assertIn("+packages?", entry["url"])
        self.assertEqual(
            entry["dscUrl"],
            "https://launchpad.net/~u/+archive/ubuntu/demo/+files/demo_1.0-0ubuntu1~noble1.dsc",
        )

    def test_failed_state(self) -> None:
        data = {
            "archives": {
                "demo": {
                    "ok": True,
                    "sources": [
                        {
                            "id": "1",
                            "package": "demo",
                            "series": "noble",
                            "version": "1.0-0ubuntu2~noble1",
                            "status": "Pending",
                            "created": "2026-11-20T10:00:00+00:00",
                        }
                    ],
                    "builds": [
                        {
                            "id": "2",
                            "package": "demo",
                            "series": "noble",
                            "arch": "amd64",
                            "version": "1.0-0ubuntu2~noble1",
                            "state": "Failed to build",
                            "created": "2026-11-20T10:05:00+00:00",
                            "logUrl": "https://launchpad.net/log",
                        }
                    ],
                }
            }
        }
        status = status_for(data, "demo", "demo", "noble", "1.0-0ubuntu2~noble1")
        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["logUrl"], "https://launchpad.net/log")


class CompareTests(unittest.TestCase):
    def test_parse_name_status(self) -> None:
        files = _parse_name_status("M\0src/a.py\0R100\0old.py\0new.py\0D\0gone.txt\0")
        self.assertEqual(
            [(item["status"], item["path"]) for item in files],
            [("D", "gone.txt"), ("R", "new.py"), ("M", "src/a.py")],
        )
        self.assertEqual(files[1]["oldPath"], "old.py")

    def test_tree(self) -> None:
        files = [
            {"status": "M", "path": "src/a.py", "statusDetail": "M"},
            {"status": "A", "path": "docs/b.md", "statusDetail": "A"},
        ]
        tree = _tree(files)
        self.assertEqual([node["name"] for node in tree], ["docs", "src"])
        self.assertEqual(tree[1]["children"][0]["status"], "M")

    def test_anomaly_paths(self) -> None:
        data = {
            "comparisons": [
                {
                    "files": [
                        {"path": "src/a.py", "oldPath": ""},
                        {"path": "src/b.py", "oldPath": "src/c.py"},
                    ]
                }
            ]
        }
        self.assertEqual(_anomaly_paths(data), ["src/a.py", "src/b.py", "src/c.py"])

    def test_anomaly_repo_marks_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            source.mkdir()
            (source / "a.txt").write_text("actual\n", encoding="utf-8")
            (source / "b.txt").write_text("clean\n", encoding="utf-8")
            marked = _make_anomaly_repo(source, ["a.txt"])
            self.assertEqual(marked, ["a.txt"])
            self.assertTrue((source / ".zed" / "settings.json").is_file())
            status = run(["git", "status", "--porcelain"], cwd=source).stdout
            self.assertIn("a.txt", status)
            self.assertNotIn("b.txt", status)

    def test_missing_release_is_informational(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            run(["git", "init", "-q", "-b", "main"], cwd=repo)
            run(["git", "config", "user.name", "Test"], cwd=repo)
            run(["git", "config", "user.email", "test@example.invalid"], cwd=repo)
            (repo / "file.txt").write_text("one\n", encoding="utf-8")
            run(["git", "add", "file.txt"], cwd=repo)
            run(["git", "commit", "-qm", "one"], cwd=repo)
            run(["git", "update-ref", "refs/remotes/origin/main", "HEAD"], cwd=repo)
            data = compare_root(repo, {})
            self.assertTrue(data["ok"])
            self.assertFalse(data["release"]["available"])
            self.assertFalse(data["refs"]["released"]["available"])
            self.assertEqual(len(data["comparisons"]), 1)
            self.assertIn("no release tag", " ".join(data["warnings"]))


class ConfigTests(unittest.TestCase):
    def test_root_remote_and_deployment(self) -> None:
        config = {
            "roots": [
                {
                    "name": "Demo",
                    "path": "/tmp/demo",
                    "remote": "upstream",
                    "host": "gitlab",
                    "deployment": "staging",
                }
            ],
            "deployments": {
                "staging": {
                    "user": "staging-user",
                    "api": "https://example.invalid/api",
                    "archive": "https://example.invalid/archive/{user}",
                    "web": "https://example.invalid/~{user}",
                }
            },
        }
        entries = roots(config)
        self.assertEqual(entries[0]["remote"], "upstream")
        self.assertEqual(entries[0]["host"], "gitlab")
        self.assertEqual(entries[0]["deployment"], "staging")
        deployment = deployment_for("staging", config)
        self.assertEqual(deployment["user"], "staging-user")
        self.assertEqual(deployment["api"], "https://example.invalid/api")

    def test_root_deployment_targets(self) -> None:
        config = {
            "roots": [
                {
                    "name": "Demo",
                    "path": "/tmp/demo",
                    "deployment": "staging",
                }
            ],
            "deployments": {
                "launchpad": {
                    "user": "main-user",
                    "api": "https://api.launchpad.net/1.0",
                    "web": "https://launchpad.net/~{user}/+archive/ubuntu",
                },
                "staging": {
                    "user": "staging-user",
                    "api": "https://staging.example.invalid/api",
                    "web": "https://staging.example.invalid/~{user}",
                },
            },
            "launchpadUser": "fallback",
        }
        with patch("release_board.launchpad._detect_source", return_value="demo"):
            targets = _targets(config)
        self.assertEqual(targets["demo"]["environment"], "staging")
        self.assertEqual(targets["demo"]["user"], "staging-user")
        self.assertEqual(targets["demo"]["api"], "https://staging.example.invalid/api")
        self.assertEqual(targets["qa-dashboard"]["environment"], "launchpad")
        self.assertEqual(targets["qa-dashboard"]["user"], "main-user")

    def test_stored_hosts_and_deployments_replace_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}):
                save_config(
                    {
                        "repoHosts": {"internal": "https://git.example.invalid"},
                        "deployments": {
                            "staging": {
                                "kind": "launchpad",
                                "user": "staging-user",
                                "api": "https://example.invalid/api",
                            }
                        },
                    }
                )
                config = load_config()
        self.assertEqual(set(config["repoHosts"]), {"internal"})
        self.assertEqual(set(config["deployments"]), {"staging"})


class LedgerTests(unittest.TestCase):
    def test_append_and_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            run(["git", "init", "-q", "-b", "main"], cwd=repo)
            run(["git", "config", "user.name", "Test"], cwd=repo)
            run(["git", "config", "user.email", "test@example.invalid"], cwd=repo)
            (repo / "release").mkdir()
            (repo / "README").write_text("ledger\n", encoding="utf-8")
            run(["git", "add", "README"], cwd=repo)
            run(["git", "commit", "-qm", "initial"], cwd=repo)
            config = {
                "ledgerRepo": str(repo),
                "ledgerPath": "release/ledger.jsonl",
            }
            append_event(
                {
                    "event": "built",
                    "package": "demo",
                    "version": "1.0-0ubuntu1~noble1",
                    "series": "noble",
                    "commit": "abc",
                },
                config,
                commit=True,
            )
            entries = read_ledger(config)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["package"], "demo")
            self.assertEqual(run(["git", "log", "-1", "--format=%s"], cwd=repo).stdout.strip().split(":")[0], "release")


class BoardTests(unittest.TestCase):
    def test_markdown(self) -> None:
        board = {
            "generatedAt": "now",
            "summary": {"roots": 1, "packages": 1, "published": 1},
            "roots": [
                {
                    "configuredName": "Demo",
                    "isGit": True,
                    "clean": True,
                    "branch": "main",
                    "ahead": 0,
                    "behind": 0,
                    "head": {"short": "abc123"},
                }
            ],
            "rows": [
                {
                    "package": "demo",
                    "series": "noble",
                    "untested": True,
                    "expectedVersion": "1.0-0ubuntu1~noble1",
                    "publishedVersion": "1.0-0ubuntu1~noble1",
                    "uploadState": "published",
                    "buildState": "built",
                    "state": "published",
                    "commitShort": "abc123",
                    "tag": "v1.0",
                }
            ],
            "packages": [],
            "config": {"ledger": "/tmp/ledger.jsonl"},
        }
        text = render_markdown(board)
        self.assertIn("Release Board", text)
        self.assertIn("demo", text)
        self.assertIn("noble (untested)", text)
        self.assertIn("1.0-0ubuntu1~noble1", text)


class BootstrapTests(unittest.TestCase):
    def _repo(self, root: Path) -> Path:
        repo = root / "Faultlab"
        repo.mkdir()
        run(["git", "init", "-q", "-b", "main"], cwd=repo)
        run(["git", "config", "user.name", "Test User"], cwd=repo)
        run(["git", "config", "user.email", "test@example.invalid"], cwd=repo)
        (repo / "README.md").write_text(
            "# Faultlab\n\nClassify and isolate a degrading CPU core.\n", encoding="utf-8"
        )
        (repo / "bin").mkdir()
        (repo / "bin" / "faultlab").write_text("#!/bin/sh\n", encoding="utf-8")
        run(["git", "add", "-A"], cwd=repo)
        run(["git", "commit", "-qm", "initial"], cwd=repo)
        return repo

    def test_first_release_scaffold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            repo = self._repo(home)
            env = {"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config")}
            with patch.dict(os.environ, env), \
                 patch("release_board.bootstrap._signing_key_ok",
                       return_value=(True, "secret key present")), \
                 patch("release_board.packaging._signing_key_ok",
                       return_value=(True, "secret key present")):
                config = load_config()
                config["roots"] = [
                    {"name": "Faultlab", "path": str(repo), "deployment": "launchpad"}
                ]
                payload = bootstrap(repo, config, commit=False)
                self.assertTrue(payload["ok"])
                self.assertTrue((repo / "debian" / "changelog").is_file())
                self.assertTrue((repo / "debian" / "control").is_file())
                self.assertTrue((repo / "debian" / "rules").is_file())
                self.assertTrue((repo / "debian" / "source" / "format").is_file())
                self.assertTrue((repo / "debian" / "faultlab.install").is_file())
                self.assertTrue((home / ".dput.cf").is_file())
                self.assertIn("[faultlab]", (home / ".dput.cf").read_text())
                reloaded = load_config()
                self.assertIn("faultlab", reloaded["profiles"])
                package = inspect_package(repo, reloaded)
                self.assertEqual(package["errors"], [])
                self.assertTrue(package["dput"]["configured"])

    def test_ensure_first_release_bootstraps_only_debian_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            repo = self._repo(home)
            env = {"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config")}
            with patch.dict(os.environ, env), \
                 patch("release_board.bootstrap._signing_key_ok",
                       return_value=(True, "secret key present")):
                config = load_config()
                config["roots"] = [
                    {"name": "Faultlab", "path": str(repo), "deployment": "launchpad"}
                ]
                payload = ensure_first_release(repo, config)
                self.assertEqual(payload["action"], "bootstrapped")
                self.assertTrue(payload["ok"])
                self.assertTrue((repo / "debian" / "changelog").is_file())
                self.assertFalse(git_out(["status", "--porcelain"], repo))

                config["deployments"] = {"static": {"kind": "static"}}
                other_root = home / "other"
                other_root.mkdir()
                other = self._repo(other_root)
                config["roots"] = [
                    {"name": "Faultlab", "path": str(other), "deployment": "static"}
                ]
                payload = ensure_first_release(other, config)
                self.assertEqual(payload["action"], "not-debian")
                self.assertFalse((other / "debian").exists())

    def test_scaffold_is_limited_to_source_deployments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            repo = self._repo(home)
            env = {"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config")}
            with patch.dict(os.environ, env):
                config = load_config()
                config["deployments"] = {"static": {"kind": "static"}}
                config["roots"] = [
                    {"name": "Faultlab", "path": str(repo), "deployment": "static"}
                ]
                with self.assertRaises(BootstrapError):
                    bootstrap(repo, config)


if __name__ == "__main__":
    unittest.main()
