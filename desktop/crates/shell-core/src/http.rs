//! One place that builds the HTTP agent, so every https call uses the system TLS (ureq panics on https without a chosen provider).

use std::time::Duration;

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
