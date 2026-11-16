//! OpenRouter, end to end: "Connect OpenRouter" (OAuth PKCE: browser, localhost callback, key exchange) and a small typed
//! client for the account data view (key usage and limit, credits, models with prices, generation stats).
//!
//! A thin client over `ureq` rather than an SDK: the desktop is Rust, and the only official SDK is the TypeScript
//! `@openrouter/sdk` for Node. The base address is a parameter so tests use a fake server and never reach OpenRouter.
//! The key is returned to the caller to store in the keyring (or load into a core) and is never logged or put in an error.

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use serde::Deserialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

pub const BASE: &str = "https://openrouter.ai";

/// How long "Connect OpenRouter" waits for the browser callback before it resets.
pub const CONNECT_TIMEOUT: Duration = Duration::from_secs(120);

/// The wait ended because the person cancelled, or because the time ran out.
pub const CANCELLED: &str = "The sign-in was cancelled. Try again.";
pub const TIMED_OUT: &str = "The sign-in was not finished in time. Try again.";

/// The error text for HTTP 403: the key is fine but is not allowed to read this (account credits need a management key).
pub const FORBIDDEN: &str = "OpenRouter does not allow this key to read that.";

// ---------- PKCE ----------

pub struct Pkce {
    pub verifier: String,
    pub challenge: String,
}

pub fn challenge_for(verifier: &str) -> String {
    URL_SAFE_NO_PAD.encode(Sha256::digest(verifier.as_bytes()))
}

pub fn new_pkce() -> Result<Pkce, String> {
    let mut raw = [0u8; 48];
    getrandom::fill(&mut raw)
        .map_err(|_| "No secure random source on this computer.".to_owned())?;
    let verifier = URL_SAFE_NO_PAD.encode(raw);
    let challenge = challenge_for(&verifier);
    Ok(Pkce {
        verifier,
        challenge,
    })
}

pub fn auth_url(base: &str, callback: &str, challenge: &str) -> String {
    format!(
        "{base}/auth?callback_url={}&code_challenge={challenge}&code_challenge_method=S256",
        percent(callback)
    )
}

