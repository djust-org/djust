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
        reversed: bool,
        nodes: Vec<Node>,
    },
    Block {
        name: String,
        nodes: Vec<Node>,
    },
    Include(String),
    Comment,
    CsrfToken,
    Static(String), // Path to static file
    With {
        assignments: Vec<(String, String)>, // var_name, expression
        nodes: Vec<Node>,
    },
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
            let filters: Vec<(String, Option<String>)> = parts[1..]
                .iter()
                .map(|filter_spec| {
                    if let Some(colon_pos) = filter_spec.find(':') {
                        let filter_name = filter_spec[..colon_pos].trim().to_string();
                        let mut arg = filter_spec[colon_pos + 1..].trim().to_string();

                        // Strip surrounding quotes from the argument (single or double)
                        if ((arg.starts_with('"') && arg.ends_with('"'))
                            || (arg.starts_with('\'') && arg.ends_with('\'')))
                            && arg.len() >= 2
                        {
                            arg = arg[1..arg.len() - 1].to_string();
                        }

                        (filter_name, Some(arg))
                    } else {
                        (filter_spec.clone(), None)
                    }
                })
                .collect();

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
                            "Invalid for tag syntax. Expected: {% for var in iterable %}"
                                .to_string(),
                        ));
                    }
                    let var_name = args[0].clone();

                    // Check if the last argument is "reversed"
                    let mut iterable_parts: Vec<String> = args[2..].to_vec();
                    let reversed = if iterable_parts.last().map(|s| s.as_str()) == Some("reversed")
                    {
                        iterable_parts.pop(); // Remove "reversed" from iterable
                        true
                    } else {
                        false
                    };

                    let iterable = iterable_parts.join(" ");
                    let (nodes, end_pos) = parse_for_block(tokens, *i + 1)?;
                    *i = end_pos;
                    Ok(Some(Node::For {
                        var_name,
                        iterable,
                        reversed,
                        nodes,
                    }))
                }

                "block" => {
                    if args.is_empty() {
                        return Err(DjangoRustError::TemplateError(
                            "Block tag requires a name".to_string(),
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
                            "Include tag requires a template name".to_string(),
                        ));
                    }
                    Ok(Some(Node::Include(args[0].clone())))
                }

                "csrf_token" => {
                    // {% csrf_token %} - generates CSRF token hidden input
                    Ok(Some(Node::CsrfToken))
                }

                "static" => {
                    // {% static 'path/to/file' %} - generates static file URL
                    if args.is_empty() {
                        return Err(DjangoRustError::TemplateError(
                            "Static tag requires a file path".to_string(),
                        ));
                    }
                    // Remove quotes from path if present
                    let path = args[0].trim_matches(|c| c == '"' || c == '\'').to_string();
                    Ok(Some(Node::Static(path)))
                }

                "comment" => {
                    // {% comment %} tag - skip content until {% endcomment %}
                    // Find and skip to endcomment tag
                    let mut depth = 1;
                    let mut j = *i + 1;
                    while j < tokens.len() && depth > 0 {
                        if let Token::Tag(tag_name, _) = &tokens[j] {
                            if tag_name == "comment" {
                                depth += 1;
                            } else if tag_name == "endcomment" {
                                depth -= 1;
                            }
                        }
                        j += 1;
                    }
                    *i = j - 1; // Point to endcomment tag
                    Ok(Some(Node::Comment))
                }

                "endcomment" => {
                    // Handled by comment tag
                    Ok(None)
                }

                "verbatim" => {
                    // {% verbatim %} tag - output content literally without template processing
                    // Collect all content between {% verbatim %} and {% endverbatim %}
                    let mut content = String::new();
                    let mut j = *i + 1;

                    while j < tokens.len() {
                        match &tokens[j] {
                            Token::Tag(name, _) if name == "endverbatim" => {
                                *i = j; // Point to endverbatim tag
                                return Ok(Some(Node::Text(content)));
                            }
                            Token::Text(text) => content.push_str(text),
                            Token::Variable(var) => {
                                // Output the raw variable syntax
                                content.push_str(&format!("{{{{ {} }}}}", var));
                            }
                            Token::Tag(name, args) => {
                                // Output the raw tag syntax
                                let args_str = if args.is_empty() {
                                    String::new()
                                } else {
                                    format!(" {}", args.join(" "))
                                };
                                content.push_str(&format!("{{% {}{} %}}", name, args_str));
                            }
                            Token::Comment => {
                                // Skip comments
                            }
                            _ => {}
                        }
                        j += 1;
                    }

                    Err(DjangoRustError::TemplateError(
                        "Unclosed verbatim tag".to_string(),
                    ))
                }

                "endverbatim" => {
                    // Handled by verbatim tag
                    Ok(None)
                }

                "with" => {
                    // {% with var=value var2=value2 %} ... {% endwith %}
                    // Parse assignments
                    let mut assignments = Vec::new();
                    for arg in args {
                        if let Some(eq_pos) = arg.find('=') {
                            let var_name = arg[..eq_pos].trim().to_string();
                            let expression = arg[eq_pos + 1..].trim().to_string();
                            assignments.push((var_name, expression));
                        }
                    }

                    let (nodes, end_pos) = parse_with_block(tokens, *i + 1)?;
                    *i = end_pos;
                    Ok(Some(Node::With { assignments, nodes }))
                }

                "endwith" => {
                    // Handled by with tag
                    Ok(None)
                }

                "load" => {
                    // {% load static %} - For now, just treat as a no-op comment
                    // In full Django, this loads template tag libraries
                    // Our static files are handled via {% static %} tag
                    Ok(Some(Node::Comment))
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

        Token::JsxComponent {
            name,
            props,
            children,
            ..
        } => {
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
        "Unclosed if tag".to_string(),
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
        "Unclosed for tag".to_string(),
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
        "Unclosed block tag".to_string(),
    ))
}

