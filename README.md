# Release Board
Standalone release cockpit for Debian/Ubuntu packages published to Launchpad.
- `bin/release-board` inspects, builds, uploads and traces source packages.
- `npm start` opens the desktop board; it is independent of QA Dashboard.
- `scripts/workspace.sh` opens the configured roots as separate Zed projects.
- Source diff opens Local → Origin → Released tree diffs in Zed.
- Config: `~/.config/release-board/config.json` (mode 0600).
- Pipeline: preflight, test, auto-tag build, push, upload, publication snapshot.
- Docs: `docs/ARCHITECTURE.md`.
- First release: `release-board bootstrap --root PATH` targets the host series; repeat `--series` to opt in.
