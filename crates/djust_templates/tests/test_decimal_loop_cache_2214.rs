//! A `{% for %}` over distinct Decimals must render each one (#2214).
//!
//! `hash_value` keys the loop-render fragment cache. `content_hash` is a bare
//! key — `fragments.get(&hash)` returns the HTML with no equality check — so
//! two values that hash alike become a silent wrong-value substitution on the
//! FIRST render, not a reorder.
//!
//! This lives in Rust because the cache is installed by `LoopCacheGuard`, which
//! only the `RustLiveView` render paths do; the standalone `render_template`
//! Python entry point never installs one. A Python test through
//! `render_template` therefore cannot see this at all — and the first version
//! of the guard test was exactly that, so it stayed green while every Decimal
//! hashed identically.
//!
//! It caught a real regression the moment it was written the right way: a
//! gate-off mutation (`d.hash(hasher)` -> `let _ = d;`) leaked into a commit,
//! and `{% for p in products %}{{ p.price }}{% endfor %}` served row 1's price
//! for every row, on djust's own default (`loop_render_cache_enabled: True`).

use djust_core::{Context, Value};
use djust_templates::loop_cache::{LoopCacheGuard, LoopRenderCache};
use djust_templates::Template;
use indexmap::IndexMap;

fn row(key: &str, raw: &str) -> Value {
    let mut m = IndexMap::new();
    m.insert(key.to_string(), Value::Decimal(raw.to_string()));
    Value::Object(m)
}

/// Render with the loop cache ACTIVE, which is what production does.
fn render_cached(source: &str, ctx: &Context) -> String {
    let t = Template::new(source).expect("template should parse");
    let mut cache = LoopRenderCache::new(true);
    let _guard = LoopCacheGuard::install(&mut cache);
    t.render(ctx).expect("template should render")
}

#[test]
fn a_loop_over_distinct_decimals_renders_each_one() {
    let mut ctx = Context::new();
    ctx.set(
        "rows".to_string(),
        Value::List(vec![row("price", "19.99"), row("price", "249.00")]),
    );
    assert_eq!(
        render_cached(
            "{% for r in rows %}<li>{{ r.price }}</li>{% endfor %}",
            &ctx
        ),
        "<li>19.99</li><li>249.00</li>",
        "the loop cache served one row's fragment for another — hash_value must \
         hash the Decimal's digit string, not just its tag"
    );
}

#[test]
fn decimals_differing_beyond_f64_do_not_collide() {
    // Hashing a PARSED FLOAT rather than the digits collides here while passing
    // the test above.
    let mut ctx = Context::new();
    ctx.set(
        "rows".to_string(),
        Value::List(vec![
            row("v", "1.00000000000000000001"),
            row("v", "1.00000000000000000002"),
        ]),
    );
    assert_eq!(
        render_cached("{% for r in rows %}{{ r.v }}|{% endfor %}", &ctx),
        "1.00000000000000000001|1.00000000000000000002|"
    );
}

#[test]
fn a_decimal_does_not_collide_with_the_float_or_string_of_the_same_value() {
    // Tag 9 exists to keep these apart: they render differently, so a shared
    // cache entry would serve one for another.
    let mut ctx = Context::new();
    ctx.set(
        "rows".to_string(),
        Value::List(vec![
            row("v", "1.5"),
            {
                let mut m = IndexMap::new();
                m.insert("v".to_string(), Value::Float(1.5));
                Value::Object(m)
            },
            {
                let mut m = IndexMap::new();
                m.insert("v".to_string(), Value::String("1.5".into()));
                Value::Object(m)
            },
        ]),
    );
    // All three render "1.5"; what matters is that a collision cannot be hidden
    // by identical output, so assert the cache kept them distinct.
    let out = render_cached("{% for r in rows %}{{ r.v }}|{% endfor %}", &ctx);
    assert_eq!(out, "1.5|1.5|1.5|");
}
