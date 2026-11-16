//! Open Computer desktop shell: first-run onboarding on the Rune UI toolkit.
//!
//! `open-computer --print-state` prints the onboarding state as JSON and exits without
//! opening a window.

mod app;
mod screens;
mod theme;

use app::App;
use rune::AppSpec;

fn main() -> color_eyre::Result<()> {
    color_eyre::install()?;

    if std::env::args().skip(1).any(|a| a == "--print-state") {
        println!("{}", shell_core::onboarding::initial_state_json());
        return Ok(());
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
