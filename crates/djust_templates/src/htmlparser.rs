//! A port of `html.parser.HTMLParser`'s tokenizer, shared by every djust
//! filter that has to see HTML the way Django does (#2262, #2273).
//!
//! # Why this is one tokenizer and not two
//!
//! `truncatechars_html` / `truncatewords_html` (#2262) and `striptags` (#2273)
//! are both ports of a CPython `HTMLParser` subclass. They differ only in what
//! their handler methods *do* with the tokens: the truncators re-emit tags and
//! spend a budget on text, `MLStripper` drops tags and keeps text. Writing the
//! `goahead` state machine twice would be the parallel-path drift this
//! codebase keeps paying for, so the state machine lives here once, behind
//! [`Sink`], and the two ports are two implementations of that trait.
//!
//! # Fidelity
//!
//! This is a transcription of the **CPython 3.12.10+ / 3.13** `html/parser.py`,
//! including the parts that look like bugs.
//!
//! The precision matters, and "CPython 3.12" would not be precise enough to be
//! true. The HTML5-spec rewrite landed in **3.12.10**, so 3.12.9 *is* a CPython
//! 3.12 and djust differs from it on a quarter of the corpus — a reader on
//! 3.12.9 taking a looser header at its word would expect a match and not get
//! one. `requires-python` is `>=3.10`, so 3.10 and 3.11 are supported too and
//! carry the same pre-rewrite parser.
//!
//! Measured over the 4000 values in the fixture named below, rendering
//! `{{ p|striptags }}` and comparing against each interpreter's recorded answer
//! run through Django's `escape` (the cells where CPython *raises* — the
//! unrelated DoS guard — are excluded, which is why the middle row is 0 rather
//! than 2):
//!
//! | interpreter | djust's `striptags` differs on | |
//! |---|---|---|
//! | 3.12.9 | 992 | 24.8% |
//! | 3.12.13, 3.13.7 | 0 | — |
//! | 3.14.6 | 231 | 5.8% |
//!
//! **This is djust's own pinned behaviour on every host, not a claim about the
//! running interpreter.** djust deliberately does not branch on
//! `sys.version_info` here: a template filter whose output changes when ops
//! bumps the base image is a worse property than a documented fixed divergence,
//! and Django's own `strip_tags` is disclaimed as "not guaranteed to produce
//! safe output" regardless. That decision is #2286;
//! `python/tests/fixtures/striptags_reference_2273.json` records every supported
//! interpreter's answer per value (3.12.9, 3.12.13, 3.13.7, 3.14.6), splitting
//! them into a `stable` set asserted on every runner and an `unstable` set that
//! keeps the moving reference visible in the repo (#2273).
//!
//! The parts that look like bugs, and are faithfully reproduced:
//!
//! * `entityref` is `&([a-zA-Z][-.a-zA-Z0-9]*)[^a-zA-Z0-9]`, which requires a
//!   trailing character, so `&one two` resolves as the entity `one` and
//!   `MLStripper` re-emits it **with** the `;` the source did not have.
//! * A `<` that is not followed by a letter, `/`, `!` or `?` is *data*, which
//!   is why `a < b` survives `striptags` unharmed.
//! * `handle_comment` / `handle_decl` / `handle_pi` / `unknown_decl` are not
//!   overridden by either Django parser, so comments, doctypes and processing
//!   instructions contribute nothing to either filter's output.
//!
//! The four regexes CPython uses that the `regex` crate cannot express
//! (`attrfind_tolerant` and `locatestarttagend_tolerant` both use lookbehind,
//! `tagfind_tolerant` and the `(?!>)` in both use lookahead) are hand-rolled
//! as [`match_attr`], [`locate_starttag_end`] and [`tagfind`].
//!
//! * `entityref`'s name class `[-.a-zA-Z0-9]` overlaps its own trailing
//!   `[^a-zA-Z0-9]` on `-` and `.`, so `re`'s BACKTRACKING is load-bearing:
//!   `&amp-` resolves as the entity `amp` with `-` as the trail. A greedy
//!   scan gets this wrong, and only a differential finds it.
//!
//! # How many passes
//!
//! CPython's `feed()` runs `goahead(0)` and `close()` runs `goahead(1)`.
//! Django's two parsers differ in how many of those they take, so the driver
//! is a choice of [`Tokenizer::feed`] or [`Tokenizer::feed_and_close`] rather
//! than an `end` flag — see those two methods for why the second pass is
//! observably distinct from a single `goahead(1)` rather than a shorthand for
//! it.

use once_cell::sync::Lazy;
use regex::Regex;

use crate::truncate::py_is_space;

