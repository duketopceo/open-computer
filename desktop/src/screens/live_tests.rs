//! Tests for the live-failure fixes: a keyring that refuses is shown and handled, the address is saved either way, the core chip
//! reflects a real check, history survives a restart, and Copy diagnostics redacts. All on fakes and temporary folders.

use std::sync::Arc;

use rune::{Env, Runtime};
use shell_core::secrets::{InMemorySecrets, SecretError, SecretStore, Slot};

use super::view;
use crate::app::{App, CoreCheck};
use crate::main_app::{Main, Tab};

const TOKEN: &str = "tok-live-test-sentinel-4242";

struct Refusing;
impl SecretStore for Refusing {
    fn set(&self, _: Slot, _: &str) -> Result<(), SecretError> {
        Err(SecretError(
            "Your system keyring refused to save the access token: no answer after 8 s.".into(),
        ))
    }
    fn get(&self, _: Slot) -> Result<Option<String>, SecretError> {
        Ok(None)
    }
    fn delete(&self, _: Slot) -> Result<(), SecretError> {
        Ok(())
    }
}

fn temp_dir(name: &str) -> std::path::PathBuf {
    let d = std::env::temp_dir().join(format!("oc-live-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    d
}

/// An app whose config and saved connection live in a temporary folder holding a `cloud.json`.
fn app_with_saved_core(store: Arc<dyn SecretStore>, name: &str) -> (App, std::path::PathBuf) {
    let dir = temp_dir(name);
    std::fs::write(
        dir.join("cloud.json"),
        format!(r#"{{"url":"http://127.0.0.1:9","token":"{TOKEN}"}}"#),
    )
    .unwrap();
    let mut app = App::with_store(store);
    app.config_path = dir.join("desktop.json");
    app.saved_core_path = dir.join("cloud.json");
    (app, dir)
}

fn runtime(app: App) -> Runtime<App> {
    let mut rt = Runtime::new(app, view, (1440.0, 900.0), Env::new(true, 1.0));
    rt.frame(0.0);
    rt
}

fn has(rt: &Runtime<App>, needle: &str) -> bool {
    rt.elements()
        .into_iter()
        .filter_map(|e| e.text)
        .any(|t| t.contains(needle))
}

fn find(rt: &Runtime<App>, key: &str) -> bool {
    rt.elements()
        .into_iter()
        .any(|e| e.key.as_deref() == Some(key) && !e.exiting)
}

#[test]
fn a_refusing_keyring_is_shown_with_its_reason_the_address_is_saved_and_the_token_is_not_kept_behind_the_back()
 {
    let (mut app, dir) = app_with_saved_core(Arc::new(Refusing), "refuse");
    let connected = app.auto_connect_saved();
    assert!(!connected, "nothing was connected without the person's yes");
    assert!(
        app.keyring_problem
            .as_deref()
            .unwrap()
            .starts_with("Your system keyring refused to save the access token")
    );
    assert!(app.file_token_offer);
    // The address is in desktop.json whatever the keyring said.
    let saved = shell_core::config::load(&dir.join("desktop.json"));
    assert_eq!(saved.cloud_url, "http://127.0.0.1:9");
    assert!(!saved.file_token_ok, "consent is not assumed");
    assert!(
        !app.store.is_set(Slot::CloudAccess),
        "no silent in-memory copy"
    );
    // The screen says why and offers the saved token.
    let rt = runtime(app);
    assert!(has(
        &rt,
        "refused to save the access token: no answer after 8 s"
    ));
    assert!(find(&rt, "use-saved-token") && find(&rt, "keyring-problem"));
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn agreeing_uses_the_saved_token_for_this_session_and_remembers_the_choice_without_copying_the_token()
 {
    let store = Arc::new(shell_core::secrets::ResilientSecrets::new(Box::new(
        Refusing,
    )));
    let (mut app, dir) = app_with_saved_core(store, "consent");
    app.auto_connect_saved();
    app.use_saved_token();
    assert!(!app.file_token_offer);
    assert!(
        app.store.is_set(Slot::CloudAccess),
        "usable for this session"
    );
    assert!(
        app.core_line
            .as_deref()
            .unwrap()
            .contains("because your keyring refused it")
    );
    let saved = shell_core::config::load(&dir.join("desktop.json"));
    assert!(saved.file_token_ok);
    let text = std::fs::read_to_string(dir.join("desktop.json")).unwrap();
    assert!(
        !text.contains(TOKEN),
        "the token is read from cloud.json each time, never copied"
    );
    // The next start applies the remembered choice without asking again.
    let store2 = Arc::new(shell_core::secrets::ResilientSecrets::new(Box::new(
        Refusing,
    )));
    let mut again = App::with_store(store2);
    again.config_path = dir.join("desktop.json");
    again.saved_core_path = dir.join("cloud.json");
    assert!(again.auto_connect_saved());
    assert!(!again.file_token_offer && again.store.is_set(Slot::CloudAccess));
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn a_working_keyring_adopts_the_token_and_saves_the_address() {
    let store = Arc::new(InMemorySecrets::new());
    let (mut app, dir) = app_with_saved_core(store.clone(), "ok");
    assert!(app.auto_connect_saved());
    assert!(store.is_set(Slot::CloudAccess));
    assert_eq!(app.keyring_problem, None);
    assert_eq!(
        shell_core::config::load(&dir.join("desktop.json")).cloud_url,
        "http://127.0.0.1:9"
    );
    assert!(!format!("{:?}", app.core_line).contains(TOKEN));
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn the_core_chip_says_connected_only_after_a_real_answer() {
    let make = |check: CoreCheck| {
        let mut app = App::with_store(Arc::new(InMemorySecrets::new()));
        let mut m = Main::new();
        m.tab = Tab::Chat;
        m.endpoint = Some(shell_core::chat::Endpoint::new("http://127.0.0.1:9", "t"));
        app.main = Some(m);
        app.core_check = check;
        runtime(app)
    };
    assert!(has(&make(CoreCheck::Ok), "core connected"));
    assert!(has(&make(CoreCheck::Checking), "checking the core"));
    assert!(!has(&make(CoreCheck::Unknown), "core connected"));
    let failed = make(CoreCheck::Failed(
        "The core could not be reached: Connection refused.".into(),
    ));
    assert!(has(
        &failed,
        "core not answering: The core could not be reached"
    ));
    assert!(!has(&failed, "core connected"));
}

#[test]
fn a_real_check_against_a_dead_address_reports_the_reason() {
    let mut app = App::with_store(Arc::new(InMemorySecrets::new()));
    app.on_core_checked(Err(
        "The core could not be reached: Connection refused.".into()
    ));
    assert_eq!(
        app.core_check,
        CoreCheck::Failed("The core could not be reached: Connection refused.".into())
    );
    assert!(
        app.core_line
            .as_deref()
            .unwrap()
            .contains("Connection refused")
    );
    app.on_core_checked(Ok(()));
    assert_eq!(app.core_check, CoreCheck::Ok);
}

#[test]
fn history_survives_a_restart_and_an_interrupted_task_is_shown_as_interrupted() {
    let dir = temp_dir("history");
    shell_core::history::append_user(&dir, "app-1-1", "first task");
    shell_core::history::append(
        &dir,
        "app-1-1",
        &serde_json::json!({"type":"run","status":"running"}),
    );
    shell_core::history::append(
        &dir,
        "app-1-1",
        &serde_json::json!({"type":"message.done","text":"working on it"}),
    );
    shell_core::history::append_user(&dir, "app-1-2", "second task");
    shell_core::history::append(
        &dir,
        "app-1-2",
        &serde_json::json!({"type":"run","status":"done"}),
    );
    let mut app = App::with_store(Arc::new(InMemorySecrets::new()));
    app.history_dir = Some(dir.clone());
    let m = app.new_main();
    assert_eq!(m.threads.len(), 2);
    let titles: Vec<String> = m
        .threads
        .iter()
        .map(|t| crate::main_app::title_of(&t.chat))
        .collect();
    assert!(
        titles.contains(&"first task".to_owned()) && titles.contains(&"second task".to_owned())
    );
    let first = m
        .threads
        .iter()
        .find(|t| t.session_id == "app-1-1")
        .unwrap();
    assert!(
        !first.chat.is_busy(),
        "no thread claims to still be working after a restart"
    );
    // The sidebar shows them.
    app.main = Some(m);
    let rt = runtime(app);
    assert!(has(&rt, "first task") && has(&rt, "second task"));
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn copy_diagnostics_puts_redacted_text_on_the_clipboard_request() {
    let mut app = App::with_store(Arc::new(InMemorySecrets::new()));
    app.keyring_problem =
        Some("Your system keyring refused to save the access token: Bearer abc123def456".into());
    app.copy_diagnostics();
    let text = app.copy_request.clone().unwrap();
    assert!(text.contains("diagnostics") && app.diag_copied);
    // The keyring line is in the header as the app has it; the log tail is redacted by shell-core.
    assert_eq!(
        shell_core::applog::redact("Authorization: Bearer abc123def456"),
        "Authorization: Bearer [hidden]"
    );
}

#[test]
fn the_about_page_has_the_log_and_the_copy_button_and_the_agent_page_says_how_model_access_works() {
    let mut app = App::with_store(Arc::new(InMemorySecrets::new()));
    app.main = Some(Main::new());
    {
        let m = app.main.as_mut().unwrap();
        m.tab = Tab::Settings;
        m.section = crate::main_app::Section::About;
    }
    let rt = runtime(app);
    assert!(find(&rt, "about-copy-diagnostics") && has(&rt, "Copy diagnostics"));

    let mut app = App::with_store(Arc::new(InMemorySecrets::new()));
    app.main = Some(Main::new());
    {
        let m = app.main.as_mut().unwrap();
        m.tab = Tab::Settings;
        m.section = crate::main_app::Section::Agent;
    }
    // No real key on this computer: the page says the core holds it and how to configure the agent.
    let rt = runtime(app);
    assert!(has(&rt, "held by your core") && has(&rt, "hermes setup"));
    assert!(!find(&rt, "harness-key-toggle"));

    // A real key here: the opt-in toggle appears, and the key reaches the agents only after the person opts in.
    let store = Arc::new(InMemorySecrets::new());
    store
        .set(Slot::ModelKey, "sk-or-v1-realkeyforthetest0000")
        .unwrap();
    let mut app = App::with_store(store);
    app.config_path = temp_dir("agent").join("desktop.json");
    app.main = Some(Main::new());
    {
        let m = app.main.as_mut().unwrap();
        m.tab = Tab::Settings;
        m.section = crate::main_app::Section::Agent;
    }
    assert!(app.harness_env().is_empty(), "off until the person opts in");
    app.set_harness_key(true);
    assert_eq!(
        app.harness_env(),
        vec![(
            "OPENROUTER_API_KEY".to_owned(),
            "sk-or-v1-realkeyforthetest0000".to_owned()
        )]
    );
    assert!(find(&runtime(app), "harness-key-toggle"));
}

#[test]
fn each_local_agent_can_use_open_computers_model_or_its_own_default_and_the_choice_is_saved() {
    use shell_core::harness::Harness;
    let dir = temp_dir("agentmodel");
    let mut app = App::with_store(Arc::new(InMemorySecrets::new()));
    app.config_path = dir.join("desktop.json");
    let mut m = Main::new();
    m.settings = Some(serde_json::json!({"default_model": "z-ai/glm-5.3"}));
    app.main = Some(m);
    // Open Computer's model is the default for both agents.
    assert_eq!(
        app.harness_model(Harness::OpenCode).as_deref(),
        Some("z-ai/glm-5.3")
    );
    assert_eq!(
        app.harness_model(Harness::Hermes).as_deref(),
        Some("z-ai/glm-5.3")
    );
    app.set_harness_native(Harness::OpenCode, true);
    assert_eq!(
        app.harness_model(Harness::OpenCode),
        None,
        "the agent's own default"
    );
    assert_eq!(
        app.harness_model(Harness::Hermes).as_deref(),
        Some("z-ai/glm-5.3")
    );
    assert_eq!(
        shell_core::config::load(&dir.join("desktop.json")).harness_native_model,
        vec!["opencode".to_owned()]
    );
    app.set_harness_native(Harness::OpenCode, false);
    assert!(app.harness_native.is_empty());
    // The Settings page shows the choice per agent, and none for Built-in.
    {
        let m = app.main.as_mut().unwrap();
        m.tab = Tab::Settings;
        m.section = crate::main_app::Section::Agent;
    }
    let rt = runtime(app);
    for key in [
        "harness-model-oc-hermes",
        "harness-model-native-hermes",
        "harness-model-oc-opencode",
        "harness-model-native-opencode",
    ] {
        assert!(find(&rt, key), "{key}");
    }
    assert!(!find(&rt, "harness-model-oc-builtin"));
    assert!(has(&rt, "--provider openrouter") && has(&rt, "openrouter/<model>"));
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn a_refused_keyring_token_is_replaced_from_the_saved_file_once_and_never_shown() {
    let store = Arc::new(InMemorySecrets::new());
    store.set(Slot::CloudAccess, "stale-old-paste").unwrap();
    let (mut app, dir) = app_with_saved_core(store.clone(), "stale");
    app.onboarding.set_cloud_url("http://127.0.0.1:9");
    app.readopt_saved_token();
    assert!(app.readopt_tried);
    assert_eq!(
        store.get(Slot::CloudAccess).unwrap().as_deref(),
        Some(TOKEN),
        "the file's token replaced the stale one in the keyring"
    );
    assert!(!format!("{:?} {:?}", app.core_line, app.keyring_problem).contains("stale-old-paste"));
    // Only the saved file's token for the same core qualifies; nothing else is ever offered.
    assert!(shell_core::autoconnect::saved_token_differs(
        &dir.join("cloud.json"),
        "http://127.0.0.1:9/",
        "stale-old-paste"
    ));
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn when_the_keyring_refuses_the_replacement_the_person_is_asked_and_a_second_401_says_what_to_do() {
    let store = Arc::new(shell_core::secrets::ResilientSecrets::new(Box::new(
        Refusing,
    )));
    let (mut app, dir) = app_with_saved_core(store, "stale2");
    app.onboarding.set_cloud_url("http://127.0.0.1:9");
    app.readopt_saved_token();
    assert!(
        app.file_token_offer,
        "the saved file is offered, not used behind the person's back"
    );
    assert!(
        app.keyring_problem
            .as_deref()
            .unwrap()
            .contains("refused to save")
    );
    // A refusal that cannot be repaired says what to do.
    app.on_core_checked(Err(shell_core::autoconnect::REFUSED_SAVED.to_owned()));
    assert_eq!(
        app.core_check,
        CoreCheck::Failed(
            "The core refused the saved token. Run setup or paste the token under Advanced.".into()
        )
    );
    let _ = std::fs::remove_dir_all(dir);
}

fn app_in_a_busy_conversation() -> App {
    let mut app = App::with_store(Arc::new(InMemorySecrets::new()));
    let mut m = Main::new();
    m.endpoint = Some(shell_core::chat::Endpoint::new("http://127.0.0.1:9", "t"));
    m.chat.push_user("do the second thing");
    m.chat.task_id = Some("task-1".to_owned());
    let session = m.session_id.clone();
    app.main = Some(m);
    app.last_sent = Some((session, "do the second thing".to_owned()));
    app
}

#[test]
fn a_busy_conversation_offers_stop_it_or_a_new_conversation_and_does_not_report_a_failure() {
    let mut app = app_in_a_busy_conversation();
    let session = app.main.as_ref().unwrap().session_id.clone();
    // The core's 409 session_busy arrives as the chat's failure message.
    app.busy_conflict = Some(crate::app::BusyConflict {
        session,
        goal: "do the second thing".into(),
    });
    let rt = runtime(app);
    assert!(find(&rt, "busy-card") && find(&rt, "busy-stop") && find(&rt, "busy-new"));
    assert!(has(&rt, "A task is still running in this conversation."));
}

#[test]
fn starting_a_new_conversation_keeps_the_busy_one_and_sends_the_goal_in_a_fresh_session() {
    let mut app = app_in_a_busy_conversation();
    let before = app.main.as_ref().unwrap().session_id.clone();
    app.busy_conflict = Some(crate::app::BusyConflict {
        session: before.clone(),
        goal: "do the second thing".into(),
    });
    app.new_conversation_for_busy();
    let m = app.main.as_ref().unwrap();
    assert_ne!(m.session_id, before, "a fresh session");
    assert_eq!(
        m.threads.len(),
        1,
        "the busy conversation stays in the list"
    );
    assert_eq!(m.threads[0].session_id, before);
    assert!(app.busy_conflict.is_none());
    assert_eq!(
        app.last_sent.as_ref().map(|(s, g)| (s.clone(), g.clone())),
        Some((m.session_id.clone(), "do the second thing".to_owned()))
    );
    assert!(
        m.chat
            .items
            .iter()
            .any(|i| matches!(i, shell_core::chat::Item::User(t) if t == "do the second thing"))
    );
}

#[test]
fn stop_it_needs_to_know_which_task_and_never_cancels_a_guess() {
    let mut app = app_in_a_busy_conversation();
    let session = app.main.as_ref().unwrap().session_id.clone();
    app.main.as_mut().unwrap().chat.task_id = None;
    app.busy_conflict = Some(crate::app::BusyConflict {
        session,
        goal: "g".into(),
    });
    app.stop_busy_and_retry();
    assert!(
        app.notice
            .as_deref()
            .unwrap()
            .contains("Start a new conversation instead")
    );
    assert!(app.busy_conflict.is_some(), "the choice is still open");
}

#[test]
fn a_missing_key_says_how_to_fix_it_in_the_chat() {
    let mut app = app_in_a_busy_conversation();
    let session = app.main.as_ref().unwrap().session_id.clone();
    app.on_data_for_test_chat_failed(&session, shell_core::chat::KEY_MISSING);
    let m = app.main.as_ref().unwrap();
    assert!(
        m.chat
            .items
            .iter()
            .any(|i| matches!(i, shell_core::chat::Item::Note(n) if n.contains("connect one")))
    );
    assert!(app.busy_conflict.is_none());
}

#[test]
fn earlier_conversations_the_core_finished_are_marked_so_none_stays_interrupted() {
    let dir = temp_dir("reconcile");
    shell_core::history::append_user(&dir, "app-9-1", "long task");
    shell_core::history::append(
        &dir,
        "app-9-1",
        &serde_json::json!({"type":"run","status":"running","task_id":"t-done"}),
    );
    shell_core::history::append_user(&dir, "app-9-2", "other task");
    shell_core::history::append(
        &dir,
        "app-9-2",
        &serde_json::json!({"type":"run","status":"running","task_id":"t-still"}),
    );
    let mut app = App::with_store(Arc::new(InMemorySecrets::new()));
    app.history_dir = Some(dir.clone());
    app.main = Some(app.new_main());
    let status = |app: &App, id: &str| {
        app.main
            .as_ref()
            .unwrap()
            .threads
            .iter()
            .find(|t| t.session_id == id)
            .unwrap()
            .chat
            .status
            .clone()
    };
    assert_eq!(
        status(&app, "app-9-1"),
        "cancelled",
        "interrupted until the core says otherwise"
    );
    let statuses: std::collections::HashMap<String, String> = [
        ("t-done".to_owned(), "done".to_owned()),
        ("t-still".to_owned(), "running".to_owned()),
    ]
    .into();
    app.apply_run_statuses(&statuses);
    assert_eq!(status(&app, "app-9-1"), "done");
    assert!(app.main.as_ref().unwrap().threads.iter().find(|t| t.session_id == "app-9-1").unwrap().chat.items.iter().any(
        |i| matches!(i, shell_core::chat::Item::Note(n) if n.contains("finished while the app was closed"))
    ));
    assert_eq!(
        status(&app, "app-9-2"),
        "cancelled",
        "the core still says running: it stays interrupted"
    );
    let _ = std::fs::remove_dir_all(dir);
}
