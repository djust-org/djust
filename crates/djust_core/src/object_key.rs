//! The key of a [`crate::Value::Object`] — a Python dict key, with its type
//! kept (#2339).
//!
//! # Why this type exists
//!
//! `Value::Object` was an `IndexMap<String, Value>`, so every key was a Rust
//! `String`. Two symptoms followed, and #2339 argued they pulled in opposite
//! directions and so needed a design decision rather than a patch:
//!
//! 1. `{% if 0 in d %}` matched a `"0"` key, because the `in` arm compared
//!    `map.contains_key(&needle.to_string())` — a gate opening on a
//!    coincidence of `Display` formatting.
//! 2. A dict with any non-string key was **not a mapping at all**: PyO3's
//!    extraction required string keys, so `{0: 1}` fell through to its `repr`
//!    and `{% for k in d %}` iterated that string one character at a time.
//!
//! The reason the obvious fix for (1) was written, measured and **reverted**
//! in PR #2341 was a premise about the wire format: that djust coerces every
//! dict key to a string, so the `to_string()` was the only thing keeping
//! `{% if pk in d %}` resolving against a view's own `{pk: …}` mapping.
//!
//! **That premise is false**, and measuring it is what unblocked this. The
//! render path has no JSON hop — `LiveView.render()` hands the live Python
//! dict to PyO3 directly — so an int-keyed dict was never string-keyed by the
//! time it got here; it was not a mapping at all, and `{% if pk in d %}`
//! answered `MISS` on it already. The coercion protected nothing; its only
//! effect was to make djust wrong for the string-keyed case. Once the key
//! carries its type both symptoms are the same one fix, and both answers
//! become Python's simultaneously.
//!
//! # Numeric keys are conflated, because Python conflates them
//!
//! `hash(1) == hash(1.0) == hash(True)` and `1 == 1.0 == True`, so
//! `{1: "a"}[True]` and `{1: "a"}[1.0]` both resolve in CPython. A key type
//! that compared by VARIANT would answer False there — a new divergence
//! bought by the fix, which is precisely the "a partial model is a new
//! divergence, not a fix" failure shape. So [`ObjectKey`] hashes and compares
//! numerics **by value** across `Int` / `Float` / `Bool` / `Decimal` /
//! `BigInt`, while keeping the variant for DISPLAY: `repr({True: 1})` is
//! `{True: 1}`, not `{1: 1}`.
//!
//! # `Hash` is hand-written so `map.get("name")` still works
//!
//! Every internal producer (the model serializer, context frames, VDOM props)
//! builds string-keyed maps and looks them up with a `&str` literal — 232
//! such lines at the time of writing. Rather than rewrite them, `Str` hashes
//! **exactly as its `str` does**, with no discriminant, and [`Equivalent`] is
//! implemented for `str` / `String`. `IndexMap::get("x")` therefore lands in
//! the same bucket and compares equal, unchanged. Non-string variants hash
//! under a discriminant tag, so they can never collide with a string.
//!
//! # The wire is still lossy, and says so
//!
//! [`ObjectKey`] serializes as its string form in **both** JSON and msgpack,
//! and deserializes back as [`ObjectKey::Str`]. That is not a shortcut:
//!
//! * JSON has no other kind of key. CPython's own `json.dumps({0: 1})` is
//!   `'{"0": 1}'`, so a stringifying encoder is the *faithful* one.
//! * msgpack **could** carry a typed key, and deliberately does not. Making
//!   it would mean the same view renders differently depending on which
//!   transport carried it — a parallel-path drift (CLAUDE.md #1646) bought
//!   for a shape no template can observe, since the render path never
//!   serializes at all.
//!
//! So a round trip through either encoding turns `{0: 1}` into `{"0": 1}`,
//! exactly as CPython's does. Pinned in
//! `python/tests/test_dict_keys_keep_their_type_2339.py::TestTheWireStillStringifies`
//! rather than left as a silent property.

