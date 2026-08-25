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
#[derive(Debug, Clone, Serialize)]
#[serde(untagged)]
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
                Ok(Value::Object(obj))
            }
        }

        deserializer.deserialize_any(ValueVisitor)
    }
}

impl Value {
    pub fn is_truthy(&self) -> bool {
        match self {
            Value::Missing => false,
            // Python `None` is falsy, same as an absent value.
            Value::None => false,
            Value::Bool(b) => *b,
            Value::Integer(i) => *i != 0,
            Value::Float(f) => *f != 0.0,
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
