//! The digest shape of a rendered root, natively (`_render_digest.render_shape`).
//!
//! The page-POST fallback checks a client's DOM against the server's render by
//! a checksum of the render's element tree (#4108, #4283). Python walked the
//! HTML with a regex tokenizer at about 0.2 ms per KB, a quarter of a page GET
//! and of a fallback turn on a large page. This is the same walk, token for
//! token: the same two regular expressions (`regex` has the leftmost-first
//! semantics of Python's `re` for these patterns, which use no look-around or
//! back-references), the same child-counting rule, the same text hashed, the
//! same crc32 + adler32. `python/djust/tests/test_render_shape_native.py`
//! holds the two together on the shared vectors and on random documents.
//!
//! Character references are decoded by the caller's `unescape` (Python's
//! `html.unescape`), called only for a text or attribute value that holds a
//! `&`, so the HTML5 reference table has one implementation.

use once_cell::sync::Lazy;
use pyo3::prelude::*;
use regex::Regex;
use std::collections::BTreeMap;

/// Python's `\s` for `str` patterns: Unicode `White_Space` plus U+001C..U+001F,
/// which `str.isspace()` counts and Rust's `\s` does not.
const PY_SPACE: &str = r"\s\x1c-\x1f";

static TOKEN: Lazy<Regex> = Lazy::new(|| {
    Regex::new(&format!(
        r#"(?s)<!--(?P<comment>.*?)--!?>|<(?P<close>/?)(?P<tag>[A-Za-z][^{s}/>]*)(?P<attrs>(?:[^>"']|"[^"]*"|'[^']*')*)>"#,
        s = PY_SPACE
    ))
    .expect("TOKEN pattern")
});

/// One token TOKEN matched, split into its groups.
struct Token<'a> {
    comment: Option<&'a str>,
    close: bool,
    tag: &'a str,
    attrs: &'a str,
}

/// TOKEN's groups for its match `start..end` of `html`.
///
/// `regex` finds a match with its DFA at a tenth of the cost of resolving the
/// match's capture groups, so the groups are read off the matched text. A
/// match that starts `<!--` is a comment (the tag branch needs a letter after
/// `<`) and ends `-->` or `--!>`. Otherwise `/` is the close group, the tag is
/// the longest run of tag characters, and the attributes are the rest up to
/// the final `>`: the regex tries the longest tag first, and that try succeeds
/// whenever a shorter one does, because the attribute group takes any run of
/// tag characters that holds no quote. A tag run holding a quote is the one
/// case where the regex may settle on a shorter tag to close a quoted value,
/// so that rare token asks the regex for its groups.
fn split_token(html: &str, start: usize, end: usize) -> Option<Token<'_>> {
    let text = &html[start..end];
    if let Some(body) = text.strip_prefix("<!--") {
        let inner = body
            .strip_suffix("--!>")
            .or_else(|| body.strip_suffix("-->"))?;
        return Some(Token {
            comment: Some(inner),
            close: false,
            tag: "",
            attrs: "",
        });
    }
    let inner = text.strip_prefix('<')?.strip_suffix('>')?;
    let (close, rest) = match inner.strip_prefix('/') {
        Some(rest) => (true, rest),
        None => (false, inner),
    };
    let tag_end = rest
        .char_indices()
        .find(|&(_, c)| is_py_space(c) || c == '/' || c == '>')
        .map_or(rest.len(), |(i, _)| i);
    let tag = &rest[..tag_end];
    if tag.contains(['"', '\'']) {
        let caps = TOKEN.captures_at(html, start)?;
        return Some(Token {
            comment: caps.name("comment").map(|m| m.as_str()),
            close: caps.name("close").is_some_and(|m| !m.as_str().is_empty()),
            tag: caps.name("tag").map_or("", |m| m.as_str()),
            attrs: caps.name("attrs").map_or("", |m| m.as_str()),
        });
    }
    Some(Token {
        comment: None,
        close,
        tag,
        attrs: &rest[tag_end..],
    })
}

