//! Settings as a full page: a sticky section list on the left, one scrolling column per section on the right. Every number
//! here is live from the core or from Wrangler; a section that is still loading shows placeholders, an empty one says what
//! it will show and why it is empty, a failed one says why and offers Retry. Anything not built says so.

use rune::{El, ScrollSpec, col, el, row, text};
use rune_kit::{Badge, BadgeVariant, Button, ButtonVariant, Field, Kit, TextInput};
use shell_core::autoconnect::CoreStep;
use shell_core::defaults;
use shell_core::harness::Harness;
use shell_core::openrouter::{Generation, dollars};
use shell_core::settings_data::{
    self as data, Load, ModelSort, SpendReport, binding_rows, gateway_info, view_models,
};
use shell_core::signin::{ACCESS_STEP, AI_GATEWAY_STEP, AccessState};

use super::{accent_button, main_view};
use crate::app::App;
use crate::main_app::{Main, Section, Tab};

pub const NAV_W: f32 = 220.0;
pub const CONTENT_MAX: f32 = 860.0;

// ---------- the pieces every section uses ----------

fn card(k: &Kit, key: &str, title: &str) -> El {
    col()
        .key(key.to_owned())
        .w_full()
        .gap(8.0)
        .padding(16.0)
        .radius(12.0)
        .border(1.0, k.c.border_base)
        .child(
            text(title.to_owned())
                .font(k.ty.ui_medium)
                .color(k.c.text_strong),
        )
}

fn line(k: &Kit, t: impl Into<String>) -> El {
    text(t.into())
        .font(k.ty.ui)
        .color(k.c.text_base)
        .w_full()
        .wrap()
}

fn note(k: &Kit, t: impl Into<String>) -> El {
    text(t.into())
        .font(k.ty.caption())
        .color(k.c.text_muted)
        .w_full()
        .wrap()
}

fn pair(k: &Kit, label: &str, value: impl Into<String>) -> El {
    row()
        .w_full()
        .gap(8.0)
        .child(
            col().w(148.0).child(
                text(label.to_owned())
                    .font(k.ty.caption())
                    .color(k.c.text_muted),
            ),
        )
        .child(
            text(value.into())
                .font(k.ty.ui)
                .color(k.c.text_base)
                .grow(1.0),
        )
}

fn status(k: &Kit, ok: bool, yes: &str, no: &str) -> El {
    Badge::new(if ok { yes } else { no })
        .variant(if ok {
            BadgeVariant::Accent
        } else {
            BadgeVariant::Neutral
        })
        .build(k)
}

/// A grey placeholder bar while something loads.
fn skeleton(k: &Kit, w: f32) -> El {
    el().h(14.0).w(w).radius(7.0).fill(k.c.layer_02)
}

fn skeleton_rows(k: &Kit, n: usize) -> El {
    let mut c = col().key("skeleton").w_full().gap(8.0);
    for i in 0..n {
        c = c.child(skeleton(k, 260.0 - 40.0 * (i % 3) as f32));
    }
    c
}

/// An error with the way out.
fn failure(k: &Kit, key: &str, why: &str, retry: impl Fn(&mut App) + 'static) -> El {
    col()
        .key(key.to_owned())
        .w_full()
        .gap(8.0)
        .child(
            text(why.to_owned())
                .font(k.ty.ui)
                .color(k.c.text_strong)
                .w_full(),
        )
        .child(
            row().child(
                Button::new("Retry")
                    .variant(ButtonVariant::Neutral)
                    .on_press(retry)
                    .build(k),
            ),
        )
}

/// What an empty list will show, why it is empty and what to do.
fn empty(k: &Kit, key: &str, will_show: &str, why: &str, todo: &str) -> El {
    col()
        .key(key.to_owned())
        .w_full()
        .gap(4.0)
        .child(line(k, will_show))
        .child(note(k, format!("Why it is empty: {why}")))
        .child(note(k, format!("What to do: {todo}")))
}

fn link(k: &Kit, key: &str, label: &str, url: &'static str) -> El {
    Button::new(label.to_owned())
        .variant(ButtonVariant::Ghost)
        .on_press(move |s: &mut App| s.open_link(url))
        .build(k)
        .key(key.to_owned())
}

fn heading(k: &Kit, t: &str) -> El {
    text(t.to_owned())
        .font(rune::TextStyle {
            size: 20.0,
            line_height: 26.0,
            ..k.ty.body_medium
        })
        .color(k.c.text_strong)
}

// ---------- the page ----------

pub fn page(s: &App, k: &Kit, m: &Main) -> El {
    let nav = {
        let mut n = col()
            .key("settings-nav")
            .w(NAV_W)
            .h_full()
            .padding(16.0)
            .gap(4.0)
            .child(
                row()
                    .key("back-to-chat")
                    .role("button")
                    .label("Back to chat")
                    .focusable(true)
                    .padding(8.0)
                    .radius(8.0)
                    .child(text("Back to chat").font(k.ty.ui).color(k.c.text_muted))
                    .on_press(|s: &mut App| s.show_tab(Tab::Chat)),
            )
            .child(el().h(8.0));
        for sec in Section::ALL {
            let on = m.section == sec;
            let mut r = row()
                .key(format!("settings-nav-{}", sec.id()))
                .role("button")
                .label(sec.label())
                .focusable(true)
                .w_full()
                .padding(8.0)
                .child(
                    text(sec.label())
                        .font(k.ty.ui)
                        .color(crate::controls::ink(k, on)),
                );
            r = crate::controls::style(k, r, on, true, false);
            n = n.child(r.on_press(move |s: &mut App| s.open_section(sec)));
        }
        n
    };
    let body = match m.section {
        Section::OpenRouter => scroll(k, m, openrouter(s, k)),
        Section::Cloudflare => scroll(k, m, cloudflare(s, k, m)),
        Section::Agent => scroll(k, m, agent(s, k)),
        Section::Models => scroll(k, m, models_and_spend(s, k, m)),
        Section::Memory => scroll(k, m, memory(s, k)),
        Section::Diagnostics => col()
            .key("section-diagnostics")
            .w_full()
            .grow(1.0)
            .max_w(CONTENT_MAX)
            .padding(24.0)
            .gap(16.0)
            .child(heading(k, "Diagnostics"))
            .child(main_view::diagnostics(s, k)),
        Section::About => scroll(k, m, about(s, k)),
    };
    row()
        .key("settings-page")
        .w_full()
        .grow(1.0)
        .child(nav)
        .child(el().w(1.0).h_full().fill(k.c.border_base))
        .child(col().grow(1.0).h_full().items_center().child(body))
}

