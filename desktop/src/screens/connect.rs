//! The by-hand fields under Advanced: the address and access secret of a deployed cloud core, local-only mode, and a pasted
//! OpenRouter key. The normal way in is the Get started screen, which needs no typing.

use rune::{El, col, row};
use rune_kit::{Badge, BadgeVariant, Button, ButtonVariant, Field, Kit, TextInput};

use super::caption;
use crate::app::App;

pub fn view(s: &App, k: &Kit) -> El {
    let url = Field::new()
        .label("Cloud core address")
        .control(
            TextInput::new(&s.cloud_url)
                .placeholder("https://your-core.example.com")
                .on_edit(|s: &mut App| &mut s.cloud_url)
                .width(None)
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
                .width(None)
                .build(k),
        )
        .build(k);

    let local = s.onboarding.local_only();
    let toggle = row()
        .key("local-only")
        .gap(8.0)
        .center_y()
        .w_full()
        .child(
            Button::new("Use this computer only (no cloud)")
                .variant(ButtonVariant::Ghost)
                .on_press(|s: &mut App| s.toggle_local_only())
                .build(k),
        )
        .child(
            Badge::new(if local { "On" } else { "Off" })
                .variant(if local {
                    BadgeVariant::Accent
                } else {
                    BadgeVariant::Neutral
                })
                .build(k),
        );

    let mut page = col().key("connect").gap(16.0).w_full();
    if local {
        page = page.child(caption(
            k,
            "Local only: the agent runs on this computer with a local sandbox. No Cloudflare account is needed, and nothing keeps running when the app is closed. You still need an OpenRouter key, because models are reached through OpenRouter.",
        ));
    } else {
        page = page
            .child(caption(
                k,
                "Only if the steps above cannot find your core: type the address and access secret of your deployed core. The secret is kept in your system keyring.",
            ))
            .child(url)
            .child(secret)
            .child(row().child(status_badge(k, access_set)));
    }
    page.child(super::model_key::paste(s, k)).child(toggle)
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
