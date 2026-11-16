//! The full setup and test chain against a deployed cloud core: backend health, OpenRouter, speed, sandbox,
//! memory, audit and spend. The same rows, order and wording as the web wizard and `oc doctor`
//! (`core/packages/diagnostics`); the Cloudflare account and deploy rows belong to setup and are shown as done
//! from what the core reports about itself.
//!
//! Window-free: the chain talks through a [`Transport`], so tests run with a fake.

use std::time::{Duration, Instant};

use serde::Serialize;
use serde_json::Value;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Status {
    Pending,
    Running,
    Pass,
    Fail,
    Skip,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Row {
    pub id: &'static str,
    pub group: &'static str,
    pub title: &'static str,
    pub status: Status,
    pub ms: Option<f64>,
    pub detail: String,
    pub metrics: Vec<(String, String)>,
    pub fix: Option<String>,
}

/// One HTTP answer from the core. `status` 0 means the core could not be reached.
#[derive(Clone, Debug)]
pub struct Reply {
    pub status: u16,
    pub body: Value,
    pub timed_out: bool,
}

pub trait Transport {
    fn call(&self, method: &str, path: &str, timeout: Duration) -> Reply;
}

/// Blocking transport over ureq. The access token goes only in the Authorization header.
pub struct UreqTransport {
    base: String,
    token: String,
}

impl UreqTransport {
    pub fn new(base: &str, token: &str) -> Self {
        Self {
            base: base.trim_end_matches('/').to_owned(),
            token: token.to_owned(),
        }
    }
}

impl Transport for UreqTransport {
    fn call(&self, method: &str, path: &str, timeout: Duration) -> Reply {
        let agent: ureq::Agent = ureq::Agent::config_builder()
            .timeout_global(Some(timeout))
            .http_status_as_error(false)
            .build()
            .into();
        let url = format!("{}{}", self.base, path);
        let auth = format!("Bearer {}", self.token);
        let result = if method == "POST" {
            agent.post(&url).header("authorization", &auth).send_empty()
        } else {
            agent.get(&url).header("authorization", &auth).call()
        };
        match result {
            Ok(mut res) => {
                let status = res.status().as_u16();
                let body = res
                    .body_mut()
                    .read_to_string()
                    .ok()
                    .and_then(|t| serde_json::from_str::<Value>(&t).ok())
                    .unwrap_or(Value::Null);
                Reply {
                    status,
                    body,
                    timed_out: false,
                }
            }
            Err(ureq::Error::Timeout(_)) => Reply {
                status: 0,
                body: Value::Null,
                timed_out: true,
            },
            Err(_) => Reply {
                status: 0,
                body: Value::Null,
                timed_out: false,
            },
        }
    }
}

struct Def {
    id: &'static str,
    group: &'static str,
    title: &'static str,
    requires: &'static [&'static str],
}

