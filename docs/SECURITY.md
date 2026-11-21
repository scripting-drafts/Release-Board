# Security

- Configuration is stored at `~/.config/qa-release-board/config.json` with mode
  `0600`. It contains paths, the Launchpad user and the public signing key ID.
- No Launchpad token, password or signing passphrase is stored.
- The signing key is used through `gpg-agent`; the board never reads the
  private key.
- The renderer runs sandboxed with context isolation and no Node integration.
- External links are restricted to `launchpad.net`.
- Lifecycle tasks are allowlisted and run with fixed argv, never a shell.
- Builds run from `git archive HEAD`; the working tree is not modified.
- Upload requires a clean, pushed `main`, a signed tag pushed to origin, and
  explicit confirmation in the app or terminal.
- Monitoring is anonymous and read-only except for the local cache and ledger.
