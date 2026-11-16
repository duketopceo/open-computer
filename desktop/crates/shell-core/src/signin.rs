//! "Sign in with Cloudflare": the person clicks Allow on Cloudflare's own page in their browser. This runs Wrangler's
//! OAuth login with the scopes setup needs; Wrangler keeps and refreshes the session, and this code never reads it.
//! No token is minted, pasted or shown. Mirrors `core/packages/diagnostics/src/signin.ts`.

use std::io::BufRead;
use std::process::{Command, Stdio};

pub const REQUIRED_SCOPES: [&str; 10] = [
    "account:read",
    "user:read",
    "workers:write",
    "workers_scripts:write",
    "workers_routes:write",
    "workers_tail:read",
    "d1:write",
    "secrets_store:write",
    "containers:write",
    "cloudchamber:write",
];

pub fn sign_in_args() -> Vec<String> {
    let mut v = vec!["login".to_owned(), "--scopes".to_owned()];
    v.extend(REQUIRED_SCOPES.iter().map(|s| (*s).to_owned()));
    v
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct SignIn {
    pub signed_in: bool,
    pub email: Option<String>,
    pub accounts: Vec<String>,
    pub scopes: Vec<String>,
    pub missing: Vec<String>,
}

impl SignIn {
    /// One plain line for the Connect screen.
    pub fn summary(&self) -> String {
        if !self.signed_in {
            return "Not signed in to Cloudflare.".to_owned();
        }
        let who = self
            .email
            .clone()
            .unwrap_or_else(|| "your Cloudflare account".to_owned());
        let account = if self.accounts.is_empty() {
            String::new()
        } else {
            format!(" ({})", self.accounts.join(", "))
        };
        if self.missing.is_empty() {
            format!(
                "Signed in as {who}{account}. Access granted: {}.",
                self.scopes.join(", ")
            )
        } else {
            format!(
                "Signed in as {who}{account}, but access is missing for: {}. Sign in again and click Allow on every item.",
                self.missing.join(", ")
            )
        }
    }
}

/// Reads `wrangler whoami` output into names only. Account ids and tokens are never kept.
pub fn parse_whoami(text: &str) -> SignIn {
    let signed_in = text.contains("You are logged in") && !text.contains("not authenticated");
    let email = text
        .split("associated with the email ")
        .nth(1)
        .and_then(|rest| rest.split_whitespace().next())
        .map(|e| e.trim_end_matches('.').to_owned());
    let mut accounts = Vec::new();
    for line in text.lines() {
        let cells: Vec<&str> = line.split('│').map(str::trim).collect();
        if cells.len() >= 3
            && cells[2].len() == 32
            && cells[2].chars().all(|c| c.is_ascii_hexdigit())
            && !cells[1].is_empty()
        {
            accounts.push(cells[1].to_owned());
        }
    }
    let scopes: Vec<String> = text
        .lines()
        .filter_map(|l| l.strip_prefix("- "))
        .filter_map(|l| l.split_whitespace().next())
        .map(str::to_owned)
        .collect();
    let missing = REQUIRED_SCOPES
        .iter()
        .filter(|s| {
            !scopes
                .iter()
                .any(|have| have == s.split(':').next().unwrap_or(""))
        })
        .map(|s| (*s).to_owned())
        .collect();
    SignIn {
        signed_in,
        email,
        accounts,
        scopes,
        missing,
    }
}

/// How Wrangler will be run, and how it was found.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Located {
    pub cmd: Vec<String>,
    pub how: Found,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Found {
    /// `OC_WRANGLER` named it.
    Env,
    /// A `wrangler` program on the PATH.
    OnPath(String),
    /// Nothing installed: `npx` downloads it first, which can take 10 to 30 seconds.
    Npx,
}

impl Located {
    /// One plain line saying which Wrangler this is, for the progress text.
    pub fn describe(&self, version: Option<&str>) -> String {
        match &self.how {
            Found::Env => "Using the Wrangler set in OC_WRANGLER.".to_owned(),
            Found::OnPath(_) => match version {
                Some(v) => format!("Using wrangler {v} on this computer."),
                None => "Using wrangler on this computer.".to_owned(),
            },
            Found::Npx => {
                "Downloading Wrangler with npx… the first time takes up to 30 seconds.".to_owned()
            }
        }
    }
}

/// Finds Wrangler: `OC_WRANGLER`, then a `wrangler` on the PATH (the entries are checked here, no `which` is spawned), then
/// `npx --yes wrangler@4`. `exists` says whether a path is a file, so tests can supply a fake PATH.
pub fn locate_wrangler(
    env_override: Option<&str>,
    path: Option<&std::ffi::OsStr>,
    exists: &dyn Fn(&std::path::Path) -> bool,
) -> Located {
    locate_wrangler_with(env_override, path, &[], exists)
}

/// Directories to check after the PATH: an app started from a desktop menu has a minimal PATH, but Node and npm tools
/// usually live in the mise shims or `~/.local/bin`.
pub fn extra_dirs(home: Option<&std::path::Path>) -> Vec<std::path::PathBuf> {
    home.map(|h| vec![h.join(".local/share/mise/shims"), h.join(".local/bin")])
        .unwrap_or_default()
}

/// The first executable called `name` in the PATH entries, then in `extras`.
pub fn find_program(
    name: &str,
    path: Option<&std::ffi::OsStr>,
    extras: &[std::path::PathBuf],
    exists: &dyn Fn(&std::path::Path) -> bool,
) -> Option<std::path::PathBuf> {
    let names: Vec<String> = if cfg!(windows) {
        vec![
            format!("{name}.cmd"),
            format!("{name}.exe"),
            format!("{name}.bat"),
        ]
    } else {
        vec![name.to_owned()]
    };
    let dirs: Vec<std::path::PathBuf> = path
        .map(|p| std::env::split_paths(p).collect())
        .unwrap_or_default();
    dirs.iter()
        .chain(extras.iter())
        .find_map(|d| names.iter().map(|n| d.join(n)).find(|c| exists(c)))
}

/// Like [`locate_wrangler`], also looking in `extras` after the PATH.
pub fn locate_wrangler_with(
    env_override: Option<&str>,
    path: Option<&std::ffi::OsStr>,
    extras: &[std::path::PathBuf],
    exists: &dyn Fn(&std::path::Path) -> bool,
) -> Located {
    if let Some(c) = env_override.map(str::trim).filter(|c| !c.is_empty()) {
        return Located {
            cmd: c.split_whitespace().map(str::to_owned).collect(),
            how: Found::Env,
        };
    }
    if let Some(found) = find_program("wrangler", path, extras, exists) {
        let p = found.to_string_lossy().into_owned();
        return Located {
            cmd: vec![p.clone()],
            how: Found::OnPath(p),
        };
    }
    let npx = find_program("npx", path, extras, exists)
        .map_or_else(|| "npx".to_owned(), |p| p.to_string_lossy().into_owned());
    Located {
        cmd: vec![npx, "--yes".to_owned(), "wrangler@4".to_owned()],
        how: Found::Npx,
    }
}

fn is_executable_file(p: &std::path::Path) -> bool {
    let Ok(m) = std::fs::metadata(p) else {
        return false;
    };
    if !m.is_file() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        m.permissions().mode() & 0o111 != 0
    }
    #[cfg(not(unix))]
    {
        true
    }
}

