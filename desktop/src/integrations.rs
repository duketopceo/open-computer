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
        self.composer.replace_text("");
        self.harness_running = true;
        self.harness_cancel.store(false, Ordering::Relaxed);
        let cancel = self.harness_cancel.clone();
        let (tx, waker) = (self.tx(), self.waker());
        std::thread::spawn(move || {
            let r = harness::run_task(&mut *adapter, &goal, None, None, None, &cancel, &mut |ev| {
                notify(&tx, &waker, Msg::ChatEvent(ev))
            });
            match r {
                Ok(()) => notify(&tx, &waker, Msg::ChatEnd),
                Err(e) => notify(&tx, &waker, Msg::ChatError(e)),
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
    }

    /// Loads key usage and limit, credits, models with prices and recent generation stats.
    pub fn refresh_openrouter(&mut self) {
        let key = match self.store.get(Slot::ModelKey) {
            Ok(Some(k)) if !k.is_empty() && k != "managed-by-the-cloud-core" => k,
            _ => {
                self.or_busy = false;
                self.or_line = Some(
                    "No OpenRouter key is saved on this computer. Click Connect OpenRouter."
                        .to_owned(),
                );
                return;
            }
        };
        self.or_busy = true;
        let (tx, waker) = (self.tx(), self.waker());
        std::thread::spawn(move || {
            let c = openrouter::Client::new(openrouter::BASE, &key);
            notify(
                &tx,
                &waker,
                Msg::OrAccount(openrouter::load_account(&c, &[])),
            );
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
        if !self.cf_start("Starting the Cloudflare sign-in.") {
            return;
        }
        let (tx, waker) = (self.tx(), self.waker());
        std::thread::spawn(move || {
            let r = signin::sign_in(&mut |p| notify(&tx, &waker, Msg::CfProgress(p)));
            notify(&tx, &waker, Msg::SignedIn(r));
        });
    }

    pub fn switch_cloudflare(&mut self) {
        if !self.cf_start("Signing out, then signing in again.") {
            return;
        }
        let (tx, waker) = (self.tx(), self.waker());
        std::thread::spawn(move || {
            let r = signin::switch_account(&mut |p| notify(&tx, &waker, Msg::CfProgress(p)));
            notify(&tx, &waker, Msg::SignedIn(r));
        });
    }

    pub fn sign_out_cloudflare(&mut self) {
        if !self.cf_start("Signing out.") {
            return;
        }
        let (tx, waker) = (self.tx(), self.waker());
        std::thread::spawn(move || notify(&tx, &waker, Msg::SignedIn(signin::sign_out())));
    }

    /// Re-reads who is signed in (step 1 again) and checks the two dashboard steps.
    pub fn check_cloudflare(&mut self) {
        if !self.cf_start("Checking the Cloudflare sign-in.") {
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

    pub fn open_dashboard(&mut self, step: signin::DashboardStep) {
        self.cloudflare_line = Some(if open_url(step.url) {
            format!("Opened {} in your browser.", step.title)
        } else {
            format!("Open {} yourself: {}", step.title, step.url)
        });
    }

    /// Fake state for headless screenshots only (`OC_SCREEN_FIXTURE=1`); never touches the network or any account.
    pub fn apply_screen_fixture(&mut self) {
        use shell_core::openrouter::{Account, Credits, Generation, KeyInfo, ModelPrice};
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
            key: Some(KeyInfo {
                label: "sk-or-v1-abc...xyz".into(),
                usage: 1.25,
                limit: Some(10.0),
                limit_remaining: Some(8.75),
                is_free_tier: false,
            }),
            credits: Some(Credits {
                total: 20.0,
                used: 3.5,
            }),
            models: vec![
                ModelPrice {
                    id: "vendor/small".into(),
                    name: "Small".into(),
                    context: 128_000,
                    prompt_per_m: 0.1,
                    completion_per_m: 0.4,
                },
                ModelPrice {
                    id: "vendor/large".into(),
                    name: "Large".into(),
                    context: 200_000,
                    prompt_per_m: 3.0,
                    completion_per_m: 15.0,
                },
            ],
            generations: vec![Generation {
                id: "gen-1".into(),
                model: "vendor/small".into(),
                cost: 0.0021,
                prompt_tokens: 120,
                completion_tokens: 40,
                latency_ms: Some(900.0),
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
        self.cf_gateway = Some(false);
        self.cf_access = Some(AccessState::Protected);
    }
}
