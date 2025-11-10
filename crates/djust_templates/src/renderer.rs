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

        Node::If { condition, true_nodes, false_nodes } => {
            let condition_result = evaluate_condition(condition, context)?;

            if condition_result {
                render_nodes(true_nodes, context)
            } else {
                render_nodes(false_nodes, context)
            }
        }

        Node::For { var_name, iterable, nodes } => {
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

        Node::ReactComponent { name, props, children } => {
            // Render React component as data attributes for client-side hydration
            let mut output = String::new();
            output.push_str(&format!("<div data-react-component=\"{}\"", name));

            // Add props as data attributes
            if !props.is_empty() {
                output.push_str(" data-react-props='");
                let props_json: Vec<String> = props.iter()
                    .map(|(k, v)| {
                        // Resolve Django template variable syntax: {{ var.path }}
                        let resolved_value = if v.starts_with("{{") && v.ends_with("}}") {
                            // Extract variable name from {{ ... }}
                            let var_name = v.trim_start_matches("{{")
                                .trim_end_matches("}}")
                                .trim();

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
    let framework = context.get("_framework")
        .and_then(|v| {
            if let Value::String(s) = v {
                Some(s.as_str())
            } else {
                None
            }
        })
        .unwrap_or("bootstrap5");

    let fw = djust_components::Framework::from_str(framework);

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
                DjangoRustError::TemplateError(format!("Failed to render RustButton: {}", e))
            })
        }

        _ => {
            Err(DjangoRustError::TemplateError(
                format!("Unknown Rust component: {}", name)
            ))
        }
    }
}

/// Get a prop value, resolving template variables if needed
fn get_prop(
    key: &str,
    props: &[(String, String)],
    context: &Context,
) -> Result<String> {
    for (k, v) in props {
        if k == key {
            // Resolve Django template variable syntax: {{ var.path }}
            if v.starts_with("{{") && v.ends_with("}}") {
                let var_name = v.trim_start_matches("{{")
                    .trim_end_matches("}}")
                    .trim();

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

    Err(DjangoRustError::TemplateError(
        format!("Missing required prop: {}", key)
    ))
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
    if (expr.starts_with('"') && expr.ends_with('"')) ||
       (expr.starts_with('\'') && expr.ends_with('\'')) {
        return Ok(Value::String(expr[1..expr.len()-1].to_string()));
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
        context.set("items".to_string(), Value::List(vec![
            Value::String("a".to_string()),
            Value::String("b".to_string()),
            Value::String("c".to_string()),
        ]));
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "abc");
    }
}
