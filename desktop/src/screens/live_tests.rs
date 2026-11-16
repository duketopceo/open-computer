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

// ---------- a locked keyring ----------

use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

/// A keyring that can be locked, counting how often it is asked, with the words a locked omaseal gives.
struct Lockable {
    locked: Arc<AtomicBool>,
    reads: Arc<AtomicUsize>,
    why: &'static str,
}
impl SecretStore for Lockable {
    fn set(&self, _: Slot, _: &str) -> Result<(), SecretError> {
        Ok(())
    }
    fn get(&self, slot: Slot) -> Result<Option<String>, SecretError> {
        self.reads.fetch_add(1, Ordering::Relaxed);
        if self.locked.load(Ordering::Relaxed) {
            return Err(SecretError(format!(
                "Your system keyring refused to read the access token: {}.",
                self.why
            )));
        }
        Ok(matches!(slot, Slot::CloudAccess).then(|| "keyring-token".to_owned()))
    }
    fn delete(&self, _: Slot) -> Result<(), SecretError> {
        Ok(())
    }
}

fn locked_app(
    why: &'static str,
    locked: bool,
    name: &str,
) -> (App, Arc<AtomicBool>, Arc<AtomicUsize>, std::path::PathBuf) {
    let (flag, reads) = (
        Arc::new(AtomicBool::new(locked)),
        Arc::new(AtomicUsize::new(0)),
    );
    let store = Arc::new(shell_core::secrets::ResilientSecrets::new(Box::new(
        Lockable {
            locked: flag.clone(),
            reads: reads.clone(),
            why,
        },
    )));
    let dir = temp_dir(name);
    let mut app = App::with_store(store);
    app.config_path = dir.join("desktop.json");
    app.saved_core_path = dir.join("cloud.json");
    app.onboarding.set_cloud_url("http://127.0.0.1:9");
    let mut m = Main::new();
    m.endpoint = Some(shell_core::chat::Endpoint::new("http://127.0.0.1:9", "old"));
    app.main = Some(m);
    (app, flag, reads, dir)
}