/// Where this computer's Wrangler is, using the real environment.
pub fn locate_wrangler_here() -> Located {
    locate_wrangler_with(
        std::env::var("OC_WRANGLER").ok().as_deref(),
        std::env::var_os("PATH").as_deref(),
        &extra_dirs(home_dir().as_deref()),
        &is_executable_file,
    )
}

pub fn home_dir() -> Option<std::path::PathBuf> {
    std::env::var_os("HOME").map(std::path::PathBuf::from)
}

/// The PATH children are started with: ours, plus the extra directories, so a program found in the mise shims can find
/// `node` there too even when the app was started from a desktop menu.
pub fn child_path() -> std::ffi::OsString {
    let mut dirs: Vec<std::path::PathBuf> = std::env::var_os("PATH")
        .map(|p| std::env::split_paths(&p).collect())
        .unwrap_or_default();
    for e in extra_dirs(home_dir().as_deref()) {
        if !dirs.contains(&e) {
            dirs.push(e);
        }
    }
    std::env::join_paths(dirs).unwrap_or_default()
}

/// The command that runs Wrangler: `OC_WRANGLER`, else `wrangler` on the PATH, else `npx --yes wrangler@4`.
pub fn wrangler_command() -> Vec<String> {
    locate_wrangler_here().cmd
}

/// The text without terminal colour codes.
pub fn strip_ansi_text(s: &str) -> String {
    strip_ansi(s)
}

fn strip_ansi(s: &str) -> String {
    let mut out = String::new();
    let mut chars = s.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '\u{1b}' {
            // Skip `ESC [ ... letter`.
            if chars.peek() == Some(&'[') {
                for n in chars.by_ref() {
                    if n.is_ascii_alphabetic() {
                        break;
                    }
                }
            }
        } else {
            out.push(c);
        }
    }
    out
}

