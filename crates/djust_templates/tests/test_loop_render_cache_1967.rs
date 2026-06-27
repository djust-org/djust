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
    body_is_cacheable, body_is_position_dependent, content_hash, LoopCacheGuard, LoopRenderCache,
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

// ---------------------------------------------------------------------------
// OUTER-CONTEXT bodies (#1967 review 🔴). A position-INDEPENDENT body can still
// read an OUTER-context variable (`{{ prefix }}`, `{% with l=flag %}`,
// `{% firstof flag x %}`). Outer context is constant WITHIN a render but NOT
// across renders, and the cache is PERSISTENT across renders — so without the
// dep-subset gate, a reorder after an outer-var change serves STALE fragments.
//
// The fix: such bodies are NON-cacheable (rendered fresh every time). This
// battery proves cache-ENABLED stays byte-identical to cache-DISABLED when an
// outer var changes across renders while the items are unchanged/reordered.
// (rc4 coverage-completeness: the missing axis was "body reads outer context".)
// ---------------------------------------------------------------------------

/// Render with NO cache, setting both an outer scalar var and the item list.
fn render_with_outer(
    src: &str,
    outer_name: &str,
    outer_val: &str,
    var: &str,
    items_value: &Value,
) -> String {
    let tmpl = Template::new(src).expect("parse");
    let mut ctx = Context::new();
    ctx.set(outer_name.to_string(), Value::String(outer_val.to_string()));
    ctx.set(var.to_string(), items_value.clone());
    tmpl.render(&ctx).expect("render")
}

/// Render WITH a (persistent) cache, setting both an outer scalar var and the
/// item list; prune after.
fn render_cached_with_outer(
    src: &str,
    outer_name: &str,
    outer_val: &str,
    var: &str,
    items_value: &Value,
    cache: &mut LoopRenderCache,
) -> String {
    cache.begin_render();
    let html = {
        let _g = LoopCacheGuard::install(cache);
        let tmpl = Template::new(src).expect("parse");
        let mut ctx = Context::new();
        ctx.set(outer_name.to_string(), Value::String(outer_val.to_string()));
        ctx.set(var.to_string(), items_value.clone());
        tmpl.render(&ctx).expect("render")
    };
    cache.prune();
    html
}

/// The three outer-context body shapes the review flagged. Each reads an outer
/// var (`prefix` / `flag`) in addition to (or instead of) the loop item.
fn outer_context_templates() -> Vec<(&'static str, &'static str)> {
    vec![
        // {{ prefix }} read directly in the body.
        (
            "prefix_var",
            "<ul>{% for x in xs %}<li>{{ prefix }}-{{ x.name }}</li>{% endfor %}</ul>",
        ),
        // {% with label=flag %} binds an outer var into a body-local name.
        (
            "with_flag",
            "<ul>{% for x in xs %}{% with label=flag %}<li>{{ label }}:{{ x.name }}</li>{% endwith %}{% endfor %}</ul>",
        ),
        // {% firstof flag x.name %} — falls back to the item, but reads `flag`.
        (
            "firstof_flag",
            "<ul>{% for x in xs %}<li>{% firstof flag x.name %}</li>{% endfor %}</ul>",
        ),
    ]
}

#[test]
fn outer_context_change_is_byte_identical_with_cache() {
    // The list is REORDERED across the two renders while the outer var changes
    // A -> B. WITHOUT the dep-subset gate, the cache (persistent) would serve
    // the stale `A-...` fragments on the reorder render (cache hits) — wrong.
    // WITH the gate, the body is non-cacheable, so it renders fresh = correct.
    let initial = items(&[("1", "alpha"), ("2", "bravo"), ("3", "charlie")]);
    let reordered = items(&[("3", "charlie"), ("1", "alpha"), ("2", "bravo")]);

    for (label, src) in outer_context_templates() {
        // Each template reads a single outer var: `prefix` for prefix_var,
        // `flag` for the with/firstof shapes.
        let outer_name = if label == "prefix_var" {
            "prefix"
        } else {
            "flag"
        };

        let mut cache = LoopRenderCache::new(true);
        // Render 1: outer = "A".
        let cached_a = render_cached_with_outer(src, outer_name, "A", "xs", &initial, &mut cache);
        let uncached_a = render_with_outer(src, outer_name, "A", "xs", &initial);
        assert_eq!(cached_a, uncached_a, "template `{label}` render-1 diverged");

        // Render 2: outer flips A -> B AND the list is reordered.
        let cached_b = render_cached_with_outer(src, outer_name, "B", "xs", &reordered, &mut cache);
        let uncached_b = render_with_outer(src, outer_name, "B", "xs", &reordered);
        assert_eq!(
            cached_b, uncached_b,
            "template `{label}` render-2 (outer A->B + reorder): cache served STALE outer value",
        );
        // The correct render-2 must contain the NEW outer value "B", never "A".
        assert!(
            cached_b.contains('B') || label == "firstof_flag",
            "template `{label}` render-2 must reflect new outer value B; got: {cached_b}",
        );
        assert!(
            !cached_b.contains("A-") && !cached_b.contains("A:"),
            "template `{label}` render-2 must NOT contain stale outer value A; got: {cached_b}",
        );
    }
}

