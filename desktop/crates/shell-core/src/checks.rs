//! The Test connection round trip against a running local core.

use std::collections::BTreeMap;
use std::fmt;

use crate::core_client::{
    ClientError, CoreClient, SecretsLoadRequest, SettingsUpdate, TestConnection,
};
use crate::secrets::{SecretStore, Slot};

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum CheckError {
    /// A stored secret could not be read.
    Secrets(String),
    /// The local core failed one of the calls.
    Core(ClientError),
}

impl fmt::Display for CheckError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            CheckError::Secrets(words) => f.write_str(words),
            CheckError::Core(e) => e.fmt(f),
        }
    }
}

impl std::error::Error for CheckError {}

/// Hand the stored secrets and settings to the core, then run Test connection. Secret
/// values travel only in the load request body, in memory; the result carries flags and
/// messages.
pub fn run_test_connection(
    client: &CoreClient,
    store: &dyn SecretStore,
    settings: &SettingsUpdate,
) -> Result<TestConnection, CheckError> {
    let mut secrets = BTreeMap::new();
    for slot in Slot::ALL {
        let stored = store
            .get(slot)
            .map_err(|e| CheckError::Secrets(e.to_string()))?;
        if let Some(value) = stored
            && !value.is_empty()
        {
            secrets.insert(slot.api_name().to_owned(), value);
        }
    }
    client
        .load_secrets(&SecretsLoadRequest { secrets })
        .map_err(CheckError::Core)?;
    client.put_settings(settings).map_err(CheckError::Core)?;
    client.test_connection().map_err(CheckError::Core)
}
