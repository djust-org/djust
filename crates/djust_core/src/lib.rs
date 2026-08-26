//! Core utilities and types for djust
//!
//! This crate provides foundational data structures and utilities used across
//! the djust ecosystem.

use indexmap::IndexMap;
use pyo3::prelude::*;
use pyo3::types::{PyAnyMethods, PyDict, PyList};
use serde::de::{self, MapAccess, SeqAccess, Visitor};
use serde::{Deserialize, Deserializer, Serialize};
use std::fmt;
use std::sync::atomic::{AtomicBool, Ordering};

pub mod context;
pub mod errors;
pub mod locale;
pub mod serialization;

pub use context::Context;
pub use errors::{DjangoRustError, Result};

/// A value that can be used in Django templates
///
/// Uses a custom `Deserialize` implementation instead of `#[serde(untagged)]`
/// to correctly distinguish maps from arrays during MessagePack deserialization.
/// With `#[serde(untagged)]`, `rmp_serde` could deserialize a msgpack map as
/// `List` because the untagged deserializer tries variants in declaration order
/// and msgpack maps can be reinterpreted as sequences of pairs (#612).
/// Map key marking a `Decimal` in a BINARY encoding. See `impl Serialize`.
///
/// Deliberately ugly: `visit_map` treats a one-key map under this exact name as
/// a Decimal, so a user dict with the same single key would be misread. The
/// name is chosen to make that collision a thing you have to try to do.
pub(crate) const DECIMAL_TAG: &str = "__djust_decimal__";

/// The `DECIMAL_TAG` value, for tests that must exercise near-misses against
/// the real constant rather than a copy of the literal.
///
/// `#[doc(hidden)]`: public only because the integration tests live outside the
/// crate. Not API.
#[doc(hidden)]
pub fn decimal_tag() -> &'static str {
    DECIMAL_TAG
}

#[derive(Debug, Clone)]
pub enum Value {
    /// An absent key or attribute. Renders as `""` — Django's
    /// `string_if_invalid` — and is DISTINCT from Python `None` (#2203).
    ///
    /// This variant was `Null` and carried both meanings. It is also what
    /// `CallOutcome::Empty` resolves to, so an `alters_data` refusal or a
    /// serialization-floor denial lands here: those must keep rendering
    /// nothing, never the literal text "None".
    Missing,
    /// Python `None`. Renders as `"None"`, as `str(None)` does (#2203).
    None,
    Bool(bool),
    Integer(i64),
    Float(f64),
    String(String),
    List(Vec<Value>),
    /// A Python tuple. Separate from `List` only so it can render with
    /// parentheses, which `str()` distinguishes (#2203).
    Tuple(Vec<Value>),
    /// Insertion-ordered, NOT a `HashMap`: Rust randomises `HashMap` iteration
    /// per process, so dict repr would differ between renders of the same
    /// template. Python dicts are insertion-ordered (#2203).
    Object(IndexMap<String, Value>),
    /// A Python `Decimal`, carried as its EXACT digit string (#2214).
    ///
    /// Not a `Float`, because that is the bug: PyO3's `extract::<f64>()` goes
    /// through `PyFloat_AsDouble`, which honours `Decimal.__float__`, so every
    /// `Decimal` silently became a binary double before any special case could
    /// see it. `DecimalField` is what Django projects use for money, and a
    /// binary double is precisely what it exists to avoid.
    ///
    /// Not a `String` either, which was the fix the issue suggested: the
    /// serialized value is written back into the template context, so the Rust
    /// renderer sees the same value the wire does. As a string,
    /// `{{ p|floatformat }}` stops rounding and `{% if p > 10 %}` compares
    /// lexically — measured, both regress.
    ///
    /// So: exact digits for rendering and transport, and `as_f64()` for
    /// arithmetic and comparison. Arithmetic keeps today's float behaviour
    /// rather than claiming a precision it does not have; what changes is that
    /// the value no longer LOSES its digits on the way to the browser or to
    /// `{{ p }}`.
    Decimal(String),
}

