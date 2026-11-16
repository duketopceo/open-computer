//! Application state and the actions the screens trigger.
//!
//! Secret text lives only in the secure text fields until it is saved to the keyring and
//! cleared; the onboarding machine and the screens see set flags.

use std::sync::Arc;
use std::sync::mpsc::{Receiver, Sender, channel};

use crate::main_app::Main;
use rune::TextEdit;
use rune::{Tx, Waker};
use shell_core::checks::run_test_connection;
use shell_core::core_client::TestConnection;
use shell_core::core_process::{CoreProcess, LaunchSpec, generate_token};
use shell_core::diagnostics::{Row, UreqTransport, initial_rows, run_chain};
use shell_core::onboarding::{Onboarding, Piece, Step};
use shell_core::secrets::{KeyringSecrets, SecretStore, Slot};

/// Names the local core binary, or `node <script>`. Packaging (a later unit) bundles it.
const CORE_BIN_ENV: &str = "OC_CORE_BIN";

pub(crate) enum Msg {
    TestDone(Result<TestConnection, String>),
    DiagRows(Vec<Row>),
    DiagDone,
    ChatEvent(serde_json::Value),
    ChatEnd,
    ChatError(String),
    Settings(Result<serde_json::Value, String>),
    LocalReady(Result<(CoreProcess, shell_core::chat::Endpoint), String>),
    SignedIn(Result<shell_core::signin::SignIn, String>),
    CfProgress(shell_core::signin::Progress),
    CfDashboard {
        gateway: Option<bool>,
        access: shell_core::signin::AccessState,
    },
    HarnessDetected(Vec<shell_core::harness::Detected>),
    /// Whether the connected core routes model calls through an AI Gateway.
    Gateway(Option<bool>),
    GatewayReason(Option<String>),
    /// The key from "Connect OpenRouter", or why it did not finish.
    OrKey(Result<String, String>),
    OrAccount(shell_core::openrouter::Account),
}

pub struct App {
    pub onboarding: Onboarding,
    pub store: Arc<dyn SecretStore>,
    pub cloud_url: TextEdit,
    pub cloud_secret: TextEdit,
    pub model_key: TextEdit,
    /// A plain-words message for the person, or none.
    pub notice: Option<String>,
    pub testing: bool,
    /// The full test chain's rows, shown with a spinner on the running one.
    pub diag_rows: Vec<Row>,
    pub diag_running: bool,
    /// Set once setup is finished: the chat, settings and diagnostics screens.
    pub main: Option<Main>,
    pub composer: TextEdit,
    /// Who is signed in to Cloudflare, in one plain line, or the reason the sign-in did not finish.
    pub cloudflare_line: Option<String>,
    pub signing_in: bool,
    /// A Cloudflare page to open by hand, when Wrangler said it could not open the browser itself.
    pub cf_open_url: Option<String>,
    pub cf_state: Option<shell_core::signin::SignIn>,
    pub cf_gateway: Option<bool>,
    pub cf_gateway_reason: Option<String>,
    pub cf_access: Option<shell_core::signin::AccessState>,
    pub harness: shell_core::harness::Harness,
    pub harness_info: Vec<shell_core::harness::Detected>,
    /// Set by Stop to end a running harness subprocess.
    pub harness_cancel: Arc<std::sync::atomic::AtomicBool>,
    pub harness_running: bool,
    pub or_busy: bool,
    pub or_line: Option<String>,
    pub or_account: Option<shell_core::openrouter::Account>,
    /// The note typed on an approval card, sent with the decision.
    pub approval_note: TextEdit,
    /// Settings > Models and keys fields. Key fields are secure and are cleared once sent.
    pub set_default_model: TextEdit,
    pub set_worker_model: TextEdit,
    pub set_custom_url: TextEdit,
    pub set_custom_model: TextEdit,
    pub set_custom_key: TextEdit,
    pub set_model_key: TextEdit,
    last_tick: u64,
    waker: Option<Waker>,
    tx: Sender<Msg>,
    rx: Receiver<Msg>,
}

impl App {
    pub fn new() -> App {
        let store: Arc<dyn SecretStore> = Arc::new(shell_core::secrets::ResilientSecrets::new(
            Box::new(KeyringSecrets::new()),
        ));
        let mut onboarding = Onboarding::new();
        onboarding.sync_secrets(&*store);
        let (tx, rx) = channel();
        App {
            onboarding,
            store,
            cloud_url: TextEdit::new(""),
            cloud_secret: TextEdit::new("").secure(),
            model_key: TextEdit::new("").secure(),
            notice: None,
            testing: false,
            diag_rows: initial_rows(),
            diag_running: false,
            main: None,
            composer: TextEdit::new(""),
            cloudflare_line: None,
            signing_in: false,
            cf_open_url: None,
            cf_state: None,
            cf_gateway: None,
            cf_gateway_reason: None,
            cf_access: None,
            harness: shell_core::harness::Harness::BuiltIn,
            harness_info: Vec::new(),
            harness_cancel: Arc::new(std::sync::atomic::AtomicBool::new(false)),
            harness_running: false,
            or_busy: false,
            or_line: None,
            or_account: None,
            approval_note: TextEdit::new(""),
            set_default_model: TextEdit::new(""),
            set_worker_model: TextEdit::new(""),
            set_custom_url: TextEdit::new(""),
            set_custom_model: TextEdit::new(""),
            set_custom_key: TextEdit::new("").secure(),
            set_model_key: TextEdit::new("").secure(),
            last_tick: 0,
            waker: None,
            tx,
            rx,
        }
    }

