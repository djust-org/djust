//! #4315 slice 2: the partial renderer caches a top-level `{% if %}` /
//! `{% with %}` / `{% block %}` body child by child, so a nested loop whose
//! own inputs did not change is reused even though the wrapper re-renders.
//!
//! The skip is observed directly: the loop's input is mutated but NOT
//! reported as changed, so a reused child still shows the OLD rows.

use djust_core::{Context, Result, Value};
use djust_templates::inheritance::TemplateLoader;
use djust_templates::parser::Node;
use djust_templates::renderer::PartialRender;
use djust_templates::Template;
use std::collections::HashSet;

struct NoLoader;

impl TemplateLoader for NoLoader {
    fn shared_handle(&self) -> std::sync::Arc<dyn TemplateLoader + Send + Sync> {
        std::sync::Arc::new(NoLoader)
    }

    fn load_template(&self, name: &str) -> Result<Vec<Node>> {
        Err(djust_core::DjangoRustError::TemplateError(format!(
            "no loader for {name}"
        )))
    }
}

const LOOP: &str = "<ul>{% for e in entries %}<li>{{ e }}</li>{% endfor %}</ul>";

fn context(count: i64, entries: &[&str]) -> Context {
    let mut context = Context::new();
    context.set("count".to_string(), Value::Integer(count));
    context.set("show".to_string(), Value::Bool(true));
    context.set(
        "entries".to_string(),
        Value::List(
            entries
                .iter()
                .map(|e| Value::String(e.to_string()))
                .collect(),
        ),
    );
    context
}

fn keys(keys: &[&str]) -> HashSet<String> {
    keys.iter().map(|k| k.to_string()).collect()
}

/// First render with `count=1, entries=[old]`, then a partial render with
/// `count=2, entries=[new]` reporting `reported`. `with_children=false` runs
/// the same partial render without the per-child cache (the gate off).
fn rerender(body: &str, reported: &[&str], with_children: bool) -> PartialRender {
    let template = Template::new(body).unwrap();
    let first = template
        .render_with_loader_collecting_children(&context(1, &["old"]), &NoLoader)
        .unwrap();
    let children = if with_children {
        first.children
    } else {
        Vec::new()
    };
    template
        .render_with_loader_partial_children(
            &context(2, &["new"]),
            &NoLoader,
            &keys(reported),
            &first.fragments,
            &children,
        )
        .unwrap()
}

fn wrapped() -> Vec<String> {
    vec![
        format!("{{% if show %}}<p>{{{{ count }}}}</p>{LOOP}{{% endif %}}"),
        format!("{{% if count %}}<p>{{{{ count }}}}</p>{LOOP}{{% endif %}}"),
        format!("{{% if not show %}}x{{% else %}}<p>{{{{ count }}}}</p>{LOOP}{{% endif %}}"),
        format!("{{% with c=count %}}<p>{{{{ c }}}}</p>{LOOP}{{% endwith %}}"),
    ]
}

#[test]
fn a_nested_loop_whose_inputs_did_not_change_is_reused() {
    for body in wrapped() {
        let html = rerender(&body, &["count"], true).html;
        assert!(html.contains("<p>2</p>"), "{body}: {html}");
        assert!(
            html.contains("old") && !html.contains("new"),
            "{body}: {html}"
        );
    }
}

#[test]
fn without_the_child_cache_the_loop_is_redrawn() {
    // Gate off: the same render with no child entries redraws the loop.
    for body in wrapped() {
        let html = rerender(&body, &["count"], false).html;
        assert!(
            html.contains("new") && !html.contains("old"),
            "{body}: {html}"
        );
    }
}

#[test]
fn a_nested_loop_whose_inputs_changed_is_redrawn() {
    for body in wrapped() {
        let html = rerender(&body, &["count", "entries"], true).html;
        assert!(
            html.contains("new") && !html.contains("old"),
            "{body}: {html}"
        );
    }
}

#[test]
fn a_binding_child_redraws_the_children_after_it() {
    let body =
        format!("{{% if show %}}{{% firstof count as f %}}<i>{{{{ f }}}}</i>{LOOP}{{% endif %}}");
    let html = rerender(&body, &["count"], true).html;
    assert!(html.contains("<i>2</i>"), "{html}");
    assert!(html.contains("new") && !html.contains("old"), "{html}");
}

