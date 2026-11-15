//! The first-run state machine: connect, model key, memory, test, done.
//!
//! The machine holds only flags and the core's plain-words messages. Secret values never
//! enter it: the UI hands them to a [`SecretStore`] and the machine learns a set flag.

use std::fmt;

use serde::Serialize;

use crate::core_client::{PieceResult, SecretsSet, SettingsUpdate, TestConnection};
use crate::secrets::{SecretStore, Slot};

/// The model used until the person picks another one (OpenRouter's router model).
pub const DEFAULT_MODEL: &str = "openrouter/auto";

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Step {
    Connect,
    ModelKey,
    Memory,
    Test,
    Done,
}

impl Step {
    pub fn title(self) -> &'static str {
        match self {
            Step::Connect => "Connect Cloudflare",
            Step::ModelKey => "Add a model key",
            Step::Memory => "Memory",
            Step::Test => "Test connection",
            Step::Done => "All set",
        }
    }
}

/// The three things Test connection checks.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Piece {
    Backend,
    Model,
    Memory,
}

impl Piece {
    pub const ALL: [Piece; 3] = [Piece::Backend, Piece::Model, Piece::Memory];

    pub fn label(self) -> &'static str {
        match self {
            Piece::Backend => "Backend",
            Piece::Model => "Model",
            Piece::Memory => "Memory",
        }
    }

    /// The step where this piece is fixed.
    pub fn fix_step(self) -> Step {
        match self {
            Piece::Backend => Step::Connect,
            Piece::Model => Step::ModelKey,
            Piece::Memory => Step::Memory,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Outcome {
    Unchecked,
    Ok,
    Failed,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct PieceStatus {
    pub piece: Piece,
    pub label: &'static str,
    pub outcome: Outcome,
    /// The core's plain-words message; `None` until a test has run.
    pub message: Option<String>,
}

/// Why a step change was refused, in words safe to show.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StepError {
    CloudUrlInvalid,
    CloudAccessMissing,
    ModelKeyMissing,
    TestNotPassed,
    NotOnTestStep,
    AlreadyDone,
}

impl fmt::Display for StepError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            StepError::CloudUrlInvalid => {
                "Enter the address of your cloud core, starting with https://."
            }
            StepError::CloudAccessMissing => "Save the access secret for your cloud core first.",
            StepError::ModelKeyMissing => "Save a model key first.",
            StepError::TestNotPassed => "Run Test connection and fix any failing piece first.",
            StepError::NotOnTestStep => "Test connection runs on the last step.",
            StepError::AlreadyDone => "Setup is already finished.",
        })
    }
}

impl std::error::Error for StepError {}

#[derive(Clone, Debug)]
pub struct Onboarding {
    step: Step,
    cloud_url: String,
    model: String,
    memory_enabled: bool,
    secrets_set: SecretsSet,
    test: Option<TestConnection>,
}

/// The JSON that `--print-state` prints. Flags and messages only.
#[derive(Clone, Debug, Serialize)]
pub struct OnboardingState {
    pub step: Step,
    pub step_title: &'static str,
    pub cloud_url: String,
    pub model: String,
    pub memory_enabled: bool,
    pub secrets_set: SecretsSet,
    pub pieces: Vec<PieceStatus>,
    pub can_advance: bool,
}

impl Default for Onboarding {
    fn default() -> Self {
        Self::new()
    }
}

impl Onboarding {
    pub fn new() -> Self {
        Self {
            step: Step::Connect,
            cloud_url: String::new(),
            model: DEFAULT_MODEL.to_owned(),
            memory_enabled: true,
            secrets_set: SecretsSet::default(),
            test: None,
        }
    }

    pub fn step(&self) -> Step {
        self.step
    }

    pub fn cloud_url(&self) -> &str {
        &self.cloud_url
    }

    pub fn memory_enabled(&self) -> bool {
        self.memory_enabled
    }

    pub fn secrets_set(&self) -> SecretsSet {
        self.secrets_set
    }

    pub fn test_result(&self) -> Option<&TestConnection> {
        self.test.as_ref()
    }

    pub fn set_cloud_url(&mut self, url: impl Into<String>) {
        let url = url.into().trim().to_owned();
        if url != self.cloud_url {
            self.cloud_url = url;
            self.test = None;
        }
    }

    pub fn set_model(&mut self, model: impl Into<String>) {
        let model = model.into().trim().to_owned();
        if model != self.model && !model.is_empty() {
            self.model = model;
            self.test = None;
        }
    }

    pub fn set_memory_enabled(&mut self, on: bool) {
        if on != self.memory_enabled {
            self.memory_enabled = on;
            self.test = None;
        }
    }

