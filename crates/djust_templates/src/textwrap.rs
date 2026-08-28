//! CPython's `textwrap.TextWrapper` for the flag set `django.utils.text.wrap`
//! uses, plus that function's own splitlines / whitespace-restore /
//! trailing-newline wrapper. Reached from the `wordwrap` filter (#2293).
//!
//! What this replaced was a greedy re-joiner — `split_whitespace()` then join on
//! single spaces — which is not Django's algorithm but a different one that
//! happens to agree on `"one two three"`. It flattened every existing line
//! break into a space, collapsed runs of spaces, dropped leading indentation,
//! measured widths in BYTES, and returned the text unchanged at `width=0` where
//! Django raises.
//!
//! ## Why the byte-width defect could not be fixed on its own
//!
//! #2279 swept `word.len()` → `word.chars().count()` on its own and measured it
//! fixing 21 differential cells and REGRESSING 6 — every regression a string
//! containing `U+2028`. Django's `splitlines()` breaks a line there; the
//! re-joiner turned it into a space, and the byte overcount had been putting a
//! break at that position by accident. Two bugs cancelling, so removing one
//! alone made the output worse. Both move here.
//!
//! ## Three whitespace sets, none of them the same
//!
//! The three are pairwise different and every known defect lived in a gap
//! between two of them, so each is named rather than spelled inline:
//!
//! | set | where | contains `\t` | `\u{1f}` | `\u{a0}` | `U+2028` |
//! |---|---|---|---|---|---|
//! | `str.splitlines()` | [`py_is_line_break`] | no | no | no | **yes** |
//! | `textwrap._whitespace` | [`is_textwrap_space`] | **yes** | no | no | no |
//! | `str.isspace()` | [`py_is_space`] | **yes** | **yes** | **yes** | **yes** |
//!
//! The first decides what a line is, the second where a chunk boundary is, and
//! the third which chunks `drop_whitespace` discards. A chunk of `\u{a0}` is
//! therefore a *word* the splitter never breaks on that `drop_whitespace`
//! nonetheless throws away, and reproducing that is the whole reason
//! `is_blank_chunk` is not `is_textwrap_space`.

use std::fmt;

use crate::pprint::py_splitlines;
use crate::truncate::py_is_space;

/// The flags `django.utils.text.wrap` constructs its `TextWrapper` with, and
/// the defaults it leaves alone. Not implemented here because Django never
/// turns them on: `initial_indent` / `subsequent_indent` (both `""`, so the
/// per-line `width = self.width - len(indent)` is a no-op),
/// `fix_sentence_endings` (`False`), `max_lines` (`None`, which makes
/// `_wrap_chunks`'s placeholder branch and its `cur_len` bookkeeping after
/// `_handle_long_word` unreachable), and `break_on_hyphens` (`False`, which
/// selects `wordsep_simple_re` and retires the lookbehind regex entirely).
///
/// Django's own `max_width = min(width + 1 if line.endswith("\n") else width,
/// width)` is likewise dead arithmetic — unconditionally `width` — and is not
/// reproduced.
const TABSIZE: usize = 8;

/// `textwrap._whitespace` — SIX ASCII characters, and not one more.
///
/// Not `char::is_whitespace` and not [`py_is_space`]: `\u{a0}` and `\u{1f}` are
/// whitespace to `str.strip()` and are NOT chunk boundaries to `textwrap`.
fn is_textwrap_space(c: char) -> bool {
    matches!(c, '\t' | '\n' | '\u{0b}' | '\u{0c}' | '\r' | ' ')
}

/// The `ValueError` `textwrap._wrap_chunks` raises for a non-positive width,
/// message included, so the parity is checkable and not merely asserted.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct InvalidWidth(pub i64);

impl fmt::Display for InvalidWidth {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "invalid width {} (must be > 0)", self.0)
    }
}

/// Python's `str.expandtabs(tabsize)`.
///
/// The column counts CODE POINTS, and `tabsize == 0` deletes the tab rather
/// than emitting anything — both of which are Python's, not an approximation.
fn py_expandtabs(s: &str, tabsize: usize) -> String {
    let mut out = String::with_capacity(s.len());
    let mut column = 0usize;
    for c in s.chars() {
        match c {
            '\t' => {
                if tabsize > 0 {
                    let incr = tabsize - (column % tabsize);
                    column += incr;
                    for _ in 0..incr {
                        out.push(' ');
                    }
                }
            }
            '\n' | '\r' => {
                out.push(c);
                column = 0;
            }
            _ => {
                out.push(c);
                column += 1;
            }
        }
    }
    out
}