/// HTML5 "ASCII whitespace" — `[\t\n\r\f ]`.
///
/// CPython's tag regexes used `\s` until the 3.12.10 spec alignment, which is
/// a WIDER set: it also matches `\v` and, on `str` patterns, every Unicode
/// space. `py_is_space` is still correct for the two places CPython kept `\s`
/// (`unescape`, and the `[\s;]` probe that became `[\t\n\r\f ;]`), so the
/// two predicates coexist deliberately.
fn is_html_space(c: char) -> bool {
    matches!(c, '\t' | '\n' | '\r' | '\u{c}' | ' ')
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

pub(crate) fn char_at(s: &str, i: usize) -> Option<char> {
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

/// `locatetagend.match(rawdata, pos)` -> `m.end()`, where `pos` is the offset
/// of the tag NAME (after `<` or `</`).
///
/// Replaces the pre-3.12.10 `locatestarttagend_tolerant`. The important
/// structural change is the trailing `>?`: the regex always matches (the tag
/// name is already known to start with a letter), and the caller decides
/// whether the tag was terminated by testing `rawdata[j-1] == '>'`, rather
/// than the old five-way lookahead on the character after the match.
///
/// ```text
///   [a-zA-Z][^\t\n\r\f />]*            tag name
///   [\t\n\r\f /]*                      whitespace before the first attribute
///   (?:(?<=['"\t\n\r\f /])[^\t\n\r\f />][^\t\n\r\f /=>]*   attribute name
///     (?:[\t\n\r\f ]*=[\t\n\r\f ]*     value indicator
///       (?:'[^']*'|"[^"]*"|(?!['"])[^>\t\n\r\f ]*))?
///     [\t\n\r\f /]*)*
///   >?
/// ```
fn locate_tag_end(s: &str, pos: usize) -> usize {
    let n = s.len();
    let mut p = pos;
    // `[a-zA-Z][^\t\n\r\f />]*`
    if let Some(c) = char_at(s, p) {
        if c.is_ascii_alphabetic() {
            p += c.len_utf8();
        }
    }
    while let Some(c) = char_at(s, p) {
        if is_html_space(c) || c == '/' || c == '>' {
            break;
        }
        p += c.len_utf8();
    }
    // `[\t\n\r\f /]*`
    p = skip_space_or_slash(s, p);
    // `(?: attr )*`
    loop {
        match match_attribute(s, p) {
            Some(e) if e > p => p = e,
            _ => break,
        }
    }
    // `>?`
    if p < n && s.as_bytes()[p] == b'>' {
        p += 1;
    }
    p
}

fn skip_space_or_slash(s: &str, mut i: usize) -> usize {
    while let Some(c) = char_at(s, i) {
        if is_html_space(c) || c == '/' {
            i += c.len_utf8();
        } else {
            break;
        }
    }
    i
}

/// One iteration of `locate_tag_end`'s attribute group. Hand-rolled because
/// the leading `(?<=['"\t\n\r\f /])` is a lookbehind.
fn match_attribute(s: &str, k: usize) -> Option<usize> {
    let n = s.len();
    let b = s.as_bytes();
    // `(?<=['"\t\n\r\f /])`
    let prev = prev_char(s, k)?;
    if !(prev == '\'' || prev == '"' || prev == '/' || is_html_space(prev)) {
        return None;
    }
    // `[^\t\n\r\f />]`
    let c = char_at(s, k)?;
    if is_html_space(c) || c == '/' || c == '>' {
        return None;
    }
    let mut p = k + c.len_utf8();
    // `[^\t\n\r\f /=>]*`
    while let Some(c) = char_at(s, p) {
        if is_html_space(c) || c == '/' || c == '=' || c == '>' {
            break;
        }
        p += c.len_utf8();
    }
    // `(?:[\t\n\r\f ]*=[\t\n\r\f ]*(value))?` — note ONE `=`, where the old
    // `attrfind_tolerant` had `=+`.
    let before_value = p;
    let mut r = skip_html_space(s, p);
    let mut took_value = false;
    if r < n && b[r] == b'=' {
        r = skip_html_space(s, r + 1);
        if r < n && (b[r] == b'\'' || b[r] == b'"') {
            let quote = b[r] as char;
            if let Some(off) = s[r + 1..].find(quote) {
                r = r + 1 + off + 1;
                took_value = true;
            }
        } else {
            // `(?!['"])[^>\t\n\r\f ]*`
            while let Some(c) = char_at(s, r) {
                if c == '>' || is_html_space(c) {
                    break;
                }
                r += c.len_utf8();
            }
            took_value = true;
        }
    }
    let p = if took_value { r } else { before_value };
    // `[\t\n\r\f /]*`
    Some(skip_space_or_slash(s, p))
}

fn skip_html_space(s: &str, mut i: usize) -> usize {
    while let Some(c) = char_at(s, i) {
        if is_html_space(c) {
            i += c.len_utf8();
        } else {
            break;
        }
    }
    i
}

/// `tagfind_tolerant.match(rawdata, pos)` → `(lowercased name, m.end())`.
fn tagfind(s: &str, pos: usize) -> Option<(String, usize)> {
    let b = s.as_bytes();
    let n = s.len();
    if pos >= n || !(b[pos] as char).is_ascii_alphabetic() {
        return None;
    }
    let mut p = pos + 1;
    // `[^\t\n\r\f />]*` — `\x00` left the exclusion set in 3.12.10.
    while let Some(c) = char_at(s, p) {
        if is_html_space(c) || c == '/' || c == '>' {
            break;
        }
        p += c.len_utf8();
    }
    let _ = n;
    let name = s[pos..p].to_lowercase();
    Some((name, skip_space_or_slash(s, p)))
}

/// `parse_comment`'s close: `commentclose = --!?>` searched from `from`, and
/// if that fails `commentabruptclose = -?>` ANCHORED at `from`.
///
/// Before 3.12.10 this was `--\s*>`, so `<!-- x -- >` closed the comment and
/// `<!-->` did not. Both flipped: whitespace between `--` and `>` no longer
/// closes, and the abrupt forms `<!-->` / `<!--->` now do.
fn comment_close(s: &str, from: usize) -> Option<usize> {
    let b = s.as_bytes();
    let n = s.len();
    let mut p = from;
    while let Some(off) = s[p..].find("--") {
        let start = p + off;
        let mut e = start + 2;
        if e < n && b[e] == b'!' {
            e += 1;
        }
        if e < n && b[e] == b'>' {
            return Some(e + 1);
        }
        p = start + 1;
    }
    // `commentabruptclose.match(rawdata, i+4)` — anchored, not searched.
    let mut e = from;
    if e < n && b[e] == b'-' {
        e += 1;
    }
    if e < n && b[e] == b'>' {
        return Some(e + 1);
    }
    None
}

/// `HTMLParser.check_for_whole_start_tag`.
///
/// Since 3.12.10 this is three lines: run `locatetagend` from the character
/// after `<` and accept only if the match ends on a `>`. The old version's
/// five-way lookahead (`=` and a letter meaning "incomplete", a bare
/// character meaning "stop here") is gone, which is why an unterminated
/// `<b x=">` now returns -1 and is discarded at end of input rather than
/// being re-emitted as data.
fn check_for_whole_start_tag(s: &str, i: usize) -> i64 {
    let j = locate_tag_end(s, i + 1);
    if j == 0 || s.as_bytes()[j - 1] != b'>' {
        return -1;
    }
    j as i64
}

fn parse_html_declaration(s: &str, i: usize) -> i64 {
    if s[i..].starts_with("<!--") {
        return comment_close(s, i + 4).map(|e| e as i64).unwrap_or(-1);
    }
    // `<![CDATA[` runs to `]]>`; `unknown_decl` is a no-op for both sinks.
    if s[i..].starts_with("<![CDATA[") {
        return match s[i + 9..].find("]]>") {
            Some(o) => (i + 9 + o + 3) as i64,
            None => -1,
        };
    }
    // `s.get(..)` rather than `s[..]`: `i + 9` is a byte offset that can land
    // INSIDE a multi-byte character, and indexing panics there. Reachable
    // since the `<![` arm above stopped swallowing every `<![…` input — a
    // `<![中` used to return early and now falls through to here.
    if s.get(i..i + 9)
        .is_some_and(|d| d.eq_ignore_ascii_case("<!doctype"))
    {
        return s[i + 9..]
            .find('>')
            .map(|o| (i + 9 + o + 1) as i64)
            .unwrap_or(-1);
    }
    // `<![` that is not a CDATA section, and the `parse_bogus_comment`
    // fallback, are the same shape here: run to the next `>` and report
    // nothing. `unknown_decl` and `handle_comment` are both no-ops for
    // Django's two sinks, so the two branches CPython distinguishes collapse.
    //
    // This replaces a port of `_markupbase.parse_marked_section`, which
    // raises `AssertionError` on an unrecognised section keyword — CPython
    // dropped it in the 3.12.10 spec alignment, and djust's fail-soft stand-in
    // discarded the whole rest of the input rather than resuming after the
    // section. `strip_tags("x&&&one;<![</p>")` was `"x&&&one;<![</p>"` and is
    // now `"x&&&one;"`, matching every CPython from 3.12.10 on.
    s[i + 2..]
        .find('>')
        .map(|o| (i + 2 + o + 1) as i64)
        .unwrap_or(-1)
}

/// `set_cdata_mode`'s `interesting`, searched: `</{elem}(?=[\t\n\r\f />])`.
///
/// Before 3.12.10 this was `</\s*{elem}\s*>`, which required the `>` to be
/// present and tolerated whitespace after `</`. Neither holds now, so
/// `<style></b<<a href="a>b">` leaves CDATA mode at a different offset — the
/// value that caught this port mid-way.
fn find_cdata_close(s: &str, from: usize, elem: &str) -> Option<usize> {
    let mut p = from;
    while let Some(off) = s[p..].find("</") {
        let start = p + off;
        let q = start + 2;
        if s.get(q..q + elem.len())
            .is_some_and(|d| d.eq_ignore_ascii_case(elem))
        {
            match char_at(s, q + elem.len()) {
                Some(c) if is_html_space(c) || c == '/' || c == '>' => return Some(start),
                _ => {}
            }
        }
        p = start + 1;
    }
    None
}

// ---------------------------------------------------------------------------
// The Sink trait
// ---------------------------------------------------------------------------

/// Stand-in for an exception a handler raises to abort the parse
/// (`TruncateHTMLParser.TruncationCompleted`). A sink that never aborts
/// returns `Ok(())` from `handle_data` unconditionally.
pub(crate) struct Stop;

pub(crate) type SinkResult = Result<(), Stop>;

/// The `HTMLParser` handler methods either Django parser overrides.
///
/// The ones neither overrides -- `handle_comment`, `handle_decl`,
/// `handle_pi`, `unknown_decl` -- are deliberately absent rather than
/// no-op members: adding them here would invite a caller to assume comments
/// reach the output, and in Django they never do.
pub(crate) trait Sink {
    fn handle_data(&mut self, data: &str) -> SinkResult;
    fn handle_starttag(&mut self, tag: &str, starttag_text: &str);
    fn handle_endtag(&mut self, tag: &str);

    /// `HTMLParser.handle_startendtag`'s default: a start tag then an end tag.
    fn handle_startendtag(&mut self, tag: &str, starttag_text: &str) {
        self.handle_starttag(tag, starttag_text);
        self.handle_endtag(tag);
    }

    /// Only reachable with `convert_charrefs = false`; with it on, a run of
    /// text never stops at `&`, so `goahead` never enters the charref arms.
    fn handle_entityref(&mut self, _name: &str) {}
    fn handle_charref(&mut self, _name: &str) {}
}

// ---------------------------------------------------------------------------
// charref / entityref, the two regexes that ARE expressible but are cheaper
// hand-rolled because both need the match's interior boundary, not just its end
// ---------------------------------------------------------------------------

/// `charref = re.compile('&#(?:[0-9]+|[xX][0-9a-fA-F]+)[^0-9a-fA-F]')`
/// anchored at `i`.
///
/// Returns `(body_end, match_end)`: `rawdata[i+2..body_end]` is
/// `match.group()[2:-1]` (the name CPython hands `handle_charref`) and
/// `rawdata[body_end..match_end]` is the single trailing non-hex character,
/// which the caller inspects to decide whether the `;` was consumed.
fn charref_match(s: &str, i: usize) -> Option<(usize, usize)> {
    let rest = s.get(i + 2..)?;
    let mut it = rest.char_indices();
    let (hex, mut k) = match it.next()? {
        (_, 'x') | (_, 'X') => (true, 1),
        (_, c) if c.is_ascii_digit() => (false, 0),
        _ => return None,
    };
    // At least one digit of the right base.
    let mut body = k;
    for (off, c) in rest[k..].char_indices() {
        let ok = if hex {
            c.is_ascii_hexdigit()
        } else {
            c.is_ascii_digit()
        };
        if !ok {
            break;
        }
        body = k + off + c.len_utf8();
    }
    if body == k {
        return None;
    }
    k = body;
    // `[^0-9a-fA-F]` -- note CPython uses the HEX class for the trailing
    // character in BOTH branches, so `&#12a` does not match while `&#12z`
    // does.
    let trail = rest[k..].chars().next()?;
    if trail.is_ascii_hexdigit() {
        return None;
    }
    Some((i + 2 + k, i + 2 + k + trail.len_utf8()))
}

/// `entityref = re.compile('&([a-zA-Z][-.a-zA-Z0-9]*)[^a-zA-Z0-9]')`
/// anchored at `i`. Returns `(name_end, match_end)`.
fn entityref_match(s: &str, i: usize) -> Option<(usize, usize)> {
    let rest = s.get(i + 1..)?;
    let first = rest.chars().next()?;
    if !first.is_ascii_alphabetic() {
        return None;
    }
    let min = first.len_utf8();
    let mut k = min;
    for c in rest[k..].chars() {
        if c == '-' || c == '.' || c.is_ascii_alphanumeric() {
            k += c.len_utf8();
        } else {
            break;
        }
    }
    // The name class `[-.a-zA-Z0-9]` OVERLAPS the trailing class's complement
    // only on `-` and `.`, so a greedy scan can consume the very character the
    // trailing `[^a-zA-Z0-9]` needed. `re` backtracks; this walks back to the
    // longest name whose next character exists and is non-alphanumeric.
    //
    // `&amp-` at end of input: greedy takes `amp-`, finds no trailing
    // character, backs off to `amp` and matches with `-` as the trail. Without
    // this, `strip_tags("&amp-")` emitted a bare `&amp-` where Django emits
    // `&amp;-`. Same for `&one3.5` -> name `one3`, trail `.`.
    loop {
        match rest[k..].chars().next() {
            Some(c) if !c.is_ascii_alphanumeric() => {
                return Some((i + 1 + k, i + 1 + k + c.len_utf8()));
            }
            // Either end of input, or an alphanumeric that cannot be the
            // trail: give the character back to the trailing position.
            _ => {}
        }
        if k <= min {
            return None;
        }
        k -= rest[..k].chars().next_back().map_or(1, char::len_utf8);
    }
}

/// `incomplete = re.compile('&[a-zA-Z#]')` anchored at `i`.
fn incomplete_match(s: &str, i: usize) -> Option<usize> {
    let c = s.get(i + 1..)?.chars().next()?;
    if c.is_ascii_alphabetic() || c == '#' {
        Some(i + 1 + c.len_utf8())
    } else {
        None
    }
}

// ---------------------------------------------------------------------------
// The tokenizer
// ---------------------------------------------------------------------------

/// `HTMLParser.CDATA_CONTENT_ELEMENTS` — RAWTEXT: the body is text, and
/// character references in it are NOT resolved.
///
/// 3.12.10 widened this from `("script", "style")`.
const RAWTEXT_ELEMENTS: [&str; 7] = [
    "script",
    "style",
    "xmp",
    "iframe",
    "noembed",
    "noframes",
    "plaintext",
];

/// `HTMLParser.RCDATA_CONTENT_ELEMENTS` — like RAWTEXT, except character
/// references ARE resolved (`_escapable`).
const RCDATA_ELEMENTS: [&str; 2] = ["textarea", "title"];

fn rawtext_elem(tag: &str) -> Option<&'static str> {
    RAWTEXT_ELEMENTS.iter().find(|e| **e == tag).copied()
}

fn rcdata_elem(tag: &str) -> Option<&'static str> {
    RCDATA_ELEMENTS.iter().find(|e| **e == tag).copied()
}

