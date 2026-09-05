//! Template lexer for tokenizing Django template syntax.
//!
//! Tag arguments are split with [`split_tag_args`], which respects quoted
//! strings so that values like `name="My App"` remain a single token.

use djust_core::Result;

#[derive(Debug, Clone, PartialEq)]
pub enum Token {
    Text(String),
    Variable(String),         // {{ var }}
    Tag(String, Vec<String>), // {% tag args %}
    /// `{# comment #}`, carrying the RAW inner text (between `{#` and `#}`,
    /// unstripped). The normal parse path discards it — a comment renders as
    /// nothing on both engines. It is carried because `collect_raw_source`
    /// must re-emit the comment VERBATIM into a raw-block body (#2558): a
    /// `{% blocktranslate %}` body crosses to Django as source, and Django's
    /// `do_block_translate` refuses a comment inside it
    /// (`doesn't allow other block tags (seen 'c')`). Dropping the text here
    /// silently deleted the comment from the body, so Django never saw it and
    /// rendered a mangled msgid instead of raising.
    Comment(String),
    JsxComponent {
        // <Button prop="value">children</Button>
        name: String,
        props: Vec<(String, String)>,
        children: Vec<Token>,
        self_closing: bool,
    },
}

/// A char cursor over the template source that also knows the BYTE offset of
/// the character it is about to yield.
///
/// This is the position information Django's `Token.position` carries and
/// that `Template.get_exception_info` turns into `template_debug` — the
/// `name` / `line` / `during` / `source_lines` a technical-500 page shows
/// (#2557). The lexer used a bare `Peekable<Chars>`, which throws the offset
/// away, so every parse error reached Python with no location at all.
///
/// `pos()` reports the offset of the NEXT character (the source length once
/// the input is exhausted), so a token's span is `pos()` sampled before its
/// first character and again after its last.
#[derive(Clone)]
struct Cursor<'a> {
    inner: std::iter::Peekable<std::str::CharIndices<'a>>,
    end: usize,
}

impl<'a> Cursor<'a> {
    fn new(source: &'a str) -> Self {
        Self {
            inner: source.char_indices().peekable(),
            end: source.len(),
        }
    }

    /// Byte offset of the next character, or the source length at EOF.
    fn pos(&mut self) -> usize {
        self.inner.peek().map_or(self.end, |&(i, _)| i)
    }

    fn next(&mut self) -> Option<char> {
        self.inner.next().map(|(_, c)| c)
    }

    fn peek(&mut self) -> Option<char> {
        self.inner.peek().map(|&(_, c)| c)
    }

    /// Does the not-yet-consumed input contain the closing pair `a`+`b`?
    ///
    /// Django's lexer finds tags by scanning a regex — `{%.*?%}|{{.*?}}|{#.*?#}`
    /// — over the whole source, so an opener with NO closer anywhere after it is
    /// not a tag at all: it is plain text, and a WELL-FORMED tag later in the
    /// source still lexes normally (#2558). A sequential lexer that consumes from
    /// the opener to end-of-input instead swallows every later tag, which is how
    /// `{% blocktranslate %}a {{ unclosed b{% endblocktranslate %}` — a template
    /// Django renders verbatim — became an `Unclosed raw-block tag` failure, and
    /// how a bare `a {{ unclosed b` silently rendered as `a `.
    ///
    /// Checking BEFORE consuming keeps that behaviour: no closer means emit only
    /// the FIRST opener character as text and let the second be re-examined on
    /// the next iteration — which is what a regex scanner does when it fails to
    /// match and advances by one. That is why `{% if a %}x{{% endif %}` renders
    /// `x{` on both engines: the second `{` starts the real `{% endif %}`.
    ///
    /// The cursor is positioned ON the second opener character, and the scan
    /// starts AFTER it, so the opener can never supply half the closer: `{%}`
    /// has no `%}` of its own and is plain text, exactly as Django's regex
    /// sees it.
    fn has_closer(&self, a: char, b: char) -> bool {
        let mut it = self.inner.clone();
        it.next(); // the second opener character is not part of a closer
        let mut prev = None;
        for (_, c) in it {
            // Django's tag regex does not use DOTALL.
            if c == '\n' {
                return false;
            }
            if prev == Some(a) && c == b {
                return true;
            }
            prev = Some(c);
        }
        false
    }
}

