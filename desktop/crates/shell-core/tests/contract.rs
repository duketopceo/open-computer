//! Parses every fixture in `core/contracts/local-api/` into the client types. The same
//! files are asserted by the TypeScript server tests, so the two languages cannot drift.

#![allow(clippy::unwrap_used, clippy::expect_used)]

mod support;

use serde::Serialize;
use serde::de::DeserializeOwned;
use shell_core::core_client::{
    ErrorBody, Health, ReadyLine, SecretsLoadRequest, SecretsLoadResponse, Settings,
    SettingsUpdate, TestConnection,
};
use shell_core::core_process::parse_ready_line;
use support::{fixture, fixtures_dir};

/// Parse into `T` and require that serializing it back gives the same JSON: nothing in the
/// fixture is dropped or renamed by the client type.
fn lossless<T: DeserializeOwned + Serialize>(name: &str) {
    let text = fixture(name);
    let original: serde_json::Value = serde_json::from_str(&text).unwrap();
    let parsed: T =
        serde_json::from_str(&text).unwrap_or_else(|e| panic!("{name} does not parse: {e}"));
    let back = serde_json::to_value(&parsed).unwrap();
    assert_eq!(back, original, "{name} is not parsed without loss");
}

#[test]
fn every_fixture_parses_into_its_client_type() {
    let mut seen = Vec::new();
    for entry in std::fs::read_dir(fixtures_dir()).unwrap() {
        let path = entry.unwrap().path();
        if path.extension().and_then(|e| e.to_str()) != Some("json") {
            continue;
        }
        let name = path.file_name().unwrap().to_str().unwrap().to_owned();
        match name.as_str() {
            "health.json" => lossless::<Health>(&name),
            "settings.json" | "settings-initial.json" => lossless::<Settings>(&name),
            "settings-put-request.json" => lossless::<SettingsUpdate>(&name),
            "secrets-load-request.json" => lossless::<SecretsLoadRequest>(&name),
            "secrets-load-response.json" => lossless::<SecretsLoadResponse>(&name),
            "test-connection-ok.json" | "test-connection-model-key-rejected.json" => {
                lossless::<TestConnection>(&name)
            }
            "error-unauthorized.json" => lossless::<ErrorBody>(&name),
            "ready-line.json" => {
                lossless::<ReadyLine>(&name);
                // The launcher's validator accepts the fixture line too.
                parse_ready_line(&fixture(&name)).unwrap();
            }
            other => panic!("fixture {other} has no client type: add it here and to shell-core"),
        }
        seen.push(name);
    }
    assert!(
        seen.len() >= 9,
        "expected the full fixture set, found {seen:?}"
    );
}

#[test]
fn fixtures_carry_no_real_looking_secret_in_responses() {
    // Secret values appear only in the load request, and only as placeholders.
    for name in [
        "health.json",
        "settings.json",
        "settings-initial.json",
        "secrets-load-response.json",
        "test-connection-ok.json",
    ] {
        let text = fixture(name);
        assert!(
            !text.contains("placeholder"),
            "{name} echoes a secret placeholder"
        );
    }
    assert!(fixture("secrets-load-request.json").contains("placeholder-not-a-real"));
}

#[test]
fn test_connection_fixtures_have_three_pieces_and_consistent_flags() {
    let ok: TestConnection = serde_json::from_str(&fixture("test-connection-ok.json")).unwrap();
    assert!(ok.all_ok && ok.backend.ok && ok.model.ok && ok.memory.ok);
    let bad: TestConnection =
        serde_json::from_str(&fixture("test-connection-model-key-rejected.json")).unwrap();
    assert!(!bad.all_ok && bad.backend.ok && !bad.model.ok && bad.memory.ok);
    assert_eq!(bad.model.code, "model_key_rejected");
}