fn scroll(k: &Kit, m: &Main, inner: El) -> El {
    col()
        .key(format!("section-{}", m.section.id()))
        .w_full()
        .max_w(CONTENT_MAX)
        .grow(1.0)
        .scroll(ScrollSpec {
            stick_bottom: false,
        })
        .child(
            col()
                .w_full()
                .padding(24.0)
                .gap(16.0)
                .child(heading(k, m.section.label()))
                .child(inner),
        )
}

// ---------- 1. OpenRouter ----------

fn openrouter(s: &App, k: &Kit) -> El {
    let key_set = s.onboarding.secrets_set().model_key;
    let mut sec = col().w_full().gap(16.0);

    // Key
    let mut key_card = card(k, "or-key", "API key").child(
        row()
            .gap(8.0)
            .center_y()
            .child(accent_button(
                k,
                "or-connect",
                if s.or_busy {
                    "Working…"
                } else if key_set {
                    "Replace key"
                } else {
                    "Connect OpenRouter"
                },
                !s.or_busy,
                |s: &mut App| s.connect_openrouter(),
            ))
            .child(
                Button::new("Refresh")
                    .variant(ButtonVariant::Neutral)
                    .on_press(|s: &mut App| s.refresh_openrouter())
                    .build(k),
            )
            .child(
                Button::new("Remove key")
                    .variant(ButtonVariant::Neutral)
                    .disabled(!key_set)
                    .on_press(|s: &mut App| s.forget_openrouter_key())
                    .build(k),
            )
            .child(status(k, key_set, "Key set", "Key not set")),
    );
    if let Some(l) = &s.or_line {
        key_card = key_card.child(line(k, l.clone()));
    }
    match (&s.or_account, s.or_busy) {
        (None, true) => key_card = key_card.child(skeleton_rows(k, 4)),
        (None, false) => {
            key_card = key_card.child(empty(k, "or-key-empty", "The key's label, limit, remaining budget and usage for today, this week and this month.", "no OpenRouter data has been loaded yet", "click Connect OpenRouter, or Refresh if a key is already saved."));
        }
        (Some(a), _) => {
            key_card = key_card.child(note(k, a.source.words()));
            if let Some(key) = &a.key {
                key_card = key_card
                    .child(pair(k, "Label", key.label.clone()))
                    .child(pair(
                        k,
                        "Plan",
                        if key.is_free_tier {
                            "free tier"
                        } else {
                            "paid"
                        },
                    ));
                key_card = key_card.child(pair(
                    k,
                    "Limit",
                    key.limit.map_or("no limit".to_owned(), dollars),
                ));
                key_card = key_card.child(pair(
                    k,
                    "Limit resets",
                    key.limit_reset
                        .clone()
                        .unwrap_or_else(|| "never (a lifetime limit)".to_owned()),
                ));
                key_card = key_card.child(pair(
                    k,
                    "Remaining",
                    key.limit_remaining
                        .map_or("not reported".to_owned(), dollars),
                ));
                let fmt = |v: Option<f64>| v.map_or("not reported".to_owned(), dollars);
                key_card = key_card
                    .child(pair(k, "Used today", fmt(key.usage_daily)))
                    .child(pair(k, "Used this week", fmt(key.usage_weekly)))
                    .child(pair(k, "Used this month", fmt(key.usage_monthly)));
                key_card = key_card.child(pair(k, "Used in all", dollars(key.usage)));
                if let Some(frac) = period_fraction(key) {
                    key_card = key_card.child(period_bar(k, frac));
                }
            }
            for p in &a.problems {
                key_card = key_card.child(failure(k, "or-problem", p, |s: &mut App| {
                    s.refresh_openrouter()
                }));
            }
        }
    }
    sec = sec.child(key_card);

    // Credits
    let mut credits = card(k, "or-credits", "Credits");
    match &s.or_account {
        Some(a) => {
            if let Some(c) = &a.credits {
                credits = credits
                    .child(pair(k, "Bought", dollars(c.total)))
                    .child(pair(k, "Used", dollars(c.used)))
                    .child(pair(k, "Remaining", dollars(c.remaining())));
            } else if a.credits_need_management_key {
                credits = credits.child(line(k, "OpenRouter only shows account credits to management keys. This key's own usage and limit are above."));
            } else {
                credits = credits.child(note(k, "Credits could not be read."));
            }
        }
        None => {
            credits = credits.child(if s.or_busy {
                skeleton_rows(k, 2)
            } else {
                note(k, "Loads with the key.")
            })
        }
    }
    sec = sec.child(credits);

    // Models
    sec = sec.child(models_table(s, k));
    // Recent generations
    sec = sec.child(generations_table(s, k));
    // Links
    sec.child(
        card(k, "or-links", "OpenRouter on the web").child(
            row()
                .gap(8.0)
                .child(link(
                    k,
                    "or-link-activity",
                    "Activity",
                    "https://openrouter.ai/activity",
                ))
                .child(link(
                    k,
                    "or-link-keys",
                    "Keys",
                    "https://openrouter.ai/settings/keys",
                ))
                .child(link(
                    k,
                    "or-link-credits",
                    "Credits",
                    "https://openrouter.ai/settings/credits",
                ))
                .child(link(
                    k,
                    "or-link-models",
                    "Models",
                    "https://openrouter.ai/models",
                )),
        ),
    )
}

