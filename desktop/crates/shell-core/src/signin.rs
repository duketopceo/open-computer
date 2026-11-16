//! "Sign in with Cloudflare": the person clicks Allow on Cloudflare's own page in their browser. This runs Wrangler's
//! OAuth login with the scopes setup needs; Wrangler keeps and refreshes the session, and this code never reads it.
//! No token is minted, pasted or shown. Mirrors `core/packages/diagnostics/src/signin.ts`.

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

/// The command that runs Wrangler: `OC_WRANGLER` (for example `pnpm exec wrangler`), else `npx --yes wrangler@4`.
fn wrangler_command() -> Vec<String> {
    std::env::var("OC_WRANGLER")
        .ok()
        .filter(|c| !c.trim().is_empty())
        .map_or_else(
            || {
                vec![
                    "npx".to_owned(),
                    "--yes".to_owned(),
                    "wrangler@4".to_owned(),
                ]
            },
            |c| c.split_whitespace().map(str::to_owned).collect(),
        )
}

/// Opens the browser sign-in and waits for the person to finish, then reads who is signed in. Blocking: run on a thread.
pub fn sign_in() -> Result<SignIn, String> {
    let cmd = wrangler_command();
    let (program, rest) = cmd.split_first().ok_or("Wrangler was not found.")?;
    let status = Command::new(program)
        .args(rest)
        .args(sign_in_args())
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map_err(|_| "Wrangler was not found. Install Node.js, then try again.".to_owned())?;
    if !status.success() {
        return Err(
            "The sign-in did not finish. Try again and click Allow in the browser.".to_owned(),
        );
    }
    who_am_i()
}

pub fn who_am_i() -> Result<SignIn, String> {
    let cmd = wrangler_command();
    let (program, rest) = cmd.split_first().ok_or("Wrangler was not found.")?;
    let out = Command::new(program)
        .args(rest)
        .arg("whoami")
        .stdin(Stdio::null())
        .output()
        .map_err(|_| "Wrangler was not found.".to_owned())?;
    Ok(parse_whoami(&String::from_utf8_lossy(&out.stdout)))
}

#[cfg(test)]
mod tests {
    use super::*;

    const ID: &str = "0123456789abcdef0123456789abcdef";

    fn signed_in(extra_missing: &[&str]) -> String {
        let mut s = format!(
            "👋 You are logged in with an OAuth Token, associated with the email someone@example.com.\n│ Account Name │ Account ID │\n│ Someone's Account │ {ID} │\nScope (Access)\n"
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
        assert!(!format!("{s:?}").contains(ID));
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
}
