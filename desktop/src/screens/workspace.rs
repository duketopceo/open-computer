//! The main window as a chat-first agent workspace: a collapsible sidebar (new task, threads, settings), the conversation in
//! the centre with a multi-line composer, and a right "Computer" panel that shows the task working (steps, output, files,
//! cost). Mostly black and white; the accent gradient marks the primary action and live progress.

use std::time::SystemTime;

use rune::{El, Icon, ScrollSpec, Transition, col, el, icon, row, text};
use rune_kit::glyphs::{CHECK_SMALL, CLOSE};
use rune_kit::shell::{INSPECTOR, SIDEBAR};
use rune_kit::{Button, ButtonVariant, Kit, Markdown, Spinner, TextArea};
use shell_core::chat::{Chat, Item, ToolCard, write_diff};
use shell_core::defaults;
use shell_core::harness::Harness;

use super::{accent_button, main_view};
use crate::app::App;
use crate::main_app::{Group, Main, Tab, group_for, relative_time, title_of};

/// A thin arrow pointing up, for the round Send button.
static ARROW_UP: Icon = Icon {
    name: "oc-arrow-up",
    view: 16.0,
    body: r#"<path d="M8 13V3M3.5 7.5L8 3L12.5 7.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" fill="none"/>"#,
};
/// A filled square for Stop.
static STOP: Icon = Icon {
    name: "oc-stop",
    view: 16.0,
    body: r#"<rect x="4" y="4" width="8" height="8" rx="1.5" fill="currentColor"/>"#,
};
/// What pressing a chip does.
type Action = Box<dyn Fn(&mut App)>;

pub const SIDEBAR_W: f32 = 260.0;
pub const COMPOSER_W: f32 = 720.0;

/// Four small ways to start, one per capability.
pub const SUGGESTIONS: [(&str, &str); 4] = [
    (
        "Run a command",
        "Run the command `uname -a` in your sandbox and tell me what it printed.",
    ),
    (
        "Write a file",
        "Write a file named notes.txt that says hello, then read it back to me.",
    ),
    (
        "Look something up",
        "Use web search to find the current stable version of Node.js and answer in one sentence.",
    ),
    (
        "What do you remember?",
        "Summarize what you remember about me and my projects so far.",
    ),
];

// ---------- pure helpers (tested) ----------

/// One step of the Computer panel: a tool call.
#[derive(Clone, Debug, PartialEq)]
pub struct Step {
    /// The index of the call in the chat items.
    pub item: usize,
    pub name: String,
    pub status: String,
    pub ms: Option<f64>,
}

pub fn steps(chat: &Chat) -> Vec<Step> {
    chat.items
        .iter()
        .enumerate()
        .filter_map(|(i, it)| match it {
            Item::Tool(t) => Some(Step {
                item: i,
                name: t.name.clone(),
                status: t.status.clone(),
                ms: t.ms,
            }),
            _ => None,
        })
        .collect()
}

/// The step the panel shows: the one picked, else the last.
pub fn selected(chat: &Chat, picked: Option<usize>) -> Option<usize> {
    let all = steps(chat);
    picked
        .filter(|p| all.iter().any(|s| s.item == *p))
        .or_else(|| all.last().map(|s| s.item))
}

/// Paths the task wrote, from its write-file calls, in order and without repeats.
pub fn files_created(chat: &Chat) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for it in &chat.items {
        if let Item::Tool(t) = it
            && t.name.ends_with("write_file")
            && let Ok(v) = serde_json::from_str::<serde_json::Value>(&t.args)
            && let Some(p) = v.get("path").and_then(|p| p.as_str())
            && !out.iter().any(|o| o == p)
        {
            out.push(p.to_owned());
        }
    }
    out
}

/// The cost of each model step, as the core reported it.
pub fn model_step_costs(chat: &Chat) -> Vec<(u64, Option<f64>)> {
    chat.items
        .iter()
        .filter_map(|i| match i {
            Item::Step { step, cost, .. } => Some((*step, *cost)),
            _ => None,
        })
        .collect()
}

pub fn duration(ms: f64) -> String {
    if ms >= 1000.0 {
        format!("{:.1} s", ms / 1000.0)
    } else {
        format!("{ms:.0} ms")
    }
}

