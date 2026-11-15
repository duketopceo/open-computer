//! The client against a loopback fake that serves the shared fixtures.

#![allow(clippy::unwrap_used, clippy::expect_used)]

mod support;

use shell_core::checks::run_test_connection;
use shell_core::core_client::{ClientError, CoreClient, SettingsUpdate};
use shell_core::secrets::{InMemorySecrets, SecretStore, Slot};
use support::FakeCore;

const TOKEN: &str = "test-token-not-secret";

#[test]
fn every_request_carries_the_bearer_token_health_included() {
    let fake = FakeCore::start(TOKEN, "test-connection-ok.json", 5);
    let client = CoreClient::for_port(fake.port, TOKEN);
    assert_eq!(client.health().unwrap().version, "0.1.0");
    client.settings().unwrap();
    client
        .put_settings(&SettingsUpdate {
            cloud_url: "https://core.example.com".into(),
            model: "example/model".into(),
            memory_enabled: true,
        })
        .unwrap();
    client
        .load_secrets(&shell_core::core_client::SecretsLoadRequest {
            secrets: Default::default(),
        })
        .unwrap();
    assert!(client.test_connection().unwrap().all_ok);

    let seen = fake.finish();
    assert_eq!(seen.len(), 5);
    let paths: Vec<_> = seen
        .iter()
        .map(|r| format!("{} {}", r.method, r.path))
        .collect();
    assert_eq!(
        paths,
        [
            "GET /v1/health",
            "GET /v1/settings",
            "PUT /v1/settings",
            "POST /v1/secrets/load",
            "POST /v1/test-connection"
        ]
    );
    for r in &seen {
        assert_eq!(
            r.headers.get("authorization").map(String::as_str),
            Some("Bearer test-token-not-secret"),
            "{} {}",
            r.method,
            r.path
        );
    }
}

#[test]
fn a_wrong_token_is_an_api_error_with_the_core_message() {
    let fake = FakeCore::start(TOKEN, "test-connection-ok.json", 1);
    let client = CoreClient::for_port(fake.port, "wrong-token");
    let err = client.health().unwrap_err();
    assert_eq!(
        err,
        ClientError::Api {
            status: 401,
            code: "unauthorized".into(),
            message: "Missing or wrong token.".into()
        }
    );
    // The message shown to a person is the core's words, and the token is nowhere in it.
    assert!(!format!("{err} {err:?}").contains("wrong-token"));
    fake.finish();
}

#[test]
fn an_unreachable_core_is_a_plain_transport_error() {
    // Port 1 on loopback is closed.
    let client = CoreClient::for_port(1, TOKEN);
    let err = client.health().unwrap_err();
    assert!(matches!(err, ClientError::Transport(_)));
    assert_eq!(err.to_string(), "The local core did not answer.");
}

#[test]
fn client_debug_hides_the_token() {
    let client = CoreClient::for_port(1, TOKEN);
    assert!(!format!("{client:?}").contains(TOKEN));
}

#[test]
fn test_connection_round_trip_hands_over_secrets_in_the_body_only() {
    let fake = FakeCore::start(TOKEN, "test-connection-model-key-rejected.json", 3);
    let store = InMemorySecrets::new();
    store
        .set(Slot::CloudAccess, "access-value-for-test")
        .unwrap();
    store.set(Slot::ModelKey, "model-value-for-test").unwrap();
    let client = CoreClient::for_port(fake.port, TOKEN);
    let settings = SettingsUpdate {
        cloud_url: "https://core.example.com".into(),
        model: "example/model".into(),
        memory_enabled: true,
    };

    let result = run_test_connection(&client, &store, &settings).unwrap();
    assert!(!result.model.ok);
    assert!(result.backend.ok && result.memory.ok);

    let seen = fake.finish();
    assert_eq!(seen[0].path, "/v1/secrets/load");
    assert!(
        seen[0].body.contains("access-value-for-test")
            && seen[0].body.contains("model-value-for-test")
    );
    for r in &seen[1..] {
        assert!(
            !r.body.contains("access-value-for-test") && !r.body.contains("model-value-for-test")
        );
    }
    for r in &seen {
        assert!(!r.path.contains("value-for-test"));
    }
}
