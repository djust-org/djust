//! Template renderer that converts AST nodes to output strings

use crate::filters;
use crate::inheritance::TemplateLoader;
use crate::parser::Node;
use djust_components::Component;
use djust_core::{Context, DjangoRustError, Result, Value};
use std::collections::HashSet;
use std::sync::Mutex;

/// Track which unsupported tags we've already warned about (to avoid log spam)
static WARNED_TAGS: Mutex<Option<HashSet<String>>> = Mutex::new(None);

/// Warn about an unsupported tag (only once per tag name)
fn warn_unsupported_tag(tag_signature: &str) {
    let mut guard = WARNED_TAGS.lock().unwrap();
    let warned = guard.get_or_insert_with(HashSet::new);

    if !warned.contains(tag_signature) {
        eprintln!(
            "[djust] Warning: Unsupported template tag '{tag_signature}' - \
             this tag has no registered handler and will be ignored"
        );
        warned.insert(tag_signature.to_string());
    }
}

pub fn render_nodes(nodes: &[Node], context: &Context) -> Result<String> {
    render_nodes_with_loader(nodes, context, None::<&NoOpLoader>)
}

/// Render nodes with an optional template loader for {% include %} support
pub fn render_nodes_with_loader<L: TemplateLoader>(
    nodes: &[Node],
    context: &Context,
    loader: Option<&L>,
) -> Result<String> {
    let mut output = String::new();

    for node in nodes {
        output.push_str(&render_node_with_loader(node, context, loader)?);
    }

    Ok(output)
}

/// No-op loader for when no loader is provided
struct NoOpLoader;

impl TemplateLoader for NoOpLoader {
    fn load_template(&self, _name: &str) -> Result<Vec<Node>> {
        Err(DjangoRustError::TemplateError(
            "Template loader not configured".to_string(),
        ))
    }
}

