//! Benchmarks for component rendering
//!
//! These benchmarks measure:
//! - Simple component rendering (Button, Badge, Alert, Card)
//! - HTML builder performance
//! - Element builder performance

use criterion::{criterion_group, criterion_main, Criterion};
use djust_components::html::{element, HtmlBuilder};
use djust_components::{RustAlert, RustBadge, RustButton, RustCard};
use std::hint::black_box;

fn bench_button_render(c: &mut Criterion) {
    let mut group = c.benchmark_group("button_render");

    // Basic button
    let button_basic = RustButton::new("Click me".to_string(), "primary", "md", false, false);
    group.bench_function("basic", |b| b.iter(|| button_basic.render()));

    // Button with all options
    let button_full = RustButton::new("Submit".to_string(), "success", "lg", false, true);
    group.bench_function("with_options", |b| b.iter(|| button_full.render()));

    // Disabled button
    let button_disabled = RustButton::new("Disabled".to_string(), "secondary", "sm", true, false);
    group.bench_function("disabled", |b| b.iter(|| button_disabled.render()));

    group.finish();
}

fn bench_badge_render(c: &mut Criterion) {
    let mut group = c.benchmark_group("badge_render");

    // Basic badge
    let badge_basic = RustBadge::new("New".to_string(), "primary", "md", false);
    group.bench_function("basic", |b| b.iter(|| badge_basic.render()));

    // Pill badge
    let badge_pill = RustBadge::new("5".to_string(), "danger", "sm", true);
    group.bench_function("pill", |b| b.iter(|| badge_pill.render()));

    // Large badge
    let badge_large = RustBadge::new("Featured".to_string(), "success", "lg", false);
    group.bench_function("large", |b| b.iter(|| badge_large.render()));

    group.finish();
}

fn bench_alert_render(c: &mut Criterion) {
    let mut group = c.benchmark_group("alert_render");

    // Basic alert
    let alert_basic = RustAlert::new("This is an info alert".to_string(), "info", false);
    group.bench_function("basic", |b| b.iter(|| alert_basic.render()));

    // Dismissable alert
    let alert_dismissable = RustAlert::new("Click X to dismiss".to_string(), "warning", true);
    group.bench_function("dismissable", |b| b.iter(|| alert_dismissable.render()));

    // Long message
    let long_message = "This is a much longer alert message that contains more content to render. It simulates a real-world scenario where the alert might contain detailed information or instructions for the user.".to_string();
    let alert_long = RustAlert::new(long_message, "danger", false);
    group.bench_function("long_message", |b| b.iter(|| alert_long.render()));

    group.finish();
}

fn bench_card_render(c: &mut Criterion) {
    let mut group = c.benchmark_group("card_render");

    // Basic card (body only)
    let card_basic = RustCard::new("This is a card body".to_string(), None, None, "default");
    group.bench_function("basic", |b| b.iter(|| card_basic.render()));

    // Card with header
    let card_header = RustCard::new(
        "Card content goes here".to_string(),
        Some("Card Title".to_string()),
        None,
        "default",
    );
    group.bench_function("with_header", |b| b.iter(|| card_header.render()));

    // Card with header and footer
    let card_full = RustCard::new(
        "Full card content".to_string(),
        Some("Header".to_string()),
        Some("Footer text".to_string()),
        "elevated",
    );
    group.bench_function("full", |b| b.iter(|| card_full.render()));

    group.finish();
}

fn bench_html_builder(c: &mut Criterion) {
    let mut group = c.benchmark_group("html_builder");

    // Simple div
    group.bench_function("simple_div", |b| {
        b.iter(|| {
            HtmlBuilder::new()
                .start_tag("div")
                .class("container")
                .id("main")
                .close_start()
                .text("Hello World")
                .end_tag("div")
                .build()
        })
    });

    // Multiple attributes
    group.bench_function("multiple_attrs", |b| {
        b.iter(|| {
            HtmlBuilder::new()
                .start_tag("input")
                .attr("type", "text")
                .attr("name", "username")
                .attr("placeholder", "Enter username")
                .attr("required", "required")
                .attr("autocomplete", "username")
                .attr("data-validation", "required|min:3")
                .close_start()
                .build()
        })
    });

    // Nested structure
    group.bench_function("nested", |b| {
        b.iter(|| {
            HtmlBuilder::new()
                .start_tag("div")
                .class("card")
                .close_start()
                .raw("<div class=\"card-header\">")
                .text("Title")
                .raw("</div>")
                .raw("<div class=\"card-body\">")
                .text("Content")
                .raw("</div>")
                .end_tag("div")
                .build()
        })
    });

    group.finish();
}

