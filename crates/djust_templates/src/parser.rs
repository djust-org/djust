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
        var_names: Vec<String>, // Supports tuple unpacking: {% for a, b in items %}
        iterable: String,
        reversed: bool,
        nodes: Vec<Node>,
        empty_nodes: Vec<Node>, // Rendered when iterable is empty
    },
    Block {
        name: String,
        nodes: Vec<Node>,
    },
    Extends(String), // Parent template path
    Include {
        template: String,
        with_vars: Vec<(String, String)>, // key=value assignments
        only: bool,                       // if true, only pass with_vars, not parent context
    },
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
    /// Custom template tag handled by a Python callback.
    ///
    /// This is used for Django-specific tags like `{% url %}` and `{% static %}`
    /// that require Python runtime access (e.g., Django's URL resolver).
    ///
    /// The handler is looked up in the tag registry at parse time, and called
    /// at render time with args and context.
    CustomTag {
        /// Tag name (e.g., "url", "static")
        name: String,
        /// Arguments from the template tag as raw strings
        args: Vec<String>,
    },
    /// Unsupported template tag - renders as HTML comment with warning.
    ///
    /// This is used for Django template tags that don't have a registered
    /// handler. Instead of silently failing, it outputs a visible warning
    /// in development to help developers identify missing tag implementations.
    UnsupportedTag {
        /// Tag name (e.g., "spaceless", "verbatim")
        name: String,
        /// Original arguments from the tag
        args: Vec<String>,
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
                    if args.len() < 3 {
                        return Err(DjangoRustError::TemplateError(
                            "Invalid for tag syntax. Expected: {% for var in iterable %} or {% for a, b in iterable %}"
                                .to_string(),
                        ));
                    }

                    // Parse variable names - support tuple unpacking
                    //
                    // IMPORTANT FOR JIT OPTIMIZATION:
                    // Tuple unpacking ({% for val, label in STATUS_CHOICES %}) allows the JIT
                    // serializer to understand which fields of each item are accessed in the loop.
                    // For example, in {% for lease in leases %}{{ lease.tenant.name }}{% endfor %},
                    // the loop variable "lease" must transfer its path context so that
                    // "lease.tenant.name" is correctly identified for select_related() optimization.
                    //
                    // This parsing logic enables:
                    // 1. Single variable: {% for item in items %} → var_names = ["item"]
                    // 2. Tuple unpacking: {% for key, val in items %} → var_names = ["key", "val"]
                    //
                    // Find the "in" keyword to separate var names from iterable
                    let in_pos = args.iter().position(|arg| arg == "in").ok_or_else(|| {
                        DjangoRustError::TemplateError(
                            "Invalid for tag syntax. Expected: {% for var in iterable %}"
                                .to_string(),
                        )
                    })?;

                    if in_pos == 0 {
                        return Err(DjangoRustError::TemplateError(
                            "For tag requires at least one variable name before 'in'".to_string(),
                        ));
                    }

                    // Extract variable names before "in"
                    // Remove commas and collect variable names
                    // Note: Lexer splits on whitespace, so "{% for val, label %}" becomes ["val,", "label"]
                    let var_names: Vec<String> = args[0..in_pos]
                        .iter()
                        .filter(|&arg| arg != ",") // Filter standalone commas
                        .map(|s| s.trim_end_matches(',').to_string()) // Strip trailing commas
                        .collect();

                    if var_names.is_empty() {
                        return Err(DjangoRustError::TemplateError(
                            "For tag requires at least one variable name".to_string(),
                        ));
                    }

                    // Check if the last argument is "reversed"
                    let mut iterable_parts: Vec<String> = args[in_pos + 1..].to_vec();
                    let reversed = if iterable_parts.last().map(|s| s.as_str()) == Some("reversed")
                    {
                        iterable_parts.pop(); // Remove "reversed" from iterable
                        true
                    } else {
                        false
                    };

                    let iterable = iterable_parts.join(" ");
                    let (nodes, empty_nodes, end_pos) = parse_for_block(tokens, *i + 1)?;
                    *i = end_pos;
                    Ok(Some(Node::For {
                        var_names,
                        iterable,
                        reversed,
                        nodes,
                        empty_nodes,
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

                "extends" => {
                    // {% extends "parent.html" %}
                    if args.is_empty() {
                        return Err(DjangoRustError::TemplateError(
                            "Extends tag requires a template name".to_string(),
                        ));
                    }
                    // Remove quotes from template name
                    let template = args[0].trim_matches(|c| c == '"' || c == '\'').to_string();
                    Ok(Some(Node::Extends(template)))
                }

                "include" => {
                    if args.is_empty() {
                        return Err(DjangoRustError::TemplateError(
                            "Include tag requires a template name".to_string(),
                        ));
                    }
                    let template = args[0].clone();
                    let mut with_vars = Vec::new();
                    let mut only = false;

                    // Parse remaining args for 'with' and 'only' keywords
                    let mut i = 1;
                    while i < args.len() {
                        if args[i] == "with" {
                            // Parse key=value pairs after 'with'
                            i += 1;
                            while i < args.len() && args[i] != "only" {
                                if args[i].contains('=') {
                                    let parts: Vec<&str> = args[i].splitn(2, '=').collect();
                                    if parts.len() == 2 {
                                        with_vars
                                            .push((parts[0].to_string(), parts[1].to_string()));
                                    }
                                }
                                i += 1;
                            }
                        } else if args[i] == "only" {
                            only = true;
                            i += 1;
                        } else {
                            i += 1;
                        }
                    }

                    Ok(Some(Node::Include {
                        template,
                        with_vars,
                        only,
                    }))
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
                                content.push_str(&format!("{{{{ {var} }}}}"));
                            }
                            Token::Tag(name, args) => {
                                // Output the raw tag syntax
                                let args_str = if args.is_empty() {
                                    String::new()
                                } else {
                                    format!(" {}", args.join(" "))
                                };
                                content.push_str(&format!("{{% {name}{args_str} %}}"));
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

                "endif" | "endfor" | "endblock" | "else" | "elif" => {
                    // These are handled by their opening tags
                    Ok(None)
                }

                _ => {
                    // Check if a Python handler is registered for this tag
                    if crate::registry::handler_exists(tag_name) {
                        // Handler exists - create CustomTag node
                        Ok(Some(Node::CustomTag {
                            name: tag_name.clone(),
                            args: args.clone(),
                        }))
                    } else {
                        // Unknown tag with no handler - create warning node
                        Ok(Some(Node::UnsupportedTag {
                            name: tag_name.clone(),
                            args: args.clone(),
                        }))
                    }
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
            Token::Tag(name, args) if name == "elif" => {
                // elif after else is invalid (matches Django behavior)
                if in_else {
                    return Err(DjangoRustError::TemplateError(
                        "{% elif %} cannot appear after {% else %}".to_string(),
                    ));
                }
                // elif is equivalent to: else + nested if
                // {% elif condition %} becomes {% else %}{% if condition %}...{% endif %}
                let elif_condition = args.join(" ");
                let (elif_true, elif_false, end_pos) = parse_if_block(tokens, i + 1)?;
                false_nodes.push(Node::If {
                    condition: elif_condition,
                    true_nodes: elif_true,
                    false_nodes: elif_false,
                });
                return Ok((true_nodes, false_nodes, end_pos));
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

fn parse_for_block(tokens: &[Token], start: usize) -> Result<(Vec<Node>, Vec<Node>, usize)> {
    let mut nodes = Vec::new();
    let mut empty_nodes = Vec::new();
    let mut in_empty_block = false;
    let mut i = start;

    while i < tokens.len() {
        if let Token::Tag(name, _) = &tokens[i] {
            if name == "endfor" {
                return Ok((nodes, empty_nodes, i));
            } else if name == "empty" {
                // Switch to parsing the empty block
                in_empty_block = true;
                i += 1;
                continue;
            }
        }

        if let Some(node) = parse_token(tokens, &mut i)? {
            if in_empty_block {
                empty_nodes.push(node);
            } else {
                nodes.push(node);
            }
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

/// Extract all variable paths from a Django template for JIT serialization.
///
/// Parses the template and returns a mapping of root variable names to their access paths.
/// This function is used to analyze which Django ORM fields need to be serialized for
/// efficient template rendering in Rust.
///
/// # Behavior
///
/// - **Empty templates**: Returns an empty HashMap
/// - **Malformed templates**: Returns an error if template cannot be parsed
/// - **Duplicate paths**: Automatically deduplicated and sorted
/// - **Nested variables**: Extracts full attribute chains (e.g., `user.profile.name`)
/// - **Template tags**: Extracts variables from for/if/with/block tags
/// - **Filters**: Ignores filters but preserves variable paths
///
/// # Performance
///
/// Typically completes in <5ms for standard templates. See benchmarks for details.
///
/// # Example
///
/// ```rust
/// use std::collections::HashMap;
/// use djust_templates::extract_template_variables;
///
/// let template = "{{ lease.property.name }} {{ lease.tenant.user.email }}";
/// let vars = extract_template_variables(template).unwrap();
///
/// // Returns: {"lease": ["property.name", "tenant.user.email"]}
/// assert_eq!(vars.get("lease").unwrap().len(), 2);
/// ```
///
/// # Use Case
///
/// This function enables automatic serialization of only the required Django ORM fields:
///
/// ```ignore
/// // In Python LiveView
/// class LeaseView(LiveView):
///     def get_context_data(self):
///         # Extract template variables automatically
///         vars = extract_template_variables(self.template_string)
///         # vars = {"lease": ["property.name", "tenant.user.email"]}
///
///         # Generate optimized query
///         lease = Lease.objects.select_related('property', 'tenant__user').first()
///
///         # Serialize only required fields
///         return {"lease": lease}  # Auto-serializes property.name and tenant.user.email
/// ```
pub fn extract_template_variables(
    template: &str,
) -> Result<std::collections::HashMap<String, Vec<String>>> {
    use std::collections::HashMap;

    // Tokenize and parse the template
    let tokens = crate::lexer::tokenize(template)?;
    let nodes = parse(&tokens)?;

    let mut variables: HashMap<String, Vec<String>> = HashMap::new();

    // Walk the AST and extract variable paths
    extract_from_nodes(&nodes, &mut variables);

    // Deduplicate and sort paths for each variable
    for paths in variables.values_mut() {
        paths.sort();
        paths.dedup();
    }

    Ok(variables)
}

/// Recursively extract variable paths from AST nodes
fn extract_from_nodes(
    nodes: &[Node],
    variables: &mut std::collections::HashMap<String, Vec<String>>,
) {
    for node in nodes {
        match node {
            Node::Variable(var_expr, _filters) => {
                // Extract from variable: {{ variable.path }}
                extract_from_variable(var_expr, variables);
            }
            Node::If {
                condition,
                true_nodes,
                false_nodes,
            } => {
                // Extract from condition: {% if variable.path %}
                extract_from_expression(condition, variables);
                // Recurse into if branches
                extract_from_nodes(true_nodes, variables);
                extract_from_nodes(false_nodes, variables);
            }
            Node::For {
                var_names,
                iterable,
                nodes,
                reversed: _,
                empty_nodes,
            } => {
                // Extract from iterable: {% for item in variable.path %}
                extract_from_variable(iterable, variables);
                // Recurse into for body
                extract_from_nodes(nodes, variables);
                // Recurse into empty block
                extract_from_nodes(empty_nodes, variables);

                // FIX: Transfer paths from loop variables to iterable AND keep loop variables
                // Example: {% for property in properties %}{{ property.name }}{% endfor %}
                // - Before: properties=[], property=[name, bedrooms, ...]
                // - After:  properties=[name, bedrooms, ...], property=[name, bedrooms, ...]
                //
                // For tuple unpacking: {% for val, label in status_choices %}{{ val }} {{ label }}{% endfor %}
                // - Before: status_choices=[], val=[], label=[]
                // - After:  status_choices=[0, 1], val=[], label=[]
                //
                // Loop variables are kept for:
                // - IDE autocomplete/type checking
                // - Template debugging
                // - Documentation generation
                for var_name in var_names {
                    if let Some(loop_var_paths) = variables.get(var_name) {
                        // Transfer paths from loop variable to iterable (but keep loop var)
                        // Prepend the iterable suffix so paths are correctly nested.
                        // Example: {% for tag in post.tags.all %}{{ tag.name }}{% endfor %}
                        //   iterable = "post.tags.all", loop var paths = ["name", "url"]
                        //   iterable_name = "post", iterable_suffix = "tags.all"
                        //   transferred paths = ["tags.all.name", "tags.all.url"]
                        let iterable_name = iterable.split('.').next().unwrap_or(iterable);
                        let iterable_suffix = if iterable.len() > iterable_name.len() + 1 {
                            &iterable[iterable_name.len() + 1..]
                        } else {
                            ""
                        };
                        let prefixed_paths: Vec<String> = loop_var_paths
                            .iter()
                            .map(|path| {
                                if iterable_suffix.is_empty() {
                                    path.clone()
                                } else {
                                    format!("{}.{}", iterable_suffix, path)
                                }
                            })
                            .collect();
                        variables
                            .entry(iterable_name.to_string())
                            .or_default()
                            .extend(prefixed_paths);
                    }
                }
            }
            Node::Block { nodes, name: _ } => {
                // Recurse into block body
                extract_from_nodes(nodes, variables);
            }
            Node::With { assignments, nodes } => {
                // Extract from with assignments: {% with x=variable.path %}
                for (_var_name, expr) in assignments {
                    extract_from_variable(expr, variables);
                }
                // Recurse into with body
                extract_from_nodes(nodes, variables);
            }
            Node::ReactComponent {
                props,
                children,
                name: _,
            } => {
                // Extract from component props
                for (_prop_name, prop_value) in props {
                    extract_from_variable(prop_value, variables);
                }
                // Recurse into children
                extract_from_nodes(children, variables);
            }
            Node::RustComponent { props, name: _ } => {
                // Extract from component props
                for (_prop_name, prop_value) in props {
                    extract_from_variable(prop_value, variables);
                }
            }
            Node::CustomTag { args, name: _ } => {
                // Extract variables from custom tag arguments
                // Arguments may reference context variables (e.g., {% url 'name' post.slug %})
                for arg in args {
                    // Skip string literals
                    if (arg.starts_with('"') && arg.ends_with('"'))
                        || (arg.starts_with('\'') && arg.ends_with('\''))
                    {
                        continue;
                    }
                    // Skip named parameters (key=value) - extract the value part
                    let value = if let Some(eq_pos) = arg.find('=') {
                        arg[eq_pos + 1..].trim()
                    } else {
                        arg.trim()
                    };
                    // Check if it looks like a variable (not a number, not a string literal)
                    if !value.is_empty()
                        && !value.starts_with('"')
                        && !value.starts_with('\'')
                        && !value.chars().all(|c| c.is_numeric() || c == '.')
                    {
                        extract_from_variable(value, variables);
                    }
                }
            }
            // Text, Comment, CsrfToken, Static, Include, Extends don't contain variable references
            _ => {}
        }
    }
}

/// Extract variable path from a single variable reference
///
/// Examples:
/// - "lease.property.name" -> root="lease", path="property.name"
/// - "user.email" -> root="user", path="email"
/// - "count" -> root="count", path="" (no sub-path)
fn extract_from_variable(
    var_expr: &str,
    variables: &mut std::collections::HashMap<String, Vec<String>>,
) {
    // Split on '.' to get path components
    let parts: Vec<&str> = var_expr.split('.').collect();

    if parts.is_empty() {
        return;
    }

    let root = parts[0].to_string();

    if parts.len() == 1 {
        // Simple variable (no path)
        // Still track it, but with empty path
        variables.entry(root).or_default();
    } else {
        // Has a path (e.g., "lease.property.name")
        let path = parts[1..].join(".");
        variables.entry(root).or_default().push(path);
    }
}

/// Extract variable paths from an expression (like in if tags)
///
/// Handles:
/// - {% if lease.property %}
/// - {% if lease.tenant.user.email %}
///
/// # Known Limitations (Phase 1)
///
/// This uses simplified expression parsing that splits on whitespace and dots.
/// String literals with dots (e.g., "example.com") may be incorrectly extracted
/// as variable paths. This creates harmless false positives - extra variables
/// that won't be used in serialization.
///
/// **Impact**: Low - false positives don't break functionality
/// **Fix**: Phase 2 will implement full expression grammar parsing
fn extract_from_expression(
    expr: &str,
    variables: &mut std::collections::HashMap<String, Vec<String>>,
) {
    // Simple approach: look for word.word.word patterns
    // More sophisticated: parse the full expression grammar

    // Split by common operators and whitespace
    let tokens: Vec<&str> = expr
        .split(|c: char| c.is_whitespace() || "()[]{}=!<>&|+-*/%,".contains(c))
        .filter(|s| !s.is_empty())
        .collect();

    for token in tokens {
        // Check if this looks like a variable path (contains dots)
        if token.contains('.') && !token.starts_with('"') && !token.starts_with('\'') {
            extract_from_variable(token, variables);
        } else if !token.starts_with('"')
            && !token.starts_with('\'')
            && !token.chars().all(|c| c.is_numeric() || c == '.')
            && token.chars().any(|c| c.is_alphabetic())
        {
            // Simple variable name without path
            variables.entry(token.to_string()).or_default();
        }
    }
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

    #[test]
    fn test_extends_tag() {
        let tokens = tokenize("{% extends \"base.html\" %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        assert_eq!(nodes.len(), 1);
        match &nodes[0] {
            Node::Extends(template) => {
                assert_eq!(template, "base.html");
            }
            _ => panic!("Expected Extends node"),
        }
    }

    #[test]
    fn test_extends_tag_single_quotes() {
        let tokens = tokenize("{% extends 'parent.html' %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        match &nodes[0] {
            Node::Extends(template) => {
                assert_eq!(template, "parent.html");
            }
            _ => panic!("Expected Extends node"),
        }
    }

    #[test]
    fn test_extends_with_blocks() {
        let tokens =
            tokenize("{% extends \"base.html\" %}{% block content %}Hello{% endblock %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        assert_eq!(nodes.len(), 2);
        match &nodes[0] {
            Node::Extends(template) => assert_eq!(template, "base.html"),
            _ => panic!("Expected Extends node"),
        }
        match &nodes[1] {
            Node::Block { name, .. } => assert_eq!(name, "content"),
            _ => panic!("Expected Block node"),
        }
    }

    // Tests for variable extraction (JIT serialization)

    #[test]
    fn test_extract_simple_variable() {
        let template = "{{ name }}";
        let vars = extract_template_variables(template).unwrap();
        assert!(vars.contains_key("name"));
        assert_eq!(vars.get("name").unwrap().len(), 0); // No path, just root
    }

    #[test]
    fn test_extract_nested_variable() {
        let template = "{{ user.email }}";
        let vars = extract_template_variables(template).unwrap();
        assert!(vars.contains_key("user"));
        assert_eq!(vars.get("user").unwrap(), &vec!["email".to_string()]);
    }

    #[test]
    fn test_extract_multiple_paths() {
        let template = r#"
            {{ lease.property.name }}
            {{ lease.tenant.user.email }}
            {{ lease.end_date }}
        "#;
        let vars = extract_template_variables(template).unwrap();

        assert!(vars.contains_key("lease"));
        let lease_paths = vars.get("lease").unwrap();
        assert_eq!(lease_paths.len(), 3);
        assert!(lease_paths.contains(&"property.name".to_string()));
        assert!(lease_paths.contains(&"tenant.user.email".to_string()));
        assert!(lease_paths.contains(&"end_date".to_string()));
    }

    #[test]
    fn test_extract_with_filters() {
        let template = r#"{{ lease.end_date|date:"M d, Y" }}"#;
        let vars = extract_template_variables(template).unwrap();
        assert!(vars.contains_key("lease"));
        assert_eq!(vars.get("lease").unwrap(), &vec!["end_date".to_string()]);
    }

    #[test]
    fn test_extract_in_if_tag() {
        let template = r#"{% if lease.property.status == "active" %}...{% endif %}"#;
        let vars = extract_template_variables(template).unwrap();
        assert!(vars.contains_key("lease"));
        assert!(vars
            .get("lease")
            .unwrap()
            .contains(&"property.status".to_string()));
    }

    #[test]
    fn test_extract_in_for_tag() {
        let template = r#"{% for item in items.all %}{{ item.name }}{% endfor %}"#;
        let vars = extract_template_variables(template).unwrap();
        assert!(vars.contains_key("items"));
        assert!(vars.get("items").unwrap().contains(&"all".to_string()));
        assert!(vars.contains_key("item"));
        assert!(vars.get("item").unwrap().contains(&"name".to_string()));
    }

    #[test]
    fn test_extract_deduplication() {
        let template = r#"
            {{ lease.property.name }}
            {{ lease.property.name }}
            {{ lease.property.address }}
        "#;
        let vars = extract_template_variables(template).unwrap();
        let lease_paths = vars.get("lease").unwrap();

        // Should have 2 unique paths, not 3
        assert_eq!(lease_paths.len(), 2);
        assert!(lease_paths.contains(&"property.name".to_string()));
        assert!(lease_paths.contains(&"property.address".to_string()));
    }

    #[test]
    fn test_extract_real_world_template() {
        let template = r#"
            {% for lease in expiring_soon %}
              <td>{{ lease.property.name }}</td>
              <td>{{ lease.property.address }}</td>
              <td>{{ lease.tenant.user.get_full_name }}</td>
              <td>{{ lease.tenant.user.email }}</td>
              <td>{{ lease.end_date|date:"M d, Y" }}</td>
            {% endfor %}
        "#;
        let vars = extract_template_variables(template).unwrap();

        assert!(vars.contains_key("lease"));
        let lease_paths = vars.get("lease").unwrap();

        assert!(lease_paths.contains(&"property.name".to_string()));
        assert!(lease_paths.contains(&"property.address".to_string()));
        assert!(lease_paths.contains(&"tenant.user.get_full_name".to_string()));
        assert!(lease_paths.contains(&"tenant.user.email".to_string()));
        assert!(lease_paths.contains(&"end_date".to_string()));

        // Check expiring_soon is tracked
        assert!(vars.contains_key("expiring_soon"));
    }

    #[test]
    fn test_extract_with_tag() {
        let template = r#"{% with total=items.count %}{{ total }}{% endwith %}"#;
        let vars = extract_template_variables(template).unwrap();
        assert!(vars.contains_key("items"));
        assert!(vars.get("items").unwrap().contains(&"count".to_string()));
        assert!(vars.contains_key("total"));
    }

    // Edge case tests
    #[test]
    fn test_extract_empty_template() {
        let template = "";
        let vars = extract_template_variables(template).unwrap();
        assert_eq!(vars.len(), 0);
    }

    #[test]
    fn test_extract_only_text() {
        let template = "<html><body>Hello World</body></html>";
        let vars = extract_template_variables(template).unwrap();
        assert_eq!(vars.len(), 0);
    }

    #[test]
    fn test_extract_whitespace_handling() {
        let template = "{{  user.name  }}";
        let vars = extract_template_variables(template).unwrap();
        assert!(vars.contains_key("user"));
        assert!(vars.get("user").unwrap().contains(&"name".to_string()));
    }

    #[test]
    fn test_extract_deeply_nested_paths() {
        let template = "{{ a.b.c.d.e.f.g.h.i.j }}";
        let vars = extract_template_variables(template).unwrap();
        assert!(vars.contains_key("a"));
        assert!(vars
            .get("a")
            .unwrap()
            .contains(&"b.c.d.e.f.g.h.i.j".to_string()));
    }

    #[test]
    fn test_extract_mixed_content() {
        let template = r#"
            <div class="header">{{ site.name }}</div>
            {% if user.is_authenticated %}
                <p>Welcome {{ user.profile.display_name }}!</p>
                {% for message in user.messages.unread %}
                    <div>{{ message.text }}</div>
                {% endfor %}
            {% else %}
                <a href="/login">Login</a>
            {% endif %}
        "#;
        let vars = extract_template_variables(template).unwrap();

        assert!(vars.contains_key("site"));
        assert!(vars.get("site").unwrap().contains(&"name".to_string()));

        assert!(vars.contains_key("user"));
        let user_paths = vars.get("user").unwrap();
        assert!(user_paths.contains(&"is_authenticated".to_string()));
        assert!(user_paths.contains(&"profile.display_name".to_string()));
        assert!(user_paths.contains(&"messages.unread".to_string()));

        assert!(vars.contains_key("message"));
        assert!(vars.get("message").unwrap().contains(&"text".to_string()));
    }

    #[test]
    fn test_extract_with_complex_filters() {
        let template = r#"
            {{ date|date:"Y-m-d H:i:s" }}
            {{ text|truncatewords:10|upper }}
            {{ value|default:"N/A"|safe }}
        "#;
        let vars = extract_template_variables(template).unwrap();
        assert!(vars.contains_key("date"));
        assert!(vars.contains_key("text"));
        assert!(vars.contains_key("value"));
    }

    #[test]
    fn test_extract_multiple_variables_same_line() {
        let template = "{{ a }} {{ b }} {{ c.d }} {{ e.f.g }}";
        let vars = extract_template_variables(template).unwrap();
        assert_eq!(vars.len(), 4);
        assert!(vars.contains_key("a"));
        assert!(vars.contains_key("b"));
        assert!(vars.contains_key("c"));
        assert!(vars.contains_key("e"));
    }

    #[test]
    fn test_extract_nested_blocks() {
        let template = r#"
            {% block outer %}
                {{ outer_var }}
                {% block inner %}
                    {{ inner_var }}
                {% endblock %}
            {% endblock %}
        "#;
        let vars = extract_template_variables(template).unwrap();
        assert!(vars.contains_key("outer_var"));
        assert!(vars.contains_key("inner_var"));
    }

    #[test]
    fn test_extract_complex_for_loops() {
        let template = r#"
            {% for category in categories.active %}
                {% for item in category.items.filter_by_status %}
                    {{ item.title }}
                    {% for tag in item.tags.all %}
                        {{ tag.name }}
                    {% endfor %}
                {% endfor %}
            {% endfor %}
        "#;
        let vars = extract_template_variables(template).unwrap();

        assert!(vars.contains_key("categories"));
        assert!(vars
            .get("categories")
            .unwrap()
            .contains(&"active".to_string()));

        assert!(vars.contains_key("category"));
        assert!(vars
            .get("category")
            .unwrap()
            .contains(&"items.filter_by_status".to_string()));

        assert!(vars.contains_key("item"));
        let item_paths = vars.get("item").unwrap();
        assert!(item_paths.contains(&"title".to_string()));
        assert!(item_paths.contains(&"tags.all".to_string()));

        assert!(vars.contains_key("tag"));
        assert!(vars.get("tag").unwrap().contains(&"name".to_string()));
    }

    #[test]
    fn test_extract_complex_conditionals() {
        // Note: Current parser extracts from if condition but not elif conditions
        // This is a known limitation that will be addressed in future phases
        let template = r#"
            {% if user.profile.is_verified and user.subscription.is_active %}
                Premium User
            {% endif %}
        "#;
        let vars = extract_template_variables(template).unwrap();

        assert!(vars.contains_key("user"));
        let user_paths = vars.get("user").unwrap();
        assert!(user_paths.contains(&"profile.is_verified".to_string()));
        assert!(user_paths.contains(&"subscription.is_active".to_string()));
    }

    #[test]
    fn test_extract_special_characters_in_text() {
        let template = r#"<div data-value="{{ value }}">{{ & < > }}</div>"#;
        let vars = extract_template_variables(template).unwrap();
        assert!(vars.contains_key("value"));
    }

    #[test]
    fn test_extract_with_includes() {
        // Even though we don't process includes, we should extract variables
        let template = r#"
            {% include "header.html" with title=page.title %}
            {{ content }}
        "#;
        let vars = extract_template_variables(template).unwrap();
        // Should at least extract 'content'
        assert!(vars.contains_key("content"));
    }

    #[test]
    fn test_extract_react_component() {
        // Note: Current parser extracts from tag body but not tag arguments
        // This is a known limitation that will be addressed in future phases
        let template = r#"{% react "Button" props=button.props %}{{ button.label }}{% endreact %}"#;
        let vars = extract_template_variables(template).unwrap();
        assert!(vars.contains_key("button"));
        let button_paths = vars.get("button").unwrap();
        assert!(button_paths.contains(&"label".to_string()));
        // Note: button.props is not currently extracted from tag arguments
    }

    #[test]
    fn test_extract_large_template() {
        // Test performance with a large template
        let mut template_parts = Vec::new();
        for i in 0..100 {
            template_parts.push(format!(
                r#"
                {{% for obj{i} in list{i} %}}
                    {{{{ obj{i}.field1 }}}}
                    {{{{ obj{i}.field2.nested }}}}
                {{% endfor %}}
            "#
            ));
        }
        let template = template_parts.join("\n");
        let vars = extract_template_variables(&template).unwrap();

        // Should have extracted variables for all 100 iterations
        assert!(vars.len() >= 100);
    }

    #[test]
    fn test_extract_paths_sorted() {
        let template = r#"
            {{ obj.zebra }}
            {{ obj.apple }}
            {{ obj.middle }}
        "#;
        let vars = extract_template_variables(template).unwrap();
        let paths = vars.get("obj").unwrap();

        // Paths should be sorted
        assert_eq!(paths[0], "apple");
        assert_eq!(paths[1], "middle");
        assert_eq!(paths[2], "zebra");
    }

    #[test]
    fn test_extract_method_calls() {
        let template = "{{ items.all }} {{ user.get_full_name }} {{ count.increment }}";
        let vars = extract_template_variables(template).unwrap();

        assert!(vars.contains_key("items"));
        assert!(vars.get("items").unwrap().contains(&"all".to_string()));

        assert!(vars.contains_key("user"));
        assert!(vars
            .get("user")
            .unwrap()
            .contains(&"get_full_name".to_string()));

        assert!(vars.contains_key("count"));
        assert!(vars
            .get("count")
            .unwrap()
            .contains(&"increment".to_string()));
    }

    // Tests for elif support (Issue #79)

    #[test]
    fn test_parse_if_elif() {
        let tokens = tokenize("{% if a %}A{% elif b %}B{% endif %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        assert_eq!(nodes.len(), 1);
        match &nodes[0] {
            Node::If {
                condition,
                true_nodes,
                false_nodes,
            } => {
                assert_eq!(condition, "a");
                assert_eq!(true_nodes.len(), 1);
                // false_nodes should contain a nested If for the elif
                assert_eq!(false_nodes.len(), 1);
                match &false_nodes[0] {
                    Node::If {
                        condition: elif_cond,
                        true_nodes: elif_true,
                        false_nodes: elif_false,
                    } => {
                        assert_eq!(elif_cond, "b");
                        assert_eq!(elif_true.len(), 1);
                        assert_eq!(elif_false.len(), 0);
                    }
                    _ => panic!("Expected nested If node for elif"),
                }
            }
            _ => panic!("Expected If node"),
        }
    }

    #[test]
    fn test_parse_if_elif_else() {
        let tokens = tokenize("{% if a %}A{% elif b %}B{% else %}C{% endif %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        assert_eq!(nodes.len(), 1);
        match &nodes[0] {
            Node::If {
                condition,
                true_nodes,
                false_nodes,
            } => {
                assert_eq!(condition, "a");
                assert_eq!(true_nodes.len(), 1);
                // false_nodes should contain a nested If for the elif
                assert_eq!(false_nodes.len(), 1);
                match &false_nodes[0] {
                    Node::If {
                        condition: elif_cond,
                        true_nodes: elif_true,
                        false_nodes: elif_false,
                    } => {
                        assert_eq!(elif_cond, "b");
                        assert_eq!(elif_true.len(), 1);
                        // The else branch should be in elif's false_nodes
                        assert_eq!(elif_false.len(), 1);
                        match &elif_false[0] {
                            Node::Text(text) => assert_eq!(text, "C"),
                            _ => panic!("Expected Text node for else branch"),
                        }
                    }
                    _ => panic!("Expected nested If node for elif"),
                }
            }
            _ => panic!("Expected If node"),
        }
    }

    #[test]
    fn test_parse_multiple_elif() {
        let tokens =
            tokenize("{% if a %}A{% elif b %}B{% elif c %}C{% elif d %}D{% endif %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        assert_eq!(nodes.len(), 1);

        // Verify nested structure: if a -> elif b -> elif c -> elif d
        match &nodes[0] {
            Node::If {
                condition,
                false_nodes,
                ..
            } => {
                assert_eq!(condition, "a");
                assert_eq!(false_nodes.len(), 1);
                match &false_nodes[0] {
                    Node::If {
                        condition: cond_b,
                        false_nodes: false_b,
                        ..
                    } => {
                        assert_eq!(cond_b, "b");
                        assert_eq!(false_b.len(), 1);
                        match &false_b[0] {
                            Node::If {
                                condition: cond_c,
                                false_nodes: false_c,
                                ..
                            } => {
                                assert_eq!(cond_c, "c");
                                assert_eq!(false_c.len(), 1);
                                match &false_c[0] {
                                    Node::If {
                                        condition: cond_d, ..
                                    } => {
                                        assert_eq!(cond_d, "d");
                                    }
                                    _ => panic!("Expected If node for elif d"),
                                }
                            }
                            _ => panic!("Expected If node for elif c"),
                        }
                    }
                    _ => panic!("Expected If node for elif b"),
                }
            }
            _ => panic!("Expected If node"),
        }
    }

    #[test]
    fn test_elif_with_string_comparison() {
        // This is the exact use case from Issue #79
        let tokens = tokenize(
            r#"{% if icon == "arrow-left" %}ARROW{% elif icon == "close" %}CLOSE{% else %}DEFAULT{% endif %}"#,
        )
        .unwrap();
        let nodes = parse(&tokens).unwrap();
        assert_eq!(nodes.len(), 1);

        match &nodes[0] {
            Node::If {
                condition,
                true_nodes,
                false_nodes,
            } => {
                assert_eq!(condition, r#"icon == "arrow-left""#);
                // Verify true branch has "ARROW"
                match &true_nodes[0] {
                    Node::Text(text) => assert_eq!(text, "ARROW"),
                    _ => panic!("Expected Text node"),
                }
                // Verify elif branch
                match &false_nodes[0] {
                    Node::If {
                        condition: elif_cond,
                        true_nodes: elif_true,
                        false_nodes: elif_false,
                    } => {
                        assert_eq!(elif_cond, r#"icon == "close""#);
                        match &elif_true[0] {
                            Node::Text(text) => assert_eq!(text, "CLOSE"),
                            _ => panic!("Expected Text node in elif"),
                        }
                        match &elif_false[0] {
                            Node::Text(text) => assert_eq!(text, "DEFAULT"),
                            _ => panic!("Expected Text node in else"),
                        }
                    }
                    _ => panic!("Expected If node for elif"),
                }
            }
            _ => panic!("Expected If node"),
        }
    }

    #[test]
    fn test_extract_variables_with_elif() {
        let template = r#"
            {% if user.is_admin %}
                Admin
            {% elif user.is_staff %}
                Staff
            {% elif user.is_verified %}
                Verified
            {% else %}
                Regular
            {% endif %}
        "#;
        let vars = extract_template_variables(template).unwrap();

        assert!(vars.contains_key("user"));
        let user_paths = vars.get("user").unwrap();
        assert!(user_paths.contains(&"is_admin".to_string()));
        assert!(user_paths.contains(&"is_staff".to_string()));
        assert!(user_paths.contains(&"is_verified".to_string()));
    }

    #[test]
    fn test_elif_after_else_is_error() {
        // {% elif %} after {% else %} is invalid syntax (matches Django behavior)
        let tokens = tokenize("{% if a %}A{% else %}B{% elif c %}C{% endif %}").unwrap();
        let result = parse(&tokens);
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert!(err.to_string().contains("elif"));
        assert!(err.to_string().contains("else"));
    }
}
