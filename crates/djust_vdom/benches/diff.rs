//! Benchmarks for VDOM diffing and patch generation
//!
//! These benchmarks measure:
//! - Simple attribute changes
//! - Text node changes
//! - Child list modifications (add, remove, reorder)
//! - Deep tree diffing
//! - Real-world form scenarios

use criterion::{criterion_group, criterion_main, BenchmarkId, Criterion};
use djust_vdom::{diff, VNode};
use std::hint::black_box;

/// Create a VNode tree with specified depth and children per node
fn create_tree(depth: usize, children_per_node: usize, prefix: &str) -> VNode {
    if depth == 0 {
        VNode::text("leaf")
    } else {
        let mut node = VNode::element("div")
            .with_djust_id(format!("{prefix}-{depth}"))
            .with_attr("class", format!("level-{depth}"));

        for i in 0..children_per_node {
            let child = create_tree(depth - 1, children_per_node, &format!("{prefix}-{i}"));
            node = node.with_child(child);
        }

        node
    }
}

fn bench_diff_no_changes(c: &mut Criterion) {
    let mut group = c.benchmark_group("diff_no_changes");

    for depth in [1, 2, 3, 4].iter() {
        let tree = create_tree(*depth, 3, "n");

        group.bench_with_input(
            BenchmarkId::from_parameter(format!("depth_{depth}")),
            &tree,
            |b, tree| b.iter(|| diff(black_box(tree), black_box(tree))),
        );
    }

    group.finish();
}

fn bench_diff_attr_changes(c: &mut Criterion) {
    let mut group = c.benchmark_group("diff_attr_changes");

    // Single attribute change
    let old_single = VNode::element("div")
        .with_djust_id("0")
        .with_attr("class", "old-class");
    let new_single = VNode::element("div")
        .with_djust_id("0")
        .with_attr("class", "new-class");

    group.bench_function("single_attr", |b| {
        b.iter(|| diff(black_box(&old_single), black_box(&new_single)))
    });

    // Multiple attribute changes
    let old_multi = VNode::element("div")
        .with_djust_id("0")
        .with_attr("class", "old-class")
        .with_attr("id", "old-id")
        .with_attr("data-value", "old")
        .with_attr("style", "color: red");
    let new_multi = VNode::element("div")
        .with_djust_id("0")
        .with_attr("class", "new-class")
        .with_attr("id", "new-id")
        .with_attr("data-value", "new")
        .with_attr("style", "color: blue");

    group.bench_function("multiple_attrs", |b| {
        b.iter(|| diff(black_box(&old_multi), black_box(&new_multi)))
    });

    // Attribute add/remove
    let old_mixed = VNode::element("div")
        .with_djust_id("0")
        .with_attr("class", "same")
        .with_attr("old-attr", "remove-me");
    let new_mixed = VNode::element("div")
        .with_djust_id("0")
        .with_attr("class", "same")
        .with_attr("new-attr", "added");

    group.bench_function("attr_add_remove", |b| {
        b.iter(|| diff(black_box(&old_mixed), black_box(&new_mixed)))
    });

    group.finish();
}

fn bench_diff_text_changes(c: &mut Criterion) {
    let mut group = c.benchmark_group("diff_text_changes");

    let old_short = VNode::text("Hello");
    let new_short = VNode::text("World");

    group.bench_function("short_text", |b| {
        b.iter(|| diff(black_box(&old_short), black_box(&new_short)))
    });

    let old_long = VNode::text("x".repeat(1000));
    let new_long = VNode::text("y".repeat(1000));

    group.bench_function("long_text", |b| {
        b.iter(|| diff(black_box(&old_long), black_box(&new_long)))
    });

    group.finish();
}

