//! Benchmarks for Tag Handler Registry operations
//!
//! These benchmarks measure the pure Rust overhead of the registry system:
//! - HashMap-based tag lookup with Mutex synchronization
//! - Tag existence checks (used by parser)
//!
//! Note: The actual Python callback overhead (~15-50µs per call as per ADR-001)
//! includes GIL acquisition and Python execution, which is tested separately
//! via Python integration tests.
//!
//! These benchmarks focus on the Rust-side overhead which should be minimal.

use criterion::{criterion_group, criterion_main, BenchmarkId, Criterion};
use std::collections::HashMap;
use std::hint::black_box;
use std::sync::Mutex;

// Mock registry structure (mirrors the real TAG_HANDLERS)
struct MockRegistry {
    handlers: Mutex<HashMap<String, MockHandler>>,
}

#[derive(Clone)]
struct MockHandler {
    name: String,
}

impl MockHandler {
    fn new(name: &str) -> Self {
        Self {
            name: name.to_string(),
        }
    }

    fn render(&self, args: &[String]) -> String {
        // Simulate minimal work
        let mut output = format!("<{}", self.name);
        for arg in args {
            output.push_str(&format!(" {}", arg));
        }
        output.push_str(" />");
        output
    }
}

impl MockRegistry {
    fn new() -> Self {
        Self {
            handlers: Mutex::new(HashMap::new()),
        }
    }

    fn register(&self, name: &str, handler: MockHandler) {
        self.handlers
            .lock()
            .unwrap()
            .insert(name.to_string(), handler);
    }

    fn handler_exists(&self, name: &str) -> bool {
        self.handlers
            .lock()
            .map(|registry| registry.contains_key(name))
            .unwrap_or(false)
    }

    fn get_handler(&self, name: &str) -> Option<MockHandler> {
        self.handlers.lock().ok()?.get(name).cloned()
    }

    fn get_registered_tags(&self) -> Vec<String> {
        self.handlers
            .lock()
            .map(|registry| registry.keys().cloned().collect())
            .unwrap_or_default()
    }

    fn clear(&self) {
        if let Ok(mut registry) = self.handlers.lock() {
            registry.clear();
        }
    }
}

// Create a pre-populated registry for benchmarking
fn create_populated_registry(size: usize) -> MockRegistry {
    let registry = MockRegistry::new();
    for i in 0..size {
        registry.register(&format!("tag-{i}"), MockHandler::new(&format!("tag-{i}")));
    }
    registry
}

fn bench_handler_exists(c: &mut Criterion) {
    let mut group = c.benchmark_group("handler_exists");

    // Test with various registry sizes
    for size in [10, 50, 100].iter() {
        let registry = create_populated_registry(*size);

        // Lookup existing tag (middle of registry)
        let target = format!("tag-{}", size / 2);
        group.bench_with_input(
            BenchmarkId::from_parameter(format!("{size}_tags_found")),
            &(registry, target),
            |b, (reg, target)| b.iter(|| reg.handler_exists(black_box(target))),
        );
    }

    // Lookup non-existent tag
    let registry = create_populated_registry(50);
    group.bench_function("50_tags_not_found", |b| {
        b.iter(|| registry.handler_exists(black_box("nonexistent")))
    });

    group.finish();
}

fn bench_get_handler(c: &mut Criterion) {
    let mut group = c.benchmark_group("get_handler");

    let registry = create_populated_registry(50);

    group.bench_function("get_existing", |b| {
        b.iter(|| registry.get_handler(black_box("tag-25")))
    });

    group.bench_function("get_missing", |b| {
        b.iter(|| registry.get_handler(black_box("nonexistent")))
    });

    group.finish();
}

fn bench_handler_render(c: &mut Criterion) {
    let mut group = c.benchmark_group("handler_render");

    let handler = MockHandler::new("button");

    // No arguments
    group.bench_function("render_no_args", |b| {
        let args: Vec<String> = vec![];
        b.iter(|| handler.render(black_box(&args)))
    });

    // Few arguments
    group.bench_function("render_2_args", |b| {
        let args = vec!["class=\"btn\"".to_string(), "type=\"submit\"".to_string()];
        b.iter(|| handler.render(black_box(&args)))
    });

    // Many arguments
    group.bench_function("render_8_args", |b| {
        let args = vec![
            "class=\"btn btn-primary\"".to_string(),
            "type=\"submit\"".to_string(),
            "id=\"submit-btn\"".to_string(),
            "name=\"submit\"".to_string(),
            "value=\"Submit\"".to_string(),
            "data-loading=\"false\"".to_string(),
            "data-target=\"#form\"".to_string(),
            "aria-label=\"Submit form\"".to_string(),
        ];
        b.iter(|| handler.render(black_box(&args)))
    });

    group.finish();
}

