//! Settings sections for the agent harness, OpenRouter and Cloudflare. Black and white, one button style per intent.

use rune::{El, col, row, text};
use rune_kit::{Badge, BadgeVariant, Button, ButtonVariant, Kit};
use shell_core::harness::Harness;
use shell_core::openrouter::dollars;
use shell_core::signin::{ACCESS_STEP, AI_GATEWAY_STEP, AccessState, DashboardStep, step1};

use super::caption;

fn note(k: &Kit, t: impl Into<String>) -> El {
    caption(k, t.into()).w_full()
}
use crate::app::App;

fn section(k: &Kit, title: &str, key: &str) -> El {
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
}

fn line(k: &Kit, t: String) -> El {
    text(t).font(k.ty.ui).color(k.c.text_base).w_full()
}

fn badge(k: &Kit, label: &str, on: bool) -> El {
    Badge::new(label.to_owned())
        .variant(if on {
            BadgeVariant::Accent
        } else {
            BadgeVariant::Neutral
        })
        .build(k)
}

pub fn harness(s: &App, k: &Kit) -> El {
    let mut sec = section(k, "Agent harness", "set-harness").child(note(
        k,
        "Who runs your tasks. Hermes (NousResearch hermes-agent) and OpenCode run here as subprocesses.",
    ))
    .child(note(k, "They use their own permissions: the sandbox, approvals and spend caps apply only to Built-in."));
    for h in Harness::ALL {
        let info = s.harness_info.iter().find(|d| d.harness == h);
        let summary = info.map_or_else(
            || {
                if h == Harness::BuiltIn {
                    "Always available.".to_owned()
                } else {
                    "Checking.".to_owned()
                }
            },
            |d| d.summary(),
        );
        let chosen = s.harness == h;
        sec = sec.child(
            col()
                .key(format!("harness-{}", h.id()))
                .gap(4.0)
                .w_full()
                .child(
                    row()
                        .gap(8.0)
                        .center_y()
                        .child(
                            Button::new(h.label())
                                .variant(if chosen {
                                    ButtonVariant::Submit
                                } else {
                                    ButtonVariant::Neutral
                                })
                                .on_press(move |s: &mut App| s.set_harness(h))
                                .build(k),
                        )
                        .child(badge(
                            k,
                            if chosen { "In use" } else { "Not in use" },
                            chosen,
                        )),
                )
                .child(note(k, h.describe()))
                .child(line(k, summary)),
        );
    }
    sec.child(
        row().child(
            Button::new("Check again")
                .variant(ButtonVariant::Neutral)
                .on_press(|s: &mut App| s.detect_harnesses())
                .build(k),
        ),
    )
}

pub fn openrouter(s: &App, k: &Kit) -> El {
    let key_set = s.onboarding.secrets_set().model_key;
    let mut sec = section(k, "OpenRouter", "set-openrouter")
        .child(note(k, "Connect once: your browser opens OpenRouter, you click Authorize, and the key is saved in your system keyring. Nothing is pasted."))
        .child(
            row()
                .gap(8.0)
                .center_y()
                .child(
                    Button::new("Connect OpenRouter")
                        .variant(if s.or_busy { ButtonVariant::Loading } else { ButtonVariant::Submit })
                        .disabled(s.or_busy)
                        .on_press(|s: &mut App| s.connect_openrouter())
                        .build(k),
                )
                .child(Button::new("Refresh").variant(ButtonVariant::Neutral).on_press(|s: &mut App| s.refresh_openrouter()).build(k))
                .child(Button::new("Remove key").variant(ButtonVariant::Neutral).on_press(|s: &mut App| s.forget_openrouter_key()).build(k))
                .child(badge(k, if key_set { "Key set" } else { "Key not set" }, key_set)),
        )
        .child(note(k, "No browser? Paste a key under Models and keys below; it is saved the same way."));
    if let Some(l) = &s.or_line {
        sec = sec.child(line(k, l.clone()));
    }
    let Some(a) = &s.or_account else { return sec };
    sec = sec.child(note(k, a.source.words()));
    if let Some(key) = &a.key {
        sec = sec.child(line(k, key.line()));
    }
    if let Some(c) = &a.credits {
        sec = sec.child(line(
            k,
            format!(
                "Credits: {} bought, {} used, {} remaining",
                dollars(c.total),
                dollars(c.used),
                dollars(c.remaining())
            ),
        ));
    }
    if a.credits_need_management_key {
        sec = sec.child(note(
            k,
            "Credits: OpenRouter only shows account credits to management keys. This key's own usage and limit are above.",
        ));
    }
    for p in &a.problems {
        sec = sec.child(note(k, p.clone()));
    }
    if !a.generations.is_empty() {
        sec = sec.child(
            text("Recent generations".to_owned())
                .font(k.ty.ui_medium)
                .color(k.c.text_strong),
        );
        for g in &a.generations {
            let lat = g
                .latency_ms
                .map_or(String::new(), |l| format!(", {:.1} s", l / 1000.0));
            sec = sec.child(line(
                k,
                format!(
                    "{}: {} for {} in and {} out{lat}",
                    g.model,
                    dollars(g.cost),
                    g.prompt_tokens,
                    g.completion_tokens
                ),
            ));
        }
    }
    if !a.models.is_empty() {
        sec = sec.child(
            text(format!(
                "Models and prices ({}), dollars per million tokens, cheapest first",
                a.models.len()
            ))
            .font(k.ty.ui_medium)
            .color(k.c.text_strong),
        );
        for m in a.models.iter().take(8) {
            sec = sec.child(line(
                k,
                format!(
                    "{}: {} in, {} out, {}k context",
                    m.id,
                    dollars(m.prompt_per_m),
                    dollars(m.completion_per_m),
                    m.context / 1000
                ),
            ));
        }
    }
    sec
}