fn parse_jsx_component(chars: &mut Cursor<'_>) -> Result<Token> {
    let mut name = String::new();
    let mut props = Vec::new();

    // Parse component name
    while let Some(ch) = chars.peek() {
        if ch.is_alphanumeric() || ch == '_' {
            name.push(ch);
            chars.next();
        } else {
            break;
        }
    }

    // Skip whitespace
    while chars.peek() == Some(' ') || chars.peek() == Some('\n') || chars.peek() == Some('\t') {
        chars.next();
    }

    // Parse props
    while let Some(ch) = chars.peek() {
        if ch == '/' || ch == '>' {
            break;
        }

        if ch.is_alphabetic() {
            let mut prop_name = String::new();
            let mut prop_value = String::new();

            // Parse prop name
            while let Some(ch) = chars.peek() {
                if ch == '=' || ch.is_whitespace() {
                    break;
                }
                prop_name.push(ch);
                chars.next();
            }

            // Skip whitespace and =
            while chars.peek() == Some(' ') || chars.peek() == Some('=') {
                chars.next();
            }

            // Parse prop value
            if chars.peek() == Some('"') || chars.peek() == Some('\'') {
                let quote = chars.next().unwrap();
                while let Some(ch) = chars.next() {
                    if ch == quote {
                        break;
                    }
                    prop_value.push(ch);
                }
            } else if chars.peek() == Some('{') {
                // Handle {expression} props
                chars.next(); // consume {
                let mut depth = 1;
                while let Some(ch) = chars.next() {
                    if ch == '{' {
                        depth += 1;
                    } else if ch == '}' {
                        depth -= 1;
                        if depth == 0 {
                            break;
                        }
                    }
                    prop_value.push(ch);
                }
            }

            if !prop_name.is_empty() {
                props.push((prop_name, prop_value));
            }

            // Skip whitespace
            while chars.peek() == Some(' ')
                || chars.peek() == Some('\n')
                || chars.peek() == Some('\t')
            {
                chars.next();
            }
        } else {
            chars.next();
        }
    }

    // Check if self-closing
    if chars.peek() == Some('/') {
        chars.next(); // consume /
        chars.next(); // consume >
        return Ok(Token::JsxComponent {
            name,
            props,
            children: vec![],
            self_closing: true,
        });
    }

    // Consume >
    chars.next();

    // Parse children (simplified - just text for now)
    let mut children = vec![];
    let mut child_text = String::new();

    while let Some(ch) = chars.next() {
        if ch == '<' && chars.peek() == Some('/') {
            // Potential closing tag - verify it matches our component name
            // Save position by peeking ahead
            let mut tag_name = String::new();
            let mut temp_chars = chars.clone();
            temp_chars.next(); // consume / in temp iterator

            while let Some(ch) = temp_chars.peek() {
                if ch == '>' || ch.is_whitespace() {
                    break;
                }
                tag_name.push(ch);
                temp_chars.next();
            }

            if tag_name == name {
                // This is our closing tag
                chars.next(); // consume /
                if !child_text.is_empty() {
                    children.push(Token::Text(child_text.trim().to_string()));
                }
                // Skip to >
                while chars.peek() != Some('>') {
                    if chars.next().is_none() {
                        break;
                    }
                }
                chars.next(); // consume >
                break;
            } else {
                // This is a closing tag for nested HTML, add it as-is
                child_text.push(ch); // add the '<'
            }
        } else {
            child_text.push(ch);
        }
    }

    Ok(Token::JsxComponent {
        name,
        props,
        children,
        self_closing: false,
    })
}