/// Untagged in human-readable formats, with ONE exception (#2214).
///
/// Untagged is what puts a bare `19.99` on the wire rather than a wrapper
/// object, and that is the right JSON. But it also means a `Decimal` encodes as
/// a plain string, and the deserializer below cannot tell that string from any
/// other — so `Decimal` came back as `Value::String`.
///
/// That is not cosmetic. `SerializableViewState.state` round-trips through
/// msgpack on EVERY read of the default `InMemoryStateBackend` and of the Redis
/// backend, so one cache hit silently turned a Decimal into a string and
/// reproduced both regressions this variant exists to prevent —
/// `{{ p|floatformat }}` stopped rounding, `{% if p > 10 %}` took the wrong
/// branch — plus `bool(Decimal('0.00'))` flipping to true under the
/// non-empty-string rule. The first version of this fix shipped with a test
/// asserting only the ENCODE direction, which stayed green throughout (#2135).
///
/// So: `is_human_readable()` splits the two. JSON (human-readable) keeps the
/// bare string and the wire format is unchanged. msgpack (binary) gets a
/// one-key tagged map that `visit_map` recognises, so state survives the trip.
impl Serialize for Value {
    fn serialize<S>(&self, serializer: S) -> std::result::Result<S::Ok, S::Error>
    where
        S: serde::Serializer,
    {
        use serde::ser::SerializeMap;
        match self {
            Value::Decimal(d) if !serializer.is_human_readable() => {
                let mut m = serializer.serialize_map(Some(1))?;
                m.serialize_entry(DECIMAL_TAG, d)?;
                m.end()
            }
            // Everything else is exactly the untagged derive it replaces.
            Value::Decimal(d) => serializer.serialize_str(d),
            Value::Missing | Value::None => serializer.serialize_none(),
            Value::Bool(b) => serializer.serialize_bool(*b),
            Value::Integer(i) => serializer.serialize_i64(*i),
            Value::Float(f) => serializer.serialize_f64(*f),
            Value::String(st) => serializer.serialize_str(st),
            Value::List(items) | Value::Tuple(items) => items.serialize(serializer),
            Value::Object(o) => o.serialize(serializer),
        }
    }
}

/// Custom Deserialize that uses the deserializer's type hints to distinguish
/// maps from sequences, fixing dict→list corruption in MessagePack round-trips (#612).
impl<'de> Deserialize<'de> for Value {
    fn deserialize<D>(deserializer: D) -> std::result::Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        struct ValueVisitor;

        impl<'de> Visitor<'de> for ValueVisitor {
            type Value = Value;

            fn expecting(&self, formatter: &mut fmt::Formatter) -> fmt::Result {
                formatter.write_str("a JSON/MessagePack value")
            }

            fn visit_unit<E>(self) -> std::result::Result<Value, E>
            where
                E: de::Error,
            {
                Ok(Value::Missing)
            }

            fn visit_none<E>(self) -> std::result::Result<Value, E>
            where
                E: de::Error,
            {
                Ok(Value::Missing)
            }

            fn visit_some<D>(self, deserializer: D) -> std::result::Result<Value, D::Error>
            where
                D: Deserializer<'de>,
            {
                Deserialize::deserialize(deserializer)
            }

            fn visit_bool<E>(self, v: bool) -> std::result::Result<Value, E>
            where
                E: de::Error,
            {
                Ok(Value::Bool(v))
            }

            fn visit_i64<E>(self, v: i64) -> std::result::Result<Value, E>
            where
                E: de::Error,
            {
                Ok(Value::Integer(v))
            }

            fn visit_u64<E>(self, v: u64) -> std::result::Result<Value, E>
            where
                E: de::Error,
            {
                Ok(Value::Integer(v as i64))
            }

            fn visit_f64<E>(self, v: f64) -> std::result::Result<Value, E>
            where
                E: de::Error,
            {
                Ok(Value::Float(v))
            }

            fn visit_str<E>(self, v: &str) -> std::result::Result<Value, E>
            where
                E: de::Error,
            {
                Ok(Value::String(v.to_owned()))
            }

            fn visit_string<E>(self, v: String) -> std::result::Result<Value, E>
            where
                E: de::Error,
            {
                Ok(Value::String(v))
            }

            fn visit_seq<A>(self, mut seq: A) -> std::result::Result<Value, A::Error>
            where
                A: SeqAccess<'de>,
            {
                let mut items = Vec::new();
                while let Some(item) = seq.next_element()? {
                    items.push(item);
                }
                Ok(Value::List(items))
            }

            fn visit_map<A>(self, mut map: A) -> std::result::Result<Value, A::Error>
            where
                A: MapAccess<'de>,
            {
                let mut obj = IndexMap::new();
                while let Some((key, value)) = map.next_entry()? {
                    obj.insert(key, value);
                }
                // The binary-format Decimal tag (#2214). Exactly one key, that
                // key, and a string payload — anything else is a real dict.
                if obj.len() == 1 {
                    if let Some(Value::String(d)) = obj.get(DECIMAL_TAG) {
                        return Ok(Value::Decimal(d.clone()));
                    }
                }
                Ok(Value::Object(obj))
            }
        }

        deserializer.deserialize_any(ValueVisitor)
    }
}

