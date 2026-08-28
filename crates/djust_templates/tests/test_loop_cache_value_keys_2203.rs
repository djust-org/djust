//! The loop cache must key on what actually renders (#2203 review).
//!
//! `Display` became order- and type-sensitive in #2203, and `hash_value` was
//! updated to match — but gate-off found NONE of those updates were pinned:
//! reverting `loop_cache.rs` to its pre-#2203 shape left the whole suite green.
//! Per #1859 that protection was decorative, and a future tidy-up restoring the
//! "order-independent" sort would have been invisible to CI.
//!
//! Each case here fails if the corresponding `hash_value` arm regresses:
//!
//! | regression | symptom |
//! |---|---|
//! | dict keys hashed SORTED again | two dicts that render differently share a cache key |
//! | `None` reusing `Missing`'s tag | `""` and `"None"` share a cache key |
//! | `Tuple` reusing `List`'s tag | `"(1, 2)"` and `"[1, 2]"` share a cache key |
//!
//! Each is a STALE CACHE HIT SERVING WRONG OUTPUT — the failure mode the
//! #2203 CHANGELOG describes, which until now nothing tested.

use djust_core::{Context, Value};
use djust_templates::loop_cache::{LoopCacheGuard, LoopRenderCache};
use djust_templates::Template;
use indexmap::IndexMap;

/// Render the SAME template twice against a PERSISTENT cache, returning both
/// renderings.
///
/// The cache must be explicitly installed via `LoopCacheGuard` — a plain
/// `Template::render` never consults it, so a first version of these tests
/// gate-off'd to zero: they exercised the uncached path and could not detect a
/// hashing regression at all (#2203 review, reproduction fidelity).
///
/// The template is held across both renders because #2067 folded the For-node
/// identity into the content hash: re-parsing yields a new keyspace and every
/// lookup would miss regardless.
fn render_twice(first: Value, second: Value) -> (String, String) {
    let tmpl = Template::new("{% for row in rows %}<i>{{ row.v }}</i>{% endfor %}")
        .expect("template should parse");
    let mut cache = LoopRenderCache::new(true);

    let mut go = |v: Value| {
        cache.begin_render();
        let html = {
            let _guard = LoopCacheGuard::install(&mut cache);
            tmpl.render(&rows_with(v)).expect("render")
        };
        cache.prune();
        html
    };
    let a = go(first);
    let b = go(second);
    (a, b)
}

fn rows_with(v: Value) -> Context {
    let mut row = IndexMap::new();
    row.insert("v".into(), v);
    let mut c = Context::new();
    c.set("rows".to_string(), Value::List(vec![Value::Object(row)]));
    c
}

#[test]
fn missing_and_none_do_not_share_a_cache_entry() {
    // They render differently ("" vs "None"), so a shared key would serve one
    // for the other.
    let (a, b) = render_twice(Value::Missing, Value::None);
    assert_eq!(a, "<i></i>", "Missing should render empty");
    assert_eq!(b, "<i>None</i>", "None should render None");
    assert_ne!(a, b, "Missing and None must not collide in the loop cache");
}

#[test]
fn a_tuple_and_a_list_do_not_share_a_cache_entry() {
    // "(1, 2)" vs "[1, 2]" — different renderings, so different keys.
    let items = vec![Value::Integer(1), Value::Integer(2)];
    let (a, b) = render_twice(Value::List(items.clone()), Value::Tuple(items));
    assert_eq!(a, "<i>[1, 2]</i>");
    assert_eq!(b, "<i>(1, 2)</i>");
    assert_ne!(a, b, "List and Tuple must not collide in the loop cache");
}

#[test]
fn dicts_differing_only_in_key_order_do_not_share_a_cache_entry() {
    // The sharpest case, and the one the sorted-key hash would break: the same
    // pairs inserted in different orders render differently now that `Object`
    // is insertion-ordered, so an order-independent hash would serve the first
    // rendering for the second.
    let mut ab = IndexMap::new();
    ab.insert("a".into(), Value::Integer(1));
    ab.insert("b".into(), Value::Integer(2));

    let mut ba = IndexMap::new();
    ba.insert("b".into(), Value::Integer(2));
    ba.insert("a".into(), Value::Integer(1));

    let (first, second) = render_twice(Value::Object(ab), Value::Object(ba));

    assert_eq!(first, "<i>{&#x27;a&#x27;: 1, &#x27;b&#x27;: 2}</i>");
    assert_eq!(
        second, "<i>{&#x27;b&#x27;: 2, &#x27;a&#x27;: 1}</i>",
        "insertion order must survive the loop cache; a sorted-key hash would \
         serve the first rendering here"
    );
    assert_ne!(first, second);
}

#[test]
fn a_tuple_is_iterable_in_a_for_loop() {
    // The PR's own headline risk: ~20 `Value::List` matches have a `_`
    // fallback, so a missing `Tuple` twin is invisible to the compiler and
    // tuples silently stop iterating. Gate-off showed removing the `{% for %}`
    // twin failed nothing on the Rust side — the only catcher was a
    // Python-side security test.
    let mut c = Context::new();
    c.set(
        "rows".to_string(),
        Value::Tuple(vec![Value::Integer(1), Value::Integer(2)]),
    );
    let t = Template::new("{% for x in rows %}[{{ x }}]{% endfor %}").unwrap();
    assert_eq!(t.render(&c).unwrap(), "[1][2]");
}

#[test]
fn a_dict_view_and_a_list_of_the_same_items_do_not_share_a_cache_entry() {
    // #2340: a view renders `dict_keys(['a'])` and a list of the same items
    // renders `['a']`, so a shared key serves one for the other — the same
    // argument the Tuple/List case above makes, one variant over.
    //
    // Through the REAL cached render path, not `hash_value` directly: this
    // file's own docstring records that a first version testing the hash in
    // isolation gate-off'd to zero because it never consulted the cache.
    let items = vec![Value::String("a".into())];
    let (a, b) = render_twice(
        Value::List(items.clone()),
        Value::DictView {
            kind: djust_core::DictViewKind::Keys,
            items: items.clone(),
        },
    );
    assert_eq!(a, "<i>[&#x27;a&#x27;]</i>");
    assert_eq!(b, "<i>dict_keys([&#x27;a&#x27;])</i>");
    assert_ne!(a, b, "a view and a list must not collide in the loop cache");
}

#[test]
fn two_dict_view_kinds_do_not_share_a_cache_entry() {
    // The kind is IN the hash, not merely in `Display`: `dict_keys([1])` and
    // `dict_values([1])` are different renderings of the same items, so the
    // kind has to reach the cache key or the first is served for the second.
    let items = vec![Value::Integer(1)];
    let (a, b) = render_twice(
        Value::DictView {
            kind: djust_core::DictViewKind::Keys,
            items: items.clone(),
        },
        Value::DictView {
            kind: djust_core::DictViewKind::Values,
            items,
        },
    );
    assert_eq!(a, "<i>dict_keys([1])</i>");
    assert_eq!(
        b, "<i>dict_values([1])</i>",
        "the view KIND must survive the loop cache; a kind-blind hash would \
         serve the first rendering here"
    );
    assert_ne!(a, b);
}
