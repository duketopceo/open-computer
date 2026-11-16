//! Real-binary checks: the actual `opencode` and `hermes` programs and the actual `wrangler`, driven through Open
//! Computer's adapter. No key, no spend, no account: each agent is pointed at a local fake OpenAI-compatible server
//! (`desktop/scripts/fake_openai.py`) and wrangler runs with an empty config directory. Run by
//! `desktop/scripts/real_harness_check.sh`, which installs nothing itself and sets these variables:
//!
//! - `OC_REAL_OPENCODE`, `OC_REAL_OPENCODE_SLOW`, `OC_REAL_OPENCODE_DEAD`: wrapper scripts that start opencode against
//!   the fake server, a slow fake server, and a closed port.
//! - `OC_REAL_HERMES`, `OC_REAL_HERMES_SLOW`: the same for hermes-agent.
//! - `OC_REAL_WRANGLER`: a wrapper that runs wrangler with an isolated, empty home.
//!
//! A test whose variable is not set is skipped, so a plain `cargo test -- --ignored` on a machine without them passes.

use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use serde_json::Value;
use shell_core::chat::{Chat, Item};
use shell_core::harness::{Adapter, HermesAdapter, OpenCodeAdapter, run_task};
use shell_core::signin;

fn var(name: &str) -> Option<String> {
    std::env::var(name).ok().filter(|v| !v.is_empty())
}

struct Ran {
    chat: Chat,
    events: Vec<Value>,
}

fn run(adapter: &mut dyn Adapter, program: &str, goal: &str) -> Result<Ran, String> {
    let mut chat = Chat::default();
    chat.push_user(goal);
    let mut events = Vec::new();
    let cancel = Arc::new(AtomicBool::new(false));
    run_task(
        adapter,
        goal,
        None,
        None,
        Some(program),
        &cancel,
        &mut |e| {
            chat.apply(&e);
            events.push(e);
        },
    )?;
    Ok(Ran { chat, events })
}

fn answer(chat: &Chat) -> String {
    chat.items
        .iter()
        .filter_map(|i| match i {
            Item::Assistant { text, .. } => Some(text.clone()),
            _ => None,
        })
        .collect::<Vec<_>>()
        .join("")
}

/// Stop while the agent waits on the slow server: the run ends cancelled, quickly, and the whole process tree is gone.
fn stop_kills(adapter: &mut dyn Adapter, program: &str) {
    let cancel = Arc::new(AtomicBool::new(false));
    let flag = cancel.clone();
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_secs(4));
        flag.store(true, Ordering::Relaxed);
    });
    let mut last = String::new();
    let t = Instant::now();
    run_task(
        adapter,
        "wait forever",
        None,
        None,
        Some(program),
        &cancel,
        &mut |e| {
            if let Some(s) = e.get("status").and_then(Value::as_str) {
                last = s.to_owned();
            }
        },
    )
    .unwrap();
    assert_eq!(last, "cancelled");
    assert!(
        t.elapsed() < Duration::from_secs(30),
        "Stop took {:?}",
        t.elapsed()
    );
}

#[test]
#[ignore = "needs the real opencode; run by desktop/scripts/real_harness_check.sh"]
fn opencode_streams_text_ends_done_and_maps_a_real_tool_call() {
    let Some(program) = var("OC_REAL_OPENCODE") else {
        eprintln!("skipped: OC_REAL_OPENCODE not set");
        return;
    };
    let r = run(&mut OpenCodeAdapter::default(), &program, "say hi").unwrap();
    assert_eq!(r.chat.status, "done", "{:?}", r.events);
    assert_eq!(answer(&r.chat), "FAKE-ANSWER-OK");
    assert!(
        r.chat
            .items
            .iter()
            .any(|i| matches!(i, Item::Assistant { done: true, .. }))
    );

    // A real tool call: the fake server asks for `bash echo tool-ran`; opencode runs it and reports a `tool_use` part.
    let r = run(&mut OpenCodeAdapter::default(), &program, "USE-TOOL please").unwrap();
    assert_eq!(r.chat.status, "done", "{:?}", r.events);
    let tool = r.chat.items.iter().find_map(|i| match i {
        Item::Tool(t) => Some(t.clone()),
        _ => None,
    });
    let tool = tool.expect("a tool card");
    assert_eq!(tool.name, "bash");
    assert_eq!(tool.status, "ok");
    assert!(tool.output.as_deref().unwrap_or("").contains("tool-ran"));
    assert!(tool.args.contains("echo tool-ran"));
    assert_eq!(answer(&r.chat), "FAKE-ANSWER-OK");
}