/// Is this a `decimal.Decimal`? (#2214)
///
/// A real `isinstance`, not `type().__name__ == "Decimal"`: a name match would
/// also claim any unrelated user class called `Decimal` and stringify it. A
/// `Decimal` SUBCLASS is correctly claimed, which a name match would miss.
///
/// The type is resolved once per interpreter, NOT per call. An earlier version
/// said `py.import` "is cached by Python, so this costs a dict lookup" — true
/// of the import, and still 18-24% on context conversion once the `getattr` and
/// the `is_instance` were measured rather than reasoned about (#2240 review).
/// Every context value that is not None/bool/int reaches this.
///
/// Fails CLOSED — if `decimal` cannot be imported or the check raises, the
/// answer is "no" and the value takes its previous path. A serialization helper
/// must not raise on an odd object.
pub fn is_decimal(ob: &Bound<'_, PyAny>) -> bool {
    static DECIMAL_TYPE: pyo3::sync::PyOnceLock<Py<PyAny>> = pyo3::sync::PyOnceLock::new();
    // `PyOnceLock`, not `GILOnceCell` — pyo3 0.29 renamed it.
    let py = ob.py();
    let Ok(cls) = DECIMAL_TYPE.get_or_try_init(py, || {
        py.import("decimal")
            .and_then(|m| m.getattr("Decimal"))
            .map(|c| c.unbind())
    }) else {
        return false;
    };
    ob.is_instance(cls.bind(py)).unwrap_or(false)
}

/// Render a Decimal's `str()` form the way Django renders a number (#2214).
///
/// Django's `{{ }}` path is `localize()` -> `numberformat.format()`, which is
/// NOT `str()`. Two rules, both taken from
/// `django/utils/numberformat.py` rather than inferred:
///
/// 1. **`"{:f}".format(number)`** — the non-exponent form. `str()` gives `1E-9`
///    where Django gives `0.000000001`, and `Decimal('1')/Decimal('1000000000')`
///    is `1E-9`, as is `.normalize()` on many values. Rendering `str()` verbatim
///    was a REGRESSION against the previous release, where these were floats and
///    rendered correctly.
/// 2. **`abs(exponent) + len(digits) > 200` switches to `"{:e}"`**, which
///    Django added *"to avoid high memory usage in `{:f}'.format()`"*. Without
///    it `Decimal('1E-10000000')` — twelve bytes — expands to a ten-megabyte
///    string. `main` had no such amplification because the value was an f64.
///
/// Both were missed by the first version of this function, which claimed in its
/// own doc-comment to implement `format(d, 'f')` and did not: it rendered
/// `0E+3` as `0000` where Python gives `0`, reachable from ordinary money
/// arithmetic (`Decimal('1000').quantize(Decimal('1E+2'))` minus itself is
/// `Decimal('0E+2')`, so a zero balance rendered `000`). Verified now by a
/// randomized differential against real Django rather than by reading.
///
/// Non-finite forms (`NaN`, `sNaN`, `Infinity`) have no exponent and pass
/// through, matching `format(Decimal('NaN'), 'f')`.
pub(crate) fn expand_decimal_exponent(raw: &str) -> String {
    // Parse TOWARD Python's `as_tuple()` shape: sign, digit string, exponent.
    // Not identical to it — see `significant` below, which is where a previous
    // version's claim of equivalence went wrong.
    let (sign, rest) = match raw.strip_prefix('-') {
        Some(r) => ("-", r),
        None => ("", raw.strip_prefix('+').unwrap_or(raw)),
    };
    let (mantissa, str_exp) = match rest.find(['e', 'E']) {
        Some(i) => {
            let Ok(e) = rest[i + 1..].parse::<i64>() else {
                return raw.to_string();
            };
            (&rest[..i], e)
        }
        None => (rest, 0),
    };
    let (int_part, frac_part) = match mantissa.split_once('.') {
        Some((i, f)) => (i, f),
        None => (mantissa, ""),
    };
    // Non-finite (or otherwise unparseable) forms pass through untouched.
    //
    // LOAD-BEARING. A previous version of this comment said the opposite —
    // "measured to be so: no input distinguishes this guard" — and that
    // measurement ran only the guard-ON arm and called it a comparison. With
    // no control, of course nothing looked different.
    //
    // Removing it, `""` renders `0`, `-` renders `-0`, and `.`, `+`, `E+5`,
    // `e5` all render `0`: the general path treats an absent coefficient as
    // zero. Reachable, because the binary tag lets a `Value::Decimal` hold any
    // string. Pinned by
    // `test_decimal_value_2214.rs::the_empty_and_punctuation_guard_is_load_bearing`.
    if int_part.is_empty() && frac_part.is_empty() {
        return raw.to_string();
    }
    // Also load-bearing, and a DIFFERENT guard from the one above: these have
    // both a coefficient and an exponent, so they get past the empty check.
    // Without this, `abcE+5` renders `abc00000` and `xyzE+200` renders
    // `x.yze+202` — the exponent machinery applied to letters. Pinned by
    // `the_non_digit_guard_is_load_bearing`.
    if !int_part
        .bytes()
        .chain(frac_part.bytes())
        .all(|b| b.is_ascii_digit())
    {
        return raw.to_string();
    }
    let digits: String = format!("{int_part}{frac_part}");
    // `as_tuple()`'s exponent counts the fractional digits in.
    // SATURATING: `str_exp` comes from a `parse::<i64>()` on attacker-chosen
    // text — the binary tag lets a `Value::Decimal` hold any string — so a
    // payload like `1.5E-9223372036854775808` overflows this subtraction. In
    // debug that panics on the render path; in release, where `overflow-checks`
    // is off, it wraps silently and renders nonsense. Saturating is correct
    // either way: a magnitude that large is far past the cutoff below, so the
    // scientific branch takes it regardless of the exact value (#2240 round 6).
    let exponent = str_exp.saturating_sub(frac_part.len() as i64);

    // `as_tuple().digits` drops LEADING zeros; this string form keeps them — the
    // `0` in `0.xxx`, and any zeros after the point. Counting those inflates the
    // length and corrupts both rules below: the cutoff fires up to several places
    // early, and when it does the coefficient and exponent are shifted by one.
    //
    // A previous version said in its own comment that it split "into Python's
    // `as_tuple()` shape" and did not. It diverged from Django for EVERY `0.xxx`
    // value near the cutoff — including `Decimal(1)/Decimal(7)` under
    // `localcontext(prec=120)`, which is ordinary code. The boundary test missed
    // it because all six of its cases had `1` as their integer part, so not one
    // exercised a `0.xxx` form: true on the axis it enumerated, blind on the one
    // it did not (#1867).
    //
    // Only the two rules below use this. The fixed-point path further down needs
    // the leading zeros to place the point.
    let significant = {
        let trimmed = digits.trim_start_matches('0');
        // `Decimal('0.00').as_tuple().digits` is `(0,)`, not empty — and this
        // floor is also the only thing between an all-zero coefficient over the
        // cutoff and a PANIC: without it `significant` is `""` and the
        // scientific branch's `split_at(1)` is out of bounds. `Decimal("0E-250")`
        // is an ordinary value Django renders fine. Pinned by
        // `an_all_zero_coefficient_over_the_cutoff_does_not_panic`.
        if trimmed.is_empty() {
            "0"
        } else {
            trimmed
        }
    };

    // Django's cutoff (rule 2), on `as_tuple()`'s values, as Django computes it.
    if exponent.unsigned_abs() + significant.len() as u64 > 200 {
        // `format(d, 'e')`: one digit before the point, exponent adjusted.
        let (first, tail) = significant.split_at(1);
        let coefficient = if tail.is_empty() {
            first.to_string()
        } else {
            format!("{first}.{tail}")
        };
        // `{:+}`: Python writes the exponent sign explicitly — `1e+212`, not
        // `1e212`. A randomized differential caught this; reading the format
        // spec did not.
        // Saturating for the same reason as `exponent` above.
        let adjusted = exponent
            .saturating_add(significant.len() as i64)
            .saturating_sub(1);
        return format!("{sign}{coefficient}e{adjusted:+}");
    }

    // A zero coefficient never grows trailing zeros: `format(Decimal('0E+3'),
    // 'f')` is `0`, not `0000`. With a negative exponent it keeps that many
    // decimal places, as `0E-3` -> `0.000` does.
    if digits.bytes().all(|b| b == b'0') {
        return if exponent >= 0 {
            format!("{sign}0")
        } else {
            format!("{sign}0.{}", "0".repeat(exponent.unsigned_abs() as usize))
        };
    }

    // Position of the decimal point within `digits`, after the exponent.
    let point = int_part.len() as i64 + str_exp;
    let body = if point <= 0 {
        format!("0.{}{}", "0".repeat(point.unsigned_abs() as usize), digits)
    } else if point as usize >= digits.len() {
        format!("{}{}", digits, "0".repeat(point as usize - digits.len()))
    } else {
        let (l, r) = digits.split_at(point as usize);
        format!("{l}.{r}")
    };
    format!("{sign}{body}")
}

