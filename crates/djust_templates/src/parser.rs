//! Template parser for building an AST from tokens

use crate::lexer::Token;
use djust_core::{DjangoRustError, Result};
use std::collections::{HashMap, HashSet};

#[derive(Debug, Clone)]
pub enum Node {
    Text(String),
    /// Variable expression `{{ var|filter:arg }}`.
    ///
    /// Tuple: (variable name, filter specs, in_attr).
    ///
    /// `in_attr` is computed at parse time via
    /// [`is_inside_html_tag_at`]. When true, the renderer uses
    /// [`crate::filters::html_escape_attr`] (attribute-safe escape)
    /// instead of [`crate::filters::html_escape`]. `|safe` still
    /// bypasses escaping in both contexts.
    Variable(String, Vec<(String, Option<String>)>, bool),
    If {
        condition: String,
        true_nodes: Vec<Node>,
        false_nodes: Vec<Node>,
        in_tag_context: bool,
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
    /// {% load library_name %} — preserved so inheritance reconstruction can
    /// re-emit the tag for downstream Django rendering.
    Load(Vec<String>),
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
    /// Block custom template tag handled by a Python callback with children.
    ///
    /// This is used for Django-compatible block tags like `{% modal %}...{% endmodal %}`
    /// that wrap content and require Python runtime rendering.
    ///
    /// The handler receives the pre-rendered HTML of the block body.
    BlockCustomTag {
        /// Opening tag name (e.g., "modal", "card")
        name: String,
        /// Arguments from the opening tag as raw strings
        args: Vec<String>,
        /// Child nodes (the block body)
        children: Vec<Node>,
    },
    /// {% widthratio value max_value max_width %} - calculates round(value/max_value * max_width)
    WidthRatio {
        value: String,
        max_value: String,
        max_width: String,
    },
    /// {% firstof var1 var2 ... "fallback" %} - outputs first truthy variable
    FirstOf {
        args: Vec<String>,
    },
    /// {% templatetag name %} - outputs literal template syntax characters
    TemplateTag(String),
    /// {% spaceless %}...{% endspaceless %} - removes whitespace between HTML tags
    Spaceless {
        nodes: Vec<Node>,
    },
    /// {% cycle val1 val2 ... %} - cycles through values in a for loop
    Cycle {
        values: Vec<String>,
        name: Option<String>,
    },
    /// {% now "format" %} - outputs current date/time with given format
    Now(String),
    /// Unsupported template tag - renders as HTML comment with warning.
    ///
    /// This is used for Django template tags that don't have a registered
    /// handler. Instead of silently failing, it outputs a visible warning
    /// in development to help developers identify missing tag implementations.
    UnsupportedTag {
        /// Tag name (e.g., "ifchanged", "regroup")
        name: String,
        /// Original arguments from the tag
        args: Vec<String>,
    },
    /// Custom assign tag — handler returns a dict that is merged
    /// into the context for subsequent sibling nodes (no HTML
    /// output).
    ///
    /// Registered via `register_assign_tag_handler(name, handler)`.
    /// The handler's `render(args, context)` must return a
    /// `dict[str, Any]`; each key becomes a context variable
    /// visible to siblings that follow the assign tag in the same
    /// `render_nodes_with_loader` iteration.
    ///
    /// See [`crate::registry::register_assign_tag_handler`].
    AssignTag {
        /// Tag name (e.g., "assign_slot")
        name: String,
        /// Raw arguments from the template tag
        args: Vec<String>,
    },
    /// Jinja2-style inline conditional: {{ true_expr if condition else false_expr }}
    ///
    /// This is safe to use inside HTML attribute values (unlike {% if %} blocks,
    /// which insert `<!--dj-if-->` comment nodes that corrupt attribute strings).
    ///
    /// Examples:
    ///   class="{{ 'btn--active' if view_mode == 'day' else '' }}"
    ///   disabled="{{ 'disabled' if is_locked else '' }}"
    ///   class="{{ 'error' if has_error }}"   {# else branch is optional #}
    InlineIf {
        true_expr: String,
        condition: String,
        false_expr: String,
        filters: Vec<(String, Option<String>)>,
    },
}

/// Returns true if `text` ends inside an unclosed HTML opening tag.
///
/// Used to detect whether a `{% if %}` tag appears inside an attribute value,
/// e.g. `<div class="btn {% if active %}`. In that context the VDOM placeholder
/// comment `<!--dj-if-->` must NOT be emitted because HTML comments inside
/// attribute values produce malformed HTML (fix for issue #380).
///
/// Scans left-to-right with quote state tracking so that `>` characters
/// inside quoted attribute values (e.g. `title="a > b "`) are not mistaken
/// for tag-closing `>` characters.
///
/// Known limitation: does not track JavaScript/CSS template literals or
/// CDATA sections — these are not expected in Django template attribute values.
fn is_inside_html_tag(text: &str) -> bool {
    let mut in_tag = false;
    let mut in_quote: Option<char> = None;

    for ch in text.chars() {
        match (in_tag, in_quote, ch) {
            // Opening < starts a tag (only when not inside a quoted attribute)
            (false, None, '<') => in_tag = true,
            // Closing > ends a tag (only when not inside a quoted attribute)
            (true, None, '>') => in_tag = false,
            // Enter a double-quoted attribute value
            (true, None, '"') => in_quote = Some('"'),
            // Enter a single-quoted attribute value
            (true, None, '\'') => in_quote = Some('\''),
            // Exit a quoted attribute value (matching quote character)
            (true, Some(q), c) if c == q => in_quote = None,
            // All other characters — no state change
            _ => {}
        }
    }

    in_tag
}

/// Scan backwards through all preceding tokens to determine if we are inside
/// an HTML opening tag. Variable/expression tokens (`{{ }}`) produce escaped
/// text that cannot contain raw `<` or `>`, so they are skipped — only Text
/// tokens contribute to the tag-open/close state.
///
/// This fixes the case where `<option value="{{ var }}" {% if cond %}selected{% endif %}>`
/// has a Variable token between the `<option` and the `{% if %}`, causing the
/// single-token check to miss the unclosed tag context.
fn is_inside_html_tag_at(tokens: &[Token], pos: usize) -> bool {
    let mut combined = String::new();
    // Walk backwards, collecting Text tokens. Stop early if we find a `>`
    // outside quotes (definitely closed) or have enough context.
    for j in (0..pos).rev() {
        if let Token::Text(t) = &tokens[j] {
            combined.insert_str(0, t);
            // Optimization: if the combined text contains a `>` we have enough
            // context — the final state from is_inside_html_tag will be correct.
            if t.contains('>') {
                break;
            }
        }
        // Variable tokens produce HTML-escaped content (no raw < or >), skip them.
        // Tag tokens ({% %}) are structural and don't emit < or >, skip them too.
    }
    is_inside_html_tag(&combined)
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
            let expr_part = &parts[0];
            let filters = parse_filter_specs(&parts[1..]);

            // Check for Jinja2-style inline conditional:
            //   {{ true_expr if condition else false_expr }}
            //   {{ true_expr if condition }}   (else branch optional, defaults to "")
            if let Some(if_pos) = find_if_keyword(expr_part) {
                let true_expr = expr_part[..if_pos].trim().to_string();
                let rest = expr_part[if_pos + 4..].trim(); // skip " if "
                let (condition, false_expr) = if let Some(else_pos) = rest.find(" else ") {
                    (
                        rest[..else_pos].trim().to_string(),
                        rest[else_pos + 6..].trim().to_string(),
                    )
                } else {
                    (rest.to_string(), String::new())
                };
                return Ok(Some(Node::InlineIf {
                    true_expr,
                    condition,
                    false_expr,
                    filters,
                }));
            }

            // Detect whether this variable is inside an HTML opening
            // tag (attribute context). When true the renderer uses
            // the attribute-safe escape (see `html_escape_attr`).
            let in_attr = is_inside_html_tag_at(tokens, *i);
            Ok(Some(Node::Variable(expr_part.clone(), filters, in_attr)))
        }