/// One attribute of a start tag's attribute text: Python's
///
/// ```text
/// ([^\s"'=<>/]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?
/// ```
///
/// scanned by hand, because `regex` resolves its capture groups at about
/// 0.8 us an attribute, nine tenths of the whole walk. The pattern needs no
/// backtracking a scanner would have to imitate: the name is the longest run
/// of name characters (the optional value after it cannot shorten it), the
/// spaces around `=` cannot be `=` or a value's first character, and a value
/// that does not complete (an unclosed quote, nothing after `=`) drops the
/// whole optional group, leaving the bare name and resuming the search after
/// it, as `finditer` does.
struct Attrs<'a> {
    text: &'a str,
    at: usize,
}

fn is_name_char(c: char) -> bool {
    !(is_py_space(c) || matches!(c, '"' | '\'' | '=' | '<' | '>' | '/'))
}

impl<'a> Attrs<'a> {
    fn new(text: &'a str) -> Self {
        Attrs { text, at: 0 }
    }

    /// The end of the run of characters from `from` that satisfy `keep`.
    fn run(&self, from: usize, keep: impl Fn(char) -> bool) -> usize {
        self.text[from..]
            .char_indices()
            .find(|&(_, c)| !keep(c))
            .map_or(self.text.len(), |(i, _)| from + i)
    }

    /// The value after the name ending at `from`, and where it ends.
    fn value(&self, from: usize) -> Option<(&'a str, usize)> {
        let eq = self.run(from, is_py_space);
        if !self.text[eq..].starts_with('=') {
            return None;
        }
        let start = self.run(eq + 1, is_py_space);
        let rest = &self.text[start..];
        let quote = rest.chars().next()?;
        if quote == '"' || quote == '\'' {
            let close = rest[1..].find(quote)?;
            return Some((&rest[1..1 + close], start + 1 + close + 1));
        }
        if quote == '>' || is_py_space(quote) {
            return None;
        }
        let end = self.run(start, |c| {
            !(is_py_space(c) || matches!(c, '"' | '\'' | '>'))
        });
        Some((&self.text[start..end], end))
    }
}

impl<'a> Iterator for Attrs<'a> {
    /// `(name, value)`; `value` is `""` for a bare name, as `group(4) or ""`.
    type Item = (&'a str, &'a str);

    fn next(&mut self) -> Option<Self::Item> {
        let (offset, _) = self.text[self.at..]
            .char_indices()
            .find(|&(_, c)| is_name_char(c))?;
        let start = self.at + offset;
        let end = self.run(start, is_name_char);
        let name = &self.text[start..end];
        match self.value(end) {
            Some((value, after)) => {
                self.at = after;
                Some((name, value))
            }
            None => {
                self.at = end;
                Some((name, ""))
            }
        }
    }
}

const SKIPPED_ATTRS: &[&str] = &[
    "class",
    "style",
    "dj-id",
    "open",
    "hidden",
    "disabled",
    "checked",
    "selected",
    "tabindex",
    "role",
    "data-live-hydrated",
    "data-open",
    "data-visible",
    "data-position",
    "data-current",
    "data-theme",
    "data-theme-mode",
];
const SKIPPED_PREFIXES: &[&str] = &[
    "aria-",
    "data-djust-",
    "data-dj-",
    "data-upload-",
    "data-stream-",
];
const VOID: &[&str] = &[
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source",
    "track", "wbr",
];
const KEEPS_WHITESPACE: &[&str] = &["pre", "code", "textarea", "script", "style"];
const DROPS_FIRST_NEWLINE: &[&str] = &["pre", "textarea", "listing"];
const RAW_TEXT: &[&str] = &["script", "style", "textarea", "title"];

/// Python's `str.strip()` set (`str.isspace()`).
fn is_py_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

fn is_ascii_space(c: char) -> bool {
    matches!(c, ' ' | '\t' | '\n' | '\r' | '\u{0c}')
}

