//! Correctness + behavior tests for the per-item loop render cache (#1967).
//!
//! The load-bearing requirement for a hot-path render cache is that it is
//! OUTPUT-IDENTICAL to the uncached path. This suite proves byte-identity
//! between cache-ENABLED and cache-DISABLED renders across a battery of
//! templates (plain list, `forloop.counter` body, `dj-if` body, nested loops,
//! tuple unpacking) and operations (initial, reorder, content-change, append,
//! remove) — and includes the two gate-off tests (#1468) that prove the
//! forloop guard and the persistence are both load-bearing.

use djust_core::{Context, Value};
use djust_templates::loop_cache::{
    body_is_position_dependent, content_hash, LoopCacheGuard, LoopRenderCache,
};
use djust_templates::Template;
use std::collections::HashMap;

/// Build a `Value::Object` from `(key, string-value)` pairs.
fn obj(pairs: &[(&str, &str)]) -> Value {
    let mut m = HashMap::new();
    for (k, v) in pairs {
        m.insert(k.to_string(), Value::String(v.to_string()));
    }
    Value::Object(m)
}

/// Build a list of item objects, each `{id, name}`.
fn items(rows: &[(&str, &str)]) -> Value {
    Value::List(
        rows.iter()
            .map(|(id, name)| obj(&[("id", id), ("name", name)]))
            .collect(),
    )
}

/// Render `template_src` against a context holding `items` under `var`, with NO
/// loop cache installed (the baseline / uncached path).
fn render(template_src: &str, var: &str, items_value: &Value, _cache: Option<()>) -> String {
    let tmpl = Template::new(template_src).expect("parse");
    let mut ctx = Context::new();
    ctx.set(var.to_string(), items_value.clone());
    tmpl.render(&ctx).expect("render")
}

/// Render WITH a (possibly persistent) cache, applying prune after the render.
fn render_cached(
    template_src: &str,
    var: &str,
    items_value: &Value,
    cache: &mut LoopRenderCache,
) -> String {
    cache.begin_render();
    let html = {
        let _guard = LoopCacheGuard::install(cache);
        let tmpl = Template::new(template_src).expect("parse");
        let mut ctx = Context::new();
        ctx.set(var.to_string(), items_value.clone());
        tmpl.render(&ctx).expect("render")
    };
    cache.prune();
    html
}

// ---------------------------------------------------------------------------
// Battery: cache-ENABLED output must be BYTE-IDENTICAL to cache-DISABLED, for
// every template × every operation.
// ---------------------------------------------------------------------------

/// All the templates in the battery. The `forloop.counter` and `dj-if` ones are
/// the position-dependent cases the guard must DISABLE caching for — yet the
/// output must STILL be byte-identical (proving the guard preserves correctness).
fn battery_templates() -> Vec<(&'static str, &'static str)> {
    vec![
        ("plain", "<ul>{% for x in xs %}<li>{{ x.name }}</li>{% endfor %}</ul>"),
        (
            "forloop_counter",
            "<ul>{% for x in xs %}<li>{{ forloop.counter }}:{{ x.name }}</li>{% endfor %}</ul>",
        ),
        (
            "dj_if_body",
            "<ul>{% for x in xs %}<li>{% if x.name %}{{ x.name }}{% endif %}</li>{% endfor %}</ul>",
        ),
        (
            "cycle_body",
            "<ul>{% for x in xs %}<li class=\"{% cycle 'odd' 'even' %}\">{{ x.name }}</li>{% endfor %}</ul>",
        ),
    ]
}

/// The standard sequence of item states each template is exercised against.
fn operation_sequence() -> Vec<Value> {
    vec![
        // initial
        items(&[("1", "alpha"), ("2", "bravo"), ("3", "charlie")]),
        // reorder (same content, shuffled)
        items(&[("3", "charlie"), ("1", "alpha"), ("2", "bravo")]),
        // content-change of one
        items(&[("3", "charlie"), ("1", "ALPHA-CHANGED"), ("2", "bravo")]),
        // append
        items(&[
            ("3", "charlie"),
            ("1", "ALPHA-CHANGED"),
            ("2", "bravo"),
            ("4", "delta"),
        ]),
        // remove
        items(&[("3", "charlie"), ("2", "bravo"), ("4", "delta")]),
    ]
}