/// Model, spend today against the cap, and the core's state: the line under the composer.
pub fn status_line(chat: &Chat, model: &str, core: &str) -> String {
    let spend = chat
        .items
        .iter()
        .rev()
        .find_map(|i| match i {
            Item::Cost { day, day_cap, .. } => Some(format!(
                "today ${day:.4} of {}",
                day_cap.map_or("no cap".to_owned(), |c| format!("${c:.2}"))
            )),
            _ => None,
        })
        .unwrap_or_else(|| "no spend yet".to_owned());
    format!("{model} · {spend} · {core}")
}

fn core_state(m: &Main) -> &'static str {
    if m.endpoint.is_some() {
        "core connected"
    } else if m.starting_local {
        "starting the core"
    } else {
        "core not connected"
    }
}

fn model_name(m: &Main) -> String {
    m.settings
        .as_ref()
        .and_then(|v| v.get("default_model"))
        .and_then(|v| v.as_str())
        .map_or_else(defaults::default_model, str::to_owned)
}

// ---------- the window ----------

pub fn view(s: &App, k: &Kit, m: &Main) -> El {
    let mut window = row().key("window").w_full().h_full();
    if m.sidebar_open {
        window = window.child(sidebar(s, k, m));
    }
    let body = match m.tab {
        Tab::Chat => chat_page(s, k, m),
        Tab::Settings => page(s, k, m, "Settings", main_view::settings(s, k, m)),
        Tab::Diagnostics => page(s, k, m, "Diagnostics", main_view::diagnostics(s, k)),
    };
    let mut content = col().key("content").grow(1.0).h_full().child(body);
    if let Some(n) = &s.notice {
        content = content.child(
            text(n.clone())
                .key("notice")
                .font(k.ty.ui)
                .color(k.c.text_strong)
                .padding(8.0),
        );
    }
    window.child(content)
}

/// Settings and Diagnostics open as full pages in the content area, with a way back.
fn page(s: &App, k: &Kit, m: &Main, title: &str, body: El) -> El {
    let _ = s;
    col()
        .key("page")
        .w_full()
        .grow(1.0)
        .padding(24.0)
        .gap(16.0)
        .child(
            row()
                .gap(12.0)
                .center_y()
                .child(sidebar_toggle(k, m))
                .child(
                    text(title.to_owned())
                        .font(heading_style(k, 20.0))
                        .color(k.c.text_strong)
                        .grow(1.0),
                )
                .child(
                    Button::new("Back to chat")
                        .variant(ButtonVariant::Ghost)
                        .on_press(|s: &mut App| s.show_tab(Tab::Chat))
                        .build(k),
                ),
        )
        .child(col().w_full().grow(1.0).max_w(880.0).child(body))
}

fn heading_style(k: &Kit, size: f32) -> rune::TextStyle {
    rune::TextStyle {
        size,
        line_height: (size * 1.25).round(),
        ..k.ty.body_medium
    }
}

fn sidebar_toggle(k: &Kit, m: &Main) -> El {
    let label = if m.sidebar_open {
        "Hide sidebar"
    } else {
        "Show sidebar"
    };
    row()
        .key("sidebar-toggle")
        .role("button")
        .label(label)
        .focusable(true)
        .center_y()
        .padding(8.0)
        .radius(8.0)
        .child(icon(&SIDEBAR, 16.0).color(k.c.text_muted))
        .on_press(|s: &mut App| s.toggle_sidebar())
}

// ---------- sidebar ----------

