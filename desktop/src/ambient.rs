//! A quiet moving background, in the spirit of the owner's wisp site: soft glows that drift slowly behind the content.
//!
//! Three large radial glows sit at different places and each breathes in and out on its own slow clock (23, 31 and 37 seconds, out
//! of step), so the light seems to wander without anything moving across the screen. Two are ink (white on dark, black on light)
//! and one carries a faint hint of the accent. The strongest any glow gets is a few percent, so text keeps its contrast; the app
//! checks that in a test. The glows are Rune cycles: the window wakes about ten times a second for them and sleeps in between,
//! stops entirely when hidden, and holds one still frame when the system asks for reduced motion.

use rune::{Color, Cycle, El, el};
use rune_kit::Kit;

/// How strong each glow gets at its brightest (alpha over the page colour).
pub const INK_ALPHA: f32 = 0.038;
/// Black on white starts nearer the contrast limit of muted text, so its glows are fainter still.
pub const INK_ALPHA_LIGHT: f32 = 0.018;
pub const ACCENT_ALPHA: f32 = 0.025;

/// Wakes per second while a glow is moving. Slow drifts do not need more; each wake repaints the window.
const FPS: f32 = 10.0;

const STOPS: &[(f32, f32)] = &[(0.0, 0.0), (0.5, 1.0), (1.0, 0.0)];

fn cycle(period: f32, phase: f32) -> Cycle {
    Cycle {
        period,
        phase,
        stops: STOPS,
        // Reduced motion, and the setting turned off: a calm half-lit glow that does not move.
        still: 0.5,
        fps: FPS,
    }
}

/// One glow filling the frame: centre in unit coordinates, radii in points.
fn glow(key: &'static str, center: [f32; 2], radii: [f32; 2], color: Color) -> El {
    el().key(key)
        .pin(Some(0.0), Some(0.0), Some(0.0), Some(0.0))
        .radial_gradient(
            center,
            radii,
            [
                (0.0, color),
                (0.6, color.alpha(color.a * 0.35)),
                (1.0, color.alpha(0.0)),
            ],
        )
}

/// The background for a frame of the given appearance. Put it first among the frame's children so everything draws over it.
/// With `moving` off the glows stay put at half strength.
pub fn layer(k: &Kit, moving: bool) -> El {
    let dark = k.c.page.luminance() < 0.5;
    let ink = if dark { Color::WHITE } else { Color::BLACK };
    let ink_alpha = if dark { INK_ALPHA } else { INK_ALPHA_LIGHT };
    let glows = [
        (
            "ambient-a",
            [0.22, 0.30],
            [820.0, 560.0],
            ink.alpha(ink_alpha),
            cycle(23.0, 0.00),
        ),
        (
            "ambient-b",
            [0.78, 0.72],
            [900.0, 600.0],
            ink.alpha(ink_alpha),
            cycle(31.0, 0.37),
        ),
        (
            "ambient-c",
            [0.62, 0.18],
            [640.0, 460.0],
            crate::theme::accent_amber().alpha(ACCENT_ALPHA),
            cycle(37.0, 0.71),
        ),
    ];
    let mut layer = el()
        .key("ambient")
        .pin(Some(0.0), Some(0.0), Some(0.0), Some(0.0));
    for (key, center, radii, color, c) in glows {
        let g = glow(key, center, radii, color);
        layer = layer.child(if moving {
            g.cycle(c)
        } else {
            g.opacity(c.still)
        });
    }
    layer
}

/// The strongest the background can make the page, for the contrast test: every glow at full strength over the page colour.
/// Rune blends in sRGB-encoded space (a 5 percent white glow reads as a 5 percent grey), so that is how this mixes.
#[cfg(test)]
pub fn brightest(k: &Kit) -> Color {
    use rune::color::{srgb_decode, srgb_encode};
    let dark = k.c.page.luminance() < 0.5;
    let ink = if dark { Color::WHITE } else { Color::BLACK };
    let mut c = k.c.page;
    let ink_alpha = if dark { INK_ALPHA } else { INK_ALPHA_LIGHT };
    for a in [ink_alpha, ink_alpha, ACCENT_ALPHA] {
        let over = if a == ACCENT_ALPHA {
            crate::theme::accent_amber()
        } else {
            ink
        };
        let mix = |under: f32, over: f32| {
            srgb_decode(srgb_encode(under) * (1.0 - a) + srgb_encode(over) * a)
        };
        c = Color {
            r: mix(c.r, over.r),
            g: mix(c.g, over.g),
            b: mix(c.b, over.b),
            a: 1.0,
        };
    }
    c
}
