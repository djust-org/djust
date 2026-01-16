//! HTML parser for converting HTML strings to virtual DOM

use crate::VNode;
use djust_core::{DjangoRustError, Result};
use html5ever::parse_document;
use html5ever::tendril::TendrilSink;
use markup5ever_rcdom::{Handle, NodeData, RcDom};
use std::collections::HashMap;

pub fn parse_html(html: &str) -> Result<VNode> {
    let dom = parse_document(RcDom::default(), Default::default())
        .from_utf8()
        .read_from(&mut html.as_bytes())
        .map_err(|e| DjangoRustError::VdomError(format!("Failed to parse HTML: {e}")))?;

    // Find the body or first child
    let root = find_root(&dom.document);
    handle_to_vnode(&root)
}

fn find_root(handle: &Handle) -> Handle {
    // html5ever wraps fragments in <html><head/><body>content</body></html>
    // We want to find the actual content element, not the html wrapper

    // First, find the <html> element
    for child in handle.children.borrow().iter() {
        if let NodeData::Element { ref name, .. } = child.data {
            if name.local.as_ref() == "html" {
                // Found <html>, now look for <body>
                for html_child in child.children.borrow().iter() {
                    if let NodeData::Element { ref name, .. } = html_child.data {
                        if name.local.as_ref() == "body" {
                            // Found <body>, return its first element child
                            for body_child in html_child.children.borrow().iter() {
                                if let NodeData::Element { .. } = body_child.data {
                                    return body_child.clone();
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // Fallback: return first element found
    for child in handle.children.borrow().iter() {
        if let NodeData::Element { .. } = child.data {
            return child.clone();
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
            let mut vnode = VNode::element(tag.clone());

            // Convert attributes
            let mut attributes = HashMap::new();
            for attr in attrs.borrow().iter() {
                attributes.insert(attr.name.local.to_string(), attr.value.to_string());
            }
            vnode.attrs = attributes;

            // Convert children
            let mut children = Vec::new();
            for child in handle.children.borrow().iter() {
                // Skip comment nodes - they are not part of the DOM that JavaScript sees
                if matches!(child.data, NodeData::Comment { .. }) {
                    // Debug logging disabled - too verbose
                    // eprintln!("[Parser] Filtered comment node");
                    continue;
                }

                let child_vnode = handle_to_vnode(child)?;
                // Skip empty text nodes - use more robust whitespace detection
                if child_vnode.is_text() {
                    if let Some(text) = &child_vnode.text {
                        // Use chars().all() for more reliable whitespace detection
                        // This catches all Unicode whitespace characters
                        if !text.chars().all(|c| c.is_whitespace()) {
                            children.push(child_vnode);
                        } else {
                            // Debug logging disabled - too verbose
                            // eprintln!("[Parser] Filtered whitespace text node: {:?}", text);
                        }
                    }
                } else {
                    children.push(child_vnode);
                }
            }
            vnode.children = children;

            // Debug: log final child count for form elements
            if tag == "form" {
                eprintln!(
                    "[Parser] Form element has {} children after filtering",
                    vnode.children.len()
                );
                for (i, child) in vnode.children.iter().enumerate() {
                    if child.is_text() {
                        eprintln!(
                            "  [{}] Text: {:?}",
                            i,
                            child
                                .text
                                .as_ref()
                                .map(|t| t.chars().take(20).collect::<String>())
                        );
                    } else {
                        eprintln!("  [{}] Element: <{}>", i, child.tag);
                    }
                }
            }

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

    #[test]
    fn test_parse_html_with_comments() {
        // Test that HTML comments are filtered out during parsing
        let html = "<div><!-- comment --><span>Hello</span><!-- another --></div>";
        let vnode = parse_html(html).unwrap();

        assert_eq!(vnode.tag, "div");
        // Should have 1 child (span), not 3 (comment + span + comment)
        assert_eq!(vnode.children.len(), 1);
        assert_eq!(vnode.children[0].tag, "span");
    }

    #[test]
    fn test_parse_form_with_interspersed_comments() {
        // Test realistic form with comments between elements (like registration form)
        let html = r#"
            <form>
                <!-- Username -->
                <div class="mb-3">
                    <label>Username</label>
                    <input type="text" />
                </div>

                <!-- Email -->
                <div class="mb-3">
                    <label>Email</label>
                    <input type="email" />
                </div>

                <!-- Submit Button -->
                <div class="d-grid">
                    <button type="submit">Submit</button>
                </div>
            </form>
        "#;
        let vnode = parse_html(html).unwrap();

        assert_eq!(vnode.tag, "form");
        // Should have 3 div children (comments filtered out)
        assert_eq!(vnode.children.len(), 3);
        assert_eq!(vnode.children[0].tag, "div");
        assert_eq!(vnode.children[1].tag, "div");
        assert_eq!(vnode.children[2].tag, "div");
    }

    #[test]
    fn test_parse_nested_comments() {
        // Test that comments are filtered at all nesting levels
        let html = r#"
            <div>
                <!-- Top level comment -->
                <section>
                    <!-- Nested comment -->
                    <p>Content</p>
                    <!-- Another nested comment -->
                </section>
                <!-- Bottom comment -->
            </div>
        "#;
        let vnode = parse_html(html).unwrap();

        assert_eq!(vnode.tag, "div");
        // Should have 1 child (section), comments filtered
        assert_eq!(vnode.children.len(), 1);
        assert_eq!(vnode.children[0].tag, "section");

        // Check nested level - should have 1 child (p), nested comments filtered
        let section = &vnode.children[0];
        assert_eq!(section.children.len(), 1);
        assert_eq!(section.children[0].tag, "p");
    }

    #[test]
    fn test_parse_comments_with_text() {
        // Test that text nodes are preserved when comments are filtered
        let html = "<div><!-- comment -->Text content<span>Element</span><!-- end --></div>";
        let vnode = parse_html(html).unwrap();

        assert_eq!(vnode.tag, "div");
        // Should have 2 children: text node + span (comments filtered)
        assert_eq!(vnode.children.len(), 2);
        assert!(vnode.children[0].is_text());
        assert_eq!(vnode.children[1].tag, "span");
    }
}