#[test]
fn cache_enabled_output_is_byte_identical_to_disabled() {
    for (label, src) in battery_templates() {
        // A persistent enabled cache across the whole sequence (the real
        // usage shape — persistence is what gives reorder its win).
        let mut cache = LoopRenderCache::new(true);
        for (step, state) in operation_sequence().iter().enumerate() {
            let uncached = render(src, "xs", state, None);
            let cached = render_cached(src, "xs", state, &mut cache);
            assert_eq!(
                cached, uncached,
                "template `{label}` step {step}: cache-enabled output diverged from cache-disabled",
            );
        }
    }
}

#[test]
fn nested_loops_byte_identical() {
    // Nested loop body is treated as position-dependent (the inner loop
    // composes the dj-if loop path from the outer index), so caching is
    // disabled — but output must still match.
    let src = "{% for row in rows %}<div>{% for c in row.cells %}<span>{{ c.v }}</span>{% endfor %}</div>{% endfor %}";
    let mut m1 = HashMap::new();
    m1.insert(
        "cells".to_string(),
        Value::List(vec![obj(&[("v", "a")]), obj(&[("v", "b")])]),
    );
    let mut m2 = HashMap::new();
    m2.insert("cells".to_string(), Value::List(vec![obj(&[("v", "c")])]));
    let rows = Value::List(vec![Value::Object(m1), Value::Object(m2)]);

    let mut cache = LoopRenderCache::new(true);
    let uncached = render(src, "rows", &rows, None);
    let cached = render_cached(src, "rows", &rows, &mut cache);
    assert_eq!(cached, uncached);
}

#[test]
fn tuple_unpacking_byte_identical() {
    // {% for k, v in pairs %} — a position-independent body, should cache.
    let src = "{% for k, v in pairs %}<i>{{ k }}={{ v }}</i>{% endfor %}";
    let pairs = Value::List(vec![
        Value::List(vec![Value::String("a".into()), Value::String("1".into())]),
        Value::List(vec![Value::String("b".into()), Value::String("2".into())]),
    ]);
    // reorder
    let pairs2 = Value::List(vec![
        Value::List(vec![Value::String("b".into()), Value::String("2".into())]),
        Value::List(vec![Value::String("a".into()), Value::String("1".into())]),
    ]);
    let mut cache = LoopRenderCache::new(true);
    assert_eq!(
        render_cached(src, "pairs", &pairs, &mut cache),
        render(src, "pairs", &pairs, None)
    );
    assert_eq!(
        render_cached(src, "pairs", &pairs2, &mut cache),
        render(src, "pairs", &pairs2, None)
    );
}

// ---------------------------------------------------------------------------
// Behavior: a pure reorder of a position-INDEPENDENT body yields cache HITS
// (zero re-renders); a content-change of one item costs exactly one miss.
// ---------------------------------------------------------------------------

