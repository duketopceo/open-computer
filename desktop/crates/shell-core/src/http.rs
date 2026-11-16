//! One place that builds the HTTP agent, so every https call uses the system TLS (ureq panics on https without a chosen provider).

use std::time::Duration;

/// Logs a failed call (what, where, the transport's own reason) and returns the words to show: the same plain sentence as
/// before, now ending with the real reason (connection refused, DNS, TLS, timeout).
pub fn unreachable(who: &str, method: &str, path: &str, e: &ureq::Error) -> String {
    let reason = crate::applog::redact(&e.to_string());
    crate::log_warn!("http", "{who} {method} {path} failed: {reason}");
    format!("{who} could not be reached: {reason}.")
}

/// The problem a core answered with (`{"error":{"code","message"}}`) as `code: message`, for the log. Nothing secret is in it: the
/// core words its problems without values, and the log redacts anyway.
pub fn problem_detail(body: &serde_json::Value) -> String {
    let code = body["error"]["code"].as_str().unwrap_or("");
    let message = body["error"]["message"].as_str().unwrap_or("");
    match (code.is_empty(), message.is_empty()) {
        (true, true) => String::new(),
        (false, true) => code.to_owned(),
        (true, false) => message.to_owned(),
        (false, false) => format!("{code}: {message}"),
    }
}

/// Logs the status a call came back with.
pub fn answered(who: &str, method: &str, path: &str, status: u16) {
    answered_with(who, method, path, status, "");
}

/// [`answered`], with the problem the body names.
pub fn answered_with(who: &str, method: &str, path: &str, status: u16, detail: &str) {
    if status >= 400 {
        if detail.is_empty() {
            crate::log_warn!("http", "{who} {method} {path} -> {status}");
        } else {
            crate::log_warn!("http", "{who} {method} {path} -> {status} {detail}");
        }
    } else {
        crate::log_debug!("http", "{who} {method} {path} -> {status}");
    }
}

/// An agent with native TLS, status codes returned as values, and an optional whole-call timeout (`None` for event streams).
pub fn agent(timeout: Option<Duration>) -> ureq::Agent {
    ureq::Agent::config_builder()
        .timeout_global(timeout)
        .http_status_as_error(false)
        .tls_config(
            ureq::tls::TlsConfig::builder()
                .provider(ureq::tls::TlsProvider::NativeTls)
                .build(),
        )
        .build()
        .into()
}
