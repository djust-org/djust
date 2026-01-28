//! HTML parser for converting HTML strings to virtual DOM
//!
//! Generates compact `data-dj-id` IDs on each element for reliable patch targeting.
//!
//! ## Debugging
//!
//! Set `DJUST_VDOM_TRACE=1` environment variable to enable detailed tracing
//! of the parsing process. This logs:
//! - ID assignment to each element
//! - Element structure being parsed
//! - Child filtering decisions

use crate::{next_djust_id, reset_id_counter, should_trace, VNode};
use djust_core::{DjangoRustError, Result};
use html5ever::parse_document;
use html5ever::tendril::TendrilSink;
use markup5ever_rcdom::{Handle, NodeData, RcDom};
use std::collections::HashMap;

/// Trace macro for parser logging
macro_rules! parser_trace {
    ($($arg:tt)*) => {
        if should_trace() {
            eprintln!("[PARSER TRACE] {}", format!($($arg)*));
        }
    };
}

/// SVG attributes that require camelCase preservation.
/// html5ever lowercases all attributes per HTML5 spec, but SVG is case-sensitive.
fn normalize_svg_attribute(attr_name: &str) -> &str {
    match attr_name {
        "viewbox" => "viewBox",
        "preserveaspectratio" => "preserveAspectRatio",
        "patternunits" => "patternUnits",
        "patterntransform" => "patternTransform",
        "patterncontentunits" => "patternContentUnits",
        "gradientunits" => "gradientUnits",
        "gradienttransform" => "gradientTransform",
        "spreadmethod" => "spreadMethod",
        "clippathunits" => "clipPathUnits",
        "maskcontentunits" => "maskContentUnits",
        "maskunits" => "maskUnits",
        "filterunits" => "filterUnits",
        "primitiveunits" => "primitiveUnits",
        "markerheight" => "markerHeight",
        "markerwidth" => "markerWidth",
        "markerunits" => "markerUnits",
        "refx" => "refX",
        "refy" => "refY",
        "repeatcount" => "repeatCount",
        "repeatdur" => "repeatDur",
        "calcmode" => "calcMode",
        "keypoints" => "keyPoints",
        "keysplines" => "keySplines",
        "keytimes" => "keyTimes",
        "attributename" => "attributeName",
        "attributetype" => "attributeType",
        "basefrequency" => "baseFrequency",
        "numoctaves" => "numOctaves",
        "stitchtiles" => "stitchTiles",
        "targetx" => "targetX",
        "targety" => "targetY",
        "kernelmatrix" => "kernelMatrix",
        "kernelunitlength" => "kernelUnitLength",
        "preservealpha" => "preserveAlpha",
        "surfacescale" => "surfaceScale",
        "specularconstant" => "specularConstant",
        "specularexponent" => "specularExponent",
        "diffuseconstant" => "diffuseConstant",
        "pointsatx" => "pointsAtX",
        "pointsaty" => "pointsAtY",
        "pointsatz" => "pointsAtZ",
        "limitingconeangle" => "limitingConeAngle",
        "tablevalues" => "tableValues",
        "filterres" => "filterRes",
        "stddeviation" => "stdDeviation",
        "edgemode" => "edgeMode",
        "xchannelselector" => "xChannelSelector",
        "ychannelselector" => "yChannelSelector",
        "glyphref" => "glyphRef",
        "textlength" => "textLength",
        "lengthadjust" => "lengthAdjust",
        "startoffset" => "startOffset",
        "baseprofile" => "baseProfile",
        "contentscripttype" => "contentScriptType",
        "contentstyletype" => "contentStyleType",
        "zoomandpan" => "zoomAndPan",
        _ => attr_name, // Return original if no mapping
    }
}

/// Check if a tag name is an SVG element.
/// Note: Tag names from html5ever are already lowercase, so no conversion needed.
fn is_svg_element(tag_name: &str) -> bool {
    matches!(
        tag_name,
        "svg"
            | "path"
            | "circle"
            | "rect"
            | "line"
            | "polyline"
            | "polygon"
            | "ellipse"
            | "g"
            | "defs"
            | "use"
            | "symbol"
            | "clippath"
            | "mask"
            | "pattern"
            | "image"
            | "switch"
            | "foreignobject"
            | "desc"
            | "title"
            | "metadata"
            | "lineargradient"
            | "radialgradient"
            | "stop"
            | "filter"
            | "fegaussianblur"
            | "feoffset"
            | "feblend"
            | "fecolormatrix"
            | "fecomponenttransfer"
            | "fecomposite"
            | "feconvolvematrix"
            | "fediffuselighting"
            | "fedisplacementmap"
            | "feflood"
            | "feimage"
            | "femerge"
            | "femergenode"
            | "femorphology"
            | "fespecularlighting"
            | "fetile"
            | "feturbulence"
            | "fefunca"
            | "fefuncb"
            | "fefuncg"
            | "fefuncr"
            | "text"
            | "tspan"
            | "textpath"
            | "marker"
            | "animate"
            | "animatemotion"
            | "animatetransform"
            | "set"
            | "mpath"
    )
}

