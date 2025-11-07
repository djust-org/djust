//! HTML parser for converting HTML strings to virtual DOM

use crate::VNode;
use django_rust_core::{DjangoRustError, Result};
use ahash::AHashMap;
use html5ever::parse_document;
use html5ever::tendril::TendrilSink;
use markup5ever_rcdom::{Handle, NodeData, RcDom};

pub fn parse_html(html: &str) -> Result<VNode> {
    let dom = parse_document(RcDom::default(), Default::default())
        .from_utf8()
        .read_from(&mut html.as_bytes())
        .map_err(|e| DjangoRustError::VdomError(format!("Failed to parse HTML: {}", e)))?;

    // Find the body or first child
    let root = find_root(&dom.document);
    handle_to_vnode(&root)
}

fn find_root(handle: &Handle) -> Handle {
    for child in handle.children.borrow().iter() {
        match child.data {
            NodeData::Element { .. } => return child.clone(),
            _ => {}
        }
    }
    handle.clone()
}

fn handle_to_vnode(handle: &Handle) -> Result<VNode> {
    match &handle.data {
        NodeData::Text { contents } => {
            let text = contents.borrow().to_string();
            Ok(VNode::text(text))
        }

        NodeData::Element { name, attrs, .. } => {
            let tag = name.local.to_string();
            let mut vnode = VNode::element(tag);

            // Convert attributes
            let mut attributes = AHashMap::new();
            for attr in attrs.borrow().iter() {
                attributes.insert(
                    attr.name.local.to_string(),
                    attr.value.to_string(),
                );
            }
            vnode.attrs = attributes;

            // Convert children
            let mut children = Vec::new();
            for child in handle.children.borrow().iter() {
                let child_vnode = handle_to_vnode(child)?;
                // Skip empty text nodes
                if child_vnode.is_text() {
                    if let Some(text) = &child_vnode.text {
                        if !text.trim().is_empty() {
                            children.push(child_vnode);
                        }
                    }
                } else {
                    children.push(child_vnode);
                }
            }
            vnode.children = children;

            Ok(vnode)
        }

        NodeData::Document => {
            // For document nodes, process children and return first element
            for child in handle.children.borrow().iter() {
                if let Ok(vnode) = handle_to_vnode(child) {
                    if !vnode.is_text() {
                        return Ok(vnode);
                    }
                }
            }
            Ok(VNode::element("div"))
        }

        _ => Ok(VNode::element("div")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_simple_html() {
        let html = "<div>Hello</div>";
        let vnode = parse_html(html).unwrap();

        assert_eq!(vnode.tag, "div");
        assert_eq!(vnode.children.len(), 1);
        assert!(vnode.children[0].is_text());
    }

    #[test]
    fn test_parse_with_attributes() {
        let html = r#"<div class="container" id="main">Content</div>"#;
        let vnode = parse_html(html).unwrap();

        assert_eq!(vnode.tag, "div");
        assert_eq!(vnode.attrs.get("class"), Some(&"container".to_string()));
        assert_eq!(vnode.attrs.get("id"), Some(&"main".to_string()));
    }

    #[test]
    fn test_parse_nested() {
        let html = "<div><span>Hello</span><span>World</span></div>";
        let vnode = parse_html(html).unwrap();

        assert_eq!(vnode.tag, "div");
        assert_eq!(vnode.children.len(), 2);
        assert_eq!(vnode.children[0].tag, "span");
        assert_eq!(vnode.children[1].tag, "span");
    }
}
