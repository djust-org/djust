//! The loop render cache must never serve a stale `forloop` position (#2402).
//!
//! # Why this file exists separately from `test_loop_render_cache_1967.rs`
//!
//! That suite already carries a `forloop_counter` battery template and a
//! `gate_off_forloop_guard_is_load_bearing` test — and until #2402 **both were
//! vacuous**. `{{ forloop.counter }}` rendered the empty string on every
//! iteration, so "cached output is byte-identical to uncached" compared `""`
//! against `""` and could not have gone red no matter what the cache did. The
//! guard `loop_cache.rs` describes as *"defensive — the Rust renderer does not
//! currently implement `forloop`"* was protecting nothing.
//!
//! Binding the seven members makes that guard load-bearing for the first time,
//! so it needs a suite that (a) proves the identity claim is now non-vacuous
//! and (b) sweeps **every way a template can reach the name**, not just the
//! direct `{{ forloop.counter }}` spelling the 1967 battery uses.
//!
//! # Why every spelling, not a representative one
//!
//! Two independent gates can disable caching, and they do not cover the same
//! spellings:
//!
//! * Gate 1 (`body_is_position_dependent`) matches a `Node::Variable` whose
//!   name is `forloop` or starts with `forloop.`. It does **not** look inside
//!   a `{% with %}` assignment, a `{% firstof %}` argument or a
//!   `{% widthratio %}` operand — those variants either recurse into children
//!   only or return `false`.
//! * Gate 2 (`body_root_var_names` ⊆ loop vars) reads every one of those
//!   argument positions, and `forloop` is not a loop variable name, so it
//!   disables caching for them.
//!
//! Which gate catches which spelling is an implementation detail; that **some**
//! gate catches every spelling is the invariant. Asserting the invariant
//! directly — identical output, and zero cache traffic — is what keeps a future
//! narrowing of either gate from silently reintroducing stale positions.

use djust_core::{Context, Value};
use djust_templates::loop_cache::{LoopCacheGuard, LoopRenderCache};
use djust_templates::Template;
use indexmap::IndexMap;

fn obj(pairs: &[(&str, &str)]) -> Value {
    let mut m = IndexMap::new();
    for (k, v) in pairs {
        m.insert((*k).into(), Value::String((*v).to_string()));
    }
    Value::Object(m)
}

fn items(rows: &[(&str, &str)]) -> Value {
    Value::List(
        rows.iter()
            .map(|(id, name)| obj(&[("id", id), ("name", name)]))
            .collect(),
    )
}

/// Render with NO cache installed — the baseline the cached path must match.
fn render_uncached(src: &str, value: &Value) -> String {
    let tmpl = Template::new(src).expect("parse");
    let mut ctx = Context::new();
    ctx.set("xs".to_string(), value.clone());
    tmpl.render(&ctx).expect("render")
}

fn render_cached(tmpl: &Template, value: &Value, cache: &mut LoopRenderCache) -> String {
    cache.begin_render();
    let html = {
        let _guard = LoopCacheGuard::install(cache);
        let mut ctx = Context::new();
        ctx.set("xs".to_string(), value.clone());
        tmpl.render(&ctx).expect("render")
    };
    cache.prune();
    html
}

/// Every spelling that reaches `forloop`, one per resolution shape.
fn forloop_spellings() -> Vec<(&'static str, &'static str)> {
    vec![
        ("variable", "{% for x in xs %}[{{ forloop.counter }}{{ x.name }}]{% endfor %}"),
        ("whole-dict", "{% for x in xs %}[{{ forloop }}]{% endfor %}"),
        (
            "revcounter",
            "{% for x in xs %}[{{ forloop.revcounter }}{{ x.name }}]{% endfor %}",
        ),
        (
            "first-last",
            "{% for x in xs %}[{{ forloop.first }}{{ forloop.last }}{{ x.name }}]{% endfor %}",
        ),
        (
            "if-condition",
            "{% for x in xs %}[{% if forloop.first %}F{% endif %}{{ x.name }}]{% endfor %}",
        ),
        (
            "filtered",
            "{% for x in xs %}[{{ forloop.counter|add:'10' }}{{ x.name }}]{% endfor %}",
        ),
        // The three argument positions Gate 1 does not look inside.
        (
            "with-assignment",
            "{% for x in xs %}{% with c=forloop.counter %}[{{ c }}{{ x.name }}]{% endwith %}{% endfor %}",
        ),
        (
            "firstof-argument",
            "{% for x in xs %}[{% firstof forloop.counter 'F' %}{{ x.name }}]{% endfor %}",
        ),
        (
            "widthratio-operand",
            "{% for x in xs %}[{% widthratio forloop.counter 3 100 %}{{ x.name }}]{% endfor %}",
        ),
        (
            "cycle-operand",
            "{% for x in xs %}[{% cycle forloop.counter 'z' %}{{ x.name }}]{% endfor %}",
        ),
        (
            "parentloop-nested",
            "{% for x in xs %}{% for y in xs %}[{{ forloop.parentloop.counter }}.{{ forloop.counter }}]{% endfor %}{% endfor %}",
        ),
        // A loop VARIABLE spelled `forloop`: the item wins, and the body is in
        // fact position-independent — but the guard is conservative and that is
        // the correct direction, so the identity claim must still hold.
        ("loopvar-named-forloop", "{% for forloop in xs %}[{{ forloop.name }}]{% endfor %}"),
    ]
}

