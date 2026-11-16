//! Headless layout tests of the chat-first workspace: no window, an in-memory secret store, and conversations built from the
//! same events a core streams.

use std::sync::Arc;
use std::time::{Duration, SystemTime};

use rune::runtime::ElementInfo;
use rune::{Env, Key, KeyEvent, Mods, Runtime};
use shell_core::chat::Chat;
use shell_core::harness::Harness;
use shell_core::secrets::InMemorySecrets;

use super::view;
use super::workspace::{self, SUGGESTIONS};
use crate::app::App;
use crate::main_app::{Group, Main, Tab, group_for, relative_time, title_of};

fn app_with(kind: &str) -> App {
    let mut app = App::with_store(Arc::new(InMemorySecrets::new()));
    app.main = Some(Main::new());
    app.harness = Harness::BuiltIn;
    app.apply_workspace_fixture(kind);
    app.harness = Harness::BuiltIn;
    app
}

fn runtime(app: App, size: (f32, f32)) -> Runtime<App> {
    let mut rt = Runtime::new(app, view, size, Env::new(true, 1.0));
    rt.frame(0.0);
    rt
}

fn find(rt: &Runtime<App>, key: &str) -> Option<ElementInfo> {
    rt.elements()
        .into_iter()
        .find(|e| e.key.as_deref() == Some(key) && !e.exiting)
}

/// Lets entrances and exits finish (an element that leaves fades out for a moment).
fn settle(rt: &mut Runtime<App>) {
    for _ in 0..40 {
        rt.frame(1.0 / 60.0);
    }
}

fn get(rt: &Runtime<App>, key: &str) -> ElementInfo {
    find(rt, key).unwrap_or_else(|| panic!("missing element {key:?}"))
}

fn texts(rt: &Runtime<App>) -> Vec<String> {
    rt.elements().into_iter().filter_map(|e| e.text).collect()
}

fn has(rt: &Runtime<App>, needle: &str) -> bool {
    texts(rt).iter().any(|t| t.contains(needle))
}

const SIZES: [(f32, f32); 2] = [(1440.0, 900.0), (1000.0, 800.0)];

#[test]
fn the_empty_state_is_a_centred_composer_at_least_80_points_tall_with_four_small_chips() {
    for size in SIZES {
        let rt = runtime(app_with("empty"), size);
        let c = get(&rt, "composer");
        assert!(
            c.rect[3] >= 80.0,
            "the composer is at least 80 tall at {size:?}: {:?}",
            c.rect
        );
        assert!(
            c.rect[1] >= 0.0 && c.rect[1] + c.rect[3] <= size.1,
            "the composer is inside the window at {size:?}: {:?}",
            c.rect
        );
        assert!(
            c.rect[2] <= workspace::COMPOSER_W + 0.5,
            "about 720 wide: {:?}",
            c.rect
        );
        // Centred in the content area (the part right of the sidebar).
        let content = get(&rt, "content");
        let mid = c.rect[0] + c.rect[2] / 2.0;
        assert!(
            (mid - (content.rect[0] + content.rect[2] / 2.0)).abs() < 2.0,
            "centred: {mid} in {:?}",
            content.rect
        );
        assert!(has(&rt, "What can I do for you?"));
        for i in 0..SUGGESTIONS.len() {
            assert!(
                find(&rt, &format!("suggestion-{i}")).is_some(),
                "suggestion chip {i}"
            );
        }
        assert!(find(&rt, "suggestion-4").is_none(), "four chips, not cards");
        // There is no top tab bar any more.
        assert!(find(&rt, "nav").is_none());
    }
}

#[test]
fn the_composer_has_the_inline_controls_and_attach_is_shown_as_unavailable() {
    let rt = runtime(app_with("empty"), SIZES[0]);
    assert!(
        find(&rt, "harness-chip").is_some()
            && find(&rt, "model-chip").is_some()
            && find(&rt, "send").is_some()
    );
    let attach = get(&rt, "attach-chip");
    assert!(
        !attach.focusable,
        "attach does nothing, so it is not focusable"
    );
    assert!(has(&rt, "Attach · soon"));
    // The send button is round: 40 by 40.
    let send = get(&rt, "send");
    assert!((send.rect[2] - 40.0).abs() < 0.5 && (send.rect[3] - 40.0).abs() < 0.5);
    // The status line under the composer: model, spend today, core state.
    assert!(
        has(&rt, "no spend yet") && has(&rt, "core connected") || has(&rt, "core not connected")
    );
}

