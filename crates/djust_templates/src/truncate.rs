//! A port of `django.utils.text.Truncator` and the `html.parser.HTMLParser`
//! that drives its HTML variants (#2262).
//!
//! # Why a port rather than an implementation
//!
//! The four truncation filters were written against Django's *documentation*
//! ("truncate after N characters, preserving tags") rather than against its
//! *behaviour*, so each was correct-looking and wrong in a detail the docs do
//! not mention. `truncatechars_html` cut at `len == limit` where Django cuts at
//! `len > limit`; it measured the escaped form of its input rather than the
//! text; `truncatewords_html` escaped once where Django escapes inside the
//! parser *and* again at render. None of those are expressible as a tweak —
//! they fall out of the shape of the reference algorithm, which is:
//!
//! * an [`HTMLParser`] subclass that emits start tags verbatim, re-closes what
//!   is still open when it stops, and **escapes every run of text it keeps**;
//! * a `remaining` budget denominated in *parsed* units (characters for
//!   `chars`, whitespace-separated runs for `words`) rather than output bytes;
//! * and, for `chars` only, a special case that returns the input **unescaped
//!   and whole** when the entire input is exactly `length` characters of text.
//!
//! That last one is the whole of divergence #1 and #2 in the issue, and no
//! amount of off-by-one adjustment reaches it.
//!
//! # Fidelity
//!
//! `handle_comment` / `handle_decl` / `handle_pi` / `unknown_decl` are *not*
//! overridden by Django's parser, so the base class's no-op runs and comments,
//! doctypes and processing instructions contribute **nothing** to the output.
//! That is not an oversight here: `truncatechars_html` genuinely deletes an
//! HTML comment from its input, and the differential in
//! `python/tests/test_truncate_slugify_parity_2262.py` pins it.
//!
//! Character references are converted on the way in (`convert_charrefs=True`)
//! and re-escaped on the way out, so `&amp;` survives a round trip while a bare
//! `&` becomes `&amp;`. `html::unescape` is ported against
//! `markup5ever::data::NAMED_ENTITIES`, which is byte-identical to CPython's
//! `html.entities.html5` (2231 entries, verified equal).
//!
//! # The one thing this does NOT port
//!
//! `Truncator.chars` opens with `unicodedata.normalize("NFC", text)` and skips
//! characters whose canonical combining class is non-zero. Both need Unicode
//! normalization tables that are not in this workspace's dependency graph, so
//! neither is implemented; the residual divergence is confined to inputs that
//! carry combining marks and is pinned, with its measurement, in
//! `TestKnownRemainingDivergences`.

use std::collections::VecDeque;

use crate::filters::html_escape;

/// Django's default truncation text: `pgettext(..., "%(truncated_text)s…")`.
const DEFAULT_TRUNCATE: &str = "%(truncated_text)s…";

/// `django.utils.html.VOID_ELEMENTS` — the WHATWG list plus the two deprecated
/// tags Django keeps. `frame` and `spacer` are the two the previous ad-hoc list
/// was missing.
pub fn is_void_element(tag: &str) -> bool {
    matches!(
        tag,
        "area"
            | "base"
            | "br"
            | "col"
            | "embed"
            | "hr"
            | "img"
            | "input"
            | "link"
            | "meta"
            | "param"
            | "source"
            | "track"
            | "wbr"
            | "frame"
            | "spacer"
    )
}

/// Python's `str.isspace()` / `\s` for `str` patterns.
///
/// `char::is_whitespace` is the Unicode White_Space property, which omits the
/// four ASCII file/group/record/unit separators Python treats as space.
pub(crate) fn py_is_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// Python's `\w` for `str` patterns: alphanumeric or underscore.
fn py_is_word(c: char) -> bool {
    c.is_alphanumeric() || c == '_'
}

/// `str.split()` with no argument: split on runs of whitespace, no empties.
pub(crate) fn py_split_whitespace(s: &str) -> Vec<&str> {
    s.split(py_is_space).filter(|p| !p.is_empty()).collect()
}

/// `django.utils.text.add_truncation_text`.
pub fn add_truncation_text(text: &str, truncate: Option<&str>) -> String {
    let truncate = truncate.unwrap_or(DEFAULT_TRUNCATE);
    if truncate.contains("%(truncated_text)s") {
        return truncate.replace("%(truncated_text)s", text);
    }
    if text.ends_with(truncate) {
        return text.to_string();
    }
    format!("{text}{truncate}")
}

/// `django.utils.text.calculate_truncate_chars_length`.
///
/// The combining-character skip of the reference is absent (see the module
/// docstring); every character of the truncation text costs one.
pub fn calculate_truncate_chars_length(length: i64, replacement: Option<&str>) -> i64 {
    let mut truncate_len = length;
    for _ in add_truncation_text("", replacement).chars() {
        truncate_len -= 1;
        if truncate_len <= 0 {
            break;
        }
    }
    truncate_len
}

// ---------------------------------------------------------------------------
// html.unescape
// ---------------------------------------------------------------------------

/// CPython `html._invalid_charrefs` — numeric references the HTML5 spec
/// remaps (the windows-1252 C1 block, plus NUL and CR).
const INVALID_CHARREFS: [(u32, &str); 34] = [
    (0x00, "\u{fffd}"),
    (0x0d, "\r"),
    (0x80, "\u{20ac}"),
    (0x81, "\u{81}"),
    (0x82, "\u{201a}"),
    (0x83, "\u{192}"),
    (0x84, "\u{201e}"),
    (0x85, "\u{2026}"),
    (0x86, "\u{2020}"),
    (0x87, "\u{2021}"),
    (0x88, "\u{2c6}"),
    (0x89, "\u{2030}"),
    (0x8a, "\u{160}"),
    (0x8b, "\u{2039}"),
    (0x8c, "\u{152}"),
    (0x8d, "\u{8d}"),
    (0x8e, "\u{17d}"),
    (0x8f, "\u{8f}"),
    (0x90, "\u{90}"),
    (0x91, "\u{2018}"),
    (0x92, "\u{2019}"),
    (0x93, "\u{201c}"),
    (0x94, "\u{201d}"),
    (0x95, "\u{2022}"),
    (0x96, "\u{2013}"),
    (0x97, "\u{2014}"),
    (0x98, "\u{2dc}"),
    (0x99, "\u{2122}"),
    (0x9a, "\u{161}"),
    (0x9b, "\u{203a}"),
    (0x9c, "\u{153}"),
    (0x9d, "\u{9d}"),
    (0x9e, "\u{17e}"),
    (0x9f, "\u{178}"),
];

/// CPython `html._invalid_codepoints` — numeric references that resolve to
/// nothing at all.
const INVALID_CODEPOINTS: [u32; 126] = [
    0x1, 0x2, 0x3, 0x4, 0x5, 0x6, 0x7, 0x8, 0xb, 0xe, 0xf, 0x10, 0x11, 0x12, 0x13, 0x14, 0x15,
    0x16, 0x17, 0x18, 0x19, 0x1a, 0x1b, 0x1c, 0x1d, 0x1e, 0x1f, 0x7f, 0x80, 0x81, 0x82, 0x83, 0x84,
    0x85, 0x86, 0x87, 0x88, 0x89, 0x8a, 0x8b, 0x8c, 0x8d, 0x8e, 0x8f, 0x90, 0x91, 0x92, 0x93, 0x94,
    0x95, 0x96, 0x97, 0x98, 0x99, 0x9a, 0x9b, 0x9c, 0x9d, 0x9e, 0x9f, 0xfdd0, 0xfdd1, 0xfdd2,
    0xfdd3, 0xfdd4, 0xfdd5, 0xfdd6, 0xfdd7, 0xfdd8, 0xfdd9, 0xfdda, 0xfddb, 0xfddc, 0xfddd, 0xfdde,
    0xfddf, 0xfde0, 0xfde1, 0xfde2, 0xfde3, 0xfde4, 0xfde5, 0xfde6, 0xfde7, 0xfde8, 0xfde9, 0xfdea,
    0xfdeb, 0xfdec, 0xfded, 0xfdee, 0xfdef, 0xfffe, 0xffff, 0x1fffe, 0x1ffff, 0x2fffe, 0x2ffff,
    0x3fffe, 0x3ffff, 0x4fffe, 0x4ffff, 0x5fffe, 0x5ffff, 0x6fffe, 0x6ffff, 0x7fffe, 0x7ffff,
    0x8fffe, 0x8ffff, 0x9fffe, 0x9ffff, 0xafffe, 0xaffff, 0xbfffe, 0xbffff, 0xcfffe, 0xcffff,
    0xdfffe, 0xdffff, 0xefffe, 0xeffff, 0xffffe, 0xfffff, 0x10fffe, 0x10ffff,
];

/// Look one entity name (without the leading `&`) up in the HTML5 table.
///
/// `markup5ever`'s map also carries every *prefix* of every entity name mapped
/// to `(0, 0)` for its own incremental tokenizer; no real entity resolves to
/// U+0000, so `(0, 0)` means "not an entity" here.
fn named_entity(bare_name: &str) -> Option<String> {
    let (a, b) = *markup5ever::data::NAMED_ENTITIES.get(bare_name)?;
    if a == 0 {
        return None;
    }
    let mut s = String::new();
    s.push(char::from_u32(a)?);
    if b != 0 {
        if let Some(c) = char::from_u32(b) {
            s.push(c);
        }
    }
    Some(s)
}

fn resolve_numeric(body: &str) -> String {
    // body is the text after '&', e.g. "#65;" / "#x41" / "#65"
    let digits = body.trim_start_matches('#');
    let (radix, digits) = if let Some(rest) = digits.strip_prefix(['x', 'X']) {
        (16, rest)
    } else {
        (10, digits)
    };
    let digits = digits.trim_end_matches(';');
    let num = match u32::from_str_radix(digits, radix) {
        Ok(n) => n,
        // Python's `int()` on a >64-bit literal still succeeds and lands in the
        // `num > 0x10FFFF` branch below; an overflow here means the same thing.
        Err(_) => return "\u{fffd}".to_string(),
    };
    if let Some((_, s)) = INVALID_CHARREFS.iter().find(|(k, _)| *k == num) {
        return (*s).to_string();
    }
    if (0xd800..=0xdfff).contains(&num) || num > 0x10ffff {
        return "\u{fffd}".to_string();
    }
    if INVALID_CODEPOINTS.contains(&num) {
        return String::new();
    }
    char::from_u32(num).map(String::from).unwrap_or_default()
}