#[test]
#[ignore = "needs the real opencode; run by desktop/scripts/real_harness_check.sh"]
fn opencode_reports_an_unreachable_model_as_a_failed_run_with_words() {
    let Some(program) = var("OC_REAL_OPENCODE_DEAD") else {
        eprintln!("skipped: OC_REAL_OPENCODE_DEAD not set");
        return;
    };
    let r = run(&mut OpenCodeAdapter::default(), &program, "say hi").unwrap();
    assert_eq!(r.chat.status, "failed", "{:?}", r.events);
    assert!(
        r.chat
            .items
            .iter()
            .any(|i| matches!(i, Item::Note(n) if n.contains("Cannot connect"))),
        "{:?}",
        r.chat.items
    );
}

#[test]
#[ignore = "needs the real opencode; run by desktop/scripts/real_harness_check.sh"]
fn opencode_stop_kills_the_process_tree() {
    let Some(program) = var("OC_REAL_OPENCODE_SLOW") else {
        eprintln!("skipped: OC_REAL_OPENCODE_SLOW not set");
        return;
    };
    stop_kills(&mut OpenCodeAdapter::default(), &program);
}

#[test]
#[ignore = "needs the real hermes-agent; run by desktop/scripts/real_harness_check.sh"]
fn hermes_streams_its_answer_and_ends_done() {
    let Some(program) = var("OC_REAL_HERMES") else {
        eprintln!("skipped: OC_REAL_HERMES not set");
        return;
    };
    let r = run(&mut HermesAdapter::default(), &program, "say hi").unwrap();
    assert_eq!(r.chat.status, "done", "{:?}", r.events);
    assert_eq!(answer(&r.chat).trim(), "FAKE-ANSWER-OK");
    assert!(
        r.chat
            .items
            .iter()
            .any(|i| matches!(i, Item::Assistant { done: true, .. }))
    );
}

#[test]
#[ignore = "needs the real hermes-agent; run by desktop/scripts/real_harness_check.sh"]
fn hermes_stop_kills_the_process_tree() {
    let Some(program) = var("OC_REAL_HERMES_SLOW") else {
        eprintln!("skipped: OC_REAL_HERMES_SLOW not set");
        return;
    };
    stop_kills(&mut HermesAdapter::default(), &program);
}

#[test]
#[ignore = "needs the real wrangler; run by desktop/scripts/real_harness_check.sh"]
fn real_wrangler_signed_out_is_reported_as_not_signed_in() {
    let Some(program) = var("OC_REAL_WRANGLER") else {
        eprintln!("skipped: OC_REAL_WRANGLER not set");
        return;
    };
    let cmd = vec![program];
    let state = signin::whoami_with(&cmd).unwrap();
    assert!(!state.signed_in, "{state:?}");
    assert!(state.email.is_none() && state.accounts.is_empty());
    let (ok, words, fix) = signin::step1(&state);
    assert!(!ok && words.contains("not signed in"));
    assert!(fix.unwrap().contains("Sign in with Cloudflare"));
    // Signing out when nobody is signed in must not fail or sign anyone in.
    let after = signin::sign_out_with(&cmd).unwrap();
    assert!(!after.signed_in);
}

#[test]
#[ignore = "needs the real opencode; run by desktop/scripts/real_harness_check.sh"]
fn opencode_runs_open_computers_model_through_the_openrouter_provider() {
    let Some(program) = var("OC_REAL_OPENCODE_MAPPED") else {
        eprintln!("skipped: OC_REAL_OPENCODE_MAPPED not set");
        return;
    };
    // The real `opencode models` lists the configured openrouter provider's model; the adapter maps `z-ai/glm-5.3` onto it.
    let mut chat = Chat::default();
    chat.push_user("say hi");
    let mut events = Vec::new();
    let cancel = Arc::new(AtomicBool::new(false));
    run_task(
        &mut OpenCodeAdapter::default(),
        "say hi",
        Some("z-ai/glm-5.3"),
        None,
        Some(&program),
        &cancel,
        &mut |e| {
            chat.apply(&e);
            events.push(e);
        },
    )
    .unwrap();
    assert_eq!(chat.status, "done", "{events:?}");
    assert_eq!(answer(&chat), "FAKE-ANSWER-OK", "{events:?}");
    assert!(
        !events.iter().any(|e| e["message"]
            .as_str()
            .is_some_and(|m| m.contains("doesn't know"))),
        "the model was found, not dropped: {events:?}"
    );

    // A model OpenCode does not list is dropped with a line saying so, and the run still works on OpenCode's default.
    let mut events = Vec::new();
    run_task(
        &mut OpenCodeAdapter::default(),
        "say hi",
        Some("acme/unheard-of-7"),
        None,
        Some(&program),
        &cancel,
        &mut |e| events.push(e),
    )
    .unwrap();
    assert!(
        events.iter().any(|e| e["message"]
            .as_str()
            .is_some_and(|m| m.contains("OpenCode doesn't know acme/unheard-of-7"))),
        "{events:?}"
    );
}
