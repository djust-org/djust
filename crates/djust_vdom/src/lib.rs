//! Virtual DOM implementation for efficient DOM diffing
//!
//! This crate provides a virtual DOM with fast diffing algorithms to
//! minimize DOM updates for reactive server-side rendering.

// PyResult type annotations are required by PyO3 API
#![allow(clippy::useless_conversion)]

use djust_core::Result;
use pyo3::prelude::*;
use serde::{Deserialize, Serialize};
use std::cell::Cell;
use std::collections::HashMap;
use std::sync::OnceLock;

pub mod diff;
pub mod lis;
pub mod parser;
pub mod patch;

/// Check if VDOM tracing is enabled via environment variable.
/// Cached for performance (only checks env var once).
pub(crate) fn should_trace() -> bool {
    static SHOULD_TRACE: OnceLock<bool> = OnceLock::new();
    *SHOULD_TRACE.get_or_init(|| std::env::var("DJUST_VDOM_TRACE").is_ok())
}

/// Trace macro for VDOM debugging. Only prints when `DJUST_VDOM_TRACE=1` is set.
macro_rules! vdom_trace {
    ($($arg:tt)*) => {
        if $crate::should_trace() {
            eprintln!("[VDOM TRACE] {}", format!($($arg)*));
        }
    };
}

pub(crate) use vdom_trace;

// ============================================================================
// Compact ID Generation (Base62)
// ============================================================================

// Thread-local counter for generating unique IDs within a parse session.
//
// Thread Safety: This counter is thread-local, meaning each thread has its own
// independent counter. This ensures that concurrent parses in different threads
// don't interfere with each other's ID sequences, which is important for:
//
// 1. Test isolation - parallel tests don't affect each other
// 2. Concurrent request handling - each request gets sequential IDs
// 3. Deterministic output - same input always produces same IDs
//
// Call `reset_id_counter()` before parsing to reset the counter (this is
// done automatically by `parse_html()`).
thread_local! {
    static ID_COUNTER: Cell<u64> = const { Cell::new(0) };
}

/// Base62 character set: 0-9, a-z, A-Z
const BASE62_CHARS: &[u8] = b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ";

/// Convert a number to base62 string (compact encoding)
///
/// Examples:
/// - 0 → "0"
/// - 61 → "Z"
/// - 62 → "10"
/// - 3843 → "ZZ"
///
/// 2 chars = 3,844 unique IDs (sufficient for most pages)
/// 3 chars = 238,328 unique IDs (sufficient for any page)
pub fn to_base62(mut n: u64) -> String {
    if n == 0 {
        return "0".to_string();
    }

    let mut result = Vec::new();
    while n > 0 {
        result.push(BASE62_CHARS[(n % 62) as usize]);
        n /= 62;
    }
    result.reverse();
    String::from_utf8(result).unwrap()
}

/// Generate the next unique djust_id
pub fn next_djust_id() -> String {
    ID_COUNTER.with(|counter| {
        let id = counter.get();
        counter.set(id + 1);
        let djust_id = to_base62(id);
        vdom_trace!("next_djust_id() -> {} (counter was {})", djust_id, id);
        djust_id
    })
}

/// Reset the ID counter (call before parsing a new document)
pub fn reset_id_counter() {
    vdom_trace!("reset_id_counter() - resetting to 0");
    ID_COUNTER.with(|counter| counter.set(0));
}

/// Get the current ID counter value (for session persistence)
pub fn get_id_counter() -> u64 {
    let value = ID_COUNTER.with(|counter| counter.get());
    vdom_trace!("get_id_counter() -> {}", value);
    value
}

/// Set the ID counter to a specific value (for session restoration)
pub fn set_id_counter(value: u64) {
    vdom_trace!("set_id_counter({})", value);
    ID_COUNTER.with(|counter| counter.set(value));
}

/// A virtual DOM node
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct VNode {
    pub tag: String,
    pub attrs: HashMap<String, String>,
    pub children: Vec<VNode>,
    pub text: Option<String>,
    pub key: Option<String>,
    /// Compact unique identifier for reliable patch targeting (e.g., "0", "1a", "2b")
    /// Stored in DOM as data-d attribute for O(1) querySelector lookup
    #[serde(skip_serializing_if = "Option::is_none")]
    pub djust_id: Option<String>,
    /// Cached HTML string for to_html() — set on dj-update="ignore" subtrees
    /// to avoid re-serializing unchanged content on every render.
    #[serde(skip)]
    pub cached_html: Option<String>,
}