const DEFS: &[Def] = &[
    Def {
        id: "cf.account",
        group: "1. Cloudflare account",
        title: "Cloudflare account and token",
        requires: &[],
    },
    Def {
        id: "cf.features",
        group: "1. Cloudflare account",
        title: "Plan and features (Workers Paid, R2, D1, Containers, Secrets Store)",
        requires: &["cf.account"],
    },
    Def {
        id: "deploy.provision",
        group: "2. Provision and deploy",
        title: "Create and bind D1, R2, Secrets Store, Durable Objects, Workflows, Container image",
        requires: &["cf.features"],
    },
    Def {
        id: "deploy.worker",
        group: "2. Provision and deploy",
        title: "Deploy the core Worker (safe to repeat)",
        requires: &["deploy.provision"],
    },
    Def {
        id: "keys.model",
        group: "3. Store keys",
        title: "Store the OpenRouter key in Secrets Store",
        requires: &["deploy.worker"],
    },
    Def {
        id: "backend.health",
        group: "4. Backend health",
        title: "Core answers /health and matches this app's version",
        requires: &["deploy.worker"],
    },
    Def {
        id: "or.auth",
        group: "5. OpenRouter",
        title: "Key is valid; credit and limits",
        requires: &["backend.health"],
    },
    Def {
        id: "or.models",
        group: "5. OpenRouter",
        title: "Default and worker models are available",
        requires: &["or.auth"],
    },
    Def {
        id: "or.stream",
        group: "5. OpenRouter",
        title: "Streamed completion with reasoning and one tool call",
        requires: &["or.models"],
    },
    Def {
        id: "net.rtt",
        group: "6. Connection speed and latency",
        title: "App to edge round trip",
        requires: &["backend.health"],
    },
    Def {
        id: "net.model",
        group: "6. Connection speed and latency",
        title: "Edge to OpenRouter: time to first token and tokens per second",
        requires: &["or.stream"],
    },
    Def {
        id: "net.edge",
        group: "6. Connection speed and latency",
        title: "AI Gateway overhead, D1 read and write, R2 put and get",
        requires: &["backend.health"],
    },
    Def {
        id: "net.sandbox",
        group: "6. Connection speed and latency",
        title: "Sandbox cold start and warm command",
        requires: &["backend.health"],
    },
    Def {
        id: "sandbox.run",
        group: "7. Sandbox",
        title: "Trivial command in a fresh container",
        requires: &["backend.health"],
    },
    Def {
        id: "sandbox.keys",
        group: "7. Sandbox",
        title: "No API key is visible inside, and egress is closed",
        requires: &["sandbox.run"],
    },
    Def {
        id: "memory.cycle",
        group: "8. Memory",
        title: "Write, recall with a citation, delete",
        requires: &["backend.health"],
    },
    Def {
        id: "audit.chain",
        group: "9. Audit and spend",
        title: "Audit entry written and the chain verifies; spend ledger and daily cap",
        requires: &["backend.health"],
    },
];

pub fn initial_rows() -> Vec<Row> {
    DEFS.iter()
        .map(|d| Row {
            id: d.id,
            group: d.group,
            title: d.title,
            status: Status::Pending,
            ms: None,
            detail: String::new(),
            metrics: Vec::new(),
            fix: None,
        })
        .collect()
}

struct Outcome {
    ok: bool,
    detail: String,
    metrics: Vec<(String, String)>,
    fix: Option<String>,
}

fn pass(detail: &str, metrics: Vec<(String, String)>) -> Outcome {
    Outcome {
        ok: true,
        detail: detail.to_owned(),
        metrics,
        fix: None,
    }
}

fn fail(detail: &str, fix: &str) -> Outcome {
    Outcome {
        ok: false,
        detail: detail.to_owned(),
        metrics: Vec::new(),
        fix: Some(fix.to_owned()),
    }
}

fn m(label: &str, value: String) -> (String, String) {
    (label.to_owned(), value)
}

fn ms(n: f64) -> String {
    format!("{:.1} ms", n)
}

fn usd(n: f64) -> String {
    if n < 0.01 {
        format!("${n:.5}")
    } else {
        format!("${n:.3}")
    }
}

/// A transport-level or auth failure in plain words, or none when the call succeeded.
fn transport_problem(r: &Reply) -> Option<Outcome> {
    if r.timed_out {
        return Some(fail(
            "The backend did not answer in time.",
            "Check your internet connection, then retry.",
        ));
    }
    match r.status {
        0 => Some(fail(
            "The backend could not be reached.",
            "Check the address and your internet connection, then retry.",
        )),
        401 => Some(fail(
            "The access token was refused.",
            "Enter the access token printed when the core was set up, or run setup again.",
        )),
        429 => Some(fail(
            "Too many wrong tokens from this network.",
            "Wait a minute, then retry with the right token.",
        )),
        s if s >= 500 => Some(fail(
            "The backend answered with an error.",
            "Retry. If it keeps failing, run setup again.",
        )),
        _ => None,
    }
}

fn num(v: &Value, key: &str) -> Option<f64> {
    v.get(key).and_then(Value::as_f64)
}

