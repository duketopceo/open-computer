//! The app after setup: chat, settings and diagnostics. State and actions live here; the screens only draw them.

use std::sync::mpsc::Sender;

use rune::Waker;
use serde_json::Value;
use shell_core::chat::{Chat, Endpoint};
use shell_core::config::{self, Saved};
use shell_core::core_process::{CoreProcess, LaunchSpec, generate_token};
use shell_core::secrets::Slot;

use crate::app::{App, Msg};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Tab {
    Chat,
    Settings,
    Diagnostics,
}

/// Everything the main screens read.
pub struct Main {
    pub tab: Tab,
    pub chat: Chat,
    pub session_id: String,
    pub endpoint: Option<Endpoint>,
    pub settings: Option<Value>,
    pub local: Option<CoreProcess>,
    pub starting_local: bool,
    /// When the running task started, for the "Working" timer.
    pub started: Option<std::time::Instant>,
}

impl Main {
    pub fn elapsed_secs(&self) -> u64 {
        self.started.map_or(0, |t| t.elapsed().as_secs())
    }

    pub fn new() -> Self {
        Main {
            tab: Tab::Chat,
            chat: Chat::default(),
            session_id: format!("app-{}", std::process::id()),
            endpoint: None,
            settings: None,
            local: None,
            starting_local: false,
            started: None,
        }
    }
}

pub(crate) fn notify(tx: &Sender<Msg>, waker: &Option<Waker>, msg: Msg) {
    let _ = tx.send(msg);
    if let Some(wake) = waker {
        wake();
    }
}

impl App {
    /// Leaves setup for the app, remembering that setup finished.
    pub fn enter_app(&mut self) {
        let saved = Saved {
            cloud_url: self.onboarding.cloud_url().to_owned(),
            local_only: self.onboarding.local_only(),
            setup_done: true,
            harness: config::load(&config::default_path()).harness,
        };
        let _ = config::save(&config::default_path(), &saved);
        self.main = Some(Main::new());
        self.connect_endpoint();
    }

    /// Opens the saved app directly when setup already finished and the keys are still in the keyring.
    pub fn resume_from_config(&mut self) {
        // Automation and headless use: OC_CLOUD_URL and OC_CLOUD_TOKEN open the app straight onto that core, with the
        // token held in memory for this run only (never written anywhere).
        if let (Ok(url), Ok(token)) = (
            std::env::var("OC_CLOUD_URL"),
            std::env::var("OC_CLOUD_TOKEN"),
        ) && !url.is_empty()
            && !token.is_empty()
        {
            let _ = self.store.set(Slot::CloudAccess, &token);
            let _ = self.store.set(Slot::ModelKey, "managed-by-the-cloud-core");
            self.onboarding.sync_secrets(&*self.store);
            self.onboarding.set_cloud_url(url.clone());
            self.cloud_url.replace_text(&url);
            self.main = Some(Main::new());
            if std::env::var_os("OC_SCREEN_FIXTURE").is_some() {
                self.apply_screen_fixture();
            }
            if let (Some(m), Ok(tab)) = (self.main.as_mut(), std::env::var("OC_START_TAB")) {
                m.tab = match tab.as_str() {
                    "settings" => Tab::Settings,
                    "diagnostics" => Tab::Diagnostics,
                    _ => Tab::Chat,
                };
            }
            self.connect_endpoint();
            return;
        }
        let saved = config::load(&config::default_path());
        if !saved.setup_done {
            return;
        }
        self.harness = shell_core::harness::Harness::from_id(&saved.harness);
        self.onboarding.set_local_only(saved.local_only);
        self.onboarding.set_cloud_url(saved.cloud_url.clone());
        self.cloud_url.replace_text(&saved.cloud_url);
        let secrets = self.onboarding.secrets_set();
        let ready = secrets.model_key && (saved.local_only || secrets.cloud_access);
        if ready {
            self.main = Some(Main::new());
            self.connect_endpoint();
        }
    }

