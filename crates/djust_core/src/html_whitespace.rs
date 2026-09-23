//! Whitespace rules shared by the VDOM parser and the loop parse cache (#2999).
//!
//! Between two *inline-level* siblings, whitespace is content: it is the space
//! between two words (`<b>A</b> <i>B</i>` reads "A B"). Between block-level
//! siblings it is indentation and renders as nothing. The VDOM parser keeps
//! the first kind (collapsed to a single `" "` text node) and drops the second.
//!
//! The list below is the ONE definition of "inline-level" for the server. The
//! Python egress normalizer (`TemplateMixin._strip_comments_and_whitespace`)
//! must agree with it exactly; `python/djust/tests/test_inline_whitespace_2999.py`
//! compares the two lists through `djust._rust.vdom_inline_level_tags()`.
//!
//! The client does NOT need this list: the server encodes its decision in the
//! node itself (a kept inter-inline space is always exactly one U+0020), so the
//! client's significant-child predicate stays context-free. See
//! `isSignificantChild` in `python/djust/static/djust/src/12-vdom-patch.js`.

/// Elements whose default rendering is inline (phrasing content plus the
/// inline replaced elements). Whitespace between two of these — or between one
/// of these and a text run — is significant. Anything not listed (block
/// elements, list items, table parts, custom elements, the loop-cache
/// `dj-pc-*` sentinel) is treated as block-level.
pub const INLINE_LEVEL_TAGS: &[&str] = &[
    "a", "abbr", "acronym", "audio", "b", "bdi", "bdo", "big", "br", "button", "canvas", "cite",
    "code", "data", "del", "dfn", "em", "embed", "font", "i", "iframe", "img", "input", "ins",
    "kbd", "label", "mark", "math", "meter", "nobr", "object", "output", "picture", "progress",
    "q", "rp", "rt", "ruby", "s", "samp", "select", "small", "span", "strike", "strong", "sub",
    "sup", "svg", "textarea", "time", "tt", "u", "var", "video", "wbr",
];

/// Is `tag` (lowercase, as html5ever produces it) an inline-level element?
pub fn is_inline_level_tag(tag: &str) -> bool {
    INLINE_LEVEL_TAGS.contains(&tag)
}

/// Is `text` made only of HTML (ASCII) whitespace — space, tab, LF, FF, CR?
///
/// This is the HTML definition of collapsible whitespace, and the same set the
/// client tests with `/[^ \t\n\r\f]/`. NBSP and the other Unicode spaces are
/// rendered content, not collapsible whitespace, so a text node made of them
/// is kept verbatim. The empty string counts as whitespace-only.
pub fn is_html_whitespace_only(text: &str) -> bool {
    text.bytes().all(|b| b.is_ascii_whitespace())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn inline_list_is_sorted_and_unique() {
        let mut sorted = INLINE_LEVEL_TAGS.to_vec();
        sorted.sort_unstable();
        sorted.dedup();
        assert_eq!(sorted, INLINE_LEVEL_TAGS.to_vec());
    }

    #[test]
    fn block_tags_are_not_inline() {
        for tag in [
            "div",
            "p",
            "li",
            "ul",
            "ol",
            "tr",
            "td",
            "table",
            "section",
            "pre",
            "h1",
            "dj-pc-1",
            "my-widget",
        ] {
            assert!(!is_inline_level_tag(tag), "{tag} must be block-level");
        }
        for tag in ["b", "i", "strong", "code", "span", "a", "img", "svg", "br"] {
            assert!(is_inline_level_tag(tag), "{tag} must be inline-level");
        }
    }

    #[test]
    fn whitespace_is_ascii_only() {
        assert!(is_html_whitespace_only(" \t\n\r\x0c"));
        assert!(is_html_whitespace_only(""));
        assert!(!is_html_whitespace_only("\u{00A0}"));
        assert!(!is_html_whitespace_only("\u{2003}"));
        assert!(!is_html_whitespace_only(" x "));
    }
}