use std::borrow::Cow;
use std::hash::{Hash, Hasher};

use serde::{Serialize, Serializer};

/// A discriminant byte mixed into the hash of every NON-string variant.
///
/// `Str` deliberately writes no tag at all — that is what makes its hash
/// identical to the underlying `str`'s and keeps `map.get("literal")`
/// working. Every other variant must therefore write something, or an
/// `Int(0)` could land in `"0"`'s bucket and the `Equivalent` comparison
/// would be the only thing standing between them.
const NON_STR_TAG: u8 = 0xE1;

/// The tag written for any key that compares numerically, so `Int(1)`,
/// `Float(1.0)`, `Bool(true)` and `Decimal("1")` share one bucket.
const NUM_TAG: u8 = 0xE2;

/// A Python dict key.
///
/// The variants mirror [`crate::Value`]'s scalar shapes, plus `Tuple` (a
/// Python tuple is hashable and so may be a key) and `Other`, which carries
/// an arbitrary hashable object's `repr()` so that a dict keyed by, say, a
/// model instance is still a MAPPING rather than collapsing to its own repr.
#[derive(Debug, Clone)]
pub enum ObjectKey {
    Str(String),
    Int(i64),
    Bool(bool),
    None,
    Float(f64),
    /// A Python `Decimal` key, as its exact digit string (mirrors
    /// [`crate::Value::Decimal`]).
    Decimal(String),
    /// A Python `int` too large for `i64`, as its exact digits (mirrors
    /// [`crate::Value::BigInt`]).
    BigInt(String),
    Tuple(Vec<ObjectKey>),
    /// Any other hashable key — a model instance, a `frozenset`, an enum
    /// member — so that such a dict is still a MAPPING rather than collapsing
    /// to its own repr.
    ///
    /// Carries BOTH forms because a template can reach both and they differ:
    /// `{% for k in d %}{{ k }}{% endfor %}` emits `str(key)` while `{{ d }}`
    /// shows `repr(key)`. Distinct from `Str` so `{'x': 1}` and an object
    /// whose `repr()` is `x` cannot collide, and so [`ObjectKey::py_repr`]
    /// does not add quotes a Python repr never had.
    Other {
        display: String,
        repr: String,
    },
}

/// The canonical numeric value of a key, when it has one.
///
/// `None` for `Str` / `Tuple` / `Other` / `NoneKey`, which never compare
/// numerically. Returned as an `f64` so a `Float` key participates; the
/// integral cases go through exactly, since every `i64` this can produce
/// came from a value small enough to have been one.
fn numeric(key: &ObjectKey) -> Option<f64> {
    match key {
        ObjectKey::Int(n) => Some(*n as f64),
        ObjectKey::Bool(b) => Some(if *b { 1.0 } else { 0.0 }),
        ObjectKey::Float(f) => Some(*f),
        // A Decimal or an oversized int only compares numerically when it
        // parses; a value that does not is compared structurally instead of
        // silently collapsing to 0.0.
        ObjectKey::Decimal(d) | ObjectKey::BigInt(d) => d.parse::<f64>().ok(),
        _ => None,
    }
}

impl ObjectKey {
    /// The key as a `str`, when it is one.
    ///
    /// Used by the places that genuinely need a string key — JSON
    /// serialization, and the `__str__` / tag lookups.
    pub fn as_str(&self) -> Option<&str> {
        match self {
            ObjectKey::Str(s) => Some(s.as_str()),
            _ => None,
        }
    }