impl Value {
    /// The numeric view of a value, for arithmetic and comparison (#2214).
    ///
    /// `Decimal` parses its digit string on demand. That is lossy for more than
    /// ~15 significant digits — deliberately, because it is exactly what
    /// happened before this variant existed, so no arithmetic or comparison
    /// changes behaviour. Rendering and transport keep the exact digits, which
    /// is where the loss was actually reaching users.
    pub fn as_f64(&self) -> Option<f64> {
        match self {
            Value::Integer(i) => Some(*i as f64),
            Value::Float(f) => Some(*f),
            Value::Decimal(d) => d.parse::<f64>().ok(),
            _ => None,
        }
    }

    pub fn is_truthy(&self) -> bool {
        match self {
            Value::Missing => false,
            // Python `None` is falsy, same as an absent value.
            Value::None => false,
            Value::Bool(b) => *b,
            Value::Integer(i) => *i != 0,
            Value::Float(f) => *f != 0.0,
            // Django/Python: `bool(Decimal('0.00'))` is False. Parsing is
            // enough — a value too large to parse is certainly non-zero.
            Value::Decimal(d) => d.parse::<f64>().map(|f| f != 0.0).unwrap_or(true),
            Value::String(s) => !s.is_empty(),
            Value::List(l) => !l.is_empty(),
            Value::Tuple(t) => !t.is_empty(),
            Value::Object(o) => !o.is_empty(),
        }
    }
}

