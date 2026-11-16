//! Get started, with no typing: find the person's core from what `oc setup` saved or from their Cloudflare account, set one
//! up with one click when none exists, and open the app once the core and OpenRouter are both ready. The decisions are in
//! `shell_core::autoconnect` (tested on fakes); this file only starts the threads and moves the results onto the screen.

use std::time::Duration;

use shell_core::autoconnect::{self, CoreStep, Discovery, FoundCore, HttpCfApi};
use shell_core::chat::Endpoint;
use shell_core::onboarding::Step;
use shell_core::secrets::Slot;
use shell_core::signin;

use shell_core::config;

use crate::app::{App, CoreCheck, Msg};
use crate::main_app::notify;

/// The placeholder kept in the keyring when the core holds the OpenRouter key itself.
const MANAGED: &str = "managed-by-the-cloud-core";

impl App {
    /// Whether step 2 is done: a local core, or a cloud core with a valid address and a saved access secret.
    pub fn core_connected(&self) -> bool {
        match self.onboarding.step() {
            Step::Connect => self.onboarding.can_advance(),
            _ => true,
        }
    }

    pub fn cf_signed_in(&self) -> bool {
        self.cf_state.as_ref().is_some_and(|c| c.signed_in) || self.cf_hint_signed_in
    }

    /// What step 2 shows.
    pub fn core_step(&self) -> CoreStep {
        autoconnect::core_step(
            self.core_connected(),
            self.cf_signed_in(),
            self.finding_core,
            self.setup_running,
            self.found_core.as_ref(),
        )
    }

    /// On start: use what `oc setup` saved; failing that, if Wrangler is already installed and signed in, look on the account.
    pub fn auto_connect_on_start(&mut self) {
        // Headless screenshots set this so nothing is looked up on the machine or the account.
        if let Ok(mode) = std::env::var("OC_SCREEN_START") {
            self.apply_start_fixture(&mode);
            return;
        }
        if self.main.is_some() {
            return;
        }
        if !self.auto_connect_saved()
            && !matches!(signin::locate_wrangler_here().how, signin::Found::Npx)
        {
            self.start_discovery();
        }
    }

    /// Right after a Cloudflare sign-in: the saved connection first, then the account.
    pub fn after_sign_in(&mut self) {
        self.cf_hint_signed_in = true;
        if self.main.is_none() && !self.auto_connect_saved() {
            self.start_discovery();
        }
    }

    /// (a) Reads what `oc setup` saved, in this process only; the token goes into the keyring and is never shown. The file stays,
    /// because the `oc` command line reads it too. The address is saved to `desktop.json` whatever the keyring says. When the
    /// keyring refuses the token the reason is shown and the saved token can be used for this session if the person agrees.
    pub fn auto_connect_saved(&mut self) -> bool {
        if self.onboarding.step() != Step::Connect {
            return false;
        }
        if self.core_connected() {
            self.verify_core();
            self.check_core_model_key();
            return true;
        }
        let path = self.saved_core_path.clone();
        let Some(core) = autoconnect::read_saved_core(&path) else {
            return false;
        };
        let url = core.url.clone();
        self.remember_core_address(&url);
        match autoconnect::adopt_saved_core(core, &*self.store) {
            Ok(url) => {
                self.keyring_problem = None;
                self.file_token_offer = false;
                self.onboarding.sync_secrets(&*self.store);
                self.core_line = Some(format!("Found your core at {url}. Checking it..."));
                self.verify_core();
                self.check_core_model_key();
                true
            }
            Err(reason) => {
                self.keyring_problem = Some(reason.clone());
                if config::load(&self.config_path.clone()).file_token_ok {
                    return self.use_saved_token_inner(&reason);
                }
                self.file_token_offer = true;
                self.core_line = Some(format!(
                    "{reason} The token is still in {}. You can use it from there for now.",
                    path.display()
                ));
                false
            }
        }
    }