/// `html.unescape` — a port of CPython's `_charref.sub(_replace_charref, s)`.
///
/// The pattern is `&(#[0-9]+;?|#[xX][0-9a-fA-F]+;?|[^\t\n\f <&#;]{1,32};?)`,
/// matched leftmost-first, and the named branch falls back to the longest
/// prefix of the captured name that is a known entity.
pub fn unescape(s: &str) -> String {
    if !s.contains('&') {
        return s.to_string();
    }
    let bytes = s.as_bytes();
    let n = s.len();
    let mut out = String::with_capacity(n);
    let mut i = 0usize;
    while i < n {
        if bytes[i] != b'&' {
            let c = s[i..].chars().next().unwrap();
            out.push(c);
            i += c.len_utf8();
            continue;
        }
        // Try `#[0-9]+;?` and `#[xX][0-9a-fA-F]+;?`
        if i + 1 < n && bytes[i + 1] == b'#' {
            let mut j = i + 2;
            let hex = j < n && (bytes[j] == b'x' || bytes[j] == b'X');
            if hex {
                j += 1;
            }
            let digits_start = j;
            while j < n
                && (if hex {
                    bytes[j].is_ascii_hexdigit()
                } else {
                    bytes[j].is_ascii_digit()
                })
            {
                j += 1;
            }
            if j > digits_start {
                let mut end = j;
                if end < n && bytes[end] == b';' {
                    end += 1;
                }
                out.push_str(&resolve_numeric(&s[i + 1..end]));
                i = end;
                continue;
            }
        }
        // Named branch: `[^\t\n\f <&#;]{1,32};?`
        let mut j = i + 1;
        let mut taken = 0;
        while j < n && taken < 32 {
            let c = s[j..].chars().next().unwrap();
            if matches!(c, '\t' | '\n' | '\u{c}' | ' ' | '<' | '&' | '#' | ';') {
                break;
            }
            j += c.len_utf8();
            taken += 1;
        }
        if taken == 0 {
            out.push('&');
            i += 1;
            continue;
        }
        let mut end = j;
        if end < n && bytes[end] == b';' && taken < 32 {
            end += 1;
        }
        let bare = &s[i + 1..end]; // the capture group: no leading '&'
        if let Some(rep) = named_entity(bare) {
            out.push_str(&rep);
            i = end;
            continue;
        }
        // Longest matching prefix, per the standard. CPython walks
        // `range(len(s)-1, 1, -1)` over the captured name.
        let mut matched = None;
        let mut cut = bare.len();
        while cut > 2 {
            cut -= 1;
            if !bare.is_char_boundary(cut) {
                continue;
            }
            if let Some(rep) = named_entity(&bare[..cut]) {
                matched = Some(format!("{}{}", rep, &bare[cut..]));
                break;
            }
        }
        match matched {
            Some(rep) => out.push_str(&rep),
            None => {
                out.push('&');
                out.push_str(bare);
            }
        }
        i = end;
    }
    out
}

// ---------------------------------------------------------------------------
// Plain-text truncation
// ---------------------------------------------------------------------------

/// `Truncator._text_chars`, minus the NFC normalization and combining skip.
pub fn text_chars(text: &str, length: i64, truncate: Option<&str>) -> String {
    if length <= 0 {
        return String::new();
    }
    let truncate_len = calculate_truncate_chars_length(length, truncate);
    let mut s_len: i64 = 0;
    let mut end_index: Option<usize> = None;
    for (i, _) in text.char_indices() {
        s_len += 1;
        if end_index.is_none() && s_len > truncate_len {
            end_index = Some(i);
        }
        if s_len > length {
            return add_truncation_text(&text[..end_index.unwrap_or(0)], truncate);
        }
    }
    text.to_string()
}

/// `Truncator._text_words`.
///
/// The `" ".join(...)` is why `truncatewords` strips surrounding whitespace —
/// divergence #4 of the issue is a consequence of the join, not a separate
/// strip step.
pub fn text_words(text: &str, length: i64, truncate: Option<&str>) -> String {
    if length <= 0 {
        return String::new();
    }
    let words = py_split_whitespace(text);
    if words.len() as i64 > length {
        let kept = words[..length as usize].join(" ");
        return add_truncation_text(&kept, truncate);
    }
    words.join(" ")
}

// ---------------------------------------------------------------------------
// The HTML parser
// ---------------------------------------------------------------------------

/// Stand-in for `TruncateHTMLParser.TruncationCompleted`.
struct Truncated;

type PResult = Result<(), Truncated>;

enum Mode {
    Chars { length: i64, processed_chars: i64 },
    Words,
}

struct Parser<'a> {
    raw: &'a str,
    raw_chars: usize,
    cdata_elem: Option<&'static str>,
    tags: VecDeque<String>,
    output: String,
    output_chars: usize,
    remaining: i64,
    replacement: Option<&'a str>,
    mode: Mode,
}

fn char_at(s: &str, i: usize) -> Option<char> {
    if i >= s.len() {
        return None;
    }
    s[i..].chars().next()
}

fn prev_char(s: &str, i: usize) -> Option<char> {
    if i == 0 {
        return None;
    }
    s[..i].chars().next_back()
}

/// `(?:\s|/(?!>))*`
fn skip_ws_or_slash(s: &str, mut i: usize) -> usize {
    let b = s.as_bytes();
    let n = s.len();
    while i < n {
        let c = char_at(s, i).unwrap();
        if py_is_space(c) {
            i += c.len_utf8();
        } else if c == '/' && !(i + 1 < n && b[i + 1] == b'>') {
            i += 1;
        } else {
            break;
        }
    }
    i
}

fn skip_ws(s: &str, mut i: usize) -> usize {
    while i < s.len() {
        let c = char_at(s, i).unwrap();
        if py_is_space(c) {
            i += c.len_utf8();
        } else {
            break;
        }
    }
    i
}

/// `attrfind_tolerant`, hand-rolled because it uses a lookbehind and a
/// lookahead that the `regex` crate cannot express.
///
/// `((?<=['"\s/])[^\s/>][^\s/=>]*)(\s*=+\s*('[^']*'|"[^"]*"|(?!['"])[^>\s]*))?(?:\s|/(?!>))*`
fn match_attr(s: &str, k: usize) -> Option<usize> {
    let b = s.as_bytes();
    let n = s.len();
    let prev = prev_char(s, k)?;
    if !(prev == '\'' || prev == '"' || prev == '/' || py_is_space(prev)) {
        return None;
    }
    let c = char_at(s, k)?;
    if py_is_space(c) || c == '/' || c == '>' {
        return None;
    }
    let mut q = k + c.len_utf8();
    while q < n {
        let c = char_at(s, q).unwrap();
        if py_is_space(c) || c == '/' || c == '=' || c == '>' {
            break;
        }
        q += c.len_utf8();
    }
    // (\s*=+\s*(value))?  — the whole group backtracks to empty if the value
    // alternation fails (an unterminated quoted value is the case that matters).
    let save = q;
    let mut r = skip_ws(s, q);
    let mut matched_value = false;
    if r < n && b[r] == b'=' {
        while r < n && b[r] == b'=' {
            r += 1;
        }
        r = skip_ws(s, r);
        if r < n && (b[r] == b'\'' || b[r] == b'"') {
            let quote = b[r] as char;
            if let Some(off) = s[r + 1..].find(quote) {
                r = r + 1 + off + 1;
                matched_value = true;
            }
        } else {
            while r < n {
                let c = char_at(s, r).unwrap();
                if c == '>' || py_is_space(c) {
                    break;
                }
                r += c.len_utf8();
            }
            matched_value = true;
        }
        if matched_value {
            r = skip_ws(s, r);
        }
    }
    let q = if matched_value { r } else { save };
    Some(skip_ws_or_slash(s, q))
}

/// `locatestarttagend_tolerant.match(rawdata, i)`; returns `m.end()`.
fn locate_starttag_end(s: &str, i: usize) -> Option<usize> {
    let b = s.as_bytes();
    let n = s.len();
    if i + 1 >= n || !(b[i + 1] as char).is_ascii_alphabetic() {
        return None;
    }
    let mut p = i + 2;
    while p < n {
        let c = char_at(s, p).unwrap();
        if matches!(c, '\t' | '\n' | '\r' | '\u{c}' | ' ' | '/' | '>' | '\0') {
            break;
        }
        p += c.len_utf8();
    }
    // (?:[\s/]* (attr)*)?
    let mut q = p;
    while q < n {
        let c = char_at(s, q).unwrap();
        if py_is_space(c) || c == '/' {
            q += c.len_utf8();
        } else {
            break;
        }
    }
    while let Some(e) = match_attr(s, q) {
        if e <= q {
            break;
        }
        q = e;
    }
    Some(skip_ws(s, q))
}

/// `tagfind_tolerant.match(rawdata, pos)` → `(lowercased name, m.end())`.
fn tagfind(s: &str, pos: usize) -> Option<(String, usize)> {
    let b = s.as_bytes();
    let n = s.len();
    if pos >= n || !(b[pos] as char).is_ascii_alphabetic() {
        return None;
    }
    let mut p = pos + 1;
    while p < n {
        let c = char_at(s, p).unwrap();
        if matches!(c, '\t' | '\n' | '\r' | '\u{c}' | ' ' | '/' | '>' | '\0') {
            break;
        }
        p += c.len_utf8();
    }
    let name = s[pos..p].to_lowercase();
    Some((name, skip_ws_or_slash(s, p)))
}

/// `endtagfind.match(rawdata, i)`: `</\s*([a-zA-Z][-.a-zA-Z0-9:_]*)\s*>`.
fn endtagfind(s: &str, i: usize) -> Option<String> {
    let b = s.as_bytes();
    let n = s.len();
    if !s[i..].starts_with("</") {
        return None;
    }
    let mut p = skip_ws(s, i + 2);
    if p >= n || !(b[p] as char).is_ascii_alphabetic() {
        return None;
    }
    let start = p;
    p += 1;
    while p < n {
        let c = b[p] as char;
        if c.is_ascii_alphanumeric() || matches!(c, '-' | '.' | ':' | '_') {
            p += 1;
        } else {
            break;
        }
    }
    let name = s[start..p].to_lowercase();
    let p = skip_ws(s, p);
    if p < n && b[p] == b'>' {
        Some(name)
    } else {
        None
    }
}

/// `_markupbase._declname_match`: `[a-zA-Z][-_.a-zA-Z0-9]*\s*` → `(name, end)`.
fn declname(s: &str, i: usize) -> Option<(String, usize)> {
    let b = s.as_bytes();
    let n = s.len();
    if i >= n || !(b[i] as char).is_ascii_alphabetic() {
        return None;
    }
    let mut p = i + 1;
    while p < n {
        let c = b[p] as char;
        if c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.') {
            p += 1;
        } else {
            break;
        }
    }
    let name = s[i..p].to_lowercase();
    Some((name, skip_ws(s, p)))
}

/// Leftmost match of `--\s*>` at or after `from`; returns `m.end()`.
fn comment_close(s: &str, from: usize) -> Option<usize> {
    let b = s.as_bytes();
    let n = s.len();
    let mut p = from;
    while let Some(off) = s[p..].find("--") {
        let start = p + off;
        let e = skip_ws(s, start + 2);
        if e < n && b[e] == b'>' {
            return Some(e + 1);
        }
        p = start + 1;
    }
    None
}

/// Leftmost match of `]\s*]\s*>` (or `]\s*>` for the MS variant) → `(start, end)`.
fn marked_section_close(s: &str, from: usize, double: bool) -> Option<(usize, usize)> {
    let b = s.as_bytes();
    let n = s.len();
    let mut p = from;
    while let Some(off) = s[p..].find(']') {
        let start = p + off;
        let mut e = skip_ws(s, start + 1);
        if double {
            if e < n && b[e] == b']' {
                e = skip_ws(s, e + 1);
            } else {
                p = start + 1;
                continue;
            }
        }
        if e < n && b[e] == b'>' {
            return Some((start, e + 1));
        }
        p = start + 1;
    }
    None
}

