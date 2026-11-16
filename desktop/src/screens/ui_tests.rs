//! Control states and the ambient background, checked headless on the painted values of the elements (fill, text colour, scale,
//! opacity, position): selected is not the same as unselected for every kind of control, in both appearances, every control has
//! hover, pressed and focus states, and the background keeps text readable and costs little.

use std::sync::Arc;
use std::time::Instant;

use rune::runtime::ElementInfo;
use rune::{Cx, El, Env, Key, KeyEvent, Mods, Runtime, col, row};
use shell_core::secrets::InMemorySecrets;

use crate::app::App;
use crate::controls;
use crate::main_app::{Main, Section, Tab};

fn lum(c: rune::Color) -> f32 {
    c.luminance()
}

/// A column with each control type twice: on and off, plus a disabled one.
fn bench(_s: &App, cx: &mut Cx<App>) -> El {
    let k = crate::theme::kit(cx.system_dark());
    col()
        .key("bench")
        .w_full()
        .h_full()
        .padding(16.0)
        .gap(8.0)
        .child(controls::choice(
            k,
            "choice-on",
            "Chosen",
            true,
            true,
            |_: &mut App| {},
        ))
        .child(controls::choice(
            k,
            "choice-off",
            "Not chosen",
            false,
            true,
            |_: &mut App| {},
        ))
        .child(controls::choice(
            k,
            "choice-disabled",
            "Unavailable",
            false,
            false,
            |_: &mut App| {},
        ))
        .child(controls::switch(
            k,
            "switch-on",
            "On",
            true,
            true,
            |_: &mut App| {},
        ))
        .child(controls::switch(
            k,
            "switch-off",
            "Off",
            false,
            true,
            |_: &mut App| {},
        ))
        .child(controls::decided(k, "decided-yes", "Approve", true))
        .child(controls::decided(k, "decided-no", "Deny", false))
        .child(row().child(controls::choice(
            k,
            "tab-target",
            "Focus me",
            false,
            true,
            |_: &mut App| {},
        )))
}

fn runtime(dark: bool) -> Runtime<App> {
    let mut rt = Runtime::new(
        App::with_store(Arc::new(InMemorySecrets::new())),
        bench,
        (600.0, 700.0),
        Env::new(true, 1.0),
    );
    rt.set_system_dark(dark);
    rt.frame(0.0);
    rt
}

fn find(rt: &Runtime<App>, key: &str) -> ElementInfo {
    rt.elements()
        .into_iter()
        .find(|e| e.key.as_deref() == Some(key) && !e.exiting)
        .unwrap_or_else(|| panic!("no element {key}"))
}

fn settle(rt: &mut Runtime<App>) {
    for _ in 0..60 {
        rt.frame(1.0 / 60.0);
    }
}

fn text_color_of(rt: &Runtime<App>, parent_key: &str) -> rune::Color {
    let p = find(rt, parent_key);
    rt.elements()
        .into_iter()
        .find(|e| {
            e.parent
                .is_some_and(|i| rt.elements()[i].key.as_deref() == Some(parent_key))
                && e.text.is_some()
        })
        .and_then(|e| e.text_color)
        .unwrap_or_else(|| panic!("no text under {parent_key} ({:?})", p.rect))
}

#[test]
fn a_chosen_option_is_a_filled_ink_surface_and_an_unchosen_one_is_not_in_both_appearances() {
    for dark in [true, false] {
        let mut rt = runtime(dark);
        settle(&mut rt);
        let (on, off) = (find(&rt, "choice-on"), find(&rt, "choice-off"));
        // On: a solid ink fill (white on dark, black on light). Off: nothing painted behind it.
        let ink = if dark { 1.0 } else { 0.0 };
        assert!(
            (lum(on.fill) - ink).abs() < 0.05 && on.fill.a > 0.99,
            "dark={dark}: on is the ink fill {:?}",
            on.fill
        );
        assert!(
            off.fill.a < 0.05,
            "dark={dark}: off has no fill {:?}",
            off.fill
        );
        // The text flips so it stays readable on the fill.
        let (ton, toff) = (
            text_color_of(&rt, "choice-on"),
            text_color_of(&rt, "choice-off"),
        );
        assert!(
            (lum(ton) - (1.0 - ink)).abs() < 0.05,
            "dark={dark}: inverted text on the fill"
        );
        assert!(
            (lum(toff) - ink).abs() < 0.2,
            "dark={dark}: ordinary text when off"
        );
        assert_ne!(on.fill, off.fill);
    }
}

