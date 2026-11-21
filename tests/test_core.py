from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from qa_release.board import render_markdown  # noqa: E402
from qa_release.launchpad import _source_entry, status_for  # noqa: E402
from qa_release.packaging import parse_control  # noqa: E402
from qa_release.release import (  # noqa: E402
    _next_revision,
    _published_base,
    _published_matches,
)
from qa_release.trace import append_event, read_ledger  # noqa: E402
from qa_release.util import run, series_version, split_version  # noqa: E402


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


class PackagingTests(unittest.TestCase):
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
        self.assertIn("Release board", text)
        self.assertIn("demo", text)
        self.assertIn("1.0-0ubuntu1~noble1", text)


if __name__ == "__main__":
    unittest.main()