/// How much of the limit the current period has used, when OpenRouter reports both.
pub fn period_fraction(key: &shell_core::openrouter::KeyInfo) -> Option<f32> {
    let limit = key.limit.filter(|l| *l > 0.0)?;
    let left = key.limit_remaining?;
    Some(((limit - left) / limit).clamp(0.0, 1.0) as f32)
}

fn period_bar(k: &Kit, frac: f32) -> El {
    col()
        .key("or-period")
        .w_full()
        .gap(4.0)
        .child(
            row()
                .key("or-period-track")
                .w_full()
                .h(8.0)
                .radius(999.0)
                .fill(k.c.layer_02)
                .clip_soft(0.0)
                .child(
                    crate::theme::accent_fill(el().key("or-period-fill").h_full().radius(999.0))
                        .w_frac(frac),
                ),
        )
        .child(note(
            k,
            format!("{:.0}% of this period's limit used", frac * 100.0),
        ))
}

fn models_table(s: &App, k: &Kit) -> El {
    let mut c = card(k, "or-models", "Models and prices");
    let Some(a) = &s.or_account else {
        return c.child(if s.or_busy {
            skeleton_rows(k, 5)
        } else {
            note(k, "Loads with the key.")
        });
    };
    if a.models.is_empty() {
        return c.child(empty(k, "or-models-empty", "Every model OpenRouter offers, with price per million tokens, context, and tool and reasoning support.", "the list has not been fetched, or OpenRouter returned none", "click Refresh above."));
    }
    let search = Field::new()
        .label("Search models")
        .control(
            TextInput::new(&s.models_query)
                .placeholder("name or vendor")
                .on_edit(|s: &mut App| &mut s.models_query)
                .width(None)
                .build(k),
        )
        .build(k);
    c = c.child(search);
    let (sort, asc) = s.models_sort;
    let head = |label: &str, key: &'static str, col_sort: ModelSort, w: f32| {
        let arrow = if sort == col_sort {
            if asc { " ↑" } else { " ↓" }
        } else {
            ""
        };
        crate::controls::choice(
            k,
            key,
            &format!("{label}{arrow}"),
            sort == col_sort,
            true,
            move |s: &mut App| s.sort_models(col_sort),
        )
        .w(w)
    };
    c = c.child(
        row()
            .key("models-head")
            .w_full()
            .gap(8.0)
            .center_y()
            .child(head("Model", "sort-name", ModelSort::Name, 184.0))
            .child(col().w(64.0).child(note(k, "Vendor")))
            .child(head("Context", "sort-context", ModelSort::Context, 72.0))
            .child(head("In / 1M", "sort-prompt", ModelSort::Prompt, 80.0))
            .child(head(
                "Out / 1M",
                "sort-completion",
                ModelSort::Completion,
                84.0,
            ))
            .child(col().w(40.0).child(note(k, "Tools")))
            .child(col().w(52.0).child(note(k, "Reason"))),
    );
    let shown = view_models(&a.models, &s.models_query.text, sort, asc);
    if shown.is_empty() {
        return c.child(empty(
            k,
            "or-models-nomatch",
            "Models matching your search.",
            "nothing matches that text",
            "clear the search box.",
        ));
    }
    let total = shown.len();
    for (i, m) in shown.iter().take(40).enumerate() {
        let id = m.id.clone();
        let (a1, a2) = (id.clone(), id.clone());
        c = c.child(
            row()
                .key(format!("model-{i}"))
                .w_full()
                .gap(8.0)
                .center_y()
                .child(
                    col()
                        .w(220.0)
                        .child(text(m.id.clone()).font(k.ty.ui).color(k.c.text_base)),
                )
                .child(col().w(64.0).child(note(k, m.provider().to_owned())))
                .child(
                    col()
                        .w(84.0)
                        .child(note(k, format!("{}k", m.context / 1000))),
                )
                .child(col().w(80.0).child(note(k, dollars(m.prompt_per_m))))
                .child(col().w(84.0).child(note(k, dollars(m.completion_per_m))))
                .child(
                    col()
                        .w(40.0)
                        .child(note(k, if m.tools { "yes" } else { "no" })),
                )
                .child(
                    col()
                        .w(60.0)
                        .child(note(k, if m.reasoning { "yes" } else { "no" })),
                )
                .child(
                    Button::new("Default")
                        .variant(ButtonVariant::Ghost)
                        .on_press(move |s: &mut App| s.use_model("default_model", &a1))
                        .build(k),
                )
                .child(
                    Button::new("Worker")
                        .variant(ButtonVariant::Ghost)
                        .on_press(move |s: &mut App| s.use_model("worker_model", &a2))
                        .build(k),
                ),
        );
    }
    if total > 40 {
        c = c.child(note(
            k,
            format!("Showing 40 of {total}. Search to narrow the list."),
        ));
    }
    c
}

fn generations_table(s: &App, k: &Kit) -> El {
    let mut c = card(k, "or-generations", "Recent generations");
    let Some(a) = &s.or_account else {
        return c.child(if s.or_busy {
            skeleton_rows(k, 3)
        } else {
            note(k, "Loads with the key.")
        });
    };
    if a.generations.is_empty() {
        return c.child(empty(k, "or-gen-empty", "Each recent model call: when, which model, tokens in and out, cost, latency, and the task.", "no task has made a model call through this core yet, or this computer is talking to OpenRouter directly (generation ids come from the core)", "run a task, then Refresh."));
    }
    c = c.child(
        row()
            .w_full()
            .gap(8.0)
            .child(col().w(132.0).child(note(k, "When")))
            .child(col().grow(1.0).child(note(k, "Model")))
            .child(col().w(84.0).child(note(k, "In / out")))
            .child(col().w(76.0).child(note(k, "Cost")))
            .child(col().w(64.0).child(note(k, "Latency")))
            .child(col().w(64.0).child(note(k, "Task"))),
    );
    for (i, g) in a.generations.iter().enumerate() {
        c = c.child(generation_row(k, i, g));
    }
    c
}