fn render_node_with_loader<L: TemplateLoader>(
    node: &Node,
    context: &Context,
    loader: Option<&L>,
) -> Result<String> {
    match node {
        Node::Text(text) => Ok(text.clone()),

        Node::Variable(var_name, filter_specs) => {
            let mut value = context.get(var_name).cloned().unwrap_or(Value::Null);

            // Apply filters
            for (filter_name, arg) in filter_specs {
                value = filters::apply_filter(filter_name, &value, arg.as_deref())?;
            }

            Ok(value.to_string())
        }

        Node::If {
            condition,
            true_nodes,
            false_nodes,
        } => {
            let condition_result = evaluate_condition(condition, context)?;

            if condition_result {
                render_nodes_with_loader(true_nodes, context, loader)
            } else {
                render_nodes_with_loader(false_nodes, context, loader)
            }
        }

        Node::For {
            var_names,
            iterable,
            reversed,
            nodes,
            empty_nodes,
        } => {
            let iterable_value = context.get(iterable).cloned().unwrap_or(Value::Null);

            match iterable_value {
                Value::List(items) => {
                    // If list is empty, render the {% empty %} block
                    if items.is_empty() {
                        return render_nodes_with_loader(empty_nodes, context, loader);
                    }

                    let mut output = String::new();
                    let mut ctx = context.clone();

                    // Create an iterator, reversing if needed
                    let iter: Box<dyn Iterator<Item = Value>> = if *reversed {
                        Box::new(items.into_iter().rev())
                    } else {
                        Box::new(items.into_iter())
                    };

                    for item in iter {
                        // Handle tuple unpacking: {% for a, b in items %}
                        if var_names.len() == 1 {
                            // Single variable: {% for item in items %}
                            ctx.set(var_names[0].clone(), item);
                        } else {
                            // Multiple variables: {% for key, value in items %}
                            // Expect item to be a list/tuple
                            match &item {
                                Value::List(tuple_items) => {
                                    // Unpack tuple items into separate variables
                                    for (i, var_name) in var_names.iter().enumerate() {
                                        if i < tuple_items.len() {
                                            ctx.set(var_name.clone(), tuple_items[i].clone());
                                        } else {
                                            // If tuple has fewer items than var names, set to Null
                                            ctx.set(var_name.clone(), Value::Null);
                                        }
                                    }
                                }
                                _ => {
                                    // If item is not a list, set all vars to Null except first
                                    ctx.set(var_names[0].clone(), item.clone());
                                    for var_name in &var_names[1..] {
                                        ctx.set(var_name.clone(), Value::Null);
                                    }
                                }
                            }
                        }
                        output.push_str(&render_nodes_with_loader(nodes, &ctx, loader)?);
                    }

                    Ok(output)
                }
                _ => {
                    // If not a list (null, etc.), render the empty block
                    render_nodes_with_loader(empty_nodes, context, loader)
                }
            }
        }

        Node::Block { name: _, nodes } => {
            // For now, just render the block content
            // In a full implementation, this would handle template inheritance
            render_nodes_with_loader(nodes, context, loader)
        }

        Node::Include {
            template,
            with_vars,
            only,
        } => {
            // Load and render the included template
            if let Some(loader) = loader {
                // Remove quotes from template name if present
                let name = template.trim_matches(|c| c == '"' || c == '\'');
                let nodes = loader.load_template(name)?;

                // Create context for included template
                let mut include_context = if *only {
                    // Only use with_vars, not parent context
                    Context::new()
                } else {
                    // Start with parent context
                    context.clone()
                };

                // Apply with_vars assignments
                for (key, value_expr) in with_vars {
                    // Resolve value from parent context or use as literal
                    let value = context.get(value_expr).cloned().unwrap_or_else(|| {
                        // Check if it's a string literal
                        if (value_expr.starts_with('"') && value_expr.ends_with('"'))
                            || (value_expr.starts_with('\'') && value_expr.ends_with('\''))
                        {
                            Value::String(value_expr[1..value_expr.len() - 1].to_string())
                        } else {
                            Value::String(value_expr.clone())
                        }
                    });
                    include_context.set(key.clone(), value);
                }

                render_nodes_with_loader(&nodes, &include_context, Some(loader))
            } else {
                // No loader available - warn developers (once per template)
                let tag_sig = format!("{{% include \"{template}\" %}} (no loader)");
                warn_unsupported_tag(&tag_sig);
                Ok(format!(
                    "<!-- djust: include '{template}' ignored - no template loader -->"
                ))
            }
        }

        Node::ReactComponent {
            name,
            props,
            children,
        } => {
            // Render React component as data attributes for client-side hydration
            let mut output = String::new();
            output.push_str(&format!("<div data-react-component=\"{name}\""));

            // Add props as data attributes
            if !props.is_empty() {
                output.push_str(" data-react-props='");
                let props_json: Vec<String> = props
                    .iter()
                    .map(|(k, v)| {
                        // Resolve Django template variable syntax: {{ var.path }}
                        let resolved_value = if v.starts_with("{{") && v.ends_with("}}") {
                            // Extract variable name from {{ ... }}
                            let var_name = v.trim_start_matches("{{").trim_end_matches("}}").trim();

                            // Try to resolve from context
                            if let Some(ctx_value) = context.get(var_name) {
                                ctx_value.to_string()
                            } else {
                                // Keep the original template syntax for Python-side resolution
                                v.clone()
                            }
                        } else if let Some(ctx_value) = context.get(v) {
                            // Direct variable reference (no {{ }})
                            ctx_value.to_string()
                        } else {
                            v.clone()
                        };
                        format!("\"{}\":\"{}\"", k, resolved_value.replace('"', "\\\""))
                    })
                    .collect();
                output.push_str(&format!("{{{}}}", props_json.join(",")));
                output.push('\'');
            }

            output.push('>');

            // Render children
            for child in children {
                output.push_str(&render_node_with_loader(child, context, loader)?);
            }

            output.push_str("</div>");
            Ok(output)
        }

        Node::RustComponent { name, props } => {
            // Render Rust component server-side
            render_rust_component(name, props, context)
        }

        Node::CsrfToken => {
            // Render CSRF token hidden input
            // Get token from context (should be provided by Django)
            let token = context
                .get("csrf_token")
                .map(|v| v.to_string())
                .unwrap_or_else(|| "CSRF_TOKEN_NOT_PROVIDED".to_string());

            Ok(format!(
                "<input type=\"hidden\" name=\"csrfmiddlewaretoken\" value=\"{token}\">"
            ))
        }

        Node::Static(path) => {
            // Render static file URL
            // Get STATIC_URL from context (should be provided by Django)
            let static_url = context
                .get("STATIC_URL")
                .map(|v| v.to_string())
                .unwrap_or_else(|| "/static/".to_string());

            Ok(format!("{static_url}{path}"))
        }

        Node::With { assignments, nodes } => {
            // Create new context with assigned variables
            let mut new_context = context.clone();

            // Process assignments
            for (var_name, expression) in assignments {
                // Try to evaluate expression from context
                // For now, we'll just look up the expression as a variable name
                // In full Django, this would support complex expressions
                let value = context
                    .get(expression)
                    .cloned()
                    .unwrap_or_else(|| Value::String(expression.clone()));
                new_context.set(var_name.clone(), value);
            }

            // Render children with new context
            render_nodes_with_loader(nodes, &new_context, loader)
        }

        Node::Extends(_) => {
            // Extends should be handled at template level, not during node rendering
            // This is a marker node that triggers inheritance processing
            Err(DjangoRustError::TemplateError(
                "{% extends %} must be processed at template level, not during rendering"
                    .to_string(),
            ))
        }

        Node::Comment => Ok(String::new()),

        Node::UnsupportedTag { name, args } => {
            // Build tag signature for warning (only warn once per unique tag)
            let args_str = if args.is_empty() {
                String::new()
            } else {
                format!(" {}", args.join(" "))
            };
            let tag_sig = format!("{{% {name}{args_str} %}}");

            // Warn once per tag signature (avoids log spam)
            warn_unsupported_tag(&tag_sig);

            // Return HTML comment so it's visible in page source during development
            Ok(format!("<!-- djust: unsupported tag '{tag_sig}' -->"))
        }

        Node::CustomTag { name, args } => {
            // Call Python handler for custom tags (e.g., {% url %}, {% static %})
            //
            // The handler is looked up in the registry and called with:
            // - args: The raw arguments from the template tag
            // - context: The current template context (converted to Python dict)
            //
            // The handler must return a string to be inserted in the output.

            // First, resolve any variable references in args
            let resolved_args: Vec<String> = args
                .iter()
                .map(|arg| {
                    // Check if arg is a variable reference (not a string literal)
                    let arg_trimmed = arg.trim();
                    if (arg_trimmed.starts_with('"') && arg_trimmed.ends_with('"'))
                        || (arg_trimmed.starts_with('\'') && arg_trimmed.ends_with('\''))
                    {
                        // String literal - keep as-is
                        arg.clone()
                    } else if let Some(eq_pos) = arg.find('=') {
                        // Named parameter: key=value
                        let key = &arg[..eq_pos];
                        let value = arg[eq_pos + 1..].trim();
                        if (value.starts_with('"') && value.ends_with('"'))
                            || (value.starts_with('\'') && value.ends_with('\''))
                        {
                            // Value is a string literal
                            arg.clone()
                        } else {
                            // Value is a variable - try to resolve
                            match context.get(value) {
                                Some(resolved) => format!("{}={}", key, resolved),
                                None => arg.clone(),
                            }
                        }
                    } else {
                        // Might be a variable - try to resolve
                        match context.get(arg_trimmed) {
                            Some(resolved) => resolved.to_string(),
                            None => arg.clone(),
                        }
                    }
                })
                .collect();

            // Convert context to HashMap for the handler
            let context_map = context.to_hashmap();

            // Call the Python handler
            crate::registry::call_handler(name, &resolved_args, &context_map).map_err(|e| {
                DjangoRustError::TemplateError(format!("Custom tag '{}' error: {}", name, e))
            })
        }
    }
}

