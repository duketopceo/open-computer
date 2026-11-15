//! Window-free logic of the Open Computer desktop shell.
//!
//! Nothing here depends on Rune or a GPU, so every test runs without a display.
//!
//! - [`onboarding`]: the four-step first-run state machine and its JSON state.
//! - [`core_client`]: the typed loopback HTTP client for the local core sidecar.
//! - [`core_process`]: launching the sidecar, reading its ready line, stopping it.
//! - [`secrets`]: the secret-storage trait, an in-memory fake and the OS keyring.
//! - [`checks`]: the Test connection round trip against a running core.

pub mod checks;
pub mod core_client;
pub mod core_process;
pub mod onboarding;
pub mod secrets;
