//! Agent harness choice: the built-in core loop, Hermes, or OpenCode.
//!
//! "Hermes" here is NousResearch's `hermes-agent` (the `hermes` command; the Kurultai Hermes gateway in luke-agents is built
//! on it). A harness runs as a subprocess behind one adapter interface: the adapter says how to start it for a goal and maps
//! each line of its output onto the same events the built-in core streams, so the chat screen does not care which one ran.
//!
//! Detection and running go through small traits so tests use fake commands and never start a real agent or a model call.

use std::io::{BufRead, BufReader};
use std::process::{Command, Stdio};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};

use serde_json::{Value, json};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Harness {
    BuiltIn,
    Hermes,
    OpenCode,
}

impl Harness {
    pub const ALL: [Harness; 3] = [Harness::BuiltIn, Harness::Hermes, Harness::OpenCode];

    pub fn id(self) -> &'static str {
        match self {
            Harness::BuiltIn => "builtin",
            Harness::Hermes => "hermes",
            Harness::OpenCode => "opencode",
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            Harness::BuiltIn => "Built-in",
            Harness::Hermes => "Hermes",
            Harness::OpenCode => "OpenCode",
        }
    }

    pub fn describe(self) -> &'static str {
        match self {
            Harness::BuiltIn => {
                "Open Computer's own loop, with its approvals, sandbox and spend caps. The default."
            }
            Harness::Hermes => {
                "NousResearch hermes-agent in one-shot mode, run on this computer. One-shot mode approves its own tool use automatically."
            }
            Harness::OpenCode => {
                "The OpenCode coding agent, run on this computer. Tool use it would have to ask about is refused, because nothing can answer it here."
            }
        }
    }

    pub fn from_id(id: &str) -> Harness {
        Harness::ALL
            .into_iter()
            .find(|h| h.id() == id)
            .unwrap_or(Harness::BuiltIn)
    }

    /// The command to look for; the built-in loop needs none.
    pub fn binary(self) -> Option<&'static str> {
        match self {
            Harness::BuiltIn => None,
            Harness::Hermes => Some("hermes"),
            Harness::OpenCode => Some("opencode"),
        }
    }

    pub fn install_hint(self) -> &'static str {
        match self {
            Harness::BuiltIn => "Nothing to install.",
            Harness::Hermes => {
                "Install Hermes: see https://github.com/NousResearch/hermes-agent, then check that `hermes --version` works."
            }
            Harness::OpenCode => {
                "Install OpenCode: `curl -fsSL https://opencode.ai/install | bash` (or `npm i -g opencode-ai`), then check `opencode --version`."
            }
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Detected {
    pub harness: Harness,
    pub installed: bool,
    pub version: Option<String>,
}

impl Detected {
    /// One plain line for Settings.
    pub fn summary(&self) -> String {
        match (self.harness, self.installed, &self.version) {
            (Harness::BuiltIn, _, _) => "Always available.".to_owned(),
            (_, true, Some(v)) => format!("Installed, {v}."),
            (_, true, None) => "Installed.".to_owned(),
            (h, false, _) => format!("Not installed. {}", h.install_hint()),
        }
    }
}

/// Runs `program args` and returns its first output line, or none when it cannot start or exits non-zero.
pub trait Probe {
    fn first_line(&self, program: &str, args: &[&str]) -> Option<String>;
}

pub struct SystemProbe;

impl Probe for SystemProbe {
    fn first_line(&self, program: &str, args: &[&str]) -> Option<String> {
        let out = Command::new(resolve_program(program))
            .args(args)
            .env("PATH", crate::signin::child_path())
            .stdin(Stdio::null())
            .stderr(Stdio::null())
            .output()
            .ok()?;
        if !out.status.success() {
            crate::log_info!(
                "harness",
                "{program} {} exited with {}",
                args.join(" "),
                out.status
            );
            return None;
        }
        String::from_utf8_lossy(&out.stdout)
            .lines()
            .map(str::trim)
            .find(|l| !l.is_empty())
            .map(str::to_owned)
    }
}

pub fn detect(h: Harness, probe: &dyn Probe) -> Detected {
    let Some(bin) = h.binary() else {
        return Detected {
            harness: h,
            installed: true,
            version: None,
        };
    };
    let line = probe.first_line(bin, &["--version"]);
    Detected {
        harness: h,
        installed: line.is_some(),
        version: line.map(|l| l.chars().take(80).collect()),
    }
}

pub fn detect_all(probe: &dyn Probe) -> Vec<Detected> {
    Harness::ALL.into_iter().map(|h| detect(h, probe)).collect()
}

/// How to start a harness and how to read what it prints.
pub trait Adapter: Send {
    fn program(&self) -> String;
    fn args(&self, goal: &str, model: Option<&str>) -> Vec<String>;
    /// Maps one output line to zero or more core events. Must not panic on any input.
    fn map_line(&mut self, line: &str) -> Vec<Value>;
    /// Events to close the run when the process ended (`ok` is the exit status).
    fn finish(&mut self, ok: bool) -> Vec<Value>;
}

pub fn adapter_for(h: Harness) -> Option<Box<dyn Adapter>> {
    match h {
        Harness::BuiltIn => None,
        Harness::Hermes => Some(Box::new(HermesAdapter::default())),
        Harness::OpenCode => Some(Box::new(OpenCodeAdapter::default())),
    }
}

fn finished(ok: bool, open_text: bool) -> Vec<Value> {
    let mut v = Vec::new();
    if open_text {
        v.push(json!({"type": "message.done", "text": ""}));
    }
    v.push(if ok {
        json!({"type": "run", "status": "done"})
    } else {
        json!({"type": "run", "status": "failed", "reason": "The agent exited with an error."})
    });
    v
}

/// Hermes one-shot mode (`hermes -z PROMPT`) prints the answer as plain text, so each line is streamed as message text.
#[derive(Default)]
pub struct HermesAdapter {
    started: bool,
}

impl Adapter for HermesAdapter {
    fn program(&self) -> String {
        "hermes".to_owned()
    }
    fn args(&self, goal: &str, model: Option<&str>) -> Vec<String> {
        let mut a = vec!["-z".to_owned(), goal.to_owned()];
        if let Some(m) = model.filter(|m| !m.is_empty()) {
            a.push("-m".to_owned());
            a.push(m.to_owned());
        }
        a
    }
    fn map_line(&mut self, line: &str) -> Vec<Value> {
        self.started = true;
        vec![json!({"type": "message.delta", "text": format!("{line}\n")})]
    }
    fn finish(&mut self, ok: bool) -> Vec<Value> {
        finished(ok, self.started)
    }
}

/// OpenCode `run --format json` prints one JSON object per line: text parts, tool parts with a state, and step markers.
#[derive(Default)]
pub struct OpenCodeAdapter {
    text_open: bool,
    /// OpenCode reports a failed model call as an `error` event and still exits 0.
    errored: bool,
}

impl Adapter for OpenCodeAdapter {
    fn program(&self) -> String {
        "opencode".to_owned()
    }
    fn args(&self, goal: &str, model: Option<&str>) -> Vec<String> {
        let mut a = vec!["run".to_owned(), "--format".to_owned(), "json".to_owned()];
        if let Some(m) = model.filter(|m| !m.is_empty()) {
            a.push("--model".to_owned());
            a.push(m.to_owned());
        }
        a.push(goal.to_owned());
        a
    }
    fn map_line(&mut self, line: &str) -> Vec<Value> {
        let Ok(v) = serde_json::from_str::<Value>(line) else {
            // Not JSON: treat as text rather than dropping what the agent said.
            self.text_open = true;
            return vec![json!({"type": "message.delta", "text": format!("{line}\n")})];
        };
        let part = v.get("part").unwrap_or(&Value::Null);
        let s = |o: &Value, k: &str| o.get(k).and_then(Value::as_str).unwrap_or("").to_owned();
        match v.get("type").and_then(Value::as_str).unwrap_or("") {
            "text" => {
                self.text_open = true;
                vec![json!({"type": "message.delta", "text": s(part, "text")})]
            }
            "reasoning" => vec![json!({"type": "reasoning.delta", "text": s(part, "text")})],
            "tool_use" | "tool" => {
                let state = part.get("state").unwrap_or(&Value::Null);
                let id = s(part, "callID");
                let name = s(part, "tool");
                let args = state.get("input").map(Value::to_string).unwrap_or_default();
                let mut out = vec![
                    json!({"type": "tool_call.started", "call_id": id, "name": name}),
                    json!({"type": "tool_call.finished", "call_id": id, "name": name, "args": args, "risk": ""}),
                ];
                match s(state, "status").as_str() {
                    "completed" => out.push(json!({"type": "tool_call.result", "call_id": id, "status": "ok", "output": s(state, "output")})),
                    "error" => out.push(json!({"type": "tool_call.result", "call_id": id, "status": "error", "output": s(state, "error")})),
                    _ => {}
                }
                out
            }
            "error" => {
                self.errored = true;
                let e = v.get("error").unwrap_or(&Value::Null);
                let words = e
                    .pointer("/data/message")
                    .or_else(|| e.get("message"))
                    .and_then(Value::as_str)
                    .unwrap_or("The agent reported an error.");
                vec![json!({"type": "error", "message": words})]
            }
            _ => Vec::new(),
        }
    }
    fn finish(&mut self, ok: bool) -> Vec<Value> {
        finished(ok && !self.errored, self.text_open)
    }
}

/// Every process started by `root`, children before parents (from `ps`, so it works on Linux and macOS).
fn descendants(root: u32) -> Vec<u32> {
    let Ok(out) = Command::new("ps").args(["-e", "-o", "pid=,ppid="]).output() else {
        return Vec::new();
    };
    let pairs: Vec<(u32, u32)> = String::from_utf8_lossy(&out.stdout)
        .lines()
        .filter_map(|l| {
            let mut it = l.split_whitespace();
            Some((it.next()?.parse().ok()?, it.next()?.parse().ok()?))
        })
        .collect();
    let mut found = vec![root];
    let mut i = 0;
    while i < found.len() {
        let parent = found[i];
        for (pid, ppid) in &pairs {
            if *ppid == parent && !found.contains(pid) {
                found.push(*pid);
            }
        }
        i += 1;
    }
    found.reverse();
    found
}

/// Stops a harness and everything it started. Real agents start helper processes and some move to a process group of
/// their own, so a group signal alone misses them; this walks the whole tree, asks politely, then forces.
fn kill_tree(root: u32) {
    #[cfg(unix)]
    {
        let tree = descendants(root);
        let send = |signal: &str| {
            for pid in &tree {
                let _ = Command::new("kill")
                    .args([signal, &pid.to_string()])
                    .stderr(Stdio::null())
                    .status();
            }
        };
        send("-TERM");
        std::thread::sleep(std::time::Duration::from_millis(800));
        send("-KILL");
    }
    #[cfg(not(unix))]
    {
        let _ = Command::new("taskkill")
            .args(["/PID", &root.to_string(), "/T", "/F"])
            .status();
    }
}

/// A program name to start: a bare name is looked up on the PATH and then in the extra directories (`~/.local/bin`, the mise
/// shims), because an app opened from a desktop menu has a minimal PATH. Anything with a slash is used as given.
pub fn resolve_program(program: &str) -> String {
    if program.contains('/') || program.contains('\\') {
        return program.to_owned();
    }
    crate::signin::find_program(
        program,
        std::env::var_os("PATH").as_deref(),
        &crate::signin::extra_dirs(crate::signin::home_dir().as_deref()),
        &crate::signin::is_executable_file,
    )
    .map_or_else(|| program.to_owned(), |p| p.to_string_lossy().into_owned())
}

/// What to tell the person when a harness fails: the program's own words (its stderr), plus the documented fix when the words
/// say it has no model, provider or login. `stderr` has already been through [`crate::applog::redact`].
pub fn explain_failure(program: &str, code: Option<i32>, stderr: &str) -> String {
    let name = match program.rsplit('/').next().unwrap_or(program) {
        "hermes" => "Hermes",
        "opencode" => "OpenCode",
        other => other,
    };
    let tail = stderr.trim();
    let lower = tail.to_ascii_lowercase();
    let unconfigured = [
        "no model",
        "no provider",
        "not configured",
        "api key",
        "api_key",
        "apikey",
        "not logged in",
        "no credentials",
        "unauthorized",
        "authenticat",
        "login",
        "no inference provider",
        "provider",
    ]
    .iter()
    .any(|w| lower.contains(w));
    let fix = match (name, unconfigured) {
        ("Hermes", true) => {
            " Hermes has no model or provider configured: run `hermes setup` (or `hermes model`) in a terminal, then try again. If you keep an OpenRouter key on this computer you can also let Open Computer hand it to Hermes under Settings, Agent harness."
        }
        ("OpenCode", true) => {
            " OpenCode has no provider configured: run `opencode auth login` in a terminal, then try again. If you keep an OpenRouter key on this computer you can also let Open Computer hand it to OpenCode under Settings, Agent harness."
        }
        _ => "",
    };
    let code = code.map_or_else(
        || "was stopped".to_owned(),
        |c| format!("exited with code {c}"),
    );
    if tail.is_empty() {
        format!("{name} {code} and printed nothing to stderr.{fix}")
    } else {
        format!("{name} {code}. It said: {tail}.{fix}")
    }
}

/// Starts the harness, feeds every mapped event to `emit` and ends with a `run` event. `cancel` kills the process.
/// `program_override` lets tests start a fake command instead of the real agent.
pub fn run_task(
    adapter: &mut dyn Adapter,
    goal: &str,
    model: Option<&str>,
    cwd: Option<&std::path::Path>,
    program_override: Option<&str>,
    cancel: &Arc<AtomicBool>,
    emit: &mut dyn FnMut(Value),
) -> Result<(), String> {
    run_task_with(
        adapter,
        goal,
        model,
        cwd,
        program_override,
        &[],
        cancel,
        emit,
    )
}

/// [`run_task`] with extra environment variables for the child (for example `OPENROUTER_API_KEY`, only when the person opted in).
/// The program's stderr is kept (last 4 KB, redacted) so a failure can say what the program said.
#[allow(clippy::too_many_arguments)]
pub fn run_task_with(
    adapter: &mut dyn Adapter,
    goal: &str,
    model: Option<&str>,
    cwd: Option<&std::path::Path>,
    program_override: Option<&str>,
    env: &[(String, String)],
    cancel: &Arc<AtomicBool>,
    emit: &mut dyn FnMut(Value),
) -> Result<(), String> {
    let program = program_override.map_or_else(|| adapter.program(), str::to_owned);
    let exe = resolve_program(&program);
    let mut cmd = Command::new(&exe);
    cmd.args(adapter.args(goal, model))
        .env("PATH", crate::signin::child_path())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    for (k, v) in env {
        cmd.env(k, v);
    }
    if let Some(d) = cwd {
        cmd.current_dir(d);
    }
    crate::log_info!(
        "harness",
        "starting {program} ({exe}); env passed: {}",
        env.iter()
            .map(|(k, _)| k.as_str())
            .collect::<Vec<_>>()
            .join(",")
    );
    let mut child = cmd.spawn().map_err(|e| {
        crate::log_error!("harness", "{program} could not be started: {e}");
        format!("{program} could not be started ({e}). Check that it is installed.")
    })?;
    emit(json!({"type": "run", "status": "running"}));
    let stdout = child.stdout.take().ok_or("The agent has no output.")?;
    let stderr_tail = Arc::new(std::sync::Mutex::new(String::new()));
    let err_reader = child.stderr.take().map(|e| {
        let tail = stderr_tail.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(e).lines().map_while(Result::ok) {
                let clean = crate::applog::redact(&crate::signin::strip_ansi_text(&line));
                crate::log_debug!("harness", "stderr: {clean}");
                let mut t = tail.lock().unwrap_or_else(|e| e.into_inner());
                if !t.is_empty() {
                    t.push(' ');
                }
                t.push_str(clean.trim());
                let len = t.len();
                if len > 4000 {
                    let cut = t.ceil_char_boundary(len - 4000);
                    *t = t[cut..].to_owned();
                }
            }
        })
    });
    // Killing from another thread unblocks the read below, so Stop works while the agent is quiet.
    let id = child.id();
    let watch_cancel = cancel.clone();
    let done = Arc::new(AtomicBool::new(false));
    let watch_done = done.clone();
    let watcher = std::thread::spawn(move || {
        while !watch_done.load(Ordering::Relaxed) {
            if watch_cancel.load(Ordering::Relaxed) {
                kill_tree(id);
                return;
            }
            std::thread::sleep(std::time::Duration::from_millis(50));
        }
    });
    for line in BufReader::new(stdout).lines().map_while(Result::ok) {
        for ev in adapter.map_line(&line) {
            emit(ev);
        }
    }
    let status = child.wait().map_err(|e| e.to_string())?;
    done.store(true, Ordering::Relaxed);
    let _ = watcher.join();
    if let Some(r) = err_reader {
        let _ = r.join();
    }
    if cancel.load(Ordering::Relaxed) {
        emit(json!({"type": "run", "status": "cancelled"}));
        return Ok(());
    }
    let stderr = stderr_tail
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .clone();
    crate::log_info!("harness", "{program} finished: {status}");
    let events = adapter.finish(status.success());
    let failed = events
        .iter()
        .any(|e| e.get("status").and_then(Value::as_str) == Some("failed"));
    let why = failed.then(|| explain_failure(&program, status.code(), &stderr));
    if let Some(why) = &why {
        crate::log_warn!("harness", "{program} failed: {why}");
        emit(json!({"type": "error", "message": why}));
    }
    for ev in events {
        emit(ev);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::chat::{Chat, Item};

    struct FakeProbe(Vec<(&'static str, &'static str)>);
    impl Probe for FakeProbe {
        fn first_line(&self, program: &str, _: &[&str]) -> Option<String> {
            self.0
                .iter()
                .find(|(p, _)| *p == program)
                .map(|(_, v)| (*v).to_owned())
        }
    }

    #[test]
    fn detection_reports_installed_with_version_and_missing_with_how_to_install() {
        let probe = FakeProbe(vec![("hermes", "Hermes Agent v0.23.1 (2026.9.24)")]);
        let all = detect_all(&probe);
        assert!(all[0].installed && all[0].summary() == "Always available.");
        assert_eq!(
            all[1].version.as_deref(),
            Some("Hermes Agent v0.23.1 (2026.9.24)")
        );
        assert_eq!(
            all[1].summary(),
            "Installed, Hermes Agent v0.23.1 (2026.9.24)."
        );
        assert!(!all[2].installed);
        assert!(all[2].summary().contains("opencode.ai/install"));
    }

    #[test]
    fn the_choice_round_trips_and_unknown_falls_back_to_built_in() {
        for h in Harness::ALL {
            assert_eq!(Harness::from_id(h.id()), h);
        }
        assert_eq!(Harness::from_id("???"), Harness::BuiltIn);
        assert!(adapter_for(Harness::BuiltIn).is_none());
    }

    #[test]
    fn adapters_build_the_documented_commands() {
        let h = HermesAdapter::default();
        assert_eq!(h.args("fix it", Some("m/x")), ["-z", "fix it", "-m", "m/x"]);
        let o = OpenCodeAdapter::default();
        assert_eq!(
            o.args("fix it", None),
            ["run", "--format", "json", "fix it"]
        );
    }

    #[test]
    fn opencode_lines_map_onto_the_chat_event_model() {
        let mut a = OpenCodeAdapter::default();
        let mut chat = Chat::default();
        chat.push_user("hi");
        let lines = [
            r#"{"type":"step_start"}"#,
            r#"{"type":"text","part":{"text":"Looking. "}}"#,
            r#"{"type":"tool_use","part":{"callID":"c1","tool":"bash","state":{"status":"completed","input":{"command":"ls"},"output":"a.txt"}}}"#,
            r#"{"type":"text","part":{"text":"Done."}}"#,
            "not json at all",
        ];
        for l in lines {
            for e in a.map_line(l) {
                chat.apply(&e);
            }
        }
        for e in a.finish(true) {
            chat.apply(&e);
        }
        assert_eq!(chat.status, "done");
        assert!(chat.items.iter().any(|i| matches!(i, Item::Tool(t) if t.name == "bash" && t.status == "ok" && t.output.as_deref() == Some("a.txt"))));
        assert!(
            chat.items
                .iter()
                .any(|i| matches!(i, Item::Assistant { text, .. } if text.contains("Looking. ")))
        );
    }

    #[test]
    fn a_real_subprocess_streams_through_the_adapter_and_ends_done() {
        let mut a = HermesAdapter::default();
        let mut chat = Chat::default();
        chat.push_user("go");
        let cancel = Arc::new(AtomicBool::new(false));
        // `echo` stands in for the agent: it prints one line and exits 0.
        run_task(
            &mut a,
            "hello world",
            None,
            None,
            Some("echo"),
            &cancel,
            &mut |e| chat.apply(&e),
        )
        .unwrap();
        assert_eq!(chat.status, "done");
        assert!(chat.items.iter().any(
            |i| matches!(i, Item::Assistant { text, done: true } if text.contains("-z hello world"))
        ));
    }

    #[test]
    fn a_failing_subprocess_ends_failed_and_a_missing_one_says_so() {
        let mut a = HermesAdapter::default();
        let cancel = Arc::new(AtomicBool::new(false));
        let mut statuses = Vec::new();
        run_task(&mut a, "x", None, None, Some("false"), &cancel, &mut |e| {
            statuses.push(e["status"].as_str().unwrap_or("").to_owned())
        })
        .unwrap();
        assert_eq!(statuses.last().map(String::as_str), Some("failed"));
        let err = run_task(
            &mut HermesAdapter::default(),
            "x",
            None,
            None,
            Some("definitely-not-installed-xyz"),
            &cancel,
            &mut |_| {},
        )
        .unwrap_err();
        assert!(err.contains("could not be started"));
    }

    #[test]
    fn a_real_opencode_error_event_fails_the_run_with_its_message_even_when_the_exit_is_zero() {
        let mut a = OpenCodeAdapter::default();
        let mut chat = Chat::default();
        chat.push_user("hi");
        let line = r#"{"type":"error","timestamp":1,"sessionID":"s","error":{"name":"APIError","data":{"message":"Cannot connect to API: ECONNREFUSED","isRetryable":true}}}"#;
        for e in a.map_line(line).into_iter().chain(a.finish(true)) {
            chat.apply(&e);
        }
        assert_eq!(chat.status, "failed");
        assert!(
            chat.items
                .iter()
                .any(|i| matches!(i, Item::Note(n) if n == "Cannot connect to API: ECONNREFUSED"))
        );
    }

    struct Sleeper;
    impl Adapter for Sleeper {
        fn program(&self) -> String {
            "sleep".to_owned()
        }
        fn args(&self, goal: &str, _: Option<&str>) -> Vec<String> {
            vec![goal.to_owned()]
        }
        fn map_line(&mut self, _: &str) -> Vec<Value> {
            Vec::new()
        }
        fn finish(&mut self, ok: bool) -> Vec<Value> {
            finished(ok, false)
        }
    }

    #[test]
    fn stop_kills_a_quiet_process_and_reports_cancelled() {
        let cancel = Arc::new(AtomicBool::new(false));
        let flag = cancel.clone();
        std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_millis(150));
            flag.store(true, Ordering::Relaxed);
        });
        let mut last = String::new();
        let t = std::time::Instant::now();
        run_task(&mut Sleeper, "30", None, None, None, &cancel, &mut |e| {
            last = e["status"].as_str().unwrap_or("").to_owned()
        })
        .unwrap();
        assert!(t.elapsed().as_secs() < 5);
        assert_eq!(last, "cancelled");
    }

    #[test]
    fn a_failure_says_what_the_program_said_and_the_fix_for_a_missing_model() {
        let w = explain_failure(
            "hermes",
            Some(1),
            "Error: no model configured for provider openrouter",
        );
        assert!(w.contains("It said: Error: no model configured"), "{w}");
        assert!(w.contains("`hermes setup`"), "{w}");
        let w = explain_failure("opencode", Some(1), "ProviderAuthError: not logged in");
        assert!(w.contains("`opencode auth login`"), "{w}");
        let w = explain_failure("hermes", Some(2), "segfault in libfoo");
        assert!(
            w.contains("segfault in libfoo") && !w.contains("hermes setup"),
            "{w}"
        );
        let w = explain_failure("opencode", Some(1), "");
        assert!(w.contains("printed nothing to stderr"), "{w}");
    }

    #[test]
    fn a_failing_run_carries_the_real_stderr_into_the_chat() {
        let dir = std::env::temp_dir().join(format!("oc-fake-harness-{}", std::process::id()));
        let _ = std::fs::create_dir_all(&dir);
        let script = dir.join("fake-agent.sh");
        std::fs::write(
            &script,
            "#!/bin/sh\necho 'no model configured; run setup' >&2\nexit 3\n",
        )
        .unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&script, std::fs::Permissions::from_mode(0o755)).unwrap();
        }
        let mut events = Vec::new();
        let cancel = Arc::new(AtomicBool::new(false));
        let mut a = HermesAdapter::default();
        run_task(
            &mut a,
            "x",
            None,
            None,
            Some(script.to_str().unwrap()),
            &cancel,
            &mut |e| events.push(e),
        )
        .unwrap();
        let msg = events
            .iter()
            .find(|e| e["type"] == "error")
            .and_then(|e| e["message"].as_str())
            .unwrap_or("");
        assert!(
            msg.contains("no model configured; run setup") && msg.contains("code 3"),
            "{events:?}"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn extra_environment_reaches_the_child_but_only_its_name_is_logged() {
        let mut events = Vec::new();
        let cancel = Arc::new(AtomicBool::new(false));
        struct Echo;
        impl Adapter for Echo {
            fn program(&self) -> String {
                "sh".into()
            }
            fn args(&self, _: &str, _: Option<&str>) -> Vec<String> {
                vec!["-c".into(), "echo $OC_TEST_ENV".into()]
            }
            fn map_line(&mut self, line: &str) -> Vec<Value> {
                vec![json!({"type": "message.delta", "text": line})]
            }
            fn finish(&mut self, ok: bool) -> Vec<Value> {
                finished(ok, true)
            }
        }
        run_task_with(
            &mut Echo,
            "x",
            None,
            None,
            None,
            &[("OC_TEST_ENV".into(), "visible-123".into())],
            &cancel,
            &mut |e| events.push(e),
        )
        .unwrap();
        assert!(events.iter().any(|e| e["text"] == "visible-123"));
    }
}