pub(crate) struct Tokenizer<'a, S: Sink> {
    raw: &'a str,
    convert_charrefs: bool,
    /// `HTMLParser._escapable`: false only inside a RAWTEXT element. Was
    /// `not self.cdata_elem` until 3.12.10 introduced RCDATA, where the body
    /// is opaque to markup but NOT to character references.
    escapable: bool,
    /// How much of `raw` a previous `goahead` consumed. CPython carries the
    /// unconsumed tail in `self.rawdata` and re-indexes from 0 on the next
    /// call; keeping the input whole and carrying the offset is equivalent,
    /// because every position-sensitive expression in `goahead`
    /// (`rfind('&', max(i, n-34))`, `find('>', i+1)`, `rawdata[i:]`, the tail
    /// flush) is a suffix operation.
    pos: usize,
    cdata_elem: Option<&'static str>,
    pub(crate) sink: S,
}

impl<'a, S: Sink> Tokenizer<'a, S> {
    pub(crate) fn new(raw: &'a str, convert_charrefs: bool, sink: S) -> Self {
        Tokenizer {
            raw,
            convert_charrefs,
            escapable: true,
            pos: 0,
            cdata_elem: None,
            sink,
        }
    }

    /// `HTMLParser.feed(value)` -- one `goahead(0)` over the whole input.
    ///
    /// This is all Django's `TruncateHTMLParser` ever does: its `feed`
    /// override `reset()`s first, so `close()` can never see a buffered tail
    /// and `"trailing &amp"` really does truncate to the empty string.
    pub(crate) fn feed(&mut self) -> SinkResult {
        self.goahead(false)
    }

