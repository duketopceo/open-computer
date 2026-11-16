//! What the Settings page shows, as typed data read from the connected core. Every number comes from the core or from
//! Wrangler; nothing is made up. The parsers are pure (and tested on fake answers); the fetchers add one HTTP call each.

use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::Value;

use crate::chat::Endpoint;
use crate::openrouter::ModelPrice;

/// A value that is on its way, has arrived, or failed with words the person can read.
#[derive(Clone, Debug, PartialEq, Default)]
pub enum Load<T> {
    #[default]
    Idle,
    Loading,
    Ready(T),
    Failed(String),
}

impl<T> Load<T> {
    pub fn ready(&self) -> Option<&T> {
        match self {
            Load::Ready(v) => Some(v),
            _ => None,
        }
    }
    pub fn error(&self) -> Option<&str> {
        match self {
            Load::Failed(e) => Some(e),
            _ => None,
        }
    }
    pub fn is_loading(&self) -> bool {
        matches!(self, Load::Loading)
    }
    /// Idle and failed values are worth fetching again; ready and loading ones are not.
    pub fn wants_fetch(&self) -> bool {
        matches!(self, Load::Idle | Load::Failed(_))
    }
}

impl<T> From<Result<T, String>> for Load<T> {
    fn from(r: Result<T, String>) -> Self {
        match r {
            Ok(v) => Load::Ready(v),
            Err(e) => Load::Failed(e),
        }
    }
}

// ---------- dates (UTC, no clock crate) ----------

/// Days since 1970-01-01 for now.
pub fn today_days() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| (d.as_secs() / 86_400) as i64)
}

/// The civil date for a day count (Howard Hinnant's algorithm).
pub fn ymd_from_days(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (if m <= 2 { y + 1 } else { y }, m, d)
}

pub fn ymd_string(days: i64) -> String {
    let (y, m, d) = ymd_from_days(days);
    format!("{y:04}-{m:02}-{d:02}")
}

// ---------- spend, from the audit log ----------

/// Spend by day as the core's audit log recorded it (one `event:usage` record per model step).
#[derive(Clone, Debug, PartialEq, Default)]
pub struct SpendReport {
    pub today: f64,
    /// The last seven days, today included.
    pub week: f64,
    /// This calendar month so far.
    pub month: f64,
    /// The last 14 days, oldest first, as (date, dollars).
    pub days: Vec<(String, f64)>,
    /// What the core said about the audit chain: `Some(true)` verified, `Some(false)` broken, none when it did not say.
    pub chain_ok: Option<bool>,
    pub records: usize,
}

pub fn spend_from_audit(ndjson: &str, today: i64, chain_header: Option<&str>) -> SpendReport {
    let mut by_day: std::collections::BTreeMap<String, f64> = Default::default();
    let mut records = 0;
    for line in ndjson.lines().filter(|l| !l.trim().is_empty()) {
        let Ok(v) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        records += 1;
        if v["kind"].as_str() != Some("event:usage") {
            continue;
        }
        let (Some(ts), Some(cost)) = (v["ts"].as_str(), v["data"]["cost"].as_f64()) else {
            continue;
        };
        if let Some(day) = ts.get(..10) {
            *by_day.entry(day.to_owned()).or_default() += cost;
        }
    }
    let at = |offset: i64| {
        by_day
            .get(&ymd_string(today - offset))
            .copied()
            .unwrap_or(0.0)
    };
    let days: Vec<(String, f64)> = (0..14)
        .rev()
        .map(|o| (ymd_string(today - o), at(o)))
        .collect();
    let this_month = &ymd_string(today)[..7];
    SpendReport {
        today: at(0),
        week: (0..7).map(at).sum(),
        month: by_day
            .iter()
            .filter(|(d, _)| d.starts_with(this_month))
            .map(|(_, c)| *c)
            .sum(),
        days,
        chain_ok: chain_header.map(|h| h.trim() == "ok"),
        records,
    }
}

