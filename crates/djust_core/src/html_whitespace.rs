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

use std::borrow::Cow;

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

/// End of the markup token starting at `html[start] == b'<'` (exclusive), or
/// `None` when `<` does not open one (`a < b` is text).
///
/// A comment runs to its `-->`; any other token to the first `>` outside a
/// quoted attribute value. A quote opens a value only right after `=`, so an
/// apostrophe in an unquoted value (`title=it's`) cannot swallow the page.
fn markup_token_end(html: &[u8], start: usize) -> Option<usize> {
    let next = *html.get(start + 1)?;
    if !(next.is_ascii_alphabetic() || matches!(next, b'/' | b'!' | b'?')) {
        return None;
    }
    if html[start..].starts_with(b"<!--") {
        return Some(
            find_fwd(&html[start + 4..], b"-->").map_or(html.len(), |i| start + 4 + i + 3),
        );
    }
    let mut quote: Option<u8> = None;
    let mut last = b'<';
    for (offset, &c) in html[start + 1..].iter().enumerate() {
        match quote {
            Some(q) if c == q => quote = None,
            Some(_) => {}
            None if (c == b'"' || c == b'\'') && last == b'=' => quote = Some(c),
            None if c == b'>' => return Some(start + 1 + offset + 1),
            None => {}
        }
        if !c.is_ascii_whitespace() {
            last = c;
        }
    }
    Some(html.len())
}

/// The whitespace runs the egress normalizer folds to one `" "`: every run
/// inside markup (tags, the comments that survive), and every text run made
/// only of whitespace (which [`collapse_inter_tag_whitespace`] then keeps or
/// drops). The whitespace of a text run that has content is never touched.
///
/// That last part is the parser's rule: `build_children` keeps a text node
/// with content verbatim, and Django renders it verbatim, so
/// `<p class="whitespace-pre-wrap">a\n\nb</p>` must reach the browser with its
/// line feeds. Folding it made a page loaded over HTTP read "a b" while the
/// same text patched in over the socket kept its lines.
///
/// Expects `pre`/`code`/`textarea`/`script`/`style` already replaced by
/// `__PRESERVED_BLOCK_<n>__` placeholders, which count as markup here.
pub fn fold_whitespace_edits(html: &str) -> Vec<(usize, usize)> {
    let bytes = html.as_bytes();
    let mut edits = Vec::new();
    let fold_runs = |from: usize, to: usize, edits: &mut Vec<(usize, usize)>| {
        let mut i = from;
        while i < to {
            if bytes[i].is_ascii_whitespace() {
                let start = i;
                while i < to && bytes[i].is_ascii_whitespace() {
                    i += 1;
                }
                if &bytes[start..i] != b" " {
                    edits.push((start, i));
                }
            } else {
                i += 1;
            }
        }
    };
    let mut text_start = 0;
    let mut i = 0;
    while i < bytes.len() {
        let token_end = if bytes[i] == b'<' {
            markup_token_end(bytes, i)
        } else if bytes[i] == b'_' {
            placeholder_starting_at(bytes, i)
                .map(|n| i + PLACEHOLDER_PREFIX.len() + n.to_string().len() + 2)
        } else {
            None
        };
        let Some(end) = token_end else {
            i += 1;
            continue;
        };
        if is_html_whitespace_only(&html[text_start..i]) {
            fold_runs(text_start, i, &mut edits);
        }
        if bytes[i] == b'<' {
            fold_runs(i, end, &mut edits);
        }
        i = end;
        text_start = end;
    }
    if is_html_whitespace_only(&html[text_start..]) {
        fold_runs(text_start, bytes.len(), &mut edits);
    }
    edits
}

/// Apply [`fold_whitespace_edits`].
pub fn fold_whitespace(html: &str) -> String {
    let mut out = String::with_capacity(html.len());
    let mut cursor = 0;
    for (start, end) in fold_whitespace_edits(html) {
        out.push_str(&html[cursor..start]);
        out.push(' ');
        cursor = end;
    }
    out.push_str(&html[cursor..]);
    out
}

