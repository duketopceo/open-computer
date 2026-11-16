//! Actions for Settings > Agent harness, OpenRouter and Cloudflare. Every blocking call runs on a thread and reports back
//! through a message; no secret value is ever put in a message the screens read except the one-time key from the
//! OpenRouter callback, which goes straight to the keyring.

use std::sync::atomic::Ordering;
use std::time::Duration;

use shell_core::config;
use shell_core::harness::{self, Harness};
use shell_core::openrouter;
use shell_core::secrets::Slot;
use shell_core::signin::{self, AccessState};

use crate::app::{App, Msg};
use crate::main_app::notify;

/// Opens an https page in the default browser. Anything else is refused.
pub fn open_url(url: &str) -> bool {
    if !url.starts_with("https://") {
        return false;
    }
    let (program, lead): (&str, &[&str]) = if cfg!(target_os = "macos") {
        ("open", &[])
    } else if cfg!(target_os = "windows") {
        ("cmd", &["/c", "start", ""])
    } else {
        ("xdg-open", &[])
    };
    std::process::Command::new(program)
        .args(lead)
        .arg(url)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
        .is_ok()
}

impl App {
    // ---------- agent harness ----------

    pub fn detect_harnesses(&mut self) {
        if std::env::var_os("OC_SCREEN_FIXTURE").is_some() {
            return;
        }
        let (tx, waker) = (self.tx(), self.waker());
        std::thread::spawn(move || {
            notify(
                &tx,
                &waker,
                Msg::HarnessDetected(harness::detect_all(&harness::SystemProbe)),
            )
        });
    }

    pub fn set_harness(&mut self, h: Harness) {
        self.harness = h;
        let path = config::default_path();
        let mut saved = config::load(&path);
        saved.harness = h.id().to_owned();
        let _ = config::save(&path, &saved);
        self.notice = None;
    }

    /// Runs the goal through the chosen harness as a subprocess and streams its output into the chat.
    pub fn send_via_harness(&mut self, goal: String) {
        let h = self.harness;
        let installed = self
            .harness_info
            .iter()
            .find(|d| d.harness == h)
            .is_some_and(|d| d.installed);
        let Some(mut adapter) = harness::adapter_for(h) else {
            return;
        };
        if !installed {
            self.notice = Some(format!(
                "{} is not installed. {}",
                h.label(),
                h.install_hint()
            ));
            return;
        }
        let Some(m) = self.main.as_mut() else { return };
        self.notice = None;
        m.chat.push_user(&goal);
        m.started = Some(std::time::Instant::now());
        m.selected_step = None;
        let session = m.session_id.clone();
        self.composer.replace_text("");
        self.harness_running = true;
        self.harness_cancel.store(false, Ordering::Relaxed);
        let cancel = self.harness_cancel.clone();
        let (tx, waker) = (self.tx(), self.waker());
        std::thread::spawn(move || {
            let sid = session.clone();
            let r = harness::run_task(&mut *adapter, &goal, None, None, None, &cancel, &mut |ev| {
                notify(&tx, &waker, Msg::ChatEvent(sid.clone(), ev))
            });
            match r {
                Ok(()) => notify(&tx, &waker, Msg::ChatEnd(session.clone())),
                Err(e) => notify(&tx, &waker, Msg::ChatFailed(session.clone(), e)),
            }
        });
    }

    // ---------- OpenRouter ----------

    /// "Connect OpenRouter": open the browser, wait on a localhost callback, exchange the code for a key.
    pub fn connect_openrouter(&mut self) {
        if self.or_busy {
            return;
        }
        let cb = match openrouter::Callback::bind() {
            Ok(c) => c,
            Err(e) => {
                self.or_line = Some(e);
                return;
            }
        };
        let Ok(pkce) = openrouter::new_pkce() else {
            self.or_line = Some("No secure random source on this computer.".to_owned());
            return;
        };
        let url = openrouter::auth_url(openrouter::BASE, &cb.url(), &pkce.challenge);
        if !open_url(&url) {
            self.or_line =
                Some("The browser could not be opened. Paste a key below instead.".to_owned());
            return;
        }
        self.or_busy = true;
        self.or_line = Some("Your browser opened OpenRouter. Click Authorize there.".to_owned());
        let (tx, waker) = (self.tx(), self.waker());
        std::thread::spawn(move || {
            let r =
                openrouter::finish_connect(openrouter::BASE, &cb, &pkce, Duration::from_secs(300));
            notify(&tx, &waker, Msg::OrKey(r));
        });
    }