#[test]
fn reorder_of_plain_list_is_all_hits() {
    let src = "<ul>{% for x in xs %}<li>{{ x.name }}</li>{% endfor %}</ul>";
    let initial = items(&[("1", "alpha"), ("2", "bravo"), ("3", "charlie")]);
    let reordered = items(&[("3", "charlie"), ("1", "alpha"), ("2", "bravo")]);

    let mut cache = LoopRenderCache::new(true);

    // First render: 3 misses (cold cache), 0 hits.
    cache.begin_render();
    {
        let _g = LoopCacheGuard::install(&mut cache);
        let tmpl = Template::new(src).unwrap();
        let mut ctx = Context::new();
        ctx.set("xs".into(), initial.clone());
        let _ = tmpl.render(&ctx).unwrap();
    }
    assert_eq!(cache.misses(), 3, "cold render should miss all 3 items");
    assert_eq!(cache.hits(), 0);
    cache.prune();

    // Reorder render: 3 HITS, 0 misses — no item subtree re-rendered.
    cache.begin_render();
    {
        let _g = LoopCacheGuard::install(&mut cache);
        let tmpl = Template::new(src).unwrap();
        let mut ctx = Context::new();
        ctx.set("xs".into(), reordered.clone());
        let _ = tmpl.render(&ctx).unwrap();
    }
    assert_eq!(
        cache.hits(),
        3,
        "a pure reorder must reuse every cached fragment (O(changed)=0 re-renders)"
    );
    assert_eq!(
        cache.misses(),
        0,
        "a pure reorder must not re-render any item"
    );
}

#[test]
fn content_change_of_one_item_is_one_miss() {
    let src = "<ul>{% for x in xs %}<li>{{ x.name }}</li>{% endfor %}</ul>";
    let initial = items(&[("1", "alpha"), ("2", "bravo"), ("3", "charlie")]);
    let changed = items(&[("1", "alpha"), ("2", "CHANGED"), ("3", "charlie")]);

    let mut cache = LoopRenderCache::new(true);
    let _ = render_cached(src, "xs", &initial, &mut cache);

    cache.begin_render();
    {
        let _g = LoopCacheGuard::install(&mut cache);
        let tmpl = Template::new(src).unwrap();
        let mut ctx = Context::new();
        ctx.set("xs".into(), changed.clone());
        let _ = tmpl.render(&ctx).unwrap();
    }
    assert_eq!(cache.misses(), 1, "only the changed item re-renders");
    assert_eq!(cache.hits(), 2, "the two unchanged items are reused");
}

#[test]
fn append_only_misses_the_new_item() {
    let src = "<ul>{% for x in xs %}<li>{{ x.name }}</li>{% endfor %}</ul>";
    let initial = items(&[("1", "alpha"), ("2", "bravo")]);
    let appended = items(&[("1", "alpha"), ("2", "bravo"), ("3", "charlie")]);

    let mut cache = LoopRenderCache::new(true);
    let _ = render_cached(src, "xs", &initial, &mut cache);

    cache.begin_render();
    {
        let _g = LoopCacheGuard::install(&mut cache);
        let tmpl = Template::new(src).unwrap();
        let mut ctx = Context::new();
        ctx.set("xs".into(), appended.clone());
        let _ = tmpl.render(&ctx).unwrap();
    }
    assert_eq!(cache.misses(), 1, "only the appended item misses");
    assert_eq!(cache.hits(), 2);
}

#[test]
fn prune_bounds_cache_to_current_items() {
    let src = "<ul>{% for x in xs %}<li>{{ x.name }}</li>{% endfor %}</ul>";
    let initial = items(&[("1", "alpha"), ("2", "bravo"), ("3", "charlie")]);
    let removed = items(&[("1", "alpha")]); // drop bravo + charlie

    let mut cache = LoopRenderCache::new(true);
    let _ = render_cached(src, "xs", &initial, &mut cache);
    assert_eq!(cache.len(), 3, "3 fragments cached after initial render");

    let _ = render_cached(src, "xs", &removed, &mut cache);
    assert_eq!(
        cache.len(),
        1,
        "prune drops the 2 removed items; cache tracks current item count"
    );
}

// ---------------------------------------------------------------------------
// GATE-OFF #1 (#1468): the forloop/position guard is load-bearing.
//
// A `forloop.counter` body reordered with a cache that does NOT honor the
// guard would emit STALE counter positions. We prove the guard is required by
// asserting that WITH the guard, a reordered forloop.counter list still
// produces correct (uncached-identical) positions — AND that a body the guard
// flags is reported position-dependent (so caching is disabled for it).
// ---------------------------------------------------------------------------

