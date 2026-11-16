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

/// Whether the core answered a real request just now. The sidebar chip says "core connected" only for `Ok`.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub enum CoreCheck {
    #[default]
    Unknown,
    Checking,
    Ok,
    Failed(String),
}

pub(crate) enum DataMsg {
    Bindings(Result<serde_json::Value, String>),
    CoreInfo(Result<shell_core::settings_data::CoreInfo, String>),
    Spend(Result<shell_core::settings_data::SpendReport, String>),
    Memory(Result<shell_core::settings_data::MemoryStats, String>),
    MemoryCleared(Result<u64, String>),
}

pub(crate) enum Msg {
    /// The answer of a real request to the core (`/v1/diag/bindings`).
    CoreChecked(Result<(), String>),
    TestDone(Result<TestConnection, String>),
    DiagRows(Vec<Row>),
    DiagDone,
    /// An event from the task of the session named first.
    ChatEvent(String, serde_json::Value),
    ChatEnd(String),
    /// The task of that session could not start or its stream broke.
    ChatFailed(String, String),
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
    /// What looking for the core on the Cloudflare account found.
    CoreFound(Result<shell_core::autoconnect::Discovery, String>),
    SetupRows(Vec<shell_core::autoconnect::SetupRow>),
    /// A line about setup for step 2, and whether to offer the AI Gateway dashboard link.
    SetupNote {
        line: String,
        gateway_link: bool,
    },
    SetupDone(Result<bool, String>),
    /// Whether the connected core already holds an OpenRouter key.
    CoreHasKey(bool),
    /// Settings page data arriving from the core.
    Data(DataMsg),
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
    /// Rows of the test chain the person opened (failures open themselves).
    pub expanded_rows: std::collections::BTreeSet<String>,
    /// Keep the running row in view while the chain runs; opening a row stops it.
    pub follow_running: bool,
    /// When each running row started, and when the chain did, for the live timers.
    pub row_started: std::collections::BTreeMap<String, std::time::Instant>,
    pub chain_started: Option<std::time::Instant>,
    // Get started: finding or setting up the core with no typing.
    pub found_core: Option<shell_core::autoconnect::FoundCore>,
    pub finding_core: bool,
    /// Wrangler's own sign-in answered a token request, so someone is signed in even before a sign-in click.
    pub cf_hint_signed_in: bool,
    pub core_line: Option<String>,
    pub setup_rows: Vec<shell_core::autoconnect::SetupRow>,
    pub setup_running: bool,
    pub gateway_link: bool,
    pub show_advanced: bool,
    pub core_has_model_key: bool,
    // Settings page data, each loaded from the core on its own.
    pub bindings: shell_core::settings_data::Load<serde_json::Value>,
    pub core_info: shell_core::settings_data::Load<shell_core::settings_data::CoreInfo>,
    pub spend: shell_core::settings_data::Load<shell_core::settings_data::SpendReport>,
    pub memory: shell_core::settings_data::Load<shell_core::settings_data::MemoryStats>,
    pub confirm_clear: bool,
    pub models_query: TextEdit,
    pub models_sort: (shell_core::settings_data::ModelSort, bool),
    /// Whether the core answered a real request, for the sidebar chip.
    pub core_check: CoreCheck,
    /// Why the keyring could not hold a secret, in words (from the keyring's own error), shown until a save works.
    pub keyring_problem: Option<String>,
    /// The keyring refused the saved token: offer to use `cloud.json` for this session instead (needs the person's yes).
    pub file_token_offer: bool,
    /// Where finished conversations are kept (none in tests and screenshots).
    pub history_dir: Option<std::path::PathBuf>,
    /// The person agreed to hand their OpenRouter key to Hermes and OpenCode.
    pub harness_key: bool,
    /// Harness ids that use their own default model instead of Open Computer's.
    pub harness_native: Vec<String>,
    /// Text for the clipboard, sent on the next poll (the clipboard belongs to the window).
    pub copy_request: Option<String>,
    pub diag_copied: bool,
    /// `desktop.json` and the saved connection `cloud.json`; tests point these at a temporary folder.
    pub config_path: std::path::PathBuf,
    pub saved_core_path: std::path::PathBuf,
    last_tick: u64,
    waker: Option<Waker>,
    tx: Sender<Msg>,
    rx: Receiver<Msg>,
}

impl App {
    pub fn new() -> App {
        // Screenshot runs never touch the real keyring.
        let store: Arc<dyn SecretStore> = if std::env::var_os("OC_SCREEN_START").is_some() {
            Arc::new(shell_core::secrets::InMemorySecrets::new())
        } else {
            Arc::new(shell_core::secrets::ResilientSecrets::new(Box::new(
                KeyringSecrets::new(),
            )))
        };
        let mut app = Self::with_store(store);
        // Screenshot runs keep no history on the real disk either.
        if std::env::var_os("OC_SCREEN_START").is_none()
            && std::env::var_os("OC_SCREEN_FIXTURE").is_none()
        {
            app.history_dir = Some(shell_core::history::default_dir());
        }
        app
    }

