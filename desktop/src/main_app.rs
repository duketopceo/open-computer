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

/// An earlier task: its own conversation and session. The task on screen is `Main::chat`; opening an earlier one swaps them.
pub struct Thread {
    pub session_id: String,
    pub chat: Chat,
    pub began: std::time::SystemTime,
}

/// The sidebar groups threads by when they began.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Group {
    Today,
    Earlier,
}

/// Today means the same UTC day as `now`; anything before is Earlier.
pub fn group_for(began: std::time::SystemTime, now: std::time::SystemTime) -> Group {
    let day = |t: std::time::SystemTime| {
        t.duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |d| d.as_secs() / 86_400)
    };
    if day(began) == day(now) {
        Group::Today
    } else {
        Group::Earlier
    }
}

/// "now", "5 min", "3 h", "2 d".
pub fn relative_time(began: std::time::SystemTime, now: std::time::SystemTime) -> String {
    let s = now.duration_since(began).map_or(0, |d| d.as_secs());
    match s {
        0..=44 => "now".to_owned(),
        45..=3_599 => format!("{} min", (s / 60).max(1)),
        3_600..=86_399 => format!("{} h", s / 3_600),
        _ => format!("{} d", s / 86_400),
    }
}

/// A thread's title: its first message, shortened.
pub fn title_of(chat: &Chat) -> String {
    let first = chat.items.iter().find_map(|i| match i {
        shell_core::chat::Item::User(t) => Some(t.trim().replace('\n', " ")),
        _ => None,
    });
    match first {
        Some(t) if t.chars().count() > 44 => {
            format!("{}...", t.chars().take(44).collect::<String>())
        }
        Some(t) if !t.is_empty() => t,
        _ => "New task".to_owned(),
    }
}

/// The sections of the Settings page.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Section {
    OpenRouter,
    Cloudflare,
    Agent,
    Models,
    Memory,
    Diagnostics,
    About,
}

impl Section {
    pub const ALL: [Section; 7] = [
        Section::OpenRouter,
        Section::Cloudflare,
        Section::Agent,
        Section::Models,
        Section::Memory,
        Section::Diagnostics,
        Section::About,
    ];

    pub fn id(self) -> &'static str {
        match self {
            Section::OpenRouter => "openrouter",
            Section::Cloudflare => "cloudflare",
            Section::Agent => "agent",
            Section::Models => "models",
            Section::Memory => "memory",
            Section::Diagnostics => "diagnostics",
            Section::About => "about",
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            Section::OpenRouter => "OpenRouter",
            Section::Cloudflare => "Cloudflare",
            Section::Agent => "Agent harness",
            Section::Models => "Models and spend",
            Section::Memory => "Memory and privacy",
            Section::Diagnostics => "Diagnostics",
            Section::About => "About",
        }
    }
}

/// Everything the main screens read.
pub struct Main {
    pub section: Section,
    pub tab: Tab,
    pub chat: Chat,
    pub session_id: String,
    /// Unique to this run of the app, so session ids and saved history never collide with an earlier run's.
    pub run_id: String,
    /// Earlier tasks (not the one on screen).
    pub threads: Vec<Thread>,
    /// When the task on screen began.
    pub began: std::time::SystemTime,
    pub next_session: u32,
    /// The sidebar and the Computer panel. The panel follows the task (open while it runs) until the person toggles it.
    pub sidebar_open: bool,
    pub panel_user: Option<bool>,
    /// The step shown in the Computer panel (an index into the chat items); the last step by default.
    pub selected_step: Option<usize>,
    pub harness_menu: bool,
    pub endpoint: Option<Endpoint>,
    pub settings: Option<Value>,
    pub local: Option<CoreProcess>,
    pub starting_local: bool,
    /// When the running task started, for the "Working" timer.
    pub started: Option<std::time::Instant>,
}

impl Main {
    /// Puts the conversations saved by earlier runs into the sidebar (the newest 30). A task that was running when the app
    /// closed is shown as interrupted, not as still working.
    pub fn restore_history(&mut self, dir: &std::path::Path) {
        use shell_core::chat::Item;
        let mut metas = shell_core::history::list(dir);
        metas.truncate(30);
        for meta in metas.into_iter().rev() {
            if meta.session_id == self.session_id {
                continue;
            }
            let mut chat = Chat::default();
            for ev in shell_core::history::load(dir, &meta.session_id) {
                chat.apply(&ev);
            }
            if chat.is_busy() {
                chat.items.push(Item::Note(
                    "This task was interrupted when the app closed.".to_owned(),
                ));
                chat.status = "cancelled".to_owned();
            }
            self.threads.push(Thread {
                session_id: meta.session_id,
                chat,
                began: std::time::UNIX_EPOCH + std::time::Duration::from_secs(meta.began),
            });
        }
    }

