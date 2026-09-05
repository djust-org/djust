//! Pins the registry-generation contract behind `compile_template`'s cache
//! gate (PR #2665 review, finding 2; tightened by the #2668 review).
//!
//! Structural: EVERY registry mutation the PARSER can observe must bump the
//! generation, and must do so AFTER the write completes. That is:
//!   * every `pub fn register_* / unregister_* / clear_*` in `registry.rs`
//!   * `arm_scope_tags` in `registry.rs` (the parser reads `ARMED_SCOPE_TAGS`)
//!   * every `pub fn register_* / unregister_* / clear_*` in
//!     `filter_registry.rs` — `parser.rs` rejects unknown filters via
//!     `is_known_filter`, so the filter registry is parse-validating state too.
//!
//! "After the write" is enforced by requiring the `BumpOnReturn` Drop guard
//! rather than a direct call: a bump placed before the write let a concurrent
//! compile read the new generation, parse against the old registry, and store
//! the stale parse as current.

use std::fs;
use std::path::Path;

const GUARD: &str = "let _bump = ";

fn scan(file: &str, extra_names: &[&str]) -> (usize, Vec<String>) {
    let src = fs::read_to_string(Path::new(env!("CARGO_MANIFEST_DIR")).join(file))
        .unwrap_or_else(|e| panic!("read {file}: {e}"));
    let mut checked = 0usize;
    let mut missing = Vec::new();
    let mut rest = src.as_str();
    while let Some(pos) = rest.find("\npub fn ") {
        let fn_start = &rest[pos + 1..];
        let name_end = fn_start.find('(').unwrap_or(fn_start.len());
        let name = &fn_start[7..name_end];
        let body_end = fn_start[1..]
            .find("\npub fn ")
            .map(|i| i + 1)
            .unwrap_or(fn_start.len());
        let body = &fn_start[..body_end];
        let is_mutation = name.starts_with("register_")
            || name.starts_with("unregister_")
            || name.starts_with("clear_")
            || extra_names.contains(&name);
        if is_mutation {
            checked += 1;
            // The guard must be the FIRST statement so it is dropped last.
            let body_open = body.find('{').unwrap_or(0);
            let head = &body[body_open..(body_open + 400).min(body.len())];
            if !head.contains(GUARD) || !head.contains("BumpOnReturn") {
                missing.push(format!("{file}::{name}"));
            }
            // A direct pre-write bump is the exact race the guard replaces.
            if body.contains("bump_registry_generation();") && name != "bump_registry_generation" {
                missing.push(format!(
                    "{file}::{name} (direct pre-write bump — use the guard)"
                ));
            }
        }
        rest = &fn_start[1..];
    }
    (checked, missing)
}

#[test]
fn every_tag_registry_mutation_bumps_after_the_write() {
    let (checked, missing) = scan("src/registry.rs", &["arm_scope_tags"]);
    assert!(
        checked >= 20,
        "expected ≥20 tag-registry mutation fns, found {checked}"
    );
    assert!(
        missing.is_empty(),
        "tag-registry mutations without a post-write bump: {missing:?}"
    );
}

#[test]
fn every_filter_registry_mutation_bumps_after_the_write() {
    let (checked, missing) = scan("src/filter_registry.rs", &[]);
    assert!(
        checked >= 3,
        "expected ≥3 filter-registry mutation fns, found {checked}"
    );
    assert!(
        missing.is_empty(),
        "filter-registry mutations without a post-write bump: {missing:?}"
    );
}

#[test]
fn generation_is_readable_and_monotonic() {
    use djust_templates::registry::registry_generation;
    let a = registry_generation();
    let b = registry_generation();
    assert!(b >= a, "generation must never move backwards: {a} -> {b}");
}

#[test]
fn the_guard_bumps_directly_and_never_constructs_itself() {
    // A blanket "replace every `bump_registry_generation();` with the guard"
    // edit once rewrote the guard's OWN drop body into `let _bump =
    // BumpOnReturn;` — infinite recursion on every registry mutation. rustc
    // only WARNS (`unconditional_recursion`); this makes it a failure.
    let src = fs::read_to_string(Path::new(env!("CARGO_MANIFEST_DIR")).join("src/registry.rs"))
        .expect("read registry.rs");
    let start = src
        .find("impl Drop for BumpOnReturn")
        .expect("guard impl present");
    let body = &src[start
        ..src[start..]
            .find("\n}\n")
            .map(|i| start + i)
            .unwrap_or(src.len())];
    assert!(
        body.contains("bump_registry_generation();"),
        "drop must call the bump directly"
    );
    assert!(
        !body.contains("let _bump"),
        "drop must not construct another guard: {body}"
    );
}