/// Drop the whitespace between two tags unless both neighbours are inline —
/// the egress normalizer's half of the rule `build_children` applies in the
/// VDOM parser (#2999).
///
/// `html` has already been through [`fold_whitespace`] and had its
/// `pre`/`code`/`textarea`/`script`/`style` blocks replaced by
/// `__PRESERVED_BLOCK_<i>__` placeholders whose tag names are
/// `block_tags[i]` (see `TemplateMixin._strip_comments_and_whitespace`). A
/// space between two tag-like tokens (tags, comments, placeholders) is kept
/// exactly when the parser keeps it as a `" "` node: its nearest non-comment
/// neighbour on each side is text or an inline-level element. Decisions are
/// made on the input, never on partially rewritten output.
pub fn collapse_inter_tag_whitespace(html: &str, block_tags: &[String]) -> String {
    collapse_with_edits(html, block_tags, |_, _| {})
}

/// Exact deleted UTF-8 ranges for composing a render-result sideband (#3252).
pub fn inter_tag_whitespace_edits(html: &str, block_tags: &[String]) -> Vec<(usize, usize)> {
    let mut edits = Vec::new();
    collapse_with_edits(html, block_tags, |start, end| edits.push((start, end)));
    edits
}

fn collapse_with_edits<F: FnMut(usize, usize)>(
    html: &str,
    block_tags: &[String],
    mut removed: F,
) -> String {
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
                    removed(i, i + 1);
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

// ---------------------------------------------------------------------------
// The whole egress normalizer, in one GIL-free pass
// ---------------------------------------------------------------------------

/// Characters that make the Python regex engine's case-insensitive match of an
/// ASCII tag letter succeed on a non-ASCII character (`ſ` matches `s`, `İ` and
/// `ı` match `i`). Inputs holding one go to the Python normalizer instead, so
/// the two never disagree on which `<script>` is a script.
const PY_IGNORECASE_SPECIALS: [char; 3] = ['\u{130}', '\u{131}', '\u{17f}'];

/// Python's `\s` on a `str` (`str.isspace()`), checked against every code
/// point by `tests/unit/test_egress_normalizer_rust_parity.py`.
fn is_py_whitespace(c: char) -> bool {
    matches!(
        c,
        '\t' | '\n'
            | '\x0b'
            | '\x0c'
            | '\r'
            | '\x1c'..='\x1f'
            | ' '
            | '\u{85}'
            | '\u{a0}'
            | '\u{1680}'
            | '\u{2000}'..='\u{200a}'
            | '\u{2028}'
            | '\u{2029}'
            | '\u{202f}'
            | '\u{205f}'
            | '\u{3000}'
    )
}

/// `html[at..]` starts with `<` + `tag` (ASCII, case-insensitive) — or, when
/// `closing`, with `</` + `tag`. No boundary after the name: the normalizer's
/// regex `<pre[^>]*>` matches `<preview>` too, and this keeps that.
fn starts_tag(html: &[u8], at: usize, tag: &[u8], closing: bool) -> bool {
    let lead: &[u8] = if closing { b"</" } else { b"<" };
    let end = at + lead.len() + tag.len();
    end <= html.len()
        && &html[at..at + lead.len()] == lead
        && html[at + lead.len()..end].eq_ignore_ascii_case(tag)
}

/// Preserved before the comment strip (their bodies are raw text, #1927) …
const RAW_TEXT_TAGS: [&[u8]; 2] = [b"script", b"style"];
/// … and after it.
const WHITESPACE_TAGS: [&[u8]; 3] = [b"pre", b"code", b"textarea"];

/// Which of `RAW_TEXT_TAGS ++ WHITESPACE_TAGS` has an open tag in `html`, in
/// one scan, so a pass that cannot match is skipped instead of copying the
/// page. A false "present" only costs a pass that finds nothing.
fn open_tags_present(html: &str) -> [bool; 5] {
    let bytes = html.as_bytes();
    let mut present = [false; 5];
    let tags = RAW_TEXT_TAGS.iter().chain(WHITESPACE_TAGS.iter());
    let tags: Vec<&[u8]> = tags.copied().collect();
    for (i, &b) in bytes.iter().enumerate() {
        if b != b'<' {
            continue;
        }
        for (k, tag) in tags.iter().enumerate() {
            if !present[k] && starts_tag(bytes, i, tag, false) {
                present[k] = true;
            }
        }
        if present.iter().all(|&p| p) {
            break;
        }
    }
    present
}

/// `<tag[^>]*>.*?</tag[^>]*>` (DOTALL, IGNORECASE): every match replaced by a
/// `__PRESERVED_BLOCK_<n>__` placeholder, its text pushed to `blocks`.
fn preserve_raw_blocks(html: &str, tag: &[u8], blocks: &mut Vec<String>) -> String {
    let bytes = html.as_bytes();
    let mut out = String::with_capacity(html.len());
    let mut copied = 0;
    let mut i = 0;
    while let Some(off) = html[i..].find('<') {
        let start = i + off;
        if !starts_tag(bytes, start, tag, false) {
            i = start + 1;
            continue;
        }
        // `[^>]*>`: the open tag ends at the first `>`. With none left, no
        // later start can match either.
        let Some(open_gt) = find_byte(html, start + 1 + tag.len(), '>') else {
            break;
        };
        // `.*?</tag[^>]*>`: the first `</tag` that has a `>` after it — and
        // if the first has none, no later one does.
        let mut j = open_gt + 1;
        let close_end = loop {
            let Some(off) = html[j..].find("</") else {
                break None;
            };
            let at = j + off;
            if starts_tag(bytes, at, tag, true) {
                break find_byte(html, at + 2 + tag.len(), '>').map(|gt| gt + 1);
            }
            j = at + 1;
        };
        let Some(end) = close_end else {
            break;
        };
        out.push_str(&html[copied..start]);
        out.push_str(PLACEHOLDER_PREFIX);
        out.push_str(&blocks.len().to_string());
        out.push_str("__");
        blocks.push(html[start..end].to_string());
        copied = end;
        i = end;
    }
    out.push_str(&html[copied..]);
    out
}

fn find_byte(html: &str, from: usize, needle: char) -> Option<usize> {
    html.get(from..)?.find(needle).map(|i| from + i)
}

/// `<!--(?!\s*/?dj-if\b).*?-->` (DOTALL) removed: every comment except the
/// dj-if boundary markers (#1678). `None` when the `\b` after `dj-if` meets a
/// non-ASCII character, whose word-ness is Python's `str.isalnum()`.
fn strip_comments(html: &str) -> Option<String> {
    let mut out = String::with_capacity(html.len());
    let mut copied = 0;
    let mut i = 0;
    while let Some(off) = html[i..].find("<!--") {
        let start = i + off;
        let body = start + 4;
        // The negative lookahead: `\s*` `/?` `dj-if` then a word boundary.
        let rest = &html[body..];
        let trimmed = rest.trim_start_matches(is_py_whitespace);
        let trimmed = trimmed.strip_prefix('/').unwrap_or(trimmed);
        if let Some(after) = trimmed.strip_prefix("dj-if") {
            let is_marker = match after.chars().next() {
                None => true,
                Some(c) if c.is_ascii() => !(c.is_ascii_alphanumeric() || c == '_'),
                Some(_) => return None,
            };
            if is_marker {
                i = start + 1;
                continue;
            }
        }
        let Some(close) = html[body..].find("-->") else {
            break;
        };
        out.push_str(&html[copied..start]);
        copied = body + close + 3;
        i = copied;
    }
    out.push_str(&html[copied..]);
    Some(out)
}

/// `<([A-Za-z][^\s/>]*)` on a preserved block, lowercased: the tag name the
/// inter-tag pass asks "inline?" about.
fn block_tag(block: &str) -> String {
    let name = block
        .get(1..)
        .unwrap_or("")
        .split(|c: char| is_py_whitespace(c) || c == '/' || c == '>')
        .next()
        .unwrap_or("");
    name.to_lowercase()
}

/// Python's `for i, b in enumerate(blocks): html = html.replace(P_i, b)`.
///
/// When the input held no placeholder-looking text, every placeholder occurs
/// once and a block only ever holds lower-numbered ones (blocks extracted
/// earlier), which the sequential replace never reaches — so one
/// left-to-right pass that inserts each block verbatim gives the same bytes.
fn restore_blocks(html: &str, blocks: &[String], input_had_marker: bool) -> String {
    if input_had_marker {
        let mut out = html.to_string();
        for (n, block) in blocks.iter().enumerate() {
            out = out.replace(&format!("{PLACEHOLDER_PREFIX}{n}__"), block);
        }
        return out;
    }
    let bytes = html.as_bytes();
    let extra: usize = blocks.iter().map(String::len).sum();
    let mut out = String::with_capacity(html.len() + extra);
    let mut copied = 0;
    let mut i = 0;
    while let Some(off) = html[i..].find(PLACEHOLDER_PREFIX) {
        let at = i + off;
        match placeholder_starting_at(bytes, at) {
            Some(n) if n < blocks.len() => {
                out.push_str(&html[copied..at]);
                out.push_str(&blocks[n]);
                copied = at + PLACEHOLDER_PREFIX.len() + n.to_string().len() + 2;
                i = copied;
            }
            _ => i = at + 1,
        }
    }
    out.push_str(&html[copied..]);
    out
}

/// The egress normalizer `TemplateMixin._strip_comments_and_whitespace`,
/// byte for byte, for a plain (non-provenance) render.
///
/// 1. `<script>` and `<style>` blocks are hidden behind placeholders (#1927),
///    BEFORE comments are stripped, so comment-like text in JS/CSS survives;
/// 2. comments are removed, except the dj-if boundary markers (#1678);
/// 3. `<pre>`, `<code>` and `<textarea>` blocks are hidden;
/// 4. [`fold_whitespace`] (#3570: text with content keeps its white space);
/// 5. [`collapse_inter_tag_whitespace`] (#2999), told each placeholder's tag;
/// 6. the blocks are put back verbatim.
///
/// Returns `None` for the inputs whose Python semantics this does not model
/// (see [`PY_IGNORECASE_SPECIALS`] and [`strip_comments`]); the caller then
/// runs the Python normalizer. `tests/unit/test_egress_normalizer_rust_parity.py`
/// holds the two to identical bytes over a fixture and fuzzed corpus.
pub fn strip_comments_and_whitespace(html: &str) -> Option<String> {
    if html.contains(PY_IGNORECASE_SPECIALS) {
        return None;
    }
    let input_had_marker = html.contains(PLACEHOLDER_PREFIX);
    let mut blocks = Vec::new();
    let mut present = open_tags_present(html);
    let mut s = Cow::Borrowed(html);
    for (k, tag) in RAW_TEXT_TAGS.iter().enumerate() {
        if present[k] {
            s = Cow::Owned(preserve_raw_blocks(&s, tag, &mut blocks));
        }
    }
    if s.contains("<!--") {
        let stripped = strip_comments(&s)?;
        if stripped.len() != s.len() {
            // Removing a comment can join `<` and `pre>` into a new tag.
            present = open_tags_present(&stripped);
        }
        s = Cow::Owned(stripped);
    }
    for (k, tag) in WHITESPACE_TAGS.iter().enumerate() {
        if present[RAW_TEXT_TAGS.len() + k] {
            s = Cow::Owned(preserve_raw_blocks(&s, tag, &mut blocks));
        }
    }
    let s = fold_whitespace(&s);
    let tags: Vec<String> = blocks.iter().map(|b| block_tag(b)).collect();
    let s = collapse_inter_tag_whitespace(&s, &tags);
    Some(restore_blocks(&s, &blocks, input_had_marker))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fold_keeps_text_with_content_verbatim() {
        assert_eq!(
            fold_whitespace("<p class=\"x\">a\n\nb  c</p>"),
            "<p class=\"x\">a\n\nb  c</p>"
        );
        assert_eq!(fold_whitespace("<p>\n   lead\n</p>"), "<p>\n   lead\n</p>");
    }

    #[test]
    fn fold_folds_markup_and_whitespace_only_text() {
        assert_eq!(
            fold_whitespace("<div\n   class=\"a\n b\">\n\n  <span>x</span>\t\n</div>"),
            "<div class=\"a b\"> <span>x</span> </div>"
        );
        assert_eq!(fold_whitespace("  \n"), " ");
        assert_eq!(
            fold_whitespace("<!--dj-if\n id=\"if-1\"-->\n\n<!--/dj-if-->"),
            "<!--dj-if id=\"if-1\"--> <!--/dj-if-->"
        );
    }

    #[test]
    fn fold_treats_placeholders_and_stray_lt_correctly() {
        assert_eq!(
            fold_whitespace("<p>a\n __PRESERVED_BLOCK_0__\n\n__PRESERVED_BLOCK_1__  b</p>"),
            "<p>a\n __PRESERVED_BLOCK_0__ __PRESERVED_BLOCK_1__  b</p>"
        );
        // `<` that opens no tag is text; an apostrophe in an unquoted value
        // does not open a quote.
        assert_eq!(fold_whitespace("<p>1 <  2</p>"), "<p>1 <  2</p>");
        assert_eq!(
            fold_whitespace("<a title=it's\n  href=\"/\">x  y</a>"),
            "<a title=it's href=\"/\">x  y</a>"
        );
        // NBSP is content, never folded.
        assert_eq!(
            fold_whitespace("<p>\u{a0}\u{a0}</p>"),
            "<p>\u{a0}\u{a0}</p>"
        );
    }

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

    fn strip(html: &str) -> String {
        strip_comments_and_whitespace(html).expect("modelled input")
    }

    #[test]
    fn strip_keeps_raw_text_and_markers() {
        assert_eq!(
            strip("<div>\n  <script>\n// c\nvar s = '<!-- x -->';\n</script >\n</div>"),
            "<div><script>\n// c\nvar s = '<!-- x -->';\n</script ></div>"
        );
        assert_eq!(
            strip("<p>a<!-- gone -->b</p> <!--dj-if id=\"1\"--> <!--/dj-if-->"),
            "<p>ab</p><!--dj-if id=\"1\"--><!--/dj-if-->"
        );
        assert_eq!(strip("<p>x<!--dj-ifx-->y</p>"), "<p>xy</p>");
        assert_eq!(
            strip("<div>\n <pre> a\n  b </pre>\n <p>t  u</p>\n</div>"),
            "<div><pre> a\n  b </pre><p>t  u</p></div>"
        );
    }

    #[test]
    fn strip_finds_a_tag_a_removed_comment_joins() {
        assert_eq!(
            strip("<div> <<!-- c -->pre>a  b</pre> </div>"),
            "<div><pre>a  b</pre></div>"
        );
    }

    #[test]
    fn strip_declines_what_python_folds_differently() {
        assert_eq!(
            strip_comments_and_whitespace("<\u{17f}cript>a</script>"),
            None
        );
        assert_eq!(strip_comments_and_whitespace("<!--dj-if\u{e9}-->"), None);
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
