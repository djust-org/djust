//! Django-compatible template filters

use django_rust_core::{DjangoRustError, Result, Value};

pub fn apply_filter(filter_name: &str, value: &Value) -> Result<Value> {
    match filter_name {
        "upper" => Ok(Value::String(value.to_string().to_uppercase())),
        "lower" => Ok(Value::String(value.to_string().to_lowercase())),
        "title" => Ok(Value::String(titlecase(&value.to_string()))),
        "length" => {
            let len = match value {
                Value::String(s) => s.len(),
                Value::List(l) => l.len(),
                _ => 0,
            };
            Ok(Value::Integer(len as i64))
        }
        "default" => {
            // default filter needs an argument, for now just return empty string
            if value.is_truthy() {
                Ok(value.clone())
            } else {
                Ok(Value::String(String::new()))
            }
        }
        "escape" => Ok(Value::String(html_escape(&value.to_string()))),
        "safe" => Ok(value.clone()), // Mark as safe (no escaping)
        "first" => {
            match value {
                Value::List(l) => Ok(l.first().cloned().unwrap_or(Value::Null)),
                Value::String(s) => Ok(Value::String(
                    s.chars().next().map(|c| c.to_string()).unwrap_or_default()
                )),
                _ => Ok(Value::Null),
            }
        }
        "last" => {
            match value {
                Value::List(l) => Ok(l.last().cloned().unwrap_or(Value::Null)),
                Value::String(s) => Ok(Value::String(
                    s.chars().last().map(|c| c.to_string()).unwrap_or_default()
                )),
                _ => Ok(Value::Null),
            }
        }
        "join" => {
            // join needs a separator argument, default to comma
            match value {
                Value::List(items) => {
                    let strings: Vec<String> = items.iter().map(|v| v.to_string()).collect();
                    Ok(Value::String(strings.join(", ")))
                }
                _ => Ok(value.clone()),
            }
        }
        _ => Err(DjangoRustError::TemplateError(
            format!("Unknown filter: {}", filter_name)
        )),
    }
}

fn titlecase(s: &str) -> String {
    s.split_whitespace()
        .map(|word| {
            let mut chars = word.chars();
            match chars.next() {
                None => String::new(),
                Some(first) => {
                    first.to_uppercase().collect::<String>() + &chars.as_str().to_lowercase()
                }
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

fn html_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#x27;")
}

pub mod tags {
    // Placeholder for custom tags
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_upper_filter() {
        let value = Value::String("hello".to_string());
        let result = apply_filter("upper", &value).unwrap();
        assert_eq!(result.to_string(), "HELLO");
    }

    #[test]
    fn test_length_filter() {
        let value = Value::List(vec![Value::Integer(1), Value::Integer(2)]);
        let result = apply_filter("length", &value).unwrap();
        assert!(matches!(result, Value::Integer(2)));
    }

    #[test]
    fn test_escape_filter() {
        let value = Value::String("<script>alert('xss')</script>".to_string());
        let result = apply_filter("escape", &value).unwrap();
        assert!(result.to_string().contains("&lt;script&gt;"));
    }
}