// Implement Display trait instead of inherent to_string method
//
// For serialized Django-model dicts the Python-side serializer sets
// `"__str__": str(obj)` on every dict it produces (see
// `python/djust/serialization.py::_serialize_model_safely`). This
// matches Django's default template semantics: `{{ obj }}` in a
// Django template calls `str(obj)`, so a rendered FK like
// `{{ claim.claimant }}` produces the claimant's `__str__`, not a
// placeholder.
//
// Before the #968 fix the Rust renderer ignored the `__str__` key
// and emitted the literal `"[Object]"` for any dict, breaking the
// Django semantic for LiveView templates. The current
// implementation checks for a `"__str__"` entry first and renders
// its string value when present, falling back to `"[Object]"` only
// for dicts that weren't produced by the model serializer.
/// Django-parity value rendering (#2203).
///
/// A process-global rather than a per-render parameter because `Display` has no
/// place to thread config through — the same reason `virtual_keyed_ops` is one
/// (#2017). Applied once from `DjustConfig.ready()`.
///
/// Default ON: `{{ flag }}` renders `True`, matching Django. Set
/// `LIVEVIEW_CONFIG['django_value_repr'] = False` to restore the pre-1.2
/// rendering — the escape hatch for a template that embeds a bool directly in
/// a script block, where `True` is a JS `ReferenceError`. (Django has the same
/// hazard; the Django-correct forms are `|yesno:"true,false"` and
/// `json_script`.)
pub static DJANGO_VALUE_REPR: AtomicBool = AtomicBool::new(true);

/// Set the rendering mode. Called once at startup from Python config.
pub fn set_django_value_repr(enabled: bool) {
    DJANGO_VALUE_REPR.store(enabled, Ordering::Relaxed);
}

/// Read the rendering mode. Exposed so the setter can be tested end to end —
/// a setter alone cannot be (#2017).
pub fn django_value_repr() -> bool {
    DJANGO_VALUE_REPR.load(Ordering::Relaxed)
}

impl Value {
    /// Python `repr()`, used for values NESTED inside a container.
    ///
    /// `str(['a'])` is `"['a']"` while `str('a')` is `"a"` — a nested string is
    /// quoted, a top-level one is not. Containers therefore cannot reuse
    /// `Display` for their elements.
    fn py_repr(&self) -> String {
        match self {
            Value::String(s) => {
                // Python's `repr` rule: single quotes, UNLESS the string
                // contains a `'` and no `"` — then double quotes, with the `'`
                // left unescaped. `repr("a'b")` is `"a'b"`, not `'a\'b'`.
                let escaped = s.replace('\\', "\\\\");
                if escaped.contains('\'') && !escaped.contains('"') {
                    format!("\"{escaped}\"")
                } else {
                    format!("'{}'", escaped.replace('\'', "\\'"))
                }
            }
            // `repr(Decimal('19.99'))` is `Decimal('19.99')`, so a Decimal
            // nested in a list or dict renders the constructor form while a
            // top-level one renders bare digits — the same str/repr split that
            // makes containers unable to reuse Display (#2203, #2214).
            Value::Decimal(d) => format!("Decimal('{d}')"),
            other => other.to_string(),
        }
    }

    /// The pre-#2203 rendering, kept verbatim for the flag's OFF path.
    fn legacy_display(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Value::Missing | Value::None => write!(f, ""),
            Value::Bool(b) => write!(f, "{b}"),
            Value::Integer(i) => write!(f, "{i}"),
            Value::Float(fl) => write!(f, "{fl}"),
            // Exact digits even on the legacy path: `django_value_repr` is the
            // #2203 repr switch, and restoring the #2214 precision loss through
            // it would make a rendering-parity flag silently lossy.
            Value::Decimal(d) => write!(f, "{}", expand_decimal_exponent(d)),
            Value::String(s) => write!(f, "{s}"),
            Value::List(_) | Value::Tuple(_) => write!(f, "[List]"),
            Value::Object(o) => match o.get("__str__") {
                Some(Value::String(s)) => write!(f, "{s}"),
                _ => write!(f, "[Object]"),
            },
        }
    }
}

