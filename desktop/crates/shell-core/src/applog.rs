//! The app's own log: one rotating file (`~/.local/state/open-computer/desktop.log`) and, when stderr is a terminal, stderr.
//!
//! Level is `info` unless `RUST_LOG` names another (`error`, `warn`, `info`, `debug`, `trace`; the highest word wins, so
//! `RUST_LOG=open_computer=debug` works). Callers never pass a secret: only slot names, hosts, statuses and error words.
//! [`redact`] is a second line of defence for text that comes from outside (a program's stderr, an HTTP error).

use std::fs::{self, OpenOptions};
use std::io::{IsTerminal, Write};
use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Level {
    Error,
    Warn,
    Info,
    Debug,
    Trace,
}

impl Level {
    fn word(self) -> &'static str {
        match self {
            Level::Error => "ERROR",
            Level::Warn => "WARN ",
            Level::Info => "INFO ",
            Level::Debug => "DEBUG",
            Level::Trace => "TRACE",
        }
    }
}

/// The level named by a `RUST_LOG` value; `info` when it names none.
pub fn parse_level(spec: &str) -> Level {
    let lower = spec.to_ascii_lowercase();
    let mut best: Option<Level> = None;
    for word in lower.split(|c: char| !c.is_ascii_alphabetic()) {
        let l = match word {
            "error" => Level::Error,
            "warn" | "warning" => Level::Warn,
            "info" => Level::Info,
            "debug" => Level::Debug,
            "trace" => Level::Trace,
            _ => continue,
        };
        best = Some(best.map_or(l, |b| b.max(l)));
    }
    best.unwrap_or(Level::Info)
}

/// Where the log goes: `OC_LOG_FILE`, else `$XDG_STATE_HOME/open-computer/desktop.log`, else `~/.local/state/...`.
pub fn log_path() -> PathBuf {
    if let Some(p) = std::env::var_os("OC_LOG_FILE").filter(|p| !p.is_empty()) {
        return PathBuf::from(p);
    }
    let base = std::env::var_os("XDG_STATE_HOME")
        .filter(|p| !p.is_empty())
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".local").join("state")))
        .unwrap_or_else(|| PathBuf::from("."));
    base.join("open-computer").join("desktop.log")
}

struct State {
    level: Level,
    path: PathBuf,
    max_bytes: u64,
    to_stderr: bool,
}

static STATE: OnceLock<Mutex<State>> = OnceLock::new();

/// Starts the log. Safe to call more than once; the first call wins.
pub fn init() {
    let level = parse_level(&std::env::var("RUST_LOG").unwrap_or_default());
    let _ = STATE.set(Mutex::new(State {
        level,
        path: log_path(),
        max_bytes: 1024 * 1024,
        to_stderr: std::io::stderr().is_terminal(),
    }));
}

/// The time as `2026-11-16T09:30:00Z` (UTC, no dependency).
fn stamp() -> String {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_secs());
    let (days, rem) = ((secs / 86_400) as i64, secs % 86_400);
    format!(
        "{}T{:02}:{:02}:{:02}Z",
        crate::settings_data::ymd_string(days),
        rem / 3600,
        (rem % 3600) / 60,
        rem % 60
    )
}

/// Writes one line when `level` is enabled. `target` names the part of the app (`keyring`, `core`, `wrangler`...).
pub fn log(level: Level, target: &str, msg: &str) {
    let Some(state) = STATE.get() else { return };
    emit(
        &state.lock().unwrap_or_else(|e| e.into_inner()),
        level,
        target,
        msg,
    );
}

fn emit(st: &State, level: Level, target: &str, msg: &str) {
    if level > st.level {
        return;
    }
    let line = format!(
        "{} {} {target}: {}\n",
        stamp(),
        level.word(),
        redact(msg).replace('\n', " | ")
    );
    if st.to_stderr {
        let _ = std::io::stderr().write_all(line.as_bytes());
    }
    if let Some(dir) = st.path.parent() {
        let _ = fs::create_dir_all(dir);
    }
    if fs::metadata(&st.path).is_ok_and(|m| m.len() > st.max_bytes) {
        let mut old = st.path.clone().into_os_string();
        old.push(".1");
        let _ = fs::rename(&st.path, PathBuf::from(old));
    }
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(&st.path) {
        let _ = f.write_all(line.as_bytes());
    }
}

#[macro_export]
macro_rules! log_info {
    ($t:expr, $($a:tt)*) => { $crate::applog::log($crate::applog::Level::Info, $t, &format!($($a)*)) };
}
#[macro_export]
macro_rules! log_warn {
    ($t:expr, $($a:tt)*) => { $crate::applog::log($crate::applog::Level::Warn, $t, &format!($($a)*)) };
}
#[macro_export]
macro_rules! log_error {
    ($t:expr, $($a:tt)*) => { $crate::applog::log($crate::applog::Level::Error, $t, &format!($($a)*)) };
}
#[macro_export]
macro_rules! log_debug {
    ($t:expr, $($a:tt)*) => { $crate::applog::log($crate::applog::Level::Debug, $t, &format!($($a)*)) };
}

fn is_token_char(c: char) -> bool {
    c.is_ascii_alphanumeric() || matches!(c, '_' | '-' | '.' | '+' | '/')
}

