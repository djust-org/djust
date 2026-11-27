//! The `dj-model` bindings a render emits from template text.
//!
//! A view's dj-model write allowlist is the set of `dj-model="<field>"`
//! attributes that come from its template's own text (`Node::Text`) AND
//! rendered for the current state. This module records them during the real
//! render, so nothing renders twice:
//!
//! * [`record_text`] is called for every `Node::Text` the renderer emits. Only
//!   template text is ever recorded; variables, filters and tag output never
//!   reach it, so rendered data cannot add a binding.
//! * [`Suppress`] is held while a custom tag (inline, block, raw block, assign)
//!   or an `{% ifchanged %}` runs: their handler decides what of the body
//!   reaches the output (a permission-gate tag may drop it), so text inside
//!   them never counts (fail closed; list such fields in
//!   `allowed_model_fields`).
//! * Output reused from a cache without rendering contributes what it recorded
//!   when it was rendered: the partial renderer replays per-node records
//!   ([`begin_node`] / [`end_node`] / [`reuse_node`]); the loop caches are not
//!   used for a body that may emit a binding while a recording runs
//!   ([`nodes_may_emit_binding`]).
//!
//! A recording is a stack frame on a thread-local: a nested render (a Python
//! tag rendering a template through another entry point) pushes its own frame
//! or a [`Pause`], so it never adds to the outer render's set.

use crate::parser::Node;
use std::cell::RefCell;
use std::collections::HashSet;

#[derive(Default)]
struct Frame {
    fields: Vec<String>,
    suppressed: usize,
    previous_nodes: Vec<Vec<String>>,
    nodes: Vec<Vec<String>>,
}

thread_local! {
    static FRAMES: RefCell<Vec<Option<Frame>>> = const { RefCell::new(Vec::new()) };
}

fn with_top<T>(f: impl FnOnce(&mut Frame) -> T) -> Option<T> {
    FRAMES.with(|frames| {
        frames
            .borrow_mut()
            .last_mut()
            .and_then(|top| top.as_mut())
            .map(f)
    })
}

/// A recording for one render. Dropping it without [`Recording::finish`]
/// (an error or a panic) discards what it recorded.
pub struct Recording {
    depth: usize,
    finished: bool,
}

impl Recording {
    /// Start recording. `previous_nodes` are the per-top-level-node records of
    /// the render whose node cache the partial renderer may reuse.
    pub fn begin(previous_nodes: Vec<Vec<String>>) -> Self {
        let depth = FRAMES.with(|frames| {
            let mut frames = frames.borrow_mut();
            frames.push(Some(Frame {
                previous_nodes,
                ..Frame::default()
            }));
            frames.len()
        });
        Self {
            depth,
            finished: false,
        }
    }

    /// The sorted, deduplicated fields, and the per-node records for the next
    /// partial render.
    pub fn finish(mut self) -> (Vec<String>, Vec<Vec<String>>) {
        self.finished = true;
        let frame = FRAMES.with(|frames| {
            let mut frames = frames.borrow_mut();
            if frames.len() == self.depth {
                frames.pop().flatten()
            } else {
                None
            }
        });
        let Some(frame) = frame else {
            return (Vec::new(), Vec::new());
        };
        let mut fields = frame.fields;
        fields.sort();
        fields.dedup();
        (fields, frame.nodes)
    }
}

impl Drop for Recording {
    fn drop(&mut self) {
        if !self.finished {
            FRAMES.with(|frames| {
                let mut frames = frames.borrow_mut();
                if frames.len() == self.depth {
                    frames.pop();
                }
            });
        }
    }
}

/// A render that records nothing and keeps any outer recording untouched.
pub struct Pause {
    depth: usize,
}

impl Pause {
    pub fn begin() -> Self {
        let depth = FRAMES.with(|frames| {
            let mut frames = frames.borrow_mut();
            frames.push(None);
            frames.len()
        });
        Self { depth }
    }
}

impl Drop for Pause {
    fn drop(&mut self) {
        FRAMES.with(|frames| {
            let mut frames = frames.borrow_mut();
            if frames.len() == self.depth {
                frames.pop();
            }
        });
    }
}

