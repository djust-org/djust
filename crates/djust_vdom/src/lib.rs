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
        // Replace the new subtree's children with old's (preserving IDs).
        // NOTE (#1252): Do NOT copy `old.cached_html` — that cache may be
        // stale relative to the spliced children if the surrounding markup
        // changed (e.g., a wrapping conditional toggled). Clear the cache
        // so `cache_ignore_subtree_html` (called after splice on the
        // server's render path) recomputes from current children.
        new.children = old.children.clone();
        new.djust_id = old.djust_id.clone();
        new.cached_html = None;
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
    /// Remove a `dj-if` keyed subtree by marker id.
    ///
    /// Capability of issue #1358 (keyed VDOM diff for `{% if %}` conditional
    /// subtrees, re-open of #256 Option A). Iter 3 of v0.9.4-1.
    ///
    /// The client locates the `<!--dj-if id="X"-->...<!--/dj-if-->` marker
    /// pair by id and removes the bracketed range. Position-based path
    /// tracking is bypassed — the boundary's id normalizes positions, so
    /// sibling shifts no longer cascade into patch failures.
    RemoveSubtree {
        /// Boundary marker id (e.g. `"if-a3b1c2d4-0"`).
        id: String,
    },
    /// Insert a `dj-if` keyed subtree at a parent + index.
    ///
    /// Capability of issue #1358. The `html` carries the full marker pair
    /// (open `<!--dj-if id="..."-->`, body, close `<!--/dj-if-->`) — Shape A
    /// per Iter 2's locked wire contract. The client parses it via an inert
    /// `<template>.innerHTML` so any nested `<script>` tags are NOT executed.
    InsertSubtree {
        /// Boundary marker id (e.g. `"if-a3b1c2d4-1"`).
        id: String,
        /// Parent path (same shape as `InsertChild.path`).
        path: Vec<usize>,
        /// Parent's djust_id (same role as `InsertChild.d`).
        #[serde(skip_serializing_if = "Option::is_none")]
        d: Option<String>,
        /// Index within the parent's significant-children where the open
        /// marker should land (same semantics as `InsertChild.index`).
        index: usize,
        /// Full HTML fragment: `<!--dj-if id="..."-->{body}<!--/dj-if-->`.
        html: String,
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

// ============================================================================
// Text-Only VDOM Fast Path
// ============================================================================

/// A single text replacement found between old and new HTML.
#[derive(Debug)]
struct TextReplacement {
    /// Byte offset within the concatenated text stream (all text regions in order).
    text_byte_offset: usize,
    /// The old text content at this position.
    old_text: String,
    /// The new text content to replace it with.
    new_text: String,
}

/// Check if the changes between old_html and new_html are text-only.
/// Returns Some(replacements) if all differences fall within text regions
/// (between `>` and `<`). Returns None if any structural HTML changed.
fn find_text_replacements(old_html: &str, new_html: &str) -> Option<Vec<TextReplacement>> {
    // Quick reject: if the tag structure changed, it's structural.
    // Count '<' occurrences — if they differ, tags were added/removed.
    if old_html.bytes().filter(|&b| b == b'<').count()
        != new_html.bytes().filter(|&b| b == b'<').count()
    {
        return None;
    }

    // Walk both HTML strings in parallel, tracking whether we're inside a tag.
    // If any difference occurs inside a tag (between '<' and '>'), return None.
    let old_bytes = old_html.as_bytes();
    let new_bytes = new_html.as_bytes();

    let mut replacements = Vec::new();
    let mut in_tag = false;
    let mut text_byte_offset: usize = 0; // offset within the text content stream
    let mut oi: usize = 0; // old index
    let mut ni: usize = 0; // new index

    while oi < old_bytes.len() && ni < new_bytes.len() {
        let ob = old_bytes[oi];
        let nb = new_bytes[ni];

        if ob == b'<' && nb == b'<' {
            // Both entering a tag simultaneously — good
            in_tag = true;
            oi += 1;
            ni += 1;
        } else if ob == b'>' && nb == b'>' && in_tag {
            // Both exiting a tag simultaneously — good
            in_tag = false;
            oi += 1;
            ni += 1;
        } else if in_tag {
            // Inside a tag: bytes must match exactly, or it's structural
            if ob != nb {
                return None;
            }
            oi += 1;
            ni += 1;
        } else {
            // In text region
            if ob == nb {
                // Matching text byte
                text_byte_offset += 1;
                oi += 1;
                ni += 1;
            } else {
                // Difference found in text region — collect the full diff span.
                // Find where this text region ends in both strings (next '<').
                let old_region_end = old_bytes[oi..]
                    .iter()
                    .position(|&b| b == b'<')
                    .map_or(old_bytes.len(), |p| oi + p);
                let new_region_end = new_bytes[ni..]
                    .iter()
                    .position(|&b| b == b'<')
                    .map_or(new_bytes.len(), |p| ni + p);

                // Check if any '<' or '>' appear in the diff, which would be structural
                let old_span = &old_bytes[oi..old_region_end];
                let new_span = &new_bytes[ni..new_region_end];
                if old_span.contains(&b'<')
                    || old_span.contains(&b'>')
                    || new_span.contains(&b'<')
                    || new_span.contains(&b'>')
                {
                    return None;
                }

                // Find the exact diff boundaries within this text region.
                // Scan forward to find where they start matching again.
                // We need the old text and new text that differ.
                let old_text = std::str::from_utf8(old_span).ok()?;
                let new_text = std::str::from_utf8(new_span).ok()?;

                // Trim common suffix to narrow the replacement
                let common_suffix_len = old_text
                    .bytes()
                    .rev()
                    .zip(new_text.bytes().rev())
                    .take_while(|(a, b)| a == b)
                    .count();

                let old_diff = &old_text[..old_text.len() - common_suffix_len];
                let new_diff = &new_text[..new_text.len() - common_suffix_len];

                replacements.push(TextReplacement {
                    text_byte_offset,
                    old_text: old_diff.to_string(),
                    new_text: new_diff.to_string(),
                });

                // Advance past the entire text region
                text_byte_offset += old_span.len();
                oi = old_region_end;
                ni = new_region_end;
            }
        }
    }

    // If one string has trailing content, it's structural (unless both are exhausted)
    if oi < old_bytes.len() || ni < new_bytes.len() {
        // Check if remaining content is only in text regions
        let old_remaining = &old_bytes[oi..];
        let new_remaining = &new_bytes[ni..];

        // If either has remaining tag content, it's structural
        if old_remaining.contains(&b'<')
            || old_remaining.contains(&b'>')
            || new_remaining.contains(&b'<')
            || new_remaining.contains(&b'>')
        {
            return None;
        }

        // Both can't have remaining text — that means the tag structure diverged
        if !old_remaining.is_empty() && !new_remaining.is_empty() {
            // This shouldn't happen if tag counts match, but handle gracefully
            return None;
        }
        if !old_remaining.is_empty() || !new_remaining.is_empty() {
            return None;
        }
    }

    if replacements.is_empty() {
        return None; // No changes found — no need for fast path
    }

    Some(replacements)
}

/// Clear `cached_html` on a node and all its ancestors up to root.
/// Since we modify the tree by walking it, we clear cached_html on every node
/// along the path during the depth-first walk.
fn clear_cached_html_recursive(node: &mut VNode) {
    node.cached_html = None;
    for child in &mut node.children {
        clear_cached_html_recursive(child);
    }
}

/// Collect text nodes from a VDOM tree in depth-first document order.
/// Returns mutable references and their cumulative byte offsets in the text stream.
fn collect_text_nodes_mut(node: &mut VNode) -> Vec<(*mut VNode, usize)> {
    let mut result = Vec::new();
    let mut offset = 0;
    collect_text_nodes_inner(node, &mut result, &mut offset);
    result
}

fn collect_text_nodes_inner(
    node: &mut VNode,
    result: &mut Vec<(*mut VNode, usize)>,
    offset: &mut usize,
) {
    if node.is_text() {
        let text_len = node.text.as_ref().map_or(0, |t| t.len());
        result.push((node as *mut VNode, *offset));
        *offset += text_len;
    }
    for child in &mut node.children {
        collect_text_nodes_inner(child, result, offset);
    }
}

/// Try to update the old VDOM with text-only changes from old_html to new_html.
/// Returns Some(updated_vdom) if ALL differences are text-only (within text regions).
/// Returns None if any structural HTML changed (different tags, attributes, etc).
///
/// This is a performance fast path that avoids html5ever parsing when only text
/// content changed between renders (e.g., counter increment, text field update).
/// Try to update the VDOM in-place with text-only changes from old_html to new_html.
/// Returns true if ALL differences were text-only and the VDOM was updated.
/// Returns false if any structural HTML changed — caller must fall back to html5ever.
/// Mutates `vdom` in-place for zero-copy performance (no VDOM clone).
pub fn try_text_only_vdom_update_inplace(vdom: &mut VNode, old_html: &str, new_html: &str) -> bool {
    let replacements = match find_text_replacements(old_html, new_html) {
        Some(r) => r,
        None => return false,
    };

    if replacements.is_empty() {
        return false;
    }

    vdom_trace!(
        "text-only fast path: {} text replacement(s) found",
        replacements.len()
    );

    apply_text_replacements_inplace(vdom, &replacements)
}

/// Legacy API: returns a cloned+updated VDOM, or None.
/// Prefer `try_text_only_vdom_update_inplace` for large VDOMs.
pub fn try_text_only_vdom_update(
    old_vdom: &VNode,
    old_html: &str,
    new_html: &str,
) -> Option<VNode> {
    let replacements = find_text_replacements(old_html, new_html)?;

    if replacements.is_empty() {
        return None;
    }

    let mut updated = old_vdom.clone();

    // Collect all text nodes with their byte offsets in the text stream.
    // The text stream is the concatenation of html_escape(text) for all text nodes
    // in document order, because that's what appears in the HTML between tags.
    // However, find_text_replacements works on raw HTML bytes, so offsets are in
    // terms of the escaped HTML text content.
    let text_nodes = collect_text_nodes_mut(&mut updated);

    // Build a mapping of cumulative escaped byte offsets for text nodes.
    // In the HTML, text is escaped (& -> &amp;, < -> &lt;, etc.), so we need
    // to compute escaped lengths to match the offsets from find_text_replacements.
    // These offsets are immutable — they correspond to the original old HTML.
    let mut escaped_offsets: Vec<(usize, usize)> = Vec::with_capacity(text_nodes.len());
    let mut cum_offset = 0;
    for &(ptr, _) in &text_nodes {
        // SAFETY: we hold a mutable borrow on `updated` and these pointers
        // are into that tree. We only read here to compute offsets.
        let node = unsafe { &*ptr };
        let text = node.text.as_deref().unwrap_or("");
        let escaped_len = html_escape(text).len();
        escaped_offsets.push((cum_offset, escaped_len));
        cum_offset += escaped_len;
    }

    // Group replacements by text node index, then apply all at once per node.
    // Replacement offsets are relative to the original old HTML text stream,
    // so we use the immutable escaped_offsets for lookup.
    let mut node_replacements: HashMap<usize, Vec<&TextReplacement>> = HashMap::new();

    for replacement in &replacements {
        let target_offset = replacement.text_byte_offset;

        let mut found = false;
        for (i, &(esc_start, esc_len)) in escaped_offsets.iter().enumerate() {
            if target_offset >= esc_start && target_offset < esc_start + esc_len {
                node_replacements.entry(i).or_default().push(replacement);
                found = true;
                break;
            }
        }

        if !found {
            return None; // Could not map offset to a text node — bail
        }
    }

    // Apply replacements to each affected text node
    for (node_idx, mut reps) in node_replacements {
        let (esc_start, _esc_len) = escaped_offsets[node_idx];
        let (ptr, _) = text_nodes[node_idx];
        // SAFETY: exclusive access through the mutable borrow on `updated`
        let node = unsafe { &mut *ptr };
        let current_text = node.text.as_deref().unwrap_or("");
        let mut current_escaped = html_escape(current_text);

        // Sort replacements by offset (descending) so later replacements don't
        // shift earlier offsets when we do string surgery.
        reps.sort_by(|a, b| b.text_byte_offset.cmp(&a.text_byte_offset));

        for rep in reps {
            let local_offset = rep.text_byte_offset - esc_start;

            // Verify the old text matches at this position
            if local_offset + rep.old_text.len() > current_escaped.len() {
                return None; // Offset mismatch — bail to full parse
            }
            let escaped_slice = &current_escaped[local_offset..local_offset + rep.old_text.len()];
            if escaped_slice != rep.old_text {
                return None; // Content mismatch — bail to full parse
            }

            // Apply the replacement
            current_escaped = format!(
                "{}{}{}",
                &current_escaped[..local_offset],
                rep.new_text,
                &current_escaped[local_offset + rep.old_text.len()..]
            );
        }

        // Unescape back to the VDOM text representation
        node.text = Some(html_unescape(&current_escaped));
        node.cached_html = None;
    }

    // Clear cached_html on all ancestors of modified nodes.
    // For simplicity, clear it on the entire tree (it's a clone anyway).
    clear_cached_html_recursive(&mut updated);

    Some(updated)
}

/// Apply text replacements to a VDOM in-place (no clone).
/// Returns true on success, false if any replacement couldn't be mapped.
fn apply_text_replacements_inplace(vdom: &mut VNode, replacements: &[TextReplacement]) -> bool {
    let text_nodes = collect_text_nodes_mut(vdom);

    // Build cumulative escaped byte offsets for text nodes
    let mut escaped_offsets: Vec<(usize, usize)> = Vec::with_capacity(text_nodes.len());
    let mut cum_offset = 0;
    for &(ptr, _) in &text_nodes {
        let node = unsafe { &*ptr };
        let text = node.text.as_deref().unwrap_or("");
        let escaped_len = html_escape(text).len();
        escaped_offsets.push((cum_offset, escaped_len));
        cum_offset += escaped_len;
    }

    // Group replacements by text node index
    let mut node_replacements: HashMap<usize, Vec<&TextReplacement>> = HashMap::new();
    for replacement in replacements {
        let target_offset = replacement.text_byte_offset;
        let mut found = false;
        for (i, &(esc_start, esc_len)) in escaped_offsets.iter().enumerate() {
            if target_offset >= esc_start && target_offset < esc_start + esc_len {
                node_replacements.entry(i).or_default().push(replacement);
                found = true;
                break;
            }
        }
        if !found {
            return false;
        }
    }

    // Apply replacements to each affected text node
    for (node_idx, mut reps) in node_replacements {
        let (esc_start, _) = escaped_offsets[node_idx];
        let (ptr, _) = text_nodes[node_idx];
        let node = unsafe { &mut *ptr };
        let current_text = node.text.as_deref().unwrap_or("");
        let mut current_escaped = html_escape(current_text);

        reps.sort_by(|a, b| b.text_byte_offset.cmp(&a.text_byte_offset));

        for rep in reps {
            let local_offset = rep.text_byte_offset - esc_start;
            if local_offset + rep.old_text.len() > current_escaped.len() {
                return false;
            }
            let escaped_slice = &current_escaped[local_offset..local_offset + rep.old_text.len()];
            if escaped_slice != rep.old_text {
                return false;
            }
            current_escaped = format!(
                "{}{}{}",
                &current_escaped[..local_offset],
                rep.new_text,
                &current_escaped[local_offset + rep.old_text.len()..]
            );
        }

        node.text = Some(html_unescape(&current_escaped));
        node.cached_html = None;
    }

    true
}

/// Unescape basic HTML entities back to text.
/// Inverse of html_escape() for the entities we produce.
fn html_unescape(s: &str) -> String {
    s.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&nbsp;", "\u{00A0}")
        .replace("&quot;", "\"")
}

/// Detect text-only changes and produce SetText patches directly.
/// Returns Some(patches) if ALL changes are text-only; None for structural changes.
/// This skips both html5ever parsing AND VDOM diffing.
pub fn try_text_patches(old_vdom: &VNode, old_html: &str, new_html: &str) -> Option<Vec<Patch>> {
    let replacements = find_text_replacements(old_html, new_html)?;
    if replacements.is_empty() {
        return Some(vec![]);
    }

    // Walk the VDOM depth-first, collecting text nodes with their paths and
    // cumulative escaped byte offsets in the HTML text stream.
    let mut text_entries: Vec<(Vec<usize>, usize, usize, String)> = Vec::new(); // (path, esc_start, esc_len, djust_id)
    let mut cum_offset: usize = 0;
    collect_text_paths(old_vdom, &mut vec![], &mut text_entries, &mut cum_offset);

    // Map each replacement to a text node and produce a SetText patch
    let mut patches = Vec::new();
    for rep in &replacements {
        let target_offset = rep.text_byte_offset;
        let mut found = false;
        for (path, esc_start, esc_len, djust_id) in &text_entries {
            if target_offset >= *esc_start && target_offset < esc_start + esc_len {
                // Compute the new text for this node
                let old_text_node = get_text_node_at_path(old_vdom, path)?;
                let old_text = old_text_node.text.as_deref().unwrap_or("");
                let mut escaped = html_escape(old_text);
                let local_offset = target_offset - esc_start;
                if local_offset + rep.old_text.len() > escaped.len() {
                    return None;
                }
                if escaped[local_offset..local_offset + rep.old_text.len()] != *rep.old_text {
                    return None;
                }
                escaped = format!(
                    "{}{}{}",
                    &escaped[..local_offset],
                    rep.new_text,
                    &escaped[local_offset + rep.old_text.len()..]
                );
                let new_text = html_unescape(&escaped);

                let d_val = if djust_id.is_empty() {
                    None
                } else {
                    Some(djust_id.clone())
                };
                patches.push(Patch::SetText {
                    path: path.clone(),
                    d: d_val,
                    text: new_text,
                });
                found = true;
                break;
            }
        }
        if !found {
            return None;
        }
    }

    Some(patches)
}

fn get_text_node_at_path<'a>(vdom: &'a VNode, path: &[usize]) -> Option<&'a VNode> {
    let mut node = vdom;
    for &idx in path {
        node = node.children.get(idx)?;
    }
    Some(node)
}