    /// The key rendered the way `str()` renders it — what `{% for k in d %}`
    /// emits into the template.
    pub fn to_display_string(&self) -> Cow<'_, str> {
        match self {
            ObjectKey::Str(s) => Cow::Borrowed(s.as_str()),
            ObjectKey::Other { display, .. } => Cow::Borrowed(display.as_str()),
            _ => Cow::Owned(crate::Value::from(self.clone()).to_string()),
        }
    }

    /// The key rendered the way `repr()` renders it — what `{{ d }}` shows
    /// INSIDE the braces.
    pub fn py_repr(&self) -> String {
        match self {
            // NOT quoted: `Other` already holds a `repr()`.
            ObjectKey::Other { repr, .. } => repr.clone(),
            other => crate::Value::from(other.clone()).py_repr(),
        }
    }
}

/// A key back to a [`crate::Value`] — what `{% for k in d %}` binds, and what
/// `.items` / `.keys` yield.
impl From<ObjectKey> for crate::Value {
    fn from(key: ObjectKey) -> Self {
        match key {
            ObjectKey::Str(s) => crate::Value::String(s),
            ObjectKey::Int(n) => crate::Value::Integer(n),
            ObjectKey::Bool(b) => crate::Value::Bool(b),
            ObjectKey::None => crate::Value::None,
            ObjectKey::Float(f) => crate::Value::Float(f),
            ObjectKey::Decimal(d) => crate::Value::Decimal(d),
            ObjectKey::BigInt(d) => crate::Value::BigInt(d),
            ObjectKey::Tuple(items) => {
                crate::Value::Tuple(items.into_iter().map(crate::Value::from).collect())
            }
            // The DISPLAY form, because that is what `str()` of the original
            // object gave and what a bound loop variable must render as.
            ObjectKey::Other { display, .. } => crate::Value::String(display),
        }
    }
}

/// A [`crate::Value`] used as a lookup NEEDLE — `{% if x in d %}`.
///
/// Returns `None` for a value Python could not hash either (a list, a dict),
/// which is the honest answer: `[] in {}` raises `TypeError` there, and the
/// caller renders the `{% else %}` branch rather than matching something.
impl ObjectKey {
    pub fn from_value(value: &crate::Value) -> Option<ObjectKey> {
        Some(match value {
            crate::Value::String(s) => ObjectKey::Str(s.clone()),
            crate::Value::Integer(n) => ObjectKey::Int(*n),
            crate::Value::Bool(b) => ObjectKey::Bool(*b),
            crate::Value::None => ObjectKey::None,
            crate::Value::Float(f) => ObjectKey::Float(*f),
            crate::Value::Decimal(d) => ObjectKey::Decimal(d.clone()),
            crate::Value::BigInt(d) => ObjectKey::BigInt(d.clone()),
            crate::Value::Tuple(items) => ObjectKey::Tuple(
                items
                    .iter()
                    .map(ObjectKey::from_value)
                    .collect::<Option<_>>()?,
            ),
            // `Missing` is an ABSENT key, not a value: an unresolved variable
            // must miss the lookup, never match a key that happens to hold
            // the empty string.
            // A view is unhashable in Python too, so `d.keys() in other` raises
            // there and misses here (#2340).
            crate::Value::Missing
            | crate::Value::List(_)
            | crate::Value::Object(_)
            | crate::Value::DictView { .. } => return None,
        })
    }
}

/// What iterating a Python `dict` yields: its KEYS, each as the value it is.
///
/// **The one definition of that rule.** It had three copies within a day of
/// the key type landing — `Node::For`'s normalisation, `iter_values` (which
/// feeds `|length` / `|join` / `|unordered_list`) and `Context::dict_view`'s
/// `keys` arm — which is the parallel-path-drift shape (CLAUDE.md #1646) at
/// its very beginning. Converged before it could drift.
///
/// The distinction the `Value::from` makes is real but only observable
/// through a COMPARISON: `{% for k in d %}{{ k }}{% endfor %}` renders `0`
/// either way, and it is `{% if k == 0 %}` that tells an `Integer` key from
/// the text `"0"`. Measured — every `iter_values` consumer merely counts or
/// stringifies, so the gate-off mutation survived there until this
/// convergence gave all three sites one mechanism and one test to kill it.
pub fn dict_iteration_values(
    map: &indexmap::IndexMap<ObjectKey, crate::Value>,
) -> Vec<crate::Value> {
    map.keys().cloned().map(crate::Value::from).collect()
}