    pub fn elapsed_secs(&self) -> u64 {
        self.started.map_or(0, |t| t.elapsed().as_secs())
    }

    /// The conversation of the session that sent an event: the one on screen or an earlier one.
    pub fn chat_for(&mut self, session: &str) -> Option<&mut Chat> {
        if self.session_id == session {
            Some(&mut self.chat)
        } else {
            self.threads
                .iter_mut()
                .find(|t| t.session_id == session)
                .map(|t| &mut t.chat)
        }
    }

    /// Whether the Computer panel is showing: the person's choice, else open while the task runs.
    pub fn panel_open(&self) -> bool {
        self.panel_user.unwrap_or_else(|| self.chat.is_busy())
    }

    /// Starts a fresh task. The one on screen goes to the sidebar list (it keeps running if it is).
    pub fn new_task(&mut self) {
        self.tab = Tab::Chat;
        self.selected_step = None;
        self.panel_user = None;
        if self.chat.items.is_empty() {
            return;
        }
        let old = Thread {
            session_id: std::mem::replace(
                &mut self.session_id,
                format!("app-{}-{}", self.run_id, self.next_session),
            ),
            chat: std::mem::take(&mut self.chat),
            began: std::mem::replace(&mut self.began, std::time::SystemTime::now()),
        };
        self.next_session += 1;
        self.threads.push(old);
        self.started = None;
    }

    /// Opens an earlier task by swapping it with the one on screen.
    pub fn open_thread(&mut self, index: usize) {
        self.tab = Tab::Chat;
        self.selected_step = None;
        self.panel_user = None;
        if index >= self.threads.len() {
            return;
        }
        let t = &mut self.threads[index];
        std::mem::swap(&mut self.session_id, &mut t.session_id);
        std::mem::swap(&mut self.chat, &mut t.chat);
        std::mem::swap(&mut self.began, &mut t.began);
    }

    pub fn new() -> Self {
        let run_id = format!(
            "{}-{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_or(0, |d| d.as_secs()),
            std::process::id()
        );
        Main {
            section: Section::OpenRouter,
            tab: Tab::Chat,
            chat: Chat::default(),
            session_id: format!("app-{run_id}"),
            run_id,
            threads: Vec::new(),
            began: std::time::SystemTime::now(),
            next_session: 1,
            sidebar_open: true,
            panel_user: None,
            selected_step: None,
            harness_menu: false,
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
        let before = config::load(&self.config_path.clone());
        let saved = Saved {
            cloud_url: self.onboarding.cloud_url().to_owned(),
            local_only: self.onboarding.local_only(),
            setup_done: true,
            ..before
        };
        let _ = config::save(&self.config_path.clone(), &saved);
        self.main = Some(self.new_main());
        self.connect_endpoint();
    }

    /// The workspace, with the conversations of earlier runs restored into the sidebar.
    pub fn new_main(&self) -> Main {
        let mut m = Main::new();
        if let Some(dir) = &self.history_dir {
            m.restore_history(dir);
        }
        m
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
            self.main = Some(self.new_main());
            if std::env::var_os("OC_SCREEN_FIXTURE").is_some() {
                self.apply_screen_fixture();
            }
            if let (Some(m), Ok(tab)) = (self.main.as_mut(), std::env::var("OC_START_TAB")) {
                // `settings` or `settings:<section>` (openrouter, cloudflare, agent, models, memory, diagnostics, about).
                let (page, section) = tab.split_once(':').unwrap_or((tab.as_str(), ""));
                m.tab = match page {
                    "settings" | "diagnostics" => Tab::Settings,
                    _ => Tab::Chat,
                };
                m.section = Section::ALL
                    .into_iter()
                    .find(|s| {
                        s.id() == section
                            || (section.is_empty()
                                && page == "diagnostics"
                                && *s == Section::Diagnostics)
                    })
                    .unwrap_or(Section::OpenRouter);
            }
            self.connect_endpoint();
            return;
        }
        let saved = config::load(&self.config_path.clone());
        if !saved.setup_done {
            // An address saved while the keyring or setup was still unfinished is still the person's core.
            if !saved.cloud_url.is_empty() && !saved.local_only {
                self.onboarding.set_cloud_url(saved.cloud_url.clone());
                self.cloud_url.replace_text(&saved.cloud_url);
            }
            return;
        }
        self.harness = shell_core::harness::Harness::from_id(&saved.harness);
        self.harness_key = saved.harness_key;
        self.harness_native = saved.harness_native_model.clone();
        self.onboarding.set_local_only(saved.local_only);
        self.onboarding.set_cloud_url(saved.cloud_url.clone());
        self.cloud_url.replace_text(&saved.cloud_url);
        let mut secrets = self.onboarding.secrets_set();
        if !saved.local_only && !secrets.cloud_access && saved.file_token_ok {
            // The keyring would not hold the token and the person agreed to use the saved connection file instead.
            if let Some(core) =
                shell_core::autoconnect::read_saved_core(&self.saved_core_path.clone())
                && core.use_for_session(&*self.store).is_ok()
            {
                shell_core::log_info!(
                    "connect",
                    "resumed with the token from the saved connection file"
                );
                if !secrets.model_key {
                    let _ = self.store.set(Slot::ModelKey, "managed-by-the-cloud-core");
                }
                self.onboarding.sync_secrets(&*self.store);
                secrets = self.onboarding.secrets_set();
            }
        }
        let ready = secrets.model_key && (saved.local_only || secrets.cloud_access);
        shell_core::log_info!(
            "connect",
            "resume: setup done {}, address saved {}, token present {}, model key present {}, ready {ready}",
            saved.setup_done,
            !saved.cloud_url.is_empty(),
            secrets.cloud_access,
            secrets.model_key
        );
        if ready {
            self.main = Some(self.new_main());
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
                self.verify_core();
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
            // Diagnostics is a section of the Settings page, not a page of its own.
            m.tab = if tab == Tab::Diagnostics {
                Tab::Settings
            } else {
                tab
            };
            if tab == Tab::Diagnostics {
                m.section = Section::Diagnostics;
            }
        }
        if matches!(tab, Tab::Settings | Tab::Diagnostics) {
            self.load_settings();
            self.load_section();
        }
    }