impl<'a> Parser<'a> {
    fn new(raw: &'a str, length: i64, replacement: Option<&'a str>, words: bool) -> Self {
        let (mode, remaining) = if words {
            (Mode::Words, length)
        } else {
            (
                Mode::Chars {
                    length,
                    processed_chars: 0,
                },
                calculate_truncate_chars_length(length, replacement),
            )
        };
        Parser {
            raw,
            raw_chars: raw.chars().count(),
            cdata_elem: None,
            tags: VecDeque::new(),
            output: String::new(),
            output_chars: 0,
            remaining,
            replacement,
            mode,
        }
    }

    fn push_out(&mut self, s: &str) {
        self.output.push_str(s);
        self.output_chars += s.chars().count();
    }

    // --- handlers ---------------------------------------------------------

    fn handle_starttag(&mut self, tag: &str, starttag_text: &str) {
        self.push_out(starttag_text);
        if !is_void_element(tag) {
            self.tags.push_front(tag.to_string());
        }
    }

    fn handle_endtag(&mut self, tag: &str) {
        if !is_void_element(tag) {
            self.push_out(&format!("</{tag}>"));
            if self.tags.front().map(|t| t == tag).unwrap_or(false) {
                self.tags.pop_front();
            }
        }
    }

    fn handle_startendtag(&mut self, tag: &str, starttag_text: &str) {
        self.handle_starttag(tag, starttag_text);
        if !is_void_element(tag) {
            self.handle_endtag(tag);
        }
    }

    fn handle_data(&mut self, data: &str) -> PResult {
        let (data_len, out) = match self.mode {
            Mode::Chars { .. } => self.process_chars(data)?,
            Mode::Words => self.process_words(data),
        };
        if self.remaining < data_len {
            self.remaining = 0;
            let t = add_truncation_text(&out, self.replacement);
            self.push_out(&t);
            return Err(Truncated);
        }
        self.remaining -= data_len;
        self.push_out(&out);
        Ok(())
    }

    /// `TruncateCharsHTMLParser.process`.
    fn process_chars(&mut self, data: &str) -> Result<(i64, String), Truncated> {
        let dlen = data.chars().count() as i64;
        let special = if let Mode::Chars {
            length,
            processed_chars,
        } = &mut self.mode
        {
            *processed_chars += dlen;
            *processed_chars == *length
        } else {
            false
        };
        // `len(self.output) + len(data) == len(self.rawdata)`: the whole input
        // is one run of text of exactly `length` characters. Django emits it
        // RAW — no escape, no truncation text — which is divergences #1 and #2.
        if special && self.output_chars as i64 + dlen == self.raw_chars as i64 {
            self.push_out(data);
            return Err(Truncated);
        }
        let take = self.remaining.max(0) as usize;
        let kept: String = data.chars().take(take).collect();
        Ok((dlen, html_escape(&kept)))
    }

    /// `TruncateWordsHTMLParser.process`: `re.split(r"(?<=\S)\s+(?=\S)", data)`.
    fn process_words(&mut self, data: &str) -> (i64, String) {
        let parts = split_interior_whitespace(data);
        let take = self.remaining.max(0) as usize;
        let joined = parts
            .iter()
            .take(take)
            .copied()
            .collect::<Vec<_>>()
            .join(" ");
        (parts.len() as i64, html_escape(&joined))
    }

    // --- the tokenizer ----------------------------------------------------

    /// `HTMLParser.goahead(0)`.
    ///
    /// `end` is always 0: Django's `feed` override calls `reset()` before
    /// `close()` can run, so an incomplete construct at the end of the input is
    /// discarded rather than flushed. `"trailing &amp"` really does render as
    /// the empty string.
    fn goahead(&mut self) -> PResult {
        let raw = self.raw;
        let b = raw.as_bytes();
        let n = raw.len();
        let mut i = 0usize;
        while i < n {
            let j;
            if self.cdata_elem.is_none() {
                match raw[i..].find('<') {
                    Some(off) => j = i + off,
                    None => {
                        // `amppos = rawdata.rfind('&', max(i, n-34))`
                        let back = raw
                            .char_indices()
                            .rev()
                            .take(34)
                            .last()
                            .map(|(idx, _)| idx)
                            .unwrap_or(0);
                        let start = back.max(i);
                        match raw[start..].rfind('&') {
                            Some(off) => {
                                let amppos = start + off;
                                if !raw[amppos..].contains(|c: char| py_is_space(c) || c == ';') {
                                    break; // wait for the rest of the text
                                }
                                j = n;
                            }
                            None => j = n,
                        }
                    }
                }
            } else if let Some(elem) = self.cdata_elem {
                // In CDATA mode `interesting` is `</\s*{elem}\s*>`, so the only
                // thing that ends a text run is that element's own end tag.
                match find_cdata_close(raw, i, elem) {
                    Some(pos) => j = pos,
                    None => break,
                }
            } else {
                unreachable!("the `is_none` arm above covers this case")
            }
            if i < j {
                let data = if self.cdata_elem.is_none() {
                    unescape(&raw[i..j])
                } else {
                    raw[i..j].to_string()
                };
                self.handle_data(&data)?;
            }
            i = j;
            if i == n {
                break;
            }
            debug_assert_eq!(b[i], b'<');
            let k: i64 = if i + 1 < n && (b[i + 1] as char).is_ascii_alphabetic() {
                self.parse_starttag(i)?
            } else if raw[i..].starts_with("</") {
                self.parse_endtag(i)?
            } else if raw[i..].starts_with("<!--") {
                comment_close(raw, i + 4).map(|e| e as i64).unwrap_or(-1)
            } else if raw[i..].starts_with("<?") {
                raw[i + 2..]
                    .find('>')
                    .map(|o| (i + 2 + o + 1) as i64)
                    .unwrap_or(-1)
            } else if raw[i..].starts_with("<!") {
                parse_html_declaration(raw, i)
            } else if i + 1 < n {
                self.handle_data("<")?;
                (i + 1) as i64
            } else {
                break;
            };
            if k < 0 {
                break; // `end` is 0, so an incomplete construct stops the parse
            }
            i = k as usize;
        }
        Ok(())
    }

    fn parse_starttag(&mut self, i: usize) -> Result<i64, Truncated> {
        let raw = self.raw;
        let endpos = match check_for_whole_start_tag(raw, i) {
            e if e < 0 => return Ok(e),
            e => e as usize,
        };
        let starttag_text = raw[i..endpos].to_string();
        let (tag, mut k) = match tagfind(raw, i + 1) {
            Some(t) => t,
            None => return Ok(endpos as i64),
        };
        while k < endpos {
            match match_attr(raw, k) {
                Some(e) if e > k => k = e,
                _ => break,
            }
        }
        let end: &str = raw[k..endpos].trim_matches(py_is_space);
        if end != ">" && end != "/>" {
            self.handle_data(&raw[i..endpos])?;
            return Ok(endpos as i64);
        }
        if end.ends_with("/>") {
            self.handle_startendtag(&tag, &starttag_text);
        } else {
            self.handle_starttag(&tag, &starttag_text);
            if tag == "script" {
                self.cdata_elem = Some("script");
            } else if tag == "style" {
                self.cdata_elem = Some("style");
            }
        }
        Ok(endpos as i64)
    }

    fn parse_endtag(&mut self, i: usize) -> Result<i64, Truncated> {
        let raw = self.raw;
        let gt = match raw[i + 1..].find('>') {
            Some(o) => i + 1 + o,
            None => return Ok(-1),
        };
        let gtpos = gt + 1;
        if let Some(elem) = endtagfind(raw, i) {
            if let Some(cd) = self.cdata_elem {
                if elem != cd {
                    self.handle_data(&raw[i..gtpos])?;
                    return Ok(gtpos as i64);
                }
            }
            self.handle_endtag(&elem);
            self.cdata_elem = None;
            return Ok(gtpos as i64);
        }
        if self.cdata_elem.is_some() {
            self.handle_data(&raw[i..gtpos])?;
            return Ok(gtpos as i64);
        }
        match tagfind(raw, i + 2) {
            None => {
                if raw[i..].starts_with("</>") {
                    Ok((i + 3) as i64)
                } else {
                    // parse_bogus_comment: handle_comment is a no-op.
                    Ok(raw[i + 2..]
                        .find('>')
                        .map(|o| (i + 2 + o + 1) as i64)
                        .unwrap_or(-1))
                }
            }
            Some((tagname, nend)) => {
                let gp = raw[nend..].find('>').map(|o| nend + o);
                self.handle_endtag(&tagname);
                // Django would return 0 here and spin; there is always a `>` at
                // or after `nend` because `endendtag` found one and `tagfind`
                // cannot consume it, so the fallback is unreachable in practice.
                Ok(gp.map(|g| (g + 1) as i64).unwrap_or(-1))
            }
        }
    }

    /// `TruncateHTMLParser.feed`'s `except` branch.
    fn finish_truncated(&mut self) {
        let closing: String = self.tags.iter().map(|t| format!("</{t}>")).collect();
        self.output.push_str(&closing);
        self.tags.clear();
    }
}

fn check_for_whole_start_tag(s: &str, i: usize) -> i64 {
    let j = match locate_starttag_end(s, i) {
        Some(j) => j,
        None => return (i + 1) as i64,
    };
    let next = char_at(s, j);
    match next {
        Some('>') => (j + 1) as i64,
        Some('/') => {
            if s[j..].starts_with("/>") {
                (j + 2) as i64
            } else {
                -1
            }
        }
        None => -1,
        Some(c) if c.is_ascii_alphabetic() || c == '=' => -1,
        Some(_) => {
            if j > i {
                j as i64
            } else {
                (i + 1) as i64
            }
        }
    }
}

fn parse_html_declaration(s: &str, i: usize) -> i64 {
    let n = s.len();
    if s[i..].starts_with("<!--") {
        return comment_close(s, i + 4).map(|e| e as i64).unwrap_or(-1);
    }
    if s[i..].starts_with("<![") {
        // parse_marked_section
        if i + 3 >= n {
            return -1;
        }
        let (name, end) = match declname(s, i + 3) {
            Some(v) => v,
            // Django raises AssertionError here; refusing to parse is the
            // fail-soft equivalent (the alternative is a 500 on a template).
            None => return -1,
        };
        if end == n {
            return -1;
        }
        let double = match name.as_str() {
            "temp" | "cdata" | "ignore" | "include" | "rcdata" => true,
            "if" | "else" | "endif" => false,
            _ => return -1,
        };
        return marked_section_close(s, i + 3, double)
            .map(|(_, e)| e as i64)
            .unwrap_or(-1);
    }
    if n >= i + 9 && s[i..i + 9].eq_ignore_ascii_case("<!doctype") {
        return s[i + 9..]
            .find('>')
            .map(|o| (i + 9 + o + 1) as i64)
            .unwrap_or(-1);
    }
    // parse_bogus_comment
    s[i + 2..]
        .find('>')
        .map(|o| (i + 2 + o + 1) as i64)
        .unwrap_or(-1)
}

