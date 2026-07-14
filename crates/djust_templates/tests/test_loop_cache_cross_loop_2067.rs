//! #2067 — cross-loop fragment corruption in the loop render cache.
//!
//! `content_hash` hashed only `(loop_var_name, item_value)` while
//! `LoopRenderCache.fragments` is ONE map shared across every `{% for %}` in
//! the template. Two sibling loops that reuse the same loop-variable name over
//! equal-content items therefore collided on identical hashes, and the second
//! loop silently rendered the FIRST loop's fragment.
//!
//! The fix folds the For-node BODY IDENTITY (the same `(ptr, len)` identity
//! the `cacheable_verdict` memo already uses — stable for a cached Template's
//! immutable AST, and `LoopRenderCache::clear()` already drops everything on
//! template change) into the content hash, isolating each For-node's keyspace.

use djust_core::{Context, Value};
use djust_templates::loop_cache::{LoopCacheGuard, LoopRenderCache};
use djust_templates::Template;
use std::collections::HashMap;

fn obj(pairs: &[(&str, &str)]) -> Value {
    let mut m = HashMap::new();
    for (k, v) in pairs {
        m.insert((*k).to_string(), Value::String((*v).to_string()));
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

/// Parse-once render helper: production holds ONE cached `Template` across
/// renders (and clears the cache on template change), and since #2067 the
/// content hash folds the For-node body identity — so the template must stay
/// stable for cross-render hit assertions to be meaningful.
fn render_cached(tmpl: &Template, items_value: &Value, cache: &mut LoopRenderCache) -> String {
    cache.begin_render();
    let html = {
        let _guard = LoopCacheGuard::install(cache);
        let mut ctx = Context::new();
        ctx.set("items".to_string(), items_value.clone());
        tmpl.render(&ctx).expect("render")
    };
    cache.prune();
    html
}

/// The #2067 reproducer: two sibling loops, SAME loop-variable name, SAME item
/// content, DIFFERENT bodies. Pre-fix the second loop rendered `A[...]`
/// fragments instead of `B[...]`.
#[test]
fn sibling_loops_with_same_var_and_items_do_not_cross_render() {
    let src = "{% for x in items %}A[{{ x.name }}]{% endfor %}\
               {% for x in items %}B[{{ x.name }}]{% endfor %}";
    let data = items(&[("1", "one"), ("2", "two")]);

    let tmpl = Template::new(src).expect("parse");
    let mut cache = LoopRenderCache::default();
    cache.set_enabled(true);

    let html = render_cached(&tmpl, &data, &mut cache);
    assert!(
        html.contains("B[one]") && html.contains("B[two]"),
        "#2067: second loop must render its OWN body, got: {html}"
    );
    assert_eq!(
        html.matches("A[").count(),
        2,
        "first loop's fragments leaked into the second loop: {html}"
    );

    // The corruption persisted across renders pre-fix (all-hit on the collided
    // keys) — assert a second render is also correct.
    let html2 = render_cached(&tmpl, &data, &mut cache);
    assert_eq!(html, html2, "second render must be identical and correct");
}

/// Same-var sibling loops with IDENTICAL bodies stay correct too (post-fix each
/// For-node owns its keyspace; correctness over cross-loop sharing).
#[test]
fn identical_sibling_loops_render_identically() {
    let src = "{% for x in items %}<i>{{ x.name }}</i>{% endfor %}\
               {% for x in items %}<i>{{ x.name }}</i>{% endfor %}";
    let data = items(&[("1", "one"), ("2", "two")]);

    let tmpl = Template::new(src).expect("parse");
    let mut cache = LoopRenderCache::default();
    cache.set_enabled(true);
    let cached = render_cached(&tmpl, &data, &mut cache);

    let mut ctx = Context::new();
    ctx.set("items".to_string(), data.clone());
    let uncached = tmpl.render(&ctx).expect("render");

    assert_eq!(cached, uncached, "cache ON must be byte-identical to OFF");
}

/// The per-loop reorder win is preserved: a pure reorder within ONE loop is
/// still all cache hits after the body-identity fold.
#[test]
fn reorder_within_one_loop_is_still_all_hits() {
    let src = "{% for x in items %}<li>{{ x.name }}</li>{% endfor %}";
    let tmpl = Template::new(src).expect("parse");
    let mut cache = LoopRenderCache::default();
    cache.set_enabled(true);

    let first = items(&[("1", "one"), ("2", "two"), ("3", "three")]);
    render_cached(&tmpl, &first, &mut cache);
    // Counters are per-render (reset in `begin_render`), matching the
    // `reorder_of_plain_list_is_all_hits` idiom in the #1967 suite.
    assert_eq!(
        (cache.hits(), cache.misses()),
        (0, 3),
        "cold render: all misses"
    );

    let reordered = items(&[("3", "three"), ("1", "one"), ("2", "two")]);
    render_cached(&tmpl, &reordered, &mut cache);
    assert_eq!(
        cache.hits(),
        3,
        "reorder must be all hits (the cache's raison d'être)"
    );
    assert_eq!(cache.misses(), 0, "reorder must add no misses");
}
