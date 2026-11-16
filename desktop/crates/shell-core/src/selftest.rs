//! `open-computer --self-test`: the same checks the app depends on, run without a window, one PASS/FAIL/SKIP line each.
//! No secret is ever printed: the keyring check uses a dummy value under its own service name, the saved connection is
//! reported as "token present: yes/no", and every reason goes through [`crate::applog::redact`].

use std::io::Read;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use crate::autoconnect;
use crate::chat::Endpoint;
use crate::secrets::{KeyringSecrets, SecretStore, Slot};
use crate::signin;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Verdict {
    Pass,
    Fail,
    /// Not run, with the reason (there is nothing safe to run).
    Skip,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Line {
    pub name: &'static str,
    pub verdict: Verdict,
    pub detail: String,
}

impl Line {
    fn new(name: &'static str, verdict: Verdict, detail: impl Into<String>) -> Line {
        Line {
            name,
            verdict,
            detail: crate::applog::redact(&detail.into()),
        }
    }
}

/// What the checks look at. [`Targets::here`] is this computer; tests build their own.
pub struct Targets {
    /// The saved connection (`~/.config/open-computer/cloud.json`).
    pub cloud_json: PathBuf,
    /// The keyring probed with a dummy value.
    pub probe_store: Box<dyn SecretStore>,
    /// The keyring that holds the real access token, used when there is no saved file.
    pub real_store: Box<dyn SecretStore>,
    /// The command that runs Wrangler.
    pub wrangler: Vec<String>,
    /// Programs checked with `--version`, as (label, program).
    pub harnesses: Vec<(&'static str, String)>,
    /// How long a program may take before it counts as hung.
    pub program_timeout: Duration,
    /// The log file whose writability is checked.
    pub log: PathBuf,
}

impl Targets {
    pub fn here() -> Targets {
        Targets {
            cloud_json: autoconnect::default_state_path(),
            probe_store: Box::new(KeyringSecrets::with_service("open-computer-selftest")),
            real_store: Box::new(KeyringSecrets::new()),
            wrangler: signin::wrangler_command(),
            harnesses: vec![
                ("hermes", "hermes".to_owned()),
                ("opencode", "opencode".to_owned()),
            ],
            program_timeout: Duration::from_secs(20),
            log: crate::applog::log_path(),
        }
    }
}

/// Runs `program args`, returning (success, stdout+stderr) or why it could not run. Gives up after `limit`.
fn run_bounded(program: &str, args: &[&str], limit: Duration) -> Result<(bool, String), String> {
    let exe = crate::harness::resolve_program(program);
    let mut child = Command::new(&exe)
        .args(args)
        .env("PATH", signin::child_path())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("{program} could not be started ({e}); looked at {exe}"))?;
    let start = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                let mut text = String::new();
                if let Some(mut o) = child.stdout.take() {
                    let _ = o.read_to_string(&mut text);
                }
                if let Some(mut e) = child.stderr.take() {
                    let _ = e.read_to_string(&mut text);
                }
                return Ok((status.success(), text));
            }
            Ok(None) if start.elapsed() > limit => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(format!(
                    "{program} did not answer within {} s",
                    limit.as_secs()
                ));
            }
            Ok(None) => std::thread::sleep(Duration::from_millis(25)),
            Err(e) => return Err(format!("{program}: {e}")),
        }
    }
}

fn keyring_line(t: &Targets) -> Line {
    const DUMMY: &str = "open-computer-selftest-dummy-value";
    let s = &*t.probe_store;
    if let Err(e) = s.set(Slot::ModelKey, DUMMY) {
        return Line::new("keyring", Verdict::Fail, format!("write: {e}"));
    }
    let back = s.get(Slot::ModelKey);
    let _ = s.delete(Slot::ModelKey);
    match back {
        Ok(Some(v)) if v == DUMMY => {}
        Ok(Some(_)) => return Line::new("keyring", Verdict::Fail, "read back a different value"),
        Ok(None) => {
            return Line::new(
                "keyring",
                Verdict::Fail,
                "write said ok but the value was not there",
            );
        }
        Err(e) => return Line::new("keyring", Verdict::Fail, format!("read: {e}")),
    }
    match s.get(Slot::ModelKey) {
        Ok(None) => Line::new(
            "keyring",
            Verdict::Pass,
            "dummy entry written, read back and deleted",
        ),
        Ok(Some(_)) => Line::new(
            "keyring",
            Verdict::Fail,
            "delete said ok but the value is still there",
        ),
        Err(e) => Line::new("keyring", Verdict::Fail, format!("read after delete: {e}")),
    }
}

