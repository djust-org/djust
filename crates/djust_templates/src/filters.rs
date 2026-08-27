//! Django-compatible template filters

use chrono::{DateTime, Datelike, Timelike, Utc};
use djust_core::{Context, DjangoRustError, Result, Value};
use once_cell::sync::Lazy;
use regex::Regex;

use crate::filter_registry;

pub fn apply_filter(filter_name: &str, value: &Value, arg: Option<&str>) -> Result<Value> {
    apply_filter_with_context(filter_name, value, arg, None)
}

pub fn apply_filter_with_context(
    filter_name: &str,
    value: &Value,
    arg: Option<&str>,
    context: Option<&Context>,
) -> Result<Value> {
    // The classic call site: arg has already been quote-stripped by
    // ``strip_filter_arg_quotes``. We can no longer tell quoted literals
    // from bare identifiers, so default ``arg_was_quoted=true`` for the
    // custom-filter fallback — i.e. assume callers pre-resolved any
    // context-variable args. New call sites (renderer.rs) call
    // ``apply_filter_full`` directly with the original arg + quoting hint.
    apply_filter_full(filter_name, value, arg, context, true)
}

/// Internal entry point used by the renderer when full quoting metadata
/// is available. The ``arg_was_quoted`` flag tells the custom-filter
/// fallback whether to treat the arg as a literal string (quoted) or a
/// context-variable identifier (bare).
pub fn apply_filter_full(
    filter_name: &str,
    value: &Value,
    arg: Option<&str>,
    context: Option<&Context>,
    arg_was_quoted: bool,
) -> Result<Value> {
    apply_filter_full_safe(filter_name, value, arg, context, arg_was_quoted).map(|(v, _)| v)
}

/// Like [`apply_filter_full`] but also reports whether the produced value is a
/// runtime ``SafeString`` (Django ``mark_safe`` / a value with ``__html__``).
///
/// Built-in filters never produce a runtime-safe value — their output is a
/// plain string, and the renderer's name-based ``safe_output_filters`` list
/// governs built-in safe filters like ``safe``/``urlize``. A *custom* filter is
/// runtime-safe iff its Python result has ``__html__``. The renderer threads
/// this out so a value a filter explicitly ``mark_safe()``d at runtime bypasses
/// auto-escaping — matching Django's ``render_value_in_context`` (escape iff the
/// *final* value lacks ``__html__``), even when the filter is not decorated
/// ``is_safe=True`` (#1660). A later plain-returning filter re-taints, because
/// it overwrites this flag with ``false``.
pub fn apply_filter_full_safe(
    filter_name: &str,
    value: &Value,
    arg: Option<&str>,
    context: Option<&Context>,
    arg_was_quoted: bool,
) -> Result<(Value, bool)> {
    // #2202: Django resolves a bare-identifier filter argument as a context
    // variable (`Variable(arg).resolve(context)`); only a QUOTED argument is a
    // literal. The custom-filter path already does this
    // (``filter_registry::apply_custom_filter``, which resolves to a Python
    // object); the built-in dispatch table did not, so `{{ x|default:fallback }}`
    // rendered the literal text "fallback". Same parallel-path-drift class as
    // #1646, on the filter-argument axis.
    //
    // Resolved ONCE here rather than inside each affected arm: TWENTY-SIX
    // built-ins take an argument, and per-arm fixes would be twenty-six more
    // places to drift from — which is the bug this is. The dispatch table stays
    // arg-source-agnostic.
    //
    // ``arg_was_quoted`` gates it, so a quoted literal is never looked up even
    // when a context key of that name exists. ``apply_filter_with_context``
    // passes ``true`` (documented there as "assume callers pre-resolved"), so
    // that classic call site keeps its current behaviour; only the renderer
    // path — which computes the real quoting hint at ``renderer.rs`` — changes.
    //
    // ``Context::resolve`` distinguishes two outcomes, and they are NOT treated
    // alike:
    //
    //   * ``Ok(None)`` — a lookup MISS. Falls back to the raw string via
    //     ``.or(arg)``, matching the custom-filter path and preserving templates
    //     that rely on the accident (`{{ n|pluralize:es }}` renders "es" because
    //     `es` does not resolve). Numeric args (`add:7`) take the same path.
    //     This is a DELIBERATE divergence from Django, which raises
    //     ``VariableDoesNotExist`` here — raising would turn a silent
    //     wrong-output bug into a site-wide 500 on upgrade.
    //
    //   * ``Err`` — an exception raised INSIDE a method auto-called during
    //     resolution (ADR-024). Propagated with ``?``. Django propagates it
    //     (verified: a raising method in a filter arg 500s exactly as in value
    //     position), ``filter_registry.rs`` propagates it on the custom-filter
    //     path, and ``renderer.rs`` propagates it on the main variable path.
    //     Swallowing it here would leave `{{ x|default:obj.raising_method }}`
    //     rendering the literal text "obj.raising_method" into the page — the
    //     exact silent-wrong-output failure this fix exists to remove,
    //     reintroduced on the error branch. Converging the resolver but not its
    //     error policy would still be #1646 drift, just subtler.
    let resolved_arg: Option<String> = match (arg, arg_was_quoted, context) {
        (Some(a), false, Some(ctx)) => ctx.resolve(a)?.map(|v| v.to_string()),
        _ => None,
    };
    let builtin_arg = resolved_arg.as_deref().or(arg);

    // Built-ins take precedence over custom filters (mirrors the original
    // dispatch order). A built-in hit is never runtime-safe.
    // `arg_was_quoted` reaches the dispatch table because `add` needs it: a
    // quoted "1.5" is a STRING to Python's int() (which raises), while an
    // unquoted 1.5 is a float literal (which truncates). See that arm (#2203).
    if let Some(builtin) =
        apply_builtin_filter(filter_name, value, builtin_arg, context, arg_was_quoted)
    {
        return builtin.map(|v| (v, false));
    }
    // Built-in match miss — fall through to the custom filter registry for
    // project-defined ``@register.filter`` callables (#1121).
    // ``apply_custom_filter`` returns ``Some(Ok|Err)`` on hit (the ``Ok`` now
    // carries the result's runtime ``__html__``-ness, #1660), ``None`` on miss.
    //
    // ``autoescape=true`` is supplied here as the engine's current (pinned)
    // policy. When ``{% autoescape %}`` block tracking lands in a future PR,
    // the renderer will thread the surrounding policy through this call site
    // (#1162).
    if let Some(result) =
        filter_registry::apply_custom_filter(filter_name, value, arg, context, arg_was_quoted, true)
    {
        return result.map_err(DjangoRustError::TemplateError);
    }
    Err(DjangoRustError::TemplateError(format!(
        "Unknown filter: {filter_name}"
    )))
}

/// Django's ``@stringfilter``-decorated built-ins that actually stringify.
///
/// ``django.template.defaultfilters.stringfilter`` wraps a filter so it runs on
/// ``str(value)`` rather than on the value. Django applies it to 29 built-ins;
/// all 29 are implemented here and 27 of them are listed below. The list is not
/// a judgement call about which filters "feel string-shaped" — it is a
/// transcript of Django's decorators, and
/// ``python/tests/test_string_filter_stringification_2250.py`` re-derives the
/// set by introspecting the live ``defaultfilters`` registry, so a filter
/// Django adds to (or removes from) the decorator fails that test rather than
/// drifting silently.
///
/// **The two deliberate omissions — ``escape`` and ``safe``.** Django's
/// versions return a string; djust's are no-ops returning the value unchanged,
/// because auto-escaping is decided by filter NAME at the render site. So their
/// `Decimal` divergence has a different mechanism from the other 27: nothing
/// stringifies it, the value simply stays a `Decimal` and the renderer's
/// ``localize_if_number`` fires where Django's had already become a ``str``.
/// Coercing them here is not free — it changes the TYPE flowing down the rest
/// of the chain, and ``floatformat`` cannot parse a numeric string, so
/// ``{{ d|escape|floatformat }}`` regressed in 1,168 measured cells. Teaching
/// ``floatformat`` to parse strings was tried and is worse (it cannot reproduce
/// Django's >200-digit passthrough or its NaN/inf handling from an ``f64``, and
/// broke 538 cells of ``{{ d|upper|floatformat }}``). Both residues are
/// measured and tracked in #2257; neither belongs in this fix.
///
/// Also deliberately NOT here: ``default``/``default_if_none`` (Django returns
/// the value itself, which then localizes at render), and every numeric filter
/// (``add``, ``floatformat``, ``divisibleby``, ``get_digit``, ``length``,
/// ``pluralize``, ``yesno``, ``filesizeformat``, ``stringformat``) — Django
/// does not stringify those, and coercing here would be a different bug.
const STRING_FILTERS: &[&str] = &[
    "addslashes",
    "capfirst",
    "center",
    "cut",
    "escapejs",
    "force_escape",
    "iriencode",
    "linebreaks",
    "linebreaksbr",
    "linenumbers",
    "ljust",
    "lower",
    "make_list",
    "rjust",
    "slugify",
    "striptags",
    "title",
    "truncatechars",
    "truncatechars_html",
    "truncatewords",
    "truncatewords_html",
    "upper",
    "urlencode",
    "urlize",
    "urlizetrunc",
    "wordcount",
    "wordwrap",
];

/// Is this built-in one of Django's ``@stringfilter``s? (#2250)
pub fn is_string_filter(filter_name: &str) -> bool {
    STRING_FILTERS.contains(&filter_name)
}