    /// Record whether a secret is stored. A change invalidates an earlier test.
    pub fn set_secret_flag(&mut self, slot: Slot, set: bool) {
        let flag = match slot {
            Slot::CloudAccess => &mut self.secrets_set.cloud_access,
            Slot::ModelKey => &mut self.secrets_set.model_key,
        };
        if *flag != set {
            *flag = set;
            self.test = None;
        }
    }

    /// A secret was just stored (possibly replacing one). Whatever the test proved no longer holds.
    pub fn secret_saved(&mut self, slot: Slot) {
        self.set_secret_flag(slot, true);
        self.test = None;
    }

    /// Read the set flags from a store. Values are not kept.
    pub fn sync_secrets(&mut self, store: &dyn SecretStore) {
        for slot in Slot::ALL {
            self.set_secret_flag(slot, store.is_set(slot));
        }
    }

    /// What the core should be told before a test.
    pub fn settings_update(&self) -> SettingsUpdate {
        SettingsUpdate {
            cloud_url: self.cloud_url.clone(),
            model: self.model.clone(),
            memory_enabled: self.memory_enabled,
        }
    }

    /// True when a test result exists and backend, model and memory are all ok. The
    /// core's own `all_ok` flag is not trusted over the three pieces.
    pub fn test_passed(&self) -> bool {
        self.test
            .as_ref()
            .is_some_and(|t| t.backend.ok && t.model.ok && t.memory.ok)
    }

    pub fn can_advance(&self) -> bool {
        self.check_advance().is_ok()
    }

    fn check_advance(&self) -> Result<Step, StepError> {
        match self.step {
            Step::Connect => {
                if !cloud_url_is_valid(&self.cloud_url) {
                    Err(StepError::CloudUrlInvalid)
                } else if !self.secrets_set.cloud_access {
                    Err(StepError::CloudAccessMissing)
                } else {
                    Ok(Step::ModelKey)
                }
            }
            Step::ModelKey => {
                if self.secrets_set.model_key {
                    Ok(Step::Memory)
                } else {
                    Err(StepError::ModelKeyMissing)
                }
            }
            Step::Memory => Ok(Step::Test),
            Step::Test => {
                if self.test_passed() {
                    Ok(Step::Done)
                } else {
                    Err(StepError::TestNotPassed)
                }
            }
            Step::Done => Err(StepError::AlreadyDone),
        }
    }

    /// Move to the next step. Done is reachable only from a passing test.
    pub fn advance(&mut self) -> Result<Step, StepError> {
        let next = self.check_advance()?;
        self.step = next;
        Ok(next)
    }

    /// Move to the previous step. Earlier answers and the last test result are kept.
    pub fn back(&mut self) -> Step {
        self.step = match self.step {
            Step::Connect | Step::ModelKey => Step::Connect,
            Step::Memory => Step::ModelKey,
            Step::Test => Step::Memory,
            Step::Done => Step::Test,
        };
        self.step
    }

    /// Jump back to the screen that fixes a failed piece.
    pub fn fix(&mut self, piece: Piece) -> Step {
        self.step = piece.fix_step();
        self.step
    }

    /// Store a Test connection result. Only valid on the test step.
    pub fn record_test(&mut self, result: TestConnection) -> Result<(), StepError> {
        if self.step != Step::Test {
            return Err(StepError::NotOnTestStep);
        }
        self.test = Some(result);
        Ok(())
    }

    pub fn pieces(&self) -> Vec<PieceStatus> {
        Piece::ALL
            .into_iter()
            .map(|piece| {
                let result: Option<&PieceResult> = self.test.as_ref().map(|t| match piece {
                    Piece::Backend => &t.backend,
                    Piece::Model => &t.model,
                    Piece::Memory => &t.memory,
                });
                PieceStatus {
                    piece,
                    label: piece.label(),
                    outcome: match result {
                        None => Outcome::Unchecked,
                        Some(r) if r.ok => Outcome::Ok,
                        Some(_) => Outcome::Failed,
                    },
                    message: result.map(|r| r.message.clone()),
                }
            })
            .collect()
    }

    pub fn state(&self) -> OnboardingState {
        OnboardingState {
            step: self.step,
            step_title: self.step.title(),
            cloud_url: self.cloud_url.clone(),
            model: self.model.clone(),
            memory_enabled: self.memory_enabled,
            secrets_set: self.secrets_set,
            pieces: self.pieces(),
            can_advance: self.can_advance(),
        }
    }

    pub fn to_json(&self) -> String {
        serde_json::to_string_pretty(&self.state()).unwrap_or_else(|_| "{}".to_owned())
    }
}

/// The JSON `--print-state` prints: a fresh onboarding state.
pub fn initial_state_json() -> String {
    Onboarding::new().to_json()
}

