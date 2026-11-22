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
3. Run **Release: preflight**. Fix every blocker; a missing release tag is
   informational because the build creates it.
4. Run **Release: build source (all series)**. The signed annotated tag is
   created automatically for the local base version.
5. Run **Release: push main and tag**.
6. Run **Release: upload latest** and confirm.
7. Run **Release: watch publication**; the ledger commits the published event.
8. Run **Release: trace verify** to check tags, hashes and Launchpad state.

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
back to Local → Origin and still succeeds.

Clicking any source card opens the three source trees in one Zed window. Each
tree is a temporary Git repository whose files changed by the comparison are
left dirty, so Zed's project panel shows `M`/`D` status badges next to them.
Clicking a comparison opens its files; clicking a file opens that file's diff.
Exported trees live under `~/.local/state/release-board/diffs`; `compare --clean`
removes them.

## Automated pipeline

The pipeline runs the complete release sequence:

```bash
release-board pipeline --root ~/QA-Sentinels --confirm
```

Steps:

1. Local preflight; ahead/behind is informational.
2. Fetch and rebase `main` onto the configured remote when behind.
3. Package tests.
4. Source build; the signed tag is created automatically.
5. Push `main` and the tag to the configured remote.
6. Upload every built source package.
7. Take one publication snapshot.

Publication does not block the pipeline. To poll until publication, run
`release-board watch` without `--once`.

Pipeline state is saved under `~/.local/state/release-board/pipelines/`.
Completed steps are skipped when resuming:

```bash
release-board pipeline --root ~/QA-Sentinels --status
release-board pipeline --root ~/QA-Sentinels --resume --confirm
```

The app's pipeline panel provides **Start**, **Pause**, **Stop**, **Resume** and
**Resolve conflicts**. Pause suspends the task process group; Resume continues
it. Stop terminates the group, and the saved state can be resumed. Resolve
conflicts opens the conflicted files in Zed.

Pipelines for different project roots can run concurrently. A second task for
the same root is refused. Ledger appends and commits are serialized with a
file lock, so concurrent projects cannot corrupt the shared ledger.

If the sync step finds remote commits, it rebases before building. A rebase
conflict blocks the pipeline and records the conflicted paths. Resolve them,
run `git rebase --continue`, then resume the pipeline.

## Repository and deployment settings

Each configured root has a Git remote and a deployment environment. They are
editable in Settings and in `~/.config/release-board/config.json`.
Deployment environments define the Launchpad user, API base, archive base and
web base. The default environment is `launchpad`.

## Command examples

```bash
bin/release-board board --json
bin/release-board git --fetch
bin/release-board compare --root ~/QA-Sentinels --open
bin/release-board pkg --root ~/QA-Sentinels
bin/release-board launchpad --refresh
bin/release-board build-source --root ~/QA-Sentinels --series "noble resolute"
bin/release-board upload-latest --root ~/QA-Sentinels
bin/release-board pipeline --root ~/QA-Sentinels
bin/release-board watch --root ~/QA-Sentinels --once
bin/release-board trace --verify
bin/release-board slsa --attestation <file.intoto.jsonl> --artifact <artifact>
```

Revision selection is automatic: when the published base version matches the
local base, the next upload uses the next `-0ubuntuN~series1` revision.

## Untested preview series

`stonking` (Ubuntu 26.10) is listed for visibility but marked `untested` in
the package matrix. It is excluded from the default build, watch and preflight
sets. Build it only when intended:

```bash
bin/release-board build-source --root ~/QA-Sentinels --series stonking
```

No runtime testing on 26.10 is planned in the near term. Do not describe
stonking packages as supported or tested.

## SLSA provenance

Each source build writes a signed SLSA v1 attestation to
`release/attestations/<package>/<version>/<series>/`. The DSSE envelope is the
authoritative provenance; `.slsa.json` is the readable statement. Upload and
`trace --verify` validate its signature, predicate type and subject hashes.

## Recovery

- A failed build leaves its artifacts under
  `~/.local/state/release-board/builds/<package>/<version>/<series>/`.
- A rejected upload is retried with a new revision; do not reuse a `.changes`
  file.
- The ledger is append-only. To investigate, read it with `trace --json`; do
  not edit historical lines.
