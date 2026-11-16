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

fn opt(k: &Kit, key: String, label: &str, on: bool, press: impl Fn(&mut App) + 'static) -> El {
    crate::controls::choice(k, key, label, on, true, press)
}

/// Which model a harness runs: Open Computer's own, translated to the harness's naming, or the harness's default.
fn model_choice(s: &App, k: &Kit, h: Harness) -> El {
    if h == Harness::BuiltIn {
        return col().key("harness-model-builtin");
    }
    let native = s.harness_native.iter().any(|id| id == h.id());
    let how = match h {
        Harness::Hermes => "Hermes is told `--provider openrouter` with the model id.",
        _ => {
            "OpenCode is given `openrouter/<model>` when it lists that model; if it does not, it uses its default and the chat says so."
        }
    };
    col()
        .key(format!("harness-model-{}", h.id()))
        .gap(4.0)
        .w_full()
        .child(note(k, "Model"))
        .child(
            row()
                .gap(8.0)
                .child(opt(
                    k,
                    format!("harness-model-oc-{}", h.id()),
                    "Use Open Computer's model",
                    !native,
                    move |s: &mut App| s.set_harness_native(h, false),
                ))
                .child(opt(
                    k,
                    format!("harness-model-native-{}", h.id()),
                    "Harness default",
                    native,
                    move |s: &mut App| s.set_harness_native(h, true),
                )),
        )
        .child(note(k, how))
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
                .child(line(k, summary))
                .child(model_choice(s, k, h)),
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
