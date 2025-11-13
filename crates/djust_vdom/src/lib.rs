//! Virtual DOM implementation for efficient DOM diffing
//!
//! This crate provides a virtual DOM with fast diffing algorithms to
//! minimize DOM updates for reactive server-side rendering.

// PyResult type annotations are required by PyO3 API
#![allow(clippy::useless_conversion)]

use djust_core::Result;
use pyo3::prelude::*;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

pub mod diff;
pub mod parser;
pub mod patch;

/// A virtual DOM node
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct VNode {
    pub tag: String,
    pub attrs: HashMap<String, String>,
    pub children: Vec<VNode>,
    pub text: Option<String>,
    pub key: Option<String>,
}

impl VNode {
    pub fn element(tag: impl Into<String>) -> Self {
        Self {
            tag: tag.into(),
            attrs: HashMap::new(),
            children: Vec::new(),
            text: None,
            key: None,
        }
    }

    pub fn text(content: impl Into<String>) -> Self {
        Self {
            tag: "#text".to_string(),
            attrs: HashMap::new(),
            children: Vec::new(),
            text: Some(content.into()),
            key: None,
        }
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
}

/// A patch operation to apply to the DOM
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum Patch {
    /// Replace a node at path
    Replace { path: Vec<usize>, node: VNode },
    /// Update text content
    SetText { path: Vec<usize>, text: String },
    /// Set an attribute
    SetAttr {
        path: Vec<usize>,
        key: String,
        value: String,
    },
    /// Remove an attribute
    RemoveAttr { path: Vec<usize>, key: String },
    /// Insert a child at index
    InsertChild {
        path: Vec<usize>,
        index: usize,
        node: VNode,
    },
    /// Remove a child at index
    RemoveChild { path: Vec<usize>, index: usize },
    /// Move a child from one index to another
    MoveChild {
        path: Vec<usize>,
        from: usize,
        to: usize,
    },
}

/// Compute the difference between two virtual DOM trees
pub fn diff(old: &VNode, new: &VNode) -> Vec<Patch> {
    diff::diff_nodes(old, new, &[])
}

/// Parse HTML into a virtual DOM
pub fn parse_html(html: &str) -> Result<VNode> {
    parser::parse_html(html)
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
}