fn generation_row(k: &Kit, i: usize, g: &Generation) -> El {
    let when = g.ts.as_deref().map_or("—".to_owned(), data::short_time);
    let task = g.task_id.clone();
    let open = match task {
        Some(t) => Button::new("Open")
            .variant(ButtonVariant::Ghost)
            .on_press(move |s: &mut App| s.open_task(&t))
            .build(k),
        None => col().w(64.0).child(note(k, "—")),
    };
    row()
        .key(format!("generation-{i}"))
        .w_full()
        .gap(8.0)
        .center_y()
        .child(col().w(132.0).child(note(k, when)))
        .child(
            text(g.model.clone())
                .font(k.ty.ui)
                .color(k.c.text_base)
                .grow(1.0),
        )
        .child(col().w(84.0).child(note(
            k,
            format!("{} / {}", g.prompt_tokens, g.completion_tokens),
        )))
        .child(col().w(76.0).child(note(k, dollars(g.cost))))
        .child(
            col().w(64.0).child(note(
                k,
                g.latency_ms
                    .map_or("—".to_owned(), |l| format!("{:.1} s", l / 1000.0)),
            )),
        )
        .child(open)
}

// ---------- 2. Cloudflare ----------

/// Which browser sign-in pages open in: the page uses whatever account that browser is signed in to.
fn browser_card(s: &App, k: &Kit) -> El {
    let current = s.signin_browser.text.trim().to_owned();
    let mut c = card(k, "cf-browser", "Open sign-in pages with")
        .child(note(
            k,
            "Cloudflare and OpenRouter show their consent page in a browser, and it uses the account that browser is signed in to. Choose the browser or profile that holds the right one. Empty uses your system browser.",
        ))
        .child(
            TextInput::new(&s.signin_browser)
                .placeholder("system browser, or a command such as chromium --profile-directory=\"Profile 2\"")
                .on_edit(|s: &mut App| &mut s.signin_browser)
                .width(None)
                .build(k),
        );
    let mut picks = row().key("browser-picks").w_full().gap(8.0);
    picks = picks.child(crate::controls::choice(
        k,
        "browser-system",
        "System browser",
        current.is_empty(),
        true,
        |s: &mut App| s.pick_signin_browser(""),
    ));
    for (i, d) in s.browsers.iter().take(8).enumerate() {
        let command = d.command.clone();
        picks = picks.child(crate::controls::choice(
            k,
            format!("browser-{i}"),
            &d.label,
            current == d.command,
            true,
            move |s: &mut App| s.pick_signin_browser(&command),
        ));
    }
    c = c.child(picks);
    c.child(
        row().child(
            Button::new("Save")
                .variant(ButtonVariant::Neutral)
                .on_press(|s: &mut App| s.save_signin_browser())
                .build(k)
                .key("browser-save"),
        ),
    )
    .child(note(
        k,
        if s.browsers.is_empty() {
            "No other browser was found on this computer. Type the command if you use one."
        } else {
            "Found on this computer. Profiles are listed by folder name."
        },
    ))
}

