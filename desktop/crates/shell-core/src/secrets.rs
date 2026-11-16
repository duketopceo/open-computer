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

    /// Saves to the durable store only, with no in-memory fallback: the caller wants to know whether it really persisted.
    fn set_persistent(&self, slot: Slot, value: &str) -> Result<(), SecretError> {
        self.set(slot, value)
    }

    /// The most recent keyring failure, in words fit to show, until a later operation succeeds.
    fn last_failure(&self) -> Option<String> {
        None
    }

    /// True once a secret is being kept for this session only.
    fn is_volatile(&self) -> bool {
        false
    }
}

/// The keyring first; when it cannot be used (locked, no service, a prompt nobody answered) a secret is kept in memory for
/// this session only, so the app still works. The failure is recorded with its reason and shown, never swallowed. Nothing
/// is ever written to a file.
pub struct ResilientSecrets {
    primary: Box<dyn SecretStore>,
    session: InMemorySecrets,
    volatile: std::sync::atomic::AtomicBool,
    failure: Mutex<Option<String>>,
}

impl ResilientSecrets {
    pub fn new(primary: Box<dyn SecretStore>) -> Self {
        Self {
            primary,
            session: InMemorySecrets::new(),
            volatile: std::sync::atomic::AtomicBool::new(false),
            failure: Mutex::new(None),
        }
    }

    fn note(&self, failure: Option<String>) {
        *self.failure.lock().unwrap_or_else(|e| e.into_inner()) = failure;
    }
}

impl SecretStore for ResilientSecrets {
    fn set(&self, slot: Slot, value: &str) -> Result<(), SecretError> {
        match self.primary.set(slot, value) {
            Ok(()) => {
                let _ = self.session.delete(slot);
                self.note(None);
                Ok(())
            }
            Err(e) => {
                crate::log_warn!(
                    "keyring",
                    "write of {} failed, keeping it for this session only: {e}",
                    slot.api_name()
                );
                self.note(Some(e.0));
                self.volatile
                    .store(true, std::sync::atomic::Ordering::Relaxed);
                self.session.set(slot, value)
            }
        }
    }

    fn set_persistent(&self, slot: Slot, value: &str) -> Result<(), SecretError> {
        match self.primary.set(slot, value) {
            Ok(()) => {
                let _ = self.session.delete(slot);
                self.note(None);
                Ok(())
            }
            Err(e) => {
                self.note(Some(e.0.clone()));
                Err(e)
            }
        }
    }

    fn get(&self, slot: Slot) -> Result<Option<String>, SecretError> {
        if let Ok(Some(v)) = self.session.get(slot) {
            return Ok(Some(v));
        }
        match self.primary.get(slot) {
            Ok(v) => Ok(v),
            Err(e) => {
                crate::log_debug!("keyring", "read of {} failed: {e}", slot.api_name());
                self.note(Some(e.0));
                Ok(None)
            }
        }
    }

    fn delete(&self, slot: Slot) -> Result<(), SecretError> {
        let _ = self.session.delete(slot);
        self.primary.delete(slot)
    }

    fn last_failure(&self) -> Option<String> {
        self.failure
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone()
    }

    fn is_volatile(&self) -> bool {
        self.volatile.load(std::sync::atomic::Ordering::Relaxed)
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

/// Set once a keyring call timed out, so later calls fail at once instead of freezing the window again.
static KEYRING_STALLED: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

fn keyring_timeout() -> std::time::Duration {
    let ms = std::env::var("OC_KEYRING_TIMEOUT_MS")
        .ok()
        .and_then(|v| v.parse::<u64>().ok())
        .unwrap_or(8000);
    std::time::Duration::from_millis(ms)
}

/// Runs `f` on its own thread and gives up after `timeout`, so a keyring that is locked or showing a prompt nobody answers cannot
/// freeze the app. The abandoned thread is left to finish by itself.
pub fn bounded<T: Send + 'static>(
    timeout: std::time::Duration,
    f: impl FnOnce() -> T + Send + 'static,
) -> Option<T> {
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let _ = tx.send(f());
    });
    rx.recv_timeout(timeout).ok()
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

    /// One keyring operation, bounded in time, logged without the value.
    fn run<T: Send + 'static>(
        &self,
        slot: Slot,
        verb: &'static str,
        op: impl FnOnce(keyring::Entry) -> Result<T, keyring::Error> + Send + 'static,
    ) -> Result<T, SecretError> {
        use std::sync::atomic::Ordering;
        if KEYRING_STALLED.load(Ordering::Relaxed) {
            return Err(refused(
                verb,
                slot,
                "an earlier request is still waiting for the keyring",
            ));
        }
        let service = self.service.clone();
        let name = slot.api_name();
        let out = bounded(keyring_timeout(), move || {
            keyring::Entry::new(&service, name).and_then(op)
        });
        let r = match out {
            None => {
                KEYRING_STALLED.store(true, Ordering::Relaxed);
                Err(refused(
                    verb,
                    slot,
                    &format!(
                        "no answer after {} s; the keyring may be locked or waiting for you to approve a prompt",
                        keyring_timeout().as_secs()
                    ),
                ))
            }
            Some(Err(e)) => Err(keyring_error(&e, verb, slot)),
            Some(Ok(v)) => Ok(v),
        };
        match &r {
            Ok(_) => crate::log_debug!("keyring", "{verb} {name}: ok"),
            Err(e) => crate::log_warn!("keyring", "{verb} {name}: {e}"),
        }
        r
    }
}

