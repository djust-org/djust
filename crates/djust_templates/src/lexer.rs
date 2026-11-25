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
    /// `<Name ...>` whose name starts with an uppercase letter and is not an
    /// uppercase spelling of a standard HTML/SVG element (#4006). djust 2.0
    /// removed React/JSX component tags; the parser refuses it by name.
    CapitalizedTag(String),
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

/// Standard HTML and SVG element names (lowercase). A capitalized spelling of
/// one of these (`<DIV>`, `<BR>`) is HTML, not a component tag (#4006).
const STANDARD_ELEMENTS: &[&str] = &[
    "a",
    "abbr",
    "acronym",
    "address",
    "area",
    "article",
    "aside",
    "audio",
    "b",
    "base",
    "bdi",
    "bdo",
    "big",
    "blockquote",
    "body",
    "br",
    "button",
    "canvas",
    "caption",
    "center",
    "cite",
    "code",
    "col",
    "colgroup",
    "data",
    "datalist",
    "dd",
    "del",
    "details",
    "dfn",
    "dialog",
    "dir",
    "div",
    "dl",
    "dt",
    "em",
    "embed",
    "fieldset",
    "figcaption",
    "figure",
    "font",
    "footer",
    "form",
    "frame",
    "frameset",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "head",
    "header",
    "hgroup",
    "hr",
    "html",
    "i",
    "iframe",
    "img",
    "input",
    "ins",
    "kbd",
    "label",
    "legend",
    "li",
    "link",
    "main",
    "map",
    "mark",
    "marquee",
    "menu",
    "meta",
    "meter",
    "nav",
    "noframes",
    "noscript",
    "object",
    "ol",
    "optgroup",
    "option",
    "output",
    "p",
    "param",
    "picture",
    "pre",
    "progress",
    "q",
    "rp",
    "rt",
    "ruby",
    "s",
    "samp",
    "script",
    "search",
    "section",
    "select",
    "slot",
    "small",
    "source",
    "span",
    "strike",
    "strong",
    "style",
    "sub",
    "summary",
    "sup",
    "table",
    "tbody",
    "td",
    "template",
    "textarea",
    "tfoot",
    "th",
    "thead",
    "time",
    "title",
    "tr",
    "track",
    "tt",
    "u",
    "ul",
    "var",
    "video",
    "wbr",
    "math",
    // SVG
    "svg",
    "g",
    "path",
    "circle",
    "ellipse",
    "line",
    "polygon",
    "polyline",
    "rect",
    "text",
    "tspan",
    "defs",
    "use",
    "symbol",
    "marker",
    "mask",
    "pattern",
    "clippath",
    "lineargradient",
    "radialgradient",
    "stop",
    "image",
    "foreignobject",
    "switch",
    "view",
    "filter",
    "desc",
    "textpath",
    "animate",
    "animatetransform",
    "animatemotion",
    "set",
    "mpath",
    "metadata",
];

/// When `source[at..]` opens an HTML comment or a `<script>` / `<style>` element,
/// the byte offset where that region ends (the end of the source when it is
/// never closed). A capitalized `<Name` inside is JavaScript, CSS or comment
/// text, not a component tag (#4006).
fn literal_region_end(source: &str, at: usize) -> Option<usize> {
    let rest = &source[at..];
    if rest.starts_with("<!--") {
        return Some(rest.find("-->").map_or(source.len(), |i| at + i + 3));
    }
    for (open, close) in [("<script", "</script"), ("<style", "</style")] {
        let bytes = rest.as_bytes();
        if bytes.len() > open.len()
            && bytes[..open.len()].eq_ignore_ascii_case(open.as_bytes())
            && matches!(
                bytes[open.len()],
                b' ' | b'\t' | b'\n' | b'\r' | b'>' | b'/'
            )
        {
            let lowered = rest.to_ascii_lowercase();
            return Some(lowered.find(close).map_or(source.len(), |i| at + i));
        }
    }
    None
}

/// True for an all-uppercase spelling of a standard element (`DIV`, `BR`).
/// `Button`-style PascalCase names are component tags even when a lowercase
/// element of that name exists.
fn is_uppercase_html_element(name: &str) -> bool {
    !name.chars().any(|c| c.is_ascii_lowercase())
        && STANDARD_ELEMENTS.contains(&name.to_ascii_lowercase().as_str())
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
    // Byte offset up to which `<` is literal HTML text: inside `<!-- -->` and
    // `<script>` / `<style>` raw text a capitalized `<Name` is not a tag (#4006).
    let mut literal_until = 0usize;
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
            if tok_start >= literal_until {
                literal_until = literal_region_end(source, tok_start).unwrap_or(0);
            }
            // `<Name` with an uppercase first letter is a refused component tag (#4006).
            if let Some(next_ch) = chars.peek().filter(|_| tok_start >= literal_until) {
                if next_ch.is_ascii_uppercase() {
                    let mut look = chars.clone();
                    let mut name = String::new();
                    while let Some(c) = look.peek() {
                        if c.is_ascii_alphanumeric() || c == '_' {
                            name.push(c);
                            look.next();
                        } else {
                            break;
                        }
                    }
                    if matches!(look.peek(), Some(c) if c.is_whitespace() || c == '/' || c == '>')
                        && !is_uppercase_html_element(&name)
                    {
                        flush_text!(tok_start);
                        chars = look;
                        while let Some(c) = chars.next() {
                            if c == '>' {
                                break;
                            }
                        }
                        out.push(Token::CapitalizedTag(name), tok_start, chars.pos());
                        continue;
                    }
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
    fn capitalized_tag_is_one_token() {
        let tokens = tokenize("<Button label=\"x\" />").unwrap();
        assert_eq!(tokens, vec![Token::CapitalizedTag("Button".to_string())]);
    }

    #[test]
    fn uppercase_after_lt_without_tag_shape_is_text() {
        let tokens = tokenize("a<B)").unwrap();
        assert_eq!(tokens, vec![Token::Text("a<B)".to_string())]);
    }

    #[test]
    fn capitalized_tag_shapes_inside_script_style_and_comments_stay_text() {
        for src in [
            "<script>if (a<MAX && b){}</script>",
            "<script>x<Y>z</script>",
            "<SCRIPT type=\"x\">x<Y>z</SCRIPT>",
            "<style>a<B >c</style>",
            "<!-- <Header /> -->",
        ] {
            assert_eq!(
                tokenize(src).unwrap(),
                vec![Token::Text(src.to_string())],
                "{src}"
            );
        }
        // Outside the region the tag is refused again.
        let tokens = tokenize("<script>x<Y>z</script><Header />").unwrap();
        assert_eq!(
            tokens.last(),
            Some(&Token::CapitalizedTag("Header".to_string()))
        );
        let tokens = tokenize("<!-- c --><Header />").unwrap();
        assert_eq!(
            tokens.last(),
            Some(&Token::CapitalizedTag("Header".to_string()))
        );
    }

    #[test]
    fn uppercase_html_element_names_stay_text() {
        let tokens = tokenize("<DIV>x<BR></DIV>").unwrap();
        assert_eq!(tokens, vec![Token::Text("<DIV>x<BR></DIV>".to_string())]);
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
                    Token::CapitalizedTag(_) => {}
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
