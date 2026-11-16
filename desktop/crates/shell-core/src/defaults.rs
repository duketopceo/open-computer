//! The owner's defaults, read from the one shared contract file (`core/contracts/defaults.json`).

use serde_json::Value;

const DEFAULTS_JSON: &str = include_str!("../../../../core/contracts/defaults.json");

fn field(name: &str) -> String {
    serde_json::from_str::<Value>(DEFAULTS_JSON)
        .ok()
        .and_then(|v| v[name].as_str().map(str::to_owned))
        .unwrap_or_default()
}

pub fn default_model() -> String {
    field("default_model")
}

pub fn worker_model() -> String {
    field("worker_model")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn both_models_are_named() {
        assert!(!default_model().is_empty());
        assert!(!worker_model().is_empty());
        assert_ne!(default_model(), worker_model());
    }
}
