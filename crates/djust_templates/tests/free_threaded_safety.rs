//! Free-threaded-safety concurrency stress test (#1432).
//!
//! `djust._rust` is declared `#[pymodule(gil_used = false)]`
//! (`crates/djust_live/src/lib.rs`). That declaration is a *promise* to
//! CPython: on a free-threaded interpreter (3.13t / 3.14t) CPython will
//! NOT auto-re-enable the GIL on import, so any unsynchronized shared
//! mutable state reachable through the module silently becomes a data
//! race. This file is the regression guard for that promise.
//!
//! Why this lives in `djust_templates` and not `djust_live`:
//! `crates/djust_live` carries the `pyo3` `extension-module` feature
//! unconditionally, so it cannot be `cargo test`'d — the workspace test
//! run is `cargo test --workspace --exclude djust_live` for exactly that
//! reason. `djust_templates` IS in the workspace `cargo test` run, and
//! the genuinely-interesting shared types named in the Stage-4 audit
//! (`.pipeline-state/feat-1432-free-threaded-safe-plan.md`) live here:
//!   * `Template`'s per-instance `OnceLock<ResolvedInheritance>` /
//!     `OnceLock<FlattenedNodes>` lazy-resolve race — `Template`
//!     instances are shared across threads behind `Arc` in
//!     `djust_live`'s `TEMPLATE_CACHE`, so the `OnceLock` set-race is
//!     the load-bearing case.
//!   * The four `Lazy<Mutex<HashMap<..>>>` tag / filter registries
//!     (`registry.rs`, `filter_registry.rs`).
//!
//! Why `std::thread` rather than a Python `threading` test: `std::thread`
//! gives genuine OS-thread parallelism with **no GIL involved at all** —
//! a *stronger* test of the Rust `Send`/`Sync` contracts than a
//! free-threaded Python would give for the pure-Rust paths. A
//! GIL'd-Python `threading` test (see
//! `python/djust/tests/test_rust_module_thread_safety.py`) cannot run
//! Rust code truly concurrently and so cannot catch a data race; this
//! Rust test is the real gate.

use djust_core::Result as DjustResult;
use djust_core::{Context, Value};
use djust_templates::filter_registry::{get_registered_custom_filters, has_custom_filter};
use djust_templates::inheritance::TemplateLoader;
use djust_templates::parser::{self, Node};
use djust_templates::registry::{
    block_handler_exists, get_registered_tags, handler_exists, has_block_tag_handler,
    has_tag_handler,
};
use djust_templates::{lexer, Template};
use std::collections::HashMap;
use std::sync::{Arc, Barrier};
use std::thread;

/// Number of concurrent OS threads each test spawns. 12 is comfortably
/// inside the 8-16 range the plan specifies — enough to make a missing
/// `Sync` bound or an unsynchronized write reliably surface.
const N_THREADS: usize = 12;
/// Tight-loop iteration count per thread — widens the race window so a
/// real data race has many chances to corrupt observable state.
const ITERS: usize = 200;

/// In-memory `TemplateLoader` for the inheritance / `OnceLock` test.
/// Implementing the public `TemplateLoader` trait in the test crate is
/// the supported way to exercise `Template::resolve_inheritance`.
struct InMemoryLoader {
    templates: HashMap<String, String>,
}

impl TemplateLoader for InMemoryLoader {
    fn load_template(&self, name: &str) -> DjustResult<Vec<Node>> {
        let src = self.templates.get(name).ok_or_else(|| {
            djust_core::DjangoRustError::TemplateError(format!("missing template: {name}"))
        })?;
        let tokens = lexer::tokenize(src)?;
        parser::parse_with_source(&tokens, src)
    }
}

/// Case 1 — `Template` `OnceLock` lazy-resolve race.
///
/// One `Arc<Template>` that uses `{% extends %}` (so `resolve_inheritance`
/// does real work) is shared across N threads that all call
/// `resolve_inheritance` + `render` simultaneously. The threads
/// rendezvous on a `Barrier` so they hit `OnceLock::set` as close to
/// simultaneously as the OS scheduler allows. Assert every thread
/// observes byte-identical resolved output and no panic — this directly
/// stresses the `OnceLock::set` race the plan flags as load-bearing.
#[test]
fn template_oncelock_resolve_race_is_safe() {
    let child = "{% extends \"base.html\" %}{% block body %}child-{{ name }}{% endblock %}";
    let base = "[base|{% block body %}default{% endblock %}|base]";

    let mut templates = HashMap::new();
    templates.insert("base.html".to_string(), base.to_string());
    let loader = Arc::new(InMemoryLoader { templates });

    // ONE shared Template instance — this is the case that matters.
    let template = Arc::new(Template::new(child).expect("child template parses"));
    let barrier = Arc::new(Barrier::new(N_THREADS));

    let handles: Vec<_> = (0..N_THREADS)
        .map(|t| {
            let template = Arc::clone(&template);
            let loader = Arc::clone(&loader);
            let barrier = Arc::clone(&barrier);
            thread::spawn(move || {
                barrier.wait(); // all threads race the OnceLock together
                for _ in 0..ITERS {
                    // Concurrent resolve_inheritance: races OnceLock::set.
                    template
                        .resolve_inheritance(&*loader)
                        .expect("resolve_inheritance must not error under contention");
                    let mut ctx = Context::new();
                    ctx.set("name".to_string(), Value::String(format!("thread{t}")));
                    let html = template
                        .render_with_loader(&ctx, &*loader)
                        .expect("render must not error under contention");
                    // The resolved output must always reflect the merged
                    // inheritance chain — never a half-set OnceLock state.
                    assert!(
                        html.contains("[base|") && html.contains(&format!("child-thread{t}")),
                        "thread {t} saw a corrupt/unresolved render: {html:?}"
                    );
                }
            })
        })
        .collect();

    for h in handles {
        h.join().expect("no thread panicked during OnceLock race");
    }
}

