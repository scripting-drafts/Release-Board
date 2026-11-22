# Release evidence
`ledger.jsonl` is an append-only index of build, upload and publication events.
Each build also produces a signed **SLSA v1 provenance** attestation under
`release/attestations/<package>/<version>/<series>/`:
- `.intoto.jsonl` — DSSE envelope around an in-toto statement
- `.slsa.json` — readable SLSA statement
The ledger commits the attestation directory after upload and publication.
Historical entries without SLSA provenance are reported as missing evidence.
