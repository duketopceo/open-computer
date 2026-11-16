//! Headless tests of every Settings section on fakes: the data is what a core answers, built by `apply_settings_fixture`.

use std::sync::Arc;

use rune::runtime::ElementInfo;
use rune::{Env, Runtime};
use shell_core::secrets::InMemorySecrets;
use shell_core::settings_data::{Load, MemoryStats, SpendReport};

use super::settings_page::{CONTENT_MAX, NAV_W, period_fraction};
use super::view;
use crate::app::App;
use crate::main_app::{Main, Section, Tab};

fn app_in(section: Section) -> App {
    let mut app = App::with_store(Arc::new(InMemorySecrets::new()));
    app.main = Some(Main::new());
    app.apply_settings_fixture();
    let m = app.main.as_mut().unwrap();
    m.tab = Tab::Settings;
    m.section = section;
    app
}

fn runtime(app: App) -> Runtime<App> {
    let mut rt = Runtime::new(app, view, (1440.0, 900.0), Env::new(true, 1.0));
    rt.frame(0.0);
    rt
}

fn find(rt: &Runtime<App>, key: &str) -> Option<ElementInfo> {
    rt.elements()
        .into_iter()
        .find(|e| e.key.as_deref() == Some(key) && !e.exiting)
}

fn has(rt: &Runtime<App>, needle: &str) -> bool {
    rt.elements()
        .into_iter()
        .filter_map(|e| e.text)
        .any(|t| t.contains(needle))
}

fn count_prefix(rt: &Runtime<App>, prefix: &str) -> usize {
    rt.elements()
        .into_iter()
        .filter(|e| !e.exiting && e.key.as_deref().is_some_and(|k| k.starts_with(prefix)))
        .count()
}

#[test]
fn the_page_has_a_220_wide_section_list_with_back_to_chat_and_all_seven_sections() {
    let rt = runtime(app_in(Section::OpenRouter));
    let nav = find(&rt, "settings-nav").expect("the section list");
    assert!(
        (nav.rect[2] - NAV_W).abs() < 0.5,
        "about 220 wide: {:?}",
        nav.rect
    );
    assert!(has(&rt, "Back to chat"));
    for sec in Section::ALL {
        assert!(
            find(&rt, &format!("settings-nav-{}", sec.id())).is_some(),
            "{}",
            sec.label()
        );
    }
    let body = find(&rt, "section-openrouter").expect("one scrolling column");
    assert!(
        body.rect[2] <= CONTENT_MAX + 0.5,
        "at most 860 wide: {:?}",
        body.rect
    );
}

#[test]
fn openrouter_shows_the_key_period_usage_a_bar_credits_models_generations_and_links() {
    let rt = runtime(app_in(Section::OpenRouter));
    for needle in [
        "$50.00", "monthly", "$12.50", "$1.10", "$7.70", "$37.50", "$82.29", "paid",
    ] {
        assert!(has(&rt, needle), "{needle}");
    }
    assert!(find(&rt, "or-period-fill").is_some(), "the period bar");
    assert!(has(
        &rt,
        "OpenRouter only shows account credits to management keys"
    ));
    assert_eq!(count_prefix(&rt, "model-"), 6, "a row per model");
    assert!(find(&rt, "sort-prompt").is_some() && find(&rt, "sort-name").is_some());
    assert_eq!(count_prefix(&rt, "generation-"), 3);
    assert!(has(&rt, "2026-11-15 12:04"));
    for link in [
        "or-link-activity",
        "or-link-keys",
        "or-link-credits",
        "or-link-models",
    ] {
        assert!(find(&rt, link).is_some(), "{link}");
    }
    assert!(find(&rt, "or-connect").is_some());
    let key = shell_core::openrouter::KeyInfo {
        limit: Some(50.0),
        limit_remaining: Some(12.5),
        ..Default::default()
    };
    assert_eq!(period_fraction(&key), Some(0.75));
    assert_eq!(
        period_fraction(&shell_core::openrouter::KeyInfo::default()),
        None
    );
}