    /// The app over a given secret store (the keyring in the app, memory in tests).
    pub fn with_store(store: Arc<dyn SecretStore>) -> App {
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
            expanded_rows: Default::default(),
            follow_running: true,
            row_started: Default::default(),
            chain_started: None,
            found_core: None,
            finding_core: false,
            cf_hint_signed_in: false,
            core_line: None,
            setup_rows: Vec::new(),
            setup_running: false,
            gateway_link: false,
            show_advanced: false,
            core_has_model_key: false,
            bindings: Default::default(),
            core_info: Default::default(),
            spend: Default::default(),
            memory: Default::default(),
            confirm_clear: false,
            models_query: TextEdit::new(""),
            models_sort: (shell_core::settings_data::ModelSort::Prompt, true),
            core_check: CoreCheck::Unknown,
            keyring_problem: None,
            file_token_offer: false,
            history_dir: None,
            harness_key: false,
            harness_native: Vec::new(),
            copy_request: None,
            diag_copied: false,
            // Tests never touch the person's real configuration.
            config_path: if cfg!(test) {
                std::env::temp_dir()
                    .join(format!("oc-test-{}", std::process::id()))
                    .join("desktop.json")
            } else {
                shell_core::config::default_path()
            },
            saved_core_path: if cfg!(test) {
                std::env::temp_dir()
                    .join(format!("oc-test-{}", std::process::id()))
                    .join("cloud.json")
            } else {
                shell_core::autoconnect::default_state_path()
            },
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
        shell_core::log_info!("app", "started, version {}", env!("CARGO_PKG_VERSION"));
        self.waker = Some(waker);
        self.resume_from_config();
        self.auto_connect_on_start();
    }

    /// Drain results from the test thread. True when state changed.
    pub fn poll(&mut self, seconds: f64, tx: &mut Tx) -> bool {
        // The "Working" timer ticks once a second while a task runs.
        let tick = (seconds * 4.0).floor() as u64;
        let mut changed = (self.main.as_ref().is_some_and(|m| m.chat.is_busy())
            || self.diag_running)
            && tick != self.last_tick;
        self.last_tick = tick;
        if let Some(text) = self.copy_request.take() {
            tx.copy(text);
            changed = true;
        }
        while let Ok(msg) = self.rx.try_recv() {
            changed = true;
            match msg {
                Msg::DiagRows(rows) => {
                    // Keep the row that is running in view; the wheel, or opening a row, ends the following.
                    if self.follow_running
                        && let Some(r) = rows
                            .iter()
                            .find(|r| r.status == shell_core::diagnostics::Status::Running)
                    {
                        tx.scroll_into_view(format!("row-{}", r.id));
                    }
                    for r in &rows {
                        if r.status == shell_core::diagnostics::Status::Running {
                            self.row_started
                                .entry(r.id.to_owned())
                                .or_insert_with(std::time::Instant::now);
                        } else {
                            self.row_started.remove(r.id);
                        }
                    }
                    self.diag_rows = rows;
                }
                Msg::DiagDone => self.diag_running = false,
                Msg::CoreChecked(r) => self.on_core_checked(r),
                Msg::ChatEvent(sid, e) => {
                    if let Some(dir) = &self.history_dir {
                        shell_core::history::append(dir, &sid, &e);
                    }
                    if let Some(chat) = self.main.as_mut().and_then(|m| m.chat_for(&sid)) {
                        chat.apply(&e);
                    }
                }
                Msg::SignedIn(r) => {
                    self.signing_in = false;
                    self.cf_open_url = None;
                    match r {
                        Ok(st) => {
                            self.cloudflare_line = Some(st.summary());
                            let signed = st.signed_in;
                            self.cf_state = Some(st);
                            if signed {
                                self.after_sign_in();
                            }
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
                Msg::CoreFound(r) => self.on_core_found(r),
                Msg::SetupRows(rows) => self.setup_rows = rows,
                Msg::SetupNote { line, gateway_link } => {
                    self.core_line = Some(line);
                    self.gateway_link = gateway_link;
                }
                Msg::SetupDone(r) => self.on_setup_done(r),
                Msg::CoreHasKey(has) => self.on_core_has_key(has),
                Msg::Data(d) => self.on_data(d),
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
                Msg::ChatEnd(sid) => {
                    self.harness_running = false;
                    // A stream that ends without ever saying how the run went counts as done.
                    if let Some(chat) = self.main.as_mut().and_then(|m| m.chat_for(&sid))
                        && chat.status.is_empty()
                    {
                        chat.status = "done".to_owned();
                    }
                }
                Msg::ChatFailed(sid, words) => {
                    self.harness_running = false;
                    self.notice = Some(words);
                    if let Some(chat) = self.main.as_mut().and_then(|m| m.chat_for(&sid)) {
                        chat.status = "failed".to_owned();
                    }
                }
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
            Step::ModelKey if self.core_has_model_key => {
                self.mark_core_key_managed();
                true
            }
            Step::ModelKey => self.save_secret(Slot::ModelKey),
            _ => true,
        };
        if !saved {
            return;
        }
        if let Err(e) = self.onboarding.advance() {
            self.notice = Some(e.to_string());
            return;
        }
        // The OpenRouter step is skipped when the core already holds a key.
        if self.onboarding.step() == Step::ModelKey && self.core_has_model_key {
            self.mark_core_key_managed();
            let _ = self.onboarding.advance();
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

    /// Opens or closes one row of the test chain.
    pub fn toggle_row(&mut self, id: &str) {
        self.follow_running = false;
        if !self.expanded_rows.remove(id) {
            self.expanded_rows.insert(id.to_owned());
        }
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
        self.follow_running = true;
        self.chain_started = Some(std::time::Instant::now());
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