impl VNode {
    pub fn element(tag: impl Into<String>) -> Self {
        Self {
            tag: tag.into(),
            attrs: HashMap::new(),
            children: Vec::new(),
            text: None,
            key: None,
            djust_id: None,
            cached_html: None,
        }
    }

    pub fn text(content: impl Into<String>) -> Self {
        Self {
            tag: "#text".to_string(),
            attrs: HashMap::new(),
            children: Vec::new(),
            text: Some(content.into()),
            key: None,
            djust_id: None,
            cached_html: None,
        }
    }

    pub fn with_djust_id(mut self, id: impl Into<String>) -> Self {
        self.djust_id = Some(id.into());
        self
    }

    pub fn with_attr(mut self, key: impl Into<String>, value: impl Into<String>) -> Self {
        self.attrs.insert(key.into(), value.into());
        self
    }

    pub fn with_key(mut self, key: impl Into<String>) -> Self {
        self.key = Some(key.into());
        self
    }

    pub fn with_child(mut self, child: VNode) -> Self {
        self.children.push(child);
        self
    }

    pub fn with_children(mut self, children: Vec<VNode>) -> Self {
        self.children = children;
        self
    }

    pub fn is_text(&self) -> bool {
        self.tag == "#text"
    }

    pub fn is_comment(&self) -> bool {
        self.tag == "#comment"
    }

    /// Serialize the VNode back to HTML string.
    /// This includes dj-id attributes for reliable patch targeting.
    pub fn to_html(&self) -> String {
        self._to_html(false)
    }

    /// Internal serialization with raw-text context tracking.
    ///
    /// `in_raw_text` is true when the parent element is `<script>` or
    /// `<style>`, whose text content must NOT be HTML-escaped per the
    /// HTML spec (they are "raw text elements").
    fn _to_html(&self, in_raw_text: bool) -> String {
        // Use cached HTML if available (set on dj-update="ignore" subtrees)
        if let Some(ref cached) = self.cached_html {
            return cached.clone();
        }

        if self.is_text() {
            let text = self.text.clone().unwrap_or_default();
            // Raw text elements (<script>, <style>) must not have their
            // text content HTML-escaped — the browser treats the bytes
            // literally, so escaping `&` to `&amp;` would corrupt JS/CSS
            // code and cause double-escaping bugs (#613).
            if in_raw_text {
                return text;
            }
            return html_escape(&text);
        }

        if self.is_comment() {
            // Comment nodes: render as HTML comments
            return format!("<!--{}-->", self.text.clone().unwrap_or_default());
        }

        let mut html = String::new();

        // Void elements that don't have closing tags
        let void_elements = [
            "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
            "source", "track", "wbr",
        ];
        let is_void = void_elements.contains(&self.tag.as_str());

        // Raw text elements whose children must not be HTML-escaped
        let is_raw_text = matches!(self.tag.as_str(), "script" | "style");

        // Opening tag
        html.push('<');
        html.push_str(&self.tag);

        // Attributes (sorted for deterministic output)
        let mut attrs: Vec<_> = self.attrs.iter().collect();
        attrs.sort_by_key(|(k, _)| *k);
        for (key, value) in attrs {
            html.push(' ');
            html.push_str(key);
            html.push_str("=\"");
            html.push_str(&html_escape_attr(value));
            html.push('"');
        }

        if is_void {
            html.push_str(" />");
        } else {
            html.push('>');

            // Children — propagate raw-text context so text nodes inside
            // <script>/<style> are emitted verbatim.
            for child in &self.children {
                html.push_str(&child._to_html(is_raw_text));
            }

            // Closing tag
            html.push_str("</");
            html.push_str(&self.tag);
            html.push('>');
        }

        html
    }
}

/// Splice old VDOM subtrees into a new VDOM for `dj-update="ignore"` nodes.
///
/// After parsing a new render, this function walks both old and new VDOM trees
/// in parallel. When it finds a node with `dj-update="ignore"` in the new tree,
/// it replaces the new node's children with the old node's children (preserving
/// IDs and cached HTML). This means:
/// - The diff step sees identical subtrees → zero patches for ignored sections
/// - `to_html()` uses `cached_html` → skip serialization for ignored sections
pub fn splice_ignore_subtrees(old: &VNode, new: &mut VNode) {
    if old.tag != new.tag {
        return;
    }
    if new.attrs.get("dj-update").map(|v| v.as_str()) == Some("ignore") {
        // Replace the new subtree's children with old's (preserving IDs + cache)
        new.children = old.children.clone();
        new.djust_id = old.djust_id.clone();
        new.cached_html = old.cached_html.clone();
        return;
    }
    // Recurse into children by position
    let len = old.children.len().min(new.children.len());
    for i in 0..len {
        splice_ignore_subtrees(&old.children[i], &mut new.children[i]);
    }
}