    /// `feed(value)` **then** `close()`, which is how `MLStripper` is driven.
    ///
    /// The two passes are NOT collapsible into a single `goahead(1)`. Most of
    /// `goahead`'s `break`s re-enter the same branch on the next pass and
    /// break again at the same offset -- but the `&#`-bail arm consumes two
    /// characters *before* breaking, so the following pass resumes the LOOP
    /// over what comes after. That makes the behaviour genuinely
    /// pass-dependent: `strip_tags("&#;<b>x</b>")` is `"&#;x"` because pass 2
    /// parses the tag, while `strip_tags("&#xZZ</b>&#;<br />a <b")` KEEPS its
    /// `<br />` because that second bail happens during pass 2 and there is no
    /// pass 3 -- the remainder leaves through the tail flush as data. A
    /// one-pass port gets one of those right and the other wrong; the
    /// randomized differential in
    /// `python/tests/test_striptags_parity_2273.py` found both.
    pub(crate) fn feed_and_close(&mut self) -> SinkResult {
        self.goahead(false)?;
        self.goahead(true)
    }

    /// True while a run of text is being read verbatim rather than unescaped:
    /// CPython tests `self.convert_charrefs and not self.cdata_elem` at four
    /// separate points and they must not drift apart.
    fn converting(&self) -> bool {
        self.convert_charrefs && self.escapable
    }

    /// `set_cdata_mode(elem, escapable=...)`.
    fn set_cdata_mode(&mut self, elem: &'static str, escapable: bool) {
        self.cdata_elem = Some(elem);
        self.escapable = escapable;
    }

    /// `clear_cdata_mode()`.
    fn clear_cdata_mode(&mut self) {
        self.cdata_elem = None;
        self.escapable = true;
    }

    fn emit_text(&mut self, text: &str) -> SinkResult {
        if self.converting() {
            let un = unescape(text);
            self.sink.handle_data(&un)
        } else {
            self.sink.handle_data(text)
        }
    }

