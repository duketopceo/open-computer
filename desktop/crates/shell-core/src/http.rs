//! One place that builds the HTTP agent, so every https call uses the system TLS (ureq panics on https without a chosen provider).

use std::time::Duration;

/// Logs a failed call (what, where, the transport's own reason) and returns the words to show: the same plain sentence as
/// before, now ending with the real reason (connection refused, DNS, TLS, timeout).
pub fn unreachable(who: &str, method: &str, path: &str, e: &ureq::Error) -> String {
    let reason = crate::applog::redact(&e.to_string());
    crate::log_warn!("http", "{who} {method} {path} failed: {reason}");
    format!("{who} could not be reached: {reason}.")
}

/// Logs the status a call came back with.
pub fn answered(who: &str, method: &str, path: &str, status: u16) {
    if status >= 400 {
        crate::log_warn!("http", "{who} {method} {path} -> {status}");
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
