//! Headless tests of the Get started screen, the OpenRouter step and the live test rows, on fakes: an in-memory secret store
//! and a rune runtime with no window.

use std::sync::Arc;
use std::time::{Duration, Instant};

use rune::{Env, Runtime};
use shell_core::autoconnect::{CoreStep, FoundCore};
use shell_core::diagnostics::{Status, initial_rows};
use shell_core::onboarding::Step;
use shell_core::secrets::{InMemorySecrets, SecretStore, Slot};

use super::view;
use crate::app::App;

fn app() -> App {
    App::with_store(Arc::new(InMemorySecrets::new()))
}

fn texts(app: App) -> Vec<String> {
    let mut rt = Runtime::new(app, view, (900.0, 900.0), Env::new(true, 1.0));
    rt.frame(0.0);
    rt.elements().into_iter().filter_map(|e| e.text).collect()
}

fn keys(app: App) -> Vec<String> {
    let mut rt = Runtime::new(app, view, (900.0, 900.0), Env::new(true, 1.0));
    rt.frame(0.0);
    rt.elements().into_iter().filter_map(|e| e.key).collect()
}

fn has(t: &[String], needle: &str) -> bool {
    t.iter().any(|s| s.contains(needle))
}

fn found() -> FoundCore {
    FoundCore::new(
        "open-computer-core",
        "Account",
        "https://open-computer-core.me.workers.dev",
        "0123456789abcdef".repeat(2),
    )
}

#[test]
fn step_two_offers_sign_in_then_connect_or_set_up_and_never_asks_for_an_address_first() {
    // Nobody signed in: sign in is the action, and there is no setup button yet.
    let t = texts(app());
    assert!(has(&t, "Sign in with Cloudflare"));
    assert!(!has(&t, "Set up Open Computer in your Cloudflare account"));
    assert!(
        !has(&t, "Cloud core address"),
        "typing lives under Advanced"
    );

    // Signed in and no core on the account: one click sets one up.
    let mut a = app();
    a.cf_hint_signed_in = true;
    assert_eq!(a.core_step(), CoreStep::CanSetUp);
    let t = texts(a);
    assert!(has(&t, "Set up Open Computer in your Cloudflare account"));
    assert!(has(&t, "Docker must be running"));

    // A core found on the account: one click connects to it, named.
    let mut a = app();
    a.cf_hint_signed_in = true;
    a.found_core = Some(found());
    let t = texts(a);
    assert!(has(&t, "Connect to your core at open-computer-core"));
    assert!(!has(&t, "Set up Open Computer in your Cloudflare account"));
}

#[test]
fn a_connected_core_and_a_core_with_its_own_openrouter_key_finish_steps_two_and_three() {
    let mut a = app();
    a.onboarding
        .set_cloud_url("https://open-computer-core.me.workers.dev");
    a.onboarding.secret_saved(Slot::CloudAccess);
    a.core_has_model_key = true;
    assert_eq!(a.core_step(), CoreStep::Connected);
    let t = texts(a);
    assert!(has(
        &t,
        "Connected to https://open-computer-core.me.workers.dev"
    ));
    assert!(has(&t, "Your core already has an OpenRouter key."));
    assert!(
        !has(&t, "Connect OpenRouter"),
        "no button when the core already has a key"
    );
}

#[test]
fn without_a_key_on_the_core_step_three_is_the_connect_button_and_paste_is_under_advanced() {
    let mut a = app();
    a.onboarding.set_local_only(true);
    let t = texts(a);
    assert!(has(&t, "OpenRouter (pays for the AI models)"));
    assert!(has(&t, "Connect OpenRouter"));
    assert!(
        !has(&t, "Paste your OpenRouter key"),
        "paste is hidden until Advanced is opened"
    );

    let mut a = app();
    a.onboarding.set_local_only(true);
    a.show_advanced = true;
    let t = texts(a);
    assert!(
        has(&t, "OpenRouter key (if you cannot use the button)")
            || has(&t, "Paste your OpenRouter key")
    );
}