    /// Saves the key from the callback (keyring first), hands it to the connected core, then loads the account view.
    pub fn on_openrouter_key(&mut self, r: Result<String, String>) {
        let key = match r {
            Ok(k) => k,
            Err(e) => {
                self.or_busy = false;
                self.or_line = Some(e);
                return;
            }
        };
        if let Err(e) = self.store.set(Slot::ModelKey, &key) {
            self.or_busy = false;
            self.or_line = Some(format!("The key could not be saved: {e}"));
            return;
        }
        self.onboarding.sync_secrets(&*self.store);
        self.or_line = Some("Connected. The key is saved in your system keyring.".to_owned());
        if let Some(ep) = self.main.as_ref().and_then(|m| m.endpoint.clone()) {
            let (tx, waker) = (self.tx(), self.waker());
            let key = key.clone();
            std::thread::spawn(move || {
                if let Err(e) = ep.load_secret("model_key", &key) {
                    notify(&tx, &waker, Msg::ChatError(e));
                }
            });
        }
        self.refresh_openrouter();
        self.finish_onboarding_if_ready();
    }

    /// Loads key usage and limit, credits, models with prices and recent generation stats. A connected core answers through
    /// the official OpenRouter SDK; with no core (or one without a key) this computer asks OpenRouter directly.
    pub fn refresh_openrouter(&mut self) {
        let ep = self.main.as_ref().and_then(|m| m.endpoint.clone());
        let key = match self.store.get(Slot::ModelKey) {
            Ok(Some(k)) if !k.is_empty() && k != "managed-by-the-cloud-core" => Some(k),
            _ => None,
        };
        if ep.is_none() && key.is_none() {
            self.or_busy = false;
            self.or_line = Some(
                "No OpenRouter key is saved on this computer. Click Connect OpenRouter.".to_owned(),
            );
            return;
        }
        self.or_busy = true;
        let (tx, waker) = (self.tx(), self.waker());
        std::thread::spawn(move || {
            let via_core = ep.as_ref().and_then(openrouter::load_account_via_core);
            let account = via_core.or_else(|| {
                key.map(|k| {
                    openrouter::load_account(&openrouter::Client::new(openrouter::BASE, &k), &[])
                })
            });
            let account = account.unwrap_or_else(|| openrouter::Account {
                problems: vec![
                    "The core has no OpenRouter key yet. Click Connect OpenRouter.".to_owned(),
                ],
                ..openrouter::Account::default()
            });
            notify(&tx, &waker, Msg::OrAccount(account));
        });
    }

    pub fn forget_openrouter_key(&mut self) {
        let _ = self.store.delete(Slot::ModelKey);
        self.onboarding.sync_secrets(&*self.store);
        self.or_account = None;
        self.or_line = Some("The OpenRouter key was removed from your keyring.".to_owned());
    }

    // ---------- Cloudflare ----------

    fn cf_start(&mut self, first: &str) -> bool {
        if self.signing_in {
            return false;
        }
        self.signing_in = true;
        self.cloudflare_line = Some(first.to_owned());
        true
    }

    /// "Sign in with Cloudflare": opens the browser to Cloudflare's own consent page. Click Allow, done.
    pub fn sign_in_cloudflare(&mut self) {
        // Shown the instant the button is pressed, before anything is spawned.
        if !self.cf_start("Starting Wrangler…") {
            return;
        }
        let (tx, waker) = (self.tx(), self.waker());
        std::thread::spawn(move || {
            let r = signin::sign_in(&mut |p| notify(&tx, &waker, Msg::CfProgress(p)));
            notify(&tx, &waker, Msg::SignedIn(r));
        });
    }