impl fmt::Display for Value {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        if !django_value_repr() {
            return self.legacy_display(f);
        }
        match self {
            // Django's `string_if_invalid` — an ABSENT value renders nothing.
            // Distinct from `None`, and the reason the old `Null` was split:
            // `CallOutcome::Empty` (an `alters_data` refusal or a
            // serialization-floor denial) lands here and must stay silent.
            Value::Missing => write!(f, ""),
            Value::None => write!(f, "None"),
            Value::Bool(b) => write!(f, "{}", if *b { "True" } else { "False" }),
            Value::Integer(i) => write!(f, "{i}"),
            Value::Float(fl) => {
                // Python keeps the `.0` on an integral float; Rust's Display
                // drops it. Guarded on `is_finite` and a magnitude below 2^53
                // so `inf`, `NaN` and values already in exponent form keep
                // their own formatting.
                if fl.is_finite() && fl.fract() == 0.0 && fl.abs() < 1e16 {
                    write!(f, "{fl:.1}")
                } else {
                    write!(f, "{fl}")
                }
            }
            // Django renders a number through `numberformat.format()`, which
            // uses `"{:f}".format(...)`, so an exponent-form Decimal expands:
            // `1E-9` renders `0.000000001`. NOT `str()` — see
            // `expand_decimal_exponent`.
            Value::Decimal(d) => write!(f, "{}", expand_decimal_exponent(d)),
            Value::String(s) => write!(f, "{s}"),
            Value::List(items) => {
                let inner: Vec<String> = items.iter().map(Value::py_repr).collect();
                write!(f, "[{}]", inner.join(", "))
            }
            Value::Tuple(items) => {
                let inner: Vec<String> = items.iter().map(Value::py_repr).collect();
                // Python renders a 1-tuple as `(1,)`.
                if items.len() == 1 {
                    write!(f, "({},)", inner[0])
                } else {
                    write!(f, "({})", inner.join(", "))
                }
            }
            Value::Object(o) => match o.get("__str__") {
                // A model instance carries `__str__`; that keeps winning over
                // dict repr, which is how `{{ obj }}` renders a model.
                Some(Value::String(s)) => write!(f, "{s}"),
                _ => {
                    let inner: Vec<String> = o
                        .iter()
                        // Keys go through `py_repr` too (#2203 review): a hand-rolled
                        // escaper here missed the BACKSLASH, so a key like `a\`
                        // emitted `{'a\': 1}` where the closing quote reads as
                        // escaped. Two escapers, one wrong.
                        .map(|(k, v)| {
                            format!("{}: {}", Value::String(k.clone()).py_repr(), v.py_repr())
                        })
                        .collect();
                    write!(f, "{{{}}}", inner.join(", "))
                }
            },
        }
    }
}

