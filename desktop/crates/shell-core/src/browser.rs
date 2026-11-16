//! Which browser sign-in pages open in. Cloudflare's and OpenRouter's consent pages use whatever account the browser is signed in to,
//! so a person with a work and a personal profile needs to choose. The choice is a command such as
//! `chromium --profile-directory="Profile 2"`; empty means the system's default browser.
//!
//! Wrangler's own opener (the `open` package) ignores the `BROWSER` variable, so for Cloudflare the app runs
//! `wrangler login --browser=false`, reads the address Wrangler prints and opens it with this command itself.

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::Mutex;

static CHOSEN: Mutex<String> = Mutex::new(String::new());

/// Sets the command used from now on (empty: the system browser).
pub fn set(command: &str) {
    *CHOSEN.lock().unwrap_or_else(|e| e.into_inner()) = command.trim().to_owned();
}

/// The chosen command, or none for the system browser.
pub fn get() -> Option<String> {
    let c = CHOSEN.lock().unwrap_or_else(|e| e.into_inner()).clone();
    (!c.is_empty()).then_some(c)
}

/// Splits a command line the way a shell would for plain cases: spaces separate words, single or double quotes keep one.
pub fn split_command(text: &str) -> Vec<String> {
    let (mut words, mut cur, mut quote, mut any) = (Vec::new(), String::new(), None::<char>, false);
    for c in text.chars() {
        match (quote, c) {
            (Some(q), c) if c == q => quote = None,
            (Some(_), c) => cur.push(c),
            (None, '"' | '\'') => {
                quote = Some(c);
                any = true;
            }
            (None, c) if c.is_whitespace() => {
                if any || !cur.is_empty() {
                    words.push(std::mem::take(&mut cur));
                    any = false;
                }
            }
            (None, c) => cur.push(c),
        }
    }
    if any || !cur.is_empty() {
        words.push(cur);
    }
    words
}

/// Opens `url` with `command` (the browser, then its own arguments, then the address). Only https addresses are opened.
pub fn open_with(command: &str, url: &str) -> Result<(), String> {
    if !url.starts_with("https://") {
        return Err("Only https pages are opened.".to_owned());
    }
    let words = split_command(command);
    let Some((program, rest)) = words.split_first() else {
        return Err("No browser command is set.".to_owned());
    };
    let exe = crate::harness::resolve_program(program);
    let mut cmd = Command::new(exe);
    cmd.args(rest)
        .arg(url)
        .env("PATH", crate::signin::child_path())
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    crate::harness::spawn_with_retry(&mut cmd)
        .map(|_| ())
        .map_err(|e| {
            crate::log_warn!("browser", "could not start {program}: {e}");
            format!("The browser command could not be started ({e}).")
        })?;
    crate::log_info!("browser", "opened a sign-in page with {program}");
    Ok(())
}

/// A browser found on this computer.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Detected {
    pub label: String,
    pub command: String,
}

const BROWSERS: [(&str, &str); 8] = [
    ("firefox", "Firefox"),
    ("chromium", "Chromium"),
    ("google-chrome-stable", "Google Chrome"),
    ("google-chrome", "Google Chrome"),
    ("brave", "Brave"),
    ("brave-browser", "Brave"),
    ("vivaldi", "Vivaldi"),
    ("microsoft-edge-stable", "Microsoft Edge"),
];

/// Browsers on the PATH (and the usual extra folders), and for the Chromium family the profile folders by name. Only names are read:
/// no profile contents. `config_dir` is the folder holding `chromium`, `google-chrome` and so on (`~/.config`).
pub fn detect_in(path_dirs: &[PathBuf], config_dir: &Path) -> Vec<Detected> {
    let mut out: Vec<Detected> = Vec::new();
    for (bin, label) in BROWSERS {
        if !path_dirs.iter().any(|d| d.join(bin).is_file()) {
            continue;
        }
        out.push(Detected {
            label: label.to_owned(),
            command: bin.to_owned(),
        });
        // Chromium-family profile folders: `Default` and `Profile N`.
        let profile_root = match bin {
            "chromium" => "chromium",
            "google-chrome-stable" | "google-chrome" => "google-chrome",
            "brave" | "brave-browser" => "BraveSoftware/Brave-Browser",
            "vivaldi" => "vivaldi",
            "microsoft-edge-stable" => "microsoft-edge",
            _ => continue,
        };
        let mut profiles: Vec<String> = std::fs::read_dir(config_dir.join(profile_root))
            .into_iter()
            .flatten()
            .filter_map(Result::ok)
            .filter(|e| e.path().is_dir())
            .map(|e| e.file_name().to_string_lossy().into_owned())
            .filter(|n| n == "Default" || n.starts_with("Profile "))
            .collect();
        profiles.sort();
        for p in profiles {
            out.push(Detected {
                label: format!("{label}, {p}"),
                command: format!("{bin} --profile-directory=\"{p}\""),
            });
        }
    }
    // Chrome and Chromium under two names each: keep the first of every (label, command stem) pair.
    let mut seen = std::collections::HashSet::new();
    out.retain(|d| seen.insert(d.label.clone()));
    out
}