/// `TextWrapper._split` with `break_on_hyphens=False`.
///
/// That branch is `wordsep_simple_re.split(text)` with the empty pieces
/// dropped, and `re.split` on a single capturing group of `[ws]+` returns
/// exactly the maximal alternating runs — so this is the same partition without
/// the regex. Whitespace chunks therefore never adjoin, which is why the
/// leading-whitespace drop in [`wrap_chunks`] can remove ONE chunk and be
/// done.
fn split_chunks(text: &str) -> Vec<&str> {
    let mut chunks: Vec<&str> = Vec::new();
    let mut start = 0usize;
    let mut run_is_space: Option<bool> = None;
    for (i, c) in text.char_indices() {
        let is_space = is_textwrap_space(c);
        match run_is_space {
            Some(prev) if prev != is_space => {
                chunks.push(&text[start..i]);
                start = i;
                run_is_space = Some(is_space);
            }
            Some(_) => {}
            None => run_is_space = Some(is_space),
        }
    }
    if start < text.len() {
        chunks.push(&text[start..]);
    }
    chunks
}

/// `chunk.strip() == ''`, the test `drop_whitespace` actually applies.
///
/// `str.strip()` uses Python's `str.isspace()` set, which is WIDER than the set
/// the splitter breaks on — see the module table. Using `is_textwrap_space`
/// here would leave `\u{a0}` and `\u{1f}` chunks in the output that Django
/// drops.
fn is_blank_chunk(chunk: &str) -> bool {
    chunk.chars().all(py_is_space)
}

fn char_len(s: &str) -> usize {
    s.chars().count()
}

/// `TextWrapper._wrap_chunks` for Django's flags.
///
/// Every outer iteration removes at least one chunk, so it terminates: the
/// inner loop pops whatever fits, and if nothing fits then the head chunk is
/// longer than `width`, which is exactly the condition under which
/// `_handle_long_word` takes it (`cur_line` is empty in that case, by
/// construction — nothing fit).
fn wrap_chunks(mut chunks: Vec<&str>, width: usize) -> Vec<String> {
    let mut lines: Vec<String> = Vec::new();
    // Reversed so the head of the text is the top of a stack, as in CPython.
    chunks.reverse();

    while !chunks.is_empty() {
        let mut cur_line: Vec<&str> = Vec::new();
        let mut cur_len = 0usize;

        // "First chunk on line is whitespace -- drop it, unless this is the
        // very beginning of the text (ie. no lines started yet)." That trailing
        // clause is what PRESERVES a line's leading indentation, which the
        // re-joiner dropped.
        if !lines.is_empty() && is_blank_chunk(chunks[chunks.len() - 1]) {
            chunks.pop();
        }

        while let Some(&head) = chunks.last() {
            let l = char_len(head);
            if cur_len + l <= width {
                cur_line.push(head);
                chunks.pop();
                cur_len += l;
            } else {
                break;
            }
        }

        // `_handle_long_word` with `break_long_words=False`: keep the word
        // intact, and take it only when the line is still empty — that
        // minimises how much the width constraint is violated. When the line
        // already has text, do nothing; the next pass finds `cur_len == 0` and
        // devotes a whole line to it.
        if let Some(&head) = chunks.last() {
            if char_len(head) > width && cur_line.is_empty() {
                cur_line.push(head);
                chunks.pop();
            }
        }

        // "If the last chunk on this line is all whitespace, drop it."
        if let Some(&last) = cur_line.last() {
            if is_blank_chunk(last) {
                cur_line.pop();
            }
        }

        if !cur_line.is_empty() {
            lines.push(cur_line.concat());
        }
    }
    lines
}

