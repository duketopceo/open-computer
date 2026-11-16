//! Test connection: backend, model and memory, each ok or the failing piece's plain message.

use rune::{El, col, el, row, text};
use rune_kit::{Badge, BadgeVariant, Button, ButtonVariant, Kit};
use shell_core::diagnostics::{Row, Status};
use shell_core::onboarding::{Outcome, PieceStatus, Step};

use super::caption;
use crate::app::App;

pub fn view(s: &App, k: &Kit) -> El {
    let intro = if s.onboarding.step() == Step::Done {
        "Backend, model and memory all work. You are ready to go."
    } else {
        "Checks that the backend answers, the model key works and memory saves and reads back."
    };
    let mut list = col().key("pieces").gap(12.0).w_full();
    for piece in s.onboarding.pieces() {
        list = list.child(piece_row(s, k, &piece));
    }
    col()
        .key("test")
        .gap(16.0)
        .w_full()
        .child(caption(k, intro))
        .child(list)
        .child(full_tests(s, k))
}

/// How far the chain has got: rows that passed, rows in all, rows that finished either way, and seconds spent so far.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Counts {
    pub passed: usize,
    pub total: usize,
    pub finished: usize,
    pub seconds: f64,
}

pub fn counts(rows: &[Row]) -> Counts {
    let finished = |r: &&Row| matches!(r.status, Status::Pass | Status::Fail | Status::Skip);
    Counts {
        passed: rows.iter().filter(|r| r.status == Status::Pass).count(),
        total: rows.len(),
        finished: rows.iter().filter(finished).count(),
        seconds: rows.iter().filter_map(|r| r.ms).fold(0.0, |a, b| a + b) / 1000.0,
    }
}

/// A thin bar that fills as rows finish. The fill is the accent.
fn progress_bar(k: &Kit, frac: f32) -> El {
    let track = row()
        .key("progress")
        .w_full()
        .h(8.0)
        .radius(999.0)
        .fill(k.c.layer_02)
        .border(1.0, k.c.border_base)
        .clip_soft(0.0);
    let fill = crate::theme::accent_fill(el().key("progress-fill").h_full().radius(999.0));
    track.child(fill.w_frac(frac.clamp(0.0, 1.0)))
}

/// The fixed header of the chain: how far it got, the bar, and the button that runs it again.
pub fn header(s: &App, k: &Kit) -> El {
    let c = counts(&s.diag_rows);
    let started = c.finished > 0 || s.diag_running;
    let label = if s.diag_running {
        "Running…"
    } else if started {
        "Run again"
    } else {
        "Run tests"
    };
    let mut button = row()
        .key("run-again")
        .role("button")
        .label(label)
        .focusable(!s.diag_running)
        .center_y()
        .padding(10.0)
        .radius(8.0)
        .child(
            text(label)
                .font(k.ty.ui_medium)
                .color(crate::theme::on_accent()),
        );
    button = crate::theme::accent_fill(button);
    if !s.diag_running {
        button = button.on_press(|s: &mut App| s.run_full_tests());
    }
    let frac = if c.total == 0 {
        0.0
    } else {
        c.finished as f32 / c.total as f32
    };
    col()
        .key("chain-header")
        .w_full()
        .gap(8.0)
        .child(
            row()
                .w_full()
                .gap(16.0)
                .center_y()
                .child(
                    text(format!(
                        "{} of {} passed · {:.1} s",
                        c.passed, c.total, c.seconds
                    ))
                    .key("chain-summary")
                    .font(k.ty.ui_medium)
                    .color(k.c.text_strong)
                    .grow(1.0),
                )
                .child(button),
        )
        .child(progress_bar(k, frac))
}

