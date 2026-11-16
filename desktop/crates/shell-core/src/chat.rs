//! The chat model: turns the core's event stream into the list a screen shows (your message, streamed thinking, tool cards,
//! approvals, the answer and the running cost), and the client calls that start a task and decide an approval.
//!
//! Window-free: the reducer is pure and the network calls go through [`ureq`] with the access token only in a header.

use std::io::{BufRead, BufReader};
use std::time::Duration;

use serde_json::Value;

#[derive(Clone, Debug, PartialEq)]
pub enum Item {
    User(String),
    /// The model's reasoning, streamed. `done` once the model finished thinking.
    Thinking {
        text: String,
        done: bool,
    },
    Assistant {
        text: String,
        done: bool,
    },
    Tool(ToolCard),
    Approval(ApprovalCard),
    /// Task and day spend against their caps, in dollars.
    Cost {
        task: f64,
        day: f64,
        task_cap: f64,
        day_cap: Option<f64>,
    },
    /// What one model step used: tokens in and out, reasoning tokens and the cost when the provider reported one.
    Step {
        step: u64,
        prompt: u64,
        completion: u64,
        reasoning: u64,
        cost: Option<f64>,
    },
    /// Memory notes, run status changes and errors, in plain words.
    Note(String),
}

#[derive(Clone, Debug, PartialEq)]
pub struct ToolCard {
    pub call_id: String,
    pub name: String,
    pub risk: String,
    pub args: String,
    /// `running`, `ok`, `error` or `denied`.
    pub status: String,
    pub output: Option<String>,
    pub ms: Option<f64>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ApprovalCard {
    pub id: String,
    pub hash: String,
    pub summary: String,
    /// `requested`, `approved`, `denied` or `expired`.
    pub state: String,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Chat {
    pub items: Vec<Item>,
    /// `running`, `waiting`, `done`, `failed` or `cancelled`; empty before a task starts.
    pub status: String,
    /// The running task, for Stop.
    pub task_id: Option<String>,
    /// Item indexes the person opened (thinking and tool output start collapsed once finished).
    pub open: std::collections::BTreeSet<usize>,
}

impl Chat {
    pub fn push_user(&mut self, text: &str) {
        self.items.push(Item::User(text.to_owned()));
        self.status = "running".to_owned();
        self.task_id = None;
    }

    /// Whether the model has shown any reasoning in the current task (else the screen says "Working").
    pub fn has_reasoning(&self) -> bool {
        self.items
            .iter()
            .any(|i| matches!(i, Item::Thinking { .. }))
    }

    pub fn toggle(&mut self, index: usize) {
        if !self.open.remove(&index) {
            self.open.insert(index);
        }
    }

    pub fn is_busy(&self) -> bool {
        matches!(self.status.as_str(), "running" | "waiting")
    }

    /// The approval waiting for a decision, if any.
    pub fn pending_approval(&self) -> Option<&ApprovalCard> {
        self.items.iter().rev().find_map(|i| match i {
            Item::Approval(a) if a.state == "requested" => Some(a),
            _ => None,
        })
    }

    fn tool_mut(&mut self, call_id: &str) -> Option<&mut ToolCard> {
        self.items.iter_mut().rev().find_map(|i| match i {
            Item::Tool(t) if t.call_id == call_id => Some(t),
            _ => None,
        })
    }

    /// Applies one event from the core. Unknown event types are ignored, so a newer core never breaks an older app.
    pub fn apply(&mut self, e: &Value) {
        let s = |k: &str| e.get(k).and_then(Value::as_str).unwrap_or("").to_owned();
        let f = |k: &str| e.get(k).and_then(Value::as_f64);
        match e.get("type").and_then(Value::as_str).unwrap_or("") {
            "run" => {
                if let Some(id) = e.get("task_id").and_then(Value::as_str) {
                    self.task_id = Some(id.to_owned());
                }
                let status = s("status");
                if status == "failed" || status == "cancelled" {
                    let why = s("reason");
                    self.items.push(Item::Note(if why.is_empty() {
                        format!("Task {status}.")
                    } else {
                        format!("Task {status}: {why}")
                    }));
                }
                self.status = status;
            }
            // Written by the history store for what the person typed; replaying a saved conversation applies it.
            "user" => self.push_user(&s("text")),
            "reasoning.delta" => match self.items.last_mut() {
                Some(Item::Thinking { text, done: false }) => text.push_str(&s("text")),
                _ => self.items.push(Item::Thinking {
                    text: s("text"),
                    done: false,
                }),
            },
            "reasoning.done" => {
                if let Some(Item::Thinking { done, .. }) = self
                    .items
                    .iter_mut()
                    .rev()
                    .find(|i| matches!(i, Item::Thinking { done: false, .. }))
                {
                    *done = true;
                }
            }
            "message.delta" => match self.items.last_mut() {
                Some(Item::Assistant { text, done: false }) => text.push_str(&s("text")),
                _ => self.items.push(Item::Assistant {
                    text: s("text"),
                    done: false,
                }),
            },
            "message.done" => {
                let full = s("text");
                match self
                    .items
                    .iter_mut()
                    .rev()
                    .find(|i| matches!(i, Item::Assistant { done: false, .. }))
                {
                    Some(Item::Assistant { text, done }) => {
                        if !full.is_empty() {
                            *text = full;
                        }
                        *done = true;
                    }
                    _ => {
                        if !full.is_empty() {
                            self.items.push(Item::Assistant {
                                text: full,
                                done: true,
                            });
                        }
                    }
                }
            }
            "tool_call.started" => self.items.push(Item::Tool(ToolCard {
                call_id: s("call_id"),
                name: s("name"),
                risk: String::new(),
                args: String::new(),
                status: "running".to_owned(),
                output: None,
                ms: None,
            })),
            "tool_call.args.delta" => {
                let id = s("call_id");
                let more = s("text");
                if let Some(t) = self.tool_mut(&id) {
                    t.args.push_str(&more);
                }
            }
            "usage" => {
                let n = |k: &str| e.get(k).and_then(Value::as_u64).unwrap_or(0);
                let step = n("step");
                let item = Item::Step {
                    step,
                    prompt: n("prompt"),
                    completion: n("completion"),
                    reasoning: n("reasoning"),
                    cost: f("cost"),
                };
                match self
                    .items
                    .iter_mut()
                    .find(|i| matches!(i, Item::Step { step: x, .. } if *x == step))
                {
                    Some(existing) => *existing = item,
                    None => self.items.push(item),
                }
            }
            "tool_call.finished" => {
                let id = s("call_id");
                if let Some(t) = self.tool_mut(&id) {
                    t.args = s("args");
                    t.risk = s("risk");
                    t.name = s("name");
                } else {
                    self.items.push(Item::Tool(ToolCard {
                        call_id: id,
                        name: s("name"),
                        risk: s("risk"),
                        args: s("args"),
                        status: "running".to_owned(),
                        output: None,
                        ms: None,
                    }));
                }
            }
            "tool_call.result" => {
                let id = s("call_id");
                let (status, output, ms) = (s("status"), s("output"), f("ms"));
                if let Some(t) = self.tool_mut(&id) {
                    t.status = status;
                    t.output = Some(output);
                    t.ms = ms;
                }
            }
            "approval" => {
                let id = s("approval_id");
                let state = s("state");
                let existing = self.items.iter_mut().find_map(|i| match i {
                    Item::Approval(a) if a.id == id => Some(a),
                    _ => None,
                });
                match existing {
                    Some(a) => a.state = state,
                    None => self.items.push(Item::Approval(ApprovalCard {
                        id,
                        hash: s("hash"),
                        summary: s("summary"),
                        state,
                    })),
                }
            }
            "cost" => {
                let card = Item::Cost {
                    task: f("task_total").unwrap_or(0.0),
                    day: f("day_total").unwrap_or(0.0),
                    task_cap: f("task_cap").unwrap_or(0.0),
                    day_cap: f("day_cap"),
                };
                match self
                    .items
                    .iter_mut()
                    .rev()
                    .find(|i| matches!(i, Item::Cost { .. }))
                {
                    Some(c) => *c = card,
                    None => self.items.push(card),
                }
            }
            "memory_recall" => {
                let n = e.get("items").and_then(Value::as_array).map_or(0, Vec::len);
                if n > 0 {
                    self.items.push(Item::Note(format!(
                        "Recalled {n} note{} from memory.",
                        if n == 1 { "" } else { "s" }
                    )));
                }
            }
            "memory_write" => self
                .items
                .push(Item::Note("Saved a summary to memory.".to_owned())),
            "error" => self.items.push(Item::Note(s("message"))),
            _ => {}
        }
    }
}

/// A unified diff-style view of a file write: every line of the new content marked with a plus. Only for the write tool.
pub fn write_diff(args: &str) -> Option<String> {
    let v: Value = serde_json::from_str(args).ok()?;
    let path = v.get("path")?.as_str()?;
    let content = v.get("content")?.as_str()?;
    let mut out = format!("+++ {path}\n");
    for line in content.lines().take(40) {
        out.push_str("+ ");
        out.push_str(line);
        out.push('\n');
    }
    Some(out)
}

/// Where a core lives and the token that opens it.
#[derive(Clone)]
pub struct Endpoint {
    pub base: String,
    token: String,
}

impl std::fmt::Debug for Endpoint {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Endpoint")
            .field("base", &self.base)
            .finish()
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Started {
    pub task_id: String,
    pub session_id: String,
}

fn plain(status: u16) -> String {
    match status {
        401 => "The core refused the access token.".to_owned(),
        403 => "Your IT policy does not allow that.".to_owned(),
        409 => "A task is already running in this session.".to_owned(),
        s if s >= 500 => "The core had an error. Try again.".to_owned(),
        s => format!("The core answered with an error ({s})."),
    }
}

/// A route's status, its headers (names lowercased) and its body as text.
pub type RawReply = (u16, Vec<(String, String)>, String);

impl Endpoint {
    pub fn new(base: &str, token: &str) -> Self {
        Self {
            base: base.trim_end_matches('/').to_owned(),
            token: token.to_owned(),
        }
    }

    fn agent(timeout: Option<Duration>) -> ureq::Agent {
        crate::http::agent(timeout)
    }

    fn json(&self, method: &str, path: &str, body: Option<&Value>) -> Result<(u16, Value), String> {
        let agent = Self::agent(Some(Duration::from_secs(30)));
        let url = format!("{}{}", self.base, path);
        let auth = format!("Bearer {}", self.token);
        let res = match (method, body) {
            ("POST", Some(b)) => agent
                .post(&url)
                .header("authorization", &auth)
                .header("content-type", "application/json")
                .send(b.to_string()),
            ("POST", None) => agent.post(&url).header("authorization", &auth).send_empty(),
            ("PUT", Some(b)) => agent
                .put(&url)
                .header("authorization", &auth)
                .header("content-type", "application/json")
                .send(b.to_string()),
            _ => agent.get(&url).header("authorization", &auth).call(),
        };
        let mut res = res.map_err(|e| crate::http::unreachable("The core", method, path, &e))?;
        let status = res.status().as_u16();
        crate::http::answered("core", method, path, status);
        let text = res.body_mut().read_to_string().unwrap_or_default();
        Ok((status, serde_json::from_str(&text).unwrap_or(Value::Null)))
    }

    pub fn start_task(&self, goal: &str, session_id: &str) -> Result<Started, String> {
        let (status, body) = self.json(
            "POST",
            "/v1/tasks",
            Some(&serde_json::json!({ "goal": goal, "session_id": session_id })),
        )?;
        if status != 202 {
            return Err(body["error"]["message"]
                .as_str()
                .map_or_else(|| plain(status), str::to_owned));
        }
        Ok(Started {
            task_id: body["task_id"].as_str().unwrap_or("").to_owned(),
            session_id: body["session_id"].as_str().unwrap_or(session_id).to_owned(),
        })
    }

    pub fn decide(
        &self,
        session_id: &str,
        approval_id: &str,
        hash: &str,
        approved: bool,
        note: &str,
    ) -> Result<(), String> {
        let mut payload = serde_json::json!({ "hash": hash, "approved": approved });
        if !note.trim().is_empty() {
            payload["note"] = Value::String(note.trim().chars().take(500).collect());
        }
        let (status, body) = self.json(
            "POST",
            &format!("/v1/sessions/{session_id}/approvals/{approval_id}"),
            Some(&payload),
        )?;
        if status == 200 {
            Ok(())
        } else {
            Err(body["error"]["message"]
                .as_str()
                .map_or_else(|| plain(status), str::to_owned))
        }
    }

    /// Saves non-secret settings (model names, the custom endpoint address). Returns the settings as the core now holds them.
    pub fn put_settings(&self, changes: &Value) -> Result<Value, String> {
        let (status, body) = self.json("PUT", "/v1/settings", Some(changes))?;
        if status == 200 {
            Ok(body)
        } else {
            Err(body["error"]["message"]
                .as_str()
                .map_or_else(|| plain(status), str::to_owned))
        }
    }

    /// Hands a key to a self-hosted or local core, which keeps it in memory. A cloud core has no such route.
    pub fn load_secret(&self, slot: &str, value: &str) -> Result<(), String> {
        let (status, _) = self.json(
            "POST",
            "/v1/secrets/load",
            Some(&serde_json::json!({ "secrets": { slot: value } })),
        )?;
        match status {
            200 => Ok(()),
            404 => Err("A cloud core keeps its keys in its own secret store. Replace them with `oc setup`.".to_owned()),
            s => Err(plain(s)),
        }
    }

    pub fn cancel(&self, session_id: &str, task_id: &str) -> Result<(), String> {
        let (status, _) = self.json(
            "POST",
            &format!("/v1/sessions/{session_id}/tasks/{task_id}/cancel"),
            None,
        )?;
        if status == 200 {
            Ok(())
        } else {
            Err(plain(status))
        }
    }

    /// What the core reports about its own bindings (including whether it routes through an AI Gateway).
    pub fn bindings(&self) -> Result<Value, String> {
        let (status, body) = self.json("GET", "/v1/diag/bindings", None)?;
        if status == 200 {
            Ok(body)
        } else {
            Err(plain(status))
        }
    }

    /// A request to one of the core's own routes with the answer left as text and the response headers kept (names lowercased).
    pub fn raw(&self, method: &str, path: &str) -> Result<RawReply, String> {
        let agent = Self::agent(Some(Duration::from_secs(60)));
        let url = format!("{}{}", self.base, path);
        let auth = format!("Bearer {}", self.token);
        let res = if method == "POST" {
            agent.post(&url).header("authorization", &auth).send_empty()
        } else {
            agent.get(&url).header("authorization", &auth).call()
        };
        let mut res = res.map_err(|e| crate::http::unreachable("The core", method, path, &e))?;
        let status = res.status().as_u16();
        crate::http::answered("core", method, path, status);
        let headers = res
            .headers()
            .iter()
            .map(|(k, v)| {
                (
                    k.as_str().to_ascii_lowercase(),
                    v.to_str().unwrap_or("").to_owned(),
                )
            })
            .collect();
        let text = res.body_mut().read_to_string().unwrap_or_default();
        Ok((status, headers, text))
    }

    /// A GET on one of the core's own routes: the status and the JSON body.
    pub fn get(&self, path: &str) -> Result<(u16, Value), String> {
        self.json("GET", path, None)
    }

    pub fn settings(&self) -> Result<Value, String> {
        let (status, body) = self.json("GET", "/v1/settings", None)?;
        if status == 200 {
            Ok(body)
        } else {
            Err(plain(status))
        }
    }

    /// Sets the daily spend cap in dollars, or clears it with `None` (back to the default).
    pub fn set_daily_cap(&self, usd: Option<f64>) -> Result<Value, String> {
        let (status, body) = self.json(
            "PUT",
            "/v1/settings",
            Some(&serde_json::json!({ "daily_cap_usd": usd })),
        )?;
        if status == 200 {
            Ok(body)
        } else {
            Err(body["error"]["message"]
                .as_str()
                .map_or_else(|| plain(status), str::to_owned))
        }
    }

    /// Reads the session's event stream until the core closes it, calling `on_event` for each event.
    pub fn stream_events(
        &self,
        session_id: &str,
        mut on_event: impl FnMut(&Value),
    ) -> Result<(), String> {
        let agent = Self::agent(None);
        let url = format!("{}/v1/sessions/{}/events", self.base, session_id);
        let res = agent
            .get(&url)
            .header("authorization", &format!("Bearer {}", self.token))
            .call()
            .map_err(|e| {
                crate::http::unreachable(
                    "The core",
                    "GET",
                    &format!("/v1/sessions/{session_id}/events"),
                    &e,
                )
            })?;
        if res.status().as_u16() != 200 {
            return Err(plain(res.status().as_u16()));
        }
        let reader = BufReader::new(res.into_body().into_reader());
        for line in reader.lines() {
            let Ok(line) = line else { break };
            if let Some(data) = line.strip_prefix("data:")
                && let Ok(v) = serde_json::from_str::<Value>(data.trim())
            {
                on_event(&v);
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn run(events: &[Value]) -> Chat {
        let mut c = Chat::default();
        c.push_user("do the thing");
        for e in events {
            c.apply(e);
        }
        c
    }

    #[test]
    fn streams_thinking_and_the_answer_into_single_items() {
        let c = run(&[
            json!({"type":"run","status":"running"}),
            json!({"type":"reasoning.delta","text":"Let me "}),
            json!({"type":"reasoning.delta","text":"think."}),
            json!({"type":"reasoning.done"}),
            json!({"type":"message.delta","text":"All "}),
            json!({"type":"message.delta","text":"done."}),
            json!({"type":"message.done","text":"All done."}),
            json!({"type":"run","status":"done"}),
        ]);
        assert_eq!(
            c.items[1],
            Item::Thinking {
                text: "Let me think.".into(),
                done: true
            }
        );
        assert_eq!(
            c.items[2],
            Item::Assistant {
                text: "All done.".into(),
                done: true
            }
        );
        assert_eq!(c.status, "done");
        assert!(!c.is_busy());
    }

    #[test]
    fn a_tool_call_becomes_one_card_that_fills_in_as_events_arrive() {
        let c = run(&[
            json!({"type":"tool_call.started","call_id":"c1","name":"sandbox.exec"}),
            json!({"type":"tool_call.finished","call_id":"c1","name":"sandbox.exec","args":"{\"command\":\"ls\"}","risk":"exec"}),
            json!({"type":"tool_call.result","call_id":"c1","status":"ok","output":"a.txt","ms":12.0}),
        ]);
        let tools: Vec<_> = c
            .items
            .iter()
            .filter(|i| matches!(i, Item::Tool(_)))
            .collect();
        assert_eq!(tools.len(), 1);
        let Item::Tool(t) = tools[0] else {
            unreachable!()
        };
        assert_eq!(
            (
                t.name.as_str(),
                t.risk.as_str(),
                t.status.as_str(),
                t.output.as_deref(),
                t.ms
            ),
            ("sandbox.exec", "exec", "ok", Some("a.txt"), Some(12.0))
        );
    }

    #[test]
    fn an_approval_waits_then_settles_in_place_and_is_found_while_pending() {
        let mut c = run(&[
            json!({"type":"approval","approval_id":"a1","kind":"tool","state":"requested","hash":"h1","summary":"sandbox.exec ls"}),
            json!({"type":"run","status":"waiting"}),
        ]);
        assert!(c.is_busy());
        assert_eq!(c.pending_approval().map(|a| a.hash.as_str()), Some("h1"));
        c.apply(&json!({"type":"approval","approval_id":"a1","kind":"tool","state":"approved","hash":"h1","summary":"sandbox.exec ls"}));
        assert!(c.pending_approval().is_none());
        assert_eq!(
            c.items
                .iter()
                .filter(|i| matches!(i, Item::Approval(_)))
                .count(),
            1
        );
    }

    #[test]
    fn cost_updates_one_line_and_failures_are_said_plainly() {
        let c = run(&[
            json!({"type":"cost","task_total":0.001,"day_total":0.01,"task_cap":1.0,"day_cap":5.0}),
            json!({"type":"cost","task_total":0.002,"day_total":0.011,"task_cap":1.0,"day_cap":5.0}),
            json!({"type":"run","status":"failed","reason":"The daily cap is reached."}),
        ]);
        assert_eq!(
            c.items
                .iter()
                .filter(|i| matches!(i, Item::Cost { .. }))
                .count(),
            1
        );
        assert!(
            matches!(c.items.iter().find(|i| matches!(i, Item::Cost { .. })), Some(Item::Cost { task, .. }) if (*task - 0.002).abs() < 1e-9)
        );
        assert!(
            c.items.iter().any(
                |i| matches!(i, Item::Note(n) if n == "Task failed: The daily cap is reached.")
            )
        );
    }

    #[test]
    fn unknown_events_are_ignored_and_memory_notes_are_plain() {
        let c = run(&[
            json!({"type":"from_the_future","x":1}),
            json!({"type":"memory_recall","items":[{"id":"a"},{"id":"b"}]}),
            json!({"type":"memory_write","id":"i","tier":"history"}),
        ]);
        assert_eq!(c.items.len(), 3);
        assert!(matches!(&c.items[1], Item::Note(n) if n == "Recalled 2 notes from memory."));
    }

    #[test]
    fn the_endpoint_debug_output_never_shows_the_token() {
        let e = Endpoint::new("https://core.example/", "tok-secret-xyz");
        assert!(!format!("{e:?}").contains("tok-secret-xyz"));
        assert_eq!(e.base, "https://core.example");
    }

    /// Against a live core: `OC_CLOUD_URL=... OC_CLOUD_TOKEN=... cargo test -p shell-core --lib live_ -- --ignored --nocapture`.
    #[test]
    #[ignore = "needs a live core and spends a fraction of a cent"]
    fn live_task_streams_into_the_chat_model() {
        let (Ok(url), Ok(token)) = (
            std::env::var("OC_CLOUD_URL"),
            std::env::var("OC_CLOUD_TOKEN"),
        ) else {
            return;
        };
        let ep = Endpoint::new(&url, &token);
        let started = ep
            .start_task(
                "Reply with the single word: ok",
                &format!("live-test-{}", std::process::id()),
            )
            .expect("task starts");
        let mut chat = Chat::default();
        chat.push_user("Reply with the single word: ok");
        ep.stream_events(&started.session_id, |e| chat.apply(e))
            .expect("stream reads");
        eprintln!("{:#?}", chat.items);
        assert_eq!(chat.status, "done");
        assert!(
            chat.items
                .iter()
                .any(|i| matches!(i, Item::Assistant { done: true, .. }))
        );
    }

    #[test]
    fn tool_arguments_stream_in_and_a_write_has_a_diff_view() {
        let c = run(&[
            json!({"type":"tool_call.started","call_id":"c1","name":"sandbox.write_file"}),
            json!({"type":"tool_call.args.delta","call_id":"c1","text":"{\"path\":\"a.txt\","}),
            json!({"type":"tool_call.args.delta","call_id":"c1","text":"\"content\":\"one\\ntwo\"}"}),
        ]);
        let Item::Tool(t) = c.items.iter().find(|i| matches!(i, Item::Tool(_))).unwrap() else {
            unreachable!()
        };
        assert_eq!(
            write_diff(&t.args).as_deref(),
            Some("+++ a.txt\n+ one\n+ two\n")
        );
        assert_eq!(write_diff("not json"), None);
        assert_eq!(write_diff("{\"command\":\"ls\"}"), None);
    }

    #[test]
    fn each_step_reports_its_tokens_and_cost_once_and_the_task_id_is_kept_for_stop() {
        let c = run(&[
            json!({"type":"run","status":"running","task_id":"t-1"}),
            json!({"type":"usage","step":0,"prompt":10,"completion":5,"reasoning":2,"cached":0,"cost":0.001,"estimated":false}),
            json!({"type":"usage","step":0,"prompt":10,"completion":6,"reasoning":2,"cached":0,"cost":0.002,"estimated":false}),
            json!({"type":"usage","step":1,"prompt":20,"completion":3,"reasoning":0,"cached":0,"cost":null,"estimated":false}),
        ]);
        let steps: Vec<_> = c
            .items
            .iter()
            .filter(|i| matches!(i, Item::Step { .. }))
            .collect();
        assert_eq!(steps.len(), 2);
        assert!(matches!(steps[0], Item::Step { completion: 6, .. }));
        assert!(matches!(steps[1], Item::Step { cost: None, .. }));
        assert_eq!(c.task_id.as_deref(), Some("t-1"));
    }

    #[test]
    fn working_is_shown_until_the_model_emits_reasoning_and_blocks_toggle() {
        let mut c = run(&[json!({"type":"run","status":"running"})]);
        assert!(!c.has_reasoning());
        c.apply(&json!({"type":"reasoning.delta","text":"hm"}));
        assert!(c.has_reasoning());
        c.toggle(1);
        assert!(c.open.contains(&1));
        c.toggle(1);
        assert!(!c.open.contains(&1));
    }
}