/// `django.utils.text.wrap`, which is what the `wordwrap` filter calls.
///
/// `width` is `i64` rather than `usize` because Django's is `int(arg)` and a
/// NEGATIVE width is a reachable input that has to raise, not wrap around.
///
/// The `width <= 0` guard sits INSIDE the per-line loop on purpose:
/// `_wrap_chunks` raises before it looks at its chunks, but it is only reached
/// once per line, so a `text` whose `splitlines()` is empty — the empty string,
/// and only the empty string — never raises. `wrap("", 0)` returns `""` in
/// Django; `wrap("\n", 0)` raises.
pub fn wrap(text: &str, width: i64) -> Result<String, InvalidWidth> {
    let mut result: Vec<String> = Vec::new();
    for line in py_splitlines(text) {
        if width <= 0 {
            return Err(InvalidWidth(width));
        }
        let expanded = py_expandtabs(&line, TABSIZE);
        let wrapped = wrap_chunks(split_chunks(&expanded), width as usize);
        if wrapped.is_empty() {
            // "If `line` contains only whitespaces that are dropped, restore
            // it." Note it restores the ORIGINAL line, not the tab-expanded
            // one.
            result.push(line);
        } else {
            result.extend(wrapped);
        }
    }
    if text.ends_with('\n') {
        // Only a literal `\n`. `text.endswith("\n")` does not answer the
        // `splitlines` question, so a text ending in `U+2028` does NOT get the
        // trailing empty line even though it ended a line.
        result.push(String::new());
    }
    Ok(result.join("\n"))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The three-set table in the module docstring, as an executable claim.
    #[test]
    fn the_three_whitespace_sets_are_pairwise_different() {
        // `\t` splits chunks and is `isspace`, but is not a line break.
        assert!(is_textwrap_space('\t'));
        assert!(py_is_space('\t'));
        assert!(!crate::pprint::py_is_line_break('\t'));
        // `\u{1f}` is `isspace` only.
        assert!(!is_textwrap_space('\u{1f}'));
        assert!(py_is_space('\u{1f}'));
        assert!(!crate::pprint::py_is_line_break('\u{1f}'));
        // `\u{a0}` is `isspace` only.
        assert!(!is_textwrap_space('\u{a0}'));
        assert!(py_is_space('\u{a0}'));
        assert!(!crate::pprint::py_is_line_break('\u{a0}'));
        // `U+2028` is a line break and `isspace`, but not a chunk boundary.
        assert!(!is_textwrap_space('\u{2028}'));
        assert!(py_is_space('\u{2028}'));
        assert!(crate::pprint::py_is_line_break('\u{2028}'));
    }

    #[test]
    fn existing_line_breaks_are_preserved_not_flattened() {
        // The re-joiner rendered this as "a b" — it had no notion of a line.
        assert_eq!(wrap("a\nb", 10).unwrap(), "a\nb");
        // ... and `U+2028` is a line break to Django too. This is the half of
        // the cancelling pair the byte-width fix could not have on its own.
        assert_eq!(wrap("f\u{2028}\u{5b57}", 10).unwrap(), "f\n\u{5b57}");
    }

    #[test]
    fn width_is_counted_in_characters_not_bytes() {
        // Four 3-byte characters and a width of 4: one line if the count is in
        // characters, four lines if it is in bytes.
        assert_eq!(wrap("字 日 本 語", 4).unwrap(), "字 日\n本 語");
    }

    #[test]
    fn interior_whitespace_and_indentation_survive() {
        assert_eq!(wrap("a  b", 10).unwrap(), "a  b");
        assert_eq!(wrap("    indented", 40).unwrap(), "    indented");
        // Leading whitespace is dropped on a CONTINUATION line only.
        assert_eq!(wrap("  aaa bbb", 5).unwrap(), "  aaa\nbbb");
    }

    #[test]
    fn a_whitespace_only_line_is_restored_verbatim() {
        assert_eq!(wrap("   ", 5).unwrap(), "   ");
        assert_eq!(wrap("a\n   \nb", 5).unwrap(), "a\n   \nb");
    }

    #[test]
    fn a_trailing_newline_is_preserved_but_a_u2028_is_not() {
        assert_eq!(wrap("a\n", 5).unwrap(), "a\n");
        assert_eq!(wrap("a\u{2028}", 5).unwrap(), "a");
    }

    #[test]
    fn a_long_word_is_never_broken() {
        assert_eq!(wrap("aaaaaaaa b", 3).unwrap(), "aaaaaaaa\nb");
    }

    #[test]
    fn tabs_expand_to_the_next_multiple_of_eight() {
        assert_eq!(py_expandtabs("a\tb", TABSIZE), "a       b");
        assert_eq!(py_expandtabs("\t", TABSIZE), "        ");
        assert_eq!(py_expandtabs("abcdefgh\tx", TABSIZE), "abcdefgh        x");
    }

    #[test]
    fn a_non_positive_width_raises_with_djangos_message() {
        assert_eq!(
            wrap("a", 0).unwrap_err().to_string(),
            "invalid width 0 (must be > 0)"
        );
        assert_eq!(
            wrap("a", -3).unwrap_err().to_string(),
            "invalid width -3 (must be > 0)"
        );
        // ... except for the empty string, whose `splitlines()` is empty, so
        // `_wrap_chunks` is never reached.
        assert_eq!(wrap("", 0).unwrap(), "");
        assert_eq!(wrap("\n", 0), Err(InvalidWidth(0)));
    }

    #[test]
    fn an_isspace_only_chunk_is_dropped_though_it_is_not_a_chunk_boundary() {
        // `\u{a0}` is a WORD to the splitter and whitespace to `drop_whitespace`.
        // The chunks are `["a", " ", "\u{a0}", " ", "b"]`: the first three fit in
        // three columns, and the trailing-whitespace drop removes ONE chunk — the
        // `\u{a0}` — so the ordinary space before it SURVIVES on the line. Django
        // agrees, and the expectation is its live answer, not a derivation:
        // `wrap("a \xa0 b", 3) == "a \nb"`.
        assert_eq!(wrap("a \u{a0} b", 3).unwrap(), "a \nb");
        // ... and a line that is nothing but one is restored whole, because
        // `wrap()` returned no lines for it at all.
        assert_eq!(wrap("\u{a0}", 3).unwrap(), "\u{a0}");
    }

    #[test]
    fn hyphens_are_never_broken_on() {
        // `break_on_hyphens=False` — Django's flag. `wordsep_re` would have
        // split "goof-ball" into "goof-" and "ball".
        assert_eq!(wrap("goof-ball", 5).unwrap(), "goof-ball");
    }
}
