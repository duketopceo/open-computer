//! Launcher tests with `sh` stand-ins for the sidecar. Unix only.
#![allow(clippy::unwrap_used, clippy::expect_used)]
#![cfg(unix)]

use std::time::Duration;

use shell_core::core_process::{CoreProcess, LaunchError, LaunchSpec};

fn sh(script: &str) -> LaunchSpec {
    let mut spec = LaunchSpec::new(
        "sh",
        vec!["-c".into(), script.into()],
        "stdin-token-for-test",
    );
    spec.ready_timeout = Duration::from_secs(5);
    spec
}

fn alive(pid: u32) -> bool {
    std::process::Command::new("kill")
        .args(["-0", &pid.to_string()])
        .stderr(std::process::Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

#[test]
fn parses_a_valid_ready_line() {
    let p = CoreProcess::launch(&sh(
        r#"read t; printf '{"event":"ready","port":4242,"version":"9.9.9"}\n'; sleep 30"#,
    ))
    .unwrap();
    assert_eq!(p.port(), 4242);
    assert_eq!(p.version(), "9.9.9");
    assert_eq!(p.base_url(), "http://127.0.0.1:4242");
}

#[test]
fn rejects_a_malformed_ready_line() {
    let err =
        CoreProcess::launch(&sh(r#"read t; echo 'listening on 4242'; sleep 30"#)).unwrap_err();
    assert!(matches!(err, LaunchError::BadReadyLine(_)), "{err:?}");
}

#[test]
fn errors_when_the_sidecar_exits_before_the_ready_line() {
    let err = CoreProcess::launch(&sh("exit 3")).unwrap_err();
    assert_eq!(err, LaunchError::ExitedEarly(Some(3)));
}

#[test]
fn times_out_when_no_ready_line_arrives() {
    let mut spec = sh("read t; sleep 30");
    spec.ready_timeout = Duration::from_millis(300);
    assert_eq!(
        CoreProcess::launch(&spec).unwrap_err(),
        LaunchError::Timeout
    );
}

#[test]
fn a_missing_program_is_a_spawn_error() {
    let mut spec = sh("true");
    spec.program = "definitely-not-a-real-program-xyz".into();
    assert!(matches!(
        CoreProcess::launch(&spec).unwrap_err(),
        LaunchError::Spawn(_)
    ));
}

#[test]
fn passes_the_token_on_stdin_and_not_in_argv() {
    // The stand-in echoes what it read on stdin into the version field and what it was
    // given in argv; the token must be only in the first.
    let p = CoreProcess::launch(&sh(
        r#"read t; printf '{"event":"ready","port":4243,"version":"%s"}\n' "$t"; sleep 30"#,
    ))
    .unwrap();
    assert_eq!(p.version(), "stdin-token-for-test");
    let spec = sh("true");
    assert!(!spec.args.iter().any(|a| a.contains("stdin-token-for-test")));
}

#[test]
fn process_debug_hides_the_token() {
    let p = CoreProcess::launch(&sh(
        r#"read t; printf '{"event":"ready","port":4245,"version":"1"}\n'; sleep 30"#,
    ))
    .unwrap();
    assert!(!format!("{p:?}").contains("stdin-token-for-test"));
}

#[test]
fn kills_the_sidecar_on_drop() {
    let p = CoreProcess::launch(&sh(
        r#"read t; printf '{"event":"ready","port":4244,"version":"1"}\n'; sleep 30"#,
    ))
    .unwrap();
    let pid = p.pid();
    assert!(alive(pid));
    drop(p);
    assert!(!alive(pid), "sidecar still running after drop");
}
