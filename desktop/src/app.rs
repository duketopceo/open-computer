//! Application state and the actions the screens trigger.
//!
//! Secret text lives only in the secure text fields until it is saved to the keyring and
//! cleared; the onboarding machine and the screens see set flags.

use std::sync::Arc;
use std::sync::mpsc::{Receiver, Sender, channel};

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

enum Msg {
    TestDone(Result<TestConnection, String>),
    DiagRows(Vec<Row>),
    DiagDone,
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
    waker: Option<Waker>,
    tx: Sender<Msg>,
    rx: Receiver<Msg>,
}

impl App {
    pub fn new() -> App {
        let store: Arc<dyn SecretStore> = Arc::new(KeyringSecrets::new());
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
            waker: None,
            tx,
            rx,
        }
    }

    pub fn setup(&mut self, waker: Waker) {
        self.waker = Some(waker);
    }

    /// Drain results from the test thread. True when state changed.
    pub fn poll(&mut self, _seconds: f64, _tx: &mut Tx) -> bool {
        let mut changed = false;
        while let Ok(msg) = self.rx.try_recv() {
            changed = true;
            match msg {
                Msg::DiagRows(rows) => self.diag_rows = rows,
                Msg::DiagDone => self.diag_running = false,
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
            run_chain(&transport, env!("CARGO_PKG_VERSION"), 20, |rows| {
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
