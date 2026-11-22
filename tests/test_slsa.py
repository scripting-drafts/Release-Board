from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from release_board.slsa import (  # noqa: E402
    PAYLOAD_TYPE,
    PREDICATE_TYPE,
    STATEMENT_TYPE,
    build_statement,
    create_attestation,
    pae,
    verify_attestation,
)


class SlsaTests(unittest.TestCase):
    def test_pae(self) -> None:
        self.assertEqual(
            pae("application/test", b"hello"),
            b"DSSEv1 16 application/test 5 hello",
        )

    def test_statement_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "demo_1.0.dsc"
            artifact.write_text("source\n", encoding="utf-8")
            statement = build_statement(
                package="demo",
                version="1.0",
                base_version="1.0",
                series="noble",
                revision=1,
                source_uri="https://example.invalid/demo.git",
                commit="abc123",
                tree="tree123",
                tag="v1.0",
                tag_object="tag123",
                tag_verified=True,
                artifacts=[artifact],
                command=["dpkg-buildpackage", "-S", "-sa"],
                started_on="2026-11-22T10:00:00+00:00",
                finished_on="2026-11-22T10:01:00+00:00",
                builder_version="0.8.0",
            )
            self.assertEqual(statement["_type"], STATEMENT_TYPE)
            self.assertEqual(statement["predicateType"], PREDICATE_TYPE)
            self.assertEqual(statement["subject"][0]["name"], artifact.name)
            self.assertEqual(
                statement["predicate"]["buildDefinition"]["externalParameters"]["source"]["digest"]["gitCommit"],
                "abc123",
            )
            self.assertEqual(
                statement["predicate"]["runDetails"]["builder"]["id"],
                "https://github.com/scripting-drafts/Release-Board",
            )

    def test_unsigned_attestation_and_subjects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "demo_1.0.dsc"
            artifact.write_text("source\n", encoding="utf-8")
            meta = create_attestation(
                package="demo",
                version="1.0",
                base_version="1.0",
                series="noble",
                revision=1,
                source_uri="https://example.invalid/demo.git",
                commit="abc123",
                tree="tree123",
                tag="v1.0",
                tag_object="tag123",
                tag_verified=True,
                artifacts=[artifact],
                command=["dpkg-buildpackage", "-S", "-sa"],
                started_on="2026-11-22T10:00:00+00:00",
                finished_on="2026-11-22T10:01:00+00:00",
                builder_version="0.8.0",
                output_dir=Path(tmp),
                key="",
            )
            self.assertTrue(Path(meta["attestation"]).is_file())
            self.assertTrue(Path(meta["statement"]).is_file())
            verification = verify_attestation(Path(meta["attestation"]), [artifact])
            names = {item["name"]: item for item in verification["checks"]}
            self.assertTrue(names["predicate-type"]["ok"])
            self.assertTrue(names[f"subject:{artifact.name}"]["ok"])
            self.assertFalse(verification["signed"])


if __name__ == "__main__":
    unittest.main()