fn sidebar(_s: &App, k: &Kit, m: &Main) -> El {
    let now = SystemTime::now();
    let mut list = col().key("threads").w_full().gap(2.0);
    // Newest first: the task on screen (when it has begun), then earlier ones from the most recent.
    let mut entries: Vec<(Option<usize>, String, SystemTime, bool)> = Vec::new();
    if !m.chat.items.is_empty() {
        entries.push((None, short(&title_of(&m.chat)), m.began, m.chat.is_busy()));
    }
    for (i, t) in m.threads.iter().enumerate().rev() {
        entries.push((
            Some(i),
            short(&title_of(&t.chat)),
            t.began,
            t.chat.is_busy(),
        ));
    }
    for group in [Group::Today, Group::Earlier] {
        let here: Vec<_> = entries
            .iter()
            .filter(|e| group_for(e.2, now) == group)
            .collect();
        if here.is_empty() {
            continue;
        }
        list = list.child(
            text(if group == Group::Today {
                "Today"
            } else {
                "Earlier"
            })
            .font(k.ty.caption())
            .color(k.c.text_muted)
            .padding(8.0),
        );
        for (index, title, began, running) in here {
            let current = index.is_none();
            let dot = if *running {
                Spinner::new()
                    .size(10.0)
                    .color(crate::theme::accent_amber())
                    .build(k)
            } else {
                el().w(8.0).h(8.0).radius(4.0).fill(if current {
                    k.c.text_strong
                } else {
                    k.c.border_strong
                })
            };
            let key = format!(
                "thread-{}",
                index.map_or("current".to_owned(), |i| i.to_string())
            );
            let mut r = row()
                .key(key)
                .role("button")
                .label(title.clone())
                .focusable(true)
                .w_full()
                .gap(8.0)
                .center_y()
                .padding(8.0)
                .radius(8.0)
                .child(col().w(12.0).items_center().child(dot))
                .child(
                    text(title.clone())
                        .font(k.ty.ui)
                        .color(k.c.text_base)
                        .grow(1.0),
                )
                .child(
                    text(relative_time(*began, now))
                        .font(k.ty.caption())
                        .color(k.c.text_muted),
                );
            if current && m.tab == Tab::Chat {
                r = r.fill(k.c.layer_02);
            }
            r = match index {
                Some(i) => {
                    let i = *i;
                    r.on_press(move |s: &mut App| s.open_thread(i))
                }
                None => r.on_press(|s: &mut App| s.show_tab(Tab::Chat)),
            };
            list = list.child(r);
        }
    }
    if entries.is_empty() {
        list = list.child(
            text("Your tasks appear here.")
                .font(k.ty.caption())
                .color(k.c.text_muted)
                .padding(8.0),
        );
    }

    let nav_button = |label: &str, key: &'static str, tab: Tab, indent: bool| {
        let on = m.tab == tab;
        let mut r = row()
            .key(key)
            .role("button")
            .label(label.to_owned())
            .focusable(true)
            .w_full()
            .center_y()
            .padding(8.0)
            .radius(8.0)
            .child(text(label.to_owned()).font(k.ty.ui).color(if on {
                k.c.text_strong
            } else {
                k.c.text_base
            }));
        if indent {
            r = r.padding(8.0);
        }
        if on {
            r = r.fill(k.c.layer_02);
        }
        r.on_press(move |s: &mut App| s.show_tab(tab))
    };
    let chip = row()
        .key("core-chip")
        .gap(8.0)
        .center_y()
        .padding(8.0)
        .radius(8.0)
        .border(1.0, k.c.border_base)
        .child(
            el().w(8.0)
                .h(8.0)
                .radius(4.0)
                .fill(if m.endpoint.is_some() {
                    k.c.text_strong
                } else {
                    k.c.border_strong
                }),
        )
        .child(
            text(core_state(m))
                .font(k.ty.caption())
                .color(k.c.text_muted),
        );

    col()
        .key("sidebar")
        .w(SIDEBAR_W)
        .h_full()
        .fill(k.c.layer_01)
        .padding(12.0)
        .gap(12.0)
        .child(
            row()
                .gap(8.0)
                .center_y()
                .child(
                    text("Open Computer")
                        .font(k.ty.body_medium)
                        .color(k.c.text_strong)
                        .grow(1.0),
                )
                .child(sidebar_toggle(k, m)),
        )
        .child(accent_button(
            k,
            "new-task",
            "New task",
            true,
            |s: &mut App| s.new_task(),
        ))
        .child(
            col()
                .key("threads-scroll")
                .w_full()
                .grow(1.0)
                .scroll(ScrollSpec {
                    stick_bottom: false,
                })
                .child(list),
        )
        .child(
            col()
                .key("sidebar-bottom")
                .w_full()
                .gap(4.0)
                .child(nav_button("Settings", "nav-settings", Tab::Settings, false))
                .child(
                    row()
                        .w_full()
                        .child(el().w(16.0))
                        .child(col().grow(1.0).child(nav_button(
                            "Diagnostics",
                            "nav-diagnostics",
                            Tab::Diagnostics,
                            true,
                        ))),
                )
                .child(chip),
        )
}

// ---------- the chat ----------