    pub fn switch_cloudflare(&mut self) {
        if !self.cf_start("Starting Wrangler…") {
            return;
        }
        let (tx, waker) = (self.tx(), self.waker());
        std::thread::spawn(move || {
            let r = signin::switch_account(&mut |p| notify(&tx, &waker, Msg::CfProgress(p)));
            notify(&tx, &waker, Msg::SignedIn(r));
        });
    }

    pub fn sign_out_cloudflare(&mut self) {
        if !self.cf_start("Starting Wrangler…") {
            return;
        }
        let (tx, waker) = (self.tx(), self.waker());
        std::thread::spawn(move || notify(&tx, &waker, Msg::SignedIn(signin::sign_out())));
    }

    /// Re-reads who is signed in (step 1 again) and checks the two dashboard steps.
    pub fn check_cloudflare(&mut self) {
        if !self.cf_start("Starting Wrangler…") {
            return;
        }
        let ep = self.main.as_ref().and_then(|m| m.endpoint.clone());
        let url = self.onboarding.cloud_url().to_owned();
        let local = self.onboarding.local_only();
        let (tx, waker) = (self.tx(), self.waker());
        std::thread::spawn(move || {
            notify(&tx, &waker, Msg::SignedIn(signin::who_am_i()));
            let gateway = ep
                .as_ref()
                .and_then(|e| e.bindings().ok())
                .map(|b| signin::gateway_configured(&b));
            let access = if local || url.is_empty() {
                AccessState::Unknown
            } else {
                signin::detect_access(&url)
            };
            notify(&tx, &waker, Msg::CfDashboard { gateway, access });
        });
    }

    /// Opens the sign-in page Wrangler printed, when it could not open the browser itself.
    pub fn open_sign_in_page(&mut self) {
        let Some(url) = self.cf_open_url.clone() else {
            return;
        };
        if !open_url(&url) {
            self.cloudflare_line = Some(format!("Open this page yourself: {url}"));
        }
    }

    pub fn open_dashboard(&mut self, step: signin::DashboardStep) {
        self.cloudflare_line = Some(if open_url(step.url) {
            format!("Opened {} in your browser.", step.title)
        } else {
            format!("Open {} yourself: {}", step.title, step.url)
        });
    }