impl Default for KeyringSecrets {
    fn default() -> Self {
        Self::new()
    }
}

fn noun(slot: Slot) -> &'static str {
    match slot {
        Slot::CloudAccess => "the access token",
        Slot::ModelKey => "the model key",
    }
}

fn refused(verb: &str, slot: Slot, reason: &str) -> SecretError {
    let mut words = format!(
        "Your system keyring refused to {verb} {}: {reason}.",
        noun(slot)
    );
    if omaseal_present() {
        words.push_str(" This machine's keyring is omaseal: approve its prompt, run `omaseal agent unlock`, or allow open-computer in ~/.config/omaseal/ai-manifest.txt.");
    }
    SecretError(words)
}

fn omaseal_present() -> bool {
    std::env::var_os("HOME")
        .map(|h| std::path::PathBuf::from(h).join(".config/omaseal"))
        .is_some_and(|p| p.is_dir())
}

fn keyring_error(e: &keyring::Error, verb: &str, slot: Slot) -> SecretError {
    // The keyring's error text never contains the stored value; it names the service or the D-Bus failure.
    let reason = match e {
        keyring::Error::NoStorageAccess(inner) => format!("no access to the keyring ({inner})"),
        keyring::Error::PlatformFailure(inner) => format!("the keyring service failed ({inner})"),
        keyring::Error::NoEntry => "that secret is not stored".to_owned(),
        other => other.to_string(),
    };
    refused(verb, slot, &reason)
}

impl SecretStore for KeyringSecrets {
    fn set(&self, slot: Slot, value: &str) -> Result<(), SecretError> {
        let value = value.to_owned();
        self.run(slot, "save", move |e| e.set_password(&value))
    }

    fn get(&self, slot: Slot) -> Result<Option<String>, SecretError> {
        self.run(slot, "read", |e| match e.get_password() {
            Ok(v) => Ok(Some(v)),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(e) => Err(e),
        })
    }

    fn delete(&self, slot: Slot) -> Result<(), SecretError> {
        self.run(slot, "remove", |e| match e.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(e) => Err(e),
        })
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
        assert_eq!(
            s.last_failure().as_deref(),
            Some("The system keyring is not available. Unlock it or start your keyring service.")
        );
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

    #[test]
    fn a_persistent_save_reports_the_keyring_refusal_instead_of_hiding_it() {
        let s = ResilientSecrets::new(Box::new(Refusing));
        let e = s.set_persistent(Slot::CloudAccess, "v").unwrap_err();
        assert!(e.0.contains("keyring"));
        assert!(
            !s.is_set(Slot::CloudAccess),
            "nothing was kept behind the caller's back"
        );
        assert!(!s.is_volatile());
        assert!(s.last_failure().is_some());
    }

    #[test]
    fn a_working_save_clears_an_earlier_failure() {
        let s = ResilientSecrets::new(Box::new(InMemorySecrets::new()));
        s.note(Some("old".into()));
        s.set(Slot::ModelKey, "abc").unwrap();
        assert_eq!(s.last_failure(), None);
    }

    #[test]
    fn a_stalled_keyring_call_is_abandoned_after_the_timeout() {
        let t = std::time::Instant::now();
        let r = bounded(std::time::Duration::from_millis(60), || {
            std::thread::sleep(std::time::Duration::from_millis(600));
            1
        });
        assert_eq!(r, None);
        assert!(t.elapsed() < std::time::Duration::from_millis(400));
        assert_eq!(bounded(std::time::Duration::from_secs(2), || 7), Some(7));
    }

    #[test]
    fn the_refusal_reads_in_plain_words_with_the_reason() {
        let e = refused("save", Slot::CloudAccess, "no answer after 8 s");
        assert!(e.0.starts_with(
            "Your system keyring refused to save the access token: no answer after 8 s."
        ));
    }
}