#[test]
fn the_openrouter_step_is_renamed_uses_the_button_and_is_skipped_when_the_core_has_a_key() {
    assert_eq!(
        Step::ModelKey.title(),
        "OpenRouter (pays for the AI models)"
    );
    let mut a = app();
    a.onboarding.set_local_only(true);
    a.core_has_model_key = true;
    // Continue from Get started: the OpenRouter step is passed over and the placeholder key stays in the keyring.
    a.next();
    assert_eq!(a.onboarding.step(), Step::Memory);
    assert!(a.onboarding.secrets_set().model_key);

    // On that step itself, a core with a key says so; without one it is the Connect button, not a paste box.
    let mut b = app();
    b.onboarding.set_local_only(true);
    b.onboarding.advance().unwrap();
    assert_eq!(b.onboarding.step(), Step::ModelKey);
    let t = texts(b);
    assert!(has(&t, "Connect OpenRouter") && !has(&t, "Model key"));
}

#[test]
fn a_found_core_without_a_saved_token_says_so_plainly_and_opens_advanced() {
    let mut a = app();
    a.found_core = Some(found());
    a.connect_found_core();
    assert_eq!(
        a.core_line.as_deref(),
        Some(
            "This computer doesn't have the access token for that core. Run setup here, or paste the token under Advanced."
        )
    );
    assert!(a.show_advanced);

    // With the token already in the keyring, one click connects.
    let store = Arc::new(InMemorySecrets::new());
    store
        .set(Slot::CloudAccess, "fake-token-for-tests")
        .unwrap();
    let mut b = App::with_store(store);
    b.found_core = Some(found());
    b.connect_found_core();
    assert!(
        b.core_line
            .as_deref()
            .unwrap()
            .starts_with("Connected to https://open-computer-core.me.workers.dev")
    );
    assert_eq!(
        b.onboarding.cloud_url(),
        "https://open-computer-core.me.workers.dev"
    );
    assert!(b.core_connected());
}

#[test]
fn setup_shows_its_rows_as_they_arrive_and_a_missing_gateway_offers_the_dashboard_link() {
    use shell_core::autoconnect::{RowStatus, SetupRow};
    let mut a = app();
    a.cf_hint_signed_in = true;
    a.setup_running = true;
    a.gateway_link = true;
    a.setup_rows = vec![
        SetupRow {
            status: RowStatus::Pass,
            title: "Sign-in to Cloudflare  (1.2 s)".into(),
            detail: vec![],
        },
        SetupRow {
            status: RowStatus::Fail,
            title: "Deploy".into(),
            detail: vec!["The deploy failed.".into()],
        },
    ];
    let t = texts(a);
    assert!(has(&t, "Sign-in to Cloudflare"));
    assert!(has(&t, "The deploy failed."), "a failing row shows why");
    assert!(has(&t, "Open AI Gateway page"));
}

#[test]
fn a_running_row_has_a_spinner_and_a_live_timer_and_a_settled_row_swaps_its_mark() {
    let mut a = app();
    a.onboarding.set_local_only(true);
    a.onboarding.advance().unwrap();
    a.onboarding.set_secret_flag(Slot::ModelKey, true);
    a.onboarding.advance().unwrap();
    a.onboarding.advance().unwrap();
    let mut rows = initial_rows();
    rows[0].status = Status::Pass;
    rows[0].ms = Some(420.0);
    rows[1].status = Status::Running;
    let (done, running) = (rows[0].id, rows[1].id);
    a.row_started.insert(
        running.to_owned(),
        Instant::now().checked_sub(Duration::from_secs(3)).unwrap(),
    );
    a.chain_started = Instant::now().checked_sub(Duration::from_secs(5));
    a.diag_rows = rows;
    a.diag_running = true;
    let k = keys(a);
    assert!(
        k.contains(&format!("mark-{done}-ok")),
        "a finished row shows its check"
    );
    assert!(
        k.contains(&format!("mark-{running}-...")),
        "a running row has its own cell with the spinner"
    );
}

#[test]
fn the_running_timers_count_up_from_when_the_row_and_the_chain_started() {
    let mut a = app();
    a.onboarding.set_local_only(true);
    a.onboarding.advance().unwrap();
    a.onboarding.set_secret_flag(Slot::ModelKey, true);
    a.onboarding.advance().unwrap();
    a.onboarding.advance().unwrap();
    let mut rows = initial_rows();
    rows[0].status = Status::Running;
    let id = rows[0].id;
    a.row_started.insert(
        id.to_owned(),
        Instant::now().checked_sub(Duration::from_secs(3)).unwrap(),
    );
    a.chain_started = Instant::now().checked_sub(Duration::from_secs(5));
    a.diag_rows = rows;
    a.diag_running = true;
    let t = texts(a);
    assert!(
        t.iter().any(|s| s == "3.0 s" || s == "3.1 s"),
        "the running row counts up live: {t:?}"
    );
    assert!(
        t.iter().any(|s| s.contains("passed · 5.")),
        "the header total counts up live: {t:?}"
    );
}