fn percent(s: &str) -> String {
    let mut out = String::new();
    for b in s.bytes() {
        if b.is_ascii_alphanumeric() || b"-._~".contains(&b) {
            out.push(b as char);
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

// ---------- localhost callback ----------

pub struct Callback {
    listener: TcpListener,
    pub port: u16,
    cancel: Arc<AtomicBool>,
    /// Runs once after accept and the accept-time cancel check, before the request is read.
    #[cfg(test)]
    after_accept: std::sync::Mutex<Option<Box<dyn FnOnce() + Send>>>,
}

impl Callback {
    pub fn bind() -> Result<Callback, String> {
        let listener = TcpListener::bind("127.0.0.1:0")
            .map_err(|_| "Could not open a local port for the sign-in.".to_owned())?;
        let port = listener.local_addr().map_err(|e| e.to_string())?.port();
        Ok(Callback {
            listener,
            port,
            cancel: Arc::new(AtomicBool::new(false)),
            #[cfg(test)]
            after_accept: std::sync::Mutex::new(None),
        })
    }

    pub fn url(&self) -> String {
        format!("http://localhost:{}/callback", self.port)
    }

    /// The flag the UI sets to abandon a wait. Dropping this `Callback` (when the wait returns) closes the port.
    pub fn cancel_flag(&self) -> Arc<AtomicBool> {
        self.cancel.clone()
    }

    /// Test-only: run once after a connection is accepted and the accept-time cancel check has passed.
    #[cfg(test)]
    fn on_after_accept(&self, f: impl FnOnce() + Send + 'static) {
        *self.after_accept.lock().unwrap() = Some(Box::new(f));
    }

    /// Stops a wait on `port` and lets `accept` return so the listener can be dropped.
    pub fn request_cancel(flag: &AtomicBool, port: u16) {
        flag.store(true, Ordering::Relaxed);
        let addr = std::net::SocketAddr::from(([127, 0, 0, 1], port));
        let _ = TcpStream::connect_timeout(&addr, Duration::from_millis(100));
    }

    /// Waits for the browser to arrive at `/callback?code=...` and returns the code. Other requests get a 404 and are ignored.
    pub fn wait_for_code(&self, timeout: Duration) -> Result<String, String> {
        self.listener
            .set_nonblocking(true)
            .map_err(|e| e.to_string())?;
        let end = Instant::now() + timeout;
        while Instant::now() < end {
            if self.cancel.load(Ordering::Relaxed) {
                return Err(CANCELLED.to_owned());
            }
            match self.listener.accept() {
                Ok((mut s, _)) => {
                    if self.cancel.load(Ordering::Relaxed) {
                        return Err(CANCELLED.to_owned());
                    }
                    #[cfg(test)]
                    if let Some(hook) = self.after_accept.lock().unwrap().take() {
                        hook();
                    }
                    let _ = s.set_nonblocking(false);
                    let _ = s.set_read_timeout(Some(Duration::from_secs(5)));
                    if let Some(code) = read_callback(&mut s) {
                        // Cancel during the read must not return a code or exchange it.
                        if self.cancel.load(Ordering::Relaxed) {
                            reply(
                                &mut s,
                                200,
                                "The sign-in was cancelled. You can close this tab.",
                            );
                            return Err(CANCELLED.to_owned());
                        }
                        reply(
                            &mut s,
                            200,
                            "Connected. You can close this tab and go back to Open Computer.",
                        );
                        return Ok(code);
                    }
                    reply(&mut s, 404, "Not found.");
                }
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                    std::thread::sleep(Duration::from_millis(40))
                }
                Err(e) => return Err(e.to_string()),
            }
        }
        Err(TIMED_OUT.to_owned())
    }
}

fn read_callback(s: &mut TcpStream) -> Option<String> {
    let mut buf = [0u8; 4096];
    let n = s.read(&mut buf).ok()?;
    let head = String::from_utf8_lossy(&buf[..n]);
    let target = head
        .lines()
        .next()?
        .strip_prefix("GET ")?
        .split_whitespace()
        .next()?
        .to_owned();
    let (path, query) = target.split_once('?')?;
    if path != "/callback" {
        return None;
    }
    query
        .split('&')
        .find_map(|kv| kv.strip_prefix("code="))
        .filter(|c| !c.is_empty())
        .map(str::to_owned)
}

fn reply(s: &mut TcpStream, status: u16, body: &str) {
    let page = format!(
        "<!doctype html><meta charset=utf-8><title>Open Computer</title><body style=\"background:#000;color:#fff;font:16px monospace;padding:2rem\"><p>{body}</p>"
    );
    let _ = write!(
        s,
        "HTTP/1.1 {status} OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{page}",
        page.len()
    );
}

fn read_body(mut r: ureq::http::Response<ureq::Body>) -> (u16, Value) {
    let status = r.status().as_u16();
    let text = r.body_mut().read_to_string().unwrap_or_default();
    (status, serde_json::from_str(&text).unwrap_or(Value::Null))
}

/// Trades the callback code for a key.
pub fn exchange_code(base: &str, code: &str, verifier: &str) -> Result<String, String> {
    let body = json!({"code": code, "code_verifier": verifier, "code_challenge_method": "S256"})
        .to_string();
    let r = crate::http::agent(Some(Duration::from_secs(20)))
        .post(&format!("{base}/api/v1/auth/keys"))
        .header("Content-Type", "application/json")
        .send(body.as_bytes())
        .map_err(|_| "OpenRouter could not be reached.".to_owned())?;
    let (status, v) = read_body(r);
    if status != 200 {
        return Err(
            "OpenRouter did not accept the sign-in. Try again, or paste a key instead.".to_owned(),
        );
    }
    v.get("key")
        .and_then(Value::as_str)
        .filter(|k| !k.is_empty())
        .map(str::to_owned)
        .ok_or_else(|| "OpenRouter returned no key.".to_owned())
}

/// The whole flow after the browser was opened by the caller: wait for the callback, then exchange. Returns the key.
pub fn finish_connect(
    base: &str,
    cb: &Callback,
    pkce: &Pkce,
    timeout: Duration,
) -> Result<String, String> {
    let code = cb.wait_for_code(timeout)?;
    exchange_code(base, &code, &pkce.verifier)
}

// ---------- account data ----------

pub struct Client {
    base: String,
    key: String,
}

impl std::fmt::Debug for Client {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "openrouter::Client({})", self.base)
    }
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct KeyInfo {
    pub label: String,
    pub usage: f64,
    pub limit: Option<f64>,
    pub limit_remaining: Option<f64>,
    pub is_free_tier: bool,
    /// How often the limit resets: `daily`, `weekly`, `monthly`, or none when it never does.
    pub limit_reset: Option<String>,
    pub usage_daily: Option<f64>,
    pub usage_weekly: Option<f64>,
    pub usage_monthly: Option<f64>,
}

impl KeyInfo {
    /// One plain line about the key. `usage` is lifetime spend, so against a limit that resets it is not shown; the
    /// spend for the current period is, with what is left and when the limit resets.
    pub fn line(&self) -> String {
        let tier = if self.is_free_tier {
            " (free tier)"
        } else {
            ""
        };
        let name = &self.label;
        let Some(limit) = self.limit else {
            return format!("Key {name}: used {}, no limit{tier}", dollars(self.usage));
        };
        let left = self
            .limit_remaining
            .map_or(String::new(), |r| format!(", {} left", dollars(r)));
        let period = match self.limit_reset.as_deref() {
            Some("daily") => Some(("today", "daily", self.usage_daily)),
            Some("weekly") => Some(("this week", "weekly", self.usage_weekly)),
            Some("monthly") => Some(("this month", "monthly", self.usage_monthly)),
            _ => None,
        };
        match period {
            Some((when, resets, spent)) => {
                // Without the period's own figure, what is left says what was spent this period.
                let spent = spent.or_else(|| self.limit_remaining.map(|r| (limit - r).max(0.0)));
                let used = spent.map_or(String::new(), |s| {
                    format!("used {} of {} {when}", dollars(s), dollars(limit))
                });
                let used = if used.is_empty() {
                    format!("limit {}", dollars(limit))
                } else {
                    used
                };
                format!("Key {name}: {used}{left}, resets {resets}{tier}")
            }
            None => format!(
                "Key {name}: used {} of {}{left}{tier}",
                dollars(self.usage),
                dollars(limit)
            ),
        }
    }
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Credits {
    pub total: f64,
    pub used: f64,
}

impl Credits {
    pub fn remaining(&self) -> f64 {
        (self.total - self.used).max(0.0)
    }
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct ModelPrice {
    pub id: String,
    pub name: String,
    pub context: u64,
    /// Dollars per million tokens.
    pub prompt_per_m: f64,
    pub completion_per_m: f64,
    pub tools: bool,
    pub reasoning: bool,
}

impl ModelPrice {
    /// The vendor part of `vendor/model`.
    pub fn provider(&self) -> &str {
        self.id.split('/').next().unwrap_or("")
    }
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Generation {
    pub id: String,
    pub model: String,
    pub cost: f64,
    pub prompt_tokens: u64,
    pub completion_tokens: u64,
    pub latency_ms: Option<f64>,
    /// When the core made the call (ISO time) and the task it belonged to, when the core said.
    pub ts: Option<String>,
    pub task_id: Option<String>,
}

#[derive(Deserialize)]
struct Wrap<T> {
    data: T,
}

/// Whether a list field (such as `supported_parameters`) contains `word`.
fn supports(v: &Value, field: &str, word: &str) -> bool {
    v.get(field)
        .and_then(Value::as_array)
        .is_some_and(|l| l.iter().any(|x| x.as_str() == Some(word)))
}

fn num(v: &Value, k: &str) -> f64 {
    match v.get(k) {
        Some(Value::Number(n)) => n.as_f64().unwrap_or(0.0),
        Some(Value::String(s)) => s.parse().unwrap_or(0.0),
        _ => 0.0,
    }
}

fn opt(v: &Value, k: &str) -> Option<f64> {
    match v.get(k) {
        Some(Value::Number(n)) => n.as_f64(),
        _ => None,
    }
}

impl Client {
    pub fn new(base: &str, key: &str) -> Client {
        Client {
            base: base.trim_end_matches('/').to_owned(),
            key: key.to_owned(),
        }
    }

    fn get(&self, path: &str) -> Result<Value, String> {
        let r = crate::http::agent(Some(Duration::from_secs(20)))
            .get(&format!("{}{path}", self.base))
            .header("Authorization", &format!("Bearer {}", self.key))
            .call()
            .map_err(|_| "OpenRouter could not be reached.".to_owned())?;
        match read_body(r) {
            (200, v) => Ok(v),
            (401, _) => Err("OpenRouter rejected the key.".to_owned()),
            (403, _) => Err(FORBIDDEN.to_owned()),
            (c, _) => Err(format!("OpenRouter answered with an error ({c}).")),
        }
    }

    pub fn key_info(&self) -> Result<KeyInfo, String> {
        let d = self.get("/api/v1/key")?;
        let d = d.get("data").unwrap_or(&d);
        Ok(KeyInfo {
            label: d
                .get("label")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_owned(),
            usage: num(d, "usage"),
            limit: opt(d, "limit"),
            limit_remaining: opt(d, "limit_remaining"),
            is_free_tier: d
                .get("is_free_tier")
                .and_then(Value::as_bool)
                .unwrap_or(false),
            limit_reset: d
                .get("limit_reset")
                .and_then(Value::as_str)
                .map(str::to_owned),
            usage_daily: opt(d, "usage_daily"),
            usage_weekly: opt(d, "usage_weekly"),
            usage_monthly: opt(d, "usage_monthly"),
        })
    }

    pub fn credits(&self) -> Result<Credits, String> {
        let v = self.get("/api/v1/credits")?;
        let d = v.get("data").unwrap_or(&v);
        Ok(Credits {
            total: num(d, "total_credits"),
            used: num(d, "total_usage"),
        })
    }

    pub fn models(&self) -> Result<Vec<ModelPrice>, String> {
        let v = self.get("/api/v1/models")?;
        let w: Wrap<Vec<Value>> = serde_json::from_value(v)
            .map_err(|_| "OpenRouter's model list was not understood.".to_owned())?;
        Ok(w.data
            .iter()
            .map(|m| {
                let p = m.get("pricing").unwrap_or(&Value::Null);
                ModelPrice {
                    id: m.get("id").and_then(Value::as_str).unwrap_or("").to_owned(),
                    name: m
                        .get("name")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_owned(),
                    context: m.get("context_length").and_then(Value::as_u64).unwrap_or(0),
                    prompt_per_m: num(p, "prompt") * 1_000_000.0,
                    completion_per_m: num(p, "completion") * 1_000_000.0,
                    tools: supports(m, "supported_parameters", "tools"),
                    reasoning: supports(m, "supported_parameters", "reasoning")
                        || supports(m, "supported_parameters", "include_reasoning"),
                }
            })
            .filter(|m| !m.id.is_empty())
            .collect())
    }

    pub fn generation(&self, id: &str) -> Result<Generation, String> {
        let v = self.get(&format!("/api/v1/generation?id={}", percent(id)))?;
        let d = v.get("data").unwrap_or(&v);
        Ok(Generation {
            id: id.to_owned(),
            model: d
                .get("model")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_owned(),
            cost: num(d, "total_cost"),
            prompt_tokens: d.get("tokens_prompt").and_then(Value::as_u64).unwrap_or(0),
            completion_tokens: d
                .get("tokens_completion")
                .and_then(Value::as_u64)
                .unwrap_or(0),
            latency_ms: opt(d, "latency"),
            ts: None,
            task_id: None,
        })
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Source {
    #[default]
    Direct,
    Core,
}

impl Source {
    pub fn words(self) -> &'static str {
        match self {
            Source::Core => "Source: your core, through the official OpenRouter SDK.",
            Source::Direct => "Source: this computer, straight to OpenRouter (no core connected).",
        }
    }
}

/// Everything the data view shows, fetched in one go. A part that fails is left out and named in `problems`.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Account {
    /// Where the data came from: the connected core (official OpenRouter SDK) or this computer talking to OpenRouter directly.
    pub source: Source,
    pub key: Option<KeyInfo>,
    pub credits: Option<Credits>,
    /// Credits were asked for and OpenRouter only shows them to management keys (an ordinary key gets a 403).
    pub credits_need_management_key: bool,
    pub models: Vec<ModelPrice>,
    pub generations: Vec<Generation>,
    pub problems: Vec<String>,
}

pub fn load_account(c: &Client, recent_generation_ids: &[String]) -> Account {
    let mut a = Account {
        source: Source::Direct,
        ..Account::default()
    };
    match c.key_info() {
        Ok(k) => a.key = Some(k),
        Err(e) => a.problems.push(e),
    }
    match c.credits() {
        Ok(k) => a.credits = Some(k),
        // An ordinary key (what Connect OpenRouter makes) cannot read account credits. That is not a problem with the key.
        Err(e) if e == FORBIDDEN => a.credits_need_management_key = true,
        Err(e) => a.problems.push(format!("Credits: {e}")),
    }
    match c.models() {
        Ok(mut m) => {
            m.sort_by(|x, y| {
                x.prompt_per_m
                    .partial_cmp(&y.prompt_per_m)
                    .unwrap_or(std::cmp::Ordering::Equal)
                    .then(x.id.cmp(&y.id))
            });
            a.models = m;
        }
        Err(e) => a.problems.push(format!("Models: {e}")),
    }
    for id in recent_generation_ids.iter().rev().take(5) {
        if let Ok(g) = c.generation(id) {
            a.generations.push(g);
        }
    }
    a
}

/// The same view from a connected core's authenticated `/v1/openrouter/*` routes (the core holds the key and uses the SDK).
/// Returns `None` when the core has no key stored or does not have these routes, so the caller can fall back to direct.
pub fn load_account_via_core(ep: &crate::chat::Endpoint) -> Option<Account> {
    let mut a = Account {
        source: Source::Core,
        ..Account::default()
    };
    let get = |name: &str| ep.get(&format!("/v1/openrouter/{name}"));
    match get("key") {
        Ok((200, v)) => {
            a.key = Some(KeyInfo {
                label: v["label"].as_str().unwrap_or("").to_owned(),
                usage: num(&v, "usage"),
                limit: opt(&v, "limit"),
                limit_remaining: opt(&v, "limit_remaining"),
                is_free_tier: v["is_free_tier"].as_bool().unwrap_or(false),
                limit_reset: v["limit_reset"].as_str().map(str::to_owned),
                usage_daily: opt(&v, "usage_daily"),
                usage_weekly: opt(&v, "usage_weekly"),
                usage_monthly: opt(&v, "usage_monthly"),
            });
        }
        Ok((401, _)) => a
            .problems
            .push("OpenRouter rejected the core's key.".to_owned()),
        // No key on the core, or an older core without these routes: use the direct path instead.
        Ok((404 | 409, _)) | Err(_) => return None,
        Ok((c, _)) => a
            .problems
            .push(format!("OpenRouter answered with an error ({c}).")),
    }
    if let Ok((200, v)) = get("credits") {
        if v["available"].as_bool() == Some(false) {
            a.credits_need_management_key = true;
        } else {
            a.credits = Some(Credits {
                total: num(&v, "total"),
                used: num(&v, "used"),
            });
        }
    }
    if let Ok((200, v)) = get("models") {
        let mut m: Vec<ModelPrice> = v["models"]
            .as_array()
            .map(|l| {
                l.iter()
                    .map(|m| ModelPrice {
                        id: m["id"].as_str().unwrap_or("").to_owned(),
                        name: m["name"].as_str().unwrap_or("").to_owned(),
                        context: m["context"].as_u64().unwrap_or(0),
                        prompt_per_m: num(m, "prompt_per_m"),
                        completion_per_m: num(m, "completion_per_m"),
                        tools: m["tools"].as_bool().unwrap_or(false),
                        reasoning: m["reasoning"].as_bool().unwrap_or(false),
                    })
                    .filter(|m| !m.id.is_empty())
                    .collect()
            })
            .unwrap_or_default();
        m.sort_by(|x, y| {
            x.prompt_per_m
                .partial_cmp(&y.prompt_per_m)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then(x.id.cmp(&y.id))
        });
        a.models = m;
    }
    if let Ok((200, v)) = get("generations") {
        a.generations = v["generations"]
            .as_array()
            .map(|l| {
                l.iter()
                    .map(|g| Generation {
                        id: g["id"].as_str().unwrap_or("").to_owned(),
                        model: if g["pending"].as_bool() == Some(true) {
                            "waiting for OpenRouter's record".to_owned()
                        } else {
                            g["model"].as_str().unwrap_or("").to_owned()
                        },
                        cost: num(g, "cost"),
                        prompt_tokens: g["prompt_tokens"].as_u64().unwrap_or(0),
                        completion_tokens: g["completion_tokens"].as_u64().unwrap_or(0),
                        latency_ms: opt(g, "latency_ms"),
                        ts: g["ts"].as_str().map(str::to_owned),
                        task_id: g["task_id"].as_str().map(str::to_owned),
                    })
                    .collect()
            })
            .unwrap_or_default();
    }
    Some(a)
}

pub fn dollars(v: f64) -> String {
    if v != 0.0 && v.abs() < 0.01 {
        format!("${v:.4}")
    } else {
        format!("${v:.2}")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Arc, Mutex};

    /// A fake OpenRouter: answers canned JSON by path, and records the request lines and bodies it saw.
    fn fake_server(routes: Vec<(&'static str, String)>) -> (String, Arc<Mutex<Vec<String>>>) {
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        let base = format!("http://127.0.0.1:{}", l.local_addr().unwrap().port());
        let seen = Arc::new(Mutex::new(Vec::new()));
        let log = seen.clone();
        std::thread::spawn(move || {
            for stream in l.incoming() {
                let Ok(mut s) = stream else { break };
                let _ = s.set_read_timeout(Some(Duration::from_millis(300)));
                let mut req = String::new();
                let mut buf = vec![0u8; 8192];
                // Headers and body can arrive in separate reads; keep reading until the body stops coming.
                while let Ok(n) = s.read(&mut buf) {
                    if n == 0 {
                        break;
                    }
                    req.push_str(&String::from_utf8_lossy(&buf[..n]));
                    if req.contains("\r\n\r\n")
                        && (!req.starts_with("POST") || req.contains("code_challenge_method"))
                    {
                        break;
                    }
                }
                log.lock().unwrap().push(req.clone());
                let first = req.lines().next().unwrap_or("");
                let body = routes
                    .iter()
                    .find(|(p, _)| first.contains(p))
                    .map(|(_, b)| b.clone());
                // A route body starting with "403:" answers with that status.
                let (code, body) =
                    body.map_or((404, "{}".to_owned()), |b| match b.strip_prefix("403:") {
                        Some(rest) => (403, rest.to_owned()),
                        None => (200, b),
                    });
                let _ = write!(
                    s,
                    "HTTP/1.1 {code} X\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                );
            }
        });
        (base, seen)
    }

    #[test]
    fn pkce_challenge_is_the_s256_of_the_verifier_and_the_auth_url_carries_it() {
        // RFC 7636 appendix B vector.
        assert_eq!(
            challenge_for("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"),
            "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
        );
        let p = new_pkce().unwrap();
        assert!(p.verifier.len() >= 43 && p.verifier != new_pkce().unwrap().verifier);
        let u = auth_url(BASE, "http://localhost:5555/callback", &p.challenge);
        assert!(u.starts_with("https://openrouter.ai/auth?callback_url=http%3A%2F%2Flocalhost%3A5555%2Fcallback&code_challenge="));
        assert!(u.ends_with("&code_challenge_method=S256") && !u.contains(&p.verifier));
    }

    #[test]
    fn the_connect_flow_gets_the_code_from_the_callback_and_exchanges_it_without_leaking_the_key() {
        let (base, seen) = fake_server(vec![(
            "/api/v1/auth/keys",
            r#"{"key":"sk-or-fake-123","user_id":"u"}"#.to_owned(),
        )]);
        let cb = Callback::bind().unwrap();
        let pkce = new_pkce().unwrap();
        let port = cb.port;
        // The "browser": stray request first (ignored), then the real callback.
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(100));
            for path in ["/favicon.ico", "/callback?code=abc123"] {
                if let Ok(mut s) = TcpStream::connect(("127.0.0.1", port)) {
                    let _ = write!(s, "GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n");
                    let mut out = String::new();
                    let _ = s.read_to_string(&mut out);
                }
            }
        });
        let key = finish_connect(&base, &cb, &pkce, Duration::from_secs(5)).unwrap();
        assert_eq!(key, "sk-or-fake-123");
        let sent = seen.lock().unwrap().join("\n");
        assert!(
            sent.contains("POST /api/v1/auth/keys")
                && sent.contains("\"code\":\"abc123\"")
                && sent.contains(&pkce.verifier)
        );
    }

    #[test]
    fn a_callback_that_never_comes_times_out_in_plain_words() {
        let cb = Callback::bind().unwrap();
        let e = cb.wait_for_code(Duration::from_millis(200)).unwrap_err();
        assert_eq!(e, TIMED_OUT);
    }

    #[test]
    fn cancelling_the_callback_stops_the_wait_and_releases_the_port() {
        let cb = Callback::bind().unwrap();
        let port = cb.port;
        let flag = cb.cancel_flag();
        let handle = std::thread::spawn(move || cb.wait_for_code(Duration::from_secs(30)));
        std::thread::sleep(Duration::from_millis(80));
        Callback::request_cancel(&flag, port);
        let e = handle.join().expect("wait thread").unwrap_err();
        assert_eq!(e, CANCELLED);
        // The listener is dropped when the wait returns, so the port can be bound again.
        assert!(TcpListener::bind(("127.0.0.1", port)).is_ok());
    }

    #[test]
    fn a_callback_that_arrives_after_cancel_is_not_accepted() {
        let cb = Callback::bind().unwrap();
        let port = cb.port;
        let flag = cb.cancel_flag();
        flag.store(true, Ordering::Relaxed);
        let handle = std::thread::spawn(move || cb.wait_for_code(Duration::from_secs(5)));
        std::thread::sleep(Duration::from_millis(50));
        if let Ok(mut s) = TcpStream::connect(("127.0.0.1", port)) {
            let _ = write!(
                s,
                "GET /callback?code=should-not-be-used HTTP/1.1\r\nHost: localhost\r\n\r\n"
            );
            let mut out = String::new();
            let _ = s.read_to_string(&mut out);
        }
        let e = handle.join().expect("wait thread").unwrap_err();
        assert_eq!(e, CANCELLED);
    }

    #[test]
    fn a_callback_cancelled_after_accept_is_not_exchanged() {
        let (base, seen) = fake_server(vec![(
            "/api/v1/auth/keys",
            r#"{"key":"sk-or-should-not-be-issued","user_id":"u"}"#.to_owned(),
        )]);
        let cb = Callback::bind().unwrap();
        let pkce = new_pkce().unwrap();
        let port = cb.port;
        let flag = cb.cancel_flag();
        let (accepted_tx, accepted_rx) = std::sync::mpsc::sync_channel(1);
        let (resume_tx, resume_rx) = std::sync::mpsc::sync_channel(0);
        cb.on_after_accept(move || {
            accepted_tx.send(()).expect("signal accepted");
            resume_rx.recv().expect("resume after cancel");
        });
        let handle =
            std::thread::spawn(move || finish_connect(&base, &cb, &pkce, Duration::from_secs(5)));
        // Hold the connection open with no request bytes so the wait can accept
        // and pass the accept-time check; then cancel before the callback is sent.
        let mut s = TcpStream::connect(("127.0.0.1", port)).expect("connect to callback port");
        let _ = s.set_read_timeout(Some(Duration::from_secs(3)));
        accepted_rx
            .recv_timeout(Duration::from_secs(3))
            .expect("worker accepted the connection");
        flag.store(true, Ordering::Relaxed);
        resume_tx.send(()).expect("resume the worker");
        let _ = write!(
            s,
            "GET /callback?code=should-not-be-exchanged HTTP/1.1\r\nHost: localhost\r\n\r\n"
        );
        let mut out = String::new();
        let _ = s.read_to_string(&mut out);
        let e = handle.join().expect("wait thread").unwrap_err();
        assert_eq!(e, CANCELLED);
        let sent = seen.lock().unwrap().join("\n");
        assert!(
            !sent.contains("POST /api/v1/auth/keys"),
            "cancelled callback was exchanged: {sent}"
        );
    }

    #[test]
    fn a_rejected_exchange_says_so_and_never_echoes_the_code() {
        let (base, _) = fake_server(vec![]);
        let e = exchange_code(&base, "secret-code", "v").unwrap_err();
        assert!(e.contains("did not accept") && !e.contains("secret-code"));
    }

    #[test]
    fn the_data_view_reads_key_credits_models_and_generations_from_typed_responses() {
        let (base, seen) = fake_server(vec![
            ("/api/v1/key", r#"{"data":{"label":"sk-or-v1-abc...xyz","usage":1.25,"limit":10,"limit_remaining":8.75,"is_free_tier":false}}"#.to_owned()),
            ("/api/v1/credits", r#"{"data":{"total_credits":20,"total_usage":3.5}}"#.to_owned()),
            ("/api/v1/models", r#"{"data":[{"id":"b/pricey","name":"Pricey","context_length":8000,"pricing":{"prompt":"0.00001","completion":"0.00003"}},{"id":"a/cheap","name":"Cheap","context_length":128000,"pricing":{"prompt":"0.0000001","completion":"0.0000004"}}]}"#.to_owned()),
            ("/api/v1/generation?id=gen-1", r#"{"data":{"model":"a/cheap","total_cost":0.0021,"tokens_prompt":120,"tokens_completion":40,"latency":900}}"#.to_owned()),
        ]);
        let c = Client::new(&base, "sk-or-secret");
        let a = load_account(&c, &["gen-1".to_owned()]);
        assert!(a.problems.is_empty(), "{:?}", a.problems);
        assert_eq!(a.key.as_ref().unwrap().limit_remaining, Some(8.75));
        assert_eq!(a.credits.as_ref().unwrap().remaining(), 16.5);
        assert_eq!(a.models[0].id, "a/cheap");
        assert!((a.models[0].prompt_per_m - 0.1).abs() < 1e-9);
        assert_eq!(a.generations[0].completion_tokens, 40);
        assert!(seen.lock().unwrap().iter().all(|r| {
            r.to_lowercase()
                .contains("authorization: bearer sk-or-secret")
        }));
        assert!(!format!("{c:?}").contains("sk-or-secret"));
        assert_eq!(dollars(0.0021), "$0.0021");
        assert_eq!(dollars(16.5), "$16.50");
    }

    #[test]
    fn a_403_on_credits_means_a_management_key_is_needed_not_a_rejected_key() {
        let (base, _) = fake_server(vec![
            ("/api/v1/key", r#"{"data":{"label":"k","usage":82.29,"limit":50,"limit_remaining":12.5,"limit_reset":"monthly","usage_monthly":37.5,"is_free_tier":false}}"#.to_owned()),
            ("/api/v1/credits", r#"403:{"error":{"code":403,"message":"Only management keys can fetch credits for an account"}}"#.to_owned()),
            ("/api/v1/models", r#"{"data":[]}"#.to_owned()),
        ]);
        let a = load_account(&Client::new(&base, "k"), &[]);
        assert!(a.credits.is_none() && a.credits_need_management_key);
        assert!(a.problems.is_empty(), "{:?}", a.problems);
        assert_eq!(
            a.key.as_ref().unwrap().limit_reset.as_deref(),
            Some("monthly")
        );
        // A 403 is never reported as a rejected key; only a 401 is.
        let (b401, _) = fake_server(vec![]);
        assert!(Client::new(&b401, "k").key_info().is_err());
    }

    #[test]
    fn a_core_that_says_credits_are_unavailable_sets_the_same_flag() {
        let (base, _) = fake_server(vec![
            ("/v1/openrouter/key", r#"{"label":"k","usage":82.29,"limit":50,"limit_remaining":12.5,"limit_reset":"monthly","usage_monthly":37.5,"is_free_tier":false}"#.to_owned()),
            ("/v1/openrouter/credits", r#"{"available":false,"reason":"management_key_required"}"#.to_owned()),
        ]);
        let a = load_account_via_core(&crate::chat::Endpoint::new(&base, "t")).unwrap();
        assert!(a.credits.is_none() && a.credits_need_management_key);
        assert_eq!(a.key.unwrap().usage_monthly, Some(37.5));
    }

    #[test]
    fn the_key_line_shows_this_periods_spend_and_when_it_resets_not_lifetime_usage() {
        let base = KeyInfo {
            label: "k".into(),
            usage: 82.29,
            limit: Some(50.0),
            limit_remaining: Some(12.5),
            limit_reset: Some("monthly".into()),
            usage_monthly: Some(37.5),
            ..KeyInfo::default()
        };
        assert_eq!(
            base.line(),
            "Key k: used $37.50 of $50.00 this month, $12.50 left, resets monthly"
        );
        assert!(!base.line().contains("82.29"));
        // Without the period's own figure, the limit minus what is left says what was spent this period.
        let derived = KeyInfo {
            usage_monthly: None,
            ..base.clone()
        };
        assert_eq!(
            derived.line(),
            "Key k: used $37.50 of $50.00 this month, $12.50 left, resets monthly"
        );
        let weekly = KeyInfo {
            limit_reset: Some("weekly".into()),
            usage_weekly: Some(4.0),
            ..base.clone()
        };
        assert!(
            weekly.line().contains("used $4.00 of $50.00 this week")
                && weekly.line().ends_with("resets weekly")
        );
        // A limit that never resets is a lifetime figure, so lifetime usage is the right thing to show.
        let lifetime = KeyInfo {
            limit_reset: None,
            usage: 1.25,
            limit: Some(10.0),
            limit_remaining: Some(8.75),
            ..KeyInfo::default()
        };
        assert_eq!(lifetime.line(), "Key : used $1.25 of $10.00, $8.75 left");
        assert_eq!(
            KeyInfo {
                usage: 2.0,
                ..KeyInfo::default()
            }
            .line(),
            "Key : used $2.00, no limit"
        );
    }

    #[test]
    fn a_bad_key_is_reported_as_rejected_and_other_parts_are_still_tried() {
        let (base, _) = fake_server(vec![]);
        let a = load_account(&Client::new(&base, "k"), &[]);
        assert!(a.key.is_none() && a.problems.len() >= 2);
    }

    #[test]
    fn a_connected_core_is_the_source_when_it_has_the_routes_and_a_missing_key_falls_back() {
        let (base, seen) = fake_server(vec![
            ("/v1/openrouter/key", r#"{"label":"k","usage":1.0,"limit":10,"limit_remaining":9,"is_free_tier":false}"#.to_owned()),
            ("/v1/openrouter/credits", r#"{"total":20,"used":3.5}"#.to_owned()),
            ("/v1/openrouter/models", r#"{"models":[{"id":"b/x","name":"X","context":8000,"prompt_per_m":3,"completion_per_m":9},{"id":"a/y","name":"Y","context":4000,"prompt_per_m":0.1,"completion_per_m":0.4}]}"#.to_owned()),
            ("/v1/openrouter/generations", r#"{"generations":[{"id":"gen-1","model":"a/y","cost":0.002,"prompt_tokens":10,"completion_tokens":5,"latency_ms":700},{"id":"gen-2","pending":true}]}"#.to_owned()),
        ]);
        let ep = crate::chat::Endpoint::new(&base, "core-token");
        let a = load_account_via_core(&ep).unwrap();
        assert_eq!(a.source, Source::Core);
        assert!(a.source.words().contains("official OpenRouter SDK"));
        assert_eq!(a.credits.as_ref().unwrap().remaining(), 16.5);
        assert_eq!(a.models[0].id, "a/y");
        assert_eq!(a.generations.len(), 2);
        assert_eq!(a.generations[0].completion_tokens, 5);
        assert!(a.generations[1].model.contains("waiting"));
        assert!(seen.lock().unwrap().iter().all(|r| {
            r.to_lowercase()
                .contains("authorization: bearer core-token")
        }));
        // A core with no key (404 from the fake) means: use the direct client.
        let (empty, _) = fake_server(vec![]);
        assert!(load_account_via_core(&crate::chat::Endpoint::new(&empty, "t")).is_none());
    }
}