    pub fn load_settings(&mut self) {
        let Some(ep) = self.main.as_ref().and_then(|m| m.endpoint.clone()) else {
            return;
        };
        let (tx, waker) = (self.tx(), self.waker());
        std::thread::spawn(move || {
            notify(&tx, &waker, Msg::Settings(ep.settings()));
            // The core says whether it routes through an AI Gateway; the Routing and Cloudflare sections show it.
            let bindings = ep.bindings().ok();
            let gateway = bindings
                .as_ref()
                .map(shell_core::signin::gateway_configured);
            notify(&tx, &waker, Msg::Gateway(gateway));
            notify(
                &tx,
                &waker,
                Msg::GatewayReason(
                    bindings
                        .as_ref()
                        .and_then(shell_core::signin::gateway_reason),
                ),
            );
        });
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
        m.selected_step = None;
        if let Some(dir) = &self.history_dir {
            shell_core::history::append_user(dir, &session, &goal);
        }
        self.composer.replace_text("");
        let (tx, waker) = (self.tx(), self.waker());
        std::thread::spawn(move || match ep.start_task(&goal, &session) {
            Err(e) => notify(&tx, &waker, Msg::ChatFailed(session.clone(), e)),
            Ok(started) => {
                let sid = started.session_id.clone();
                let r = ep.stream_events(&started.session_id, |ev| {
                    notify(&tx, &waker, Msg::ChatEvent(sid.clone(), ev.clone()))
                });
                match r {
                    Ok(()) => notify(&tx, &waker, Msg::ChatEnd(session.clone())),
                    Err(e) => notify(&tx, &waker, Msg::ChatFailed(session.clone(), e)),
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
    pub fn new_task(&mut self) {
        if let Some(m) = self.main.as_mut() {
            m.new_task();
        }
    }

    pub fn open_thread(&mut self, index: usize) {
        if let Some(m) = self.main.as_mut() {
            m.open_thread(index);
        }
    }

    pub fn toggle_sidebar(&mut self) {
        if let Some(m) = self.main.as_mut() {
            m.sidebar_open = !m.sidebar_open;
        }
    }

    pub fn toggle_panel(&mut self) {
        if let Some(m) = self.main.as_mut() {
            let now = m.panel_open();
            m.panel_user = Some(!now);
        }
    }

    pub fn select_step(&mut self, item: usize) {
        if let Some(m) = self.main.as_mut() {
            m.selected_step = Some(item);
            m.panel_user = Some(true);
        }
    }

    pub fn toggle_harness_menu(&mut self) {
        if let Some(m) = self.main.as_mut() {
            m.harness_menu = !m.harness_menu;
        }
    }

    pub fn pick_harness(&mut self, h: shell_core::harness::Harness) {
        self.set_harness(h);
        if let Some(m) = self.main.as_mut() {
            m.harness_menu = false;
        }
    }

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