    /// The address goes into `desktop.json` as soon as it is known, so a keyring that refuses never loses it.
    fn remember_core_address(&mut self, url: &str) {
        self.onboarding.set_cloud_url(url.to_owned());
        self.cloud_url.replace_text(url);
        let path = self.config_path.clone();
        let mut saved = config::load(&path);
        if saved.cloud_url != url {
            saved.cloud_url = url.to_owned();
            match config::save(&path, &saved) {
                Ok(()) => {
                    shell_core::log_info!("connect", "saved the core address {url} to desktop.json")
                }
                Err(e) => shell_core::log_warn!("connect", "could not save desktop.json: {e}"),
            }
        }
    }

    /// "Use the saved token": the person agrees to read the token from `cloud.json` each time instead of the keyring.
    pub fn use_saved_token(&mut self) {
        let reason = self.keyring_problem.clone().unwrap_or_default();
        let path = self.config_path.clone();
        let mut saved = config::load(&path);
        saved.file_token_ok = true;
        if let Err(e) = config::save(&path, &saved) {
            shell_core::log_warn!("connect", "could not save the choice: {e}");
        }
        self.file_token_offer = false;
        self.use_saved_token_inner(&reason);
    }

    fn use_saved_token_inner(&mut self, reason: &str) -> bool {
        let Some(core) = autoconnect::read_saved_core(&self.saved_core_path.clone()) else {
            self.core_line = Some("The saved connection file is no longer usable.".to_owned());
            return false;
        };
        match core.use_for_session(&*self.store) {
            Ok(url) => {
                self.onboarding.sync_secrets(&*self.store);
                self.core_line = Some(format!(
                    "Using the saved token from the connection file because your keyring refused it ({}). Found your core at {url}. Checking it...",
                    reason.trim_end_matches('.')
                ));
                self.verify_core();
                self.check_core_model_key();
                true
            }
            Err(e) => {
                self.core_line = Some(e);
                false
            }
        }
    }

    /// Asks the core a real question (`/v1/diag/bindings`); the sidebar chip follows the answer.
    pub fn verify_core(&mut self) {
        if std::env::var_os("OC_SCREEN_FIXTURE").is_some() {
            self.core_check = CoreCheck::Ok;
            return;
        }
        if self.onboarding.local_only() {
            self.core_check = if self.main.as_ref().is_some_and(|m| m.endpoint.is_some()) {
                CoreCheck::Ok
            } else {
                CoreCheck::Unknown
            };
            return;
        }
        let url = self.onboarding.cloud_url().to_owned();
        let token = self.store.get(Slot::CloudAccess).ok().flatten();
        let Some(token) = token.filter(|t| !t.is_empty() && !url.is_empty()) else {
            self.core_check = CoreCheck::Failed(
                "no access token is available on this computer to check it with".to_owned(),
            );
            return;
        };
        self.core_check = CoreCheck::Checking;
        let (tx, waker) = (self.tx(), self.waker());
        std::thread::spawn(move || {
            let r = Endpoint::new(&url, &token).bindings().map(|_| ());
            notify(&tx, &waker, Msg::CoreChecked(r));
        });
    }

    pub fn on_core_checked(&mut self, r: Result<(), String>) {
        match r {
            Ok(()) => {
                shell_core::log_info!("connect", "the core answered /v1/diag/bindings");
                self.core_check = CoreCheck::Ok;
                if self.main.is_none() {
                    let url = self.onboarding.cloud_url().to_owned();
                    self.core_line = Some(format!("Connected to {url}."));
                }
            }
            Err(e) => {
                shell_core::log_warn!("connect", "the core did not answer: {e}");
                self.core_line = Some(format!("The core did not answer: {e}"));
                if self.main.is_some() {
                    self.notice = Some(format!("The core did not answer: {e}"));
                }
                self.core_check = CoreCheck::Failed(e);
            }
        }
    }

    /// (b) Looks for the `open-computer-core` Worker on the signed-in account.
    pub fn start_discovery(&mut self) {
        if self.finding_core {
            return;
        }
        self.finding_core = true;
        let cmd = signin::wrangler_command();
        let (tx, waker) = (self.tx(), self.waker());
        std::thread::spawn(move || {
            let r = autoconnect::discover_via(&cmd, &|t| Box::new(HttpCfApi::new(t)));
            notify(&tx, &waker, Msg::CoreFound(r));
        });
    }