#[test]
fn a_switch_is_on_when_its_track_is_the_accent_and_its_knob_is_at_the_right() {
    for dark in [true, false] {
        let rt = runtime(dark);
        let (t_on, t_off) = (find(&rt, "switch-on"), find(&rt, "switch-off"));
        let track = |rt: &Runtime<App>, parent: &ElementInfo| {
            rt.elements()
                .into_iter()
                .find(|e| {
                    e.key.as_deref() == Some("track")
                        && e.parent
                            .is_some_and(|i| rt.elements()[i].rect == parent.rect)
                })
                .expect("a track")
        };
        let (a, b) = (track(&rt, &t_on), track(&rt, &t_off));
        assert!(
            a.fill.a > 0.99 && b.fill.a < 0.05,
            "dark={dark}: filled when on, hairline when off"
        );
        assert_ne!(a.fill, b.fill);
        // The knob sits at opposite ends of the track.
        let knob = |rt: &Runtime<App>, track: &ElementInfo| {
            rt.elements()
                .into_iter()
                .find(|e| {
                    e.key.as_deref() == Some("knob")
                        && e.rect[1] >= track.rect[1] - 1.0
                        && e.rect[1] < track.rect[1] + track.rect[3]
                        && e.rect[0] >= track.rect[0] - 1.0
                        && e.rect[0] < track.rect[0] + track.rect[2]
                })
                .expect("a knob")
        };
        let (ka, kb) = (knob(&rt, &a), knob(&rt, &b));
        assert!(
            ka.rect[0] - a.rect[0] > 18.0,
            "on: the knob is at the right ({:?} in {:?})",
            ka.rect,
            a.rect
        );
        assert!(kb.rect[0] - b.rect[0] < 6.0, "off: the knob is at the left");
        assert_ne!(ka.fill, kb.fill);
    }
}

#[test]
fn a_decision_already_made_stays_readable_the_chosen_one_filled_and_the_other_dimmed() {
    for dark in [true, false] {
        let mut rt = runtime(dark);
        settle(&mut rt);
        let (yes, no) = (find(&rt, "decided-yes"), find(&rt, "decided-no"));
        assert!(yes.fill.a > 0.99 && no.fill.a < 0.05);
        assert!(
            (yes.opacity - 1.0).abs() < 0.01 && (no.opacity - 0.4).abs() < 0.01,
            "{} {}",
            yes.opacity,
            no.opacity
        );
        assert!(
            !yes.focusable && !no.focusable,
            "decided answers do not press"
        );
    }
}

#[test]
fn a_disabled_control_is_dimmed_to_forty_percent_and_cannot_be_focused_or_pressed() {
    let mut rt = runtime(true);
    settle(&mut rt);
    let d = find(&rt, "choice-disabled");
    assert!((d.opacity - 0.4).abs() < 0.01, "{}", d.opacity);
    assert!(!d.focusable);
    let e = find(&rt, "choice-off");
    assert!((e.opacity - 1.0).abs() < 0.01 && e.focusable);
}