fn shape_attr_counts(name: &str) -> bool {
    !SKIPPED_ATTRS.contains(&name) && !SKIPPED_PREFIXES.iter().any(|p| name.starts_with(p))
}

fn is_dj_if_comment(text: &str) -> bool {
    let text = text.trim_matches(is_py_space);
    text == "dj-if" || text == "/dj-if" || text.starts_with("dj-if ") || text.starts_with("dj-if\t")
}

/// `_shape_text`: runs of ASCII whitespace as one space, trimmed of spaces.
fn shape_text(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut in_run = false;
    for c in text.chars() {
        if is_ascii_space(c) {
            if !in_run {
                out.push(' ');
                in_run = true;
            }
        } else {
            out.push(c);
            in_run = false;
        }
    }
    out.trim_matches(' ').to_string()
}

fn counts_as_text(text: &str, keeps_whitespace: bool) -> bool {
    if text.is_empty() {
        return false;
    }
    if keeps_whitespace || text == " " {
        return true;
    }
    !text.trim_matches(is_ascii_space).is_empty()
}

/// Python's `str.lower()` for a tag or attribute name.
fn py_lower(s: &str) -> String {
    if s.is_ascii() {
        s.to_ascii_lowercase()
    } else {
        s.to_lowercase()
    }
}

struct Walker<'py, 'a> {
    unescape: &'a Bound<'py, PyAny>,
}

impl Walker<'_, '_> {
    fn unescape(&self, text: &str) -> PyResult<String> {
        if !text.contains('&') {
            return Ok(text.to_string());
        }
        self.unescape.call1((text,))?.extract()
    }

    fn counted_attrs(&self, attrs: &str) -> PyResult<BTreeMap<String, String>> {
        let mut pairs: BTreeMap<String, String> = BTreeMap::new();
        for (name, value) in Attrs::new(attrs) {
            let name = py_lower(name);
            if pairs.contains_key(&name) || !shape_attr_counts(&name) {
                continue;
            }
            let value = self.unescape(value)?;
            pairs.insert(name, value);
        }
        Ok(pairs)
    }
}

fn element_piece(out: &mut String, tag: &str, pairs: &BTreeMap<String, String>) {
    let drop_value = pairs.get("name").map(String::as_str) == Some("csrfmiddlewaretoken");
    out.push('<');
    out.push_str(tag);
    // BTreeMap iterates by name, as Python's `sorted(pairs)` (code-point order).
    for (name, value) in pairs {
        if drop_value && name == "value" {
            continue;
        }
        out.push(' ');
        out.push_str(name);
        out.push('=');
        out.push_str(value);
        out.push('\0');
    }
}

fn is_opaque(pairs: &BTreeMap<String, String>) -> bool {
    pairs.get("dj-update").map(String::as_str) == Some("ignore")
}

fn is_client_owned(pairs: &BTreeMap<String, String>) -> bool {
    if let Some(v) = pairs.get("dj-update") {
        if v != "ignore" {
            return true;
        }
    }
    pairs
        .keys()
        .any(|n| n == "dj-virtual" || n.starts_with("dj-virtual-"))
}

/// `</tag\s*>` for each raw-text element, case-insensitive (Python's
/// `re.IGNORECASE` folds as `regex`'s `(?i)` does: `</ſcript>` ends a script).
static RAW_TEXT_ENDS: Lazy<Vec<(&'static str, Regex)>> = Lazy::new(|| {
    RAW_TEXT
        .iter()
        .map(|tag| {
            let re = Regex::new(&format!(r"(?i)</{}[{}]*>", tag, PY_SPACE)).expect("raw-text end");
            (*tag, re)
        })
        .collect()
});

/// The span of a raw-text element's end tag at or after `from`.
fn raw_text_end(html: &str, tag: &str, from: usize) -> Option<(usize, usize)> {
    let (_, re) = RAW_TEXT_ENDS.iter().find(|(t, _)| *t == tag)?;
    re.find_at(html, from).map(|m| (m.start(), m.end()))
}