#[test]
fn a_binding_child_after_the_loop_leaves_it_reusable() {
    let body =
        format!("{{% if show %}}{LOOP}{{% firstof count as f %}}<i>{{{{ f }}}}</i>{{% endif %}}");
    let html = rerender(&body, &["count"], true).html;
    assert!(html.contains("<i>2</i>"), "{html}");
    assert!(html.contains("old") && !html.contains("new"), "{html}");
}

#[test]
fn a_top_level_binding_before_the_wrapper_redraws_its_children() {
    let body =
        format!("{{% firstof count as f %}}{{% if show %}}<i>{{{{ f }}}}</i>{LOOP}{{% endif %}}");
    let html = rerender(&body, &["count"], true).html;
    assert!(html.contains("new") && !html.contains("old"), "{html}");
}

#[test]
fn a_child_entry_is_ignored_once_its_top_level_fragment_is_replaced() {
    let body = format!("{{% if show %}}<p>{{{{ count }}}}</p>{LOOP}{{% endif %}}");
    let template = Template::new(&body).unwrap();
    let first = template
        .render_with_loader_collecting_children(&context(1, &["old"]), &NoLoader)
        .unwrap();
    // The caller replaced the top-level fragment but kept the child entry.
    let replaced: Vec<String> = first.fragments.iter().map(|f| format!("{f} ")).collect();
    let html = template
        .render_with_loader_partial_children(
            &context(2, &["new"]),
            &NoLoader,
            &keys(&["count"]),
            &replaced,
            &first.children,
        )
        .unwrap()
        .html;
    assert!(html.contains("new") && !html.contains("old"), "{html}");
}

#[test]
fn partial_render_equals_a_full_render() {
    let bodies = wrapped().into_iter().chain([
        format!("{{% if count > 1 %}}<p>{{{{ count }}}}</p>{LOOP}{{% else %}}<em>{{{{ count }}}}</em>{{% endif %}}"),
        format!("{{% if show %}}{{% firstof count as f %}}<i>{{{{ f }}}}</i>{LOOP}{{% endif %}}"),
    ]);
    let steps: [(i64, &[&str], &[&str]); 4] = [
        (2, &["a"], &["count"]),
        (2, &["b", "c"], &["entries"]),
        (3, &["d"], &["count", "entries"]),
        (3, &["d"], &[]),
    ];
    for body in bodies {
        let template = Template::new(&body).unwrap();
        let mut rendered = template
            .render_with_loader_collecting_children(&context(1, &["a"]), &NoLoader)
            .unwrap();
        for (count, entries, reported) in steps {
            let ctx = context(count, entries);
            rendered = template
                .render_with_loader_partial_children(
                    &ctx,
                    &NoLoader,
                    &keys(reported),
                    &rendered.fragments,
                    &rendered.children,
                )
                .unwrap();
            let full = template.render_with_loader(&ctx, &NoLoader).unwrap();
            assert_eq!(rendered.html, full, "{body} at count={count}");
        }
    }
}

/// #4315 review: a `READS_ONLY_ARGS` tag's operands are parsed strictly, and
/// anything that does not parse cleanly keeps the `"*"` dependency.
#[test]
fn reads_only_operand_roots_names_every_operand_or_refuses() {
    use djust_templates::parser::reads_only_operand_roots;
    let roots = |args: &[&str]| {
        reads_only_operand_roots(&args.iter().map(|a| a.to_string()).collect::<Vec<_>>())
    };
    // An `=` inside a filter literal is not a keyword split.
    assert_eq!(roots(&["'xs'", "v|default:'a=b'"]), Some(vec!["v".into()]));
    // A value that starts with a literal still reads its filter arguments.
    assert_eq!(roots(&["'xs'", "s=\"p\"|add:v"]), Some(vec!["v".into()]));
    assert_eq!(roots(&["asset|add:'?v=2'"]), Some(vec!["asset".into()]));
    assert_eq!(
        roots(&["e.id", "k=obj.items.0|default:w", "3", "-1.5"]),
        Some(vec!["e".into(), "obj".into(), "w".into()])
    );
    for unclean in [
        &["\"open"][..],
        &["v|default:'open"],
        &["_(\"x\")"],
        &["v|"],
        &["v||upper"],
        &[""],
        &["a b"],
        &["=v"],
    ] {
        assert_eq!(roots(unclean), None, "{unclean:?}");
    }
}