/// Parse HTML into a virtual DOM with compact IDs for patch targeting.
///
/// Each element receives a `data-dj-id` attribute with a base62-encoded unique ID.
/// These IDs enable O(1) querySelector lookup on the client, avoiding fragile
/// index-based path traversal.
///
/// **Important**: This function resets the ID counter to 0. For subsequent renders
/// within the same session (where you need unique IDs), use `parse_html_continue()`
/// instead to avoid ID collisions.
///
/// Example output:
/// ```html
/// <div data-dj-id="0">
///   <span data-dj-id="1">Hello</span>
///   <span data-dj-id="2">World</span>
/// </div>
/// ```
pub fn parse_html(html: &str) -> Result<VNode> {
    parser_trace!(
        "parse_html() - resetting ID counter and parsing {} bytes",
        html.len()
    );
    // Reset ID counter for this parse session
    reset_id_counter();
    let result = parse_html_continue(html);
    if let Ok(ref vnode) = result {
        parser_trace!(
            "parse_html() complete - root=<{}> id={:?} children={}",
            vnode.tag,
            vnode.djust_id,
            vnode.children.len()
        );
    }
    result
}

/// Parse HTML without resetting the ID counter.
///
/// Use this for subsequent renders within the same session to ensure
/// newly inserted elements get unique IDs that don't collide with existing elements.
///
/// The ID counter continues from where the previous parse left off.
pub fn parse_html_continue(html: &str) -> Result<VNode> {
    parser_trace!(
        "parse_html_continue() - parsing {} bytes (counter NOT reset)",
        html.len()
    );
    // Don't reset - continue from current counter value

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

            // Generate compact unique ID for this element
            let djust_id = next_djust_id();
            parser_trace!("Assigned ID '{}' to <{}>", djust_id, tag);
            vnode.djust_id = Some(djust_id.clone());

            // Convert attributes and extract data-key for keyed diffing
            let mut attributes = HashMap::new();
            let mut key: Option<String> = None;
            let mut id_attr: Option<String> = None;

            // Add data-dj-id attribute for client-side querySelector lookup
            attributes.insert("data-dj-id".to_string(), djust_id);

            for attr in attrs.borrow().iter() {
                let attr_name_lower = attr.name.local.to_string();
                // Normalize SVG attributes to preserve camelCase (html5ever lowercases everything)
                let attr_name = if is_svg_element(&tag) {
                    normalize_svg_attribute(&attr_name_lower).to_string()
                } else {
                    attr_name_lower.clone()
                };
                let attr_value = attr.value.to_string();

                // Extract data-key for efficient list diffing (highest priority)
                if attr_name_lower == "data-key" && !attr_value.is_empty() {
                    key = Some(attr_value.clone());
                }

                // Extract id attribute as fallback key (if no data-key)
                if attr_name_lower == "id" && !attr_value.is_empty() {
                    id_attr = Some(attr_value.clone());
                }

                // Don't overwrite our generated data-dj-id if template already has one
                if attr_name_lower == "data-dj-id" {
                    continue;
                }

                attributes.insert(attr_name, attr_value);
            }
            vnode.attrs = attributes;

            // Use data-key if present, otherwise fall back to id attribute
            // This allows existing code patterns with id="..." to benefit from keyed diffing
            vnode.key = key.or(id_attr);
            if vnode.key.is_some() {
                parser_trace!("  Element <{}> has key: {:?}", tag, vnode.key);
            }

            // Convert children
            let mut children = Vec::new();

            // Check if this element preserves whitespace
            let preserve_whitespace = matches!(
                tag.to_lowercase().as_str(),
                "pre" | "code" | "textarea" | "script" | "style"
            );

            for child in handle.children.borrow().iter() {
                // Skip comment nodes - they are not part of the DOM that JavaScript sees
                if matches!(child.data, NodeData::Comment { .. }) {
                    // Debug logging disabled - too verbose
                    // eprintln!("[Parser] Filtered comment node");
                    continue;
                }

                let child_vnode = handle_to_vnode(child)?;
                // Skip empty text nodes - use more robust whitespace detection
                // IMPORTANT: Preserve whitespace inside pre, code, textarea, script, style
                if child_vnode.is_text() {
                    if let Some(text) = &child_vnode.text {
                        // Preserve ALL text nodes inside whitespace-preserving elements
                        if preserve_whitespace {
                            children.push(child_vnode);
                        } else {
                            // Use chars().all() for more reliable whitespace detection
                            // This catches all Unicode whitespace characters
                            if !text.chars().all(|c| c.is_whitespace()) {
                                children.push(child_vnode);
                            }
                            // Debug logging disabled - too verbose
                            // else { eprintln!("[Parser] Filtered whitespace text node: {:?}", text); }
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

    #[test]
    fn test_parse_data_key_attribute() {
        // Test that data-key attribute is extracted and set as VNode.key
        let html = r#"<div data-key="item-123">Content</div>"#;
        let vnode = parse_html(html).unwrap();

        assert_eq!(vnode.tag, "div");
        assert_eq!(vnode.key, Some("item-123".to_string()));
        // data-key should still be in attrs for DOM rendering
        assert_eq!(vnode.attrs.get("data-key"), Some(&"item-123".to_string()));
    }

    #[test]
    fn test_parse_list_with_data_keys() {
        // Test parsing a list where each item has a data-key for efficient diffing
        let html = r#"
            <ul>
                <li data-key="1">Item 1</li>
                <li data-key="2">Item 2</li>
                <li data-key="3">Item 3</li>
            </ul>
        "#;
        let vnode = parse_html(html).unwrap();

        assert_eq!(vnode.tag, "ul");
        assert_eq!(vnode.children.len(), 3);

        // Each child should have its key extracted
        assert_eq!(vnode.children[0].key, Some("1".to_string()));
        assert_eq!(vnode.children[1].key, Some("2".to_string()));
        assert_eq!(vnode.children[2].key, Some("3".to_string()));
    }

    #[test]
    fn test_parse_empty_data_key_ignored() {
        // Test that empty data-key values are not set as keys
        let html = r#"<div data-key="">Content</div>"#;
        let vnode = parse_html(html).unwrap();

        assert_eq!(vnode.tag, "div");
        assert_eq!(vnode.key, None);
    }

    #[test]
    fn test_parse_nested_data_keys() {
        // Test that data-key works at any nesting level
        let html = r#"
            <div data-key="parent">
                <span data-key="child">Nested content</span>
            </div>
        "#;
        let vnode = parse_html(html).unwrap();

        assert_eq!(vnode.key, Some("parent".to_string()));
        assert_eq!(vnode.children.len(), 1);
        assert_eq!(vnode.children[0].key, Some("child".to_string()));
    }

    #[test]
    fn test_id_attribute_used_as_key() {
        // Test that id attribute is automatically used as key for keyed diffing
        let html = r#"<div id="message-123">Content</div>"#;
        let vnode = parse_html(html).unwrap();

        assert_eq!(vnode.tag, "div");
        assert_eq!(vnode.key, Some("message-123".to_string()));
        // id should still be in attrs
        assert_eq!(vnode.attrs.get("id"), Some(&"message-123".to_string()));
    }

    #[test]
    fn test_data_key_takes_priority_over_id() {
        // Test that data-key takes priority over id when both are present
        let html = r#"<div id="dom-id" data-key="diff-key">Content</div>"#;
        let vnode = parse_html(html).unwrap();

        // data-key should be used, not id
        assert_eq!(vnode.key, Some("diff-key".to_string()));
    }

    #[test]
    fn test_id_key_in_list() {
        // Test that id attributes work for keyed diffing in lists
        let html = r#"
            <ul>
                <li id="item-1">First</li>
                <li id="item-2">Second</li>
                <li id="item-3">Third</li>
            </ul>
        "#;
        let vnode = parse_html(html).unwrap();

        assert_eq!(vnode.tag, "ul");
        assert_eq!(vnode.children.len(), 3);
        assert_eq!(vnode.children[0].key, Some("item-1".to_string()));
        assert_eq!(vnode.children[1].key, Some("item-2".to_string()));
        assert_eq!(vnode.children[2].key, Some("item-3".to_string()));
    }

    #[test]
    fn test_empty_id_not_used_as_key() {
        // Test that empty id values are not used as keys
        let html = r#"<div id="">Content</div>"#;
        let vnode = parse_html(html).unwrap();

        assert_eq!(vnode.key, None);
    }

    #[test]
    fn test_svg_viewbox_preserved() {
        // Test that SVG viewBox attribute preserves camelCase (Issue #81)
        let html = r#"<svg viewBox="0 0 24 24"><path d="M0 0"/></svg>"#;
        let vnode = parse_html(html).unwrap();

        assert_eq!(vnode.tag, "svg");
        assert_eq!(
            vnode.attrs.get("viewBox"),
            Some(&"0 0 24 24".to_string()),
            "viewBox should be camelCase, not lowercase"
        );
    }

    #[test]
    fn test_svg_preserve_aspect_ratio() {
        // Test that preserveAspectRatio attribute is preserved
        let html = r#"<svg preserveAspectRatio="xMidYMid meet"></svg>"#;
        let vnode = parse_html(html).unwrap();

        assert_eq!(
            vnode.attrs.get("preserveAspectRatio"),
            Some(&"xMidYMid meet".to_string())
        );
    }

    #[test]
    fn test_svg_nested_elements_preserve_case() {
        // Test that nested SVG elements also get attribute case normalization
        let html = r#"<svg viewBox="0 0 100 100"><linearGradient gradientUnits="userSpaceOnUse"></linearGradient></svg>"#;
        let vnode = parse_html(html).unwrap();

        assert_eq!(vnode.attrs.get("viewBox"), Some(&"0 0 100 100".to_string()));
        assert_eq!(vnode.children.len(), 1);
        assert_eq!(
            vnode.children[0].attrs.get("gradientUnits"),
            Some(&"userSpaceOnUse".to_string())
        );
    }

    #[test]
    fn test_non_svg_attributes_unchanged() {
        // Test that non-SVG elements don't get SVG attribute normalization
        let html = r#"<div data-viewbox="test"></div>"#;
        let vnode = parse_html(html).unwrap();

        // Should remain lowercase for non-SVG elements
        assert_eq!(
            vnode.attrs.get("data-viewbox"),
            Some(&"test".to_string()),
            "Non-SVG elements should keep lowercase attributes"
        );
        assert_eq!(vnode.attrs.get("data-viewBox"), None);
    }

    #[test]
    fn test_svg_multiple_camelcase_attributes() {
        // Test multiple SVG attributes that need case normalization
        let html =
            r#"<svg viewBox="0 0 24 24" preserveAspectRatio="xMinYMin" baseProfile="full"></svg>"#;
        let vnode = parse_html(html).unwrap();

        assert_eq!(vnode.attrs.get("viewBox"), Some(&"0 0 24 24".to_string()));
        assert_eq!(
            vnode.attrs.get("preserveAspectRatio"),
            Some(&"xMinYMin".to_string())
        );
        assert_eq!(vnode.attrs.get("baseProfile"), Some(&"full".to_string()));
    }

    #[test]
    fn test_is_svg_element() {
        // Test the SVG element detection function
        // Note: html5ever provides tag names in lowercase
        assert!(is_svg_element("svg"));
        assert!(is_svg_element("path"));
        assert!(is_svg_element("circle"));
        assert!(is_svg_element("lineargradient"));
        assert!(is_svg_element("fegaussianblur"));
        assert!(!is_svg_element("div"));
        assert!(!is_svg_element("span"));
        assert!(!is_svg_element("input"));
    }

    #[test]
    fn test_svg_nested_in_html() {
        // Test that SVG elements nested inside HTML elements get attribute normalization
        let html =
            r#"<div class="icon-wrapper"><svg viewBox="0 0 24 24"><path d="M0 0"/></svg></div>"#;
        let vnode = parse_html(html).unwrap();

        assert_eq!(vnode.tag, "div");
        assert_eq!(vnode.attrs.get("class"), Some(&"icon-wrapper".to_string()));

        // The nested SVG should have viewBox preserved
        assert_eq!(vnode.children.len(), 1);
        let svg = &vnode.children[0];
        assert_eq!(svg.tag, "svg");
        assert_eq!(
            svg.attrs.get("viewBox"),
            Some(&"0 0 24 24".to_string()),
            "SVG nested in HTML should still have viewBox camelCased"
        );
    }

    #[test]
    fn test_normalize_svg_attribute() {
        // Test the SVG attribute normalization function
        assert_eq!(normalize_svg_attribute("viewbox"), "viewBox");
        assert_eq!(
            normalize_svg_attribute("preserveaspectratio"),
            "preserveAspectRatio"
        );
        assert_eq!(normalize_svg_attribute("gradientunits"), "gradientUnits");
        assert_eq!(normalize_svg_attribute("stddeviation"), "stdDeviation");
        // Non-mapped attributes should pass through unchanged
        assert_eq!(normalize_svg_attribute("class"), "class");
        assert_eq!(normalize_svg_attribute("id"), "id");
        assert_eq!(normalize_svg_attribute("fill"), "fill");
    }
}