    /// `HTMLParser.goahead(end)`.
    fn goahead(&mut self, end: bool) -> SinkResult {
        let raw = self.raw;
        let b = raw.as_bytes();
        let n = raw.len();
        let mut i = self.pos;
        while i < n {
            let j;
            if self.converting() {
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
                                    break; // wait till we get all the text
                                }
                                j = n;
                            }
                            None => j = n,
                        }
                    }
                }
            } else {
                // `self.interesting.search(rawdata, i)`: in CDATA mode that is
                // the element's own end tag, otherwise `[&<]`.
                let found = match self.cdata_elem {
                    // `plaintext`'s `interesting` is `\Z`: nothing ends it.
                    Some("plaintext") => None,
                    Some(elem) => {
                        let close = find_cdata_close(raw, i, elem);
                        if self.escapable && !self.convert_charrefs {
                            // RCDATA with charrefs off: `&|</elem(?=...)`.
                            let amp = raw[i..].find('&').map(|o| i + o);
                            match (close, amp) {
                                (Some(a), Some(b)) => Some(a.min(b)),
                                (a, b) => a.or(b),
                            }
                        } else {
                            close
                        }
                    }
                    None => raw[i..].find(['&', '<']).map(|o| i + o),
                };
                match found {
                    Some(p) => j = p,
                    None => {
                        if self.cdata_elem.is_some() {
                            break;
                        }
                        j = n;
                    }
                }
            }
            if i < j {
                let run = &raw[i..j];
                self.emit_text(run)?;
            }
            i = j;
            if i == n {
                break;
            }
            if b[i] == b'<' {
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
                } else if i + 1 < n || end {
                    // `elif (i + 1) < n or end:` — a `<` that is the last
                    // character of the input is data once there is no more
                    // input coming.
                    self.sink.handle_data("<")?;
                    (i + 1) as i64
                } else {
                    break;
                };
                if k < 0 {
                    if !end {
                        break; // wait for the rest of the construct
                    }
                    self.finish_incomplete_construct(i)?;
                    i = n;
                } else {
                    i = k as usize;
                }
            } else if raw[i..].starts_with("&#") {
                match charref_match(raw, i) {
                    Some((body_end, match_end)) => {
                        self.sink.handle_charref(&raw[i + 2..body_end]);
                        // `if not startswith(';', k-1): k = k - 1`
                        i = if &raw[body_end..match_end] == ";" {
                            match_end
                        } else {
                            body_end
                        };
                        continue;
                    }
                    None => {
                        // "bail by consuming `&#`" -- and then STOP. The only
                        // arm that advances `i` before breaking, which is what
                        // makes `feed_and_close` two observably different
                        // passes rather than one.
                        if raw[i..].contains(';') {
                            self.sink.handle_data("&#")?;
                            i += 2;
                        }
                        break;
                    }
                }
            } else if b[i] == b'&' {
                if let Some((name_end, match_end)) = entityref_match(raw, i) {
                    self.sink.handle_entityref(&raw[i + 1..name_end]);
                    i = if &raw[name_end..match_end] == ";" {
                        match_end
                    } else {
                        name_end
                    };
                    continue;
                }
                if let Some(m_end) = incomplete_match(raw, i) {
                    // CPython computes a `k` here and never uses it.
                    if end && m_end == n {
                        i += 1;
                    }
                    break;
                } else if i + 1 < n {
                    self.sink.handle_data("&")?;
                    i += 1;
                } else {
                    break;
                }
            } else {
                unreachable!("the interesting scan only stops on `<` or `&`");
            }
        }
        // `if end and i < n:` — the `and not self.cdata_elem` guard was
        // removed with the rest of this path, so an unterminated `<script>`
        // body now reaches the sink as data instead of vanishing.
        if end && i < n {
            self.emit_text(&raw[i..n])?;
            i = n;
        }
        // `self.rawdata = rawdata[i:]`: where the next pass picks up.
        self.pos = i;
        Ok(())
    }

    /// The `if k < 0:` branch of `goahead(1)` — an unterminated construct at
    /// end of input.
    ///
    /// Until CPython 3.12.10 this scanned forward for a `>` or a `<` and
    /// emitted whatever it spanned as **data**. It now dispatches on what the
    /// construct was trying to be, hands the fragment to that construct's
    /// handler, and consumes the rest of the input either way.
    ///
    /// For both of Django's parsers every one of those handlers is the base
    /// class's no-op, so the whole branch reduces to "discard to end of
    /// input" — with one exception, a `</` that IS the end of the input, which
    /// is data. That is the whole of the change:
    ///
    /// ```text
    /// strip_tags("<b>x</b> <c")          "x <c"  ->  "x "
    /// strip_tags("<b>x</b><!-- open")    "x<!-- open" -> "x"
    /// ```
    ///
    /// Discarding is also the safer of the two: an unterminated,
    /// attacker-controlled construct is no longer re-emitted as page text —
    /// the same argument the depth guard in `strip_tags` makes.
    ///
    /// The CPython branches are enumerated here rather than collapsed to
    /// `Ok(())` so that a future [`Sink`] which does want comments has an
    /// obvious place to receive them.
    fn finish_incomplete_construct(&mut self, i: usize) -> SinkResult {
        let raw = self.raw;
        let n = raw.len();
        if i + 1 < n && (raw.as_bytes()[i + 1] as char).is_ascii_alphabetic() {
            // `starttagopen.match(...)` -> `pass`: an incomplete START TAG is
            // dropped whole. This is the `"<b>x</b> <c"` cell.
        } else if raw[i..].starts_with("</") {
            if i + 2 == n {
                // A `</` with nothing after it is the one shape that is data.
                self.sink.handle_data("</")?;
            } else {
                // `endtagopen` -> `pass`, otherwise a bogus comment; both are
                // dropped by every Django sink.
            }
        } else {
            // `<!--` -> handle_comment, `<![CDATA[` -> unknown_decl,
            // `<!doctype` -> handle_decl, `<!` -> bogus comment,
            // `<?` -> handle_pi. All no-ops here; see the `Sink` docs for why
            // those four handlers are not trait members.
        }
        Ok(())
    }

    fn parse_starttag(&mut self, i: usize) -> Result<i64, Stop> {
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
            match match_attribute(raw, k) {
                Some(e) if e > k => k = e,
                _ => break,
            }
        }
        let end: &str = raw[k..endpos].trim_matches(py_is_space);
        if end != ">" && end != "/>" {
            self.sink.handle_data(&raw[i..endpos])?;
            return Ok(endpos as i64);
        }
        if end.ends_with("/>") {
            self.sink.handle_startendtag(&tag, &starttag_text);
        } else {
            self.sink.handle_starttag(&tag, &starttag_text);
            if let Some(elem) = rawtext_elem(&tag) {
                self.set_cdata_mode(elem, false);
            } else if let Some(elem) = rcdata_elem(&tag) {
                self.set_cdata_mode(elem, true);
            }
        }
        Ok(endpos as i64)
    }

    /// `HTMLParser.parse_endtag`.
    ///
    /// Rewritten in 3.12.10 against "13.2.5.7 End tag open state". Two
    /// behaviours the old version had are gone: it no longer emits a
    /// mismatched end tag as DATA while in CDATA mode (the CDATA
    /// `interesting` regex means only the matching element's tag gets here),
    /// and it no longer scans past the tag name for a `>` — the tag must be
    /// terminated inside `locatetagend` or it is incomplete.
    fn parse_endtag(&mut self, i: usize) -> Result<i64, Stop> {
        let raw = self.raw;
        // Fast check: `if rawdata.find('>', i+2) < 0: return -1`.
        if raw.len() < i + 2 || !raw[i + 2..].contains('>') {
            return Ok(-1);
        }
        // `endtagopen.match(rawdata, i)` — `</` + letter.
        let is_named_end = char_at(raw, i + 2).is_some_and(|c| c.is_ascii_alphabetic());
        if !is_named_end {
            if raw[i..].starts_with("</>") {
                // "missing-end-tag-name" parser error: `</>` is ignored.
                return Ok((i + 3) as i64);
            }
            // parse_bogus_comment: `handle_comment` is a no-op for both sinks.
            return Ok(raw[i + 2..]
                .find('>')
                .map(|o| (i + 2 + o + 1) as i64)
                .unwrap_or(-1));
        }
        let j = locate_tag_end(raw, i + 2);
        if j == 0 || raw.as_bytes()[j - 1] != b'>' {
            return Ok(-1);
        }
        let tag = match tagfind(raw, i + 2) {
            Some((t, _)) => t,
            // `assert match` in CPython: unreachable, since `i+2` is a letter.
            None => return Ok(-1),
        };
        self.sink.handle_endtag(&tag);
        self.clear_cdata_mode();
        Ok(j as i64)
    }
}

// ---------------------------------------------------------------------------
// striptags (#2273)
// ---------------------------------------------------------------------------

/// `django.utils.html.MLStripper`.
///
/// `convert_charrefs=False`, so character references arrive as
/// `handle_entityref` / `handle_charref` rather than inside the text, and are
/// re-emitted **normalised with a trailing `;`** -- which is why
/// `striptags("&one two<b>x</b>")` is `"&one; twox"` and not `"&one twox"`.
#[derive(Default)]
struct MLStripper {
    fed: String,
}

impl Sink for MLStripper {
    fn handle_data(&mut self, data: &str) -> SinkResult {
        self.fed.push_str(data);
        Ok(())
    }

    fn handle_starttag(&mut self, _tag: &str, _starttag_text: &str) {}

    fn handle_endtag(&mut self, _tag: &str) {}

    fn handle_entityref(&mut self, name: &str) {
        self.fed.push('&');
        self.fed.push_str(name);
        self.fed.push(';');
    }

    fn handle_charref(&mut self, name: &str) {
        self.fed.push_str("&#");
        self.fed.push_str(name);
        self.fed.push(';');
    }
}