impl<'py> FromPyObject<'_, 'py> for Value {
    // PyO3 0.29 reshaped FromPyObject: it now carries an associated `Error`
    // type and a single `extract(Borrowed<...>)` method (the old single-lifetime
    // `extract_bound(&Bound<...>)` was removed). `Borrowed` derefs to `Bound`,
    // so the body below is unchanged — method calls on `ob` auto-deref.
    type Error = PyErr;
    fn extract(ob: pyo3::Borrowed<'_, 'py, PyAny>) -> PyResult<Self> {
        if ob.is_none() {
            // Python `None` — NOT `Missing`. An absent key never reaches this
            // conversion; it arrives as `Option::None` from the resolver (#2203).
            Ok(Value::None)
        } else if let Ok(b) = ob.extract::<bool>() {
            Ok(Value::Bool(b))
        } else if let Ok(i) = ob.extract::<i64>() {
            Ok(Value::Integer(i))
        } else if is_decimal(&ob.to_owned()) {
            // BEFORE the f64 arm, and that ordering is the whole point (#2214).
            // `extract::<f64>()` goes through `PyFloat_AsDouble`, which honours
            // `Decimal.__float__`, so a Decimal placed after it is unreachable
            // — silently, because the arms have different types and neither
            // rustc nor clippy can see a dead if-else branch. That is exactly
            // how the `serialize_python_value` branch died.
            Ok(Value::Decimal(ob.str()?.extract::<String>()?))
        } else if let Ok(f) = ob.extract::<f64>() {
            Ok(Value::Float(f))
        } else if let Ok(s) = ob.extract::<String>() {
            Ok(Value::String(s))
        } else if let Ok(tuple) = ob.cast::<pyo3::types::PyTuple>() {
            // BEFORE the sequence arm: a tuple extracts as `Vec<Value>` too, so
            // checking after it would render every tuple as a list (#2203).
            let items: Vec<Value> = tuple.extract()?;
            Ok(Value::Tuple(items))
        } else if let Ok(list) = ob.extract::<Vec<Value>>() {
            Ok(Value::List(list))
        } else if let Some(map) = ob.cast::<PyDict>().ok().and_then(|d| {
            // Iterated by hand rather than `extract::<IndexMap<..>>()`, because
            // extraction is exactly where Python's insertion order would be
            // lost — and no later re-sort can recover it (#2203). PyDict
            // iteration yields entries in insertion order.
            //
            // Returns None (rather than propagating) when a key is not a
            // string, so the arm simply does not match and the conversion falls
            // through to the object handling below — precisely what the previous
            // `extract::<HashMap<String, Value>>()` did. Propagating instead
            // turned a context containing `{1: "a"}` from "renders something"
            // into a hard TypeError, which is a regression (#2203 self-review).
            let mut m = IndexMap::with_capacity(d.len());
            for (k, v) in d.iter() {
                m.insert(k.extract::<String>().ok()?, v.extract::<Value>().ok()?);
            }
            Some(m)
        }) {
            Ok(Value::Object(map))
        } else {
            // #1986: a djust sidecar proxy exposes `__djust_serialize__()`,
            // returning a DENYLIST-FILTERED dict (via the same eager serializer
            // the rest of djust uses). Route through it FIRST — otherwise the
            // `__dict__` bulk-dump below (which filters only `_`-prefixed keys)
            // would leak floor fields like `password` for any model converted
            // to a value (queryset items in a `{% for %}`, a terminal model).
            // Only djust proxies carry this method, so `update_state` ingestion
            // (plain dicts/primitives) is unaffected.
            if let Ok(serializer) = ob.getattr("__djust_serialize__") {
                if let Ok(result) = serializer.call0() {
                    // The hook returns a plain, denylist-filtered dict (model)
                    // or list-of-dicts (queryset) — recurse via Value so both
                    // shapes convert (Object / List). The result carries no
                    // proxies, so this does not re-enter this branch.
                    if let Ok(v) = result.extract::<Value>() {
                        return Ok(v);
                    }
                }
            }
            // #1986 (vector 7): a RAW Django model reaching Value conversion —
            // e.g. an element of a raw list/tuple/dict the getattr walk never
            // wrapped (`{% for x in presenter.items %}{{ x.password }}`) — must
            // ALSO route through the denylist serializer, NOT the `__dict__`
            // bulk-dump below (which filters only `_`-prefixed keys and so
            // leaks `password`). Detect a model via
            // `isinstance(django.db.models.Model)` and hand it to the same
            // `normalize_django_value` the eager path uses. `update_state`
            // ingestion passes pre-normalized dicts, so no raw model reaches
            // here on that path; the import is a cached sys.modules lookup.
            if let Ok(models_mod) = ob.py().import("django.db.models") {
                if let Ok(model_cls) = models_mod.getattr("Model") {
                    if ob.is_instance(&model_cls).unwrap_or(false) {
                        if let Ok(v) = ob
                            .py()
                            .import("djust.serialization")
                            .and_then(|m| m.getattr("normalize_django_value"))
                            .and_then(|f| f.call1((ob.to_owned(),)))
                            .and_then(|r| r.extract::<Value>())
                        {
                            return Ok(v);
                        }
                    }
                }
            }
            // For arbitrary Python objects (e.g. Django model instances), try to
            // extract public attributes from __dict__ so that template expressions
            // like `{{ obj.name }}` or `{{ obj.path }}` work without requiring
            // callers to manually convert to dicts.
            if let Ok(obj_dict) = ob.getattr("__dict__") {
                // Iterated as a PyDict, NOT via `extract::<HashMap<..>>()`
                // (#2203 review). A std `HashMap` randomises iteration order
                // PER INSTANCE, and a fresh one is built on every conversion —
                // so extracting through one made `{{ obj }}` reorder on every
                // render, not merely between restarts. That is the exact
                // non-determinism the PyDict arm above exists to avoid, and a
                // first pass reintroduced it sixty lines later.
                if let Ok(items) = obj_dict.cast::<PyDict>() {
                    let mut map: IndexMap<String, Value> = IndexMap::new();
                    for (k, v) in items.iter() {
                        let Ok(k) = k.extract::<String>() else {
                            continue;
                        };
                        // Skip private/dunder attrs and Django's internal _state
                        if k.starts_with('_') {
                            continue;
                        }
                        if let Ok(val) = v.extract::<Value>() {
                            map.insert(k, val);
                        }
                    }
                    if !map.is_empty() {
                        return Ok(Value::Object(map));
                    }
                }
            }
            Ok(Value::String(ob.str()?.to_string()))
        }
    }
}

/// Convert Value to Python object using the new IntoPyObject trait.
impl<'py> IntoPyObject<'py> for Value {
    type Target = PyAny;
    type Output = Bound<'py, Self::Target>;
    type Error = PyErr;