impl From<&str> for ObjectKey {
    fn from(s: &str) -> Self {
        ObjectKey::Str(s.to_string())
    }
}

impl From<String> for ObjectKey {
    fn from(s: String) -> Self {
        ObjectKey::Str(s)
    }
}

impl From<&String> for ObjectKey {
    fn from(s: &String) -> Self {
        ObjectKey::Str(s.clone())
    }
}

impl std::fmt::Display for ObjectKey {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.to_display_string())
    }
}

impl PartialEq for ObjectKey {
    fn eq(&self, other: &Self) -> bool {
        // Numeric-first, so `Int(1) == Float(1.0) == Bool(true)` the way
        // Python's dict lookup does. Both sides must be numeric; a `Str` is
        // never equal to a number even when it looks like one, which is
        // symptom 1 of #2339.
        match (numeric(self), numeric(other)) {
            (Some(a), Some(b)) => a == b,
            (Some(_), None) | (None, Some(_)) => false,
            (None, None) => match (self, other) {
                (ObjectKey::Str(a), ObjectKey::Str(b)) => a == b,
                (ObjectKey::None, ObjectKey::None) => true,
                // By `repr`, which is the more discriminating of the two: two
                // distinct objects can share a `str()`.
                (ObjectKey::Other { repr: a, .. }, ObjectKey::Other { repr: b, .. }) => a == b,
                (ObjectKey::Tuple(a), ObjectKey::Tuple(b)) => a == b,
                // A Decimal/BigInt whose digits do not parse falls here.
                (ObjectKey::Decimal(a), ObjectKey::Decimal(b)) => a == b,
                (ObjectKey::BigInt(a), ObjectKey::BigInt(b)) => a == b,
                _ => false,
            },
        }
    }
}

impl Eq for ObjectKey {}

impl ObjectKey {
    /// `str(type(key))` — CPython's tie-break for two keys that cannot be
    /// ordered against each other.
    fn type_name(&self) -> &'static str {
        match self {
            ObjectKey::Str(_) => "<class 'str'>",
            ObjectKey::Int(_) | ObjectKey::BigInt(_) => "<class 'int'>",
            ObjectKey::Bool(_) => "<class 'bool'>",
            ObjectKey::None => "<class 'NoneType'>",
            ObjectKey::Float(_) => "<class 'float'>",
            ObjectKey::Decimal(_) => "<class 'decimal.Decimal'>",
            ObjectKey::Tuple(_) => "<class 'tuple'>",
            ObjectKey::Other { .. } => "<class 'object'>",
        }
    }
}

/// Ordering for the one caller that needs it: `pprint`, which sorts a dict's
/// keys.
///
/// Mirrors CPython's `pprint._safe_key`, measured rather than assumed:
///
/// * Mutually orderable keys compare by VALUE. Every numeric kind is mutually
///   orderable in Python (`Decimal(1) < 2.0 < True` all work), so they form
///   one group — `pformat({True: 1, 0: 2, 1.5: 3})` is `{0: 2, True: 1, 1.5: 3}`.
/// * Otherwise CPython falls back to `(str(type(obj)), id(obj))`. The
///   type-name half is reproduced; the `id()` half deliberately is NOT,
///   because it is a memory address — CPython's own output is not
///   reproducible across runs there, so matching it is impossible and
///   copying it would only make djust nondeterministic too. Equal type names
///   fall back to the key's `repr`, which is stable.
impl Ord for ObjectKey {
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        if let (Some(a), Some(b)) = (numeric(self), numeric(other)) {
            return a.total_cmp(&b);
        }
        match (self, other) {
            (ObjectKey::Str(a), ObjectKey::Str(b)) => a.cmp(b),
            (ObjectKey::Tuple(a), ObjectKey::Tuple(b)) => a.cmp(b),
            (ObjectKey::None, ObjectKey::None) => std::cmp::Ordering::Equal,
            _ => self
                .type_name()
                .cmp(other.type_name())
                .then_with(|| self.py_repr().cmp(&other.py_repr())),
        }
    }
}