fn cloudflare(s: &App, k: &Kit, m: &Main) -> El {
    let mut sec = col().w_full().gap(16.0);

    // Account
    let signed = s.cf_signed_in();
    let mut account = card(k, "cf-account", "Account").child(
        row()
            .gap(8.0)
            .center_y()
            .child(if signed {
                Button::new("Switch account")
                    .variant(ButtonVariant::Neutral)
                    .disabled(s.signing_in)
                    .on_press(|s: &mut App| s.switch_cloudflare())
                    .build(k)
            } else {
                accent_button(
                    k,
                    "cf-sign-in",
                    if s.signing_in {
                        "Working…"
                    } else {
                        "Sign in with Cloudflare"
                    },
                    !s.signing_in,
                    |s: &mut App| s.sign_in_cloudflare(),
                )
            })
            .child(
                Button::new("Sign out")
                    .variant(ButtonVariant::Neutral)
                    .disabled(s.signing_in || !signed)
                    .on_press(|s: &mut App| s.sign_out_cloudflare())
                    .build(k),
            )
            .child(
                Button::new("Check")
                    .variant(ButtonVariant::Neutral)
                    .disabled(s.signing_in)
                    .on_press(|s: &mut App| s.check_cloudflare())
                    .build(k),
            )
            .child(status(k, signed, "Signed in", "Not signed in")),
    );
    if let Some(l) = &s.cloudflare_line {
        account = account.child(line(k, l.clone()));
    }
    if s.signing_in {
        account = account.child(skeleton_rows(k, 1));
    }
    if s.cf_open_url.is_some() {
        account = account.child(
            row().child(
                Button::new("Open sign-in page")
                    .variant(ButtonVariant::Submit)
                    .on_press(|s: &mut App| s.open_sign_in_page())
                    .build(k),
            ),
        );
    }
    match &s.cf_state {
        Some(c) if c.signed_in => {
            account = account
                .child(pair(k, "Account", c.accounts.join(", ")))
                .child(pair(
                    k,
                    "Email",
                    c.email
                        .clone()
                        .unwrap_or_else(|| "not shown by Wrangler".to_owned()),
                ))
                .child(pair(
                    k,
                    "Access granted",
                    if c.scopes.is_empty() {
                        "none listed".to_owned()
                    } else {
                        c.scopes.join(", ")
                    },
                ));
            let (ok, words, fix) = shell_core::signin::step1(c);
            account = account.child(pair(
                k,
                "Missing",
                if c.missing.is_empty() {
                    "nothing".to_owned()
                } else {
                    c.missing.join(", ")
                },
            ));
            if !ok {
                account = account.child(line(k, words));
                if let Some(f) = fix {
                    account = account.child(note(k, f));
                }
            }
        }
        _ if !s.signing_in => {
            account = account.child(empty(
                k,
                "cf-account-empty",
                "Who is signed in, the access Wrangler was granted, and anything missing.",
                if signed {
                    "Wrangler answered, but this page has not read the details yet"
                } else {
                    "nobody is signed in to Wrangler"
                },
                "click Sign in with Cloudflare, or Check.",
            ));
        }
        _ => {}
    }
    // Right after any sign-in the account is stated plainly, with the way out if it is the wrong one.
    if let Some(c) = s.cf_state.as_ref().filter(|c| c.signed_in) {
        account = account.child(
            col()
                .key("cf-who")
                .w_full()
                .gap(4.0)
                .child(
                    text(c.account_line())
                        .font(k.ty.ui_medium)
                        .color(k.c.text_strong)
                        .w_full()
                        .wrap(),
                )
                .child(
                    row().child(
                        Button::new("Not you? Switch account")
                            .variant(ButtonVariant::Neutral)
                            .disabled(s.signing_in)
                            .on_press(|s: &mut App| s.switch_cloudflare())
                            .build(k)
                            .key("cf-not-you"),
                    ),
                ),
        );
    }
    account = account.child(note(k, shell_core::signin::CONSENT_TIP));
    sec = sec.child(account);
    sec = sec.child(browser_card(s, k));

    // Core
    let mut core = card(k, "cf-core", "Core");
    let url = if s.onboarding.local_only() {
        "this computer".to_owned()
    } else {
        s.onboarding.cloud_url().to_owned()
    };
    core = core
        .child(pair(k, "Worker", "open-computer-core"))
        .child(pair(
            k,
            "Address",
            if url.is_empty() {
                "not connected".to_owned()
            } else {
                url
            },
        ));
    match &s.core_info {
        Load::Ready(i) => {
            core = core.child(pair(
                k,
                "Deployed version",
                if i.version.is_empty() {
                    "not reported".to_owned()
                } else {
                    i.version.clone()
                },
            ));
            core = core.child(pair(
                k,
                "Answered from",
                i.served_from.clone().map_or(
                    "not reported (the answer did not come through Cloudflare)".to_owned(),
                    |c| format!("Cloudflare data centre {c}"),
                ),
            ));
        }
        Load::Failed(e) => {
            core = core.child(failure(k, "cf-core-error", e, |s: &mut App| {
                s.reload_core_info()
            }))
        }
        _ => core = core.child(skeleton_rows(k, 2)),
    }
    match &s.found_core {
        Some(f) => {
            core = core
                .child(pair(
                    k,
                    "Last deployed",
                    f.modified_on
                        .clone()
                        .map_or("not reported".to_owned(), |t| data::short_time(&t)),
                ))
                .child(pair(k, "Account", f.account.clone()));
        }
        None => {
            core = core.child(note(k, "The last deploy time comes from your Cloudflare account. Sign in, then look it up."));
            core = core.child(
                row().child(
                    Button::new(if s.finding_core {
                        "Looking…"
                    } else {
                        "Look up on my account"
                    })
                    .variant(ButtonVariant::Neutral)
                    .disabled(s.finding_core || !signed)
                    .on_press(|s: &mut App| s.start_discovery())
                    .build(k),
                ),
            );
        }
    }
    sec = sec.child(core);

    // Bindings
    let mut bindings = card(k, "cf-bindings", "Bindings");
    match &s.bindings {
        Load::Ready(b) => {
            for r in binding_rows(b) {
                bindings = bindings.child(
                    row()
                        .w_full()
                        .gap(8.0)
                        .center_y()
                        .child(text(r.name).font(k.ty.ui).color(k.c.text_base).grow(1.0))
                        .child(status(k, r.ok, r.note, r.note)),
                );
            }
        }
        Load::Failed(e) => {
            bindings = bindings.child(failure(k, "cf-bindings-error", e, |s: &mut App| {
                s.reload_bindings()
            }))
        }
        _ => bindings = bindings.child(skeleton_rows(k, 4)),
    }
    sec = sec.child(bindings);

    // AI Gateway
    let mut gw = card(k, "cf-gateway", "AI Gateway");
    match &s.bindings {
        Load::Ready(b) => {
            let g = gateway_info(b);
            gw = gw.child(
                row()
                    .gap(8.0)
                    .center_y()
                    .child(status(
                        k,
                        g.in_use,
                        "In use",
                        if g.configured {
                            "Set up, not in use"
                        } else {
                            "Not configured"
                        },
                    ))
                    .child(note(
                        k,
                        format!(
                            "Model calls go: {}",
                            if g.in_use {
                                g.via.clone()
                            } else {
                                "direct to OpenRouter".to_owned()
                            }
                        ),
                    )),
            );
            if let Some(why) = g.reason {
                gw = gw.child(line(
                    k,
                    format!("Why not: {why} The core retries directly, so tasks still work."),
                ));
            } else if !g.configured {
                gw = gw.child(note(k, "Create a gateway named open-computer in the dashboard, then run setup with OC_AI_GATEWAY_ID=open-computer."));
            }
        }
        Load::Failed(_) => {
            gw = gw.child(note(
                k,
                "The core did not answer, so the gateway state is unknown.",
            ))
        }
        _ => gw = gw.child(skeleton_rows(k, 2)),
    }
    gw = gw.child(
        row()
            .gap(8.0)
            .child(
                Button::new("Open AI Gateway in the dashboard")
                    .variant(ButtonVariant::Neutral)
                    .on_press(|s: &mut App| s.open_dashboard(AI_GATEWAY_STEP))
                    .build(k),
            )
            .child(link(
                k,
                "cf-gw-logs",
                "About gateway logs",
                "https://developers.cloudflare.com/ai-gateway/observability/logging/",
            ))
            .child(link(
                k,
                "cf-gw-auth",
                "About authentication",
                "https://developers.cloudflare.com/ai-gateway/configuration/authentication/",
            )),
    );
    sec = sec.child(gw);

    // Access
    let access = match s.cf_access {
        Some(AccessState::Protected) => "On: the core's address asks you to sign in.".to_owned(),
        Some(AccessState::NotProtected) => "Off: anyone with the access token can reach the core. This is deliberate for now: Access would lock the desktop, `oc` and the web wizard out, because they call the core with a bearer token. It needs service-token support first.".to_owned(),
        Some(AccessState::Unknown) | None => "Not checked yet. Click Check in Account.".to_owned(),
    };
    sec = sec.child(
        card(k, "cf-access", "Cloudflare Access")
            .child(line(k, access))
            .child(
                row().child(
                    Button::new("Open Access in the dashboard")
                        .variant(ButtonVariant::Neutral)
                        .on_press(|s: &mut App| s.open_dashboard(ACCESS_STEP))
                        .build(k),
                ),
            ),
    );

    // Redeploy
    let step = s.core_step();
    let busy = matches!(step, CoreStep::SettingUp);
    let mut redeploy = card(k, "cf-redeploy", "Redeploy").child(note(k, "Runs the same setup as `oc setup`: it checks Docker first, repairs missing resources and deploys the matching version. It is safe to repeat."));
    redeploy = redeploy.child(row().child(accent_button(
        k,
        "cf-redeploy-button",
        if busy { "Working…" } else { "Re-run setup" },
        !busy,
        |s: &mut App| s.start_setup(),
    )));
    if let Some(l) = &s.core_line {
        redeploy = redeploy.child(line(k, l.clone()));
    }
    let _ = m;
    sec = sec.child(redeploy);

    // Docs
    sec.child(
        card(k, "cf-docs", "Documentation")
            .child(
                row()
                    .gap(8.0)
                    .child(link(k, "doc-workers", "Workers", "https://developers.cloudflare.com/workers/"))
                    .child(link(k, "doc-d1", "D1", "https://developers.cloudflare.com/d1/"))
                    .child(link(k, "doc-r2", "R2", "https://developers.cloudflare.com/r2/"))
                    .child(link(k, "doc-do", "Durable Objects", "https://developers.cloudflare.com/durable-objects/")),
            )
            .child(
                row()
                    .gap(8.0)
                    .child(link(k, "doc-aig", "AI Gateway", "https://developers.cloudflare.com/ai-gateway/"))
                    .child(link(k, "doc-access", "Access", "https://developers.cloudflare.com/cloudflare-one/"))
                    .child(link(k, "doc-containers", "Containers", "https://developers.cloudflare.com/containers/"))
                    .child(link(k, "doc-runbook", "Our setup runbook", "https://github.com/duketopceo/open-computer/blob/main/docs/runbooks/cloud-setup.md")),
            ),
    )
}