fn chat_page(s: &App, k: &Kit, m: &Main) -> El {
    if m.chat.items.is_empty() {
        return empty_state(s, k, m);
    }
    let panel = m.panel_open();
    let mut top = row()
        .key("task-bar")
        .w_full()
        .gap(12.0)
        .center_y()
        .padding(12.0)
        .child(if m.sidebar_open {
            el().w(0.0)
        } else {
            sidebar_toggle(k, m)
        })
        .child(
            text(title_of(&m.chat))
                .font(heading_style(k, 15.0))
                .color(k.c.text_strong)
                .grow(1.0),
        );
    top = top.child(
        row()
            .key("panel-toggle")
            .role("button")
            .label(if panel {
                "Hide Computer panel"
            } else {
                "Show Computer panel"
            })
            .focusable(true)
            .gap(8.0)
            .center_y()
            .padding(8.0)
            .radius(8.0)
            .border(1.0, k.c.border_base)
            .child(icon(&INSPECTOR, 16.0).color(k.c.text_muted))
            .child(text("Computer").font(k.ty.ui).color(k.c.text_base))
            .on_press(|s: &mut App| s.toggle_panel()),
    );
    let conversation = col()
        .key("conversation")
        .grow(1.0)
        .h_full()
        .child(top)
        .child(transcript(s, k, m))
        .child(col().w_full().items_center().padding(16.0).child(composer(
            s,
            k,
            m,
            "Ask a follow-up",
        )));
    let mut body = row().key("task").w_full().h_full().child(conversation);
    if panel {
        body = body.child(computer_panel(s, k, m));
    }
    body
}

fn empty_state(s: &App, k: &Kit, m: &Main) -> El {
    let mut chips = row().key("suggestions").gap(8.0).items_center();
    for (i, (label, prompt)) in SUGGESTIONS.iter().enumerate() {
        let prompt: &'static str = prompt;
        chips = chips.child(
            row()
                .key(format!("suggestion-{i}"))
                .role("button")
                .label(*label)
                .focusable(true)
                .center_y()
                .padding(8.0)
                .radius(16.0)
                .border(1.0, k.c.border_base)
                .child(text(*label).font(k.ty.ui).color(k.c.text_base))
                .on_press(move |s: &mut App| s.use_example(prompt)),
        );
    }
    let hint = if m.endpoint.is_some() || m.starting_local {
        None
    } else {
        Some("Not connected to a core yet. Open Settings.")
    };
    let mut centre = col()
        .key("empty")
        .w_full()
        .max_w(COMPOSER_W)
        .gap(24.0)
        .items_center()
        .child(
            text("What can I do for you?")
                .key("empty-heading")
                .font(heading_style(k, 24.0))
                .color(k.c.text_strong),
        )
        .child(composer(s, k, m, "What should Open Computer do?"))
        .child(chips);
    if let Some(h) = hint {
        centre = centre.child(text(h).font(k.ty.caption()).color(k.c.text_muted));
    }
    let mut top = row().w_full().padding(12.0);
    if !m.sidebar_open {
        top = top.child(sidebar_toggle(k, m));
    }
    col().key("empty-page").w_full().h_full().child(top).child(
        col()
            .w_full()
            .grow(1.0)
            .items_center()
            .justify_center()
            .padding(24.0)
            .child(centre),
    )
}

fn transcript(s: &App, k: &Kit, m: &Main) -> El {
    let mut list = col()
        .key("chat-list")
        .w_full()
        .max_w(COMPOSER_W)
        .gap(16.0)
        .padding(16.0);
    for (i, item) in m.chat.items.iter().enumerate() {
        if let Some(e) = turn(k, s, m, i, item) {
            list = list.child(e);
        }
    }
    if m.chat.is_busy() && !m.chat.has_reasoning() {
        list = list.child(
            row()
                .key("working")
                .gap(8.0)
                .center_y()
                .child(
                    Spinner::new()
                        .size(12.0)
                        .color(crate::theme::accent_amber())
                        .build(k),
                )
                .child(
                    text(format!("Working · {}s", m.elapsed_secs()))
                        .font(k.ty.ui)
                        .color(k.c.text_muted),
                ),
        );
    }
    col()
        .key("chat-scroll")
        .w_full()
        .grow(1.0)
        .items_center()
        .scroll(ScrollSpec { stick_bottom: true })
        .child(list)
}