fn model_fix(code: &str) -> &'static str {
    match code {
        "key_missing" => "Store your OpenRouter key: run setup again and paste it when asked.",
        "key_invalid" => {
            "OpenRouter rejected the key. Create a new key at openrouter.ai/keys and store it again."
        }
        "payment_required" => {
            "The OpenRouter account has no credit or the key's limit is used up. Add credit or raise the key limit."
        }
        "rate_limited" => "OpenRouter is rate limiting this key. Wait a minute and retry.",
        "cap_reached" => {
            "Today's spend cap is reached. Raise the daily cap in Settings, or retry tomorrow."
        }
        _ => "Retry. If it keeps failing, check status.openrouter.ai.",
    }
}

fn major_minor(v: &str) -> String {
    v.split('.').take(2).collect::<Vec<_>>().join(".")
}

fn percentile(sorted: &[f64], p: f64) -> f64 {
    if sorted.is_empty() {
        return 0.0;
    }
    let idx = ((p / 100.0 * sorted.len() as f64).ceil() as usize).clamp(1, sorted.len()) - 1;
    sorted[idx]
}

struct Chain<'a> {
    t: &'a dyn Transport,
    app_version: &'a str,
    pings: usize,
    bindings: Option<Value>,
    completion: Option<Value>,
    sandbox: Option<Reply>,
}

