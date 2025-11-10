//! Template parser for building an AST from tokens

use crate::lexer::Token;
use djust_core::{DjangoRustError, Result};

#[derive(Debug, Clone)]
pub enum Node {
    Text(String),
    Variable(String, Vec<(String, Option<String>)>), // variable name, (filter, arg)
    If {
        condition: String,
        true_nodes: Vec<Node>,
        false_nodes: Vec<Node>,
    },
    For {
        var_name: String,
        iterable: String,
        nodes: Vec<Node>,
    },
    Block {
        name: String,
        nodes: Vec<Node>,
    },
    Include(String),
    Comment,
    ReactComponent {
        name: String,
        props: Vec<(String, String)>,
        children: Vec<Node>,
    },
    RustComponent {
        name: String,
        props: Vec<(String, String)>,
    },
}

pub fn parse(tokens: &[Token]) -> Result<Vec<Node>> {
    let mut nodes = Vec::new();
    let mut i = 0;

    while i < tokens.len() {
        let node = parse_token(tokens, &mut i)?;
        if let Some(n) = node {
            nodes.push(n);
        }
        i += 1;
    }

    Ok(nodes)
}

fn parse_token(tokens: &[Token], i: &mut usize) -> Result<Option<Node>> {
    match &tokens[*i] {
        Token::Text(text) => Ok(Some(Node::Text(text.clone()))),

        Token::Variable(var) => {
            // Parse variable and filters: {{ var|filter1:arg1|filter2 }}
            let parts: Vec<String> = var.split('|').map(|s| s.trim().to_string()).collect();
            let var_name = parts[0].clone();

            // Parse each filter and its optional argument
            let filters: Vec<(String, Option<String>)> = parts[1..].iter().map(|filter_spec| {
                if let Some(colon_pos) = filter_spec.find(':') {
                    let filter_name = filter_spec[..colon_pos].trim().to_string();
                    let mut arg = filter_spec[colon_pos + 1..].trim().to_string();

                    // Strip surrounding quotes from the argument (single or double)
                    if (arg.starts_with('"') && arg.ends_with('"')) ||
                       (arg.starts_with('\'') && arg.ends_with('\'')) {
                        if arg.len() >= 2 {
                            arg = arg[1..arg.len()-1].to_string();
                        }
                    }

                    (filter_name, Some(arg))
                } else {
                    (filter_spec.clone(), None)
                }
            }).collect();

            Ok(Some(Node::Variable(var_name, filters)))
        }

        Token::Tag(tag_name, args) => {
            match tag_name.as_str() {
                "if" => {
                    let condition = args.join(" ");
                    let (true_nodes, false_nodes, end_pos) = parse_if_block(tokens, *i + 1)?;
                    *i = end_pos;
                    Ok(Some(Node::If {
                        condition,
                        true_nodes,
                        false_nodes,
                    }))
                }

                "for" => {
                    if args.len() < 3 || args[1] != "in" {
                        return Err(DjangoRustError::TemplateError(
                            "Invalid for tag syntax. Expected: {% for var in iterable %}".to_string()
                        ));
                    }
                    let var_name = args[0].clone();
                    let iterable = args[2..].join(" ");
                    let (nodes, end_pos) = parse_for_block(tokens, *i + 1)?;
                    *i = end_pos;
                    Ok(Some(Node::For {
                        var_name,
                        iterable,
                        nodes,
                    }))
                }

                "block" => {
                    if args.is_empty() {
                        return Err(DjangoRustError::TemplateError(
                            "Block tag requires a name".to_string()
                        ));
                    }
                    let name = args[0].clone();
                    let (nodes, end_pos) = parse_block(tokens, *i + 1)?;
                    *i = end_pos;
                    Ok(Some(Node::Block { name, nodes }))
                }

                "include" => {
                    if args.is_empty() {
                        return Err(DjangoRustError::TemplateError(
                            "Include tag requires a template name".to_string()
                        ));
                    }
                    Ok(Some(Node::Include(args[0].clone())))
                }

                "endif" | "endfor" | "endblock" | "else" => {
                    // These are handled by their opening tags
                    Ok(None)
                }

                _ => {
                    // Unknown tag, treat as comment
                    Ok(Some(Node::Comment))
                }
            }
        }

        Token::JsxComponent { name, props, children, .. } => {
            // Check if this is a Rust component (starts with "Rust")
            if name.starts_with("Rust") {
                // Rust components are rendered server-side, no children support
                Ok(Some(Node::RustComponent {
                    name: name.clone(),
                    props: props.clone(),
                }))
            } else {
                // Convert token children to Node children for React components
                let mut child_nodes = Vec::new();
                for child in children {
                    if let Token::Text(text) = child {
                        child_nodes.push(Node::Text(text.clone()));
                    }
                }

                Ok(Some(Node::ReactComponent {
                    name: name.clone(),
                    props: props.clone(),
                    children: child_nodes,
                }))
            }
        }

        Token::Comment => Ok(Some(Node::Comment)),
    }
}

fn parse_if_block(tokens: &[Token], start: usize) -> Result<(Vec<Node>, Vec<Node>, usize)> {
    let mut true_nodes = Vec::new();
    let mut false_nodes = Vec::new();
    let mut in_else = false;
    let mut i = start;

    while i < tokens.len() {
        match &tokens[i] {
            Token::Tag(name, _) if name == "else" => {
                in_else = true;
                i += 1;
                continue;
            }
            Token::Tag(name, _) if name == "endif" => {
                return Ok((true_nodes, false_nodes, i));
            }
            _ => {
                if let Some(node) = parse_token(tokens, &mut i)? {
                    if in_else {
                        false_nodes.push(node);
                    } else {
                        true_nodes.push(node);
                    }
                }
            }
        }
        i += 1;
    }

    Err(DjangoRustError::TemplateError(
        "Unclosed if tag".to_string()
    ))
}

fn parse_for_block(tokens: &[Token], start: usize) -> Result<(Vec<Node>, usize)> {
    let mut nodes = Vec::new();
    let mut i = start;

    while i < tokens.len() {
        if let Token::Tag(name, _) = &tokens[i] {
            if name == "endfor" {
                return Ok((nodes, i));
            }
        }

        if let Some(node) = parse_token(tokens, &mut i)? {
            nodes.push(node);
        }
        i += 1;
    }

    Err(DjangoRustError::TemplateError(
        "Unclosed for tag".to_string()
    ))
}

fn parse_block(tokens: &[Token], start: usize) -> Result<(Vec<Node>, usize)> {
    let mut nodes = Vec::new();
    let mut i = start;

    while i < tokens.len() {
        if let Token::Tag(name, _) = &tokens[i] {
            if name == "endblock" {
                return Ok((nodes, i));
            }
        }

        if let Some(node) = parse_token(tokens, &mut i)? {
            nodes.push(node);
        }
        i += 1;
    }

    Err(DjangoRustError::TemplateError(
        "Unclosed block tag".to_string()
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::lexer::tokenize;

    #[test]
    fn test_parse_simple() {
        let tokens = tokenize("Hello {{ name }}").unwrap();
        let nodes = parse(&tokens).unwrap();
        assert_eq!(nodes.len(), 2);
    }

    #[test]
    fn test_parse_if() {
        let tokens = tokenize("{% if true %}yes{% endif %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        assert_eq!(nodes.len(), 1);
        match &nodes[0] {
            Node::If { .. } => (),
            _ => panic!("Expected If node"),
        }
    }
}