/// One turn of the conversation. Steps and costs show in the Computer panel instead, so they return nothing here.
fn turn(k: &Kit, s: &App, m: &Main, i: usize, item: &Item) -> Option<El> {
    let key = format!("item-{i}");
    Some(match item {
        Item::User(t) => row().key(key).w_full().justify_end().child(
            col()
                .max_w(560.0)
                .padding(12.0)
                .radius(16.0)
                .fill(k.c.layer_02)
                .child(text(t.clone()).font(k.ty.ui).color(k.c.text_strong)),
        ),
        Item::Thinking { text: t, done } => {
            let open = !*done || m.chat.open.contains(&i);
            let mut c = col().key(key).w_full().gap(4.0).child(
                row()
                    .key(format!("thinking-toggle-{i}"))
                    .role("button")
                    .label(if *done { "Thought" } else { "Thinking…" })
                    .focusable(true)
                    .gap(8.0)
                    .center_y()
                    .child(if *done {
                        icon(&CHECK_SMALL, 14.0).color(k.c.text_muted)
                    } else {
                        Spinner::new()
                            .size(12.0)
                            .color(crate::theme::accent_amber())
                            .build(k)
                    })
                    .child(
                        text(if *done { "Thought" } else { "Thinking…" })
                            .font(k.ty.ui)
                            .color(k.c.text_muted),
                    )
                    .on_press(move |s: &mut App| s.toggle_item(i)),
            );
            if open {
                c = c.child(
                    text(t.clone())
                        .font(k.ty.caption())
                        .color(k.c.text_muted)
                        .padding(8.0),
                );
            }
            c
        }
        Item::Assistant { text: t, done } => col()
            .key(key)
            .w_full()
            .child(Markdown::new(t.clone()).streaming(!*done).build(k)),
        Item::Tool(t) => tool_chip(k, m, i, key, t),
        Item::Approval(a) => main_view::approval_card(k, s, key, a),
        Item::Note(n) => text(n.clone())
            .key(key)
            .font(k.ty.caption())
            .color(k.c.text_muted),
        Item::Step { .. } | Item::Cost { .. } => return None,
    })
}

/// A tool call in the conversation: one line, which opens its step in the Computer panel.
fn tool_chip(k: &Kit, m: &Main, i: usize, key: String, t: &ToolCard) -> El {
    let picked = selected(&m.chat, m.selected_step) == Some(i) && m.panel_open();
    let mut r = row()
        .key(key)
        .role("button")
        .label(t.name.clone())
        .focusable(true)
        .gap(8.0)
        .center_y()
        .padding(8.0)
        .radius(12.0)
        .border(1.0, k.c.border_base)
        .child(status_mark(k, &t.status))
        .child(text(t.name.clone()).font(k.ty.ui).color(k.c.text_base))
        .child(
            text(t.ms.map_or(String::new(), duration))
                .font(k.ty.caption())
                .color(k.c.text_muted),
        );
    if picked {
        r = r.border(1.0, k.c.border_strong);
    }
    row()
        .w_full()
        .child(r.on_press(move |s: &mut App| s.select_step(i)))
}

fn status_mark(k: &Kit, status: &str) -> El {
    match status {
        "running" => Spinner::new()
            .size(12.0)
            .color(crate::theme::accent_amber())
            .build(k),
        "ok" => icon(&CHECK_SMALL, 14.0).color(k.c.text_strong),
        _ => icon(&CLOSE, 14.0).color(k.c.text_strong),
    }
}

// ---------- the composer ----------

fn chip(k: &Kit, key: &str, label: String, on: Option<Action>, disabled_note: bool) -> El {
    let mut r = row()
        .key(key.to_owned())
        .role("button")
        .label(label.clone())
        .gap(4.0)
        .center_y()
        .padding(8.0)
        .radius(12.0)
        .border(1.0, k.c.border_base)
        .child(text(label).font(k.ty.caption()).color(if disabled_note {
            k.c.text_muted
        } else {
            k.c.text_base
        }));
    if let Some(f) = on {
        r = r.focusable(true).on_press(move |s: &mut App| f(s));
    }
    r
}

