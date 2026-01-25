//! Benchmarks for Context operations and Value serialization
//!
//! These benchmarks measure:
//! - Context get/set operations (simple and nested)
//! - Context stack push/pop
//! - Value serialization (JSON, MessagePack)
//! - HashMap to Context conversion

use criterion::{criterion_group, criterion_main, BenchmarkId, Criterion};
use djust_core::{Context, Value};
use std::collections::HashMap;
use std::hint::black_box;

fn create_test_value(depth: usize, width: usize) -> Value {
    if depth == 0 {
        Value::String("leaf".to_string())
    } else {
        let mut obj = HashMap::new();
        for i in 0..width {
            obj.insert(format!("field_{i}"), create_test_value(depth - 1, width));
        }
        Value::Object(obj)
    }
}

fn bench_context_simple_get(c: &mut Criterion) {
    let mut group = c.benchmark_group("context_simple_get");

    // Benchmark simple key lookup
    let mut ctx = Context::new();
    ctx.set("name".to_string(), Value::String("test".to_string()));
    ctx.set("count".to_string(), Value::Integer(42));
    ctx.set("active".to_string(), Value::Bool(true));

    group.bench_function("single_key", |b| b.iter(|| ctx.get(black_box("name"))));

    group.bench_function("missing_key", |b| {
        b.iter(|| ctx.get(black_box("nonexistent")))
    });

    group.finish();
}

fn bench_context_nested_get(c: &mut Criterion) {
    let mut group = c.benchmark_group("context_nested_get");

    // Create nested object: user.profile.settings.theme
    let mut theme = HashMap::new();
    theme.insert("name".to_string(), Value::String("dark".to_string()));

    let mut settings = HashMap::new();
    settings.insert("theme".to_string(), Value::Object(theme));
    settings.insert("notifications".to_string(), Value::Bool(true));

    let mut profile = HashMap::new();
    profile.insert("settings".to_string(), Value::Object(settings));
    profile.insert("bio".to_string(), Value::String("Developer".to_string()));

    let mut user = HashMap::new();
    user.insert("profile".to_string(), Value::Object(profile));
    user.insert("name".to_string(), Value::String("John".to_string()));

    let mut ctx = Context::new();
    ctx.set("user".to_string(), Value::Object(user));

    let lookups = vec![
        ("1_level", "user.name"),
        ("2_levels", "user.profile.bio"),
        ("3_levels", "user.profile.settings.notifications"),
        ("4_levels", "user.profile.settings.theme.name"),
    ];

    for (name, path) in lookups {
        group.bench_with_input(BenchmarkId::from_parameter(name), &path, |b, &path| {
            b.iter(|| ctx.get(black_box(path)))
        });
    }

    group.finish();
}

fn bench_context_set(c: &mut Criterion) {
    let mut group = c.benchmark_group("context_set");

    group.bench_function("string_value", |b| {
        let mut ctx = Context::new();
        b.iter(|| {
            ctx.set(
                black_box("key".to_string()),
                black_box(Value::String("value".to_string())),
            )
        })
    });

    group.bench_function("integer_value", |b| {
        let mut ctx = Context::new();
        b.iter(|| {
            ctx.set(
                black_box("count".to_string()),
                black_box(Value::Integer(42)),
            )
        })
    });

    group.bench_function("complex_object", |b| {
        let mut ctx = Context::new();
        let obj = create_test_value(2, 3);
        b.iter(|| ctx.set(black_box("data".to_string()), black_box(obj.clone())))
    });

    group.finish();
}

fn bench_context_stack(c: &mut Criterion) {
    let mut group = c.benchmark_group("context_stack");

    group.bench_function("push_pop_cycle", |b| {
        let mut ctx = Context::new();
        ctx.set("base".to_string(), Value::Integer(1));
        b.iter(|| {
            ctx.push();
            ctx.set("temp".to_string(), Value::Integer(2));
            black_box(ctx.get("base"));
            black_box(ctx.get("temp"));
            ctx.pop();
        })
    });

    group.bench_function("deep_stack_lookup", |b| {
        let mut ctx = Context::new();
        // Create a stack 10 levels deep
        for i in 0..10 {
            ctx.push();
            ctx.set(format!("level_{i}"), Value::Integer(i as i64));
        }
        ctx.set("target".to_string(), Value::String("found".to_string()));

        b.iter(|| black_box(ctx.get("level_0"))) // Lookup at bottom of stack
    });

    group.finish();
}

fn bench_context_from_dict(c: &mut Criterion) {
    let mut group = c.benchmark_group("context_from_dict");

    for size in [5, 10, 25, 50].iter() {
        let mut dict = HashMap::new();
        for i in 0..*size {
            dict.insert(format!("key_{i}"), Value::String(format!("value_{i}")));
        }

        group.bench_with_input(
            BenchmarkId::from_parameter(format!("{size}_keys")),
            &dict,
            |b, dict| b.iter(|| Context::from_dict(black_box(dict.clone()))),
        );
    }

    group.finish();
}

fn bench_value_truthy(c: &mut Criterion) {
    let mut group = c.benchmark_group("value_truthy");

    let values = vec![
        ("null", Value::Null),
        ("bool_true", Value::Bool(true)),
        ("bool_false", Value::Bool(false)),
        ("integer_nonzero", Value::Integer(42)),
        ("integer_zero", Value::Integer(0)),
        ("string_nonempty", Value::String("hello".to_string())),
        ("string_empty", Value::String(String::new())),
        ("list_nonempty", Value::List(vec![Value::Integer(1)])),
        ("list_empty", Value::List(Vec::new())),
    ];

    for (name, value) in values {
        group.bench_with_input(BenchmarkId::from_parameter(name), &value, |b, val| {
            b.iter(|| val.is_truthy())
        });
    }

    group.finish();
}

fn bench_value_display(c: &mut Criterion) {
    let mut group = c.benchmark_group("value_display");

    let values = vec![
        ("null", Value::Null),
        ("bool", Value::Bool(true)),
        ("integer", Value::Integer(12345)),
        ("float", Value::Float(123.456)),
        ("string_short", Value::String("hello".to_string())),
        ("string_long", Value::String("a".repeat(100))),
    ];

    for (name, value) in values {
        group.bench_with_input(BenchmarkId::from_parameter(name), &value, |b, val| {
            b.iter(|| format!("{}", val))
        });
    }

    group.finish();
}

criterion_group!(
    benches,
    bench_context_simple_get,
    bench_context_nested_get,
    bench_context_set,
    bench_context_stack,
    bench_context_from_dict,
    bench_value_truthy,
    bench_value_display,
);
criterion_main!(benches);