    pub fn on_core_found(&mut self, r: Result<Discovery, String>) {
        self.finding_core = false;
        match r {
            Err(e) => self.core_line = Some(e),
            Ok(Discovery::NotSignedIn) => {}
            Ok(Discovery::NoCore) => self.cf_hint_signed_in = true,
            Ok(Discovery::Found(f)) => {
                self.cf_hint_signed_in = true;
                self.found_core = Some(f);
                // A token already in the keyring means one click is not even needed.
                if self.store.is_set(Slot::CloudAccess) {
                    self.connect_found_core();
                }
            }
        }
    }

    /// "Connect to your core at <name>": the address is known from the account; the access token has to be on this computer.
    pub fn connect_found_core(&mut self) {
        let Some(f): Option<FoundCore> = self.found_core.clone() else {
            return;
        };
        self.onboarding.set_cloud_url(f.url.clone());
        self.cloud_url.replace_text(&f.url);
        self.onboarding.sync_secrets(&*self.store);
        if self.store.is_set(Slot::CloudAccess) {
            self.core_line = Some(format!("Connected to {}.", f.url));
            self.check_core_model_key();
        } else {
            // Setup saved the token only on the computer that ran it. Nothing is rotated.
            self.core_line = Some("This computer doesn't have the access token for that core. Run setup here, or paste the token under Advanced.".to_owned());
            self.show_advanced = true;
        }
    }

    /// Asks the connected core whether it already holds an OpenRouter key.
    pub fn check_core_model_key(&mut self) {
        if self.onboarding.local_only() {
            return;
        }
        let url = self.onboarding.cloud_url().to_owned();
        let Ok(Some(token)) = self.store.get(Slot::CloudAccess) else {
            return;
        };
        if url.is_empty() || token.is_empty() {
            return;
        }
        let (tx, waker) = (self.tx(), self.waker());
        std::thread::spawn(move || {
            let ep = Endpoint::new(&url, &token);
            let has = autoconnect::core_has_model_key(
                ep.bindings().ok().as_ref(),
                ep.settings().ok().as_ref(),
            );
            notify(&tx, &waker, Msg::CoreHasKey(has));
        });
    }

    pub fn on_core_has_key(&mut self, has: bool) {
        self.core_has_model_key = has;
        if has {
            self.mark_core_key_managed();
        }
        self.finish_onboarding_if_ready();
    }

    /// The core holds the key, so this computer keeps only a placeholder (never a real key).
    pub fn mark_core_key_managed(&mut self) {
        if !self.onboarding.secrets_set().model_key {
            let _ = self.store.set(Slot::ModelKey, MANAGED);
            self.onboarding.sync_secrets(&*self.store);
        }
    }

    /// When the core is reachable and OpenRouter is ready, the app opens on Chat.
    pub fn finish_onboarding_if_ready(&mut self) {
        if self.main.is_some() || self.onboarding.step() != Step::Connect {
            return;
        }
        let key_here = self.onboarding.secrets_set().model_key;
        if autoconnect::onboarding_ready(self.core_connected(), key_here, self.core_has_model_key) {
            self.mark_core_key_managed();
            self.enter_app();
        }
    }