fn bench_diff_children(c: &mut Criterion) {
    let mut group = c.benchmark_group("diff_children");

    // Append child
    let old_append = VNode::element("ul").with_djust_id("0").with_child(
        VNode::element("li")
            .with_djust_id("1")
            .with_child(VNode::text("Item 1")),
    );
    let new_append = VNode::element("ul")
        .with_djust_id("0")
        .with_child(
            VNode::element("li")
                .with_djust_id("1")
                .with_child(VNode::text("Item 1")),
        )
        .with_child(
            VNode::element("li")
                .with_djust_id("2")
                .with_child(VNode::text("Item 2")),
        );

    group.bench_function("append_child", |b| {
        b.iter(|| diff(black_box(&old_append), black_box(&new_append)))
    });

    // Remove child
    group.bench_function("remove_child", |b| {
        b.iter(|| diff(black_box(&new_append), black_box(&old_append)))
    });

    // Many children unchanged
    let children: Vec<VNode> = (0..20)
        .map(|i| {
            VNode::element("li")
                .with_djust_id(format!("{i}"))
                .with_child(VNode::text(format!("Item {i}")))
        })
        .collect();
    let old_many = VNode::element("ul")
        .with_djust_id("list")
        .with_children(children.clone());
    let new_many = VNode::element("ul")
        .with_djust_id("list")
        .with_children(children);

    group.bench_function("many_children_unchanged", |b| {
        b.iter(|| diff(black_box(&old_many), black_box(&new_many)))
    });

    group.finish();
}

fn bench_diff_keyed_children(c: &mut Criterion) {
    let mut group = c.benchmark_group("diff_keyed_children");

    // Keyed list reorder
    let old_keyed = VNode::element("ul").with_djust_id("0").with_children(vec![
        VNode::element("li")
            .with_key("a")
            .with_djust_id("1")
            .with_child(VNode::text("A")),
        VNode::element("li")
            .with_key("b")
            .with_djust_id("2")
            .with_child(VNode::text("B")),
        VNode::element("li")
            .with_key("c")
            .with_djust_id("3")
            .with_child(VNode::text("C")),
    ]);

    let new_keyed = VNode::element("ul").with_djust_id("0").with_children(vec![
        VNode::element("li")
            .with_key("c")
            .with_djust_id("3")
            .with_child(VNode::text("C")),
        VNode::element("li")
            .with_key("a")
            .with_djust_id("1")
            .with_child(VNode::text("A")),
        VNode::element("li")
            .with_key("b")
            .with_djust_id("2")
            .with_child(VNode::text("B")),
    ]);

    group.bench_function("reorder_3_items", |b| {
        b.iter(|| diff(black_box(&old_keyed), black_box(&new_keyed)))
    });

    // Larger keyed list
    let old_large: Vec<VNode> = (0..50)
        .map(|i| {
            VNode::element("li")
                .with_key(format!("key-{i}"))
                .with_djust_id(format!("{i}"))
                .with_child(VNode::text(format!("Item {i}")))
        })
        .collect();

    // Reverse order
    let new_large: Vec<VNode> = (0..50)
        .rev()
        .map(|i| {
            VNode::element("li")
                .with_key(format!("key-{i}"))
                .with_djust_id(format!("{i}"))
                .with_child(VNode::text(format!("Item {i}")))
        })
        .collect();

    let old_ul = VNode::element("ul")
        .with_djust_id("list")
        .with_children(old_large);
    let new_ul = VNode::element("ul")
        .with_djust_id("list")
        .with_children(new_large);

    group.bench_function("reverse_50_items", |b| {
        b.iter(|| diff(black_box(&old_ul), black_box(&new_ul)))
    });

    group.finish();
}

fn bench_diff_tag_replace(c: &mut Criterion) {
    let mut group = c.benchmark_group("diff_tag_replace");

    let old_tag = VNode::element("div")
        .with_djust_id("0")
        .with_attr("class", "container")
        .with_child(VNode::text("content"));
    let new_tag = VNode::element("span")
        .with_djust_id("0")
        .with_attr("class", "container")
        .with_child(VNode::text("content"));

    group.bench_function("simple_replace", |b| {
        b.iter(|| diff(black_box(&old_tag), black_box(&new_tag)))
    });

    group.finish();
}

