//! Zero-typing onboarding: find the person's core without asking for an address or a token, and set one up when none
//! exists.
//!
//! 1. `oc setup` saves its connection under `~/.config/open-computer/`. [`read_saved_core`] reads it inside this process,
//!    [`adopt_saved_core`] puts the token in the OS keyring, and nothing is ever shown. The file stays because the `oc`
//!    command line uses it too.
//! 2. Otherwise, with Wrangler signed in, [`discover_core`] looks for the `open-computer-core` Worker on the account and
//!    builds its workers.dev address. It asks Cloudflare through `wrangler auth token`, held in memory only.
//! 3. With no core, [`run_setup`] runs the same setup chain as `oc setup` and streams its rows.
//!
//! Everything that talks to the outside goes through a trait or a command argument, so tests use fakes.

use std::fmt;
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde_json::Value;

use crate::secrets::{SecretStore, Slot};
use crate::signin;

// ---------- 1. the connection `oc setup` saved ----------

/// The address and access token `oc setup` saved. The token is never printed, not even by `{:?}`.
pub struct SavedCore {
    pub url: String,
    token: String,
}

impl fmt::Debug for SavedCore {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "SavedCore({}, token hidden)", self.url)
    }
}

pub fn default_state_path() -> PathBuf {
    let base = std::env::var_os("XDG_CONFIG_HOME")
        .map(PathBuf::from)
        .or_else(|| signin::home_dir().map(|h| h.join(".config")))
        .unwrap_or_else(|| PathBuf::from("."));
    base.join("open-computer").join("cloud.json")
}

/// What the saved connection file holds, without ever carrying the token: for logs, the self-test and the screen.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SavedInspect {
    pub exists: bool,
    pub url: Option<String>,
    pub token_present: bool,
    /// Why the file cannot be used, in words.
    pub problem: Option<String>,
}

pub fn inspect_saved_core(path: &Path) -> SavedInspect {
    let mut out = SavedInspect {
        exists: false,
        url: None,
        token_present: false,
        problem: None,
    };
    let text = match std::fs::read_to_string(path) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            out.problem = Some("no saved connection file".to_owned());
            return out;
        }
        Err(e) => {
            out.exists = true;
            out.problem = Some(format!(
                "the saved connection file cannot be read ({})",
                e.kind()
            ));
            return out;
        }
    };
    out.exists = true;
    let Ok(v) = serde_json::from_str::<Value>(&text) else {
        out.problem = Some("the saved connection file is not valid JSON".to_owned());
        return out;
    };
    out.url = v
        .get("url")
        .and_then(Value::as_str)
        .map(|u| u.trim().trim_end_matches('/').to_owned())
        .filter(|u| !u.is_empty());
    out.token_present = v
        .get("token")
        .and_then(Value::as_str)
        .is_some_and(|t| !t.trim().is_empty());
    out.problem = match (&out.url, out.token_present) {
        (None, _) => Some("the saved connection file has no address".to_owned()),
        (Some(u), _) if !(u.starts_with("https://") || u.starts_with("http://")) => {
            Some("the saved address is not http(s)".to_owned())
        }
        (Some(_), false) => Some("the saved connection file has no token".to_owned()),
        _ => None,
    };
    out
}

/// Reads `{ "url": ..., "token": ... }`. Returns none unless both are present and the address is http(s).
pub fn read_saved_core(path: &Path) -> Option<SavedCore> {
    let report = inspect_saved_core(path);
    match &report.problem {
        Some(p) => crate::log_info!("connect", "saved connection not usable: {p}"),
        None => crate::log_info!(
            "connect",
            "saved connection found: url {}, token present",
            report.url.as_deref().unwrap_or("")
        ),
    }
    if report.problem.is_some() {
        return None;
    }
    let text = std::fs::read_to_string(path).ok()?;
    let v: Value = serde_json::from_str(&text).ok()?;
    let url = v
        .get("url")?
        .as_str()?
        .trim()
        .trim_end_matches('/')
        .to_owned();
    let token = v.get("token")?.as_str()?.trim().to_owned();
    Some(SavedCore { url, token })
}

/// Moves the token into the secret store (the OS keyring) and returns the address. The token is not returned. A keyring that
/// refuses is an error here, with its reason, not a silent in-memory copy: the caller decides what to offer next.
pub fn adopt_saved_core(core: SavedCore, store: &dyn SecretStore) -> Result<String, String> {
    match store.set_persistent(Slot::CloudAccess, &core.token) {
        Ok(()) => {
            crate::log_info!(
                "connect",
                "adopted the saved token into the keyring for {}",
                core.url
            );
            Ok(core.url)
        }
        Err(e) => {
            crate::log_warn!("connect", "adopting the saved token failed: {e}");
            Err(e.0)
        }
    }
}

