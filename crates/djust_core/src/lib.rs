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
pub mod decimal;
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

/// Map key marking a [`Value::BigInt`] in a BINARY encoding (#2260).
///
/// Same mechanism, same deliberate ugliness, and DISTINCT from [`DECIMAL_TAG`]:
/// a big int that came back as a `Decimal` would leave the process as a
/// `decimal.Decimal`, which is the type change the variant exists to prevent.
pub(crate) const BIGINT_TAG: &str = "__djust_bigint__";

/// The `DECIMAL_TAG` value, for tests that must exercise near-misses against
/// the real constant rather than a copy of the literal.
///
/// `#[doc(hidden)]`: public only because the integration tests live outside the
/// crate. Not API.
#[doc(hidden)]
pub fn decimal_tag() -> &'static str {
    DECIMAL_TAG
}

/// The [`BIGINT_TAG`] value, for tests outside the crate. `#[doc(hidden)]`, not
/// API — same rationale as [`decimal_tag`].
#[doc(hidden)]
pub fn bigint_tag() -> &'static str {
    BIGINT_TAG
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
    /// A Python `int` too large for [`Value::Integer`], carried as its EXACT
    /// digit string (#2260).
    ///
    /// `Integer` is an `i64`; a Python `int` is arbitrary-precision. Past
    /// `2**63 - 1` the `i64` arm of `FromPyObject` fails and — before this
    /// variant — the next arm that matched was `extract::<f64>()`, so
    /// `12345678901234567890` reached the renderer as a binary double and
    /// `{{ p }}` printed `12345678901234567000`. Reachable from a `Sum()`
    /// aggregate, a nanosecond timestamp product, or an id from an external
    /// system.
    ///
    /// **Not `Value::Decimal`**, which carries an exact digit string already
    /// and would have cost nothing to reuse. Two things a `Decimal` does that
    /// an `int` must not: it renders `Decimal('123')` from [`Value::py_repr`]
    /// when nested in a list (Python renders `123`), and it converts back to a
    /// `decimal.Decimal` in [`IntoPyObject`], so a view attribute holding a big
    /// int would come back from the session round trip as a `Decimal` and stop
    /// being an `int` to every `isinstance` downstream. A separate variant
    /// costs six exhaustive `match` arms; sharing `Decimal` costs a type change
    /// that leaves the process.
    ///
    /// **Not a wider `Integer`** either. `i128` reaches 39 digits and stops;
    /// `1234567890123456789012345678901234567890` is 40 and is not exotic for a
    /// hash. A digit string has no ceiling, which is the property Python has.
    ///
    /// The invariant: `BigInt` holds `str(int)` — an optional `-` then ASCII
    /// digits, and a magnitude that does NOT fit an `i64` (a value that fits is
    /// always `Integer`, so the two variants never both spell one number).
    /// `as_f64()` parses it on demand, deliberately lossily, for exactly the
    /// reason `Decimal` does: arithmetic and comparison keep the behaviour they
    /// had when this value simply WAS a float. What changes is that the digits
    /// survive rendering and transport.
    BigInt(String),
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
            // A big int takes the same two-format split, for the same reason:
            // `SerializableViewState` round-trips through msgpack on every read
            // of the default state backend, and an untagged big int comes back
            // as `Value::String` — which renders the same but stops being an
            // `int` on the way back to Python and loses `{% if p > 10 %}`
            // (#2260).
            //
            // The JSON half stays a string, as `Decimal`'s does. Scope, checked
            // rather than assumed: this arm is reached only through
            // `serialization::to_json`/`from_json`, which no caller in this
            // workspace uses — the client-facing JSON is `json_script`'s own
            // `value_to_json`, which emits BARE digits (a `json.dumps(int)` is a
            // number). So the choice here is about the pair round-tripping
            // through one format, not about what a browser parses; a string is
            // what `from_json` can read back without a tag, and JSON has no way
            // to say "a number with more digits than a double" anyway.
            Value::BigInt(d) if !serializer.is_human_readable() => {
                let mut m = serializer.serialize_map(Some(1))?;
                m.serialize_entry(BIGINT_TAG, d)?;
                m.end()
            }
            // Everything else is exactly the untagged derive it replaces.
            Value::Decimal(d) => serializer.serialize_str(d),
            Value::BigInt(d) => serializer.serialize_str(d),
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
                    // The binary-format big-int tag (#2260), same shape and the
                    // same "exactly one key, that key, a string payload"
                    // discrimination — anything else is a real dict.
                    if let Some(Value::String(d)) = obj.get(BIGINT_TAG) {
                        return Ok(Value::BigInt(d.clone()));
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

/// The exact decimal digits of a Python `int`, or `None` if this is not one
/// (#2260).
///
/// Called only after `extract::<i64>()` has already failed, so a `Some` means
/// "an int too large for [`Value::Integer`]" and the [`Value::BigInt`]
/// invariant holds by construction.
///
/// NOT `ob.str()`. `bool` and `IntEnum` are `int` SUBCLASSES and may spell
/// themselves any way they like — `str(Color.RED)` is `Color.RED`, and a
/// subclass could stringify to something that is not digits at all, which would
/// then be parsed back as an `int` on the way out. `int(ob)` narrows to a plain
/// `int` first, so the digits are the value's, not its `__str__`'s. (`bool` is
/// claimed by the earlier arm and never reaches here; the point is that the
/// rule does not depend on that.)
///
/// Fails CLOSED, like [`is_decimal`]: on any error the answer is `None` and the
/// value takes its previous path — a conversion helper must not raise.
pub fn big_int_digits(ob: &Bound<'_, PyAny>) -> Option<String> {
    let py = ob.py();
    if !ob.is_instance_of::<pyo3::types::PyInt>() {
        return None;
    }
    let plain = py.get_type::<pyo3::types::PyInt>().call1((ob,)).ok()?;
    let digits = plain.str().ok()?.extract::<String>().ok()?;
    // Defence in depth: whatever produced this string, only `[-]digits` may
    // become a `BigInt`, because `Display` writes it back out verbatim.
    let body = digits.strip_prefix('-').unwrap_or(&digits);
    (!body.is_empty() && body.bytes().all(|b| b.is_ascii_digit())).then_some(digits)
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
    // The parse itself is `decimal::parse_decimal_parts` — lifted out of this
    // function in #2253 so `floatformat` uses the same definition of "is this a
    // decimal" rather than growing a second one (#1646). Its doc-comment
    // carries the two load-bearing rejections (an absent coefficient is not a
    // zero; letters with an exponent are not digits) and the saturating
    // exponent, all of which have pinning tests in
    // `crates/djust_core/tests/test_decimal_value_2214.rs`.
    let Some(parts) = crate::decimal::parse_decimal_parts(raw) else {
        return raw.to_string();
    };
    let sign = if parts.neg { "-" } else { "" };
    let digits = &parts.digits;
    let exponent = parts.exponent;

    // Django's cutoff (rule 2), on `as_tuple()`'s values, as Django computes it.
    if parts.over_django_digit_cutoff() {
        // `as_tuple().digits` drops LEADING zeros; `parts.digits` keeps them.
        // Counting those inflates the length and shifts the coefficient by one,
        // which diverged for EVERY `0.xxx` value near the cutoff until #2240 —
        // an ordinary shape (`Decimal(1)/Decimal(7)` under `prec=120`) that the
        // boundary test missed because all six of its cases had `1` as their
        // integer part (#1867). `significant()` is the shared definition the
        // cutoff above also uses, so the two cannot disagree about what a
        // digit is (#1646).
        let significant = parts.significant();
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
        // Saturating for the same reason `parse_decimal_parts` saturates.
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

    // Position of the decimal point within `digits`, after the exponent. Equal
    // to the pre-#2253 `int_part.len() + str_exp` by construction: `exponent`
    // already has the fractional length subtracted out of it.
    let point = digits.len() as i64 + exponent;
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
            // Same contract as `Decimal`: lossy on purpose. Before this variant
            // the value already WAS this double, so no comparison or arithmetic
            // changes answer; only rendering and transport gain the digits.
            Value::BigInt(d) => d.parse::<f64>().ok(),
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
            // Every `BigInt` is past `i64` by construction, so it is never zero;
            // written on the digits anyway rather than through a parse that
            // gives `inf` for a 400-digit value.
            Value::BigInt(d) => d.bytes().any(|b| b.is_ascii_digit() && b != b'0'),
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
            // `repr`, NOT `Display` (#2258). `str([1e20])` is `[1e+20]` while
            // `str(1e20)` is `100000000000000000000`: the bare render goes
            // through `numberformat.format`, but a NESTED float is spelled by
            // Python's list repr, which calls `repr` on the element. So the
            // delegation below — correct for every other variant — was the
            // third site of the same str/repr split the string-filter coercion
            // and `floatformat` already carry.
            Value::Float(f) => decimal::python_float_repr(*f),
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
            // it would make a rendering-parity flag silently lossy. Same for
            // `BigInt` and the #2260 loss.
            Value::Decimal(d) => write!(f, "{}", expand_decimal_exponent(d)),
            Value::BigInt(d) => write!(f, "{d}"),
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
                // Django's `{{ }}` path for a float is `numberformat.format`,
                // which is TWO steps, and this arm used to be neither of them
                // (#2258):
                //
                //     if isinstance(number, float) and "e" in str(number).lower():
                //         number = Decimal(str(number))
                //     if isinstance(number, Decimal):  <200-digit cutoff, else "{:f}">
                //     else:                            str_number = str(number)
                //
                // So the input to both steps is `str(float)` — CPython's `repr`
                // since 3.1, which is what `python_float_repr` is. Rust's `{}`
                // is not it: it never uses exponent notation and spells the
                // non-finite values `NaN`/`inf` where Python gives `nan`/`inf`.
                // The old `{:.1}` guard was a partial hand-port of the `.0` case
                // (#2203) that could not see either.
                //
                // Then the SECOND step is exactly `expand_decimal_exponent` —
                // the same >200-digit cutoff and the same `{:f}` expansion the
                // `Decimal` arm below uses, because Django reaches it by turning
                // the float INTO a Decimal. One definition, not two (#1646).
                // That is what makes `1e20` render `100000000000000000000` while
                // `1e300` renders `1e+300`: Django really does spell them
                // differently, on the digit count, not on the variant.
                //
                // Non-finite spellings hold no `e` and `expand_decimal_exponent`
                // rejects them, so `nan`/`inf`/`-inf` pass through verbatim.
                write!(
                    f,
                    "{}",
                    expand_decimal_exponent(&decimal::python_float_repr(*fl))
                )
            }
            // Django renders a number through `numberformat.format()`, which
            // uses `"{:f}".format(...)`, so an exponent-form Decimal expands:
            // `1E-9` renders `0.000000001`. NOT `str()` — see
            // `expand_decimal_exponent`.
            Value::Decimal(d) => write!(f, "{}", expand_decimal_exponent(d)),
            // `str(int)` is the digits, with no cutoff and no exponent form:
            // `numberformat.format` short-circuits an `int` before it reaches
            // either rule, and its non-grouping path is `str(number)` (#2260).
            Value::BigInt(d) => write!(f, "{d}"),
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
        } else if let Some(digits) = big_int_digits(&ob.to_owned()) {
            // BEFORE the f64 arm, and for the same reason the Decimal arm is
            // (#2260): `extract::<f64>()` succeeds on ANY Python `int`, so a
            // value past `i64` placed after it is unreachable and silently
            // becomes a double. Only reached when the `i64` arm above already
            // failed, so this is exactly "an int that does not fit".
            Ok(Value::BigInt(digits))
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
            // Back to a real Python `int`, not a `str` and not a `Decimal`
            // (#2260). This is the half of the variant that a shared
            // `Value::Decimal` could not have done: a handler that put an
            // `int` in the context must read an `int` back out of it, or every
            // `isinstance(x, int)` downstream of a state round trip changes
            // answer. Falls back to the digits as a string if `int(s)` raises,
            // which it cannot for a string this crate produced.
            Value::BigInt(d) => {
                let int_cls = py.get_type::<pyo3::types::PyInt>();
                match int_cls.call1((d.as_str(),)) {
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
