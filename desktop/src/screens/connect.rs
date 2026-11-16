//! Connect Cloudflare. In this slice the person enters the address and access secret of an
//! already deployed cloud core; the guided deploy step arrives later, so there is no deploy
//! button here.

use rune::{El, col, row};
use rune_kit::{Badge, BadgeVariant, Field, Kit, TextInput};

use super::caption;
use crate::app::App;

pub fn view(s: &App, k: &Kit) -> El {
    let url = Field::new()
        .label("Cloud core address")
        .control(
            TextInput::new(&s.cloud_url)
                .placeholder("https://your-core.example.com")
                .on_edit(|s: &mut App| &mut s.cloud_url)
                .build(k),
        )
        .build(k);

    let access_set = s.onboarding.secrets_set().cloud_access;
    let secret = Field::new()
        .label("Access secret")
        .control(
            TextInput::new(&s.cloud_secret)
                .secure()
                .placeholder(if access_set {
                    "Saved. Type to replace."
                } else {
                    "Paste the access secret"
                })
                .on_edit(|s: &mut App| &mut s.cloud_secret)
                .build(k),
        )
        .build(k);

    col()
        .key("connect")
        .gap(16.0)
        .w_full()
        .child(caption(
            k,
            "Point the app at your deployed cloud core. The secret is kept in your system keyring.",
        ))
        .child(url)
        .child(secret)
        .child(row().child(status_badge(k, access_set)))
}

fn status_badge(k: &Kit, set: bool) -> El {
    Badge::new(if set {
        "Access secret set"
    } else {
        "Access secret not set"
    })
    .variant(if set {
        BadgeVariant::Accent
    } else {
        BadgeVariant::Neutral
    })
    .build(k)
}