/// Case 2 — concurrent `Template` parse + render across distinct sources.
///
/// N threads each build their own `Template` from a distinct source and
/// render it in a tight loop. This exercises the parser, lexer, and
/// renderer (which read the process-global `Lazy<Regex>` statics
/// `VAR_REGEX` / `TAG_REGEX` / `SPACELESS_RE`) under genuine parallelism.
/// Read-only `Regex` access from many threads must produce deterministic
/// output with no panic.
#[test]
fn concurrent_parse_and_render_is_deterministic() {
    let barrier = Arc::new(Barrier::new(N_THREADS));

    let handles: Vec<_> = (0..N_THREADS)
        .map(|t| {
            let barrier = Arc::clone(&barrier);
            thread::spawn(move || {
                let source = format!(
                    "<p>thread {t}: {{{{ greeting }}}} {{% if show %}}YES{{% endif %}}</p>"
                );
                let template = Template::new(&source).expect("template parses");
                barrier.wait();
                for i in 0..ITERS {
                    let mut ctx = Context::new();
                    ctx.set("greeting".to_string(), Value::String(format!("hello-{i}")));
                    ctx.set("show".to_string(), Value::Bool(i % 2 == 0));
                    let html = template.render(&ctx).expect("render succeeds");
                    let expected_greeting = format!("hello-{i}");
                    assert!(
                        html.contains(&expected_greeting),
                        "thread {t} iter {i}: missing greeting in {html:?}"
                    );
                    if i % 2 == 0 {
                        assert!(
                            html.contains("YES"),
                            "thread {t} iter {i}: {{% if %}} body lost"
                        );
                    } else {
                        assert!(
                            !html.contains("YES"),
                            "thread {t} iter {i}: stale {{% if %}} body"
                        );
                    }
                }
            })
        })
        .collect();

    for h in handles {
        h.join()
            .expect("no thread panicked during concurrent render");
    }
}

/// Case 3 — concurrent registry lookups across all four
/// `Lazy<Mutex<HashMap<..>>>` registries.
///
/// N threads hammer the `Mutex`-guarded lookup paths
/// (`has_tag_handler`, `has_block_tag_handler`, `block_handler_exists`,
/// `handler_exists`, `has_custom_filter`, `get_registered_tags`,
/// `get_registered_custom_filters`) in a tight loop. Registration needs
/// a `Python` token + `Py<PyAny>` and so cannot run in a plain
/// `cargo test` process — but the *lookup* path is the one every
/// concurrent template render takes, and it is the one whose `Mutex`
/// discipline this test pins. Asserts no `Mutex` poisoning, no panic,
/// and a stable (empty, in this fresh process) result.
#[test]
fn concurrent_registry_lookups_are_safe() {
    let barrier = Arc::new(Barrier::new(N_THREADS));

    let handles: Vec<_> = (0..N_THREADS)
        .map(|_| {
            let barrier = Arc::clone(&barrier);
            thread::spawn(move || {
                barrier.wait();
                for _ in 0..ITERS {
                    // Each call takes a Mutex lock on its registry. A
                    // non-Sync registry or a poisoned Mutex would panic
                    // here; clean concurrent access returns Ok/false.
                    assert!(!has_tag_handler("__nope__").expect("tag lookup ok"));
                    assert!(!has_block_tag_handler("__nope__").expect("block lookup ok"));
                    assert!(block_handler_exists("__nope__").is_none());
                    assert!(!handler_exists("__nope__"));
                    assert!(!has_custom_filter("__nope__").expect("filter lookup ok"));
                    // List accessors return the full keyset under lock —
                    // a torn read of the HashMap would panic or corrupt.
                    let _tags = get_registered_tags().expect("list tags ok");
                    let _filters = get_registered_custom_filters().expect("list filters ok");
                }
            })
        })
        .collect();

    for h in handles {
        h.join()
            .expect("no thread panicked during registry lookups");
    }
}

/// Case 4 — concurrent `template_hash_hex` calls.
///
/// `parser::template_hash_hex` is a pure function exposed to Python as
/// `compute_template_hash`. N threads compute the hash of a shared
/// source concurrently; every thread must agree on the exact hash.
/// A non-deterministic result would betray hidden shared mutable state
/// in the hash path.
#[test]
fn concurrent_template_hash_is_deterministic() {
    let source = "<div>{{ a }}{% for x in xs %}{{ x }}{% endfor %}</div>";
    let expected = parser::template_hash_hex(source);
    let barrier = Arc::new(Barrier::new(N_THREADS));

    let handles: Vec<_> = (0..N_THREADS)
        .map(|_| {
            let expected = expected.clone();
            let barrier = Arc::clone(&barrier);
            thread::spawn(move || {
                barrier.wait();
                for _ in 0..ITERS {
                    let h = parser::template_hash_hex(source);
                    assert_eq!(h, expected, "template hash diverged under contention");
                }
            })
        })
        .collect();

    for h in handles {
        h.join()
            .expect("no thread panicked during hash computation");
    }
}
