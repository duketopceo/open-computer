//! Launching the local core sidecar.
//!
//! The sidecar binds to loopback on a random port, reads the bearer token from stdin and
//! prints one JSON line, `{"event":"ready","port":N,"version":"..."}`, to stdout. The
//! launcher passes the token on stdin (never argv or env), waits for that line and kills
//! the sidecar when the handle is dropped.

use std::fmt;
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, Command, ExitStatus, Stdio};
use std::sync::mpsc;
use std::time::Duration;

use crate::core_client::{CoreClient, ReadyLine};

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum LaunchError {
    /// The program could not be started (missing, not executable).
    Spawn(String),
    /// The first stdout line was not a valid ready line.
    BadReadyLine(String),
    /// The sidecar ended before it printed the ready line.
    ExitedEarly(Option<i32>),
    /// No ready line within the timeout.
    Timeout,
    /// Writing the token to the sidecar failed.
    Stdin(String),
}

impl fmt::Display for LaunchError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let words = match self {
            LaunchError::Spawn(_) => "The local core could not be started.",
            LaunchError::BadReadyLine(_) => "The local core did not report that it was ready.",
            LaunchError::ExitedEarly(_) => "The local core stopped before it was ready.",
            LaunchError::Timeout => "The local core took too long to start.",
            LaunchError::Stdin(_) => "The local core did not accept its start-up token.",
        };
        f.write_str(words)
    }
}

impl std::error::Error for LaunchError {}

/// What to run. `Debug` never shows the token.
#[derive(Clone)]
pub struct LaunchSpec {
    pub program: String,
    pub args: Vec<String>,
    pub token: String,
    pub ready_timeout: Duration,
}

impl fmt::Debug for LaunchSpec {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("LaunchSpec")
            .field("program", &self.program)
            .field("args", &self.args)
            .field("ready_timeout", &self.ready_timeout)
            .finish_non_exhaustive()
    }
}

impl LaunchSpec {
    pub const DEFAULT_READY_TIMEOUT: Duration = Duration::from_secs(15);

    pub fn new(program: impl Into<String>, args: Vec<String>, token: impl Into<String>) -> Self {
        Self {
            program: program.into(),
            args,
            token: token.into(),
            ready_timeout: Self::DEFAULT_READY_TIMEOUT,
        }
    }

    /// Split a command text such as `/path/to/core` or `node dist/main.js` on whitespace.
    /// Returns `None` for an empty text.
    pub fn from_command_text(text: &str, token: impl Into<String>) -> Option<Self> {
        let mut parts = text.split_whitespace().map(str::to_owned);
        let program = parts.next()?;
        Some(Self::new(program, parts.collect(), token))
    }
}

/// A fresh random bearer token for one launch.
pub fn generate_token() -> Result<String, getrandom::Error> {
    let mut bytes = [0u8; 32];
    getrandom::fill(&mut bytes)?;
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect())
}

/// Parse and validate the ready line.
pub fn parse_ready_line(line: &str) -> Result<ReadyLine, LaunchError> {
    let ready: ReadyLine =
        serde_json::from_str(line.trim()).map_err(|e| LaunchError::BadReadyLine(e.to_string()))?;
    if ready.event != "ready" {
        return Err(LaunchError::BadReadyLine(format!(
            "unexpected event {:?}",
            ready.event
        )));
    }
    if ready.port == 0 {
        return Err(LaunchError::BadReadyLine("port 0".to_owned()));
    }
    Ok(ready)
}

/// A running sidecar. Dropping it kills the process.
pub struct CoreProcess {
    child: Child,
    // Held open so the sidecar's stdin stays valid; closed on drop.
    _stdin: ChildStdin,
    ready: ReadyLine,
    token: String,
}

impl fmt::Debug for CoreProcess {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("CoreProcess")
            .field("pid", &self.child.id())
            .field("ready", &self.ready)
            .finish_non_exhaustive()
    }
}