/// Turns one line of Wrangler's login output into a short progress message, or none for noise.
pub fn progress_line(raw: &str) -> Option<String> {
    let line = strip_ansi(raw).trim().to_owned();
    let lower = line.to_lowercase();
    if lower.contains("opening a link") || lower.contains("attempting to login") {
        Some("Opening Cloudflare's sign-in page in your browser. Click Allow there.".to_owned())
    } else if lower.contains("waiting for") {
        Some("Waiting for you to click Allow in the browser.".to_owned())
    } else if lower.contains("successfully logged in") {
        Some("Cloudflare accepted the sign-in.".to_owned())
    } else if lower.contains("error") || lower.contains("denied") || lower.contains("timed out") {
        Some("Cloudflare reported a problem with the sign-in.".to_owned())
    } else {
        None
    }
}

/// What a spawn failure means in words the person can act on.
pub fn spawn_failure_words(program: &str, e: &std::io::Error) -> String {
    let name = std::path::Path::new(program)
        .file_name()
        .map_or_else(|| program.to_owned(), |n| n.to_string_lossy().into_owned());
    if e.kind() == std::io::ErrorKind::NotFound {
        if name.starts_with("npx") || name.starts_with("node") {
            return "Node.js / npx was not found on this computer. Install Node.js from https://nodejs.org, then try again.".to_owned();
        }
        return format!(
            "{name} was not found on this computer. Install Wrangler (npm i -g wrangler) or Node.js, then try again."
        );
    }
    if e.kind() == std::io::ErrorKind::PermissionDenied {
        return format!("{name} could not be started: permission denied.");
    }
    format!("{name} could not be started ({}).", e.kind())
}

/// How long to wait for Wrangler's first line before saying it is still working.
pub const SILENCE: std::time::Duration = std::time::Duration::from_secs(60);
pub const STILL_WAITING: &str =
    "Still waiting for Wrangler… the first run can take a minute while it downloads.";

/// Runs a command, handing each output line (stdout and stderr) to `on_line`. If nothing has been printed for `silence`,
/// `on_silence` is called once (and again after each further `silence` without a line). Returns whether it exited successfully.
fn run_lines_ex(
    cmd: &[String],
    extra: &[String],
    silence: std::time::Duration,
    on_line: &mut dyn FnMut(&str),
    on_silence: &mut dyn FnMut(),
) -> Result<bool, String> {
    run_lines_env(cmd, extra, &[], silence, on_line, on_silence)
}

/// [`run_lines_ex`] with extra environment variables for the child only (for example a key setup needs).
pub fn run_lines_env(
    cmd: &[String],
    extra: &[String],
    envs: &[(String, String)],
    silence: std::time::Duration,
    on_line: &mut dyn FnMut(&str),
    on_silence: &mut dyn FnMut(),
) -> Result<bool, String> {
    let (program, rest) = cmd.split_first().ok_or("Wrangler was not found.")?;
    let mut child = Command::new(program)
        .args(rest)
        .args(extra)
        .env("PATH", child_path())
        .envs(envs.iter().map(|(k, v)| (k, v)))
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| spawn_failure_words(program, &e))?;
    let (tx, rx) = std::sync::mpsc::channel::<String>();
    let mut readers = Vec::new();
    if let Some(o) = child.stdout.take() {
        let tx = tx.clone();
        readers.push(std::thread::spawn(move || {
            for l in std::io::BufReader::new(o).lines().map_while(Result::ok) {
                let _ = tx.send(l);
            }
        }));
    }
    if let Some(e) = child.stderr.take() {
        let tx = tx.clone();
        readers.push(std::thread::spawn(move || {
            for l in std::io::BufReader::new(e).lines().map_while(Result::ok) {
                let _ = tx.send(l);
            }
        }));
    }
    drop(tx);
    loop {
        match rx.recv_timeout(silence) {
            Ok(l) => on_line(&l),
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => on_silence(),
            Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => break,
        }
    }
    for r in readers {
        let _ = r.join();
    }
    Ok(child.wait().map(|s| s.success()).unwrap_or(false))
}

pub fn run_lines(
    cmd: &[String],
    extra: &[String],
    on_line: &mut dyn FnMut(&str),
) -> Result<bool, String> {
    run_lines_ex(cmd, extra, SILENCE, on_line, &mut || {})
}

pub fn whoami_with(cmd: &[String]) -> Result<SignIn, String> {
    let mut text = String::new();
    run_lines(cmd, &["whoami".to_owned()], &mut |l| {
        text.push_str(l);
        text.push('\n');
    })?;
    Ok(parse_whoami(&text))
}

/// One progress update for the screen: the words, and a Cloudflare page the person can open by hand when Wrangler could
/// not open the browser itself.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Progress {
    pub text: String,
    pub open_url: Option<String>,
}

impl Progress {
    pub fn say(text: impl Into<String>) -> Progress {
        Progress {
            text: text.into(),
            open_url: None,
        }
    }
}

