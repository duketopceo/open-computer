//! End to end against a built sidecar. Ignored unless run explicitly with
//! `OC_CORE_BIN` set to the compiled core binary, or to `node <script>`:
//!
//!   OC_CORE_BIN="/path/to/core" cargo test -p shell-core --test e2e -- --ignored

#![allow(clippy::unwrap_used, clippy::expect_used)]

use shell_core::core_process::{CoreProcess, LaunchSpec, generate_token};
use shell_core::secrets::{InMemorySecrets, SecretStore, Slot};

#[test]
#[ignore = "needs OC_CORE_BIN pointing at a built local core"]
fn launch_load_a_fake_secret_and_call_health() {
    // Run explicitly with --ignored, so a missing binary is a failure, never a silent pass.
    let command =
        std::env::var("OC_CORE_BIN").expect("OC_CORE_BIN must point at the built sidecar");
    let token = generate_token().unwrap();
    let spec = LaunchSpec::from_command_text(&command, token).expect("OC_CORE_BIN is empty");
    let process = CoreProcess::launch(&spec).expect("sidecar did not become ready");
    let client = process.client();

    let health = client.health().expect("health");
    assert!(health.ok);
    assert_eq!(health.version, process.version());

    let store = InMemorySecrets::new();
    store
        .set(Slot::ModelKey, "placeholder-not-a-real-key")
        .unwrap();
    let request = shell_core::core_client::SecretsLoadRequest {
        secrets: [(
            Slot::ModelKey.api_name().to_owned(),
            "placeholder-not-a-real-key".to_owned(),
        )]
        .into(),
    };
    let loaded = client.load_secrets(&request).expect("secrets load");
    assert_eq!(loaded.loaded, vec!["model_key".to_owned()]);

    // The core reports the flag, never the value.
    let settings = client.settings().expect("settings");
    assert!(settings.secrets_set.model_key);
    assert!(
        !serde_json::to_string(&settings)
            .unwrap()
            .contains("placeholder-not-a-real-key")
    );
}
