//! Controls whose state can be read at a glance. One rule for the whole app:
//!
//! - **Selected or on**: a filled surface in ink (white on black, black on white) with inverted text, plus a small dot where
//!   it helps. A switch is on when its track is filled with the accent and the knob sits at the right.
//! - **Unselected or off**: a hairline outline only, no fill.
//! - Every control has hover, pressed (0.96 scale) and keyboard-focus (2 px ring) states, and disabled at 40 percent.

use rune::{Color, El, el, row, text};
use rune_kit::Kit;

use crate::app::App;

/// Text colour on a control: inverted when it is selected, because it then sits on an ink fill.
pub fn ink(k: &Kit, on: bool) -> Color {
    if on { k.c.page } else { k.c.text_base }
}

/// Secondary text colour on a control.
pub fn ink_muted(k: &Kit, on: bool) -> Color {
    if on { k.c.page } else { k.c.text_muted }
}

/// A 6 px dot in the control's ink: the "on" mark.
pub fn mark(k: &Kit, on: bool) -> El {
    el().w(6.0)
        .h(6.0)
        .radius(3.0)
        .fill(if on { k.c.page } else { k.c.border_strong })
}

/// The states every pressable control shares: hover, pressed with a 0.96 scale, a 2 px focus ring for the keyboard, and
/// disabled at 40 percent with no press at all. `outline` is the hairline an unselected option wears.
pub fn style(k: &Kit, r: El, on: bool, enabled: bool, outline: bool) -> El {
    let mut r = r.radius(8.0);
    r = if on {
        r.fill(k.c.text_strong)
    } else if outline {
        r.border(1.0, k.c.border_strong)
    } else {
        r
    };
    if !enabled {
        return r.opacity(0.4);
    }
    r.hover(k.c.hover)
        .press(k.c.pressed)
        .press_scale(0.96)
        .focusable(true)
        .focus_ring(2.0, 2.0, k.c.border_focus)
}

/// An option in a set (a harness, a cap, a model): filled and dotted when chosen, hairline when not.
pub fn choice(
    k: &Kit,
    key: impl Into<String>,
    label: &str,
    on: bool,
    enabled: bool,
    press: impl Fn(&mut App) + 'static,
) -> El {
    let mut r = row()
        .key(key.into())
        .role("button")
        .label(label.to_owned())
        .gap(8.0)
        .center_y()
        .padding(8.0);
    if on {
        r = r.child(mark(k, true));
    }
    r = r.child(text(label.to_owned()).font(k.ty.ui).color(ink(k, on)));
    let r = style(k, r, on, enabled, true);
    if enabled { r.on_press(press) } else { r }
}

/// The answer already given, shown as two options that no longer press: the chosen one filled and dotted, the other dimmed.
pub fn decided(k: &Kit, key: impl Into<String>, label: &str, chosen: bool) -> El {
    let mut r = row()
        .key(key.into())
        .role("button")
        .label(label.to_owned())
        .gap(8.0)
        .center_y()
        .padding(8.0);
    if chosen {
        r = r.child(mark(k, true));
    }
    let r = r.child(text(label.to_owned()).font(k.ty.ui).color(ink(k, chosen)));
    let r = style(k, r, chosen, false, true);
    if chosen { r.opacity(1.0) } else { r }
}

/// A real switch: a track with a knob that slides. On is the accent gradient with the knob at the right; off is a hairline
/// track with the knob at the left.
pub fn switch(
    k: &Kit,
    key: impl Into<String>,
    label: &str,
    on: bool,
    enabled: bool,
    press: impl Fn(&mut App) + 'static,
) -> El {
    const W: f32 = 40.0;
    const H: f32 = 22.0;
    const KNOB: f32 = 16.0;
    const INSET: f32 = 3.0;
    let knob = el()
        .key("knob")
        .size(KNOB, KNOB)
        .radius(KNOB / 2.0)
        .absolute(if on { W - INSET - KNOB } else { INSET }, INSET)
        .fill(if on {
            crate::theme::on_accent()
        } else {
            k.c.text_muted
        })
        .motion(k.motion.control);
    let track = el()
        .key("track")
        .size(W, H)
        .radius(H / 2.0)
        .motion(k.motion.control);
    let track = if on {
        crate::theme::accent_fill(track.fill(crate::theme::accent_amber()))
    } else {
        track.border(1.0, k.c.border_strong)
    };
    let r = row()
        .key(key.into())
        .role("switch")
        .label(label.to_owned())
        .gap(8.0)
        .center_y()
        .padding(4.0)
        .child(track.child(knob))
        .child(text(label.to_owned()).font(k.ty.ui).color(k.c.text_base));
    // The switch itself never fills: the track carries the state. The row only gets the shared interaction states.
    let r = style(k, r, false, enabled, false);
    if enabled { r.on_press(press) } else { r }
}
