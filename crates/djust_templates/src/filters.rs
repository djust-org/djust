//! Django-compatible template filters

use chrono::{DateTime, Datelike, Timelike, Utc};
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
        "add" => {
            // add filter: adds argument to value (for numbers)
            let arg_val = arg.and_then(|s| s.parse::<i64>().ok()).unwrap_or(0);
            match value {
                Value::Integer(n) => Ok(Value::Integer(n + arg_val)),
                Value::Float(f) => Ok(Value::Float(f + arg_val as f64)),
                _ => Ok(value.clone()),
            }
        }
        "pluralize" => {
            // pluralize filter: returns plural suffix if value != 1
            let suffix = arg.unwrap_or("s");
            match value {
                Value::Integer(n) => {
                    if *n == 1 {
                        Ok(Value::String(String::new()))
                    } else {
                        Ok(Value::String(suffix.to_string()))
                    }
                }
                Value::List(l) => {
                    if l.len() == 1 {
                        Ok(Value::String(String::new()))
                    } else {
                        Ok(Value::String(suffix.to_string()))
                    }
                }
                _ => Ok(Value::String(suffix.to_string())),
            }
        }
        "slugify" => {
            // slugify filter: converts to URL-friendly slug
            Ok(Value::String(slugify(&value.to_string())))
        }
        "capfirst" => {
            // capfirst filter: capitalizes first character
            let s = value.to_string();
            let mut chars = s.chars();
            match chars.next() {
                None => Ok(Value::String(String::new())),
                Some(first) => Ok(Value::String(
                    first.to_uppercase().collect::<String>() + chars.as_str(),
                )),
            }
        }
        "yesno" => {
            // yesno filter: maps true/false/none to custom strings
            // Argument format: "yes,no,maybe" (maybe is optional)
            let parts: Vec<&str> = arg.unwrap_or("yes,no,maybe").split(',').collect();
            let yes_str = parts.first().unwrap_or(&"yes");
            let no_str = parts.get(1).unwrap_or(&"no");
            let maybe_str = parts.get(2).unwrap_or(&"maybe");

            let result = match value {
                Value::Bool(true) => yes_str,
                Value::Bool(false) => no_str,
                Value::Null => maybe_str,
                Value::String(s) if s.is_empty() => maybe_str,
                _ => {
                    if value.is_truthy() {
                        yes_str
                    } else {
                        maybe_str
                    }
                }
            };
            Ok(Value::String(result.to_string()))
        }
        "linebreaks" => {
            // linebreaks filter: converts newlines to <p> and <br> tags
            Ok(Value::String(linebreaks(&value.to_string())))
        }
        "linebreaksbr" => {
            // linebreaksbr filter: converts newlines to <br> tags
            Ok(Value::String(linebreaksbr(&value.to_string())))
        }
        "cut" => {
            // cut filter: removes all occurrences of arg from string
            let remove_str = arg.unwrap_or("");
            Ok(Value::String(value.to_string().replace(remove_str, "")))
        }
        "divisibleby" => {
            // divisibleby filter: returns true if value is divisible by arg
            let divisor = arg.and_then(|s| s.parse::<i64>().ok()).unwrap_or(1);
            match value {
                Value::Integer(n) => Ok(Value::Bool(divisor != 0 && n % divisor == 0)),
                _ => Ok(Value::Bool(false)),
            }
        }
        "floatformat" => {
            // floatformat filter: formats float to specified decimal places
            let decimals = arg.and_then(|s| s.parse::<usize>().ok()).unwrap_or(1);
            match value {
                Value::Float(f) => Ok(Value::String(format!("{f:.decimals$}"))),
                Value::Integer(n) => Ok(Value::String(format!(
                    "{:.prec$}",
                    *n as f64,
                    prec = decimals
                ))),
                _ => Ok(value.clone()),
            }
        }
        "filesizeformat" => {
            // filesizeformat filter: formats bytes to human-readable size
            match value {
                Value::Integer(n) => Ok(Value::String(format_filesize(*n))),
                Value::Float(f) => Ok(Value::String(format_filesize(*f as i64))),
                _ => Ok(value.clone()),
            }
        }
        "random" => {
            // random filter: returns random item from list
            match value {
                Value::List(items) if !items.is_empty() => {
                    // Use simple pseudo-random selection based on list length
                    // For deterministic testing, we'll use first item
                    // In production, you'd want to use rand crate
                    use std::collections::hash_map::DefaultHasher;
                    use std::hash::{Hash, Hasher};
                    use std::time::{SystemTime, UNIX_EPOCH};

                    let mut hasher = DefaultHasher::new();
                    SystemTime::now()
                        .duration_since(UNIX_EPOCH)
                        .unwrap()
                        .as_nanos()
                        .hash(&mut hasher);
                    let random_index = (hasher.finish() as usize) % items.len();
                    Ok(items[random_index].clone())
                }
                Value::List(_) => Ok(Value::Null),
                _ => Ok(value.clone()),
            }
        }
        "timeuntil" => {
            // timeuntil filter: converts ISO datetime to "in X minutes/hours/days" format
            let datetime_str = value.to_string();
            match format_timeuntil(&datetime_str) {
                Ok(formatted) => Ok(Value::String(formatted)),
                Err(_) => Ok(value.clone()), // If parsing fails, return original value
            }
        }
        "date" => {
            // date filter: formats datetime with format string
            // Supports common Django/strftime format codes
            let format_str = arg.unwrap_or("N j, Y"); // Default: "Nov. 13, 2025"
            let datetime_str = value.to_string();
            match format_date(&datetime_str, format_str) {
                Ok(formatted) => Ok(Value::String(formatted)),
                Err(_) => Ok(value.clone()), // If parsing fails, return original value
            }
        }
        "time" => {
            // time filter: formats time with format string
            let format_str = arg.unwrap_or("P"); // Default: "2:30 p.m."
            let datetime_str = value.to_string();
            match format_time(&datetime_str, format_str) {
                Ok(formatted) => Ok(Value::String(formatted)),
                Err(_) => Ok(value.clone()),
            }
        }
        "dictsort" => {
            // dictsort filter: sorts list of dicts by key
            let sort_key = arg.unwrap_or("name");
            match value {
                Value::List(items) => Ok(Value::List(sort_dicts_by_key(items, sort_key))),
                _ => Ok(value.clone()),
            }
        }
        "dictsortreversed" => {
            // dictsortreversed filter: sorts list of dicts by key in reverse
            let sort_key = arg.unwrap_or("name");
            match value {
                Value::List(items) => {
                    let mut sorted = sort_dicts_by_key(items, sort_key);
                    sorted.reverse();
                    Ok(Value::List(sorted))
                }
                _ => Ok(value.clone()),
            }
        }
        "urlencode" => {
            // urlencode filter: URL-encodes the string
            // Matches Django behavior: spaces become %20, safe chars are preserved
            Ok(Value::String(urlencode(&value.to_string())))
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

fn format_timeuntil(datetime_str: &str) -> Result<String> {
    // Parse ISO datetime string
    let dt = DateTime::parse_from_rfc3339(datetime_str)
        .map_err(|e| DjangoRustError::TemplateError(format!("Invalid datetime format: {e}")))?;

    let now = Utc::now();
    let duration = dt.with_timezone(&Utc).signed_duration_since(now);

    let seconds = duration.num_seconds();

    // If datetime is in the past, return empty string like Django
    if seconds < 0 {
        return Ok("0 minutes".to_string());
    }

    // Format like Django's timeuntil
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

fn format_filesize(bytes: i64) -> String {
    const KB: i64 = 1024;
    const MB: i64 = KB * 1024;
    const GB: i64 = MB * 1024;
    const TB: i64 = GB * 1024;
    const PB: i64 = TB * 1024;

    if bytes < KB {
        format!("{bytes} bytes")
    } else if bytes < MB {
        format!("{:.1} KB", bytes as f64 / KB as f64)
    } else if bytes < GB {
        format!("{:.1} MB", bytes as f64 / MB as f64)
    } else if bytes < TB {
        format!("{:.1} GB", bytes as f64 / GB as f64)
    } else if bytes < PB {
        format!("{:.1} TB", bytes as f64 / TB as f64)
    } else {
        format!("{:.1} PB", bytes as f64 / PB as f64)
    }
}

fn format_date(datetime_str: &str, format_str: &str) -> Result<String> {
    // Parse ISO datetime string
    let dt = DateTime::parse_from_rfc3339(datetime_str)
        .map_err(|e| DjangoRustError::TemplateError(format!("Invalid datetime format: {e}")))?;

    // Convert common Django format codes to output
    // This is a simplified implementation - Django has many more format codes
    let mut result = String::new();
    let mut chars = format_str.chars().peekable();

    while let Some(ch) = chars.next() {
        match ch {
            // Common date format codes
            'Y' => result.push_str(&dt.year().to_string()), // 2025
            'y' => result.push_str(&format!("{:02}", dt.year() % 100)), // 25
            'm' => result.push_str(&format!("{:02}", dt.month())), // 01-12
            'n' => result.push_str(&dt.month().to_string()), // 1-12
            'd' => result.push_str(&format!("{:02}", dt.day())), // 01-31
            'j' => result.push_str(&dt.day().to_string()),  // 1-31
            'D' => result.push_str(&dt.format("%a").to_string()), // Mon
            'l' => result.push_str(&dt.format("%A").to_string()), // Monday
            'F' => result.push_str(&dt.format("%B").to_string()), // January
            'M' => result.push_str(&dt.format("%b").to_string()), // Jan
            'N' => {
                // Django: "Jan.", "Feb.", etc.
                let month = dt.format("%b").to_string();
                result.push_str(&format!("{month}."));
            }
            // Time format codes
            'G' => result.push_str(&dt.hour().to_string()), // 0-23 (24-hour, no leading zero)
            'H' => result.push_str(&format!("{:02}", dt.hour())), // 00-23
            'g' => {
                // 1-12 (12-hour, no leading zero)
                let hour = dt.hour();
                let display_hour = if hour == 0 {
                    12
                } else if hour > 12 {
                    hour - 12
                } else {
                    hour
                };
                result.push_str(&display_hour.to_string());
            }
            'h' => {
                // 01-12 (12-hour, with leading zero)
                let hour = dt.hour();
                let display_hour = if hour == 0 {
                    12
                } else if hour > 12 {
                    hour - 12
                } else {
                    hour
                };
                result.push_str(&format!("{:02}", display_hour));
            }
            'i' => result.push_str(&format!("{:02}", dt.minute())), // 00-59
            's' => result.push_str(&format!("{:02}", dt.second())), // 00-59
            'A' => {
                // AM/PM
                if dt.hour() < 12 {
                    result.push_str("AM");
                } else {
                    result.push_str("PM");
                }
            }
            'a' => {
                // am/pm
                if dt.hour() < 12 {
                    result.push_str("am");
                } else {
                    result.push_str("pm");
                }
            }
            'P' => {
                // Django: "2:30 p.m.", "midnight", "noon"
                let hour = dt.hour();
                let minute = dt.minute();
                if hour == 0 && minute == 0 {
                    result.push_str("midnight");
                } else if hour == 12 && minute == 0 {
                    result.push_str("noon");
                } else {
                    let display_hour = if hour == 0 {
                        12
                    } else if hour > 12 {
                        hour - 12
                    } else {
                        hour
                    };
                    let ampm = if hour < 12 { "a.m." } else { "p.m." };
                    if minute == 0 {
                        result.push_str(&format!("{display_hour} {ampm}"));
                    } else {
                        result.push_str(&format!("{display_hour}:{minute:02} {ampm}"));
                    }
                }
            }
            // Literal characters
            '\\' => {
                // Escape next character
                if let Some(next) = chars.next() {
                    result.push(next);
                }
            }
            _ => result.push(ch),
        }
    }

    Ok(result)
}

fn format_time(datetime_str: &str, format_str: &str) -> Result<String> {
    // Reuse format_date but focused on time formatting
    format_date(datetime_str, format_str)
}

fn sort_dicts_by_key(items: &[Value], sort_key: &str) -> Vec<Value> {
    let mut sorted_items = items.to_vec();

    sorted_items.sort_by(|a, b| {
        let a_val = get_dict_value(a, sort_key);
        let b_val = get_dict_value(b, sort_key);

        // Compare values
        match (&a_val, &b_val) {
            (Value::String(a_str), Value::String(b_str)) => a_str.cmp(b_str),
            (Value::Integer(a_int), Value::Integer(b_int)) => a_int.cmp(b_int),
            (Value::Float(a_float), Value::Float(b_float)) => a_float
                .partial_cmp(b_float)
                .unwrap_or(std::cmp::Ordering::Equal),
            (Value::Bool(a_bool), Value::Bool(b_bool)) => a_bool.cmp(b_bool),
            _ => std::cmp::Ordering::Equal,
        }
    });

    sorted_items
}

fn get_dict_value(value: &Value, key: &str) -> Value {
    match value {
        Value::Object(map) => map.get(key).cloned().unwrap_or(Value::Null),
        _ => Value::Null,
    }
}

fn slugify(s: &str) -> String {
    // Convert to lowercase and replace non-alphanumeric characters with hyphens
    s.to_lowercase()
        .chars()
        .map(|c| if c.is_alphanumeric() { c } else { '-' })
        .collect::<String>()
        // Remove consecutive hyphens
        .split('-')
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join("-")
}

fn linebreaks(s: &str) -> String {
    // Convert double newlines to </p><p> and single newlines to <br>
    // Similar to Django's linebreaks filter
    let paragraphs: Vec<&str> = s.split("\n\n").collect();

    let formatted_paragraphs: Vec<String> = paragraphs
        .iter()
        .filter(|p| !p.trim().is_empty())
        .map(|p| {
            let lines_with_br = p.split('\n').collect::<Vec<_>>().join("<br>");
            format!("<p>{lines_with_br}</p>")
        })
        .collect();

    formatted_paragraphs.join("\n")
}

fn linebreaksbr(s: &str) -> String {
    // Simply replace newlines with <br> tags
    s.replace('\n', "<br>")
}

fn urlencode(s: &str) -> String {
    // URL-encode a string, matching Django's urlencode behavior
    // Safe characters (not encoded): alphanumeric, -, _, ., ~
    // Everything else is percent-encoded
    // Spaces become %20 (not +)
    let mut result = String::with_capacity(s.len() * 3); // Worst case: every char becomes %XX

    for c in s.chars() {
        if c.is_ascii_alphanumeric() || c == '-' || c == '_' || c == '.' || c == '~' {
            result.push(c);
        } else {
            // Percent-encode the character
            // For multi-byte UTF-8 characters, encode each byte separately
            let mut buf = [0u8; 4];
            let encoded = c.encode_utf8(&mut buf);
            for byte in encoded.bytes() {
                result.push_str(&format!("%{:02X}", byte));
            }
        }
    }

    result
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

    #[test]
    fn test_add_filter() {
        let value = Value::Integer(5);
        let result = apply_filter("add", &value, Some("3")).unwrap();
        assert!(matches!(result, Value::Integer(8)));
    }

    #[test]
    fn test_pluralize_filter() {
        let value = Value::Integer(1);
        let result = apply_filter("pluralize", &value, None).unwrap();
        assert_eq!(result.to_string(), "");

        let value = Value::Integer(2);
        let result = apply_filter("pluralize", &value, None).unwrap();
        assert_eq!(result.to_string(), "s");

        let value = Value::Integer(0);
        let result = apply_filter("pluralize", &value, Some("es")).unwrap();
        assert_eq!(result.to_string(), "es");
    }

    #[test]
    fn test_slugify_filter() {
        let value = Value::String("Hello World Test!".to_string());
        let result = apply_filter("slugify", &value, None).unwrap();
        assert_eq!(result.to_string(), "hello-world-test");
    }

    #[test]
    fn test_capfirst_filter() {
        let value = Value::String("hello world".to_string());
        let result = apply_filter("capfirst", &value, None).unwrap();
        assert_eq!(result.to_string(), "Hello world");
    }

    #[test]
    fn test_yesno_filter() {
        let value = Value::Bool(true);
        let result = apply_filter("yesno", &value, Some("yeah,nope,dunno")).unwrap();
        assert_eq!(result.to_string(), "yeah");

        let value = Value::Bool(false);
        let result = apply_filter("yesno", &value, Some("yeah,nope,dunno")).unwrap();
        assert_eq!(result.to_string(), "nope");

        let value = Value::Null;
        let result = apply_filter("yesno", &value, Some("yeah,nope,dunno")).unwrap();
        assert_eq!(result.to_string(), "dunno");
    }

    #[test]
    fn test_linebreaks_filter() {
        let value = Value::String("Line 1\nLine 2\n\nParagraph 2".to_string());
        let result = apply_filter("linebreaks", &value, None).unwrap();
        assert!(result.to_string().contains("<p>"));
        assert!(result.to_string().contains("<br>"));
    }

    #[test]
    fn test_linebreaksbr_filter() {
        let value = Value::String("Line 1\nLine 2\nLine 3".to_string());
        let result = apply_filter("linebreaksbr", &value, None).unwrap();
        assert_eq!(result.to_string(), "Line 1<br>Line 2<br>Line 3");
    }

    #[test]
    fn test_cut_filter() {
        let value = Value::String("hello world".to_string());
        let result = apply_filter("cut", &value, Some(" ")).unwrap();
        assert_eq!(result.to_string(), "helloworld");
    }

    #[test]
    fn test_divisibleby_filter() {
        let value = Value::Integer(10);
        let result = apply_filter("divisibleby", &value, Some("2")).unwrap();
        assert!(matches!(result, Value::Bool(true)));

        let value = Value::Integer(10);
        let result = apply_filter("divisibleby", &value, Some("3")).unwrap();
        assert!(matches!(result, Value::Bool(false)));
    }

    #[test]
    fn test_floatformat_filter() {
        let value = Value::Float(std::f64::consts::PI);
        let result = apply_filter("floatformat", &value, Some("2")).unwrap();
        assert_eq!(result.to_string(), "3.14");

        let value = Value::Integer(42);
        let result = apply_filter("floatformat", &value, Some("2")).unwrap();
        assert_eq!(result.to_string(), "42.00");
    }

    #[test]
    fn test_filesizeformat_filter() {
        let value = Value::Integer(1024);
        let result = apply_filter("filesizeformat", &value, None).unwrap();
        assert_eq!(result.to_string(), "1.0 KB");

        let value = Value::Integer(1048576);
        let result = apply_filter("filesizeformat", &value, None).unwrap();
        assert_eq!(result.to_string(), "1.0 MB");

        let value = Value::Integer(500);
        let result = apply_filter("filesizeformat", &value, None).unwrap();
        assert_eq!(result.to_string(), "500 bytes");
    }

    #[test]
    fn test_random_filter() {
        let value = Value::List(vec![
            Value::String("a".to_string()),
            Value::String("b".to_string()),
            Value::String("c".to_string()),
        ]);
        let result = apply_filter("random", &value, None).unwrap();
        // Result should be one of the list items
        match result {
            Value::String(s) => assert!(s == "a" || s == "b" || s == "c"),
            _ => panic!("Expected string value"),
        }

        // Empty list should return Null
        let empty = Value::List(vec![]);
        let result = apply_filter("random", &empty, None).unwrap();
        assert!(matches!(result, Value::Null));
    }

    #[test]
    fn test_timeuntil_filter() {
        // Create a future datetime (1 day from now)
        use chrono::Duration;
        let future = Utc::now() + Duration::days(1);
        let future_str = future.to_rfc3339();
        let value = Value::String(future_str);
        let result = apply_filter("timeuntil", &value, None).unwrap();
        // Should contain "day" or "hour" (depending on exact timing)
        let result_str = result.to_string();
        assert!(
            result_str.contains("day") || result_str.contains("hour"),
            "Expected 'day' or 'hour' in result: {result_str}"
        );
    }

    #[test]
    fn test_date_filter() {
        use chrono::TimeZone;
        // Create a specific datetime for testing
        let dt = Utc.with_ymd_and_hms(2025, 11, 13, 14, 30, 0).unwrap();
        let dt_str = dt.to_rfc3339();
        let value = Value::String(dt_str);

        // Test Y-m-d format
        let result = apply_filter("date", &value, Some("Y-m-d")).unwrap();
        assert_eq!(result.to_string(), "2025-11-13");

        // Test Django default format
        let result = apply_filter("date", &value, Some("N j, Y")).unwrap();
        assert_eq!(result.to_string(), "Nov. 13, 2025");

        // Test with time
        let result = apply_filter("date", &value, Some("Y-m-d H:i")).unwrap();
        assert_eq!(result.to_string(), "2025-11-13 14:30");

        // Test 12-hour format codes (g, h) - afternoon time (14:30 = 2:30 PM)
        let result = apply_filter("date", &value, Some("g:i A")).unwrap();
        assert_eq!(result.to_string(), "2:30 PM");

        let result = apply_filter("date", &value, Some("h:i A")).unwrap();
        assert_eq!(result.to_string(), "02:30 PM");

        // Test 24-hour without leading zero (G)
        let result = apply_filter("date", &value, Some("G:i")).unwrap();
        assert_eq!(result.to_string(), "14:30");

        // Test morning time for 12-hour formats
        let morning = Utc.with_ymd_and_hms(2025, 11, 13, 9, 5, 0).unwrap();
        let morning_str = morning.to_rfc3339();
        let morning_value = Value::String(morning_str);

        let result = apply_filter("date", &morning_value, Some("g:i A")).unwrap();
        assert_eq!(result.to_string(), "9:05 AM");

        let result = apply_filter("date", &morning_value, Some("h:i A")).unwrap();
        assert_eq!(result.to_string(), "09:05 AM");

        // Test midnight (00:00 should be 12:xx AM)
        let midnight = Utc.with_ymd_and_hms(2025, 11, 13, 0, 30, 0).unwrap();
        let midnight_str = midnight.to_rfc3339();
        let midnight_value = Value::String(midnight_str);

        let result = apply_filter("date", &midnight_value, Some("g:i A")).unwrap();
        assert_eq!(result.to_string(), "12:30 AM");

        // Test noon (12:00 should be 12:xx PM)
        let noon = Utc.with_ymd_and_hms(2025, 11, 13, 12, 30, 0).unwrap();
        let noon_str = noon.to_rfc3339();
        let noon_value = Value::String(noon_str);

        let result = apply_filter("date", &noon_value, Some("g:i A")).unwrap();
        assert_eq!(result.to_string(), "12:30 PM");
    }

    #[test]
    fn test_time_filter() {
        use chrono::TimeZone;
        // Test afternoon time
        let dt = Utc.with_ymd_and_hms(2025, 11, 13, 14, 30, 0).unwrap();
        let dt_str = dt.to_rfc3339();
        let value = Value::String(dt_str);

        let result = apply_filter("time", &value, Some("H:i")).unwrap();
        assert_eq!(result.to_string(), "14:30");

        // Test P format (Django time format)
        let result = apply_filter("time", &value, Some("P")).unwrap();
        assert_eq!(result.to_string(), "2:30 p.m.");

        // Test midnight
        let midnight = Utc.with_ymd_and_hms(2025, 11, 13, 0, 0, 0).unwrap();
        let midnight_str = midnight.to_rfc3339();
        let value = Value::String(midnight_str);
        let result = apply_filter("time", &value, Some("P")).unwrap();
        assert_eq!(result.to_string(), "midnight");

        // Test noon
        let noon = Utc.with_ymd_and_hms(2025, 11, 13, 12, 0, 0).unwrap();
        let noon_str = noon.to_rfc3339();
        let value = Value::String(noon_str);
        let result = apply_filter("time", &value, Some("P")).unwrap();
        assert_eq!(result.to_string(), "noon");
    }

    #[test]
    fn test_dictsort_filter() {
        use std::collections::HashMap;

        // Create list of dicts
        let mut dict1 = HashMap::new();
        dict1.insert("name".to_string(), Value::String("Charlie".to_string()));
        dict1.insert("age".to_string(), Value::Integer(30));

        let mut dict2 = HashMap::new();
        dict2.insert("name".to_string(), Value::String("Alice".to_string()));
        dict2.insert("age".to_string(), Value::Integer(25));

        let mut dict3 = HashMap::new();
        dict3.insert("name".to_string(), Value::String("Bob".to_string()));
        dict3.insert("age".to_string(), Value::Integer(35));

        let value = Value::List(vec![
            Value::Object(dict1),
            Value::Object(dict2),
            Value::Object(dict3),
        ]);

        // Sort by name
        let result = apply_filter("dictsort", &value, Some("name")).unwrap();
        if let Value::List(sorted) = result {
            assert_eq!(sorted.len(), 3);
            // First should be Alice
            if let Value::Object(first) = &sorted[0] {
                assert_eq!(first.get("name").unwrap().to_string(), "Alice");
            }
        } else {
            panic!("Expected List value");
        }
    }

    #[test]
    fn test_dictsortreversed_filter() {
        use std::collections::HashMap;

        let mut dict1 = HashMap::new();
        dict1.insert("name".to_string(), Value::String("Alice".to_string()));

        let mut dict2 = HashMap::new();
        dict2.insert("name".to_string(), Value::String("Bob".to_string()));

        let value = Value::List(vec![Value::Object(dict1), Value::Object(dict2)]);

        let result = apply_filter("dictsortreversed", &value, Some("name")).unwrap();
        if let Value::List(sorted) = result {
            // First should be Bob (reversed)
            if let Value::Object(first) = &sorted[0] {
                assert_eq!(first.get("name").unwrap().to_string(), "Bob");
            }
        }
    }

    #[test]
    fn test_urlencode_filter() {
        // Basic text with spaces
        let value = Value::String("Hello World".to_string());
        let result = apply_filter("urlencode", &value, None).unwrap();
        assert_eq!(result.to_string(), "Hello%20World");

        // Text with special characters
        let value = Value::String("Hello World & Friends".to_string());
        let result = apply_filter("urlencode", &value, None).unwrap();
        assert_eq!(result.to_string(), "Hello%20World%20%26%20Friends");

        // Text with query string characters
        let value = Value::String("foo=bar&baz=qux".to_string());
        let result = apply_filter("urlencode", &value, None).unwrap();
        assert_eq!(result.to_string(), "foo%3Dbar%26baz%3Dqux");

        // Safe characters should NOT be encoded
        let value = Value::String("hello-world_test.file~name".to_string());
        let result = apply_filter("urlencode", &value, None).unwrap();
        assert_eq!(result.to_string(), "hello-world_test.file~name");

        // Empty string
        let value = Value::String("".to_string());
        let result = apply_filter("urlencode", &value, None).unwrap();
        assert_eq!(result.to_string(), "");

        // Question mark and slash should be encoded
        let value = Value::String("path/to/file?query=1".to_string());
        let result = apply_filter("urlencode", &value, None).unwrap();
        assert_eq!(result.to_string(), "path%2Fto%2Ffile%3Fquery%3D1");
    }
}
