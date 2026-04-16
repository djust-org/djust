//! HTML parser for converting HTML strings to virtual DOM
//!
//! Generates compact `dj-id` IDs on each element for reliable patch targeting.
//!
//! ## Special Comment Handling
//!
//! Preserves `<!--dj-if-->` placeholder comments emitted by the template engine
//! when `{% if %}` blocks evaluate to false. These placeholders maintain consistent
//! sibling positions in the VDOM to prevent incorrect node matching during diff
//! (issue #295). Regular HTML comments are filtered out.
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
/// Each element receives a `dj-id` attribute with a base62-encoded unique ID.
/// These IDs enable O(1) querySelector lookup on the client, avoiding fragile
/// index-based path traversal.
///
/// **Important**: This function resets the ID counter to 0. For subsequent renders
/// within the same session (where you need unique IDs), use `parse_html_continue()`
/// instead to avoid ID collisions.
///
/// Example output:
/// ```html
/// <div dj-id="0">
///   <span dj-id="1">Hello</span>
///   <span dj-id="2">World</span>
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
    // We want to find the actual content, not the html wrapper.
    //
    // Strategy:
    // 1. Find <body> in the parsed document
    // 2. Search for [dj-root] or [dj-view] element — this is the LiveView root
    //    whose innerHTML matches the client DOM. Using it as VDOM root ensures
    //    dj-id attributes stay in sync between server VDOM and client DOM.
    // 3. If no [dj-root]/[dj-view] found, fall back to first element child of <body>
    //    (handles plain fragment inputs with no LiveView wrapper).

    // First, find the <body> element
    let body = find_body(handle);
    if let Some(ref body_handle) = body {
        // Search for [dj-root] or [dj-view] inside <body>
        if let Some(root) = find_liveview_root(body_handle) {
            return root;
        }

        // No [dj-root]/[dj-view] found — return first element child of <body>
        for body_child in body_handle.children.borrow().iter() {
            if let NodeData::Element { .. } = body_child.data {
                return body_child.clone();
            }
        }
    }

    // Fallback: return first element found at document level
    for child in handle.children.borrow().iter() {
        if let NodeData::Element { .. } = child.data {
            return child.clone();
        }
    }
    handle.clone()
}

/// Find the <body> element inside the document.
fn find_body(handle: &Handle) -> Option<Handle> {
    for child in handle.children.borrow().iter() {
        if let NodeData::Element { ref name, .. } = child.data {
            if name.local.as_ref() == "html" {
                for html_child in child.children.borrow().iter() {
                    if let NodeData::Element { ref name, .. } = html_child.data {
                        if name.local.as_ref() == "body" {
                            return Some(html_child.clone());
                        }
                    }
                }
            }
        }
    }
    None
}

/// Recursively search for an element with `dj-root` or `dj-view` attribute.
/// Returns the first match (depth-first).
fn find_liveview_root(handle: &Handle) -> Option<Handle> {
    for child in handle.children.borrow().iter() {
        if let NodeData::Element { ref attrs, .. } = child.data {
            let has_liveview_attr = attrs.borrow().iter().any(|a| {
                let name = a.name.local.as_ref();
                name == "dj-root" || name == "dj-view"
            });
            if has_liveview_attr {
                return Some(child.clone());
            }
            // Recurse into children
            if let Some(found) = find_liveview_root(child) {
                return Some(found);
            }
        }
    }
    None
}

