//! The elements whose body the HTML parser keeps as RAW text (#3045).
//!
//! html5ever, run with scripting enabled as djust runs it, does not decode
//! character references inside these elements and never finds tags there:
//! `<noscript>Tom &amp; Jerry</noscript>` holds the text `Tom &amp; Jerry`.
//! So a serializer must write their text back verbatim (escaping it again
//! turned `&amp;` into `&amp;amp;`), and a fast path must not decode it.
//!
//! ONE list, shared by the VDOM serializer (`djust_vdom::VNode::write_html`)
//! and the text fast path (`djust_live::text_node_value`), so the two cannot
//! drift (#1646). Under an `svg` / `math` ancestor the same names are foreign
//! elements whose text IS decoded; callers that can see ancestors handle that
//! case themselves.

/// Raw-text elements, lowercase: `script` and `style` (raw text), `xmp`,
/// `iframe`, `noembed`, `noframes`, `plaintext`, and `noscript` (raw text
/// while scripting is enabled).
pub const RAW_TEXT_ELEMENTS: [&str; 8] = [
    "script",
    "style",
    "xmp",
    "iframe",
    "noembed",
    "noframes",
    "plaintext",
    "noscript",
];

/// Is `tag` (any ASCII case) one of [`RAW_TEXT_ELEMENTS`]?
pub fn is_raw_text_element(tag: &str) -> bool {
    RAW_TEXT_ELEMENTS
        .iter()
        .any(|t| tag.eq_ignore_ascii_case(t))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn raw_text_elements_match_any_case() {
        assert!(is_raw_text_element("noscript"));
        assert!(is_raw_text_element("XMP"));
        assert!(!is_raw_text_element("textarea")); // RCDATA: entities decode
        assert!(!is_raw_text_element("div"));
    }
}