/// `django.utils.html.MAX_STRIP_TAGS_DEPTH`.
pub const MAX_STRIP_TAGS_DEPTH: usize = 50;

/// `django.utils.html.long_open_tag_without_closing_re`.
static LONG_OPEN_TAG_WITHOUT_CLOSING: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"<[a-zA-Z][^>]{1000,}").expect("literal pattern compiles"));

/// `django.utils.html._strip_once`: `feed(value)` then `close()`, which is one
/// `goahead` with `end` set.
fn strip_once(value: &str) -> String {
    let mut tok = Tokenizer::new(value, false, MLStripper::default());
    // `MLStripper::handle_data` is infallible, so the parse always runs to the
    // end; the `Result` exists for the truncating sink.
    let _ = tok.feed_and_close();
    tok.sink.fed
}

/// `django.utils.html.strip_tags`.
///
/// # The two refusals
///
/// Django raises `SuspiciousOperation` when the input looks like a strip-tags
/// DoS -- a `<tag` running 1000+ characters with no `>` and carrying 50+ `<`,
/// or an input that is still shedding tags after 50 passes. Both are quadratic
/// blowups: each pass is O(n) over an input that only shrinks by one nesting
/// level.
///
/// A djust filter has no channel to a 400: `SuspiciousOperation` is a Django
/// *request* concept, and raising from here would surface as a 500 on the
/// whole template render rather than as a refusal of one value. So the guard
/// is kept -- the work really is bounded at 50 passes -- and the refused value
/// renders as the **empty string**, with a `tracing::warn!`.
///
/// Empty is the only refusal value that is safe in every context this output
/// can reach. Returning the input, or the partially-stripped value, would emit
/// attacker-controlled markup that Django declined to emit; under
/// `{{ v|striptags|safe }}` that is an XSS surface Django does not have, and
/// "the tags were stripped" is precisely the claim this function could not
/// finish verifying.
pub fn strip_tags(value: &str) -> String {
    for m in LONG_OPEN_TAG_WITHOUT_CLOSING.find_iter(value) {
        if m.as_str().matches('<').count() >= MAX_STRIP_TAGS_DEPTH {
            tracing::warn!(
                "striptags refused a value with a {}-character unclosed tag \
                 carrying {} `<` (Django raises SuspiciousOperation here); \
                 rendering the empty string",
                m.as_str().len(),
                m.as_str().matches('<').count(),
            );
            return String::new();
        }
    }
    let mut value = value.to_string();
    let mut depth = 0usize;
    // Note: in the typical case this loop executes `strip_once` twice (the
    // second execution does not remove any more tags).
    while value.contains('<') && value.contains('>') {
        if depth >= MAX_STRIP_TAGS_DEPTH {
            tracing::warn!(
                "striptags refused a value still shedding tags after {} passes \
                 (Django raises SuspiciousOperation here); rendering the empty \
                 string",
                MAX_STRIP_TAGS_DEPTH,
            );
            return String::new();
        }
        let new_value = strip_once(&value);
        if value.matches('<').count() == new_value.matches('<').count() {
            // `strip_once` wasn't able to detect more tags.
            break;
        }
        value = new_value;
        depth += 1;
    }
    value
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

    // -----------------------------------------------------------------------
    // Mechanism 1: the `<`-is-data tokenization (#2273's headline defect)
    // -----------------------------------------------------------------------

    /// GATE-OFF for the tokenization: the branch that emits a lone `<` as
    /// data. Reverting it to the old `in_tag = true` scan reddens this.
    ///
    /// Every value here carries BOTH a `<` and a `>`, so `strip_tags`'
    /// while-loop condition holds and a full parse really runs -- a value with
    /// only one delimiter would return early and prove nothing about the
    /// tokenizer.
    #[test]
    fn strip_tags_keeps_a_lone_less_than_as_data() {
        assert_eq!(strip_tags("5 < 10 and 10 > 5"), "5 < 10 and 10 > 5");
        assert_eq!(strip_tags("price < 5 and > 3"), "price < 5 and > 3");
        // `<y<z>` parses as ONE start tag (`<z` reads as an attribute of
        // `y`), so the whole run goes; the surviving `<` is the one in
        // `a <b c>`'s neighbour case below.
        assert_eq!(strip_tags("x<y<z>"), "x");
        assert_eq!(strip_tags("a <b c> d < e"), "a  d < e");
        // `<` immediately before a digit, a space and a `-` are all data;
        // only `<` + letter / `/` / `!` / `?` opens a construct.
        assert_eq!(strip_tags("<1> <-> < >"), "<1> <-> < >");
    }

    /// The `<`-only and `>`-only halves, which never reach the parser because
    /// the while-loop needs both delimiters -- the cheap half of the fix.
    #[test]
    fn strip_tags_returns_a_single_delimiter_input_untouched() {
        assert_eq!(strip_tags("a < b"), "a < b");
        assert_eq!(strip_tags("a > b"), "a > b");
        assert_eq!(strip_tags("x<y<z"), "x<y<z");
        assert_eq!(strip_tags("<bx&y3.5word"), "<bx&y3.5word");
        assert_eq!(strip_tags("<"), "<");
        assert_eq!(strip_tags(">"), ">");
    }

    #[test]
    fn strip_tags_still_strips_well_formed_markup() {
        assert_eq!(strip_tags("a<b>c</b>d"), "acd");
        assert_eq!(strip_tags("<p class='x'>hi</p>"), "hi");
        assert_eq!(strip_tags("<br/>x<img src=y>"), "x");
        assert_eq!(strip_tags("<!-- c -->keep"), "keep");
        assert_eq!(strip_tags("<!DOCTYPE html><h1>T</h1>"), "T");
    }

    // -----------------------------------------------------------------------
    // Mechanism 2: the wrapper loop
    // -----------------------------------------------------------------------

    /// GATE-OFF for the loop: replace `strip_tags`' body with a single
    /// `strip_once` call and this reddens. One pass turns `<<b>script>` into
    /// `<script>`, which is exactly the tag the loop exists to catch.
    #[test]
    fn strip_tags_loops_until_the_tag_count_stops_falling() {
        assert_eq!(strip_once("<<b>script>"), "<script>");
        assert_eq!(strip_tags("<<b>script>"), "");

        assert_eq!(strip_once("<<i>b>x</<i>b>"), "<b>xb>");
        assert_eq!(strip_tags("<<i>b>x</<i>b>"), "xb>");
    }

    /// The loop terminates on the FIRST pass that removes nothing, so a value
    /// the parser cannot reduce is returned unchanged rather than re-parsed
    /// fifty times.
    #[test]
    fn strip_tags_stops_as_soon_as_a_pass_removes_no_tag() {
        assert_eq!(strip_tags("5 < 10 and 10 > 5"), "5 < 10 and 10 > 5");
        assert_eq!(strip_tags("a <b"), "a <b");
    }

    // -----------------------------------------------------------------------
    // Mechanism 3: the depth guard
    // -----------------------------------------------------------------------

    /// GATE-OFF for the pre-scan: drop the `find_iter` block and this reddens
    /// (the value comes back as itself, since a 1001-character unclosed tag
    /// sheds nothing).
    #[test]
    fn strip_tags_refuses_a_long_unclosed_tag_carrying_fifty_opens() {
        // `<[a-zA-Z][^>]{1000,}` with >= 50 `<` inside: Django raises
        // SuspiciousOperation, djust renders the empty string.
        // The match's OWN leading `<` counts toward the 50, so 49 inner
        // ones are enough -- the boundary is 49 refused / 48 allowed, and
        // both sides are pinned so an off-by-one in either direction fails.
        let hostile = format!("<a{}{}", "<".repeat(49), "y".repeat(1001));
        assert_eq!(strip_tags(&hostile), "");

        let benign = format!("<a{}{}", "<".repeat(48), "y".repeat(1001));
        assert_eq!(strip_tags(&benign), benign);
    }

    /// GATE-OFF for the loop cap: remove the `depth >= MAX_STRIP_TAGS_DEPTH`
    /// check and this reddens.
    ///
    /// The values carry `keepA`/`keepB` **specifically so that the uncapped
    /// fixpoint is not the empty string**. The first version of this test used
    /// bare `"<"*60 + "b>"*60`, whose uncapped fixpoint IS `""` -- so removing
    /// the cap changed the source and computed the identical answer, and the
    /// mutation survived. A surviving mutation is a question, not a pass
    /// (v1.1.1-2 retro); the answer here was "the input cannot tell the two
    /// apart", not "the cap is untested".
    #[test]
    fn strip_tags_refuses_an_input_still_shedding_tags_after_the_depth_cap() {
        // 51 nesting levels, one peeled per pass: Django allows exactly 50 and
        // raises on the 51st, and so does this.
        let deep = format!("keepA{}{}keepB", "<".repeat(51), "b>".repeat(51));
        assert_eq!(passes_to_fixpoint(&deep), MAX_STRIP_TAGS_DEPTH + 1);
        assert_eq!(strip_tags(&deep), "");
        // Uncapped, this value strips to `keepAkeepB` -- which is what makes
        // the `""` above evidence of the cap rather than of stripping.
        assert_eq!(strip_to_fixpoint(&deep), "keepAkeepB");

        // 50 passes is the last allowed depth, and comes back intact.
        let ok = format!("keepA{}{}keepB", "<".repeat(50), "b>".repeat(50));
        assert_eq!(passes_to_fixpoint(&ok), MAX_STRIP_TAGS_DEPTH);
        assert_eq!(strip_tags(&ok), "keepAkeepB");
    }

    /// `strip_tags`' loop with the cap and the pre-scan removed: the pair a
    /// cap test needs to tell "refused at the cap" from "finished under it".
    fn uncapped(value: &str) -> (usize, String) {
        let mut v = value.to_string();
        let mut n = 0usize;
        while v.contains('<') && v.contains('>') && n < 500 {
            let nv = strip_once(&v);
            if v.matches('<').count() == nv.matches('<').count() {
                break;
            }
            v = nv;
            n += 1;
        }
        (n, v)
    }

    fn passes_to_fixpoint(value: &str) -> usize {
        uncapped(value).0
    }

    fn strip_to_fixpoint(value: &str) -> String {
        uncapped(value).1
    }

    // -----------------------------------------------------------------------
    // convert_charrefs = false: the `;`-restoring branch (#2273 defect 2)
    // -----------------------------------------------------------------------

    /// GATE-OFF for `handle_entityref`: drop its `push(';')` and this reddens.
    #[test]
    fn strip_tags_normalises_a_named_reference_to_carry_its_semicolon() {
        assert_eq!(strip_tags("&one two<b>x</b>"), "&one; twox");
        assert_eq!(strip_tags("<i>&a b</i>"), "&a; b");
        // Already-terminated references are unchanged.
        assert_eq!(strip_tags("&amp;<b>x</b>"), "&amp;x");
        // Numeric references take the `handle_charref` arm.
        assert_eq!(strip_tags("&#65 <b>x</b>"), "&#65; x");
        assert_eq!(strip_tags("&#x41;<b>x</b>"), "&#x41;x");
    }

    /// GATE-OFF for `entityref_match`'s backtracking loop: replace it with the
    /// greedy scan's single trailing-character test and every case here
    /// reddens.
    ///
    /// `-` and `.` are in BOTH the name class `[-.a-zA-Z0-9]` and the trailing
    /// class `[^a-zA-Z0-9]`, so a greedy scan eats the character the trail
    /// needed and `re`'s backtracking is load-bearing. Found by the randomized
    /// differential, not by inspection.
    #[test]
    fn entityref_backtracks_when_the_greedy_name_eats_its_own_trail() {
        assert_eq!(strip_tags("<b/>&amp-"), "&amp;-");
        assert_eq!(strip_tags("<b/>&amp."), "&amp;.");
        assert_eq!(strip_tags("<b/>x&one3.5"), "x&one3;.5");
        assert_eq!(strip_tags("<b/>&a-b"), "&a;-b");
        assert_eq!(strip_tags("<b/>&nbsp-x"), "&nbsp;-x");
        // Not an entity at all: the name must START with a letter, so the
        // `&` falls through to `handle_data("&")`.
        assert_eq!(strip_tags("<b/>&9a<i>x</i>"), "&9ax");
        assert_eq!(strip_tags("<b/>&<i>x</i>"), "&x");
        // The trail can be the `<` that opens the NEXT tag, in which case the
        // greedy name is already correct and nothing is given back.
        assert_eq!(strip_tags("<i>&amp--</i>"), "&amp--;");
    }

    /// GATE-OFF for `finish_incomplete_construct`: restore the pre-3.12.10
    /// "scan to the next `>` or `<` and emit the span as data" recovery and
    /// every discard here reddens.
    ///
    /// `MLStripper` is fed AND closed, so `goahead(1)` runs and an
    /// unterminated construct at end of input is resolved rather than left
    /// buffered. Current CPython DISCARDS it; the `</`-at-exact-EOF row is the
    /// single shape that is still data, and it is here so the discard cannot
    /// be implemented as an unconditional `Ok(())`.
    ///
    /// Every expectation below is identical on CPython 3.12.10+, 3.13 and
    /// 3.14 — deliberately, since these run unconditionally on every runner.
    /// The shapes 3.13 and 3.14 disagree on are `&` / `&#` at END OF INPUT,
    /// and they are in the version-dependent fixture instead. Checked
    /// mechanically by `scripts/check-striptags-version-stability.py`, which
    /// re-runs every literal in this module through each supported CPython.
    #[test]
    fn strip_tags_discards_an_incomplete_construct_at_end_of_input() {
        // An unterminated START tag takes the whole tail with it.
        assert_eq!(strip_tags("<b>x</b> <c"), "x ");
        assert_eq!(strip_tags("<b>x</b></d"), "x");
        assert_eq!(strip_tags("<b x=\">keep"), "");
        // ...as do an unterminated comment, declaration, PI and CDATA section.
        assert_eq!(strip_tags("<b>x</b><!-- open"), "x");
        assert_eq!(strip_tags("<b>x</b><!"), "x");
        assert_eq!(strip_tags("<b>x</b><?"), "x");
        assert_eq!(strip_tags("<b>x</b><![CDATA["), "x");
        // The two shapes that are still DATA: a bare `<` at the very end, and
        // a `</` that is exactly the end of the input.
        assert_eq!(strip_tags("<b>x</b><"), "x<");
        assert_eq!(strip_tags("<b>x</b></"), "x</");
        // A CDATA body with no closing tag now reaches the sink, because the
        // tail flush lost its `and not self.cdata_elem` guard.
        assert_eq!(strip_tags("<script>abc"), "abc");
        assert_eq!(strip_tags("<style>q{}"), "q{}");
    }

    /// GATE-OFF for the comment-close rewrite: `commentclose` went from
    /// `--\s*>` to `--!?>`, and `commentabruptclose = -?>` was added, in the
    /// 3.12.10 spec alignment. Removing the `!` or the abrupt fallback
    /// reddens a row here.
    ///
    /// Added because the gate-off found both mechanisms unreachable from the
    /// suite: a surviving mutation is a question, not a pass.
    #[test]
    fn comment_close_follows_the_html5_shape() {
        // `--!>` closes a comment...
        assert_eq!(strip_tags("<!--x--!>keep<b>y</b>"), "keepy");
        // ...and so do the two abrupt forms, which `--\s*>` could not match.
        assert_eq!(strip_tags("<!-->keep<b>y</b>"), "keepy");
        assert_eq!(strip_tags("<!--->keep<b>y</b>"), "keepy");
        // The ordinary close still works, so the rows above are not merely
        // "a comment is dropped however it ends".
        assert_eq!(strip_tags("<!---->keep<b>y</b>"), "keepy");
        // The converse, and the sharper half: whitespace between `--` and `>`
        // NO LONGER closes, so this comment swallows the rest of the input.
        assert_eq!(strip_tags("<!--x-- >keep<b>y</b>"), "");
    }

    /// GATE-OFF for the `<![CDATA[` arm: a CDATA section runs to `]]>`, so a
    /// `>` inside it does not end it. Falling back to the generic "run to the
    /// next `>`" reddens the first row.
    #[test]
    fn a_cdata_section_runs_to_its_close_not_to_the_next_gt() {
        assert_eq!(strip_tags("<![CDATA[a>b]]>keep<i>z</i>"), "keepz");
        assert_eq!(strip_tags("<![CDATA[a]]>keep"), "keep");
    }

    /// GATE-OFF for the CDATA-close lookahead: `</{elem}(?=[\t\n\r\f />])`
    /// replaced `</\s*{elem}\s*>`, so an end tag carrying attributes — or a
    /// `/` — now leaves RAWTEXT mode where before it did not, and the rest of
    /// the document stayed swallowed inside the element.
    #[test]
    fn rawtext_closes_on_the_tag_name_not_on_a_following_gt() {
        // Asserted on `strip_once`, not `strip_tags`: this is a SINGLE-PASS
        // property, and the re-strip loop repairs it. Gating the lookahead
        // off leaves `strip_tags` green here while `strip_once` returns
        // `"x</style foo>keep<b>y</b>"` — two mechanisms shadowing each
        // other, which the gate-off caught.
        assert_eq!(strip_once("<style>x</style foo>keep<b>y</b>"), "xkeepy");
        assert_eq!(strip_once("<style>x</style/>keep<b>y</b>"), "xkeepy");
        assert_eq!(strip_once("<script>a</script bar>keep"), "akeep");
        // The filter-level answer is the same, so the rows above are not
        // asserting some `strip_once`-only artefact.
        assert_eq!(strip_tags("<style>x</style foo>keep<b>y</b>"), "xkeepy");
    }

    /// GATE-OFF for the `<![` branch of `parse_html_declaration`: restore the
    /// `parse_marked_section` port's `-1` and these reddens.
    ///
    /// That port refused unrecognised section keywords, which under
    /// `goahead(1)` discarded the rest of the input; CPython 3.12.10 replaced
    /// `parse_marked_section` with "run to the next `>`", so parsing resumes
    /// after the section. It also removes the `AssertionError` that Django
    /// used to raise on these values — 496 cells of the #2273 differential
    /// that had no reference answer at all.
    #[test]
    fn an_unrecognised_marked_section_runs_to_the_next_gt() {
        assert_eq!(strip_tags("x&&&one;<![</p>"), "x&&&one;");
        assert_eq!(strip_tags("<![foo[x]]>"), "");
        assert_eq!(strip_tags("<!--a--!><![</p>"), "");
        // A real CDATA section still runs to `]]>`, so what follows survives.
        assert_eq!(strip_tags("<![CDATA[y]]>z"), "z");
    }

    /// GATE-OFF for the `&#`-bail arm's `continue`: turn it back into
    /// CPython's literal `break` and every case here reddens, because the
    /// markup AFTER the bailed-out `&#` stops being parsed and comes back out
    /// through the tail flush verbatim.
    ///
    /// The randomized differential found this: `feed()` + `close()` is two
    /// `goahead` passes, and only this branch's `break` leaves the second one
    /// real work to do.
    #[test]
    fn strip_tags_resumes_parsing_after_a_bailed_out_numeric_reference() {
        assert_eq!(strip_tags("&#;<b>x</b>"), "&#;x");
        assert_eq!(strip_tags("&#;<b\n>"), "&#;");
        assert_eq!(strip_tags("&#x</ b>&z;"), "&#x&z;");
        assert_eq!(strip_tags("&#;<p><br />&#65;=&&"), "&#;&#65;=&&");
        // No `;` anywhere after the `&#`, so the parse really does stop and
        // the tail is flushed whole -- the arm's OTHER branch.
        assert_eq!(strip_tags("<b>y</b>&#"), "y&#");
    }

    // -----------------------------------------------------------------------
    // The tokenizer is shared: the truncators must be unmoved by all of this
    // -----------------------------------------------------------------------

    /// The convergence claim, asserted rather than trusted: the same
    /// `Tokenizer` drives both sinks, and the truncating one still sees tags
    /// as tags with `convert_charrefs = true` / `end = false`.
    #[test]
    fn the_same_tokenizer_drives_the_truncating_sink() {
        use crate::truncate::{html_chars, html_words};
        assert_eq!(
            html_chars("<p>Hello world</p>", 5, Some("...")),
            "<p>He...</p>"
        );
        assert_eq!(html_words("<p>a b c d</p>", 2, Some("…")), "<p>a b…</p>");
        // `end = false`: the tail is DISCARDED here, the opposite of the
        // stripper's behaviour above.
        assert_eq!(html_chars("trailing &amp", 40, None), "");
    }
}
