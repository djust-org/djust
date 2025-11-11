//! Django-compatible template filters

use chrono::{DateTime, Utc};
use djust_core::{DjangoRustError, Result, Value};

pub fn apply_filter(filter_name: &str, value: &Value, arg: Option<&str>) -> Result<Value> {
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
            // default filter with argument
            if value.is_truthy() {
                Ok(value.clone())
            } else {
                Ok(Value::String(arg.unwrap_or("").to_string()))
            }
        }
        "escape" => Ok(Value::String(html_escape(&value.to_string()))),
        "safe" => Ok(value.clone()), // Mark as safe (no escaping)
        "first" => match value {
            Value::List(l) => Ok(l.first().cloned().unwrap_or(Value::Null)),
            Value::String(s) => Ok(Value::String(
                s.chars().next().map(|c| c.to_string()).unwrap_or_default(),
            )),
            _ => Ok(Value::Null),
        },
        "last" => match value {
            Value::List(l) => Ok(l.last().cloned().unwrap_or(Value::Null)),
            Value::String(s) => Ok(Value::String(
                s.chars().last().map(|c| c.to_string()).unwrap_or_default(),
            )),
            _ => Ok(Value::Null),
        },
        "join" => {
            // join with separator argument
            let separator = arg.unwrap_or(", ");
            match value {
                Value::List(items) => {
                    let strings: Vec<String> = items.iter().map(|v| v.to_string()).collect();
                    Ok(Value::String(strings.join(separator)))
                }
                _ => Ok(value.clone()),
            }
        }
        "truncatewords" => {
            let num_words = arg.and_then(|s| s.parse::<usize>().ok()).unwrap_or(10);
            let text = value.to_string();
            Ok(Value::String(truncate_words(&text, num_words)))
        }
        "truncatechars" => {
            let num_chars = arg.and_then(|s| s.parse::<usize>().ok()).unwrap_or(20);
            let text = value.to_string();
            Ok(Value::String(truncate_chars(&text, num_chars)))
        }
        "slice" => {
            // slice filter supports Python slice syntax: ":5", "2:", "2:5"
            let slice_str = arg.unwrap_or(":");
            Ok(apply_slice(value, slice_str)?)
        }
        "timesince" => {
            // timesince filter: converts ISO datetime to "X minutes/hours/days ago" format
            let datetime_str = value.to_string();
            match format_timesince(&datetime_str) {
                Ok(formatted) => Ok(Value::String(formatted)),
                Err(_) => Ok(value.clone()), // If parsing fails, return original value
            }
        }
        _ => Err(DjangoRustError::TemplateError(format!(
            "Unknown filter: {filter_name}"
        ))),
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

fn truncate_words(text: &str, num_words: usize) -> String {
    let words: Vec<&str> = text.split_whitespace().collect();
    if words.len() <= num_words {
        text.to_string()
    } else {
        words[..num_words].join(" ") + "..."
    }
}

fn truncate_chars(text: &str, num_chars: usize) -> String {
    if text.chars().count() <= num_chars {
        text.to_string()
    } else {
        text.chars()
            .take(num_chars.saturating_sub(3))
            .collect::<String>()
            + "..."
    }
}

fn apply_slice(value: &Value, slice_str: &str) -> Result<Value> {
    let parts: Vec<&str> = slice_str.split(':').collect();

    match value {
        Value::String(s) => {
            let chars: Vec<char> = s.chars().collect();
            let len = chars.len() as isize;

            let (start, end) = parse_slice_indices(&parts, len);
            let start = start.max(0) as usize;
            let end = end.min(len).max(0) as usize;

            if start < end && start < chars.len() {
                let sliced: String = chars[start..end.min(chars.len())].iter().collect();
                Ok(Value::String(sliced))
            } else {
                Ok(Value::String(String::new()))
            }
        }
        Value::List(items) => {
            let len = items.len() as isize;
            let (start, end) = parse_slice_indices(&parts, len);
            let start = start.max(0) as usize;
            let end = end.min(len).max(0) as usize;

            if start < end && start < items.len() {
                Ok(Value::List(items[start..end.min(items.len())].to_vec()))
            } else {
                Ok(Value::List(vec![]))
            }
        }
        _ => Ok(value.clone()),
    }
}

fn parse_slice_indices(parts: &[&str], len: isize) -> (isize, isize) {
    let start = if parts.is_empty() || parts[0].is_empty() {
        0
    } else {
        parts[0].parse::<isize>().unwrap_or(0)
    };

    let end = if parts.len() < 2 || parts[1].is_empty() {
        len
    } else {
        parts[1].parse::<isize>().unwrap_or(len)
    };

    (start, end)
}

fn format_timesince(datetime_str: &str) -> Result<String> {
    // Parse ISO datetime string
    let dt = DateTime::parse_from_rfc3339(datetime_str)
        .map_err(|e| DjangoRustError::TemplateError(format!("Invalid datetime format: {e}")))?;

    let now = Utc::now();
    let duration = now.signed_duration_since(dt.with_timezone(&Utc));

    let seconds = duration.num_seconds();

    // Format like Django's timesince
    let formatted = if seconds < 60 {
        format!("{} second{}", seconds, if seconds != 1 { "s" } else { "" })
    } else if seconds < 3600 {
        let minutes = seconds / 60;
        format!("{} minute{}", minutes, if minutes != 1 { "s" } else { "" })
    } else if seconds < 86400 {
        let hours = seconds / 3600;
        format!("{} hour{}", hours, if hours != 1 { "s" } else { "" })
    } else if seconds < 604800 {
        let days = seconds / 86400;
        format!("{} day{}", days, if days != 1 { "s" } else { "" })
    } else if seconds < 2629746 {
        let weeks = seconds / 604800;
        format!("{} week{}", weeks, if weeks != 1 { "s" } else { "" })
    } else if seconds < 31556952 {
        let months = seconds / 2629746;
        format!("{} month{}", months, if months != 1 { "s" } else { "" })
    } else {
        let years = seconds / 31556952;
        format!("{} year{}", years, if years != 1 { "s" } else { "" })
    };

    Ok(formatted)
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
        let result = apply_filter("upper", &value, None).unwrap();
        assert_eq!(result.to_string(), "HELLO");
    }

    #[test]
    fn test_length_filter() {
        let value = Value::List(vec![Value::Integer(1), Value::Integer(2)]);
        let result = apply_filter("length", &value, None).unwrap();
        assert!(matches!(result, Value::Integer(2)));
    }

    #[test]
    fn test_escape_filter() {
        let value = Value::String("<script>alert('xss')</script>".to_string());
        let result = apply_filter("escape", &value, None).unwrap();
        assert!(result.to_string().contains("&lt;script&gt;"));
    }

    #[test]
    fn test_truncatewords_filter() {
        let value = Value::String("This is a long sentence with many words".to_string());
        let result = apply_filter("truncatewords", &value, Some("5")).unwrap();
        assert_eq!(result.to_string(), "This is a long sentence...");
    }

    #[test]
    fn test_truncatechars_filter() {
        let value = Value::String("This is a long string".to_string());
        let result = apply_filter("truncatechars", &value, Some("10")).unwrap();
        assert_eq!(result.to_string(), "This is...");
    }

    #[test]
    fn test_slice_filter() {
        let value = Value::String("hello world".to_string());
        let result = apply_filter("slice", &value, Some(":5")).unwrap();
        assert_eq!(result.to_string(), "hello");
    }
}