#[test]
fn the_models_table_searches_sorts_and_sets_the_defaults() {
    let mut app = app_in(Section::OpenRouter);
    app.models_query.replace_text("flash");
    let rt = runtime(app);
    assert_eq!(
        count_prefix(&rt, "model-"),
        2,
        "only the flash models match"
    );
    assert!(has(&rt, "z-ai/glm-5.3-flash") && has(&rt, "google/gemini-flash"));
    assert!(!has(&rt, "anthropic/claude-sonnet"));

    let mut app = app_in(Section::OpenRouter);
    assert_eq!(
        app.models_sort,
        (shell_core::settings_data::ModelSort::Prompt, true)
    );
    app.sort_models(shell_core::settings_data::ModelSort::Prompt);
    assert!(!app.models_sort.1, "the same column again flips the order");
    app.sort_models(shell_core::settings_data::ModelSort::Context);
    assert_eq!(
        app.models_sort,
        (shell_core::settings_data::ModelSort::Context, true)
    );
    // With no core connected, choosing a model says so instead of failing silently.
    app.use_model("default_model", "z-ai/glm-5.3");
    assert!(app.notice.as_deref().unwrap().starts_with("Not connected"));
}

#[test]
fn openrouter_loading_empty_and_error_states_say_what_is_going_on() {
    let mut loading = app_in(Section::OpenRouter);
    loading.or_account = None;
    loading.or_busy = true;
    assert!(
        find(&runtime(loading), "skeleton").is_some(),
        "placeholders while it loads"
    );

    let mut idle = app_in(Section::OpenRouter);
    idle.or_account = None;
    let rt = runtime(idle);
    assert!(has(&rt, "Why it is empty") && has(&rt, "What to do"));

    let mut failed = app_in(Section::OpenRouter);
    failed.or_account.as_mut().unwrap().problems = vec!["OpenRouter rejected the key.".to_owned()];
    let rt = runtime(failed);
    assert!(has(&rt, "OpenRouter rejected the key.") && has(&rt, "Retry"));
}

#[test]
fn cloudflare_shows_account_core_bindings_gateway_access_redeploy_and_docs() {
    let rt = runtime(app_in(Section::Cloudflare));
    for needle in [
        "you@example.com",
        "account, workers, d1, containers",
        "open-computer-core",
        "0.1.0",
        "Cloudflare data centre SJC",
        "2026-11-15 19:20",
    ] {
        assert!(has(&rt, needle), "{needle}");
    }
    assert_eq!(count_prefix(&rt, "cf-bindings"), 1);
    assert!(
        has(&rt, "D1 database")
            && has(&rt, "Containers (sandbox)")
            && has(&rt, "Secrets Store: OpenRouter key")
    );
    assert!(
        has(&rt, "Set up, not in use")
            && has(&rt, "needs authentication")
            && has(&rt, "direct to OpenRouter")
    );
    assert!(
        has(&rt, "service-token support"),
        "Access says why it is off"
    );
    assert!(find(&rt, "cf-redeploy-button").is_some());
    for doc in [
        "doc-workers",
        "doc-d1",
        "doc-r2",
        "doc-aig",
        "doc-access",
        "doc-containers",
        "doc-runbook",
    ] {
        assert!(find(&rt, doc).is_some(), "{doc}");
    }
    assert!(
        find(&rt, "cf-sign-in").is_none() && has(&rt, "Switch account"),
        "signed in, so switch and sign out"
    );
}

#[test]
fn cloudflare_sections_show_skeletons_while_loading_and_a_retry_when_they_fail() {
    let mut a = app_in(Section::Cloudflare);
    a.bindings = Load::Loading;
    a.core_info = Load::Loading;
    assert!(find(&runtime(a), "skeleton").is_some());

    let mut b = app_in(Section::Cloudflare);
    b.bindings = Load::Failed("The core could not be reached.".into());
    b.core_info = Load::Failed("The core could not be reached.".into());
    let rt = runtime(b);
    assert!(find(&rt, "cf-bindings-error").is_some() && find(&rt, "cf-core-error").is_some());
    assert!(has(&rt, "Retry"));

    let mut c = app_in(Section::Cloudflare);
    c.cf_state = None;
    c.cf_hint_signed_in = false;
    c.cf_access = None;
    let rt = runtime(c);
    assert!(
        find(&rt, "cf-sign-in").is_some(),
        "not signed in: sign in is offered"
    );
    assert!(has(&rt, "Why it is empty"));
}

