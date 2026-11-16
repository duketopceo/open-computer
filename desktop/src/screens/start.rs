//! Get started: three clicks and no typing. Sign in with Cloudflare, set up or connect to your core, connect OpenRouter. Then
//! the app opens on Chat. Typing an address or a secret lives under Advanced.

use rune::{El, col, row, text};
use rune_kit::{Badge, BadgeVariant, Button, ButtonVariant, Kit, Spinner};
use shell_core::autoconnect::{CoreStep, RowStatus, SetupRow};
use shell_core::signin::AI_GATEWAY_STEP;

use super::{accent_button, caption, connect};
use crate::app::App;

/// Which step the person is on: the first one not done.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Current {
    SignIn,
    Core,
    OpenRouter,
}

pub fn current(s: &App) -> Current {
    if !s.cf_signed_in() && !s.core_connected() {
        Current::SignIn
    } else if !s.core_connected() {
        Current::Core
    } else {
        Current::OpenRouter
    }
}

fn badge(k: &Kit, label: &str, done: bool) -> El {
    Badge::new(label.to_owned())
        .variant(if done {
            BadgeVariant::Accent
        } else {
            BadgeVariant::Neutral
        })
        .build(k)
}

fn step(k: &Kit, key: &str, n: u32, title: &str, done: bool, status: &str) -> El {
    col()
        .key(key.to_owned())
        .gap(8.0)
        .w_full()
        .padding(12.0)
        .radius(8.0)
        .border(1.0, k.c.border_strong)
        .child(
            row()
                .gap(8.0)
                .center_y()
                .child(
                    text(format!("{n}. {title}"))
                        .font(k.ty.ui_medium)
                        .color(k.c.text_strong),
                )
                .child(badge(k, status, done)),
        )
}

/// A button that is the accent when it is the step to do now, and plain otherwise.
fn action(
    k: &Kit,
    now: bool,
    key: &'static str,
    label: &str,
    busy: bool,
    f: impl Fn(&mut App) + 'static,
) -> El {
    if now {
        // In a row, so the button is as wide as its label instead of stretching across the card.
        row().child(accent_button(
            k,
            key,
            if busy { "Working…" } else { label },
            !busy,
            f,
        ))
    } else {
        row().child(
            Button::new(label.to_owned())
                .variant(if busy {
                    ButtonVariant::Loading
                } else {
                    ButtonVariant::Neutral
                })
                .disabled(busy)
                .on_press(f)
                .build(k),
        )
    }
}

fn setup_rows(k: &Kit, rows: &[SetupRow]) -> El {
    let mut list = col().key("setup-rows").gap(4.0).w_full();
    for r in rows {
        let mark = match r.status {
            RowStatus::Running => "...",
            RowStatus::Pass => "ok",
            RowStatus::Fail => "FAIL",
            RowStatus::Skip => "skip",
        };
        list = list.child(
            row()
                .gap(8.0)
                .w_full()
                .child(if r.status == RowStatus::Running {
                    col().w(40.0).child(
                        Spinner::new()
                            .size(14.0)
                            .color(crate::theme::accent_amber())
                            .build(k),
                    )
                } else {
                    col()
                        .w(40.0)
                        .child(text(mark).font(k.ty.ui).color(k.c.text_base))
                })
                .child(
                    text(r.title.clone())
                        .font(k.ty.ui)
                        .color(k.c.text_base)
                        .grow(1.0),
                ),
        );
        if r.status == RowStatus::Fail {
            for d in &r.detail {
                list = list.child(caption(k, format!("      {d}")));
            }
        }
    }
    list
}