/// Cache the HTML serialization for all `dj-update="ignore"` subtrees in a VDOM tree.
/// Call this after the first render so subsequent `to_html()` calls can skip
/// serialization for these static sections.
pub fn cache_ignore_subtree_html(node: &mut VNode) {
    if node.attrs.get("dj-update").map(|v| v.as_str()) == Some("ignore") {
        if node.cached_html.is_none() {
            node.cached_html = Some(node._to_html(false));
        }
        return; // Don't recurse — entire subtree is cached
    }
    for child in &mut node.children {
        cache_ignore_subtree_html(child);
    }
}

/// Escape HTML special characters in text content
fn html_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('\u{00A0}', "&nbsp;")
}

/// Escape HTML special characters in attribute values
fn html_escape_attr(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}

/// A patch operation to apply to the DOM
///
/// Each patch includes:
/// - `path`: Index-based path for fallback traversal
/// - `d`: Compact djust_id for O(1) querySelector lookup (e.g., "1a")
///
/// Client resolution strategy:
/// 1. Try querySelector('[data-d="1a"]') - fast, reliable
/// 2. Fall back to index-based path traversal if ID not found
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum Patch {
    /// Replace a node at path
    Replace {
        path: Vec<usize>,
        #[serde(skip_serializing_if = "Option::is_none")]
        d: Option<String>,
        node: VNode,
    },
    /// Update text content
    SetText {
        path: Vec<usize>,
        #[serde(skip_serializing_if = "Option::is_none")]
        d: Option<String>,
        text: String,
    },
    /// Set an attribute
    SetAttr {
        path: Vec<usize>,
        #[serde(skip_serializing_if = "Option::is_none")]
        d: Option<String>,
        key: String,
        value: String,
    },
    /// Remove an attribute
    RemoveAttr {
        path: Vec<usize>,
        #[serde(skip_serializing_if = "Option::is_none")]
        d: Option<String>,
        key: String,
    },
    /// Insert a child at index (d = parent's djust_id)
    InsertChild {
        path: Vec<usize>,
        #[serde(skip_serializing_if = "Option::is_none")]
        d: Option<String>,
        index: usize,
        node: VNode,
        /// The djust_id of the sibling currently at `index` (the reference node).
        /// The client can use this for ID-based insertBefore instead of index-based,
        /// preventing mis-targeting when earlier patches shift sibling positions.
        #[serde(skip_serializing_if = "Option::is_none")]
        ref_d: Option<String>,
    },
    /// Remove a child at index (d = parent's djust_id)
    RemoveChild {
        path: Vec<usize>,
        #[serde(skip_serializing_if = "Option::is_none")]
        d: Option<String>,
        index: usize,
        /// The child's djust_id for ID-based resolution on the client.
        /// Prevents stale-index mis-targeting when earlier patches shift sibling positions.
        #[serde(skip_serializing_if = "Option::is_none")]
        child_d: Option<String>,
    },
    /// Move a child from one index to another (d = parent's djust_id)
    MoveChild {
        path: Vec<usize>,
        #[serde(skip_serializing_if = "Option::is_none")]
        d: Option<String>,
        from: usize,
        to: usize,
        /// The child's djust_id for ID-based resolution on the client.
        /// Prevents stale-index mis-targeting when multiple MoveChild patches
        /// shift DOM positions before subsequent patches are applied.
        #[serde(skip_serializing_if = "Option::is_none")]
        child_d: Option<String>,
    },
}

/// Compute the difference between two virtual DOM trees
pub fn diff(old: &VNode, new: &VNode) -> Vec<Patch> {
    vdom_trace!("===== DIFF START =====");
    vdom_trace!(
        "old_root: <{}> id={:?} children={}",
        old.tag,
        old.djust_id,
        old.children.len()
    );
    vdom_trace!(
        "new_root: <{}> id={:?} children={}",
        new.tag,
        new.djust_id,
        new.children.len()
    );

    let patches = diff::diff_nodes(old, new, &[]);

    vdom_trace!(
        "===== DIFF COMPLETE: {} patches generated =====",
        patches.len()
    );
    if should_trace() {
        for (i, patch) in patches.iter().enumerate() {
            eprintln!("[VDOM TRACE] Patch[{}]: {:?}", i, patch);
        }
    }

    patches
}