    /// Builds the endpoint the chat talks to: the cloud core with its stored secret, or a local core started for this session.
    pub fn connect_endpoint(&mut self) {
        if self.onboarding.local_only() {
            self.start_local_core();
            return;
        }
        self.detect_harnesses();
        let url = self.onboarding.cloud_url().to_owned();
        match self.store.get(Slot::CloudAccess) {
            Ok(Some(t)) if !t.is_empty() && !url.is_empty() => {
                if let Some(m) = self.main.as_mut() {
                    m.endpoint = Some(Endpoint::new(&url, &t));
                }
                self.load_settings();
            }
            _ => self.notice = Some("Save the access secret for your cloud core first.".to_owned()),
        }
    }

    fn start_local_core(&mut self) {
        let Some(command) = std::env::var("OC_CORE_BIN")
            .ok()
            .filter(|c| !c.trim().is_empty())
        else {
            self.notice = Some(
                "The local core was not found. Set OC_CORE_BIN to the core binary.".to_owned(),
            );
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
        if let Some(m) = self.main.as_mut() {
            m.starting_local = true;
        }
        let key = self.store.get(Slot::ModelKey).ok().flatten();
        let (tx, waker) = (self.tx(), self.waker());
        std::thread::spawn(move || {
            let out = CoreProcess::launch(&spec)
                .map_err(|e| e.to_string())
                .map(|p| {
                    let mut secrets = std::collections::BTreeMap::new();
                    if let Some(k) = key {
                        secrets.insert(Slot::ModelKey.api_name().to_owned(), k);
                    }
                    let _ = p
                        .client()
                        .load_secrets(&shell_core::core_client::SecretsLoadRequest { secrets });
                    let ep = Endpoint::new(&p.base_url(), &token);
                    (p, ep)
                });
            notify(&tx, &waker, Msg::LocalReady(out));
        });
    }

    pub fn show_tab(&mut self, tab: Tab) {
        if let Some(m) = self.main.as_mut() {
            m.tab = tab;
        }
        if tab == Tab::Settings {
            self.load_settings();
        }
    }

    pub fn load_settings(&mut self) {
        let Some(ep) = self.main.as_ref().and_then(|m| m.endpoint.clone()) else {
            return;
        };
        let (tx, waker) = (self.tx(), self.waker());
        std::thread::spawn(move || notify(&tx, &waker, Msg::Settings(ep.settings())));
    }

    pub fn set_daily_cap(&mut self, usd: Option<f64>) {
        let Some(ep) = self.main.as_ref().and_then(|m| m.endpoint.clone()) else {
            return;
        };
        let (tx, waker) = (self.tx(), self.waker());
        std::thread::spawn(move || notify(&tx, &waker, Msg::Settings(ep.set_daily_cap(usd))));
    }

    /// Sends the composer text as a task and streams its events into the chat.
    pub fn send(&mut self) {
        let goal = self.composer.text.trim().to_owned();
        let Some(m) = self.main.as_mut() else { return };
        if goal.is_empty() || m.chat.is_busy() {
            return;
        }
        if self.harness != shell_core::harness::Harness::BuiltIn {
            self.send_via_harness(goal);
            return;
        }
        let Some(ep) = m.endpoint.clone() else {
            self.notice = Some(if m.starting_local {
                "The local core is still starting.".to_owned()
            } else {
                "Not connected to a core yet.".to_owned()
            });
            return;
        };
        self.notice = None;
        m.chat.push_user(&goal);
        m.started = Some(std::time::Instant::now());
        let session = m.session_id.clone();
        self.composer.replace_text("");
        let (tx, waker) = (self.tx(), self.waker());
        std::thread::spawn(move || match ep.start_task(&goal, &session) {
            Err(e) => notify(&tx, &waker, Msg::ChatError(e)),
            Ok(started) => {
                let r = ep.stream_events(&started.session_id, |ev| {
                    notify(&tx, &waker, Msg::ChatEvent(ev.clone()))
                });
                match r {
                    Ok(()) => notify(&tx, &waker, Msg::ChatEnd),
                    Err(e) => notify(&tx, &waker, Msg::ChatError(e)),
                }
            }
        });
    }

    /// Opens or closes a thinking block or tool card.
    pub fn toggle_item(&mut self, index: usize) {
        if let Some(m) = self.main.as_mut() {
            m.chat.toggle(index);
        }
    }

    /// Stops the running task.
    pub fn stop_task(&mut self) {
        if self.harness_running {
            self.harness_cancel
                .store(true, std::sync::atomic::Ordering::Relaxed);
            return;
        }
        let Some(m) = self.main.as_ref() else { return };
        let (Some(ep), Some(task)) = (m.endpoint.clone(), m.chat.task_id.clone()) else {
            return;
        };
        let session = m.session_id.clone();
        let (tx, waker) = (self.tx(), self.waker());
        std::thread::spawn(move || {
            if let Err(e) = ep.cancel(&session, &task) {
                notify(&tx, &waker, Msg::ChatError(e));
            }
        });
    }

    /// Answers the approval the chat is waiting on.
    pub fn decide_pending(&mut self, approved: bool) {
        let Some(m) = self.main.as_ref() else { return };
        let (Some(ep), Some(a)) = (m.endpoint.clone(), m.chat.pending_approval().cloned()) else {
            return;
        };
        let session = m.session_id.clone();
        let note = self.approval_note.text.clone();
        self.approval_note.replace_text("");
        let (tx, waker) = (self.tx(), self.waker());
        std::thread::spawn(move || {
            if let Err(e) = ep.decide(&session, &a.id, &a.hash, approved, &note) {
                notify(&tx, &waker, Msg::ChatError(e));
            }
        });
    }
}

impl App {
    /// Fills the composer with an example task.
    pub fn use_example(&mut self, text: &str) {
        self.composer.replace_text(text);
    }