#[test]
fn outer_context_bodies_are_non_cacheable() {
    // Direct classification: each outer-context body is NON-cacheable for var
    // names `["x"]` (the loop var), because it reads `prefix`/`flag` ∉ {x}.
    for (label, src) in outer_context_templates() {
        let body = for_body(src);
        assert!(
            !body_is_cacheable(&body, &["x".to_string()]),
            "outer-context body `{label}` must be NON-cacheable (reads outer var)",
        );
        // Sanity: these bodies are NOT position-dependent — they pass gate 1 and
        // are caught ONLY by the outer-context gate (gate 2). This pins that the
        // new gate, not the old one, is what disables them.
        assert!(
            !body_is_position_dependent(&body),
            "outer-context body `{label}` is position-INDEPENDENT; gate 2 must be the one disabling it",
        );
    }
}

#[test]
fn outer_context_body_never_hits_cache() {
    // Behavioral: a body reading outer context produces ZERO cache hits AND zero
    // misses (it never touches the cache), even on a pure reorder where an
    // item-only body would be all hits.
    let src = "<ul>{% for x in xs %}<li>{{ prefix }}-{{ x.name }}</li>{% endfor %}</ul>";
    let initial = items(&[("1", "alpha"), ("2", "bravo"), ("3", "charlie")]);
    let reordered = items(&[("3", "charlie"), ("1", "alpha"), ("2", "bravo")]);

    let mut cache = LoopRenderCache::new(true);
    let _ = render_cached_with_outer(src, "prefix", "A", "xs", &initial, &mut cache);
    cache.begin_render();
    {
        let _g = LoopCacheGuard::install(&mut cache);
        let tmpl = Template::new(src).unwrap();
        let mut ctx = Context::new();
        ctx.set("prefix".into(), Value::String("A".into()));
        ctx.set("xs".into(), reordered.clone());
        let _ = tmpl.render(&ctx).unwrap();
    }
    assert_eq!(
        cache.hits(),
        0,
        "outer-context body must NOT be cached (0 hits)"
    );
    assert_eq!(
        cache.misses(),
        0,
        "outer-context body must NOT touch the cache (0 misses)"
    );
}

// ---------------------------------------------------------------------------
// POSITIVE CONTROL: an item-ONLY body (`{{ x.* }}` only, no outer context) is
// STILL cacheable — a reorder yields cache HITS. The dep-subset gate narrows
// the cacheable surface to item-only bodies WITHOUT killing the win for them.
// ---------------------------------------------------------------------------

#[test]
fn item_only_body_still_caches_and_reorder_hits() {
    // A body reading only `x.name` (root `x` == the loop var) is cacheable.
    let src = "<ul>{% for x in xs %}<li>{{ x.name }}</li>{% endfor %}</ul>";
    let body = for_body(src);
    assert!(
        body_is_cacheable(&body, &["x".to_string()]),
        "item-only body must remain cacheable — the win must survive the gate",
    );

    let initial = items(&[("1", "alpha"), ("2", "bravo"), ("3", "charlie")]);
    let reordered = items(&[("3", "charlie"), ("1", "alpha"), ("2", "bravo")]);

    let mut cache = LoopRenderCache::new(true);
    let _ = render_cached(src, "xs", &initial, &mut cache);
    cache.begin_render();
    {
        let _g = LoopCacheGuard::install(&mut cache);
        let tmpl = Template::new(src).unwrap();
        let mut ctx = Context::new();
        ctx.set("xs".into(), reordered.clone());
        let _ = tmpl.render(&ctx).unwrap();
    }
    assert!(
        cache.hits() > 0,
        "item-only body reorder must still hit the cache (the win survives); got {} hits",
        cache.hits()
    );
    assert_eq!(
        cache.hits(),
        3,
        "a pure reorder of an item-only body is all hits"
    );
    assert_eq!(cache.misses(), 0);
}

// ---------------------------------------------------------------------------
// GATE-OFF #3 (#1468): the outer-context dep-subset gate is load-bearing.
//
// This test reproduces the EXACT review-cited bug shape and asserts the cached
// render equals the uncached render. If the dep-subset check in
// `body_is_cacheable` were reverted (back to `!body_is_position_dependent`),
// the `{{ prefix }}` body would be (wrongly) deemed cacheable, the reorder
// render would serve the stale `A-...` fragments, and this assertion goes RED.
// We pin that by also asserting, via `body_is_cacheable`, that the gate's
// decision is the one that matters here (position-dependence alone is false).
// ---------------------------------------------------------------------------