// ---------- 3. Agent harness ----------

fn agent(s: &App, k: &Kit) -> El {
    let _ = Harness::ALL;
    col()
        .w_full()
        .gap(16.0)
        .child(note(k, "The default for new tasks is the one marked In use. You can also change it from the chip under the composer."))
        .child(super::integrations::harness(s, k))
        .child(harness_key_card(s, k))
        .child(row().child(Button::new("Open Codex").variant(ButtonVariant::Neutral).on_press(|s: &mut App| s.launch_codex()).build(k)))
}

/// Hermes and OpenCode read `OPENROUTER_API_KEY`. Handing it over is the person's choice, and only possible when a real key is on
/// this computer; a key the core holds never leaves the core.
fn harness_key_card(s: &App, k: &Kit) -> El {
    let mut c = card(k, "harness-key", "Model access for Hermes and OpenCode");
    if s.has_shareable_key() {
        c = c
            .child(note(k, "Both agents read the OPENROUTER_API_KEY environment variable. When this is on, Open Computer sets it for their process only. It is not written to any file or log."))
            .child(crate::controls::switch(
                k,
                "harness-key-toggle",
                "Hand my OpenRouter key to them",
                s.harness_key,
                true,
                |s: &mut App| {
                    let on = !s.harness_key;
                    s.set_harness_key(on)
                },
            ));
    } else {
        c = c.child(note(k, "Your OpenRouter key is held by your core, so it cannot be handed to a local agent. Give the agent its own model instead: run `hermes setup` or `opencode auth login` in a terminal. If a task fails for that reason, the failure message says so."));
    }
    c
}

// ---------- 4. Models and spend ----------