    /// Fake Settings data for the headless screenshots and tests: what each section shows once the core has answered. Built
    /// from the same answers a core gives (the audit log text goes through the real spend parser).
    pub fn apply_settings_fixture(&mut self) {
        use shell_core::autoconnect::FoundCore;
        use shell_core::openrouter::{Account, Credits, Generation, KeyInfo, ModelPrice, Source};
        use shell_core::settings_data::{
            CoreInfo, Load, MemoryStats, spend_from_audit, today_days, ymd_string,
        };
        let model = |id: &str, ctx: u64, p: f64, c: f64, tools: bool, reasoning: bool| ModelPrice {
            id: id.into(),
            name: id.into(),
            context: ctx,
            prompt_per_m: p,
            completion_per_m: c,
            tools,
            reasoning,
        };
        self.or_account = Some(Account {
            source: Source::Core,
            key: Some(KeyInfo {
                label: "sk-or-v1-abc...xyz".into(),
                usage: 82.29,
                limit: Some(50.0),
                limit_remaining: Some(12.5),
                is_free_tier: false,
                limit_reset: Some("monthly".into()),
                usage_daily: Some(1.1),
                usage_weekly: Some(7.7),
                usage_monthly: Some(37.5),
            }),
            credits: None,
            credits_need_management_key: true,
            models: vec![
                model("z-ai/glm-5.3", 200_000, 0.6, 2.2, true, true),
                model("z-ai/glm-5.3-flash", 128_000, 0.1, 0.4, true, false),
                model("anthropic/claude-sonnet", 200_000, 3.0, 15.0, true, true),
                model("openai/gpt-mini", 128_000, 0.15, 0.6, true, false),
                model("meta/llama-70b", 32_000, 0.9, 0.9, false, false),
                model("google/gemini-flash", 1_000_000, 0.1, 0.4, true, true),
            ],
            generations: vec![
                Generation {
                    id: "gen-3".into(),
                    model: "z-ai/glm-5.3".into(),
                    cost: 0.00033,
                    prompt_tokens: 760,
                    completion_tokens: 52,
                    latency_ms: Some(1100.0),
                    ts: Some("2026-11-15T12:04:10Z".into()),
                    task_id: Some("t1".into()),
                },
                Generation {
                    id: "gen-2".into(),
                    model: "z-ai/glm-5.3".into(),
                    cost: 0.00041,
                    prompt_tokens: 640,
                    completion_tokens: 88,
                    latency_ms: Some(1900.0),
                    ts: Some("2026-11-15T12:03:58Z".into()),
                    task_id: Some("t1".into()),
                },
                Generation {
                    id: "gen-1".into(),
                    model: "z-ai/glm-5.3-flash".into(),
                    cost: 0.0002,
                    prompt_tokens: 420,
                    completion_tokens: 30,
                    latency_ms: Some(700.0),
                    ts: Some("2026-11-14T09:30:00Z".into()),
                    task_id: None,
                },
            ],
            problems: Vec::new(),
        });
        let _ = Credits {
            total: 0.0,
            used: 0.0,
        };
        self.bindings = Load::Ready(serde_json::json!({
            "d1": true, "r2": true, "durable_objects": true, "workflow": true, "container": true, "access_secret": true, "model_key": true,
            "gateway": false, "gateway_configured": true, "gateway_via": "direct",
            "gateway_reason": "AI Gateway refused the request (needs authentication)."
        }));
        self.core_info = Load::Ready(CoreInfo {
            version: "0.1.0".into(),
            served_from: Some("SJC".into()),
        });
        let mut found = FoundCore::new(
            "open-computer-core",
            "Your account",
            "https://open-computer-core.you.workers.dev",
            "0",
        );
        found.modified_on = Some("2026-11-15T19:20:00Z".into());
        self.found_core = Some(found);
        let today = today_days();
        let line = |back: i64, cost: f64| {
            format!(
                r#"{{"seq":1,"ts":"{}T10:00:00.000Z","task_id":"t","kind":"event:usage","data":{{"cost":{cost}}}}}"#,
                ymd_string(today - back)
            )
        };
        let audit = [
            line(0, 0.012),
            line(1, 0.031),
            line(2, 0.0),
            line(3, 0.02),
            line(4, 0.008),
            line(6, 0.045),
            line(8, 0.015),
            line(11, 0.027),
            line(12, 0.004),
        ]
        .join("\n");
        self.spend = Load::Ready(spend_from_audit(&audit, today, Some("ok")));
        self.memory = Load::Ready(MemoryStats {
            blocks: vec![("agent_notes".into(), 640), ("user_profile".into(), 212)],
            entries: 7,
            newest: Some("2026-11-15T12:05:00Z".into()),
        });
        self.cf_state = Some(signin::SignIn {
            signed_in: true,
            email: Some("you@example.com".into()),
            accounts: vec!["Your account".into()],
            scopes: vec![
                "account".into(),
                "workers".into(),
                "d1".into(),
                "containers".into(),
            ],
            missing: Vec::new(),
        });
        self.cf_access = Some(AccessState::NotProtected);
    }