#[test]
fn gate_off_outer_context_subset_check_is_load_bearing() {
    let src = "<ul>{% for x in xs %}<li>{{ prefix }}-{{ x.name }}</li>{% endfor %}</ul>";
    let initial = items(&[("1", "alpha"), ("2", "bravo"), ("3", "charlie")]);
    let reordered = items(&[("3", "charlie"), ("1", "alpha"), ("2", "bravo")]);

    let mut cache = LoopRenderCache::new(true);
    // Render 1: prefix = "A".
    let _ = render_cached_with_outer(src, "prefix", "A", "xs", &initial, &mut cache);
    // Render 2: prefix flips to "B" AND the list reorders.
    let cached = render_cached_with_outer(src, "prefix", "B", "xs", &reordered, &mut cache);
    let uncached = render_with_outer(src, "prefix", "B", "xs", &reordered);

    // The load-bearing assertion: WITHOUT the gate, `cached` would be the stale
    // "A-..." reorder served from the persistent cache; WITH the gate it is the
    // fresh "B-..." render. They must match the uncached (correct) render.
    assert_eq!(
        cached, uncached,
        "GATE-OFF: outer-context dep-subset gate must keep cached==uncached after prefix A->B + reorder",
    );
    assert!(
        cached.contains("B-charlie") && cached.contains("B-alpha"),
        "render-2 must use the NEW prefix B for every item; got: {cached}",
    );
    assert!(
        !cached.contains("A-"),
        "render-2 must contain NO stale `A-` fragment; got: {cached}",
    );

    // Pin that the OUTER-CONTEXT gate (not the position-dependence gate) is the
    // one disabling this body — so reverting JUST the dep-subset check breaks it.
    let body = for_body(src);
    assert!(
        !body_is_position_dependent(&body),
        "this body is position-INDEPENDENT — only the dep-subset gate disables it",
    );
    assert!(
        !body_is_cacheable(&body, &["x".to_string()]),
        "this body must be non-cacheable via the outer-context (dep-subset) gate",
    );
}

// ===========================================================================
// #1970 — parsed-subtree cache: foster-safe gate + placeholder + manifest.
//
// The parse cache reuses the SAME content-hash key + the SAME two cacheability
// gates as the render cache; these tests cover the NEW surface the parse cache
// adds at the djust_templates layer. The end-to-end byte-identity (ON==OFF
// through render_with_diff) + the parse-count probe live in the Python suite;
// the splice + dj-id re-walk primitive is tested in
// crates/djust_vdom/tests/test_loop_parse_cache_1970.rs.
// ===========================================================================

mod parse_cache_1970 {
    use djust_templates::loop_cache::{
        item_html_is_foster_safe, render_loop_placeholder, LoopRenderCache,
    };

    /// The foster-safe gate accepts non-table/non-select item roots so a
    /// `<dj-pc>` placeholder emitted in their place survives html5ever.
    #[test]
    fn foster_safe_accepts_list_and_block_roots() {
        for html in [
            "<li>x</li>",
            "<div><span>x</span></div>",
            "<span>x</span>",
            "<p>x</p>",
            "<a href=\"#\">x</a>",
            "  <li>leading whitespace ok</li>",
            "<LI>uppercase tag ok</LI>",
        ] {
            assert!(
                item_html_is_foster_safe(html),
                "expected foster-SAFE for: {html}",
            );
        }
    }

    /// The gate REJECTS table/select-family roots — emitting a placeholder there
    /// would be foster-parented out of the container (total structure loss).
    #[test]
    fn foster_safe_rejects_table_and_select_roots() {
        for html in [
            "<tr><td>x</td></tr>",
            "<td>x</td>",
            "<th>x</th>",
            "<thead><tr><td>x</td></tr></thead>",
            "<tbody><tr><td>x</td></tr></tbody>",
            "<tfoot><tr><td>x</td></tr></tfoot>",
            "<caption>x</caption>",
            "<colgroup><col></colgroup>",
            "<col>",
            "<option>x</option>",
            "<optgroup><option>x</option></optgroup>",
        ] {
            assert!(
                !item_html_is_foster_safe(html),
                "expected foster-UNSAFE for: {html}",
            );
        }
    }

    /// A `<table>` item root IS foster-safe — `<table>` is valid flow content in
    /// any non-table container (e.g. `<div>{% for %}<table>…`), so a sibling
    /// `<dj-pc>` survives. (Only the table-INTERIOR tags are unsafe.)
    #[test]
    fn foster_safe_accepts_table_root() {
        assert!(item_html_is_foster_safe(
            "<table><tr><td>x</td></tr></table>"
        ));
    }

