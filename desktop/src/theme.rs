//! The desktop palette: super-high-contrast black and white from DESIGN.md. Dark is white on black, light is
//! black on white. There is no hue anywhere: not in the surfaces, the accent, the focus ring or the status colours
//! (status is carried by words and marks, never by colour). Typography, radii, motion and glyphs still come from the
//! OpenCode theme; only the colours are replaced.

use std::sync::OnceLock;

use rune::Color;
use rune_kit::{Kit, PaletteFocus, PaletteSeed, StateColors};

/// The kit for one appearance with the black and white palette.
pub fn kit(dark: bool) -> &'static Kit {
    static KITS: [OnceLock<Kit>; 2] = [OnceLock::new(), OnceLock::new()];
    KITS[dark as usize].get_or_init(|| build(dark))
}

fn build(dark: bool) -> Kit {
    let mut k = rune_theme_opencode::kit(dark).clone();
    let (bg, fg) = if dark {
        (Color::BLACK, Color::WHITE)
    } else {
        (Color::WHITE, Color::BLACK)
    };
    let mono = Kit::from_palette_focus(PaletteSeed { bg, fg, accent: fg }, PaletteFocus::Ink);
    k.c = mono.c;
    // Status without colour: every state reads in ink on the page, outlined in ink.
    let state = StateColors {
        bg: k.c.bg_base,
        fg,
        border: fg,
        icon: fg,
    };
    k.c.success = state;
    k.c.warning = state;
    k.c.danger = state;
    k.c.info = state;
    k
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Chroma of a colour in OKLab; zero for black, white and every grey.
    fn chroma(c: Color) -> f32 {
        let o = c.to_oklab();
        o[1].hypot(o[2])
    }

    fn contrast(a: Color, b: Color) -> f32 {
        let (a, b) = (a.luminance(), b.luminance());
        (a.max(b) + 0.05) / (a.min(b) + 0.05)
    }

    #[test]
    fn no_colour_has_a_hue_in_either_appearance() {
        for dark in [true, false] {
            let c = kit(dark).c;
            let named = [
                ("page", c.page),
                ("bg_base", c.bg_base),
                ("bg_deep", c.bg_deep),
                ("layer_01", c.layer_01),
                ("layer_02", c.layer_02),
                ("layer_03", c.layer_03),
                ("layer_04", c.layer_04),
                ("bg_accent", c.bg_accent),
                ("text_base", c.text_base),
                ("text_muted", c.text_muted),
                ("text_faint", c.text_faint),
                ("text_accent", c.text_accent),
                ("icon_base", c.icon_base),
                ("icon_accent", c.icon_accent),
                ("border_base", c.border_base),
                ("border_strong", c.border_strong),
                ("border_focus", c.border_focus),
                ("hover", c.hover),
                ("pressed", c.pressed),
                ("submit_fg", c.submit_fg),
                ("success.fg", c.success.fg),
                ("warning.fg", c.warning.fg),
                ("danger.fg", c.danger.fg),
                ("info.fg", c.info.fg),
                ("danger.border", c.danger.border),
                ("danger.icon", c.danger.icon),
            ];
            for (name, col) in named {
                assert!(
                    chroma(col) < 0.012,
                    "{name} has a hue in {} mode (chroma {})",
                    if dark { "dark" } else { "light" },
                    chroma(col)
                );
            }
        }
    }

    #[test]
    fn text_is_black_and_white_with_very_high_contrast() {
        let d = kit(true).c;
        let l = kit(false).c;
        assert!(contrast(d.text_base, d.page) >= 18.0, "dark text on page");
        assert!(contrast(l.text_base, l.page) >= 18.0, "light text on page");
        assert!(contrast(d.text_muted, d.page) >= 7.0, "dark muted text");
        assert!(contrast(l.text_muted, l.page) >= 7.0, "light muted text");
        assert!(
            contrast(d.danger.fg, d.page) >= 18.0,
            "status text keeps full contrast without colour"
        );
    }

    #[test]
    fn dark_is_white_on_black_and_light_is_the_inverse() {
        assert!(kit(true).c.page.luminance() < 0.02);
        assert!(kit(false).c.page.luminance() > 0.98);
        assert!(kit(true).c.text_base.luminance() > 0.98);
        assert!(kit(false).c.text_base.luminance() < 0.02);
    }
}