impl PartialOrd for ObjectKey {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}

impl Hash for ObjectKey {
    fn hash<H: Hasher>(&self, state: &mut H) {
        // Every numeric key hashes under one tag and one canonical form, so
        // the `Int(1) == Float(1.0)` equality above cannot put two equal keys
        // in different buckets — the bug that makes a HashMap silently lose
        // entries.
        if let Some(n) = numeric(self) {
            state.write_u8(NUM_TAG);
            // An integral value hashes as its integer form so `1.0` and `1`
            // agree; a non-integral one hashes its bits.
            if n.fract() == 0.0 && n.is_finite() && n >= i64::MIN as f64 && n <= i64::MAX as f64 {
                state.write_u8(0);
                (n as i64).hash(state);
            } else {
                state.write_u8(1);
                n.to_bits().hash(state);
            }
            return;
        }
        match self {
            // NO tag — this is what makes `map.get("x")` find the bucket that
            // `ObjectKey::Str("x")` was inserted into. See the module docs.
            ObjectKey::Str(s) => s.hash(state),
            ObjectKey::None => {
                state.write_u8(NON_STR_TAG);
                state.write_u8(2);
            }
            ObjectKey::Other { repr, .. } => {
                state.write_u8(NON_STR_TAG);
                state.write_u8(3);
                repr.hash(state);
            }
            ObjectKey::Tuple(items) => {
                state.write_u8(NON_STR_TAG);
                state.write_u8(4);
                items.hash(state);
            }
            ObjectKey::Decimal(d) => {
                state.write_u8(NON_STR_TAG);
                state.write_u8(5);
                d.hash(state);
            }
            ObjectKey::BigInt(d) => {
                state.write_u8(NON_STR_TAG);
                state.write_u8(6);
                d.hash(state);
            }
            // Unreachable: every remaining variant is numeric and returned
            // above. Written out rather than `unreachable!()` so a future
            // variant cannot panic in a renderer.
            ObjectKey::Int(_) | ObjectKey::Bool(_) | ObjectKey::Float(_) => {
                state.write_u8(NON_STR_TAG);
                state.write_u8(7);
            }
        }
    }
}

// `IndexMap::get(&Q)` needs `Q: Hash + Equivalent<K>`. With the `Hash` impl
// above matching `str`'s for the `Str` variant, these two make every existing
// `map.get("literal")` / `map.contains_key("literal")` call site compile and
// behave exactly as it did.
impl indexmap::Equivalent<ObjectKey> for str {
    fn equivalent(&self, key: &ObjectKey) -> bool {
        matches!(key, ObjectKey::Str(s) if s.as_str() == self)
    }
}

impl indexmap::Equivalent<ObjectKey> for String {
    fn equivalent(&self, key: &ObjectKey) -> bool {
        matches!(key, ObjectKey::Str(s) if s == self)
    }
}

/// Serializes as the key's STRING form, in every format.
///
/// See the module docs: JSON cannot express another kind of key, and msgpack
/// deliberately matches it rather than letting the transport decide what a
/// template renders.
impl Serialize for ObjectKey {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.serialize_str(&self.to_display_string())
    }
}

impl<'de> serde::Deserialize<'de> for ObjectKey {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        // A string, because that is the only thing the serializer above ever
        // writes. Accepting more here would make the codec asymmetric.
        String::deserialize(deserializer).map(ObjectKey::Str)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use indexmap::IndexMap;