/// An https address, or http for a loopback host (local development).
pub fn cloud_url_is_valid(url: &str) -> bool {
    let url = url.trim();
    let (secure, rest) = if let Some(rest) = url.strip_prefix("https://") {
        (true, rest)
    } else if let Some(rest) = url.strip_prefix("http://") {
        (false, rest)
    } else {
        return false;
    };
    let host = rest.split(['/', '?', '#']).next().unwrap_or("");
    if host.is_empty() || host.contains(char::is_whitespace) || host.starts_with(':') {
        return false;
    }
    let host_only = host
        .rsplit_once('@')
        .map_or(host, |(_, h)| h)
        .split(':')
        .next()
        .unwrap_or("");
    if host_only.is_empty() {
        return false;
    }
    secure || matches!(host_only, "localhost" | "127.0.0.1")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::secrets::InMemorySecrets;

    fn piece(ok: bool, code: &str, message: &str) -> PieceResult {
        PieceResult {
            ok,
            code: code.to_owned(),
            message: message.to_owned(),
        }
    }

    fn result(backend: bool, model: bool, memory: bool) -> TestConnection {
        TestConnection {
            all_ok: backend && model && memory,
            backend: piece(
                backend,
                if backend { "ok" } else { "backend_down" },
                "backend words",
            ),
            model: piece(
                model,
                if model { "ok" } else { "model_key_rejected" },
                "model words",
            ),
            memory: piece(
                memory,
                if memory { "ok" } else { "memory_failed" },
                "memory words",
            ),
        }
    }

    fn ready_for_test() -> Onboarding {
        let mut o = Onboarding::new();
        o.set_cloud_url("https://core.example.com");
        o.set_secret_flag(Slot::CloudAccess, true);
        o.set_secret_flag(Slot::ModelKey, true);
        assert_eq!(o.advance(), Ok(Step::ModelKey));
        assert_eq!(o.advance(), Ok(Step::Memory));
        assert_eq!(o.advance(), Ok(Step::Test));
        o
    }

    #[test]
    fn replacing_a_secret_after_a_passing_test_requires_testing_again() {
        let mut o = ready_for_test();
        o.record_test(result(true, true, true)).unwrap();
        assert!(o.test_passed());
        o.secret_saved(Slot::CloudAccess);
        assert!(!o.test_passed());
        assert_eq!(o.advance(), Err(StepError::TestNotPassed));
    }

    #[test]
    fn order_is_connect_model_memory_test_done() {
        let mut o = ready_for_test();
        o.record_test(result(true, true, true)).unwrap();
        assert_eq!(o.advance(), Ok(Step::Done));
        assert_eq!(o.advance(), Err(StepError::AlreadyDone));
    }

    #[test]
    fn connect_needs_a_valid_url_and_the_access_secret() {
        let mut o = Onboarding::new();
        assert_eq!(o.advance(), Err(StepError::CloudUrlInvalid));
        o.set_cloud_url("core.example.com");
        assert_eq!(o.advance(), Err(StepError::CloudUrlInvalid));
        o.set_cloud_url("https://core.example.com");
        assert_eq!(o.advance(), Err(StepError::CloudAccessMissing));
        o.set_secret_flag(Slot::CloudAccess, true);
        assert_eq!(o.advance(), Ok(Step::ModelKey));
    }

    #[test]
    fn model_key_step_needs_a_saved_key() {
        let mut o = Onboarding::new();
        o.set_cloud_url("https://core.example.com");
        o.set_secret_flag(Slot::CloudAccess, true);
        o.advance().unwrap();
        assert_eq!(o.advance(), Err(StepError::ModelKeyMissing));
        o.set_secret_flag(Slot::ModelKey, true);
        assert_eq!(o.advance(), Ok(Step::Memory));
    }

    #[test]
    fn done_is_refused_without_a_test_result() {
        let mut o = ready_for_test();
        assert_eq!(o.advance(), Err(StepError::TestNotPassed));
        assert_eq!(o.step(), Step::Test);
    }

    #[test]
    fn done_is_refused_unless_all_three_pieces_are_ok() {
        for (b, m, mem) in [
            (false, true, true),
            (true, false, true),
            (true, true, false),
            (false, false, false),
        ] {
            let mut o = ready_for_test();
            o.record_test(result(b, m, mem)).unwrap();
            assert_eq!(o.advance(), Err(StepError::TestNotPassed), "{b} {m} {mem}");
            assert_eq!(o.step(), Step::Test);
        }
    }

    #[test]
    fn a_lying_all_ok_flag_does_not_open_done() {
        let mut o = ready_for_test();
        let mut r = result(true, false, true);
        r.all_ok = true;
        o.record_test(r).unwrap();
        assert!(!o.test_passed());
        assert_eq!(o.advance(), Err(StepError::TestNotPassed));
    }

    #[test]
    fn record_test_only_on_the_test_step() {
        let mut o = Onboarding::new();
        assert_eq!(
            o.record_test(result(true, true, true)),
            Err(StepError::NotOnTestStep)
        );
    }

    #[test]
    fn can_go_back_to_fix_a_failed_piece_and_retest() {
        let mut o = ready_for_test();
        o.record_test(result(true, false, true)).unwrap();
        assert_eq!(o.fix(Piece::Model), Step::ModelKey);
        // Changing the key invalidates the failed result.
        o.set_secret_flag(Slot::ModelKey, false);
        o.set_secret_flag(Slot::ModelKey, true);
        assert!(o.test_result().is_none());
        assert_eq!(o.advance(), Ok(Step::Memory));
        assert_eq!(o.advance(), Ok(Step::Test));
        o.record_test(result(true, true, true)).unwrap();
        assert_eq!(o.advance(), Ok(Step::Done));
    }

    #[test]
    fn back_walks_one_step_and_stops_at_connect() {
        let mut o = ready_for_test();
        assert_eq!(o.back(), Step::Memory);
        assert_eq!(o.back(), Step::ModelKey);
        assert_eq!(o.back(), Step::Connect);
        assert_eq!(o.back(), Step::Connect);
    }

    #[test]
    fn fix_targets_match_the_pieces() {
        assert_eq!(Piece::Backend.fix_step(), Step::Connect);
        assert_eq!(Piece::Model.fix_step(), Step::ModelKey);
        assert_eq!(Piece::Memory.fix_step(), Step::Memory);
    }

    #[test]
    fn changing_an_answer_clears_a_passing_test() {
        let mut o = ready_for_test();
        o.record_test(result(true, true, true)).unwrap();
        o.back();
        o.set_memory_enabled(false);
        assert!(!o.test_passed());
        o.advance().unwrap();
        assert_eq!(o.advance(), Err(StepError::TestNotPassed));
    }

    /// AE1: a revoked model key fails the model piece; backend and memory still pass.
    #[test]
    fn ae1_model_key_rejected_fixture_names_the_model_piece() {
        let path = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../core/contracts/local-api/test-connection-model-key-rejected.json"
        );
        let text = std::fs::read_to_string(path).unwrap();
        let fixture: TestConnection = crate::core_client::parse(&text).unwrap();
        let mut o = ready_for_test();
        o.record_test(fixture.clone()).unwrap();

        let pieces = o.pieces();
        let by = |p: Piece| pieces.iter().find(|s| s.piece == p).unwrap();
        assert_eq!(by(Piece::Model).outcome, Outcome::Failed);
        assert_eq!(
            by(Piece::Model).message.as_deref(),
            Some(fixture.model.message.as_str())
        );
        assert_eq!(by(Piece::Backend).outcome, Outcome::Ok);
        assert_eq!(by(Piece::Memory).outcome, Outcome::Ok);
        assert!(!o.test_passed());
    }

    #[test]
    fn unchecked_before_any_test() {
        let o = Onboarding::new();
        assert!(
            o.pieces()
                .iter()
                .all(|p| p.outcome == Outcome::Unchecked && p.message.is_none())
        );
    }

    #[test]
    fn state_never_exposes_a_stored_secret() {
        const SENTINEL: &str = "sentinel-secret-value-123";
        let store = InMemorySecrets::new();
        store.set(Slot::CloudAccess, SENTINEL).unwrap();
        store.set(Slot::ModelKey, SENTINEL).unwrap();
        let mut o = Onboarding::new();
        o.sync_secrets(&store);
        assert_eq!(
            o.secrets_set(),
            SecretsSet {
                cloud_access: true,
                model_key: true
            }
        );

        let shown = format!("{:?} {:?} {} {:?}", o, o.state(), o.to_json(), store);
        assert!(!shown.contains(SENTINEL));
        assert!(o.to_json().contains("\"cloud_access\": true"));
    }

    #[test]
    fn initial_state_json_is_valid_and_starts_at_connect() {
        let value: serde_json::Value = serde_json::from_str(&initial_state_json()).unwrap();
        assert_eq!(value["step"], "connect");
        assert_eq!(value["can_advance"], false);
        assert_eq!(value["secrets_set"]["model_key"], false);
        assert_eq!(value["pieces"].as_array().unwrap().len(), 3);
    }

    #[test]
    fn url_validation() {
        assert!(cloud_url_is_valid("https://core.example.com"));
        assert!(cloud_url_is_valid("https://core.example.com/base"));
        assert!(cloud_url_is_valid("http://127.0.0.1:8787"));
        assert!(cloud_url_is_valid("http://localhost:8787"));
        assert!(!cloud_url_is_valid("http://core.example.com"));
        assert!(!cloud_url_is_valid("https://"));
        assert!(!cloud_url_is_valid("https://:80"));
        assert!(!cloud_url_is_valid("https://bad host"));
        assert!(!cloud_url_is_valid("ftp://x.y"));
        assert!(!cloud_url_is_valid(""));
    }
}