/// Whether only the owner can read the file (mode 0600 or tighter). False for a missing file, and everywhere but Unix.
pub fn file_is_private(path: &Path) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::metadata(path).is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o077 == 0)
    }
    #[cfg(not(unix))]
    {
        let _ = path;
        false
    }
}

/// A 401 with the token the keyring holds: does the saved connection file name the same core with a different token? Compares
/// without showing either token. False when the file is missing, unusable, for another core, or holds the same token.
pub fn saved_token_differs(path: &Path, url: &str, current_token: &str) -> bool {
    let norm = |u: &str| u.trim().trim_end_matches('/').to_ascii_lowercase();
    read_saved_core(path).is_some_and(|c| norm(&c.url) == norm(url) && c.token != current_token)
}

/// What to say when the core refuses the token and the saved file cannot replace it.
pub const REFUSED_SAVED: &str =
    "The core refused the saved token. Run setup or paste the token under Advanced.";

impl SavedCore {
    /// Takes the token for this session only (never the keyring): used when the person agrees to use the saved file instead.
    pub fn use_for_session(self, store: &dyn SecretStore) -> Result<String, String> {
        store
            .set_session(Slot::CloudAccess, &self.token)
            .map_err(|e| e.0)?;
        crate::log_info!(
            "connect",
            "using the saved token for this session for {}",
            self.url
        );
        Ok(self.url)
    }
}

// ---------- 2. finding the deployed core through Wrangler ----------

pub const CORE_NAME: &str = "open-computer-core";

/// Reads Cloudflare's API. A trait so tests do not reach the network.
pub trait CfApi {
    fn get(&self, path: &str) -> Result<(u16, Value), String>;
}

/// The real API, with the account's token held in memory only. `{:?}` shows nothing of it.
pub struct HttpCfApi {
    token: String,
}

impl fmt::Debug for HttpCfApi {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("HttpCfApi(token hidden)")
    }
}

impl HttpCfApi {
    pub fn new(token: String) -> HttpCfApi {
        HttpCfApi { token }
    }
}

impl CfApi for HttpCfApi {
    fn get(&self, path: &str) -> Result<(u16, Value), String> {
        let mut r = crate::http::agent(Some(Duration::from_secs(20)))
            .get(&format!("https://api.cloudflare.com/client/v4{path}"))
            .header("Authorization", &format!("Bearer {}", self.token))
            .call()
            .map_err(|e| crate::http::unreachable("Cloudflare", "GET", path, &e))?;
        let status = r.status().as_u16();
        crate::http::answered("Cloudflare", "GET", path, status);
        let text = r.body_mut().read_to_string().unwrap_or_default();
        Ok((status, serde_json::from_str(&text).unwrap_or(Value::Null)))
    }
}

/// The signed-in session's token from `wrangler auth token --json`, for this process only. None when nobody is signed in.
pub fn token_from_wrangler(cmd: &[String]) -> Result<Option<String>, String> {
    let mut out = String::new();
    let ok = signin::run_lines(
        cmd,
        &["auth".to_owned(), "token".to_owned(), "--json".to_owned()],
        &mut |l| {
            out.push_str(l);
            out.push('\n');
        },
    )?;
    // Nothing from the output is put in an error: it may contain the token.
    let start = out.find('{');
    let parsed: Option<Value> = start.and_then(|i| serde_json::from_str(out[i..].trim()).ok());
    match parsed {
        Some(v) => match v.get("token").and_then(Value::as_str).filter(|t| !t.is_empty()) {
            Some(t) => Ok(Some(t.to_owned())),
            None => Err("This kind of Cloudflare sign-in cannot be used to look up your core. Use Advanced to enter its address.".to_owned()),
        },
        None if !ok => Ok(None),
        None => Ok(None),
    }
}

/// A core found on the account. The account id is kept for later lookups and never shown or printed.
#[derive(Clone, PartialEq, Eq)]
pub struct FoundCore {
    pub name: String,
    pub account: String,
    pub url: String,
    /// When the Worker was last deployed (ISO time), when Cloudflare said.
    pub modified_on: Option<String>,
    account_id: String,
}

impl fmt::Debug for FoundCore {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "FoundCore({} at {})", self.name, self.url)
    }
}

impl FoundCore {
    pub fn new(
        name: impl Into<String>,
        account: impl Into<String>,
        url: impl Into<String>,
        account_id: impl Into<String>,
    ) -> FoundCore {
        FoundCore {
            name: name.into(),
            account: account.into(),
            url: url.into(),
            modified_on: None,
            account_id: account_id.into(),
        }
    }

    pub fn account_id(&self) -> &str {
        &self.account_id
    }
}

