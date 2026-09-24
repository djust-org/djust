//! Whitespace rules shared by the VDOM parser and the loop parse cache (#2999).
//!
//! Between two *inline-level* siblings, whitespace is content: it is the space
//! between two words (`<b>A</b> <i>B</i>` reads "A B"). Between block-level
//! siblings it is indentation and renders as nothing. The VDOM parser keeps
//! the first kind (collapsed to a single `" "` text node) and drops the second.
//!
//! The list below is the ONE definition of "inline-level". The Python egress
//! normalizer (`TemplateMixin._strip_comments_and_whitespace`) calls
//! `collapse_inter_tag_whitespace` (below) through `djust._rust`, so the
//! parser and the normalizer share the predicate instead of keeping copies.
//!
//! The client does NOT need this list: the server encodes its decision in the
//! node itself (a kept inter-inline space is always exactly one U+0020), so the
//! client's significant-child predicate stays context-free. See
//! `isSignificantChild` in `python/djust/static/djust/src/12-vdom-patch.js`.

/// Elements whose default rendering is inline (phrasing content plus the
/// inline replaced elements). Whitespace between two of these — or between one
/// of these and a text run — is significant. Custom elements are inline too
/// (see `is_inline_level_tag`). Anything else (block elements, list items,
/// table parts, the loop-cache `dj-pc-*` sentinel) is treated as block-level.
pub const INLINE_LEVEL_TAGS: &[&str] = &[
    "a", "abbr", "acronym", "audio", "b", "bdi", "bdo", "big", "br", "button", "canvas", "cite",
    "code", "data", "del", "dfn", "em", "embed", "font", "i", "iframe", "img", "input", "ins",
    "kbd", "label", "mark", "math", "meter", "nobr", "object", "output", "picture", "progress",
    "q", "rp", "rt", "ruby", "s", "samp", "select", "small", "span", "strike", "strong", "sub",
    "sup", "svg", "textarea", "time", "tt", "u", "var", "video", "wbr",
];

/// Prefix of the loop parse cache's placeholder element (`dj-pc-<nonce>`).
/// It must stay block-level: the splice relies on whitespace next to it being
/// dropped (see `djust_vdom::splice_loop_placeholders`).
pub const LOOP_PLACEHOLDER_PREFIX: &str = "dj-pc-";