/// `2026-11-15T12:00:00.000Z` as `2026-11-15 12:00`.
pub fn short_time(ts: &str) -> String {
    ts.replace('T', " ").chars().take(16).collect()
}

/// Dollars with the precision that matters for small spends.
pub fn dollars(v: f64) -> String {
    crate::openrouter::dollars(v)
}

// ---------- memory ----------

#[derive(Clone, Debug, PartialEq, Default)]
pub struct MemoryStats {
    pub blocks: Vec<(String, u64)>,
    pub entries: u64,
    pub newest: Option<String>,
}

pub fn parse_memory_stats(v: &Value) -> MemoryStats {
    MemoryStats {
        blocks: v["blocks"]
            .as_array()
            .map(|l| {
                l.iter()
                    .map(|b| {
                        (
                            b["name"].as_str().unwrap_or("").to_owned(),
                            b["chars"].as_u64().unwrap_or(0),
                        )
                    })
                    .collect()
            })
            .unwrap_or_default(),
        entries: v["entries"].as_u64().unwrap_or(0),
        newest: v["newest"].as_str().map(str::to_owned),
    }
}

// ---------- the core itself ----------

#[derive(Clone, Debug, PartialEq, Default)]
pub struct CoreInfo {
    pub version: String,
    /// The Cloudflare data centre that answered (from the `cf-ray` header), when the answer came through Cloudflare.
    pub served_from: Option<String>,
}

/// `8f1a2b3c4d5e6f70-SJC` gives `SJC`.
pub fn colo_from_cf_ray(ray: &str) -> Option<String> {
    ray.rsplit_once('-')
        .map(|(_, c)| c.trim().to_owned())
        .filter(|c| !c.is_empty())
}

pub fn parse_core_info(body: &Value, headers: &[(String, String)]) -> CoreInfo {
    CoreInfo {
        version: body["version"].as_str().unwrap_or("").to_owned(),
        served_from: headers
            .iter()
            .find(|(k, _)| k == "cf-ray")
            .and_then(|(_, v)| colo_from_cf_ray(v)),
    }
}

/// One line of the Bindings list.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BindingRow {
    pub name: &'static str,
    pub ok: bool,
    pub note: &'static str,
}

/// The core's own report of what it is bound to, from `/v1/diag/bindings`.
pub fn binding_rows(b: &Value) -> Vec<BindingRow> {
    let row = |name, field: &str, ok_note, bad_note| {
        let ok = b.get(field).and_then(Value::as_bool).unwrap_or(false);
        BindingRow {
            name,
            ok,
            note: if ok { ok_note } else { bad_note },
        }
    };
    vec![
        row("D1 database", "d1", "answers", "not answering"),
        row("R2 storage", "r2", "answers", "not answering"),
        row("Durable Objects", "durable_objects", "bound", "not bound"),
        row("Workflows", "workflow", "bound", "not bound"),
        row(
            "Containers (sandbox)",
            "container",
            "bound",
            "not bound (needs Workers Paid)",
        ),
        row(
            "Secrets Store: access secret",
            "access_secret",
            "set",
            "not set",
        ),
        row(
            "Secrets Store: OpenRouter key",
            "model_key",
            "set",
            "not set",
        ),
    ]
}

/// What the core says about the AI Gateway.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct GatewayInfo {
    pub configured: bool,
    pub in_use: bool,
    /// `binding`, `account` or `direct`.
    pub via: String,
    pub reason: Option<String>,
}

pub fn gateway_info(b: &Value) -> GatewayInfo {
    GatewayInfo {
        configured: b["gateway_configured"]
            .as_bool()
            .or_else(|| b["gateway"].as_bool())
            .unwrap_or(false),
        in_use: b["gateway"].as_bool().unwrap_or(false),
        via: b["gateway_via"].as_str().unwrap_or("direct").to_owned(),
        reason: b["gateway_reason"].as_str().map(str::to_owned),
    }
}

// ---------- the models table ----------

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ModelSort {
    Name,
    Context,
    Prompt,
    Completion,
}