fn crc32(data: &[u8]) -> u32 {
    static TABLE: Lazy<[u32; 256]> = Lazy::new(|| {
        let mut table = [0u32; 256];
        for (i, slot) in table.iter_mut().enumerate() {
            let mut c = i as u32;
            for _ in 0..8 {
                c = if c & 1 != 0 {
                    0xEDB8_8320 ^ (c >> 1)
                } else {
                    c >> 1
                };
            }
            *slot = c;
        }
        table
    });
    let mut crc = 0xFFFF_FFFFu32;
    for &b in data {
        crc = TABLE[((crc ^ b as u32) & 0xFF) as usize] ^ (crc >> 8);
    }
    crc ^ 0xFFFF_FFFF
}

fn adler32(data: &[u8]) -> u32 {
    const MOD: u32 = 65521;
    let (mut a, mut b) = (1u32, 0u32);
    for chunk in data.chunks(5552) {
        for &x in chunk {
            a += x as u32;
            b += a;
        }
        a %= MOD;
        b %= MOD;
    }
    (b << 16) | a
}

type Shape = (String, Vec<Vec<usize>>, bool);

fn walk(html: &str, w: &Walker<'_, '_>) -> PyResult<Option<Shape>> {
    let mut out = String::with_capacity(html.len() / 2);
    let mut stack: Vec<String> = Vec::new();
    let mut counts: Vec<usize> = Vec::new();
    let mut paths: Vec<Vec<usize>> = Vec::new();
    let mut opaque_paths: Vec<Vec<usize>> = Vec::new();
    let mut client_owned = false;
    let mut opaque_depth = 0usize;
    let mut position = 0usize;
    let mut after_start_of = String::new();

    macro_rules! inside_opaque {
        () => {
            opaque_depth != 0 && stack.len() >= opaque_depth
        };
    }
    // `text_child`: a closure cannot borrow the state mutably and be called in
    // a loop that also mutates it, so it is a macro over the same names.
    macro_rules! text_child {
        ($text:expr, $parent:expr, $references:expr) => {{
            if !inside_opaque!() {
                let raw: &str = $text;
                let text = if $references {
                    w.unescape(raw)?
                } else {
                    raw.to_string()
                };
                let keeps = KEEPS_WHITESPACE.contains(&$parent);
                if counts_as_text(&text, keeps) {
                    out.push('"');
                    out.push_str(&shape_text(&text));
                    out.push('\0');
                    if let Some(last) = counts.last_mut() {
                        *last += 1;
                    }
                }
            }
        }};
    }

    // One `find_iter` pass, restarted only after a raw-text element's content
    // is skipped: a fresh search per token costs several times as much. TOKEN
    // has no anchors or look-around, so searching a
    // suffix finds what searching from an offset finds.
    'restart: loop {
        let base = position;
        for whole in TOKEN.find_iter(&html[base..]) {
            let (start, end) = (base + whole.start(), base + whole.end());
            let Some(token) = split_token(html, start, end) else {
                return Err(pyo3::exceptions::PyRuntimeError::new_err(
                    "render_shape: a token without its groups",
                ));
            };
            let mut text = &html[position..start];
            position = end;
            if DROPS_FIRST_NEWLINE.contains(&after_start_of.as_str()) && text.starts_with('\n') {
                text = &text[1..];
            }
            after_start_of.clear();
            if let Some(parent) = stack.last().cloned() {
                text_child!(text, parent.as_str(), true);
            }
            if let Some(comment) = token.comment {
                if !stack.is_empty() && !inside_opaque!() && is_dj_if_comment(comment) {
                    out.push('!');
                    if let Some(last) = counts.last_mut() {
                        *last += 1;
                    }
                }
                continue;
            }
            let tag = py_lower(token.tag);
            if token.close {
                if stack.last() != Some(&tag) {
                    return Ok(None);
                }
                if opaque_depth != 0 && stack.len() == opaque_depth {
                    opaque_depth = 0;
                }
                stack.pop();
                counts.pop();
                if !stack.is_empty() {
                    paths.pop();
                    if !inside_opaque!() {
                        out.push('>');
                    }
                }
                continue;
            }
            let attrs = token.attrs;
            let pairs = w.counted_attrs(attrs)?;
            if is_client_owned(&pairs) {
                client_owned = true;
            }
            let void =
                attrs.trim_end_matches(is_py_space).ends_with('/') || VOID.contains(&tag.as_str());
            if stack.is_empty() {
                stack.push(tag);
                counts.push(0);
                if void {
                    return Ok(None);
                }
                if is_opaque(&pairs) {
                    client_owned = true;
                }
                continue;
            }
            if inside_opaque!() {
                if !void {
                    let raw = RAW_TEXT.contains(&tag.as_str());
                    if raw {
                        match raw_text_end(html, &tag, position) {
                            Some((_, end)) => {
                                position = end;
                                continue 'restart;
                            }
                            None => return Ok(None),
                        }
                    } else {
                        stack.push(tag);
                        counts.push(0);
                        paths.push(Vec::new());
                    }
                }
                continue;
            }
            let mut path = paths.last().cloned().unwrap_or_default();
            if let Some(last) = counts.last_mut() {
                path.push(*last);
                *last += 1;
            }
            element_piece(&mut out, &tag, &pairs);
            if void {
                out.push('>');
                continue;
            }
            let opaque = is_opaque(&pairs);
            if RAW_TEXT.contains(&tag.as_str()) {
                let (start, end) = match raw_text_end(html, &tag, position) {
                    Some(found) => found,
                    None => return Ok(None),
                };
                let mut raw = &html[position..start];
                if DROPS_FIRST_NEWLINE.contains(&tag.as_str()) && raw.starts_with('\n') {
                    raw = &raw[1..];
                }
                if opaque {
                    opaque_paths.push(path.clone());
                }
                // Pushed for the text child's parent and the opaque check, popped
                // at once, as the Python walk does.
                stack.push(tag.clone());
                counts.push(0);
                paths.push(path);
                if opaque {
                    opaque_depth = stack.len();
                }
                let references = tag == "textarea" || tag == "title";
                text_child!(raw, tag.as_str(), references);
                position = end;
                if opaque_depth == stack.len() {
                    opaque_depth = 0;
                }
                stack.pop();
                counts.pop();
                paths.pop();
                out.push('>');
                continue 'restart;
            }
            stack.push(tag.clone());
            counts.push(0);
            paths.push(path.clone());
            after_start_of = tag;
            if opaque {
                opaque_paths.push(path);
                opaque_depth = stack.len();
            }
        }
        break;
    }
    if !stack.is_empty() {
        return Ok(None);
    }
    let data = out.as_bytes();
    let shape = format!("{:08x}{:08x}", crc32(data), adler32(data));
    Ok(Some((shape, opaque_paths, client_owned)))
}

/// `(shape, opaque_paths, client_owned)` of the rendered root `html`, or
/// `None` where the Python walk answers `None` (unbalanced tags). `unescape`
/// decodes character references (`html.unescape`).
#[pyfunction]
pub fn render_shape_walk(html: &str, unescape: &Bound<'_, PyAny>) -> PyResult<Option<Shape>> {
    crate::guard_panic("render_shape_walk", || walk(html, &Walker { unescape }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn checksums_match_zlib() {
        // zlib.crc32(b"hello") / zlib.adler32(b"hello")
        assert_eq!(crc32(b"hello"), 0x3610_a686);
        assert_eq!(adler32(b"hello"), 0x062c_0215);
        assert_eq!(crc32(b""), 0);
        assert_eq!(adler32(b""), 1);
    }

    #[test]
    fn shape_text_collapses_ascii_runs() {
        assert_eq!(shape_text("  a \t\n b  "), "a b");
        assert_eq!(shape_text("a\u{a0}b"), "a\u{a0}b");
    }
}
