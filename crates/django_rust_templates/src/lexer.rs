//! Template lexer for tokenizing Django template syntax

use django_rust_core::{DjangoRustError, Result};

#[derive(Debug, Clone, PartialEq)]
pub enum Token {
    Text(String),
    Variable(String),      // {{ var }}
    Tag(String, Vec<String>), // {% tag args %}
    Comment,               // {# comment #}
}

pub fn tokenize(source: &str) -> Result<Vec<Token>> {
    let mut tokens = Vec::new();
    let mut chars = source.chars().peekable();
    let mut current = String::new();

    while let Some(ch) = chars.next() {
        if ch == '{' {
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
                        let mut depth = 0;

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
                                    .trim()
                                    .split_whitespace()
                                    .map(|s| s.to_string())
                                    .collect();

                                if let Some(tag_name) = parts.first() {
                                    tokens.push(Token::Tag(
                                        tag_name.clone(),
                                        parts[1..].to_vec(),
                                    ));
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
        assert_eq!(tokens, vec![
            Token::Text("Hello ".to_string()),
            Token::Variable("name".to_string()),
        ]);
    }

    #[test]
    fn test_tokenize_tag() {
        let tokens = tokenize("{% if true %}yes{% endif %}").unwrap();
        assert_eq!(tokens[0], Token::Tag("if".to_string(), vec!["true".to_string()]));
    }

    #[test]
    fn test_tokenize_comment() {
        let tokens = tokenize("Hello {# comment #} World").unwrap();
        assert_eq!(tokens, vec![
            Token::Text("Hello ".to_string()),
            Token::Comment,
            Token::Text(" World".to_string()),
        ]);
    }
}