/// Looks for the `open-computer-core` Worker on each of the person's accounts and builds its workers.dev address.
pub fn discover_core(api: &dyn CfApi) -> Result<Option<FoundCore>, String> {
    let (status, accounts) = api.get("/accounts")?;
    match status {
        200 => {}
        401 | 403 => {
            return Err("Cloudflare would not list your account. Sign in again.".to_owned());
        }
        c => return Err(format!("Cloudflare answered with an error ({c}).")),
    }
    for a in accounts["result"].as_array().cloned().unwrap_or_default() {
        let (Some(id), name) = (
            a["id"].as_str(),
            a["name"].as_str().unwrap_or("your account"),
        ) else {
            continue;
        };
        let (status, service) = api.get(&format!("/accounts/{id}/workers/services/{CORE_NAME}"))?;
        if status != 200 {
            continue;
        }
        let modified_on = service["result"]["default_environment"]["script"]["modified_on"]
            .as_str()
            .or_else(|| service["result"]["modified_on"].as_str())
            .map(str::to_owned);
        let (status, sub) = api.get(&format!("/accounts/{id}/workers/subdomain"))?;
        let Some(subdomain) = sub["result"]["subdomain"]
            .as_str()
            .filter(|_| status == 200)
        else {
            return Err(
                "Your core exists, but its workers.dev address could not be read.".to_owned(),
            );
        };
        return Ok(Some(FoundCore {
            name: CORE_NAME.to_owned(),
            account: name.to_owned(),
            url: format!("https://{CORE_NAME}.{subdomain}.workers.dev"),
            modified_on,
            account_id: id.to_owned(),
        }));
    }
    Ok(None)
}

/// Whether an AI Gateway with this name exists on the account. None when the sign-in is not allowed to look (OAuth has no
/// AI Gateway scope), so the caller offers the dashboard link instead of guessing.
pub fn gateway_exists(api: &dyn CfApi, account_id: &str, name: &str) -> Option<bool> {
    match api.get(&format!(
        "/accounts/{account_id}/ai-gateway/gateways/{name}"
    )) {
        Ok((200, _)) => Some(true),
        Ok((404, _)) => Some(false),
        _ => None,
    }
}

// ---------- 3. one-click setup ----------

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DockerState {
    Running,
    NotRunning,
    Missing,
}

impl DockerState {
    /// The plain words for the person, or none when Docker is fine.
    pub fn problem(self) -> Option<&'static str> {
        match self {
            DockerState::Running => None,
            DockerState::NotRunning => {
                Some("Docker is not running. Start Docker, then click Set up again.")
            }
            DockerState::Missing => {
                Some("Docker was not found. Install Docker, start it, then click Set up again.")
            }
        }
    }
}

/// Asks `<docker> info` whether the Docker engine answers. Gives up after 15 seconds.
pub fn docker_state(program: &str) -> DockerState {
    let mut child = match std::process::Command::new(program)
        .args(["info", "--format", "{{.ServerVersion}}"])
        .env("PATH", signin::child_path())
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
    {
        Ok(c) => c,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return DockerState::Missing,
        Err(_) => return DockerState::NotRunning,
    };
    let end = std::time::Instant::now() + Duration::from_secs(15);
    loop {
        match child.try_wait() {
            Ok(Some(s)) => {
                return if s.success() {
                    DockerState::Running
                } else {
                    DockerState::NotRunning
                };
            }
            Ok(None) if std::time::Instant::now() < end => {
                std::thread::sleep(Duration::from_millis(50))
            }
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return DockerState::NotRunning;
            }
        }
    }
}

/// How to run `oc setup`: `OC_SETUP_CMD` (for example `node /path/to/core/packages/diagnostics/src/cli.ts`), else an `oc` found
/// on the PATH or in the extra directories. The word `setup` is added by the caller.
pub fn setup_command(
    env_override: Option<&str>,
    path: Option<&std::ffi::OsStr>,
    extras: &[PathBuf],
    exists: &dyn Fn(&Path) -> bool,
) -> Option<Vec<String>> {
    if let Some(c) = env_override.map(str::trim).filter(|c| !c.is_empty()) {
        return Some(c.split_whitespace().map(str::to_owned).collect());
    }
    signin::find_program("oc", path, extras, exists).map(|p| vec![p.to_string_lossy().into_owned()])
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RowStatus {
    Running,
    Pass,
    Fail,
    Skip,
}

/// One row of the setup chain, as `oc setup` prints it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SetupRow {
    pub status: RowStatus,
    pub title: String,
    pub detail: Vec<String>,
}