/// `re.compile(r'</\s*%s\s*>' % cdata_elem, re.I).search(rawdata, i)` → start.
fn find_cdata_close(s: &str, from: usize, elem: &str) -> Option<usize> {
    let b = s.as_bytes();
    let n = s.len();
    let mut p = from;
    while let Some(off) = s[p..].find("</") {
        let start = p + off;
        let q = skip_ws(s, start + 2);
        if q + elem.len() <= n && s[q..q + elem.len()].eq_ignore_ascii_case(elem) {
            let r = skip_ws(s, q + elem.len());
            if r < n && b[r] == b'>' {
                return Some(start);
            }
        }
        p = start + 1;
    }
    None
}

/// `re.split(r"(?<=\S)\s+(?=\S)", data)` — split only on whitespace runs that
/// have a non-space on both sides, so leading and trailing padding stays glued
/// to the first and last part.
fn split_interior_whitespace(data: &str) -> Vec<&str> {
    let mut parts = Vec::new();
    let mut start = 0usize;
    let mut i = 0usize;
    let n = data.len();
    while i < n {
        let c = char_at(data, i).unwrap();
        if !py_is_space(c) {
            i += c.len_utf8();
            continue;
        }
        let run_start = i;
        while i < n {
            let c = char_at(data, i).unwrap();
            if py_is_space(c) {
                i += c.len_utf8();
            } else {
                break;
            }
        }
        let has_left = run_start > 0;
        let has_right = i < n;
        if has_left && has_right {
            parts.push(&data[start..run_start]);
            start = i;
        }
    }
    parts.push(&data[start..]);
    parts
}

fn run_html(text: &str, length: i64, truncate: Option<&str>, words: bool) -> String {
    if length <= 0 {
        return String::new();
    }
    let mut parser = Parser::new(text, length, truncate, words);
    if parser.goahead().is_err() {
        parser.finish_truncated();
    }
    parser.output
}

/// `Truncator.chars(num, truncate, html=True)`.
pub fn html_chars(text: &str, length: i64, truncate: Option<&str>) -> String {
    run_html(text, length, truncate, false)
}

/// `Truncator.words(num, truncate, html=True)`.
pub fn html_words(text: &str, length: i64, truncate: Option<&str>) -> String {
    run_html(text, length, truncate, true)
}

// ---------------------------------------------------------------------------
// slugify / title / urlencode
// ---------------------------------------------------------------------------

/// `django.utils.text.slugify(value, allow_unicode=False)` **minus** the
/// leading `unicodedata.normalize("NFKD", …).encode("ascii", "ignore")`.
///
/// The ASCII fold is the one step that needs Unicode decomposition tables (it
/// is what turns `café` into `cafe` rather than dropping the `é` outright), so
/// it is deliberately absent and its residual divergence is measured in the
/// parity suite. Everything after it — delete anything outside `[\w\s-]`,
/// collapse runs of `[-\s]` to one `-`, strip `-_` from both ends — is exact,
/// and that is the half the issue reports: a `.` is *deleted*, never mapped to
/// a separator.
pub fn slugify(value: &str) -> String {
    let lowered = value.to_lowercase();
    let mut out = String::with_capacity(lowered.len());
    let mut prev_sep = false;
    for c in lowered.chars() {
        if c == '-' || py_is_space(c) {
            if !prev_sep {
                out.push('-');
                prev_sep = true;
            }
        } else if py_is_word(c) {
            out.push(c);
            prev_sep = false;
        }
        // Anything else is deleted, and deleting it does NOT break a run:
        // `re.sub` removes it before the collapse pass ever sees the string.
    }
    out.trim_matches(|c| c == '-' || c == '_').to_string()
}

/// The 135 codepoints where Python's titlecase mapping
/// (`_PyUnicode_ToTitleFull`) differs from `char::to_uppercase`.
const TITLE_EXCEPTIONS: [(char, &str); 135] = [
    ('\u{df}', "\u{53}\u{73}"),
    ('\u{1c4}', "\u{1c5}"),
    ('\u{1c5}', "\u{1c5}"),
    ('\u{1c6}', "\u{1c5}"),
    ('\u{1c7}', "\u{1c8}"),
    ('\u{1c8}', "\u{1c8}"),
    ('\u{1c9}', "\u{1c8}"),
    ('\u{1ca}', "\u{1cb}"),
    ('\u{1cb}', "\u{1cb}"),
    ('\u{1cc}', "\u{1cb}"),
    ('\u{1f1}', "\u{1f2}"),
    ('\u{1f2}', "\u{1f2}"),
    ('\u{1f3}', "\u{1f2}"),
    ('\u{587}', "\u{535}\u{582}"),
    ('\u{10d0}', "\u{10d0}"),
    ('\u{10d1}', "\u{10d1}"),
    ('\u{10d2}', "\u{10d2}"),
    ('\u{10d3}', "\u{10d3}"),
    ('\u{10d4}', "\u{10d4}"),
    ('\u{10d5}', "\u{10d5}"),
    ('\u{10d6}', "\u{10d6}"),
    ('\u{10d7}', "\u{10d7}"),
    ('\u{10d8}', "\u{10d8}"),
    ('\u{10d9}', "\u{10d9}"),
    ('\u{10da}', "\u{10da}"),
    ('\u{10db}', "\u{10db}"),
    ('\u{10dc}', "\u{10dc}"),
    ('\u{10dd}', "\u{10dd}"),
    ('\u{10de}', "\u{10de}"),
    ('\u{10df}', "\u{10df}"),
    ('\u{10e0}', "\u{10e0}"),
    ('\u{10e1}', "\u{10e1}"),
    ('\u{10e2}', "\u{10e2}"),
    ('\u{10e3}', "\u{10e3}"),
    ('\u{10e4}', "\u{10e4}"),
    ('\u{10e5}', "\u{10e5}"),
    ('\u{10e6}', "\u{10e6}"),
    ('\u{10e7}', "\u{10e7}"),
    ('\u{10e8}', "\u{10e8}"),
    ('\u{10e9}', "\u{10e9}"),
    ('\u{10ea}', "\u{10ea}"),
    ('\u{10eb}', "\u{10eb}"),
    ('\u{10ec}', "\u{10ec}"),
    ('\u{10ed}', "\u{10ed}"),
    ('\u{10ee}', "\u{10ee}"),
    ('\u{10ef}', "\u{10ef}"),
    ('\u{10f0}', "\u{10f0}"),
    ('\u{10f1}', "\u{10f1}"),
    ('\u{10f2}', "\u{10f2}"),
    ('\u{10f3}', "\u{10f3}"),
    ('\u{10f4}', "\u{10f4}"),
    ('\u{10f5}', "\u{10f5}"),
    ('\u{10f6}', "\u{10f6}"),
    ('\u{10f7}', "\u{10f7}"),
    ('\u{10f8}', "\u{10f8}"),
    ('\u{10f9}', "\u{10f9}"),
    ('\u{10fa}', "\u{10fa}"),
    ('\u{10fd}', "\u{10fd}"),
    ('\u{10fe}', "\u{10fe}"),
    ('\u{10ff}', "\u{10ff}"),
    ('\u{1f80}', "\u{1f88}"),
    ('\u{1f81}', "\u{1f89}"),
    ('\u{1f82}', "\u{1f8a}"),
    ('\u{1f83}', "\u{1f8b}"),
    ('\u{1f84}', "\u{1f8c}"),
    ('\u{1f85}', "\u{1f8d}"),
    ('\u{1f86}', "\u{1f8e}"),
    ('\u{1f87}', "\u{1f8f}"),
    ('\u{1f88}', "\u{1f88}"),
    ('\u{1f89}', "\u{1f89}"),
    ('\u{1f8a}', "\u{1f8a}"),
    ('\u{1f8b}', "\u{1f8b}"),
    ('\u{1f8c}', "\u{1f8c}"),
    ('\u{1f8d}', "\u{1f8d}"),
    ('\u{1f8e}', "\u{1f8e}"),
    ('\u{1f8f}', "\u{1f8f}"),
    ('\u{1f90}', "\u{1f98}"),
    ('\u{1f91}', "\u{1f99}"),
    ('\u{1f92}', "\u{1f9a}"),
    ('\u{1f93}', "\u{1f9b}"),
    ('\u{1f94}', "\u{1f9c}"),
    ('\u{1f95}', "\u{1f9d}"),
    ('\u{1f96}', "\u{1f9e}"),
    ('\u{1f97}', "\u{1f9f}"),
    ('\u{1f98}', "\u{1f98}"),
    ('\u{1f99}', "\u{1f99}"),
    ('\u{1f9a}', "\u{1f9a}"),
    ('\u{1f9b}', "\u{1f9b}"),
    ('\u{1f9c}', "\u{1f9c}"),
    ('\u{1f9d}', "\u{1f9d}"),
    ('\u{1f9e}', "\u{1f9e}"),
    ('\u{1f9f}', "\u{1f9f}"),
    ('\u{1fa0}', "\u{1fa8}"),
    ('\u{1fa1}', "\u{1fa9}"),
    ('\u{1fa2}', "\u{1faa}"),
    ('\u{1fa3}', "\u{1fab}"),
    ('\u{1fa4}', "\u{1fac}"),
    ('\u{1fa5}', "\u{1fad}"),
    ('\u{1fa6}', "\u{1fae}"),
    ('\u{1fa7}', "\u{1faf}"),
    ('\u{1fa8}', "\u{1fa8}"),
    ('\u{1fa9}', "\u{1fa9}"),
    ('\u{1faa}', "\u{1faa}"),
    ('\u{1fab}', "\u{1fab}"),
    ('\u{1fac}', "\u{1fac}"),
    ('\u{1fad}', "\u{1fad}"),
    ('\u{1fae}', "\u{1fae}"),
    ('\u{1faf}', "\u{1faf}"),
    ('\u{1fb2}', "\u{1fba}\u{345}"),
    ('\u{1fb3}', "\u{1fbc}"),
    ('\u{1fb4}', "\u{386}\u{345}"),
    ('\u{1fb7}', "\u{391}\u{342}\u{345}"),
    ('\u{1fbc}', "\u{1fbc}"),
    ('\u{1fc2}', "\u{1fca}\u{345}"),
    ('\u{1fc3}', "\u{1fcc}"),
    ('\u{1fc4}', "\u{389}\u{345}"),
    ('\u{1fc7}', "\u{397}\u{342}\u{345}"),
    ('\u{1fcc}', "\u{1fcc}"),
    ('\u{1ff2}', "\u{1ffa}\u{345}"),
    ('\u{1ff3}', "\u{1ffc}"),
    ('\u{1ff4}', "\u{38f}\u{345}"),
    ('\u{1ff7}', "\u{3a9}\u{342}\u{345}"),
    ('\u{1ffc}', "\u{1ffc}"),
    ('\u{fb00}', "\u{46}\u{66}"),
    ('\u{fb01}', "\u{46}\u{69}"),
    ('\u{fb02}', "\u{46}\u{6c}"),
    ('\u{fb03}', "\u{46}\u{66}\u{69}"),
    ('\u{fb04}', "\u{46}\u{66}\u{6c}"),
    ('\u{fb05}', "\u{53}\u{74}"),
    ('\u{fb06}', "\u{53}\u{74}"),
    ('\u{fb13}', "\u{544}\u{576}"),
    ('\u{fb14}', "\u{544}\u{565}"),
    ('\u{fb15}', "\u{544}\u{56b}"),
    ('\u{fb16}', "\u{54e}\u{576}"),
    ('\u{fb17}', "\u{544}\u{56d}"),
];

