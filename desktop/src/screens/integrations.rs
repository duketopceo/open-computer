//! Settings sections for the agent harness, OpenRouter and Cloudflare. Black and white, one button style per intent.

use rune::{El, col, row, text};
use rune_kit::{Badge, BadgeVariant, Button, ButtonVariant, Kit};
use shell_core::harness::Harness;

use super::caption;

fn note(k: &Kit, t: impl Into<String>) -> El {
    caption(k, t.into()).w_full()
}
use crate::app::App;

fn section(k: &Kit, title: &str, key: &str) -> El {
    col()
        .key(key.to_owned())
        .gap(6.0)
        .w_full()
        .padding(10.0)
        .radius(8.0)
        .border(1.0, k.c.border_strong)
        .child(
            text(title.to_owned())
                .font(k.ty.ui_medium)
                .color(k.c.text_strong),
        )
}

fn line(k: &Kit, t: String) -> El {
    text(t).font(k.ty.ui).color(k.c.text_base).w_full()
}

fn badge(k: &Kit, label: &str, on: bool) -> El {
    Badge::new(label.to_owned())
        .variant(if on {
            BadgeVariant::Accent
        } else {
            BadgeVariant::Neutral
        })
        .build(k)
}

pub fn harness(s: &App, k: &Kit) -> El {
    let mut sec = section(k, "Agent harness", "set-harness").child(note(
        k,
        "Who runs your tasks. Hermes (NousResearch hermes-agent) and OpenCode run here as subprocesses.",
    ))
    .child(note(k, "They use their own permissions: the sandbox, approvals and spend caps apply only to Built-in."));
    for h in Harness::ALL {
        let info = s.harness_info.iter().find(|d| d.harness == h);
        let summary = info.map_or_else(
            || {
                if h == Harness::BuiltIn {
                    "Always available.".to_owned()
                } else {
                    "Checking.".to_owned()
                }
            },
            |d| d.summary(),
        );
        let chosen = s.harness == h;
        sec = sec.child(
            col()
                .key(format!("harness-{}", h.id()))
                .gap(4.0)
                .w_full()
                .child(
                    row()
                        .gap(8.0)
                        .center_y()
                        .child(crate::controls::choice(
                            k,
                            format!("harness-pick-{}", h.id()),
                            h.label(),
                            chosen,
                            true,
                            move |s: &mut App| s.set_harness(h),
                        ))
                        .child(badge(
                            k,
                            if chosen { "In use" } else { "Not in use" },
                            chosen,
                        )),
                )
                .child(note(k, h.describe()))
                .child(line(k, summary)),
        );
    }
    sec.child(
        row().child(
            Button::new("Check again")
                .variant(ButtonVariant::Neutral)
                .on_press(|s: &mut App| s.detect_harnesses())
                .build(k),
        ),
    )
}