/// Folds one line of `oc setup` output into the rows. `[ok]`, `[FAIL]`, `[skip]` and `[ .. ]` start a row; indented lines
/// after it are its detail, timing metrics and fix. Group headings and the summary are ignored.
pub fn fold_setup_line(rows: &mut Vec<SetupRow>, raw: &str) {
    let line = raw.trim_end();
    let marks = [
        ("[ok]", RowStatus::Pass),
        ("[FAIL]", RowStatus::Fail),
        ("[skip]", RowStatus::Skip),
        ("[ .. ]", RowStatus::Running),
    ];
    for (mark, status) in marks {
        if let Some(rest) = line.trim_start().strip_prefix(mark) {
            let title = rest.trim().to_owned();
            // A finished row replaces its running placeholder of the same title.
            let title_only = title.split("  (").next().unwrap_or(&title).to_owned();
            if let Some(last) = rows.last_mut()
                && last.status == RowStatus::Running
                && last.title.split("  (").next() == Some(title_only.as_str())
            {
                last.status = status;
                last.title = title;
                return;
            }
            rows.push(SetupRow {
                status,
                title,
                detail: Vec::new(),
            });
            return;
        }
    }
    if line.starts_with("   ")
        && let Some(last) = rows.last_mut()
    {
        let t = line.trim();
        if !t.is_empty() {
            last.detail.push(t.to_owned());
        }
    }
}

/// Runs `oc setup` and reports the rows as they arrive. `envs` go to the child only (the model key, the gateway name).
/// Returns whether every row passed.
pub fn run_setup(
    cmd: &[String],
    envs: &[(String, String)],
    on_rows: &mut dyn FnMut(&[SetupRow]),
    silence: Duration,
) -> Result<bool, String> {
    let mut rows: Vec<SetupRow> = Vec::new();
    let ok = signin::run_lines_env(
        cmd,
        &["setup".to_owned()],
        envs,
        silence,
        &mut |l| {
            let before = rows.clone();
            fold_setup_line(&mut rows, &signin::strip_ansi_text(l));
            if rows != before {
                on_rows(&rows);
            }
        },
        &mut || {},
    )?;
    Ok(ok && !rows.is_empty() && rows.iter().all(|r| r.status != RowStatus::Fail))
}

// ---------- deciding what to show and do (pure, so the screens stay thin) ----------

/// What looking for the core on the account found.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Discovery {
    /// Nobody is signed in to Wrangler, so nothing could be asked.
    NotSignedIn,
    /// Signed in, and there is no `open-computer-core` Worker on the account.
    NoCore,
    Found(FoundCore),
}

/// Signs in to Cloudflare's API with the Wrangler session (token in memory only), then looks for the core. `make_api` builds
/// the API client from the token, so tests pass a fake.
pub fn discover_via(
    cmd: &[String],
    make_api: &dyn Fn(String) -> Box<dyn CfApi>,
) -> Result<Discovery, String> {
    let Some(token) = token_from_wrangler(cmd)? else {
        return Ok(Discovery::NotSignedIn);
    };
    let api = make_api(token);
    Ok(match discover_core(&*api)? {
        Some(f) => Discovery::Found(f),
        None => Discovery::NoCore,
    })
}

/// The first account's id, for the gateway lookup before a core exists.
pub fn first_account_id(api: &dyn CfApi) -> Option<String> {
    let (status, v) = api.get("/accounts").ok()?;
    (status == 200)
        .then(|| v["result"][0]["id"].as_str().map(str::to_owned))
        .flatten()
}

/// Whether a core reports that it already holds an OpenRouter key: `model_key` in `/v1/diag/bindings`, or the settings'
/// `secrets_set.model_key`.
pub fn core_has_model_key(bindings: Option<&Value>, settings: Option<&Value>) -> bool {
    bindings
        .and_then(|b| b.get("model_key"))
        .and_then(Value::as_bool)
        == Some(true)
        || settings
            .and_then(|s| s.pointer("/secrets_set/model_key"))
            .and_then(Value::as_bool)
            == Some(true)
}

/// What step 2 ("your core") of Get started shows.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum CoreStep {
    Connected,
    NeedSignIn,
    Finding,
    SettingUp,
    /// A core was found on the account: one click connects to it.
    CanConnect(String),
    /// Signed in and no core exists: one click sets one up.
    CanSetUp,
}

pub fn core_step(
    connected: bool,
    signed_in: bool,
    finding: bool,
    setting_up: bool,
    found: Option<&FoundCore>,
) -> CoreStep {
    if connected {
        CoreStep::Connected
    } else if setting_up {
        CoreStep::SettingUp
    } else if !signed_in {
        CoreStep::NeedSignIn
    } else if finding {
        CoreStep::Finding
    } else if let Some(f) = found {
        CoreStep::CanConnect(f.name.clone())
    } else {
        CoreStep::CanSetUp
    }
}

/// Onboarding is finished when the core is reachable and the models are paid for: then the app opens on Chat.
pub fn onboarding_ready(connected: bool, model_key_here: bool, core_has_key: bool) -> bool {
    connected && (model_key_here || core_has_key)
}

