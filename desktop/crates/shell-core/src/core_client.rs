//! Typed client for the local core's loopback API (`core/contracts/local-api/`).
//!
//! Every request, `GET /v1/health` included, carries `Authorization: Bearer <token>`.

use std::collections::BTreeMap;
use std::fmt;
use std::time::Duration;

use serde::{Deserialize, Serialize};

/// `GET /v1/health`.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Health {
    pub ok: bool,
    pub version: String,
}

/// Which secrets the core holds. Flags only, never values.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct SecretsSet {
    pub cloud_access: bool,
    pub model_key: bool,
}

/// `GET /v1/settings` and the `PUT` response.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Settings {
    /// `null` until the user has entered a cloud core address.
    pub cloud_url: Option<String>,
    pub model: String,
    pub memory_enabled: bool,
    pub secrets_set: SecretsSet,
}

/// `PUT /v1/settings` body.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct SettingsUpdate {
    pub cloud_url: String,
    pub model: String,
    pub memory_enabled: bool,
}

/// `POST /v1/secrets/load` body. Holds secret values, so `Debug` lists names only.
#[derive(Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SecretsLoadRequest {
    pub secrets: BTreeMap<String, String>,
}

impl fmt::Debug for SecretsLoadRequest {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("SecretsLoadRequest")
            .field("names", &self.secrets.keys().collect::<Vec<_>>())
            .finish()
    }
}

/// `POST /v1/secrets/load` response: names only.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct SecretsLoadResponse {
    pub loaded: Vec<String>,
}

/// One of the three Test connection pieces.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct PieceResult {
    pub ok: bool,
    pub code: String,
    /// Plain words, safe to show as is.
    pub message: String,
}

/// `POST /v1/test-connection`. The core always returns all three pieces.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct TestConnection {
    pub all_ok: bool,
    pub backend: PieceResult,
    pub model: PieceResult,
    pub memory: PieceResult,
}

/// The body of any failure response.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct ErrorBody {
    pub error: ErrorDetail,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct ErrorDetail {
    pub code: String,
    pub message: String,
}

/// The one line the core prints to stdout when it is listening.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct ReadyLine {
    pub event: String,
    pub port: u16,
    pub version: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ClientError {
    /// The core answered with an error body.
    Api {
        status: u16,
        code: String,
        message: String,
    },
    /// The core did not answer (refused, timed out, reset).
    Transport(String),
    /// The core answered with something that is not the contract.
    Decode(String),
}

impl fmt::Display for ClientError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ClientError::Api { message, .. } => f.write_str(message),
            ClientError::Transport(_) => f.write_str("The local core did not answer."),
            ClientError::Decode(_) => {
                f.write_str("The local core sent a reply this app does not understand.")
            }
        }
    }
}

impl std::error::Error for ClientError {}

pub struct CoreClient {
    base_url: String,
    token: String,
    agent: ureq::Agent,
}

impl fmt::Debug for CoreClient {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("CoreClient")
            .field("base_url", &self.base_url)
            .finish_non_exhaustive()
    }
}

impl CoreClient {
    /// Longest the shell waits for the core, Test connection included (the core's own
    /// per-piece timeouts are shorter than this).
    pub const REQUEST_TIMEOUT: Duration = Duration::from_secs(30);

    pub fn new(base_url: impl Into<String>, token: impl Into<String>) -> Self {
        // No proxy: the core is on loopback and an environment proxy must not see the token.
        let config = ureq::Agent::config_builder()
            .http_status_as_error(false)
            .proxy(None)
            .timeout_global(Some(Self::REQUEST_TIMEOUT))
            .build();
        Self {
            base_url: base_url.into().trim_end_matches('/').to_owned(),
            token: token.into(),
            agent: ureq::Agent::new_with_config(config),
        }
    }

    pub fn for_port(port: u16, token: impl Into<String>) -> Self {
        Self::new(format!("http://127.0.0.1:{port}"), token)
    }

    pub fn health(&self) -> Result<Health, ClientError> {
        self.get("/v1/health")
    }

    pub fn settings(&self) -> Result<Settings, ClientError> {
        self.get("/v1/settings")
    }

    pub fn put_settings(&self, update: &SettingsUpdate) -> Result<Settings, ClientError> {
        let body = to_json(update)?;
        let req = self.agent.put(self.url("/v1/settings"));
        self.finish(
            req.header("Authorization", self.bearer())
                .header("Content-Type", "application/json")
                .send(body),
        )
    }

    pub fn load_secrets(
        &self,
        request: &SecretsLoadRequest,
    ) -> Result<SecretsLoadResponse, ClientError> {
        let body = to_json(request)?;
        let req = self.agent.post(self.url("/v1/secrets/load"));
        self.finish(
            req.header("Authorization", self.bearer())
                .header("Content-Type", "application/json")
                .send(body),
        )
    }

    pub fn test_connection(&self) -> Result<TestConnection, ClientError> {
        let req = self.agent.post(self.url("/v1/test-connection"));
        self.finish(req.header("Authorization", self.bearer()).send_empty())
    }

    fn get<T: for<'de> Deserialize<'de>>(&self, path: &str) -> Result<T, ClientError> {
        let req = self.agent.get(self.url(path));
        self.finish(req.header("Authorization", self.bearer()).call())
    }

    fn url(&self, path: &str) -> String {
        format!("{}{}", self.base_url, path)
    }

    fn bearer(&self) -> String {
        format!("Bearer {}", self.token)
    }

    fn finish<T: for<'de> Deserialize<'de>>(
        &self,
        sent: Result<ureq::http::Response<ureq::Body>, ureq::Error>,
    ) -> Result<T, ClientError> {
        let mut response = sent.map_err(|e| ClientError::Transport(e.to_string()))?;
        let status = response.status().as_u16();
        let text = response
            .body_mut()
            .read_to_string()
            .map_err(|e| ClientError::Transport(e.to_string()))?;
        if (200..300).contains(&status) {
            return parse(&text);
        }
        match parse::<ErrorBody>(&text) {
            Ok(body) => Err(ClientError::Api {
                status,
                code: body.error.code,
                message: body.error.message,
            }),
            Err(_) => Err(ClientError::Decode(format!(
                "status {status} without an error body"
            ))),
        }
    }
}

fn to_json<T: Serialize>(value: &T) -> Result<String, ClientError> {
    serde_json::to_string(value).map_err(|e| ClientError::Decode(e.to_string()))
}

/// Parse a JSON body into a contract type.
pub fn parse<T: for<'de> Deserialize<'de>>(text: &str) -> Result<T, ClientError> {
    serde_json::from_str(text).map_err(|e| ClientError::Decode(e.to_string()))
}