    /// Fake conversations for the workspace screenshots: `OC_SCREEN_WORKSPACE=empty|running|done`. Built from the same events a
    /// core streams, so the screens draw what a real task would show. Never touches a network or an account.
    pub fn apply_workspace_fixture(&mut self, kind: &str) {
        use serde_json::json;
        self.harness = Harness::BuiltIn;
        let Some(m) = self.main.as_mut() else { return };
        let run = |chat: &mut shell_core::chat::Chat, events: &[serde_json::Value]| {
            for e in events {
                chat.apply(e);
            }
        };
        // Two earlier tasks for the sidebar.
        let earlier = |goal: &str, answer: &str, ago_secs: u64, session: &str| {
            let mut c = shell_core::chat::Chat::default();
            c.push_user(goal);
            c.apply(&json!({"type": "message.done", "text": answer}));
            c.apply(&json!({"type": "run", "status": "done"}));
            crate::main_app::Thread {
                session_id: session.to_owned(),
                chat: c,
                began: std::time::SystemTime::now() - std::time::Duration::from_secs(ago_secs),
            }
        };
        if kind != "empty" {
            m.threads.push(earlier(
                "Find the current Node.js LTS",
                "Node.js 24 is the current LTS.",
                26 * 3600,
                "app-old-1",
            ));
            m.threads.push(earlier(
                "Summarize my notes from last week",
                "You worked on the sandbox and the desktop shell.",
                3 * 3600,
                "app-old-2",
            ));
        }
        m.began = std::time::SystemTime::now() - std::time::Duration::from_secs(95);
        m.chat = shell_core::chat::Chat::default();
        if kind == "empty" {
            return;
        }
        m.chat.push_user("Write a file named notes.txt that says hello, then run it through wc and tell me the result.");
        run(
            &mut m.chat,
            &[
                json!({"type": "run", "status": "running", "task_id": "t1"}),
                json!({"type": "reasoning.delta", "text": "I need to write the file first, then count its words with wc."}),
                json!({"type": "reasoning.done"}),
                json!({"type": "tool_call.started", "call_id": "c1", "name": "sandbox.write_file"}),
                json!({"type": "tool_call.finished", "call_id": "c1", "name": "sandbox.write_file", "args": "{\"path\":\"/workspace/notes.txt\",\"content\":\"hello\\n\"}", "risk": "write"}),
                json!({"type": "tool_call.result", "call_id": "c1", "status": "ok", "output": "wrote 6 bytes to /workspace/notes.txt", "ms": 212.0}),
                json!({"type": "tool_call.started", "call_id": "c2", "name": "sandbox.exec"}),
                json!({"type": "tool_call.finished", "call_id": "c2", "name": "sandbox.exec", "args": "{\"command\":\"wc /workspace/notes.txt\"}", "risk": "exec"}),
            ],
        );
        if kind == "running" {
            run(
                &mut m.chat,
                &[
                    json!({"type": "approval", "id": "a1", "hash": "h", "state": "requested", "summary": "Run `wc /workspace/notes.txt` in the sandbox"}),
                    json!({"type": "usage", "step": 0, "prompt": 640, "completion": 88, "reasoning": 31, "cost": 0.00041}),
                    json!({"type": "cost", "task_total": 0.00041, "day_total": 0.0123, "task_cap": 1.0, "day_cap": 5.0}),
                    json!({"type": "run", "status": "waiting"}),
                ],
            );
        } else {
            run(
                &mut m.chat,
                &[
                    json!({"type": "tool_call.result", "call_id": "c2", "status": "ok", "output": "1 1 6 /workspace/notes.txt", "ms": 1840.0}),
                    json!({"type": "usage", "step": 0, "prompt": 640, "completion": 88, "reasoning": 31, "cost": 0.00041}),
                    json!({"type": "usage", "step": 1, "prompt": 760, "completion": 52, "reasoning": 0, "cost": 0.00033}),
                    json!({"type": "message.delta", "text": "I wrote `notes.txt` with the word **hello** and counted it with `wc`:\n\n- 1 line\n- 1 word\n- 6 bytes\n\nThe file is at `/workspace/notes.txt`."}),
                    json!({"type": "message.done", "text": ""}),
                    json!({"type": "cost", "task_total": 0.00074, "day_total": 0.0127, "task_cap": 1.0, "day_cap": 5.0}),
                    json!({"type": "run", "status": "done"}),
                ],
            );
        }
        m.selected_step = None;
        if std::env::var_os("OC_SCREEN_SIDEBAR_OFF").is_some() {
            m.sidebar_open = false;
        }
    }