/// Split tag content into arguments, respecting quoted strings.
///
/// Handles both single and double quotes so that spaces inside quoted values
/// are preserved as part of the argument.
///
/// # Examples
/// ```text
/// "djust_pwa_head name=\"My App\" theme_color=\"#09090b\""
/// → ["djust_pwa_head", "name=\"My App\"", "theme_color=\"#09090b\""]
/// ```
fn split_tag_args(content: &str) -> Vec<String> {
    let mut parts = Vec::new();
    let mut current = String::new();
    let mut in_quote: Option<char> = None;

    for ch in content.chars() {
        match in_quote {
            Some(q) => {
                current.push(ch);
                if ch == q {
                    in_quote = None;
                }
            }
            None => {
                if ch == '"' || ch == '\'' {
                    current.push(ch);
                    in_quote = Some(ch);
                } else if ch.is_whitespace() {
                    if !current.is_empty() {
                        parts.push(current.clone());
                        current.clear();
                    }
                } else {
                    current.push(ch);
                }
            }
        }
    }
    if !current.is_empty() {
        parts.push(current);
    }
    parts
}

/// The `[start, end)` BYTE span of one token in the template source.
///
/// Parallel to the token vector `tokenize_spanned` returns: `spans[i]` is the
/// span of `tokens[i]`. Django carries the same pair on `Token.position` and
/// hands it to `Template.get_exception_info`, which is what fills the
/// `template_debug` dict a technical-500 page renders (#2557).
pub type Span = (usize, usize);

/// Tokens plus the source span of each, collected side by side.
struct Spanned {
    tokens: Vec<Token>,
    spans: Vec<Span>,
}

impl Spanned {
    fn new() -> Self {
        Self {
            tokens: Vec::new(),
            spans: Vec::new(),
        }
    }

    fn push(&mut self, token: Token, start: usize, end: usize) {
        self.tokens.push(token);
        self.spans.push((start, end));
    }
}

pub fn tokenize(source: &str) -> Result<Vec<Token>> {
    tokenize_spanned(source).map(|(tokens, _)| tokens)
}