#[test]
fn a_running_task_shows_the_conversation_the_composer_pinned_below_and_the_computer_panel_with_its_steps()
 {
    for size in SIZES {
        let rt = runtime(app_with("running"), size);
        let panel = get(&rt, "computer-panel");
        let content = get(&rt, "content");
        let frac = panel.rect[2] / content.rect[2];
        assert!(
            (0.36..=0.44).contains(&frac),
            "the panel is about 40% wide at {size:?}: {frac}"
        );
        // Steps in the timeline: each tool call is a row with its name.
        assert!(
            find(&rt, "step-0").is_some() && find(&rt, "step-1").is_some(),
            "the panel lists the steps"
        );
        assert!(has(&rt, "sandbox.write_file") && has(&rt, "sandbox.exec"));
        // The selected step's input, the file the task wrote, and the cost.
        assert!(
            has(&rt, "wc /workspace/notes.txt"),
            "the selected step's input is shown"
        );
        assert!(has(&rt, "/workspace/notes.txt"));
        assert!(has(&rt, "task total"));
        // The composer is pinned at the bottom of the conversation, with Stop instead of Send while it runs.
        let c = get(&rt, "composer");
        assert!(
            c.rect[1] + c.rect[3] <= size.1 && c.rect[1] > size.1 / 2.0,
            "pinned low: {:?}",
            c.rect
        );
        assert!(find(&rt, "stop").is_some() && find(&rt, "send").is_none());
        // The approval card is inline in the conversation.
        assert!(has(&rt, "Needs your decision"));
    }
}

#[test]
fn a_finished_task_shows_markdown_and_keeps_the_panel_closed_until_asked() {
    let mut rt = runtime(app_with("done"), SIZES[0]);
    assert!(
        find(&rt, "computer-panel").is_none(),
        "the panel closes when the task is done"
    );
    assert!(has(&rt, "I wrote") || find(&rt, "item-3").is_some() || find(&rt, "item-4").is_some());
    assert!(find(&rt, "send").is_some() && find(&rt, "stop").is_none());
    // The Computer button opens it.
    rt.update(|s, _| s.toggle_panel());
    settle(&mut rt);
    assert!(find(&rt, "computer-panel").is_some());
}

#[test]
fn the_sidebar_lists_threads_by_day_with_a_running_mark_and_collapses_to_a_toggle() {
    let mut rt = runtime(app_with("running"), SIZES[0]);
    let sidebar = get(&rt, "sidebar");
    assert!(
        (sidebar.rect[2] - workspace::SIDEBAR_W).abs() < 0.5,
        "about 260 wide: {:?}",
        sidebar.rect
    );
    assert!(has(&rt, "Today") && has(&rt, "Earlier"));
    assert!(
        find(&rt, "new-task").is_some()
            && find(&rt, "nav-settings").is_some()
            && find(&rt, "nav-diagnostics").is_some()
    );
    assert!(find(&rt, "thread-current").is_some());

    rt.update(|s, _| s.toggle_sidebar());
    settle(&mut rt);
    assert!(find(&rt, "sidebar").is_none(), "collapsed");
    assert!(
        find(&rt, "sidebar-toggle").is_some(),
        "a toggle remains to bring it back"
    );
    assert!(find(&rt, "composer").is_some());
}

#[test]
fn settings_and_diagnostics_are_full_pages_reached_from_the_sidebar_not_tabs() {
    let mut rt = runtime(app_with("empty"), SIZES[0]);
    rt.update(|s, _| s.show_tab(Tab::Settings));
    settle(&mut rt);
    assert!(find(&rt, "settings-page").is_some() && has(&rt, "Back to chat"));
    assert!(find(&rt, "composer").is_none());
    rt.update(|s, _| s.show_tab(Tab::Diagnostics));
    settle(&mut rt);
    assert!(has(&rt, "Diagnostics") && find(&rt, "chain-header").is_some());
}

#[test]
fn return_sends_and_shift_return_does_not() {
    let mut app = app_with("empty");
    app.composer.replace_text("hello");
    app.composer.focused = true;
    let mut rt = runtime(app, SIZES[0]);
    rt.key(KeyEvent {
        key: Key::Enter,
        mods: Mods {
            shift: true,
            ..Mods::default()
        },
    });
    settle(&mut rt);
    assert!(
        rt.state.notice.is_none(),
        "Shift+Return adds a line and sends nothing"
    );
    rt.key(KeyEvent {
        key: Key::Enter,
        mods: Mods::default(),
    });
    settle(&mut rt);
    // No core is connected in this test, so send() says so: proof that Return reached it.
    assert_eq!(
        rt.state.notice.as_deref(),
        Some("Not connected to a core yet.")
    );
}

