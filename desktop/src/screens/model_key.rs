//! OpenRouter (pays for the AI models). One button connects it: the browser opens OpenRouter, the person clicks Authorize, and
//! the key goes to the system keyring. A core that already holds a key skips this step. Pasting a key lives under Advanced.

use rune::{El, col, row, text};
use rune_kit::{Badge, BadgeVariant, Button, ButtonVariant, Field, Kit, TextInput};

use super::{accent_button, caption};
use crate::app::App;

/// The pasted-key field and its status, shown only under Advanced.
pub fn paste(s: &App, k: &Kit) -> El {
    let key_set = s.onboarding.secrets_set().model_key;
    let field = Field::new()
        .label("OpenRouter key (if you cannot use the button)")
        .control(
            TextInput::new(&s.model_key)
                .secure()
                .placeholder(if key_set {
                    "Saved. Type to replace."
                } else {
                    "Paste your OpenRouter key"
                })
                .on_edit(|s: &mut App| &mut s.model_key)
                .width(None)
                .build(k),
        )
        .build(k);
    col().key("paste-key").gap(8.0).w_full().child(field).child(
        row().child(
            Badge::new(if key_set { "Key set" } else { "Key not set" })
                .variant(if key_set {
                    BadgeVariant::Accent
                } else {
                    BadgeVariant::Neutral
                })
                .build(k),
        ),
    )
}

pub fn view(s: &App, k: &Kit) -> El {
    let key_set = s.onboarding.secrets_set().model_key;
    let mut page = col().key("model-key").gap(16.0).w_full();
    if s.core_has_model_key {
        return page.child(caption(k, "Your core already has an OpenRouter key."));
    }
    page = page.child(caption(k, "OpenRouter pays for the AI models. Your browser opens OpenRouter, you click Authorize, and the key is saved in your system keyring. It is never shown."));
    if key_set {
        page = page.child(
            text("OpenRouter is connected.".to_owned())
                .font(k.ty.ui)
                .color(k.c.text_base),
        );
    } else {
        let retry = s.or_line.is_some();
        let label = super::openrouter_action_label(s.or_busy, retry);
        let mut actions = row().gap(8.0).center_y().child(accent_button(
            k,
            "connect-openrouter",
            label,
            !s.or_busy,
            |s: &mut App| s.connect_openrouter(),
        ));
        if s.or_busy {
            actions = actions.child(
                Button::new("Cancel")
                    .variant(ButtonVariant::Ghost)
                    .on_press(|s: &mut App| s.cancel_openrouter())
                    .build(k),
            );
        }
        page = page.child(actions);
    }
    if let Some(l) = &s.or_line {
        page = page.child(text(l.clone()).font(k.ty.ui).color(k.c.text_base).w_full());
    }
    if s.show_advanced {
        page = page.child(paste(s, k));
    }
    page
}
