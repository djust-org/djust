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
fn a_decimal_does_not_share_a_cache_entry_with_the_string_of_the_same_digits() {
    // Tag 9 keeps Decimal apart from BOTH String and Float, and both halves are
    // pinned below with values that genuinely collide without it.
    //
    // The Float half needed a constructed witness, because the obvious pairs do
    // not collide and a previous version of this comment generalised one such
    // non-collision into "Float hashes an 8-byte bit pattern where Decimal
    // hashes a `&str`, so the two cannot collide whatever tags they carry."
    // That is false. `Hash for str` writes the bytes then `0xff`; `Hash for u64`
    // writes eight native-endian bytes. So any SEVEN-byte string collides with
    // the f64 whose NE bit pattern is those bytes followed by `0xff` — here
    // `"1.5E+30"` and `f64::from_bits(u64::from_ne_bytes([b'1', b'.', b'5',
    // b'E', b'+', b'3', b'0', 0xff]))`, verified to hash identically.
    //
    // Same error as the guard comment this file's sibling fixed: one
    // observation reported as a universal, telling the next maintainer a live
    // separator was not load-bearing.
    let colliding_float = f64::from_bits(u64::from_ne_bytes([
        b'1', b'.', b'5', b'E', b'+', b'3', b'0', 0xff,
    ]));
    let mut ctx = Context::new();
    ctx.set(
        "rows".to_string(),
        Value::List(vec![
            row("v", "1E+1"),
            {
                // String half: identical payload, different rendering.
                let mut m = IndexMap::new();
                m.insert("v".to_string(), Value::String("1E+1".into()));
                Value::Object(m)
            },
            row("v", "1.5E+30"),
            {
                // Float half: hashes to the same bytes as the Decimal above.
                let mut m = IndexMap::new();
                m.insert("v".to_string(), Value::Float(colliding_float));
                Value::Object(m)
            },
        ]),
    );
    assert_eq!(
        render_cached("{% for r in rows %}{{ r.v }}|{% endfor %}", &ctx),
        // The float's spelling is Python's, not Rust's `{}` — since #2258
        // `Display` renders a float the way `numberformat.format` does, and this
        // one is past Django's 200-digit cut-off so it stays in exponent form.
        // Written out rather than interpolated: an interpolated `{colliding_float}`
        // asserted Rust's spelling against the renderer's and had to change here
        // when the renderer became correct.
        "10|1E+1|1500000000000000000000000000000|-4.443727305504026e+304|",
        "a Decimal shared a loop-cache entry with the String or Float that hashes \
         to the same bytes — tag 9 is what keeps all three apart"
    );
}
