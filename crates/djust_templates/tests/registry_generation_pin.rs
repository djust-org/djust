//! Pins the registry-generation contract behind `compile_template`'s cache
//! gate (PR #2665 review, finding 2).
//!
//! Structural: EVERY `pub fn register_* / unregister_* / clear_*` in
//! `registry.rs` must call `bump_registry_generation()` — a mutation that
//! forgets it lets a stale parse be served against a changed library set,
//! which is the exact class the gate exists to prevent. Eighteen sites is
//! the #1646 parallel-path-drift shape; this test is the mechanical cure.

use std::fs;
use std::path::Path;

#[test]
fn every_registry_mutation_bumps_the_generation() {
    let src = fs::read_to_string(Path::new(env!("CARGO_MANIFEST_DIR")).join("src/registry.rs"))
        .expect("read registry.rs");

    let mut checked = 0usize;
    let mut missing = Vec::new();
    let mut rest = src.as_str();
    while let Some(pos) = rest.find("\npub fn ") {
        let fn_start = &rest[pos + 1..];
        let name_end = fn_start.find('(').unwrap_or(fn_start.len());
        let name = &fn_start[7..name_end];
        // the body runs to the next top-level fn or the end of file
        let body_end = fn_start[1..]
            .find("\npub fn ")
            .map(|i| i + 1)
            .unwrap_or(fn_start.len());
        let body = &fn_start[..body_end];
        if name.starts_with("register_")
            || name.starts_with("unregister_")
            || name.starts_with("clear_")
        {
            checked += 1;
            if !body.contains("bump_registry_generation();") {
                missing.push(name.to_string());
            }
        }
        rest = &fn_start[1..];
    }

    assert!(
        checked >= 18,
        "expected at least 18 registry mutation fns, found {checked} — the scan regressed"
    );
    assert!(
        missing.is_empty(),
        "registry mutation fns that do NOT bump the generation (a stale parse \
         would be served against a changed library set): {missing:?}"
    );
}

#[test]
fn generation_is_readable_and_monotonic() {
    use djust_templates::registry::registry_generation;
    let a = registry_generation();
    let b = registry_generation();
    assert!(b >= a, "generation must never move backwards: {a} -> {b}");
}
