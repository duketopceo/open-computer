//! Secret storage. Values go in and come out only through [`SecretStore::get`], which the
//! core launcher uses to hand them to the sidecar in memory. Everything the UI sees is a
//! set or not-set flag.

use std::collections::BTreeMap;
use std::fmt;
use std::sync::Mutex;

/// The secrets the desktop shell holds in this slice.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Slot {
    /// The access secret of an already deployed cloud core.
    CloudAccess,
    /// The model provider key (an OpenRouter key by default).
    ModelKey,
}

impl Slot {
    pub const ALL: [Slot; 2] = [Slot::CloudAccess, Slot::ModelKey];

    /// The name the local core API uses for this secret (`secrets-load-request.json`).
    pub fn api_name(self) -> &'static str {
        match self {
            Slot::CloudAccess => "cloud_access",
            Slot::ModelKey => "model_key",
        }
    }
}

/// A storage failure in words that are safe to show. Never carries a secret value.
#[derive(Clone, PartialEq, Eq)]
pub struct SecretError(pub String);

impl fmt::Display for SecretError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl fmt::Debug for SecretError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "SecretError({:?})", self.0)
    }
}

impl std::error::Error for SecretError {}

pub trait SecretStore: Send + Sync {
    fn set(&self, slot: Slot, value: &str) -> Result<(), SecretError>;
    /// Only for handing the value to the local core. UI code uses [`SecretStore::is_set`].
    fn get(&self, slot: Slot) -> Result<Option<String>, SecretError>;
    fn delete(&self, slot: Slot) -> Result<(), SecretError>;

    fn is_set(&self, slot: Slot) -> bool {
        matches!(self.get(slot), Ok(Some(v)) if !v.is_empty())
    }
}

/// The keyring first; when it cannot be used (locked, no service) secrets are kept in memory for this session only, so the
/// app still works and says so, instead of blocking. Nothing is ever written to a file.
pub struct ResilientSecrets {
    primary: Box<dyn SecretStore>,
    session: InMemorySecrets,
    volatile: std::sync::atomic::AtomicBool,
}

impl ResilientSecrets {
    pub fn new(primary: Box<dyn SecretStore>) -> Self {
        Self {
            primary,
            session: InMemorySecrets::new(),
            volatile: std::sync::atomic::AtomicBool::new(false),
        }
    }

    /// True once a secret had to be kept in memory because the keyring refused it.
    pub fn is_volatile(&self) -> bool {
        self.volatile.load(std::sync::atomic::Ordering::Relaxed)
    }
}

impl SecretStore for ResilientSecrets {
    fn set(&self, slot: Slot, value: &str) -> Result<(), SecretError> {
        match self.primary.set(slot, value) {
            Ok(()) => {
                let _ = self.session.delete(slot);
                Ok(())
            }
            Err(_) => {
                self.volatile
                    .store(true, std::sync::atomic::Ordering::Relaxed);
                self.session.set(slot, value)
            }
        }
    }

    fn get(&self, slot: Slot) -> Result<Option<String>, SecretError> {
        if let Ok(Some(v)) = self.session.get(slot) {
            return Ok(Some(v));
        }
        self.primary.get(slot).or(Ok(None))
    }

    fn delete(&self, slot: Slot) -> Result<(), SecretError> {
        let _ = self.session.delete(slot);
        self.primary.delete(slot)
    }
}

/// In-memory store for tests and headless runs. Its `Debug` output shows only flags.
#[derive(Default)]
pub struct InMemorySecrets {
    values: Mutex<BTreeMap<Slot, String>>,
}

impl InMemorySecrets {
    pub fn new() -> Self {
        Self::default()
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, BTreeMap<Slot, String>> {
        self.values.lock().unwrap_or_else(|e| e.into_inner())
    }
}

impl fmt::Debug for InMemorySecrets {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let mut d = f.debug_struct("InMemorySecrets");
        for slot in Slot::ALL {
            d.field(slot.api_name(), &self.is_set(slot));
        }
        d.finish()
    }
}

impl SecretStore for InMemorySecrets {
    fn set(&self, slot: Slot, value: &str) -> Result<(), SecretError> {
        self.lock().insert(slot, value.to_owned());
        Ok(())
    }

    fn get(&self, slot: Slot) -> Result<Option<String>, SecretError> {
        Ok(self.lock().get(&slot).cloned())
    }

    fn delete(&self, slot: Slot) -> Result<(), SecretError> {
        self.lock().remove(&slot);
        Ok(())
    }
}

/// The operating system keyring: Keychain on macOS, Credential Manager on Windows, the
/// Secret Service on Linux.
#[derive(Debug, Clone)]
pub struct KeyringSecrets {
    service: String,
}