/// The first https address on a line, without trailing punctuation.
pub fn extract_url(line: &str) -> Option<String> {
    let start = line.find("https://")?;
    let url: String = line[start..]
        .chars()
        .take_while(|c| !c.is_whitespace() && !matches!(c, '"' | '\'' | '<' | '>'))
        .collect();
    let url = url.trim_end_matches(['.', ',', ')', ']']).to_owned();
    (url.len() > "https://".len()).then_some(url)
}

/// Only Cloudflare's own pages are offered as a button, never an arbitrary address from program output.
pub fn is_cloudflare_url(url: &str) -> bool {
    let Some(rest) = url.strip_prefix("https://") else {
        return false;
    };
    let host = rest.split(['/', '?', '#']).next().unwrap_or("");
    host == "cloudflare.com" || host.ends_with(".cloudflare.com")
}

fn browser_did_not_open(lower: &str) -> bool {
    [
        "failed to open",
        "could not open",
        "couldn't open",
        "unable to open",
        "no browser",
        "cannot open",
        "visit the following",
        "open the following",
        "please visit",
    ]
    .iter()
    .any(|p| lower.contains(p))
}

/// Opens the browser sign-in, reports progress in plain words, and reads who is signed in afterwards. Blocking: run on a thread.
/// The first update is sent before anything starts, so the screen is never silent.
pub fn sign_in_with_timing(
    cmd: &[String],
    silence: std::time::Duration,
    on_progress: &mut dyn FnMut(Progress),
) -> Result<SignIn, String> {
    on_progress(Progress::say("Starting Wrangler…"));
    let mut last = String::new();
    let mut link: Option<String> = None;
    let ok = {
        let on_progress = std::cell::RefCell::new(&mut *on_progress);
        run_lines_ex(
            cmd,
            &sign_in_args(),
            silence,
            &mut |l| {
                let clean = strip_ansi(l);
                let lower = clean.to_lowercase();
                // Keep the address Wrangler printed: it is the fallback when the browser cannot be opened.
                if let Some(u) = extract_url(&clean)
                    && is_cloudflare_url(&u)
                {
                    link = Some(u);
                }
                let p = if browser_did_not_open(&lower) {
                    link.clone().map(|u| Progress {
                        text: "Your browser did not open. Use the button to open the sign-in page."
                            .to_owned(),
                        open_url: Some(u),
                    })
                } else {
                    progress_line(l).map(Progress::say)
                };
                if let Some(p) = p
                    && p.text != last
                {
                    last = p.text.clone();
                    (on_progress.borrow_mut())(p);
                }
            },
            &mut || (on_progress.borrow_mut())(Progress::say(STILL_WAITING)),
        )?
    };
    if !ok {
        return Err(
            "The sign-in did not finish. Try again and click Allow in the browser.".to_owned(),
        );
    }
    whoami_with(cmd)
}

pub fn sign_in_with(
    cmd: &[String],
    on_progress: &mut dyn FnMut(Progress),
) -> Result<SignIn, String> {
    sign_in_with_timing(cmd, SILENCE, on_progress)
}

/// Finds Wrangler, says which one it is, then signs in.
pub fn sign_in(on_progress: &mut dyn FnMut(Progress)) -> Result<SignIn, String> {
    on_progress(Progress::say("Starting Wrangler…"));
    let found = locate_wrangler_here();
    let version = match found.how {
        Found::OnPath(_) => version_of(&found.cmd),
        _ => None,
    };
    on_progress(Progress::say(found.describe(version.as_deref())));
    sign_in_with(&found.cmd, on_progress)
}

/// `wrangler --version`, first line, for the "Using wrangler 4.x" message. None when it cannot be read.
fn version_of(cmd: &[String]) -> Option<String> {
    let mut out = String::new();
    run_lines(cmd, &["--version".to_owned()], &mut |l| {
        if out.is_empty() {
            out = strip_ansi(l).trim().to_owned();
        }
    })
    .ok()?;
    out.split_whitespace()
        .find(|w| w.chars().next().is_some_and(|c| c.is_ascii_digit()))
        .map(str::to_owned)
}

pub fn who_am_i() -> Result<SignIn, String> {
    whoami_with(&wrangler_command())
}

/// Signs out: Wrangler revokes and forgets the session. Returns the state afterwards (not signed in).
pub fn sign_out_with(cmd: &[String]) -> Result<SignIn, String> {
    // The real wrangler exits non-zero from `logout` in some environments when nobody is signed in, so the goal decides:
    // success is being signed out afterwards, whatever the exit code said.
    let _ = run_lines(cmd, &["logout".to_owned()], &mut |_| {})?;
    let after = whoami_with(cmd)?;
    if after.signed_in {
        return Err("Signing out did not finish. Try again.".to_owned());
    }
    Ok(after)
}

pub fn sign_out() -> Result<SignIn, String> {
    sign_out_with(&wrangler_command())
}