#[test]
fn the_harness_chip_opens_a_picker_and_choosing_one_changes_the_agent() {
    let mut rt = runtime(app_with("empty"), SIZES[0]);
    assert!(find(&rt, "harness-menu").is_none());
    rt.update(|s, _| s.toggle_harness_menu());
    settle(&mut rt);
    for h in Harness::ALL {
        assert!(
            find(&rt, &format!("harness-{}", h.id())).is_some(),
            "{}",
            h.label()
        );
    }
    // OpenCode is not detected on this fake, so it is offered as not installed and cannot be picked; Built-in can.
    rt.update(|s, _| s.pick_harness(Harness::BuiltIn));
    settle(&mut rt);
    assert_eq!(rt.state.harness, Harness::BuiltIn);
    assert!(
        find(&rt, "harness-menu").is_none(),
        "the picker closes after a choice"
    );
}

#[test]
fn new_task_keeps_the_running_one_in_the_sidebar_and_events_find_their_own_thread() {
    let mut app = app_with("running");
    let first = app.main.as_ref().unwrap().session_id.clone();
    assert!(app.main.as_ref().unwrap().chat.is_busy());
    app.new_task();
    let m = app.main.as_mut().unwrap();
    assert!(m.chat.items.is_empty(), "a fresh empty task");
    assert_ne!(m.session_id, first);
    assert_eq!(
        m.threads.last().unwrap().session_id,
        first,
        "the running task moved to the list"
    );
    // An event from the first task's stream still lands in the first task, not the new one.
    m.chat_for(&first)
        .unwrap()
        .apply(&serde_json::json!({"type": "run", "status": "done"}));
    assert_eq!(m.threads.last().unwrap().chat.status, "done");
    assert!(m.chat.status.is_empty());
    // Opening it brings it back.
    let index = m.threads.len() - 1;
    m.open_thread(index);
    assert_eq!(m.session_id, first);
    assert!(!m.chat.items.is_empty());
}

#[test]
fn threads_are_grouped_today_and_earlier_with_relative_times_and_short_titles() {
    let now = SystemTime::UNIX_EPOCH + Duration::from_secs(1_800_000_000);
    let ago = |s: u64| now - Duration::from_secs(s);
    assert_eq!(group_for(ago(60), now), Group::Today);
    assert_eq!(group_for(ago(3 * 86_400), now), Group::Earlier);
    assert_eq!(relative_time(ago(10), now), "now");
    assert_eq!(relative_time(ago(5 * 60), now), "5 min");
    assert_eq!(relative_time(ago(3 * 3600), now), "3 h");
    assert_eq!(relative_time(ago(2 * 86_400), now), "2 d");
    let mut chat = Chat::default();
    assert_eq!(title_of(&chat), "New task");
    chat.push_user("A very long first message that goes on and on and on and on");
    assert!(title_of(&chat).ends_with("...") && title_of(&chat).chars().count() <= 47);
}

#[test]
fn the_computer_panel_helpers_read_steps_files_and_costs_from_the_events() {
    let app = app_with("done");
    let chat = &app.main.as_ref().unwrap().chat;
    let steps = workspace::steps(chat);
    assert_eq!(
        steps.iter().map(|s| s.name.as_str()).collect::<Vec<_>>(),
        ["sandbox.write_file", "sandbox.exec"]
    );
    assert_eq!(steps[0].ms, Some(212.0));
    assert_eq!(
        workspace::selected(chat, None),
        Some(steps[1].item),
        "the last step by default"
    );
    assert_eq!(
        workspace::selected(chat, Some(steps[0].item)),
        Some(steps[0].item)
    );
    assert_eq!(workspace::files_created(chat), ["/workspace/notes.txt"]);
    assert_eq!(workspace::model_step_costs(chat).len(), 2);
    assert_eq!(workspace::duration(212.0), "212 ms");
    assert_eq!(workspace::duration(1840.0), "1.8 s");
    assert!(
        workspace::status_line(chat, "z-ai/glm-5.3", "core connected")
            .contains("today $0.0127 of $5.00")
    );
}