/// Parse HTML into a virtual DOM (resets ID counter)
pub fn parse_html(html: &str) -> Result<VNode> {
    vdom_trace!("parse_html() called - will reset ID counter");
    parser::parse_html(html)
}

/// Synchronize IDs from old VDOM to new VDOM for matched elements.
/// Call after diffing to ensure `last_vdom` retains IDs matching the client DOM.
pub fn sync_ids(old: &VNode, new: &mut VNode) {
    diff::sync_ids(old, new);
}

/// Parse HTML into a virtual DOM without resetting ID counter.
/// Use this for subsequent renders within the same session.
pub fn parse_html_continue(html: &str) -> Result<VNode> {
    vdom_trace!("parse_html_continue() called - keeping ID counter");
    parser::parse_html_continue(html)
}

/// Python bindings
#[pyclass]
#[derive(Clone)]
pub struct PyVNode {
    inner: VNode,
}

#[pymethods]
impl PyVNode {
    #[new]
    fn new(tag: String) -> Self {
        Self {
            inner: VNode::element(tag),
        }
    }

    fn set_attr(&mut self, key: String, value: String) {
        self.inner.attrs.insert(key, value);
    }

    fn add_child(&mut self, child: PyVNode) {
        self.inner.children.push(child.inner);
    }

    fn to_json(&self) -> PyResult<String> {
        serde_json::to_string(&self.inner)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))
    }
}

#[pyfunction]
fn diff_html(old_html: String, new_html: String) -> PyResult<String> {
    let old = parse_html(&old_html)?;
    let new = parse_html(&new_html)?;
    let patches = diff(&old, &new);

    serde_json::to_string(&patches)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))
}

#[pymodule]
fn djust_vdom(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyVNode>()?;
    m.add_function(wrap_pyfunction!(diff_html, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_vnode_creation() {
        let node = VNode::element("div")
            .with_attr("class", "container")
            .with_child(VNode::text("Hello"));

        assert_eq!(node.tag, "div");
        assert_eq!(node.attrs.get("class"), Some(&"container".to_string()));
        assert_eq!(node.children.len(), 1);
    }

    #[test]
    fn test_text_node() {
        let node = VNode::text("Hello World");
        assert!(node.is_text());
        assert_eq!(node.text, Some("Hello World".to_string()));
    }

    #[test]
    fn test_to_html_escapes_normal_text() {
        let node = VNode::element("div").with_child(VNode::text("a < b & c > d"));
        let html = node.to_html();
        assert!(html.contains("a &lt; b &amp; c &gt; d"));
    }

    #[test]
    fn test_to_html_no_escape_script_content() {
        // Regression test for #613: text inside <script> must NOT be HTML-escaped.
        // JavaScript code like `if (a < b && c > d)` would break if `<` became `&lt;`.
        let script =
            VNode::element("script").with_child(VNode::text("if (a < b && c > d) { x = '&'; }"));
        let html = script.to_html();
        assert!(
            html.contains("if (a < b && c > d) { x = '&'; }"),
            "Script text must not be HTML-escaped, got: {}",
            html
        );
        assert!(!html.contains("&lt;"), "Script text must not contain &lt;");
        assert!(
            !html.contains("&amp;"),
            "Script text must not contain &amp;"
        );
    }

    #[test]
    fn test_to_html_no_escape_style_content() {
        // <style> is also a raw text element
        let style = VNode::element("style").with_child(VNode::text(".foo > .bar { color: red; }"));
        let html = style.to_html();
        assert!(
            html.contains(".foo > .bar { color: red; }"),
            "Style text must not be HTML-escaped, got: {}",
            html
        );
    }

    #[test]
    fn test_to_html_svg_attrs_not_double_escaped() {
        // SVG attribute values must survive parse -> to_html() without double-escaping
        let svg = VNode::element("svg")
            .with_attr("viewBox", "0 0 16 16")
            .with_child(VNode::element("path").with_attr("d", "M8 1L2 9H6L5 15L14 7H9L10 1H8Z"));
        let html = svg.to_html();
        assert!(
            html.contains("viewBox=\"0 0 16 16\""),
            "viewBox must not be escaped, got: {}",
            html
        );
        assert!(
            html.contains("d=\"M8 1L2 9H6L5 15L14 7H9L10 1H8Z\""),
            "path d must not be escaped, got: {}",
            html
        );
    }
}