/// Unicode general category `Nd`, as sorted ranges — what Python's `\d`
/// matches for `str` patterns. `char::is_numeric()` is `N*` and so also
/// covers `No` (`²`, `½`), which made `title` lowercase the letter after
/// a superscript.
const ND_RANGES: [(char, char); 64] = [
    ('\u{30}', '\u{39}'),
    ('\u{660}', '\u{669}'),
    ('\u{6f0}', '\u{6f9}'),
    ('\u{7c0}', '\u{7c9}'),
    ('\u{966}', '\u{96f}'),
    ('\u{9e6}', '\u{9ef}'),
    ('\u{a66}', '\u{a6f}'),
    ('\u{ae6}', '\u{aef}'),
    ('\u{b66}', '\u{b6f}'),
    ('\u{be6}', '\u{bef}'),
    ('\u{c66}', '\u{c6f}'),
    ('\u{ce6}', '\u{cef}'),
    ('\u{d66}', '\u{d6f}'),
    ('\u{de6}', '\u{def}'),
    ('\u{e50}', '\u{e59}'),
    ('\u{ed0}', '\u{ed9}'),
    ('\u{f20}', '\u{f29}'),
    ('\u{1040}', '\u{1049}'),
    ('\u{1090}', '\u{1099}'),
    ('\u{17e0}', '\u{17e9}'),
    ('\u{1810}', '\u{1819}'),
    ('\u{1946}', '\u{194f}'),
    ('\u{19d0}', '\u{19d9}'),
    ('\u{1a80}', '\u{1a89}'),
    ('\u{1a90}', '\u{1a99}'),
    ('\u{1b50}', '\u{1b59}'),
    ('\u{1bb0}', '\u{1bb9}'),
    ('\u{1c40}', '\u{1c49}'),
    ('\u{1c50}', '\u{1c59}'),
    ('\u{a620}', '\u{a629}'),
    ('\u{a8d0}', '\u{a8d9}'),
    ('\u{a900}', '\u{a909}'),
    ('\u{a9d0}', '\u{a9d9}'),
    ('\u{a9f0}', '\u{a9f9}'),
    ('\u{aa50}', '\u{aa59}'),
    ('\u{abf0}', '\u{abf9}'),
    ('\u{ff10}', '\u{ff19}'),
    ('\u{104a0}', '\u{104a9}'),
    ('\u{10d30}', '\u{10d39}'),
    ('\u{11066}', '\u{1106f}'),
    ('\u{110f0}', '\u{110f9}'),
    ('\u{11136}', '\u{1113f}'),
    ('\u{111d0}', '\u{111d9}'),
    ('\u{112f0}', '\u{112f9}'),
    ('\u{11450}', '\u{11459}'),
    ('\u{114d0}', '\u{114d9}'),
    ('\u{11650}', '\u{11659}'),
    ('\u{116c0}', '\u{116c9}'),
    ('\u{11730}', '\u{11739}'),
    ('\u{118e0}', '\u{118e9}'),
    ('\u{11950}', '\u{11959}'),
    ('\u{11c50}', '\u{11c59}'),
    ('\u{11d50}', '\u{11d59}'),
    ('\u{11da0}', '\u{11da9}'),
    ('\u{11f50}', '\u{11f59}'),
    ('\u{16a60}', '\u{16a69}'),
    ('\u{16ac0}', '\u{16ac9}'),
    ('\u{16b50}', '\u{16b59}'),
    ('\u{1d7ce}', '\u{1d7ff}'),
    ('\u{1e140}', '\u{1e149}'),
    ('\u{1e2f0}', '\u{1e2f9}'),
    ('\u{1e4f0}', '\u{1e4f9}'),
    ('\u{1e950}', '\u{1e959}'),
    ('\u{1fbf0}', '\u{1fbf9}'),
];

/// Unicode `Cased` (`_PyUnicode_IsCased`), as sorted ranges.
///
/// `char::is_lowercase() || char::is_uppercase()` is NOT this set: it
/// omits Lt and includes nothing for the Other_Lowercase modifiers, and a
/// hand-rolled approximation put `²` in it, which made `title` leave the
/// following letter lowercase.
const CASED_RANGES: [(char, char); 157] = [
    ('\u{41}', '\u{5a}'),
    ('\u{61}', '\u{7a}'),
    ('\u{aa}', '\u{aa}'),
    ('\u{b5}', '\u{b5}'),
    ('\u{ba}', '\u{ba}'),
    ('\u{c0}', '\u{d6}'),
    ('\u{d8}', '\u{f6}'),
    ('\u{f8}', '\u{1ba}'),
    ('\u{1bc}', '\u{1bf}'),
    ('\u{1c4}', '\u{293}'),
    ('\u{295}', '\u{2b8}'),
    ('\u{2c0}', '\u{2c1}'),
    ('\u{2e0}', '\u{2e4}'),
    ('\u{345}', '\u{345}'),
    ('\u{370}', '\u{373}'),
    ('\u{376}', '\u{377}'),
    ('\u{37a}', '\u{37d}'),
    ('\u{37f}', '\u{37f}'),
    ('\u{386}', '\u{386}'),
    ('\u{388}', '\u{38a}'),
    ('\u{38c}', '\u{38c}'),
    ('\u{38e}', '\u{3a1}'),
    ('\u{3a3}', '\u{3f5}'),
    ('\u{3f7}', '\u{481}'),
    ('\u{48a}', '\u{52f}'),
    ('\u{531}', '\u{556}'),
    ('\u{560}', '\u{588}'),
    ('\u{10a0}', '\u{10c5}'),
    ('\u{10c7}', '\u{10c7}'),
    ('\u{10cd}', '\u{10cd}'),
    ('\u{10d0}', '\u{10fa}'),
    ('\u{10fc}', '\u{10ff}'),
    ('\u{13a0}', '\u{13f5}'),
    ('\u{13f8}', '\u{13fd}'),
    ('\u{1c80}', '\u{1c88}'),
    ('\u{1c90}', '\u{1cba}'),
    ('\u{1cbd}', '\u{1cbf}'),
    ('\u{1d00}', '\u{1dbf}'),
    ('\u{1e00}', '\u{1f15}'),
    ('\u{1f18}', '\u{1f1d}'),
    ('\u{1f20}', '\u{1f45}'),
    ('\u{1f48}', '\u{1f4d}'),
    ('\u{1f50}', '\u{1f57}'),
    ('\u{1f59}', '\u{1f59}'),
    ('\u{1f5b}', '\u{1f5b}'),
    ('\u{1f5d}', '\u{1f5d}'),
    ('\u{1f5f}', '\u{1f7d}'),
    ('\u{1f80}', '\u{1fb4}'),
    ('\u{1fb6}', '\u{1fbc}'),
    ('\u{1fbe}', '\u{1fbe}'),
    ('\u{1fc2}', '\u{1fc4}'),
    ('\u{1fc6}', '\u{1fcc}'),
    ('\u{1fd0}', '\u{1fd3}'),
    ('\u{1fd6}', '\u{1fdb}'),
    ('\u{1fe0}', '\u{1fec}'),
    ('\u{1ff2}', '\u{1ff4}'),
    ('\u{1ff6}', '\u{1ffc}'),
    ('\u{2071}', '\u{2071}'),
    ('\u{207f}', '\u{207f}'),
    ('\u{2090}', '\u{209c}'),
    ('\u{2102}', '\u{2102}'),
    ('\u{2107}', '\u{2107}'),
    ('\u{210a}', '\u{2113}'),
    ('\u{2115}', '\u{2115}'),
    ('\u{2119}', '\u{211d}'),
    ('\u{2124}', '\u{2124}'),
    ('\u{2126}', '\u{2126}'),
    ('\u{2128}', '\u{2128}'),
    ('\u{212a}', '\u{212d}'),
    ('\u{212f}', '\u{2134}'),
    ('\u{2139}', '\u{2139}'),
    ('\u{213c}', '\u{213f}'),
    ('\u{2145}', '\u{2149}'),
    ('\u{214e}', '\u{214e}'),
    ('\u{2160}', '\u{217f}'),
    ('\u{2183}', '\u{2184}'),
    ('\u{24b6}', '\u{24e9}'),
    ('\u{2c00}', '\u{2ce4}'),
    ('\u{2ceb}', '\u{2cee}'),
    ('\u{2cf2}', '\u{2cf3}'),
    ('\u{2d00}', '\u{2d25}'),
    ('\u{2d27}', '\u{2d27}'),
    ('\u{2d2d}', '\u{2d2d}'),
    ('\u{a640}', '\u{a66d}'),
    ('\u{a680}', '\u{a69d}'),
    ('\u{a722}', '\u{a787}'),
    ('\u{a78b}', '\u{a78e}'),
    ('\u{a790}', '\u{a7ca}'),
    ('\u{a7d0}', '\u{a7d1}'),
    ('\u{a7d3}', '\u{a7d3}'),
    ('\u{a7d5}', '\u{a7d9}'),
    ('\u{a7f2}', '\u{a7f6}'),
    ('\u{a7f8}', '\u{a7fa}'),
    ('\u{ab30}', '\u{ab5a}'),
    ('\u{ab5c}', '\u{ab69}'),
    ('\u{ab70}', '\u{abbf}'),
    ('\u{fb00}', '\u{fb06}'),
    ('\u{fb13}', '\u{fb17}'),
    ('\u{ff21}', '\u{ff3a}'),
    ('\u{ff41}', '\u{ff5a}'),
    ('\u{10400}', '\u{1044f}'),
    ('\u{104b0}', '\u{104d3}'),
    ('\u{104d8}', '\u{104fb}'),
    ('\u{10570}', '\u{1057a}'),
    ('\u{1057c}', '\u{1058a}'),
    ('\u{1058c}', '\u{10592}'),
    ('\u{10594}', '\u{10595}'),
    ('\u{10597}', '\u{105a1}'),
    ('\u{105a3}', '\u{105b1}'),
    ('\u{105b3}', '\u{105b9}'),
    ('\u{105bb}', '\u{105bc}'),
    ('\u{10780}', '\u{10780}'),
    ('\u{10783}', '\u{10785}'),
    ('\u{10787}', '\u{107b0}'),
    ('\u{107b2}', '\u{107ba}'),
    ('\u{10c80}', '\u{10cb2}'),
    ('\u{10cc0}', '\u{10cf2}'),
    ('\u{118a0}', '\u{118df}'),
    ('\u{16e40}', '\u{16e7f}'),
    ('\u{1d400}', '\u{1d454}'),
    ('\u{1d456}', '\u{1d49c}'),
    ('\u{1d49e}', '\u{1d49f}'),
    ('\u{1d4a2}', '\u{1d4a2}'),
    ('\u{1d4a5}', '\u{1d4a6}'),
    ('\u{1d4a9}', '\u{1d4ac}'),
    ('\u{1d4ae}', '\u{1d4b9}'),
    ('\u{1d4bb}', '\u{1d4bb}'),
    ('\u{1d4bd}', '\u{1d4c3}'),
    ('\u{1d4c5}', '\u{1d505}'),
    ('\u{1d507}', '\u{1d50a}'),
    ('\u{1d50d}', '\u{1d514}'),
    ('\u{1d516}', '\u{1d51c}'),
    ('\u{1d51e}', '\u{1d539}'),
    ('\u{1d53b}', '\u{1d53e}'),
    ('\u{1d540}', '\u{1d544}'),
    ('\u{1d546}', '\u{1d546}'),
    ('\u{1d54a}', '\u{1d550}'),
    ('\u{1d552}', '\u{1d6a5}'),
    ('\u{1d6a8}', '\u{1d6c0}'),
    ('\u{1d6c2}', '\u{1d6da}'),
    ('\u{1d6dc}', '\u{1d6fa}'),
    ('\u{1d6fc}', '\u{1d714}'),
    ('\u{1d716}', '\u{1d734}'),
    ('\u{1d736}', '\u{1d74e}'),
    ('\u{1d750}', '\u{1d76e}'),
    ('\u{1d770}', '\u{1d788}'),
    ('\u{1d78a}', '\u{1d7a8}'),
    ('\u{1d7aa}', '\u{1d7c2}'),
    ('\u{1d7c4}', '\u{1d7cb}'),
    ('\u{1df00}', '\u{1df09}'),
    ('\u{1df0b}', '\u{1df1e}'),
    ('\u{1df25}', '\u{1df2a}'),
    ('\u{1e030}', '\u{1e06d}'),
    ('\u{1e900}', '\u{1e943}'),
    ('\u{1f130}', '\u{1f149}'),
    ('\u{1f150}', '\u{1f169}'),
    ('\u{1f170}', '\u{1f189}'),
];

