"""SLSA v1 provenance statements and DSSE signing for release artifacts."""

from __future__ import annotations

import base64
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .util import now_iso, run, sha256_file, which, write_json_atomic

STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PREDICATE_TYPE = "https://slsa.dev/provenance/v1"
PAYLOAD_TYPE = "application/vnd.in-toto+json"
BUILD_TYPE = "https://github.com/scripting-drafts/Release-Board/slsa/debian-source/v1"
BINARY_BUILD_TYPE = "https://github.com/scripting-drafts/Release-Board/slsa/debian-binary/v1"
BUILDER_ID = "https://github.com/scripting-drafts/Release-Board"


def _descriptor(path: Path, name: str | None = None) -> dict[str, Any]:
    return {
        "name": name or path.name,
        "digest": {"sha256": sha256_file(path)},
        "mediaType": "application/octet-stream",
    }


def build_statement(
    *,
    package: str,
    version: str,
    base_version: str,
    series: str,
    revision: int,
    source_uri: str,
    commit: str,
    tree: str,
    tag: str,
    tag_object: str,
    tag_verified: bool,
    artifacts: list[Path],
    command: list[str],
    started_on: str,
    finished_on: str,
    builder_version: str,
    build_type: str = BUILD_TYPE,
) -> dict[str, Any]:
    subjects = [_descriptor(path) for path in artifacts]
    return {
        "_type": STATEMENT_TYPE,
        "subject": subjects,
        "predicateType": PREDICATE_TYPE,
        "predicate": {
            "buildDefinition": {
                "buildType": build_type,
                "externalParameters": {
                    "source": {
                        "uri": source_uri,
                        "digest": {"gitCommit": commit},
                        "ref": tag,
                    },
                    "package": package,
                    "version": version,
                    "baseVersion": base_version,
                    "series": series,
                    "revision": revision,
                },
                "internalParameters": {
                    "builder": f"release-board/{builder_version}",
                    "command": command,
                    "tree": tree,
                    "tagObject": tag_object,
                    "tagVerified": bool(tag_verified),
                },
                "resolvedDependencies": [
                    {
                        "uri": source_uri,
                        "digest": {"gitCommit": commit},
                        "annotations": {"ref": tag, "tree": tree},
                    }
                ],
            },
            "runDetails": {
                "builder": {
                    "id": BUILDER_ID,
                    "version": {"release-board": builder_version},
                },
                "metadata": {
                    "invocationId": str(uuid.uuid4()),
                    "startedOn": started_on,
                    "finishedOn": finished_on,
                },
                "byproducts": [],
            },
        },
    }


def pae(payload_type: str, payload: bytes) -> bytes:
    return (
        b"DSSEv1 "
        + str(len(payload_type)).encode()
        + b" "
        + payload_type.encode()
        + b" "
        + str(len(payload)).encode()
        + b" "
        + payload
    )


def _sign_pae(pae_bytes: bytes, key: str) -> tuple[str, bool]:
    if not key or not which("gpg"):
        return "", False
    with tempfile.TemporaryDirectory(prefix="release-board-dsse-") as tmp:
        payload_path = Path(tmp) / "pae.bin"
        sig_path = Path(tmp) / "pae.sig"
        payload_path.write_bytes(pae_bytes)
        proc = run(
            [
                "gpg",
                "--batch",
                "--yes",
                "--detach-sign",
                "--output",
                str(sig_path),
                "--local-user",
                key,
                str(payload_path),
            ],
            timeout=120,
        )
        if proc.returncode != 0 or not sig_path.is_file():
            return "", False
        return base64.b64encode(sig_path.read_bytes()).decode("ascii"), True


