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
    /// `desktop.json`: where the core address must persist.
    pub config_path: PathBuf,
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
            config_path: crate::config::default_path(),
        }
    }
}

/// Runs `program args`, returning (success, stdout+stderr) or why it could not run. Gives up after `limit`.
fn run_bounded(program: &str, args: &[&str], limit: Duration) -> Result<(bool, String), String> {
    let exe = crate::harness::resolve_program(program);
    let mut cmd = Command::new(&exe);
    cmd.args(args)
        .env("PATH", signin::child_path())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = crate::harness::spawn_with_retry(&mut cmd)
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

/// The token the keyring holds, tried on the core: a stale one (an old paste) makes every call fail with 401 while the saved file's
/// token works, and the app must not hide that.
fn keyring_token_line(t: &Targets) -> Line {
    const NAME: &str = "core accepts the keyring token";
    let token = match t.real_store.get(Slot::CloudAccess) {
        Ok(Some(v)) if !v.is_empty() => v,
        Ok(_) => return Line::new(NAME, Verdict::Skip, "the keyring holds no access token"),
        Err(e) => {
            return Line::new(
                NAME,
                Verdict::Fail,
                format!("the keyring could not be read: {}", e.0),
            );
        }
    };
    let saved = crate::config::load(&t.config_path);
    let from_file = autoconnect::read_saved_core(&t.cloud_json).map(|c| c.url);
    let Some(url) = Some(saved.cloud_url)
        .filter(|u| !u.is_empty())
        .or(from_file)
    else {
        return Line::new(
            NAME,
            Verdict::Fail,
            "a token is in the keyring but no core address is saved to try it on",
        );
    };
    match Endpoint::new(&url, &token).bindings() {
        Ok(_) => Line::new(
            NAME,
            Verdict::Pass,
            format!("{url} accepted the keyring's token"),
        ),
        Err(e) if e == crate::chat::REFUSED => {
            let better = autoconnect::saved_token_differs(&t.cloud_json, &url, &token);
            Line::new(
                NAME,
                Verdict::Fail,
                format!(
                    "{e} The keyring's token is stale.{}",
                    if better {
                        " cloud.json holds a different token for this core; the app will replace the keyring's with it on start."
                    } else {
                        " Run setup or paste the token under Advanced."
                    }
                ),
            )
        }
        Err(e) => Line::new(NAME, Verdict::Fail, e),
    }
}

/// What is saved right now: the address in `desktop.json` and the token in the keyring (or the consent to use `cloud.json`).
fn persisted_line(t: &Targets) -> Line {
    const NAME: &str = "persisted connection (what survives a restart)";
    let saved = crate::config::load(&t.config_path);
    let addr = !saved.cloud_url.is_empty();
    let token = match t.real_store.get(Slot::CloudAccess) {
        Ok(Some(v)) if !v.is_empty() => Ok(true),
        Ok(_) => Ok(false),
        Err(e) => Err(e.0),
    };
    let file_ok = saved.file_token_ok
        && autoconnect::inspect_saved_core(&t.cloud_json)
            .problem
            .is_none();
    let token_words = match &token {
        Ok(true) => "token in keyring: yes".to_owned(),
        Ok(false) if file_ok => "token in keyring: no, but you chose to use cloud.json".to_owned(),
        Ok(false) => "token in keyring: no".to_owned(),
        Err(e) => format!("token in keyring: unreadable ({e})"),
    };
    let address_words = if addr {
        format!("address in desktop.json: yes ({})", saved.cloud_url)
    } else {
        "address in desktop.json: no".to_owned()
    };
    let ok = addr && (token == Ok(true) || file_ok);
    Line::new(
        NAME,
        if ok { Verdict::Pass } else { Verdict::Fail },
        format!(
            "{address_words}; {token_words}; setup finished: {}",
            if saved.setup_done { "yes" } else { "no" }
        ),
    )
}

/// Proves adoption end to end without touching the real entries: the saved token goes into the keyring under the probe service
/// name and is read back and deleted, and the address round-trips through a probe file next to `desktop.json`.
fn adoption_lines(t: &Targets) -> Vec<Line> {
    let mut out = Vec::new();
    match autoconnect::read_saved_core(&t.cloud_json) {
        None => {
            out.push(Line::new(
                "adopt: token into the keyring",
                Verdict::Fail,
                "no usable saved connection to adopt",
            ));
            out.push(Line::new(
                "persist: address survives a reload",
                Verdict::Fail,
                "no address to save",
            ));
        }
        Some(core) => {
            let url = core.url.clone();
            let tmp = crate::secrets::InMemorySecrets::new();
            let adopted = core.use_for_session(&tmp).and_then(|_| {
                let token = tmp
                    .get(Slot::CloudAccess)
                    .ok()
                    .flatten()
                    .unwrap_or_default();
                t.probe_store
                    .set_persistent(Slot::CloudAccess, &token)
                    .map_err(|e| e.0)?;
                let back = t.probe_store.get(Slot::CloudAccess).map_err(|e| e.0)?;
                let _ = t.probe_store.delete(Slot::CloudAccess);
                if back.as_deref() == Some(token.as_str()) {
                    Ok(())
                } else {
                    Err("the keyring accepted the token but did not give it back".to_owned())
                }
            });
            out.push(match adopted {
                Ok(()) => Line::new("adopt: token into the keyring", Verdict::Pass, "the saved token was stored in the keyring, read back and removed (probe entry, your real one is untouched)"),
                Err(e) => Line::new("adopt: token into the keyring", Verdict::Fail, e),
            });
            let probe = t.config_path.with_file_name("desktop.json.selftest");
            let saved = crate::config::Saved {
                cloud_url: url.clone(),
                ..Default::default()
            };
            let persisted = crate::config::save(&probe, &saved)
                .map_err(|e| format!("could not write {}: {e}", probe.display()))
                .and_then(|()| {
                    let back = crate::config::load(&probe);
                    let _ = std::fs::remove_file(&probe);
                    if back.cloud_url == url {
                        Ok(())
                    } else {
                        Err("the address did not survive a reload".to_owned())
                    }
                });
            out.push(match persisted {
                Ok(()) => Line::new(
                    "persist: address survives a reload",
                    Verdict::Pass,
                    format!(
                        "{url} written next to {} and read back",
                        t.config_path.display()
                    ),
                ),
                Err(e) => Line::new("persist: address survives a reload", Verdict::Fail, e),
            });
        }
    }
    out
}

pub fn run(t: &Targets) -> Vec<Line> {
    let mut out = vec![persisted_line(t)];
    out.extend(adoption_lines(t));
    out.push(keyring_line(t));

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

    out.push(keyring_token_line(t));

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
                let (code, body) = if req.contains("stale-token-xyz") {
                    (401, r#"{"error":{"message":"bad token"}}"#)
                } else if first.contains("/v1/diag/bindings") {
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
            config_path: std::env::temp_dir()
                .join(format!("oc-selftest-cfg-{}", std::process::id()))
                .join("desktop.json"),
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

    fn by_name<'a>(lines: &'a [Line], n: &str) -> &'a Line {
        lines
            .iter()
            .find(|l| l.name == n)
            .unwrap_or_else(|| panic!("{n}"))
    }

    #[test]
    fn nothing_persisted_is_the_first_failure_and_says_what_is_missing() {
        // The owner's symptom: a token in the keyring but no address saved.
        let url = fake_core();
        let f = temp_file("p.json", &format!(r#"{{"url":"{url}","token":"{TOKEN}"}}"#));
        let mut t = targets(f.clone(), Box::new(InMemorySecrets::new()));
        t.config_path = std::env::temp_dir()
            .join(format!("oc-selftest-none-{}", std::process::id()))
            .join("desktop.json");
        let real = InMemorySecrets::new();
        real.set(Slot::CloudAccess, TOKEN).unwrap();
        t.real_store = Box::new(real);
        let lines = run(&t);
        assert_eq!(lines[0].verdict, Verdict::Fail);
        assert!(
            lines[0].detail.contains("address in desktop.json: no")
                && lines[0].detail.contains("token in keyring: yes"),
            "{}",
            lines[0].detail
        );
        let _ = std::fs::remove_file(f);
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
        let mut t = targets(f.clone(), Box::new(InMemorySecrets::new()));
        // Something is already persisted: the address in desktop.json and the token in the keyring.
        std::fs::create_dir_all(t.config_path.parent().unwrap()).unwrap();
        crate::config::save(
            &t.config_path,
            &crate::config::Saved {
                cloud_url: url.clone(),
                ..Default::default()
            },
        )
        .unwrap();
        let real = InMemorySecrets::new();
        real.set(Slot::CloudAccess, TOKEN).unwrap();
        t.real_store = Box::new(real);
        let lines = run(&t);
        let text = render(&lines);
        assert!(!text.contains(TOKEN), "{text}");
        assert_eq!(
            lines[0].name, "persisted connection (what survives a restart)",
            "persistence is proven first: {text}"
        );
        assert_eq!(lines[0].verdict, Verdict::Pass, "{text}");
        assert_eq!(
            by_name(&lines, "adopt: token into the keyring").verdict,
            Verdict::Pass,
            "{text}"
        );
        assert_eq!(
            by_name(&lines, "persist: address survives a reload").verdict,
            Verdict::Pass,
            "{text}"
        );
        assert!(
            !t.config_path
                .with_file_name("desktop.json.selftest")
                .exists(),
            "the probe file is removed"
        );
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

    #[test]
    fn a_stale_keyring_token_fails_its_own_line_and_says_the_file_can_replace_it() {
        let url = fake_core();
        let f = temp_file(
            "stale.json",
            &format!(r#"{{"url":"{url}","token":"{TOKEN}"}}"#),
        );
        let mut t = targets(f.clone(), Box::new(InMemorySecrets::new()));
        std::fs::create_dir_all(t.config_path.parent().unwrap()).unwrap();
        crate::config::save(
            &t.config_path,
            &crate::config::Saved {
                cloud_url: url.clone(),
                ..Default::default()
            },
        )
        .unwrap();
        let real = InMemorySecrets::new();
        real.set(Slot::CloudAccess, "stale-token-xyz").unwrap();
        t.real_store = Box::new(real);
        let lines = run(&t);
        let line = by_name(&lines, "core accepts the keyring token");
        assert_eq!(line.verdict, Verdict::Fail, "{}", render(&lines));
        assert!(
            line.detail.contains("stale")
                && line.detail.contains("cloud.json holds a different token"),
            "{}",
            line.detail
        );
        assert!(
            !render(&lines).contains("stale-token-xyz"),
            "the stale token is never printed"
        );
        // The saved file's own token still works against the same core.
        assert_eq!(by_name(&lines, "core /v1/diag").verdict, Verdict::Pass);
        // A good keyring token passes.
        let real = InMemorySecrets::new();
        real.set(Slot::CloudAccess, TOKEN).unwrap();
        t.real_store = Box::new(real);
        assert_eq!(
            by_name(&run(&t), "core accepts the keyring token").verdict,
            Verdict::Pass
        );
        let _ = std::fs::remove_file(f);
    }

    #[test]
    fn the_saved_file_is_compared_by_core_and_token_without_showing_either_token() {
        let f = temp_file(
            "cmp.json",
            r#"{"url":"https://Core.Example/","token":"file-token-1"}"#,
        );
        assert!(autoconnect::saved_token_differs(
            &f,
            "https://core.example",
            "keyring-token"
        ));
        assert!(
            !autoconnect::saved_token_differs(&f, "https://core.example", "file-token-1"),
            "same token"
        );
        assert!(
            !autoconnect::saved_token_differs(&f, "https://other.example", "keyring-token"),
            "another core"
        );
        assert!(!autoconnect::saved_token_differs(
            std::path::Path::new("/nonexistent"),
            "https://core.example",
            "x"
        ));
        let _ = std::fs::remove_file(f);
    }
}