pub fn cloudflare(s: &App, k: &Kit) -> El {
    let signed = s.cf_state.as_ref().is_some_and(|c| c.signed_in);
    let mut sec = section(k, "Cloudflare", "set-cloudflare")
        .child(note(k, "Sign in on Cloudflare's own page and click Allow. Wrangler keeps the session; Open Computer never reads or shows it."))
        .child(
            row()
                .gap(8.0)
                .center_y()
                .child(
                    Button::new("Sign in with Cloudflare")
                        .variant(if s.signing_in { ButtonVariant::Loading } else { ButtonVariant::Submit })
                        .disabled(s.signing_in)
                        .on_press(|s: &mut App| s.sign_in_cloudflare())
                        .build(k),
                )
                .child(Button::new("Switch account").variant(ButtonVariant::Neutral).disabled(s.signing_in).on_press(|s: &mut App| s.switch_cloudflare()).build(k))
                .child(Button::new("Sign out").variant(ButtonVariant::Neutral).disabled(s.signing_in).on_press(|s: &mut App| s.sign_out_cloudflare()).build(k))
                .child(Button::new("Check").variant(ButtonVariant::Neutral).disabled(s.signing_in).on_press(|s: &mut App| s.check_cloudflare()).build(k))
                .child(badge(k, if signed { "Signed in" } else { "Not signed in" }, signed)),
        );
    if let Some(l) = &s.cloudflare_line {
        sec = sec.child(line(k, l.clone()));
    }
    if s.signing_in {
        sec = sec.child(note(k, "Working… the first run can take up to 30 seconds while Wrangler is found or downloaded."));
    }
    if s.cf_open_url.is_some() {
        sec = sec.child(
            row().child(
                Button::new("Open sign-in page")
                    .variant(ButtonVariant::Submit)
                    .on_press(|s: &mut App| s.open_sign_in_page())
                    .build(k),
            ),
        );
    }
    if let Some(c) = &s.cf_state {
        if c.signed_in {
            sec = sec.child(line(
                k,
                format!(
                    "Account: {} ({})",
                    c.accounts.join(", "),
                    c.email
                        .clone()
                        .unwrap_or_else(|| "email not shown".to_owned())
                ),
            ));
            sec = sec.child(line(
                k,
                format!(
                    "Access granted: {}",
                    if c.scopes.is_empty() {
                        "none listed".to_owned()
                    } else {
                        c.scopes.join(", ")
                    }
                ),
            ));
        }
        let (ok, words, fix) = step1(c);
        sec = sec.child(line(
            k,
            format!(
                "Step 1 of the setup chain: {}. {words}",
                if ok { "pass" } else { "fail" }
            ),
        ));
        if let Some(f) = fix {
            sec = sec.child(note(k, f));
        }
    }
    let gateway = match (&s.cf_gateway, &s.cf_gateway_reason) {
        (_, Some(why)) => format!("set up, but not in use: {why} Calls go straight to OpenRouter."),
        (Some(true), None) => "configured".to_owned(),
        (Some(false), None) => "not configured".to_owned(),
        (None, None) => "not checked (needs a connected core)".to_owned(),
    };
    let access = match s.cf_access {
        Some(AccessState::Protected) => "on, the core's address asks you to sign in",
        Some(AccessState::NotProtected) => {
            "off, the core's address answers anyone with the access token"
        }
        Some(AccessState::Unknown) | None => "not checked",
    };
    let step = |k: &Kit, st: DashboardStep, state: String| {
        row()
            .gap(8.0)
            .center_y()
            .w_full()
            .child(
                Button::new(format!("Open {}", st.title))
                    .variant(ButtonVariant::Neutral)
                    .on_press(move |s: &mut App| s.open_dashboard(st))
                    .build(k),
            )
            .child(line(k, state))
    };
    sec.child(note(k, "These two steps are one click each in the Cloudflare dashboard; the sign-in cannot grant them."))
        .child(step(k, AI_GATEWAY_STEP, format!("AI Gateway: {gateway}")))
        .child(note(k, AI_GATEWAY_STEP.how))
        .child(step(k, ACCESS_STEP, format!("Access: {access}")))
        .child(note(k, ACCESS_STEP.how))
}