/// Tokenize, also returning each token's byte span in `source` (#2557).
///
/// `tokenize` is this function with the spans dropped, so there is one
/// tokenizer and no parallel-path drift (#1646) — a lexer change reaches both
/// callers or neither.
pub fn tokenize_spanned(source: &str) -> Result<(Vec<Token>, Vec<Span>)> {
    let mut out = Spanned::new();
    let mut chars = Cursor::new(source);
    let mut current = String::new();
    // Byte offset where the run of literal text in `current` began. Only
    // meaningful while `current` is non-empty.
    let mut text_start = 0usize;
    let mut verbatim_end: Option<String> = None;

    macro_rules! flush_text {
        ($end:expr) => {
            if !current.is_empty() {
                out.push(Token::Text(current.clone()), text_start, $end);
                current.clear();
            }
        };
    }

    loop {
        let tok_start = chars.pos();
        let Some(ch) = chars.next() else { break };
        if current.is_empty() {
            text_start = tok_start;
        }
        // Django's lexer treats every token inside verbatim as literal text
        // until the complete named closing tag matches. Consume whole tokens
        // so a closer inside a comment or variable cannot end the block.
        if let Some(expected) = verbatim_end.as_ref() {
            if ch == '{' {
                if let Some(opener @ ('{' | '%' | '#')) = chars.peek() {
                    let closer = if opener == '{' { '}' } else { opener };
                    if chars.has_closer(closer, '}') {
                        chars.next();
                        while let Some(c) = chars.next() {
                            if c == closer && chars.peek() == Some('}') {
                                chars.next();
                                break;
                            }
                        }
                        let end = chars.pos();
                        let inner = &source[tok_start + 2..end - 2];
                        if opener == '%' && inner.trim() == expected {
                            flush_text!(tok_start);
                            out.push(
                                Token::Tag(
                                    "endverbatim".into(),
                                    split_tag_args(inner)[1..].to_vec(),
                                ),
                                tok_start,
                                end,
                            );
                            verbatim_end = None;
                        } else {
                            current.push_str(&source[tok_start..end]);
                        }
                        continue;
                    }
                }
            }
            current.push(ch);
            continue;
        }
        if ch == '<' {
            // Check if this is a JSX component (starts with uppercase)
            if let Some(next_ch) = chars.peek() {
                if next_ch.is_uppercase() {
                    // JSX component detected
                    flush_text!(tok_start);
                    match parse_jsx_component(&mut chars) {
                        Ok(token) => {
                            let end = chars.pos();
                            out.push(token, tok_start, end);
                        }
                        Err(_) => {
                            // Fallback to text if parsing fails
                            text_start = tok_start;
                            current.push(ch);
                        }
                    }
                    continue;
                }
            }
            current.push(ch);
        } else if ch == '{' {
            if let Some(next) = chars.peek() {
                match next {
                    '{' => {
                        // Variable start {{
                        if !chars.has_closer('}', '}') {
                            // No `}}` after: literal text, as in Django.
                            // Only the FIRST `{` is consumed — the second is
                            // re-examined next iteration and may open a real
                            // tag. See `Cursor::has_closer`.
                            current.push(ch);
                            continue;
                        }
                        chars.next(); // consume second {
                        flush_text!(tok_start);

                        let mut var_content = String::new();

                        while let Some(ch) = chars.next() {
                            if ch == '}' && chars.peek() == Some('}') {
                                chars.next(); // consume second }
                                let content = var_content.trim().to_string();
                                // An EMPTY `{{ }}` is emitted, not refused.
                                // Django's `Lexer.create_token` returns
                                // `Token(TokenType.VAR, "")` here and the
                                // refusal lives one layer up, in
                                // `Parser.parse` (`django/template/base.py`
                                // 483-486) — which is what lets
                                // `{% verbatim %}{{ }}{% endverbatim %}` and
                                // `{% comment %}{{ }}{% endcomment %}` render
                                // (their bodies never reach the parser loop).
                                // djust mirrors that placement in
                                // `parser::parse_token_inner` (#2557).
                                out.push(Token::Variable(content), tok_start, chars.pos());
                                break;
                            } else {
                                var_content.push(ch);
                            }
                        }
                    }
                    '%' => {
                        // Tag start {%
                        if !chars.has_closer('%', '}') {
                            // No `%}` after: literal text. See the `{{` arm.
                            current.push(ch);
                            continue;
                        }
                        chars.next(); // consume %
                        flush_text!(tok_start);

                        let mut tag_content = String::new();

                        while let Some(ch) = chars.next() {
                            if ch == '%' && chars.peek() == Some('}') {
                                chars.next(); // consume }
                                let parts: Vec<String> = split_tag_args(&tag_content);

                                // An empty `{% %}` yields a Tag with an EMPTY
                                // name rather than nothing at all. Django's
                                // lexer does the same — `create_token` builds
                                // `Token(TokenType.BLOCK, "")` — and
                                // `Parser.parse` refuses it with
                                // `Empty block tag on line %d`
                                // (`django/template/base.py:497`). Dropping
                                // the token here made the refusal
                                // unreachable, so `{% %}` rendered as
                                // nothing (#2557).
                                let tag_name = parts.first().cloned().unwrap_or_default();
                                let args = if parts.is_empty() {
                                    Vec::new()
                                } else {
                                    parts[1..].to_vec()
                                };
                                if tag_name == "verbatim" {
                                    verbatim_end = Some(format!("end{}", tag_content.trim()));
                                }
                                out.push(Token::Tag(tag_name, args), tok_start, chars.pos());
                                break;
                            } else {
                                tag_content.push(ch);
                            }
                        }
                    }
                    '#' => {
                        // Comment start {#
                        if !chars.has_closer('#', '}') {
                            // No `#}` after: literal text. See the `{{` arm.
                            current.push(ch);
                            continue;
                        }
                        chars.next(); // consume #
                        flush_text!(tok_start);

                        // Collect until `#}`. The text is kept so a raw-block
                        // body can re-emit the comment verbatim (#2558).
                        let mut comment_content = String::new();
                        while let Some(ch) = chars.next() {
                            if ch == '#' && chars.peek() == Some('}') {
                                chars.next(); // consume }
                                out.push(
                                    Token::Comment(comment_content.clone()),
                                    tok_start,
                                    chars.pos(),
                                );
                                break;
                            }
                            comment_content.push(ch);
                        }
                    }
                    _ => {
                        current.push(ch);
                    }
                }
            } else {
                current.push(ch);
            }
        } else {
            current.push(ch);
        }
    }

    let eof = chars.pos();
    flush_text!(eof);

    Ok((out.tokens, out.spans))
}

