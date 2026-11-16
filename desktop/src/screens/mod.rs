//! The four onboarding screens and the frame they share.

mod connect;
mod memory;
mod model_key;
mod test_connection;

use rune::{Cx, El, col, row, text};
use rune_kit::{Button, ButtonVariant, Kit};
use shell_core::onboarding::Step;

use crate::app::App;

pub fn view(s: &App, cx: &mut Cx<App>) -> El {
    let k = rune_theme_opencode::kit(cx.system_dark());
    let (position, body) = match s.onboarding.step() {
        Step::Connect => (1, connect::view(s, k)),
        Step::ModelKey => (2, model_key::view(s, k)),
        Step::Memory => (3, memory::view(s, k)),
        Step::Test | Step::Done => (4, test_connection::view(s, k)),
    };
    let step = s.onboarding.step();

    let mut page = col().w_full().max_w(520.0).gap(16.0).child(
        text(format!("Step {position} of 4"))
            .key("position")
            .font(k.ty.caption())
            .color(k.c.text_muted),
    );
    page = page
        .child(
            text(step.title())
                .key("title")
                .font(k.ty.body_medium)
                .color(k.c.text_strong),
        )
        .child(body);
    if let Some(notice) = &s.notice {
        page = page.child(
            text(notice.clone())
                .key("notice")
                .font(k.ty.ui)
                .color(k.c.danger.fg),
        );
    }
    page = page.child(footer(s, k));

    rune_kit::root(
        col()
            .key("frame")
            .fill(k.c.page)
            .padding(32.0)
            .items_start()
            .child(page),
        [],
    )
}

fn footer(s: &App, k: &Kit) -> El {
    let step = s.onboarding.step();
    let mut bar = row().key("footer").gap(8.0).center_y();
    if step != Step::Connect {
        bar = bar.child(
            Button::new("Back")
                .variant(ButtonVariant::Ghost)
                .on_press(|s: &mut App| s.back())
                .build(k),
        );
    }
    match step {
        Step::Test => {
            bar = bar.child(
                Button::new("Test connection")
                    .variant(if s.testing {
                        ButtonVariant::Loading
                    } else {
                        ButtonVariant::Neutral
                    })
                    .disabled(s.testing)
                    .on_press(|s: &mut App| s.run_test())
                    .build(k),
            );
            bar = bar.child(
                Button::new("Run full tests")
                    .variant(if s.diag_running {
                        ButtonVariant::Loading
                    } else {
                        ButtonVariant::Ghost
                    })
                    .disabled(s.diag_running)
                    .on_press(|s: &mut App| s.run_full_tests())
                    .build(k),
            );
            bar = bar.child(
                Button::new("Finish")
                    .variant(ButtonVariant::Submit)
                    .disabled(!s.onboarding.can_advance())
                    .on_press(|s: &mut App| s.next())
                    .build(k),
            );
        }
        Step::Done => {}
        _ => {
            bar = bar.child(
                Button::new("Continue")
                    .variant(ButtonVariant::Submit)
                    .on_press(|s: &mut App| s.next())
                    .build(k),
            );
        }
    }
    bar
}

/// A caption under a control.
fn caption(k: &Kit, words: impl Into<String>) -> El {
    text(words.into())
        .font(k.ty.caption())
        .color(k.c.text_muted)
}
