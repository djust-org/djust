//! The lazily-hydrated containers a render ACTUALLY emitted (#3252).
//!
//! [`crate::parser::collect_lazy_containers`] answers "what is AUTHORED"; it
//! cannot answer "what rendered", because a container inside an `{% if %}` that
//! did not run is still authored. Registration needs the second answer — a
//! container in a branch that did not render must not register — so the
//! renderer records what it emits here, per render, on this thread.
//!
//! Why this is not a marker in the output: a constant marker attribute is
//! copyable into user HTML (including through an intentional `|safe` path), so
//! it authorizes nothing. This record is written only by the renderer, for text
//! that came from a [`crate::Node::Text`] — developer-authored template text,
//! which attacker data can never enter because it arrives as
//! [`crate::Node::Variable`] instead. The property is structural.
//!
//! Thread-local and drained by the caller, mirroring `registry_scope`'s
//! per-render `CURRENT`: the whole page render runs on one thread, and the
//! caller reads the record immediately after it.

use crate::parser::{scan_lazy_containers_in_text, AuthoredLazyContainer};
use std::cell::RefCell;

thread_local! {
    static EMITTED: RefCell<Vec<AuthoredLazyContainer>> = const { RefCell::new(Vec::new()) };
}

/// Forget anything recorded so far. Call immediately BEFORE a page render so a
/// reused thread cannot carry a previous render's containers into this one.
pub fn reset() {
    EMITTED.with(|cell| cell.borrow_mut().clear());
}

/// Record the containers in one `Node::Text` about to be emitted.
///
/// Guarded by a `contains` check: the scan is tag-aware and allocates, and
/// almost every text node in a template has nothing to do with lazy slots.
pub(crate) fn record_text(text: &str) {
    if !text.contains("dj-lazy") {
        return;
    }
    let mut found = Vec::new();
    scan_lazy_containers_in_text(text, &mut found);
    if found.is_empty() {
        return;
    }
    // Order and multiplicity preserved: two identical containers are two
    // containers, and registration must give each its own id (#3252).
    EMITTED.with(|cell| cell.borrow_mut().extend(found));
}

/// Take everything recorded since the last [`reset`], leaving the record empty.
pub fn take() -> Vec<AuthoredLazyContainer> {
    EMITTED.with(|cell| std::mem::take(&mut *cell.borrow_mut()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn records_what_a_text_node_carries_and_nothing_else() {
        reset();
        record_text(r#"<div dj-view="app.Room" dj-lazy></div>"#);
        record_text("<p>nothing lazy here</p>");
        let taken = take();
        assert_eq!(
            taken,
            vec![AuthoredLazyContainer {
                view_path: "app.Room".into(),
                trigger: None,
            }]
        );
        assert!(take().is_empty(), "take must leave the record empty");
    }

    #[test]
    fn a_text_without_the_lazy_attribute_records_nothing() {
        reset();
        record_text(r#"<div dj-view="app.Room"></div>"#);
        assert!(take().is_empty());
    }

    /// The whole point of recording at the renderer rather than at the parser:
    /// a container in a branch that did not run is AUTHORED (the parser's
    /// `collect_lazy_containers` returns both) but never EMITTED, so it must
    /// not reach registration.
    #[test]
    fn only_emitted_containers_are_recorded() {
        let source = concat!(
            r#"{% if flag %}<div dj-view="app.Unrendered" dj-lazy></div>{% endif %}"#,
            r#"<div dj-view="app.Rendered" dj-lazy></div>"#,
        );
        let template = crate::Template::new(source).expect("parse");
        let mut context = crate::Context::new();
        context.set("flag".to_string(), djust_core::Value::Bool(false));

        reset();
        let html = template.render(&context).expect("render");
        assert!(html.contains("app.Rendered"), "render sanity: {html}");

        let paths: Vec<String> = take().into_iter().map(|c| c.view_path).collect();
        assert_eq!(paths, vec!["app.Rendered".to_string()]);
    }
}