/// Walk VDOM depth-first collecting text node paths and cumulative escaped byte offsets.
fn collect_text_paths(
    node: &VNode,
    current_path: &mut Vec<usize>,
    entries: &mut Vec<(Vec<usize>, usize, usize, String)>,
    cum_offset: &mut usize,
) {
    if let Some(ref text) = node.text {
        let escaped_len = html_escape(text).len();
        let djust_id = node.djust_id.clone().unwrap_or_default();
        entries.push((current_path.clone(), *cum_offset, escaped_len, djust_id));
        *cum_offset += escaped_len;
    }
    for (i, child) in node.children.iter().enumerate() {
        current_path.push(i);
        collect_text_paths(child, current_path, entries, cum_offset);
        current_path.pop();
    }
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

/// Parse an HTML fragment with a specific parent context.
///
/// Use this when you know where in the DOM the fragment will live — it
/// skips the full-document wrapper and allows html5ever to tokenize
/// context-sensitive elements (`<tr>`, `<td>`, `<option>`) correctly.
///
/// Does NOT reset the ID counter.
pub fn parse_html_fragment(html: &str, context_tag: &str) -> Result<Vec<VNode>> {
    vdom_trace!(
        "parse_html_fragment() called - context=<{}>, {} bytes",
        context_tag,
        html.len()
    );
    parser::parse_html_fragment(html, context_tag)
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

    // ========================================================================
    // Text-only VDOM fast path tests
    // ========================================================================

    #[test]
    fn test_text_only_update_simple() {
        // Single text change: "5" -> "6"
        let old_html = "<div><span>Count: 5</span></div>";
        let new_html = "<div><span>Count: 6</span></div>";

        let old_vdom = parse_html(old_html).unwrap();
        let result = try_text_only_vdom_update(&old_vdom, old_html, new_html);

        assert!(result.is_some(), "Should detect text-only change");
        let updated = result.unwrap();
        let html = updated.to_html();
        assert!(
            html.contains("Count: 6"),
            "Updated VDOM should contain new text, got: {}",
            html
        );
    }

    #[test]
    fn test_text_only_update_rejects_structural() {
        // Tag added — should reject
        let old_html = "<div><span>Hello</span></div>";
        let new_html = "<div><span>Hello</span><p>New</p></div>";

        let old_vdom = parse_html(old_html).unwrap();
        let result = try_text_only_vdom_update(&old_vdom, old_html, new_html);
        assert!(
            result.is_none(),
            "Should reject structural change (tag added)"
        );
    }

    #[test]
    fn test_text_only_update_rejects_attribute_change() {
        // Attribute changed — should reject
        let old_html = r#"<div class="old"><span>Hello</span></div>"#;
        let new_html = r#"<div class="new"><span>Hello</span></div>"#;

        let old_vdom = parse_html(old_html).unwrap();
        let result = try_text_only_vdom_update(&old_vdom, old_html, new_html);
        assert!(result.is_none(), "Should reject attribute change");
    }

    #[test]
    fn test_text_only_update_multiple_changes() {
        // Two text nodes changed simultaneously
        let old_html = "<div><span>Alice</span><span>100</span></div>";
        let new_html = "<div><span>Bob</span><span>200</span></div>";

        let old_vdom = parse_html(old_html).unwrap();
        let result = try_text_only_vdom_update(&old_vdom, old_html, new_html);

        assert!(result.is_some(), "Should handle multiple text changes");
        let updated = result.unwrap();
        let html = updated.to_html();
        assert!(
            html.contains("Bob") && html.contains("200"),
            "Updated VDOM should contain both new texts, got: {}",
            html
        );
    }

    #[test]
    fn test_text_only_update_length_change() {
        // Text "5" -> "10" (different length) — still text-only
        let old_html = "<div><span>5</span></div>";
        let new_html = "<div><span>10</span></div>";

        let old_vdom = parse_html(old_html).unwrap();
        let result = try_text_only_vdom_update(&old_vdom, old_html, new_html);

        assert!(result.is_some(), "Should handle different-length text");
        let updated = result.unwrap();
        let html = updated.to_html();
        assert!(
            html.contains(">10<"),
            "Updated VDOM should contain '10', got: {}",
            html
        );
    }

    #[test]
    fn test_text_only_update_no_changes() {
        // Identical HTML — returns None (no changes to apply)
        let html = "<div><span>Hello</span></div>";
        let old_vdom = parse_html(html).unwrap();
        let result = try_text_only_vdom_update(&old_vdom, html, html);
        assert!(result.is_none(), "Should return None when no changes");
    }

    #[test]
    fn test_text_only_update_with_entities() {
        // Text containing HTML entities
        let old_html = "<div>1 &amp; 2</div>";
        let new_html = "<div>3 &amp; 4</div>";

        let old_vdom = parse_html(old_html).unwrap();
        let result = try_text_only_vdom_update(&old_vdom, old_html, new_html);

        assert!(result.is_some(), "Should handle HTML entities");
        let updated = result.unwrap();
        let html = updated.to_html();
        assert!(
            html.contains("3 &amp; 4"),
            "Updated VDOM should contain new entity text, got: {}",
            html
        );
    }

    #[test]
    fn test_text_only_update_preserves_djust_ids() {
        // Ensure djust_id values are preserved after text update
        let old_html = "<div><span>Old</span></div>";
        let new_html = "<div><span>New</span></div>";

        let old_vdom = parse_html(old_html).unwrap();
        // Capture IDs from the parsed VDOM
        let div_id = old_vdom.djust_id.clone();
        let span_id = old_vdom.children.first().and_then(|c| c.djust_id.clone());

        let result = try_text_only_vdom_update(&old_vdom, old_html, new_html);
        assert!(result.is_some());
        let updated = result.unwrap();

        assert_eq!(updated.djust_id, div_id, "Root djust_id must be preserved");
        assert_eq!(
            updated.children.first().and_then(|c| c.djust_id.clone()),
            span_id,
            "Child djust_id must be preserved"
        );
    }
}