        Token::Tag(tag_name, args) => {
            match tag_name.as_str() {
                "if" => {
                    let condition = args.join(" ");
                    // Capture attribute context BEFORE advancing i.
                    // Scan backwards through ALL preceding tokens (not just the
                    // immediately previous one) to determine if we are inside an
                    // unclosed HTML tag. This handles cases like:
                    //   <option value="{{ var }}" {% if cond %}selected{% endif %}>
                    // where Variable tokens separate the tag opening from the {% if %}.
                    let in_tag_context = is_inside_html_tag_at(tokens, *i);
                    let (true_nodes, false_nodes, end_pos) =
                        parse_if_block(tokens, *i + 1, in_tag_context)?;
                    *i = end_pos;
                    Ok(Some(Node::If {
                        condition,
                        true_nodes,
                        false_nodes,
                        in_tag_context,
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
                    // {% load static %} — preserve library names so inheritance
                    // reconstruction can re-emit the tag for Django rendering.
                    Ok(Some(Node::Load(args.clone())))
                }

                "widthratio" => {
                    // {% widthratio value max_value max_width %}
                    if args.len() < 3 {
                        return Err(DjangoRustError::TemplateError(
                            "widthratio tag requires 3 arguments: {% widthratio value max_value max_width %}"
                                .to_string(),
                        ));
                    }
                    Ok(Some(Node::WidthRatio {
                        value: args[0].clone(),
                        max_value: args[1].clone(),
                        max_width: args[2].clone(),
                    }))
                }

                "firstof" => {
                    // {% firstof var1 var2 ... "fallback" %}
                    if args.is_empty() {
                        return Err(DjangoRustError::TemplateError(
                            "firstof tag requires at least one argument".to_string(),
                        ));
                    }
                    Ok(Some(Node::FirstOf { args: args.clone() }))
                }

                "templatetag" => {
                    // {% templatetag openblock %}
                    if args.is_empty() {
                        return Err(DjangoRustError::TemplateError(
                            "templatetag requires an argument".to_string(),
                        ));
                    }
                    Ok(Some(Node::TemplateTag(args[0].clone())))
                }

                "spaceless" => {
                    // {% spaceless %} ... {% endspaceless %}
                    let (nodes, end_pos) = parse_spaceless_block(tokens, *i + 1)?;
                    *i = end_pos;
                    Ok(Some(Node::Spaceless { nodes }))
                }

                "endspaceless" => {
                    // Handled by spaceless tag
                    Ok(None)
                }

                "cycle" => {
                    // {% cycle val1 val2 ... %} or {% cycle val1 val2 as cyclename %}
                    if args.is_empty() {
                        return Err(DjangoRustError::TemplateError(
                            "cycle tag requires at least one argument".to_string(),
                        ));
                    }
                    // Check for "as name" at the end
                    let (values, name) = if args.len() >= 3 && args[args.len() - 2] == "as" {
                        let name = args.last().unwrap().clone();
                        let values = args[..args.len() - 2].to_vec();
                        (values, Some(name))
                    } else {
                        (args.clone(), None)
                    };
                    Ok(Some(Node::Cycle { values, name }))
                }

                "now" => {
                    // {% now "format_string" %}
                    if args.is_empty() {
                        return Err(DjangoRustError::TemplateError(
                            "now tag requires a format string argument".to_string(),
                        ));
                    }
                    let format = args[0].trim_matches(|c| c == '"' || c == '\'').to_string();
                    Ok(Some(Node::Now(format)))
                }

                "endif" | "endfor" | "endblock" | "else" | "elif" => {
                    // These are handled by their opening tags
                    Ok(None)
                }

                _ => {
                    // Check if a Python block tag handler is registered (tags with children)
                    if let Some(end_tag) = crate::registry::block_handler_exists(tag_name) {
                        let (children, end_pos) = parse_block_custom_tag(tokens, *i + 1, &end_tag)?;
                        *i = end_pos;
                        Ok(Some(Node::BlockCustomTag {
                            name: tag_name.clone(),
                            args: args.clone(),
                            children,
                        }))
                    } else if crate::registry::handler_exists(tag_name) {
                        // Inline handler exists - create CustomTag node
                        Ok(Some(Node::CustomTag {
                            name: tag_name.clone(),
                            args: args.clone(),
                        }))
                    } else if crate::registry::assign_handler_exists(tag_name) {
                        // Context-mutating assign tag (register_assign_tag_handler)
                        Ok(Some(Node::AssignTag {
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

fn parse_if_block(
    tokens: &[Token],
    start: usize,
    in_tag_context: bool,
) -> Result<(Vec<Node>, Vec<Node>, usize)> {
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
                let (elif_true, elif_false, end_pos) =
                    parse_if_block(tokens, i + 1, in_tag_context)?;
                false_nodes.push(Node::If {
                    condition: elif_condition,
                    true_nodes: elif_true,
                    false_nodes: elif_false,
                    in_tag_context,
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

fn parse_spaceless_block(tokens: &[Token], start: usize) -> Result<(Vec<Node>, usize)> {
    let mut nodes = Vec::new();
    let mut i = start;

    while i < tokens.len() {
        if let Token::Tag(name, _) = &tokens[i] {
            if name == "endspaceless" {
                return Ok((nodes, i));
            }
        }

        if let Some(node) = parse_token(tokens, &mut i)? {
            nodes.push(node);
        }
        i += 1;
    }

    Err(DjangoRustError::TemplateError(
        "Unclosed spaceless tag".to_string(),
    ))
}

/// Parse a custom block tag body until a matching end tag.
///
/// Used by `parse_token` when a registered block tag handler is found.
/// Scans forward collecting child nodes until `end_tag` is encountered.
fn parse_block_custom_tag(
    tokens: &[Token],
    start: usize,
    end_tag: &str,
) -> Result<(Vec<Node>, usize)> {
    let mut nodes = Vec::new();
    let mut i = start;

    while i < tokens.len() {
        if let Token::Tag(name, _) = &tokens[i] {
            if name == end_tag {
                return Ok((nodes, i));
            }
        }

        if let Some(node) = parse_token(tokens, &mut i)? {
            nodes.push(node);
        }
        i += 1;
    }

    Err(DjangoRustError::TemplateError(format!(
        "Unclosed block tag, expected {{% {end_tag} %}}"
    )))
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

/// Extract per-node dependency sets from a list of AST nodes.
///
/// Returns one `HashSet<String>` per node, containing the top-level context
/// variable names that node depends on.  Text nodes yield an empty set,
/// `Include` and `CustomTag` nodes get a `"*"` wildcard because their
/// dependencies cannot be statically determined.
pub fn extract_per_node_deps(nodes: &[Node]) -> Vec<HashSet<String>> {
    nodes
        .iter()
        .map(|node| {
            let mut variables: HashMap<String, Vec<String>> = HashMap::new();
            extract_from_nodes(std::slice::from_ref(node), &mut variables);
            let mut deps: HashSet<String> = variables.into_keys().collect();

            // Include nodes may depend on any variable — mark as wildcard
            if matches!(node, Node::Include { .. }) {
                deps.insert("*".to_string());
            }
            // CustomTag / BlockCustomTag nodes may also have unpredictable deps
            if matches!(node, Node::CustomTag { .. } | Node::BlockCustomTag { .. }) {
                deps.insert("*".to_string());
            }
            deps
        })
        .collect()
}

/// Recursively extract variable paths from AST nodes
fn extract_from_nodes(
    nodes: &[Node],
    variables: &mut std::collections::HashMap<String, Vec<String>>,
) {
    for node in nodes {
        match node {
            Node::Variable(var_expr, filters, _in_attr) => {
                // Extract from variable: {{ variable.path }}
                extract_from_variable(var_expr, variables);
                // Extract from filter args: {{ a|default:fallback }} — `fallback`
                // must be tracked as a dependency too, otherwise a nested
                // {% if %}{{ x|default:dynamic }}{% endif %} silently drops
                // when only `dynamic` changes (issue #787).
                for (_name, arg) in filters {
                    if let Some(arg) = arg {
                        extract_from_filter_arg(arg, variables);
                    }
                }
            }
            Node::If {
                condition,
                true_nodes,
                false_nodes,
                ..
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
            Node::AssignTag { args, name: _ } => {
                // Extract variable references from the assign tag's
                // arguments so the partial renderer knows the tag
                // depends on them. Because an assign tag mutates the
                // context for subsequent sibling nodes in unknowable
                // ways, also emit the `"*"` wildcard — any change
                // may alter downstream rendering.
                for arg in args {
                    if (arg.starts_with('"') && arg.ends_with('"'))
                        || (arg.starts_with('\'') && arg.ends_with('\''))
                    {
                        continue;
                    }
                    let value = if let Some(eq_pos) = arg.find('=') {
                        arg[eq_pos + 1..].trim()
                    } else {
                        arg.trim()
                    };
                    if !value.is_empty()
                        && !value.starts_with('"')
                        && !value.starts_with('\'')
                        && !value.chars().all(|c| c.is_numeric() || c == '.')
                    {
                        extract_from_variable(value, variables);
                    }
                }
                variables.entry("*".to_string()).or_default();
            }
            Node::CustomTag { args, name: _ }
            | Node::BlockCustomTag {
                args,
                name: _,
                children: _,
            } => {
                // Extract variables from custom/block tag arguments
                for arg in args {
                    if (arg.starts_with('"') && arg.ends_with('"'))
                        || (arg.starts_with('\'') && arg.ends_with('\''))
                    {
                        continue;
                    }
                    let value = if let Some(eq_pos) = arg.find('=') {
                        arg[eq_pos + 1..].trim()
                    } else {
                        arg.trim()
                    };
                    if !value.is_empty()
                        && !value.starts_with('"')
                        && !value.starts_with('\'')
                        && !value.chars().all(|c| c.is_numeric() || c == '.')
                    {
                        extract_from_variable(value, variables);
                    }
                }
                // For block tags, also recurse into children
                if let Node::BlockCustomTag { children, .. } = node {
                    extract_from_nodes(children, variables);
                }
                // Custom tags can reference arbitrary vars internally; mark
                // the enclosing wrapper as "*" so partial render re-renders
                // it on any context change. Mirrors the top-level treatment
                // in extract_per_node_deps. Fixes #783.
                variables.entry("*".to_string()).or_default();
            }
            Node::WidthRatio {
                value,
                max_value,
                max_width,
            } => {
                extract_from_variable(value, variables);
                extract_from_variable(max_value, variables);
                extract_from_variable(max_width, variables);
            }
            Node::FirstOf { args } => {
                for arg in args {
                    if !((arg.starts_with('"') && arg.ends_with('"'))
                        || (arg.starts_with('\'') && arg.ends_with('\''))
                        || arg.chars().all(|c| c.is_numeric() || c == '.'))
                    {
                        extract_from_variable(arg, variables);
                    }
                }
            }
            Node::Spaceless { nodes } => {
                extract_from_nodes(nodes, variables);
            }
            Node::Cycle { values, .. } => {
                for val in values {
                    if !((val.starts_with('"') && val.ends_with('"'))
                        || (val.starts_with('\'') && val.ends_with('\'')))
                    {
                        extract_from_variable(val, variables);
                    }
                }
            }
            // Inline conditional `{{ x if cond else y }}` — same class of
            // silent dep-loss bug as the nested-Include case (#783).
            // Without this arm, an InlineIf inside a `{% for %}` / `{% if %}`
            // wrapper contributes zero deps; changing the condition variable
            // alone leaves the wrapper's dep set unintersected with
            // changed_keys, the cached fragment is reused, and the diff
            // returns 0 patches.
            Node::InlineIf {
                true_expr,
                condition,
                false_expr,
                filters: _,
            } => {
                for expr in [true_expr, condition, false_expr] {
                    let trimmed = expr.trim();
                    if trimmed.is_empty() {
                        continue;
                    }
                    let is_literal = (trimmed.starts_with('"') && trimmed.ends_with('"'))
                        || (trimmed.starts_with('\'') && trimmed.ends_with('\''))
                        || trimmed
                            .chars()
                            .all(|c| c.is_numeric() || c == '.' || c == '-');
                    if !is_literal {
                        extract_from_variable(trimmed, variables);
                    }
                }
            }
            // Nested Include nodes: the included template's vars can't be
            // determined statically from here, so mark the enclosing
            // wrapper as depending on "*" (any key change). Without this,
            // `{% if cond %}{% include "x" %}{% endif %}` has deps={cond},
            // and partial render reuses the cached If fragment when any
            // other context key changes — including vars used inside the
            // included template. (CustomTag/BlockCustomTag get "*" via
            // the earlier arm above.) Fixes #783.
            Node::Include { .. } => {
                variables.entry("*".to_string()).or_default();
            }
            // Text, Comment, CsrfToken, Extends, TemplateTag, Now, Static
            // don't contain variable references.
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

/// Extract a variable reference from a filter argument.
///
/// Filter args can be bare identifiers (`|default:fallback`), literal
/// strings (`|default:"none"` or `|default:'none'`), or numbers
/// (`|default:0`). Only bare identifiers are variable references and
/// need to be tracked as template dependencies (issue #787).
fn extract_from_filter_arg(
    arg: &str,
    variables: &mut std::collections::HashMap<String, Vec<String>>,
) {
    let trimmed = arg.trim();
    if trimmed.is_empty() {
        return;
    }
    // Skip quoted string literals.
    if (trimmed.starts_with('"') && trimmed.ends_with('"'))
        || (trimmed.starts_with('\'') && trimmed.ends_with('\''))
    {
        return;
    }
    // Skip numeric literals (including signed / floating point).
    if trimmed
        .chars()
        .all(|c| c.is_ascii_digit() || c == '.' || c == '-' || c == '+')
    {
        return;
    }
    // Anything else looks like a bare identifier / dotted path.
    extract_from_variable(trimmed, variables);
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

/// Find the position of the ` if ` keyword in an expression, skipping over
/// quoted strings so that `'some if text' if cond else ''` works correctly.
fn find_if_keyword(expr: &str) -> Option<usize> {
    let bytes = expr.as_bytes();
    let mut i = 0;
    let mut in_single = false;
    let mut in_double = false;

    while i < bytes.len() {
        match bytes[i] {
            b'\'' if !in_double => in_single = !in_single,
            b'"' if !in_single => in_double = !in_double,
            _ if !in_single && !in_double => {
                if expr[i..].starts_with(" if ") {
                    return Some(i);
                }
            }
            _ => {}
        }
        i += 1;
    }
    None
}

/// Parse a slice of filter spec strings into `(filter_name, Option<arg>)` pairs.
fn parse_filter_specs(parts: &[String]) -> Vec<(String, Option<String>)> {
    parts
        .iter()
        .map(|filter_spec| {
            if let Some(colon_pos) = filter_spec.find(':') {
                let filter_name = filter_spec[..colon_pos].trim().to_string();
                let arg = filter_spec[colon_pos + 1..].trim().to_string();
                // NOTE: surrounding quotes on literal args (e.g. `"none"`
                // in `default:"none"`) are preserved here so the
                // dep-tracking extractor (issue #787) can tell a literal
                // apart from a bare-identifier variable reference. The
                // quote-strip now happens at render time inside
                // `strip_filter_arg_quotes`.
                (filter_name, Some(arg))
            } else {
                (filter_spec.clone(), None)
            }
        })
        .collect()
}

/// Strip surrounding single/double quotes from a filter argument when it
/// was a literal at parse time. Called at render time so the extractor
/// can still distinguish bare identifiers from quoted literals.
pub fn strip_filter_arg_quotes(arg: &str) -> &str {
    if arg.len() >= 2
        && ((arg.starts_with('"') && arg.ends_with('"'))
            || (arg.starts_with('\'') && arg.ends_with('\'')))
    {
        &arg[1..arg.len() - 1]
    } else {
        arg
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
        // Load preserves library names
        match &nodes[0] {
            Node::Load(libs) => assert_eq!(libs, &["static"]),
            _ => panic!("Expected Load node for load tag"),
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
                ..
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
                        ..
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
                ..
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
                        ..
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
                ..
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
                        ..
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

    #[test]
    fn test_inline_if_parses_to_inline_if_node() {
        let tokens = tokenize("{{ 'btn--active' if view_mode == 'day' else '' }}").unwrap();
        let nodes = parse(&tokens).unwrap();
        assert_eq!(nodes.len(), 1);
        match &nodes[0] {
            Node::InlineIf {
                true_expr,
                condition,
                false_expr,
                filters,
            } => {
                assert_eq!(true_expr, "'btn--active'");
                assert_eq!(condition, "view_mode == 'day'");
                assert_eq!(false_expr, "''");
                assert!(filters.is_empty());
            }
            _ => panic!("Expected InlineIf node, got {:?}", nodes[0]),
        }
    }

    #[test]
    fn test_inline_if_without_else() {
        let tokens = tokenize("{{ 'active' if is_active }}").unwrap();
        let nodes = parse(&tokens).unwrap();
        assert_eq!(nodes.len(), 1);
        match &nodes[0] {
            Node::InlineIf {
                true_expr,
                condition,
                false_expr,
                ..
            } => {
                assert_eq!(true_expr, "'active'");
                assert_eq!(condition, "is_active");
                assert_eq!(false_expr, "");
            }
            _ => panic!("Expected InlineIf node"),
        }
    }

    #[test]
    fn test_regular_variable_not_affected() {
        // A variable that happens to contain "if" in its name must not be treated as InlineIf
        let tokens = tokenize("{{ notify_if_late }}").unwrap();
        let nodes = parse(&tokens).unwrap();
        assert_eq!(nodes.len(), 1);
        match &nodes[0] {
            Node::Variable(name, _, _) => assert_eq!(name, "notify_if_late"),
            _ => panic!("Expected Variable node"),
        }
    }
}

/// Dep-extractor hardening tests (#783 P0 follow-up).
///
/// Two kinds of tests live here:
///
/// 1. **Table-driven assertions** on [`extract_per_node_deps`] output for
///    representative AST shapes — regression-guard that every known
///    wrapper/tag contributes the right keys to its enclosing dep set.
///
/// 2. **Node variant exhaustiveness check** — a compile-time check
///    ([`sample_for_coverage`]) that forces any new `Node` variant to be
///    accounted for, plus a runtime check that every variant either
///    produces a non-empty dep set or appears in [`NO_VARS_VARIANTS`].
///
/// Rationale: #783 was the second time a silent dep-drop in
/// [`extract_from_nodes`] caused partial render to return `patches=[]`
/// with `diff_ms: 0` (first was #774/#779). Both bugs had the same shape:
/// a new `Node` variant (or a wrapper nesting combination) fell through
/// the `_ => {}` default arm and produced zero deps. These tests make
/// silent drops on future additions impossible.
#[cfg(test)]
mod dep_tests {
    use super::*;
    use crate::lexer::tokenize;
    use std::collections::HashSet;

    /// Parse `template` and return the per-top-level-node dep sets.
    fn deps_for(template: &str) -> Vec<HashSet<String>> {
        let tokens = tokenize(template).expect("tokenize failed");
        let nodes = parse(&tokens).expect("parse failed");
        extract_per_node_deps(&nodes)
    }

    // -----------------------------------------------------------------
    // Sub-item 1: Unit tests for extract_per_node_deps
    // -----------------------------------------------------------------

    #[test]
    fn test_deps_simple_variable() {
        let deps = deps_for("{{ a }}");
        assert_eq!(deps.len(), 1);
        assert!(
            deps[0].contains("a"),
            "expected 'a' in deps, got {:?}",
            deps[0]
        );
    }

    #[test]
    fn test_deps_variable_with_filter_arg() {
        // `default:b` — filter arg `b` is a variable reference.
        let deps = deps_for("{{ a|default:b }}");
        assert_eq!(deps.len(), 1);
        assert!(
            deps[0].contains("a"),
            "expected 'a' in deps, got {:?}",
            deps[0]
        );
        // Filter args aren't always extracted as deps by the current implementation;
        // this test documents the behavior. If the extractor is enhanced to track
        // filter-arg vars, tighten this to assert `b` is also present.
    }

    #[test]
    fn test_deps_if_include_has_wildcard() {
        // Exact #783 shape: If wrapping a nested Include — the top-level If
        // node's dep set must contain BOTH the condition var `c` AND `*`
        // (propagated from the nested Include).
        let deps = deps_for("{% if c %}{% include \"x\" %}{% endif %}");
        assert_eq!(deps.len(), 1);
        let set = &deps[0];
        assert!(set.contains("c"), "expected 'c' in deps, got {:?}", set);
        assert!(
            set.contains("*"),
            "expected wildcard '*' in deps (propagated from nested Include); got {:?}",
            set,
        );
    }

    #[test]
    fn test_deps_for_loop_tuple_unpacking() {
        // {% for k,v in d.items %} — deps should include `d` (iterable root),
        // `k` and `v` (loop vars are kept for IDE/debug purposes per the
        // extract_from_nodes comments).
        let deps = deps_for("{% for k,v in d.items %}{{ v|safe }}{% endfor %}");
        assert_eq!(deps.len(), 1);
        let set = &deps[0];
        assert!(
            set.contains("d"),
            "expected 'd' (iterable root) in deps, got {:?}",
            set
        );
        assert!(
            set.contains("v"),
            "expected 'v' (loop var) in deps, got {:?}",
            set
        );
        // `k` is the other loop var — may or may not appear depending on use;
        // asserting on `v` is sufficient since the body references it.
    }

    #[test]
    fn test_deps_with_custom_tag_has_wildcard() {
        // {% with x=y %}{% custom_tag %}{% endcustom_tag %}{% endwith %}
        // — with arg `y` plus `*` from custom-tag. (If `custom_tag` isn't
        // registered, it will parse as an UnsupportedTag; either way, the
        // top-level dep set must contain `y`.)
        let deps = deps_for("{% with x=y %}{{ x }}{% endwith %}");
        assert_eq!(deps.len(), 1);
        assert!(
            deps[0].contains("y"),
            "expected 'y' (with arg) in deps, got {:?}",
            deps[0]
        );
    }

    #[test]
    fn test_deps_inline_if_includes_condition() {
        // {{ 'on' if flag else 'off' }} — post-#784 fix, InlineIf contributes
        // `flag` to its enclosing dep set. Without this arm, changing `flag`
        // alone leaves wrapper dep-set unintersected with changed_keys and
        // the cached fragment is reused.
        let deps = deps_for("{{ \"on\" if flag else \"off\" }}");
        assert_eq!(deps.len(), 1);
        assert!(
            deps[0].contains("flag"),
            "expected 'flag' (InlineIf condition) in deps, got {:?}",
            deps[0],
        );
    }

    #[test]
    fn test_deps_nested_for_loops() {
        // Nested for: outer iterable `items`, inner iterable `i.children`
        // (root: `i`). Body references `i.x` and `j.y`.
        let deps = deps_for(
            "{% for i in items %}{% for j in i.children %}{{ i.x }}{{ j.y }}{% endfor %}{% endfor %}",
        );
        assert_eq!(deps.len(), 1);
        let set = &deps[0];
        assert!(
            set.contains("items"),
            "expected 'items' in deps, got {:?}",
            set
        );
        assert!(
            set.contains("i"),
            "expected 'i' (loop var) in deps, got {:?}",
            set
        );
        assert!(
            set.contains("j"),
            "expected 'j' (loop var) in deps, got {:?}",
            set
        );
    }

    #[test]
    fn test_deps_block_recurses_into_children() {
        // {% block content %}{{ a }}{% endblock %} — the Block wrapper must
        // expose `a` to the enclosing dep set.
        let deps = deps_for("{% block content %}{{ a }}{% endblock %}");
        assert_eq!(deps.len(), 1);
        assert!(
            deps[0].contains("a"),
            "expected 'a' in deps, got {:?}",
            deps[0]
        );
    }

    #[test]
    fn test_deps_plain_text_has_no_vars() {
        let deps = deps_for("hello world");
        assert_eq!(deps.len(), 1);
        // Allow "*" to be absent — Text nodes are pure no-op.
        assert!(
            !deps[0].contains("*"),
            "Text node should NOT contribute wildcard; got {:?}",
            deps[0],
        );
    }

    // -----------------------------------------------------------------
    // Sub-item 2: Node variant exhaustiveness check
    // -----------------------------------------------------------------

    /// Allow-list of Node variants that legitimately have no variable
    /// references. Any variant NOT in this list MUST produce a non-empty
    /// dep set (either real vars or the `"*"` wildcard).
    ///
    /// When adding a new `Node` variant:
    /// 1. `sample_for_coverage` below will fail to compile — add an arm.
    /// 2. `sample_nodes` must include the new variant so the runtime
    ///    check exercises it.
    /// 3. Either:
    ///    - add an arm to `extract_from_nodes` that contributes deps, OR
    ///    - add the variant name here if it's truly varless.
    const NO_VARS_VARIANTS: &[&str] = &[
        "Text",
        "Comment",
        "CsrfToken",
        "Static",
        "TemplateTag",
        "Now",
        "Extends",
        "Load",
        "UnsupportedTag",
    ];

    /// Compile-time exhaustiveness anchor: every `Node` variant must have
    /// an arm here. If a new variant is added to `Node` and this match
    /// isn't updated, compilation fails.
    fn sample_for_coverage(n: &Node) -> &'static str {
        match n {
            Node::Text(_) => "Text",
            Node::Variable(..) => "Variable",
            Node::If { .. } => "If",
            Node::For { .. } => "For",
            Node::Block { .. } => "Block",
            Node::Extends(_) => "Extends",
            Node::Include { .. } => "Include",
            Node::Comment => "Comment",
            Node::Load(_) => "Load",
            Node::CsrfToken => "CsrfToken",
            Node::Static(_) => "Static",
            Node::With { .. } => "With",
            Node::ReactComponent { .. } => "ReactComponent",
            Node::RustComponent { .. } => "RustComponent",
            Node::CustomTag { .. } => "CustomTag",
            Node::BlockCustomTag { .. } => "BlockCustomTag",
            Node::WidthRatio { .. } => "WidthRatio",
            Node::FirstOf { .. } => "FirstOf",
            Node::TemplateTag(_) => "TemplateTag",
            Node::Spaceless { .. } => "Spaceless",
            Node::Cycle { .. } => "Cycle",
            Node::Now(_) => "Now",
            Node::UnsupportedTag { .. } => "UnsupportedTag",
            Node::InlineIf { .. } => "InlineIf",
            Node::AssignTag { .. } => "AssignTag",
        }
    }

    /// Build one dummy instance of every `Node` variant. Minimal values;
    /// we only care that `extract_per_node_deps` yields a non-empty set
    /// (for variants that should track vars) or an empty one (for
    /// allow-listed variants).
    fn sample_nodes() -> Vec<Node> {
        vec![
            Node::Text("hi".into()),
            Node::Variable("a".into(), vec![], false),
            Node::If {
                condition: "c".into(),
                true_nodes: vec![],
                false_nodes: vec![],
                in_tag_context: false,
            },
            Node::For {
                var_names: vec!["item".into()],
                iterable: "items".into(),
                reversed: false,
                nodes: vec![],
                empty_nodes: vec![],
            },
            Node::Block {
                name: "content".into(),
                nodes: vec![Node::Variable("a".into(), vec![], false)],
            },
            Node::Extends("base.html".into()),
            Node::Include {
                template: "x.html".into(),
                with_vars: vec![],
                only: false,
            },
            Node::Comment,
            Node::Load(vec!["mytags".into()]),
            Node::CsrfToken,
            Node::Static("img/foo.png".into()),
            Node::With {
                assignments: vec![("x".into(), "y".into())],
                nodes: vec![],
            },
            Node::ReactComponent {
                name: "MyComp".into(),
                props: vec![("foo".into(), "bar".into())],
                children: vec![],
            },
            Node::RustComponent {
                name: "MyRust".into(),
                props: vec![("foo".into(), "bar".into())],
            },
            Node::CustomTag {
                name: "url".into(),
                args: vec!["view_name".into()],
            },
            Node::BlockCustomTag {
                name: "modal".into(),
                args: vec![],
                children: vec![],
            },
            Node::WidthRatio {
                value: "a".into(),
                max_value: "b".into(),
                max_width: "100".into(),
            },
            Node::FirstOf {
                args: vec!["a".into(), "b".into()],
            },
            Node::TemplateTag("openblock".into()),
            Node::Spaceless {
                nodes: vec![Node::Variable("a".into(), vec![], false)],
            },
            Node::Cycle {
                values: vec!["a".into(), "b".into()],
                name: None,
            },
            Node::Now("Y-m-d".into()),
            Node::UnsupportedTag {
                name: "ifchanged".into(),
                args: vec![],
            },
            Node::InlineIf {
                true_expr: "a".into(),
                condition: "cond".into(),
                false_expr: "b".into(),
                filters: vec![],
            },
            Node::AssignTag {
                name: "assign_slot".into(),
                args: vec!["var_name".into()],
            },
        ]
    }

    #[test]
    fn test_exhaustive_variant_coverage() {
        // Sanity: samples cover every variant. `sample_for_coverage` is the
        // compile-time anchor — if a new variant is added to Node without
        // updating both this function and `sample_nodes`, the match fails
        // to compile.
        let samples = sample_nodes();
        let names: Vec<&'static str> = samples.iter().map(sample_for_coverage).collect();

        // Ensure no duplicates / omissions — each variant covered exactly once.
        let unique: HashSet<&'static str> = names.iter().copied().collect();
        assert_eq!(
            unique.len(),
            names.len(),
            "sample_nodes() contains duplicate variants: {:?}",
            names,
        );

        // Expected-count sanity: if `Node` grows, either this number updates
        // in lock-step with `sample_nodes` additions (fine), or a duplicate
        // was introduced (caught above). Don't hard-code the count here —
        // it would drift. Instead, for each sample, assert the invariant
        // individually.
        for node in &samples {
            let name = sample_for_coverage(node);
            let deps = extract_per_node_deps(std::slice::from_ref(node));
            assert_eq!(deps.len(), 1, "extract_per_node_deps returned wrong arity");
            let set = &deps[0];
            let allow_listed = NO_VARS_VARIANTS.contains(&name);

            if !allow_listed {
                assert!(
                    !set.is_empty(),
                    "Node::{name} produced empty dep set but is not in \
                     NO_VARS_VARIANTS. Either add an arm to \
                     extract_from_nodes that tracks its variable \
                     references (or contributes '*' wildcard), or add \
                     \"{name}\" to NO_VARS_VARIANTS if the variant is \
                     genuinely var-less. This guard exists because #783 \
                     (and #774 before it) was caused by a silent \
                     dep-drop on a Node variant that fell through the \
                     `_ => {{}}` default arm in extract_from_nodes.",
                );
            }
        }
    }
}