/// The access token for the core: the saved file first (read here, never printed), else the keyring.
fn endpoint(t: &Targets) -> Result<(Endpoint, &'static str), String> {
    if let Some(core) = autoconnect::read_saved_core(&t.cloud_json) {
        let url = core.url.clone();
        let tmp = crate::secrets::InMemorySecrets::new();
        core.use_for_session(&tmp)?;
        let token = tmp
            .get(Slot::CloudAccess)
            .ok()
            .flatten()
            .ok_or("the saved token could not be read")?;
        return Ok((Endpoint::new(&url, &token), "cloud.json"));
    }
    let saved = crate::config::load(&crate::config::default_path());
    let token = t
        .real_store
        .get(Slot::CloudAccess)
        .map_err(|e| e.0)?
        .filter(|v| !v.is_empty())
        .ok_or("no saved connection file and no access token in the keyring")?;
    if saved.cloud_url.is_empty() {
        return Err("an access token is in the keyring but no core address is saved".to_owned());
    }
    Ok((Endpoint::new(&saved.cloud_url, &token), "keyring"))
}

pub fn run(t: &Targets) -> Vec<Line> {
    let mut out = vec![keyring_line(t)];

    let saved = autoconnect::inspect_saved_core(&t.cloud_json);
    out.push(match (&saved.problem, &saved.url) {
        (None, Some(url)) => Line::new(
            "adopt cloud.json (dry run)",
            Verdict::Pass,
            format!("found url {url}, token present: yes (nothing was saved)"),
        ),
        (Some(p), url) => Line::new(
            "adopt cloud.json (dry run)",
            Verdict::Fail,
            format!(
                "{p}; url {}, token present: {}",
                url.as_deref().unwrap_or("none"),
                if saved.token_present { "yes" } else { "no" }
            ),
        ),
        (None, None) => Line::new("adopt cloud.json (dry run)", Verdict::Fail, "no address"),
    });

    match endpoint(t) {
        Err(e) => {
            for name in ["core /v1/diag", "openrouter key via core"] {
                out.push(Line::new(
                    name,
                    Verdict::Fail,
                    format!("no connection to try: {e}"),
                ));
            }
        }
        Ok((ep, from)) => {
            out.push(match ep.bindings() {
                Ok(b) => {
                    let flags = ["d1", "r2", "durable_objects", "container", "model_key"]
                        .iter()
                        .map(|k| {
                            format!(
                                "{k}={}",
                                b.get(*k).and_then(|v| v.as_bool()).unwrap_or(false)
                            )
                        })
                        .collect::<Vec<_>>()
                        .join(" ");
                    Line::new(
                        "core /v1/diag",
                        Verdict::Pass,
                        format!("{} answered (token from {from}); {flags}", ep.base),
                    )
                }
                Err(e) => Line::new(
                    "core /v1/diag",
                    Verdict::Fail,
                    format!("{} (token from {from})", e),
                ),
            });
            out.push(match ep.get("/v1/openrouter/key") {
                Ok((200, v)) => Line::new(
                    "openrouter key via core",
                    Verdict::Pass,
                    format!(
                        "the core reached OpenRouter and the key is accepted (usage today {})",
                        v.get("usage_daily")
                            .and_then(|u| u.as_f64())
                            .map_or("unknown".to_owned(), |u| format!("${u:.4}"))
                    ),
                ),
                Ok((status, v)) => Line::new(
                    "openrouter key via core",
                    Verdict::Fail,
                    format!(
                        "the core answered {status}: {}",
                        v.pointer("/error/message")
                            .and_then(|m| m.as_str())
                            .unwrap_or("no message")
                    ),
                ),
                Err(e) => Line::new("openrouter key via core", Verdict::Fail, e),
            });
        }
    }

    out.push(match signin::whoami_with(&t.wrangler) {
        Ok(s) if s.signed_in => Line::new(
            "wrangler whoami",
            Verdict::Pass,
            format!(
                "signed in: yes, account {}, missing scopes: {}",
                if s.accounts.is_empty() {
                    "unknown".to_owned()
                } else {
                    s.accounts.join(", ")
                },
                if s.missing.is_empty() {
                    "none".to_owned()
                } else {
                    s.missing.join(", ")
                }
            ),
        ),
        Ok(_) => Line::new(
            "wrangler whoami",
            Verdict::Fail,
            "signed in: no (run `wrangler login`)",
        ),
        Err(e) => Line::new("wrangler whoami", Verdict::Fail, e),
    });

    for (label, program) in &t.harnesses {
        let name: &'static str = if *label == "hermes" {
            "hermes --version"
        } else {
            "opencode --version"
        };
        out.push(
            match run_bounded(program.as_str(), &["--version"], t.program_timeout) {
                Ok((true, text)) => Line::new(
                    name,
                    Verdict::Pass,
                    text.lines()
                        .map(str::trim)
                        .find(|l| !l.is_empty())
                        .unwrap_or("(no output)")
                        .to_owned(),
                ),
                Ok((false, text)) => Line::new(
                    name,
                    Verdict::Fail,
                    format!("exited with an error: {}", text.trim()),
                ),
                Err(e) => Line::new(name, Verdict::Fail, e),
            },
        );
    }
    out.push(Line::new(
        "hermes/opencode dry run",
        Verdict::Skip,
        "neither agent has a mode that checks its model without calling it, and a real call spends money; run `hermes doctor` or `opencode auth list` yourself to see their model setup",
    ));

    out.push(
        match crate::applog::probe_write(&t.log, "self-test log write check") {
            Ok(()) => Line::new(
                "log file",
                Verdict::Pass,
                format!("writing to {}", t.log.display()),
            ),
            Err(e) => Line::new(
                "log file",
                Verdict::Fail,
                format!("{}: {e}", t.log.display()),
            ),
        },
    );
    out
}

