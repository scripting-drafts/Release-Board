# Operations

## First setup

```bash
cd ~/qa-release-board
npm install
./scripts/install.sh
```

`install.sh` links `qa-release` into `~/.local/bin`, installs the global Zed
tasks and adds a desktop entry. Edit roots in the app's Settings view; no
source file needs to change.

## Normal release

1. Make the package version change and commit it on `main`.
2. Push `main`.
3. Run **Release: preflight**. Fix every blocker.
4. Run **Release: tag** to create the signed annotated tag.
5. Run **Release: push main and tag**.
6. Run **Release: build source (all series)**.
7. Run **Release: upload latest** and confirm.
8. Run **Release: watch publication**; the ledger commits the published event.
9. Run **Release: trace verify** to check tags, hashes and Launchpad state.

The package's installed provenance is visible with:

```bash
cat /usr/share/doc/qa-dashboard/provenance.json
```

## Command examples

```bash
bin/qa-release board --json
bin/qa-release git --fetch
bin/qa-release pkg --root ~/QA-Sentinels
bin/qa-release launchpad --refresh
bin/qa-release build-source --root ~/QA-Sentinels --series "noble resolute"
bin/qa-release upload-latest --root ~/QA-Sentinels
bin/qa-release watch --root ~/QA-Sentinels
bin/qa-release trace --verify
```

Revision selection is automatic: when the published base version matches the
local base, the next upload uses the next `-0ubuntuN~series1` revision.

## Recovery

- A failed build leaves its artifacts under
  `~/.local/state/qa-release-board/builds/<package>/<version>/<series>/`.
- A rejected upload is retried with a new revision; do not reuse a `.changes`
  file.
- The ledger is append-only. To investigate, read it with `trace --json`; do
  not edit historical lines.
