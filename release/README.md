# Release ledger

`ledger.jsonl` is append-only. Each line records a build, upload or publication
event with the package version, source commit, signed tag, artifact hashes and
Launchpad identifiers. The board commits this directory after an upload and
after publication. Do not rewrite history.