fn write_cloud_json(dir: &std::path::Path, mode: u32) {
    use std::os::unix::fs::PermissionsExt;
    let p = dir.join("cloud.json");
    std::fs::write(
        &p,
        format!(r#"{{"url":"http://127.0.0.1:9","token":"{TOKEN}"}}"#),
    )
    .unwrap();
    std::fs::set_permissions(&p, std::fs::Permissions::from_mode(mode)).unwrap();
}

#[test]
fn a_locked_dismissed_or_refused_keyring_gives_one_clear_banner_with_retry_and_never_a_raw_401() {
    for why in [
        "SS error: prompt dismissed",
        "the keyring is locked",
        "access refused by the user",
    ] {
        let (mut app, _flag, _reads, dir) = locked_app(why, true, "lockedbanner");
        app.refresh_token();
        let banner = app
            .keyring_banner()
            .unwrap_or_else(|| panic!("{why}: a banner"));
        assert!(
            banner.starts_with("Your keyring is locked") && banner.contains("click Retry"),
            "{banner}"
        );
        assert!(!banner.contains("401") && !banner.contains("refused the access token"));
        let rt = runtime(app);
        assert!(
            find(&rt, "keyring-banner")
                && find(&rt, "keyring-retry")
                && find(&rt, "keyring-diagnostics"),
            "{why}"
        );
        assert!(has(&rt, "Unlock it") && has(&rt, "Retry"));
        // A refused token is explained by the banner, not shown as a 401.
        let mut app = locked_app(why, true, "lockedbanner2").0;
        app.refresh_token();
        let shown = app.after_core_error(shell_core::chat::REFUSED);
        assert!(
            shown.contains("keyring is locked") && !shown.contains("refused the access token"),
            "{shown}"
        );
        let _ = std::fs::remove_dir_all(dir);
    }
}

#[test]
fn retry_reads_the_keyring_again_and_the_banner_goes_when_it_is_unlocked() {
    let (mut app, flag, reads, dir) = locked_app("SS error: prompt dismissed", true, "retry");
    app.refresh_token();
    assert!(app.keyring_banner().is_some());
    let asked = reads.load(Ordering::Relaxed);
    // While locked, nothing asks the keyring again (no prompt per request).
    for _ in 0..10 {
        app.refresh_token();
    }
    assert_eq!(
        reads.load(Ordering::Relaxed),
        asked,
        "a failed read is not repeated until Retry"
    );
    flag.store(false, Ordering::Relaxed);
    app.retry_keyring();
    assert_eq!(app.keyring_banner(), None);
    assert!(!app.file_fallback);
    assert!(
        reads.load(Ordering::Relaxed) > asked,
        "Retry asked the keyring"
    );
    assert_eq!(
        app.core_check,
        CoreCheck::Checking,
        "and the core was asked with the token it gave"
    );
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn the_token_is_read_once_per_session_however_many_times_it_is_needed() {
    let (mut app, _flag, reads, dir) = locked_app("unused", false, "cache");
    for _ in 0..25 {
        app.refresh_token();
        let _ = app.store.get(Slot::CloudAccess);
    }
    assert!(
        reads.load(Ordering::Relaxed) <= 2,
        "one read per secret, not one per request: {}",
        reads.load(Ordering::Relaxed)
    );
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn a_refusal_in_the_middle_of_a_session_reads_the_token_again_at_most_once_a_minute() {
    let (mut app, _flag, _reads, dir) = locked_app("unused", false, "midsession");
    assert!(app.last_recheck.is_none());
    let said = app.after_core_error(shell_core::chat::REFUSED);
    assert!(said.contains("checked again"), "{said}");
    assert!(app.last_recheck.is_some());
    assert_eq!(
        app.core_check,
        CoreCheck::Checking,
        "the same check as at start ran"
    );
    // A second refusal within the minute does not start another check.
    app.core_check = CoreCheck::Unknown;
    app.after_core_error(shell_core::chat::REFUSED);
    assert_eq!(app.core_check, CoreCheck::Unknown);
    // A minute later it does.
    app.last_recheck = Some(std::time::Instant::now() - std::time::Duration::from_secs(61));
    app.after_core_error(shell_core::chat::REFUSED);
    assert_eq!(app.core_check, CoreCheck::Checking);
    // Other errors pass through untouched and trigger nothing.
    app.core_check = CoreCheck::Unknown;
    app.last_recheck = None;
    assert_eq!(
        app.after_core_error("The core had an error. Try again."),
        "The core had an error. Try again."
    );
    assert_eq!(app.core_check, CoreCheck::Unknown);
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn with_the_keyring_locked_a_private_cloud_json_stands_in_and_the_banner_says_so() {
    let (mut app, _flag, _reads, dir) = locked_app("SS error: prompt dismissed", true, "fallback");
    write_cloud_json(&dir, 0o600);
    app.refresh_token();
    assert!(app.file_fallback);
    assert!(
        app.store.is_set(Slot::CloudAccess),
        "this session has the token from cloud.json"
    );
    let banner = app.keyring_banner().unwrap();
    assert!(
        banner.contains("saved in cloud.json") && banner.contains("readable only by you"),
        "{banner}"
    );
    // Unlocking and pressing Retry goes back to the keyring.
    let (mut app2, flag, _r, dir2) = locked_app("SS error: prompt dismissed", true, "fallback2");
    write_cloud_json(&dir2, 0o600);
    app2.refresh_token();
    flag.store(false, Ordering::Relaxed);
    app2.retry_keyring();
    assert!(!app2.file_fallback && app2.keyring_banner().is_none());
    let _ = (std::fs::remove_dir_all(dir), std::fs::remove_dir_all(dir2));
}

#[test]
fn a_cloud_json_that_others_can_read_is_never_used_as_a_stand_in() {
    for mode in [0o644, 0o640, 0o604] {
        let (mut app, _flag, _reads, dir) =
            locked_app("SS error: prompt dismissed", true, "worldreadable");
        write_cloud_json(&dir, mode);
        assert!(!app.file_fallback_allowed(), "{mode:o}");
        app.refresh_token();
        assert!(
            !app.file_fallback && !app.store.is_set(Slot::CloudAccess),
            "{mode:o}"
        );
        assert!(app.keyring_banner().unwrap().contains("click Retry"));
        let _ = std::fs::remove_dir_all(dir);
    }
}

#[test]
fn an_endpoint_without_a_token_sends_nothing_and_says_why() {
    let ep = shell_core::chat::Endpoint::new("http://127.0.0.1:9", "");
    assert_eq!(ep.bindings().unwrap_err(), shell_core::chat::NO_TOKEN);
    assert_eq!(
        ep.get("/v1/health").unwrap_err(),
        shell_core::chat::NO_TOKEN
    );
    assert_eq!(
        ep.raw("GET", "/v1/audit/export").unwrap_err(),
        shell_core::chat::NO_TOKEN
    );
}
