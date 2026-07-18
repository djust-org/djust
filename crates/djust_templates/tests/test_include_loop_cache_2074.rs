//! #2074 — `{% include %}`'d template bodies re-parse from disk on EVERY
//! call to `FilesystemTemplateLoader::load_template`, returning a fresh
//! `Vec<Node>` (fresh heap allocation) each time. The #2067 loop-render
//! cache keys each `{% for %}` body by IDENTITY
//! `(nodes.as_ptr() as usize, nodes.len())`
//! (`crates/djust_templates/src/renderer.rs:800-801`,
//! `crate::loop_cache::content_hash`). Because an included template's body
//! gets a fresh ptr on every render, a `{% for %}` loop INSIDE an
//! `{% include %}` never hits the loop cache across renders — the #2067
//! reorder-win (O(changed)=0 re-renders on a pure reorder) is silently lost
//! for every included template, and there is a theoretical
//! allocator-reuse staleness window (a freed include's ptr reused for a
//! DIFFERENT include on a later render → key collision).
//!
//! Reproduces with a REAL `FilesystemTemplateLoader` pointed at a temp dir,
//! and deliberately constructs a FRESH loader instance per render — this
//! mirrors production exactly: `crates/djust_live/src/lib.rs`'s `render()`
//! and `render_with_diff()` both do
//! `let loader = FilesystemTemplateLoader::new(self.template_dirs.clone());`
//! on EVERY call, while the `Template` (parsed parent AST) and the
//! `LoopRenderCache` are held PERSISTENTLY across renders (`TEMPLATE_CACHE`
//! static + `self.loop_render_cache` instance field). An instance-scoped
//! fix on `FilesystemTemplateLoader` would never be reachable from
//! production, since production never reuses a loader instance across
//! renders — only a process-global (or otherwise loader-instance-external)
//! cache can give the include's parsed body a STABLE ptr across renders.

use djust_core::{Context, Value};
use djust_templates::inheritance::FilesystemTemplateLoader;
use djust_templates::loop_cache::{LoopCacheGuard, LoopRenderCache};
use djust_templates::Template;
use std::collections::HashMap;
use std::fs;
use tempfile::TempDir;

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

/// Parse-once-hold-the-template, fresh-loader-per-render render helper —
/// the production-faithful shape. `tmpl` and `cache` are held across calls
/// (mirroring `TEMPLATE_CACHE` + `self.loop_render_cache`); `loader` is a
/// BRAND NEW `FilesystemTemplateLoader` constructed by the CALLER for each
/// render (mirroring `FilesystemTemplateLoader::new(self.template_dirs.clone())`
/// being called fresh inside `render()`/`render_with_diff()`).
fn render_cached(
    tmpl: &Template,
    loader: &FilesystemTemplateLoader,
    items_value: &Value,
    cache: &mut LoopRenderCache,
) -> String {
    cache.begin_render();
    let html = {
        let _guard = LoopCacheGuard::install(cache);
        let mut ctx = Context::new();
        ctx.set("xs".to_string(), items_value.clone());
        tmpl.render_with_loader(&ctx, loader).expect("render")
    };
    cache.prune();
    html
}

