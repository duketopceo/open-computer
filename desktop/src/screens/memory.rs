//! The memory toggle.

use rune::{El, col};
use rune_kit::{Kit, Switch};

use super::caption;
use crate::app::App;

pub fn view(s: &App, k: &Kit) -> El {
    col()
        .key("memory")
        .gap(16.0)
        .w_full()
        .child(caption(k, "Memory lets the agent recall what it learned in earlier tasks, with a source for each item."))
        .child(
            Switch::new(s.onboarding.memory_enabled())
                .label("Turn memory on")
                .on_toggle(|s: &mut App| s.toggle_memory())
                .build(k),
        )
}
