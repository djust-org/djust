//! #4124: "reads unknown keys" (`"*"`, re-render this node) is not "may
//! bind names" (re-render every later sibling). Only the second latches the
//! partial renderer's later siblings.

use djust_core::{Context, Result, Value};
use djust_templates::inheritance::TemplateLoader;
use djust_templates::parser::Node;
use djust_templates::renderer::{node_may_bind_names, render_nodes_partial};
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

fn var(name: &str) -> Node {
    Node::Variable(name.to_string(), Vec::new(), false)
}

fn firstof_as() -> Node {
    Node::FirstOf {
        args: vec!["p".into()],
        asvar: Some("v".into()),
    }
}

fn deps(keys: &[&str]) -> HashSet<String> {
    keys.iter().map(|k| k.to_string()).collect()
}

/// Render `[first, {{ x }}]` with `first` marked `"*"`, against a cache
/// that holds a STALE `{{ x }}` fragment, with NO changed keys.
fn later_sibling_rerendered(first: Node) -> bool {
    let nodes = vec![first, var("x")];
    let node_deps = vec![deps(&["*"]), deps(&["x"])];
    let mut context = Context::new();
    context.set("x".to_string(), Value::String("new".to_string()));
    context.set("p".to_string(), Value::String("p".to_string()));
    let cache = vec![String::new(), "stale".to_string()];
    let (html, _fragments, changed) = render_nodes_partial::<NoLoader>(
        &nodes,
        &node_deps,
        &context,
        None,
        &HashSet::new(),
        &cache,
    )
    .unwrap();
    assert!(
        changed.contains(&0),
        "a wildcard node always re-renders itself"
    );
    let rerendered = changed.contains(&1);
    assert_eq!(html.ends_with("new"), rerendered, "{html}");
    assert_eq!(html.ends_with("stale"), !rerendered, "{html}");
    rerendered
}

#[test]
fn a_wildcard_that_binds_nothing_does_not_latch_later_siblings() {
    // Gate off: reads-unknown-keys only. The later node's own key did not
    // change, so its cached fragment is reused.
    assert!(!later_sibling_rerendered(Node::Text("t".into())));
    assert!(!later_sibling_rerendered(Node::If {
        condition: "p".into(),
        true_nodes: vec![var("p")],
        false_nodes: vec![],
        in_tag_context: false,
        marker_id: None,
    }));
}

#[test]
fn a_wildcard_that_may_bind_still_latches_later_siblings() {
    // Gate on: `{% firstof p as v %}`, alone or nested in a wrapper that
    // renders in the enclosing context, may bind what follows reads.
    assert!(later_sibling_rerendered(firstof_as()));
    assert!(later_sibling_rerendered(Node::If {
        condition: "p".into(),
        true_nodes: vec![firstof_as()],
        false_nodes: vec![],
        in_tag_context: false,
        marker_id: None,
    }));
    assert!(later_sibling_rerendered(Node::With {
        assignments: vec![("a".into(), "p".into())],
        nodes: vec![firstof_as()],
    }));
}

#[test]
fn binding_capability_is_conservative() {
    assert!(!node_may_bind_names(&var("x")));
    assert!(!node_may_bind_names(&Node::FirstOf {
        args: vec!["p".into()],
        asvar: None,
    }));
    // A tag with no bindings-returning handler cannot write the context.
    assert!(!node_may_bind_names(&Node::CustomTag {
        name: "unregistered_4124".into(),
        args: vec!["as".into(), "v".into()],
    }));
    for binding in [
        firstof_as(),
        Node::AssignTag {
            name: "a".into(),
            args: vec![],
        },
        Node::Include {
            origin: None,
            template: "'p.html'".into(),
            with_vars: vec![],
            only: false,
            site_id: None,
        },
        Node::Cycle {
            values: vec!["'a'".into()],
            name: Some("c".into()),
            silent: false,
            id: "c".into(),
            reference: false,
        },
        Node::For {
            var_names: vec!["e".into()],
            iterable: "entries".into(),
            reversed: false,
            nodes: vec![],
            empty_nodes: vec![firstof_as()],
        },
    ] {
        assert!(node_may_bind_names(&binding), "{binding:?}");
    }
}
