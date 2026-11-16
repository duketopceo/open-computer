# Open Computer desktop shell

A native Rust app on the Rune UI toolkit. This slice has first-run onboarding only:
Connect Cloudflare (address and access secret of an already deployed cloud core), Add model
key, Memory toggle, Test connection. **Nothing deploys from here**: there is no deploy
button, and no code in this directory talks to Cloudflare.

## Layout

| Path | What |
|---|---|
| `crates/shell-core/` | Pure crate, no Rune or GPU: onboarding state machine, local core client, sidecar launcher, secret store trait (in-memory fake and OS keyring), Test connection round trip |
| `src/` | Rune application: `main.rs`, `app.rs` (state and actions), `screens/` (one file per onboarding screen) |
| `crates/shell-core/tests/` | Contract tests over `core/contracts/local-api/`, client and launcher tests, one ignored end-to-end test |

## Test

```sh
cargo fmt --check
cargo clippy --workspace -- -D warnings
cargo test -p shell-core        # no display needed
cargo check --workspace
```

The contract test parses every file in `core/contracts/local-api/` into the client types;
the TypeScript server tests assert the same files.

The end-to-end test is ignored unless you point it at a built local core:

```sh
OC_CORE_BIN="/path/to/core-binary" cargo test -p shell-core --test e2e -- --ignored
OC_CORE_BIN="node path/to/core/main.js" cargo test -p shell-core --test e2e -- --ignored
```

## Run

```sh
cargo run -- --print-state   # onboarding state as JSON, no window
cargo run                    # opens the window
```

The Test connection button launches the local core named by `OC_CORE_BIN` (same format as
above), hands it the saved secrets and settings in memory, and shows backend, model and
memory results. Secrets live in the OS keyring; screens show only set or not set.

## When something does not work

- **Log:** the app writes `~/.local/state/open-computer/desktop.log` (rotated at 1 MB, `RUST_LOG=debug` for more).
  Every connection, keyring, Wrangler and agent call is logged with its outcome and error, never a secret.
  Settings > About > Copy diagnostics copies the last 200 lines, redacted.
- **Self-test:** `open-computer --self-test` runs the checks the app depends on without a window and prints one
  `PASS`/`FAIL`/`SKIP` line each: keyring write/read/delete of a dummy entry, the saved connection (dry run, never
  prints the token), the core's `/v1/diag/bindings`, the OpenRouter key through the core, `wrangler whoami`, and
  `hermes --version` / `opencode --version`. Exit code 1 if anything failed.
- **Keyring refuses or hangs:** the reason is shown on Get started. The core address is saved either way. You can choose
  to use the token in `~/.config/open-computer/cloud.json` instead; the choice is remembered and the token is read from
  that file each time, never copied.
- **Hermes or OpenCode fails:** the chat shows what the program printed to stderr and the documented fix
  (`hermes setup`, `opencode auth login`). With a real OpenRouter key on this computer you can hand it to them
  (`OPENROUTER_API_KEY`) under Settings > Agent harness.
- **Task history** is kept under `~/.local/state/open-computer/threads/` (newest 100) and restored on start.

## Build requirements

Linux needs the usual windowing and GPU libraries for Rune (X11 or Wayland, Vulkan,
`libxkbcommon`) and OpenSSL headers for `native-tls`; the keyring uses the Secret Service
over D-Bus in pure Rust (no `libdbus`). macOS and Windows need no extra libraries.

Licenses: see `THIRD_PARTY_NOTICES.md`.
