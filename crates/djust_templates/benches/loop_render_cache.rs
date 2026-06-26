//! Render-phase benchmark for the per-item loop render cache (#1967).
//!
//! Isolates the TEMPLATE RENDER phase (the half this PR optimizes) on a pure
//! reorder of a large keyed list — same item content, shuffled order. With the
//! cache ENABLED every item is a hit (O(0) re-renders); with it DISABLED every
//! item is re-rendered from the AST (the ~9 µs/item baseline from #1967).
//!
//! Note: this measures the RENDER phase only — not the full
//! `render_with_diff`, whose end-to-end win is bounded by the (uncached)
//! html5ever parse + VDOM diff phases (Amdahl). The render-phase delta is the
//! direct, deterministic measure of what #1967 changes.

use criterion::{criterion_group, criterion_main, BenchmarkId, Criterion};
use djust_core::{Context, Value};
use djust_templates::loop_cache::{LoopCacheGuard, LoopRenderCache};
use djust_templates::Template;
use std::collections::HashMap;
use std::hint::black_box;

const SRC: &str =
    "<ul>{% for x in xs %}<li class=\"row\"><span>{{ x.name }}</span> - <em>{{ x.detail }}</em></li>{% endfor %}</ul>";

fn make_items(n: usize) -> Vec<Value> {
    (0..n)
        .map(|i| {
            let mut m = HashMap::new();
            m.insert("id".to_string(), Value::Integer(i as i64));
            m.insert("name".to_string(), Value::String(format!("item-{i}")));
            m.insert("detail".to_string(), Value::String(format!("detail-{i}")));
            Value::Object(m)
        })
        .collect()
}

/// A deterministic "reorder" permutation (rotate by 1) so each cycle presents
/// the same content in a different order — the pure-reorder case.
fn rotated(items: &[Value]) -> Vec<Value> {
    let mut v = items.to_vec();
    v.rotate_left(1);
    v
}

fn render_once(tmpl: &Template, items: &[Value], cache: Option<&mut LoopRenderCache>) -> String {
    let mut ctx = Context::new();
    ctx.set("xs".to_string(), Value::List(items.to_vec()));
    match cache {
        Some(c) => {
            c.begin_render();
            let _g = LoopCacheGuard::install(c);
            tmpl.render(&ctx).unwrap()
        }
        None => tmpl.render(&ctx).unwrap(),
    }
}

fn bench_reorder(c: &mut Criterion) {
    let mut group = c.benchmark_group("loop_reorder_render");
    let tmpl = Template::new(SRC).unwrap();

    for &n in &[50usize, 500] {
        let items = make_items(n);

        // DISABLED: every reorder re-renders every item from the AST.
        group.bench_with_input(BenchmarkId::new("cache_off", n), &items, |b, items| {
            let mut cur = items.clone();
            b.iter(|| {
                cur = rotated(&cur);
                black_box(render_once(&tmpl, &cur, None))
            });
        });

        // ENABLED: warm the cache once, then every reorder is all hits.
        group.bench_with_input(BenchmarkId::new("cache_on", n), &items, |b, items| {
            let mut cache = LoopRenderCache::new(true);
            // Warm: render the initial order so every fragment is cached.
            let _ = render_once(&tmpl, items, Some(&mut cache));
            cache.prune();
            let mut cur = items.clone();
            b.iter(|| {
                cur = rotated(&cur);
                let html = render_once(&tmpl, &cur, Some(&mut cache));
                cache.prune();
                black_box(html)
            });
        });
    }

    group.finish();
}

criterion_group!(benches, bench_reorder);
criterion_main!(benches);