/// Unicode `Case_Ignorable`, as sorted ranges — the characters the
/// final-sigma lookahead skips over. `'` and `.` are both in it, which is
/// why `Σ.Ζ` lowercases to σ rather than ς.
const CASE_IGNORABLE_RANGES: [(char, char); 426] = [
    ('\u{27}', '\u{27}'),
    ('\u{2e}', '\u{2e}'),
    ('\u{3a}', '\u{3a}'),
    ('\u{5e}', '\u{5e}'),
    ('\u{60}', '\u{60}'),
    ('\u{a8}', '\u{a8}'),
    ('\u{ad}', '\u{ad}'),
    ('\u{af}', '\u{af}'),
    ('\u{b4}', '\u{b4}'),
    ('\u{b7}', '\u{b8}'),
    ('\u{2b9}', '\u{2bf}'),
    ('\u{2c2}', '\u{2df}'),
    ('\u{2e5}', '\u{344}'),
    ('\u{346}', '\u{36f}'),
    ('\u{374}', '\u{375}'),
    ('\u{384}', '\u{385}'),
    ('\u{387}', '\u{387}'),
    ('\u{483}', '\u{489}'),
    ('\u{559}', '\u{559}'),
    ('\u{55f}', '\u{55f}'),
    ('\u{591}', '\u{5bd}'),
    ('\u{5bf}', '\u{5bf}'),
    ('\u{5c1}', '\u{5c2}'),
    ('\u{5c4}', '\u{5c5}'),
    ('\u{5c7}', '\u{5c7}'),
    ('\u{5f4}', '\u{5f4}'),
    ('\u{600}', '\u{605}'),
    ('\u{610}', '\u{61a}'),
    ('\u{61c}', '\u{61c}'),
    ('\u{640}', '\u{640}'),
    ('\u{64b}', '\u{65f}'),
    ('\u{670}', '\u{670}'),
    ('\u{6d6}', '\u{6dd}'),
    ('\u{6df}', '\u{6e8}'),
    ('\u{6ea}', '\u{6ed}'),
    ('\u{70f}', '\u{70f}'),
    ('\u{711}', '\u{711}'),
    ('\u{730}', '\u{74a}'),
    ('\u{7a6}', '\u{7b0}'),
    ('\u{7eb}', '\u{7f5}'),
    ('\u{7fa}', '\u{7fa}'),
    ('\u{7fd}', '\u{7fd}'),
    ('\u{816}', '\u{82d}'),
    ('\u{859}', '\u{85b}'),
    ('\u{888}', '\u{888}'),
    ('\u{890}', '\u{891}'),
    ('\u{898}', '\u{89f}'),
    ('\u{8c9}', '\u{902}'),
    ('\u{93a}', '\u{93a}'),
    ('\u{93c}', '\u{93c}'),
    ('\u{941}', '\u{948}'),
    ('\u{94d}', '\u{94d}'),
    ('\u{951}', '\u{957}'),
    ('\u{962}', '\u{963}'),
    ('\u{971}', '\u{971}'),
    ('\u{981}', '\u{981}'),
    ('\u{9bc}', '\u{9bc}'),
    ('\u{9c1}', '\u{9c4}'),
    ('\u{9cd}', '\u{9cd}'),
    ('\u{9e2}', '\u{9e3}'),
    ('\u{9fe}', '\u{9fe}'),
    ('\u{a01}', '\u{a02}'),
    ('\u{a3c}', '\u{a3c}'),
    ('\u{a41}', '\u{a42}'),
    ('\u{a47}', '\u{a48}'),
    ('\u{a4b}', '\u{a4d}'),
    ('\u{a51}', '\u{a51}'),
    ('\u{a70}', '\u{a71}'),
    ('\u{a75}', '\u{a75}'),
    ('\u{a81}', '\u{a82}'),
    ('\u{abc}', '\u{abc}'),
    ('\u{ac1}', '\u{ac5}'),
    ('\u{ac7}', '\u{ac8}'),
    ('\u{acd}', '\u{acd}'),
    ('\u{ae2}', '\u{ae3}'),
    ('\u{afa}', '\u{aff}'),
    ('\u{b01}', '\u{b01}'),
    ('\u{b3c}', '\u{b3c}'),
    ('\u{b3f}', '\u{b3f}'),
    ('\u{b41}', '\u{b44}'),
    ('\u{b4d}', '\u{b4d}'),
    ('\u{b55}', '\u{b56}'),
    ('\u{b62}', '\u{b63}'),
    ('\u{b82}', '\u{b82}'),
    ('\u{bc0}', '\u{bc0}'),
    ('\u{bcd}', '\u{bcd}'),
    ('\u{c00}', '\u{c00}'),
    ('\u{c04}', '\u{c04}'),
    ('\u{c3c}', '\u{c3c}'),
    ('\u{c3e}', '\u{c40}'),
    ('\u{c46}', '\u{c48}'),
    ('\u{c4a}', '\u{c4d}'),
    ('\u{c55}', '\u{c56}'),
    ('\u{c62}', '\u{c63}'),
    ('\u{c81}', '\u{c81}'),
    ('\u{cbc}', '\u{cbc}'),
    ('\u{cbf}', '\u{cbf}'),
    ('\u{cc6}', '\u{cc6}'),
    ('\u{ccc}', '\u{ccd}'),
    ('\u{ce2}', '\u{ce3}'),
    ('\u{d00}', '\u{d01}'),
    ('\u{d3b}', '\u{d3c}'),
    ('\u{d41}', '\u{d44}'),
    ('\u{d4d}', '\u{d4d}'),
    ('\u{d62}', '\u{d63}'),
    ('\u{d81}', '\u{d81}'),
    ('\u{dca}', '\u{dca}'),
    ('\u{dd2}', '\u{dd4}'),
    ('\u{dd6}', '\u{dd6}'),
    ('\u{e31}', '\u{e31}'),
    ('\u{e34}', '\u{e3a}'),
    ('\u{e46}', '\u{e4e}'),
    ('\u{eb1}', '\u{eb1}'),
    ('\u{eb4}', '\u{ebc}'),
    ('\u{ec6}', '\u{ec6}'),
    ('\u{ec8}', '\u{ece}'),
    ('\u{f18}', '\u{f19}'),
    ('\u{f35}', '\u{f35}'),
    ('\u{f37}', '\u{f37}'),
    ('\u{f39}', '\u{f39}'),
    ('\u{f71}', '\u{f7e}'),
    ('\u{f80}', '\u{f84}'),
    ('\u{f86}', '\u{f87}'),
    ('\u{f8d}', '\u{f97}'),
    ('\u{f99}', '\u{fbc}'),
    ('\u{fc6}', '\u{fc6}'),
    ('\u{102d}', '\u{1030}'),
    ('\u{1032}', '\u{1037}'),
    ('\u{1039}', '\u{103a}'),
    ('\u{103d}', '\u{103e}'),
    ('\u{1058}', '\u{1059}'),
    ('\u{105e}', '\u{1060}'),
    ('\u{1071}', '\u{1074}'),
    ('\u{1082}', '\u{1082}'),
    ('\u{1085}', '\u{1086}'),
    ('\u{108d}', '\u{108d}'),
    ('\u{109d}', '\u{109d}'),
    ('\u{135d}', '\u{135f}'),
    ('\u{1712}', '\u{1714}'),
    ('\u{1732}', '\u{1733}'),
    ('\u{1752}', '\u{1753}'),
    ('\u{1772}', '\u{1773}'),
    ('\u{17b4}', '\u{17b5}'),
    ('\u{17b7}', '\u{17bd}'),
    ('\u{17c6}', '\u{17c6}'),
    ('\u{17c9}', '\u{17d3}'),
    ('\u{17d7}', '\u{17d7}'),
    ('\u{17dd}', '\u{17dd}'),
    ('\u{180b}', '\u{180f}'),
    ('\u{1843}', '\u{1843}'),
    ('\u{1885}', '\u{1886}'),
    ('\u{18a9}', '\u{18a9}'),
    ('\u{1920}', '\u{1922}'),
    ('\u{1927}', '\u{1928}'),
    ('\u{1932}', '\u{1932}'),
    ('\u{1939}', '\u{193b}'),
    ('\u{1a17}', '\u{1a18}'),
    ('\u{1a1b}', '\u{1a1b}'),
    ('\u{1a56}', '\u{1a56}'),
    ('\u{1a58}', '\u{1a5e}'),
    ('\u{1a60}', '\u{1a60}'),
    ('\u{1a62}', '\u{1a62}'),
    ('\u{1a65}', '\u{1a6c}'),
    ('\u{1a73}', '\u{1a7c}'),
    ('\u{1a7f}', '\u{1a7f}'),
    ('\u{1aa7}', '\u{1aa7}'),
    ('\u{1ab0}', '\u{1ace}'),
    ('\u{1b00}', '\u{1b03}'),
    ('\u{1b34}', '\u{1b34}'),
    ('\u{1b36}', '\u{1b3a}'),
    ('\u{1b3c}', '\u{1b3c}'),
    ('\u{1b42}', '\u{1b42}'),
    ('\u{1b6b}', '\u{1b73}'),
    ('\u{1b80}', '\u{1b81}'),
    ('\u{1ba2}', '\u{1ba5}'),
    ('\u{1ba8}', '\u{1ba9}'),
    ('\u{1bab}', '\u{1bad}'),
    ('\u{1be6}', '\u{1be6}'),
    ('\u{1be8}', '\u{1be9}'),
    ('\u{1bed}', '\u{1bed}'),
    ('\u{1bef}', '\u{1bf1}'),
    ('\u{1c2c}', '\u{1c33}'),
    ('\u{1c36}', '\u{1c37}'),
    ('\u{1c78}', '\u{1c7d}'),
    ('\u{1cd0}', '\u{1cd2}'),
    ('\u{1cd4}', '\u{1ce0}'),
    ('\u{1ce2}', '\u{1ce8}'),
    ('\u{1ced}', '\u{1ced}'),
    ('\u{1cf4}', '\u{1cf4}'),
    ('\u{1cf8}', '\u{1cf9}'),
    ('\u{1dc0}', '\u{1dff}'),
    ('\u{1fbd}', '\u{1fbd}'),
    ('\u{1fbf}', '\u{1fc1}'),
    ('\u{1fcd}', '\u{1fcf}'),
    ('\u{1fdd}', '\u{1fdf}'),
    ('\u{1fed}', '\u{1fef}'),
    ('\u{1ffd}', '\u{1ffe}'),
    ('\u{200b}', '\u{200f}'),
    ('\u{2018}', '\u{2019}'),
    ('\u{2024}', '\u{2024}'),
    ('\u{2027}', '\u{2027}'),
    ('\u{202a}', '\u{202e}'),
    ('\u{2060}', '\u{2064}'),
    ('\u{2066}', '\u{206f}'),
    ('\u{20d0}', '\u{20f0}'),
    ('\u{2cef}', '\u{2cf1}'),
    ('\u{2d6f}', '\u{2d6f}'),
    ('\u{2d7f}', '\u{2d7f}'),
    ('\u{2de0}', '\u{2dff}'),
    ('\u{2e2f}', '\u{2e2f}'),
    ('\u{3005}', '\u{3005}'),
    ('\u{302a}', '\u{302d}'),
    ('\u{3031}', '\u{3035}'),
    ('\u{303b}', '\u{303b}'),
    ('\u{3099}', '\u{309e}'),
    ('\u{30fc}', '\u{30fe}'),
    ('\u{a015}', '\u{a015}'),
    ('\u{a4f8}', '\u{a4fd}'),
    ('\u{a60c}', '\u{a60c}'),
    ('\u{a66f}', '\u{a672}'),
    ('\u{a674}', '\u{a67d}'),
    ('\u{a67f}', '\u{a67f}'),
    ('\u{a69e}', '\u{a69f}'),
    ('\u{a6f0}', '\u{a6f1}'),
    ('\u{a700}', '\u{a721}'),
    ('\u{a788}', '\u{a78a}'),
    ('\u{a802}', '\u{a802}'),
    ('\u{a806}', '\u{a806}'),
    ('\u{a80b}', '\u{a80b}'),
    ('\u{a825}', '\u{a826}'),
    ('\u{a82c}', '\u{a82c}'),
    ('\u{a8c4}', '\u{a8c5}'),
    ('\u{a8e0}', '\u{a8f1}'),
    ('\u{a8ff}', '\u{a8ff}'),
    ('\u{a926}', '\u{a92d}'),
    ('\u{a947}', '\u{a951}'),
    ('\u{a980}', '\u{a982}'),
    ('\u{a9b3}', '\u{a9b3}'),
    ('\u{a9b6}', '\u{a9b9}'),
    ('\u{a9bc}', '\u{a9bd}'),
    ('\u{a9cf}', '\u{a9cf}'),
    ('\u{a9e5}', '\u{a9e6}'),
    ('\u{aa29}', '\u{aa2e}'),
    ('\u{aa31}', '\u{aa32}'),
    ('\u{aa35}', '\u{aa36}'),
    ('\u{aa43}', '\u{aa43}'),
    ('\u{aa4c}', '\u{aa4c}'),
    ('\u{aa70}', '\u{aa70}'),
    ('\u{aa7c}', '\u{aa7c}'),
    ('\u{aab0}', '\u{aab0}'),
    ('\u{aab2}', '\u{aab4}'),
    ('\u{aab7}', '\u{aab8}'),
    ('\u{aabe}', '\u{aabf}'),
    ('\u{aac1}', '\u{aac1}'),
    ('\u{aadd}', '\u{aadd}'),
    ('\u{aaec}', '\u{aaed}'),
    ('\u{aaf3}', '\u{aaf4}'),
    ('\u{aaf6}', '\u{aaf6}'),
    ('\u{ab5b}', '\u{ab5b}'),
    ('\u{ab6a}', '\u{ab6b}'),
    ('\u{abe5}', '\u{abe5}'),
    ('\u{abe8}', '\u{abe8}'),
    ('\u{abed}', '\u{abed}'),
    ('\u{fb1e}', '\u{fb1e}'),
    ('\u{fbb2}', '\u{fbc2}'),
    ('\u{fe00}', '\u{fe0f}'),
    ('\u{fe13}', '\u{fe13}'),
    ('\u{fe20}', '\u{fe2f}'),
    ('\u{fe52}', '\u{fe52}'),
    ('\u{fe55}', '\u{fe55}'),
    ('\u{feff}', '\u{feff}'),
    ('\u{ff07}', '\u{ff07}'),
    ('\u{ff0e}', '\u{ff0e}'),
    ('\u{ff1a}', '\u{ff1a}'),
    ('\u{ff3e}', '\u{ff3e}'),
    ('\u{ff40}', '\u{ff40}'),
    ('\u{ff70}', '\u{ff70}'),
    ('\u{ff9e}', '\u{ff9f}'),
    ('\u{ffe3}', '\u{ffe3}'),
    ('\u{fff9}', '\u{fffb}'),
    ('\u{101fd}', '\u{101fd}'),
    ('\u{102e0}', '\u{102e0}'),
    ('\u{10376}', '\u{1037a}'),
    ('\u{10781}', '\u{10782}'),
    ('\u{10a01}', '\u{10a03}'),
    ('\u{10a05}', '\u{10a06}'),
    ('\u{10a0c}', '\u{10a0f}'),
    ('\u{10a38}', '\u{10a3a}'),
    ('\u{10a3f}', '\u{10a3f}'),
    ('\u{10ae5}', '\u{10ae6}'),
    ('\u{10d24}', '\u{10d27}'),
    ('\u{10eab}', '\u{10eac}'),
    ('\u{10efd}', '\u{10eff}'),
    ('\u{10f46}', '\u{10f50}'),
    ('\u{10f82}', '\u{10f85}'),
    ('\u{11001}', '\u{11001}'),
    ('\u{11038}', '\u{11046}'),
    ('\u{11070}', '\u{11070}'),
    ('\u{11073}', '\u{11074}'),
    ('\u{1107f}', '\u{11081}'),
    ('\u{110b3}', '\u{110b6}'),
    ('\u{110b9}', '\u{110ba}'),
    ('\u{110bd}', '\u{110bd}'),
    ('\u{110c2}', '\u{110c2}'),
    ('\u{110cd}', '\u{110cd}'),
    ('\u{11100}', '\u{11102}'),
    ('\u{11127}', '\u{1112b}'),
    ('\u{1112d}', '\u{11134}'),
    ('\u{11173}', '\u{11173}'),
    ('\u{11180}', '\u{11181}'),
    ('\u{111b6}', '\u{111be}'),
    ('\u{111c9}', '\u{111cc}'),
    ('\u{111cf}', '\u{111cf}'),
    ('\u{1122f}', '\u{11231}'),
    ('\u{11234}', '\u{11234}'),
    ('\u{11236}', '\u{11237}'),
    ('\u{1123e}', '\u{1123e}'),
    ('\u{11241}', '\u{11241}'),
    ('\u{112df}', '\u{112df}'),
    ('\u{112e3}', '\u{112ea}'),
    ('\u{11300}', '\u{11301}'),
    ('\u{1133b}', '\u{1133c}'),
    ('\u{11340}', '\u{11340}'),
    ('\u{11366}', '\u{1136c}'),
    ('\u{11370}', '\u{11374}'),
    ('\u{11438}', '\u{1143f}'),
    ('\u{11442}', '\u{11444}'),
    ('\u{11446}', '\u{11446}'),
    ('\u{1145e}', '\u{1145e}'),
    ('\u{114b3}', '\u{114b8}'),
    ('\u{114ba}', '\u{114ba}'),
    ('\u{114bf}', '\u{114c0}'),
    ('\u{114c2}', '\u{114c3}'),
    ('\u{115b2}', '\u{115b5}'),
    ('\u{115bc}', '\u{115bd}'),
    ('\u{115bf}', '\u{115c0}'),
    ('\u{115dc}', '\u{115dd}'),
    ('\u{11633}', '\u{1163a}'),
    ('\u{1163d}', '\u{1163d}'),
    ('\u{1163f}', '\u{11640}'),
    ('\u{116ab}', '\u{116ab}'),
    ('\u{116ad}', '\u{116ad}'),
    ('\u{116b0}', '\u{116b5}'),
    ('\u{116b7}', '\u{116b7}'),
    ('\u{1171d}', '\u{1171f}'),
    ('\u{11722}', '\u{11725}'),
    ('\u{11727}', '\u{1172b}'),
    ('\u{1182f}', '\u{11837}'),
    ('\u{11839}', '\u{1183a}'),
    ('\u{1193b}', '\u{1193c}'),
    ('\u{1193e}', '\u{1193e}'),
    ('\u{11943}', '\u{11943}'),
    ('\u{119d4}', '\u{119d7}'),
    ('\u{119da}', '\u{119db}'),
    ('\u{119e0}', '\u{119e0}'),
    ('\u{11a01}', '\u{11a0a}'),
    ('\u{11a33}', '\u{11a38}'),
    ('\u{11a3b}', '\u{11a3e}'),
    ('\u{11a47}', '\u{11a47}'),
    ('\u{11a51}', '\u{11a56}'),
    ('\u{11a59}', '\u{11a5b}'),
    ('\u{11a8a}', '\u{11a96}'),
    ('\u{11a98}', '\u{11a99}'),
    ('\u{11c30}', '\u{11c36}'),
    ('\u{11c38}', '\u{11c3d}'),
    ('\u{11c3f}', '\u{11c3f}'),
    ('\u{11c92}', '\u{11ca7}'),
    ('\u{11caa}', '\u{11cb0}'),
    ('\u{11cb2}', '\u{11cb3}'),
    ('\u{11cb5}', '\u{11cb6}'),
    ('\u{11d31}', '\u{11d36}'),
    ('\u{11d3a}', '\u{11d3a}'),
    ('\u{11d3c}', '\u{11d3d}'),
    ('\u{11d3f}', '\u{11d45}'),
    ('\u{11d47}', '\u{11d47}'),
    ('\u{11d90}', '\u{11d91}'),
    ('\u{11d95}', '\u{11d95}'),
    ('\u{11d97}', '\u{11d97}'),
    ('\u{11ef3}', '\u{11ef4}'),
    ('\u{11f00}', '\u{11f01}'),
    ('\u{11f36}', '\u{11f3a}'),
    ('\u{11f40}', '\u{11f40}'),
    ('\u{11f42}', '\u{11f42}'),
    ('\u{13430}', '\u{13440}'),
    ('\u{13447}', '\u{13455}'),
    ('\u{16af0}', '\u{16af4}'),
    ('\u{16b30}', '\u{16b36}'),
    ('\u{16b40}', '\u{16b43}'),
    ('\u{16f4f}', '\u{16f4f}'),
    ('\u{16f8f}', '\u{16f9f}'),
    ('\u{16fe0}', '\u{16fe1}'),
    ('\u{16fe3}', '\u{16fe4}'),
    ('\u{1aff0}', '\u{1aff3}'),
    ('\u{1aff5}', '\u{1affb}'),
    ('\u{1affd}', '\u{1affe}'),
    ('\u{1bc9d}', '\u{1bc9e}'),
    ('\u{1bca0}', '\u{1bca3}'),
    ('\u{1cf00}', '\u{1cf2d}'),
    ('\u{1cf30}', '\u{1cf46}'),
    ('\u{1d167}', '\u{1d169}'),
    ('\u{1d173}', '\u{1d182}'),
    ('\u{1d185}', '\u{1d18b}'),
    ('\u{1d1aa}', '\u{1d1ad}'),
    ('\u{1d242}', '\u{1d244}'),
    ('\u{1da00}', '\u{1da36}'),
    ('\u{1da3b}', '\u{1da6c}'),
    ('\u{1da75}', '\u{1da75}'),
    ('\u{1da84}', '\u{1da84}'),
    ('\u{1da9b}', '\u{1da9f}'),
    ('\u{1daa1}', '\u{1daaf}'),
    ('\u{1e000}', '\u{1e006}'),
    ('\u{1e008}', '\u{1e018}'),
    ('\u{1e01b}', '\u{1e021}'),
    ('\u{1e023}', '\u{1e024}'),
    ('\u{1e026}', '\u{1e02a}'),
    ('\u{1e08f}', '\u{1e08f}'),
    ('\u{1e130}', '\u{1e13d}'),
    ('\u{1e2ae}', '\u{1e2ae}'),
    ('\u{1e2ec}', '\u{1e2ef}'),
    ('\u{1e4eb}', '\u{1e4ef}'),
    ('\u{1e8d0}', '\u{1e8d6}'),
    ('\u{1e944}', '\u{1e94b}'),
    ('\u{1f3fb}', '\u{1f3ff}'),
    ('\u{e0001}', '\u{e0001}'),
    ('\u{e0020}', '\u{e007f}'),
    ('\u{e0100}', '\u{e01ef}'),
];

