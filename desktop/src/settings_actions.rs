//! Actions behind the Settings page: loading each section's data from the core, sorting and choosing models, opening an
//! earlier task, clearing memory, and opening links. Every fetch runs on a thread and comes back as a message.

use shell_core::chat::Endpoint;
use shell_core::settings_data::{self as data, Load, ModelSort};

use crate::app::{App, DataMsg, Msg};
use crate::integrations::open_url;
use crate::main_app::{Section, notify};

const NOT_CONNECTED: &str =
    "Not connected to a core yet. Connect one from Get started or Cloudflare.";

impl App {
    fn endpoint(&self) -> Option<Endpoint> {
        self.main.as_ref().and_then(|m| m.endpoint.clone())
    }

    pub fn open_section(&mut self, section: Section) {
        if let Some(m) = self.main.as_mut() {
            m.section = section;
        }
        self.load_section();
    }

    /// Fetches whatever the open section needs and does not have yet.
    pub fn load_section(&mut self) {
        let Some(section) = self.main.as_ref().map(|m| m.section) else {
            return;
        };
        match section {
            Section::OpenRouter => {
                if self.or_account.is_none() && !self.or_busy {
                    self.refresh_openrouter();
                }
            }
            Section::Cloudflare => {
                if self.bindings.wants_fetch() {
                    self.reload_bindings();
                }
                if self.core_info.wants_fetch() {
                    self.reload_core_info();
                }
            }
            Section::Models => {
                if self.spend.wants_fetch() {
                    self.reload_spend();
                }
            }
            Section::Memory => {
                if self.memory.wants_fetch() {
                    self.reload_memory();
                }
                if self.spend.wants_fetch() {
                    self.reload_spend();
                }
            }
            Section::About => {
                if self.core_info.wants_fetch() {
                    self.reload_core_info();
                }
            }
            Section::Agent | Section::Diagnostics => {}
        }
    }

    pub fn reload_bindings(&mut self) {
        let Some(ep) = self.endpoint() else {
            self.bindings = Load::Failed(NOT_CONNECTED.to_owned());
            return;
        };
        self.bindings = Load::Loading;
        let (tx, waker) = (self.tx(), self.waker());
        std::thread::spawn(move || {
            notify(&tx, &waker, Msg::Data(DataMsg::Bindings(ep.bindings())))
        });
    }

    pub fn reload_core_info(&mut self) {
        let Some(ep) = self.endpoint() else {
            self.core_info = Load::Failed(NOT_CONNECTED.to_owned());
            return;
        };
        self.core_info = Load::Loading;
        let (tx, waker) = (self.tx(), self.waker());
        std::thread::spawn(move || {
            notify(
                &tx,
                &waker,
                Msg::Data(DataMsg::CoreInfo(data::fetch_core_info(&ep))),
            )
        });
    }

    pub fn reload_spend(&mut self) {
        let Some(ep) = self.endpoint() else {
            self.spend = Load::Failed(NOT_CONNECTED.to_owned());
            return;
        };
        self.spend = Load::Loading;
        let (tx, waker) = (self.tx(), self.waker());
        std::thread::spawn(move || {
            notify(
                &tx,
                &waker,
                Msg::Data(DataMsg::Spend(data::fetch_spend(&ep))),
            )
        });
    }

    pub fn reload_memory(&mut self) {
        let Some(ep) = self.endpoint() else {
            self.memory = Load::Failed(NOT_CONNECTED.to_owned());
            return;
        };
        self.memory = Load::Loading;
        let (tx, waker) = (self.tx(), self.waker());
        std::thread::spawn(move || {
            notify(
                &tx,
                &waker,
                Msg::Data(DataMsg::Memory(data::fetch_memory(&ep))),
            )
        });
    }

    pub fn on_data(&mut self, d: DataMsg) {
        match d {
            DataMsg::Bindings(r) => {
                if let Ok(b) = &r {
                    self.cf_gateway = Some(shell_core::signin::gateway_configured(b));
                    self.cf_gateway_reason = shell_core::signin::gateway_reason(b);
                }
                self.bindings = r.into();
            }
            DataMsg::CoreInfo(r) => self.core_info = r.into(),
            DataMsg::Spend(r) => self.spend = r.into(),
            DataMsg::Memory(r) => self.memory = r.into(),
            DataMsg::RunStatuses(r) => {
                if let Ok(statuses) = r {
                    self.apply_run_statuses(&statuses);
                }
            }
            DataMsg::MemoryCleared(r) => {
                self.confirm_clear = false;
                match r {
                    Ok(n) => {
                        self.notice = Some(format!("Memory cleared ({n} task summaries removed)."));
                        self.spend = Load::Idle;
                        self.reload_memory();
                        self.reload_spend();
                    }
                    Err(e) => self.notice = Some(e),
                }
            }
        }
    }

    /// Clear memory asks first; the second press does it.
    pub fn clear_memory(&mut self) {
        if !self.confirm_clear {
            self.confirm_clear = true;
            return;
        }
        let Some(ep) = self.endpoint() else {
            self.notice = Some(NOT_CONNECTED.to_owned());
            self.confirm_clear = false;
            return;
        };
        let (tx, waker) = (self.tx(), self.waker());
        std::thread::spawn(move || {
            notify(
                &tx,
                &waker,
                Msg::Data(DataMsg::MemoryCleared(data::clear_memory(&ep))),
            )
        });
    }

    pub fn cancel_clear(&mut self) {
        self.confirm_clear = false;
    }

    /// Clicking a column heading sorts by it; clicking again flips the order.
    pub fn sort_models(&mut self, by: ModelSort) {
        let (cur, asc) = self.models_sort;
        self.models_sort = if cur == by { (by, !asc) } else { (by, true) };
    }

    /// Sets the default or worker model on the core to a model from the OpenRouter table.
    pub fn use_model(&mut self, field: &'static str, id: &str) {
        let Some(ep) = self.endpoint() else {
            self.notice = Some(NOT_CONNECTED.to_owned());
            return;
        };
        let body = serde_json::json!({ field: id });
        let (tx, waker) = (self.tx(), self.waker());
        std::thread::spawn(move || notify(&tx, &waker, Msg::Settings(ep.put_settings(&body))));
        self.notice = Some(format!(
            "{} set to {id}.",
            if field == "default_model" {
                "Default model"
            } else {
                "Worker model"
            }
        ));
    }

    /// Opens the task a generation belonged to, if it is one of this session's.
    pub fn open_task(&mut self, task_id: &str) {
        let Some(m) = self.main.as_mut() else { return };
        if m.chat.task_id.as_deref() == Some(task_id) {
            m.tab = crate::main_app::Tab::Chat;
            return;
        }
        if let Some(i) = m
            .threads
            .iter()
            .position(|t| t.chat.task_id.as_deref() == Some(task_id))
        {
            m.open_thread(i);
        } else {
            self.notice = Some(
                "That task is from an earlier session; only this session's tasks can be opened."
                    .to_owned(),
            );
        }
    }

    pub fn open_link(&mut self, url: &'static str) {
        if !open_url(url) {
            self.notice = Some(format!("Open this page yourself: {url}"));
        }
    }
}