/// [`detect_in`] on this computer.
pub fn detect() -> Vec<Detected> {
    let mut dirs: Vec<PathBuf> = std::env::var_os("PATH")
        .map(|p| std::env::split_paths(&p).collect())
        .unwrap_or_default();
    dirs.extend(crate::signin::extra_dirs(
        crate::signin::home_dir().as_deref(),
    ));
    let config = std::env::var_os("XDG_CONFIG_HOME")
        .map(PathBuf::from)
        .or_else(|| crate::signin::home_dir().map(|h| h.join(".config")))
        .unwrap_or_default();
    detect_in(&dirs, &config)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn a_command_with_quotes_splits_into_the_words_a_shell_would_make() {
        assert_eq!(
            split_command(r#"chromium --profile-directory="Profile 2""#),
            ["chromium", "--profile-directory=Profile 2"]
        );
        assert_eq!(
            split_command("firefox -P 'work profile' --new-window"),
            ["firefox", "-P", "work profile", "--new-window"]
        );
        assert_eq!(split_command("  one   two "), ["one", "two"]);
        assert!(split_command("   ").is_empty());
    }

    #[test]
    fn only_https_pages_open_and_the_chosen_command_gets_the_address_last() {
        let dir = std::env::temp_dir().join(format!("oc-browser-{}", std::process::id()));
        let _ = fs::create_dir_all(&dir);
        let out = dir.join("seen.txt");
        let script = dir.join("fake-browser.sh");
        fs::write(
            &script,
            format!("#!/bin/sh\necho \"$@\" > '{}'\n", out.display()),
        )
        .unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&script, fs::Permissions::from_mode(0o755)).unwrap();
        }
        let cmd = format!("{} --profile-directory=\"Profile 2\"", script.display());
        assert!(open_with(&cmd, "http://insecure.example").is_err());
        assert!(open_with("", "https://dash.cloudflare.com/x").is_err());
        open_with(&cmd, "https://dash.cloudflare.com/oauth2/auth?x=1").unwrap();
        for _ in 0..100 {
            if out.exists() && fs::metadata(&out).map(|m| m.len() > 0).unwrap_or(false) {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(30));
        }
        assert_eq!(
            fs::read_to_string(&out).unwrap().trim(),
            "--profile-directory=Profile 2 https://dash.cloudflare.com/oauth2/auth?x=1"
        );
        assert!(open_with("definitely-not-a-browser-xyz", "https://a.example").is_err());
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn installed_browsers_and_their_profile_folders_are_listed_by_name_only() {
        let root = std::env::temp_dir().join(format!("oc-detect-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        let bin = root.join("bin");
        let cfg = root.join("cfg");
        fs::create_dir_all(&bin).unwrap();
        for b in ["firefox", "chromium"] {
            fs::write(bin.join(b), "").unwrap();
        }
        for p in ["Default", "Profile 2", "Crashpad", "Profile 10"] {
            fs::create_dir_all(cfg.join("chromium").join(p)).unwrap();
        }
        let found = detect_in(&[bin], &cfg);
        let commands: Vec<&str> = found.iter().map(|d| d.command.as_str()).collect();
        assert_eq!(
            commands,
            [
                "firefox",
                "chromium",
                "chromium --profile-directory=\"Default\"",
                "chromium --profile-directory=\"Profile 10\"",
                "chromium --profile-directory=\"Profile 2\""
            ]
        );
        assert!(!found.iter().any(|d| d.command.contains("Crashpad")));
        assert!(detect_in(&[root.join("nowhere")], &cfg).is_empty());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn the_choice_is_held_for_the_run_and_empty_means_the_system_browser() {
        // A command that does nothing: sign-in tests running at the same time must never open a real browser.
        set("  true -P work ");
        assert_eq!(get().as_deref(), Some("true -P work"));
        set("");
        assert_eq!(get(), None);
    }
}
