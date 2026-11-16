//! Test connection: backend, model and memory, each ok or the failing piece's plain message.

use rune::{El, col, row, text};
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

/// The full chain: one row per check with its state and time, a failure's reason and fix underneath.
pub(super) fn full_tests(s: &App, k: &Kit) -> El {
    let mut group = "";
    let mut list = col().key("full-tests").gap(6.0).w_full();
    for r in &s.diag_rows {
        if r.group != group {
            group = r.group;
            list = list.child(text(r.group).font(k.ty.ui_medium).color(k.c.text_strong));
        }
        list = list.child(diag_row(k, r));
    }
    list
}

fn diag_row(k: &Kit, r: &Row) -> El {
    let (mark, color) = match r.status {
        Status::Pending => ("[ . ]", k.c.text_muted),
        Status::Running => ("[ ... ]", k.c.text_strong),
        Status::Pass => ("[ ok ]", k.c.text_strong),
        Status::Fail => ("[FAILED]", k.c.danger.fg),
        Status::Skip => ("[skip]", k.c.text_muted),
    };
    let time = r.ms.map_or(String::new(), |ms| {
        if ms >= 1000.0 {
            format!("  {:.1} s", ms / 1000.0)
        } else {
            format!("  {ms:.0} ms")
        }
    });
    let mut block = col().key(r.id).gap(2.0).w_full().child(
        text(format!("{mark} {}{time}", r.title))
            .font(k.ty.ui)
            .color(color),
    );
    if !r.detail.is_empty() && r.status != Status::Pending {
        block = block.child(
            text(r.detail.clone())
                .font(k.ty.caption())
                .color(k.c.text_muted),
        );
    }
    for (label, value) in &r.metrics {
        block = block.child(
            text(format!("    {label}: {value}"))
                .font(k.ty.caption())
                .color(k.c.text_muted),
        );
    }
    if let Some(fix) = &r.fix {
        block = block.child(
            text(format!("Fix: {fix}"))
                .font(k.ty.caption())
                .color(k.c.danger.fg),
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
