//! Test connection: backend, model and memory, each ok or the failing piece's plain message.

use rune::{El, col, row, text};
use rune_kit::{Badge, BadgeVariant, Button, ButtonVariant, Kit};
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
