//! What the app remembers between launches: the cloud core's address, the backend choice and whether setup finished.
//! No secret is ever written here; the keyring holds those.

use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Saved {
    #[serde(default)]
    pub cloud_url: String,
    #[serde(default)]
    pub local_only: bool,
    /// Set when the first test passed, so the next launch opens the app instead of setup.
    #[serde(default)]
    pub setup_done: bool,
    /// `builtin`, `hermes` or `opencode`.
    #[serde(default)]
    pub harness: String,
    /// The command sign-in pages open with (empty: the system browser), for example `chromium --profile-directory="Profile 2"`.
    #[serde(default)]
    pub signin_browser: String,
    /// The person turned the moving background off (it is on unless this is set).
    #[serde(default)]
    pub ambient_off: bool,
    /// The person agreed to use the token in `cloud.json` for this session when the keyring refuses to hold it.
    #[serde(default)]
    pub file_token_ok: bool,
    /// Harness ids (`hermes`, `opencode`) told to use their own default model instead of Open Computer's.
    #[serde(default)]
    pub harness_native_model: Vec<String>,
    /// The person agreed to hand their OpenRouter key to Hermes or OpenCode through `OPENROUTER_API_KEY`.
    #[serde(default)]
    pub harness_key: bool,
}

pub fn default_path() -> PathBuf {
    let base = std::env::var_os("XDG_CONFIG_HOME")
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".config")))
        .unwrap_or_else(|| PathBuf::from("."));
    base.join("open-computer").join("desktop.json")
}

pub fn load(path: &Path) -> Saved {
    fs::read_to_string(path)
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or_default()
}

pub fn save(path: &Path, saved: &Saved) -> std::io::Result<()> {
    if let Some(dir) = path.parent() {
        fs::create_dir_all(dir)?;
    }
    fs::write(
        path,
        serde_json::to_string_pretty(saved).unwrap_or_else(|_| "{}".to_owned()),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp(name: &str) -> PathBuf {
        let p = std::env::temp_dir().join(format!("oc-config-test-{}-{name}", std::process::id()));
        let _ = fs::remove_file(&p);
        p
    }

    #[test]
    fn round_trips_and_a_missing_or_broken_file_means_defaults() {
        let p = temp("a.json");
        assert_eq!(load(&p), Saved::default());
        let s = Saved {
            cloud_url: "https://core.example".into(),
            local_only: false,
            setup_done: true,
            harness: "hermes".into(),
            ambient_off: true,
            signin_browser: "firefox -P work".into(),
            file_token_ok: true,
            harness_key: true,
            harness_native_model: vec!["opencode".into()],
        };
        save(&p, &s).unwrap();
        assert_eq!(load(&p), s);
        fs::write(&p, "{not json").unwrap();
        assert_eq!(load(&p), Saved::default());
        let _ = fs::remove_file(&p);
    }

    #[test]
    fn the_file_holds_no_secret_field() {
        let p = temp("b.json");
        save(
            &p,
            &Saved {
                cloud_url: "https://x".into(),
                ..Saved::default()
            },
        )
        .unwrap();
        let text = fs::read_to_string(&p).unwrap().to_lowercase();
        for word in ["secret", "password"] {
            assert!(!text.contains(word), "{word}");
        }
        let _ = fs::remove_file(&p);
    }
}