#[test]
fn every_control_reacts_to_hover_and_press_with_a_0_96_scale_and_shows_a_ring_for_the_keyboard() {
    for key in ["choice-off", "choice-on", "switch-off"] {
        let mut rt = runtime(true);
        settle(&mut rt);
        let r = find(&rt, key).rect;
        let (x, y) = (r[0] + r[2] / 2.0, r[1] + r[3] / 2.0);
        assert!(find(&rt, key).hover < 0.01 && (find(&rt, key).scale - 1.0).abs() < 0.001);
        rt.pointer_move(x, y);
        settle(&mut rt);
        assert!(find(&rt, key).hover > 0.5, "{key}: hover shows");
        rt.pointer_down(x, y);
        settle(&mut rt);
        let pressed = find(&rt, key);
        assert!(pressed.press > 0.5, "{key}: pressed shows");
        assert!(
            (pressed.scale - 0.96).abs() < 0.01,
            "{key}: pressed scale is 0.96, got {}",
            pressed.scale
        );
        rt.pointer_up(x, y);
        rt.pointer_leave();
        settle(&mut rt);
        assert!(
            (find(&rt, key).scale - 1.0).abs() < 0.001,
            "{key}: back to rest"
        );
    }
    // Keyboard focus draws the ring; the mouse does not.
    let mut rt = runtime(true);
    settle(&mut rt);
    assert!(!find(&rt, "choice-off").focus_visible);
    rt.key(KeyEvent {
        key: Key::Tab,
        mods: Mods::default(),
    });
    settle(&mut rt);
    assert!(
        rt.elements().into_iter().any(|e| e.focus_visible),
        "Tab focuses a control and shows its ring"
    );
}

#[test]
fn the_navigation_rows_mark_the_open_section_with_the_ink_fill() {
    for dark in [true, false] {
        let mut app = App::with_store(Arc::new(InMemorySecrets::new()));
        let mut m = Main::new();
        m.tab = Tab::Settings;
        m.section = Section::Memory;
        app.main = Some(m);
        let mut rt = Runtime::new(app, super::view, (1440.0, 900.0), Env::new(true, 1.0));
        rt.set_system_dark(dark);
        rt.frame(0.0);
        settle(&mut rt);
        let on = find(&rt, "settings-nav-memory");
        let off = find(&rt, "settings-nav-agent");
        assert!(
            on.fill.a > 0.99 && off.fill.a < 0.05,
            "dark={dark}: {:?} vs {:?}",
            on.fill,
            off.fill
        );
        assert!((lum(on.fill) - if dark { 1.0 } else { 0.0 }).abs() < 0.05);
        // The sidebar's own selection reads the same way.
        let side = find(&rt, "nav-settings");
        assert!(
            side.fill.a > 0.99,
            "Settings is open, so its sidebar entry is the filled one"
        );
    }
}

#[test]
fn the_sidebar_marks_the_open_task_and_the_composer_chip_fills_while_its_menu_is_open() {
    let mut app = App::with_store(Arc::new(InMemorySecrets::new()));
    let mut m = Main::new();
    m.chat.push_user("hello there");
    app.main = Some(m);
    let mut rt = Runtime::new(app, super::view, (1440.0, 900.0), Env::new(true, 1.0));
    rt.frame(0.0);
    settle(&mut rt);
    let t = find(&rt, "thread-current");
    assert!(t.fill.a > 0.99, "the open task is filled: {:?}", t.fill);
    let chip = find(&rt, "harness-chip");
    assert!(chip.fill.a < 0.05);
    rt.update(|s, _| s.toggle_harness_menu());
    settle(&mut rt);
    assert!(
        find(&rt, "harness-chip").fill.a > 0.99,
        "open menu: the chip is filled"
    );
    // In the menu, the agent in use is filled and the others are outlined.
    let in_use = find(&rt, "harness-builtin");
    let other = find(&rt, "harness-hermes");
    assert!(in_use.fill.a > 0.99 && other.fill.a < 0.05);
}

// ---------- the background ----------

#[test]
fn the_background_never_costs_text_its_contrast() {
    let contrast = |a: rune::Color, b: rune::Color| {
        let (a, b) = (a.luminance(), b.luminance());
        (a.max(b) + 0.05) / (a.min(b) + 0.05)
    };
    for dark in [true, false] {
        let k = crate::theme::kit(dark);
        let worst = crate::ambient::brightest(k);
        assert!(
            contrast(k.c.text_base, worst) >= 15.0,
            "dark={dark}: body text on the brightest background {:?}",
            contrast(k.c.text_base, worst)
        );
        assert!(
            contrast(k.c.text_muted, worst) >= 7.0,
            "dark={dark}: muted text keeps 7:1"
        );
        // And it is only a hint: a few percent away from the page.
        assert!(
            (worst.luminance() - k.c.page.luminance()).abs() < 0.12,
            "dark={dark}: faint"
        );
    }
}