fn models_and_spend(s: &App, k: &Kit, m: &Main) -> El {
    let st = m.settings.as_ref();
    let num = |key: &str| st.and_then(|v| v.get(key)).and_then(|v| v.as_f64());
    let saved = |key: &str, fallback: String| {
        st.and_then(|v| v.get(key))
            .and_then(|v| v.as_str())
            .map_or(fallback, str::to_owned)
    };
    let field = |label: &str,
                 edit: &rune::TextEdit,
                 hint: &str,
                 on: fn(&mut App) -> &mut rune::TextEdit| {
        Field::new()
            .label(label.to_owned())
            .control(
                TextInput::new(edit)
                    .placeholder(hint.to_owned())
                    .on_edit(on)
                    .width(None)
                    .build(k),
            )
            .build(k)
    };

    let mut models = card(k, "ms-models", "Models")
        .child(pair(
            k,
            "Default model",
            saved("default_model", defaults::default_model()),
        ))
        .child(pair(
            k,
            "Worker model",
            saved("worker_model", defaults::worker_model()),
        ))
        .child(field(
            "Default model",
            &s.set_default_model,
            "vendor/model (leave empty to keep)",
            |s| &mut s.set_default_model,
        ))
        .child(field(
            "Worker model",
            &s.set_worker_model,
            "vendor/model (leave empty to keep)",
            |s| &mut s.set_worker_model,
        ))
        .child(note(
            k,
            "Pick from the OpenRouter table with Default and Worker, or type a name here.",
        ))
        .child(pair(
            k,
            "Custom endpoint",
            saved("custom_endpoint_url", "not set".to_owned()),
        ))
        .child(field(
            "Custom OpenAI-compatible address",
            &s.set_custom_url,
            "https://llm.example.com/v1",
            |s| &mut s.set_custom_url,
        ))
        .child(field(
            "Custom endpoint model",
            &s.set_custom_model,
            "the model name it expects",
            |s| &mut s.set_custom_model,
        ))
        .child(field(
            "Custom endpoint key",
            &s.set_custom_key,
            "Paste its key (never shown again)",
            |s| &mut s.set_custom_key,
        ))
        .child(note(
            k,
            "Choose the custom endpoint for a task by naming the model \"custom\".",
        ));
    models = models.child(
        row().child(
            Button::new("Save")
                .variant(ButtonVariant::Submit)
                .on_press(|s: &mut App| s.save_model_settings())
                .build(k),
        ),
    );

    // Cap
    let effective = num("effective_daily_cap_usd");
    let policy_max = num("policy_max_daily_cap_usd");
    let cap_button = |label: &str, usd: Option<f64>, allowed: bool| {
        // The one in effect is filled; the others are outlined; one the policy forbids is dimmed.
        let on = match (usd, effective) {
            (Some(v), Some(e)) => (v - e).abs() < 0.005,
            (None, Some(e)) => (e - 5.0).abs() < 0.005,
            _ => false,
        };
        crate::controls::choice(
            k,
            format!(
                "cap-{}",
                label
                    .split_whitespace()
                    .next()
                    .unwrap_or("x")
                    .trim_start_matches('$')
            ),
            label,
            on,
            allowed,
            move |s: &mut App| s.set_daily_cap(usd),
        )
    };
    let within = |v: f64| policy_max.is_none_or(|p| v <= p);
    let mut cap = card(k, "ms-cap", "Daily spend cap").child(pair(
        k,
        "In effect",
        effective.map_or("loading…".to_owned(), |c| format!("${c:.2}")),
    ));
    cap = cap.child(
        row()
            .gap(8.0)
            .child(cap_button("$1", Some(1.0), within(1.0)))
            .child(cap_button("$5 (default)", None, true))
            .child(cap_button("$20", Some(20.0), within(20.0)))
            .child(cap_button("$50", Some(50.0), within(50.0))),
    );
    cap = cap.child(note(k, match policy_max {
        Some(p) => format!("Your IT policy holds the cap at ${p:.2} or less; higher choices are disabled."),
        None => "No IT policy file is in effect, so the cap can be set up to the core's own maximum.".to_owned(),
    }));

    // Spend
    let mut spend = card(k, "ms-spend", "Spend");
    match &s.spend {
        Load::Ready(r) => {
            spend = spend
                .child(pair(k, "Today", dollars(r.today)))
                .child(pair(k, "Last 7 days", dollars(r.week)))
                .child(pair(k, "This month", dollars(r.month)));
            if r.records == 0 || r.days.iter().all(|(_, c)| *c == 0.0) {
                spend = spend.child(empty(
                    k,
                    "ms-spend-empty",
                    "A bar for each of the last 14 days.",
                    "no model spend has been recorded in that time",
                    "run a task; the bars appear as the core records each model step.",
                ));
            } else {
                spend = spend.child(spend_chart(k, r));
            }
            spend = spend.child(note(
                k,
                "Read from the core's audit log, which records the cost of each model step.",
            ));
        }
        Load::Failed(e) => {
            spend = spend.child(failure(k, "ms-spend-error", e, |s: &mut App| {
                s.reload_spend()
            }))
        }
        _ => spend = spend.child(skeleton_rows(k, 3)),
    }

    col().w_full().gap(16.0).child(spend).child(cap).child(models).child(
        card(k, "ms-policy", "Policy file").child(note(k, match policy_max {
            Some(p) => format!("An IT policy file is in effect and limits the daily cap to ${p:.2}. It can also limit models and tools, and set an OpenTelemetry endpoint; traces carry names, timings, tokens and cost only."),
            None => "No IT policy file is in effect on this core. A policy file can lock the daily cap, limit models and tools, and set an OpenTelemetry endpoint.".to_owned(),
        })),
    )
}

/// Fourteen bars, one per day, in the accent; a day with nothing is a thin grey line.
fn spend_chart(k: &Kit, r: &SpendReport) -> El {
    let max = r
        .days
        .iter()
        .map(|(_, c)| *c)
        .fold(0.0_f64, f64::max)
        .max(1e-9);
    let mut bars = row()
        .key("spend-chart")
        .w_full()
        .h(112.0)
        .gap(6.0)
        .items_end();
    for (i, (_, cost)) in r.days.iter().enumerate() {
        let h = if *cost <= 0.0 {
            2.0
        } else {
            (*cost / max * 100.0).max(4.0) as f32
        };
        let bar = if *cost <= 0.0 {
            el().w_full().h(h).fill(k.c.border_base)
        } else {
            crate::theme::accent_fill(el().w_full().h(h).radius(4.0))
        };
        bars = bars.child(
            col()
                .key(format!("day-{i}"))
                .grow(1.0)
                .items_end()
                .child(bar),
        );
    }
    let first = r.days.first().map_or("", |d| d.0.as_str()).to_owned();
    let last = r.days.last().map_or("", |d| d.0.as_str()).to_owned();
    col().w_full().gap(4.0).child(bars).child(note(
        k,
        format!("{first} to {last}, busiest day {}", dollars(max)),
    ))
}

// ---------- 5. Memory and privacy ----------

