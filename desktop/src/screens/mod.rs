//! The four onboarding screens and the frame they share.

mod connect;
pub(crate) mod integrations;
mod main_view;
mod memory;
mod model_key;
mod settings_page;
mod start;
mod test_connection;
mod workspace;

use rune::{Cx, El, ScrollSpec, col, row, text};
use rune_kit::{Button, ButtonVariant, Kit};
use shell_core::onboarding::Step;

use crate::app::App;

pub fn view(s: &App, cx: &mut Cx<App>) -> El {
    let k = crate::theme::kit(cx.system_dark());
    if let Some(m) = &s.main {
        return rune_kit::root(
            col()
                .key("frame")
                .fill(k.c.page)
                .w_full()
                .h_full()
                .child(crate::ambient::layer(k, s.ambient))
                .child(main_view::view(s, k, m)),
            [],
        );
    }
    let (position, body) = match s.onboarding.step() {
        Step::Connect => (1, start::view(s, k)),
        Step::ModelKey => (2, model_key::view(s, k)),
        Step::Memory => (3, memory::view(s, k)),
        Step::Test | Step::Done => (4, test_connection::view(s, k)),
    };
    let step = s.onboarding.step();

    // Fixed header, a body that scrolls and fills the window, fixed footer: the window size never cuts content off.
    let mut head = col().key("head").w_full().gap(8.0);
    head = head.child(
        text(format!("Step {position} of 4"))
            .key("position")
            .font(k.ty.ui)
            .color(k.c.text_muted),
    );
    head = head.child(
        text(step.title())
            .key("title")
            .font(k.ty.body_medium)
            .color(k.c.text_strong),
    );
    if matches!(step, Step::Test | Step::Done) {
        head = head.child(test_connection::header(s, k));
    }
    let scroll = col()
        .key("onboarding-scroll")
        .w_full()
        .grow(1.0)
        .scroll(ScrollSpec {
            stick_bottom: false,
        })
        .child(col().key("onboarding-body").w_full().gap(16.0).child(body));
    let mut page = col()
        .key("page")
        .w_full()
        .max_w(760.0)
        .h_full()
        .gap(16.0)
        .child(head)
        .child(scroll);
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
            .w_full()
            .h_full()
            .items_center()
            .child(crate::ambient::layer(k, s.ambient))
            .child(page),
        [],
    )
}

/// The keyring banner, when the keyring cannot be read: one clear sentence, a Retry button and a way to the diagnostics.
pub(crate) fn keyring_banner(s: &App, k: &Kit) -> Option<El> {
    let words = s.keyring_banner()?;
    Some(
        col()
            .key("keyring-banner")
            .w_full()
            .gap(8.0)
            .padding(12.0)
            .radius(12.0)
            .border(1.0, k.c.border_strong)
            .child(
                text(words)
                    .font(k.ty.ui_medium)
                    .color(k.c.text_strong)
                    .w_full()
                    .wrap(),
            )
            .child(
                row()
                    .gap(8.0)
                    .child(
                        Button::new("Retry")
                            .variant(ButtonVariant::Submit)
                            .on_press(|s: &mut App| s.retry_keyring())
                            .build(k)
                            .key("keyring-retry"),
                    )
                    .child(
                        Button::new("Copy diagnostics")
                            .variant(ButtonVariant::Neutral)
                            .on_press(|s: &mut App| s.open_diagnostics_page())
                            .build(k)
                            .key("keyring-diagnostics"),
                    ),
            ),
    )
}

/// The accent button: the one thing to do now on a screen. Black text on the gradient, in both appearances.
pub(crate) fn accent_button(
    k: &Kit,
    key: &'static str,
    label: &str,
    enabled: bool,
    on_press: impl Fn(&mut App) + 'static,
) -> El {
    let mut b = row()
        .key(key)
        .role("button")
        .label(label.to_owned())
        .focusable(enabled)
        .center_y()
        .padding(10.0)
        .radius(8.0)
        .child(
            text(label.to_owned())
                .font(k.ty.ui_medium)
                .color(crate::theme::on_accent()),
        );
    b = crate::theme::accent_fill(b);
    if enabled {
        b = b.on_press(on_press);
    }
    b
}

fn footer(s: &App, k: &Kit) -> El {
    let step = s.onboarding.step();
    if step == Step::Connect && !s.show_advanced {
        return row().key("footer");
    }
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
        Step::Done => {
            bar = bar.child(
                Button::new("Open Open Computer")
                    .variant(ButtonVariant::Submit)
                    .on_press(|s: &mut App| s.enter_app())
                    .build(k),
            );
        }
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

/// A caption under a control. Uses the UI size so it stays readable, and is full width so long
/// lines wrap instead of being clipped mid-word.
fn caption(k: &Kit, words: impl Into<String>) -> El {
    text(words.into())
        .font(k.ty.ui)
        .color(k.c.text_muted)
        .w_full()
        .wrap()
}

/// Label on the OpenRouter connect button: Working while the browser wait is open, Try again after
/// a cancelled or failed attempt, Connect otherwise.
pub(crate) fn openrouter_action_label(busy: bool, retry: bool) -> &'static str {
    if busy {
        "Working…"
    } else if retry {
        "Try again"
    } else {
        "Connect OpenRouter"
    }
}

