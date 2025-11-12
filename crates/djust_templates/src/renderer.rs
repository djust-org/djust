//! Template renderer that converts AST nodes to output strings

use crate::filters;
use crate::parser::Node;
use djust_components::Component;
use djust_core::{Context, DjangoRustError, Result, Value};

pub fn render_nodes(nodes: &[Node], context: &Context) -> Result<String> {
    let mut output = String::new();

    for node in nodes {
        output.push_str(&render_node(node, context)?);
    }

    Ok(output)
}

fn render_node(node: &Node, context: &Context) -> Result<String> {
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
                render_nodes(true_nodes, context)
            } else {
                render_nodes(false_nodes, context)
            }
        }

        Node::For {
            var_name,
            iterable,
            nodes,
        } => {
            let iterable_value = context.get(iterable).cloned().unwrap_or(Value::Null);

            match iterable_value {
                Value::List(items) => {
                    let mut output = String::new();
                    let mut ctx = context.clone();

                    for item in items {
                        ctx.set(var_name.clone(), item);
                        output.push_str(&render_nodes(nodes, &ctx)?);
                    }

                    Ok(output)
                }
                _ => Ok(String::new()),
            }
        }

        Node::Block { name: _, nodes } => {
            // For now, just render the block content
            // In a full implementation, this would handle template inheritance
            render_nodes(nodes, context)
        }

        Node::Include(_template_name) => {
            // For now, just skip includes
            // In a full implementation, this would load and render the included template
            Ok(String::new())
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
                output.push_str(&render_node(child, context)?);
            }

            output.push_str("</div>");
            Ok(output)
        }

        Node::RustComponent { name, props } => {
            // Render Rust component server-side
            render_rust_component(name, props, context)
        }

        Node::Comment => Ok(String::new()),
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
}
