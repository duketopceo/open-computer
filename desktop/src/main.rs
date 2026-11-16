//! Open Computer desktop shell: first-run onboarding on the Rune UI toolkit.
//!
//! `open-computer --print-state` prints the onboarding state as JSON and exits without
//! opening a window.

mod ambient;
mod app;
mod controls;
mod getstarted;
mod integrations;
mod main_app;
mod screens;
mod settings_actions;
mod theme;

use app::App;
use rune::AppSpec;

fn main() -> color_eyre::Result<()> {
    color_eyre::install()?;
    // Headless screenshot runs (`--snap`) are not real runs: they must never add lines to the person's log.
    if !std::env::args().skip(1).any(|a| a == "--snap") {
        shell_core::applog::init();
    }

    if std::env::args().skip(1).any(|a| a == "--print-state") {
        println!("{}", shell_core::onboarding::initial_state_json());
        return Ok(());
    }

    // `--self-test`: the checks the app depends on, without a window, one PASS/FAIL/SKIP line each. Never prints a secret.
    if std::env::args().skip(1).any(|a| a == "--self-test") {
        let lines = shell_core::selftest::run(&shell_core::selftest::Targets::here());
        println!("{}", shell_core::selftest::render(&lines));
        std::process::exit(shell_core::selftest::exit_code(&lines));
    }

    rune::run(AppSpec {
        fonts: rune_theme_opencode::FONTS,
        families: rune_theme_opencode::FAMILIES,
        min_size: Some((520.0, 520.0)),
        setup: Some(App::setup),
        poll: Some(App::poll),
        ..AppSpec::new("Open Computer", (640.0, 640.0), App::new, screens::view)
    });
    Ok(())
}