#[test]
fn gate_off_forloop_guard_is_load_bearing() {
    // The dj-if body is position-dependent: WITHOUT the guard, a reorder would
    // reuse a fragment rendered at a different index, emitting a stale
    // `if-<hash>-N-<index>` marker id. WITH the guard, caching is disabled for
    // this body and the output stays correct.
    let src =
        "<ul>{% for x in xs %}<li>{% if x.name %}{{ x.name }}{% endif %}</li>{% endfor %}</ul>";
    let initial = items(&[("1", "alpha"), ("2", "bravo"), ("3", "charlie")]);
    let reordered = items(&[("3", "charlie"), ("1", "alpha"), ("2", "bravo")]);

    let mut cache = LoopRenderCache::new(true);
    let _ = render_cached(src, "xs", &initial, &mut cache);
    let cached = render_cached(src, "xs", &reordered, &mut cache);
    let uncached = render(src, "xs", &reordered, None);
    assert_eq!(
        cached, uncached,
        "guard must keep dj-if marker positions correct on reorder",
    );

    // The guard MUST report a NON-zero set of caching events to be load-bearing:
    // for the dj-if body, caching is disabled, so the cache sees zero hits AND
    // zero misses (the body never touches the cache).
    cache.begin_render();
    {
        let _g = LoopCacheGuard::install(&mut cache);
        let tmpl = Template::new(src).unwrap();
        let mut ctx = Context::new();
        ctx.set("xs".into(), reordered.clone());
        let _ = tmpl.render(&ctx).unwrap();
    }
    assert_eq!(
        cache.hits(),
        0,
        "position-dependent body must NOT be cached (0 hits)"
    );
    assert_eq!(
        cache.misses(),
        0,
        "position-dependent body must NOT touch the cache (0 misses)"
    );
}

/// Directly assert the position-dependence verdict the guard relies on. If a
/// future change removed the `If`/`Cycle`/`For`/`forloop` arms from
/// `body_is_position_dependent`, THESE assertions go red — the gate-off proof
/// that the guard exists and is consulted.
#[test]
fn gate_off_position_dependence_classification() {
    let pos_dep = [
        "<ul>{% for x in xs %}<li>{{ forloop.counter }}{{ x.name }}</li>{% endfor %}</ul>",
        "<ul>{% for x in xs %}<li>{% if x.name %}y{% endif %}</li>{% endfor %}</ul>",
        "<ul>{% for x in xs %}<li class=\"{% cycle 'a' 'b' %}\">{{ x.name }}</li>{% endfor %}</ul>",
        "{% for r in rows %}{% for c in r.cells %}{{ c }}{% endfor %}{% endfor %}",
    ];
    for src in pos_dep {
        let body = for_body(src);
        assert!(
            body_is_position_dependent(&body),
            "expected position-dependent body for `{src}`",
        );
    }

    // The plain `{{ x.name }}` body is position-INDEPENDENT (cacheable).
    let plain = for_body("<ul>{% for x in xs %}<li>{{ x.name }}</li>{% endfor %}</ul>");
    assert!(
        !body_is_position_dependent(&plain),
        "plain `{{{{ x.name }}}}` body must be cacheable",
    );
}

/// Extract the For-loop body nodes from a template source for direct
/// classification testing.
fn for_body(src: &str) -> Vec<djust_templates::parser::Node> {
    use djust_templates::lexer::tokenize;
    use djust_templates::parser::{parse, Node};
    let tokens = tokenize(src).expect("tokenize");
    let nodes = parse(&tokens).expect("parse");
    fn find_for(nodes: &[Node]) -> Option<Vec<Node>> {
        for n in nodes {
            match n {
                Node::For { nodes, .. } => return Some(nodes.clone()),
                Node::Block { nodes, .. }
                | Node::With { nodes, .. }
                | Node::Spaceless { nodes, .. } => {
                    if let Some(b) = find_for(nodes) {
                        return Some(b);
                    }
                }
                _ => {}
            }
        }
        None
    }
    find_for(&nodes).expect("template must contain a {% for %}")
}