fn bench_full_lifecycle(c: &mut Criterion) {
    let mut group = c.benchmark_group("full_lifecycle");

    let registry = create_populated_registry(50);
    let args = vec!["class=\"btn\"".to_string(), "type=\"submit\"".to_string()];

    // Complete cycle: check exists -> get handler -> render
    group.bench_function("check_get_render", |b| {
        b.iter(|| {
            let tag_name = black_box("tag-25");
            if registry.handler_exists(tag_name) {
                if let Some(handler) = registry.get_handler(tag_name) {
                    handler.render(black_box(&args))
                } else {
                    String::new()
                }
            } else {
                String::new()
            }
        })
    });

    // Multiple tags in sequence (simulates template with several custom tags)
    group.bench_function("render_5_tags_sequence", |b| {
        let tags = ["tag-10", "tag-20", "tag-30", "tag-40", "tag-49"];
        b.iter(|| {
            for tag_name in tags {
                if let Some(handler) = registry.get_handler(black_box(tag_name)) {
                    let _ = handler.render(black_box(&args));
                }
            }
        })
    });

    group.finish();
}

fn bench_registry_operations(c: &mut Criterion) {
    let mut group = c.benchmark_group("registry_operations");

    // Registration benchmark
    group.bench_function("register_single", |b| {
        let registry = MockRegistry::new();
        let mut i = 0;
        b.iter(|| {
            registry.register(&format!("tag-{i}"), MockHandler::new(&format!("tag-{i}")));
            i += 1;
        })
    });

    // Get all registered tags
    let registry = create_populated_registry(50);
    group.bench_function("get_registered_tags_50", |b| {
        b.iter(|| registry.get_registered_tags())
    });

    // Clear registry
    group.bench_function("clear_50_tags", |b| {
        b.iter_batched(
            || create_populated_registry(50),
            |registry| registry.clear(),
            criterion::BatchSize::SmallInput,
        )
    });

    group.finish();
}

fn bench_concurrent_access(c: &mut Criterion) {
    let mut group = c.benchmark_group("concurrent_access");

    // Simulates contention scenario (single-threaded but measures lock overhead)
    let registry = create_populated_registry(50);

    group.bench_function("repeated_lookups", |b| {
        b.iter(|| {
            for i in 0..10 {
                let _ = registry.handler_exists(black_box(&format!("tag-{i}")));
            }
        })
    });

    group.bench_function("mixed_operations", |b| {
        b.iter(|| {
            let _ = registry.handler_exists(black_box("tag-0"));
            let _ = registry.get_handler(black_box("tag-25"));
            let _ = registry.get_registered_tags();
            let _ = registry.handler_exists(black_box("tag-49"));
        })
    });

    group.finish();
}

fn bench_overhead_comparison(c: &mut Criterion) {
    let mut group = c.benchmark_group("overhead_comparison");

    // Baseline: direct HashMap lookup (no Mutex)
    let mut direct_map: HashMap<String, MockHandler> = HashMap::new();
    for i in 0..50 {
        direct_map.insert(format!("tag-{i}"), MockHandler::new(&format!("tag-{i}")));
    }

    group.bench_function("direct_hashmap_lookup", |b| {
        b.iter(|| direct_map.get(black_box("tag-25")))
    });

    // With Mutex (as in real registry)
    let registry = create_populated_registry(50);
    group.bench_function("mutex_hashmap_lookup", |b| {
        b.iter(|| registry.get_handler(black_box("tag-25")))
    });

    // Baseline: direct string formatting
    group.bench_function("direct_format", |b| {
        b.iter(|| {
            format!(
                "<button class=\"{}\" type=\"{}\" />",
                black_box("btn"),
                black_box("submit")
            )
        })
    });

    // Via handler
    let handler = MockHandler::new("button");
    let args = vec!["class=\"btn\"".to_string(), "type=\"submit\"".to_string()];
    group.bench_function("handler_format", |b| {
        b.iter(|| handler.render(black_box(&args)))
    });

    group.finish();
}

criterion_group!(
    benches,
    bench_handler_exists,
    bench_get_handler,
    bench_handler_render,
    bench_full_lifecycle,
    bench_registry_operations,
    bench_concurrent_access,
    bench_overhead_comparison,
);
criterion_main!(benches);