    pub(crate) fn tx(&self) -> Sender<Msg> {
        self.tx.clone()
    }

    pub(crate) fn waker(&self) -> Option<Waker> {
        self.waker.clone()
    }

    pub fn setup(&mut self, waker: Waker) {
        self.waker = Some(waker);
        self.resume_from_config();
    }

    /// Drain results from the test thread. True when state changed.
    pub fn poll(&mut self, seconds: f64, _tx: &mut Tx) -> bool {
        // The "Working" timer ticks once a second while a task runs.
        let tick = seconds.floor() as u64;
        let mut changed =
            self.main.as_ref().is_some_and(|m| m.chat.is_busy()) && tick != self.last_tick;
        self.last_tick = tick;
        while let Ok(msg) = self.rx.try_recv() {
            changed = true;
            match msg {
                Msg::DiagRows(rows) => self.diag_rows = rows,
                Msg::DiagDone => self.diag_running = false,
                Msg::ChatEvent(e) => {
                    if let Some(m) = self.main.as_mut() {
                        m.chat.apply(&e);
                    }
                }
                Msg::SignedIn(r) => {
                    self.signing_in = false;
                    self.cf_open_url = None;
                    match r {
                        Ok(st) => {
                            self.cloudflare_line = Some(st.summary());
                            self.cf_state = Some(st);
                        }
                        Err(words) => self.cloudflare_line = Some(words),
                    }
                }
                Msg::CfProgress(p) => {
                    self.cloudflare_line = Some(p.text);
                    if p.open_url.is_some() {
                        self.cf_open_url = p.open_url;
                    }
                }
                Msg::CfDashboard { gateway, access } => {
                    self.cf_gateway = gateway;
                    self.cf_access = Some(access);
                }
                Msg::HarnessDetected(list) => self.harness_info = list,
                Msg::GatewayReason(r) => self.cf_gateway_reason = r,
                Msg::Gateway(g) => {
                    if g.is_some() {
                        self.cf_gateway = g;
                    }
                }
                Msg::OrKey(r) => self.on_openrouter_key(r),
                Msg::OrAccount(a) => {
                    self.or_busy = false;
                    self.or_account = Some(a);
                }
                Msg::ChatEnd => self.harness_running = false,
                Msg::ChatError(words) => {
                    self.notice = Some(words);
                    if let Some(m) = self.main.as_mut() {
                        m.chat.status = "failed".to_owned();
                    }
                }
                Msg::Settings(r) => match r {
                    Ok(v) => {
                        if let Some(m) = self.main.as_mut() {
                            m.settings = Some(v);
                        }
                    }
                    Err(words) => self.notice = Some(words),
                },
                Msg::LocalReady(r) => match r {
                    Ok((process, endpoint)) => {
                        if let Some(m) = self.main.as_mut() {
                            m.local = Some(process);
                            m.endpoint = Some(endpoint);
                            m.starting_local = false;
                        }
                        self.load_settings();
                    }
                    Err(words) => {
                        self.notice = Some(format!("The local core could not start: {words}"));
                        if let Some(m) = self.main.as_mut() {
                            m.starting_local = false;
                        }
                    }
                },
                Msg::TestDone(result) => {
                    self.testing = false;
                    match result {
                        Ok(result) => {
                            self.notice = None;
                            if let Err(e) = self.onboarding.record_test(result) {
                                self.notice = Some(e.to_string());
                            }
                        }
                        Err(words) => self.notice = Some(words),
                    }
                }
            }
        }
        changed
    }

    fn save_secret(&mut self, slot: Slot) -> bool {
        let edit = match slot {
            Slot::CloudAccess => &mut self.cloud_secret,
            Slot::ModelKey => &mut self.model_key,
        };
        if edit.text.is_empty() {
            return true;
        }
        match self.store.set(slot, &edit.text) {
            Ok(()) => {
                edit.replace_text("");
                self.onboarding.secret_saved(slot);
                true
            }
            Err(e) => {
                self.notice = Some(e.to_string());
                false
            }
        }
    }

    pub fn next(&mut self) {
        self.notice = None;
        let saved = match self.onboarding.step() {
            Step::Connect if self.onboarding.local_only() => true,
            Step::Connect => {
                self.onboarding.set_cloud_url(self.cloud_url.text.clone());
                self.save_secret(Slot::CloudAccess)
            }
            Step::ModelKey => self.save_secret(Slot::ModelKey),
            _ => true,
        };
        if !saved {
            return;
        }
        if let Err(e) = self.onboarding.advance() {
            self.notice = Some(e.to_string());
        }
    }

    pub fn back(&mut self) {
        self.notice = None;
        self.onboarding.back();
    }