    /// An item with no leading element (pure text / leading comment) is NOT
    /// eligible (conservative — only a clean single leading element splices).
    #[test]
    fn foster_safe_rejects_textual_roots() {
        assert!(!item_html_is_foster_safe("just text"));
        assert!(!item_html_is_foster_safe("   "));
        assert!(!item_html_is_foster_safe(""));
        assert!(!item_html_is_foster_safe("<!-- c --><li>x</li>"));
    }

    /// The placeholder round-trips the hash as lowercase hex and carries the
    /// per-render nonce in the tag name: `<dj-pc-<nonce> h="<hash>">` (#1970
    /// security: the nonce makes the sentinel unforgeable by `|safe` content).
    #[test]
    fn placeholder_emits_nonce_tag_and_hex_hash() {
        // nonce 0xab, hash 0
        assert_eq!(
            render_loop_placeholder(0, 0xab),
            "<dj-pc-ab h=\"0\"></dj-pc-ab>"
        );
        assert_eq!(
            render_loop_placeholder(255, 0xab),
            "<dj-pc-ab h=\"ff\"></dj-pc-ab>"
        );
        assert_eq!(
            render_loop_placeholder(0xDEADBEEF, 0x1234),
            "<dj-pc-1234 h=\"deadbeef\"></dj-pc-1234>"
        );
        // The composed tag carries the prefix + nonce.
        assert_eq!(
            djust_templates::loop_cache::placeholder_tag(0xff),
            "dj-pc-ff"
        );
    }

    /// SECURITY (#1970): a loop item whose rendered HTML embeds a literal
    /// `<dj-pc ...>` (e.g. via `|safe`/`mark_safe`) is NOT parse-cache-eligible
    /// — `item_html_is_foster_safe` rejects it so no placeholder is ever emitted
    /// for it, and (belt-and-braces) the nonce-tagged sentinel wouldn't match it
    /// anyway. This is the gate that defends the byte-identity guarantee against
    /// the sentinel-collision attack.
    #[test]
    fn item_with_literal_sentinel_is_ineligible() {
        assert!(!item_html_is_foster_safe(
            "<li><dj-pc h=\"ffff\"></dj-pc>X</li>"
        ));
        assert!(!item_html_is_foster_safe("<div>see </dj-pc> here</div>"));
        assert!(!item_html_is_foster_safe(
            "<li><DJ-PC h=\"ab\"></DJ-PC></li>"
        ));
        // A normal item with no sentinel text stays eligible.
        assert!(item_html_is_foster_safe("<li>normal content</li>"));
    }

    /// Parse cache get/insert + manifest recording follow the same lifecycle as
    /// the render fragments (begin_render clears manifest + parse counters;
    /// has_parsed/get_parsed/insert_parsed; prune retains by seen-this-render).
    #[test]
    fn parse_cache_get_insert_and_manifest_lifecycle() {
        let mut c = LoopRenderCache::new(true);
        assert!(!c.has_parsed(42));
        assert!(c.get_parsed(42).is_none());

        c.insert_parsed(42, vec![djust_vdom::VNode::element("li")]);
        assert!(c.has_parsed(42));
        assert_eq!(c.parse_misses(), 1);
        let got = c.get_parsed(42).expect("hit");
        assert_eq!(got.len(), 1);
        assert_eq!(c.parse_hits(), 1);

        // Manifest recording + take.
        c.begin_render(); // clears manifest + parse counters
        assert_eq!(c.manifest_len(), 0);
        assert_eq!(c.parse_hits(), 0);
        c.record_manifest_item(1, true, "<li>a</li>".to_string());
        c.record_manifest_item(2, false, "<li>b</li>".to_string());
        assert_eq!(c.manifest_len(), 2);
        let m = c.take_manifest();
        assert_eq!(m.len(), 2);
        assert!(m[0].placeholder && !m[1].placeholder);
        assert_eq!(m[1].item_html, "<li>b</li>");
        assert_eq!(c.manifest_len(), 0, "take leaves the manifest empty");
    }

    /// Disabling the cache drops the parse cache + manifest (default-off memory
    /// invariant, mirroring the render fragments).
    #[test]
    fn disable_clears_parse_cache_and_manifest() {
        let mut c = LoopRenderCache::new(true);
        c.insert_parsed(7, vec![djust_vdom::VNode::element("li")]);
        c.record_manifest_item(7, true, "<li>x</li>".to_string());
        c.set_enabled(false);
        assert!(!c.has_parsed(7), "disable clears the parse cache");
        assert_eq!(c.manifest_len(), 0, "disable clears the manifest");
    }
}