/// The full chain: one single line per check (status, name, time in a fixed right column). Details are folded away and open
/// on a click; a failure opens itself with its fix.
pub(super) fn full_tests(s: &App, k: &Kit) -> El {
    let mut group = "";
    let mut list = col().key("full-tests").gap(4.0).w_full();
    for r in &s.diag_rows {
        if r.group != group {
            group = r.group;
            list = list.child(text(r.group).font(k.ty.ui_medium).color(k.c.text_strong));
        }
        list = list.child(diag_row(s, k, r));
    }
    list
}

fn diag_row(s: &App, k: &Kit, r: &Row) -> El {
    let (mark, color) = match r.status {
        Status::Pending => ("·", k.c.text_muted),
        Status::Running => ("...", k.c.text_strong),
        Status::Pass => ("ok", k.c.text_strong),
        Status::Fail => ("FAIL", k.c.danger.fg),
        Status::Skip => ("skip", k.c.text_muted),
    };
    let time = r.ms.map_or(String::new(), |ms| {
        if ms >= 1000.0 {
            format!("{:.1} s", ms / 1000.0)
        } else {
            format!("{ms:.0} ms")
        }
    });
    let id = r.id;
    let open = s.expanded_rows.contains(id) || r.status == Status::Fail;
    let has_more = r.status != Status::Pending
        && (!r.detail.is_empty() || !r.metrics.is_empty() || r.fix.is_some());
    let line = row()
        .key(format!("row-{id}"))
        .role("button")
        .label(r.title)
        .focusable(true)
        .w_full()
        .gap(8.0)
        .center_y()
        .padding(2.0)
        .child(col().w(40.0).child(text(mark).font(k.ty.ui).color(color)))
        .child(text(r.title).font(k.ty.ui).color(color).grow(1.0))
        .child(
            col()
                .w(72.0)
                .items_end()
                .child(text(time).font(k.ty.caption()).color(k.c.text_muted)),
        )
        .on_press(move |s: &mut App| s.toggle_row(id));
    let mut block = col().w_full().gap(2.0).child(line);
    if open && has_more {
        let mut details = col().w_full().gap(2.0);
        if !r.detail.is_empty() {
            details = details.child(
                text(r.detail.clone())
                    .font(k.ty.caption())
                    .color(k.c.text_muted),
            );
        }
        for (label, value) in &r.metrics {
            details = details.child(
                text(format!("{label}: {value}"))
                    .font(k.ty.caption())
                    .color(k.c.text_muted),
            );
        }
        if let Some(fix) = &r.fix {
            details = details.child(
                text(format!("Fix: {fix}"))
                    .font(k.ty.caption())
                    .color(k.c.danger.fg),
            );
        }
        block = block.child(
            row()
                .w_full()
                .gap(8.0)
                .child(el().w(48.0))
                .child(details.grow(1.0)),
        );
    }
    block
}

fn piece_row(s: &App, k: &Kit, p: &PieceStatus) -> El {
    let (badge, variant) = match p.outcome {
        Outcome::Unchecked => ("Not checked", BadgeVariant::Neutral),
        Outcome::Ok => ("ok", BadgeVariant::Accent),
        Outcome::Failed => ("Failed", BadgeVariant::Subtle),
    };
    let mut header = row()
        .gap(8.0)
        .center_y()
        .child(text(p.label).font(k.ty.ui_medium).color(k.c.text_base))
        .child(Badge::new(badge).variant(variant).build(k));
    let mut block = col().key(p.label).gap(4.0).w_full();
    if p.outcome == Outcome::Failed {
        let piece = p.piece;
        header = header.child(
            Button::new("Fix")
                .variant(ButtonVariant::Ghost)
                .disabled(s.testing)
                .on_press(move |s: &mut App| s.fix(piece))
                .build(k),
        );
    }
    block = block.child(header);
    if let Some(message) = &p.message {
        let color = if p.outcome == Outcome::Failed {
            k.c.danger.fg
        } else {
            k.c.text_muted
        };
        block = block.child(text(message.clone()).font(k.ty.ui).color(color));
    }
    block
}