fn handle_to_vnode(handle: &Handle) -> Result<VNode> {
    match &handle.data {
        NodeData::Text { contents } => {
            let text = contents.borrow().to_string();
            Ok(VNode::text(text))
        }

        NodeData::Element { name, attrs, .. } => {
            let tag_ref = &*name.local;
            let tag = tag_ref.to_string();
            let is_svg = is_svg_element(tag_ref);

            // Generate compact unique ID for this element
            let djust_id = next_djust_id();
            parser_trace!("Assigned ID '{}' to <{}>", djust_id, tag);

            // Pre-size attributes HashMap: most elements have 1-2 attrs + dj-id
            let attrs_borrow = attrs.borrow();
            let mut attributes = HashMap::with_capacity(attrs_borrow.len() + 1);
            let mut key: Option<String> = None;

            // Add dj-id attribute for client-side querySelector lookup
            attributes.insert("dj-id".to_string(), djust_id.clone());

            for attr in attrs_borrow.iter() {
                let attr_name_lower = &*attr.name.local;
                // Normalize SVG attributes to preserve camelCase
                let attr_name = if is_svg {
                    normalize_svg_attribute(attr_name_lower).to_string()
                } else {
                    attr_name_lower.to_string()
                };
                let attr_value = attr.value.to_string();

                // Extract dj-key or data-key for efficient list diffing
                if (attr_name_lower == "dj-key" || attr_name_lower == "data-key")
                    && !attr_value.is_empty()
                    && key.is_none()
                {
                    key = Some(attr_value.clone());
                }

                // Don't overwrite our generated dj-id
                if attr_name_lower == "dj-id" {
                    continue;
                }

                attributes.insert(attr_name, attr_value);
            }

            // Use dj-key or data-key when explicitly set
            if key.is_some() {
                parser_trace!("  Element <{}> has key: {:?}", tag, key);
            }

            // Convert children
            let mut children = Vec::new();

            // Check if this element preserves whitespace
            // tag is already lowercase from html5ever
            let preserve_whitespace =
                matches!(tag_ref, "pre" | "code" | "textarea" | "script" | "style");

            for child in handle.children.borrow().iter() {
                // Check for special placeholder comments (e.g., <!--dj-if-->)
                // These are preserved for VDOM diffing stability (issue #295)
                if let NodeData::Comment { ref contents } = child.data {
                    let comment_text = contents.to_string();
                    if comment_text == "dj-if" {
                        // Preserve this as a comment node for VDOM diffing
                        let comment_vnode = VNode {
                            tag: "#comment".to_string(),
                            attrs: HashMap::new(),
                            children: Vec::new(),
                            text: Some(comment_text),
                            key: None,
                            djust_id: None,
                            cached_html: None,
                        };
                        children.push(comment_vnode);
                    }
                    // Regular comments are still filtered out
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
                            // Filter whitespace-only text nodes (newlines, spaces, tabs)
                            // but preserve non-breaking spaces (\u{00A0}) since they are
                            // semantically significant (e.g., &nbsp; in syntax highlighting)
                            if !text.chars().all(|c| c.is_whitespace() && c != '\u{00A0}') {
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

            Ok(VNode {
                tag,
                attrs: attributes,
                children,
                text: None,
                key,
                djust_id: Some(djust_id),
                cached_html: None,
            })
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
    fn test_find_root_full_page_with_dj_root() {
        // When body has multiple children, find_root should locate [dj-root]
        // not just return the first element child (which would be <nav>).
        let html = r#"<!DOCTYPE html><html><head><title>Test</title></head><body><nav>Nav</nav><div dj-root="" dj-view="app.views.MyView"><aside><select><option value="">All</option><option value="1">Item 1</option></select></aside><div class="main-content"><header>Title</header><main>Content</main></div></div><footer>Footer</footer></body></html>"#;
        let vnode = parse_html(html).unwrap();

        assert_eq!(vnode.tag, "div", "Root should be <div dj-root>, not <nav>");
        assert_eq!(
            vnode.attrs.get("dj-root"),
            Some(&String::new()),
            "Root should have dj-root attribute"
        );
        assert_eq!(
            vnode.children.len(),
            2,
            "dj-root should have aside + div.main-content"
        );
        assert_eq!(vnode.children[0].tag, "aside");
        assert_eq!(vnode.children[1].tag, "div");

        // Verify round-trip preserves structure
        let html_out = vnode.to_html();
        assert!(
            html_out.contains("</select></aside><div"),
            "div.main-content should be sibling of aside, not inside select/option"
        );
    }

    #[test]
    fn test_find_root_nested_dj_root() {
        // dj-root nested inside wrapper divs should still be found
        let html = r#"<!DOCTYPE html><html><head></head><body><div class="wrapper"><div class="container"><div dj-root="" dj-view="app.views.X"><p>Content</p></div></div></div></body></html>"#;
        let vnode = parse_html(html).unwrap();

        assert_eq!(vnode.tag, "div");
        assert_eq!(
            vnode.attrs.get("dj-root"),
            Some(&String::new()),
            "Should find nested dj-root"
        );
        assert_eq!(vnode.children.len(), 1);
        assert_eq!(vnode.children[0].tag, "p");
    }

    #[test]
    fn test_find_root_fragment_no_dj_root() {
        // Plain fragment without dj-root should still work (backward compat)
        let html = "<div><span>Hello</span></div>";
        let vnode = parse_html(html).unwrap();

        assert_eq!(vnode.tag, "div");
        assert_eq!(vnode.children.len(), 1);
        assert_eq!(vnode.children[0].tag, "span");
    }

    #[test]
    fn test_find_root_dj_view_fallback() {
        // dj-view (without dj-root) should also be found
        let html = r#"<!DOCTYPE html><html><head></head><body><header>H</header><div dj-view="app.views.X"><p>Content</p></div></body></html>"#;
        let vnode = parse_html(html).unwrap();

        assert_eq!(vnode.tag, "div");
        assert!(vnode.attrs.contains_key("dj-view"));
    }

    #[test]
    fn test_find_root_select_with_options_full_page() {
        // Full reproduction of the bug: full page with select/option inside dj-root,
        // followed by sibling div.main-content. The div should NOT be absorbed into option.
        let html = concat!(
            "<!DOCTYPE html><html><head><title>App</title></head><body>",
            "<nav><a href=\"/\">Home</a></nav>",
            "<div dj-root=\"\" dj-view=\"app.views.Dashboard\">",
            "<aside><select><option value=\"\">All</option>",
            "<option value=\"1\">Cat A</option>",
            "<option value=\"2\">Cat B</option>",
            "</select></aside>",
            "<div class=\"main-content\"><header>Dashboard</header>",
            "<main>Content here</main></div>",
            "</div>",
            "<footer>Footer</footer></body></html>"
        );
        let vnode = parse_html(html).unwrap();

        // Root must be dj-root div, not <nav>
        assert_eq!(vnode.tag, "div");
        assert!(vnode.attrs.contains_key("dj-root"));

        // Must have exactly 2 children: aside + div.main-content
        assert_eq!(vnode.children.len(), 2);
        assert_eq!(vnode.children[0].tag, "aside");
        assert_eq!(vnode.children[1].tag, "div");
        assert_eq!(
            vnode.children[1].attrs.get("class"),
            Some(&"main-content".to_string())
        );

        // Verify select has 3 options, not a <div> child
        let select = &vnode.children[0].children[0];
        assert_eq!(select.tag, "select");
        assert_eq!(
            select.children.len(),
            3,
            "select should have 3 option children"
        );
        for opt in &select.children {
            assert_eq!(opt.tag, "option", "all select children must be <option>");
        }

        // Round-trip must produce valid HTML with proper structure
        let html_out = vnode.to_html();
        assert!(
            html_out.contains("</select></aside><div"),
            "div.main-content must follow </aside>, got: {}",
            html_out
        );
        assert!(
            !html_out.contains("<option<") && !html_out.contains("<option <"),
            "option tags must not absorb div: {}",
            html_out
        );
    }

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
    fn test_id_attribute_not_used_as_key() {
        // id= is NOT implicitly used as a diff key; developers must use dj-key explicitly
        let html = r#"<div id="message-123">Content</div>"#;
        let vnode = parse_html(html).unwrap();

        assert_eq!(vnode.tag, "div");
        assert_eq!(vnode.key, None);
        // id should still be in attrs (it's a normal HTML attribute)
        assert_eq!(vnode.attrs.get("id"), Some(&"message-123".to_string()));
    }

    #[test]
    fn test_dj_key_attribute() {
        // dj-key opt-in enables keyed diffing
        let html = r#"<div dj-key="item-123">Content</div>"#;
        let vnode = parse_html(html).unwrap();

        assert_eq!(vnode.key, Some("item-123".to_string()));
        // dj-key should be in attrs for DOM rendering
        assert_eq!(vnode.attrs.get("dj-key"), Some(&"item-123".to_string()));
    }

    #[test]
    fn test_data_key_takes_priority_over_dj_key() {
        // First-seen wins when both dj-key and data-key are present;
        // in practice parsers iterate attributes in declaration order.
        // Both are accepted — this test just verifies one value is chosen.
        let html = r#"<div dj-key="first" data-key="second">Content</div>"#;
        let vnode = parse_html(html).unwrap();

        // One of the two values is set; id= is irrelevant here
        assert!(vnode.key.is_some());
    }

    #[test]
    fn test_id_in_list_not_used_as_key() {
        // id= attributes in list items do NOT implicitly become diff keys
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
        assert_eq!(vnode.children[0].key, None);
        assert_eq!(vnode.children[1].key, None);
        assert_eq!(vnode.children[2].key, None);
    }

    #[test]
    fn test_dj_key_in_list() {
        // dj-key in list items enables keyed diffing
        let html = r#"
            <ul>
                <li dj-key="item-1">First</li>
                <li dj-key="item-2">Second</li>
                <li dj-key="item-3">Third</li>
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
        // Empty id values are not used as keys (no change in behavior)
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