#[cfg(test)]
mod tests {
    use super::*;

    // ---------------------------------------------------------------------------
    // tokenize tests
    // ---------------------------------------------------------------------------

    #[test]
    fn test_tokenize_text() {
        let tokens = tokenize("Hello World").unwrap();
        assert_eq!(tokens, vec![Token::Text("Hello World".to_string())]);
    }

    #[test]
    fn test_tokenize_variable() {
        let tokens = tokenize("Hello {{ name }}").unwrap();
        assert_eq!(
            tokens,
            vec![
                Token::Text("Hello ".to_string()),
                Token::Variable("name".to_string()),
            ]
        );
    }

    #[test]
    fn test_tokenize_tag() {
        let tokens = tokenize("{% if true %}yes{% endif %}").unwrap();
        assert_eq!(
            tokens[0],
            Token::Tag("if".to_string(), vec!["true".to_string()])
        );
    }

    #[test]
    fn test_tokenize_tag_quoted_arg_with_space() {
        // Regression: {% djust_pwa_head name="My App" %} must NOT split "My App"
        // into two tokens. Before the fix, split_whitespace() produced
        // ["name=\"My", "App\""] instead of ["name=\"My App\""].
        let tokens =
            tokenize(r##"{% djust_pwa_head name="My App" theme_color="#09090b" %}"##).unwrap();
        assert_eq!(
            tokens[0],
            Token::Tag(
                "djust_pwa_head".to_string(),
                vec![
                    r#"name="My App""#.to_string(),
                    r##"theme_color="#09090b""##.to_string(),
                ]
            )
        );
    }

    #[test]
    fn test_split_tag_args_helper() {
        // Unit-test the helper directly
        let args = split_tag_args(r##"name="My App" theme_color="#09090b""##);
        assert_eq!(args, vec![r#"name="My App""#, r##"theme_color="#09090b""##]);

        // Single-quoted value with space
        let args2 = split_tag_args("name='Hello World' other=simple");
        assert_eq!(args2, vec!["name='Hello World'", "other=simple"]);

        // No quotes – should behave like split_whitespace
        let args3 = split_tag_args("foo bar baz");
        assert_eq!(args3, vec!["foo", "bar", "baz"]);
    }

    #[test]
    fn test_tokenize_comment() {
        let tokens = tokenize("Hello {# comment #} World").unwrap();
        assert_eq!(
            tokens,
            vec![
                Token::Text("Hello ".to_string()),
                Token::Comment(" comment ".to_string()),
                Token::Text(" World".to_string()),
            ]
        );
    }

    // -----------------------------------------------------------------
    // Unterminated markers are literal text, as in Django (#2558/#2597).
    // Django's lexer scans `{%.*?%}|{{.*?}}|{#.*?#}` over the source, so an
    // opener with no closer is not a tag and does not shadow a later one.
    // -----------------------------------------------------------------

    #[test]
    fn test_unterminated_variable_marker_is_text() {
        assert_eq!(
            tokenize("a {{ unclosed b").unwrap(),
            vec![Token::Text("a {{ unclosed b".to_string())]
        );
    }

    #[test]
    fn test_unterminated_tag_marker_is_text() {
        assert_eq!(
            tokenize("a {% unclosed b").unwrap(),
            vec![Token::Text("a {% unclosed b".to_string())]
        );
    }

    #[test]
    fn test_unterminated_comment_marker_is_text() {
        assert_eq!(
            tokenize("a {# unclosed b").unwrap(),
            vec![Token::Text("a {# unclosed b".to_string())]
        );
    }

    /// The load-bearing case: an unterminated `{{` must NOT swallow the
    /// well-formed tag after it. Consuming to end-of-input turned
    /// `{% blocktranslate %}a {{ x{% endblocktranslate %}` — which Django
    /// renders verbatim — into an `Unclosed raw-block tag` parse failure.
    #[test]
    fn test_unterminated_marker_does_not_swallow_a_later_tag() {
        assert_eq!(
            tokenize("a {{ unclosed b{% endblocktranslate %}").unwrap(),
            vec![
                Token::Text("a {{ unclosed b".to_string()),
                Token::Tag("endblocktranslate".to_string(), vec![]),
            ]
        );
    }

    /// A closer that IS present later keeps the marker a real tag — the
    /// lookahead must not turn well-formed syntax into text.
    #[test]
    fn test_marker_with_a_later_closer_is_still_a_tag() {
        assert_eq!(
            tokenize("a {{ x }} b").unwrap(),
            vec![
                Token::Text("a ".to_string()),
                Token::Variable("x".to_string()),
                Token::Text(" b".to_string()),
            ]
        );
    }

    /// A failed lookahead consumes only the FIRST brace, so the second can
    /// still open a real tag — `{{%` is text plus whatever follows, and
    /// `x{{% endif %}` is `x{` + the `endif` tag, as in Django.
    #[test]
    fn a_failed_opener_lets_the_second_brace_start_a_real_tag() {
        assert_eq!(
            tokenize("x{{% endif %}").unwrap(),
            vec![
                Token::Text("x{".to_string()),
                Token::Tag("endif".to_string(), vec![]),
            ]
        );
    }

    /// The opener cannot supply half its own closer: `{%}` has no `%}` after
    /// the `{%`, so it is plain text — Django's regex reads it the same way.
    #[test]
    fn an_opener_does_not_supply_half_of_its_own_closer() {
        assert_eq!(
            tokenize("{%}").unwrap(),
            vec![Token::Text("{%}".to_string())]
        );
        assert_eq!(
            tokenize("{#}").unwrap(),
            vec![Token::Text("{#}".to_string())]
        );
        assert_eq!(
            tokenize("{{%").unwrap(),
            vec![Token::Text("{{%".to_string())]
        );
    }

    /// A comment carries its RAW inner text so `collect_raw_source` can
    /// re-emit it verbatim into a raw-block body (#2558).
    #[test]
    fn test_comment_token_carries_its_raw_text() {
        assert_eq!(
            tokenize("{# Translators: hi #}").unwrap(),
            vec![Token::Comment(" Translators: hi ".to_string())]
        );
    }

    #[test]
    fn test_tokenize_jsx_self_closing() {
        let tokens = tokenize("Hello <Button label=\"Click me\" />").unwrap();
        assert_eq!(tokens.len(), 2);
        assert_eq!(tokens[0], Token::Text("Hello ".to_string()));
        if let Token::JsxComponent {
            name,
            props,
            self_closing,
            ..
        } = &tokens[1]
        {
            assert_eq!(name, "Button");
            assert_eq!(props.len(), 1);
            assert_eq!(props[0].0, "label");
            assert_eq!(props[0].1, "Click me");
            assert!(self_closing);
        } else {
            panic!("Expected JsxComponent token");
        }
    }

    #[test]
    fn test_tokenize_jsx_with_children() {
        let tokens = tokenize("<Button>Click me</Button>").unwrap();
        assert_eq!(tokens.len(), 1);
        if let Token::JsxComponent {
            name,
            children,
            self_closing,
            ..
        } = &tokens[0]
        {
            assert_eq!(name, "Button");
            assert!(!self_closing);
            assert_eq!(children.len(), 1);
        } else {
            panic!("Expected JsxComponent token");
        }
    }

    #[test]
    fn test_split_tag_args_simple() {
        let args = split_tag_args(" url 'post_detail' post.slug ");
        assert_eq!(args, vec!["url", "'post_detail'", "post.slug"]);
    }

    #[test]
    fn test_split_tag_args_quoted_with_spaces() {
        let input = r##" djust_pwa_head name="My App" theme_color="#09090b" "##;
        let args = split_tag_args(input);
        assert_eq!(args[0], "djust_pwa_head");
        assert_eq!(args[1], r##"name="My App""##);
        assert_eq!(args[2], r##"theme_color="#09090b""##);
        assert_eq!(args.len(), 3);
    }

    #[test]
    fn test_split_tag_args_single_quotes_with_spaces() {
        let args = split_tag_args(" mytag label='Hello World' ");
        assert_eq!(args, vec!["mytag", "label='Hello World'"]);
    }

    #[test]
    fn test_split_tag_args_empty() {
        let args = split_tag_args("   ");
        assert!(args.is_empty());
    }

    #[test]
    fn test_split_tag_args_no_quotes() {
        let args = split_tag_args(" for item in items ");
        assert_eq!(args, vec!["for", "item", "in", "items"]);
    }

    #[test]
    fn test_split_tag_args_mixed_quotes() {
        let args = split_tag_args(r#" tag key="value with spaces" plain 'another arg' "#);
        assert_eq!(
            args,
            vec![
                "tag",
                r#"key="value with spaces""#,
                "plain",
                "'another arg'"
            ]
        );
    }

    #[test]
    fn test_tokenize_tag_with_quoted_spaces() {
        let tokens = tokenize(r#"{% djust_pwa_head name="My App" %}"#).unwrap();
        assert_eq!(tokens.len(), 1);
        if let Token::Tag(name, args) = &tokens[0] {
            assert_eq!(name, "djust_pwa_head");
            assert_eq!(args, &vec![r#"name="My App""#]);
        } else {
            panic!("Expected Tag token, got {:?}", tokens[0]);
        }
    }

    // -----------------------------------------------------------------------
    // token spans (#2557)
    // -----------------------------------------------------------------------

    /// Every span must slice its own token back out of the source.
    ///
    /// This is the property `template_debug`'s `during` field depends on: a
    /// span off by one produces an excerpt Django would never show.
    #[test]
    fn every_span_slices_its_own_token_out_of_the_source() {
        for source in [
            "plain text only",
            "{{ x }}",
            "a{{ x }}b",
            "{% if x %}y{% endif %}",
            "{# a comment #}",
            "before {# c #} between {% tag a b %} after {{ v }} end",
            "line one\n{% nosuchtag %}\nline three",
            "caf\u{e9} \u{2615} {{ na\u{ef}ve }} tail",
            "{% if a %}{{ b }}{% else %}{{ c }}{% endif %}",
        ] {
            let (tokens, spans) = tokenize_spanned(source).expect("tokenize");
            assert_eq!(tokens.len(), spans.len(), "one span per token: {source:?}");
            for (i, (start, end)) in spans.iter().copied().enumerate() {
                assert!(
                    start <= end && end <= source.len(),
                    "{source:?} span {i} = ({start},{end})"
                );
                let slice = &source[start..end];
                match &tokens[i] {
                    Token::Text(t) => assert_eq!(slice, t, "text span {i} of {source:?}"),
                    Token::Variable(_) => {
                        assert!(
                            slice.starts_with("{{") && slice.ends_with("}}"),
                            "{slice:?}"
                        )
                    }
                    Token::Tag(name, _) => {
                        assert!(
                            slice.starts_with("{%") && slice.ends_with("%}"),
                            "{slice:?}"
                        );
                        assert!(
                            slice.contains(name.as_str()),
                            "{slice:?} should name {name}"
                        );
                    }
                    Token::Comment(_) => {
                        assert!(
                            slice.starts_with("{#") && slice.ends_with("#}"),
                            "{slice:?}"
                        )
                    }
                    Token::JsxComponent { .. } => {}
                }
            }
        }
    }

    /// Spans are contiguous and ordered: they tile the source with no gap.
    #[test]
    fn spans_tile_the_source_in_order() {
        let source = "a{{ x }}b{% t %}c{# k #}d";
        let (_, spans) = tokenize_spanned(source).expect("tokenize");
        let mut upto = 0usize;
        for (start, end) in spans {
            assert_eq!(start, upto, "gap or overlap before ({start},{end})");
            upto = end;
        }
        assert_eq!(upto, source.len(), "the spans must reach the end");
    }

    /// A multi-byte character before a token must not shift that token's span.
    #[test]
    fn a_multibyte_prefix_does_not_shift_a_later_span() {
        let source = "caf\u{e9}\n{% nosuchtag %}";
        let (tokens, spans) = tokenize_spanned(source).expect("tokenize");
        let idx = tokens
            .iter()
            .position(|t| matches!(t, Token::Tag(n, _) if n == "nosuchtag"))
            .expect("the tag token");
        let (start, end) = spans[idx];
        assert_eq!(&source[start..end], "{% nosuchtag %}");
        assert_eq!(start, source.find("{% nosuchtag %}").unwrap());
        // The byte offset is genuinely larger than the CHARACTER offset here,
        // which is the difference the Python side has to convert away.
        let char_offset = source.chars().take_while(|c| *c != '{').count();
        assert!(start > char_offset, "expected a byte/char divergence");
    }

    /// `tokenize` is `tokenize_spanned` with the spans dropped — one
    /// tokenizer, so a lexer change cannot reach one caller and not the other.
    #[test]
    fn tokenize_agrees_with_tokenize_spanned() {
        for source in [
            "plain",
            "{{ x }}{% y %}{# z #}",
            "a {{ unclosed b",
            "{% if a %}x{{% endif %}",
        ] {
            let plain = tokenize(source).expect("tokenize");
            let (spanned, _) = tokenize_spanned(source).expect("tokenize_spanned");
            assert_eq!(plain, spanned, "{source:?}");
        }
    }

    /// The lexer EMITS an empty `{{ }}` / `{% %}` rather than refusing it —
    /// Django's `Lexer.create_token` returns `Token(TokenType.VAR, "")` /
    /// `Token(TokenType.BLOCK, "")` and the refusal lives in `Parser.parse`
    /// (`django/template/base.py:483-486` and `:497`).
    ///
    /// The placement is what lets a raw block hold `{{ }}` literally, so this
    /// is not a stylistic choice: refusing here regresses
    /// `{% verbatim %}{{ }}{% endverbatim %}`. The refusals themselves are
    /// pinned in `parser::tests` (#2557).
    #[test]
    fn an_empty_tag_lexes_to_an_empty_token_not_an_error() {
        for source in ["{{ }}", "{{}}", "{{    }}", "a\nb\n{{ }}"] {
            let (tokens, _) = tokenize_spanned(source).expect("the lexer must not refuse");
            assert!(
                tokens.contains(&Token::Variable(String::new())),
                "{source:?} gave {tokens:?}"
            );
        }
        for source in ["{% %}", "{%  %}"] {
            let (tokens, _) = tokenize_spanned(source).expect("the lexer must not refuse");
            assert!(
                tokens.contains(&Token::Tag(String::new(), Vec::new())),
                "{source:?} gave {tokens:?}"
            );
        }
    }

    /// The empty token still carries its span, so the parser's refusal can
    /// report a location like every other located parse error (#2557).
    #[test]
    fn an_empty_tag_carries_its_span() {
        let (tokens, spans) = tokenize_spanned("a\nb\n{{ }}").expect("tokenize");
        let i = tokens
            .iter()
            .position(|t| t == &Token::Variable(String::new()))
            .expect("the empty variable token");
        assert_eq!(spans[i], (4, 9));
    }

    /// A non-empty variable is untouched, and so is a `{{` with no closer —
    /// which `has_closer` keeps as literal text (#2558).
    #[test]
    fn the_empty_variable_token_reaches_nothing_else() {
        for source in ["{{ x }}", "a {{ unclosed b"] {
            let (tokens, _) = tokenize_spanned(source).expect("tokenize");
            assert!(
                !tokens.contains(&Token::Variable(String::new())),
                "{source:?} gave {tokens:?}"
            );
        }
    }
}
