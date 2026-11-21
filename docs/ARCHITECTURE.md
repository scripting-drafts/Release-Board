# Architecture

The board is a standalone release cockpit. It has no code dependency on QA
Dashboard; only the visual language is shared.

## Components

| Component | Implementation |
| --- | --- |
| Workspace | `release-board workspace` opens configured roots as separate Zed projects in one window. Roots are editable in Settings or `~/.config/release-board/config.json`. Zed 1.28 has no portable workspace file; the launcher recreates the three-project session. |
| Lifecycle tasks | `bin/release-board` exposes check, test, tag, push-release, build-binary, build-source, upload and watch. Zed tasks call the same commands with `$ZED_WORKTREE_ROOT`. |
| Git inspector | `git` reads status, upstream tracking, local/remote branches, divergence, tags and HEAD. `--fetch` is the only network operation. |
| Source diff | `compare` builds changed-file trees for Local HEAD → Origin branch → Released tag, and opens only the changed files in a new Zed multi-diff window. |
| Packaging inspector | Parses `debian/changelog`, `debian/control`, source format, version files, series policy, dput targets and the signing key. |
| Launchpad monitor | Anonymous read-only calls to the Launchpad API for published sources and build records, cached under `~/.cache/release-board`. |
| Traceability | Signed annotated tag plus `debian/provenance.json` included in the source and installed at `/usr/share/doc/<package>/provenance.json`; an append-only JSONL ledger records build, upload and publication events. |

## Build and upload flow

1. Preflight requires a clean `main`, an upstream match, a signed tag at HEAD,
   a pushed tag, a configured dput target and an available signing key.
2. `build-source` exports `git archive HEAD` to a temporary tree, rewrites the
   changelog for the target series, embeds provenance, and runs
   `dpkg-buildpackage -S -sa` with the configured key.
3. The built `.changes`, `.dsc` and `.buildinfo` hashes are appended to the
   ledger. Upload records the `.changes` hash and commits the ledger.
4. `watch` polls Launchpad and records the published source and build IDs,
   committing the ledger after publication.

## Trust boundaries

- The renderer is sandboxed with `contextIsolation` and no Node integration.
- IPC exposes named operations only; roots must be configured paths.
- No shell is exposed. Commands use fixed argv arrays.
- No Launchpad credentials are read or stored. Upload authentication is dput's
  own mechanism; monitoring is anonymous.
- The ledger records evidence, not secrets. It never includes the signing key,
  passphrase, token or environment.
