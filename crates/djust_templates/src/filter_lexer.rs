//! Django's filter-expression LEXER rule (#2409).
//!
//! # The defect this closes
//!
//! Django's variable lexer matches **at most one** argument per filter.
//! The parser validates that match before rejecting leftover text. See `filter_raw_string` in
//! `django/template/base.py`:
//!
//! ```python
//! filter_raw_string = r"""
//! ^(?P<constant>%(constant)s)|
//! ^(?P<var>[%(var_chars)s]+|%(num)s)|
//!  (?:\s*%(filter_sep)s\s*
//!      (?P<filter_name>\w+)
//!          (?:%(arg_sep)s
//!              (?:
//!               (?P<constant_arg>%(constant)s)|
//!               (?P<var_arg>[%(var_chars)s]+|%(num)s)
//!              )
//!          )?
//!  )"""
//! ```
//!
//! `FilterExpression.__init__` runs `filter_re.finditer` over the token and
//! requires the matches to TILE it — each match must start where the previous
//! one ended, and the last must end at the end of the token. Anything left
//! over is `TemplateSyntaxError: Could not parse the remainder`. A second
//! `:arg` matches nothing, so it is exactly that remainder.
//!
//! djust split the expression on the FIRST colon and kept everything after it
//! as one argument, so `{{ p|cut:"a":"b" }}` handed `cut` the argument
//! `"a":"b"` — quotes and all — found no such substring, and rendered the
//! input unchanged. A wrong page, silently, from a template Django refuses to
//! compile.
//!
//! # This is not the arity check
//!
//! [`crate::filter_arity`] (#2400) reads each filter's own signature and
//! refuses a wrong COUNT. This is one layer up and applies to EVERY filter,
//! including ones that legitimately take one argument — which is why `upper`
//! agreed before this fix (djust folded two arguments into one and the arity
//! check refused that one for a filter taking none) while `cut`, `default`
//! and `truncatewords` all diverged.
//!
//! # Quoting, which is the whole difficulty
//!
//! A naive "refuse a second colon" is wrong: `{{ p|date:"H:i" }}` and
//! `{{ p|cut:":" }}` both parse on both engines and must keep doing so.
//! Django's alternatives are anchored on a QUOTED-STRING grammar, so the scan
//! has to respect quoting rather than count separators. The same blindness
//! sat one character over on the pipe: `{{ p|cut:"a|b" }}` was split into two
//! filters and raised `Unknown filter`, where Django renders normally.
//!
//! # One rule, two call sites
//!
//! Both of djust's filter-expression splitters — `parser::parse_token` for
//! `{{ … }}` and `renderer::get_value_safe` for a TAG operand
//! (`{% if p|… %}`, `{% for x in p|… %}`, `{% with v=p|… %}`) — were
//! independently quote-blind and independently accepted a second argument.
//! They call this module rather than carrying a copy each (#1646); a
//! `{{ }}`-only fix would have left three tags over-permissive, which is what
//! the measurement in #2409 shows.

use djust_core::{DjangoRustError, Result};

/// Where a quoted string starting at `bytes[start]` ends, or `None` if it is
/// never closed.
///
/// Django's constant grammar is `"[^"\\]*(?:\\.[^"\\]*)*"` (and the single-
/// quoted twin), i.e. a backslash escapes the following character. Returns the
/// index one past the closing quote.
fn quoted_string_end(bytes: &[u8], start: usize) -> Option<usize> {
    let quote = bytes[start];
    let mut i = start + 1;
    while i < bytes.len() {
        match bytes[i] {
            b'\\' => i += 2,
            c if c == quote => return Some(i + 1),
            _ => i += 1,
        }
    }
    None
}

/// Split *token* on the `|` characters that are OUTSIDE a quoted string.
///
/// The plain `str::split('|')` this replaces treated `{{ p|cut:"a|b" }}` as
/// two filters. An UNTERMINATED quote falls back to treating the rest of the
/// token as ordinary text, so a malformed template still reaches the existing
/// error paths rather than silently losing its tail.
pub fn split_pipes(token: &str) -> Vec<&str> {
    let bytes = token.as_bytes();
    let mut parts = Vec::new();
    let mut segment_start = 0usize;
    let mut i = 0usize;
    while i < bytes.len() {
        match bytes[i] {
            b'"' | b'\'' => match quoted_string_end(bytes, i) {
                Some(end) => i = end,
                None => break,
            },
            b'|' => {
                parts.push(&token[segment_start..i]);
                segment_start = i + 1;
                i += 1;
            }
            _ => i += 1,
        }
    }
    parts.push(&token[segment_start..]);
    parts
}