fn memory(s: &App, k: &Kit) -> El {
    let mut mem = card(k, "mem-stored", "What is stored");
    match &s.memory {
        Load::Ready(st) => {
            for (name, chars) in &st.blocks {
                let label = match name.as_str() {
                    "agent_notes" => "Agent notes",
                    "user_profile" => "About you",
                    other => other,
                };
                mem = mem.child(pair(k, label, format!("{chars} characters")));
            }
            mem = mem
                .child(pair(k, "Task summaries", st.entries.to_string()))
                .child(pair(
                    k,
                    "Newest",
                    st.newest
                        .clone()
                        .map_or("none yet".to_owned(), |t| data::short_time(&t)),
                ));
            if st.entries == 0 && st.blocks.iter().all(|b| b.1 == 0) {
                mem = mem.child(empty(
                    k,
                    "mem-empty",
                    "Notes the agent keeps and a summary of each finished task.",
                    "nothing has been remembered yet",
                    "finish a task with memory on; its summary appears here.",
                ));
            }
            mem = mem.child(note(k, "Only sizes and counts are shown here, never the text itself. Memory is stored in your core and is kept until you clear it; there is no automatic expiry."));
        }
        Load::Failed(e) => {
            mem = mem.child(failure(k, "mem-error", e, |s: &mut App| s.reload_memory()))
        }
        _ => mem = mem.child(skeleton_rows(k, 3)),
    }
    let clearing = s.confirm_clear;
    let clear_row = if clearing {
        row()
            .key("mem-confirm")
            .gap(8.0)
            .center_y()
            .child(
                text("Clear everything the agent remembers? This cannot be undone.")
                    .font(k.ty.ui)
                    .color(k.c.text_strong),
            )
            .child(
                Button::new("Yes, clear memory")
                    .variant(ButtonVariant::Submit)
                    .on_press(|s: &mut App| s.clear_memory())
                    .build(k),
            )
            .child(
                Button::new("Cancel")
                    .variant(ButtonVariant::Neutral)
                    .on_press(|s: &mut App| s.cancel_clear())
                    .build(k),
            )
    } else {
        row().child(
            Button::new("Clear memory")
                .variant(ButtonVariant::Neutral)
                .disabled(!matches!(s.memory, Load::Ready(_)))
                .on_press(|s: &mut App| s.clear_memory())
                .build(k),
        )
    };
    mem = mem.child(clear_row);

    let mut audit = card(k, "mem-audit", "Audit log");
    match &s.spend {
        Load::Ready(r) => {
            let (ok, words) = match r.chain_ok {
                Some(true) => (
                    true,
                    "The audit log verifies: every entry links to the one before.",
                ),
                Some(false) => (
                    false,
                    "The audit log does NOT verify: an entry was changed or removed.",
                ),
                None => (
                    false,
                    "The core did not say whether the audit log verifies.",
                ),
            };
            audit = audit
                .child(
                    row()
                        .gap(8.0)
                        .center_y()
                        .child(status(k, ok, "Verified", "Not verified"))
                        .child(line(k, words)),
                )
                .child(pair(k, "Entries read", r.records.to_string()));
        }
        Load::Failed(e) => {
            audit = audit.child(failure(k, "mem-audit-error", e, |s: &mut App| {
                s.reload_spend()
            }))
        }
        _ => audit = audit.child(skeleton_rows(k, 2)),
    }

    col().w_full().gap(16.0).child(mem).child(audit).child(
        card(k, "mem-privacy", "Data and privacy")
            .child(line(k, "Keys are stored in your system keyring, or in memory only if it is locked."))
            .child(note(k, "Prompts and answers are not written to the audit log. Nothing is sent anywhere except to your core and OpenRouter.")),
    )
}

// ---------- 7. About ----------

fn about(s: &App, k: &Kit) -> El {
    let core = match &s.core_info {
        Load::Ready(i) if !i.version.is_empty() => i.version.clone(),
        Load::Failed(_) => "not reachable".to_owned(),
        _ => "loading…".to_owned(),
    };
    col()
        .w_full()
        .gap(16.0)
        .child(
            card(k, "about-versions", "Versions")
                .child(pair(k, "Desktop", env!("CARGO_PKG_VERSION")))
                .child(pair(k, "Core", core))
                .child(pair(
                    k,
                    "Keyring",
                    s.keyring_problem
                        .clone()
                        .unwrap_or_else(|| "no problem recorded".to_owned()),
                ))
                .child(pair(
                    k,
                    "Backend",
                    if s.onboarding.local_only() {
                        "this computer only".to_owned()
                    } else {
                        s.onboarding.cloud_url().to_owned()
                    },
                )),
        )
        .child(
            card(k, "about-appearance", "Appearance")
                .child(crate::controls::switch(
                    k,
                    "bg-motion",
                    "Background motion",
                    s.ambient,
                    true,
                    |s: &mut App| {
                        let on = !s.ambient;
                        s.set_ambient(on)
                    },
                ))
                .child(note(
                    k,
                    "A slow, faint glow drifts behind the window. Off holds it still. It also stays still when your system asks for reduced motion.",
                )),
        )
        .child(
            card(k, "about-log", "Diagnostics log")
                .child(note(
                    k,
                    format!(
                        "The app writes what it does (connections, keyring, Wrangler, agents) to {}. Keys and tokens are never written, and anything that looks like one is hidden.",
                        shell_core::applog::log_path().display()
                    ),
                ))
                .child(
                    row()
                        .gap(8.0)
                        .center_y()
                        .child(
                            Button::new("Copy diagnostics")
                                .variant(ButtonVariant::Neutral)
                                .on_press(|s: &mut App| s.copy_diagnostics())
                                .build(k)
                                .key("about-copy-diagnostics"),
                        )
                        .child(note(
                            k,
                            if s.diag_copied {
                                "Copied the last 200 lines."
                            } else {
                                "Copies the last 200 lines, redacted."
                            },
                        )),
                ),
        )
        .child(
            card(k, "about-links", "Project").child(
                row()
                    .gap(8.0)
                    .child(link(
                        k,
                        "about-repo",
                        "Source code",
                        "https://github.com/duketopceo/open-computer",
                    ))
                    .child(link(
                        k,
                        "about-license",
                        "Licence (MIT)",
                        "https://github.com/duketopceo/open-computer/blob/main/LICENSE",
                    ))
                    .child(link(
                        k,
                        "about-support",
                        "Report a problem",
                        "https://github.com/duketopceo/open-computer/issues",
                    )),
            ),
        )
}