/// Dispatch table for all built-in filters. Returns ``None`` when
/// ``filter_name`` is not a built-in, so the caller falls through to the
/// custom-filter registry. Extracted from ``apply_filter_full`` so the
/// runtime-safe variant and the plain variant share ONE dispatch table — a
/// single source of truth, with no parallel built-in-name list that could
/// drift (#1660; cf. the #1640 parallel-path-drift class).
fn apply_builtin_filter(
    filter_name: &str,
    value: &Value,
    arg: Option<&str>,
    context: Option<&Context>,
    arg_was_quoted: bool,
) -> Option<Result<Value>> {
    // #2250: Django's `@stringfilter` consumes `str(value)`. For a `Decimal`
    // that is NOT the rendered form — `str(Decimal('1E-9'))` is `1E-9`, while
    // `{{ d }}` renders `0.000000001` because `numberformat.format` uses
    // `"{:f}".format(number)`. Both are correct, for different jobs, and
    // `Display` is the render one (#2214), so the string filters need the other.
    //
    // The coercion is free: `Value::Decimal` already CARRIES `str(Decimal)` —
    // it is built from `ob.str()` at the PyO3 boundary (`djust_core::lib.rs`),
    // and `Display` is what applies the expansion. So this hands the filter the
    // raw payload rather than re-deriving anything.
    //
    // Placed HERE — the one dispatch table every built-in call funnels through
    // — rather than in the ~30 arms that call `value.to_string()`, which is the
    // #1646 shape: N correct copies, one of which the next filter forgets.
    // Custom filters need nothing: `apply_custom_filter` hands Python a real
    // `Decimal`, so Django's own `@stringfilter` applies to them unchanged.
    //
    // Only `Decimal` is coerced. Every other variant's `Display` already IS
    // Python's `str()` (or diverges for reasons that show up in `{{ v }}` too,
    // which a filter-boundary coercion could not fix — e.g. `1e300`, #2258).
    let coerced_decimal: Value;
    let value: &Value = match value {
        Value::Decimal(d) if is_string_filter(filter_name) => {
            coerced_decimal = Value::String(d.clone());
            &coerced_decimal
        }
        _ => value,
    };
    let result: Result<Value> = match filter_name {
        "upper" => Ok(Value::String(value.to_string().to_uppercase())),
        "lower" => Ok(Value::String(value.to_string().to_lowercase())),
        "title" => Ok(Value::String(titlecase(&value.to_string()))),
        "length" => {
            let len = match value {
                Value::String(s) => s.len(),
                Value::List(l) | Value::Tuple(l) => l.len(),
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
        "escape" => Ok(value.clone()), // No-op: auto-escaping at render time handles this
        "safe" => Ok(value.clone()),   // No-op: renderer checks for |safe to skip auto-escaping
        "first" => match value {
            Value::List(l) | Value::Tuple(l) => Ok(l.first().cloned().unwrap_or(Value::Missing)),
            Value::String(s) => Ok(Value::String(
                s.chars().next().map(|c| c.to_string()).unwrap_or_default(),
            )),
            _ => Ok(Value::Missing),
        },
        "last" => match value {
            Value::List(l) | Value::Tuple(l) => Ok(l.last().cloned().unwrap_or(Value::Missing)),
            Value::String(s) => Ok(Value::String(
                s.chars().last().map(|c| c.to_string()).unwrap_or_default(),
            )),
            _ => Ok(Value::Missing),
        },
        "join" => {
            // join with separator argument
            let separator = arg.unwrap_or(", ");
            match value {
                Value::List(items) | Value::Tuple(items) => {
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
            // slice filter supports Python slice syntax: ":5", "2:", "2:5".
            // Return the Result directly (no `?`): this arm's value IS the
            // match's `Result<Value>`, and `apply_builtin_filter` returns
            // `Option<Result<Value>>`, where `?` would wrongly target the Option.
            let slice_str = arg.unwrap_or(":");
            apply_slice(value, slice_str)
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
            // Django's `add` is a three-branch chain (#2203):
            //
            //     try:    return int(value) + int(arg)
            //     except: try:    return value + arg
            //             except: return ""
            //
            // The previous implementation was only a partial first branch: it
            // parsed the argument as `i64` and **defaulted to 0** on failure,
            // so `{{ n|add:1.5 }}` silently added nothing, and it had no
            // concatenation branch at all, so `{{ "a"|add:"b" }}` returned "a".
            //
            // Branch order is load-bearing: the int branch runs FIRST, so
            // `{{ "4"|add:"3" }}` is 7, not "43".
            //
            // But `int()` is stricter than "looks numeric", and the difference
            // decides which branch wins. `int("1.5")` RAISES in Python, so
            // Django falls through and CONCATENATES: `{{ "1.5"|add:"1.5" }}` is
            // "1.51.5", not 3. A first pass here accepted "1.5" via an `f64`
            // fallback and returned **2** — not merely a different answer but a
            // fabricated number where Django produces text, which is worse than
            // the bug it replaced.
            //
            // A float LITERAL is different: `{{ n|add:1.5 }}` passes Python a
            // float, and `int(1.5)` is 1. The template layer distinguishes the
            // two by quoting, so `arg_was_quoted` is what separates them —
            // `float_ok` is false for a quoted argument, mirroring `int(str)`.
            // `i128`, not `i64`. Python's ints are unbounded, and TWO separate
            // truncations were losing digits before #2253: `as_f64()` on a
            // `Value::Decimal` is a binary double, so `int()` was off by one
            // from 2^53 up (`Decimal('9007199254740993')|add:1` gave back
            // 9007199254740993), and `as i64` saturated from 2^63 up, so the
            // `checked_add` below overflowed and the filter returned its input
            // UNCHANGED — silently doing nothing rather than adding.
            //
            // The issue that reported this named only the first cause. The
            // second is why widening the truncation alone would not have fixed
            // the cell it cites: 12345678901234567890 does not fit an i64 no
            // matter how exactly you compute it.
            //
            // Non-finite floats are refused rather than saturated. `int(inf)`
            // raises `OverflowError` in Python — uncaught by Django's
            // `except (ValueError, TypeError)` — so there is no answer to
            // agree with, and `i64::MAX` was a fabricated number where the
            // fail-soft below at least returns the value it was given.
            let as_int = |v: &Value, float_ok: bool| -> Option<i128> {
                // `f64 as i128` SATURATES, so a magnitude past i128 would
                // become `i128::MAX` and be reported as a real sum.
                let from_f64 = |f: f64| -> Option<i128> {
                    (f.is_finite() && f.abs() < 1.7e38).then(|| f.trunc() as i128)
                };
                match v {
                    Value::Integer(n) => Some(i128::from(*n)),
                    // `int()` truncates toward zero, so int(1.5) == 1.
                    Value::Float(f) => from_f64(*f),
                    // `int(Decimal('19.99'))` is 19 — truncation, on the EXACT
                    // digits. One definition of Decimal->integer, in
                    // `djust_core::decimal`, shared with `floatformat` (#1646).
                    Value::Decimal(d) => {
                        djust_core::decimal::parse_decimal_parts(d).and_then(|p| p.to_i128_trunc())
                    }
                    // `int(True)` is 1 in Python, so Django's first branch
                    // handles bools: `{{ True|add:1 }}` is 2.
                    Value::Bool(b) => Some(i128::from(*b)),
                    Value::String(s) => s.trim().parse::<i128>().ok().or_else(|| {
                        float_ok
                            .then(|| s.trim().parse::<f64>().ok())
                            .flatten()
                            .and_then(from_f64)
                    }),
                    _ => None,
                }
            };
            let arg_value = arg.map(|s| Value::String(s.to_string()));
            // `checked_add`, not `+`. Python's ints are arbitrary-precision so
            // Django cannot overflow here; a fixed width can, and plain `+`
            // PANICS in a debug build ("attempt to add with overflow") while
            // silently wrapping in release — `{{ max|add:1 }}` returned a
            // NEGATIVE number.
            //
            // On overflow, fall through to the branch below and return the
            // value unchanged — the same fail-soft posture `date` takes on an
            // unparseable input, and honest about not being able to compute it.
            // The VALUE is a real typed value, never a template literal, so its
            // float coercion is always allowed. Only the ARGUMENT's quoting is
            // in question.
            let lhs = as_int(value, true);
            let rhs = arg_value.as_ref().and_then(|a| as_int(a, !arg_was_quoted));
            match lhs.zip(rhs).and_then(|(a, b)| a.checked_add(b)) {
                // A sum outside `i64` is carried as its exact digits rather than
                // being thrown away: `Value::Integer` is an i64 and Python's is
                // not, so `{{ p|add:1 }}` on a 20-digit `DecimalField` had no
                // Integer to return and silently returned its input. A
                // `Value::Decimal` is precisely "an exact digit string" (#2214),
                // renders exactly, and truncates back exactly if another `add`
                // is chained onto it. Beyond i128 the fail-soft below still
                // applies — Python's unbounded ints have no equivalent here
                // (#2253).
                Some(sum) => Ok(match i64::try_from(sum) {
                    Ok(n) => Value::Integer(n),
                    Err(_) => Value::Decimal(sum.to_string()),
                }),
                None => match (value, arg) {
                    // Concatenation branch.
                    (Value::String(s), Some(a)) => Ok(Value::String(format!("{s}{a}"))),
                    // Django's third branch returns "". djust returns the value
                    // unchanged instead: turning a rendered value into silent
                    // emptiness on upgrade is the silent-wrong-output class this
                    // engine keeps having to fix. Documented divergence, not an
                    // oversight.
                    _ => Ok(value.clone()),
                },
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
                Value::List(l) | Value::Tuple(l) => {
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
                Value::Missing => maybe_str,
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
            // Django's `floatformat` is decimal arithmetic, not float
            // formatting: `Decimal(str(text)).quantize(exp, ROUND_HALF_UP)`.
            // The whole algorithm — the `-1` default, the `p <= 0`
            // drop-the-fraction branch, half-up rounding, the `g`/`u` suffixes,
            // string and bool coercion, the 200-digit cut-off — lives in
            // `crate::floatformat`, whose module docs explain what the old
            // `format!("{f:.n$}")` got wrong and what is still not covered
            // (#2253). Localization happens INSIDE it, because by the time the
            // renderer sees the result it is a `Value::String` and
            // indistinguishable from a user's own digits (#2221).
            Ok(crate::floatformat::floatformat(value, arg, arg_was_quoted))
        }
        "filesizeformat" => {
            // filesizeformat filter: formats bytes to human-readable size
            match value {
                Value::Integer(n) => Ok(Value::String(format_filesize(*n))),
                Value::Float(f) => Ok(Value::String(format_filesize(*f as i64))),
                Value::Decimal(_) => Ok(match value.as_f64() {
                    Some(f) => Value::String(format_filesize(f as i64)),
                    None => value.clone(),
                }),
                _ => Ok(value.clone()),
            }
        }
        "random" => {
            // random filter: returns random item from list
            match value {
                Value::List(items) | Value::Tuple(items) if !items.is_empty() => {
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
                Value::List(_) | Value::Tuple(_) => Ok(Value::Missing),
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
            // Supports common Django/strftime format codes.
            // When no format arg is given, check context for DATE_FORMAT
            // (injected from Django settings) before falling back to the
            // hardcoded default (#713).
            let default_format = if arg.is_none() {
                context
                    .and_then(|ctx| ctx.get("DATE_FORMAT"))
                    .and_then(|v| match v {
                        Value::String(s) => Some(s.as_str()),
                        _ => None,
                    })
            } else {
                None
            };
            let format_str = arg.or(default_format).unwrap_or("N j, Y"); // Default: "Nov. 13, 2025"
            let datetime_str = value.to_string();
            match format_date(&datetime_str, format_str) {
                Ok(formatted) => Ok(Value::String(formatted)),
                Err(e) => {
                    // #1090: surface silent parse failures so template authors
                    // can diagnose without instrumentation. Common cause:
                    // upstream JSON-encoding of datetime objects produces
                    // strings with embedded literal `\"` chars that don't
                    // round-trip through chrono's parsers.
                    tracing::debug!(
                        target: "djust.templates.filters",
                        value = %datetime_str,
                        format = %format_str,
                        error = %e,
                        "|date filter parse failed; returning original value unchanged",
                    );
                    Ok(value.clone()) // If parsing fails, return original value
                }
            }
        }
        "time" => {
            // time filter: formats time with format string.
            // When no format arg is given, check context for TIME_FORMAT
            // (injected from Django settings) before falling back to the
            // hardcoded default (#713).
            let default_format = if arg.is_none() {
                context
                    .and_then(|ctx| ctx.get("TIME_FORMAT"))
                    .and_then(|v| match v {
                        Value::String(s) => Some(s.as_str()),
                        _ => None,
                    })
            } else {
                None
            };
            let format_str = arg.or(default_format).unwrap_or("P"); // Default: "2:30 p.m."
            let datetime_str = value.to_string();
            match format_time(&datetime_str, format_str) {
                Ok(formatted) => Ok(Value::String(formatted)),
                Err(e) => {
                    // #1090: see |date filter — same parse-failure surface,
                    // same diagnostic value when log target is enabled.
                    tracing::debug!(
                        target: "djust.templates.filters",
                        value = %datetime_str,
                        format = %format_str,
                        error = %e,
                        "|time filter parse failed; returning original value unchanged",
                    );
                    Ok(value.clone())
                }
            }
        }
        "dictsort" => {
            // dictsort filter: sorts list of dicts by key
            let sort_key = arg.unwrap_or("name");
            match value {
                Value::List(items) | Value::Tuple(items) => {
                    Ok(Value::List(sort_dicts_by_key(items, sort_key)))
                }
                _ => Ok(value.clone()),
            }
        }
        "dictsortreversed" => {
            // dictsortreversed filter: sorts list of dicts by key in reverse
            let sort_key = arg.unwrap_or("name");
            match value {
                Value::List(items) | Value::Tuple(items) => {
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
        "stringformat" => {
            // stringformat filter: formats value using Python %-style format spec
            // Usage: {{ value|stringformat:"s" }} → "%s" % value
            // The argument is the format spec WITHOUT the leading %
            let spec = arg.unwrap_or("s");
            Ok(Value::String(apply_stringformat(value, spec)))
        }
        "default_if_none" => {
            // Fallback only when the value is None or absent — never for an
            // empty string. BOTH variants (#2203 review): matching only
            // `Missing` inverted the filter, firing on an absent variable while
            // rendering the literal text "None" for the one input it is named
            // for.
            match value {
                // `None` ONLY — not `Missing`. Django substitutes
                // `string_if_invalid` ("") for an absent variable BEFORE the
                // filter runs, so the filter never sees None there and returns
                // the empty string rather than the fallback. Matching `Missing`
                // too made `{{ absent|default_if_none:'NA' }}` render "NA"
                // where Django renders "" — a pre-existing divergence this PR
                // is in the right place to close (#2203 review).
                Value::None => Ok(Value::String(arg.unwrap_or("").to_string())),
                _ => Ok(value.clone()),
            }
        }
        "wordcount" => {
            // wordcount filter: count the number of words
            let count = value.to_string().split_whitespace().count();
            Ok(Value::Integer(count as i64))
        }
        "wordwrap" => {
            // wordwrap filter: wrap text at N characters (word boundary)
            let width = arg.and_then(|s| s.parse::<usize>().ok()).unwrap_or(75);
            Ok(Value::String(word_wrap(&value.to_string(), width)))
        }
        "striptags" => {
            // striptags filter: strip HTML tags from string
            Ok(Value::String(strip_tags(&value.to_string())))
        }
        "addslashes" => {
            // addslashes filter: escape \, ', " with backslashes
            let s = value.to_string();
            let escaped = s
                .replace('\\', "\\\\")
                .replace('\'', "\\'")
                .replace('"', "\\\"");
            Ok(Value::String(escaped))
        }
        "ljust" => {
            // ljust filter: left-align string, pad to width with spaces
            let width = arg.and_then(|s| s.parse::<usize>().ok()).unwrap_or(0);
            let s = value.to_string();
            Ok(Value::String(format!("{s:<width$}")))
        }
        "rjust" => {
            // rjust filter: right-align string, pad to width with spaces
            let width = arg.and_then(|s| s.parse::<usize>().ok()).unwrap_or(0);
            let s = value.to_string();
            Ok(Value::String(format!("{s:>width$}")))
        }
        "center" => {
            // center filter: center string, pad to width with spaces
            let width = arg.and_then(|s| s.parse::<usize>().ok()).unwrap_or(0);
            let s = value.to_string();
            Ok(Value::String(format!("{s:^width$}")))
        }
        "make_list" => {
            // make_list filter: split string into list of characters
            let s = value.to_string();
            let chars: Vec<Value> = s.chars().map(|c| Value::String(c.to_string())).collect();
            Ok(Value::List(chars))
        }
        "json_script" => {
            // json_script filter: wrap value as JSON inside <script id="..."> tag
            let element_id = arg.unwrap_or("data");
            let json_str = value_to_json(value);
            let safe_json = json_escape_for_script(&json_str);
            let safe_id = html_escape(element_id);
            Ok(Value::String(format!(
                "<script id=\"{safe_id}\" type=\"application/json\">{safe_json}</script>"
            )))
        }
        "force_escape" => {
            // force_escape filter: always HTML-escape (unlike escape which is a no-op)
            Ok(Value::String(html_escape(&value.to_string())))
        }
        "escapejs" => {
            // escapejs filter: escape string for use in JavaScript
            Ok(Value::String(escape_js(&value.to_string())))
        }
        "linenumbers" => {
            // linenumbers filter: prepend line numbers to each line
            Ok(Value::String(add_linenumbers(&value.to_string())))
        }
        "get_digit" => {
            // get_digit filter: return Nth digit from right (1-indexed)
            let n = arg.and_then(|s| s.parse::<usize>().ok()).unwrap_or(0);
            Ok(Value::String(get_digit(&value.to_string(), n)))
        }
        "iriencode" => {
            // iriencode filter: like urlencode but preserves non-ASCII chars
            Ok(Value::String(iriencode(&value.to_string())))
        }
        "phone2numeric" => {
            // phone2numeric filter: convert phone letters to digits
            Ok(Value::String(phone2numeric(&value.to_string())))
        }
        "pprint" => {
            // pprint filter: Python-like repr of value
            Ok(Value::String(pprint_value(value)))
        }
        "safeseq" => {
            // safeseq filter: marks each item in a sequence as safe (no-op at filter level)
            Ok(value.clone())
        }
        "escapeseq" => {
            // escapeseq filter: apply HTML escaping to each item in a sequence
            match value {
                Value::List(items) | Value::Tuple(items) => {
                    let escaped: Vec<Value> = items
                        .iter()
                        .map(|item| Value::String(html_escape(&item.to_string())))
                        .collect();
                    Ok(Value::List(escaped))
                }
                _ => Ok(Value::String(html_escape(&value.to_string()))),
            }
        }
        "urlize" => {
            // urlize filter: convert URLs and emails to clickable links
            Ok(Value::String(urlize(&value.to_string(), None)))
        }
        "urlizetrunc" => {
            // urlizetrunc filter: like urlize but truncates displayed URL
            let limit = arg.and_then(|s| s.parse::<usize>().ok());
            Ok(Value::String(urlize(&value.to_string(), limit)))
        }
        "unordered_list" => {
            // unordered_list filter: recursively render nested lists as <li>/<ul>
            match value {
                Value::List(items) | Value::Tuple(items) => {
                    Ok(Value::String(unordered_list(items, 1)))
                }
                _ => Ok(value.clone()),
            }
        }
        "truncatechars_html" => {
            // truncatechars_html filter: truncate by char count, preserving HTML tags
            let num_chars = arg.and_then(|s| s.parse::<usize>().ok()).unwrap_or(20);
            Ok(Value::String(truncate_chars_html(
                &value.to_string(),
                num_chars,
            )))
        }
        "truncatewords_html" => {
            // truncatewords_html filter: truncate by word count, preserving HTML tags
            let num_words = arg.and_then(|s| s.parse::<usize>().ok()).unwrap_or(10);
            Ok(Value::String(truncate_words_html(
                &value.to_string(),
                num_words,
            )))
        }
        // Not a built-in — signal the caller to try the custom-filter
        // registry. (The custom fallback lives in ``apply_filter_full_safe``
        // so it can capture the result's runtime safeness, #1660.)
        _ => return None,
    };
    Some(result)
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

pub fn html_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#x27;")
}

/// Attribute-safe HTML escape.
///
/// Currently behaves identically to [`html_escape`]: both escape
/// `&`, `<`, `>`, `"`, and `'`. The distinct function exists so
/// parse-time classification (whether a `{{ var }}` is inside an
/// HTML opening tag) is visible at the render layer — callers can
/// rely on this to produce attribute-safe output even if the base
/// `html_escape` were ever relaxed to omit quote escaping for text
/// context. Matches Django's `escape()` contract for attribute
/// values: `"` → `&quot;` and `'` → `&#x27;` are always emitted.
pub fn html_escape_attr(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#x27;")
}

/// Django's truncation ellipsis: U+2026, ONE character (#2203).
///
/// django.utils.text.Truncator appends `…`, not `...`. The distinction is not
/// cosmetic for `truncate_chars` — see below.
const ELLIPSIS: &str = "…";

fn truncate_words(text: &str, num_words: usize) -> String {
    let words: Vec<&str> = text.split_whitespace().collect();
    if words.len() <= num_words {
        text.to_string()
    } else {
        // Django separates the ellipsis with a space: "one two …".
        words[..num_words].join(" ") + " " + ELLIPSIS
    }
}

fn truncate_chars(text: &str, num_chars: usize) -> String {
    // Django's `Truncator.chars` opens with `if length <= 0: return ""`, so a
    // limit of 0 yields nothing at all — not a bare ellipsis (#2203 review).
    if num_chars == 0 {
        return String::new();
    }
    if text.chars().count() <= num_chars {
        text.to_string()
    } else {
        // Django counts the ellipsis as ONE character inside the limit, so
        // `truncatechars:5` keeps 4 characters plus `…`. Reserving three (for
        // `...`) kept only 2 and rendered "ab..." where Django gives "abcd…".
        text.chars()
            .take(num_chars.saturating_sub(1))
            .collect::<String>()
            + ELLIPSIS
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
        Value::List(items) | Value::Tuple(items) => {
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

/// The one place a serialized Python datetime is parsed (#2227).
///
/// Four filters need this and three of them grew their own copy, one value
/// shape at a time: `date`/`time` learned datetimes in #2203 and bare times in
/// #2216, while `timesince`/`timeuntil` still accepted only RFC3339 — so a
/// NAIVE datetime, the normal shape under `USE_TZ = False`, did not parse and
/// the filter returned its input verbatim into the page.
///
/// Three instances of one class in three releases, each found by fixing the
/// previous. The cure is this function rather than a third correct copy
/// (#1646).
///
/// Returns the instant plus the two facts callers need about how it was
/// obtained: whether the input carried an offset (`aware`), and whether it was
/// a bare time with no date at all (`time_only`).
///
/// `allow_time_only` exists because the two consumers genuinely differ. A bare
/// time is formattable — `{{ v|time:"H:i" }}` is meaningful — but it has no
/// instant, so measuring elapsed time against it is not: it is anchored on an
/// arbitrary epoch date, and `timesince` would happily report the decades since
/// 1970. Django raises there. Passing `false` keeps that branch unreachable
/// from the duration filters rather than trusting them not to call it.
fn parse_serialized_datetime(
    datetime_str: &str,
    allow_time_only: bool,
) -> Option<(DateTime<chrono::FixedOffset>, bool, bool)> {
    if let Ok(dt) = DateTime::parse_from_rfc3339(datetime_str) {
        return Some((dt, true, false));
    }
    // Naive datetime: "2026-08-22 14:30:00" -> that instant, UTC (#2203).
    //
    // This is how a Python `datetime` arrives -- space-separated, no offset --
    // so it matched NEITHER the RFC3339 branch nor the date-only one below.
    //
    // Both separators, with and without seconds. The `T` variants are not
    // RFC3339 without an offset, so they miss the branch above too. Seconds are
    // optional for a reason worth naming, because the obvious one is wrong:
    // Python ALWAYS emits them. The real source is an HTML
    // `<input type="datetime-local">`, whose submitted value is
    // `YYYY-MM-DDTHH:MM` -- so the no-seconds case that actually occurs is the
    // `T` one, which a first pass missed while covering the space variant that
    // never occurs.
    //
    // `%.f` makes the fractional part OPTIONAL, so each covers both "...:00"
    // and "...:00.123456".
    for fmt in [
        "%Y-%m-%dT%H:%M:%S%.f",
        "%Y-%m-%d %H:%M:%S%.f",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M",
    ] {
        if let Ok(ndt) = chrono::NaiveDateTime::parse_from_str(datetime_str.trim(), fmt) {
            return Some((ndt.and_utc().fixed_offset(), false, false));
        }
    }
    // Date-only: "2026-03-15" -> midnight UTC (#719).
    if let Ok(d) = chrono::NaiveDate::parse_from_str(datetime_str.trim(), "%Y-%m-%d") {
        return Some((
            d.and_hms_opt(0, 0, 0).unwrap().and_utc().fixed_offset(),
            false,
            false,
        ));
    }
    if allow_time_only {
        // Time-only (#2216). Anchored on an arbitrary epoch date so the
        // `Timelike` accessors work unchanged; `time_only` is what stops that
        // borrowed date from ever being rendered -- and why the duration
        // filters must not reach this branch at all.
        for fmt in ["%H:%M:%S%.f", "%H:%M"] {
            if let Ok(t) = chrono::NaiveTime::parse_from_str(datetime_str.trim(), fmt) {
                return Some((
                    chrono::NaiveDate::from_ymd_opt(1970, 1, 1)
                        .unwrap()
                        .and_time(t)
                        .and_utc()
                        .fixed_offset(),
                    false,
                    true,
                ));
            }
        }
    }
    None
}

/// Django's `timesince` / `timeuntil`, ported from `django/utils/timesince.py`
/// rather than approximated (#2228).
///
/// The previous implementation diverged from Django on **every** input,
/// including the aware values that always parsed:
///
/// | elapsed | Django | before |
/// |---|---|---|
/// | 30 s | `0 minutes` | `30 seconds` |
/// | 2 h 30 m | `2 hours, 30 minutes` | `2 hours` |
/// | 10 d | `1 week, 3 days` | `1 week` |
/// | 400 d | `1 year, 1 month` | `1 year` |
///
/// Three separate defects: the separator between a count and its unit is a
/// NO-BREAK SPACE (so the pair never wraps across a line), up to **two
/// adjacent** units are shown, and the smallest unit is the MINUTE — Django
/// ignores seconds entirely.
///
/// Years and months are **calendar-aware**, which is the part an approximation
/// cannot reach: Django's own docstring notes there is exactly "1 year, 1
/// month" between 2013-02-10 and 2014-03-10 *and* between 2007-08-10 and
/// 2008-09-10, though the deltas are 393 and 397 days. Dividing by a fixed
/// 2629746 seconds gets both wrong.
///
/// `MONTHS_DAYS` is Django's own table, February included — it carries 28 with
/// no leap-year case, so the pivot for a source date late in the month clamps
/// to the 28th even in a leap year. A quirk rather than a design, reproduced
/// because parity is the point: changing it here would make djust disagree with
/// Django on exactly those dates.
const MONTHS_DAYS: [u32; 12] = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];

/// Seconds per week / day / hour / minute — Django's `TIME_CHUNKS`.
const TIME_CHUNKS: [i64; 4] = [604_800, 86_400, 3_600, 60];

/// Unit names in `partials` order, singular and plural.
const UNIT_NAMES: [(&str, &str); 6] = [
    ("year", "years"),
    ("month", "months"),
    ("week", "weeks"),
    ("day", "days"),
    ("hour", "hours"),
    ("minute", "minutes"),
];

/// Django's `avoid_wrapping`: every space in a rendered unit becomes U+00A0, so
/// the count and its noun cannot break across a line.
///
/// A test written with an ordinary space passes while shipping the wrong byte,
/// which is why the suite asserts the codepoint rather than the look.
fn nbsp_unit(count: i64, index: usize) -> String {
    let (singular, plural) = UNIT_NAMES[index];
    let name = if count == 1 { singular } else { plural };
    format!("{count}\u{a0}{name}")
}

fn zero_minutes() -> String {
    nbsp_unit(0, 5)
}

/// The shared body of both filters; `timeuntil` is `timesince` with the two
/// datetimes swapped, exactly as Django implements it (`reversed=True`).
///
/// Both filters previously carried near-identical 30-line formatting blocks, so
/// every change had to be made twice — the #1646 shape that produced this
/// issue's siblings. One function now.
#[doc(hidden)]
/// Test-only re-export of [`django_timesince`]. Not part of the public API.
///
/// The filters compare against "now", so a test through them can only assert
/// coarse buckets and would be a coin flip near a boundary (#1795). Exposing
/// the pure two-argument function lets the parity suite pin exact strings —
/// including the calendar cases, which a "now"-relative test could never reach.
pub fn django_timesince_for_tests(d: chrono::NaiveDateTime, now: chrono::NaiveDateTime) -> String {
    django_timesince(d, now)
}

fn django_timesince(d: chrono::NaiveDateTime, now: chrono::NaiveDateTime) -> String {
    use chrono::{Datelike, Timelike};

    // No swap here, deliberately. A first pass added one so both filters could
    // share the body, and it silently broke `timesince` on a FUTURE value:
    // Django returns `0 minutes` there, and a swap turns it into the elapsed
    // time in the wrong direction. `timeuntil` swaps at its own call site,
    // which is exactly what Django's `reversed=True` does.
    if (now - d).num_seconds() <= 0 {
        return zero_minutes();
    }

    // Calendar months between the two dates, backed off by one when the
    // day-of-month (or the time within that day) has not yet come round.
    let mut total_months = (now.year() - d.year()) * 12 + (now.month() as i32 - d.month() as i32);
    if d.day() > now.day() || (d.day() == now.day() && d.time() > now.time()) {
        total_months -= 1;
    }
    let years = total_months.div_euclid(12);
    let months = total_months.rem_euclid(12);

    // The pivot is `d` advanced by whole years and months, so the remainder
    // below is measured from a real calendar date rather than from a fixed
    // number of seconds.
    let pivot = if years != 0 || months != 0 {
        let mut pivot_year = d.year() + years;
        let mut pivot_month = d.month() as i32 + months;
        if pivot_month > 12 {
            pivot_month -= 12;
            pivot_year += 1;
        }
        let day = MONTHS_DAYS[(pivot_month - 1) as usize].min(d.day());
        chrono::NaiveDate::from_ymd_opt(pivot_year, pivot_month as u32, day)
            .and_then(|date| date.and_hms_opt(d.hour(), d.minute(), d.second()))
            .unwrap_or(d)
    } else {
        d
    };

    let mut remaining = (now - pivot).num_seconds();
    let mut partials = vec![years as i64, months as i64];
    for chunk in TIME_CHUNKS {
        let count = remaining.div_euclid(chunk);
        partials.push(count);
        remaining -= chunk * count;
    }

    // First non-zero unit, then up to `DEPTH` ADJACENT units — the walk stops
    // at the first zero, so `1 year, 0 months` renders as `1 year` and never
    // reaches down to `1 year, 3 days`.
    const DEPTH: usize = 2;
    let Some(first) = partials.iter().position(|v| *v != 0) else {
        return zero_minutes();
    };
    let mut out = Vec::new();
    let mut i = first;
    while i < UNIT_NAMES.len() && out.len() < DEPTH {
        if partials[i] == 0 {
            break;
        }
        out.push(nbsp_unit(partials[i], i));
        i += 1;
    }
    out.join(", ")
}

/// Both wall clocks, in the frame Django would compare them in.
///
/// An AWARE value is an instant, and Django does `datetime.now(d.tzinfo)` — so
/// "now" is read in the SOURCE value's offset, not the active zone's. A NAIVE
/// value is compared against `datetime.now()`, naive local time.
///
/// Getting this wrong gives a plausible answer rather than an obvious one:
/// pre-#2227 a naive datetime two hours old reported six hours in a UTC-4 zone,
/// because it was compared against UTC.
fn now_and_then(
    dt: DateTime<chrono::FixedOffset>,
    aware: bool,
) -> (chrono::NaiveDateTime, chrono::NaiveDateTime) {
    if aware {
        let offset = *dt.offset();
        (
            dt.naive_local(),
            Utc::now().with_timezone(&offset).naive_local(),
        )
    } else {
        (dt.naive_utc(), chrono::Local::now().naive_local())
    }
}

fn format_timesince(datetime_str: &str) -> Result<String> {
    // `allow_time_only = false`: a bare time has no instant to measure from
    // (#2227). Django raises there; this returns the input unchanged, which is
    // djust's fail-soft convention for an unparseable value.
    let (dt, aware, _time_only) =
        parse_serialized_datetime(datetime_str, false).ok_or_else(|| {
            DjangoRustError::TemplateError(format!("Invalid datetime format: {datetime_str}"))
        })?;
    let (then, now) = now_and_then(dt, aware);
    Ok(django_timesince(then, now))
}

fn format_timeuntil(datetime_str: &str) -> Result<String> {
    // See the note in `format_timesince` on `allow_time_only = false` (#2227).
    let (dt, aware, _time_only) =
        parse_serialized_datetime(datetime_str, false).ok_or_else(|| {
            DjangoRustError::TemplateError(format!("Invalid datetime format: {datetime_str}"))
        })?;
    let (then, now) = now_and_then(dt, aware);
    // Django's `timeuntil` is `timesince(d, now, reversed=True)` — the same
    // computation with the arguments swapped. A past value yields `0 minutes`.
    if then <= now {
        return Ok(zero_minutes());
    }
    Ok(django_timesince(now, then))
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

/// Format a datetime or date string using Django-style format codes.
///
/// Supported input formats:
/// - RFC 3339 datetime: "2026-04-14T12:00:00Z", "2026-04-14T12:00:00+05:00"
/// - ISO 8601 date only: "2026-04-14" (DateField values — pinned to midnight UTC)
///
/// Not yet supported (Django's `|date` filter accepts these in Python):
/// - Python datetime.date / datetime.datetime objects (handled before Rust via serialization)
/// - Epoch timestamps as integers
/// - Locale-specific string formats (e.g., "March 15, 2026")
///
/// Note: bare date inputs pinned to midnight UTC will show "00:00" for time format
/// codes like "H:i". This matches Django's behavior with DateField values.
/// A parsed datetime plus everything the timezone-dependent format codes need.
///
/// Split out because the wall-clock accessors and the zone metadata answer to
/// different rules, and collapsing them is how the bug got here: `dt` is what
/// `Y`/`H`/`i` read, while `abbrev`/`offset_secs`/`timestamp` describe the zone
/// that wall clock is expressed IN.
struct Stamped {
    /// Wall clock in the zone the value should DISPLAY in.
    dt: DateTime<chrono::FixedOffset>,
    /// Zone abbreviation (`EDT`) when one is known, for `T`/`e`.
    abbrev: Option<String>,
    /// Whether the input carried an offset. Django treats the two differently
    /// and so must this: `e` is the empty string for a naive value but `EDT`
    /// for an aware one.
    aware: bool,
    /// Seconds since the epoch, for `U`. Not derivable from `dt` alone for a
    /// naive value, which Django interprets in the DEFAULT zone rather than UTC.
    timestamp: i64,
    /// The input was a bare `datetime.time` with no date (#2216).
    ///
    /// A distinct state from `!aware`, and the distinction is Django's, not an
    /// invention here: for a naive DATETIME the zone codes report the default
    /// zone (`T` gives `EDT`), while for a bare TIME they render empty, because
    /// `TimeFormat` has no date to resolve an offset against.
    time_only: bool,
}

/// Apply the active render timezone (#2209).
///
/// Django's rules, taken from a live 5.2 render rather than from the docs:
///
/// | input | wall clock | `T`/`O`/`Z` | `e` |
/// |---|---|---|---|
/// | aware  | converted to the active zone | that zone at that instant | same |
/// | naive  | **unchanged** | the active zone at that local time | `""` |
///
/// The naive row is the one that is easy to get wrong. Django does not shift a
/// naive datetime — it is already understood to be local — but it still reports
/// the default zone's abbreviation and offset for it. Shifting naive values
/// would break every project on `USE_TZ = False`, which is the configuration
/// where naive datetimes are the norm.
fn apply_active_timezone(
    dt: DateTime<chrono::FixedOffset>,
    aware: bool,
    time_only: bool,
) -> Stamped {
    use chrono::TimeZone;

    let Some(tz) = crate::timezone::active_timezone() else {
        // No active zone: `USE_TZ = False`, or this crate embedded without
        // Django settings. Pre-#2209 behaviour exactly — format what arrived.
        return Stamped {
            dt,
            abbrev: None,
            aware,
            timestamp: dt.timestamp(),
            time_only,
        };
    };

    if aware {
        let local = dt.with_timezone(&tz);
        return Stamped {
            dt: local.fixed_offset(),
            abbrev: Some(local.format("%Z").to_string()),
            aware,
            timestamp: dt.timestamp(),
            time_only,
        };
    }

    // Naive: keep the wall clock, and read the zone metadata off that same wall
    // clock interpreted in the active zone. `from_local_datetime` is not total —
    // a local time inside a DST gap does not exist, and one inside a fold is
    // ambiguous. `.earliest()` picks the pre-transition offset for a fold, which
    // is what Django's `_datetime_ambiguous_or_imaginary` guard effectively
    // yields; a gap falls back to leaving the metadata unknown rather than
    // inventing an offset.
    let naive = dt.naive_local();
    match tz.from_local_datetime(&naive).earliest() {
        Some(local) => Stamped {
            // Re-stamp the SAME wall clock with the zone's offset at that
            // local time. The wall clock is untouched — this only replaces the
            // meaningless "+0000" the parse produced with the offset the value
            // is actually understood to be in, which is what `O` and `Z` read.
            // Leaving `dt` alone rendered `-0400` as `+0000` for every naive
            // value; caught by the differential against Django, not by
            // inspection.
            dt: local.fixed_offset(),
            abbrev: Some(local.format("%Z").to_string()),
            aware,
            timestamp: local.timestamp(),
            time_only,
        },
        None => Stamped {
            dt,
            abbrev: None,
            aware,
            timestamp: dt.timestamp(),
            time_only,
        },
    }
}

/// Django's `I` format code: is this instant in daylight saving time?
///
/// Compared against the SAME zone's January offset rather than against a fixed
/// UTC baseline, because "is DST active" is a statement about the zone's own
/// standard offset, not about UTC. Southern-hemisphere zones make the naive
/// version wrong: `Australia/Sydney` is +1100 in January (DST) and +1000 in
/// July (standard), so anchoring on January would report every winter instant
/// as DST and every summer one as not.
fn is_dst(stamped: &Stamped) -> bool {
    use chrono::{Datelike, TimeZone};

    let Some(tz) = crate::timezone::active_timezone() else {
        return false;
    };
    let here = stamped.dt.offset().local_minus_utc();
    // The zone's standard offset is the MINIMUM it takes across the year: DST
    // only ever moves a clock forward. Two probes six months apart bracket any
    // real transition rule.
    let year = stamped.dt.year();
    let probe = |month: u32| {
        chrono::NaiveDate::from_ymd_opt(year, month, 15)
            .and_then(|d| d.and_hms_opt(12, 0, 0))
            .and_then(|ndt| tz.from_local_datetime(&ndt).earliest())
            .map(|d| d.fixed_offset().offset().local_minus_utc())
    };
    match (probe(1), probe(7)) {
        (Some(jan), Some(jul)) => here > jan.min(jul),
        _ => false,
    }
}

/// Django's `N`: month in Associated Press style (`django.utils.dates.MONTHS_AP`).
///
/// NOT `%b` plus a period, which is what this was. AP does not abbreviate the
/// short months at all — March, April, May, June and July are spelled out — and
/// September is `Sept.`, not `Sep.`. So five of twelve months were wrong, plus
/// September: **half the year**.
///
/// It survived because the three values in the format-code parity table are
/// January, August and February, and all three happen to be months where
/// `%b` + `.` is correct. A randomized sweep over 400 (date, code) pairs found
/// it in seconds. Three carefully chosen values are still a sample.
const MONTHS_AP: [&str; 12] = [
    "Jan.", "Feb.", "March", "April", "May", "June", "July", "Aug.", "Sept.", "Oct.", "Nov.",
    "Dec.",
];

/// English ordinal suffix for a day of the month (#2217).
///
/// The 11th/12th/13th exception is why this is a function and not
/// `["th","st","nd","rd"][n % 10]`: those end in 1/2/3 but take `th`.
fn ordinal_suffix(day: u32) -> &'static str {
    match (day % 100, day % 10) {
        (11..=13, _) => "th",
        (_, 1) => "st",
        (_, 2) => "nd",
        (_, 3) => "rd",
        _ => "th",
    }
}

/// Django's `L`: a real leap-year test, unlike the February-28 clamp in
/// `timesince` which Django itself does not leap-adjust.
fn is_leap_year(year: i32) -> bool {
    (year % 4 == 0 && year % 100 != 0) || year % 400 == 0
}

/// Django's `t`: days in the month, leap-aware — February 2028 is 29.
fn days_in_month(year: i32, month: u32) -> u32 {
    match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 if is_leap_year(year) => 29,
        2 => 28,
        _ => 30,
    }
}

/// 12-hour clock, with midnight and noon reading as 12 rather than 0.
fn twelve_hour(hour: u32) -> u32 {
    match hour % 12 {
        0 => 12,
        h => h,
    }
}

/// Django's `c`: ISO 8601, with microseconds only when non-zero.
///
/// A bare time renders just the time part — Django's `TimeFormat` has no date
/// to emit, and a borrowed epoch anchor must not leak (#2216).
fn iso_8601(dt: &DateTime<chrono::FixedOffset>, time_only: bool) -> String {
    let micros = dt.timestamp_subsec_micros();
    if time_only {
        return if micros == 0 {
            dt.format("%H:%M:%S").to_string()
        } else {
            dt.format("%H:%M:%S%.6f").to_string()
        };
    }
    if micros == 0 {
        dt.format("%Y-%m-%dT%H:%M:%S%:z").to_string()
    } else {
        dt.format("%Y-%m-%dT%H:%M:%S%.6f%:z").to_string()
    }
}

/// Does this format string use a code a bare `datetime.time` cannot answer?
///
/// The set is Django's, enumerated by running all 38 format characters against a
/// `datetime.time` through Django's own engine rather than reasoned about — the
/// three groups do not follow from the docs:
///
/// * **supported**: `a A c f g G h H i P s u`
/// * **empty in place**: `e T O Z` (a naive time has no zone, but the rest of
///   the format still renders)
/// * **empties everything**: the date codes below
///
/// `r` and `U` are excluded deliberately: Django RAISES on those
/// (`combine() argument 1 must be datetime.date`), so a template using them
/// 500s rather than rendering empty. Returning `""` here would be a silent
/// divergence in the direction of hiding a template bug; they instead fall
/// through to the catch-all and render as themselves, which is what every
/// unimplemented code does (#2217).
///
/// Backslash escapes are honoured — `{{ v|date:"\Y H:i" }}` is a literal `Y`
/// and does NOT blank the render.
///
/// The walk deliberately mirrors the FORMATTER's own escape handling below
/// (consume the backslash, skip the next char) rather than Django's, which uses
/// a negative lookbehind and so treats `\\Y` — an escaped backslash followed
/// by `Y` — as a literal too. djust's formatter has always disagreed with
/// Django on that double-escape case; matching Django here while the formatter
/// did not would be worse than the disagreement, because the guard would blank
/// a render the formatter was about to produce correctly. The pre-existing
/// double-escape divergence belongs with the other format-string gaps (#2217).
fn format_has_date_code(format_str: &str) -> bool {
    const DATE_ONLY: &[char] = &[
        'b', 'd', 'D', 'F', 'I', 'j', 'l', 'L', 'm', 'M', 'n', 'N', 'o', 'S', 't', 'w', 'W', 'y',
        'Y', 'z',
    ];
    let mut chars = format_str.chars();
    while let Some(ch) = chars.next() {
        if ch == '\\' {
            chars.next(); // escaped: a literal, not a code
            continue;
        }
        if DATE_ONLY.contains(&ch) {
            return true;
        }
    }
    false
}

fn format_date(datetime_str: &str, format_str: &str) -> Result<String> {
    // Tracks whether the input carried a UTC offset. Set by the naive branches
    // below; the RFC3339 branch leaves it true. The distinction survives all
    // the way to `apply_active_timezone` because Django applies `localtime` to
    // aware values ONLY — converting a naive one would move every timestamp in
    // a `USE_TZ = False` project.
    // One shared parser for every filter that takes a serialized datetime
    // (#2227). `allow_time_only = true` here: formatting a bare time is
    // meaningful, unlike measuring a duration against one.
    let (dt, aware, time_only) =
        parse_serialized_datetime(datetime_str, true).ok_or_else(|| {
            DjangoRustError::TemplateError(format!("Invalid datetime format: {datetime_str}"))
        })?;

    // #2209: everything above produced a wall clock in whatever offset arrived.
    // Django would have run `timezone.localtime()` by now.
    let stamped = apply_active_timezone(dt, aware, time_only);
    let dt = stamped.dt;

    // #2216: a bare time has no date, so Django's `TimeFormat` has no attribute
    // to answer a date code with — it raises, the filter swallows it, and the
    // WHOLE render comes back empty. Verified: `{{ v|date:"H:i Y" }}` on a
    // `time` is `''`, not `'23:30 '`.
    //
    // Note this is all-or-nothing, unlike the TIMEZONE codes, which render as
    // an empty string in place and leave the rest formatted
    // (`{{ v|date:"H:i T" }}` is `'23:30 '`). Two different rules that look
    // alike; conflating them gives `'23:30 '` where Django gives `''`.
    if time_only && format_has_date_code(format_str) {
        return Ok(String::new());
    }

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
            'N' => result.push_str(MONTHS_AP[(dt.month() - 1) as usize]),
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
                // Django's `a` is `'a.m.'` / `'p.m.'` WITH the periods — only
                // the uppercase `A` is bare `AM`/`PM`. djust emitted `am`/`pm`
                // for both, which the #2216 differential caught on a datetime
                // as well as a time, so it is not a time-only defect.
                //
                // Out of #2216's scope strictly speaking, and fixed here rather
                // than filed because it is two lines in the function being
                // edited, verified against Django, and shipping "time filters
                // now match Django" alongside a wrong `a` in the same match arm
                // would be odd.
                if dt.hour() < 12 {
                    result.push_str("a.m.");
                } else {
                    result.push_str("p.m.");
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
            // The remaining Django format codes (#2217). Before this they fell
            // through the catch-all and rendered as THEIR OWN LETTER, so
            // `{{ v|date:"jS F Y" }}` produced `22S August 2026`.
            //
            // Quiet for a structural reason: rendering an unknown character as
            // itself is also the CORRECT behaviour for a literal, so an
            // unimplemented code is indistinguishable from an intentional one
            // without a differential against Django. Every expectation below
            // came from running all 38 characters through Django's own engine.
            'b' => result.push_str(&dt.format("%b").to_string().to_lowercase()), // aug
            'S' => result.push_str(ordinal_suffix(dt.day())),                    // nd
            'w' => result.push_str(&dt.weekday().num_days_from_sunday().to_string()), // 0=Sun
            'z' => result.push_str(&dt.ordinal().to_string()), // day of year, 1-based
            't' => result.push_str(&days_in_month(dt.year(), dt.month()).to_string()),
            'L' => {
                // Python's `bool` repr, not `true`/`false` — Django renders the
                // object, so the template shows `True`/`False`.
                result.push_str(if is_leap_year(dt.year()) {
                    "True"
                } else {
                    "False"
                });
            }
            'o' => result.push_str(&dt.iso_week().year().to_string()), // ISO week-year
            'W' => result.push_str(&dt.iso_week().week().to_string()), // ISO week number
            'u' => result.push_str(&format!("{:06}", dt.timestamp_subsec_micros())),
            'f' => {
                // 12-hour time, minutes elided when zero: `7:30`, but plain `7`
                // on the hour. Django's own shorthand for "time, tersely".
                let hour = twelve_hour(dt.hour());
                if dt.minute() == 0 {
                    result.push_str(&hour.to_string());
                } else {
                    result.push_str(&format!("{}:{:02}", hour, dt.minute()));
                }
            }
            'c' => result.push_str(&iso_8601(&dt, stamped.time_only)),
            'r' => {
                // RFC 5322, e.g. `Sat, 22 Aug 2026 19:30:45 -0400`.
                result.push_str(&dt.format("%a, %d %b %Y %H:%M:%S %z").to_string());
            }
            // Timezone codes (#2209). Before the active-zone plumbing existed
            // these had no answer to give, so they fell through to the
            // catch-all and rendered as their own letter: `{{ v|date:"H:i T" }}`
            // produced "19:30 T". They are grouped here because they are the
            // visible face of the same blindness the conversion above fixes —
            // shipping the conversion while `T` still emitted a literal T would
            // be a timezone-parity claim that fails on the commonest idiom that
            // shows a timezone.
            //
            // Django's naive-value semantics differ per code and are NOT
            // uniform, which is why each is handled rather than sharing one
            // branch: `e` is "" for a naive value, while `T`/`O`/`Z` report the
            // DEFAULT zone's abbreviation and offset for that local time.
            'e' => {
                // Timezone name — empty for a naive value.
                if stamped.aware {
                    if let Some(a) = stamped.abbrev.as_deref() {
                        result.push_str(a);
                    }
                }
            }
            'T' => {
                // Timezone abbreviation, for naive DATETIMES too — but not for
                // a bare time, which has no date to resolve an offset against
                // (#2216). Django gives `'23:30 '` for `H:i T` on a time; an
                // implementation that reused the naive-datetime rule here would
                // give `'23:30 UTC'`, inventing a zone from the epoch date this
                // value was anchored on.
                if !stamped.time_only {
                    if let Some(a) = stamped.abbrev.as_deref() {
                        result.push_str(a);
                    }
                }
            }
            // Same rule as `T`: a bare time reports no offset, and the borrowed
            // anchor date must not leak one.
            'O' if !stamped.time_only => result.push_str(&dt.format("%z").to_string()),
            'Z' if !stamped.time_only => {
                result.push_str(&dt.offset().local_minus_utc().to_string())
            }
            'O' | 'Z' => {}
            'U' => result.push_str(&stamped.timestamp.to_string()), // seconds since epoch
            'I' => {
                // '1' during DST, '0' otherwise. Django reads `dst()`; the
                // equivalent here is whether this zone's offset at this instant
                // differs from its offset in January, which is what a DST rule
                // means. A zone with no DST answers 0 for every instant.
                result.push(if is_dst(&stamped) { '1' } else { '0' });
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
            // Any pair that is numeric on BOTH sides. Two deltas, not one:
            // a Decimal column used to sort as all-Equal, i.e. not at all
            // (#2214) — and so did a MIXED int/float column, since the arms
            // above cover only like-for-like and `sort_dicts_by_key` has no
            // `(Integer, Float)` arm.
            //
            // The mixed case is a strict improvement, not a regression: against
            // Django, an all-permutations sweep of a mixed pool agreed 938/2184
            // before and 2184/2184 after. Deliberately left unguarded for that
            // reason, unlike `values_equal`'s wildcard, which was restricted to
            // Decimal pairs because widening it there CHANGED answers for the
            // worse (#2240 round 6).
            _ => match (a_val.as_f64(), b_val.as_f64()) {
                (Some(a), Some(b)) => a.partial_cmp(&b).unwrap_or(std::cmp::Ordering::Equal),
                _ => std::cmp::Ordering::Equal,
            },
        }
    });

    sorted_items
}

fn get_dict_value(value: &Value, key: &str) -> Value {
    match value {
        Value::Object(map) => map.get(key).cloned().unwrap_or(Value::Missing),
        _ => Value::Missing,
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

fn apply_stringformat(value: &Value, spec: &str) -> String {
    // Implements Django's stringformat filter.
    // The spec is a Python printf-style format specifier WITHOUT the leading %.
    // Common specifiers: "s" (string), "d" (integer), "f" (float),
    // "05d" (zero-padded int), ".2f" (2 decimal places).

    let last_char = spec.chars().last().unwrap_or('s');

    match last_char {
        's' => value.to_string(),
        'd' | 'i' => {
            let int_val = match value {
                Value::Integer(n) => *n,
                Value::Float(f) => *f as i64,
                Value::Decimal(_) => value.as_f64().unwrap_or(0.0) as i64,
                Value::Bool(b) => {
                    if *b {
                        1
                    } else {
                        0
                    }
                }
                _ => value.to_string().parse::<i64>().unwrap_or(0),
            };

            let prefix = &spec[..spec.len() - 1];
            if prefix.is_empty() {
                format!("{int_val}")
            } else if let Some(stripped) = prefix.strip_prefix('0') {
                let width = if stripped.is_empty() {
                    prefix.parse::<usize>().unwrap_or(0)
                } else {
                    stripped.parse::<usize>().unwrap_or(0)
                };
                format!("{int_val:0>width$}")
            } else {
                let width = prefix.parse::<usize>().unwrap_or(0);
                format!("{int_val:>width$}")
            }
        }
        'f' | 'F' => {
            let float_val = match value {
                Value::Float(f) => *f,
                Value::Integer(n) => *n as f64,
                _ => value.to_string().parse::<f64>().unwrap_or(0.0),
            };

            let prefix = &spec[..spec.len() - 1];
            if let Some(dot_pos) = prefix.find('.') {
                let precision = prefix[dot_pos + 1..].parse::<usize>().unwrap_or(6);
                format!("{float_val:.precision$}")
            } else {
                format!("{float_val:.6}")
            }
        }
        'e' | 'E' => {
            let float_val = match value {
                Value::Float(f) => *f,
                Value::Integer(n) => *n as f64,
                _ => value.to_string().parse::<f64>().unwrap_or(0.0),
            };
            let prefix = &spec[..spec.len() - 1];
            let precision = if let Some(dot_pos) = prefix.find('.') {
                prefix[dot_pos + 1..].parse::<usize>().unwrap_or(6)
            } else {
                6
            };
            if last_char == 'E' {
                format!("{float_val:.precision$E}")
            } else {
                format!("{float_val:.precision$e}")
            }
        }
        _ => value.to_string(),
    }
}

fn word_wrap(text: &str, width: usize) -> String {
    if width == 0 {
        return text.to_string();
    }
    let mut result = String::new();
    let mut line_len = 0;

    for (i, word) in text.split_whitespace().enumerate() {
        let word_len = word.len();
        if i > 0 && line_len + 1 + word_len > width {
            result.push('\n');
            line_len = 0;
        } else if i > 0 {
            result.push(' ');
            line_len += 1;
        }
        result.push_str(word);
        line_len += word_len;
    }
    result
}

fn strip_tags(s: &str) -> String {
    let mut result = String::with_capacity(s.len());
    let mut in_tag = false;
    for c in s.chars() {
        match c {
            '<' => in_tag = true,
            '>' => in_tag = false,
            _ if !in_tag => result.push(c),
            _ => {}
        }
    }
    result
}

fn json_escape_for_script(s: &str) -> String {
    // Escape characters that could break out of <script> tags
    // Matches Django's _json_script_escapes (django/utils/html.py)
    s.replace('&', "\\u0026")
        .replace('<', "\\u003C")
        .replace('>', "\\u003E")
        .replace('\u{2028}', "\\u2028")
        .replace('\u{2029}', "\\u2029")
}

/// The escaped BODY of a JSON string, without the surrounding quotes.
///
/// The ONE helper every quoted string in `value_to_json` goes through — the
/// `String` arm, the `Decimal` arm (#2214 review) and the object KEY path
/// (#2241). A value that can reach a `<script type="application/json">` body
/// must be escaped whatever position carries it; a key is a JSON string with
/// exactly the same grammar as a value, and the partial chain it used to own
/// (`\` and `"` only) emitted a raw newline that `json.loads` rejects.
///
/// Escapes the whole `0x00`–`0x1F` control range, not just the three
/// characters with short forms: RFC 8259 forbids ALL of them unescaped inside
/// a string, so `{"k": "a\u{0}b"}` did not parse either — in every arm, which
/// is why the range moved here rather than into the key path alone.
///
/// Deliberately does NOT escape `<`, `>`, `&`, U+2028 or U+2029:
/// `json_escape_for_script` runs over the assembled document afterwards and
/// covers exactly those (`json_script` composes the two). Escaping them here
/// too would be the double-application the single-helper shape exists to
/// avoid. `0x7F` (DEL) is left raw because JSON permits it raw — `json.loads`
/// round-trips it, and `json.dumps(ensure_ascii=False)` emits it unescaped.
fn json_string_body(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '"' => out.push_str("\\\""),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            // The two remaining short forms `json.dumps` emits, so the output
            // matches Python's for the same input.
            '\u{08}' => out.push_str("\\b"),
            '\u{0C}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => {
                out.push_str(&format!("\\u{:04x}", c as u32));
            }
            c => out.push(c),
        }
    }
    out
}

fn value_to_json(value: &Value) -> String {
    match value {
        // Both are JSON `null`: JSON cannot distinguish absent from None.
        Value::Missing | Value::None => "null".to_string(),
        Value::Bool(b) => {
            if *b {
                "true".to_string()
            } else {
                "false".to_string()
            }
        }
        Value::Integer(n) => n.to_string(),
        Value::Float(f) => format!("{f}"),
        // A quoted JSON string, matching `DjangoJSONEncoder.default`, which
        // returns `str(o)` for a Decimal (#2214). Emitting a bare JSON number
        // here would put the precision loss back on the wire by the other door
        // — `json_script` is a path to the browser too.
        // Escaped through the SAME helper as `String`, deliberately.
        //
        // The first version reasoned it was exempt: `str(Decimal)` yields only
        // digits, `.`, sign, `E`/`e` and `NaN`/`Infinity`, none JSON-significant.
        // True of a Python-sourced Decimal and false of the variant — since
        // #2214 gave binary encodings a tag, a `Value::Decimal` can be
        // deserialized holding an ARBITRARY string, and the unescaped form let
        // it inject JSON structure into a `<script type="application/json">`
        // body that client code parses. Found by review; the argument was sound
        // about the values it considered and wrong about the type.
        Value::Decimal(d) => format!("\"{}\"", json_string_body(d)),
        Value::String(s) => format!("\"{}\"", json_string_body(s)),
        // JSON has no tuple; Python's `json.dumps` emits an array for one.
        Value::List(items) | Value::Tuple(items) => {
            let parts: Vec<String> = items.iter().map(value_to_json).collect();
            format!("[{}]", parts.join(", "))
        }
        Value::Object(map) => {
            let mut parts: Vec<String> = map
                .iter()
                .map(|(k, v)| {
                    // Through the SAME helper as the value arms (#2241). The
                    // partial chain this replaces escaped `\` and `"` only, so
                    // a key holding a newline emitted it raw and the whole
                    // script body stopped parsing — a dict key is as
                    // attacker-reachable as a dict value.
                    let key_json = format!("\"{}\"", json_string_body(k));
                    format!("{}: {}", key_json, value_to_json(v))
                })
                .collect();
            // Kept sorted. The stated reason ("deterministic output") no longer
            // applies — `Object` is an IndexMap since #2203, so iteration order
            // is already deterministic — but changing it would alter every
            // existing `json_script` payload's key order. Python's
            // `json.dumps` preserves insertion order, so this is a remaining
            // divergence, deliberately left alone here (#1079).
            parts.sort();
            format!("{{{}}}", parts.join(", "))
        }
    }
}

/// Structural pins over `value_to_json`, taken from Rust's own TOKEN STREAM (#2249).
///
/// These two assertions used to live in `python/tests/test_json_script_escaping_2241.py`,
/// where they sliced this function's body out of `filters.rs` with `str::index` and
/// matched against the RAW text. That is the #2238 defect in a language the #2238
/// helper cannot reach: `djust.tests._source_scan` runs CPython's `tokenize`, so a
/// `.rs` file comes back **unchanged, silently**, and wiring those pins to it would
/// have looked like a fix and been a no-op (pinned from the other side by
/// `test_rust_source_is_NOT_stripped_and_comes_back_unchanged`).
///
/// Both text pins were prose-blind, and — measured, because the issue's first draft
/// had it backwards — **the direction follows the assertion's shape**:
///
/// | mutation of the real source              | `.replace(` ban | `json_string_body(` count |
/// |------------------------------------------|-----------------|---------------------------|
/// | baseline                                  | GREEN           | 3, GREEN                  |
/// | `//` comment naming `.replace(`           | **RED**         | 3, GREEN                  |
/// | `//` comment naming `json_string_body(`   | GREEN           | **4, RED**                |
/// | a real arm deleted, its text left in a `//` | GREEN         | **3, GREEN** ← the deletion|
/// | a real arm deleted cleanly                | GREEN           | 2, RED                    |
///
/// A NEGATIVE assertion false-**alarms** on prose — the comment someone writes next
/// to the ban ("never add a `.replace(` chain here") fails the build, and the #2237
/// shape is then to contort the prose. A POSITIVE count false-**passes** — deleting a
/// real escape site and leaving `// was: json_string_body(d)` behind keeps it at 3.
/// That second one is the #1817 bug verbatim, in Rust, and is the half nothing caught.
///
/// The cure is to stop matching text. `proc_macro2` lexes this file with Rust's own
/// lexer, which drops `//` and `/* */` before the pin ever sees them and makes each
/// string literal ONE opaque token — so `".replace("` inside a string is not a call
/// either, which the text pin also got wrong. A hand-written Rust stripper in Python
/// was the alternative and is the #1646 shape: a second lexer to keep correct through
/// raw strings (`r#"…"#`), byte strings, nested block comments, and lifetimes, which
/// look exactly like unterminated char literals. There is no such thing to maintain
/// here.
///
/// It also cannot silently no-op, which the text version could in three ways: the path
/// is `include_str!`, so a moved file is a COMPILE error rather than a skipped test;
/// the lex result is asserted; and the function is located in the token tree rather
/// than by `str::index`, so the body is exactly the brace group and not "everything up
/// to the next `\nfn `".
#[cfg(test)]
mod value_to_json_structure {
    use proc_macro2::{Delimiter, TokenStream, TokenTree};
    use std::str::FromStr;

    /// This file's own source, embedded at COMPILE time. A wrong path cannot
    /// reach a runtime `assert` — it fails to build.
    const SOURCE: &str = include_str!("filters.rs");

    /// The token trees inside `fn value_to_json`'s `{ … }` block.
    fn body_tokens() -> Vec<TokenTree> {
        let stream = TokenStream::from_str(SOURCE).expect("filters.rs must lex as Rust");
        let toks: Vec<TokenTree> = stream.into_iter().collect();
        for i in 0..toks.len() {
            let is_target = matches!((&toks[i], toks.get(i + 1)),
                (TokenTree::Ident(kw), Some(TokenTree::Ident(name)))
                    if *kw == "fn" && *name == "value_to_json");
            if !is_target {
                continue;
            }
            // The first brace group after the signature IS the body.
            for tok in &toks[i + 2..] {
                if let TokenTree::Group(g) = tok {
                    if g.delimiter() == Delimiter::Brace {
                        return g.stream().into_iter().collect();
                    }
                }
            }
            panic!("`fn value_to_json` has no body block");
        }
        panic!("`fn value_to_json` not found in filters.rs — did it move or get renamed?");
    }

    /// Count occurrences of the IDENTIFIER `name`, recursing into every group.
    ///
    /// One matcher, deliberately — a name and a call shape were two mechanisms and
    /// only one of them could ever be reached from a test, which makes the other
    /// decoration (#1859). After the lexer has run, an occurrence of the identifier
    /// IS a reference to the thing: a comment is gone and a string literal is a
    /// single opaque `Literal`, so there is nothing left for a `(`-suffix rule to
    /// exclude. It also over-approximates in the safe direction for the ban below,
    /// where a bare `replace` used any way at all is the drift being banned.
    ///
    /// Recursion is required and not incidental: every call being counted sits
    /// inside `format!(…)`, one level down in a parenthesis group.
    fn count_ident(toks: &[TokenTree], name: &str) -> usize {
        let mut n = 0;
        for tok in toks {
            match tok {
                TokenTree::Ident(id) if *id == name => n += 1,
                TokenTree::Group(g) => {
                    let inner: Vec<TokenTree> = g.stream().into_iter().collect();
                    n += count_ident(&inner, name);
                }
                _ => {}
            }
        }
        n
    }

    /// Three quoted-string sites — `Decimal`, `String`, the object key — one helper.
    ///
    /// The partial chain #2241 fixed survived a convergence that had already NAMED
    /// the gap. A comment naming a gap does not close it; a count that goes red when
    /// a third chain appears does (#1646/#1859).
    #[test]
    fn value_to_json_escapes_every_string_through_the_one_helper() {
        let body = body_tokens();
        let n = count_ident(&body, "json_string_body");
        assert_eq!(
            n, 3,
            "value_to_json should escape exactly its three quoted-string sites \
             (Decimal, String, the object key) through json_string_body; found {n}"
        );
    }

    /// A local `.replace(` inside `value_to_json` is how the key path drifted.
    #[test]
    fn value_to_json_has_no_escape_chain_of_its_own() {
        let body = body_tokens();
        let n = count_ident(&body, "replace");
        assert_eq!(
            n, 0,
            "value_to_json grew an inline escape chain again ({n} references to \
             `replace`) — route it through json_string_body instead (#2241)"
        );
    }

    /// Both counts must see through a `format!(…)` wrapper — every real one is there.
    ///
    /// This is what makes the recursion load-bearing rather than incidental: drop
    /// the `Group` arm of `count_ident` and this goes red on its own.
    #[test]
    fn a_call_nested_inside_a_macro_group_is_still_counted() {
        let src = "let x = format!(\"{}\", json_string_body(s.replace('a', \"b\")));\n";
        let toks: Vec<TokenTree> = TokenStream::from_str(src)
            .expect("the canary fixture must lex")
            .into_iter()
            .collect();
        assert_eq!(count_ident(&toks, "json_string_body"), 1);
        assert_eq!(count_ident(&toks, "replace"), 1);
    }

    /// Empirical canary (#1459) — the pin must be blind to PROSE, in both shapes.
    ///
    /// Not a re-implementation: the mutations run through the same `count_*`
    /// functions the pins above use. Each asserts the mutation applied before
    /// reporting anything, so it cannot pass by failing to mutate (#2129/#2135).
    #[test]
    fn prose_naming_the_pattern_does_not_move_either_pin() {
        let real = body_tokens();
        assert_eq!(
            (
                count_ident(&real, "json_string_body"),
                count_ident(&real, "replace")
            ),
            (3, 0),
            "baseline drifted"
        );

        let prose = "\
            // never add a .replace( chain here — route through json_string_body(x)\n\
            /* json_string_body(a); json_string_body(b); s.replace('x', \"y\") */\n\
            let quoted = format!(\"\\\"{}\\\"\", json_string_body(s));\n";
        let toks: Vec<TokenTree> = TokenStream::from_str(prose)
            .expect("the canary fixture must lex")
            .into_iter()
            .collect();

        // The mutation IS real: the RAW text names both patterns many times over,
        // which is exactly what the pre-#2249 text pins were counting.
        assert_eq!(
            prose.matches("json_string_body(").count(),
            4,
            "fixture text changed"
        );
        assert!(prose.contains(".replace("), "fixture text changed");

        // ...and the lexer sees exactly the one live call and no `replace` at all.
        assert_eq!(
            count_ident(&toks, "json_string_body"),
            1,
            "prose in a // or /* */ comment was counted as a call site"
        );
        assert_eq!(
            count_ident(&toks, "replace"),
            0,
            "prose in a comment tripped the `.replace(` ban — the #2237 false alarm"
        );
    }

    /// Empirical canary (#1459) — the LATENT half, which nothing caught before.
    ///
    /// A real escape site deleted with its text left behind in a comment kept the
    /// text count at 3 and the pin green. Through the lexer the count drops, so the
    /// pin's own `assert_eq!(n, 3)` goes red.
    #[test]
    fn a_deleted_call_site_left_behind_in_a_comment_still_drops_the_count() {
        let real = body_tokens();
        assert_eq!(
            count_ident(&real, "json_string_body"),
            3,
            "baseline drifted"
        );

        let mutated = "\
            let a = format!(\"\\\"{}\\\"\", json_string_body(d));\n\
            // was: let b = format!(\"\\\"{}\\\"\", json_string_body(s));\n\
            let b = format!(\"\\\"{}\\\"\", s);\n\
            let c = format!(\"\\\"{}\\\"\", json_string_body(k));\n";
        assert_eq!(
            mutated.matches("json_string_body(").count(),
            3,
            "the RAW text must still read 3 — that IS the false negative being shown"
        );

        let toks: Vec<TokenTree> = TokenStream::from_str(mutated)
            .expect("the canary fixture must lex")
            .into_iter()
            .collect();
        assert_eq!(
            count_ident(&toks, "json_string_body"),
            2,
            "a commented-out call site is still being counted — the deletion is invisible"
        );
    }

    /// A string literal is ONE token, so naming a call inside one is not a call.
    ///
    /// The text pins got this wrong too, in both directions: the `.replace(` ban
    /// would have fired on an error message quoting the banned shape, and the count
    /// would have risen on one quoting the helper.
    #[test]
    fn a_call_shape_inside_a_string_literal_is_not_a_call() {
        let src = "\
            let msg = \"route it through json_string_body(x), never s.replace('a', \\\"b\\\")\";\n";
        let toks: Vec<TokenTree> = TokenStream::from_str(src)
            .expect("the canary fixture must lex")
            .into_iter()
            .collect();
        assert!(src.contains("json_string_body(") && src.contains(".replace("));
        assert_eq!(count_ident(&toks, "json_string_body"), 0);
        assert_eq!(count_ident(&toks, "replace"), 0);
    }

    /// The locator is structural, not textual: it finds the function by tokens and
    /// takes exactly its brace group.
    ///
    /// The text version sliced to the next `"\nfn "`, so it carried whatever trailing
    /// comment sat between the closing brace and the next item — and would have taken
    /// the wrong end had a nested `fn` appeared. This asserts the body starts at
    /// `match value` and ends inside the function.
    #[test]
    fn the_body_is_the_function_s_brace_group_and_nothing_after_it() {
        let body = body_tokens();
        assert!(!body.is_empty(), "empty body");
        match &body[0] {
            TokenTree::Ident(id) => assert_eq!(id.to_string(), "match", "body starts at `match`"),
            other => panic!("unexpected first token: {other:?}"),
        }
        // `escape_js` is the next item in the file; the body must NOT reach it.
        assert_eq!(
            count_ident(&body, "escape_js"),
            0,
            "the slice ran past the end of value_to_json"
        );
    }
}

fn escape_js(s: &str) -> String {
    let mut result = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '\\' => result.push_str("\\u005C"),
            '\'' => result.push_str("\\u0027"),
            '"' => result.push_str("\\u0022"),
            '>' => result.push_str("\\u003E"),
            '<' => result.push_str("\\u003C"),
            '&' => result.push_str("\\u0026"),
            '=' => result.push_str("\\u003D"),
            '-' => result.push_str("\\u002D"),
            ';' => result.push_str("\\u003B"),
            '\n' => result.push_str("\\u000A"),
            '\r' => result.push_str("\\u000D"),
            '\t' => result.push_str("\\u0009"),
            '\0' => result.push_str("\\u0000"),
            '\u{2028}' => result.push_str("\\u2028"),
            '\u{2029}' => result.push_str("\\u2029"),
            _ => result.push(c),
        }
    }
    result
}

fn add_linenumbers(s: &str) -> String {
    let lines: Vec<&str> = s.split('\n').collect();
    let width = lines.len().to_string().len();
    lines
        .iter()
        .enumerate()
        .map(|(i, line)| format!("{:>width$}. {line}", i + 1))
        .collect::<Vec<_>>()
        .join("\n")
}

fn get_digit(s: &str, n: usize) -> String {
    if n == 0 {
        return s.to_string();
    }
    let digits: Vec<char> = s.chars().filter(|c| c.is_ascii_digit()).collect();
    if n > digits.len() {
        return s.to_string();
    }
    digits[digits.len() - n].to_string()
}

fn iriencode(s: &str) -> String {
    // Like urlencode but preserves non-ASCII characters (for IRIs).
    // Matches Django's iri_to_uri: preserves RFC 3986 unreserved + reserved chars.
    let mut result = String::with_capacity(s.len() * 3);
    for c in s.chars() {
        if c.is_ascii_alphanumeric()
            || matches!(c, '-' | '_' | '.' | '~')  // unreserved
            || matches!(c, '/' | ':' | '?' | '#' | '[' | ']' | '@')  // gen-delims
            || matches!(c, '!' | '$' | '&' | '\'' | '(' | ')' | '*' | '+' | ',' | ';' | '=')  // sub-delims
            || !c.is_ascii()
        // non-ASCII preserved for IRIs
        {
            result.push(c);
        } else {
            let mut buf = [0u8; 4];
            let encoded = c.encode_utf8(&mut buf);
            for byte in encoded.bytes() {
                result.push_str(&format!("%{:02X}", byte));
            }
        }
    }
    result
}

fn phone2numeric(s: &str) -> String {
    s.chars()
        .map(|c| match c.to_ascii_uppercase() {
            'A' | 'B' | 'C' => '2',
            'D' | 'E' | 'F' => '3',
            'G' | 'H' | 'I' => '4',
            'J' | 'K' | 'L' => '5',
            'M' | 'N' | 'O' => '6',
            'P' | 'Q' | 'R' | 'S' => '7',
            'T' | 'U' | 'V' => '8',
            'W' | 'X' | 'Y' | 'Z' => '9',
            other => other,
        })
        .collect()
}

fn pprint_value(value: &Value) -> String {
    match value {
        // `pprint` already rendered Python repr before #2203 — it was `Display`
        // that was the outlier. Both variants print "None" here to preserve
        // this filter's existing output exactly.
        Value::Missing | Value::None => "None".to_string(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::Integer(n) => n.to_string(),
        Value::Float(f) => format!("{f}"),
        // `pprint` shows the constructor form, as `repr` does (#2214).
        Value::Decimal(d) => format!("Decimal('{d}')"),
        Value::String(s) => format!("'{s}'"),
        // List-only: `pprint` renders a tuple with parentheses, so the arm
        // below must stay reachable. (A blanket Tuple twin here made it dead
        // code — caught by clippy's unreachable_patterns.)
        Value::List(items) => {
            let parts: Vec<String> = items.iter().map(pprint_value).collect();
            format!("[{}]", parts.join(", "))
        }
        Value::Tuple(items) => {
            let parts: Vec<String> = items.iter().map(pprint_value).collect();
            if parts.len() == 1 {
                format!("({},)", parts[0])
            } else {
                format!("({})", parts.join(", "))
            }
        }
        Value::Object(map) => {
            let mut parts: Vec<String> = map
                .iter()
                .map(|(k, v)| format!("'{}': {}", k, pprint_value(v)))
                .collect();
            parts.sort();
            format!("{{{}}}", parts.join(", "))
        }
    }
}

static URLIZE_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r#"(?x)
        (?:
            (?:https?://|ftp://|www\.)   # URL starting with http(s)://, ftp://, or www.
            [^\s<>"']+                   # URL body
        )
        |
        (?:
            [a-zA-Z0-9._%+\-]+          # email local part
            @
            [a-zA-Z0-9.\-]+             # email domain
            \.[a-zA-Z]{2,}              # TLD
        )
    "#,
    )
    .unwrap()
});

fn urlize(text: &str, trunc_limit: Option<usize>) -> String {
    let mut result = String::new();
    let mut last_end = 0;

    for m in URLIZE_RE.find_iter(text) {
        // Escape non-URL text between matches (prevents XSS via raw HTML injection)
        result.push_str(&html_escape(&text[last_end..m.start()]));

        let matched = m.as_str();

        // Determine if this is an email or a URL
        if matched.contains('@') && !matched.starts_with("http") {
            // Email — escape href and display text
            let safe_href = html_escape(matched);
            let display = html_escape(&truncate_url_display(matched, trunc_limit));
            result.push_str(&format!("<a href=\"mailto:{safe_href}\">{display}</a>"));
        } else {
            // URL
            let href = if matched.starts_with("www.") {
                format!("http://{matched}")
            } else {
                matched.to_string()
            };
            // Strip trailing punctuation from href/display that's not part of URL
            let (href_clean, display_raw, trailing) = strip_url_trailing(&href, matched);
            let safe_href = html_escape(&href_clean);
            let display = html_escape(&truncate_url_display(&display_raw, trunc_limit));
            let safe_trailing = html_escape(&trailing);
            result.push_str(&format!(
                "<a href=\"{safe_href}\" rel=\"nofollow\">{display}</a>{safe_trailing}"
            ));
        }

        last_end = m.end();
    }

    // Escape remaining text after last match
    result.push_str(&html_escape(&text[last_end..]));
    result
}

fn strip_url_trailing<'a>(href: &'a str, display: &'a str) -> (String, String, String) {
    // Strip trailing punctuation that's likely not part of the URL.
    // Only strip ')' if parentheses are unbalanced (preserves Wikipedia-style URLs).
    let trailing_chars: &[char] = &['.', ',', '!', '?', ';', ':'];
    let mut href_s = href.to_string();
    let mut display_s = display.to_string();
    let mut trailing = String::new();

    loop {
        if href_s.ends_with(trailing_chars) {
            let c = href_s.pop().unwrap();
            display_s.pop();
            trailing.insert(0, c);
        } else if href_s.ends_with(')') {
            // Only strip ')' if there are more closing than opening parens
            let open = href_s.chars().filter(|&c| c == '(').count();
            let close = href_s.chars().filter(|&c| c == ')').count();
            if close > open {
                href_s.pop();
                display_s.pop();
                trailing.insert(0, ')');
            } else {
                break;
            }
        } else {
            break;
        }
    }

    (href_s, display_s, trailing)
}

fn truncate_url_display(s: &str, limit: Option<usize>) -> String {
    match limit {
        Some(n) if s.chars().count() > n => {
            let truncated: String = s.chars().take(n.saturating_sub(3)).collect();
            format!("{truncated}...")
        }
        _ => s.to_string(),
    }
}

fn unordered_list(items: &[Value], depth: usize) -> String {
    let indent = "\t".repeat(depth);
    let mut result = Vec::new();

    let mut i = 0;
    while i < items.len() {
        let item = &items[i];

        // Check if the next item is a sublist
        let sublist = if i + 1 < items.len() {
            if let Value::List(sub) = &items[i + 1] {
                i += 1; // consume the sublist
                Some(sub)
            } else {
                None
            }
        } else {
            None
        };

        let escaped_item = html_escape(&item.to_string());
        match sublist {
            Some(sub) if !sub.is_empty() => {
                let sub_content = unordered_list(sub, depth + 1);
                let sub_indent = "\t".repeat(depth + 1);
                result.push(format!(
                    "{indent}<li>{escaped_item}\n{sub_indent}<ul>\n{sub_content}\n{sub_indent}</ul>\n{indent}</li>"
                ));
            }
            _ => {
                result.push(format!("{indent}<li>{escaped_item}</li>"));
            }
        }

        i += 1;
    }

    result.join("\n")
}

/// Tracks open HTML tags during truncation, providing shared logic for
/// `truncate_chars_html` and `truncate_words_html`.
struct HtmlTagTracker {
    open_tags: Vec<String>,
}

impl HtmlTagTracker {
    fn new() -> Self {
        Self {
            open_tags: Vec::new(),
        }
    }

    /// Read a full HTML tag from the char iterator and track open/close state.
    /// Returns the raw tag string (e.g. `<b>`, `</p>`).
    fn consume_tag(&mut self, chars: &mut impl Iterator<Item = char>) -> String {
        let mut tag = String::from('<');
        for tc in chars {
            tag.push(tc);
            if tc == '>' {
                break;
            }
        }
        let tag_inner = tag.trim_start_matches('<').trim_end_matches('>').trim();
        if let Some(stripped) = tag_inner.strip_prefix('/') {
            let name = stripped
                .split_whitespace()
                .next()
                .unwrap_or("")
                .to_lowercase();
            if let Some(pos) = self.open_tags.iter().rposition(|t| *t == name) {
                self.open_tags.remove(pos);
            }
        } else if !tag_inner.ends_with('/')
            && !tag_inner.starts_with('!')
            && !is_void_element(tag_inner.split_whitespace().next().unwrap_or(""))
        {
            let name = tag_inner
                .split_whitespace()
                .next()
                .unwrap_or("")
                .to_lowercase();
            if !name.is_empty() {
                self.open_tags.push(name);
            }
        }
        tag
    }

    /// Append closing tags for all still-open elements (in reverse order).
    fn close_open_tags(&self, result: &mut String) {
        for tag_name in self.open_tags.iter().rev() {
            result.push_str(&format!("</{tag_name}>"));
        }
    }
}

fn truncate_chars_html(text: &str, limit: usize) -> String {
    if limit == 0 {
        return String::new();
    }

    // #2203: the HTML twins carried BOTH halves of the bug their plain-text
    // counterparts just had — the wrong glyph and a three-character reservation
    // — so `truncatechars` and `truncatechars_html` disagreed on the same page
    // (#1646).
    //
    // `.chars().count()`, NOT `.len()`. `"…".len()` is 3 BYTES, exactly like
    // `"..."`, so swapping the constant alone keeps reserving three and looks
    // correct while silently preserving the arithmetic bug.
    let ellipsis = ELLIPSIS;
    let mut visible_count = 0;
    let mut tracker = HtmlTagTracker::new();
    let mut result = String::new();
    let mut chars = text.chars().peekable();
    let target = limit.saturating_sub(ellipsis.chars().count());

    while let Some(c) = chars.next() {
        if c == '<' {
            result.push_str(&tracker.consume_tag(&mut chars));
        } else {
            visible_count += 1;
            if visible_count > target {
                result.push_str(ellipsis);
                tracker.close_open_tags(&mut result);
                return result;
            }
            result.push(c);
        }
    }

    // Text was shorter than the limit, return as-is
    result
}

fn truncate_words_html(text: &str, limit: usize) -> String {
    if limit == 0 {
        return String::new();
    }

    // #2203 — Django's `truncatewords` passes `truncate=" …"` explicitly, so the
    // leading space is genuine here (unlike `truncatechars`, which has none).
    let ellipsis = concat!(" ", "…");
    let mut word_count = 0;
    let mut in_word = false;
    let mut tracker = HtmlTagTracker::new();
    let mut result = String::new();
    let mut chars = text.chars().peekable();

    while let Some(c) = chars.next() {
        if c == '<' {
            in_word = false;
            result.push_str(&tracker.consume_tag(&mut chars));
        } else if c.is_whitespace() {
            if in_word {
                in_word = false;
            }
            if word_count >= limit {
                result.push_str(ellipsis);
                tracker.close_open_tags(&mut result);
                return result;
            }
            result.push(c);
        } else {
            if !in_word {
                word_count += 1;
                in_word = true;
                if word_count > limit {
                    result.push_str(ellipsis);
                    tracker.close_open_tags(&mut result);
                    return result;
                }
            }
            result.push(c);
        }
    }

    result
}

fn is_void_element(tag: &str) -> bool {
    matches!(
        tag.to_lowercase().as_str(),
        "area"
            | "base"
            | "br"
            | "col"
            | "embed"
            | "hr"
            | "img"
            | "input"
            | "link"
            | "meta"
            | "param"
            | "source"
            | "track"
            | "wbr"
    )
}

pub mod tags {
    // Placeholder for custom tags
}

#[cfg(test)]
mod parse_shape_tests_2227 {
    //! One parser for every filter that takes a serialized datetime (#2227).
    //!
    //! Three filters grew their own copy, one value shape at a time — `date`
    //! and `time` learned datetimes in #2203 and bare times in #2216, while
    //! `timesince`/`timeuntil` still accepted only RFC3339. Three instances of
    //! one class in three releases, each found by fixing the previous. These
    //! cases pin the shared parser so the fourth filter does not start a fourth
    //! copy.

    use super::parse_serialized_datetime;

    /// Every shape a serialized Python value actually arrives in, and what the
    /// parser must report about each.
    const SHAPES: &[(&str, bool, bool)] = &[
        // (input, aware, time_only)
        ("2026-08-22T23:30:00+00:00", true, false), // aware datetime, isoformat
        ("2026-08-22 23:30:00+00:00", true, false), // aware datetime, str()
        ("2026-08-22T23:30:00", false, false),      // NAIVE — the #2227 case
        ("2026-08-22 23:30:00", false, false),
        ("2026-08-22T23:30:00.123456", false, false), // microseconds
        ("2026-08-22T23:30", false, false),           // <input type=datetime-local>
        ("2026-08-22 23:30", false, false),
        ("2026-08-22", false, false), // date only
    ];

    #[test]
    fn every_serialized_shape_parses() {
        for (input, aware, time_only) in SHAPES {
            let got = parse_serialized_datetime(input, true);
            let (_, got_aware, got_time_only) =
                got.unwrap_or_else(|| panic!("{input:?} should parse"));
            assert_eq!(got_aware, *aware, "aware for {input:?}");
            assert_eq!(got_time_only, *time_only, "time_only for {input:?}");
        }
    }

    #[test]
    fn the_naive_shapes_are_reported_as_naive() {
        // The distinction the whole timezone fix rests on (#2209): an aware
        // value is converted to the active zone, a naive one is not. A parser
        // that reported everything as aware would silently shift every naive
        // datetime.
        let (_, aware, _) = parse_serialized_datetime("2026-08-22T23:30:00", true).unwrap();
        assert!(!aware);
        let (_, aware, _) = parse_serialized_datetime("2026-08-22T23:30:00+00:00", true).unwrap();
        assert!(aware);
    }

    #[test]
    fn a_bare_time_parses_only_when_the_caller_allows_it() {
        // The reason the flag exists. A bare time is formattable but has no
        // instant, so it is anchored on an arbitrary epoch date — and
        // `timesince` against that anchor would report the decades since 1970.
        // Django raises there; keeping the branch unreachable is safer than
        // trusting the duration filters not to call it.
        let (_, _, time_only) = parse_serialized_datetime("23:30:00", true).unwrap();
        assert!(time_only);
        assert!(
            parse_serialized_datetime("23:30:00", false).is_none(),
            "a duration filter must NOT be handed a bare time"
        );
        assert!(parse_serialized_datetime("23:30", false).is_none());
    }

    #[test]
    fn a_date_is_still_a_date_when_time_only_is_disallowed() {
        // Guard: `allow_time_only = false` must narrow ONLY the time branch.
        // Rejecting date-only values too would re-break `timesince` on a
        // `DateField`, which is one of its commonest inputs.
        let (_, _, time_only) = parse_serialized_datetime("2026-08-22", false).unwrap();
        assert!(!time_only);
        assert!(parse_serialized_datetime("2026-08-22T23:30:00", false).is_some());
    }

    #[test]
    fn garbage_is_rejected_rather_than_guessed_at() {
        for junk in ["", "not a date", "2026-13-45", "23:99:99", "hello 2026"] {
            assert!(
                parse_serialized_datetime(junk, true).is_none(),
                "{junk:?} should not parse"
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use indexmap::IndexMap;

    #[test]
    fn test_upper_filter() {
        let value = Value::String("hello".to_string());
        let result = apply_filter("upper", &value, None).unwrap();
        assert_eq!(result.to_string(), "HELLO");
    }

    /// #2250: a string filter gets `str(Decimal)`, not the rendered expansion.
    ///
    /// The dispatch-table level of the claim. Parity against a live Django,
    /// across every filter and locale, is
    /// `python/tests/test_string_filter_stringification_2250.py`; this pins that
    /// the coercion is at the ONE chokepoint rather than in the arms, which is
    /// the property that keeps a future filter from missing it (#1646).
    #[test]
    fn string_filters_see_the_decimals_str_form_not_its_display() {
        let value = Value::Decimal("1E-9".to_string());
        // `Display` is the RENDER form and must stay that way (#2214).
        assert_eq!(value.to_string(), "0.000000001");

        for (name, expected) in [
            ("upper", "1E-9"),
            ("lower", "1e-9"),
            ("truncatechars", "1E-9"),
            ("addslashes", "1E-9"),
            ("center", "        1E-9        "),
        ] {
            let arg = match name {
                "truncatechars" => Some("8"),
                "center" => Some("20"),
                _ => None,
            };
            let got = apply_filter(name, &value, arg).unwrap();
            assert_eq!(got.to_string(), expected, "|{name}");
        }
    }

    /// The other half: a NON-string filter must still see the `Decimal`.
    ///
    /// Without this the coercion could be applied to every filter and the test
    /// above would not notice — `floatformat` losing its `Decimal` arm is one of
    /// the two regressions #2214 measured.
    #[test]
    fn non_string_filters_still_receive_the_decimal() {
        let value = Value::Decimal("1234.56".to_string());
        assert!(!is_string_filter("floatformat"));
        assert!(!is_string_filter("add"));
        assert!(!is_string_filter("default"));
        // Reaches the `Value::Decimal` arm and rounds; a `Value::String` would
        // fall through the dispatch and come back unchanged.
        let got = apply_filter("floatformat", &value, Some("1u")).unwrap();
        assert_eq!(got.to_string(), "1234.6");
        // `default` returns the value itself, so it is still a Decimal and still
        // localizes at the render site.
        let got = apply_filter("default", &value, Some("x")).unwrap();
        assert!(matches!(got, Value::Decimal(_)), "{got:?}");
    }

    /// `escape`/`safe` are Django `@stringfilter`s that djust EXCLUDES (#2257).
    ///
    /// Pinned so the exclusion stays a decision. They stay no-ops returning the
    /// value, which is what lets the renderer localize it — the divergence
    /// #2257 tracks.
    #[test]
    fn escape_and_safe_are_excluded_from_the_coercion() {
        assert!(!is_string_filter("escape"));
        assert!(!is_string_filter("safe"));
        let value = Value::Decimal("1E-9".to_string());
        for name in ["escape", "safe"] {
            let got = apply_filter(name, &value, None).unwrap();
            assert!(matches!(got, Value::Decimal(_)), "|{name} gave {got:?}");
        }
    }

    /// The set is Django's, transcribed — 27 of its 29, minus `escape`/`safe`.
    ///
    /// A count pin rather than a floor (#1125): adding a name without deciding
    /// about it fails here, and the Python test re-derives the set from the live
    /// Django registry so a name that is not really a `@stringfilter` fails
    /// there.
    #[test]
    fn the_string_filter_set_is_the_twenty_seven_it_claims() {
        assert_eq!(STRING_FILTERS.len(), 27);
        let mut sorted = STRING_FILTERS.to_vec();
        sorted.sort_unstable();
        sorted.dedup();
        assert_eq!(sorted.len(), 27, "duplicate entry in STRING_FILTERS");
        assert_eq!(sorted, STRING_FILTERS, "STRING_FILTERS is not sorted");
    }

    #[test]
    fn test_length_filter() {
        let value = Value::List(vec![Value::Integer(1), Value::Integer(2)]);
        let result = apply_filter("length", &value, None).unwrap();
        assert!(matches!(result, Value::Integer(2)));
    }

    #[test]
    fn test_escape_filter_is_noop() {
        // |escape is a no-op at filter time; auto-escaping happens at render time
        let value = Value::String("<script>alert('xss')</script>".to_string());
        let result = apply_filter("escape", &value, None).unwrap();
        assert_eq!(result.to_string(), "<script>alert('xss')</script>");
    }

    #[test]
    fn test_html_escape_function() {
        assert_eq!(
            html_escape("<b>\"hello\"</b>"),
            "&lt;b&gt;&quot;hello&quot;&lt;/b&gt;"
        );
        assert_eq!(html_escape("safe text"), "safe text");
        assert_eq!(html_escape("a&b"), "a&amp;b");
        assert_eq!(html_escape("it's"), "it&#x27;s");
    }

    #[test]
    fn test_html_escape_attr_contract_is_independent() {
        // `html_escape_attr` is called in attribute-value context, where
        // unescaped quotes and apostrophes break the attribute string.
        // Pin the contract directly — do NOT rely on `html_escape` happening
        // to do the same work today. If a future change loosens
        // `html_escape` (e.g. to skip quote escaping outside attrs per
        // strict Django parity), `html_escape_attr` MUST still escape
        // quotes + apostrophes. This test locks that in. (PR #TBD,
        // Stage 7 review of feat/rust-template-parity-v050.)
        assert_eq!(html_escape_attr("\""), "&quot;");
        assert_eq!(html_escape_attr("'"), "&#x27;");
        assert_eq!(html_escape_attr("<"), "&lt;");
        assert_eq!(html_escape_attr(">"), "&gt;");
        assert_eq!(html_escape_attr("&"), "&amp;");
        // Combined realistic case: a URL with quotes inside an href=".." value
        assert_eq!(
            html_escape_attr("https://ex.com/?x=\"a\"&y='b'"),
            "https://ex.com/?x=&quot;a&quot;&amp;y=&#x27;b&#x27;"
        );
    }

    #[test]
    fn test_truncatewords_filter() {
        let value = Value::String("This is a long sentence with many words".to_string());
        let result = apply_filter("truncatewords", &value, Some("5")).unwrap();
        // `… ` not `...` — #2203. Value taken from Django itself
        // (`django.template.defaultfilters.truncatewords`), not from what this
        // implementation happens to produce.
        assert_eq!(result.to_string(), "This is a long sentence …");
    }

    #[test]
    fn test_truncatechars_filter() {
        let value = Value::String("This is a long string".to_string());
        let result = apply_filter("truncatechars", &value, Some("10")).unwrap();
        // 9 characters + `…` = the limit of 10. Django reserves ONE character
        // for the ellipsis, not three (#2203). Value taken from Django itself.
        assert_eq!(result.to_string(), "This is a…");
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

        let value = Value::Missing;
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
        assert!(matches!(result, Value::Missing));
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
    fn test_date_filter_datefield_bare_date() {
        // #719: DateField serializes to "2026-03-15" (no time component).
        // The |date filter must handle this by parsing as NaiveDate.
        let value = Value::String("2026-03-15".to_string());

        // `N` is Associated Press style, and AP spells March out in full —
        // this asserted "Mar. 15, 2026" until #2217, pinning the wrong answer
        // for six of twelve months. Verified against Django: 'March 15, 2026'.
        let result = apply_filter("date", &value, Some("N j, Y")).unwrap();
        assert_eq!(result.to_string(), "March 15, 2026");

        let result = apply_filter("date", &value, Some("Y-m-d")).unwrap();
        assert_eq!(result.to_string(), "2026-03-15");

        let result = apply_filter("date", &value, Some("F j")).unwrap();
        assert_eq!(result.to_string(), "March 15");
    }

    #[test]
    fn test_date_filter_invalid_input() {
        // #725: Invalid date strings return original value (Django convention),
        // not an error. The date filter gracefully degrades.
        let invalid_date = Value::String("2026-13-45".to_string());
        let result = apply_filter("date", &invalid_date, Some("Y-m-d")).unwrap();
        assert_eq!(result.to_string(), "2026-13-45");

        let not_a_date = Value::String("not-a-date".to_string());
        let result = apply_filter("date", &not_a_date, Some("Y-m-d")).unwrap();
        assert_eq!(result.to_string(), "not-a-date");

        let empty = Value::String("".to_string());
        let result = apply_filter("date", &empty, Some("Y-m-d")).unwrap();
        assert_eq!(result.to_string(), "");

        let partial = Value::String("2026-03".to_string());
        let result = apply_filter("date", &partial, Some("Y-m-d")).unwrap();
        assert_eq!(result.to_string(), "2026-03");
    }

    #[test]
    fn test_date_filter_uses_context_date_format() {
        use chrono::TimeZone;

        let dt = Utc.with_ymd_and_hms(2025, 11, 13, 14, 30, 0).unwrap();
        let value = Value::String(dt.to_rfc3339());

        // With DATE_FORMAT in context and no explicit arg, should use context format
        let ctx = Context::from_dict(IndexMap::from([(
            "DATE_FORMAT".to_string(),
            Value::String("Y-m-d".to_string()),
        )]));
        let result = apply_filter_with_context("date", &value, None, Some(&ctx)).unwrap();
        assert_eq!(result.to_string(), "2025-11-13");

        // With explicit arg, should ignore context DATE_FORMAT
        let result = apply_filter_with_context("date", &value, Some("N j, Y"), Some(&ctx)).unwrap();
        assert_eq!(result.to_string(), "Nov. 13, 2025");

        // Without context, falls back to default (N j, Y)
        let result = apply_filter_with_context("date", &value, None, None).unwrap();
        assert_eq!(result.to_string(), "Nov. 13, 2025");
    }

    #[test]
    fn test_time_filter_uses_context_time_format() {
        use chrono::TimeZone;

        let dt = Utc.with_ymd_and_hms(2025, 11, 13, 14, 30, 0).unwrap();
        let value = Value::String(dt.to_rfc3339());

        // With TIME_FORMAT in context and no explicit arg, should use context format
        let ctx = Context::from_dict(IndexMap::from([(
            "TIME_FORMAT".to_string(),
            Value::String("H:i".to_string()),
        )]));
        let result = apply_filter_with_context("time", &value, None, Some(&ctx)).unwrap();
        assert_eq!(result.to_string(), "14:30");

        // With explicit arg, should ignore context TIME_FORMAT
        let result = apply_filter_with_context("time", &value, Some("P"), Some(&ctx)).unwrap();
        assert_eq!(result.to_string(), "2:30 p.m.");

        // Without context, falls back to default (P)
        let result = apply_filter_with_context("time", &value, None, None).unwrap();
        assert_eq!(result.to_string(), "2:30 p.m.");
    }

    #[test]
    fn test_dictsort_filter() {
        // Create list of dicts
        let mut dict1 = IndexMap::new();
        dict1.insert("name".to_string(), Value::String("Charlie".to_string()));
        dict1.insert("age".to_string(), Value::Integer(30));

        let mut dict2 = IndexMap::new();
        dict2.insert("name".to_string(), Value::String("Alice".to_string()));
        dict2.insert("age".to_string(), Value::Integer(25));

        let mut dict3 = IndexMap::new();
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
        let mut dict1 = IndexMap::new();
        dict1.insert("name".to_string(), Value::String("Alice".to_string()));

        let mut dict2 = IndexMap::new();
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

    #[test]
    fn test_stringformat_filter_string() {
        let value = Value::Integer(42);
        let result = apply_filter("stringformat", &value, Some("s")).unwrap();
        assert_eq!(result.to_string(), "42");

        let value = Value::String("hello".to_string());
        let result = apply_filter("stringformat", &value, Some("s")).unwrap();
        assert_eq!(result.to_string(), "hello");
    }

    #[test]
    fn test_stringformat_filter_integer() {
        let value = Value::Integer(42);
        let result = apply_filter("stringformat", &value, Some("d")).unwrap();
        assert_eq!(result.to_string(), "42");

        let value = Value::Integer(42);
        let result = apply_filter("stringformat", &value, Some("05d")).unwrap();
        assert_eq!(result.to_string(), "00042");
    }

    #[test]
    fn test_stringformat_filter_float() {
        let value = Value::Float(3.14259);
        let result = apply_filter("stringformat", &value, Some(".2f")).unwrap();
        assert_eq!(result.to_string(), "3.14");

        let value = Value::Integer(42);
        let result = apply_filter("stringformat", &value, Some(".1f")).unwrap();
        assert_eq!(result.to_string(), "42.0");
    }

    #[test]
    fn test_stringformat_filter_scientific() {
        let value = Value::Float(1234.5);
        let result = apply_filter("stringformat", &value, Some(".2e")).unwrap();
        assert_eq!(result.to_string(), "1.23e3");

        let result = apply_filter("stringformat", &value, Some(".2E")).unwrap();
        assert_eq!(result.to_string(), "1.23E3");
    }

    #[test]
    fn test_stringformat_filter_default() {
        let value = Value::Integer(42);
        let result = apply_filter("stringformat", &value, None).unwrap();
        assert_eq!(result.to_string(), "42");
    }

    #[test]
    fn test_default_if_none_with_null() {
        // `None` fires the fallback; `Missing` (an ABSENT variable) does not —
        // Django substitutes "" for it before the filter runs (#2203).
        let none = apply_filter("default_if_none", &Value::None, Some("NA")).unwrap();
        assert_eq!(none.to_string(), "NA");
        let missing = apply_filter("default_if_none", &Value::Missing, Some("NA")).unwrap();
        assert_eq!(missing.to_string(), "");
    }

    #[test]
    fn test_default_if_none_with_empty_string() {
        let value = Value::String("".to_string());
        let result = apply_filter("default_if_none", &value, Some("fallback")).unwrap();
        assert_eq!(result.to_string(), "");
    }

    #[test]
    fn test_default_if_none_with_value() {
        let value = Value::String("hello".to_string());
        let result = apply_filter("default_if_none", &value, Some("fallback")).unwrap();
        assert_eq!(result.to_string(), "hello");
    }

    #[test]
    fn test_wordcount_filter() {
        let value = Value::String("one two three four".to_string());
        let result = apply_filter("wordcount", &value, None).unwrap();
        assert!(matches!(result, Value::Integer(4)));
    }

    #[test]
    fn test_wordcount_filter_empty() {
        let value = Value::String("".to_string());
        let result = apply_filter("wordcount", &value, None).unwrap();
        assert!(matches!(result, Value::Integer(0)));
    }

    #[test]
    fn test_wordwrap_filter() {
        let value = Value::String("this is a long string that should wrap".to_string());
        let result = apply_filter("wordwrap", &value, Some("15")).unwrap();
        assert!(result.to_string().contains('\n'));
    }

    #[test]
    fn test_wordwrap_filter_default() {
        let value = Value::String("short".to_string());
        let result = apply_filter("wordwrap", &value, None).unwrap();
        assert_eq!(result.to_string(), "short");
    }

    #[test]
    fn test_striptags_filter() {
        let value = Value::String("<b>Hello</b> <i>world</i>".to_string());
        let result = apply_filter("striptags", &value, None).unwrap();
        assert_eq!(result.to_string(), "Hello world");
    }

    #[test]
    fn test_striptags_filter_nested() {
        let value = Value::String("<div><p>Text</p></div>".to_string());
        let result = apply_filter("striptags", &value, None).unwrap();
        assert_eq!(result.to_string(), "Text");
    }

    #[test]
    fn test_addslashes_filter() {
        let value = Value::String("it's a \"test\" with \\ backslash".to_string());
        let result = apply_filter("addslashes", &value, None).unwrap();
        assert_eq!(
            result.to_string(),
            "it\\'s a \\\"test\\\" with \\\\ backslash"
        );
    }

    #[test]
    fn test_ljust_filter() {
        let value = Value::String("hi".to_string());
        let result = apply_filter("ljust", &value, Some("10")).unwrap();
        assert_eq!(result.to_string(), "hi        ");
        assert_eq!(result.to_string().len(), 10);
    }

    #[test]
    fn test_ljust_filter_no_pad_needed() {
        let value = Value::String("hello".to_string());
        let result = apply_filter("ljust", &value, Some("3")).unwrap();
        assert_eq!(result.to_string(), "hello");
    }

    #[test]
    fn test_rjust_filter() {
        let value = Value::String("hi".to_string());
        let result = apply_filter("rjust", &value, Some("10")).unwrap();
        assert_eq!(result.to_string(), "        hi");
        assert_eq!(result.to_string().len(), 10);
    }

    #[test]
    fn test_center_filter() {
        let value = Value::String("hi".to_string());
        let result = apply_filter("center", &value, Some("10")).unwrap();
        assert_eq!(result.to_string(), "    hi    ");
        assert_eq!(result.to_string().len(), 10);
    }

    #[test]
    fn test_make_list_filter() {
        let value = Value::String("abc".to_string());
        let result = apply_filter("make_list", &value, None).unwrap();
        match result {
            Value::List(items) | Value::Tuple(items) => {
                assert_eq!(items.len(), 3);
                assert_eq!(items[0].to_string(), "a");
                assert_eq!(items[1].to_string(), "b");
                assert_eq!(items[2].to_string(), "c");
            }
            _ => panic!("Expected List value"),
        }
    }

    #[test]
    fn test_json_script_filter() {
        let value = Value::String("hello".to_string());
        let result = apply_filter("json_script", &value, Some("my-data")).unwrap();
        let s = result.to_string();
        assert!(s.starts_with("<script id=\"my-data\" type=\"application/json\">"));
        assert!(s.ends_with("</script>"));
        assert!(s.contains("\"hello\""));
    }

    #[test]
    fn test_json_script_filter_escapes_script_tags() {
        let value = Value::String("</script><script>alert(1)".to_string());
        let result = apply_filter("json_script", &value, Some("data")).unwrap();
        let s = result.to_string();
        // Must not contain literal </script> inside the JSON
        assert!(!s[..s.len() - 9].contains("</script>"));
        assert!(s.contains("\\u003C"));
    }

    #[test]
    fn test_json_script_filter_escapes_line_separators() {
        let value = Value::String("line\u{2028}sep\u{2029}end".to_string());
        let result = apply_filter("json_script", &value, Some("data")).unwrap();
        let s = result.to_string();
        assert!(s.contains("\\u2028"));
        assert!(s.contains("\\u2029"));
        assert!(!s.contains('\u{2028}'));
        assert!(!s.contains('\u{2029}'));
    }

    /// #2241: the object-KEY path had its own partial chain (`\` and `"`
    /// only), so a key holding a newline emitted a raw control character.
    #[test]
    fn test_json_script_escapes_control_characters_in_object_keys() {
        let mut map = IndexMap::new();
        map.insert(
            "a\nb\tc\rd\\e\"f".to_string(),
            Value::String("v".to_string()),
        );
        let result = apply_filter("json_script", &Value::Object(map), Some("data")).unwrap();
        let s = result.to_string();
        assert!(
            s.contains(r#""a\nb\tc\rd\\e\"f""#),
            "key was not escaped through json_string_body: {s}"
        );
        for raw in ['\n', '\t', '\r'] {
            assert!(!s.contains(raw), "raw {raw:?} survived into the body: {s}");
        }
    }

    /// The whole `0x00`–`0x1F` range, in a key and in a value — the arms
    /// without a short form were raw everywhere before #2241.
    #[test]
    fn test_json_script_escapes_the_whole_control_range() {
        for code in 0x00u32..0x20 {
            let c = char::from_u32(code).unwrap();
            let mut map = IndexMap::new();
            map.insert(format!("k{c}"), Value::String(format!("v{c}")));
            let result = apply_filter("json_script", &Value::Object(map), Some("d")).unwrap();
            let s = result.to_string();
            assert!(
                !s.contains(c),
                "U+{code:04X} rendered raw into the script body: {s:?}"
            );
        }
    }

    /// `0x7F` is legal raw in a JSON string and `json.dumps(ensure_ascii=False)`
    /// leaves it alone; escaping it would be a divergence, not a fix.
    #[test]
    fn test_json_script_leaves_delete_raw() {
        let value = Value::String("a\u{7f}b".to_string());
        let result = apply_filter("json_script", &value, Some("d")).unwrap();
        assert!(result.to_string().contains("\"a\u{7f}b\""));
    }

    #[test]
    fn test_force_escape_filter() {
        let value = Value::String("<b>hello</b>".to_string());
        let result = apply_filter("force_escape", &value, None).unwrap();
        assert_eq!(result.to_string(), "&lt;b&gt;hello&lt;/b&gt;");
    }

    #[test]
    fn test_force_escape_quotes() {
        let value = Value::String("it's \"quoted\"".to_string());
        let result = apply_filter("force_escape", &value, None).unwrap();
        assert!(result.to_string().contains("&#x27;"));
        assert!(result.to_string().contains("&quot;"));
    }

    #[test]
    fn test_escapejs_filter() {
        let value = Value::String("hello\\world".to_string());
        let result = apply_filter("escapejs", &value, None).unwrap();
        assert!(result.to_string().contains("\\u005C"));

        let value = Value::String("it's \"quoted\"".to_string());
        let result = apply_filter("escapejs", &value, None).unwrap();
        assert!(result.to_string().contains("\\u0027"));
        assert!(result.to_string().contains("\\u0022"));

        let value = Value::String("line1\nline2\ttab".to_string());
        let result = apply_filter("escapejs", &value, None).unwrap();
        assert!(result.to_string().contains("\\u000A"));
        assert!(result.to_string().contains("\\u0009"));

        // Test U+2028/U+2029 (line/paragraph separators)
        let value = Value::String("a\u{2028}b\u{2029}c".to_string());
        let result = apply_filter("escapejs", &value, None).unwrap();
        assert!(result.to_string().contains("\\u2028"));
        assert!(result.to_string().contains("\\u2029"));
    }

    #[test]
    fn test_linenumbers_filter() {
        let value = Value::String("first\nsecond\nthird".to_string());
        let result = apply_filter("linenumbers", &value, None).unwrap();
        assert_eq!(result.to_string(), "1. first\n2. second\n3. third");
    }

    #[test]
    fn test_linenumbers_filter_alignment() {
        // With 10+ lines, numbers should be right-aligned
        let lines: Vec<&str> = (0..12).map(|_| "line").collect();
        let value = Value::String(lines.join("\n"));
        let result = apply_filter("linenumbers", &value, None).unwrap();
        let output = result.to_string();
        assert!(output.starts_with(" 1. line"));
        assert!(output.contains("12. line"));
    }

    #[test]
    fn test_get_digit_filter() {
        // 1 = rightmost digit
        let value = Value::String("12345".to_string());
        let result = apply_filter("get_digit", &value, Some("1")).unwrap();
        assert_eq!(result.to_string(), "5");

        let result = apply_filter("get_digit", &value, Some("3")).unwrap();
        assert_eq!(result.to_string(), "3");

        // Out of range returns original
        let result = apply_filter("get_digit", &value, Some("10")).unwrap();
        assert_eq!(result.to_string(), "12345");

        // 0 returns original (Django behavior)
        let result = apply_filter("get_digit", &value, Some("0")).unwrap();
        assert_eq!(result.to_string(), "12345");
    }

    #[test]
    fn test_iriencode_filter() {
        // Preserves non-ASCII chars (unlike urlencode)
        let value = Value::String("café".to_string());
        let result = apply_filter("iriencode", &value, None).unwrap();
        assert_eq!(result.to_string(), "café");

        // Encodes ASCII specials (spaces)
        let value = Value::String("hello world".to_string());
        let result = apply_filter("iriencode", &value, None).unwrap();
        assert!(result.to_string().contains("%20"));

        // Preserves / and :
        let value = Value::String("http://example.com/path".to_string());
        let result = apply_filter("iriencode", &value, None).unwrap();
        assert_eq!(result.to_string(), "http://example.com/path");

        // Preserves # and ? (query/fragment)
        let value = Value::String("http://example.com/path?q=1&p=2#frag".to_string());
        let result = apply_filter("iriencode", &value, None).unwrap();
        assert_eq!(result.to_string(), "http://example.com/path?q=1&p=2#frag");
    }

    #[test]
    fn test_phone2numeric_filter() {
        let value = Value::String("1-800-COLLECT".to_string());
        let result = apply_filter("phone2numeric", &value, None).unwrap();
        assert_eq!(result.to_string(), "1-800-2655328");
    }

    #[test]
    fn test_pprint_filter() {
        // String value
        let value = Value::String("hello".to_string());
        let result = apply_filter("pprint", &value, None).unwrap();
        assert_eq!(result.to_string(), "'hello'");

        // Integer
        let value = Value::Integer(42);
        let result = apply_filter("pprint", &value, None).unwrap();
        assert_eq!(result.to_string(), "42");

        // List
        let value = Value::List(vec![Value::Integer(1), Value::Integer(2)]);
        let result = apply_filter("pprint", &value, None).unwrap();
        assert_eq!(result.to_string(), "[1, 2]");

        // Bool
        let value = Value::Bool(true);
        let result = apply_filter("pprint", &value, None).unwrap();
        assert_eq!(result.to_string(), "True");

        // Null
        let value = Value::Missing;
        let result = apply_filter("pprint", &value, None).unwrap();
        assert_eq!(result.to_string(), "None");
    }

    #[test]
    fn test_safeseq_filter() {
        let value = Value::List(vec![
            Value::String("<b>bold</b>".to_string()),
            Value::String("plain".to_string()),
        ]);
        let result = apply_filter("safeseq", &value, None).unwrap();
        // safeseq is a no-op at filter level; returns the list unchanged
        if let Value::List(items) = result {
            assert_eq!(items.len(), 2);
            assert_eq!(items[0].to_string(), "<b>bold</b>");
        } else {
            panic!("Expected List value");
        }
    }

    #[test]
    fn test_escapeseq_filter() {
        let value = Value::List(vec![
            Value::String("<b>bold</b>".to_string()),
            Value::String("plain".to_string()),
        ]);
        let result = apply_filter("escapeseq", &value, None).unwrap();
        if let Value::List(items) = result {
            assert_eq!(items.len(), 2);
            assert_eq!(items[0].to_string(), "&lt;b&gt;bold&lt;/b&gt;");
            assert_eq!(items[1].to_string(), "plain");
        } else {
            panic!("Expected List value");
        }
    }

    #[test]
    fn test_urlize_filter() {
        // URL detection
        let value = Value::String("Visit https://example.com for info".to_string());
        let result = apply_filter("urlize", &value, None).unwrap();
        let s = result.to_string();
        assert!(s.contains("<a href=\"https://example.com\""));
        assert!(s.contains("rel=\"nofollow\""));

        // Email detection
        let value = Value::String("Email user@example.com for help".to_string());
        let result = apply_filter("urlize", &value, None).unwrap();
        let s = result.to_string();
        assert!(s.contains("<a href=\"mailto:user@example.com\">"));

        // www prefix
        let value = Value::String("Go to www.example.com today".to_string());
        let result = apply_filter("urlize", &value, None).unwrap();
        let s = result.to_string();
        assert!(s.contains("<a href=\"http://www.example.com\""));

        // ftp:// support
        let value = Value::String("Download from ftp://files.example.com/pub".to_string());
        let result = apply_filter("urlize", &value, None).unwrap();
        let s = result.to_string();
        assert!(s.contains("<a href=\"ftp://files.example.com/pub\""));

        // Balanced parentheses preserved (Wikipedia-style URLs)
        let value =
            Value::String("See https://en.wikipedia.org/wiki/Foo_(bar) for details".to_string());
        let result = apply_filter("urlize", &value, None).unwrap();
        let s = result.to_string();
        assert!(s.contains("href=\"https://en.wikipedia.org/wiki/Foo_(bar)\""));

        // Unbalanced trailing paren still stripped
        let value = Value::String("(visit https://example.com) for info".to_string());
        let result = apply_filter("urlize", &value, None).unwrap();
        let s = result.to_string();
        assert!(s.contains("href=\"https://example.com\""));
        assert!(s.contains("</a>)"));
    }

    #[test]
    fn test_urlizetrunc_filter() {
        let value = Value::String("Visit https://example.com/very/long/path for info".to_string());
        let result = apply_filter("urlizetrunc", &value, Some("15")).unwrap();
        let s = result.to_string();
        assert!(s.contains("..."));
        assert!(s.contains("href=\"https://example.com/very/long/path\""));
    }

    #[test]
    fn test_unordered_list_filter() {
        // Flat list
        let value = Value::List(vec![
            Value::String("one".to_string()),
            Value::String("two".to_string()),
        ]);
        let result = apply_filter("unordered_list", &value, None).unwrap();
        let s = result.to_string();
        assert!(s.contains("<li>one</li>"));
        assert!(s.contains("<li>two</li>"));

        // Nested list: ["item", ["sub1", "sub2"]]
        let value = Value::List(vec![
            Value::String("parent".to_string()),
            Value::List(vec![
                Value::String("child1".to_string()),
                Value::String("child2".to_string()),
            ]),
        ]);
        let result = apply_filter("unordered_list", &value, None).unwrap();
        let s = result.to_string();
        assert!(s.contains("<li>parent"));
        assert!(s.contains("<ul>"));
        assert!(s.contains("<li>child1</li>"));
        assert!(s.contains("<li>child2</li>"));
        assert!(s.contains("</ul>"));
    }

    #[test]
    fn test_truncatechars_html_filter() {
        // Truncate and close tags
        let value = Value::String("<p>Hello <b>world</b> this is long</p>".to_string());
        let result = apply_filter("truncatechars_html", &value, Some("11")).unwrap();
        let s = result.to_string();
        // Should preserve tags, count only visible chars, and close open tags
        assert!(s.contains("<p>"));
        // `…` not `...` — #2203. Django renders
        // `<p>Hello <b>worl…</b></p>` for this input.
        assert!(s.contains("…"));
        // Open tags should be closed
        assert!(s.ends_with("</p>") || s.ends_with("</b></p>"));

        // Short text unchanged
        let value = Value::String("<b>Hi</b>".to_string());
        let result = apply_filter("truncatechars_html", &value, Some("20")).unwrap();
        assert_eq!(result.to_string(), "<b>Hi</b>");
    }

    #[test]
    fn test_truncatewords_html_filter() {
        // Truncate by words, preserving tags
        let value = Value::String("<p>one two <b>three four</b> five six</p>".to_string());
        let result = apply_filter("truncatewords_html", &value, Some("3")).unwrap();
        let s = result.to_string();
        assert!(s.contains("one"));
        assert!(s.contains("two"));
        assert!(s.contains("three"));
        // `…` not `...` — #2203. Django renders
        // `<p>one two <b>three …</b></p>` for this input.
        assert!(s.contains("…"));

        // Short text unchanged
        let value = Value::String("<b>one two</b>".to_string());
        let result = apply_filter("truncatewords_html", &value, Some("10")).unwrap();
        assert_eq!(result.to_string(), "<b>one two</b>");
    }
}
