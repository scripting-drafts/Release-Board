# Operations

## First setup

```bash
cd ~/Release-Board
npm install
./scripts/install.sh
```

`install.sh` links `release-board` into `~/.local/bin`, installs the global Zed
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

## Source diff

**Source diff** shows the changed-file tree for:

```text
Local Sources (HEAD) → Origin branch (default: main) → Released tag
```

Use `--origin` to compare another remote branch, `--release` to choose a
specific tag, `--fetch` to update refs first, and `--open` to launch the changed
files in a new Zed window as a multi-diff. Only changed files are exported, so
empty comparisons do not open. Uncommitted changes are reported but Local
Sources remains `HEAD`. A missing release tag is informational: the view falls
back to Local → Origin and still succeeds. Exported trees live under
`~/.local/state/release-board/diffs`; `compare --clean` removes them.

## Command examples

```bash
bin/release-board board --json
bin/release-board git --fetch
bin/release-board compare --root ~/QA-Sentinels --open
bin/release-board pkg --root ~/QA-Sentinels
bin/release-board launchpad --refresh
bin/release-board build-source --root ~/QA-Sentinels --series "noble resolute"
bin/release-board upload-latest --root ~/QA-Sentinels
bin/release-board watch --root ~/QA-Sentinels
bin/release-board trace --verify
```

Revision selection is automatic: when the published base version matches the
local base, the next upload uses the next `-0ubuntuN~series1` revision.

## Recovery

- A failed build leaves its artifacts under
  `~/.local/state/release-board/builds/<package>/<version>/<series>/`.
- A rejected upload is retried with a new revision; do not reuse a `.changes`
  file.
- The ledger is append-only. To investigate, read it with `trace --json`; do
  not edit historical lines.