impl KeyringSecrets {
    pub const DEFAULT_SERVICE: &'static str = "open-computer";

    pub fn new() -> Self {
        Self::with_service(Self::DEFAULT_SERVICE)
    }

    pub fn with_service(service: impl Into<String>) -> Self {
        Self {
            service: service.into(),
        }
    }

    fn entry(&self, slot: Slot) -> Result<keyring::Entry, SecretError> {
        keyring::Entry::new(&self.service, slot.api_name()).map_err(keyring_error)
    }
}

impl Default for KeyringSecrets {
    fn default() -> Self {
        Self::new()
    }
}

fn keyring_error(e: keyring::Error) -> SecretError {
    // The keyring error text never contains the stored value, but keep our own wording so
    // the UI shows plain words.
    let words = match e {
        keyring::Error::NoStorageAccess(_) | keyring::Error::PlatformFailure(_) => {
            "The system keyring is not available. Unlock it or start your keyring service."
        }
        keyring::Error::NoEntry => "That secret is not stored.",
        _ => "The system keyring refused the request.",
    };
    SecretError(words.to_owned())
}

impl SecretStore for KeyringSecrets {
    fn set(&self, slot: Slot, value: &str) -> Result<(), SecretError> {
        self.entry(slot)?.set_password(value).map_err(keyring_error)
    }

    fn get(&self, slot: Slot) -> Result<Option<String>, SecretError> {
        match self.entry(slot)?.get_password() {
            Ok(v) => Ok(Some(v)),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(e) => Err(keyring_error(e)),
        }
    }

    fn delete(&self, slot: Slot) -> Result<(), SecretError> {
        match self.entry(slot)?.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(e) => Err(keyring_error(e)),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SENTINEL: &str = "sentinel-secret-value-123";

    #[test]
    fn in_memory_round_trip_and_flags() {
        let s = InMemorySecrets::new();
        assert!(!s.is_set(Slot::ModelKey));
        s.set(Slot::ModelKey, SENTINEL).unwrap();
        assert!(s.is_set(Slot::ModelKey));
        assert!(!s.is_set(Slot::CloudAccess));
        assert_eq!(s.get(Slot::ModelKey).unwrap().as_deref(), Some(SENTINEL));
        s.delete(Slot::ModelKey).unwrap();
        assert!(!s.is_set(Slot::ModelKey));
    }

    #[test]
    fn debug_output_shows_flags_only() {
        let s = InMemorySecrets::new();
        s.set(Slot::CloudAccess, SENTINEL).unwrap();
        let shown = format!("{s:?} {:#?}", s);
        assert!(!shown.contains(SENTINEL));
        assert!(shown.contains("cloud_access: true"));
        assert!(shown.contains("model_key: false"));
    }

    #[test]
    fn empty_value_counts_as_not_set() {
        let s = InMemorySecrets::new();
        s.set(Slot::ModelKey, "").unwrap();
        assert!(!s.is_set(Slot::ModelKey));
    }

    #[test]
    fn keyring_store_debug_has_no_values() {
        let k = KeyringSecrets::with_service("open-computer-test");
        assert!(!format!("{k:?}").contains(SENTINEL));
    }

    struct Refusing;
    impl SecretStore for Refusing {
        fn set(&self, _: Slot, _: &str) -> Result<(), SecretError> {
            Err(SecretError(
                "The system keyring is not available. Unlock it or start your keyring service."
                    .into(),
            ))
        }
        fn get(&self, _: Slot) -> Result<Option<String>, SecretError> {
            Err(SecretError("locked".into()))
        }
        fn delete(&self, _: Slot) -> Result<(), SecretError> {
            Ok(())
        }
    }

    #[test]
    fn a_locked_keyring_falls_back_to_this_session_only_and_says_so() {
        let s = ResilientSecrets::new(Box::new(Refusing));
        assert!(!s.is_volatile());
        s.set(Slot::CloudAccess, "value-123").unwrap();
        assert!(s.is_volatile());
        assert!(s.is_set(Slot::CloudAccess));
        assert_eq!(
            s.get(Slot::CloudAccess).unwrap().as_deref(),
            Some("value-123")
        );
        assert!(!s.is_set(Slot::ModelKey));
    }

    #[test]
    fn a_working_keyring_is_used_and_nothing_stays_in_memory() {
        let s = ResilientSecrets::new(Box::new(InMemorySecrets::new()));
        s.set(Slot::ModelKey, "abc").unwrap();
        assert!(!s.is_volatile());
        assert!(s.is_set(Slot::ModelKey));
    }
}