    /// Saves the model names and custom endpoint, and hands any typed key to the core. Typed keys are cleared right away
    /// and are never shown again; the screen only learns "set" or "not set".
    pub fn save_model_settings(&mut self) {
        let Some(ep) = self.main.as_ref().and_then(|m| m.endpoint.clone()) else {
            self.notice = Some("Not connected to a core yet.".to_owned());
            return;
        };
        let mut changes = serde_json::Map::new();
        for (name, edit) in [
            ("default_model", &self.set_default_model),
            ("worker_model", &self.set_worker_model),
            ("custom_endpoint_url", &self.set_custom_url),
            ("custom_endpoint_model", &self.set_custom_model),
        ] {
            let t = edit.text.trim();
            if !t.is_empty() {
                changes.insert(name.to_owned(), serde_json::Value::String(t.to_owned()));
            }
        }
        let keys: Vec<(&'static str, String)> = [
            ("model_key", &self.set_model_key),
            ("custom_key", &self.set_custom_key),
        ]
        .into_iter()
        .filter(|(_, e)| !e.text.is_empty())
        .map(|(slot, e)| (slot, e.text.clone()))
        .collect();
        self.set_model_key.replace_text("");
        self.set_custom_key.replace_text("");
        let (tx, waker) = (self.tx(), self.waker());
        std::thread::spawn(move || {
            let mut last = ep.settings();
            if !changes.is_empty() {
                last = ep.put_settings(&serde_json::Value::Object(changes));
            }
            for (slot, value) in keys {
                if let Err(e) = ep.load_secret(slot, &value) {
                    last = Err(e);
                }
            }
            if last.is_ok() {
                last = ep.settings();
            }
            notify(&tx, &waker, Msg::Settings(last));
        });
    }

    /// Opens the Codex app if it is installed on this computer.
    pub fn launch_codex(&mut self) {
        match std::process::Command::new("codex")
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
        {
            Ok(_) => self.notice = Some("Opened Codex.".to_owned()),
            Err(_) => {
                self.notice = Some(
                    "Codex was not found on this computer. Install it, then try again.".to_owned(),
                )
            }
        }
    }
}
