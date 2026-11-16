//! The app after setup: Chat, Settings and Diagnostics, in one black and white frame.

use rune::{El, ScrollSpec, col, row, text};
use rune_kit::{Badge, BadgeVariant, Button, ButtonVariant, Field, Kit, TextInput};
use shell_core::chat::ApprovalCard;
use shell_core::defaults;

use super::{caption, test_connection};
use crate::app::App;
use crate::main_app::Main;

pub fn view(s: &App, k: &Kit, m: &Main) -> El {
    super::workspace::view(s, k, m)
}

fn card(k: &Kit, key: String) -> El {
    col()
        .key(key)
        .gap(4.0)
        .w_full()
        .padding(10.0)
        .radius(8.0)
        .border(1.0, k.c.border_strong)
}

pub(super) fn approval_card(k: &Kit, s: &App, key: String, a: &ApprovalCard) -> El {
    let word = match a.state.as_str() {
        "requested" => "Needs your decision",
        "approved" => "Approved",
        "denied" => "Denied",
        _ => "Expired",
    };
    let mut c = card(k, key)
        .child(
            row()
                .gap(8.0)
                .center_y()
                .child(text("Approval").font(k.ty.ui_medium).color(k.c.text_strong))
                .child(Badge::new(word).variant(BadgeVariant::Accent).build(k)),
        )
        .child(text(a.summary.clone()).font(k.ty.ui).color(k.c.text_base));
    if a.state == "requested" {
        c = c.child(
            TextInput::new(&s.approval_note)
                .placeholder("Add a note (optional). It is kept in the audit log.")
                .on_edit(|s: &mut App| &mut s.approval_note)
                .width(None)
                .build(k),
        );
        c = c.child(
            row()
                .gap(8.0)
                .child(
                    Button::new("Approve")
                        .variant(ButtonVariant::Submit)
                        .on_press(|s: &mut App| s.decide_pending(true))
                        .build(k),
                )
                .child(
                    Button::new("Deny")
                        .variant(ButtonVariant::Neutral)
                        .on_press(|s: &mut App| s.decide_pending(false))
                        .build(k),
                ),
        );
    }
    c
}