// ---------------------------------------------------------------------------
// GATE-OFF #2 (#1468): persistence across renders is load-bearing.
//
// A per-render cache (begun fresh each render) would give a reorder ZERO hits
// because each item appears once per render. The persistent cache yields hits.
// We prove persistence is required by showing that a FRESH cache per render
// produces 0 hits on the reorder, while the persistent cache produces 3.
// ---------------------------------------------------------------------------

#[test]
fn gate_off_persistence_is_load_bearing() {
    let src = "<ul>{% for x in xs %}<li>{{ x.name }}</li>{% endfor %}</ul>";
    let initial = items(&[("1", "alpha"), ("2", "bravo"), ("3", "charlie")]);
    let reordered = items(&[("3", "charlie"), ("1", "alpha"), ("2", "bravo")]);

    // PERSISTENT cache (correct design): reorder = 3 hits.
    let mut persistent = LoopRenderCache::new(true);
    let _ = render_cached(src, "xs", &initial, &mut persistent);
    persistent.begin_render();
    {
        let _g = LoopCacheGuard::install(&mut persistent);
        let tmpl = Template::new(src).unwrap();
        let mut ctx = Context::new();
        ctx.set("xs".into(), reordered.clone());
        let _ = tmpl.render(&ctx).unwrap();
    }
    assert_eq!(
        persistent.hits(),
        3,
        "persistent cache must hit on reorder — this is the whole win"
    );

    // PER-RENDER cache (the wrong design): a fresh cache every render. Simulate
    // by using a brand-new cache for the reorder render. It has never seen the
    // items → 0 hits, 3 misses. This is what we are NOT doing.
    let mut per_render = LoopRenderCache::new(true);
    per_render.begin_render();
    {
        let _g = LoopCacheGuard::install(&mut per_render);
        let tmpl = Template::new(src).unwrap();
        let mut ctx = Context::new();
        ctx.set("xs".into(), reordered.clone());
        let _ = tmpl.render(&ctx).unwrap();
    }
    assert_eq!(
        per_render.hits(),
        0,
        "a per-render (non-persistent) cache gives ZERO reorder benefit"
    );
    assert_eq!(per_render.misses(), 3);
}

// ---------------------------------------------------------------------------
// Disabled-cache path: byte-identical to no-cache AND zero cache activity.
// ---------------------------------------------------------------------------

#[test]
fn disabled_cache_is_inert() {
    let src = "<ul>{% for x in xs %}<li>{{ x.name }}</li>{% endfor %}</ul>";
    let state = items(&[("1", "alpha"), ("2", "bravo")]);

    let mut disabled = LoopRenderCache::new(false);
    let with_disabled = render_cached(src, "xs", &state, &mut disabled);
    let no_cache = render(src, "xs", &state, None);
    assert_eq!(with_disabled, no_cache);
    assert!(disabled.is_empty(), "disabled cache stores nothing");
    assert_eq!(disabled.hits(), 0);
    assert_eq!(disabled.misses(), 0);
}

// ---------------------------------------------------------------------------
// content_hash: distinct content → distinct hash; same content → same hash.
// ---------------------------------------------------------------------------

#[test]
fn content_hash_is_stable_and_distinguishing() {
    let a = obj(&[("id", "1"), ("name", "alpha")]);
    let a2 = obj(&[("id", "1"), ("name", "alpha")]);
    let b = obj(&[("id", "1"), ("name", "bravo")]);

    let ha = content_hash(&[("x", &a)]);
    let ha2 = content_hash(&[("x", &a2)]);
    let hb = content_hash(&[("x", &b)]);

    assert_eq!(ha, ha2, "identical content must hash identically");
    assert_ne!(ha, hb, "different content must hash differently");

    // Variable name participates in the hash (no cross-loop collision).
    let hy = content_hash(&[("y", &a)]);
    assert_ne!(ha, hy, "different var name must hash differently");
}