    pub fn fix(&mut self, piece: Piece) {
        self.notice = None;
        self.onboarding.fix(piece);
    }

    pub fn toggle_local_only(&mut self) {
        let on = self.onboarding.local_only();
        self.onboarding.set_local_only(!on);
    }

    pub fn toggle_memory(&mut self) {
        let on = self.onboarding.memory_enabled();
        self.onboarding.set_memory_enabled(!on);
    }

    pub fn run_test(&mut self) {
        if self.testing {
            return;
        }
        self.notice = None;
        let Some(command) = std::env::var(CORE_BIN_ENV)
            .ok()
            .filter(|c| !c.trim().is_empty())
        else {
            self.notice =
                Some("The local core was not found. This build does not bundle it yet.".to_owned());
            return;
        };
        let Ok(token) = generate_token() else {
            self.notice = Some("Could not create a start-up token for the local core.".to_owned());
            return;
        };
        let Some(spec) = LaunchSpec::from_command_text(&command, token) else {
            self.notice = Some("The local core was not found.".to_owned());
            return;
        };
        self.testing = true;
        let store = self.store.clone();
        let settings = self.onboarding.settings_update();
        let tx = self.tx.clone();
        let waker = self.waker.clone();
        std::thread::spawn(move || {
            let outcome = CoreProcess::launch(&spec)
                .map_err(|e| e.to_string())
                .and_then(|process| {
                    run_test_connection(&process.client(), &*store, &settings)
                        .map_err(|e| e.to_string())
                });
            let _ = tx.send(Msg::TestDone(outcome));
            if let Some(wake) = waker {
                wake();
            }
        });
    }
}

impl App {
    /// Runs the full setup and test chain against the saved cloud core, streaming rows to the screen.
    pub fn run_full_tests(&mut self) {
        if self.diag_running {
            return;
        }
        self.notice = None;
        if self.onboarding.local_only() {
            self.run_local_full_tests();
            return;
        }
        let url = self.onboarding.cloud_url().to_owned();
        let token = match self.store.get(Slot::CloudAccess) {
            Ok(Some(t)) if !t.is_empty() => t,
            _ => {
                self.notice = Some("Save the access secret for your cloud core first.".to_owned());
                return;
            }
        };
        if url.is_empty() {
            self.notice = Some("Enter the address of your cloud core first.".to_owned());
            return;
        }
        self.diag_running = true;
        let tx = self.tx.clone();
        let waker = self.waker.clone();
        std::thread::spawn(move || {
            let transport = UreqTransport::new(&url, &token);
            run_chain(&transport, env!("CARGO_PKG_VERSION"), 20, false, |rows| {
                let _ = tx.send(Msg::DiagRows(rows.to_vec()));
                if let Some(wake) = &waker {
                    wake();
                }
            });
            let _ = tx.send(Msg::DiagDone);
            if let Some(wake) = waker {
                wake();
            }
        });
    }
}

impl App {
    /// Local-only mode: start the local core, give it the stored model key, and run the same chain against it.
    fn run_local_full_tests(&mut self) {
        let Some(command) = std::env::var(CORE_BIN_ENV)
            .ok()
            .filter(|c| !c.trim().is_empty())
        else {
            self.notice =
                Some("The local core was not found. This build does not bundle it yet.".to_owned());
            return;
        };
        let Ok(token) = generate_token() else {
            self.notice = Some("Could not create a start-up token for the local core.".to_owned());
            return;
        };
        let Some(spec) = LaunchSpec::from_command_text(&command, token.clone()) else {
            self.notice = Some("The local core was not found.".to_owned());
            return;
        };
        self.diag_running = true;
        let store = self.store.clone();
        let tx = self.tx.clone();
        let waker = self.waker.clone();
        std::thread::spawn(move || {
            let notify = |msg: Msg| {
                let _ = tx.send(msg);
                if let Some(wake) = &waker {
                    wake();
                }
            };
            match CoreProcess::launch(&spec) {
                Ok(process) => {
                    let mut secrets = std::collections::BTreeMap::new();
                    if let Ok(Some(key)) = store.get(Slot::ModelKey) {
                        secrets.insert(Slot::ModelKey.api_name().to_owned(), key);
                    }
                    let _ = process
                        .client()
                        .load_secrets(&shell_core::core_client::SecretsLoadRequest { secrets });
                    let transport = UreqTransport::new(&process.base_url(), &token);
                    run_chain(&transport, env!("CARGO_PKG_VERSION"), 20, true, |rows| {
                        notify(Msg::DiagRows(rows.to_vec()))
                    });
                }
                Err(e) => notify(Msg::DiagRows(vec![failed_launch_row(&e.to_string())])),
            }
            notify(Msg::DiagDone);
        });
    }
}

fn failed_launch_row(why: &str) -> Row {
    let mut rows = initial_rows();
    let mut row = rows.remove(5);
    row.status = shell_core::diagnostics::Status::Fail;
    row.detail = format!("The local core could not start: {why}");
    row.fix =
        Some("Check that the core binary named by OC_CORE_BIN exists and can run.".to_owned());
    row
}