    /// Fake Get started states for headless screenshots (`OC_SCREEN_START=signin|setup|connect|ready`). No lookups are made.
    fn apply_start_fixture(&mut self, mode: &str) {
        use shell_core::autoconnect::{RowStatus, SetupRow};
        match mode {
            "setup" => {
                self.cf_hint_signed_in = true;
                self.setup_running = true;
                self.gateway_link = true;
                self.core_line =
                    Some("Setting up Open Computer in your Cloudflare account…".to_owned());
                let row = |status, title: &str| SetupRow {
                    status,
                    title: title.to_owned(),
                    detail: Vec::new(),
                };
                self.setup_rows = vec![
                    row(RowStatus::Pass, "Cloudflare account and token  (0.4 s)"),
                    row(RowStatus::Pass, "Plan and features  (0.6 s)"),
                    row(
                        RowStatus::Running,
                        "Create and bind D1, R2, Secrets Store, Durable Objects",
                    ),
                ];
            }
            "connect" => {
                self.cf_hint_signed_in = true;
                self.found_core = Some(FoundCore::new(
                    "open-computer-core",
                    "Your account",
                    "https://open-computer-core.you.workers.dev",
                    "0",
                ));
            }
            "keyring" => {
                self.cf_hint_signed_in = true;
                self.keyring_problem = Some("Your system keyring refused to save the access token: no answer after 8 s; the keyring may be locked or waiting for you to approve a prompt.".to_owned());
                self.file_token_offer = true;
                self.core_line = Some("The token is still in ~/.config/open-computer/cloud.json. You can use it from there for now.".to_owned());
            }
            "ready" => {
                self.cf_hint_signed_in = true;
                self.onboarding
                    .set_cloud_url("https://open-computer-core.you.workers.dev");
                self.onboarding.secret_saved(Slot::CloudAccess);
                self.core_has_model_key = true;
                self.core_line =
                    Some("Connected to https://open-computer-core.you.workers.dev.".to_owned());
            }
            _ => {}
        }
    }

    pub fn toggle_advanced(&mut self) {
        self.show_advanced = !self.show_advanced;
    }

    /// One-click setup: the same chain as `oc setup`, run from the app with its rows shown as they arrive.
    pub fn start_setup(&mut self) {
        if self.setup_running {
            return;
        }
        let signed = self.cf_signed_in();
        let wrangler = signin::locate_wrangler_here().cmd;
        let home = signin::home_dir();
        let setup_cmd = autoconnect::setup_command(
            std::env::var("OC_SETUP_CMD").ok().as_deref(),
            std::env::var_os("PATH").as_deref(),
            &signin::extra_dirs(home.as_deref()),
            &signin::is_executable_file,
        );
        let key = match self.store.get(Slot::ModelKey) {
            Ok(Some(k)) if !k.is_empty() => Some(k),
            _ => None,
        };
        self.setup_running = true;
        self.setup_rows.clear();
        self.core_line = Some("Checking that Docker is running…".to_owned());
        let (tx, waker) = (self.tx(), self.waker());
        std::thread::spawn(move || {
            let docker = autoconnect::docker_state("docker");
            if let Err(why) = autoconnect::plan_setup(signed, &setup_cmd, docker) {
                notify(&tx, &waker, Msg::SetupDone(Err(why)));
                return;
            }
            // The gateway name goes in only if that gateway exists on the account; otherwise the dashboard link is offered.
            let gateway = autoconnect::token_from_wrangler(&wrangler)
                .ok()
                .flatten()
                .and_then(|t| {
                    let api = HttpCfApi::new(t);
                    let account = autoconnect::first_account_id(&api)?;
                    autoconnect::gateway_exists(&api, &account, "open-computer")
                });
            let (envs, link) = autoconnect::setup_env(gateway, key.as_deref(), &wrangler);
            notify(
                &tx,
                &waker,
                Msg::SetupNote {
                    line: "Setting up Open Computer in your Cloudflare account…".to_owned(),
                    gateway_link: link,
                },
            );
            let Some(cmd) = setup_cmd else { return };
            let rows_tx = tx.clone();
            let rows_waker = waker.clone();
            let r = autoconnect::run_setup(
                &cmd,
                &envs,
                &mut |rows| notify(&rows_tx, &rows_waker, Msg::SetupRows(rows.to_vec())),
                Duration::from_secs(120),
            );
            notify(&tx, &waker, Msg::SetupDone(r));
        });
    }

    pub fn on_setup_done(&mut self, r: Result<bool, String>) {
        self.setup_running = false;
        match r {
            Ok(true) => {
                self.core_line = Some("Setup finished.".to_owned());
                if !self.auto_connect_saved() {
                    self.core_line = Some("Setup finished, but its connection was not found on this computer. Open Advanced to paste the address and token.".to_owned());
                    self.show_advanced = true;
                }
            }
            Ok(false) => self.core_line = Some("Setup did not finish. The row that failed says why; fix it and click Set up again.".to_owned()),
            Err(why) => self.core_line = Some(why),
        }
    }
}
