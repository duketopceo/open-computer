//! Task history that survives a restart. Every conversation is kept as one line-per-event file under
//! `~/.local/state/open-computer/threads/`, written as the events arrive (the same events the chat applies, plus a `user`
//! event for what the person typed). Opening the app reads the newest ones back into the sidebar.
//!
//! This is local to the computer and independent of which agent ran the task, so built-in, Hermes and OpenCode tasks are all
//! kept. The files hold the conversation text, so they stay under the person's own state directory and are never logged.

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

use serde_json::{Value, json};

/// One conversation, as the sidebar needs it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Meta {
    pub session_id: String,
    pub title: String,
    /// Unix seconds when the first message was sent.
    pub began: u64,
}

/// A file stops growing past this, so one runaway task cannot fill the disk.
const MAX_BYTES: u64 = 4 * 1024 * 1024;
/// Conversations kept; older ones are deleted when the list is read.
const KEEP: usize = 100;

pub fn default_dir() -> PathBuf {
    let base = std::env::var_os("XDG_STATE_HOME")
        .filter(|p| !p.is_empty())
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".local").join("state")))
        .unwrap_or_else(|| PathBuf::from("."));
    base.join("open-computer").join("threads")
}

fn file_for(dir: &Path, session_id: &str) -> Option<PathBuf> {
    let ok = !session_id.is_empty()
        && session_id.len() <= 64
        && session_id
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.'));
    ok.then(|| dir.join(format!("{session_id}.jsonl")))
}

fn now_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_secs())
}

/// Adds one event to a conversation. Failures are logged and otherwise ignored: history must never break a task.
pub fn append(dir: &Path, session_id: &str, event: &Value) {
    let Some(path) = file_for(dir, session_id) else {
        return;
    };
    if fs::metadata(&path).is_ok_and(|m| m.len() > MAX_BYTES) {
        return;
    }
    let _ = fs::create_dir_all(dir);
    let line = format!("{event}\n");
    match OpenOptions::new().create(true).append(true).open(&path) {
        Ok(mut f) => {
            if let Err(e) = f.write_all(line.as_bytes()) {
                crate::log_warn!("history", "could not write {}: {e}", path.display());
            }
        }
        Err(e) => crate::log_warn!("history", "could not open {}: {e}", path.display()),
    }
}

/// Records what the person typed, with the time.
pub fn append_user(dir: &Path, session_id: &str, text: &str) {
    append(
        dir,
        session_id,
        &json!({"type": "user", "text": text, "ts": now_secs()}),
    );
}

/// Every event of a conversation, in order. Lines that do not parse are skipped.
pub fn load(dir: &Path, session_id: &str) -> Vec<Value> {
    let Some(path) = file_for(dir, session_id) else {
        return Vec::new();
    };
    fs::read_to_string(path)
        .unwrap_or_default()
        .lines()
        .filter_map(|l| serde_json::from_str(l).ok())
        .collect()
}

/// The conversations on disk, newest first. Older than [`KEEP`] are removed.
pub fn list(dir: &Path) -> Vec<Meta> {
    let Ok(rd) = fs::read_dir(dir) else {
        return Vec::new();
    };
    let mut out: Vec<Meta> = rd
        .filter_map(Result::ok)
        .filter_map(|e| {
            let name = e.file_name().to_string_lossy().into_owned();
            let session_id = name.strip_suffix(".jsonl")?.to_owned();
            let first = load_first_user(&e.path())?;
            let text = first.0.trim().replace('\n', " ");
            let title = if text.chars().count() > 44 {
                format!("{}...", text.chars().take(44).collect::<String>())
            } else {
                text
            };
            Some(Meta {
                session_id,
                title,
                began: first.1,
            })
        })
        .collect();
    out.sort_by(|a, b| b.began.cmp(&a.began).then(b.session_id.cmp(&a.session_id)));
    for old in out.split_off(out.len().min(KEEP)) {
        if let Some(p) = file_for(dir, &old.session_id) {
            let _ = fs::remove_file(p);
        }
    }
    out
}

fn load_first_user(path: &Path) -> Option<(String, u64)> {
    let text = fs::read_to_string(path).ok()?;
    text.lines().find_map(|l| {
        let v: Value = serde_json::from_str(l).ok()?;
        (v.get("type")?.as_str()? == "user").then(|| {
            (
                v.get("text")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_owned(),
                v.get("ts").and_then(Value::as_u64).unwrap_or(0),
            )
        })
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::chat::{Chat, Item};

    fn temp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("oc-history-{}-{name}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        d
    }

    #[test]
    fn a_conversation_survives_a_restart_and_rebuilds_the_same_chat() {
        let dir = temp("a");
        append_user(&dir, "app-1-100", "make me a sandwich");
        for ev in [
            json!({"type":"run","status":"running","task_id":"t1"}),
            json!({"type":"message.delta","text":"Sure, "}),
            json!({"type":"message.delta","text":"done."}),
            json!({"type":"message.done","text":"Sure, done."}),
            json!({"type":"run","status":"done"}),
        ] {
            append(&dir, "app-1-100", &ev);
        }
        // "Restart": nothing in memory, read it back.
        let metas = list(&dir);
        assert_eq!(metas.len(), 1);
        assert_eq!(metas[0].session_id, "app-1-100");
        assert_eq!(metas[0].title, "make me a sandwich");
        let mut chat = Chat::default();
        for ev in load(&dir, "app-1-100") {
            chat.apply(&ev);
        }
        assert_eq!(chat.items[0], Item::User("make me a sandwich".into()));
        assert!(
            matches!(&chat.items[1], Item::Assistant { text, done: true } if text == "Sure, done.")
        );
        assert_eq!(chat.status, "done");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn the_list_is_newest_first_keeps_only_the_newest_hundred_and_ignores_bad_names() {
        let dir = temp("b");
        for i in 0..105u64 {
            let id = format!("app-{i}");
            fs::create_dir_all(&dir).unwrap();
            fs::write(
                dir.join(format!("{id}.jsonl")),
                format!(
                    "{}\n",
                    json!({"type":"user","text":format!("task {i}"),"ts":1000 + i})
                ),
            )
            .unwrap();
        }
        fs::write(dir.join("notes.txt"), "x").unwrap();
        let metas = list(&dir);
        assert_eq!(metas.len(), 100);
        assert_eq!(metas[0].title, "task 104");
        assert!(!dir.join("app-0.jsonl").exists(), "the oldest were removed");
        append(&dir, "../escape", &json!({"type":"user"}));
        assert!(!dir.join("../escape.jsonl").exists());
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_file_stops_growing_at_the_size_limit() {
        let dir = temp("c");
        fs::create_dir_all(&dir).unwrap();
        let big = "x".repeat((MAX_BYTES + 10) as usize);
        fs::write(dir.join("s.jsonl"), &big).unwrap();
        append(&dir, "s", &json!({"type":"note","text":"more"}));
        assert_eq!(
            fs::metadata(dir.join("s.jsonl")).unwrap().len(),
            big.len() as u64
        );
        let _ = fs::remove_dir_all(&dir);
    }
}
