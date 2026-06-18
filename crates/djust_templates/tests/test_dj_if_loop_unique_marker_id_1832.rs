//! Regression tests for issue #1832 — `{% if %}` inside `{% for %}` must
//! emit a UNIQUE `<!--dj-if id="...">` open-marker id per loop iteration.
//!
//! Root cause: the parser assigns each `Node::If` a single compile-time
//! ordinal (`if-<hash>-N`). The `For` node renders the SAME `If` node once
//! per iteration, so before #1832 the id was DUPLICATED across every
//! iteration. On re-render, the VDOM differ emitted `MoveSubtree` patches
//! whose id matched N identical markers, which the client could not pair
//! ("close marker not found") — most patches failed and the recovery morph
//! dropped a row per toggle (a P0 data-loss bug).
//!
//! Fix: a per-iteration loop-path (`-<index>`, composing for nested loops
//! as `-<outer>-<inner>`) is threaded through the render context and
//! appended to the marker id at emission, so each rendered `{% if %}`
//! boundary inside a loop gets a unique-yet-stable id. An `{% if %}`
//! OUTSIDE any loop keeps the bare `if-<hash>-N` form (backward compatible).

use djust_core::{Context, Value};
use djust_templates::Template;
use std::collections::HashSet;

fn render(source: &str, ctx: &Context) -> String {
    let t = Template::new(source).expect("template should parse");
    t.render(ctx).expect("template should render")
}

/// All dj-if OPEN-marker ids in document order (path-suffix aware: matches
/// both bare `if-<8hex>-N` and looped `if-<8hex>-N-<path>`).
fn open_marker_ids(rendered: &str) -> Vec<String> {
    let re = regex::Regex::new(r#"<!--dj-if id="(if-[0-9a-f]{8}-[0-9-]+)"-->"#).expect("regex");
    re.captures_iter(rendered)
        .map(|c| c[1].to_string())
        .collect()
}

fn obj(pairs: &[(&str, Value)]) -> Value {
    let mut m = std::collections::HashMap::new();
    for (k, v) in pairs {
        m.insert((*k).to_string(), v.clone());
    }
    Value::Object(m)
}

/// Build a list of N items each with `show=true` and a distinct name.
fn rows(n: usize) -> Value {
    Value::List(
        (0..n)
            .map(|i| {
                obj(&[
                    ("show", Value::Bool(true)),
                    ("name", Value::Integer(i as i64)),
                ])
            })
            .collect(),
    )
}

#[test]
fn loop_if_marker_ids_are_unique_across_iterations() {
    let template =
        "{% for r in rows %}{% if r.show %}<div>{{ r.name }}</div>{% endif %}{% endfor %}";
    let mut c = Context::new();
    c.set("rows".to_string(), rows(16));

    let rendered = render(template, &c);
    let ids = open_marker_ids(&rendered);

    assert_eq!(ids.len(), 16, "expected one open marker per iteration");
    let unique: HashSet<&String> = ids.iter().collect();
    assert_eq!(
        unique.len(),
        ids.len(),
        "all dj-if marker ids must be unique across loop iterations; got duplicates in {ids:?}"
    );
}

#[test]
fn loop_if_marker_ids_are_byte_stable_across_renders() {
    // Stability matters: the differ pairs markers by id equality, so the
    // ids must NOT change when only non-loop state toggles between renders.
    let template =
        "{% for r in rows %}{% if r.show %}<div>{{ r.name }}</div>{% endif %}{% endfor %}";
    let mut c = Context::new();
    c.set("rows".to_string(), rows(8));

    let first = render(template, &c);
    let second = render(template, &c);
    assert_eq!(
        open_marker_ids(&first),
        open_marker_ids(&second),
        "looped dj-if ids must be byte-stable across renders"
    );
}

#[test]
fn if_outside_loop_keeps_bare_id() {
    // An {% if %} that is NOT inside any {% for %} must keep the bare
    // `if-<hash>-N` form (no loop-path suffix) — backward compatible.
    let template = "{% if show %}<div>x</div>{% endif %}";
    let c = {
        let mut c = Context::new();
        c.set("show".to_string(), Value::Bool(true));
        c
    };
    let rendered = render(template, &c);
    let ids = open_marker_ids(&rendered);
    assert_eq!(ids.len(), 1);
    // Bare form: exactly `if-<8hex>-<digits>` with NO trailing `-<path>`.
    let bare = regex::Regex::new(r#"^if-[0-9a-f]{8}-\d+$"#).expect("regex");
    assert!(
        bare.is_match(&ids[0]),
        "if outside a loop must keep bare id form, got {:?}",
        ids[0]
    );
}

#[test]
fn nested_loop_if_marker_ids_compose_and_are_unique() {
    // Outer loop over groups, inner loop over members; the {% if %} lives
    // in the inner body. The loop path must compose (`-<outer>-<inner>`),
    // yielding a unique id for every (outer, inner) pair.
    let template = "{% for g in groups %}\
{% for m in g.members %}{% if m.show %}<div>{{ m.name }}</div>{% endif %}{% endfor %}\
{% endfor %}";

    let group = |members: usize| {
        obj(&[(
            "members",
            Value::List(
                (0..members)
                    .map(|i| {
                        obj(&[
                            ("show", Value::Bool(true)),
                            ("name", Value::Integer(i as i64)),
                        ])
                    })
                    .collect(),
            ),
        )])
    };

    let mut c = Context::new();
    // 3 groups: 2, 3, and 2 members -> 7 if-markers total, all unique.
    c.set(
        "groups".to_string(),
        Value::List(vec![group(2), group(3), group(2)]),
    );

    let rendered = render(template, &c);
    let ids = open_marker_ids(&rendered);

    assert_eq!(ids.len(), 7, "expected one marker per (group, member) pair");
    let unique: HashSet<&String> = ids.iter().collect();
    assert_eq!(
        unique.len(),
        ids.len(),
        "nested-loop dj-if marker ids must all be unique; got {ids:?}"
    );

    // Spot-check the composed path shape: the second group's third member
    // should carry a `-1-2` suffix (outer index 1, inner index 2).
    let composed = regex::Regex::new(r#"^if-[0-9a-f]{8}-\d+-1-2$"#).expect("regex");
    assert!(
        ids.iter().any(|id| composed.is_match(id)),
        "expected a composed `-1-2` nested path in {ids:?}"
    );
}

#[test]
fn reversed_loop_keeps_index_based_ids_stable() {
    // `reversed` iterates items in reverse but the id uses the ORIGINAL
    // item index, so the same item keeps the same id whether reversed or
    // not — only the document ORDER of markers flips.
    let template = "{% for r in rows reversed %}\
{% if r.show %}<div>{{ r.name }}</div>{% endif %}{% endfor %}";
    let mut c = Context::new();
    c.set("rows".to_string(), rows(3));

    let rendered = render(template, &c);
    let ids = open_marker_ids(&rendered);

    assert_eq!(ids.len(), 3);
    let unique: HashSet<&String> = ids.iter().collect();
    assert_eq!(unique.len(), 3, "reversed-loop ids must still be unique");

    // Original indices 0,1,2 each appear exactly once (order is reversed
    // in the document but the index suffixes are the original indices).
    for idx in 0..3 {
        let suffix = format!("-{idx}");
        assert!(
            ids.iter().any(|id| id.ends_with(&suffix)),
            "expected an id ending with original index suffix {suffix:?} in {ids:?}"
        );
    }
}