/// Text rendered while this is held does not count (custom tag bodies).
pub struct Suppress(bool);

impl Suppress {
    pub fn begin() -> Self {
        Self(with_top(|frame| frame.suppressed += 1).is_some())
    }
}

impl Drop for Suppress {
    fn drop(&mut self) {
        if self.0 {
            with_top(|frame| frame.suppressed = frame.suppressed.saturating_sub(1));
        }
    }
}

/// Whether a recording is active (and not suppressed) on this thread.
pub fn is_recording() -> bool {
    with_top(|frame| frame.suppressed == 0).unwrap_or(false)
}

/// Record the `dj-model` bindings of one emitted run of template text.
pub fn record_text(text: &str) {
    if !text.contains("dj-model") {
        return;
    }
    with_top(|frame| {
        if frame.suppressed == 0 {
            let mut found = HashSet::new();
            crate::parser::scan_dj_model_in_text(text, &mut found);
            frame.fields.extend(found);
        }
    });
}

/// Record bindings a raw-block handler declaring `EMITS_BODY` read from the
/// template text of its own (Django) render of its body.
pub fn record_fields(fields: Vec<String>) {
    with_top(|frame| {
        if frame.suppressed == 0 {
            frame.fields.extend(fields);
        }
    });
}

/// Mark the start of one top-level node of a collecting/partial render.
pub fn begin_node() -> Option<usize> {
    with_top(|frame| frame.fields.len())
}

/// Close the node opened by [`begin_node`], keeping its record.
pub fn end_node(mark: Option<usize>) {
    if let Some(mark) = mark {
        with_top(|frame| {
            let record = frame.fields.get(mark..).unwrap_or_default().to_vec();
            frame.nodes.push(record);
        });
    }
}

/// A top-level node served from the node cache: replay its earlier record.
pub fn reuse_node(index: usize) {
    with_top(|frame| {
        let record = frame.previous_nodes.get(index).cloned().unwrap_or_default();
        frame.fields.extend(record.iter().cloned());
        frame.nodes.push(record);
    });
}

