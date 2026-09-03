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

fn parse_jsx_component(chars: &mut std::iter::Peekable<std::str::Chars>) -> Result<Token> {
    let mut name = String::new();
    let mut props = Vec::new();

    // Parse component name
    while let Some(&ch) = chars.peek() {
        if ch.is_alphanumeric() || ch == '_' {
            name.push(ch);
            chars.next();
        } else {
            break;
        }
    }

    // Skip whitespace
    while chars.peek() == Some(&' ') || chars.peek() == Some(&'\n') || chars.peek() == Some(&'\t') {
        chars.next();
    }

    // Parse props
    while let Some(&ch) = chars.peek() {
        if ch == '/' || ch == '>' {
            break;
        }

        if ch.is_alphabetic() {
            let mut prop_name = String::new();
            let mut prop_value = String::new();

            // Parse prop name
            while let Some(&ch) = chars.peek() {
                if ch == '=' || ch.is_whitespace() {
                    break;
                }
                prop_name.push(ch);
                chars.next();
            }

            // Skip whitespace and =
            while chars.peek() == Some(&' ') || chars.peek() == Some(&'=') {
                chars.next();
            }

            // Parse prop value
            if chars.peek() == Some(&'"') || chars.peek() == Some(&'\'') {
                let quote = chars.next().unwrap();
                for ch in chars.by_ref() {
                    if ch == quote {
                        break;
                    }
                    prop_value.push(ch);
                }
            } else if chars.peek() == Some(&'{') {
                // Handle {expression} props
                chars.next(); // consume {
                let mut depth = 1;
                for ch in chars.by_ref() {
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
            while chars.peek() == Some(&' ')
                || chars.peek() == Some(&'\n')
                || chars.peek() == Some(&'\t')
            {
                chars.next();
            }
        } else {
            chars.next();
        }
    }

    // Check if self-closing
    if chars.peek() == Some(&'/') {
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
        if ch == '<' && chars.peek() == Some(&'/') {
            // Potential closing tag - verify it matches our component name
            // Save position by peeking ahead
            let mut tag_name = String::new();
            let mut temp_chars = chars.clone();
            temp_chars.next(); // consume / in temp iterator

            while let Some(&ch) = temp_chars.peek() {
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
                while chars.peek() != Some(&'>') {
                    chars.next();
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
/// `rest` is positioned ON the second opener character, and the scan starts
/// AFTER it, so the opener can never supply half the closer: `{%}` has no
/// `%}` of its own and is plain text, exactly as Django's regex sees it.
fn has_closer(rest: &std::iter::Peekable<std::str::Chars>, a: char, b: char) -> bool {
    let mut it = rest.clone();
    it.next(); // the second opener character is not part of a closer
    let mut prev = None;
    for c in it {
        if prev == Some(a) && c == b {
            return true;
        }
        prev = Some(c);
    }
    false
}

pub fn tokenize(source: &str) -> Result<Vec<Token>> {
    let mut tokens = Vec::new();
    let mut chars = source.chars().peekable();
    let mut current = String::new();

    while let Some(ch) = chars.next() {
        if ch == '<' {
            // Check if this is a JSX component (starts with uppercase)
            if let Some(&next_ch) = chars.peek() {
                if next_ch.is_uppercase() {
                    // JSX component detected
                    if !current.is_empty() {
                        tokens.push(Token::Text(current.clone()));
                        current.clear();
                    }
                    match parse_jsx_component(&mut chars) {
                        Ok(token) => tokens.push(token),
                        Err(_) => current.push(ch), // Fallback to text if parsing fails
                    }
                    continue;
                }
            }
            current.push(ch);
        } else if ch == '{' {
            if let Some(&next) = chars.peek() {
                match next {
                    '{' => {
                        // Variable start {{
                        if !has_closer(&chars, '}', '}') {
                            // No `}}` after: literal text, as in Django.
                            // Only the FIRST `{` is consumed — the second is
                            // re-examined next iteration and may open a real
                            // tag. See `has_closer`.
                            current.push(ch);
                            continue;
                        }
                        chars.next(); // consume second {
                        if !current.is_empty() {
                            tokens.push(Token::Text(current.clone()));
                            current.clear();
                        }

                        let mut var_content = String::new();

                        while let Some(ch) = chars.next() {
                            if ch == '}' && chars.peek() == Some(&'}') {
                                chars.next(); // consume second }
                                tokens.push(Token::Variable(var_content.trim().to_string()));
                                break;
                            } else {
                                var_content.push(ch);
                            }
                        }
                    }
                    '%' => {
                        // Tag start {%
                        if !has_closer(&chars, '%', '}') {
                            // No `%}` after: literal text. See the `{{` arm.
                            current.push(ch);
                            continue;
                        }
                        chars.next(); // consume %
                        if !current.is_empty() {
                            tokens.push(Token::Text(current.clone()));
                            current.clear();
                        }

                        let mut tag_content = String::new();

                        while let Some(ch) = chars.next() {
                            if ch == '%' && chars.peek() == Some(&'}') {
                                chars.next(); // consume }
                                let parts: Vec<String> = split_tag_args(&tag_content);

                                if let Some(tag_name) = parts.first() {
                                    tokens.push(Token::Tag(tag_name.clone(), parts[1..].to_vec()));
                                }
                                break;
                            } else {
                                tag_content.push(ch);
                            }
                        }
                    }
                    '#' => {
                        // Comment start {#
                        if !has_closer(&chars, '#', '}') {
                            // No `#}` after: literal text. See the `{{` arm.
                            current.push(ch);
                            continue;
                        }
                        chars.next(); // consume #
                        if !current.is_empty() {
                            tokens.push(Token::Text(current.clone()));
                            current.clear();
                        }

                        // Collect until `#}`. The text is kept so a raw-block
                        // body can re-emit the comment verbatim (#2558).
                        let mut comment_content = String::new();
                        while let Some(ch) = chars.next() {
                            if ch == '#' && chars.peek() == Some(&'}') {
                                chars.next(); // consume }
                                tokens.push(Token::Comment(comment_content.clone()));
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

    if !current.is_empty() {
        tokens.push(Token::Text(current));
    }

    Ok(tokens)
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
}
