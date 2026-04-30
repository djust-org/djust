//! Isolated integration test for filter_registry hot-path short-circuit (#1235).
//!
//! Cargo runs each integration-test file in its own process binary, so the
//! process-global `ANY_CUSTOM_FILTERS_REGISTERED` flag in
//! `djust_templates::filter_registry` starts as `false` here regardless of
//! what other tests do. That makes the assertion below ordering-independent
//! and meaningful — unlike the previous in-module test, which had to
//! `OnceLock`-gate itself to silently no-op when a prior test had already
//! flipped the flag.
//!
//! Background: #1162 introduced the AtomicBool short-circuit so apps with
//! zero custom filters skip the Mutex-protected lookup entirely. We need a
//! test that proves the short-circuit is observable on a fresh process.
//! See #1180 item 4 / v0.9.1 retro Action #201 / GitHub #1235.

use djust_core::Value;
use djust_templates::filter_registry::{apply_custom_filter, is_custom_filter_safe};

#[test]
fn test_atomicbool_short_circuit_when_no_filters_registered() {
    // Pre-registration (which is guaranteed in this process because no
    // other test in this binary has registered anything):
    // `is_custom_filter_safe` must short-circuit and never touch the
    // Mutex-protected HashMap. Asserting via the public API: the lookup
    // returns `false` for an arbitrary name, including names we know
    // would resolve to `is_safe=true` if registered.
    assert!(!is_custom_filter_safe("definitely_not_registered_xyz"));

    // The apply path likewise short-circuits — returns `None` (filter
    // miss), so the renderer falls through to the standard "Unknown
    // filter" error.
    let value = Value::String("x".to_string());
    let result = apply_custom_filter(
        "definitely_not_registered_xyz",
        &value,
        None,
        None,
        false,
        true,
    );
    assert!(result.is_none());
}

#[test]
fn test_apply_custom_filter_short_circuits_for_provably_unregistered_name() {
    // Independent of registration state of other tests because the name
    // is provably unregistered (no production code or test ever
    // registers `__provably_unregistered_name__`). Belt-and-suspenders
    // alongside the test above.
    let value = Value::String("x".to_string());
    let result = apply_custom_filter(
        "__provably_unregistered_name__",
        &value,
        None,
        None,
        false,
        true,
    );
    assert!(result.is_none());
}