/// Could rendering `nodes` record a binding? Text carrying `dj-model`, or a
/// node whose template text is not in view here (an include).
pub fn nodes_may_emit_binding(nodes: &[Node]) -> bool {
    nodes.iter().any(|node| match node {
        Node::Text(text) => text.contains("dj-model"),
        Node::Include { .. } | Node::BlockSuperScope { .. } => true,
        _ => node
            .child_lists()
            .into_iter()
            .flatten()
            .any(|children| nodes_may_emit_binding(children)),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::Template;
    use djust_core::{Context, Value};

    #[derive(Clone)]
    struct Loader(std::collections::HashMap<&'static str, &'static str>);
    impl crate::TemplateLoader for Loader {
        fn shared_handle(&self) -> std::sync::Arc<dyn crate::TemplateLoader + Send + Sync> {
            std::sync::Arc::new(self.clone())
        }
        fn load_template(&self, name: &str) -> crate::Result<Vec<crate::parser::Node>> {
            let source = self.0.get(name).copied().unwrap_or("");
            Ok(Template::new(source)?.nodes.clone())
        }
    }

    fn bindings(
        source: &str,
        vars: &[(&str, Value)],
        files: &[(&'static str, &'static str)],
    ) -> Vec<String> {
        let mut ctx = Context::new();
        for (k, v) in vars {
            ctx.set((*k).into(), v.clone());
        }
        let loader = Loader(files.iter().copied().collect());
        let recording = Recording::begin(Vec::new());
        Template::new(source)
            .unwrap()
            .render_with_loader(&ctx, &loader)
            .unwrap();
        recording.finish().0
    }

    const GATED: &str =
        r#"<input dj-model="search">{% if staff %}<input dj-model="note">{% endif %}"#;

    #[test]
    fn only_the_taken_branch_contributes() {
        assert_eq!(
            bindings(GATED, &[("staff", Value::Bool(false))], &[]),
            ["search"]
        );
        assert_eq!(
            bindings(GATED, &[("staff", Value::Bool(true))], &[]),
            ["note", "search"]
        );
        let other = r#"{% if staff %}<input dj-model="note">{% else %}<input dj-model="search">{% endif %}"#;
        assert_eq!(
            bindings(other, &[("staff", Value::Bool(false))], &[]),
            ["search"]
        );
    }

    #[test]
    fn loops_includes_with_and_blocks_carry_their_bindings() {
        let rows = Value::List(vec![Value::Integer(1)]);
        let src = r#"{% for r in rows %}<input dj-model="title">{% endfor %}"#;
        assert_eq!(bindings(src, &[("rows", rows)], &[]), ["title"]);
        assert!(bindings(src, &[("rows", Value::List(vec![]))], &[]).is_empty());
        let inc = r#"{% if staff %}{% with x=1 %}{% include "p.html" %}{% endwith %}{% endif %}"#;
        let files = [("p.html", r#"<textarea dj-model="note"></textarea>"#)];
        assert_eq!(
            bindings(inc, &[("staff", Value::Bool(true))], &files),
            ["note"]
        );
        assert!(bindings(inc, &[("staff", Value::Bool(false))], &files).is_empty());
        let ext = r#"{% extends "base.html" %}{% block b %}{% if staff %}<input dj-model="note">{% endif %}{% endblock %}"#;
        let files = [(
            "base.html",
            r#"<input dj-model="search">{% block b %}{% endblock %}"#,
        )];
        assert_eq!(
            bindings(ext, &[("staff", Value::Bool(true))], &files),
            ["note", "search"]
        );
        assert_eq!(
            bindings(ext, &[("staff", Value::Bool(false))], &files),
            ["search"]
        );
    }

    #[test]
    fn rendered_data_never_adds_a_binding() {
        let payload = Value::String("x dj-model=note".into());
        let src = r#"<input value={{ name }}>{% if staff %}<input dj-model="note">{% endif %}"#;
        assert!(bindings(
            src,
            &[("name", payload), ("staff", Value::Bool(false))],
            &[]
        )
        .is_empty());
        let src = r#"<input dj-model="{{ field }}">"#;
        assert!(bindings(src, &[("field", Value::String("note".into()))], &[]).is_empty());
        let src = r#"<div>{{ html|safe }}</div>"#;
        let html = Value::String(r#"<input dj-model="note">"#.into());
        assert!(bindings(src, &[("html", html)], &[]).is_empty());
    }

    #[test]
    fn ifchanged_bodies_do_not_count() {
        let src = r#"{% for r in rows %}{% ifchanged %}<input dj-model="note">{% endifchanged %}{% endfor %}"#;
        let rows = Value::List(vec![Value::Integer(1)]);
        assert!(bindings(src, &[("rows", rows)], &[]).is_empty());
    }

    #[test]
    fn frames_nest_and_suppress() {
        let outer = Recording::begin(Vec::new());
        record_text(r#"<input dj-model="a">"#);
        {
            let _pause = Pause::begin();
            record_text(r#"<input dj-model="b">"#);
            assert!(!is_recording());
        }
        {
            let _suppress = Suppress::begin();
            record_text(r#"<input dj-model="c">"#);
        }
        {
            let inner = Recording::begin(Vec::new());
            record_text(r#"<input dj-model="d">"#);
            assert_eq!(inner.finish().0, ["d"]);
        }
        let mark = begin_node();
        record_text(r#"<input dj-model="e">"#);
        end_node(mark);
        assert_eq!(
            outer.finish(),
            (vec!["a".into(), "e".into()], vec![vec!["e".into()]])
        );
        assert!(!is_recording());
    }

    #[test]
    fn static_split_marks_branch_and_loop_bindings_conditional() {
        let src = r#"<input dj-model="a">{% block b %}{% with x=1 %}<input dj-model="b">{% endwith %}{% endblock %}{% if f %}<input dj-model="c">{% endif %}{% for r in rs %}<input dj-model="d">{% endfor %}"#;
        let loader = Loader(std::collections::HashMap::new());
        let (all, unconditional) = crate::extract_dj_model_field_split(src, Some(&loader)).unwrap();
        assert_eq!(all, ["a", "b", "c", "d"]);
        assert_eq!(unconditional, ["a", "b"]);
    }
}
