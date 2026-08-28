//! A `{% for %}` over distinct big ints must render each one (#2260).
//!
//! Sibling of `test_decimal_loop_cache_2214.rs`, and for the same reason:
//! `hash_value` keys the loop-render fragment cache and `content_hash` is a
//! BARE key — `fragments.get(&hash)` returns the HTML with no equality check —
//! so two values that hash alike become a silent wrong-value substitution on
//! the FIRST render, not a reorder.
//!
//! It has to live in Rust because the cache is installed by `LoopCacheGuard`,
//! which only the `RustLiveView` render paths do; the standalone
//! `render_template` entry point never installs one, so a Python test through
//! it cannot see this at all.
//!
//! Two distinct failures are pinned, and they need different witnesses:
//! hashing the TAG alone (or reusing `Decimal`'s tag 9) collides two variants
//! spelling the same digits, and hashing a PARSED FLOAT collides two values
//! that differ past f64 precision.

use djust_core::{Context, Value};
use djust_templates::loop_cache::{LoopCacheGuard, LoopRenderCache};
use djust_templates::Template;
use indexmap::IndexMap;

fn row(v: Value) -> Value {
    let mut m = IndexMap::new();
    m.insert("v".into(), v);
    Value::Object(m)
}

fn render_cached(source: &str, ctx: &Context) -> String {
    let t = Template::new(source).expect("template should parse");
    let mut cache = LoopRenderCache::new(true);
    let _guard = LoopCacheGuard::install(&mut cache);
    t.render(ctx).expect("template should render")
}

#[test]
fn big_ints_differing_beyond_f64_do_not_collide() {
    // 2^64 and 2^64 + 1 are the SAME double. Hashing a parsed float — the
    // obvious way to reuse the `Float` arm — serves the first row's digits for
    // the second, which is the silent wrong-value class this cache has already
    // produced once (#2214).
    let mut ctx = Context::new();
    ctx.set(
        "rows".to_string(),
        Value::List(vec![
            row(Value::BigInt("18446744073709551616".into())),
            row(Value::BigInt("18446744073709551617".into())),
        ]),
    );
    assert_eq!(
        render_cached("{% for r in rows %}{{ r.v }}|{% endfor %}", &ctx),
        "18446744073709551616|18446744073709551617|"
    );
}

#[test]
fn a_big_int_does_not_share_a_cache_entry_with_the_decimal_or_string_of_the_same_digits() {
    // Tag 10 is what keeps these apart. All three spell the same digits and
    // RENDER the same, so the fragment substitution would be invisible here —
    // but they do not SERIALIZE the same (one leaves as an `int`, one as a
    // `decimal.Decimal`, one as a `str`) and `pprint`/`json_script` spell them
    // differently, which is what makes the collision observable and what this
    // template exercises.
    let digits = "12345678901234567890";
    let mut ctx = Context::new();
    ctx.set(
        "rows".to_string(),
        Value::List(vec![
            row(Value::BigInt(digits.into())),
            row(Value::Decimal(digits.into())),
            row(Value::String(digits.into())),
        ]),
    );
    assert_eq!(
        render_cached("{% for r in rows %}{{ r.v|pprint }}|{% endfor %}", &ctx),
        // `&#x27;` — the renderer autoescapes, and `repr` puts quotes in.
        format!("{digits}|Decimal(&#x27;{digits}&#x27;)|&#x27;{digits}&#x27;|"),
        "a BigInt shared a loop-cache entry with the Decimal or String of the \
         same digits — tag 10 is what keeps all three apart"
    );
}