/// Switch account: sign out, then sign in again so Cloudflare asks which account to approve.
pub fn switch_account_with(
    cmd: &[String],
    on_progress: &mut dyn FnMut(Progress),
) -> Result<SignIn, String> {
    sign_out_with(cmd)?;
    sign_in_with(cmd, on_progress)
}

pub fn switch_account(on_progress: &mut dyn FnMut(Progress)) -> Result<SignIn, String> {
    on_progress(Progress::say("Starting Wrangler…"));
    let found = locate_wrangler_here();
    on_progress(Progress::say(found.describe(None)));
    switch_account_with(&found.cmd, on_progress)
}

/// Step 1 of the setup chain, from the sign-in state: pass or fail, the words, and the fix when it fails.
pub fn step1(s: &SignIn) -> (bool, String, Option<String>) {
    if !s.signed_in {
        return (
            false,
            "You are not signed in to Cloudflare.".to_owned(),
            Some("Click Sign in with Cloudflare, then Allow in the browser.".to_owned()),
        );
    }
    if !s.missing.is_empty() {
        return (
            false,
            format!(
                "The sign-in is missing access for: {}.",
                s.missing.join(", ")
            ),
            Some("Sign in again and click Allow on every item.".to_owned()),
        );
    }
    (true, "Signed in with Cloudflare.".to_owned(), None)
}

// ---------- the two dashboard steps OAuth cannot do ----------

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct DashboardStep {
    pub title: &'static str,
    pub url: &'static str,
    pub how: &'static str,
}

/// Deep links use Cloudflare's `?to=/:account/...` redirect, which picks the signed-in account.
pub const AI_GATEWAY_STEP: DashboardStep = DashboardStep {
    title: "AI Gateway",
    url: "https://dash.cloudflare.com/?to=/:account/ai/ai-gateway",
    how: "Create gateway, name it open-computer, then run `OC_AI_GATEWAY_ID=open-computer oc setup` so the core uses it.",
};

pub const ACCESS_STEP: DashboardStep = DashboardStep {
    title: "Cloudflare Access",
    url: "https://one.dash.cloudflare.com/?to=/:account/access/apps",
    how: "Add a self-hosted application for your core's address and allow only your email.",
};

/// The core reports whether it routes through a gateway in `/v1/diag/bindings` (`gateway: true`).
pub fn gateway_configured(bindings: &serde_json::Value) -> bool {
    bindings
        .get("gateway")
        .and_then(serde_json::Value::as_bool)
        .unwrap_or(false)
}

/// Why a configured gateway is not carrying requests (for example "AI Gateway refused the request (needs authentication)"),
/// when the core reports one. The core retries such requests directly, so the app still works.
pub fn gateway_reason(bindings: &serde_json::Value) -> Option<String> {
    bindings
        .get("gateway_reason")
        .and_then(serde_json::Value::as_str)
        .map(str::to_owned)
}

/// What a request with no credentials to the core's address shows about Access.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum AccessState {
    Protected,
    NotProtected,
    Unknown,
}

pub fn classify_access(status: u16, location: Option<&str>, body: &str) -> AccessState {
    let at_access = location.is_some_and(|l| l.contains("cloudflareaccess.com"))
        || body.contains("cloudflareaccess.com");
    match status {
        301 | 302 | 303 | 307 | 308 if at_access => AccessState::Protected,
        401 | 403 if at_access => AccessState::Protected,
        0 => AccessState::Unknown,
        _ => AccessState::NotProtected,
    }
}