/// THE #2074 REPRODUCER: a `{% for %}` loop living inside an
/// `{% include %}`'d template must still get the #2067 reorder win (all
/// cache HITS on a pure reorder) — exactly like a `{% for %}` loop living
/// directly in the parent template already does
/// (`test_loop_render_cache_1967.rs::reorder_within_one_loop_is_all_hits`).
#[test]
fn include_body_loop_reorder_is_all_hits_2074() {
    let temp_dir = TempDir::new().expect("tempdir");
    let dir = temp_dir.path();
    fs::write(
        dir.join("child.html"),
        "{% for x in xs %}[{{ x.name }}]{% endfor %}",
    )
    .expect("write child.html");

    let parent_src = "{% include \"child.html\" %}";
    let tmpl = Template::new(parent_src).expect("parse parent");

    let initial = items(&[("1", "alpha"), ("2", "bravo"), ("3", "charlie")]);
    let reordered = items(&[("3", "charlie"), ("1", "alpha"), ("2", "bravo")]);

    let mut cache = LoopRenderCache::new(true);

    // Cold render: 3 misses (nothing cached yet). Fresh loader #1.
    let loader1 = FilesystemTemplateLoader::new(vec![dir.to_path_buf()]);
    let html1 = render_cached(&tmpl, &loader1, &initial, &mut cache);
    assert!(
        html1.contains("[alpha]") && html1.contains("[bravo]") && html1.contains("[charlie]"),
        "sanity: cold render must contain all three items, got: {html1}"
    );
    assert_eq!(cache.misses(), 3, "cold render should miss all 3 items");
    assert_eq!(cache.hits(), 0, "cold render should have 0 hits");

    // Reorder render: SAME items, shuffled order. A FRESH loader #2 —
    // mirrors production reconstructing `FilesystemTemplateLoader` on
    // every render call.
    let loader2 = FilesystemTemplateLoader::new(vec![dir.to_path_buf()]);
    let html2 = render_cached(&tmpl, &loader2, &reordered, &mut cache);
    assert!(
        html2.contains("[alpha]") && html2.contains("[bravo]") && html2.contains("[charlie]"),
        "sanity: reorder render must contain all three items, got: {html2}"
    );

    assert_eq!(
        cache.hits(),
        3,
        "#2074: a pure reorder of an `{{% include %}}`'d loop body must be \
         all cache HITS (the #2067 reorder win) — pre-fix, \
         `FilesystemTemplateLoader::load_template` re-tokenizes+re-parses \
         from disk on every call, returning a fresh `Vec<Node>` (fresh heap \
         ptr) each render, so the For-node body-identity key \
         `(nodes.as_ptr(), nodes.len())` never matches across renders and \
         every item misses. Got {} hits / {} misses.",
        cache.hits(),
        cache.misses(),
    );
    assert_eq!(
        cache.misses(),
        0,
        "#2074: reorder of an include's loop body must add no misses"
    );
}

/// Staleness-invalidation companion: the cache MUST NOT serve a stale parse
/// after the included template's file content (and mtime) change on disk —
/// this is the correctness backstop for making the parse cache
/// cross-render-persistent (mtime-keyed, not identity/session-keyed).
#[test]
fn include_body_cache_invalidates_on_mtime_change_2074() {
    let temp_dir = TempDir::new().expect("tempdir");
    let dir = temp_dir.path();
    let child_path = dir.join("child.html");
    fs::write(&child_path, "ORIGINAL-CONTENT").expect("write v1");

    let parent_src = "{% include \"child.html\" %}";
    let tmpl = Template::new(parent_src).expect("parse parent");
    let ctx = Context::new();

    let loader1 = FilesystemTemplateLoader::new(vec![dir.to_path_buf()]);
    let html1 = tmpl.render_with_loader(&ctx, &loader1).expect("render 1");
    assert!(
        html1.contains("ORIGINAL-CONTENT"),
        "sanity: first render must show v1 content, got: {html1}"
    );

    // Rewrite the include with DIFFERENT content and an EXPLICIT, distinctly
    // bumped mtime — deterministic regardless of filesystem mtime
    // resolution / how fast the test executes within the same wall-clock
    // second.
    fs::write(&child_path, "UPDATED-CONTENT").expect("write v2");
    let bumped = std::time::SystemTime::now() + std::time::Duration::from_secs(120);
    {
        let f = fs::File::open(&child_path).expect("open for mtime bump");
        f.set_modified(bumped).expect("bump mtime");
    }

    // A brand-new loader again (production-faithful — every render gets a
    // fresh `FilesystemTemplateLoader` instance).
    let loader2 = FilesystemTemplateLoader::new(vec![dir.to_path_buf()]);
    let html2 = tmpl.render_with_loader(&ctx, &loader2).expect("render 2");

    assert!(
        html2.contains("UPDATED-CONTENT"),
        "cache must invalidate on mtime change and reflect the NEW file \
         content, got: {html2}"
    );
    assert!(
        !html2.contains("ORIGINAL-CONTENT"),
        "cache must NOT serve the stale v1 content after the file changed, \
         got: {html2}"
    );
}
