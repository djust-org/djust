//! #2597 — the four-arm scope family is guarded on the PANIC path, and the
//! renderer's USE of the guards is pinned, not just the guard types.
//!
//! `{% language %}`, `{% timezone %}`, `{% localize %}` and `{% localtime %}`
//! are one family: each installs a scope, renders its children, and takes the
//! scope back down. Two of the four originally took it down with a plain
//! statement after the render call, which a panicking child skips — and these
//! scopes live on POOLED worker threads, so a leak serves the wrong language,
//! zone or l10n flag to whatever renders on that thread next.
//!
//! The unit tests in `renderer.rs` that construct the guards by hand pin the
//! `Drop` impls only: revert an arm to the manual shape and they stay green.
//! That is the decorative-pin class (#1859/#1860). The two `Localize` /
//! `LocalTime` arms now have real render-path panic tests; the `Language` /
//! `Timezone` arms cannot have one, because their observable state lives in
//! Python (`translation.override`, `timezone._active`) and `cargo test` runs
//! with no interpreter. This file is their mechanical pin: revert either arm
//! to the pre-#2597 `if let Err(exit_err) = …_scope_exit(token.as_ref())`
//! shape and these tests go red.
//!
//! The last two tests are the #1646 anti-drift half: a FIFTH arm of this
//! family cannot be added without either a new `*_scope_exit` registry hook
//! or a new `Drop` guard type, and both sets are pinned here.

use std::collections::BTreeSet;
use std::str::FromStr;

const RENDERER_SRC: &str = include_str!("../src/renderer.rs");
const REGISTRY_SRC: &str = include_str!("../src/registry.rs");

/// Everything before the first `#[cfg(test)]` module. Without this cut a pin
/// on `let _guard = UseL10nGuard;` matches the TEST module's own copy of the
/// line and stays green while production drifts — a pin that cannot go red is
/// the thing this file exists to prevent (#1859/#1860).
fn production_half(src: &str) -> &str {
    let cut = src
        .find("\n#[cfg(test)]\n")
        .expect("renderer.rs must have a test module");
    &src[..cut]
}

/// Lex `src` with Rust's own lexer and return the token text with every space
/// removed. Ordinary comments are gone before the pin sees them (a raw-text
/// grep would count prose as code); the whitespace squeeze makes the needles
/// independent of `proc_macro2`'s own spacing choices.
fn code_only(src: &str) -> String {
    let stream =
        proc_macro2::TokenStream::from_str(production_half(src)).expect("the source must lex");
    stream
        .to_string()
        .chars()
        .filter(|c| !c.is_whitespace())
        .collect()
}

fn squeeze(needle: &str) -> String {
    needle.chars().filter(|c| !c.is_whitespace()).collect()
}

/// The `{% language %}` arm must reach its exit hook through `ScopeExitGuard`.
#[test]
fn the_language_arm_binds_the_scope_exit_guard() {
    let code = code_only(RENDERER_SRC);
    let bound = squeeze("ScopeExitGuard::new(token, crate::registry::language_scope_exit)");
    assert!(
        code.contains(&bound),
        "render_language_scope must bind ScopeExitGuard — without it a \
         panicking child leaks translation.override onto this pooled thread"
    );
}

/// Its `{% timezone %}` twin.
#[test]
fn the_timezone_arm_binds_the_scope_exit_guard() {
    let code = code_only(RENDERER_SRC);
    let bound = squeeze("ScopeExitGuard::new(token, crate::registry::timezone_scope_exit)");
    assert!(
        code.contains(&bound),
        "render_timezone_scope must bind ScopeExitGuard"
    );
}

/// And neither may call the exit hook directly any more — the guard is the
/// only door, so there is no second, unguarded path to drift onto (#1646).
#[test]
fn no_arm_calls_a_scope_exit_hook_outside_the_guard() {
    let code = code_only(RENDERER_SRC);
    for hook in ["language_scope_exit", "timezone_scope_exit"] {
        let direct = squeeze(&format!("crate::registry::{hook}(token"));
        assert!(
            !code.contains(&direct),
            "renderer.rs calls {hook} directly — the pre-#2597 shape, which \
             skips the exit when a child panics. Route it through \
             ScopeExitGuard instead."
        );
    }
}

/// The two thread-local arms of the same family, pinned the same way. Their
/// behaviour is covered by the render-path panic tests in `renderer.rs`; this
/// says the binding is what makes those tests pass.
#[test]
fn the_localize_and_localtime_arms_bind_their_drop_guards() {
    let code = code_only(RENDERER_SRC);
    assert!(
        code.contains(&squeeze("let _guard = UseL10nGuard;")),
        "the Node::Localize arm must bind UseL10nGuard"
    );
    assert!(
        code.contains(&squeeze("Some(ActiveTimezoneGuard { prev })")),
        "the Node::LocalTime arm must bind ActiveTimezoneGuard"
    );
}

/// #1646: the family is exactly these two Python-backed scope hooks. A fifth
/// arm needs a third `*_scope_exit`, and this pin makes that visible instead
/// of letting it ship un-guarded.
#[test]
fn the_registry_declares_exactly_two_scope_exit_hooks() {
    let found: BTreeSet<&str> = REGISTRY_SRC
        .lines()
        .filter_map(|line| line.trim().strip_prefix("pub fn "))
        .filter_map(|rest| rest.split('(').next())
        .filter(|name| name.ends_with("_scope_exit"))
        .collect();
    let expected: BTreeSet<&str> = ["language_scope_exit", "timezone_scope_exit"]
        .into_iter()
        .collect();
    assert_eq!(
        found, expected,
        "a new *_scope_exit hook joined the family — give its renderer arm a \
         Drop guard and add it to this pin"
    );
}

/// The other half of the same net: the scope guard types themselves.
#[test]
fn the_renderer_declares_exactly_three_scope_guard_types() {
    let found: BTreeSet<&str> = production_half(RENDERER_SRC)
        .lines()
        .filter_map(|line| line.trim().strip_prefix("impl Drop for "))
        .filter_map(|rest| rest.split_whitespace().next())
        .filter(|name| name.ends_with("Guard"))
        .collect();
    let expected: BTreeSet<&str> = ["ActiveTimezoneGuard", "ScopeExitGuard", "UseL10nGuard"]
        .into_iter()
        .collect();
    assert_eq!(
        found, expected,
        "the set of scope Drop guards in renderer.rs changed — a fifth arm of \
         the {{% language %}}/{{% timezone %}}/{{% localize %}}/\
         {{% localtime %}} family must be guarded too (#1646)"
    );
}