/// Render a Rust component by instantiating it and calling its render method
fn render_rust_component(
    name: &str,
    props: &[(String, String)],
    context: &Context,
) -> Result<String> {
    // Get framework from context or default to Bootstrap5
    let framework = context
        .get("_framework")
        .and_then(|v| {
            if let Value::String(s) = v {
                Some(s.as_str())
            } else {
                None
            }
        })
        .unwrap_or("bootstrap5");

    let fw = framework.parse().unwrap();

    // Match component name and instantiate
    match name {
        "RustButton" => {
            // Extract required props
            let id = get_prop("id", props, context)?;
            let label = get_prop("label", props, context)?;

            // Create button with basic props
            let mut button = djust_components::ui::Button::new(id, label);

            // Apply optional props
            if let Ok(variant_str) = get_prop("variant", props, context) {
                let variant = match variant_str.as_str() {
                    "secondary" => djust_components::ui::button::ButtonVariant::Secondary,
                    "success" => djust_components::ui::button::ButtonVariant::Success,
                    "danger" => djust_components::ui::button::ButtonVariant::Danger,
                    "warning" => djust_components::ui::button::ButtonVariant::Warning,
                    "info" => djust_components::ui::button::ButtonVariant::Info,
                    "light" => djust_components::ui::button::ButtonVariant::Light,
                    "dark" => djust_components::ui::button::ButtonVariant::Dark,
                    "link" => djust_components::ui::button::ButtonVariant::Link,
                    _ => djust_components::ui::button::ButtonVariant::Primary,
                };
                button.variant = variant;
            }

            if let Ok(size_str) = get_prop("size", props, context) {
                let size = match size_str.as_str() {
                    "sm" | "small" => djust_components::ui::button::ButtonSize::Small,
                    "lg" | "large" => djust_components::ui::button::ButtonSize::Large,
                    _ => djust_components::ui::button::ButtonSize::Medium,
                };
                button.size = size;
            }

            if let Ok(outline) = get_prop("outline", props, context) {
                button.outline = outline == "true" || outline == "True";
            }

            if let Ok(disabled) = get_prop("disabled", props, context) {
                button.disabled = disabled == "true" || disabled == "True";
            }

            if let Ok(full_width) = get_prop("fullWidth", props, context) {
                button.full_width = full_width == "true" || full_width == "True";
            }

            if let Ok(icon) = get_prop("icon", props, context) {
                button.icon = Some(icon);
            }

            if let Ok(on_click) = get_prop("onClick", props, context) {
                button.on_click = Some(on_click);
            }

            // Render the component
            button.render(fw).map_err(|e| {
                DjangoRustError::TemplateError(format!("Failed to render RustButton: {e}"))
            })
        }

        "RustInput" => {
            // Extract required props
            let id = get_prop("id", props, context)?;

            // Create input with basic props
            let mut input = djust_components::ui::Input::new(id);

            // Apply optional props
            if let Ok(input_type_str) = get_prop("inputType", props, context) {
                let input_type = match input_type_str.as_str() {
                    "email" => djust_components::ui::input::InputType::Email,
                    "password" => djust_components::ui::input::InputType::Password,
                    "number" => djust_components::ui::input::InputType::Number,
                    "tel" => djust_components::ui::input::InputType::Tel,
                    "url" => djust_components::ui::input::InputType::Url,
                    "search" => djust_components::ui::input::InputType::Search,
                    "date" => djust_components::ui::input::InputType::Date,
                    "time" => djust_components::ui::input::InputType::Time,
                    "datetime" => djust_components::ui::input::InputType::DateTime,
                    "color" => djust_components::ui::input::InputType::Color,
                    "file" => djust_components::ui::input::InputType::File,
                    _ => djust_components::ui::input::InputType::Text,
                };
                input.input_type = input_type;
            }

            if let Ok(size_str) = get_prop("size", props, context) {
                let size = match size_str.as_str() {
                    "sm" | "small" => djust_components::ui::input::InputSize::Small,
                    "lg" | "large" => djust_components::ui::input::InputSize::Large,
                    _ => djust_components::ui::input::InputSize::Medium,
                };
                input.size = size;
            }

            if let Ok(name) = get_prop("name", props, context) {
                input.name = Some(name);
            }

            if let Ok(value) = get_prop("value", props, context) {
                input.value = Some(value);
            }

            if let Ok(placeholder) = get_prop("placeholder", props, context) {
                input.placeholder = Some(placeholder);
            }

            if let Ok(disabled) = get_prop("disabled", props, context) {
                input.disabled = disabled == "true" || disabled == "True";
            }

            if let Ok(readonly) = get_prop("readonly", props, context) {
                input.readonly = readonly == "true" || readonly == "True";
            }

            if let Ok(required) = get_prop("required", props, context) {
                input.required = required == "true" || required == "True";
            }

            if let Ok(on_input) = get_prop("onInput", props, context) {
                input.on_input = Some(on_input);
            }

            if let Ok(on_change) = get_prop("onChange", props, context) {
                input.on_change = Some(on_change);
            }

            // Render the component
            input.render(fw).map_err(|e| {
                DjangoRustError::TemplateError(format!("Failed to render RustInput: {e}"))
            })
        }

        "RustText" => {
            // Extract required content prop
            let content = get_prop("content", props, context)?;

            // Create text with basic props
            let mut text = djust_components::ui::Text::new(content);

            // Apply optional props
            if let Ok(element_str) = get_prop("element", props, context) {
                let element = match element_str.as_str() {
                    "p" | "paragraph" => djust_components::ui::text::TextElement::Paragraph,
                    "span" => djust_components::ui::text::TextElement::Span,
                    "label" => djust_components::ui::text::TextElement::Label,
                    "div" => djust_components::ui::text::TextElement::Div,
                    "h1" => djust_components::ui::text::TextElement::H1,
                    "h2" => djust_components::ui::text::TextElement::H2,
                    "h3" => djust_components::ui::text::TextElement::H3,
                    "h4" => djust_components::ui::text::TextElement::H4,
                    "h5" => djust_components::ui::text::TextElement::H5,
                    "h6" => djust_components::ui::text::TextElement::H6,
                    _ => djust_components::ui::text::TextElement::Span,
                };
                text.element = element;
            }

            if let Ok(color_str) = get_prop("color", props, context) {
                let color = match color_str.as_str() {
                    "primary" => djust_components::ui::text::TextColor::Primary,
                    "secondary" => djust_components::ui::text::TextColor::Secondary,
                    "success" => djust_components::ui::text::TextColor::Success,
                    "danger" => djust_components::ui::text::TextColor::Danger,
                    "warning" => djust_components::ui::text::TextColor::Warning,
                    "info" => djust_components::ui::text::TextColor::Info,
                    "light" => djust_components::ui::text::TextColor::Light,
                    "dark" => djust_components::ui::text::TextColor::Dark,
                    "muted" => djust_components::ui::text::TextColor::Muted,
                    _ => djust_components::ui::text::TextColor::Dark,
                };
                text.color = Some(color);
            }

            if let Ok(weight_str) = get_prop("weight", props, context) {
                let weight = match weight_str.as_str() {
                    "bold" => djust_components::ui::text::FontWeight::Bold,
                    "light" => djust_components::ui::text::FontWeight::Light,
                    _ => djust_components::ui::text::FontWeight::Normal,
                };
                text.weight = weight;
            }

            if let Ok(for_input) = get_prop("forInput", props, context) {
                text.for_input = Some(for_input);
            }

            if let Ok(id) = get_prop("id", props, context) {
                text.id = Some(id);
            }

            // Render the component
            text.render(fw).map_err(|e| {
                DjangoRustError::TemplateError(format!("Failed to render RustText: {e}"))
            })
        }

        "RustCard" => {
            // Extract required body prop
            let body = get_prop("body", props, context)?;

            // Create card with basic props
            let mut card = djust_components::ui::Card::new(body);

            // Apply optional props
            if let Ok(variant_str) = get_prop("variant", props, context) {
                let variant = match variant_str.as_str() {
                    "primary" => djust_components::ui::card::CardVariant::Primary,
                    "secondary" => djust_components::ui::card::CardVariant::Secondary,
                    "success" => djust_components::ui::card::CardVariant::Success,
                    "danger" => djust_components::ui::card::CardVariant::Danger,
                    "warning" => djust_components::ui::card::CardVariant::Warning,
                    "info" => djust_components::ui::card::CardVariant::Info,
                    "light" => djust_components::ui::card::CardVariant::Light,
                    "dark" => djust_components::ui::card::CardVariant::Dark,
                    _ => djust_components::ui::card::CardVariant::Default,
                };
                card.variant = variant;
            }

            if let Ok(header) = get_prop("header", props, context) {
                card.header = Some(header);
            }

            if let Ok(footer) = get_prop("footer", props, context) {
                card.footer = Some(footer);
            }

            if let Ok(border) = get_prop("border", props, context) {
                card.border = border == "true" || border == "True";
            }

            if let Ok(shadow) = get_prop("shadow", props, context) {
                card.shadow = shadow == "true" || shadow == "True";
            }

            if let Ok(id) = get_prop("id", props, context) {
                card.id = Some(id);
            }

            // Render the component
            card.render(fw).map_err(|e| {
                DjangoRustError::TemplateError(format!("Failed to render RustCard: {e}"))
            })
        }

        "RustAlert" => {
            // Extract required message prop
            let message = get_prop("message", props, context)?;

            // Create alert with basic props
            let mut alert = djust_components::ui::Alert::new(message);

            // Apply optional props
            if let Ok(variant_str) = get_prop("variant", props, context) {
                let variant = match variant_str.as_str() {
                    "primary" => djust_components::ui::alert::AlertVariant::Primary,
                    "secondary" => djust_components::ui::alert::AlertVariant::Secondary,
                    "success" => djust_components::ui::alert::AlertVariant::Success,
                    "danger" => djust_components::ui::alert::AlertVariant::Danger,
                    "warning" => djust_components::ui::alert::AlertVariant::Warning,
                    "info" => djust_components::ui::alert::AlertVariant::Info,
                    "light" => djust_components::ui::alert::AlertVariant::Light,
                    "dark" => djust_components::ui::alert::AlertVariant::Dark,
                    _ => djust_components::ui::alert::AlertVariant::Info,
                };
                alert.variant = variant;
            }

            if let Ok(dismissible) = get_prop("dismissible", props, context) {
                alert.dismissible = dismissible == "true" || dismissible == "True";
            }

            if let Ok(icon) = get_prop("icon", props, context) {
                alert.icon = Some(icon);
            }

            if let Ok(id) = get_prop("id", props, context) {
                alert.id = Some(id);
            }

            // Render the component
            alert.render(fw).map_err(|e| {
                DjangoRustError::TemplateError(format!("Failed to render RustAlert: {e}"))
            })
        }

        "RustModal" => {
            // Extract required props
            let id = get_prop("id", props, context)?;
            let body = get_prop("body", props, context)?;

            // Create modal with basic props
            let mut modal = djust_components::ui::Modal::new(id, body);

            // Apply optional props
            if let Ok(title) = get_prop("title", props, context) {
                modal.title = Some(title);
            }

            if let Ok(footer) = get_prop("footer", props, context) {
                modal.footer = Some(footer);
            }

            if let Ok(size_str) = get_prop("size", props, context) {
                let size = match size_str.as_str() {
                    "small" | "sm" => djust_components::ui::modal::ModalSize::Small,
                    "medium" | "md" => djust_components::ui::modal::ModalSize::Medium,
                    "large" | "lg" => djust_components::ui::modal::ModalSize::Large,
                    "xl" | "extralarge" => djust_components::ui::modal::ModalSize::ExtraLarge,
                    _ => djust_components::ui::modal::ModalSize::Medium,
                };
                modal.size = size;
            }

            if let Ok(centered) = get_prop("centered", props, context) {
                modal.centered = centered == "true" || centered == "True";
            }

            if let Ok(scrollable) = get_prop("scrollable", props, context) {
                modal.scrollable = scrollable == "true" || scrollable == "True";
            }

            // Render the component
            modal.render(fw).map_err(|e| {
                DjangoRustError::TemplateError(format!("Failed to render RustModal: {e}"))
            })
        }

        "RustDropdown" => {
            // Extract required id prop
            let id = get_prop("id", props, context)?;

            // Create dropdown with basic props
            let mut dropdown = djust_components::ui::Dropdown::new(id);

            // Parse items from template
            // Expected format: items="[{'label': 'Option 1', 'value': 'opt1'}, ...]"
            if let Ok(items_str) = get_prop("items", props, context) {
                // Try to parse as JSON
                if let Ok(items_json) = serde_json::from_str::<Vec<serde_json::Value>>(&items_str) {
                    let mut items = Vec::new();
                    for item in items_json {
                        if let (Some(label), Some(value)) = (
                            item.get("label").and_then(|v| v.as_str()),
                            item.get("value").and_then(|v| v.as_str()),
                        ) {
                            items.push(djust_components::ui::dropdown::DropdownItem {
                                label: label.to_string(),
                                value: value.to_string(),
                            });
                        }
                    }
                    dropdown.items = items;
                }
            }

            // Apply optional props
            if let Ok(selected) = get_prop("selected", props, context) {
                dropdown.selected = Some(selected);
            }

            if let Ok(variant_str) = get_prop("variant", props, context) {
                let variant = match variant_str.as_str() {
                    "primary" => djust_components::ui::dropdown::DropdownVariant::Primary,
                    "secondary" => djust_components::ui::dropdown::DropdownVariant::Secondary,
                    "success" => djust_components::ui::dropdown::DropdownVariant::Success,
                    "danger" => djust_components::ui::dropdown::DropdownVariant::Danger,
                    "warning" => djust_components::ui::dropdown::DropdownVariant::Warning,
                    "info" => djust_components::ui::dropdown::DropdownVariant::Info,
                    "light" => djust_components::ui::dropdown::DropdownVariant::Light,
                    "dark" => djust_components::ui::dropdown::DropdownVariant::Dark,
                    _ => djust_components::ui::dropdown::DropdownVariant::Primary,
                };
                dropdown.variant = variant;
            }

            if let Ok(size_str) = get_prop("size", props, context) {
                let size = match size_str.as_str() {
                    "sm" | "small" => djust_components::ui::dropdown::DropdownSize::Small,
                    "lg" | "large" => djust_components::ui::dropdown::DropdownSize::Large,
                    _ => djust_components::ui::dropdown::DropdownSize::Medium,
                };
                dropdown.size = size;
            }

            if let Ok(disabled) = get_prop("disabled", props, context) {
                dropdown.disabled = disabled == "true" || disabled == "True";
            }

            if let Ok(placeholder) = get_prop("placeholder", props, context) {
                dropdown.placeholder = Some(placeholder);
            }

            // Render the component
            dropdown.render(fw).map_err(|e| {
                DjangoRustError::TemplateError(format!("Failed to render RustDropdown: {e}"))
            })
        }

        "RustTabs" => {
            // Extract required id prop
            let id = get_prop("id", props, context)?;

            // Create tabs with basic props
            let mut tabs = djust_components::ui::Tabs::new(id);

            // Parse tabs from template
            // Expected format: tabs="[{'id': 'tab1', 'label': 'Tab 1', 'content': 'Content 1'}, ...]"
            if let Ok(tabs_str) = get_prop("tabs", props, context) {
                // Try to parse as JSON
                if let Ok(tabs_json) = serde_json::from_str::<Vec<serde_json::Value>>(&tabs_str) {
                    let mut tabs_vec = Vec::new();
                    for tab in tabs_json {
                        if let (Some(tab_id), Some(label), Some(content)) = (
                            tab.get("id").and_then(|v| v.as_str()),
                            tab.get("label").and_then(|v| v.as_str()),
                            tab.get("content").and_then(|v| v.as_str()),
                        ) {
                            tabs_vec.push(djust_components::ui::tabs::TabItem {
                                id: tab_id.to_string(),
                                label: label.to_string(),
                                content: content.to_string(),
                            });
                        }
                    }
                    if !tabs_vec.is_empty() && tabs.active.is_empty() {
                        tabs.active = tabs_vec[0].id.clone();
                    }
                    tabs.tabs = tabs_vec;
                }
            }

            // Apply optional props
            if let Ok(active) = get_prop("active", props, context) {
                tabs.active = active;
            }

            if let Ok(variant_str) = get_prop("variant", props, context) {
                let variant = match variant_str.as_str() {
                    "pills" => djust_components::ui::tabs::TabVariant::Pills,
                    "underline" => djust_components::ui::tabs::TabVariant::Underline,
                    _ => djust_components::ui::tabs::TabVariant::Default,
                };
                tabs.variant = variant;
            }

            if let Ok(vertical) = get_prop("vertical", props, context) {
                tabs.vertical = vertical == "true" || vertical == "True";
            }

            // Render the component
            tabs.render(fw).map_err(|e| {
                DjangoRustError::TemplateError(format!("Failed to render RustTabs: {e}"))
            })
        }

        _ => Err(DjangoRustError::TemplateError(format!(
            "Unknown Rust component: {name}"
        ))),
    }
}