pub fn view(s: &App, k: &Kit) -> El {
    let now = current(s);
    let signed = s.cf_signed_in();
    let core = s.core_step();
    let connected = matches!(core, CoreStep::Connected);
    let key_here = s.onboarding.secrets_set().model_key;
    let key_ready = key_here || s.core_has_model_key;

    // 1. Cloudflare
    let who = s
        .cf_state
        .as_ref()
        .filter(|c| c.signed_in)
        .map(|c| c.email.clone().unwrap_or_else(|| "signed in".to_owned()));
    let mut one = step(
        k,
        "step-signin",
        1,
        "Cloudflare",
        signed || connected,
        if signed || connected {
            "Signed in"
        } else {
            "Not signed in"
        },
    );
    one = one.child(caption(
        k,
        "Your browser opens Cloudflare's own page and you click Allow. Nothing is typed or pasted.",
    ));
    if let Some(w) = who {
        one = one.child(caption(k, format!("Signed in as {w}.")));
    }
    if !(signed || connected) {
        one = one.child(action(
            k,
            now == Current::SignIn,
            "sign-in",
            "Sign in with Cloudflare",
            s.signing_in,
            |s: &mut App| s.sign_in_cloudflare(),
        ));
    }
    if let Some(l) = &s.cloudflare_line
        && s.signing_in
    {
        one = one.child(text(l.clone()).font(k.ty.ui).color(k.c.text_base).w_full());
    }
    if s.cf_open_url.is_some() {
        one = one.child(
            row().child(
                Button::new("Open sign-in page")
                    .variant(ButtonVariant::Submit)
                    .on_press(|s: &mut App| s.open_sign_in_page())
                    .build(k),
            ),
        );
    }

    // 2. The core
    let status = match &core {
        CoreStep::Connected => "Connected",
        CoreStep::SettingUp => "Setting up",
        CoreStep::Finding => "Looking",
        CoreStep::CanConnect(_) => "Found",
        CoreStep::NeedSignIn | CoreStep::CanSetUp => "Not connected",
    };
    let mut two = step(k, "step-core", 2, "Your core", connected, status);
    match &core {
        CoreStep::Connected => {
            let words = if s.onboarding.local_only() {
                "This computer only: the agent runs here with a local sandbox.".to_owned()
            } else {
                format!("Connected to {}.", s.onboarding.cloud_url())
            };
            two = two.child(caption(k, words));
        }
        CoreStep::NeedSignIn => {
            two = two.child(caption(
                k,
                "Sign in first; Open Computer then looks for your core by itself.",
            ))
        }
        CoreStep::Finding => {
            two = two.child(caption(
                k,
                "Looking for your core on your Cloudflare account…",
            ))
        }
        CoreStep::SettingUp => {
            two = two.child(caption(
                k,
                "Setting up in your Cloudflare account. This takes a few minutes.",
            ));
        }
        CoreStep::CanConnect(name) => {
            let label = format!("Connect to your core at {name}");
            two = two.child(action(
                k,
                now == Current::Core,
                "connect-core",
                &label,
                false,
                |s: &mut App| s.connect_found_core(),
            ));
        }
        CoreStep::CanSetUp => {
            two = two
                .child(caption(k, "No core was found. Setup builds one in your account: a database, storage, secrets and the Worker. Docker must be running."))
                .child(action(k, now == Current::Core, "set-up", "Set up Open Computer in your Cloudflare account", false, |s: &mut App| s.start_setup()));
        }
    }
    if !s.setup_rows.is_empty() {
        two = two.child(setup_rows(k, &s.setup_rows));
    }
    if let Some(l) = &s.core_line {
        two = two.child(text(l.clone()).font(k.ty.ui).color(k.c.text_base).w_full());
    }
    if s.gateway_link && !connected && (s.setup_running || !s.setup_rows.is_empty()) {
        two = two.child(
            row().gap(8.0).center_y().child(
                Button::new("Open AI Gateway page")
                    .variant(ButtonVariant::Neutral)
                    .on_press(|s: &mut App| s.open_dashboard(AI_GATEWAY_STEP))
                    .build(k),
            ),
        );
    }

    // 3. OpenRouter
    let mut three = step(
        k,
        "step-openrouter",
        3,
        "OpenRouter (pays for the AI models)",
        key_ready,
        if key_ready { "Ready" } else { "Not connected" },
    );
    if s.core_has_model_key {
        three = three.child(caption(k, "Your core already has an OpenRouter key."));
    } else if key_here {
        three = three.child(caption(
            k,
            "OpenRouter is connected. The key is in your system keyring.",
        ));
    } else {
        three = three
            .child(caption(k, "Your browser opens OpenRouter and you click Authorize. The key is saved in your system keyring."))
            .child(action(k, now == Current::OpenRouter, "connect-openrouter", "Connect OpenRouter", s.or_busy, |s: &mut App| s.connect_openrouter()));
    }
    if let Some(l) = &s.or_line {
        three = three.child(text(l.clone()).font(k.ty.ui).color(k.c.text_base).w_full());
    }

    // Advanced: the address, the secret and the key, typed by hand.
    let mut advanced = col().key("advanced").gap(12.0).w_full().child(
        Button::new(if s.show_advanced {
            "Advanced (hide)"
        } else {
            "Advanced"
        })
        .variant(ButtonVariant::Ghost)
        .on_press(|s: &mut App| s.toggle_advanced())
        .build(k),
    );
    if s.show_advanced {
        advanced = advanced.child(connect::view(s, k));
    }

    col()
        .key("start")
        .gap(12.0)
        .w_full()
        .child(one)
        .child(two)
        .child(three)
        .child(advanced)
}