fn in_ranges(table: &[(char, char)], c: char) -> bool {
    table
        .binary_search_by(|&(lo, hi)| {
            if c < lo {
                std::cmp::Ordering::Greater
            } else if c > hi {
                std::cmp::Ordering::Less
            } else {
                std::cmp::Ordering::Equal
            }
        })
        .is_ok()
}

/// `_PyUnicode_IsCased`.
fn is_cased(c: char) -> bool {
    in_ranges(&CASED_RANGES, c)
}

/// `_PyUnicode_IsCaseIgnorable`.
fn is_case_ignorable(c: char) -> bool {
    in_ranges(&CASE_IGNORABLE_RANGES, c)
}

/// Python's `\d` for `str` patterns: general category `Nd` only.
fn py_is_digit(c: char) -> bool {
    in_ranges(&ND_RANGES, c)
}

/// `_PyUnicode_ToTitleFull` — the titlecase mapping, which is NOT uppercase.
///
/// It differs for exactly 135 codepoints and in two ways: a multi-character
/// uppercase titlecases as first-upper-rest-lower (`ß` is `Ss`, not `SS`), and
/// the four Latin digraphs plus Georgian Mtavruli have their own mapping. The
/// table above is generated from CPython, so the set is closed rather than
/// guessed.
fn to_title_full(c: char) -> String {
    if let Some((_, t)) = TITLE_EXCEPTIONS.iter().find(|(k, _)| *k == c) {
        return (*t).to_string();
    }
    c.to_uppercase().collect()
}