/// Get a prop value, resolving template variables if needed
fn get_prop(key: &str, props: &[(String, String)], context: &Context) -> Result<String> {
    for (k, v) in props {
        if k == key {
            // Resolve Django template variable syntax: {{ var.path }}
            if v.starts_with("{{") && v.ends_with("}}") {
                let var_name = v.trim_start_matches("{{").trim_end_matches("}}").trim();

                if let Some(ctx_value) = context.get(var_name) {
                    return Ok(ctx_value.to_string());
                }
            } else if let Some(ctx_value) = context.get(v) {
                // Direct variable reference (no {{ }})
                return Ok(ctx_value.to_string());
            } else {
                // Literal value
                return Ok(v.clone());
            }
        }
    }

    Err(DjangoRustError::TemplateError(format!(
        "Missing required prop: {key}"
    )))
}

fn evaluate_condition(condition: &str, context: &Context) -> Result<bool> {
    let condition = condition.trim();

    // Handle simple boolean values
    if condition == "true" || condition == "True" {
        return Ok(true);
    }
    if condition == "false" || condition == "False" {
        return Ok(false);
    }

    // Handle "or" (lowest precedence - split first)
    // Use " or " with spaces to avoid matching variable names containing "or"
    if let Some(pos) = condition.find(" or ") {
        let left = &condition[..pos];
        let right = &condition[pos + 4..];
        return Ok(evaluate_condition(left, context)? || evaluate_condition(right, context)?);
    }

    // Handle "and" (higher precedence than "or")
    if let Some(pos) = condition.find(" and ") {
        let left = &condition[..pos];
        let right = &condition[pos + 5..];
        return Ok(evaluate_condition(left, context)? && evaluate_condition(right, context)?);
    }

    // Handle variable lookups
    if let Some(value) = context.get(condition) {
        return Ok(value.is_truthy());
    }

    // Handle negation
    if let Some(rest) = condition.strip_prefix("not ") {
        return Ok(!evaluate_condition(rest, context)?);
    }

    // Handle comparisons
    if condition.contains("==") {
        let parts: Vec<&str> = condition.split("==").map(|s| s.trim()).collect();
        if parts.len() == 2 {
            let left = get_value(parts[0], context)?;
            let right = get_value(parts[1], context)?;
            return Ok(values_equal(&left, &right));
        }
    }

    if condition.contains("!=") {
        let parts: Vec<&str> = condition.split("!=").map(|s| s.trim()).collect();
        if parts.len() == 2 {
            let left = get_value(parts[0], context)?;
            let right = get_value(parts[1], context)?;
            return Ok(!values_equal(&left, &right));
        }
    }

    // Handle >= (must be before > to avoid false match)
    if condition.contains(">=") {
        let parts: Vec<&str> = condition.split(">=").map(|s| s.trim()).collect();
        if parts.len() == 2 {
            let left = get_value(parts[0], context)?;
            let right = get_value(parts[1], context)?;
            return Ok(compare_values(&left, &right) >= 0);
        }
    }

    // Handle <= (must be before < to avoid false match)
    if condition.contains("<=") {
        let parts: Vec<&str> = condition.split("<=").map(|s| s.trim()).collect();
        if parts.len() == 2 {
            let left = get_value(parts[0], context)?;
            let right = get_value(parts[1], context)?;
            return Ok(compare_values(&left, &right) <= 0);
        }
    }

    // Handle "in" operator: {% if item in list %}
    if condition.contains(" in ") {
        let parts: Vec<&str> = condition.splitn(2, " in ").map(|s| s.trim()).collect();
        if parts.len() == 2 {
            let needle = get_value(parts[0], context)?;
            let haystack = get_value(parts[1], context)?;
            return match haystack {
                Value::List(items) => Ok(items.iter().any(|item| values_equal(&needle, item))),
                Value::String(s) => {
                    if let Value::String(n) = &needle {
                        Ok(s.contains(n.as_str()))
                    } else {
                        Ok(false)
                    }
                }
                _ => Ok(false),
            };
        }
    }

    // Handle > (greater than)
    if condition.contains(" > ") {
        let parts: Vec<&str> = condition.split(" > ").map(|s| s.trim()).collect();
        if parts.len() == 2 {
            let left = get_value(parts[0], context)?;
            let right = get_value(parts[1], context)?;
            return Ok(compare_values(&left, &right) > 0);
        }
    }

    // Handle < (less than)
    if condition.contains(" < ") {
        let parts: Vec<&str> = condition.split(" < ").map(|s| s.trim()).collect();
        if parts.len() == 2 {
            let left = get_value(parts[0], context)?;
            let right = get_value(parts[1], context)?;
            return Ok(compare_values(&left, &right) < 0);
        }
    }

    // Default to false for unknown conditions
    Ok(false)
}