/// Models whose id or name contains the query (case-insensitive), sorted by a column.
pub fn view_models<'a>(
    models: &'a [ModelPrice],
    query: &str,
    sort: ModelSort,
    ascending: bool,
) -> Vec<&'a ModelPrice> {
    let q = query.trim().to_lowercase();
    let mut v: Vec<&ModelPrice> = models
        .iter()
        .filter(|m| {
            q.is_empty() || m.id.to_lowercase().contains(&q) || m.name.to_lowercase().contains(&q)
        })
        .collect();
    v.sort_by(|a, b| {
        let o = match sort {
            ModelSort::Name => a.id.cmp(&b.id),
            ModelSort::Context => a.context.cmp(&b.context),
            ModelSort::Prompt => a
                .prompt_per_m
                .partial_cmp(&b.prompt_per_m)
                .unwrap_or(std::cmp::Ordering::Equal),
            ModelSort::Completion => a
                .completion_per_m
                .partial_cmp(&b.completion_per_m)
                .unwrap_or(std::cmp::Ordering::Equal),
        };
        let o = o.then(a.id.cmp(&b.id));
        if ascending { o } else { o.reverse() }
    });
    v
}

// ---------- fetchers ----------

/// The last status each task reached, from the audit log's `event:run` records (`task id -> done | failed | cancelled | running`).
pub fn run_statuses(ndjson: &str) -> std::collections::HashMap<String, String> {
    let mut out = std::collections::HashMap::new();
    for line in ndjson.lines() {
        let Ok(v) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        if v.get("kind").and_then(Value::as_str) != Some("event:run") {
            continue;
        }
        let (Some(task), Some(status)) = (
            v.get("task_id").and_then(Value::as_str),
            v.pointer("/data/status").and_then(Value::as_str),
        ) else {
            continue;
        };
        out.insert(task.to_owned(), status.to_owned());
    }
    out
}

pub fn fetch_run_statuses(
    ep: &Endpoint,
) -> Result<std::collections::HashMap<String, String>, String> {
    let (status, _, text) = ep.raw("GET", "/v1/audit/export")?;
    if status != 200 {
        return Err(format!("The core answered with an error ({status})."));
    }
    Ok(run_statuses(&text))
}

pub fn fetch_spend(ep: &Endpoint) -> Result<SpendReport, String> {
    let (status, headers, text) = ep.raw("GET", "/v1/audit/export")?;
    if status != 200 {
        return Err(format!("The core answered with an error ({status})."));
    }
    let chain = headers
        .iter()
        .find(|(k, _)| k == "x-audit-chain")
        .map(|(_, v)| v.as_str());
    Ok(spend_from_audit(&text, today_days(), chain))
}

pub fn fetch_memory(ep: &Endpoint) -> Result<MemoryStats, String> {
    match ep.get("/v1/memory/stats")? {
        (200, v) => Ok(parse_memory_stats(&v)),
        (404, _) => {
            Err("This core is older than the memory page. Update it with `oc setup`.".to_owned())
        }
        (c, _) => Err(format!("The core answered with an error ({c}).")),
    }
}

/// Empties the core's memory and returns how many entries went.
pub fn clear_memory(ep: &Endpoint) -> Result<u64, String> {
    let (status, _, text) = ep.raw("POST", "/v1/memory/clear")?;
    if status != 200 {
        return Err(format!("The core answered with an error ({status})."));
    }
    Ok(serde_json::from_str::<Value>(&text)
        .ok()
        .and_then(|v| v["cleared"].as_u64())
        .unwrap_or(0))
}