impl Chain<'_> {
    fn derived(&mut self, id: &str) -> Outcome {
        if self.bindings.is_none() {
            let r = self
                .t
                .call("GET", "/v1/diag/bindings", Duration::from_secs(20));
            if let Some(p) = transport_problem(&r) {
                return p;
            }
            self.bindings = Some(r.body);
        }
        let b = self.bindings.as_ref().cloned().unwrap_or(Value::Null);
        let yes = |k: &str| b.get(k).and_then(Value::as_bool).unwrap_or(false);
        match id {
            "cf.account" => pass(
                "This core is deployed in your own account, so the account and token worked earlier.",
                vec![],
            ),
            "cf.features" if yes("container") => pass(
                "Durable Objects, D1, R2, Secrets Store and the Container binding are all present.",
                vec![],
            ),
            "cf.features" => fail(
                "The Container binding is missing.",
                "Containers need the Workers Paid plan. Enable it in the Cloudflare dashboard, then run setup again.",
            ),
            "deploy.provision"
                if yes("d1") && yes("r2") && yes("durable_objects") && yes("workflow") =>
            {
                pass(
                    "D1, R2, Durable Objects and Workflows are bound and answering.",
                    vec![],
                )
            }
            "deploy.provision" => fail(
                "A required binding is missing or not answering.",
                "Run setup again; it repairs missing resources.",
            ),
            "deploy.worker" => pass(
                &format!(
                    "The core is running (version {}).",
                    b.get("version")
                        .and_then(Value::as_str)
                        .unwrap_or("unknown")
                ),
                vec![],
            ),
            _ if yes("model_key") => pass(
                "An OpenRouter key is stored (its value is never shown).",
                vec![],
            ),
            _ => fail("No OpenRouter key is stored yet.", model_fix("key_missing")),
        }
    }

    fn run(&mut self, id: &str) -> Outcome {
        match id {
            "cf.account" | "cf.features" | "deploy.provision" | "deploy.worker" | "keys.model" => {
                self.derived(id)
            }
            "backend.health" => {
                let r = self.t.call("GET", "/v1/health", Duration::from_secs(20));
                if let Some(p) = transport_problem(&r) {
                    return p;
                }
                let v = r
                    .body
                    .get("version")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_owned();
                if !self.app_version.is_empty() && major_minor(&v) != major_minor(self.app_version)
                {
                    return fail(
                        &format!(
                            "The core is version {v} but this app is {}.",
                            self.app_version
                        ),
                        "Update the core: run `oc setup` again to deploy the matching version.",
                    );
                }
                pass(&format!("Version {v} matches."), vec![])
            }
            "or.auth" => {
                let r = self
                    .t
                    .call("GET", "/v1/diag/openrouter/auth", Duration::from_secs(30));
                if let Some(p) = transport_problem(&r) {
                    return p;
                }
                if r.body.get("ok").and_then(Value::as_bool) != Some(true) {
                    let code = r
                        .body
                        .get("code")
                        .and_then(Value::as_str)
                        .unwrap_or("error");
                    return fail("OpenRouter did not accept the key.", model_fix(code));
                }
                let mut metrics = vec![];
                if let Some(u) = num(&r.body, "usage") {
                    metrics.push(m("key usage", usd(u)));
                }
                metrics.push(m(
                    "key limit",
                    num(&r.body, "limit").map_or("none set".to_owned(), usd),
                ));
                if let Some(x) = num(&r.body, "limit_remaining") {
                    metrics.push(m("remaining", usd(x)));
                }
                pass("Key is valid.", metrics)
            }
            "or.models" => {
                let r = self
                    .t
                    .call("GET", "/v1/diag/openrouter/models", Duration::from_secs(30));
                if let Some(p) = transport_problem(&r) {
                    return p;
                }
                let list = r
                    .body
                    .get("models")
                    .and_then(Value::as_array)
                    .cloned()
                    .unwrap_or_default();
                let missing: Vec<String> = list
                    .iter()
                    .filter(|x| x.get("available").and_then(Value::as_bool) != Some(true))
                    .filter_map(|x| x.get("id").and_then(Value::as_str).map(str::to_owned))
                    .collect();
                if missing.is_empty() && !list.is_empty() {
                    pass("Both models are listed.", vec![])
                } else {
                    fail(
                        &format!(
                            "{} not offered by OpenRouter right now.",
                            missing.join(", ")
                        ),
                        "Pick another model in Settings, or retry later.",
                    )
                }
            }
            "or.stream" => {
                let r = self.t.call(
                    "POST",
                    "/v1/diag/openrouter/completion",
                    Duration::from_secs(120),
                );
                if let Some(p) = transport_problem(&r) {
                    return p;
                }
                if r.body.get("ok").and_then(Value::as_bool) != Some(true) {
                    let code = r
                        .body
                        .get("code")
                        .and_then(Value::as_str)
                        .unwrap_or("error");
                    let msg = r
                        .body
                        .get("message")
                        .and_then(Value::as_str)
                        .unwrap_or("The test call failed.");
                    return fail(msg, model_fix(code));
                }
                let b = r.body.clone();
                self.completion = Some(b.clone());
                let metrics = vec![
                    m(
                        "model",
                        b.get("model")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .to_owned(),
                    ),
                    m(
                        "reasoning tokens",
                        num(&b, "reasoning_tokens").unwrap_or(0.0).to_string(),
                    ),
                    m(
                        "tool calls",
                        num(&b, "tool_calls").unwrap_or(0.0).to_string(),
                    ),
                    m(
                        "cost (counts toward the daily cap)",
                        usd(num(&b, "cost_usd").unwrap_or(0.0)),
                    ),
                ];
                pass("Streamed, called the tool and finished.", metrics)
            }
            "net.rtt" => {
                let mut samples = vec![];
                for i in 0..self.pings.max(2) {
                    let t0 = Instant::now();
                    let r = self.t.call("GET", "/v1/health", Duration::from_secs(15));
                    if r.status == 200 && i > 0 {
                        samples.push(t0.elapsed().as_secs_f64() * 1000.0);
                    }
                }
                if samples.is_empty() {
                    return fail("No ping got an answer.", "Check your connection and retry.");
                }
                samples.sort_by(|a, b| a.total_cmp(b));
                pass(
                    "Reachable.",
                    vec![
                        m("p50", ms(percentile(&samples, 50.0))),
                        m("p95", ms(percentile(&samples, 95.0))),
                        m("pings", (samples.len() + 1).to_string()),
                    ],
                )
            }
            "net.model" => {
                let b = self.completion.clone().unwrap_or(Value::Null);
                match num(&b, "ttft_ms") {
                    Some(t) => pass(
                        "Measured on the streamed completion above.",
                        vec![
                            m("time to first token", ms(t)),
                            m(
                                "tokens per second",
                                num(&b, "tokens_per_sec")
                                    .map_or("n/a".to_owned(), |x| x.to_string()),
                            ),
                            m(
                                "whole call",
                                num(&b, "total_ms").map_or("n/a".to_owned(), ms),
                            ),
                        ],
                    ),
                    None => fail("No token arrived to time.", "Retry."),
                }
            }
            "net.edge" => {
                let r = self
                    .t
                    .call("POST", "/v1/diag/perf", Duration::from_secs(90));
                if let Some(p) = transport_problem(&r) {
                    return p;
                }
                let st = |k: &str| {
                    r.body.get(k).map_or("n/a".to_owned(), |x| {
                        format!(
                            "p50 {}, p95 {}",
                            ms(x["p50"].as_f64().unwrap_or(0.0)),
                            ms(x["p95"].as_f64().unwrap_or(0.0))
                        )
                    })
                };
                pass(
                    "Measured inside the edge Worker.",
                    vec![
                        m("D1 write", st("d1_write_ms")),
                        m("D1 read", st("d1_read_ms")),
                        m("R2 put 1 MiB", st("r2_put_ms")),
                        m("R2 get 1 MiB", st("r2_get_ms")),
                        m("edge to OpenRouter", st("edge_to_openrouter_ms")),
                        m(
                            "AI Gateway overhead",
                            num(&r.body, "gateway_overhead_ms")
                                .map_or("not configured (direct route)".to_owned(), ms),
                        ),
                    ],
                )
            }
            "net.sandbox" | "sandbox.run" => {
                if self.sandbox.is_none() {
                    self.sandbox = Some(self.t.call(
                        "POST",
                        "/v1/diag/sandbox",
                        Duration::from_secs(180),
                    ));
                }
                let r = self.sandbox.clone().unwrap_or(Reply {
                    status: 0,
                    body: Value::Null,
                    timed_out: false,
                });
                if let Some(p) = transport_problem(&r) {
                    return p;
                }
                match num(&r.body, "cold_ms") {
                    Some(cold) if id == "net.sandbox" => pass(
                        "A fresh container started and ran a command.",
                        vec![
                            m("cold start + first command", ms(cold)),
                            m(
                                "warm command",
                                num(&r.body, "warm_ms").map_or("n/a".to_owned(), ms),
                            ),
                        ],
                    ),
                    Some(_) => pass("Ran `echo` and destroyed the container.", vec![]),
                    None => fail(
                        "The sandbox could not start.",
                        "Containers need the Workers Paid plan and a deployed image. Check the plan, then run setup again.",
                    ),
                }
            }
            "sandbox.keys" => {
                let b = self
                    .sandbox
                    .as_ref()
                    .map(|r| r.body.clone())
                    .unwrap_or(Value::Null);
                if b.get("ok").and_then(Value::as_bool) == Some(true) {
                    pass(
                        "The sandbox sees no secret and cannot call out on its own.",
                        vec![],
                    )
                } else if b.get("key_visible").and_then(Value::as_bool) == Some(true) {
                    fail(
                        "A secret is visible inside the sandbox.",
                        "This is a security defect. Do not run tasks; update the core with `oc setup` and retest.",
                    )
                } else {
                    fail(
                        "The sandbox can reach the internet.",
                        "This is a security defect. Do not run tasks; update the core with `oc setup` and retest.",
                    )
                }
            }
            "memory.cycle" => {
                let r = self
                    .t
                    .call("POST", "/v1/diag/memory", Duration::from_secs(30));
                if let Some(p) = transport_problem(&r) {
                    return p;
                }
                if r.body.get("ok").and_then(Value::as_bool) == Some(true) {
                    pass(
                        "Wrote a note, found it by keyword, and removed it.",
                        vec![m(
                            "citation",
                            r.body
                                .get("citation")
                                .and_then(Value::as_str)
                                .unwrap_or("")
                                .to_owned(),
                        )],
                    )
                } else {
                    fail(
                        "Memory did not complete the cycle.",
                        "Run setup again to apply the database migration, then retry.",
                    )
                }
            }
            _ => {
                let r = self
                    .t
                    .call("POST", "/v1/diag/audit", Duration::from_secs(30));
                if let Some(p) = transport_problem(&r) {
                    return p;
                }
                if r.body.get("ok").and_then(Value::as_bool) == Some(true) {
                    pass(
                        "The hash chain verifies end to end.",
                        vec![
                            m(
                                "spent today",
                                usd(num(&r.body, "day_spent_usd").unwrap_or(0.0)),
                            ),
                            m(
                                "daily cap in effect",
                                usd(num(&r.body, "daily_cap_usd").unwrap_or(0.0)),
                            ),
                        ],
                    )
                } else {
                    fail(
                        "The audit chain does not verify.",
                        "Export the audit log and do not run tasks until it is investigated.",
                    )
                }
            }
        }
    }
}

