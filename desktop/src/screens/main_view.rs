//! The app after setup: Chat, Settings and Diagnostics, in one black and white frame.

use rune::{El, ScrollSpec, col, row, text};
use rune_kit::{Badge, BadgeVariant, Button, ButtonVariant, Field, Kit, TextInput};
use shell_core::chat::{ApprovalCard, Item, ToolCard, write_diff};
use shell_core::defaults;

use super::{caption, test_connection};
use crate::app::App;
use crate::main_app::{Main, Tab};

pub fn view(s: &App, k: &Kit, m: &Main) -> El {
    let tab_button = |label: &str, tab: Tab| {
        Button::new(label)
            .variant(if m.tab == tab {
                ButtonVariant::Submit
            } else {
                ButtonVariant::Ghost
            })
            .on_press(move |s: &mut App| s.show_tab(tab))
            .build(k)
    };
    let nav = row()
        .key("nav")
        .gap(8.0)
        .center_y()
        .child(
            text("Open Computer")
                .font(k.ty.body_medium)
                .color(k.c.text_strong),
        )
        .child(tab_button("Chat", Tab::Chat))
        .child(tab_button("Settings", Tab::Settings))
        .child(tab_button("Diagnostics", Tab::Diagnostics));
    let body = match m.tab {
        Tab::Chat => chat(s, k, m),
        Tab::Settings => settings(s, k, m),
        Tab::Diagnostics => diagnostics(s, k),
    };
    let mut page = col()
        .key("main")
        .w_full()
        .h_full()
        .gap(10.0)
        .child(nav)
        .child(body);
    if let Some(n) = &s.notice {
        page = page.child(
            text(n.clone())
                .key("notice")
                .font(k.ty.ui)
                .color(k.c.text_strong),
        );
    }
    page
}

/// Three things worth asking first, one per capability: run something, remember something, look something up.
const EXAMPLES: [(&str, &str); 3] = [
    (
        "Run a command in the sandbox",
        "Run the command `uname -a` in your sandbox and tell me what it printed.",
    ),
    (
        "Write and check a file",
        "Write a file named notes.txt that says hello, then read it back to me.",
    ),
    (
        "Look something up",
        "Use web search to find the current stable version of Node.js and answer in one sentence.",
    ),
];

fn card(k: &Kit, key: String) -> El {
    col()
        .key(key)
        .gap(4.0)
        .w_full()
        .padding(10.0)
        .radius(8.0)
        .border(1.0, k.c.border_strong)
}

fn chat(s: &App, k: &Kit, m: &Main) -> El {
    let mut list = col().key("chat-list").gap(8.0).w_full();
    if m.chat.items.is_empty() {
        let hint = if m.endpoint.is_some() {
            "Ask for something. The agent thinks, uses its sandbox and tools, and asks before it does anything risky."
        } else if m.starting_local {
            "Starting the local core."
        } else {
            "Not connected to a core yet. Open Settings."
        };
        list = list.child(caption(k, hint));
        for (i, (label, task)) in EXAMPLES.iter().enumerate() {
            let task: &'static str = task;
            list = list.child(
                col()
                    .key(format!("example-{i}"))
                    .gap(2.0)
                    .w_full()
                    .padding(10.0)
                    .radius(8.0)
                    .border(1.0, k.c.border_strong)
                    .child(text(*label).font(k.ty.ui_medium).color(k.c.text_strong))
                    .child(text(task).font(k.ty.caption()).color(k.c.text_muted))
                    .child(
                        Button::new("Use this")
                            .variant(ButtonVariant::Ghost)
                            .on_press(move |s: &mut App| s.use_example(task))
                            .build(k),
                    ),
            );
        }
    }
    for (i, item) in m.chat.items.iter().enumerate() {
        list = list.child(item_view(k, s, m, i, item));
    }
    if m.chat.is_busy() && !m.chat.has_reasoning() {
        list = list.child(
            text(format!("Working \u{b7} {}s", m.elapsed_secs()))
                .key("working")
                .font(k.ty.ui)
                .color(k.c.text_muted),
        );
    }
    let scroller = col()
        .key("chat-scroll")
        .w_full()
        .grow(1.0)
        .scroll(ScrollSpec { stick_bottom: true })
        .child(list);

    let busy = m.chat.is_busy();
    let composer = TextInput::new(&s.composer)
        .placeholder("What should Open Computer do?")
        .on_edit(|s: &mut App| &mut s.composer)
        .on_submit(|s: &mut App, _| s.send())
        .width(None)
        .build(k);
    let send = Button::new(if busy { "Working" } else { "Send" })
        .variant(if busy {
            ButtonVariant::Loading
        } else {
            ButtonVariant::Submit
        })
        .disabled(busy)
        .on_press(|s: &mut App| s.send())
        .build(k);
    let mut bar = row()
        .key("composer-row")
        .gap(8.0)
        .center_y()
        .w_full()
        .child(col().key("composer-fill").grow(1.0).child(composer))
        .child(send);
    if busy {
        bar = bar.child(
            Button::new("Stop")
                .variant(ButtonVariant::Neutral)
                .on_press(|s: &mut App| s.stop_task())
                .build(k),
        );
    }
    col()
        .key("chat")
        .w_full()
        .grow(1.0)
        .gap(8.0)
        .child(scroller)
        .child(bar)
        .child(footer(k, m))
}