    #[test]
    fn a_str_key_hashes_exactly_as_its_str_does() {
        // The property the whole "232 call sites keep compiling" argument
        // rests on. Asserted rather than assumed: if it broke, every
        // `map.get("literal")` would silently miss.
        use std::collections::hash_map::DefaultHasher;
        let h = |x: &dyn Fn(&mut DefaultHasher)| {
            let mut s = DefaultHasher::new();
            x(&mut s);
            s.finish()
        };
        for s in ["", "a", "items", "__str__", "0"] {
            let as_key = h(&|st| ObjectKey::Str(s.to_string()).hash(st));
            let as_str = h(&|st| s.hash(st));
            assert_eq!(as_key, as_str, "{s:?}");
        }
    }

    #[test]
    fn a_string_literal_lookup_still_finds_a_str_key() {
        let mut m: IndexMap<ObjectKey, u8> = IndexMap::new();
        m.insert(ObjectKey::Str("name".into()), 1);
        assert_eq!(m.get("name"), Some(&1));
        assert!(m.contains_key("name"));
        assert_eq!(m.get("other"), None);
    }

    #[test]
    fn an_int_key_is_not_the_string_that_prints_the_same() {
        // Symptom 1 of #2339, at the type level.
        let mut m: IndexMap<ObjectKey, u8> = IndexMap::new();
        m.insert(ObjectKey::Str("0".into()), 1);
        assert_eq!(m.get(&ObjectKey::Int(0)), None);
        assert_eq!(m.get(&ObjectKey::Str("0".into())), Some(&1));
    }

    #[test]
    fn numeric_keys_conflate_the_way_python_conflates_them() {
        let mut m: IndexMap<ObjectKey, u8> = IndexMap::new();
        m.insert(ObjectKey::Int(1), 7);
        for probe in [
            ObjectKey::Int(1),
            ObjectKey::Float(1.0),
            ObjectKey::Bool(true),
            ObjectKey::Decimal("1".into()),
        ] {
            assert_eq!(m.get(&probe), Some(&7), "{probe:?}");
        }
        assert_eq!(m.get(&ObjectKey::Float(1.5)), None);
        assert_eq!(m.get(&ObjectKey::Str("1".into())), None);
    }

    #[test]
    fn equal_keys_hash_equal() {
        // The invariant a hand-written Hash can break silently: two keys that
        // compare equal but hash differently make the map lose entries.
        use std::collections::hash_map::DefaultHasher;
        let hash = |k: &ObjectKey| {
            let mut s = DefaultHasher::new();
            k.hash(&mut s);
            s.finish()
        };
        let keys = [
            ObjectKey::Str("1".into()),
            ObjectKey::Int(1),
            ObjectKey::Float(1.0),
            ObjectKey::Bool(true),
            ObjectKey::Bool(false),
            ObjectKey::Int(0),
            ObjectKey::None,
            ObjectKey::Decimal("1".into()),
            ObjectKey::BigInt("1".into()),
            ObjectKey::Other {
                display: "obj".into(),
                repr: "<obj>".into(),
            },
            ObjectKey::Tuple(vec![ObjectKey::Int(1)]),
            ObjectKey::Float(1.5),
        ];
        for a in &keys {
            for b in &keys {
                if a == b {
                    assert_eq!(hash(a), hash(b), "{a:?} == {b:?} but hashes differ");
                }
            }
        }
    }

    #[test]
    fn none_and_false_and_zero_are_pythons_three_answers() {
        // `None` is not falsy-numeric: `{None: 1}[0]` raises in Python.
        assert_ne!(ObjectKey::None, ObjectKey::Int(0));
        assert_ne!(ObjectKey::None, ObjectKey::Bool(false));
        // But `False == 0` there, and here.
        assert_eq!(ObjectKey::Bool(false), ObjectKey::Int(0));
    }

    #[test]
    fn other_is_not_a_str_that_reads_the_same() {
        assert_ne!(
            ObjectKey::Other {
                display: "x".into(),
                repr: "x".into()
            },
            ObjectKey::Str("x".into())
        );
    }
}