#[cfg(test)]
mod live_tests;
#[cfg(test)]
mod settings_tests;

#[cfg(test)]
mod start_tests;
#[cfg(test)]
mod ui_tests;

#[cfg(test)]
mod workspace_tests;

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use rune::runtime::ElementInfo;
    use rune::{Env, Runtime};
    use shell_core::diagnostics::{Row, Status, initial_rows};
    use shell_core::secrets::{InMemorySecrets, Slot};

    use super::view;
    use crate::app::App;

    fn find(rt: &Runtime<App>, key: &str) -> ElementInfo {
        rt.elements()
            .into_iter()
            .find(|e| e.key.as_deref() == Some(key))
            .unwrap_or_else(|| panic!("missing element {key:?}"))
    }

    /// An app on the Test connection step with every row of the chain filled in: most passed, one failed with its fix.
    fn test_step_app() -> App {
        let mut app = App::with_store(Arc::new(InMemorySecrets::new()));
        app.onboarding.set_local_only(true);
        app.onboarding.advance().unwrap();
        app.onboarding.set_secret_flag(Slot::ModelKey, true);
        app.onboarding.advance().unwrap();
        app.onboarding.advance().unwrap();
        let mut rows: Vec<Row> = initial_rows();
        let n = rows.len();
        for (i, r) in rows.iter_mut().enumerate() {
            r.status = if i + 1 == n {
                Status::Pending
            } else {
                Status::Pass
            };
            r.ms = (i + 1 < n).then_some(120.0 + 40.0 * i as f64);
            r.detail = "It answered in time.".to_owned();
            r.metrics = vec![
                ("p50".to_owned(), "12 ms".to_owned()),
                ("p95".to_owned(), "30 ms".to_owned()),
            ];
        }
        let fail = rows.len() / 2;
        rows[fail].status = Status::Fail;
        rows[fail].detail =
            "The model did not call the tool, so the chain stopped early. ".repeat(3);
        rows[fail].fix =
            Some("Pick another model in Settings, then run the tests again. ".repeat(2));
        app.diag_rows = rows;
        app
    }

    #[test]
    fn the_test_connection_page_scrolls_in_a_small_window_and_its_last_row_is_reachable() {
        let app = test_step_app();
        let last = format!("row-{}", app.diag_rows.last().unwrap().id);
        let mut rt = Runtime::new(app, view, (900.0, 600.0), Env::new(true, 1.0));
        rt.frame(0.0);

        // The rows overflow the window: the last one starts below the bottom edge.
        let before = find(&rt, &last);
        assert!(
            before.rect[1] + before.rect[3] > 600.0,
            "the last row is below the window before scrolling: {:?}",
            before.rect
        );
        // The header and footer are fixed and inside the window, and the body is a scroll container.
        let header = find(&rt, "chain-header");
        assert!(header.rect[1] >= 0.0 && header.rect[1] + header.rect[3] <= 600.0);
        let footer = find(&rt, "footer");
        assert!(
            footer.rect[1] + footer.rect[3] <= 600.5,
            "the footer stays in the window: {:?}",
            footer.rect
        );
        let scroll = find(&rt, "onboarding-scroll");
        assert!(
            scroll.rect[1] + scroll.rect[3] <= footer.rect[1] + 0.5,
            "the scroll area ends where the footer begins"
        );

        // Wheel to the end: the last row comes fully into view, above the footer, and the header did not move.
        rt.wheel(450.0, 300.0, -20000.0);
        for _ in 0..90 {
            rt.frame(1.0 / 60.0);
        }
        let after = find(&rt, &last);
        assert!(
            after.rect[1] >= 0.0 && after.rect[1] + after.rect[3] <= footer.rect[1] + 0.5,
            "the last row is reachable after scrolling: {:?} (footer top {})",
            after.rect,
            footer.rect[1]
        );
        assert_eq!(
            find(&rt, "chain-header").rect,
            header.rect,
            "the header stays put"
        );
    }

    #[test]
    fn opening_a_row_toggles_it_and_stops_following_the_running_row() {
        let mut app = test_step_app();
        let pass_id = app
            .diag_rows
            .iter()
            .find(|r| r.status == Status::Pass)
            .unwrap()
            .id;
        assert!(app.follow_running);
        app.toggle_row(pass_id);
        assert!(app.expanded_rows.contains(pass_id));
        app.toggle_row(pass_id);
        assert!(!app.expanded_rows.contains(pass_id));
        assert!(
            !app.follow_running,
            "opening a row stops following the running row"
        );
    }
}