#[test]
fn the_agent_section_lists_the_harnesses_with_install_state_and_the_permissions_note() {
    let rt = runtime(app_in(Section::Agent));
    assert!(find(&rt, "set-harness").is_some());
    assert!(has(&rt, "Built-in") && has(&rt, "Hermes") && has(&rt, "OpenCode"));
    assert!(has(&rt, "own permissions"));
    assert!(has(&rt, "Open Codex"));
}

#[test]
fn models_and_spend_show_the_cap_the_spend_a_14_day_chart_and_the_policy() {
    let rt = runtime(app_in(Section::Models));
    assert!(has(&rt, "Default model") && has(&rt, "Worker model") && has(&rt, "Daily spend cap"));
    assert!(has(&rt, "Today") && has(&rt, "Last 7 days") && has(&rt, "This month"));
    assert!(find(&rt, "spend-chart").is_some());
    assert_eq!(count_prefix(&rt, "day-"), 14, "one bar per day");
    assert!(has(&rt, "No IT policy file is in effect"));

    // Nothing spent: the chart is replaced by an empty state that says why.
    let mut app = app_in(Section::Models);
    app.spend = Load::Ready(SpendReport {
        days: (0..14)
            .map(|i| (format!("2026-11-{:02}", i + 1), 0.0))
            .collect(),
        ..Default::default()
    });
    let rt = runtime(app);
    assert!(find(&rt, "spend-chart").is_none() && has(&rt, "A bar for each of the last 14 days."));

    let mut app = app_in(Section::Models);
    app.spend = Load::Failed("The core answered with an error (500).".into());
    assert!(find(&runtime(app), "ms-spend-error").is_some());
}

#[test]
fn memory_shows_sizes_never_text_asks_before_clearing_and_reports_the_audit_chain() {
    let mut rt = runtime(app_in(Section::Memory));
    assert!(has(&rt, "640 characters") && has(&rt, "Agent notes") && has(&rt, "About you"));
    assert!(has(&rt, "Task summaries") && has(&rt, "2026-11-15 12:05"));
    assert!(has(&rt, "no automatic expiry"));
    assert!(has(&rt, "The audit log verifies"));
    assert!(find(&rt, "mem-confirm").is_none());
    // The first press only asks; Cancel puts it back.
    rt.update(|s, _| s.clear_memory());
    for _ in 0..40 {
        rt.frame(1.0 / 60.0);
    }
    assert!(find(&rt, "mem-confirm").is_some() && has(&rt, "This cannot be undone"));
    rt.update(|s, _| s.cancel_clear());
    for _ in 0..40 {
        rt.frame(1.0 / 60.0);
    }
    assert!(find(&rt, "mem-confirm").is_none());

    let mut app = app_in(Section::Memory);
    app.memory = Load::Ready(MemoryStats::default());
    assert!(has(&runtime(app), "Why it is empty"));
    let mut app = app_in(Section::Memory);
    app.spend = Load::Ready(SpendReport {
        chain_ok: Some(false),
        ..Default::default()
    });
    assert!(has(&runtime(app), "does NOT verify"));
}

#[test]
fn diagnostics_is_a_section_with_the_chain_and_about_shows_versions_and_links() {
    let rt = runtime(app_in(Section::Diagnostics));
    assert!(find(&rt, "chain-header").is_some() && find(&rt, "section-diagnostics").is_some());
    let rt = runtime(app_in(Section::About));
    assert!(has(&rt, env!("CARGO_PKG_VERSION")) && has(&rt, "0.1.0"));
    for link in ["about-repo", "about-license", "about-support"] {
        assert!(find(&rt, link).is_some(), "{link}");
    }
}

#[test]
fn opening_a_task_from_a_generation_finds_this_sessions_task_or_says_it_cannot() {
    let mut app = app_in(Section::OpenRouter);
    app.main.as_mut().unwrap().chat.task_id = Some("t1".to_owned());
    app.open_task("t1");
    assert_eq!(app.main.as_ref().unwrap().tab, Tab::Chat);
    app.open_task("t-from-last-week");
    assert!(app.notice.as_deref().unwrap().contains("earlier session"));
}