/// Python's `str.title()`.
///
/// A character is titlecased when the character *before it* is not cased, and
/// lowercased otherwise — so a word boundary is any non-cased character, digits
/// and `<` included. The previous implementation split on whitespace, which is
/// why `<b>x</b>` came back untouched.
fn py_title(s: &str) -> String {
    let chars: Vec<char> = s.chars().collect();
    let mut out = String::with_capacity(s.len());
    let mut previous_is_cased = false;
    for (i, &c) in chars.iter().enumerate() {
        if previous_is_cased {
            if c == '\u{3a3}' {
                // CPython's `handle_capital_sigma`: a sigma with a cased
                // character before it and none after lowercases to ς, not σ.
                // Reaching here already establishes the "before" half (a cased
                // character is never Case_Ignorable, so CPython's backward scan
                // stops on it immediately). The forward scan skips
                // Case_Ignorable — which includes `'` and `.`, so `Σ.Ζ` is σ.
                let next_is_cased = chars[i + 1..]
                    .iter()
                    .find(|&&n| !is_case_ignorable(n))
                    .is_some_and(|&n| is_cased(n));
                out.push(if next_is_cased { '\u{3c3}' } else { '\u{3c2}' });
            } else {
                out.extend(c.to_lowercase());
            }
        } else {
            out.push_str(&to_title_full(c));
        }
        previous_is_cased = is_cased(c);
    }
    out
}

/// `django.template.defaultfilters.title`.
pub fn title(value: &str) -> String {
    let titled = py_title(value);
    // re.sub("([a-z])'([A-Z])", lambda m: m[0].lower(), …)
    let mut step1 = String::with_capacity(titled.len());
    let chars: Vec<char> = titled.chars().collect();
    let mut i = 0;
    while i < chars.len() {
        if i + 2 < chars.len()
            && chars[i].is_ascii_lowercase()
            && chars[i + 1] == '\''
            && chars[i + 2].is_ascii_uppercase()
        {
            step1.push(chars[i]);
            step1.push('\'');
            step1.extend(chars[i + 2].to_lowercase());
            i += 3;
        } else {
            step1.push(chars[i]);
            i += 1;
        }
    }
    // re.sub(r"\d([A-Z])", lambda m: m[0].lower(), …)
    let mut out = String::with_capacity(step1.len());
    let chars: Vec<char> = step1.chars().collect();
    let mut i = 0;
    while i < chars.len() {
        if i + 1 < chars.len() && py_is_digit(chars[i]) && chars[i + 1].is_ascii_uppercase() {
            out.extend(chars[i].to_lowercase());
            out.extend(chars[i + 1].to_lowercase());
            i += 2;
        } else {
            out.push(chars[i]);
            i += 1;
        }
    }
    out
}

/// `urllib.parse.quote(value, safe=…)`, which is what Django's `urlencode`
/// filter is. `safe` defaults to `"/"` — the argument form (`urlencode:""`) is
/// what removes it, and passing no argument does NOT mean "escape everything".
pub fn urlencode(value: &str, safe: Option<&str>) -> String {
    let safe = safe.unwrap_or("/");
    let mut safe_set = [false; 128];
    for b in b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-~" {
        safe_set[*b as usize] = true;
    }
    // `safe.encode("ascii", "ignore")` — non-ASCII characters are dropped, and
    // a multi-byte character's bytes are all >= 0x80 so this is the same set.
    for b in safe.bytes() {
        if b < 128 {
            safe_set[b as usize] = true;
        }
    }
    let mut out = String::with_capacity(value.len());
    for b in value.bytes() {
        if b < 128 && safe_set[b as usize] {
            out.push(b as char);
        } else {
            out.push('%');
            out.push_str(&format!("{b:02X}"));
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unescape_matches_cpython_shapes() {
        assert_eq!(unescape("a &amp; b"), "a & b");
        assert_eq!(unescape("&#65;"), "A");
        assert_eq!(unescape("&#x41;"), "A");
        assert_eq!(unescape("&#65"), "A");
        assert_eq!(unescape("&nope;"), "&nope;");
        assert_eq!(unescape("&"), "&");
        assert_eq!(unescape("&#x27;"), "'");
        // Longest-prefix fallback: `&notit;` is `&not` + `it;`.
        assert_eq!(unescape("&notit;"), "\u{ac}it;");
        // C1 remap and the invalid-codepoint drop.
        assert_eq!(unescape("&#128;"), "\u{20ac}");
        assert_eq!(unescape("&#1;"), "");
    }

    #[test]
    fn split_interior_whitespace_keeps_padding() {
        assert_eq!(split_interior_whitespace("a b"), vec!["a", "b"]);
        assert_eq!(split_interior_whitespace(" a  b "), vec![" a", "b "]);
        assert_eq!(split_interior_whitespace("   "), vec!["   "]);
        assert_eq!(split_interior_whitespace(""), vec![""]);
    }

    #[test]
    fn chars_special_case_returns_the_input_whole() {
        // The reported cell: 8 characters at a limit of 8 is not "over".
        assert_eq!(html_chars("Infinity", 8, None), "Infinity");
        assert_eq!(html_chars("Infinity!", 8, None), "Infinit…");
    }

    #[test]
    fn words_html_escapes_its_kept_text() {
        assert_eq!(html_words("{'a': 1}", 2, Some(" …")), "{&#x27;a&#x27;: 1}");
    }

    #[test]
    fn slugify_deletes_rather_than_separates() {
        assert_eq!(slugify("3.5"), "35");
        assert_eq!(slugify("<b>x</b>"), "bxb");
        assert_eq!(slugify("-1.5e+300"), "15e300");
        assert_eq!(slugify("  hello   world  "), "hello-world");
        assert_eq!(slugify("_a_"), "a");
    }

    #[test]
    fn title_uses_python_word_boundaries() {
        assert_eq!(title("  spaced  "), "  Spaced  ");
        assert_eq!(title("<b>x</b>"), "<B>X</B>");
        assert_eq!(title("a1b"), "A1b");
        assert_eq!(title("o'connor"), "O'Connor");
    }

    #[test]
    fn urlencode_keeps_slash_by_default() {
        assert_eq!(urlencode("<b>x</b>", None), "%3Cb%3Ex%3C/b%3E");
        assert_eq!(urlencode("<b>x</b>", Some("")), "%3Cb%3Ex%3C%2Fb%3E");
        assert_eq!(urlencode("a b~_.-", None), "a%20b~_.-");
    }

    #[test]
    fn text_words_joins_on_single_spaces() {
        assert_eq!(text_words("  spaced  ", 2, Some(" …")), "spaced");
        assert_eq!(text_words("a  b   c", 2, Some(" …")), "a b …");
        assert_eq!(text_words("anything", 0, Some(" …")), "");
    }
}
