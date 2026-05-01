//! Regression tests for issue #1256: extend the SVG attribute camelCase
//! normalization map to cover modern SVG attributes that were previously
//! passing through as lowercase, breaking `setAttributeNS` silently.
//!
//! The base list in `crates/djust_vdom/src/parser.rs:38-98` already
//! covers most filter/gradient/animation/marker attrs. The audit
//! identified gaps in:
//!   - `pathLength` (path/circle/ellipse/line)
//!   - Conditional processing: `requiredFeatures`, `requiredExtensions`,
//!     `systemLanguage`
//!   - `externalResourcesRequired`
//!   - `crossOrigin`, `referrerPolicy`
//!   - `tabIndex`
//!   - Font-face attrs (deprecated but still in spec): `accentHeight`,
//!     `arabicForm`, `glyphName`, `horizAdvX`, `horizOriginX`,
//!     `horizOriginY`, `vertAdvY`, `vertOriginX`, `vertOriginY`,
//!     `unicodeRange`, `unitsPerEm`, `xHeight`, `vIdeographic`,
//!     `vAlphabetic`, `vHanging`, `vMathematical`, `underlinePosition`,
//!     `underlineThickness`, `panose1`, `strikethroughPosition`,
//!     `strikethroughThickness`, `overlinePosition`, `overlineThickness`
//!
//! The contract: each lowercase form maps to its canonical camelCase form
//! when found inside an SVG element. The existing
//! `test_normalize_svg_attribute` (parser.rs:981) pins the original
//! mappings; these tests pin the new ones.

use djust_vdom::parse_html;

/// Helper: parse an SVG-wrapped attribute and return the resulting
/// attribute name on the inner element.
fn attr_name_after_parse(svg_inner: &str, expected_tag: &str) -> Option<String> {
    let html = format!(
        r#"<svg xmlns="http://www.w3.org/2000/svg">{}</svg>"#,
        svg_inner
    );
    let vnode = parse_html(&html).ok()?;
    fn find_tag<'a>(node: &'a djust_vdom::VNode, tag: &str) -> Option<&'a djust_vdom::VNode> {
        if node.tag == tag {
            return Some(node);
        }
        for child in &node.children {
            if let Some(found) = find_tag(child, tag) {
                return Some(found);
            }
        }
        None
    }
    let elem = find_tag(&vnode, expected_tag)?;
    // Find the attribute that's NOT dj-id (the test attr we set).
    elem.attrs
        .keys()
        .find(|k| *k != "dj-id" && *k != "xmlns")
        .cloned()
}

#[test]
fn test_path_length_camelcased() {
    // Use only one attribute to avoid HashMap ordering ambiguity in the helper.
    let attr = attr_name_after_parse(r#"<path pathLength="100"/>"#, "path");
    assert_eq!(
        attr.as_deref(),
        Some("pathLength"),
        "REGRESSION #1256: pathLength must be normalized to camelCase \
         (was passing through as lowercase 'pathlength' which breaks \
         setAttributeNS)"
    );
}

#[test]
fn test_required_features_camelcased() {
    let attr = attr_name_after_parse(
        r#"<g requiredFeatures="http://www.w3.org/TR/SVG11/feature#Shape"></g>"#,
        "g",
    );
    assert_eq!(attr.as_deref(), Some("requiredFeatures"));
}

#[test]
fn test_required_extensions_camelcased() {
    let attr = attr_name_after_parse(
        r#"<g requiredExtensions="http://example.com/ext"></g>"#,
        "g",
    );
    assert_eq!(attr.as_deref(), Some("requiredExtensions"));
}

#[test]
fn test_system_language_camelcased() {
    let attr = attr_name_after_parse(r#"<g systemLanguage="en-US"></g>"#, "g");
    assert_eq!(attr.as_deref(), Some("systemLanguage"));
}

#[test]
fn test_external_resources_required_camelcased() {
    let attr = attr_name_after_parse(r#"<g externalResourcesRequired="false"></g>"#, "g");
    assert_eq!(attr.as_deref(), Some("externalResourcesRequired"));
}

#[test]
fn test_cross_origin_camelcased() {
    let attr = attr_name_after_parse(r#"<image crossOrigin="anonymous" href="x.png"/>"#, "image");
    // crossOrigin OR href are options; we want crossOrigin specifically.
    let html = r#"<svg xmlns="http://www.w3.org/2000/svg"><image crossOrigin="anonymous"/></svg>"#;
    let vnode = parse_html(html).unwrap();
    fn find_tag<'a>(node: &'a djust_vdom::VNode, tag: &str) -> Option<&'a djust_vdom::VNode> {
        if node.tag == tag {
            return Some(node);
        }
        for child in &node.children {
            if let Some(f) = find_tag(child, tag) {
                return Some(f);
            }
        }
        None
    }
    let elem = find_tag(&vnode, "image").unwrap();
    assert!(
        elem.attrs.contains_key("crossOrigin"),
        "REGRESSION #1256: crossOrigin must be normalized. Got attrs: {:?}",
        elem.attrs.keys().collect::<Vec<_>>()
    );
    let _ = attr; // suppress unused warning from outer call
}

#[test]
fn test_tab_index_camelcased() {
    let attr = attr_name_after_parse(r#"<g tabIndex="0"></g>"#, "g");
    assert_eq!(attr.as_deref(), Some("tabIndex"));
}

/// Existing mappings still pass — guard against accidental regressions
/// when extending the list.
#[test]
fn test_existing_mappings_still_normalized() {
    // viewBox on the svg root.
    let html = r#"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"></svg>"#;
    let vnode = parse_html(html).unwrap();
    fn find_tag<'a>(node: &'a djust_vdom::VNode, tag: &str) -> Option<&'a djust_vdom::VNode> {
        if node.tag == tag {
            return Some(node);
        }
        for child in &node.children {
            if let Some(f) = find_tag(child, tag) {
                return Some(f);
            }
        }
        None
    }
    let svg = find_tag(&vnode, "svg").unwrap();
    assert!(
        svg.attrs.contains_key("viewBox"),
        "regression guard: existing 'viewbox→viewBox' mapping must still work. Got: {:?}",
        svg.attrs.keys().collect::<Vec<_>>()
    );
}

#[test]
fn test_horiz_adv_x_camelcased() {
    let attr = attr_name_after_parse(r#"<g horizAdvX="500"/>"#, "g");
    assert_eq!(attr.as_deref(), Some("horizAdvX"));
}

#[test]
fn test_units_per_em_camelcased() {
    let attr = attr_name_after_parse(r#"<g unitsPerEm="1000"/>"#, "g");
    assert_eq!(attr.as_deref(), Some("unitsPerEm"));
}

#[test]
fn test_unknown_attr_passes_through_unchanged() {
    // An attr that genuinely doesn't need normalization should pass through.
    let html = r#"<svg xmlns="http://www.w3.org/2000/svg"><g class="foo"/></svg>"#;
    let vnode = parse_html(html).unwrap();
    fn find_tag<'a>(node: &'a djust_vdom::VNode, tag: &str) -> Option<&'a djust_vdom::VNode> {
        if node.tag == tag {
            return Some(node);
        }
        for child in &node.children {
            if let Some(f) = find_tag(child, tag) {
                return Some(f);
            }
        }
        None
    }
    let g = find_tag(&vnode, "g").unwrap();
    assert!(
        g.attrs.contains_key("class"),
        "non-SVG-camelCase attrs like 'class' should pass through unchanged. Got: {:?}",
        g.attrs.keys().collect::<Vec<_>>()
    );
}