    fn into_pyobject(self, py: Python<'py>) -> std::result::Result<Self::Output, Self::Error> {
        match self {
            // Both map to Python `None`: `Missing` has no Python counterpart,
            // and round-tripping it as None matches the old `Null` behaviour.
            Value::Missing | Value::None => Ok(py.None().into_bound(py)),
            Value::Bool(b) => Ok(b.into_pyobject(py)?.to_owned().into_any()),
            Value::Integer(i) => Ok(i.into_pyobject(py)?.to_owned().into_any()),
            Value::Float(f) => Ok(f.into_pyobject(py)?.to_owned().into_any()),
            Value::String(s) => Ok(s.into_pyobject(py)?.to_owned().into_any()),
            // Back to a real `decimal.Decimal`, not a str: a value that made
            // the round-trip as a Decimal must come back as one, or handlers
            // reading it from the context see their type change under them.
            // Falls back to the string if `Decimal(s)` raises, which it should
            // not for a string we produced from a Decimal.
            Value::Decimal(d) => {
                let decimal_cls = py.import("decimal")?.getattr("Decimal")?;
                match decimal_cls.call1((d.as_str(),)) {
                    Ok(obj) => Ok(obj),
                    Err(_) => Ok(d.into_pyobject(py)?.to_owned().into_any()),
                }
            }
            Value::List(l) => {
                let py_list = PyList::empty(py);
                for item in l {
                    py_list.append(item.into_pyobject(py)?)?;
                }
                Ok(py_list.into_any())
            }
            Value::Tuple(t) => {
                // Round-trips back to a real Python tuple, so a tuple that
                // crosses into Rust and back does not silently become a list.
                let items: Vec<_> = t
                    .into_iter()
                    .map(|item| item.into_pyobject(py))
                    .collect::<std::result::Result<Vec<_>, _>>()?;
                Ok(pyo3::types::PyTuple::new(py, items)?.into_any())
            }
            Value::Object(o) => {
                let py_dict = PyDict::new(py);
                for (k, v) in o {
                    py_dict.set_item(k, v.into_pyobject(py)?)?;
                }
                Ok(py_dict.into_any())
            }
        }
    }
}

/// Convert &Value to Python object (clones the value).
impl<'py> IntoPyObject<'py> for &Value {
    type Target = PyAny;
    type Output = Bound<'py, Self::Target>;
    type Error = PyErr;

    fn into_pyobject(self, py: Python<'py>) -> std::result::Result<Self::Output, Self::Error> {
        self.clone().into_pyobject(py)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_value_truthy() {
        assert!(!Value::Missing.is_truthy());
        assert!(Value::Bool(true).is_truthy());
        assert!(!Value::Bool(false).is_truthy());
        assert!(Value::Integer(1).is_truthy());
        assert!(!Value::Integer(0).is_truthy());
        assert!(Value::String("hello".to_string()).is_truthy());
        assert!(!Value::String("".to_string()).is_truthy());
    }

    /// #968 — `Value::Object` with a `"__str__"` key renders that
    /// string, matching Django's default `{{ obj }}` semantics.
    /// Serialized Django-model dicts carry `"__str__": str(obj)` from
    /// `python/djust/serialization.py::_serialize_model_safely`; the
    /// Rust Display impl previously dropped it and emitted `[Object]`.
    #[test]
    fn test_display_object_with_str_key() {
        let mut map: IndexMap<String, Value> = IndexMap::new();
        map.insert("id".to_string(), Value::Integer(1));
        map.insert(
            "__str__".to_string(),
            Value::String("<Claim: 2026PD000075>".to_string()),
        );
        let obj = Value::Object(map);
        assert_eq!(obj.to_string(), "<Claim: 2026PD000075>");
    }

    /// Fallback: plain dicts without a `"__str__"` key keep rendering
    /// as `"[Object]"` — non-model data (e.g. a context dict passed
    /// directly from user code) was never meant to hit `__str__`
    /// semantics.
    #[test]
    fn test_display_object_without_str_key() {
        let mut map: IndexMap<String, Value> = IndexMap::new();
        map.insert("a".to_string(), Value::Integer(1));
        map.insert("b".to_string(), Value::Integer(2));
        let obj = Value::Object(map);
        // Was `"[Object]"`. Django renders `str({'a': 1, 'b': 2})` (#2203),
        // in insertion order — which is why `Object` is an IndexMap.
        assert_eq!(obj.to_string(), "{'a': 1, 'b': 2}");
    }

    /// Edge: `"__str__"` key present but not a `String` (e.g. an
    /// upstream bug produces `"__str__": null`). Fall back to
    /// `"[Object]"` rather than emit `null` or crash.
    #[test]
    fn test_display_object_str_key_non_string_falls_back() {
        let mut map: IndexMap<String, Value> = IndexMap::new();
        map.insert("__str__".to_string(), Value::Missing);
        let obj = Value::Object(map);
        // Falls back to dict repr rather than emitting the bad `__str__`.
        // The map has one entry, so this is the single-pair rendering.
        assert_eq!(obj.to_string(), "{'__str__': }");
    }

    /// Empty string `"__str__"` is still a valid override — Django
    /// template would render an empty string if `str(obj) == ""`,
    /// and the Rust engine must match.
    #[test]
    fn test_display_object_empty_str_key() {
        let mut map: IndexMap<String, Value> = IndexMap::new();
        map.insert("__str__".to_string(), Value::String("".to_string()));
        let obj = Value::Object(map);
        assert_eq!(obj.to_string(), "");
    }

    /// Regression-lock: bare `[List]` fallback for lists unchanged.
    #[test]
    fn test_display_list_renders_python_repr() {
        // Was `"[List]"` — a placeholder, not a rendering. Django renders
        // `str([1, 2])` (#2203).
        let list = Value::List(vec![Value::Integer(1), Value::Integer(2)]);
        assert_eq!(list.to_string(), "[1, 2]");
    }
}