#[test]
fn local_only_marks_cloudflare_as_not_needed_and_does_not_claim_a_sign_in() {
    assert_eq!(
        super::start::cloudflare_badge(true, false, true),
        ("Not needed (local only)", false)
    );
    assert_eq!(
        super::start::cloudflare_badge(false, false, false),
        ("Not signed in", false)
    );
    assert_eq!(
        super::start::cloudflare_badge(false, true, false),
        ("Signed in", true)
    );

    let mut a = app();
    a.onboarding.set_local_only(true);
    a.show_advanced = true;
    let t = texts(a);
    assert!(has(&t, "Not needed (local only)"));
    assert!(
        !has(&t, "Signed in"),
        "local only must not show a Signed in badge: {t:?}"
    );
    assert!(
        !has(&t, "click Allow"),
        "local only drops the Cloudflare Allow copy: {t:?}"
    );
    assert!(has(
        &t,
        "You still need an OpenRouter key, because models are reached through OpenRouter."
    ));
    assert!(has(&t, "Use this computer only (no cloud)"));
    assert!(
        !has(&t, "Local only: on (press to use a cloud core)") && !has(&t, "Continue with these"),
        "the old toggle wording and the second Continue are gone: {t:?}"
    );
    assert!(has(&t, "Step 1 of 4"));
}

#[test]
fn cancelling_or_timing_out_openrouter_connect_resets_the_button_for_retry() {
    let mut a = app();
    a.onboarding.set_local_only(true);
    a.or_busy = true;
    a.or_line = Some("Your browser opened OpenRouter. Click Authorize there.".to_owned());
    let t = texts(a);
    assert!(has(&t, "Working…"));
    assert!(has(&t, "Cancel"));

    let mut a = app();
    a.onboarding.set_local_only(true);
    a.or_busy = true;
    a.or_line = Some("Your browser opened OpenRouter. Click Authorize there.".to_owned());
    a.cancel_openrouter();
    assert!(!a.or_busy);
    assert_eq!(
        a.or_line.as_deref(),
        Some(shell_core::openrouter::CANCELLED)
    );
    let t = texts(a);
    assert!(has(&t, "Try again"));
    assert!(!has(&t, "Working…"), "cancel must not leave Working: {t:?}");
    assert!(
        !has(&t, "Cancel"),
        "Cancel is only shown while waiting: {t:?}"
    );

    let mut a = app();
    a.onboarding.set_local_only(true);
    a.or_busy = true;
    a.on_openrouter_key(Err(shell_core::openrouter::TIMED_OUT.to_owned()));
    assert!(!a.or_busy);
    assert_eq!(
        a.or_line.as_deref(),
        Some(shell_core::openrouter::TIMED_OUT)
    );
    let t = texts(a);
    assert!(has(&t, "Try again"));
    assert!(has(&t, "not finished in time"));
    assert!(!has(&t, "Working…"));
}

#[test]
fn going_back_cancels_a_running_openrouter_connect() {
    let mut a = app();
    a.onboarding.set_local_only(true);
    a.onboarding.advance().unwrap();
    assert_eq!(a.onboarding.step(), Step::ModelKey);
    a.or_busy = true;
    a.or_line = Some("Your browser opened OpenRouter. Click Authorize there.".to_owned());
    a.back();
    assert_eq!(a.onboarding.step(), Step::Connect);
    assert!(!a.or_busy);
    assert_eq!(
        a.or_line.as_deref(),
        Some(shell_core::openrouter::CANCELLED)
    );
}

#[test]
fn get_started_descriptions_wrap_instead_of_clipping_to_one_line() {
    let mut a = app();
    a.onboarding.set_local_only(true);
    a.show_advanced = true;
    let mut rt = Runtime::new(a, view, (520.0, 800.0), Env::new(true, 1.0));
    rt.frame(0.0);
    let cap = rt
        .elements()
        .into_iter()
        .find(|e| {
            e.text
                .as_deref()
                .is_some_and(|t| t.starts_with("Local only: the agent runs"))
        })
        .expect("the local-only description is on the page");
    assert!(
        cap.rect[3] > 28.0,
        "the description must wrap onto more than one line, height was {}: {:?}",
        cap.rect[3],
        cap.rect
    );
}