pub fn composer(s: &App, k: &Kit, m: &Main, placeholder: &str) -> El {
    let busy = m.chat.is_busy();
    let area = TextArea::new(&s.composer)
        .placeholder(placeholder.to_owned())
        .width(None)
        .auto_grow(3, 6)
        .on_edit(|s: &mut App| &mut s.composer)
        .on_submit(|s: &mut App, _| s.send())
        // Return sends; Shift+Return adds a line.
        .on_key(|s: &mut App, ev: &rune::KeyEvent, _| {
            if ev.key == rune::Key::Enter && !ev.mods.shift {
                s.send();
                true
            } else {
                false
            }
        })
        .build(k);

    let harness = s.harness;
    let mut controls = row().key("composer-controls").w_full().gap(8.0).center_y();
    controls = controls.child(chip(
        k,
        "harness-chip",
        format!("Agent: {}", harness.label()),
        Some(Box::new(|s: &mut App| s.toggle_harness_menu())),
        false,
    ));
    controls = controls.child(chip(
        k,
        "model-chip",
        model_name(m),
        Some(Box::new(|s: &mut App| s.show_tab(Tab::Settings))),
        false,
    ));
    // Attaching files is not built yet, so it is shown as unavailable and does nothing.
    controls =
        controls.child(chip(k, "attach-chip", "Attach · soon".to_owned(), None, true).opacity(0.6));
    controls = controls.child(col().grow(1.0));
    let send = if busy {
        row()
            .key("stop")
            .role("button")
            .label("Stop")
            .focusable(true)
            .w(40.0)
            .h(40.0)
            .radius(20.0)
            .items_center()
            .justify_center()
            .fill(k.c.text_strong)
            .child(icon(&STOP, 16.0).color(k.c.page))
            .on_press(|s: &mut App| s.stop_task())
    } else {
        crate::theme::accent_fill(
            row()
                .key("send")
                .role("button")
                .label("Send")
                .focusable(true)
                .w(40.0)
                .h(40.0)
                .radius(20.0)
                .items_center()
                .justify_center()
                .child(icon(&ARROW_UP, 18.0).color(crate::theme::on_accent())),
        )
        .on_press(|s: &mut App| s.send())
    };
    controls = controls.child(send);

    let mut card = col()
        .key("composer")
        .w_full()
        .max_w(COMPOSER_W)
        .gap(8.0)
        .padding(12.0)
        .radius(16.0)
        .fill(k.c.layer_01)
        .border(1.0, k.c.border_base)
        .shadow(8.0, 24.0, rune::Color::BLACK.alpha(0.18))
        .child(area);
    if m.harness_menu {
        let mut menu = row().key("harness-menu").gap(8.0);
        for h in Harness::ALL {
            let installed = s
                .harness_info
                .iter()
                .find(|d| d.harness == h)
                .is_none_or(|d| d.installed);
            let label = if installed {
                h.label().to_owned()
            } else {
                format!("{} · not installed", h.label())
            };
            menu = menu.child(
                chip(
                    k,
                    &format!("harness-{}", h.id()),
                    label,
                    if installed {
                        Some(Box::new(move |s: &mut App| s.pick_harness(h)))
                    } else {
                        None
                    },
                    !installed,
                )
                .fill(if h == harness {
                    k.c.layer_03
                } else {
                    k.c.layer_01
                }),
            );
        }
        card = card.child(menu);
    }
    card = card.child(controls);
    col()
        .key("composer-area")
        .w_full()
        .max_w(COMPOSER_W)
        .gap(8.0)
        .child(card)
        .child(
            text(status_line(&m.chat, &model_name(m), core_state(m)))
                .key("status-line")
                .font(k.ty.caption())
                .color(k.c.text_muted),
        )
}

// ---------- the Computer panel ----------