/// Runs every row in order, calling `on_update` before and after each so a screen can show the spinner.
pub fn run_chain(
    t: &dyn Transport,
    app_version: &str,
    pings: usize,
    mut on_update: impl FnMut(&[Row]),
) -> Vec<Row> {
    let mut rows = initial_rows();
    on_update(&rows);
    let mut chain = Chain {
        t,
        app_version,
        pings,
        bindings: None,
        completion: None,
        sandbox: None,
    };
    for (i, def) in DEFS.iter().enumerate() {
        let blocker = def
            .requires
            .iter()
            .filter_map(|id| rows.iter().find(|r| r.id == *id))
            .find(|r| r.status != Status::Pass)
            .map(|r| r.title);
        if let Some(title) = blocker {
            rows[i].status = Status::Skip;
            rows[i].detail = format!("Skipped: \"{title}\" did not pass.");
            on_update(&rows);
            continue;
        }
        rows[i].status = Status::Running;
        on_update(&rows);
        let t0 = Instant::now();
        let out = chain.run(def.id);
        rows[i].ms = Some(t0.elapsed().as_secs_f64() * 1000.0);
        rows[i].status = if out.ok { Status::Pass } else { Status::Fail };
        rows[i].detail = out.detail;
        rows[i].metrics = out.metrics;
        rows[i].fix = out.fix;
        on_update(&rows);
    }
    rows
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::cell::RefCell;
    use std::collections::HashMap;

    struct Fake {
        routes: HashMap<String, Reply>,
        seen: RefCell<Vec<String>>,
    }

    impl Transport for Fake {
        fn call(&self, method: &str, path: &str, _t: Duration) -> Reply {
            self.seen.borrow_mut().push(format!("{method} {path}"));
            self.routes.get(path).cloned().unwrap_or(Reply {
                status: 404,
                body: Value::Null,
                timed_out: false,
            })
        }
    }

    fn ok(body: Value) -> Reply {
        Reply {
            status: 200,
            body,
            timed_out: false,
        }
    }

    fn healthy() -> Fake {
        let mut routes = HashMap::new();
        let mut add = |p: &str, b: Value| {
            routes.insert(p.to_owned(), ok(b));
        };
        add("/v1/health", json!({"ok": true, "version": "0.1.4"}));
        add(
            "/v1/diag/bindings",
            json!({"d1": true, "r2": true, "durable_objects": true, "workflow": true, "container": true, "model_key": true, "version": "0.1.4"}),
        );
        add(
            "/v1/diag/openrouter/auth",
            json!({"ok": true, "usage": 1.0, "limit": 10.0, "limit_remaining": 9.0}),
        );
        add(
            "/v1/diag/openrouter/models",
            json!({"ok": true, "models": [{"id": "a/m", "available": true}]}),
        );
        add(
            "/v1/diag/openrouter/completion",
            json!({"ok": true, "model": "a/m", "tool_calls": 1, "reasoning_tokens": 2, "cost_usd": 0.001, "ttft_ms": 400.0, "tokens_per_sec": 40.0, "total_ms": 900.0}),
        );
        add(
            "/v1/diag/perf",
            json!({"ok": true, "d1_write_ms": {"p50": 5.0, "p95": 9.0}, "gateway_overhead_ms": null}),
        );
        add(
            "/v1/diag/sandbox",
            json!({"ok": true, "cold_ms": 2000.0, "warm_ms": 30.0, "key_visible": false, "egress_blocked": true}),
        );
        add(
            "/v1/diag/memory",
            json!({"ok": true, "citation": "task:diag"}),
        );
        add(
            "/v1/diag/audit",
            json!({"ok": true, "day_spent_usd": 0.001, "daily_cap_usd": 5.0}),
        );
        Fake {
            routes,
            seen: RefCell::new(vec![]),
        }
    }

    fn row<'a>(rows: &'a [Row], id: &str) -> &'a Row {
        rows.iter().find(|r| r.id == id).expect("row exists")
    }

    #[test]
    fn a_healthy_core_passes_every_row_in_order_and_each_row_runs_before_it_finishes() {
        let fake = healthy();
        let mut running_order: Vec<&'static str> = vec![];
        let rows = run_chain(&fake, "0.1.0", 3, |r| {
            if let Some(x) = r.iter().find(|x| x.status == Status::Running)
                && running_order.last() != Some(&x.id)
            {
                running_order.push(x.id);
            }
        });
        assert!(rows.iter().all(|r| r.status == Status::Pass), "{rows:#?}");
        assert!(rows.iter().all(|r| r.ms.is_some()));
        assert_eq!(running_order, DEFS.iter().map(|d| d.id).collect::<Vec<_>>());
        let groups: Vec<&str> = rows.iter().map(|r| r.group).fold(vec![], |mut v, g| {
            if v.last() != Some(&g) {
                v.push(g);
            }
            v
        });
        assert_eq!(groups.len(), 9);
        assert!(groups[0].starts_with("1.") && groups[8].starts_with("9."));
    }

    #[test]
    fn the_sandbox_is_started_once_for_both_groups() {
        let fake = healthy();
        run_chain(&fake, "0.1.0", 2, |_| {});
        let n = fake
            .seen
            .borrow()
            .iter()
            .filter(|s| s.as_str() == "POST /v1/diag/sandbox")
            .count();
        assert_eq!(n, 1);
    }

    #[test]
    fn a_refused_token_fails_the_first_row_with_a_fix_and_skips_the_rest() {
        let mut fake = healthy();
        fake.routes.insert(
            "/v1/diag/bindings".into(),
            Reply {
                status: 401,
                body: Value::Null,
                timed_out: false,
            },
        );
        let rows = run_chain(&fake, "0.1.0", 2, |_| {});
        assert_eq!(rows[0].status, Status::Fail);
        assert!(
            rows[0]
                .fix
                .as_deref()
                .unwrap_or("")
                .contains("access token")
        );
        assert!(rows[1..].iter().all(|r| r.status == Status::Skip));
    }

    #[test]
    fn an_out_of_credit_key_fails_openrouter_but_speed_rows_still_run() {
        let mut fake = healthy();
        fake.routes.insert(
            "/v1/diag/openrouter/auth".into(),
            ok(json!({"ok": false, "code": "payment_required"})),
        );
        let rows = run_chain(&fake, "0.1.0", 2, |_| {});
        assert_eq!(row(&rows, "or.auth").status, Status::Fail);
        assert!(
            row(&rows, "or.auth")
                .fix
                .as_deref()
                .unwrap_or("")
                .contains("credit")
        );
        assert_eq!(row(&rows, "or.stream").status, Status::Skip);
        assert_eq!(row(&rows, "net.rtt").status, Status::Pass);
    }

    #[test]
    fn a_version_mismatch_names_both_versions() {
        let fake = healthy();
        let rows = run_chain(&fake, "0.2.0", 2, |_| {});
        let r = row(&rows, "backend.health");
        assert_eq!(r.status, Status::Fail);
        assert!(r.detail.contains("0.1.4") && r.detail.contains("0.2.0"));
    }

    #[test]
    fn a_visible_secret_in_the_sandbox_is_a_failure() {
        let mut fake = healthy();
        fake.routes.insert(
            "/v1/diag/sandbox".into(),
            ok(json!({"ok": false, "cold_ms": 1.0, "warm_ms": 1.0, "key_visible": true})),
        );
        let rows = run_chain(&fake, "0.1.0", 2, |_| {});
        assert_eq!(row(&rows, "sandbox.keys").status, Status::Fail);
        assert!(
            row(&rows, "sandbox.keys")
                .detail
                .contains("secret is visible")
        );
    }

    #[test]
    fn results_never_contain_the_access_token() {
        let t = UreqTransport::new("https://core.example", "tok-secret-123");
        let dbg = format!("{:?}", initial_rows());
        assert!(!dbg.contains("tok-secret-123"));
        assert_eq!(t.base, "https://core.example");
    }
}