impl CoreProcess {
    pub fn launch(spec: &LaunchSpec) -> Result<CoreProcess, LaunchError> {
        let mut child = Command::new(&spec.program)
            .args(&spec.args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| LaunchError::Spawn(e.kind().to_string()))?;

        let Some(mut stdin) = child.stdin.take() else {
            kill(&mut child);
            return Err(LaunchError::Stdin("no stdin pipe".to_owned()));
        };
        let Some(stdout) = child.stdout.take() else {
            kill(&mut child);
            return Err(LaunchError::Spawn("no stdout pipe".to_owned()));
        };

        // A sidecar that exits at once closes its stdin; a failed write is then reported
        // as the more useful `ExitedEarly` below, so the write result is not checked here.
        let _ = stdin
            .write_all(spec.token.as_bytes())
            .and_then(|()| stdin.write_all(b"\n"))
            .and_then(|()| stdin.flush());

        let (tx, rx) = mpsc::channel();
        std::thread::spawn(move || {
            let mut line = String::new();
            let read = BufReader::new(stdout).read_line(&mut line);
            let _ = tx.send(read.map(|n| (n, line)));
        });

        let ready = match rx.recv_timeout(spec.ready_timeout) {
            Ok(Ok((0, _))) | Err(mpsc::RecvTimeoutError::Disconnected) => {
                let status = wait_briefly(&mut child);
                kill(&mut child);
                return Err(LaunchError::ExitedEarly(status.and_then(|s| s.code())));
            }
            Ok(Ok((_, line))) => parse_ready_line(&line),
            Ok(Err(e)) => Err(LaunchError::BadReadyLine(e.kind().to_string())),
            Err(mpsc::RecvTimeoutError::Timeout) => Err(LaunchError::Timeout),
        };
        match ready {
            Ok(ready) => Ok(CoreProcess {
                child,
                _stdin: stdin,
                ready,
                token: spec.token.clone(),
            }),
            Err(e) => {
                kill(&mut child);
                Err(e)
            }
        }
    }

    pub fn port(&self) -> u16 {
        self.ready.port
    }

    pub fn version(&self) -> &str {
        &self.ready.version
    }

    pub fn base_url(&self) -> String {
        format!("http://127.0.0.1:{}", self.ready.port)
    }

    pub fn client(&self) -> CoreClient {
        CoreClient::for_port(self.ready.port, self.token.clone())
    }

    pub fn pid(&self) -> u32 {
        self.child.id()
    }
}

impl Drop for CoreProcess {
    fn drop(&mut self) {
        kill(&mut self.child);
    }
}

fn kill(child: &mut Child) {
    let _ = child.kill();
    let _ = child.wait();
}

/// The exit status if the child has ended or ends within a short grace period.
fn wait_briefly(child: &mut Child) -> Option<ExitStatus> {
    for _ in 0..20 {
        if let Ok(Some(status)) = child.try_wait() {
            return Some(status);
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_the_fixture_ready_line() {
        let ready =
            parse_ready_line(r#"{"event":"ready","port":54321,"version":"0.1.0"}"#).unwrap();
        assert_eq!(ready.port, 54321);
        assert_eq!(ready.version, "0.1.0");
    }

    #[test]
    fn rejects_malformed_ready_lines() {
        for bad in [
            "",
            "not json",
            r#"{"event":"ready"}"#,
            r#"{"event":"ready","port":"80","version":"x"}"#,
            r#"{"event":"ready","port":70000,"version":"x"}"#,
            r#"{"event":"ready","port":0,"version":"x"}"#,
            r#"{"event":"starting","port":80,"version":"x"}"#,
        ] {
            assert!(
                matches!(parse_ready_line(bad), Err(LaunchError::BadReadyLine(_))),
                "accepted {bad:?}"
            );
        }
    }

    #[test]
    fn tokens_are_long_and_distinct() {
        let a = generate_token().unwrap();
        let b = generate_token().unwrap();
        assert_eq!(a.len(), 64);
        assert_ne!(a, b);
    }

    #[test]
    fn command_text_splits_program_and_args() {
        let spec = LaunchSpec::from_command_text("node dist/main.js", "t").unwrap();
        assert_eq!(spec.program, "node");
        assert_eq!(spec.args, vec!["dist/main.js"]);
        assert!(LaunchSpec::from_command_text("   ", "t").is_none());
    }

    #[test]
    fn spec_debug_hides_the_token() {
        let spec = LaunchSpec::new("core", vec![], "super-secret-token");
        assert!(!format!("{spec:?}").contains("super-secret-token"));
    }
}