/// One line per check: `PASS name: detail`.
pub fn render(lines: &[Line]) -> String {
    lines
        .iter()
        .map(|l| {
            let v = match l.verdict {
                Verdict::Pass => "PASS",
                Verdict::Fail => "FAIL",
                Verdict::Skip => "SKIP",
            };
            format!("{v} {}: {}", l.name, l.detail)
        })
        .collect::<Vec<_>>()
        .join("\n")
}

/// 0 when nothing failed, 1 otherwise (a skip is not a failure).
pub fn exit_code(lines: &[Line]) -> i32 {
    i32::from(lines.iter().any(|l| l.verdict == Verdict::Fail))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::secrets::{InMemorySecrets, SecretError};
    use std::io::Write;
    use std::net::TcpListener;

    const TOKEN: &str = "tok-self-test-sentinel-98765";

    fn fake_core() -> String {
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        let base = format!("http://127.0.0.1:{}", l.local_addr().unwrap().port());
        std::thread::spawn(move || {
            for stream in l.incoming() {
                let Ok(mut s) = stream else { break };
                let _ = s.set_read_timeout(Some(Duration::from_millis(300)));
                let mut buf = [0u8; 4096];
                let n = s.read(&mut buf).unwrap_or(0);
                let req = String::from_utf8_lossy(&buf[..n]).to_string();
                let first = req.lines().next().unwrap_or("").to_owned();
                let (code, body) = if first.contains("/v1/diag/bindings") {
                    (
                        200,
                        r#"{"d1":true,"r2":true,"durable_objects":true,"container":true,"model_key":true}"#,
                    )
                } else if first.contains("/v1/openrouter/key") {
                    (200, r#"{"label":"k","usage_daily":0.5}"#)
                } else {
                    (404, "{}")
                };
                let _ = write!(
                    s,
                    "HTTP/1.1 {code} X\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                );
            }
        });
        base
    }

    fn targets(cloud_json: PathBuf, probe: Box<dyn SecretStore>) -> Targets {
        Targets {
            cloud_json,
            probe_store: probe,
            real_store: Box::new(InMemorySecrets::new()),
            wrangler: vec!["sh".into(), "-c".into(), "echo 'You are logged in with an OAuth Token, associated with the email you@example.com.'; echo 'Account Name  Account ID'; exit 0".into()],
            harnesses: vec![("hermes", fake_agent())],
            program_timeout: Duration::from_secs(5),
            log: std::env::temp_dir().join(format!("oc-selftest-{}.log", std::process::id())),
        }
    }

    /// A stand-in agent program that answers `--version` the way the real ones do.
    fn fake_agent() -> String {
        let dir = std::env::temp_dir().join(format!("oc-selftest-agent-{}", std::process::id()));
        let _ = std::fs::create_dir_all(&dir);
        let p = dir.join("fake-agent.sh");
        std::fs::write(&p, "#!/bin/sh\necho 'Fake Agent v1.2.3'\n").unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o755)).unwrap();
        }
        p.to_string_lossy().into_owned()
    }

    fn temp_file(name: &str, text: &str) -> PathBuf {
        let p = std::env::temp_dir().join(format!("oc-selftest-{}-{name}", std::process::id()));
        std::fs::write(&p, text).unwrap();
        p
    }

    #[test]
    fn a_healthy_machine_passes_every_check_and_never_prints_the_token() {
        let url = fake_core();
        let f = temp_file(
            "ok.json",
            &format!(r#"{{"url":"{url}","token":"{TOKEN}"}}"#),
        );
        let lines = run(&targets(f.clone(), Box::new(InMemorySecrets::new())));
        let text = render(&lines);
        assert!(!text.contains(TOKEN), "{text}");
        let by = |n: &str| {
            lines
                .iter()
                .find(|l| l.name == n)
                .unwrap_or_else(|| panic!("{n}: {text}"))
        };
        assert_eq!(by("keyring").verdict, Verdict::Pass, "{text}");
        assert!(
            by("adopt cloud.json (dry run)")
                .detail
                .contains("token present: yes")
        );
        assert_eq!(by("core /v1/diag").verdict, Verdict::Pass, "{text}");
        assert_eq!(
            by("openrouter key via core").verdict,
            Verdict::Pass,
            "{text}"
        );
        assert_eq!(by("hermes --version").verdict, Verdict::Pass, "{text}");
        assert_eq!(by("hermes/opencode dry run").verdict, Verdict::Skip);
        assert_eq!(by("log file").verdict, Verdict::Pass, "{text}");
        assert_eq!(by("wrangler whoami").verdict, Verdict::Pass, "{text}");
        assert!(
            render(&lines)
                .lines()
                .all(|l| l.starts_with("PASS ") || l.starts_with("SKIP ")),
            "{text}"
        );
        assert_eq!(exit_code(&lines), 0);
        let _ = std::fs::remove_file(f);
    }

    struct Refusing;
    impl SecretStore for Refusing {
        fn set(&self, _: Slot, _: &str) -> Result<(), SecretError> {
            Err(SecretError(
                "Your system keyring refused to save the model key: no answer after 8 s.".into(),
            ))
        }
        fn get(&self, _: Slot) -> Result<Option<String>, SecretError> {
            Ok(None)
        }
        fn delete(&self, _: Slot) -> Result<(), SecretError> {
            Ok(())
        }
    }

    #[test]
    fn a_refusing_keyring_and_a_missing_file_fail_with_the_reason_and_exit_code_one() {
        let lines = run(&targets(
            PathBuf::from("/nonexistent/cloud.json"),
            Box::new(Refusing),
        ));
        let by = |n: &str| lines.iter().find(|l| l.name == n).unwrap();
        assert_eq!(by("keyring").verdict, Verdict::Fail);
        assert!(by("keyring").detail.contains("refused to save"));
        assert_eq!(by("adopt cloud.json (dry run)").verdict, Verdict::Fail);
        assert!(
            by("adopt cloud.json (dry run)")
                .detail
                .contains("no saved connection file")
        );
        assert_eq!(by("core /v1/diag").verdict, Verdict::Fail);
        assert_eq!(exit_code(&lines), 1);
    }

    #[test]
    fn a_missing_or_hung_program_fails_with_its_reason() {
        let r = run_bounded(
            "definitely-not-a-program-xyz",
            &["--version"],
            Duration::from_secs(1),
        );
        assert!(r.unwrap_err().contains("could not be started"));
        let r = run_bounded("sh", &["-c", "sleep 5"], Duration::from_millis(150));
        assert!(r.unwrap_err().contains("did not answer"));
    }
}