/// Is `tag` (lowercase, as html5ever produces it) an inline-level element?
///
/// The listed tags, plus custom elements (any name containing `-`): an
/// unknown element's default `display` is `inline`, so `<my-badge>A</my-badge>
/// <my-badge>B</my-badge>` reads "A B" in a browser. A custom element styled
/// `display: block` just keeps a `" "` node that renders as nothing. The
/// loop-cache sentinel `dj-pc-*` is the one exception.
pub fn is_inline_level_tag(tag: &str) -> bool {
    INLINE_LEVEL_TAGS.contains(&tag)
        || (tag.contains('-') && !tag.starts_with(LOOP_PLACEHOLDER_PREFIX))
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

// ---------------------------------------------------------------------------
// The egress normalizer's inter-tag pass (#2999)
// ---------------------------------------------------------------------------

/// Void elements: complete at their `>`, so they are a PREVIOUS SIBLING of a
/// whitespace run that follows them. Any other open tag makes the run its
/// first child.
const VOID_TAGS: &[&str] = &[
    "area", "base", "br", "col", "embed", "hr", "img", "input", "keygen", "link", "meta", "param",
    "source", "track", "wbr",
];

const PLACEHOLDER_PREFIX: &str = "__PRESERVED_BLOCK_";

/// `html[..end]` ends with `__PRESERVED_BLOCK_<n>__`: return `n`.
fn placeholder_ending_at(html: &[u8], end: usize) -> Option<usize> {
    if end < 2 || &html[end - 2..end] != b"__" {
        return None;
    }
    let mut i = end - 2;
    let digits_end = i;
    while i > 0 && html[i - 1].is_ascii_digit() {
        i -= 1;
    }
    if i == digits_end || i < PLACEHOLDER_PREFIX.len() {
        return None;
    }
    if &html[i - PLACEHOLDER_PREFIX.len()..i] != PLACEHOLDER_PREFIX.as_bytes() {
        return None;
    }
    std::str::from_utf8(&html[i..digits_end]).ok()?.parse().ok()
}

/// `html[start..]` starts with `__PRESERVED_BLOCK_<n>__`: return `n`.
fn placeholder_starting_at(html: &[u8], start: usize) -> Option<usize> {
    let rest = html.get(start..)?;
    let rest = rest.strip_prefix(PLACEHOLDER_PREFIX.as_bytes())?;
    let digits = rest.iter().take_while(|b| b.is_ascii_digit()).count();
    if digits == 0 || rest.get(digits..digits + 2) != Some(b"__") {
        return None;
    }
    std::str::from_utf8(&rest[..digits]).ok()?.parse().ok()
}

/// `(closing, lowercase name)` of the tag starting at `html[start] == b'<'`.
/// `<image>` is named `img`, as html5ever builds it.
fn tag_name_at(html: &[u8], start: usize) -> Option<(bool, String)> {
    let mut i = start + 1;
    let closing = html.get(i) == Some(&b'/');
    if closing {
        i += 1;
    }
    if !html.get(i)?.is_ascii_alphabetic() {
        return None;
    }
    let name_start = i;
    while i < html.len()
        && !matches!(
            html[i],
            b' ' | b'\t' | b'\n' | b'\r' | b'\x0c' | b'/' | b'>'
        )
    {
        i += 1;
    }
    let name = String::from_utf8_lossy(&html[name_start..i]).to_ascii_lowercase();
    Some((
        closing,
        if name == "image" {
            "img".to_string()
        } else {
            name
        },
    ))
}

/// Is `html[start..end]` one whole tag (`<` name attributes `>`, quoted
/// attribute values may contain `<` and `>`)?
fn is_whole_tag(html: &[u8], start: usize, end: usize) -> bool {
    if tag_name_at(html, start).is_none() {
        return false;
    }
    let mut quote: Option<u8> = None;
    for &c in &html[start + 1..end - 1] {
        match quote {
            Some(q) if c == q => quote = None,
            Some(_) => {}
            None if c == b'"' || c == b'\'' => quote = Some(c),
            None if c == b'<' || c == b'>' => return false,
            None => {}
        }
    }
    quote.is_none()
}

/// Start of the tag ending at `html[end - 1] == b'>'`, skipping a `<` inside
/// a quoted attribute value (`<img alt="<3">`).
fn tag_start(html: &[u8], end: usize) -> Option<usize> {
    let mut pos = end - 1;
    while let Some(start) = html[..pos].iter().rposition(|&b| b == b'<') {
        if is_whole_tag(html, start, end) {
            return Some(start);
        }
        pos = start;
    }
    None
}

fn placeholder_is_inline(block_tags: &[String], n: usize) -> bool {
    block_tags.get(n).is_some_and(|t| is_inline_level_tag(t))
}

fn prev_sibling_is_inline(html: &[u8], mut end: usize, block_tags: &[String]) -> bool {
    while end > 0 {
        if let Some(n) = placeholder_ending_at(html, end) {
            return placeholder_is_inline(block_tags, n);
        }
        if html[end - 1] != b'>' {
            return true; // text
        }
        if end >= 3 && &html[end - 3..end] == b"-->" {
            let Some(start) = find_back(&html[..end], b"<!--") else {
                return false;
            };
            end = start;
            if end > 0 && html[end - 1] == b' ' {
                let before = end - 1;
                if before > 0
                    && (html[before - 1] == b'>' || placeholder_ending_at(html, before).is_some())
                {
                    end = before;
                    continue;
                }
                return before > 0; // text (or nothing)
            }
            continue;
        }
        let Some(start) = tag_start(html, end) else {
            return false;
        };
        let Some((closing, name)) = tag_name_at(html, start) else {
            return false; // <!DOCTYPE> and the like
        };
        if closing {
            return is_inline_level_tag(&name);
        }
        let self_closed_foreign =
            (name == "svg" || name == "math") && end >= 2 && html[end - 2] == b'/';
        if VOID_TAGS.contains(&name.as_str()) || self_closed_foreign {
            return is_inline_level_tag(&name);
        }
        return false; // open tag: the run is its first child
    }
    false
}

fn next_sibling_is_inline(html: &[u8], mut start: usize, block_tags: &[String]) -> bool {
    let n = html.len();
    while start < n {
        if let Some(k) = placeholder_starting_at(html, start) {
            return placeholder_is_inline(block_tags, k);
        }
        if html[start] != b'<' {
            return true; // text
        }
        if html[start..].starts_with(b"<!--") {
            let Some(close) = find_fwd(&html[start + 4..], b"-->") else {
                return false;
            };
            start = start + 4 + close + 3;
            if start < n && html[start] == b' ' {
                let after = start + 1;
                if after < n
                    && (html[after] == b'<' || placeholder_starting_at(html, after).is_some())
                {
                    start = after;
                    continue;
                }
                return after < n; // text (or nothing)
            }
            continue;
        }
        return match tag_name_at(html, start) {
            Some((false, name)) => is_inline_level_tag(&name),
            _ => false, // closing tag (no next sibling) or <!DOCTYPE>
        };
    }
    false
}

fn find_back(hay: &[u8], needle: &[u8]) -> Option<usize> {
    hay.windows(needle.len()).rposition(|w| w == needle)
}

fn find_fwd(hay: &[u8], needle: &[u8]) -> Option<usize> {
    hay.windows(needle.len()).position(|w| w == needle)
}

/// Drop the whitespace between two tags unless both neighbours are inline —
/// the egress normalizer's half of the rule `build_children` applies in the
/// VDOM parser (#2999).
///
/// `html` has already had every whitespace run collapsed to one space and its
/// `pre`/`code`/`textarea`/`script`/`style` blocks replaced by
/// `__PRESERVED_BLOCK_<i>__` placeholders whose tag names are
/// `block_tags[i]` (see `TemplateMixin._strip_comments_and_whitespace`). A
/// space between two tag-like tokens (tags, comments, placeholders) is kept
/// exactly when the parser keeps it as a `" "` node: its nearest non-comment
/// neighbour on each side is text or an inline-level element. Decisions are
/// made on the input, never on partially rewritten output.
pub fn collapse_inter_tag_whitespace(html: &str, block_tags: &[String]) -> String {
    let bytes = html.as_bytes();
    let mut out = String::with_capacity(html.len());
    let mut copied = 0;
    let mut i = 1;
    while i + 1 < bytes.len() {
        if bytes[i] == b' '
            && matches!(bytes[i - 1], b'>' | b'_')
            && matches!(bytes[i + 1], b'<' | b'_')
        {
            let prev_ok = bytes[i - 1] == b'>' || placeholder_ending_at(bytes, i).is_some();
            let next_ok = bytes[i + 1] == b'<' || placeholder_starting_at(bytes, i + 1).is_some();
            if prev_ok && next_ok {
                let keep = !bytes[i + 1..].starts_with(b"</")
                    && next_sibling_is_inline(bytes, i + 1, block_tags)
                    && prev_sibling_is_inline(bytes, i, block_tags);
                if !keep {
                    out.push_str(&html[copied..i]);
                    copied = i + 1;
                }
            }
        }
        i += 1;
    }
    out.push_str(&html[copied..]);
    out
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
            "div", "p", "li", "ul", "ol", "tr", "td", "table", "section", "pre", "h1", "dj-pc-1",
            "dj-pc-7f",
        ] {
            assert!(!is_inline_level_tag(tag), "{tag} must be block-level");
        }
        for tag in [
            "b",
            "i",
            "strong",
            "code",
            "span",
            "a",
            "img",
            "svg",
            "br",
            "my-widget",
            "sl-badge",
        ] {
            assert!(is_inline_level_tag(tag), "{tag} must be inline-level");
        }
    }

    fn collapse(html: &str) -> String {
        collapse_inter_tag_whitespace(html, &[])
    }

    #[test]
    fn collapse_keeps_inline_pairs_only() {
        assert_eq!(
            collapse("<p><b>A</b> <i>B</i></p>"),
            "<p><b>A</b> <i>B</i></p>"
        );
        assert_eq!(
            collapse("<div> <div>a</div> <div>b</div> </div>"),
            "<div><div>a</div><div>b</div></div>"
        );
        assert_eq!(collapse("<p> <b>A</b> </p>"), "<p><b>A</b></p>");
        assert_eq!(collapse("<p><br> <b>A</b></p>"), "<p><br> <b>A</b></p>");
        assert_eq!(
            collapse("<p><img alt=\"<3\"> <b>h</b></p>"),
            "<p><img alt=\"<3\"> <b>h</b></p>"
        );
        assert_eq!(
            collapse("<p><image src=x> <b>y</b></p>"),
            "<p><image src=x> <b>y</b></p>"
        );
        assert_eq!(
            collapse("<p><x-a>1</x-a> <x-b>2</x-b></p>"),
            "<p><x-a>1</x-a> <x-b>2</x-b></p>"
        );
        assert_eq!(
            collapse("<p><b>A</b> <!--dj-if id=\"x\"--><i>B</i><!--/dj-if--> <u>C</u></p>"),
            "<p><b>A</b> <!--dj-if id=\"x\"--><i>B</i><!--/dj-if--> <u>C</u></p>"
        );
        assert_eq!(
            collapse("<div>a</div> <!--x--> <div>b</div>"),
            "<div>a</div><!--x--><div>b</div>"
        );
        assert_eq!(collapse("<p>a_ _b</p>"), "<p>a_ _b</p>");
    }

    #[test]
    fn collapse_uses_placeholder_tags() {
        let tags = vec!["code".to_string(), "pre".to_string()];
        assert_eq!(
            collapse_inter_tag_whitespace(
                "<p><b>x</b> __PRESERVED_BLOCK_0__ __PRESERVED_BLOCK_1__</p>",
                &tags
            ),
            "<p><b>x</b> __PRESERVED_BLOCK_0____PRESERVED_BLOCK_1__</p>"
        );
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