/// Model, spend against the caps, and the task state, always visible under the composer.
fn footer(k: &Kit, m: &Main) -> El {
    let cost = m.chat.items.iter().rev().find_map(|i| match i {
        Item::Cost {
            task,
            day,
            task_cap,
            day_cap,
        } => Some((*task, *day, *task_cap, *day_cap)),
        _ => None,
    });
    let spend = match cost {
        Some((t, d, tc, dc)) => format!(
            "task ${t:.4} of ${tc:.2}  |  today ${d:.4} of {}",
            dc.map_or("no cap".to_owned(), |c| format!("${c:.2}"))
        ),
        None => "no spend yet".to_owned(),
    };
    let state = if m.chat.status.is_empty() {
        "idle"
    } else {
        m.chat.status.as_str()
    };
    text(format!(
        "{}  |  {spend}  |  {state}",
        defaults::default_model()
    ))
    .key("footer")
    .font(k.ty.caption())
    .color(k.c.text_muted)
}

fn item_view(k: &Kit, s: &App, m: &Main, i: usize, item: &Item) -> El {
    let key = format!("item-{i}");
    match item {
        Item::User(t) => card(k, key)
            .child(text("You").font(k.ty.caption()).color(k.c.text_muted))
            .child(text(t.clone()).font(k.ty.ui).color(k.c.text_strong)),
        Item::Thinking { text: t, done } => {
            let open = !*done || m.chat.open.contains(&i);
            let head = row()
                .gap(8.0)
                .center_y()
                .child(
                    text(if *done { "Thought" } else { "Thinking" })
                        .font(k.ty.caption())
                        .color(k.c.text_muted),
                )
                .child(
                    Button::new(if open { "Hide" } else { "Show" })
                        .variant(ButtonVariant::Ghost)
                        .on_press(move |s: &mut App| s.toggle_item(i))
                        .build(k),
                );
            let mut c = card(k, key).child(head);
            if open {
                c = c.child(text(t.clone()).font(k.ty.ui).color(k.c.text_muted));
            }
            c
        }
        Item::Assistant { text: t, done } => card(k, key)
            .child(
                text(if *done {
                    "Open Computer"
                } else {
                    "Open Computer is writing"
                })
                .font(k.ty.caption())
                .color(k.c.text_muted),
            )
            .child(text(t.clone()).font(k.ty.ui).color(k.c.text_strong)),
        Item::Tool(t) => tool_card(k, m, i, key, t),
        Item::Approval(a) => approval_card(k, s, key, a),
        Item::Step {
            step,
            prompt,
            completion,
            reasoning,
            cost,
        } => text(format!(
            "step {}: {prompt} in, {completion} out{}{}",
            step + 1,
            if *reasoning > 0 {
                format!(" ({reasoning} reasoning)")
            } else {
                String::new()
            },
            cost.map_or(String::new(), |c| format!(", ${c:.5}"))
        ))
        .key(key)
        .font(k.ty.caption())
        .color(k.c.text_muted),
        Item::Cost { .. } => col().key(key),
        Item::Note(n) => text(n.clone())
            .key(key)
            .font(k.ty.caption())
            .color(k.c.text_muted),
    }
}

fn approval_card(k: &Kit, s: &App, key: String, a: &ApprovalCard) -> El {
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

fn tool_card(k: &Kit, m: &Main, i: usize, key: String, t: &ToolCard) -> El {
    let state = match t.status.as_str() {
        "running" => "Running",
        "ok" => "Done",
        "denied" => "Denied",
        _ => "Failed",
    };
    let finished = t.status != "running";
    let open = !finished || m.chat.open.contains(&i);
    let mut head = row()
        .gap(8.0)
        .center_y()
        .child(
            text(t.name.clone())
                .font(k.ty.ui_medium)
                .color(k.c.text_strong),
        )
        .child(Badge::new(state).variant(BadgeVariant::Neutral).build(k))
        .child(
            text(if t.risk.is_empty() {
                String::new()
            } else {
                format!("risk: {}", t.risk)
            })
            .font(k.ty.caption())
            .color(k.c.text_muted),
        );
    if finished {
        head = head.child(
            Button::new(if open { "Hide" } else { "Details" })
                .variant(ButtonVariant::Ghost)
                .on_press(move |s: &mut App| s.toggle_item(i))
                .build(k),
        );
    }
    let mut c = card(k, key).child(head);
    if open && !t.args.is_empty() {
        let body = write_diff(&t.args).unwrap_or_else(|| clip(&t.args, 400));
        c = c.child(text(body).font(k.ty.caption()).color(k.c.text_muted));
    }
    if open && let Some(out) = &t.output {
        let ms = t.ms.map_or(String::new(), |m| format!("  ({m:.0} ms)"));
        c = c.child(
            text(format!("{}{ms}", clip(out, 600)))
                .font(k.ty.caption())
                .color(k.c.text_base),
        );
    }
    c
}

fn clip(s: &str, n: usize) -> String {
    if s.chars().count() <= n {
        s.to_owned()
    } else {
        format!("{}...", s.chars().take(n).collect::<String>())
    }
}

fn settings(s: &App, k: &Kit, m: &Main) -> El {
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
            "Direct to OpenRouter; AI Gateway is not configured on this core.".to_owned(),
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
        .child(note(k, "OpenRouter's TypeScript SDK is used for key and model checks."));

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

fn diagnostics(s: &App, k: &Kit) -> El {
    col()
        .key("diagnostics")
        .w_full()
        .grow(1.0)
        .gap(10.0)
        .child(caption(
            k,
            "The same chain as the web wizard and `oc doctor`: backend, OpenRouter, speed, sandbox, memory, audit and spend.",
        ))
        .child(
            Button::new("Run tests")
                .variant(if s.diag_running {
                    ButtonVariant::Loading
                } else {
                    ButtonVariant::Submit
                })
                .disabled(s.diag_running)
                .on_press(|s: &mut App| s.run_full_tests())
                .build(k),
        )
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