/// Does *token* contain a `|` outside a quoted string?
///
/// The cheap pre-test `renderer::get_value_safe` uses before paying for a
/// split. `expr.contains('|')` was the quote-blind version.
pub fn has_unquoted_pipe(token: &str) -> bool {
    split_pipes(token).len() > 1
}

/// The end of one Django filter-ARGUMENT alternative starting at `bytes[0]`,
/// or `None` if none of them matches.
///
/// The three alternatives, in the regex's own order:
///
/// * `constant_arg` — a quoted string, optionally wrapped in `_( … )`;
/// * `var_arg` — `[\w.]+`;
/// * `num` — `[-+.]?\d[\d.e]*`.
///
/// These three alternatives are also Django's `filter_re` HEAD alternatives
/// (`^constant | ^var | ^num`), so `argument_end(head) == Some(head.len())` is
/// exactly the "the head atom tiles the whole token" test `FilterExpression`
/// applies before it reports a remainder — which is why `parser::url` and
/// `parser::validate_tag_operand` (#2580) both reuse this rather than
/// restating the constant/var/num grammar (#1646, #2577).
pub(crate) fn argument_end(arg: &str) -> Option<usize> {
    let bytes = arg.as_bytes();
    if bytes.is_empty() {
        return None;
    }
    // `_("x")` / `_('x')` — the i18n-wrapped constant.
    if arg.starts_with("_(") {
        let inner = 2;
        if inner < bytes.len() && (bytes[inner] == b'"' || bytes[inner] == b'\'') {
            if let Some(end) = quoted_string_end(bytes, inner) {
                if end < bytes.len() && bytes[end] == b')' {
                    return Some(end + 1);
                }
            }
        }
        return None;
    }
    if bytes[0] == b'"' || bytes[0] == b'\'' {
        return quoted_string_end(bytes, 0);
    }
    // `var_chars` is `\w.` — Python's `\w`, which is UNICODE word characters,
    // not ASCII. `char::is_alphanumeric() || '_'` is the same set for the
    // purpose of "does this run consume the whole argument".
    let var_len: usize = arg
        .chars()
        .take_while(|c| c.is_alphanumeric() || *c == '_' || *c == '.')
        .map(char::len_utf8)
        .sum();
    if var_len > 0 {
        return Some(var_len);
    }
    // `num` — `[-+.]?\d[\d.e]*`. Reached only when the `var` run is empty,
    // which for a number means it starts with `-` or `+`.
    let mut chars = arg.char_indices().peekable();
    let mut end = 0usize;
    if let Some(&(_, c)) = chars.peek() {
        if c == '-' || c == '+' || c == '.' {
            chars.next();
            end = c.len_utf8();
        }
    }
    match chars.next() {
        Some((_, c)) if c.is_ascii_digit() => end += c.len_utf8(),
        _ => return None,
    }
    for (_, c) in chars {
        if c.is_ascii_digit() || c == '.' || c == 'e' {
            end += c.len_utf8();
        } else {
            break;
        }
    }
    Some(end)
}

/// Split one filter spec into `(name, Option<raw argument>)`, applying
/// Django's lexer rule.
///
/// `spec` is one `|`-delimited segment (already trimmed); `token` is the whole
/// expression, and is used only for the error message, which quotes Django's
/// own wording.
///
/// Refuses anything left over after the single argument — a second `:arg`
/// above all, which is the shape #2409 is about. The argument's surrounding
/// quotes are PRESERVED in the returned slice: `parser::strip_filter_arg_quotes`
/// removes them at render time so the dep-tracking extractor (#787) can still
/// tell a literal from a bare identifier.
pub fn split_filter_spec<'a>(spec: &'a str, token: &str) -> Result<(&'a str, Option<&'a str>)> {
    let spec = spec.trim();
    let (name, arg, consumed) = scan_filter_spec(spec);
    if name.is_empty() || consumed != spec.len() {
        return Err(DjangoRustError::TemplateError(format!(
            "Could not parse the remainder: '{}' from '{}'",
            &spec[consumed..],
            token
        )));
    }
    Ok((name, arg))
}