    /// Fake state for headless screenshots only (`OC_SCREEN_FIXTURE=1`); never touches the network or any account.
    pub fn apply_screen_fixture(&mut self) {
        use shell_core::openrouter::{Account, Generation, KeyInfo, ModelPrice};
        self.harness = Harness::Hermes;
        self.harness_info = vec![
            harness::Detected {
                harness: Harness::BuiltIn,
                installed: true,
                version: None,
            },
            harness::Detected {
                harness: Harness::Hermes,
                installed: true,
                version: Some("Hermes Agent v0.23.1".to_owned()),
            },
            harness::Detected {
                harness: Harness::OpenCode,
                installed: false,
                version: None,
            },
        ];
        self.or_account = Some(Account {
            source: shell_core::openrouter::Source::Core,
            key: Some(KeyInfo {
                label: "sk-or-v1-abc...xyz".into(),
                usage: 82.29,
                limit: Some(50.0),
                limit_remaining: Some(12.5),
                is_free_tier: false,
                limit_reset: Some("monthly".into()),
                usage_daily: Some(1.1),
                usage_weekly: Some(7.7),
                usage_monthly: Some(37.5),
            }),
            credits: None,
            credits_need_management_key: true,
            models: vec![
                ModelPrice {
                    id: "vendor/small".into(),
                    name: "Small".into(),
                    context: 128_000,
                    prompt_per_m: 0.1,
                    completion_per_m: 0.4,
                    tools: true,
                    reasoning: false,
                },
                ModelPrice {
                    id: "vendor/large".into(),
                    name: "Large".into(),
                    context: 200_000,
                    prompt_per_m: 3.0,
                    completion_per_m: 15.0,
                    tools: true,
                    reasoning: true,
                },
            ],
            generations: vec![Generation {
                id: "gen-1".into(),
                model: "vendor/small".into(),
                cost: 0.0021,
                prompt_tokens: 120,
                completion_tokens: 40,
                latency_ms: Some(900.0),
                ts: Some("2026-11-15T12:00:00Z".into()),
                task_id: Some("t1".into()),
            }],
            problems: Vec::new(),
        });
        self.or_line = Some("Connected. The key is saved in your system keyring.".to_owned());
        self.cf_state = Some(signin::SignIn {
            signed_in: true,
            email: Some("you@example.com".into()),
            accounts: vec!["Your account".into()],
            scopes: vec![
                "account".into(),
                "workers".into(),
                "d1".into(),
                "containers".into(),
            ],
            missing: Vec::new(),
        });
        self.cf_gateway = Some(true);
        self.cf_access = Some(AccessState::Protected);
        // Screenshot states for the sign-in feedback: OC_SCREEN_SIGNING=1 (busy, Wrangler downloading) and
        // OC_SCREEN_SIGNING=url (the browser could not be opened, so the page is offered as a button).
        if let Ok(mode) = std::env::var("OC_SCREEN_SIGNING") {
            self.cf_state = None;
            self.signing_in = true;
            if mode == "url" {
                self.cloudflare_line = Some(
                    "Your browser did not open. Use the button to open the sign-in page."
                        .to_owned(),
                );
                self.cf_open_url = Some("https://dash.cloudflare.com/oauth2/auth".to_owned());
            } else {
                self.cloudflare_line =
                    Some(signin::locate_wrangler(None, None, &|_| false).describe(None));
            }
        }
        if let Ok(kind) = std::env::var("OC_SCREEN_WORKSPACE") {
            self.apply_workspace_fixture(&kind);
        }
        if std::env::var_os("OC_SCREEN_SETTINGS").is_some() {
            self.apply_settings_fixture();
        }
        // OC_SCREEN_ROWS=1: a test chain part way through, for the Diagnostics screenshot.
        if std::env::var_os("OC_SCREEN_ROWS").is_some() {
            use shell_core::diagnostics::{Status, initial_rows};
            let mut rows = initial_rows();
            let n = rows.len();
            for (i, r) in rows.iter_mut().enumerate() {
                let (status, ms) = match i {
                    x if x + 7 < n => (Status::Pass, Some(180.0 + 90.0 * x as f64)),
                    x if x + 7 == n => (Status::Fail, Some(2400.0)),
                    x if x + 6 == n => (Status::Running, None),
                    _ => (Status::Pending, None),
                };
                r.status = status;
                r.ms = ms;
                if status == Status::Fail {
                    r.detail = "The model did not call the tool.".to_owned();
                    r.fix = Some(
                        "Pick another model in Settings, then run the tests again.".to_owned(),
                    );
                }
            }
            self.diag_rows = rows;
            self.diag_running = true;
        }
    }
}