pub fn fetch_core_info(ep: &Endpoint) -> Result<CoreInfo, String> {
    let (status, headers, text) = ep.raw("GET", "/v1/health")?;
    if status != 200 {
        return Err(format!("The core answered with an error ({status})."));
    }
    Ok(parse_core_info(
        &serde_json::from_str(&text).unwrap_or(Value::Null),
        &headers,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::sync::{Arc, Mutex};
    use std::time::Duration;

    /// A fake core: canned answers by path, with headers, recording what it was asked.
    fn fake_core(
        routes: Vec<(&'static str, u16, &'static str, String)>,
    ) -> (String, Arc<Mutex<Vec<String>>>) {
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        let base = format!("http://127.0.0.1:{}", l.local_addr().unwrap().port());
        let seen = Arc::new(Mutex::new(Vec::new()));
        let log = seen.clone();
        std::thread::spawn(move || {
            for stream in l.incoming() {
                let Ok(mut s) = stream else { break };
                let _ = s.set_read_timeout(Some(Duration::from_millis(300)));
                let mut buf = [0u8; 4096];
                let n = s.read(&mut buf).unwrap_or(0);
                let req = String::from_utf8_lossy(&buf[..n]).to_string();
                let first = req.lines().next().unwrap_or("").to_owned();
                log.lock().unwrap().push(req.clone());
                let hit = routes.iter().find(|(p, ..)| first.contains(p));
                let (code, extra, body) = hit.map_or((404, "", "{}".to_owned()), |(_, c, h, b)| {
                    (*c, *h, b.clone())
                });
                let _ = write!(
                    s,
                    "HTTP/1.1 {code} X\r\nContent-Type: application/json\r\n{extra}Content-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                );
            }
        });
        (base, seen)
    }

    #[test]
    fn dates_are_right_across_month_and_year_ends() {
        assert_eq!(ymd_string(0), "1970-01-01");
        assert_eq!(ymd_string(20_453), "2025-12-31");
        assert_eq!(ymd_string(20_454), "2026-01-01");
        assert_eq!(ymd_string(19_782), "2024-02-29");
    }

    #[test]
    fn spend_is_summed_by_day_for_today_the_week_the_month_and_the_last_14_days() {
        let today = 20_454; // 2026-01-01
        let rec = |ts: &str, cost: f64, kind: &str| {
            format!(
                r#"{{"seq":1,"ts":"{ts}","task_id":"t","kind":"{kind}","data":{{"cost":{cost}}}}}"#
            )
        };
        let lines = [
            rec("2026-01-01T09:00:00.000Z", 0.5, "event:usage"),
            rec("2026-01-01T10:00:00.000Z", 0.25, "event:usage"),
            rec("2025-12-30T10:00:00.000Z", 1.0, "event:usage"),
            rec("2025-12-20T10:00:00.000Z", 2.0, "event:usage"),
            rec("2025-11-01T10:00:00.000Z", 9.0, "event:usage"),
            rec("2026-01-01T11:00:00.000Z", 7.0, "diag.model_call"),
            "not json".to_owned(),
        ]
        .join("\n");
        let r = spend_from_audit(&lines, today, Some("ok"));
        assert!((r.today - 0.75).abs() < 1e-9, "today");
        assert!(
            (r.week - 1.75).abs() < 1e-9,
            "the last 7 days: today plus Dec 30"
        );
        assert!((r.month - 0.75).abs() < 1e-9, "January only so far");
        assert_eq!(r.days.len(), 14);
        assert_eq!(r.days[13], ("2026-01-01".to_owned(), 0.75));
        assert_eq!(r.days[0].0, "2025-12-19");
        assert!(
            (r.days[1].1 - 2.0).abs() < 1e-9,
            "Dec 20 is in the 14-day chart"
        );
        assert_eq!(r.chain_ok, Some(true));
        assert_eq!(
            spend_from_audit("", today, Some("broken")).chain_ok,
            Some(false)
        );
        assert_eq!(spend_from_audit("", today, None).chain_ok, None);
    }

    #[test]
    fn memory_stats_core_info_bindings_and_gateway_are_read_from_the_core_answers() {
        let m = parse_memory_stats(
            &serde_json::json!({"blocks":[{"name":"agent_notes","chars":21},{"name":"user_profile","chars":0}],"entries":3,"newest":"2026-11-15T10:00:00Z"}),
        );
        assert_eq!(
            (m.entries, m.blocks[0].1, m.newest.as_deref()),
            (3, 21, Some("2026-11-15T10:00:00Z"))
        );
        assert_eq!(
            colo_from_cf_ray("8f1a2b3c4d5e6f70-SJC").as_deref(),
            Some("SJC")
        );
        assert_eq!(colo_from_cf_ray("nope"), None);
        let info = parse_core_info(
            &serde_json::json!({"ok":true,"version":"0.1.0"}),
            &[("cf-ray".into(), "abc-LHR".into())],
        );
        assert_eq!(
            (info.version.as_str(), info.served_from.as_deref()),
            ("0.1.0", Some("LHR"))
        );
        let rows = binding_rows(
            &serde_json::json!({"d1":true,"r2":true,"durable_objects":true,"workflow":true,"container":false,"access_secret":true,"model_key":false}),
        );
        assert_eq!(rows.len(), 7);
        assert!(rows[0].ok && !rows[4].ok && rows[4].note.contains("Workers Paid") && !rows[6].ok);
        let g = gateway_info(
            &serde_json::json!({"gateway":false,"gateway_configured":true,"gateway_via":"direct","gateway_reason":"AI Gateway refused the request (needs authentication)."}),
        );
        assert!(
            g.configured
                && !g.in_use
                && g.via == "direct"
                && g.reason.unwrap().contains("needs authentication")
        );
        assert!(!gateway_info(&serde_json::json!({})).configured);
    }

    #[test]
    fn the_models_table_filters_and_sorts() {
        let m = |id: &str, name: &str, ctx: u64, p: f64, c: f64| ModelPrice {
            id: id.into(),
            name: name.into(),
            context: ctx,
            prompt_per_m: p,
            completion_per_m: c,
            tools: true,
            reasoning: false,
        };
        let all = vec![
            m("b/large", "Large", 200_000, 3.0, 15.0),
            m("a/small", "Small", 128_000, 0.1, 0.4),
            m("c/mid", "Mid One", 32_000, 1.0, 2.0),
        ];
        let ids = |v: Vec<&ModelPrice>| v.iter().map(|m| m.id.clone()).collect::<Vec<_>>();
        assert_eq!(
            ids(view_models(&all, "", ModelSort::Name, true)),
            ["a/small", "b/large", "c/mid"]
        );
        assert_eq!(
            ids(view_models(&all, "", ModelSort::Prompt, false)),
            ["b/large", "c/mid", "a/small"]
        );
        assert_eq!(
            ids(view_models(&all, "", ModelSort::Context, true)),
            ["c/mid", "a/small", "b/large"]
        );
        assert_eq!(
            ids(view_models(&all, "MID", ModelSort::Name, true)),
            ["c/mid"],
            "matches the name, any case"
        );
        assert_eq!(
            ids(view_models(&all, "a/", ModelSort::Name, true)),
            ["a/small"],
            "matches the id"
        );
        assert!(view_models(&all, "zzz", ModelSort::Name, true).is_empty());
        assert_eq!(all[0].provider(), "b");
    }

    #[test]
    fn the_fetchers_read_the_cores_routes_and_say_plainly_when_one_is_missing() {
        let audit = format!(
            "{}\n",
            r#"{"seq":0,"ts":"2026-11-15T09:00:00.000Z","task_id":"t","kind":"event:usage","data":{"cost":0.125}}"#
        );
        let (base, seen) = fake_core(vec![
            ("/v1/audit/export", 200, "x-audit-chain: ok\r\n", audit),
            (
                "/v1/memory/stats",
                200,
                "",
                r#"{"blocks":[],"entries":2,"newest":null}"#.to_owned(),
            ),
            (
                "/v1/memory/clear",
                200,
                "",
                r#"{"ok":true,"cleared":2}"#.to_owned(),
            ),
            (
                "/v1/health",
                200,
                "cf-ray: 123-FRA\r\n",
                r#"{"ok":true,"version":"0.1.0"}"#.to_owned(),
            ),
        ]);
        let ep = Endpoint::new(&base, "core-token");
        let s = fetch_spend(&ep).unwrap();
        assert_eq!(s.chain_ok, Some(true));
        assert_eq!(s.records, 1);
        assert_eq!(fetch_memory(&ep).unwrap().entries, 2);
        assert_eq!(clear_memory(&ep).unwrap(), 2);
        let info = fetch_core_info(&ep).unwrap();
        assert_eq!(
            (info.version.as_str(), info.served_from.as_deref()),
            ("0.1.0", Some("FRA"))
        );
        let sent = seen.lock().unwrap().join("\n").to_lowercase();
        assert!(
            sent.contains("authorization: bearer core-token")
                && sent.contains("post /v1/memory/clear")
        );
        // An older core without the memory routes.
        let (old, _) = fake_core(vec![]);
        assert!(
            fetch_memory(&Endpoint::new(&old, "t"))
                .unwrap_err()
                .contains("older than the memory page")
        );
        assert!(fetch_spend(&Endpoint::new(&old, "t")).is_err());
    }

    #[test]
    fn times_are_shortened_for_tables() {
        assert_eq!(short_time("2026-11-15T12:00:00.000Z"), "2026-11-15 12:00");
        assert_eq!(short_time("bad"), "bad");
    }

    #[test]
    fn load_states() {
        let l: Load<u8> = Load::Idle;
        assert!(l.wants_fetch() && !l.is_loading() && l.ready().is_none());
        let l: Load<u8> = Ok(3).into();
        assert_eq!(l.ready(), Some(&3));
        assert!(!l.wants_fetch());
        let l: Load<u8> = Err("nope".to_owned()).into();
        assert_eq!(l.error(), Some("nope"));
        assert!(l.wants_fetch());
    }

    #[test]
    fn a_409_from_starting_a_task_is_told_apart_by_its_code() {
        let busy = r#"{"error":{"code":"session_busy","message":"This session already has a task running. Wait for it to finish or start a new session."}}"#;
        let (base, _) = fake_core(vec![("/v1/tasks", 409, "", busy.to_owned())]);
        let ep = Endpoint::new(&base, "t");
        assert_eq!(
            ep.start_task("g", "s").unwrap_err(),
            crate::chat::SESSION_BUSY
        );
        let key = r#"{"error":{"code":"key_missing","message":"No OpenRouter key is stored on this core yet."}}"#;
        let (base, _) = fake_core(vec![("/v1/tasks", 409, "", key.to_owned())]);
        assert_eq!(
            Endpoint::new(&base, "t").start_task("g", "s").unwrap_err(),
            crate::chat::KEY_MISSING
        );
        let other = r#"{"error":{"code":"conflict","message":"Something else."}}"#;
        let (base, _) = fake_core(vec![("/v1/tasks", 409, "", other.to_owned())]);
        assert_eq!(
            Endpoint::new(&base, "t").start_task("g", "s").unwrap_err(),
            "Something else."
        );
    }

    #[test]
    fn the_problem_the_core_names_is_logged_as_code_and_message() {
        let v =
            serde_json::json!({"error": {"code": "session_busy", "message": "A task is running."}});
        assert_eq!(
            crate::http::problem_detail(&v),
            "session_busy: A task is running."
        );
        assert_eq!(
            crate::http::problem_detail(&serde_json::json!({"error": {"code": "x"}})),
            "x"
        );
        assert_eq!(crate::http::problem_detail(&serde_json::Value::Null), "");
    }

    #[test]
    fn the_last_status_of_each_task_comes_from_the_audit_log() {
        let log = [
            r#"{"seq":1,"task_id":"a","kind":"event:run","data":{"status":"running"}}"#,
            r#"{"seq":2,"task_id":"b","kind":"event:run","data":{"status":"running"}}"#,
            r#"{"seq":3,"task_id":"a","kind":"event:run","data":{"status":"done"}}"#,
            r#"{"seq":4,"task_id":"a","kind":"event:usage","data":{"cost":0.1}}"#,
            "not json",
        ]
        .join("\n");
        let m = run_statuses(&log);
        assert_eq!(m.get("a").map(String::as_str), Some("done"));
        assert_eq!(m.get("b").map(String::as_str), Some("running"));
    }
}