/// Read one filter match without rejecting its tail. The parser validates
/// the matched argument/name/arity before Django reports an unmatched suffix.
pub(crate) fn scan_filter_spec(spec: &str) -> (&str, Option<&str>, usize) {
    let name_end: usize = spec
        .chars()
        .take_while(|c| c.is_alphanumeric() || *c == '_')
        .map(char::len_utf8)
        .sum();
    let mut consumed = name_end;
    let mut arg = None;
    if let Some(rest) = spec[name_end..].strip_prefix(':') {
        if let Some(end) = argument_end(rest) {
            arg = Some(&rest[..end]);
            consumed += 1 + end;
        }
    }
    (&spec[..name_end], arg, consumed)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pipes_outside_quotes_split() {
        assert_eq!(split_pipes("p|upper|lower"), vec!["p", "upper", "lower"]);
    }

    #[test]
    fn a_pipe_inside_a_quoted_argument_does_not_split() {
        assert_eq!(split_pipes(r#"p|cut:"a|b""#), vec!["p", r#"cut:"a|b""#]);
        assert_eq!(split_pipes("p|cut:'a|b'"), vec!["p", "cut:'a|b'"]);
    }

    #[test]
    fn an_escaped_quote_does_not_end_the_string() {
        assert_eq!(split_pipes(r#"p|cut:"a\"|b""#), vec!["p", r#"cut:"a\"|b""#]);
    }

    #[test]
    fn an_unterminated_quote_keeps_the_rest_of_the_token_whole() {
        // The pipes BEFORE the unterminated quote still split; from the quote
        // on, the tail is kept whole rather than dropped. Django's scan does
        // the same thing for the same reason — `|cut` matches, the constant
        // alternative cannot, and what is left becomes the remainder:
        //
        //     {{ p|cut:"a|b }}
        //     TemplateSyntaxError: Could not parse the remainder: ':"a|b'
        assert_eq!(split_pipes(r#"p|cut:"a|b"#), vec!["p", r#"cut:"a|b"#]);
        let err = split_filter_spec(r#"cut:"a|b"#, r#"p|cut:"a|b"#).unwrap_err();
        assert!(
            format!("{err}").contains(r#"Could not parse the remainder: ':"a|b'"#),
            "{err}"
        );
    }

    #[test]
    fn a_second_argument_is_refused() {
        let err = split_filter_spec(r#"cut:"a":"b""#, r#"p|cut:"a":"b""#).unwrap_err();
        assert!(
            format!("{err}").contains(r#"Could not parse the remainder: ':"b"'"#),
            "{err}"
        );
    }

    #[test]
    fn a_colon_inside_a_quoted_argument_is_kept() {
        assert_eq!(
            split_filter_spec(r#"date:"H:i""#, r#"p|date:"H:i""#).unwrap(),
            ("date", Some(r#""H:i""#))
        );
        assert_eq!(
            split_filter_spec(r#"cut:":""#, r#"p|cut:":""#).unwrap(),
            ("cut", Some(r#"":""#))
        );
    }

    #[test]
    fn djangos_three_argument_alternatives_all_consume_fully() {
        for arg in [
            r#""x""#,
            "'x'",
            r#""a:b""#,
            r#""a|b""#,
            r#""a b""#,
            r#"_("x")"#,
            "_('x')",
            "q",
            "d.k",
            "2",
            "-2",
            "+2",
            "2.5",
            ".5",
            "2.5e3",
        ] {
            assert_eq!(argument_end(arg), Some(arg.len()), "{arg:?}");
        }
    }

    #[test]
    fn shapes_django_refuses_do_not_consume_fully() {
        for arg in [
            "a-b",
            "a,b",
            "a b",
            "a(b)",
            "*",
            "",
            r#""a"junk"#,
            r#""unterminated"#,
        ] {
            assert_ne!(argument_end(arg), Some(arg.len()), "{arg:?}");
        }
    }

    #[test]
    fn a_separator_with_nothing_after_it_is_refused() {
        // `{{ p|default: }}` — Django's optional argument group cannot match a
        // bare colon, so the colon is the remainder.
        let err = split_filter_spec("default:", "p|default:").unwrap_err();
        assert!(
            format!("{err}").contains("Could not parse the remainder: ':'"),
            "{err}"
        );
    }

    #[test]
    fn a_bare_filter_has_no_argument() {
        assert_eq!(
            split_filter_spec("upper", "p|upper").unwrap(),
            ("upper", None)
        );
    }
}