/// Hides anything that looks like a credential: text after `Bearer `, `token`/`key`/`secret`/`password` assignments,
/// `sk-...` keys, and any long unbroken run of token characters that mixes letters and digits.
pub fn redact(text: &str) -> String {
    const MARKERS: [&str; 7] = [
        "bearer",
        "token",
        "secret",
        "password",
        "key",
        "authorization",
        "apikey",
    ];
    let mut out = String::with_capacity(text.len());
    let chars: Vec<char> = text.chars().collect();
    let mut i = 0;
    // The last marker word seen (and only separators since), and whether a `:`, `=` or quote came after it. A value follows a
    // marker after such a separator, or after `Bearer` with just a space; "no token found" stays readable.
    let mut marker: Option<bool> = None; // Some(is_bearer)
    let mut strong = false;
    while i < chars.len() {
        if !is_token_char(chars[i]) {
            match chars[i] {
                ' ' => {}
                ':' | '=' | '"' | '\'' => strong = true,
                _ => marker = None,
            }
            out.push(chars[i]);
            i += 1;
            continue;
        }
        let start = i;
        while i < chars.len() && is_token_char(chars[i]) {
            i += 1;
        }
        let word: String = chars[start..i].iter().collect();
        let lower = word.to_ascii_lowercase();
        if MARKERS.contains(&lower.as_str()) {
            marker = Some(lower == "bearer" || marker == Some(true));
            if lower != "bearer" && marker != Some(true) {
                marker = Some(false);
            }
            strong = false;
            out.push_str(&word);
            continue;
        }
        let after_marker = marker.is_some_and(|bearer| bearer || strong);
        let looks_long = word.len() >= 28
            && word.chars().any(|c| c.is_ascii_digit())
            && word.chars().any(|c| c.is_ascii_alphabetic());
        if after_marker || (lower.starts_with("sk-") && word.len() >= 12) || looks_long {
            out.push_str("[hidden]");
        } else {
            out.push_str(&word);
        }
        marker = None;
        strong = false;
    }
    out
}

/// Writes one line to `path` the way the log does, and says whether it worked. For the self-test.
pub fn probe_write(path: &std::path::Path, msg: &str) -> Result<(), String> {
    if let Some(dir) = path.parent() {
        fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    let mut f = OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(|e| e.to_string())?;
    f.write_all(format!("{} INFO  selftest: {msg}\n", stamp()).as_bytes())
        .map_err(|e| e.to_string())
}

/// The last `n` lines of the log (the file before it too when this one is short), redacted. For "Copy diagnostics".
pub fn tail(n: usize) -> String {
    let path = log_path();
    let mut old = path.clone().into_os_string();
    old.push(".1");
    let mut lines: Vec<String> = Vec::new();
    for p in [PathBuf::from(old), path] {
        if let Ok(t) = fs::read_to_string(p) {
            lines.extend(t.lines().map(str::to_owned));
        }
    }
    let from = lines.len().saturating_sub(n);
    lines[from..]
        .iter()
        .map(|l| redact(l))
        .collect::<Vec<_>>()
        .join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rust_log_picks_the_highest_named_level_and_defaults_to_info() {
        assert_eq!(parse_level(""), Level::Info);
        assert_eq!(parse_level("debug"), Level::Debug);
        assert_eq!(parse_level("open_computer=debug,ureq=warn"), Level::Debug);
        assert_eq!(parse_level("error"), Level::Error);
        assert_eq!(parse_level("nonsense"), Level::Info);
    }

    #[test]
    fn redaction_hides_credentials_and_keeps_ordinary_words() {
        let r = redact("GET /v1/x failed: Authorization: Bearer abc123def456 for core.example.com");
        assert!(
            !r.contains("abc123def456") && r.contains("core.example.com"),
            "{r}"
        );
        let r = redact(r#"{"token":"s3cr3tvalue","url":"https://a.b"}"#);
        assert!(
            !r.contains("s3cr3tvalue") && r.contains("https://a.b"),
            "{r}"
        );
        let r = redact("key sk-or-v1-0123456789abcdef used; token=zzz");
        assert!(!r.contains("0123456789abcdef") && !r.contains("zzz"), "{r}");
        let long = "a1".repeat(20);
        assert!(!redact(&format!("id {long} end")).contains(&long));
        assert_eq!(
            redact("token present: yes; the file has no token"),
            "token present: yes; the file has no token"
        );
        assert_eq!(
            redact("hermes exited with code 1: no model configured"),
            "hermes exited with code 1: no model configured"
        );
    }

    #[test]
    fn lines_are_written_filtered_by_level_and_tail_returns_the_last_ones() {
        let dir = std::env::temp_dir().join(format!("oc-applog-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        let file = dir.join("t.log");
        let st = State {
            level: Level::Info,
            path: file.clone(),
            max_bytes: 200,
            to_stderr: false,
        };
        let log = |l: Level, m: &str| emit(&st, l, "test", m);
        log(Level::Info, "first line");
        log(Level::Debug, "hidden by level");
        for i in 0..8 {
            log(Level::Info, &format!("line {i} Bearer topsecret999"));
        }
        let text = fs::read_to_string(&file).unwrap_or_default();
        assert!(!text.contains("hidden by level") && !text.contains("topsecret999"));
        assert!(dir.join("t.log.1").exists(), "rotated at the size limit");
        let _ = fs::remove_dir_all(&dir);
    }
}