/// Whether to run setup, and if not, why, in plain words.
pub fn plan_setup(
    signed_in: bool,
    setup_cmd: &Option<Vec<String>>,
    docker: DockerState,
) -> Result<(), String> {
    if !signed_in {
        return Err("Sign in with Cloudflare first.".to_owned());
    }
    if setup_cmd.is_none() {
        return Err("Set up needs the Open Computer command line (`oc`). Install it, or run `oc setup` in a terminal.".to_owned());
    }
    match docker.problem() {
        Some(p) => Err(p.to_owned()),
        None => Ok(()),
    }
}

/// The environment `oc setup` runs with: Wrangler's location, the gateway name when that gateway exists, and the OpenRouter
/// key when this computer has one. Returns the variables and whether to offer the gateway dashboard link instead.
pub fn setup_env(
    gateway: Option<bool>,
    model_key: Option<&str>,
    wrangler: &[String],
) -> (Vec<(String, String)>, bool) {
    let mut envs = vec![("OC_WRANGLER".to_owned(), wrangler.join(" "))];
    if gateway == Some(true) {
        envs.push(("OC_AI_GATEWAY_ID".to_owned(), "open-computer".to_owned()));
    }
    if let Some(k) = model_key.filter(|k| !k.is_empty() && *k != "managed-by-the-cloud-core") {
        envs.push(("OC_OPENROUTER_KEY".to_owned(), k.to_owned()));
    }
    (envs, gateway != Some(true))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::secrets::InMemorySecrets;
    use std::collections::BTreeMap;

    const TOKEN: &str = "fake-access-token-for-tests";

    fn temp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("oc-auto-{name}-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn a_saved_connection_is_read_adopted_into_the_keyring_and_never_printed() {
        let dir = temp("saved");
        let file = dir.join("cloud.json");
        std::fs::write(
            &file,
            format!(
                r#"{{"url":"https://open-computer-core.example.workers.dev/","token":"{TOKEN}"}}"#
            ),
        )
        .unwrap();
        let core = read_saved_core(&file).expect("saved core");
        assert_eq!(core.url, "https://open-computer-core.example.workers.dev");
        assert!(!format!("{core:?}").contains(TOKEN));
        let store = InMemorySecrets::new();
        let url = adopt_saved_core(core, &store).unwrap();
        assert_eq!(url, "https://open-computer-core.example.workers.dev");
        assert!(store.is_set(Slot::CloudAccess));
        assert_eq!(
            store.get(Slot::CloudAccess).unwrap().as_deref(),
            Some(TOKEN)
        );
        // The file stays: the `oc` command line reads it too.
        assert!(file.exists());
    }

    #[test]
    fn an_unusable_saved_file_is_ignored() {
        let dir = temp("bad");
        for (name, body) in [
            ("missing-token", r#"{"url":"https://x.workers.dev"}"#),
            (
                "empty-token",
                r#"{"url":"https://x.workers.dev","token":""}"#,
            ),
            ("not-http", r#"{"url":"ftp://x","token":"t"}"#),
            ("garbage", "{not json"),
        ] {
            let f = dir.join(name);
            std::fs::write(&f, body).unwrap();
            assert!(read_saved_core(&f).is_none(), "{name}");
        }
        assert!(read_saved_core(&dir.join("nope.json")).is_none());
    }

    struct FakeApi(BTreeMap<&'static str, (u16, Value)>);
    impl CfApi for FakeApi {
        fn get(&self, path: &str) -> Result<(u16, Value), String> {
            Ok(self.0.get(path).cloned().unwrap_or((404, Value::Null)))
        }
    }

    fn acct(id: &str) -> String {
        format!("{id}{}", "0".repeat(32 - id.len()))
    }

    #[test]
    fn the_core_is_found_on_the_account_that_has_it_and_its_address_is_built_from_the_subdomain() {
        let (a, b) = (acct("aa"), acct("bb"));
        let svc_b: &'static str = Box::leak(
            format!("/accounts/{b}/workers/services/open-computer-core").into_boxed_str(),
        );
        let sub_b: &'static str =
            Box::leak(format!("/accounts/{b}/workers/subdomain").into_boxed_str());
        let mut m = BTreeMap::new();
        m.insert("/accounts", (200, serde_json::json!({"result":[{"id": a, "name":"First"},{"id": b, "name":"Second"}]})));
        m.insert(svc_b, (200, serde_json::json!({"result":{}})));
        m.insert(
            sub_b,
            (200, serde_json::json!({"result":{"subdomain":"someone"}})),
        );
        let found = discover_core(&FakeApi(m)).unwrap().expect("a core");
        assert_eq!(found.url, "https://open-computer-core.someone.workers.dev");
        assert_eq!(found.account, "Second");
        assert_eq!(found.account_id(), b);
        assert!(
            !format!("{found:?}").contains(&b),
            "the account id is never printed"
        );
    }

    #[test]
    fn no_core_and_a_refused_listing_are_told_apart() {
        let mut m = BTreeMap::new();
        m.insert(
            "/accounts",
            (
                200,
                serde_json::json!({"result":[{"id": acct("cc"), "name":"Only"}]}),
            ),
        );
        assert_eq!(discover_core(&FakeApi(m)).unwrap(), None);
        let mut denied = BTreeMap::new();
        denied.insert("/accounts", (403, Value::Null));
        assert!(
            discover_core(&FakeApi(denied))
                .unwrap_err()
                .contains("Sign in again")
        );
    }

    #[test]
    fn a_gateway_is_found_missing_or_unknown_when_the_sign_in_cannot_look() {
        let id = acct("dd");
        let present: &'static str =
            Box::leak(format!("/accounts/{id}/ai-gateway/gateways/open-computer").into_boxed_str());
        let mut m = BTreeMap::new();
        m.insert(present, (200, Value::Null));
        assert_eq!(
            gateway_exists(&FakeApi(m.clone()), &id, "open-computer"),
            Some(true)
        );
        assert_eq!(
            gateway_exists(&FakeApi(BTreeMap::new()), &id, "open-computer"),
            Some(false)
        );
        m.insert(present, (403, Value::Null));
        assert_eq!(gateway_exists(&FakeApi(m), &id, "open-computer"), None);
    }

    fn script(body: &str) -> Vec<String> {
        ["sh", "-c", body, "x"].map(String::from).to_vec()
    }

    #[test]
    fn the_wrangler_token_is_read_for_this_process_and_never_put_in_an_error() {
        let ok = script(r#"echo '{"type":"oauth","token":"fake-wrangler-token"}'"#);
        assert_eq!(
            token_from_wrangler(&ok).unwrap().as_deref(),
            Some("fake-wrangler-token")
        );
        let none = script("echo 'You are not logged in'; exit 1");
        assert_eq!(token_from_wrangler(&none).unwrap(), None);
        let odd = script(r#"echo '{"type":"global","email":"a@b.c","key":"secret-global-key"}'"#);
        let err = token_from_wrangler(&odd).unwrap_err();
        assert!(!err.contains("secret-global-key") && err.contains("Advanced"));
    }

    #[test]
    fn docker_state_is_running_not_running_or_missing() {
        assert_eq!(docker_state("true"), DockerState::Running);
        assert_eq!(docker_state("false"), DockerState::NotRunning);
        assert_eq!(
            docker_state("docker-missing-for-test"),
            DockerState::Missing
        );
        assert!(
            DockerState::NotRunning
                .problem()
                .unwrap()
                .contains("Start Docker, then click Set up again")
        );
        assert!(DockerState::Running.problem().is_none());
    }

    #[test]
    fn setup_output_folds_into_rows_with_their_detail() {
        let mut rows = Vec::new();
        for l in [
            "",
            "1. Backend",
            "[ .. ] Sign-in to Cloudflare",
            "[ok]   Sign-in to Cloudflare  (1.2 s)",
            "         Signed in with Cloudflare.",
            "[FAIL] Deploy  (30.0 s)",
            "         The deploy failed.",
            "         Fix: Retry.",
            "[skip] Memory",
            "18 passed, 1 failed, 0 skipped in 31.2 s",
        ] {
            fold_setup_line(&mut rows, l);
        }
        assert_eq!(rows.len(), 3);
        assert_eq!(rows[0].status, RowStatus::Pass);
        assert_eq!(rows[0].detail, ["Signed in with Cloudflare."]);
        assert_eq!(rows[1].status, RowStatus::Fail);
        assert_eq!(rows[1].detail, ["The deploy failed.", "Fix: Retry."]);
        assert_eq!(rows[2].status, RowStatus::Skip);
    }

    #[test]
    fn setup_runs_as_a_real_subprocess_streams_rows_and_passes_secrets_only_to_the_child() {
        let cmd = script(
            r#"[ "$1" = setup ] || exit 2; echo '[ .. ] One'; echo "[ok]   One  (1 ms)"; echo "         gateway=$OC_AI_GATEWAY_ID key_len=${#OC_OPENROUTER_KEY}"; echo '[ok]   Two'"#,
        );
        let envs = vec![
            ("OC_AI_GATEWAY_ID".to_owned(), "open-computer".to_owned()),
            ("OC_OPENROUTER_KEY".to_owned(), "sk-fake".to_owned()),
        ];
        let mut last = Vec::new();
        let ok = run_setup(
            &cmd,
            &envs,
            &mut |r| last = r.to_vec(),
            Duration::from_secs(5),
        )
        .unwrap();
        assert!(ok);
        assert_eq!(last.len(), 2);
        assert_eq!(last[0].detail, ["gateway=open-computer key_len=7"]);
        // A failing row fails the run; a missing program says why.
        let bad = script("echo '[FAIL] Deploy'; exit 1");
        assert!(!run_setup(&bad, &[], &mut |_| {}, Duration::from_secs(5)).unwrap());
        assert!(
            run_setup(
                &["oc-missing-for-test".to_owned()],
                &[],
                &mut |_| {},
                Duration::from_secs(5)
            )
            .is_err()
        );
    }

    #[test]
    fn the_setup_command_comes_from_the_environment_then_the_path_then_the_extra_directories() {
        let on = |files: &'static [&'static str]| {
            move |p: &Path| files.contains(&p.to_string_lossy().as_ref())
        };
        let path = std::ffi::OsString::from("/usr/bin");
        let extras = vec![PathBuf::from("/home/u/.local/bin")];
        assert_eq!(
            setup_command(Some("node /x/cli.ts"), Some(&path), &extras, &on(&[])),
            Some(vec!["node".into(), "/x/cli.ts".into()])
        );
        assert_eq!(
            setup_command(
                None,
                Some(&path),
                &extras,
                &on(&["/usr/bin/oc", "/home/u/.local/bin/oc"])
            ),
            Some(vec!["/usr/bin/oc".into()])
        );
        assert_eq!(
            setup_command(None, Some(&path), &extras, &on(&["/home/u/.local/bin/oc"])),
            Some(vec!["/home/u/.local/bin/oc".into()])
        );
        assert_eq!(setup_command(None, Some(&path), &extras, &on(&[])), None);
    }

    #[test]
    fn discovery_says_not_signed_in_no_core_or_the_core_found() {
        let not_in = script("echo 'You are not logged in'; exit 1");
        let make: &dyn Fn(String) -> Box<dyn CfApi> = &|_| Box::new(FakeApi(BTreeMap::new()));
        assert_eq!(discover_via(&not_in, make).unwrap(), Discovery::NotSignedIn);
        let signed = script(r#"echo '{"type":"oauth","token":"fake-wrangler-token"}'"#);
        let empty: &dyn Fn(String) -> Box<dyn CfApi> = &|t| {
            assert_eq!(t, "fake-wrangler-token");
            let mut m = BTreeMap::new();
            m.insert(
                "/accounts",
                (
                    200,
                    serde_json::json!({"result":[{"id": acct("ee"), "name":"A"}]}),
                ),
            );
            Box::new(FakeApi(m))
        };
        assert_eq!(discover_via(&signed, empty).unwrap(), Discovery::NoCore);
        let id = acct("ff");
        let svc: &'static str = Box::leak(
            format!("/accounts/{id}/workers/services/open-computer-core").into_boxed_str(),
        );
        let sub: &'static str =
            Box::leak(format!("/accounts/{id}/workers/subdomain").into_boxed_str());
        let with_core: &dyn Fn(String) -> Box<dyn CfApi> = &move |_| {
            let mut m = BTreeMap::new();
            m.insert(
                "/accounts",
                (
                    200,
                    serde_json::json!({"result":[{"id": acct("ff"), "name":"A"}]}),
                ),
            );
            m.insert(svc, (200, Value::Null));
            m.insert(sub, (200, serde_json::json!({"result":{"subdomain":"me"}})));
            Box::new(FakeApi(m))
        };
        match discover_via(&signed, with_core).unwrap() {
            Discovery::Found(f) => assert_eq!(f.url, "https://open-computer-core.me.workers.dev"),
            other => panic!("{other:?}"),
        }
    }

    #[test]
    fn a_core_that_already_holds_an_openrouter_key_is_recognised() {
        assert!(core_has_model_key(
            Some(&serde_json::json!({"model_key": true})),
            None
        ));
        assert!(core_has_model_key(
            None,
            Some(&serde_json::json!({"secrets_set": {"model_key": true}}))
        ));
        assert!(!core_has_model_key(
            Some(&serde_json::json!({"model_key": false})),
            Some(&serde_json::json!({"secrets_set": {"model_key": false}}))
        ));
        assert!(!core_has_model_key(None, None));
    }

    #[test]
    fn step_two_shows_connected_sign_in_connect_or_set_up_in_that_order() {
        let found = FoundCore {
            name: "open-computer-core".into(),
            account: "A".into(),
            url: "https://x".into(),
            modified_on: None,
            account_id: "i".into(),
        };
        assert_eq!(
            core_step(true, false, false, false, None),
            CoreStep::Connected
        );
        assert_eq!(
            core_step(false, false, false, false, Some(&found)),
            CoreStep::NeedSignIn
        );
        assert_eq!(core_step(false, true, true, false, None), CoreStep::Finding);
        assert_eq!(
            core_step(false, true, false, true, None),
            CoreStep::SettingUp
        );
        assert_eq!(
            core_step(false, true, false, false, Some(&found)),
            CoreStep::CanConnect("open-computer-core".into())
        );
        assert_eq!(
            core_step(false, true, false, false, None),
            CoreStep::CanSetUp
        );
    }

    #[test]
    fn onboarding_finishes_only_when_the_core_is_reachable_and_models_are_paid_for() {
        assert!(onboarding_ready(true, true, false));
        assert!(onboarding_ready(true, false, true));
        assert!(!onboarding_ready(true, false, false));
        assert!(!onboarding_ready(false, true, true));
    }

    #[test]
    fn setup_is_refused_with_the_fix_when_not_signed_in_without_oc_or_without_docker() {
        let oc = Some(vec!["oc".to_owned()]);
        assert!(plan_setup(true, &oc, DockerState::Running).is_ok());
        assert!(
            plan_setup(false, &oc, DockerState::Running)
                .unwrap_err()
                .contains("Sign in")
        );
        assert!(
            plan_setup(true, &None, DockerState::Running)
                .unwrap_err()
                .contains("oc setup")
        );
        assert_eq!(
            plan_setup(true, &oc, DockerState::NotRunning).unwrap_err(),
            "Docker is not running. Start Docker, then click Set up again."
        );
        assert!(
            plan_setup(true, &oc, DockerState::Missing)
                .unwrap_err()
                .contains("Install Docker")
        );
    }

    #[test]
    fn setup_gets_the_gateway_name_only_when_that_gateway_exists_and_the_key_only_when_it_is_real()
    {
        let wr = vec!["/home/u/.local/bin/wrangler".to_owned()];
        let (e, link) = setup_env(Some(true), Some("sk-or-real"), &wr);
        assert!(e.contains(&("OC_AI_GATEWAY_ID".to_owned(), "open-computer".to_owned())) && !link);
        assert!(e.contains(&("OC_OPENROUTER_KEY".to_owned(), "sk-or-real".to_owned())));
        assert!(e.contains(&(
            "OC_WRANGLER".to_owned(),
            "/home/u/.local/bin/wrangler".to_owned()
        )));
        for gw in [Some(false), None] {
            let (e, link) = setup_env(gw, Some("managed-by-the-cloud-core"), &wr);
            assert!(
                e.iter()
                    .all(|(k, _)| k != "OC_AI_GATEWAY_ID" && k != "OC_OPENROUTER_KEY")
            );
            assert!(link, "offer the dashboard link instead");
        }
    }

    #[test]
    fn the_saved_connection_is_inspected_without_ever_carrying_the_token() {
        let dir = std::env::temp_dir().join(format!("oc-inspect-{}", std::process::id()));
        let _ = std::fs::create_dir_all(&dir);
        let f = dir.join("cloud.json");
        let _ = std::fs::remove_file(&f);
        assert_eq!(
            inspect_saved_core(&f).problem.as_deref(),
            Some("no saved connection file")
        );
        std::fs::write(
            &f,
            r#"{"url":"https://core.example/","token":"sekret-token-value"}"#,
        )
        .unwrap();
        let ok = inspect_saved_core(&f);
        assert_eq!(
            (ok.url.as_deref(), ok.token_present, ok.problem.clone()),
            (Some("https://core.example"), true, None)
        );
        assert!(!format!("{ok:?}").contains("sekret-token-value"));
        std::fs::write(&f, r#"{"url":"https://core.example"}"#).unwrap();
        let no_token = inspect_saved_core(&f);
        assert!(
            !no_token.token_present && no_token.problem.as_deref().unwrap().contains("no token")
        );
        std::fs::write(&f, r#"{"url":"ftp://x","token":"t"}"#).unwrap();
        assert!(
            inspect_saved_core(&f)
                .problem
                .as_deref()
                .unwrap()
                .contains("not http(s)")
        );
        std::fs::write(&f, "{not json").unwrap();
        assert!(
            inspect_saved_core(&f)
                .problem
                .as_deref()
                .unwrap()
                .contains("not valid JSON")
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_refused_adoption_returns_the_keyring_reason_and_stores_nothing() {
        struct Refusing;
        impl SecretStore for Refusing {
            fn set(&self, _: Slot, _: &str) -> Result<(), crate::secrets::SecretError> {
                Err(crate::secrets::SecretError(
                    "Your system keyring refused to save the access token: locked.".into(),
                ))
            }
            fn get(&self, _: Slot) -> Result<Option<String>, crate::secrets::SecretError> {
                Ok(None)
            }
            fn delete(&self, _: Slot) -> Result<(), crate::secrets::SecretError> {
                Ok(())
            }
        }
        let dir = std::env::temp_dir().join(format!("oc-adopt-{}", std::process::id()));
        let _ = std::fs::create_dir_all(&dir);
        let f = dir.join("cloud.json");
        std::fs::write(&f, r#"{"url":"https://core.example","token":"t0k"}"#).unwrap();
        let core = read_saved_core(&f).unwrap();
        let err = adopt_saved_core(core, &Refusing).unwrap_err();
        assert!(
            err.starts_with("Your system keyring refused to save the access token"),
            "{err}"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }
}