fn frame_view(s: &App, cx: &mut Cx<App>) -> El {
    let k = crate::theme::kit(cx.system_dark());
    col()
        .key("frame")
        .fill(k.c.page)
        .w_full()
        .h_full()
        .child(crate::ambient::layer(k, s.ambient))
        .child(col().key("content").w_full().h_full().padding(24.0))
}

fn timed(ambient: bool) -> (Runtime<App>, std::time::Duration) {
    let mut app = App::with_store(Arc::new(InMemorySecrets::new()));
    app.ambient = ambient;
    let mut rt = Runtime::new(app, frame_view, (1440.0, 900.0), Env::new(true, 1.0));
    rt.frame(0.0);
    let start = Instant::now();
    // A tenth of a second apart: the wake rate while the glows move.
    for i in 1..=100 {
        rt.frame(f64::from(i) * 0.1);
    }
    let took = start.elapsed();
    (rt, took)
}

#[test]
fn the_background_drifts_by_waking_about_ten_times_a_second_not_every_display_frame_and_holds_still_when_off()
 {
    let (moving, _) = timed(true);
    let wake = moving.ambient.expect("the glows ask for wakes");
    assert!(
        (wake - 0.1).abs() < 0.02,
        "about ten wakes a second, not one per display frame: {wake}"
    );
    let (still, _) = timed(false);
    assert!(
        still.ambient.is_none() && still.wake_deadline().is_none(),
        "off: the window sleeps"
    );
    // The glows are the three the design names, painted behind the content.
    for key in ["ambient-a", "ambient-b", "ambient-c"] {
        find_in(&moving, key);
    }
    let order: Vec<_> = moving
        .elements()
        .into_iter()
        .filter_map(|e| e.key)
        .collect();
    let (bg, content) = (
        order.iter().position(|k| k == "ambient").unwrap(),
        order.iter().position(|k| k == "content").unwrap(),
    );
    assert!(
        bg < content,
        "the background is first, so everything draws over it"
    );
}

fn find_in(rt: &Runtime<App>, key: &str) -> ElementInfo {
    find(rt, key)
}

#[test]
fn reduced_motion_holds_one_still_frame_with_no_wakes() {
    let mut rt = Runtime::new(
        App::with_store(Arc::new(InMemorySecrets::new())),
        frame_view,
        (800.0, 600.0),
        Env::new(true, 1.0),
    );
    rt.set_system_reduced_motion(true);
    rt.frame(0.0);
    rt.frame(1.0);
    assert_eq!(rt.wake_deadline(), None, "no wakes under reduced motion");
}

/// Idle cost of the background on the CPU side: what the glows add to each wake (view, layout, paint build), at the real wake rate.
/// GPU time is not measured here (there is no window); the glows are three full-window gradients.
#[test]
fn the_background_adds_well_under_two_percent_of_one_core_while_idle() {
    let fastest = |on: bool| (0..5).map(|_| timed(on).1).min().unwrap_or_default();
    let (with, without) = (fastest(true), fastest(false));
    let per_wake_extra = with.saturating_sub(without).as_secs_f64() / 100.0;
    let share = per_wake_extra * 10.0;
    eprintln!(
        "ambient: {:.3} ms per wake with, {:.3} ms without; extra {:.3} ms x 10 wakes/s = {:.2} percent of one core (debug build)",
        with.as_secs_f64() * 10.0,
        without.as_secs_f64() * 10.0,
        per_wake_extra * 1000.0,
        share * 100.0
    );
    assert!(
        share < 0.02,
        "the background costs {:.2} percent of a core even in a debug build",
        share * 100.0
    );
}

// ---------- sign-in: the account, the browser, the tip ----------

fn signed_in() -> shell_core::signin::SignIn {
    shell_core::signin::SignIn {
        signed_in: true,
        email: Some("me@example.com".into()),
        accounts: vec!["Work Account".into()],
        scopes: vec!["workers".into()],
        missing: Vec::new(),
    }
}

fn has_text(rt: &Runtime<App>, needle: &str) -> bool {
    rt.elements()
        .into_iter()
        .filter_map(|e| e.text)
        .any(|t| t.contains(needle))
}