fn get_value(expr: &str, context: &Context) -> Result<Value> {
    // Try to get from context
    if let Some(value) = context.get(expr) {
        return Ok(value.clone());
    }

    // Try to parse as literal
    if let Ok(i) = expr.parse::<i64>() {
        return Ok(Value::Integer(i));
    }

    if let Ok(f) = expr.parse::<f64>() {
        return Ok(Value::Float(f));
    }

    // String literal (remove quotes)
    if (expr.starts_with('"') && expr.ends_with('"'))
        || (expr.starts_with('\'') && expr.ends_with('\''))
    {
        return Ok(Value::String(expr[1..expr.len() - 1].to_string()));
    }

    Ok(Value::Null)
}

fn values_equal(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Null, Value::Null) => true,
        (Value::Bool(a), Value::Bool(b)) => a == b,
        (Value::Integer(a), Value::Integer(b)) => a == b,
        (Value::Float(a), Value::Float(b)) => (a - b).abs() < f64::EPSILON,
        (Value::String(a), Value::String(b)) => a == b,
        _ => false,
    }
}

/// Compare two values and return -1 (less), 0 (equal), or 1 (greater).
/// Returns 0 for incomparable types.
fn compare_values(a: &Value, b: &Value) -> i32 {
    match (a, b) {
        (Value::Integer(a), Value::Integer(b)) => a.cmp(b) as i32,
        (Value::Float(a), Value::Float(b)) => {
            if (a - b).abs() < f64::EPSILON {
                0
            } else if a < b {
                -1
            } else {
                1
            }
        }
        // Allow comparing integers and floats
        (Value::Integer(a), Value::Float(b)) => {
            let a_f = *a as f64;
            if (a_f - b).abs() < f64::EPSILON {
                0
            } else if a_f < *b {
                -1
            } else {
                1
            }
        }
        (Value::Float(a), Value::Integer(b)) => {
            let b_f = *b as f64;
            if (a - b_f).abs() < f64::EPSILON {
                0
            } else if *a < b_f {
                -1
            } else {
                1
            }
        }
        (Value::String(a), Value::String(b)) => a.cmp(b) as i32,
        // Null comparisons
        (Value::Null, Value::Null) => 0,
        // Incomparable types return 0 (treated as equal, so < and > fail)
        _ => 0,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::lexer::tokenize;
    use crate::parser::parse;

    #[test]
    fn test_render_text() {
        let nodes = vec![Node::Text("Hello".to_string())];
        let context = Context::new();
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "Hello");
    }

    #[test]
    fn test_render_variable() {
        let nodes = vec![Node::Variable("name".to_string(), vec![])];
        let mut context = Context::new();
        context.set("name".to_string(), Value::String("World".to_string()));
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "World");
    }

    #[test]
    fn test_render_if_true() {
        let tokens = tokenize("{% if show %}visible{% endif %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("show".to_string(), Value::Bool(true));
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "visible");
    }

    #[test]
    fn test_render_for() {
        let tokens = tokenize("{% for item in items %}{{ item }}{% endfor %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set(
            "items".to_string(),
            Value::List(vec![
                Value::String("a".to_string()),
                Value::String("b".to_string()),
                Value::String("c".to_string()),
            ]),
        );
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "abc");
    }

    #[test]
    fn test_render_for_reversed() {
        let tokens = tokenize("{% for item in items reversed %}{{ item }}{% endfor %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set(
            "items".to_string(),
            Value::List(vec![
                Value::String("a".to_string()),
                Value::String("b".to_string()),
                Value::String("c".to_string()),
            ]),
        );
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "cba");
    }

    #[test]
    fn test_render_for_reversed_numbers() {
        let tokens = tokenize("{% for num in numbers reversed %}{{ num }},{% endfor %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set(
            "numbers".to_string(),
            Value::List(vec![
                Value::Integer(1),
                Value::Integer(2),
                Value::Integer(3),
            ]),
        );
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "3,2,1,");
    }

    #[test]
    fn test_render_for_normal_not_affected() {
        // Ensure normal for loops still work
        let tokens = tokenize("{% for item in items %}{{ item }}{% endfor %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set(
            "items".to_string(),
            Value::List(vec![
                Value::String("x".to_string()),
                Value::String("y".to_string()),
                Value::String("z".to_string()),
            ]),
        );
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "xyz");
    }

    #[test]
    fn test_render_for_empty_with_items() {
        // Test that empty block is NOT rendered when list has items
        let tokens =
            tokenize("{% for item in items %}{{ item }}{% empty %}No items{% endfor %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set(
            "items".to_string(),
            Value::List(vec![
                Value::String("a".to_string()),
                Value::String("b".to_string()),
            ]),
        );
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "ab");
        assert!(!result.contains("No items"));
    }

    #[test]
    fn test_render_for_empty_without_items() {
        // Test that empty block IS rendered when list is empty
        let tokens =
            tokenize("{% for item in items %}{{ item }}{% empty %}No items{% endfor %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("items".to_string(), Value::List(vec![]));
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "No items");
    }

    #[test]
    fn test_render_for_empty_null_iterable() {
        // Test that empty block IS rendered when iterable is null/missing
        let tokens =
            tokenize("{% for item in items %}{{ item }}{% empty %}No items{% endfor %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let context = Context::new(); // items not set
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "No items");
    }

    #[test]
    fn test_render_for_empty_complex_content() {
        // Test that empty block can contain complex HTML
        let template = r#"{% for property in properties %}<tr><td>{{ property.name }}</td></tr>{% empty %}<tr><td colspan="6">No properties found. <a href="/add">Add property</a></td></tr>{% endfor %}"#;
        let tokens = tokenize(template).unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("properties".to_string(), Value::List(vec![]));
        let result = render_nodes(&nodes, &context).unwrap();
        assert!(result.contains("No properties found"));
        assert!(result.contains("<a href=\"/add\">"));
        assert!(result.contains("colspan=\"6\""));
    }

    #[test]
    fn test_csrf_token_tag() {
        let tokens = tokenize("{% csrf_token %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set(
            "csrf_token".to_string(),
            Value::String("test-csrf-token-123".to_string()),
        );
        let result = render_nodes(&nodes, &context).unwrap();
        assert!(result.contains("<input type=\"hidden\""));
        assert!(result.contains("name=\"csrfmiddlewaretoken\""));
        assert!(result.contains("value=\"test-csrf-token-123\""));
    }

    #[test]
    fn test_csrf_token_tag_without_token() {
        let tokens = tokenize("{% csrf_token %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let context = Context::new();
        let result = render_nodes(&nodes, &context).unwrap();
        assert!(result.contains("CSRF_TOKEN_NOT_PROVIDED"));
    }

    #[test]
    fn test_static_tag() {
        let tokens = tokenize("{% static 'css/style.css' %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set(
            "STATIC_URL".to_string(),
            Value::String("/static/".to_string()),
        );
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "/static/css/style.css");
    }

    #[test]
    fn test_static_tag_custom_url() {
        let tokens = tokenize("{% static \"images/logo.png\" %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set(
            "STATIC_URL".to_string(),
            Value::String("https://cdn.example.com/static/".to_string()),
        );
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "https://cdn.example.com/static/images/logo.png");
    }

    #[test]
    fn test_comment_tag() {
        let tokens = tokenize("Before{% comment %}Hidden content{% endcomment %}After").unwrap();
        let nodes = parse(&tokens).unwrap();
        let context = Context::new();
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "BeforeAfter");
    }

    #[test]
    fn test_with_tag() {
        let tokens = tokenize("{% with greeting=message %}{{ greeting }}{% endwith %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set(
            "message".to_string(),
            Value::String("Hello World".to_string()),
        );
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "Hello World");
    }

    #[test]
    fn test_with_tag_multiple_vars() {
        let tokens = tokenize("{% with a=x b=y %}{{ a }} and {{ b }}{% endwith %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("x".to_string(), Value::String("foo".to_string()));
        context.set("y".to_string(), Value::String("bar".to_string()));
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "foo and bar");
    }

    #[test]
    fn test_with_tag_scoping() {
        // Test that variables inside with don't affect outer context
        let tokens =
            tokenize("{{ name }}{% with name=other %}{{ name }}{% endwith %}{{ name }}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("name".to_string(), Value::String("outer".to_string()));
        context.set("other".to_string(), Value::String("inner".to_string()));
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "outerinnerouter");
    }

    #[test]
    fn test_if_and_operator() {
        let tokens = tokenize("{% if a and b %}yes{% endif %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("a".to_string(), Value::Bool(true));
        context.set("b".to_string(), Value::Bool(true));
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "yes");

        context.set("b".to_string(), Value::Bool(false));
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "");
    }

    #[test]
    fn test_if_or_operator() {
        let tokens = tokenize("{% if a or b %}yes{% endif %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("a".to_string(), Value::Bool(false));
        context.set("b".to_string(), Value::Bool(true));
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "yes");

        context.set("b".to_string(), Value::Bool(false));
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "");
    }

    #[test]
    fn test_if_not_and_not() {
        let tokens = tokenize("{% if not a and not b %}empty{% endif %}").unwrap();
        let nodes = parse(&tokens).unwrap();

        // Both falsy -> should show
        let mut context = Context::new();
        context.set("a".to_string(), Value::List(vec![]));
        context.set("b".to_string(), Value::String(String::new()));
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "empty");

        // a truthy -> should not show
        context.set("a".to_string(), Value::List(vec![Value::Integer(1)]));
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "");
    }

    #[test]
    fn test_if_mixed_and_or_precedence() {
        // "and" binds tighter than "or": a or b and c == a or (b and c)
        let tokens = tokenize("{% if a or b and c %}yes{% endif %}").unwrap();
        let nodes = parse(&tokens).unwrap();

        // a=false, b=true, c=false -> false or (true and false) -> false
        let mut context = Context::new();
        context.set("a".to_string(), Value::Bool(false));
        context.set("b".to_string(), Value::Bool(true));
        context.set("c".to_string(), Value::Bool(false));
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "");

        // a=true, b=false, c=false -> true or (false and false) -> true
        context.set("a".to_string(), Value::Bool(true));
        context.set("b".to_string(), Value::Bool(false));
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "yes");
    }

    #[test]
    fn test_if_chained_and() {
        let tokens = tokenize("{% if a and b and c %}yes{% endif %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("a".to_string(), Value::Bool(true));
        context.set("b".to_string(), Value::Bool(true));
        context.set("c".to_string(), Value::Bool(true));
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "yes");

        context.set("b".to_string(), Value::Bool(false));
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "");
    }

    #[test]
    fn test_if_not_with_or() {
        // not a or b == (not a) or b
        let tokens = tokenize("{% if not a or b %}yes{% endif %}").unwrap();
        let nodes = parse(&tokens).unwrap();

        // a=true, b=false -> (not true) or false -> false
        let mut context = Context::new();
        context.set("a".to_string(), Value::Bool(true));
        context.set("b".to_string(), Value::Bool(false));
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "");

        // a=true, b=true -> (not true) or true -> true
        context.set("b".to_string(), Value::Bool(true));
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "yes");

        // a=false, b=false -> (not false) or false -> true
        context.set("a".to_string(), Value::Bool(false));
        context.set("b".to_string(), Value::Bool(false));
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "yes");
    }

    #[test]
    fn test_if_in_list() {
        let tokens = tokenize("{% if item in items %}found{% endif %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("item".to_string(), Value::String("b".to_string()));
        context.set(
            "items".to_string(),
            Value::List(vec![
                Value::String("a".to_string()),
                Value::String("b".to_string()),
                Value::String("c".to_string()),
            ]),
        );
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "found");

        context.set("item".to_string(), Value::String("z".to_string()));
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "");
    }

    #[test]
    fn test_if_in_string() {
        let tokens = tokenize("{% if sub in text %}found{% endif %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("sub".to_string(), Value::String("world".to_string()));
        context.set("text".to_string(), Value::String("hello world".to_string()));
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "found");

        context.set("sub".to_string(), Value::String("xyz".to_string()));
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "");
    }
}