fn bench_diff_real_world(c: &mut Criterion) {
    let mut group = c.benchmark_group("diff_real_world");

    // Form validation scenario: error message appears
    let form_valid = VNode::element("form")
        .with_djust_id("form")
        .with_children(vec![
            VNode::element("div")
                .with_attr("class", "mb-3")
                .with_djust_id("f1")
                .with_children(vec![VNode::element("input")
                    .with_attr("class", "form-control")
                    .with_attr("name", "username")
                    .with_djust_id("i1")]),
            VNode::element("button")
                .with_attr("type", "submit")
                .with_djust_id("btn")
                .with_child(VNode::text("Submit")),
        ]);

    let form_invalid = VNode::element("form")
        .with_djust_id("form")
        .with_children(vec![
            VNode::element("div")
                .with_attr("class", "mb-3")
                .with_djust_id("f1")
                .with_children(vec![
                    VNode::element("input")
                        .with_attr("class", "form-control is-invalid")
                        .with_attr("name", "username")
                        .with_djust_id("i1"),
                    VNode::element("div")
                        .with_attr("class", "invalid-feedback")
                        .with_djust_id("e1")
                        .with_child(VNode::text("Username is required")),
                ]),
            VNode::element("button")
                .with_attr("type", "submit")
                .with_djust_id("btn")
                .with_child(VNode::text("Submit")),
        ]);

    group.bench_function("form_show_validation", |b| {
        b.iter(|| diff(black_box(&form_valid), black_box(&form_invalid)))
    });

    group.bench_function("form_hide_validation", |b| {
        b.iter(|| diff(black_box(&form_invalid), black_box(&form_valid)))
    });

    // Todo list scenario: toggle item completion
    let create_todo_item = |id: usize, completed: bool| {
        let class = if completed {
            "todo-item completed"
        } else {
            "todo-item"
        };
        VNode::element("li")
            .with_key(format!("todo-{id}"))
            .with_attr("class", class)
            .with_djust_id(format!("t{id}"))
            .with_children(vec![
                VNode::element("input")
                    .with_attr("type", "checkbox")
                    .with_attr("checked", if completed { "checked" } else { "" })
                    .with_djust_id(format!("cb{id}")),
                VNode::element("span")
                    .with_djust_id(format!("txt{id}"))
                    .with_child(VNode::text(format!("Todo item {id}"))),
            ])
    };

    let todo_old = VNode::element("ul")
        .with_djust_id("todos")
        .with_children((0..10).map(|i| create_todo_item(i, i % 2 == 0)).collect());

    let todo_new = VNode::element("ul")
        .with_djust_id("todos")
        .with_children((0..10).map(|i| create_todo_item(i, i % 2 == 1)).collect());

    group.bench_function("todo_toggle_all", |b| {
        b.iter(|| diff(black_box(&todo_old), black_box(&todo_new)))
    });

    group.finish();
}

fn bench_diff_deep_trees(c: &mut Criterion) {
    let mut group = c.benchmark_group("diff_deep_trees");

    for (depth, width) in [(3, 2), (4, 2), (3, 3), (5, 2)].iter() {
        let old_tree = create_tree(*depth, *width, "old");
        let new_tree = create_tree(*depth, *width, "new");

        group.bench_with_input(
            BenchmarkId::from_parameter(format!("d{depth}_w{width}")),
            &(old_tree, new_tree),
            |b, (old, new)| b.iter(|| diff(black_box(old), black_box(new))),
        );
    }

    group.finish();
}

criterion_group!(
    benches,
    bench_diff_no_changes,
    bench_diff_attr_changes,
    bench_diff_text_changes,
    bench_diff_children,
    bench_diff_keyed_children,
    bench_diff_tag_replace,
    bench_diff_real_world,
    bench_diff_deep_trees,
);
criterion_main!(benches);