pub(super) fn settings(s: &App, k: &Kit, m: &Main) -> El {
    let section = |title: &str, key: &str| {
        col()
            .key(key.to_owned())
            .gap(6.0)
            .w_full()
            .padding(10.0)
            .radius(8.0)
            .border(1.0, k.c.border_strong)
            .child(
                text(title.to_owned())
                    .font(k.ty.ui_medium)
                    .color(k.c.text_strong),
            )
    };
    let line = |k: &Kit, t: String| text(t).font(k.ty.ui).color(k.c.text_base).w_full();
    let note = |k: &Kit, t: &str| caption(k, t.to_owned()).w_full();
    let st = m.settings.as_ref();
    let num = |key: &str| st.and_then(|v| v.get(key)).and_then(|v| v.as_f64());
    let effective = num("effective_daily_cap_usd");
    let policy_max = num("policy_max_daily_cap_usd");
    let on = |b: bool| if b { "on" } else { "off" };

    let field = |label: &str,
                 edit: &rune::TextEdit,
                 secure_hint: &str,
                 on: fn(&mut App) -> &mut rune::TextEdit| {
        Field::new()
            .label(label.to_owned())
            .control(
                TextInput::new(edit)
                    .placeholder(secure_hint.to_owned())
                    .on_edit(on)
                    .width(None)
                    .build(k),
            )
            .build(k)
    };
    let flag = |on: bool| if on { "set" } else { "not set" };
    let saved = |key: &str, fallback: String| {
        st.and_then(|v| v.get(key))
            .and_then(|v| v.as_str())
            .map_or(fallback, str::to_owned)
    };
    let secrets = st.and_then(|v| v.get("secrets_set"));
    let key_set = |name: &str, local: bool| {
        secrets
            .and_then(|v| v.get(name))
            .and_then(|v| v.as_bool())
            .unwrap_or(local)
    };
    let models = section("Models and keys", "set-models")
        .child(line(k, format!("Default model: {}", saved("default_model", defaults::default_model()))))
        .child(line(k, format!("Worker model: {}", saved("worker_model", defaults::worker_model()))))
        .child(field("Default model", &s.set_default_model, "vendor/model (leave empty to keep)", |s| &mut s.set_default_model))
        .child(field("Worker model", &s.set_worker_model, "vendor/model (leave empty to keep)", |s| &mut s.set_worker_model))
        .child(line(k, format!("OpenRouter key: {}", flag(key_set("model_key", s.onboarding.secrets_set().model_key)))))
        .child(field("Replace the OpenRouter key", &s.set_model_key, "Paste a new key (never shown again)", |s| &mut s.set_model_key))
        .child(line(k, format!("Custom endpoint: {}", saved("custom_endpoint_url", "not set".to_owned()))))
        .child(field("Custom OpenAI-compatible address", &s.set_custom_url, "https://llm.example.com/v1", |s| &mut s.set_custom_url))
        .child(field("Custom endpoint model", &s.set_custom_model, "the model name it expects", |s| &mut s.set_custom_model))
        .child(line(k, format!("Custom endpoint key: {}", flag(key_set("custom_key", false)))))
        .child(field("Custom endpoint key", &s.set_custom_key, "Paste its key (never shown again)", |s| &mut s.set_custom_key))
        .child(note(k, "Choose the custom endpoint for a task by naming the model \"custom\". A cloud core keeps its keys in its own secret store; use `oc setup` there."))
        .child(
            row()
                .gap(8.0)
                .child(Button::new("Save").variant(ButtonVariant::Submit).on_press(|s: &mut App| s.save_model_settings()).build(k))
                .child(Button::new("Open Codex").variant(ButtonVariant::Neutral).on_press(|s: &mut App| s.launch_codex()).build(k)),
        );

    let routing = section("Routing", "set-routing")
        .child(line(
            k,
            match (&s.cf_gateway, &s.cf_gateway_reason) {
                (_, Some(why)) => format!(
                    "AI Gateway is set up but refused the request ({}); calls go straight to OpenRouter.",
                    why.trim_end_matches('.')
                ),
                (Some(true), None) => "Through your Cloudflare AI Gateway, then OpenRouter. The gateway keeps the logs.".to_owned(),
                (Some(false), None) => "Direct to OpenRouter; AI Gateway is not configured on this core.".to_owned(),
                (None, None) => "Routing is checked when a core is connected.".to_owned(),
            },
        ))
        .child(note(k, "Prices come from OpenRouter and refresh daily."));

    let sandbox = section("Sandbox", "set-sandbox")
        .child(line(
            k,
            if s.onboarding.local_only() {
                "bubblewrap on this computer".to_owned()
            } else {
                "Cloudflare Container, one fresh sandbox per task".to_owned()
            },
        ))
        .child(note(
            k,
            "No key is ever visible inside, and outbound internet is closed.",
        ));

    let cap_button = |label: &str, usd: Option<f64>| {
        Button::new(label)
            .variant(ButtonVariant::Ghost)
            .on_press(move |s: &mut App| s.set_daily_cap(usd))
            .build(k)
    };
    let mut spending = section("Spending", "set-spend").child(line(
        k,
        format!(
            "Daily cap in effect: {}",
            effective.map_or("loading".to_owned(), |c| format!("${c:.2}"))
        ),
    ));
    if let Some(p) = policy_max {
        spending = spending.child(note(
            k,
            &format!("Your IT policy holds the cap at ${p:.2} or less."),
        ));
    }
    spending = spending.child(
        row()
            .gap(8.0)
            .child(cap_button("$1", Some(1.0)))
            .child(cap_button("$5 (default)", None))
            .child(cap_button("$20", Some(20.0)))
            .child(cap_button("$50", Some(50.0))),
    );

    let permissions = section("Permissions and approvals", "set-perms")
        .child(line(
            k,
            "Reading runs freely. Writing files, running commands and using the network ask first."
                .to_owned(),
        ))
        .child(note(
            k,
            "Each approval is tied to the exact action you saw, works once, and expires.",
        ));

    let memory = section("Memory", "set-memory").child(line(
        k,
        format!(
            "Memory is {}: summaries of finished tasks are kept and recalled with a source.",
            on(s.onboarding.memory_enabled())
        ),
    ));

    let privacy = section("Data and privacy", "set-privacy")
        .child(line(k, "Keys are stored in your system keyring, or in memory only if it is locked.".to_owned()))
        .child(note(k, "Prompts and answers are not written to the audit log. Nothing is sent anywhere except to your core and OpenRouter."));

    let appearance = section("Appearance", "set-appearance").child(line(
        k,
        "Black and white. Dark by default; light follows your system.".to_owned(),
    ));

    let shortcuts = section("Shortcuts", "set-shortcuts").child(line(
        k,
        "Enter sends. Tab moves between controls. Space presses a button.".to_owned(),
    ));

    let integrations = section("Integrations, MCP and plugins", "set-integrations")
        .child(note(k, "Not available in this release."));
    let voice = section("Voice", "set-voice").child(note(k, "Not available in this release."));

    let dev = section("Developer", "set-dev")
        .child(line(k, "Same tests from the terminal: `oc doctor`. Same API as this app: /v1/tasks, /v1/sessions/:id/events.".to_owned()))
        .child(note(k, "The desktop talks to OpenRouter with a small typed client; the cloud core uses its own checks."));

    let policy = section("Policy and diagnostics", "set-policy")
        .child(note(k, "An IT policy file can lock the daily cap, limit models and tools, and set an OpenTelemetry endpoint. Traces carry names, timings, tokens and cost only."))
        .child(line(k, format!("Backend: {}", if s.onboarding.local_only() { "this computer only".to_owned() } else { s.onboarding.cloud_url().to_owned() })))
        .child(line(k, format!("Desktop version {}", env!("CARGO_PKG_VERSION"))));

    col()
        .key("settings")
        .w_full()
        .grow(1.0)
        .gap(8.0)
        .scroll(ScrollSpec {
            stick_bottom: false,
        })
        .child(super::integrations::harness(s, k))
        .child(super::integrations::openrouter(s, k))
        .child(super::integrations::cloudflare(s, k))
        .child(models)
        .child(routing)
        .child(sandbox)
        .child(spending)
        .child(permissions)
        .child(memory)
        .child(privacy)
        .child(appearance)
        .child(shortcuts)
        .child(integrations)
        .child(voice)
        .child(dev)
        .child(policy)
}

pub(super) fn diagnostics(s: &App, k: &Kit) -> El {
    col()
        .key("diagnostics")
        .w_full()
        .grow(1.0)
        .gap(10.0)
        .child(caption(
            k,
            "The same chain as the web wizard and `oc doctor`: backend, OpenRouter, speed, sandbox, memory, audit and spend.",
        ))
        .child(test_connection::header(s, k))
        .child(
            col()
                .key("diag-scroll")
                .w_full()
                .grow(1.0)
                .scroll(ScrollSpec {
                    stick_bottom: false,
                })
                .child(test_connection::full_tests(s, k)),
        )
}