fn computer_panel(_s: &App, k: &Kit, m: &Main) -> El {
    let all = steps(&m.chat);
    let pick = selected(&m.chat, m.selected_step);
    let mut timeline = col().key("timeline").w_full().gap(4.0);
    if all.is_empty() {
        timeline = timeline.child(
            text("Steps appear here as the task works.")
                .font(k.ty.caption())
                .color(k.c.text_muted),
        );
    }
    for (n, st) in all.iter().enumerate() {
        let item = st.item;
        let mut r = row()
            .key(format!("step-{n}"))
            .role("button")
            .label(st.name.clone())
            .focusable(true)
            .w_full()
            .gap(8.0)
            .center_y()
            .padding(8.0)
            .radius(8.0)
            .child(status_mark(k, &st.status))
            .child(
                text(st.name.clone())
                    .font(k.ty.ui)
                    .color(k.c.text_base)
                    .grow(1.0),
            )
            .child(
                text(st.ms.map_or(String::new(), duration))
                    .font(k.ty.caption())
                    .color(k.c.text_muted),
            );
        if pick == Some(item) {
            r = r.fill(k.c.layer_02);
        }
        timeline = timeline.child(r.on_press(move |s: &mut App| s.select_step(item)));
    }

    let mut detail = col().key("step-detail").w_full().gap(8.0);
    if let Some(Item::Tool(t)) = pick.and_then(|i| m.chat.items.get(i)) {
        detail = detail.child(
            text(t.name.clone())
                .font(k.ty.ui_medium)
                .color(k.c.text_strong),
        );
        if !t.args.is_empty() {
            let body = write_diff(&t.args).unwrap_or_else(|| clip(&t.args, 600));
            detail = detail.child(block(k, "Input", body));
        }
        if let Some(out) = &t.output {
            detail = detail.child(block(k, "Output", clip(out, 1200)));
        } else if t.status == "running" {
            detail = detail.child(text("Running…").font(k.ty.caption()).color(k.c.text_muted));
        }
    }

    let files = files_created(&m.chat);
    let mut files_col = col().key("files").w_full().gap(2.0);
    for f in &files {
        files_col = files_col.child(text(f.clone()).font(k.ty.caption()).color(k.c.text_base));
    }
    let mut cost = col().key("cost").w_full().gap(2.0);
    let costs = model_step_costs(&m.chat);
    for (step, c) in &costs {
        cost = cost.child(
            text(format!(
                "model step {}: {}",
                step + 1,
                c.map_or("cost not reported".to_owned(), |c| format!("${c:.5}"))
            ))
            .font(k.ty.caption())
            .color(k.c.text_muted),
        );
    }
    if let Some(Item::Cost { task, task_cap, .. }) = m
        .chat
        .items
        .iter()
        .rev()
        .find(|i| matches!(i, Item::Cost { .. }))
    {
        cost = cost.child(
            text(format!("task total ${task:.4} of ${task_cap:.2}"))
                .font(k.ty.ui)
                .color(k.c.text_base),
        );
    }

    col()
        .key("computer-panel")
        .w_frac(0.4)
        .h_full()
        .fill(k.c.layer_01)
        .padding(16.0)
        .gap(16.0)
        .transition(Transition::offset(48.0, 0.0))
        .child(
            row()
                .gap(8.0)
                .center_y()
                .child(
                    text("Computer")
                        .font(heading_style(k, 15.0))
                        .color(k.c.text_strong)
                        .grow(1.0),
                )
                .child(
                    row()
                        .key("panel-close")
                        .role("button")
                        .label("Close the Computer panel")
                        .focusable(true)
                        .padding(8.0)
                        .radius(8.0)
                        .child(icon(&CLOSE, 14.0).color(k.c.text_muted))
                        .on_press(|s: &mut App| s.toggle_panel()),
                ),
        )
        .child(
            col()
                .key("panel-scroll")
                .w_full()
                .grow(1.0)
                .gap(16.0)
                .scroll(ScrollSpec {
                    stick_bottom: false,
                })
                .child(section(k, "Steps", timeline))
                .child(detail)
                .child(if files.is_empty() {
                    col().key("no-files")
                } else {
                    section(k, "Files", files_col)
                })
                .child(section(k, "Cost", cost)),
        )
}

fn section(k: &Kit, title: &str, body: El) -> El {
    col()
        .w_full()
        .gap(8.0)
        .child(
            text(title.to_owned())
                .font(k.ty.caption())
                .color(k.c.text_muted),
        )
        .child(body)
}

/// Terminal-style output or a file diff: a bordered block in the mono face.
fn block(k: &Kit, label: &str, body: String) -> El {
    col()
        .w_full()
        .gap(4.0)
        .child(
            text(label.to_owned())
                .font(k.ty.caption())
                .color(k.c.text_muted),
        )
        .child(
            col()
                .w_full()
                .padding(12.0)
                .radius(12.0)
                .border(1.0, k.c.border_base)
                .fill(k.c.layer_02)
                .child(text(body).font(k.ty.mono).color(k.c.text_base).w_full()),
        )
}

/// A title that fits a sidebar row.
fn short(t: &str) -> String {
    clip(t, 24)
}

fn clip(s: &str, n: usize) -> String {
    if s.chars().count() <= n {
        s.to_owned()
    } else {
        format!("{}...", s.chars().take(n).collect::<String>())
    }
}