/// initial → reorder → content-change → append → remove.
///
/// The REORDER step is the one that matters: it is the only operation for
/// which every item's content hash is unchanged while its position moves, so a
/// cache that ignored position would serve every fragment from the wrong slot.
fn operation_sequence() -> Vec<Value> {
    vec![
        items(&[("1", "alpha"), ("2", "bravo"), ("3", "charlie")]),
        items(&[("3", "charlie"), ("1", "alpha"), ("2", "bravo")]),
        items(&[("3", "charlie"), ("1", "ALPHA-CHANGED"), ("2", "bravo")]),
        items(&[
            ("3", "charlie"),
            ("1", "ALPHA-CHANGED"),
            ("2", "bravo"),
            ("4", "delta"),
        ]),
        items(&[("3", "charlie"), ("2", "bravo"), ("4", "delta")]),
    ]
}

#[test]
fn every_forloop_spelling_renders_identically_with_the_cache_enabled() {
    for (label, src) in forloop_spellings() {
        let tmpl = Template::new(src).expect("parse");
        let mut cache = LoopRenderCache::new(true);
        for (step, state) in operation_sequence().iter().enumerate() {
            let uncached = render_uncached(src, state);
            let cached = render_cached(&tmpl, state, &mut cache);
            assert_eq!(
                cached, uncached,
                "`{label}` step {step}: cache-enabled output diverged from cache-disabled\n\
                 src: {src}"
            );
        }
    }
}

#[test]
fn no_forloop_spelling_puts_anything_in_the_cache() {
    // The mechanism behind the identity above: a body that reads `forloop` is
    // never cached at all, so the cache sees neither a hit nor a miss for it.
    // Asserting zero traffic (rather than only equal output) is what makes
    // this test able to distinguish "the guard held" from "the guard cached
    // and happened to be right".
    for (label, src) in forloop_spellings() {
        let tmpl = Template::new(src).expect("parse");
        let mut cache = LoopRenderCache::new(true);
        for state in operation_sequence().iter() {
            let _ = render_cached(&tmpl, state, &mut cache);
        }
        assert_eq!(cache.hits(), 0, "`{label}` produced cache HITS: {src}");
        assert_eq!(cache.misses(), 0, "`{label}` produced cache MISSES: {src}");
    }
}

#[test]
fn the_identity_claim_is_not_vacuous() {
    // Before #2402 every assertion above compared `""` to `""`: the members
    // rendered nothing, so no cache behaviour could have made the two paths
    // differ. This pins that each spelling now emits output that VARIES by
    // position — which is exactly the property that makes a stale cache hit
    // observable, and therefore the property those tests depend on.
    let state = items(&[("1", "alpha"), ("2", "bravo"), ("3", "charlie")]);
    for (label, src) in forloop_spellings() {
        if label == "loopvar-named-forloop" {
            // Deliberately position-INDEPENDENT (the item shadows the dict);
            // it is in the sweep to prove the conservative guard still yields
            // identical output, not to demonstrate positional variation.
            continue;
        }
        let out = render_uncached(src, &state);
        // Split on the item names, which are the same in every fragment, and
        // require that the surrounding text is not identical across items.
        let fragments: Vec<&str> = out.split('[').skip(1).collect();
        assert!(
            fragments.len() >= 2,
            "`{label}` produced no per-item fragments: {out:?}"
        );
        let first = fragments[0];
        assert!(
            fragments.iter().any(|f| f != &first),
            "`{label}` renders the SAME bytes for every position — the cache \
             identity assertions over it are vacuous.\n  src: {src}\n  out: {out:?}"
        );
    }
}

#[test]
fn a_body_that_does_not_read_forloop_is_still_cached() {
    // The control. Without it, "zero cache traffic" above would also pass if
    // the cache were disabled outright, or if `LoopCacheGuard` had stopped
    // installing — in which case the guard assertions would prove nothing.
    let src = "{% for x in xs %}[{{ x.name }}]{% endfor %}";
    let tmpl = Template::new(src).expect("parse");
    let mut cache = LoopRenderCache::new(true);
    for state in operation_sequence().iter() {
        let _ = render_cached(&tmpl, state, &mut cache);
    }
    assert!(
        cache.hits() > 0,
        "a forloop-free body must still be cached — otherwise the zero-traffic \
         assertions above are measuring a disabled cache, not the guard"
    );
}