#[test]
fn after_a_sign_in_the_account_is_stated_with_a_way_to_switch_on_get_started_and_in_settings() {
    let mut app = App::with_store(Arc::new(InMemorySecrets::new()));
    app.cf_state = Some(signed_in());
    let mut rt = Runtime::new(app, super::view, (900.0, 900.0), Env::new(true, 1.0));
    rt.frame(0.0);
    assert!(has_text(
        &rt,
        "Signed in as me@example.com \u{b7} Work Account."
    ));
    assert!(
        rt.elements()
            .into_iter()
            .any(|e| e.key.as_deref() == Some("not-you"))
    );
    assert!(has_text(&rt, "Not you? Switch account"));

    let mut app = App::with_store(Arc::new(InMemorySecrets::new()));
    app.cf_state = Some(signed_in());
    let mut m = Main::new();
    m.tab = Tab::Settings;
    m.section = Section::Cloudflare;
    app.main = Some(m);
    let mut rt = Runtime::new(app, super::view, (1440.0, 900.0), Env::new(true, 1.0));
    rt.frame(0.0);
    assert!(has_text(
        &rt,
        "Signed in as me@example.com \u{b7} Work Account."
    ));
    assert!(
        rt.elements()
            .into_iter()
            .any(|e| e.key.as_deref() == Some("cf-not-you"))
    );
}

#[test]
fn the_consent_step_says_which_account_the_browser_will_use() {
    let app = App::with_store(Arc::new(InMemorySecrets::new()));
    let mut rt = Runtime::new(app, super::view, (900.0, 900.0), Env::new(true, 1.0));
    rt.frame(0.0);
    assert!(has_text(
        &rt,
        "Cloudflare will use the account your browser is signed in to."
    ));
    // In Settings too, next to the account.
    let mut app = App::with_store(Arc::new(InMemorySecrets::new()));
    let mut m = Main::new();
    m.tab = Tab::Settings;
    m.section = Section::Cloudflare;
    app.main = Some(m);
    let mut rt = Runtime::new(app, super::view, (1440.0, 900.0), Env::new(true, 1.0));
    rt.frame(0.0);
    assert!(has_text(
        &rt,
        "Cloudflare will use the account your browser is signed in to."
    ));
}

#[test]
fn the_browser_setting_lists_what_is_found_saves_the_choice_and_uses_it() {
    let dir = std::env::temp_dir().join(format!("oc-ui-browser-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let mut app = App::with_store(Arc::new(InMemorySecrets::new()));
    app.config_path = dir.join("desktop.json");
    app.browsers = vec![
        shell_core::browser::Detected {
            label: "Chromium, Profile 2".into(),
            command: "chromium --profile-directory=\"Profile 2\"".into(),
        },
        shell_core::browser::Detected {
            label: "Firefox".into(),
            command: "firefox".into(),
        },
    ];
    let mut m = Main::new();
    m.tab = Tab::Settings;
    m.section = Section::Cloudflare;
    app.main = Some(m);
    let mut rt = Runtime::new(app, super::view, (1440.0, 900.0), Env::new(true, 1.0));
    rt.frame(0.0);
    for key in [
        "cf-browser",
        "browser-system",
        "browser-0",
        "browser-1",
        "browser-save",
    ] {
        assert!(
            rt.elements()
                .into_iter()
                .any(|e| e.key.as_deref() == Some(key)),
            "{key}"
        );
    }
    assert!(has_text(&rt, "Chromium, Profile 2") && has_text(&rt, "Open sign-in pages with"));
    // Picking a profile saves it and sets it for every sign-in page from now on.
    // (`true` stands in for a browser: nothing opens while the choice is set.)
    rt.update(|s, _| s.pick_signin_browser("true --profile-directory=\"Profile 2\""));
    assert_eq!(
        shell_core::browser::get().as_deref(),
        Some("true --profile-directory=\"Profile 2\"")
    );
    assert_eq!(
        shell_core::config::load(&dir.join("desktop.json")).signin_browser,
        "true --profile-directory=\"Profile 2\""
    );
    // The system browser clears it.
    rt.update(|s, _| s.pick_signin_browser(""));
    assert_eq!(shell_core::browser::get(), None);
    let _ = std::fs::remove_dir_all(&dir);
}
