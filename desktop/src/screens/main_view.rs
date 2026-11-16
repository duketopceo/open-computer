//! The app after setup: Chat, Settings and Diagnostics, in one black and white frame.

use rune::{El, ScrollSpec, col, row, text};
use rune_kit::{Badge, BadgeVariant, Button, ButtonVariant, Kit, TextInput};
use shell_core::chat::ApprovalCard;

use super::{caption, test_connection};
use crate::app::App;
use crate::main_app::Main;

pub fn view(s: &App, k: &Kit, m: &Main) -> El {
    super::workspace::view(s, k, m)
}

fn card(k: &Kit, key: String) -> El {
    col()
        .key(key)
        .gap(4.0)
        .w_full()
        .padding(10.0)
        .radius(8.0)
        .border(1.0, k.c.border_strong)
}

pub(super) fn approval_card(k: &Kit, s: &App, key: String, a: &ApprovalCard) -> El {
    let word = match a.state.as_str() {
        "requested" => "Needs your decision",
        "approved" => "Approved",
        "denied" => "Denied",
        _ => "Expired",
    };
    let mut c = card(k, key)
        .child(
            row()
                .gap(8.0)
                .center_y()
                .child(text("Approval").font(k.ty.ui_medium).color(k.c.text_strong))
                .child(Badge::new(word).variant(BadgeVariant::Accent).build(k)),
        )
        .child(text(a.summary.clone()).font(k.ty.ui).color(k.c.text_base));
    if a.state == "requested" {
        c = c.child(
            TextInput::new(&s.approval_note)
                .placeholder("Add a note (optional). It is kept in the audit log.")
                .on_edit(|s: &mut App| &mut s.approval_note)
                .width(None)
                .build(k),
        );
        c = c.child(
            row()
                .gap(8.0)
                .child(
                    Button::new("Approve")
                        .variant(ButtonVariant::Submit)
                        .on_press(|s: &mut App| s.decide_pending(true))
                        .build(k),
                )
                .child(
                    Button::new("Deny")
                        .variant(ButtonVariant::Neutral)
                        .on_press(|s: &mut App| s.decide_pending(false))
                        .build(k),
                ),
        );
    } else {
        // Once decided, the answer stays readable: the chosen option filled, the other dimmed.
        c = c.child(
            row()
                .gap(8.0)
                .child(crate::controls::decided(
                    k,
                    format!("{}-approve", a.id),
                    "Approve",
                    a.state == "approved",
                ))
                .child(crate::controls::decided(
                    k,
                    format!("{}-deny", a.id),
                    "Deny",
                    a.state == "denied",
                )),
        );
    }
    c
}

pub(super) fn diagnostics(s: &App, k: &Kit) -> El {
    col()
        .key("diagnostics")
        .w_full()
        .grow(1.0)
        .gap(10.0)
        .child(caption(
            k,
            "The same chain as the web wizard and `oc doctor`: backend, OpenRouter, speed, sandbox, memory, audit and spend.",
        ))
        .child(test_connection::header(s, k))
        .child(
            col()
                .key("diag-scroll")
                .w_full()
                .grow(1.0)
                .scroll(ScrollSpec {
                    stick_bottom: false,
                })
                .child(test_connection::full_tests(s, k)),
        )
}