fn parse_with_block(tokens: &[Token], start: usize) -> Result<(Vec<Node>, usize)> {
    let mut nodes = Vec::new();
    let mut i = start;

    while i < tokens.len() {
        if let Token::Tag(name, _) = &tokens[i] {
            if name == "endwith" {
                return Ok((nodes, i));
            }
        }

        if let Some(node) = parse_token(tokens, &mut i)? {
            nodes.push(node);
        }
        i += 1;
    }

    Err(DjangoRustError::TemplateError(
        "Unclosed with tag".to_string(),
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

    #[test]
    fn test_verbatim_tag() {
        let tokens = tokenize("{% verbatim %}{{ name }}{% endverbatim %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        assert_eq!(nodes.len(), 1);
        match &nodes[0] {
            Node::Text(text) => assert_eq!(text, "{{ name }}"),
            _ => panic!("Expected Text node"),
        }
    }

    #[test]
    fn test_verbatim_tag_with_tags() {
        let tokens =
            tokenize("{% verbatim %}{% if true %}{{ value }}{% endif %}{% endverbatim %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        assert_eq!(nodes.len(), 1);
        match &nodes[0] {
            Node::Text(text) => assert_eq!(text, "{% if true %}{{ value }}{% endif %}"),
            _ => panic!("Expected Text node"),
        }
    }

    #[test]
    fn test_verbatim_tag_mixed() {
        let tokens = tokenize("Before{% verbatim %}{{ name }}{% endverbatim %}After").unwrap();
        let nodes = parse(&tokens).unwrap();
        assert_eq!(nodes.len(), 3);
        match &nodes[0] {
            Node::Text(text) => assert_eq!(text, "Before"),
            _ => panic!("Expected Text node"),
        }
        match &nodes[1] {
            Node::Text(text) => assert_eq!(text, "{{ name }}"),
            _ => panic!("Expected Text node from verbatim"),
        }
        match &nodes[2] {
            Node::Text(text) => assert_eq!(text, "After"),
            _ => panic!("Expected Text node"),
        }
    }

    #[test]
    fn test_with_tag() {
        let tokens = tokenize("{% with name=user.name %}{{ name }}{% endwith %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        assert_eq!(nodes.len(), 1);
        match &nodes[0] {
            Node::With { assignments, nodes } => {
                assert_eq!(assignments.len(), 1);
                assert_eq!(assignments[0].0, "name");
                assert_eq!(assignments[0].1, "user.name");
                assert_eq!(nodes.len(), 1);
            }
            _ => panic!("Expected With node"),
        }
    }

    #[test]
    fn test_with_tag_multiple_assignments() {
        let tokens = tokenize("{% with a=x b=y %}{{ a }} {{ b }}{% endwith %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        match &nodes[0] {
            Node::With { assignments, .. } => {
                assert_eq!(assignments.len(), 2);
                assert_eq!(assignments[0].0, "a");
                assert_eq!(assignments[0].1, "x");
                assert_eq!(assignments[1].0, "b");
                assert_eq!(assignments[1].1, "y");
            }
            _ => panic!("Expected With node"),
        }
    }

    #[test]
    fn test_load_tag() {
        let tokens = tokenize("{% load static %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        assert_eq!(nodes.len(), 1);
        // Load is treated as a comment (no-op)
        match &nodes[0] {
            Node::Comment => (),
            _ => panic!("Expected Comment node for load tag"),
        }
    }
}