fn bench_element_builder(c: &mut Criterion) {
    let mut group = c.benchmark_group("element_builder");

    // Simple element
    group.bench_function("simple", |b| {
        b.iter(|| {
            element("div")
                .class("container")
                .attr("id", "main")
                .text("Hello World")
                .build()
        })
    });

    // Button with multiple classes
    group.bench_function("button_classes", |b| {
        b.iter(|| {
            element("button")
                .class("btn")
                .class("btn-primary")
                .class("btn-lg")
                .attr("type", "submit")
                .text("Submit")
                .build()
        })
    });

    // Self-closing input
    group.bench_function("self_closing_input", |b| {
        b.iter(|| {
            element("input")
                .attr("type", "email")
                .attr("name", "email")
                .attr("placeholder", "Enter email")
                .class("form-control")
                .self_closing()
                .build()
        })
    });

    // Complex card structure
    group.bench_function("complex_card", |b| {
        b.iter(|| {
            let header = element("div")
                .class("card-header")
                .text("Card Title")
                .build();

            let body = element("div")
                .class("card-body")
                .child(element("h5").text("Subtitle").build())
                .child(element("p").text("Card content here").build())
                .build();

            element("div")
                .class("card")
                .child(header)
                .child(body)
                .build()
        })
    });

    group.finish();
}

fn bench_multiple_components(c: &mut Criterion) {
    let mut group = c.benchmark_group("multiple_components");

    // Render typical form UI
    group.bench_function("form_with_5_components", |b| {
        let submit_btn = RustButton::new("Submit".to_string(), "primary", "md", false, false);
        let cancel_btn = RustButton::new("Cancel".to_string(), "secondary", "md", false, true);
        let alert = RustAlert::new("Please fill all required fields".to_string(), "info", false);
        let required_badge = RustBadge::new("Required".to_string(), "danger", "sm", false);
        let optional_badge = RustBadge::new("Optional".to_string(), "secondary", "sm", false);

        b.iter(|| {
            let _ = black_box(submit_btn.render());
            let _ = black_box(cancel_btn.render());
            let _ = black_box(alert.render());
            let _ = black_box(required_badge.render());
            let _ = black_box(optional_badge.render());
        })
    });

    // Render dashboard cards
    group.bench_function("dashboard_10_cards", |b| {
        let cards: Vec<RustCard> = (0..10)
            .map(|i| {
                RustCard::new(
                    format!("Content for dashboard card {i}"),
                    Some(format!("Card {i}")),
                    None,
                    if i % 2 == 0 { "elevated" } else { "outlined" },
                )
            })
            .collect();

        b.iter(|| {
            for card in &cards {
                let _ = black_box(card.render());
            }
        })
    });

    // Mix of all component types
    group.bench_function("mixed_20_components", |b| {
        let buttons: Vec<RustButton> = (0..5)
            .map(|i| RustButton::new(format!("Button {i}"), "primary", "md", false, false))
            .collect();
        let badges: Vec<RustBadge> = (0..5)
            .map(|i| RustBadge::new(format!("{i}"), "info", "sm", true))
            .collect();
        let alerts: Vec<RustAlert> = (0..5)
            .map(|i| RustAlert::new(format!("Alert message {i}"), "warning", false))
            .collect();
        let cards: Vec<RustCard> = (0..5)
            .map(|i| {
                RustCard::new(
                    format!("Card body {i}"),
                    Some(format!("Title {i}")),
                    None,
                    "default",
                )
            })
            .collect();

        b.iter(|| {
            for btn in &buttons {
                let _ = black_box(btn.render());
            }
            for badge in &badges {
                let _ = black_box(badge.render());
            }
            for alert in &alerts {
                let _ = black_box(alert.render());
            }
            for card in &cards {
                let _ = black_box(card.render());
            }
        })
    });

    group.finish();
}

fn bench_html_escape(c: &mut Criterion) {
    let mut group = c.benchmark_group("html_escape");

    // Component with XSS attempt (tests escape overhead)
    let malicious_text = "<script>alert('xss')</script>".to_string();
    let button_xss = RustButton::new(malicious_text.clone(), "danger", "md", false, false);

    group.bench_function("button_with_escape", |b| b.iter(|| button_xss.render()));

    // Long text with special characters
    let long_special =
        "User & Admin <roles> with \"permissions\" and 'quotes' - multiple & entities & more"
            .repeat(5);
    let alert_special = RustAlert::new(long_special, "info", false);

    group.bench_function("alert_long_special_chars", |b| {
        b.iter(|| alert_special.render())
    });

    group.finish();
}

criterion_group!(
    benches,
    bench_button_render,
    bench_badge_render,
    bench_alert_render,
    bench_card_render,
    bench_html_builder,
    bench_element_builder,
    bench_multiple_components,
    bench_html_escape,
);
criterion_main!(benches);