def create_attestation(
    *,
    package: str,
    version: str,
    base_version: str,
    series: str,
    revision: int,
    source_uri: str,
    commit: str,
    tree: str,
    tag: str,
    tag_object: str,
    tag_verified: bool,
    artifacts: list[Path],
    command: list[str],
    started_on: str,
    finished_on: str,
    builder_version: str,
    output_dir: Path,
    key: str,
    build_type: str = BUILD_TYPE,
) -> dict[str, Any]:
    statement = build_statement(
        package=package,
        version=version,
        base_version=base_version,
        series=series,
        revision=revision,
        source_uri=source_uri,
        commit=commit,
        tree=tree,
        tag=tag,
        tag_object=tag_object,
        tag_verified=tag_verified,
        artifacts=artifacts,
        command=command,
        started_on=started_on,
        finished_on=finished_on,
        builder_version=builder_version,
        build_type=build_type,
    )
    payload = json.dumps(statement, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature, signed = _sign_pae(pae(PAYLOAD_TYPE, payload), key)
    envelope = {
        "payloadType": PAYLOAD_TYPE,
        "payload": base64.b64encode(payload).decode("ascii"),
        "signatures": [{"keyid": key, "sig": signature}] if signed else [],
    }
    ensure = output_dir / f"{package}_{version}_{series}.intoto.jsonl"
    statement_path = output_dir / f"{package}_{version}_{series}.slsa.json"
    write_json_atomic(ensure, envelope, 0o644)
    write_json_atomic(statement_path, statement, 0o644)
    return {
        "standard": "SLSA v1",
        "package": package,
        "version": version,
        "series": series,
        "statementType": STATEMENT_TYPE,
        "predicateType": PREDICATE_TYPE,
        "buildType": build_type,
        "attestation": str(ensure),
        "attestationSha256": sha256_file(ensure),
        "statement": str(statement_path),
        "statementSha256": sha256_file(statement_path),
        "signed": signed,
        "keyid": key if signed else "",
        "subject": statement["subject"],
    }


def verify_attestation(attestation: Path, artifacts: list[Path] | None = None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    result: dict[str, Any] = {"ok": False, "signed": False, "checks": checks, "statement": None}
    path = Path(attestation)
    if not path.is_file():
        checks.append({"name": "attestation-file", "ok": False, "detail": f"missing {path}"})
        return result
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        checks.append({"name": "attestation-json", "ok": False, "detail": str(exc)})
        return result
    checks.append({"name": "attestation-json", "ok": True, "detail": str(path)})
    if envelope.get("payloadType") != PAYLOAD_TYPE:
        checks.append({"name": "dsse-payload-type", "ok": False, "detail": str(envelope.get("payloadType"))})
        return result
    checks.append({"name": "dsse-payload-type", "ok": True, "detail": PAYLOAD_TYPE})
    try:
        payload = base64.b64decode(str(envelope.get("payload", "")))
        statement = json.loads(payload)
    except (ValueError, TypeError) as exc:
        checks.append({"name": "dsse-payload", "ok": False, "detail": str(exc)})
        return result
    result["statement"] = statement
    checks.append({"name": "dsse-payload", "ok": True, "detail": "decoded"})
    checks.append(
        {
            "name": "statement-type",
            "ok": statement.get("_type") == STATEMENT_TYPE,
            "detail": str(statement.get("_type")),
        }
    )
    checks.append(
        {
            "name": "predicate-type",
            "ok": statement.get("predicateType") == PREDICATE_TYPE,
            "detail": str(statement.get("predicateType")),
        }
    )

    by_name = {Path(item).name: Path(item) for item in (artifacts or [])}
    for subject in statement.get("subject", []):
        name = str(subject.get("name", ""))
        expected = str((subject.get("digest") or {}).get("sha256", ""))
        actual = by_name.get(name)
        if actual and actual.is_file():
            checks.append(
                {
                    "name": f"subject:{name}",
                    "ok": sha256_file(actual) == expected,
                    "detail": str(actual),
                }
            )
        else:
            checks.append({"name": f"subject:{name}", "ok": False, "detail": "artifact not available"})

    signatures = envelope.get("signatures") or []
    result["signed"] = bool(signatures)
    if signatures:
        verify_ok = False
        detail = "signature verification failed"
        with tempfile.TemporaryDirectory(prefix="release-board-dsse-verify-") as tmp:
            payload_path = Path(tmp) / "pae.bin"
            payload_path.write_bytes(pae(PAYLOAD_TYPE, payload))
            for index, signature in enumerate(signatures):
                try:
                    sig = base64.b64decode(str(signature.get("sig", "")))
                except ValueError:
                    continue
                sig_path = Path(tmp) / f"sig-{index}.bin"
                sig_path.write_bytes(sig)
                proc = run(["gpg", "--batch", "--verify", str(sig_path), str(payload_path)], timeout=60)
                if proc.returncode == 0:
                    verify_ok = True
                    detail = (proc.stderr or proc.stdout).strip()[:240]
                    break
        checks.append({"name": "dsse-signature", "ok": verify_ok, "detail": detail})
    else:
        checks.append({"name": "dsse-signature", "ok": False, "detail": "no signatures"})

    result["ok"] = all(item["ok"] for item in checks)
    return result
