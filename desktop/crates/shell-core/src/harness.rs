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
            Harness::Hermes => "NousResearch hermes-agent, run on this computer as a subprocess.",
            Harness::OpenCode => "The OpenCode coding agent, run on this computer as a subprocess.",
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
        let out = Command::new(program)
            .args(args)
            .stdin(Stdio::null())
            .stderr(Stdio::null())
            .output()
            .ok()?;
        if !out.status.success() {
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
            "error" => vec![
                json!({"type": "error", "message": v.get("error").map(|e| e.to_string()).unwrap_or_else(|| "The agent reported an error.".to_owned())}),
            ],
            _ => Vec::new(),
        }
    }
    fn finish(&mut self, ok: bool) -> Vec<Value> {
        finished(ok, self.text_open)
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
    let program = program_override.map_or_else(|| adapter.program(), str::to_owned);
    let mut cmd = Command::new(&program);
    cmd.args(adapter.args(goal, model))
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    if let Some(d) = cwd {
        cmd.current_dir(d);
    }
    let mut child = cmd
        .spawn()
        .map_err(|_| format!("{program} could not be started. Check that it is installed."))?;
    emit(json!({"type": "run", "status": "running"}));
    let stdout = child.stdout.take().ok_or("The agent has no output.")?;
    // Killing from another thread unblocks the read below, so Stop works while the agent is quiet.
    let id = child.id();
    let watch_cancel = cancel.clone();
    let done = Arc::new(AtomicBool::new(false));
    let watch_done = done.clone();
    let watcher = std::thread::spawn(move || {
        while !watch_done.load(Ordering::Relaxed) {
            if watch_cancel.load(Ordering::Relaxed) {
                let _ = Command::new("kill").arg(id.to_string()).status();
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
    if cancel.load(Ordering::Relaxed) {
        emit(json!({"type": "run", "status": "cancelled"}));
        return Ok(());
    }
    for ev in adapter.finish(status.success()) {
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
}
