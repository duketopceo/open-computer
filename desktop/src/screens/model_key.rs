//! Add a model key (an OpenRouter key by default). The screen shows only whether a key is
//! set; the Test connection step is the test button.

use rune::{El, col, row};
use rune_kit::{Badge, BadgeVariant, Field, Kit, TextInput};

use super::caption;
use crate::app::App;

pub fn view(s: &App, k: &Kit) -> El {
    let key_set = s.onboarding.secrets_set().model_key;
    let field = Field::new()
        .label("Model key")
        .control(
            TextInput::new(&s.model_key)
                .secure()
                .placeholder(if key_set {
                    "Saved. Type to replace."
                } else {
                    "Paste your OpenRouter key"
                })
                .on_edit(|s: &mut App| &mut s.model_key)
                .build(k),
        )
        .build(k);

    col()
        .key("model-key")
        .gap(16.0)
        .w_full()
        .child(caption(
            k,
            "Your key is stored in your system keyring and is never shown again.",
        ))
        .child(field)
        .child(
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
