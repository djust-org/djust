//! Template lexer for tokenizing Django template syntax

use djust_core::Result;

#[derive(Debug, Clone, PartialEq)]
pub enum Token {
    Text(String),
    Variable(String),         // {{ var }}
    Tag(String, Vec<String>), // {% tag args %}
    Comment,                  // {# comment #}
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
                        chars.next(); // consume second {
                        if !current.is_empty() {
                            tokens.push(Token::Text(current.clone()));
                            current.clear();
                        }

                        let mut var_content = String::new();
                        let _depth = 0;

                        while let Some(ch) = chars.next() {
                            if ch == '}' && chars.peek() == Some(&'}') {
                                chars.next(); // consume second }
                                tokens.push(Token::Variable(var_content.trim().to_string()));
                                var_content.clear();
                                break;
                            } else {
                                var_content.push(ch);
                            }
                        }
                    }
                    '%' => {
                        // Tag start {%
                        chars.next(); // consume %
                        if !current.is_empty() {
                            tokens.push(Token::Text(current.clone()));
                            current.clear();
                        }

                        let mut tag_content = String::new();

                        while let Some(ch) = chars.next() {
                            if ch == '%' && chars.peek() == Some(&'}') {
                                chars.next(); // consume }
                                let parts: Vec<String> = tag_content
                                    .split_whitespace()
                                    .map(|s| s.to_string())
                                    .collect();

                                if let Some(tag_name) = parts.first() {
                                    tokens.push(Token::Tag(tag_name.clone(), parts[1..].to_vec()));
                                }
                                tag_content.clear();
                                break;
                            } else {
                                tag_content.push(ch);
                            }
                        }
                    }
                    '#' => {
                        // Comment start {#
                        chars.next(); // consume #
                        if !current.is_empty() {
                            tokens.push(Token::Text(current.clone()));
                            current.clear();
                        }

                        // Skip until #}
                        while let Some(ch) = chars.next() {
                            if ch == '#' && chars.peek() == Some(&'}') {
                                chars.next(); // consume }
                                tokens.push(Token::Comment);
                                break;
                            }
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
    fn test_tokenize_comment() {
        let tokens = tokenize("Hello {# comment #} World").unwrap();
        assert_eq!(
            tokens,
            vec![
                Token::Text("Hello ".to_string()),
                Token::Comment,
                Token::Text(" World".to_string()),
            ]
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
}