/// Probes `url` once with no credentials and no redirect following.
pub fn detect_access(url: &str) -> AccessState {
    let agent: ureq::Agent = ureq::Agent::config_builder()
        .timeout_global(Some(std::time::Duration::from_secs(10)))
        .max_redirects(0)
        .http_status_as_error(false)
        .tls_config(
            ureq::tls::TlsConfig::builder()
                .provider(ureq::tls::TlsProvider::NativeTls)
                .build(),
        )
        .build()
        .into();
    match agent.get(url).call() {
        Ok(mut r) => {
            let loc = r
                .headers()
                .get("location")
                .and_then(|v| v.to_str().ok())
                .map(str::to_owned);
            let body = r.body_mut().read_to_string().unwrap_or_default();
            classify_access(r.status().as_u16(), loc.as_deref(), &body)
        }
        Err(_) => AccessState::Unknown,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // Built at runtime so the source never holds a 32-hex literal (the open-source hygiene test reads any
    // such string as a Cloudflare account id).
    fn fake_account_id() -> String {
        "0123456789abcdef".repeat(2)
    }

    fn signed_in(extra_missing: &[&str]) -> String {
        let id = fake_account_id();
        let mut s = format!(
            "👋 You are logged in with an OAuth Token, associated with the email someone@example.com.\n│ Account Name │ Account ID │\n│ Someone's Account │ {id} │\nScope (Access)\n"
        );
        for name in [
            "user",
            "account",
            "workers",
            "workers_scripts",
            "workers_routes",
            "workers_tail",
            "d1",
            "secrets_store",
            "containers",
            "cloudchamber",
        ] {
            if !extra_missing.contains(&name) {
                s.push_str(&format!("- {name} (write)\n"));
            }
        }
        s
    }

    #[test]
    fn the_sign_in_is_a_browser_login_with_the_needed_scopes_and_no_token() {
        let a = sign_in_args();
        assert_eq!(&a[..2], ["login", "--scopes"]);
        assert_eq!(a.len(), 2 + REQUIRED_SCOPES.len());
        assert!(!a.join(" ").to_lowercase().contains("api-token"));
    }

    #[test]
    fn who_is_signed_in_is_read_as_names_only() {
        let s = parse_whoami(&signed_in(&[]));
        assert!(s.signed_in && s.missing.is_empty());
        assert_eq!(s.email.as_deref(), Some("someone@example.com"));
        assert_eq!(s.accounts, ["Someone's Account"]);
        assert!(!format!("{s:?}").contains(&fake_account_id()));
        assert!(
            s.summary()
                .starts_with("Signed in as someone@example.com (Someone's Account).")
        );
    }

    #[test]
    fn a_partial_sign_in_names_what_is_missing_and_not_signed_in_says_so() {
        let s = parse_whoami(&signed_in(&["d1", "containers"]));
        assert_eq!(s.missing, ["d1:write", "containers:write"]);
        assert!(
            s.summary()
                .contains("missing for: d1:write, containers:write")
        );
        assert_eq!(
            parse_whoami("You are not authenticated.").summary(),
            "Not signed in to Cloudflare."
        );
    }

    /// Writes a tiny fake `wrangler` shell script: prints canned login output, whoami text, or succeeds on logout.
    fn fake_wrangler(dir: &std::path::Path, whoami: &str, login_ok: bool) -> Vec<String> {
        let path = dir.join("wrangler");
        // Stateful like the real thing: a login signs in, a logout signs out, whoami reports the current state.
        let state = dir.join("signed-in");
        let _ = std::fs::remove_file(&state);
        let script = format!(
            "#!/bin/sh\nS='{}'\ncase \"$1\" in\n login) echo 'Attempting to login via OAuth...'; echo 'Opening a link in your default browser: https://dash.cloudflare.com/oauth2/auth?x=1'; echo 'Waiting for authorization...'; {} exit {};;\n logout) rm -f \"$S\"; exit 0;;\n whoami) if [ -f \"$S\" ]; then cat <<'EOF'\n{}\nEOF\nelse echo 'You are not authenticated.'; fi;;\nesac\n",
            state.display(),
            if login_ok { "touch \"$S\";" } else { "" },
            if login_ok { 0 } else { 1 },
            whoami
        );
        std::fs::write(&path, script).unwrap();
        // Run through `sh` so a freshly written file is never exec'd directly (avoids "text file busy" under parallel tests).
        vec!["sh".to_owned(), path.to_string_lossy().into_owned()]
    }

    fn temp(name: &str) -> std::path::PathBuf {
        let d = std::env::temp_dir().join(format!("oc-signin-{name}-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn login_progress_is_plain_words_and_never_the_raw_link() {
        let dir = temp("progress");
        let cmd = fake_wrangler(&dir, &signed_in(&[]), true);
        let mut seen = Vec::new();
        let s = sign_in_with(&cmd, &mut |p| seen.push(p)).unwrap();
        assert!(s.signed_in && s.missing.is_empty());
        let words: Vec<&str> = seen.iter().map(|p| p.text.as_str()).collect();
        assert_eq!(words.len(), 3, "{words:?}");
        assert_eq!(words[0], "Starting Wrangler…");
        assert!(words[1].contains("Click Allow") && words[2].contains("Waiting"));
        assert!(words.iter().all(|t| !t.contains("https://")));
        // The page Wrangler opened itself is not offered as a button.
        assert!(seen.iter().all(|p| p.open_url.is_none()));
    }

    fn script(body: &str) -> Vec<String> {
        ["sh", "-c", body, "x"].map(String::from).to_vec()
    }

    #[test]
    fn the_first_message_is_sent_before_anything_is_spawned_and_a_spawn_failure_says_why() {
        let mut seen = Vec::new();
        let err = sign_in_with(&["npx-missing-for-test".to_owned()], &mut |p| {
            seen.push(p.text)
        })
        .unwrap_err();
        assert_eq!(
            seen,
            ["Starting Wrangler…"],
            "the message came before the failure"
        );
        assert!(
            err.contains("Node.js / npx was not found") && err.contains("nodejs.org"),
            "{err}"
        );
        let err = sign_in_with(&["wrangler-missing-for-test".to_owned()], &mut |_| {}).unwrap_err();
        assert!(
            err.contains("wrangler-missing-for-test was not found"),
            "{err}"
        );
        let denied = std::io::Error::from(std::io::ErrorKind::PermissionDenied);
        assert!(spawn_failure_words("/x/wrangler", &denied).contains("permission denied"));
    }

    #[test]
    fn wrangler_is_found_in_order_env_then_path_then_npx() {
        let on = |files: &'static [&'static str]| {
            move |p: &std::path::Path| files.contains(&p.to_string_lossy().as_ref())
        };
        let path = std::ffi::OsString::from("/first/bin:/second/bin");
        // OC_WRANGLER wins over everything.
        let env = locate_wrangler(
            Some("pnpm exec wrangler"),
            Some(&path),
            &on(&["/first/bin/wrangler"]),
        );
        assert_eq!(
            (env.cmd.as_slice(), &env.how),
            (
                ["pnpm", "exec", "wrangler"].map(String::from).as_slice(),
                &Found::Env
            )
        );
        // A blank OC_WRANGLER is ignored.
        assert!(matches!(
            locate_wrangler(Some("  "), Some(&path), &on(&[])).how,
            Found::Npx
        ));
        // The first PATH entry that has it wins, and no program is spawned to find it.
        let both = locate_wrangler(
            None,
            Some(&path),
            &on(&["/second/bin/wrangler", "/first/bin/wrangler"]),
        );
        assert_eq!(both.cmd, ["/first/bin/wrangler"]);
        let second = locate_wrangler(None, Some(&path), &on(&["/second/bin/wrangler"]));
        assert_eq!(second.how, Found::OnPath("/second/bin/wrangler".to_owned()));
        // Nothing installed: npx downloads it.
        let npx = locate_wrangler(None, Some(&path), &on(&[]));
        assert_eq!(npx.cmd, ["npx", "--yes", "wrangler@4"]);
        assert!(locate_wrangler(None, None, &on(&[])).how == Found::Npx);
        // What the screen is told.
        assert_eq!(
            second.describe(Some("4.9.0")),
            "Using wrangler 4.9.0 on this computer."
        );
        assert_eq!(second.describe(None), "Using wrangler on this computer.");
        assert!(
            npx.describe(None)
                .starts_with("Downloading Wrangler with npx")
        );
        assert!(env.describe(None).contains("OC_WRANGLER"));
    }

    #[test]
    fn a_wrangler_that_stays_quiet_gets_a_still_waiting_message_before_its_first_line() {
        let cmd = script(
            "case \"$1\" in login) sleep 1; echo 'Attempting to login via OAuth...';; whoami) echo 'You are not authenticated.';; esac",
        );
        let mut seen = Vec::new();
        let _ = sign_in_with_timing(&cmd, std::time::Duration::from_millis(250), &mut |p| {
            seen.push(p.text)
        });
        let words: Vec<&str> = seen.iter().map(String::as_str).collect();
        assert_eq!(words[0], "Starting Wrangler…");
        assert!(words.contains(&STILL_WAITING), "{words:?}");
        let quiet = words.iter().position(|w| *w == STILL_WAITING).unwrap();
        let first_line = words
            .iter()
            .position(|w| w.contains("Click Allow"))
            .unwrap();
        assert!(quiet < first_line, "{words:?}");
    }

    #[test]
    fn if_wrangler_cannot_open_the_browser_the_page_it_printed_is_offered_as_a_button() {
        let cmd = script(
            "case \"$1\" in login) echo 'Opening a link in your default browser: https://dash.cloudflare.com/oauth2/auth?client_id=x&state=y'; echo 'Failed to open'; exit 1;; whoami) echo 'You are not authenticated.';; esac",
        );
        let mut seen = Vec::new();
        let _ = sign_in_with(&cmd, &mut |p| seen.push(p));
        // The "Opening a link..." line is not swallowed.
        assert!(
            seen.iter().any(|p| p.text.contains("Click Allow")),
            "{seen:?}"
        );
        let fallback = seen
            .iter()
            .find(|p| p.open_url.is_some())
            .expect("a fallback");
        assert_eq!(
            fallback.open_url.as_deref(),
            Some("https://dash.cloudflare.com/oauth2/auth?client_id=x&state=y")
        );
        assert!(fallback.text.contains("Open") || fallback.text.contains("button"));
        // An address that is not Cloudflare's is never offered.
        let evil = script(
            "case \"$1\" in login) echo 'Opening a link in your default browser: https://evil.example/phish'; echo 'Failed to open'; exit 1;; esac",
        );
        let mut seen = Vec::new();
        let _ = sign_in_with(&evil, &mut |p| seen.push(p));
        assert!(seen.iter().all(|p| p.open_url.is_none()), "{seen:?}");
    }

    #[test]
    fn urls_are_read_from_a_line_and_only_cloudflare_pages_count() {
        assert_eq!(
            extract_url("visit https://dash.cloudflare.com/a?b=1, then wait.").as_deref(),
            Some("https://dash.cloudflare.com/a?b=1")
        );
        assert_eq!(extract_url("no link here"), None);
        assert!(is_cloudflare_url(
            "https://dash.cloudflare.com/oauth2/auth?x=1"
        ));
        assert!(is_cloudflare_url("https://cloudflare.com/x"));
        assert!(!is_cloudflare_url("https://cloudflare.com.evil.example/x"));
        assert!(!is_cloudflare_url("http://dash.cloudflare.com/x"));
    }

    #[test]
    fn a_login_that_fails_says_so_and_a_missing_wrangler_says_so() {
        let dir = temp("fail");
        let cmd = fake_wrangler(&dir, "", false);
        assert!(
            sign_in_with(&cmd, &mut |_| {})
                .unwrap_err()
                .contains("did not finish")
        );
        let none = vec!["definitely-not-wrangler-xyz".to_owned()];
        assert!(
            sign_in_with(&none, &mut |_| {})
                .unwrap_err()
                .contains("not found")
        );
    }

    #[test]
    fn sign_out_and_switch_account_run_logout_then_login() {
        let dir = temp("switch");
        let cmd = fake_wrangler(&dir, "You are not authenticated.", true);
        assert!(!sign_out_with(&cmd).unwrap().signed_in);
        let cmd = fake_wrangler(&dir, &signed_in(&[]), true);
        let mut seen = Vec::new();
        assert!(
            switch_account_with(&cmd, &mut |p| seen.push(p))
                .unwrap()
                .signed_in
        );
        assert!(!seen.is_empty());
    }

    #[test]
    fn sign_out_succeeds_when_logout_exits_non_zero_but_nobody_is_signed_in_and_fails_if_still_signed_in()
     {
        let out = [
            "sh",
            "-c",
            "case $1 in logout) exit 1;; whoami) echo 'You are not authenticated.';; esac",
            "x",
        ]
        .map(String::from)
        .to_vec();
        assert!(!sign_out_with(&out).unwrap().signed_in);
        let still = ["sh", "-c", "case $1 in logout) exit 1;; whoami) echo 'You are logged in with an OAuth Token, associated with the email a@b.c.';; esac", "x"].map(String::from).to_vec();
        assert!(
            sign_out_with(&still)
                .unwrap_err()
                .contains("did not finish")
        );
    }

    #[test]
    fn step_one_is_rerun_from_the_state_and_names_what_to_do() {
        assert!(step1(&parse_whoami(&signed_in(&[]))).0);
        let (ok, words, fix) = step1(&parse_whoami(&signed_in(&["d1"])));
        assert!(!ok && words.contains("d1:write") && fix.unwrap().contains("Allow"));
        let (ok, _, fix) = step1(&parse_whoami("You are not authenticated."));
        assert!(!ok && fix.unwrap().contains("Sign in with Cloudflare"));
    }

    #[test]
    fn dashboard_links_and_detection() {
        assert!(
            AI_GATEWAY_STEP.url.contains("/:account/ai/ai-gateway")
                && ACCESS_STEP.url.contains("/:account/access/apps")
        );
        assert!(gateway_configured(&serde_json::json!({"gateway": true})));
        assert!(!gateway_configured(&serde_json::json!({"ok": true})));
        assert_eq!(
            gateway_reason(&serde_json::json!({"gateway": false, "gateway_reason": "AI Gateway refused the request (needs authentication)."})).as_deref(),
            Some("AI Gateway refused the request (needs authentication).")
        );
        assert_eq!(gateway_reason(&serde_json::json!({"gateway": true})), None);
        assert_eq!(
            classify_access(
                302,
                Some("https://team.cloudflareaccess.com/cdn-cgi/access/login/x"),
                ""
            ),
            AccessState::Protected
        );
        assert_eq!(
            classify_access(200, None, "hello"),
            AccessState::NotProtected
        );
        assert_eq!(classify_access(401, None, "{}"), AccessState::NotProtected);
        assert_eq!(classify_access(0, None, ""), AccessState::Unknown);
    }

    #[test]
    fn ansi_noise_is_stripped_from_progress() {
        assert_eq!(
            progress_line("\u{1b}[32mSuccessfully logged in.\u{1b}[0m").as_deref(),
            Some("Cloudflare accepted the sign-in.")
        );
        assert_eq!(progress_line("random chatter"), None);
    }
}
