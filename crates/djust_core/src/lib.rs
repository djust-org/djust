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
pub mod object_key;
pub mod serialization;

pub use context::Context;
pub use errors::{DjangoRustError, Result};
pub use object_key::ObjectKey;

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

/// Marks a `Tuple` in a BINARY encoding (#2276).
///
/// Third instance of the same mechanism, and the one whose absence was least
/// visible: rendering a tuple was always correct — `Value::Tuple` is reachable
/// and `{{ p }}` gives `(1, 2)` exactly as Django does — so the issue's claim
/// that the variant is unreachable is false. What was lost is the ROUND TRIP:
/// msgpack has no tuple, `Tuple` serialized as an array, and a view attribute
/// came back a `list`, so `(1, 2)` rendered `[1, 2]` after a reconnect and not
/// before one.
///
/// Note the asymmetry with JSON, which is deliberate and not a gap: `json.dumps`
/// has no tuple either, and Django's own `DjangoJSONEncoder` emits `[1.0]` for
/// `(1.0,)` — verified. So the human-readable arm matching Django means staying
/// an array, and only the binary arm needs the tag.
pub(crate) const TUPLE_TAG: &str = "__djust_tuple__";
const NAMED_TUPLE_TAG: &str = "__djust_named_tuple__";

/// Marks a [`Value::Encoded`] in a BINARY encoding (#2448).
///
/// Fourth instance of the mechanism [`DECIMAL_TAG`] documents, and it exists for
/// the same measured reason: `SerializableViewState.state` round-trips through
/// msgpack on EVERY read of the default `InMemoryStateBackend`, so an untagged
/// `Encoded` would come back as a `Value::String` holding the DISPLAY spelling
/// and `{{ p|json_script:"d" }}` would emit `"2020-01-01 03:04:05"` again after
/// one cache hit — the exact defect the variant closes, reopened by the state
/// backend. The `Decimal` version of this was shipped once and caught by a
/// gate-off (#2135); it is not being shipped twice.
///
/// The payload is a LIST, not a string, which is also what keeps it from
/// colliding with [`DECIMAL_TAG`] / [`BIGINT_TAG`] (string payloads) and
/// [`TUPLE_TAG`] (a list, but under a different key).
///
/// It is `[type_name, display, json, truthy]` since #2458 and was
/// `[type_name, display, json]` in #2448. BOTH are read, because state written
/// by a #2448-era process outlives it: `SerializableViewState` is what a Redis
/// state backend holds across a rolling deploy, so a three-element payload is a
/// live input on the first request after an upgrade — not a hypothetical. It
/// deserializes to the pre-#2458 truthiness (`!display.is_empty()`) rather than
/// guessing, which is exactly the value that entry had when it was written.
pub(crate) const ENCODED_TAG: &str = "__djust_encoded__";

/// Marks a [`Value::Missing`] in a BINARY encoding (#2484).
///
/// Fifth instance of the mechanism [`DECIMAL_TAG`] documents — and the only one
/// that had to choose WHICH of two variants gets the new spelling, because
/// unlike the four above it is not giving a name to a value that had none. It
/// is separating two values that shared one.
///
/// # The defect
///
/// [`Value::Missing`] and [`Value::None`] are deliberately DISTINCT (#2203):
/// `Missing` renders `""` as Django's `string_if_invalid` does, `None` renders
/// `"None"` as `str(None)` does. Both serialized as msgpack `nil` and every
/// `nil` read back as `Missing`, so a `None` anywhere in
/// `SerializableViewState.state` — top level, in a dict, in a list, in an
/// `Encoded`'s attribute map — rendered Django's `"None"` on the first render
/// and the EMPTY STRING after one cache hit. Measured over Django's live
/// `defaultfilters` registry: 35 of 58 `{{ p|f }}` cells with `p = None` agreed
/// with Django before one round trip and stopped agreeing after it, including
/// `{{ p|default_if_none:"D" }}` (`"D"` → `""`) and `{{ p|yesno:"y,n,m" }}`
/// (`"m"` → `"n"`) — the two filters that exist to branch on exactly this.
///
/// # Why the tag is on `Missing` and not on `None`
///
/// This is the whole decision, and it is a compatibility one rather than an
/// aesthetic one. A state blob outlives a deploy: `SerializableViewState` is
/// what a Redis state backend holds, so during a rolling upgrade an OLD reader
/// reads bytes a NEW writer wrote, and a NEW reader reads bytes an OLD writer
/// wrote. Both directions have to be stated.
///
/// Tagging `None` — the obvious fifth application — changes the encoding of the
/// most common value in any state blob, and an old reader would see a one-key
/// `Value::Object` where it used to see `nil`: `{{ p }}` would render a dict
/// spelling and `{% if p %}` would take the TRUE branch, because a one-key map
/// is truthy. That is strictly worse than the defect it fixes.
///
/// Tagging `Missing` instead leaves `None` as the bare `nil` it already was:
///
/// * **new payload, OLD reader** — a `None` is still one `nil` byte, which an
///   old reader still turns into `Missing` and still renders `""`. Exactly
///   today's behaviour; the upgrade introduces no new failure. The one new
///   spelling on the wire is a tagged `Missing`, which an old reader would
///   render as a one-key dict — but see below: no real path can produce one.
/// * **OLD payload, new reader** — a `nil` written by any pre-fix build reads
///   as `Value::None` and renders `"None"`. That is not merely tolerated, it is
///   CORRECT: `FromPyObject` maps Python `None` to `Value::None` and has no
///   arm producing `Missing`, so Python `None` is the only thing that can have
///   put a `nil` in a state blob. A stale blob is fixed by the upgrade rather
///   than misread by it.
///
/// # Why a tag at all, rather than just reading `nil` as `None`
///
/// Because `Missing` cannot reach this serializer TODAY, not because it cannot
/// ever. It is a render-time sentinel — `renderer.rs`'s
/// `resolve(...)?.unwrap_or(Value::Missing)` — and `RustLiveView::state` is
/// filled only through the `FromPyObject` conversion, which never yields one
/// (`a_missing_cannot_enter_state_through_the_python_conversion` measures
/// that rather than asserting it). Dropping the distinction on the wire would
/// make the codec lossy in the other direction and silently reopen this defect
/// with the opposite sign the first time a `Missing` did become reachable. The
/// tag costs 21 bytes on a value no real path emits and makes the codec
/// injective.
///
/// The payload is `nil` — which is what keeps it from colliding with
/// [`DECIMAL_TAG`] / [`BIGINT_TAG`] (string payloads) and [`TUPLE_TAG`] /
/// [`ENCODED_TAG`] (list payloads). Same deliberate ugliness as the four
/// above: a user dict of exactly this one key with a `None` value would be
/// misread, and the name is chosen to make that a thing you have to try to do.
///
/// JSON is unchanged and stays lossy, exactly as [`TUPLE_TAG`]'s arm does:
/// `json.dumps` has one `null` too, so the human-readable spelling matching
/// Django means both variants stay `null`.
pub(crate) const MISSING_TAG: &str = "__djust_missing__";

/// The [`MISSING_TAG`] value, for tests outside the crate. `#[doc(hidden)]`,
/// not API — same rationale as [`decimal_tag`].
#[doc(hidden)]
pub fn missing_tag() -> &'static str {
    MISSING_TAG
}

/// The [`ENCODED_TAG`] value, for tests outside the crate. `#[doc(hidden)]`, not
/// API — same rationale as [`decimal_tag`].
#[doc(hidden)]
pub fn encoded_tag() -> &'static str {
    ENCODED_TAG
}

/// The [`TUPLE_TAG`] value, for tests outside the crate. `#[doc(hidden)]`, not API.
#[doc(hidden)]
pub fn tuple_tag() -> &'static str {
    TUPLE_TAG
}

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

/// Which of Python's three dict views a [`Value::DictView`] is (#2340).
///
/// The kind decides only the CONTAINER's name in `str()` — `dict_items([…])`
/// vs `dict_keys([…])` vs `dict_values([…])`. Every other behaviour is shared,
/// which is why this is a field rather than three variants.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DictViewKind {
    Items,
    Keys,
    Values,
}

impl DictViewKind {
    /// The name Python's `repr` gives the container.
    pub fn container_name(self) -> &'static str {
        match self {
            DictViewKind::Items => "dict_items",
            DictViewKind::Keys => "dict_keys",
            DictViewKind::Values => "dict_values",
        }
    }
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
    ///
    /// In a BINARY encoding it is a one-key tagged map under `MISSING_TAG`,
    /// not a `nil` — see that constant for why the tag went on THIS variant
    /// and not on [`Value::None`] (#2484).
    Missing,
    /// Python `None`. Renders as `"None"`, as `str(None)` does (#2203).
    ///
    /// Encodes as a bare `nil` in every format, which is what it has always
    /// encoded as — and keeping it that way is the whole of #2484's
    /// rolling-deploy compatibility argument.
    None,
    Bool(bool),
    Integer(i64),
    Float(f64),
    String(String),
    /// Runtime SafeData string. Serialized as plain text; wire data cannot mint safety.
    SafeString(String),
    List(Vec<Value>),
    /// A Python tuple. Separate from `List` only so it can render with
    /// parentheses, which `str()` distinguishes (#2203).
    Tuple(Vec<Value>),
    /// A tuple with named fields, such as Django's GroupedResult.
    NamedTuple {
        name: String,
        fields: Vec<String>,
        items: Vec<Value>,
    },
    /// Insertion-ordered, NOT a `HashMap`: Rust randomises `HashMap` iteration
    /// per process, so dict repr would differ between renders of the same
    /// template. Python dicts are insertion-ordered (#2203).
    ///
    /// The key is an [`ObjectKey`], not a `String`, so a dict keyed by
    /// anything else is still a MAPPING and `{% if 0 in d %}` cannot match a
    /// `"0"` key (#2339). `ObjectKey::Str` hashes exactly as its `str` does,
    /// so every `map.get("literal")` call site is unchanged — see that
    /// module's docs for why, and for what the wire format still loses.
    Object(IndexMap<ObjectKey, Value>),
    /// A live `dict_items` / `dict_keys` / `dict_values` view (#2340).
    ///
    /// #2334 made `d.items` resolve, to a plain `Value::List`. Everything a
    /// template usually does with one was then exact — iteration, unpacking,
    /// `|length`, `|join`, truthiness, `{% with %}` — and two observable
    /// properties of a real view were not: `str()` read `[…]` rather than
    /// `dict_items([…])`, and it was subscriptable where Python's raises.
    ///
    /// **This is NOT merely a `List` that prints differently.** Django's
    /// behaviour was measured across every one of its built-in filters,
    /// against all three kinds, and it splits three ways rather than two:
    ///
    /// * **sequence-like** — `{% for %}`, `in`, truthiness, `|length`,
    ///   `|join`, `|unordered_list`, `|safeseq`, `|escapeseq`;
    /// * **raises** — `|first`, `|last`, `|random`, `|json_script` (a view is
    ///   not subscriptable and not JSON-serializable). djust renders NOTHING
    ///   there rather than raising, which is the shape #2325's differential
    ///   already classifies and accepts, and is never more permissive;
    /// * **its `str()`** — and this is the third of the registry the issue's
    ///   list missed entirely. `|truncatewords`, `|wordcount`, `|linebreaks`,
    ///   `|stringformat`, `|striptags`, `|pprint`, `|escape`, `|safe`,
    ///   `|yesno` and `|make_list` all operate on the text
    ///   `"dict_keys([…])"`, so the repr is not cosmetic — it is their input.
    ///
    /// `|slice` is the case the issue got backwards: Django's `slice` CATCHES
    /// the `TypeError` and returns the value unchanged, so
    /// `{{ d.keys|slice:':1' }}` renders the whole view and
    /// `{{ d.keys|slice:':1'|join:'' }}` is still every key. Modelling it as
    /// "returns nothing" would have been a new divergence.
    ///
    /// Only ever built by `Context::dict_view` during a render; it never
    /// arrives from Python and never comes back off the wire.
    DictView {
        kind: DictViewKind,
        items: Vec<Value>,
    },
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
    /// A Python value whose `DjangoJSONEncoder` spelling is NOT its `str()`
    /// (#2448) — today, the four `datetime` types.
    ///
    /// `django.utils.html.json_script` is `json.dumps(value, cls=DjangoJSONEncoder)`,
    /// and that encoder's `default()` is not `str()`. Measured against live
    /// Django rather than transcribed:
    ///
    /// | value                          | `str()`                  | encoder                 |
    /// |--------------------------------|--------------------------|-------------------------|
    /// | `datetime(2020,1,1,3,4,5)`     | `2020-01-01 03:04:05`    | `2020-01-01T03:04:05`   |
    /// | `datetime(…, µs=123456)`       | `…03:04:05.123456`       | `…03:04:05.123`         |
    /// | `datetime(…, tzinfo=utc)`      | `…03:04:05+00:00`        | `…03:04:05Z`            |
    /// | `time(3,4,5,123456)`           | `03:04:05.123456`        | `03:04:05.123`          |
    /// | `timedelta(seconds=90)`        | `0:01:30`                | `P0DT00H01M30S`         |
    /// | `timedelta(seconds=-90)`       | `-1 day, 23:58:30`       | `-P0DT00H01M30S`        |
    ///
    /// The issue reporting this tabulated `time` as AGREEING. It agrees only at
    /// `microsecond == 0`, which is the band the report sampled — the same
    /// coincidence-in-the-sampled-band shape as #2425's float keys, one axis
    /// over. `date` is the only member that agrees for every value, and it is
    /// carried here anyway so the family is a type SET rather than a list of
    /// the members that happened to diverge.
    ///
    /// **Why a variant and not a fix in the filter.** `FromPyObject for Value`
    /// used to land a `datetime` on its final `Ok(Value::String(ob.str()?))`
    /// fallback, so by the time `json_script` ran the value was a string
    /// carrying the TEMPLATE DISPLAY spelling and the Python type was gone.
    /// That erasure is what makes #2429's refusal question undecidable in the
    /// value position — for every value `json.dumps` refuses, djust's output is
    /// byte-identical to its output for a serialisable stand-in. It does NOT
    /// block this one: the type is plainly visible at the conversion, which is
    /// where the `Decimal` arm above already reads it, so the fix is to stop
    /// discarding it rather than to reconstruct it downstream.
    ///
    /// **Both spellings, because both are needed.** `display` is `str(o)` and is
    /// what `{{ p }}` renders — unchanged by this variant, deliberately: djust's
    /// bare-render spelling of a datetime already diverges from Django's
    /// (Django localizes, `Jan. 1, 2020, 3:04 a.m.`), and moving it here would
    /// be a second, unrelated behaviour change riding a JSON fix.
    ///
    /// `type_name` is CPython's `tp_name` — what it writes into
    /// `'X' object is not iterable` — measured, not derived: it is
    /// `datetime.datetime` (a C type carries its dotted name) where
    /// `type(o).__name__` would say `datetime` and `uuid.UUID`'s is the bare
    /// `UUID`. It is what lets the refusal filters (#2449) name the type.
    ///
    /// `truthy` is `bool(o)` — Python's own answer for the object, asked at the
    /// conversion (#2458). #2448 shipped without it and read
    /// `!display.is_empty()` instead, which is "always true" for this family
    /// and so kept the pre-#2448 answer for the one member that is falsy:
    /// `bool(timedelta(0))` is `False` in Python and was `True` here. The
    /// alternative — reading it back off `json == "P0DT00H00M00S"` or off
    /// `display == "0:00:00"` — answers a truthiness question with a string
    /// comparison, cannot see a subclass that overrides `__bool__`, and (for
    /// the display spelling) is indistinguishable from the perfectly ordinary
    /// Python-TRUTHY `str` `"0:00:00"`. Carrying the bit is exact for every
    /// member, present and future.
    Encoded(Box<Encoded>),
}

/// The payload of [`Value::Encoded`] (#2448). Boxed there to keep `Value`'s
/// size unchanged — a `Value` is cloned per context entry per render.
#[derive(Debug, Clone)]
pub struct Encoded {
    /// CPython's `tp_name` for the type, as it appears in a `TypeError`
    /// message: `datetime.datetime`, `datetime.date`, `datetime.time`,
    /// `datetime.timedelta`.
    pub type_name: String,
    /// `str(o)` — what `{{ p }}` renders, and what this value looked like
    /// before the variant existed.
    pub display: String,
    /// Runtime safety of str(o), not of o itself. Never restored from wire data.
    pub display_safe: bool,
    /// `DjangoJSONEncoder.default(o)` — the string `json.dumps` writes.
    pub json: String,
    /// `bool(o)` — Python's own truthiness for the object (#2458). See the
    /// [`Value::Encoded`] doc for why this is carried rather than derived.
    pub truthy: bool,
    /// `len(o)`, or `None` where Python RAISES — asked at the conversion
    /// (#2466, widened to a count in #2477/#2489). `None` for every datetime
    /// member: `len(timedelta(0))` raises.
    ///
    /// It was a `sized_empty: bool` until #2477/#2489, and one bit really was
    /// all that was decidable then: [`opaque_value`]'s predecessor declined
    /// every object whose `len` was non-zero, so `Some(0)` and `None` were the
    /// only reachable answers. Enumerating the items made `Some(n)` reachable,
    /// and a bit cannot carry it — the two questions
    ///
    /// ```text
    /// {{ p|length }}   over a non-empty set        Django 1   (len(o))
    /// {{ p|length }}   over a falsy __iter__ class Django 0   (len RAISES,
    ///                                                          and Django's
    ///                                                          `length` filter
    ///                                                          catches it)
    /// ```
    ///
    /// have the same `!sized_empty` answer and different `len` answers. Both
    /// carried objects are iterable with one item, so [`Encoded::items`]
    /// cannot decide it either: "has a `__len__`" is a fact of its own.
    ///
    /// Django's `ForNode` reads `len` when the object has one and calls
    /// `list()` only when it does not:
    ///
    /// ```text
    /// {% for x in set() %}      len 0        -> the {% empty %} branch
    /// {% for x in complex(0) %} no __len__   -> TypeError, not iterable
    /// ```
    pub len: Option<usize>,
    /// `iter(o)` succeeds — asked at the conversion, and a DIFFERENT question
    /// from [`Encoded::len`] (#2466).
    ///
    /// Django asks both, in different places, and gets different answers for
    /// the same object:
    ///
    /// * `ForNode.render` reads `__len__` when the object has one and calls
    ///   `list()` only when it does not — so `{% for %}` over a class with a
    ///   zero `__len__` and NO `__iter__` renders the `{% empty %}` block;
    /// * `join` / `safeseq` / `escapeseq` / `unordered_list` are
    ///   comprehensions, so they call `iter()` on the same object and RAISE.
    ///
    /// One bit answering both would have to be wrong for one of them. This is
    /// the filters' half — `filters::iter_values` reads it — and [`Encoded::len`]
    /// is `{% for %}`'s.
    ///
    /// `iter(o)` is safe to ask: it builds an iterator and consumes nothing,
    /// so a generator is not advanced by the question. Enumerating one MAY
    /// consume it, which is why [`opaque_value`] enumerates only a RE-iterable
    /// object (`iter(o) is not o`) and declines the one-shot case outright —
    /// see [`Encoded::items`].
    pub iterable: bool,
    /// `repr(o)` — Python's own constructor spelling (#2472).
    ///
    /// Carried rather than derived, for the reason `truthy` is: `repr` for this
    /// family is **not** a format string. `repr(timedelta(0))` is
    /// `datetime.timedelta(0)` while `repr(timedelta(seconds=90))` is
    /// `datetime.timedelta(seconds=90)` — the KEYWORD is chosen by the value,
    /// and `repr(datetime(2020, 1, 1))` prints the zero time fields
    /// (`datetime.datetime(2020, 1, 1, 0, 0)`) but not the zero microsecond.
    /// A hand port would be four transcriptions with a per-value branch in
    /// each; `repr()` answers it exactly, once, at the conversion.
    ///
    /// `display` is `str(o)` and is a DIFFERENT string for every member of this
    /// family, which is the whole of why both are carried: `{{ p }}` wants
    /// `str`, and `{{ p|pprint }}`, `{{ p|stringformat:"r" }}` and a datetime
    /// NESTED in a list or dict all want `repr`.
    pub repr: String,
    /// Python's own ordering for the object, reduced to a comparable key
    /// (#2471). `None` only where Python could not be asked — see
    /// [`Encoded::python_partial_cmp`], which is the ONE place this is read.
    pub cmp_key: Option<CmpKey>,
    /// The object's ATTRIBUTES, by name, measured at the conversion (#2481).
    ///
    /// Django's `Variable._resolve_lookup` tries mapping access, then
    /// `getattr`, then an integer index at every dotted segment. A `Value` is
    /// inert data with no attributes, so djust's `context::lookup_segment` had
    /// no step 2 at all and `{{ post.published.year }}` — an ordinary Django
    /// idiom — rendered the EMPTY STRING on every path with no raw-Python
    /// sidecar, which is every `DjustTemplateBackend` render. This map is that
    /// step's answer: `lookup_segment` reads it, and it is the ONE reader.
    ///
    /// **Collected by NAME, not by bulk dump.** A `datetime` is a C type: it
    /// has no `__dict__`, so the `__dict__` arm of `FromPyObject` never reached
    /// `.year` and never could. [`ENCODED_ATTR_NAMES`] states the per-type list
    /// and is the whole of the policy.
    ///
    /// **`min` / `max` / `resolution` are deliberately absent, and the reason
    /// is not taste.** They are class attributes whose values are themselves
    /// `datetime`s, so collecting them would convert a `datetime` whose own
    /// `min` is a `datetime` — `datetime.min.min is datetime.min` is `True` —
    /// and the conversion would not terminate. Measured, not reasoned about.
    /// So `{{ p.max }}` stays empty where Django renders it; that cell is
    /// unchanged by this field rather than closed by it, and is recorded as
    /// such rather than quietly widened.
    ///
    /// **Nullary METHODS are in this map too since #2485**, put there by the
    /// SECOND producer [`collect_called_attrs`] from its own table
    /// [`ENCODED_CALL_NAMES`]. Django reaches them through its auto-call
    /// (ADR-024), which turns a lookup into an EVALUATION — a different
    /// mechanism, which is why it is a second table rather than more strings in
    /// the first, and why the names it holds were chosen by MEASUREMENT rather
    /// than by listing what a `datetime` can do (see that table).
    ///
    /// They land in the SAME map, so `context::lookup_segment` stays the ONE
    /// reader: a second resolution path for "the names a dotted lookup reaches"
    /// is the #1646 shape this map exists to avoid.
    ///
    /// Keyed by [`ObjectKey`] rather than `String` so the map IS a
    /// [`Value::Object`]'s map: the same `get(part)` `lookup_segment` already
    /// makes for step 1, and the same thing on the wire.
    pub attrs: IndexMap<ObjectKey, Value>,
    /// `list(o)` — the object's ITEMS, enumerated at the conversion
    /// (#2477/#2489). `None` means "not enumerated", which is a DIFFERENT
    /// statement from `Some(vec![])`.
    ///
    /// **Why this is a field and not a separate `Value` variant.** A `set`, a
    /// `dict_keys` and a falsy `__iter__` class are the same thing this struct
    /// already exists to be — a Python object no variant models, held by facts
    /// MEASURED from it because the object itself cannot cross — with one more
    /// measurable fact. Every other fact a collection needs is already here and
    /// is not derivable from a list of items:
    ///
    /// * `{{ p }}` renders `{'a'}` for a set and `['a']` for a list, so the
    ///   container spelling must be [`Encoded::display`], not reconstructed;
    /// * `{{ p|first }}` RAISES for a set and answers for a list, so the
    ///   refusal needs [`Encoded::type_name`] to name `'set'`;
    /// * `{% if p %}` is `False` for a `__bool__`-False collection with two
    ///   items, so truthiness must be [`Encoded::truthy`];
    /// * `{{ p|length }}` is `0` for an iterable with no `__len__`, so the
    ///   count must be [`Encoded::len`] and not `items.len()`;
    /// * `{{ p|pprint }}` wants [`Encoded::repr`], `{{ p.a }}` wants
    ///   [`Encoded::attrs`].
    ///
    /// A new variant would carry seven of those all over again, and would have
    /// to be classified at every wildcard `match` arm in the workspace (#1646);
    /// [`Value::DictView`] — the one existing variant with this shape — is
    /// documented as built ONLY by `Context::dict_view` during a render, has no
    /// wire format, and derives its truthiness from `!items.is_empty()`, which
    /// is false for two of the objects this carries. Splitting the class by
    /// emptiness, so that `set()` took one carrier and `{'a'}` another, is the
    /// drift shape (#1646) rather than a design.
    ///
    /// **`None` is a real answer, not a default.** [`opaque_value`] declines to
    /// enumerate a ONE-SHOT iterator (`iter(o) is o` — a generator, a `zip`, a
    /// `map`): reading it would consume the caller's object. Such a value keeps
    /// the terminal `Value::String(str(o))` path entirely, so a `None` here
    /// reaches [`filters::iter_values`] only from the empty-`len` claim, where
    /// `iterable` alone already answers it.
    pub items: Option<Vec<Value>>,
    /// Which of Python's equality CONTRACTS this object obeys, measured at the
    /// conversion (#2480). `None` is "no contract this crate can answer" —
    /// never equal, which is the pre-fix answer for every value.
    ///
    /// # Why this is a fourth measured fact and not a rule over the others
    ///
    /// Nothing already carried decides it, in EITHER direction:
    ///
    /// * `set() == frozenset() == {}.keys() == {}.items()` is **True** in
    ///   Python — ACROSS [`Encoded::type_name`]s;
    /// * `LenZero() == LenZero()` on two DISTINCT instances is **False** —
    ///   WITHIN one `type_name`, and True when it is the same object;
    /// * `set() == {}.values()` is **False** while `set() == {}.keys()` is
    ///   True, and both views carry the same (empty) [`Encoded::items`].
    ///
    /// So neither the type name nor the items nor any carried spelling decides
    /// it. A NAME LIST would — `{set, frozenset, dict_keys, dict_items}` — and
    /// is wrong in both directions: it misses every `collections.abc.Set`
    /// registration a user writes, and it claims any user class that happens
    /// to be called `set` (`type(o).__name__` is not qualified). The PROTOCOL
    /// is the thing Python itself dispatches on, so it is the thing measured.
    ///
    /// # The four arms, each justified by a contract
    ///
    /// 1. [`EqClass::Set`] — `isinstance(o, collections.abc.Set)`. The ABC
    ///    *defines* `__eq__` as `len(self) == len(other) and self <= other`
    ///    and `__le__` as containment, so both the equality AND a real
    ///    PARTIAL order fall out of [`Encoded::items`].
    /// 2. [`EqClass::Number`] — `isinstance(o, numbers.Number)`, carrying
    ///    `complex(o)`'s two components. Equality ONLY: `complex` refuses
    ///    `<`, so this class must never grow an ordering.
    /// 3. [`EqClass::Identity`] — `type(o).__eq__ is object.__eq__` AND
    ///    `type(o).__repr__ is object.__repr__`. Identity semantics with an
    ///    identity-bearing spelling, so the default repr
    ///    (`<mod.X object at 0x…>`) IS a faithful token and
    ///    [`Encoded::repr`] answers it with no new field. Equality only.
    /// 4. `None` — every other object, including one that overrides `__eq__`
    ///    (only Python can run it) and one with default `__eq__` but a CUSTOM
    ///    `__repr__` (a `dict_values`: two distinct empty ones share the
    ///    spelling `dict_values([])`, so the repr is not a token). Never
    ///    equal, never ordered: exactly what a `cmp_key: None` already meant.
    ///
    /// **Arm 3 is a restoration, not a new hazard.** Before #2476 a
    /// `LenZero()` crossed as `Value::String("<LenZero object at 0x…>")` and
    /// compared by string through the `(String, String)` arm — this is the
    /// same token, reached deliberately. Its one caveat is the same one:
    /// CPython may reuse an address, so a value RESTORED from a state entry
    /// could in principle match a fresh object allocated where the old one
    /// died. Within a single render it cannot happen — every context object is
    /// alive at once, so their addresses are distinct.
    pub eq_class: Option<EqClass>,
    /// The LIVE object this value was measured from — ADR-027's handle
    /// (#2539). `None` unless [`resolve_lazy`] was on at the conversion.
    ///
    /// **TRANSIENT.** It is not serialized (the `ENCODED_TAG` payload stays
    /// ELEVEN slots), not compared (`PartialEq for Encoded` does not mention
    /// it). Temporal objects may cross back to Python after an isinstance
    /// check; opaque objects still cross as display strings (#2509), so model
    /// protection is unchanged. A msgpack round trip drops the handle
    /// for free, and `Deserialize` restores `None`.
    ///
    /// **Why it rides IN the value.** The raw-Python sidecar is keyed by
    /// TOP-LEVEL context name, so every construct that binds a value to a NEW
    /// name — `{% for r in rows %}`, `{% with q=p %}`, `{% include … with %}`
    /// — left the object unreachable under that name (#2504, #2505, #2542).
    /// A handle carried by the value is carried by the BINDING, with no alias
    /// fallback, no frame-origin bit and no per-construct plumbing.
    ///
    /// **`Arc` because `Py<T>` is not `Clone` in this workspace** (pyo3
    /// without `py-clone`) while `Encoded` derives `Clone`, and a `Value` is
    /// cloned per context entry per render. `Arc<Py<PyAny>>` clones with no
    /// GIL — the same shape `Context::raw_py_objects` already uses.
    ///
    /// **What can never acquire one.** `crosses_as_encoded` / the
    /// `FromPyObject` impl claim a `list`, a `dict`, a tuple, anything with
    /// `__djust_serialize__` and any `Model` in arms ABOVE [`opaque_value`],
    /// so no list, dict, model, manager or queryset reaches this field. That
    /// is enforced by the existing ordering rather than by a new rule.
    ///
    /// **When it is `Some`, [`Encoded::attrs`] is EMPTY** — see
    /// [`opaque_value`]. The handle is the authority for attribute lookups,
    /// and the eager `__dict__` dump it replaces is the unbounded recursion
    /// that segfaults on a reference cycle (#2516).
    pub live: Option<std::sync::Arc<Py<PyAny>>>,
}

/// Which of Python's equality contracts a [`Value::Encoded`] obeys (#2480).
///
/// Measured by [`equality_class`] at the conversion, because a render has no
/// interpreter in reach and cannot call `__eq__`. See [`Encoded::eq_class`]
/// for why each arm is a protocol rather than a type name, and
/// `djust_templates::renderer::encoded_equal` for the one place it is read.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum EqClass {
    /// `isinstance(o, collections.abc.Set)`. Equality AND a subset PARTIAL
    /// order, both computed over [`Encoded::items`] — the ABC defines both in
    /// terms of containment, so one derivation answers all five operators.
    Set,
    /// `isinstance(o, numbers.Number)`, as `complex(o)`'s two components.
    ///
    /// EQUALITY ONLY. `complex(0) < complex(0)` RAISES in Python — Django
    /// swallows it to False — so this class must never reach an ordering.
    /// Two components rather than one because `complex(0, 1) == 0` is False
    /// and a real part alone cannot say so.
    Number {
        /// `o.real` — or `float(o)` for a real number, which is the same.
        real: f64,
        /// `o.imag`, `0.0` for every real number.
        imag: f64,
    },
    /// `type(o).__eq__ is object.__eq__` AND `type(o).__repr__ is
    /// object.__repr__`: identity semantics, spelled by a repr that carries
    /// the address. Equality only — `object()` does not order either.
    Identity,
}

/// The attribute names carried on a [`Value::Encoded`] for each of the four
/// `DjangoJSONEncoder` types (#2481).
///
/// The per-type lists Python answers WITHOUT being called and WITHOUT
/// recursing — see the [`Encoded::attrs`] doc for why `min` / `max` /
/// `resolution` and the nullary methods are not here. Keyed by the `tp_name`
/// [`django_json_encoded`] has already resolved, so the lookup is one string
/// compare over four entries rather than a second `isinstance` sweep.
///
/// `datetime.datetime` is a `datetime.date` SUBCLASS and gets the longer list,
/// because [`django_json_encoded`] matches `datetime` first — the same
/// ordering, in the same order, for the same reason.
pub const ENCODED_ATTR_NAMES: &[(&str, &[&str])] = &[
    (
        "datetime.datetime",
        &[
            "year",
            "month",
            "day",
            "hour",
            "minute",
            "second",
            "microsecond",
            "fold",
            "tzinfo",
            // Django's `tz` filters (`localtime` / `utc` / `timezone`) return a
            // `datetimeobject` subclass flagged `convert_to_local_time = False`
            // so `template_localtime` and the `date` filter's
            // `expects_localtime` do NOT convert the value AGAIN to the active
            // zone (#2541). A plain `datetime` has no such attribute and
            // `collect_named_attrs` skips it; `filters::format_date` reads it.
            "convert_to_local_time",
        ],
    ),
    ("datetime.date", &["year", "month", "day"]),
    (
        "datetime.time",
        &["hour", "minute", "second", "microsecond", "fold", "tzinfo"],
    ),
    ("datetime.timedelta", &["days", "seconds", "microseconds"]),
];

/// The nullary METHODS whose result is carried on a [`Value::Encoded`] (#2485).
///
/// Django's `Variable._resolve_lookup` AUTO-CALLS a callable attribute
/// (ADR-024), so `{{ p.isoformat }}` is an EVALUATION where `{{ p.year }}` is a
/// lookup. That is a different mechanism from [`ENCODED_ATTR_NAMES`]'s, which
/// is why it is a second table read by a second producer rather than more
/// strings in the first.
///
/// # The membership rule, which is a measurement and not a name list
///
/// A name is here when carrying its result makes djust render what **Django
/// renders**. That is narrower than "the method exists and takes no
/// arguments", and the difference is the whole of the table: for a method whose
/// result is itself a `date` / `time` / `datetime` / `struct_time`, carrying it
/// swaps one divergence for another, because djust's BARE render of those
/// already differs from Django's (Django LOCALIZES — `{{ dt.date }}` is
/// `March 4, 2026` there and `2026-03-04` here; `{{ dt.timetuple }}` is
/// `time.struct_time(tm_year=…)` there and a plain tuple here). Every name was
/// decided by sweeping `dir(o)` on live objects and comparing the three
/// columns — what Django renders for `{{ p.<name> }}`, what djust renders
/// today, and what djust would render for the RESULT — and keeping only the
/// rows where the third equals the first:
///
/// ```text
/// isoformat    django '2026-03-04T05:06:07.000008'  result '2026-03-04T05:06:07.000008'  KEPT
/// weekday      django '2'                           result '2'                           KEPT
/// date         django 'March 4, 2026'               result '2026-03-04'                  DROPPED
/// timetuple    django 'time.struct_time(tm_year=…)' result '(2026, 3, 4, …)'             DROPPED
/// ```
///
/// So `date`, `time`, `timetz`, `astimezone`, `replace`, `isocalendar`,
/// `timetuple` and `utctimetuple` are DELIBERATELY absent, and the reason is
/// measured rather than architectural. `now`, `today` and `utcnow` are absent
/// for a second reason on top of that one: their value is the CURRENT time, so
/// carrying them would do nondeterministic work at every conversion.
///
/// A method that requires ARGUMENTS (`strftime`, `combine`, `fromisoformat`)
/// needs no entry and no exclusion: Django's auto-call catches the `TypeError`
/// and renders `string_if_invalid`, which is the empty string djust already
/// renders for a name it does not carry. Those cells agree today.
///
/// # Cost, and why `utcoffset` is free
///
/// These are C-level calls on the value being converted, made once per
/// `Encoded` rather than once per template that asks. `utcoffset` in
/// particular costs NOTHING new: [`comparison_key`] already calls it on every
/// `datetime` and every `time` to build the [`CmpKey`], so the tzinfo code
/// path was already being run at the conversion before this table existed.
///
/// # Not `min` / `max` / `resolution`
///
/// Those are DATA attributes and belong to the other table's question, where
/// they cannot go: their values are values of the same family and
/// `datetime.min.min is datetime.min`, so collecting them would not terminate.
/// Unchanged by this table and pinned as still-divergent — see
/// [`Encoded::attrs`].
pub const ENCODED_CALL_NAMES: &[(&str, &[&str])] = &[
    (
        "datetime.datetime",
        &[
            "isoformat",
            "ctime",
            "weekday",
            "isoweekday",
            "toordinal",
            "timestamp",
            "utcoffset",
            "tzname",
            "dst",
        ],
    ),
    (
        "datetime.date",
        &["isoformat", "ctime", "weekday", "isoweekday", "toordinal"],
    ),
    (
        "datetime.time",
        &["isoformat", "utcoffset", "tzname", "dst"],
    ),
    ("datetime.timedelta", &["total_seconds"]),
];

/// A [`Encoded`] value's position in Python's ordering (#2471).
///
/// `(domain, hi, lo)`, compared lexicographically, and comparable ONLY within a
/// domain — which is what makes the pairs Python refuses fall out for free
/// rather than needing their own rules. Every field is measured from the live
/// object at the PyO3 boundary; nothing here is parsed back off a string.
///
/// # Why a key at all, and why not the strings already carried
///
/// Two `Encoded`s cannot be compared by calling Python: the render happens with
/// no interpreter in reach. So the question is which of the carried spellings
/// answers Python's `==` and `<`, and the measured answer is **neither**:
///
/// * `display` (`str(o)`) does not ORDER. `str(timedelta(seconds=90))` is
///   `"0:01:30"` and `str(timedelta(days=10))` is `"10 days, 0:00:00"`, so a
///   lexicographic compare puts ten days before ninety seconds. It also does
///   not answer `==`: two aware datetimes naming the SAME instant in different
///   zones are equal in Python and have different `str()`.
/// * `json` (`DjangoJSONEncoder.default(o)`) does not answer `==` either, in
///   the direction that matters more: it TRUNCATES a datetime's microseconds to
///   milliseconds (`r[:23] + r[26:]`), so two datetimes 1 µs apart encode
///   identically and a string compare would call them equal. `duration_iso_string`
///   leaves the day count unpadded (`P10DT…` vs `P9DT…`) and appends the
///   microseconds only when non-zero, so it does not order either.
///
/// # The domains, and what each one buys
///
/// A domain is "the set of values Python will compare with this one". Splitting
/// on it is not tidiness: `date(2020, 1, 1) == datetime(2020, 1, 1)` is `False`
/// in CPython even though `datetime` IS a `date` subclass, and
/// `date < datetime` RAISES — which Django's `{% if %}` swallows to `False`
/// (`smart_if`'s `except Exception: return False`). Naive-against-aware is the
/// same pair of answers. Both fall out of "different domains do not compare".
///
/// `hi`/`lo` is a two-limb integer rather than one because a `timedelta` does
/// not fit an `i64` of microseconds: `timedelta.max` is ~8.64e19 µs and
/// `i64::MAX` is ~9.22e18. Python normalises a `timedelta` to
/// `(days, 0 <= seconds < 86400, 0 <= microseconds < 10**6)`, so
/// `(days, seconds * 10**6 + microseconds)` compared lexicographically IS
/// Python's ordering, and both limbs fit.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct CmpKey {
    /// Which set of values Python will compare this one with. Values that
    /// disagree here are never equal and never ordered.
    pub domain: u8,
    /// Days — `toordinal()` for a date/datetime, `timedelta.days` for a delta,
    /// `0` for a time.
    pub hi: i64,
    /// Microseconds within the day. Aware values are normalised by their
    /// `utcoffset()` first, which is exactly what CPython compares.
    pub lo: i64,
}

/// A `timedelta`, the only member whose values span more than a day.
pub const CMP_DOMAIN_TIMEDELTA: u8 = 1;
/// A `date` that is not a `datetime`.
pub const CMP_DOMAIN_DATE: u8 = 2;
/// A `datetime` whose `utcoffset()` is `None`.
pub const CMP_DOMAIN_DATETIME_NAIVE: u8 = 3;
/// A `datetime` whose `utcoffset()` is not `None`, normalised to UTC.
pub const CMP_DOMAIN_DATETIME_AWARE: u8 = 4;
/// A `time` whose `utcoffset()` is `None`. There is deliberately no aware-time
/// domain — see the `datetime.time` arm of [`comparison_key`].
pub const CMP_DOMAIN_TIME_NAIVE: u8 = 5;

/// STRUCTURAL equality for two [`Encoded`]s — every carried spelling, plus the
/// attribute map (#2481).
///
/// Hand-written rather than derived because [`Encoded::attrs`] holds `Value`s
/// and [`Value`] deliberately has NO `PartialEq`: Django's `==` for a template
/// value is `renderer::values_equal`, which equates `1` with `1.0` and asks
/// [`Encoded::python_partial_cmp`] for this family. Deriving a second `==` onto
/// `Value` would put a structural answer one keystroke away from every site
/// that wants the Django one — two mechanisms for one question, which is the
/// #1646 shape. So the structural comparison is reachable by NAME only, through
/// [`values_structurally_equal`], and this impl is its one caller.
///
/// What this is for: pinning that a value survived a round trip unchanged. It
/// is NOT `{% if a == b %}`.
impl PartialEq for Encoded {
    fn eq(&self, other: &Self) -> bool {
        self.type_name == other.type_name
            && self.display == other.display
            && self.display_safe == other.display_safe
            && self.json == other.json
            && self.truthy == other.truthy
            && self.len == other.len
            && self.iterable == other.iterable
            && self.repr == other.repr
            && self.cmp_key == other.cmp_key
            && self.attrs.len() == other.attrs.len()
            && self
                .attrs
                .iter()
                .zip(other.attrs.iter())
                .all(|((ka, va), (kb, vb))| ka == kb && values_structurally_equal(va, vb))
            // `None` is not `Some(vec![])` — "not enumerated" against "no
            // items" — so the option shapes are compared before the elements
            // (#2477). `zip` alone would call two differently-shaped values
            // equal, which is exactly the round-trip regression this impl pins.
            && match (&self.items, &other.items) {
                (None, None) => true,
                (Some(a), Some(b)) => {
                    a.len() == b.len()
                        && a.iter()
                            .zip(b.iter())
                            .all(|(x, y)| values_structurally_equal(x, y))
                }
                _ => false,
            }
            // The equality CLASS is part of the value (#2480): an entry that
            // came back off the wire without it answers `{% if a == b %}`
            // differently, which is exactly what a round-trip pin is for.
            // `EqClass` derives `PartialEq`, and its `f64`s compare as `f64`s
            // — a NaN component would make a value structurally unequal to
            // itself, which is the same note `values_structurally_equal`
            // already carries for `Value::Float`.
            && self.eq_class == other.eq_class
        // [`Encoded::live`] is DELIBERATELY absent (#2539). This impl answers
        // "did anything change on the way through the codec", and the codec
        // does not carry the handle — a round trip drops it by construction,
        // so comparing it would make every restored value unequal to the one
        // it was written from and turn the wire pins red for a field that has
        // no wire representation. It is also not a VALUE: two handles to the
        // same object and one to a clone describe the same measured facts.
    }
}

/// Are these two [`Value`]s the SAME VALUE — same variant, same payload?
///
/// **Not Django's `==`.** `renderer::values_equal` is that, and it deliberately
/// answers differently: `1 == 1.0` is true there and false here, a `Decimal`
/// compares against an `Integer` there and not here, and two `Encoded`s go
/// through Python's own ordering. This function answers the round-trip
/// question instead — "did anything change on the way through the codec" — and
/// exists because [`Encoded`] carries a map of `Value`s that a wire pin has to
/// compare.
///
/// Every pair is spelled explicitly and the wildcard is LAST, so the distinct
/// pairs stay distinct: `Missing` is not `None` (#2203), a `List` is not a
/// `Tuple` (#2276), and a `DictView` is not the `List` of its items (#2340). A
/// new `Value` variant lands on the wildcard and compares unequal to itself —
/// which `test_every_variant_is_structurally_equal_to_its_own_clone` turns into
/// a failure rather than a silent wrong answer.
///
/// Float NaN is not equal to itself, as `f64`'s own `==` says. No `Encoded`
/// attribute is a NaN today; the note is here so the answer is not a surprise.
pub fn values_structurally_equal(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Missing, Value::Missing) => true,
        (Value::None, Value::None) => true,
        (Value::Bool(a), Value::Bool(b)) => a == b,
        (Value::Integer(a), Value::Integer(b)) => a == b,
        (Value::Float(a), Value::Float(b)) => a == b,
        (Value::String(a), Value::String(b)) | (Value::SafeString(a), Value::SafeString(b)) => {
            a == b
        }
        (Value::Decimal(a), Value::Decimal(b)) => a == b,
        (Value::BigInt(a), Value::BigInt(b)) => a == b,
        (
            Value::NamedTuple {
                name: an,
                fields: af,
                items: a,
            },
            Value::NamedTuple {
                name: bn,
                fields: bf,
                items: b,
            },
        ) => {
            an == bn
                && af == bf
                && a.len() == b.len()
                && a.iter()
                    .zip(b)
                    .all(|(a, b)| values_structurally_equal(a, b))
        }
        (Value::List(a), Value::List(b)) | (Value::Tuple(a), Value::Tuple(b)) => {
            a.len() == b.len()
                && a.iter()
                    .zip(b.iter())
                    .all(|(x, y)| values_structurally_equal(x, y))
        }
        (Value::Object(a), Value::Object(b)) => {
            a.len() == b.len()
                && a.iter()
                    .zip(b.iter())
                    .all(|((ka, va), (kb, vb))| ka == kb && values_structurally_equal(va, vb))
        }
        (
            Value::DictView {
                kind: ka,
                items: ia,
            },
            Value::DictView {
                kind: kb,
                items: ib,
            },
        ) => {
            ka == kb
                && ia.len() == ib.len()
                && ia
                    .iter()
                    .zip(ib.iter())
                    .all(|(x, y)| values_structurally_equal(x, y))
        }
        (Value::Encoded(a), Value::Encoded(b)) => a == b,
        _ => false,
    }
}

impl Encoded {
    /// Python's answer for `a <op> b`, or `None` where Python refuses (#2471).
    ///
    /// **THE** comparison for the CMP-KEY family, and the only one. Since
    /// #2480 its three readers — `values_equal`, `try_compare` and
    /// `dictsort`'s ordering — reach it through ONE wrapper,
    /// `djust_templates::renderer::encoded_partial_cmp`, which adds the
    /// [`EqClass::Set`] subset order and otherwise delegates here. That
    /// wrapper is this method's only caller outside this crate, which is what
    /// keeps `==` and `<` from drifting apart the way they did for `Bool`
    /// (#2244), `Float` (#2243) and `List` (#2335) before their arms were
    /// written as a pair (#1646). Equality is `Some(Equal)` rather than a
    /// second rule.
    ///
    /// It stays HERE, keyed on [`Encoded::cmp_key`] alone, because the Set
    /// order compares [`Encoded::items`] with Django's `==` — which is
    /// `renderer::values_equal`, a function this crate deliberately does not
    /// have (`Value` has no `PartialEq`; see [`values_structurally_equal`]).
    ///
    /// `None` — which every caller renders as Django's own answer for a pair
    /// Python cannot compare: `False` for all four ordering operators, and NOT
    /// equal — covers three cases, and they are the same case:
    ///
    /// * different domains (`date` against `datetime`, naive against aware);
    /// * either side carrying no key at all, which happens only where the PyO3
    ///   boundary could not read the object (a `utcoffset()` that raises) or
    ///   where a value was restored from a pre-#2471 msgpack state entry. Both
    ///   keep the pre-fix answer rather than guessing one.
    pub fn python_partial_cmp(&self, other: &Encoded) -> Option<std::cmp::Ordering> {
        let (a, b) = (self.cmp_key?, other.cmp_key?);
        if a.domain != b.domain {
            return None;
        }
        Some((a.hi, a.lo).cmp(&(b.hi, b.lo)))
    }
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
            // An `Encoded` takes the same two-format split, for the reason
            // `ENCODED_TAG` documents: untagged, a state round trip through
            // msgpack turns it back into the display string and reopens #2448.
            // The payload is `[type_name, display, json, truthy, items]` — a
            // LIST, which is what keeps the tag from colliding with the two
            // string-payload tags above. The fourth element is #2458's
            // `bool(o)`; without it a state round trip restores the value with
            // the pre-#2458 truthiness and `{% if p %}` on a `timedelta(0)`
            // flips back after one cache hit — the same reopening `ENCODED_TAG`
            // exists to prevent for the JSON spelling. The fifth and sixth
            // are #2466's `sized_empty` and `iterable`, for the same reason
            // one element over: without them a `set()` comes back unable to
            // answer `{% for %}` or `|join`.
            //
            // Every new element is appended at the END, which is the safe
            // position in a POSITIONAL msgpack payload (#1541): a leading or
            // interior optional shifts every later slot on read. The reader
            // below accepts a 6-, 4- or 3-element payload, so a Redis state
            // entry written by an older build still loads.
            //
            // Serialized as a heterogeneous tuple rather than an array because
            // the elements are no longer all strings; `serde` writes a tuple as
            // the same msgpack array either way.
            //
            // #2471/#2472 grew it to SIX: `repr` and the comparison key take
            // the same trip for the same reason `truthy` does — both are
            // measured from a live Python object that no longer exists by the
            // time a state entry comes back, so an entry that dropped them
            // would restore a value whose `{% if a == b %}` and `|pprint`
            // answers are the pre-fix ones after one cache hit. That is the
            // exact reopening `ENCODED_TAG` exists to prevent, twice already.
            //
            // The key is written as its three limbs rather than as a struct so
            // the payload stays a flat msgpack array; `Option` writes `nil`.
            //
            // #2477/#2489 grew it to ELEVEN, appended for the fifth time and
            // for the fifth identical reason: slot 10 is `len(o)` as an
            // `Option` (slot 5's boolean cannot carry a COUNT, and the objects
            // this now carries have one) and slot 11 is the enumerated ITEMS.
            // Without them a `{'a'}` in state comes back after one cache hit
            // unable to answer `{% for %}`, `|join` or `|length` — the exact
            // reopening `ENCODED_TAG` exists to prevent, now for the fifth
            // time.
            Value::Encoded(e) if !serializer.is_human_readable() => {
                let mut m = serializer.serialize_map(Some(1))?;
                m.serialize_entry(
                    ENCODED_TAG,
                    &(
                        e.type_name.as_str(),
                        e.display.as_str(),
                        e.json.as_str(),
                        e.truthy,
                        // Slot 5 WIDENED from #2466's `sized_empty` boolean to
                        // `len(o)` itself (#2477/#2489) — `Option<u64>`, so
                        // `nil` is "no `__len__`". It is the one slot whose
                        // TYPE changes, and it is safe here for the reason the
                        // #1541 canon is actually about: that canon forbids a
                        // conditionally-SKIPPED serde field, which shifts every
                        // later slot within one width. This payload is
                        // dispatched on WIDTH, and
                        // no build ever wrote a 10-element one, so the 9- / 8-
                        // / 6-element arms below keep reading a `Bool` here and
                        // are untouched. Carrying the boolean as well would be
                        // two wire sources for one question (#1646), which is
                        // the drift this repo keeps paying for.
                        e.len.map(|n| n as u64),
                        e.iterable,
                        e.repr.as_str(),
                        e.cmp_key.map(|k| (k.domain, k.hi, k.lo)),
                        // Slot 9, appended (#2481). A MAP, written
                        // unconditionally — an empty one costs a byte and the
                        // slots stay aligned, which is the same choice
                        // `cmp_key` makes one slot over and for the same
                        // reason. Carried rather than rebuilt on read because
                        // `SerializableViewState.state` round-trips through
                        // msgpack on EVERY read of the state backend and there
                        // is no interpreter there to re-ask the object: without
                        // this, `{{ dt.year }}` would answer on the first
                        // render and go empty again after one cache hit. That
                        // is the exact reopening `ENCODED_TAG` exists to
                        // prevent, now for the fourth time.
                        &e.attrs,
                        // Slot 10, appended (#2477/#2489). An `Option`, so
                        // `nil` stays "not enumerated" — which is NOT the same
                        // statement as an empty list, and the deserializer
                        // keeps them apart.
                        &e.items,
                        // Slot 11, appended for the SIXTH time and for the
                        // sixth identical reason (#2480): the equality CLASS
                        // is measured from a live Python object that no longer
                        // exists when a state entry comes back, so an entry
                        // that dropped it would restore a value whose
                        // `{% if a == b %}` answer is the pre-fix one after
                        // one cache hit — the exact reopening `ENCODED_TAG`
                        // exists to prevent.
                        //
                        // A MAP, and NEVER `nil` — an absent class is the
                        // EMPTY map. That is the one structural decision in
                        // this slot, and it is what keeps the interior-insert
                        // canary airtight now that eleven is a real width.
                        //
                        // Before this slot existed, a ten-element payload with
                        // one element inserted was eleven long and refused on
                        // WIDTH alone. It is now a real width, so only a TYPE
                        // can refuse it — and an insert anywhere in a
                        // ten-element payload shifts slot 9 (the ITEMS: a list
                        // or `nil`) into this position, or the intruder
                        // itself. No slot below carries a MAP except `attrs`
                        // at 8, which an insert can only push to 9. So
                        // "this slot is always a map" is exactly the predicate
                        // that refuses every shift, and writing `nil` for the
                        // absent case would have surrendered it.
                        //
                        // It also makes a swap with `cmp_key` (a list or
                        // `nil`, four slots up) a REFUSAL rather than a
                        // silent double loss.
                        //
                        // The class itself is fail-to-absent INSIDE the map,
                        // like every other optional slot — see
                        // `decode_eq_class`. The GROW-`attrs` alternative was
                        // rejected: that map is what `context::lookup_segment`
                        // resolves `{{ p.x }}` against, so a synthetic key
                        // there would be a template-visible attribute Django
                        // does not have.
                        &encode_eq_class(e.eq_class),
                    ),
                )?;
                m.end()
            }
            // The JSON half is the DISPLAY string, as `Decimal`'s is its digits:
            // this arm is reached only through `serialization::to_json`, and a
            // bare string is what `from_json` can read back without a tag. The
            // client-facing JSON is `json_script`'s own `value_to_json`, which
            // has its own `Encoded` arm and writes the ENCODER spelling there.
            Value::Encoded(e) => serializer.serialize_str(&e.display),
            // Everything else is exactly the untagged derive it replaces.
            Value::Decimal(d) => serializer.serialize_str(d),
            Value::BigInt(d) => serializer.serialize_str(d),
            // A `Missing` takes the same two-format split the four tags above
            // take, and for the reason `MISSING_TAG` documents at length: the
            // two variants are deliberately DISTINCT (#2203) and shared one
            // `nil`, so every `None` in state came back a `Missing` and
            // rendered `""` after one cache hit (#2484).
            //
            // The tag is on THIS variant rather than on `None` so that the
            // common value's bytes do not change: a `None` stays the bare
            // `nil` an older reader already understands, and only this
            // sentinel — which no real path can put in a state blob — gets a
            // new spelling.
            Value::Missing if !serializer.is_human_readable() => {
                let mut m = serializer.serialize_map(Some(1))?;
                m.serialize_entry(MISSING_TAG, &())?;
                m.end()
            }
            // JSON keeps ONE `null` for both, as `json.dumps` does — the same
            // deliberate human-readable asymmetry `TUPLE_TAG` documents.
            Value::Missing | Value::None => serializer.serialize_none(),
            Value::Bool(b) => serializer.serialize_bool(*b),
            Value::Integer(i) => serializer.serialize_i64(*i),
            Value::Float(f) => serializer.serialize_f64(*f),
            Value::String(st) | Value::SafeString(st) => serializer.serialize_str(st),
            // A tuple keeps its identity in binary formats only (#2276) — see
            // `TUPLE_TAG` for why JSON deliberately stays an array.
            Value::NamedTuple {
                name,
                fields,
                items,
            } if !serializer.is_human_readable() => {
                let mut m = serializer.serialize_map(Some(1))?;
                m.serialize_entry(NAMED_TUPLE_TAG, &(name, fields, items))?;
                m.end()
            }
            Value::Tuple(items) if !serializer.is_human_readable() => {
                let mut m = serializer.serialize_map(Some(1))?;
                m.serialize_entry(TUPLE_TAG, items)?;
                m.end()
            }
            Value::List(items) | Value::Tuple(items) | Value::NamedTuple { items, .. } => {
                items.serialize(serializer)
            }
            Value::Object(o) => o.serialize(serializer),
            // A view is built by `Context::dict_view` during a render and is
            // never handed to a serializer on any real path — but `Value` has
            // to be total. Its ITEMS, because that is the shape a JSON or
            // msgpack consumer could do anything with; Python's own
            // `json.dumps` refuses a view outright, so there is no faithful
            // encoding to mirror (#2340).
            Value::DictView { items, .. } => items.serialize(serializer),
        }
    }
}

/// One [`CmpKey`] slot, read back off the wire (#2471).
///
/// `nil` or a three-integer array; anything else is not a payload this crate
/// wrote, so it reads as ABSENT rather than being guessed at — a value with no
/// key keeps the pre-#2471 comparison answer, which is the direction to fail
/// in.
///
/// Extracted when #2481 added the ninth slot, so the eight- and nine-element
/// arms cannot drift apart on how a key is read (#1646). Two copies of this
/// `match` is the shape where one arm gains a case and the other does not.
fn decode_cmp_key(key: &Value) -> Option<CmpKey> {
    let (Value::List(limbs) | Value::Tuple(limbs) | Value::NamedTuple { items: limbs, .. }) = key
    else {
        return None;
    };
    match limbs.as_slice() {
        [Value::Integer(domain), Value::Integer(hi), Value::Integer(lo)] => {
            u8::try_from(*domain).ok().map(|domain| CmpKey {
                domain,
                hi: *hi,
                lo: *lo,
            })
        }
        _ => None,
    }
}

/// The wire tag for [`EqClass::Set`].
pub const EQ_CLASS_SET: u8 = 1;
/// The wire tag for [`EqClass::Number`]. Its two limbs carry `complex(o)`.
pub const EQ_CLASS_NUMBER: u8 = 2;
/// The wire tag for [`EqClass::Identity`].
pub const EQ_CLASS_IDENTITY: u8 = 3;

/// The key the [`EqClass`] triple sits under inside slot 10's map (#2480).
///
/// Short, and deliberately NOT one of the four `_TAG` constants: this map is
/// nested inside the `ENCODED_TAG` payload and reads back through the same
/// `visit_map` as any other, so a name collision with a tag would make a
/// one-key map decode as a `Decimal` or a `Tuple` instead.
pub const EQ_CLASS_KEY: &str = "eq";

/// Slot 10's payload for one [`EqClass`] (#2480) — the ONE writer.
///
/// A map, always: EMPTY for `None`, one `EQ_CLASS_KEY` entry holding
/// `[tag, real, imag]` otherwise. See the serializer's comment on the slot for
/// why "always a map" is the structural property and not a preference.
fn encode_eq_class(class: Option<EqClass>) -> IndexMap<ObjectKey, Value> {
    let mut map = IndexMap::new();
    let (tag, real, imag) = match class {
        None => return map,
        Some(EqClass::Set) => (EQ_CLASS_SET, 0.0, 0.0),
        Some(EqClass::Number { real, imag }) => (EQ_CLASS_NUMBER, real, imag),
        Some(EqClass::Identity) => (EQ_CLASS_IDENTITY, 0.0, 0.0),
    };
    map.insert(
        ObjectKey::Str(EQ_CLASS_KEY.to_string()),
        Value::List(vec![
            Value::Integer(i64::from(tag)),
            Value::Float(real),
            Value::Float(imag),
        ]),
    );
    map
}

/// One [`EqClass`] slot, read back off the wire (#2480) — the ONE reader.
///
/// The map WRAPPER is required by the deserializer's pattern (it is what
/// refuses a shifted payload); the class INSIDE it is fail-to-absent like
/// every other optional slot. An empty map, a missing key, a wrong shape or an
/// unknown tag all read as ABSENT rather than being guessed at — a value with
/// no class keeps the pre-#2480 comparison answer, which is the direction to
/// fail in, and a class a future build invents must not be answered by this
/// one.
fn decode_eq_class(map: &IndexMap<ObjectKey, Value>) -> Option<EqClass> {
    let (Value::List(limbs) | Value::Tuple(limbs) | Value::NamedTuple { items: limbs, .. }) =
        map.get(EQ_CLASS_KEY)?
    else {
        return None;
    };
    let [Value::Integer(tag), real, imag] = limbs.as_slice() else {
        return None;
    };
    // A float limb is what this crate writes; an integer is accepted because a
    // hand-built fixture (and a JSON round trip) can land a whole number on
    // `Value::Integer`. Neither is a guess — both name the same number.
    let as_f64 = |v: &Value| match v {
        Value::Float(f) => Some(*f),
        Value::Integer(i) => Some(*i as f64),
        _ => None,
    };
    match u8::try_from(*tag) {
        Ok(EQ_CLASS_SET) => Some(EqClass::Set),
        Ok(EQ_CLASS_NUMBER) => Some(EqClass::Number {
            real: as_f64(real)?,
            imag: as_f64(imag)?,
        }),
        Ok(EQ_CLASS_IDENTITY) => Some(EqClass::Identity),
        _ => None,
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

            // A bare `nil` / JSON `null` is Python `None`, NOT the missing-key
            // sentinel (#2484). It reads as `Missing` before this fix, which
            // is what collapsed the two: `FromPyObject` has no arm producing a
            // `Missing`, so Python `None` is the only thing that can have put a
            // `nil` in a state blob — including one written by a pre-fix build,
            // which this therefore FIXES rather than misreads. A `Missing`
            // arrives through `MISSING_TAG` in `visit_map` instead.
            fn visit_unit<E>(self) -> std::result::Result<Value, E>
            where
                E: de::Error,
            {
                Ok(Value::None)
            }

            fn visit_none<E>(self) -> std::result::Result<Value, E>
            where
                E: de::Error,
            {
                Ok(Value::None)
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
                    // The binary-format missing-key tag (#2484). Same shape,
                    // and the payload is `nil` — which reaches here as
                    // `Value::None`, since `visit_unit` now reads a bare `nil`
                    // as Python's `None`. That is what keeps this tag from
                    // colliding with the string-payload and list-payload tags
                    // around it.
                    if let Some(Value::None) = obj.get(MISSING_TAG) {
                        return Ok(Value::Missing);
                    }
                    // The binary-format big-int tag (#2260), same shape and the
                    // same "exactly one key, that key, a string payload"
                    // discrimination — anything else is a real dict.
                    if let Some(Value::String(d)) = obj.get(BIGINT_TAG) {
                        return Ok(Value::BigInt(d.clone()));
                    }
                    // The binary-format tuple tag (#2276). Same shape, but the
                    // payload is a LIST rather than a string — which is also
                    // what keeps it from colliding with the two above.
                    if let Some(Value::List(parts)) = obj.get(NAMED_TUPLE_TAG) {
                        if let [Value::String(name), Value::List(fields), Value::List(items)] =
                            parts.as_slice()
                        {
                            let names: Option<Vec<String>> = fields
                                .iter()
                                .map(|f| match f {
                                    Value::String(s) => Some(s.clone()),
                                    _ => None,
                                })
                                .collect();
                            if let Some(fields) = names.filter(|f| f.len() == items.len()) {
                                return Ok(Value::NamedTuple {
                                    name: name.clone(),
                                    fields,
                                    items: items.clone(),
                                });
                            }
                        }
                    }
                    if let Some(Value::List(items)) = obj.get(TUPLE_TAG) {
                        return Ok(Value::Tuple(items.clone()));
                    }
                    // The binary-format `Encoded` tag (#2448, #2458, #2466).
                    // A list payload like the tuple's, but of EXACTLY three
                    // strings and up to two optional trailing elements —
                    // anything else is a real dict and falls through, so a
                    // user dict under this key cannot forge one.
                    if let Some(Value::List(parts)) = obj.get(ENCODED_TAG) {
                        // ELEVEN elements: the #2480 shape — the equality
                        // CLASS appended after the items.
                        //
                        // Every slot below 10 keeps the FREE binding and the
                        // fail-to-absent read the ten-arm gives it — a
                        // malformed `len` / key / `attrs` / `items` still
                        // restores as "absent" rather than refusing the whole
                        // payload.
                        //
                        // Slot 10 is the exception, and it is the one that
                        // makes the width safe. Eleven used to be a width no
                        // build wrote, so
                        // `an_interior_insert_is_refused_rather_than_silently_misread`
                        // could rely on WIDTH to refuse a ten-element payload
                        // with one element inserted. Eleven is real now, so
                        // only a TYPE can refuse it — and every such insert
                        // pushes the ITEMS (a list or `nil`) or the intruder
                        // itself into this position, never a MAP. Requiring a
                        // map here is therefore exactly the predicate that
                        // refuses all eleven shifts, and it is why the writer
                        // spells an absent class as an EMPTY map rather than
                        // as `nil`.
                        if let [Value::String(type_name), Value::String(display), Value::String(json), Value::Bool(truthy), len, Value::Bool(iterable), Value::String(repr), key, attrs, items, Value::Object(eq_class)] =
                            parts.as_slice()
                        {
                            return Ok(Value::Encoded(Box::new(Encoded {
                                type_name: type_name.clone(),
                                display: display.clone(),
                                json: json.clone(),
                                truthy: *truthy,
                                len: match len {
                                    Value::Integer(n) if *n >= 0 => usize::try_from(*n).ok(),
                                    _ => None,
                                },
                                iterable: *iterable,
                                repr: repr.clone(),
                                cmp_key: decode_cmp_key(key),
                                attrs: match attrs {
                                    Value::Object(map) => map.clone(),
                                    _ => IndexMap::new(),
                                },
                                items: match items {
                                    Value::List(v) => Some(v.clone()),
                                    _ => None,
                                },
                                eq_class: decode_eq_class(eq_class),
                                // The handle is TRANSIENT (#2539): the wire
                                // never carried it, so a restored value has
                                // none — and under the lazy flag its `attrs`
                                // map is EMPTY too (the handle REPLACES the
                                // eager map, #2570). A restored handle-bearing
                                // value therefore answers NOTHING for a dotted
                                // lookup until the next full sync re-converts
                                // it (`update_state` from Python). Every
                                // framework render path runs that sync before
                                // the first render of a clone (the clone is
                                // only attached in `_initialize_rust_view`,
                                // behind `_rust_view is None`, on a Python view
                                // that has no change-detection baseline yet);
                                // a `RustLiveView` clone rendered WITHOUT one —
                                // the API-level contract — renders `''` for
                                // `{{ o.a }}`. See
                                // `test_restore_round_trip_contract_2570.py`.
                                live: None,
                                display_safe: false,
                            })));
                        }
                        // TEN elements: the #2477/#2489 shape — slot 5
                        // widened from the #2466 boolean to `len(o)` itself,
                        // and the enumerated items appended after the
                        // attribute map. The equality CLASS restores ABSENT,
                        // which is the answer the entry was WRITTEN with:
                        // never equal, never ordered (#2480).
                        //
                        // `len` reads back as a `Value::Integer` (msgpack
                        // writes a `u64` as an unsigned int, which the visitor
                        // above lands on `Integer`) or `Missing` (every `nil`
                        // reads back as `Missing`); anything else in that slot
                        // reads as "no `__len__`", the same fail-to-absent
                        // every optional slot takes. `items` reads back as a
                        // `List` or not at all, and the two are NOT the same
                        // answer — absent is "never enumerated".
                        //
                        // This arm is tried FIRST, and the widths below are
                        // disjoint from it, so ordering is not load-bearing.
                        if let [Value::String(type_name), Value::String(display), Value::String(json), Value::Bool(truthy), len, Value::Bool(iterable), Value::String(repr), key, attrs, items] =
                            parts.as_slice()
                        {
                            return Ok(Value::Encoded(Box::new(Encoded {
                                type_name: type_name.clone(),
                                display: display.clone(),
                                json: json.clone(),
                                truthy: *truthy,
                                len: match len {
                                    Value::Integer(n) if *n >= 0 => usize::try_from(*n).ok(),
                                    _ => None,
                                },
                                iterable: *iterable,
                                repr: repr.clone(),
                                cmp_key: decode_cmp_key(key),
                                attrs: match attrs {
                                    Value::Object(map) => map.clone(),
                                    _ => IndexMap::new(),
                                },
                                items: match items {
                                    Value::List(v) => Some(v.clone()),
                                    _ => None,
                                },
                                eq_class: None,
                                // The handle is TRANSIENT (#2539): the wire
                                // never carried it, so a restored value has
                                // none — and under the lazy flag its `attrs`
                                // map is EMPTY too (the handle REPLACES the
                                // eager map, #2570). A restored handle-bearing
                                // value therefore answers NOTHING for a dotted
                                // lookup until the next full sync re-converts
                                // it (`update_state` from Python). Every
                                // framework render path runs that sync before
                                // the first render of a clone (the clone is
                                // only attached in `_initialize_rust_view`,
                                // behind `_rust_view is None`, on a Python view
                                // that has no change-detection baseline yet);
                                // a `RustLiveView` clone rendered WITHOUT one —
                                // the API-level contract — renders `''` for
                                // `{{ o.a }}`. See
                                // `test_restore_round_trip_contract_2570.py`.
                                live: None,
                                display_safe: false,
                            })));
                        }
                        // NINE elements: the #2481 shape, the attribute map
                        // appended after the comparison key. The map is a real
                        // msgpack map, so it reads back as a `Value::Object`;
                        // anything else in that slot reads as NO attributes
                        // rather than as a guess, which is the same
                        // fail-to-absent `cmp_key` takes one slot over.
                        //
                        // A user dict cannot forge one through this arm: the
                        // four `_TAG` constants all start with `_`, and every
                        // producer of this map skips `_`-prefixed names.
                        if let [Value::String(type_name), Value::String(display), Value::String(json), Value::Bool(truthy), Value::Bool(sized_empty), Value::Bool(iterable), Value::String(repr), key, attrs] =
                            parts.as_slice()
                        {
                            return Ok(Value::Encoded(Box::new(Encoded {
                                type_name: type_name.clone(),
                                display: display.clone(),
                                json: json.clone(),
                                truthy: *truthy,
                                // Slot 5 is a BOOLEAN in this width, and every
                                // value ever written in it was declined by
                                // `falsy_opaque` unless its `len` was exactly
                                // 0 — so `true` restores as `Some(0)` and
                                // `false` as "no `__len__`", which is the
                                // answer the entry was written with.
                                len: if *sized_empty { Some(0) } else { None },
                                iterable: *iterable,
                                repr: repr.clone(),
                                cmp_key: decode_cmp_key(key),
                                attrs: match attrs {
                                    Value::Object(map) => map.clone(),
                                    _ => IndexMap::new(),
                                },
                                // See the four-line note in the shorter widths
                                // below: this width predates the field.
                                items: None,
                                eq_class: None,
                                // The handle is TRANSIENT (#2539): the wire
                                // never carried it, so a restored value has
                                // none — and under the lazy flag its `attrs`
                                // map is EMPTY too (the handle REPLACES the
                                // eager map, #2570). A restored handle-bearing
                                // value therefore answers NOTHING for a dotted
                                // lookup until the next full sync re-converts
                                // it (`update_state` from Python). Every
                                // framework render path runs that sync before
                                // the first render of a clone (the clone is
                                // only attached in `_initialize_rust_view`,
                                // behind `_rust_view is None`, on a Python view
                                // that has no change-detection baseline yet);
                                // a `RustLiveView` clone rendered WITHOUT one —
                                // the API-level contract — renders `''` for
                                // `{{ o.a }}`. See
                                // `test_restore_round_trip_contract_2570.py`.
                                live: None,
                                display_safe: false,
                            })));
                        }
                        // Eight elements: the #2471/#2472 shape, `repr` and
                        // the comparison key carried after #2466's two bits.
                        // The key is `nil` or a three-integer array; anything
                        // else is not one this crate wrote, so it reads as
                        // absent rather than being guessed at.
                        if let [Value::String(type_name), Value::String(display), Value::String(json), Value::Bool(truthy), Value::Bool(sized_empty), Value::Bool(iterable), Value::String(repr), key] =
                            parts.as_slice()
                        {
                            return Ok(Value::Encoded(Box::new(Encoded {
                                type_name: type_name.clone(),
                                display: display.clone(),
                                json: json.clone(),
                                truthy: *truthy,
                                // Slot 5 is a BOOLEAN in this width, and every
                                // value ever written in it was declined by
                                // `falsy_opaque` unless its `len` was exactly
                                // 0 — so `true` restores as `Some(0)` and
                                // `false` as "no `__len__`", which is the
                                // answer the entry was written with.
                                len: if *sized_empty { Some(0) } else { None },
                                iterable: *iterable,
                                repr: repr.clone(),
                                cmp_key: decode_cmp_key(key),
                                // The field THIS width does not carry restores
                                // to the answer the entry was WRITTEN with: no
                                // attributes, which is what `{{ dt.year }}`
                                // resolved to before #2481. A stale entry
                                // behaves exactly as it did rather than
                                // half-way between.
                                attrs: IndexMap::new(),
                                // No items: this width predates the field, and
                                // every value written in it was declined by
                                // the enumeration gate or had none. `None` is
                                // "never enumerated" — the answer the entry
                                // was WRITTEN with — and `iter_values` reads
                                // `iterable` for it exactly as it did.
                                items: None,
                                eq_class: None,
                                // The handle is TRANSIENT (#2539): the wire
                                // never carried it, so a restored value has
                                // none — and under the lazy flag its `attrs`
                                // map is EMPTY too (the handle REPLACES the
                                // eager map, #2570). A restored handle-bearing
                                // value therefore answers NOTHING for a dotted
                                // lookup until the next full sync re-converts
                                // it (`update_state` from Python). Every
                                // framework render path runs that sync before
                                // the first render of a clone (the clone is
                                // only attached in `_initialize_rust_view`,
                                // behind `_rust_view is None`, on a Python view
                                // that has no change-detection baseline yet);
                                // a `RustLiveView` clone rendered WITHOUT one —
                                // the API-level contract — renders `''` for
                                // `{{ o.a }}`. See
                                // `test_restore_round_trip_contract_2570.py`.
                                live: None,
                                display_safe: false,
                            })));
                        }
                        // Six elements: the #2466 shape, `sized_empty` and
                        // `iterable` carried but not `repr` or the key.
                        if let [Value::String(type_name), Value::String(display), Value::String(json), Value::Bool(truthy), Value::Bool(sized_empty), Value::Bool(iterable)] =
                            parts.as_slice()
                        {
                            return Ok(Value::Encoded(Box::new(Encoded {
                                type_name: type_name.clone(),
                                display: display.clone(),
                                json: json.clone(),
                                truthy: *truthy,
                                // Slot 5 is a BOOLEAN in this width, and every
                                // value ever written in it was declined by
                                // `falsy_opaque` unless its `len` was exactly
                                // 0 — so `true` restores as `Some(0)` and
                                // `false` as "no `__len__`", which is the
                                // answer the entry was written with.
                                len: if *sized_empty { Some(0) } else { None },
                                iterable: *iterable,
                                // The two fields THIS shape does not carry
                                // restore to the answers that entry was WRITTEN
                                // with — `display` for `repr` (what `py_repr`
                                // delegated to before #2472) and no key (never
                                // equal, the pre-#2471 answer) — so a stale
                                // entry behaves exactly as it did rather than
                                // half-way between.
                                repr: display.clone(),
                                cmp_key: None,
                                attrs: IndexMap::new(),
                                // No items: this width predates the field, and
                                // every value written in it was declined by
                                // the enumeration gate or had none. `None` is
                                // "never enumerated" — the answer the entry
                                // was WRITTEN with — and `iter_values` reads
                                // `iterable` for it exactly as it did.
                                items: None,
                                eq_class: None,
                                // The handle is TRANSIENT (#2539): the wire
                                // never carried it, so a restored value has
                                // none — and under the lazy flag its `attrs`
                                // map is EMPTY too (the handle REPLACES the
                                // eager map, #2570). A restored handle-bearing
                                // value therefore answers NOTHING for a dotted
                                // lookup until the next full sync re-converts
                                // it (`update_state` from Python). Every
                                // framework render path runs that sync before
                                // the first render of a clone (the clone is
                                // only attached in `_initialize_rust_view`,
                                // behind `_rust_view is None`, on a Python view
                                // that has no change-detection baseline yet);
                                // a `RustLiveView` clone rendered WITHOUT one —
                                // the API-level contract — renders `''` for
                                // `{{ o.a }}`. See
                                // `test_restore_round_trip_contract_2570.py`.
                                live: None,
                                display_safe: false,
                            })));
                        }
                        // Four elements: the #2458 shape, `truthy` carried and
                        // `sized_empty` absent. Still readable because a Redis
                        // state backend hands one back across a rolling deploy.
                        if let [Value::String(type_name), Value::String(display), Value::String(json), Value::Bool(truthy)] =
                            parts.as_slice()
                        {
                            return Ok(Value::Encoded(Box::new(Encoded {
                                type_name: type_name.clone(),
                                display: display.clone(),
                                json: json.clone(),
                                truthy: *truthy,
                                // Every value written in the #2458 shape was a
                                // datetime, and none of the four has a
                                // `__len__` or an `__iter__`.
                                len: None,
                                iterable: false,
                                repr: display.clone(),
                                cmp_key: None,
                                attrs: IndexMap::new(),
                                // No items: this width predates the field, and
                                // every value written in it was declined by
                                // the enumeration gate or had none. `None` is
                                // "never enumerated" — the answer the entry
                                // was WRITTEN with — and `iter_values` reads
                                // `iterable` for it exactly as it did.
                                items: None,
                                eq_class: None,
                                // The handle is TRANSIENT (#2539): the wire
                                // never carried it, so a restored value has
                                // none — and under the lazy flag its `attrs`
                                // map is EMPTY too (the handle REPLACES the
                                // eager map, #2570). A restored handle-bearing
                                // value therefore answers NOTHING for a dotted
                                // lookup until the next full sync re-converts
                                // it (`update_state` from Python). Every
                                // framework render path runs that sync before
                                // the first render of a clone (the clone is
                                // only attached in `_initialize_rust_view`,
                                // behind `_rust_view is None`, on a Python view
                                // that has no change-detection baseline yet);
                                // a `RustLiveView` clone rendered WITHOUT one —
                                // the API-level contract — renders `''` for
                                // `{{ o.a }}`. See
                                // `test_restore_round_trip_contract_2570.py`.
                                live: None,
                                display_safe: false,
                            })));
                        }
                        // Three elements: the #2448 shape, still readable
                        // because a Redis state backend hands one back across
                        // a rolling deploy. `!display.is_empty()` is the
                        // truthiness that entry was written with.
                        if let [Value::String(type_name), Value::String(display), Value::String(json)] =
                            parts.as_slice()
                        {
                            return Ok(Value::Encoded(Box::new(Encoded {
                                type_name: type_name.clone(),
                                display: display.clone(),
                                json: json.clone(),
                                truthy: !display.is_empty(),
                                len: None,
                                iterable: false,
                                repr: display.clone(),
                                cmp_key: None,
                                attrs: IndexMap::new(),
                                // No items: this width predates the field, and
                                // every value written in it was declined by
                                // the enumeration gate or had none. `None` is
                                // "never enumerated" — the answer the entry
                                // was WRITTEN with — and `iter_values` reads
                                // `iterable` for it exactly as it did.
                                items: None,
                                eq_class: None,
                                // The handle is TRANSIENT (#2539): the wire
                                // never carried it, so a restored value has
                                // none — and under the lazy flag its `attrs`
                                // map is EMPTY too (the handle REPLACES the
                                // eager map, #2570). A restored handle-bearing
                                // value therefore answers NOTHING for a dotted
                                // lookup until the next full sync re-converts
                                // it (`update_state` from Python). Every
                                // framework render path runs that sync before
                                // the first render of a clone (the clone is
                                // only attached in `_initialize_rust_view`,
                                // behind `_rust_view is None`, on a Python view
                                // that has no change-detection baseline yet);
                                // a `RustLiveView` clone rendered WITHOUT one —
                                // the API-level contract — renders `''` for
                                // `{{ o.a }}`. See
                                // `test_restore_round_trip_contract_2570.py`.
                                live: None,
                                display_safe: false,
                            })));
                        }
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

/// The four `datetime` types and a live `DjangoJSONEncoder`, resolved once per
/// interpreter (#2448).
struct JsonEncoderTypes {
    /// `(datetime, date, time, timedelta)`, for ONE `isinstance` on the
    /// negative path — which is the path almost every value takes.
    any: Py<PyAny>,
    datetime: Py<PyAny>,
    date: Py<PyAny>,
    time: Py<PyAny>,
    timedelta: Py<PyAny>,
    /// A `DjangoJSONEncoder()` INSTANCE. The encoder is called rather than
    /// re-implemented: `default()` is the sink this defect is about, and a hand
    /// port would have to reproduce the microsecond truncation, the `+00:00`
    /// to `Z` rewrite and `duration_iso_string`'s negative-delta normalisation
    /// — three transcriptions, each of which the issue's own table got at least
    /// partly wrong.
    encoder: Py<PyAny>,
}

/// `DjangoJSONEncoder`'s spelling of a `datetime` / `date` / `time` /
/// `timedelta`, or `None` if this is not one of them (#2448).
///
/// Fails CLOSED, like [`is_decimal`] and [`big_int_digits`]: on any error the
/// answer is `None` and the value takes its previous path — the final
/// `Value::String(str(o))` fallback — so a missing or unconfigured Django, or
/// an encoder that raises, restores the pre-#2448 behaviour exactly rather than
/// breaking a render.
///
/// The one shape that reaches the raising case in practice is a TIMEZONE-AWARE
/// `datetime.time`, for which `default()` raises
/// `ValueError: JSON can't represent timezone-aware times.` — Django's
/// `json_script` propagates that as a 500 and djust keeps emitting the `str()`.
/// That is the refusal direction #2429 declined, unchanged here; it is NOT the
/// emitting divergence this function closes.
pub fn django_json_encoded(ob: &Bound<'_, PyAny>) -> Option<Encoded> {
    static TYPES: pyo3::sync::PyOnceLock<Option<JsonEncoderTypes>> = pyo3::sync::PyOnceLock::new();
    let py = ob.py();
    let types = TYPES
        .get_or_init(py, || {
            let dt_mod = py.import("datetime").ok()?;
            let datetime = dt_mod.getattr("datetime").ok()?;
            let date = dt_mod.getattr("date").ok()?;
            let time = dt_mod.getattr("time").ok()?;
            let timedelta = dt_mod.getattr("timedelta").ok()?;
            let any = pyo3::types::PyTuple::new(py, [&datetime, &date, &time, &timedelta]).ok()?;
            let encoder = py
                .import("django.core.serializers.json")
                .ok()?
                .getattr("DjangoJSONEncoder")
                .ok()?
                .call0()
                .ok()?;
            Some(JsonEncoderTypes {
                any: any.into_any().unbind(),
                datetime: datetime.unbind(),
                date: date.unbind(),
                time: time.unbind(),
                timedelta: timedelta.unbind(),
                encoder: encoder.unbind(),
            })
        })
        .as_ref()?;

    // The cheap negative: one `PyObject_IsInstance` against the 4-tuple.
    if !ob.is_instance(types.any.bind(py)).unwrap_or(false) {
        return None;
    }

    // `datetime` BEFORE `date`, because `datetime` IS a `date` subclass — the
    // same ordering `DjangoJSONEncoder.default` itself uses, and getting it
    // backwards would spell every datetime as a bare `2020-01-01`.
    let (cls, tp_name) = [
        (&types.datetime, "datetime.datetime"),
        (&types.date, "datetime.date"),
        (&types.time, "datetime.time"),
        (&types.timedelta, "datetime.timedelta"),
    ]
    .into_iter()
    .find(|(cls, _)| ob.is_instance(cls.bind(py)).unwrap_or(false))?;

    let display = ob.str().ok()?.extract::<String>().ok()?;
    let json = types
        .encoder
        .bind(py)
        .call_method1("default", (ob,))
        .ok()?
        .extract::<String>()
        .ok()?;

    // CPython's `tp_name`, measured rather than derived (see [`Value::Encoded`]):
    // a static C type carries its DOTTED name into a `TypeError`
    // (`'datetime.datetime' object is not subscriptable`) while a Python-level
    // SUBCLASS carries the bare `__name__` (`'MyDT' object is not …`). So the
    // builtin names are literals reached by an identity check, and only a
    // subclass asks Python.
    //
    // `__name__`, NOT `__qualname__`: a heap type's `tp_name` is the name it
    // was created with, so a class defined inside a function is `MyDT` where
    // its `__qualname__` is `outer.<locals>.MyDT`. Caught by running CPython
    // against a nested subclass rather than by reading the docs.
    let ty = ob.get_type();
    let type_name = if ty.is(cls.bind(py)) {
        tp_name.to_string()
    } else {
        ty.getattr("__name__").ok()?.extract::<String>().ok()?
    };

    // Python's own answer, not a re-derivation from either spelling (#2458).
    // `is_truthy` is `PyObject_IsTrue`, so a subclass overriding `__bool__`
    // (or `__len__`) is answered by the object rather than by this function's
    // idea of the family. Fails closed with the rest: a raising `__bool__`
    // takes the whole value back to its pre-#2448 `Value::String` path.
    let truthy = ob.is_truthy().ok()?;

    // `repr(o)` (#2472). Python's answer, not a format string — see the field
    // doc for the three shapes a transcription would have to get right.
    let repr = ob.repr().ok()?.extract::<String>().ok()?;

    // Python's ordering, measured (#2471). Fails SOFT to `None`, unlike the
    // fields above: a value with no key keeps the pre-fix comparison answer,
    // where a value with no `display` would have nothing to render.
    let cmp_key = comparison_key(ob, tp_name);

    // The attributes Django's lookup step 2 reaches (#2481). Keyed off the
    // `tp_name` already resolved above rather than a second `isinstance`
    // sweep — the same value, one string compare over four entries.
    //
    // NOT the `type_name` computed just above: that is the SUBCLASS's
    // `__name__` for a Python-level subclass (`MyDT`), which is right for a
    // `TypeError` message and wrong as a lookup key here. A `datetime`
    // subclass has a `datetime`'s attributes.
    let mut attrs = ENCODED_ATTR_NAMES
        .iter()
        .find(|(name, _)| *name == tp_name)
        .map(|(_, names)| collect_named_attrs(ob, names))
        .unwrap_or_default();

    // The names Django's auto-call reaches (#2485), keyed off the same
    // `tp_name` and merged into the SAME map — so `lookup_segment` keeps one
    // reader for both halves of Django's step 2. The two tables share no name
    // (a `datetime` has no attribute that is both data and callable), which
    // `test_the_two_tables_are_disjoint` asserts rather than assumes; the
    // `extend` order is fixed and deterministic either way, which is what
    // `values_structurally_equal`'s zipped compare needs.
    attrs.extend(
        ENCODED_CALL_NAMES
            .iter()
            .find(|(name, _)| *name == tp_name)
            .map(|(_, names)| collect_called_attrs(ob, names))
            .unwrap_or_default(),
    );

    Some(Encoded {
        type_name,
        display,
        json,
        truthy,
        // A `datetime` / `date` / `time` / `timedelta` has no `__len__`, is
        // NOT iterable in Python, and `{% for %}` over one raises on both
        // engines today (#2466). `None` / `false` / `None` on the three is
        // what keeps that true.
        len: None,
        iterable: false,
        repr,
        cmp_key,
        attrs,
        items: None,
        eq_class: None,
        // Preserve the immutable temporal object across Python filter calls.
        // The wire still carries the measured fields; live handles are transient.
        live: Some(std::sync::Arc::new(ob.clone().unbind())),
        display_safe: false,
    })
}

/// Is this `__dict__` key one a template can reach? (#2478)
///
/// The ONE statement of the `_`-prefix rule, with two callers by design:
/// [`public_dict_attrs`], which BUILDS the map, and
/// [`has_public_dict_attrs`], which only asks whether it would be empty. Two
/// copies of a filter that decide the same question about the same keys is the
/// #1646 shape; the same argument [`public_dict_attrs`]'s own doc makes about
/// its two callers, one level down.
///
/// It is Django's `_resolve_lookup` convention (`Variable.__init__` refuses a
/// path segment starting with `_`) and what keeps a user attribute from
/// colliding with the four `_TAG` constants the codec reserves.
fn is_public_attr_name(name: &str) -> bool {
    !name.starts_with('_')
}

/// Does this object have at least one public `__dict__` attribute? (#2477)
///
/// The KEYS only. [`opaque_value`] needs this to decide one thing — whether an
/// object belongs to the `__dict__` bulk-dump arm — and building the map to
/// answer it would convert every attribute VALUE through
/// `extract::<Value>()`, recursively, and then throw the result away for the
/// arm below to build again. That is the ordinary case (a presenter, a service
/// object, any plain instance in a template context), so paying for it twice
/// per value per render is not a rounding error.
///
/// `false` for an object with no `__dict__` at all — a C type, or one with
/// `__slots__` — which is what [`public_dict_attrs`] returns `None` for, and
/// the two agree because the question they answer is the same one.
fn has_public_dict_attrs(ob: &Bound<'_, PyAny>) -> bool {
    let Ok(obj_dict) = ob.getattr("__dict__") else {
        return false;
    };
    let Ok(items) = obj_dict.cast::<PyDict>() else {
        return false;
    };
    items
        .keys()
        .iter()
        .filter_map(|k| k.extract::<String>().ok())
        .any(|k| is_public_attr_name(&k))
}

/// An object's PUBLIC `__dict__`, as a [`Value::Object`]'s map (#2478).
///
/// The ONE statement of "which attributes does an ordinary Python object
/// expose to a template", and it has TWO callers by design: the `__dict__`
/// bulk-dump arm of [`FromPyObject`], which turns the map into a
/// `Value::Object`, and [`opaque_value`], which carries it on the `Encoded`.
/// Those two arms decide the same question about the same objects and are
/// selected between by the object's TRUTHINESS — so a second copy of this
/// filter is the #1646 shape, one arm growing a rule the other does not. It
/// was two copies for exactly as long as it took to write the second.
///
/// **Iterated as a `PyDict`**, not through `extract::<HashMap<..>>()` (#2203
/// review): a std `HashMap` randomises iteration order PER INSTANCE and a
/// fresh one is built on every conversion, so extracting through one made
/// `{{ obj }}` reorder on every render rather than merely between restarts.
///
/// **`_`-prefixed names are skipped**, which is both Django's `_resolve_lookup`
/// convention (`Variable.__init__` refuses a path segment starting with `_`)
/// and what keeps a user attribute from colliding with the four `_TAG`
/// constants the codec reserves.
///
/// Returns `None` when the object has no `__dict__` at all — a C type, or one
/// with `__slots__` — which is DIFFERENT from an empty one and is what lets
/// the caller tell "no attributes" from "not that kind of object".
fn public_dict_attrs(ob: &Bound<'_, PyAny>) -> Option<IndexMap<ObjectKey, Value>> {
    let obj_dict = ob.getattr("__dict__").ok()?;
    let items = obj_dict.cast::<PyDict>().ok()?;
    // The `_`-prefix rule is [`is_public_attr_name`], so this builder and the
    // key-only probe beside it cannot disagree about which names are public
    // (#1646).
    //
    // Snapshotted into an owned `Vec` BEFORE any recursive
    // `v.extract::<Value>()` call (#2510). `items.iter()` is a LIVE PyO3
    // iterator directly over `ob.__dict__`; extracting one attribute's VALUE
    // can run arbitrary Python (any dunder check on an unresolved
    // `SimpleLazyObject` — e.g. Django's `request.user` before anything has
    // forced it — triggers `_setup()`). Django's own
    // `AuthenticationMiddleware.get_user` resolves it by doing
    // `request._cached_user = auth.get_user(request)`, which writes a NEW
    // key into `request.__dict__` — the EXACT dict this loop is iterating.
    // The template need not even reference `.user`: this walk dumps the
    // WHOLE `__dict__` regardless of which attribute was asked for, so any
    // object with an unresolved lazy attribute anywhere in its `__dict__`
    // hits this, not just one whose lazy attribute happens to be requested.
    // Collecting into a `Vec` first fully drains the PyO3 iterator before any
    // Python callback can run, so a later mutation has nothing left to
    // invalidate.
    let pairs: Vec<(Bound<'_, PyAny>, Bound<'_, PyAny>)> = items.iter().collect();
    // Attribute names, so the keys stay `ObjectKey::Str` — a `__dict__`
    // cannot have a non-string key.
    let mut map: IndexMap<ObjectKey, Value> = IndexMap::new();
    for (k, v) in pairs {
        let Ok(k) = k.extract::<String>() else {
            continue;
        };
        // Skip private/dunder attrs and Django's internal `_state`.
        if !is_public_attr_name(&k) {
            continue;
        }
        if let Ok(val) = v.extract::<Value>() {
            map.insert(ObjectKey::Str(k), val);
        }
    }
    Some(map)
}

/// `getattr(o, name)` for each `name`, as a [`Value::Object`]'s map (#2481).
///
/// The ONE producer of [`Encoded::attrs`]. A name the object does not have, or
/// whose value will not convert, is SKIPPED rather than stored as `Missing` —
/// an absent key is what makes `lookup_segment` answer `None` and the render
/// fall through to the raw-Python sidecar (where there is one) exactly as it
/// did before this map existed.
///
/// Every value is MEASURED off the live object. Not one of these is derivable
/// from the strings `Encoded` already carries — `str(timedelta(days=3,
/// seconds=90))` is `"3 days, 0:01:30"` and parsing `.days` back out of it
/// would be a transcription with a per-value branch, which is the shape #2472
/// nearly shipped by cloning `display` into `repr`.
///
/// The caller decides the names, and that is the whole of the recursion
/// argument: [`ENCODED_ATTR_NAMES`] lists no attribute whose value is another
/// object of the same family, so `v.extract::<Value>()` below cannot re-enter
/// [`django_json_encoded`] on a value that would ask for the same names again.
/// `datetime.min.min is datetime.min`, so a list containing `min` would not
/// terminate.
fn collect_named_attrs(ob: &Bound<'_, PyAny>, names: &[&str]) -> IndexMap<ObjectKey, Value> {
    let mut map = IndexMap::with_capacity(names.len());
    for name in names {
        let Ok(attr) = ob.getattr(*name) else {
            continue;
        };
        let Ok(value) = attr.extract::<Value>() else {
            continue;
        };
        map.insert(ObjectKey::Str((*name).to_string()), value);
    }
    map
}

/// `o.name()` for each `name`, into the same map (#2485).
///
/// The SECOND producer of [`Encoded::attrs`], and the auto-call half of
/// Django's lookup: `_resolve_lookup` CALLS a callable attribute, so
/// `{{ p.isoformat }}` renders `o.isoformat()`. The names come from
/// [`ENCODED_CALL_NAMES`], which states the whole of that policy.
///
/// Fails SOFT, per name, exactly as [`collect_named_attrs`] does: a `getattr`
/// that misses, a call that RAISES, or a result that will not convert is
/// SKIPPED rather than stored. That matters more here than for a plain
/// attribute read, because a call runs code the framework does not own — a
/// `tzinfo` subclass decides what `utcoffset()` / `tzname()` / `dst()` do, and
/// `timestamp()` on a naive value is platform-dependent and can raise. A
/// skipped name leaves `lookup_segment` answering `None`, which renders the
/// empty string — the pre-#2485 answer for that cell, so a raising call cannot
/// make any cell WORSE than it was.
///
/// The recursion argument is [`ENCODED_CALL_NAMES`]'s, and it is the same shape
/// as the other table's: no name here returns a value of this family whose own
/// tables ask for names that return back to it. `utcoffset` and `dst` return a
/// `timedelta`, whose only entry is `total_seconds` (a `float`); everything
/// else returns a `str`, an `int`, a `float` or `None`.
fn collect_called_attrs(ob: &Bound<'_, PyAny>, names: &[&str]) -> IndexMap<ObjectKey, Value> {
    let mut map = IndexMap::with_capacity(names.len());
    for name in names {
        let Ok(method) = ob.getattr(*name) else {
            continue;
        };
        let Ok(result) = method.call0() else {
            continue;
        };
        let Ok(value) = result.extract::<Value>() else {
            continue;
        };
        map.insert(ObjectKey::Str((*name).to_string()), value);
    }
    map
}

/// `(days, microseconds-within-the-day)` for a `datetime.timedelta`.
///
/// Read off the three NORMALISED attributes rather than derived from a total,
/// which is what keeps both limbs inside an `i64`: Python guarantees
/// `0 <= seconds < 86400` and `0 <= microseconds < 10**6` with the whole sign
/// carried by `days`, so the pair orders lexicographically exactly as the
/// `timedelta` does — including for negatives, which Python normalises the same
/// way (`timedelta(seconds=-1)` is `days=-1, seconds=86399`).
fn timedelta_limbs(ob: &Bound<'_, PyAny>) -> Option<(i64, i64)> {
    let days: i64 = ob.getattr("days").ok()?.extract().ok()?;
    let seconds: i64 = ob.getattr("seconds").ok()?.extract().ok()?;
    let micros: i64 = ob.getattr("microseconds").ok()?.extract().ok()?;
    Some((days, seconds.checked_mul(1_000_000)?.checked_add(micros)?))
}

/// Microseconds since midnight, for anything carrying `hour`/`minute`/
/// `second`/`microsecond`.
fn micros_of_day(ob: &Bound<'_, PyAny>) -> Option<i64> {
    let hour: i64 = ob.getattr("hour").ok()?.extract().ok()?;
    let minute: i64 = ob.getattr("minute").ok()?.extract().ok()?;
    let second: i64 = ob.getattr("second").ok()?.extract().ok()?;
    let micro: i64 = ob.getattr("microsecond").ok()?.extract().ok()?;
    Some(((hour * 60 + minute) * 60 + second) * 1_000_000 + micro)
}

/// `o.utcoffset()` as `(days, micros)`, or `None` for a NAIVE value.
///
/// Awareness is decided by `utcoffset()` returning `None`, NOT by
/// `tzinfo is None` — that is CPython's own rule, and the two differ for a
/// `tzinfo` whose `utcoffset()` returns `None`, which Python treats as naive.
/// A `utcoffset()` that RAISES takes the whole key to `None` with the rest of
/// the fail-soft chain rather than being read as naive.
fn utc_offset_limbs(ob: &Bound<'_, PyAny>) -> Option<Option<(i64, i64)>> {
    let off = ob.call_method0("utcoffset").ok()?;
    if off.is_none() {
        return Some(None);
    }
    Some(Some(timedelta_limbs(&off)?))
}

/// Carry `lo` back into range after an offset subtraction, moving whole days
/// into `hi`. One borrow is enough — a `utcoffset()` is bounded to
/// `(-24h, 24h)` — but the loop form is written so a future widening cannot
/// silently leave `lo` out of range.
fn normalise_limbs(mut hi: i64, mut lo: i64) -> (i64, i64) {
    const DAY: i64 = 86_400_000_000;
    while lo < 0 {
        lo += DAY;
        hi -= 1;
    }
    while lo >= DAY {
        lo -= DAY;
        hi += 1;
    }
    (hi, lo)
}

/// The [`CmpKey`] for one value of the datetime family, measured from the live
/// object (#2471).
///
/// `tp_name` is the LITERAL the caller matched the type against, so this
/// dispatches on the same `datetime`-before-`date` ordering the encoder does
/// rather than re-deriving it (#1646). A subclass reaches the arm of the
/// builtin it derives from, which is also how Python compares it.
fn comparison_key(ob: &Bound<'_, PyAny>, tp_name: &str) -> Option<CmpKey> {
    match tp_name {
        "datetime.timedelta" => {
            let (hi, lo) = timedelta_limbs(ob)?;
            Some(CmpKey {
                domain: CMP_DOMAIN_TIMEDELTA,
                hi,
                lo,
            })
        }
        "datetime.date" => Some(CmpKey {
            domain: CMP_DOMAIN_DATE,
            hi: ob.call_method0("toordinal").ok()?.extract().ok()?,
            lo: 0,
        }),
        "datetime.datetime" => {
            let ordinal: i64 = ob.call_method0("toordinal").ok()?.extract().ok()?;
            let lo = micros_of_day(ob)?;
            match utc_offset_limbs(ob)? {
                None => Some(CmpKey {
                    domain: CMP_DOMAIN_DATETIME_NAIVE,
                    hi: ordinal,
                    lo,
                }),
                // CPython compares two aware datetimes by their UTC instants,
                // so two spellings of the same moment in different zones are
                // EQUAL — which a compare on `display` or on `json` (both of
                // which keep the local wall clock and the offset) gets wrong.
                Some((off_hi, off_lo)) => {
                    let (hi, lo) = normalise_limbs(ordinal - off_hi, lo - off_lo);
                    Some(CmpKey {
                        domain: CMP_DOMAIN_DATETIME_AWARE,
                        hi,
                        lo,
                    })
                }
            }
        }
        "datetime.time" => match utc_offset_limbs(ob)? {
            None => Some(CmpKey {
                domain: CMP_DOMAIN_TIME_NAIVE,
                hi: 0,
                lo: micros_of_day(ob)?,
            }),
            // An AWARE `time` gets no key, and there is no aware-time domain.
            //
            // Not an oversight and not a gap: a timezone-aware `time` never
            // becomes a `Value::Encoded` at all. `DjangoJSONEncoder.default`
            // RAISES for it — `ValueError: JSON can't represent timezone-aware
            // times.` — so `django_json_encoded` fails closed above this and
            // the value stays the `Value::String(str(o))` it was before #2448.
            // That is the refusal direction #2429 declined, unchanged here.
            //
            // Writing a `CMP_DOMAIN_TIME_AWARE` arm anyway would be an
            // unreachable branch no test could cover — the decorative-code
            // shape #1859 is about. `None` is the conservative answer if the
            // path ever opens: never equal, never ordered, i.e. exactly what
            // this variant answered before #2471. Pinned in
            // `TestAnAwareTimeIsNotAnEncodedAtAll`.
            Some(_) => None,
        },
        _ => None,
    }
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

    /// Safety after Python str() conversion, for final rendering and stringfilter.
    /// This is not the original object's SafeData status: join must still escape
    /// an opaque object whose __str__ returns SafeString.
    pub fn string_conversion_is_safe(&self) -> bool {
        match self {
            Value::SafeString(_) => true,
            Value::Encoded(e) => e.display_safe,
            _ => false,
        }
    }

    pub fn is_safe_string(&self) -> bool {
        matches!(self, Value::SafeString(_))
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
            Value::String(s) | Value::SafeString(s) => !s.is_empty(),
            // Python's own `bool(o)`, asked at the conversion and carried
            // (#2458). #2448 read `!e.display.is_empty()` here — always true
            // for this family — which kept `bool(timedelta(0))` at `True`
            // where Python and Django say `False`. Carrying the bit rather
            // than deriving it is also what makes a `timedelta` SUBCLASS with
            // an overridden `__bool__` right, and what keeps the answer from
            // depending on a string comparison against `"0:00:00"` — which is
            // ALSO the display text of a perfectly ordinary truthy `str`.
            Value::Encoded(e) => e.truthy,
            Value::List(l) => !l.is_empty(),
            Value::Tuple(t) | Value::NamedTuple { items: t, .. } => !t.is_empty(),
            Value::Object(o) => !o.is_empty(),
            // `bool({}.keys())` is False; `bool({'a': 1}.keys())` is True.
            Value::DictView { items, .. } => !items.is_empty(),
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

thread_local! {
    /// ADR-027's kill-switch, per THREAD (#2539). Default `true` since
    /// movement 3 (#2539) flipped the shipped default.
    ///
    /// # Why this default has to track `config.py`'s
    ///
    /// This value is what a thread that never called
    /// `djust.render_env.apply_resolve_lazy` answers — a caller reaching
    /// `_rust.render_template` / `render_template_with_dirs` DIRECTLY, and
    /// `ComponentActor::render`, which never pushes. While movement 2 shipped
    /// the Python default OFF the two agreed; after the flip a `false` here
    /// would mean a fresh thread silently resolves by the OLD mechanism while
    /// every framework entry resolves by the new one — two defaults seeded
    /// from one intent, which is the #1646 shape. So the two literals move
    /// together, pinned by two tests that construct a FRESH `threading.Thread`
    /// — the only place this value is observable:
    /// `TestTheFlagReachesEveryRenderEntry2539::
    /// test_the_rust_default_tracks_the_python_default` and
    /// `TestTheSwitch2539::test_a_thread_that_never_pushed_reads_the_default`.
    /// Empirically confirmed: flipping this literal to `false` and rebuilding
    /// reddens exactly those two.
    ///
    /// NOT `TestThePlainEntriesAgree`, which an earlier draft of this comment
    /// named. That test compares the backend against the raw entries
    /// IN-PROCESS, on a pytest thread the backend has already pushed on — so
    /// the raw entry inherits the pushed value and never reads this default.
    /// It stayed green against the mutant. A pin you have not watched fail is
    /// a pin whose failure mode is unknown (#1859).
    ///
    /// Fail-direction: `true` is also the *closed* direction for the
    /// serialization floor. The old sidecar walk this replaces keeps
    /// `protect_sidecar`'s `Err(_) => obj` arm, the unguarded `get_item` that
    /// segfaults on a class object, and the `__dict__` dump; the sink has none
    /// of those. See `python/djust/render_env.py::apply_resolve_lazy`.
    static RESOLVE_LAZY: std::cell::Cell<bool> = const { std::cell::Cell::new(true) };
}

/// Set this thread's ADR-027 lazy-resolution flag (#2539).
///
/// # Why a thread-local and not a `Context` field
///
/// Half of the work this flag gates lives inside `impl FromPyObject for Value`
/// — a trait method with no `Context` and no config parameter, reached
/// recursively from every nested dict value and list element. A `Context`
/// field cannot reach it, so the two halves would need two mechanisms seeded
/// from one reader, which is the #1646 shape this repo keeps paying for.
///
/// It is a thread-local rather than a process global for the reason the
/// timezone (#2209) and the number format (#2221) are: djust renders run in
/// `sync_to_async` worker threads and two connections render concurrently.
/// It is pushed by `djust.render_env.apply_render_env`, beside those two —
/// the module that exists precisely so "a render path cannot acquire one
/// ambient setting and miss the other".
///
/// A nested render inherits the enclosing render's value, exactly as the
/// timezone does; a thread that never called `apply_render_env` reads the
/// shipped default (`true` since #2539 movement 3). That is also what
/// disposes of the `{% include … only %}`
/// fresh-`Context` problem `auto_call` has (`renderer.rs`'s `Context::new()`
/// there does not carry `auto_call`): a thread-local is not per-`Context`.
pub fn set_resolve_lazy(enabled: bool) {
    RESOLVE_LAZY.with(|c| c.set(enabled));
}

/// This thread's ADR-027 lazy-resolution flag (#2539). `true` by default
/// since movement 3; `LIVEVIEW_CONFIG["template_resolve_lazy"] = False` is
/// the escape hatch.
///
/// Read at exactly two sites, and that is the whole of the routing:
/// [`opaque_gate`]'s attribute-bearing decline (which decides whether an
/// ordinary object CARRIES a live handle rather than being bulk-dumped) and
/// `Context::resolve_without_builtins` (which decides whether a dotted lookup
/// WALKS that handle). Exposed so the setter can be tested end to end (#2017).
pub fn resolve_lazy() -> bool {
    RESOLVE_LAZY.with(|c| c.get())
}

/// The code points CPython's `repr()` escapes: the union of the general
/// categories `Cc`, `Cf`, `Cs`, `Co`, `Zl`, `Zp` and `Zs`, minus `U+0020`.
///
/// Generated from Unicode 16.0.0 (CPython 3.14). 139769 code points collapse
/// to 28 ranges, because the set is dominated by three contiguous private-use
/// blocks — which is why this is a hand-written table rather than a
/// Unicode-general-category dependency. See [`py_repr_string`] for why a fixed
/// table is correct here even though `str.isprintable()` is version-dependent.
///
/// `python/tests/test_py_repr_isprintable_table_2292.py` REGENERATES this set
/// from the running interpreter's `unicodedata` and asserts equality over
/// every assigned code point, so a future Unicode version that adds a `Cf`
/// fails the suite rather than silently drifting.
const NON_PRINTABLE: [(u32, u32); 28] = [
    (0x0000, 0x001F),     // Cc          (U+0020 SPACE excluded: printable)
    (0x007F, 0x00A0),     // Cc + Zs
    (0x00AD, 0x00AD),     // Cf  SOFT HYPHEN
    (0x0600, 0x0605),     // Cf
    (0x061C, 0x061C),     // Cf
    (0x06DD, 0x06DD),     // Cf
    (0x070F, 0x070F),     // Cf
    (0x0890, 0x0891),     // Cf
    (0x08E2, 0x08E2),     // Cf
    (0x1680, 0x1680),     // Zs  OGHAM SPACE MARK
    (0x180E, 0x180E),     // Cf
    (0x2000, 0x200F),     // Zs + Cf     (includes U+200B ZERO WIDTH SPACE)
    (0x2028, 0x202F),     // Zl + Zp + Zs + Cf
    (0x205F, 0x2064),     // Zs + Cf
    (0x2066, 0x206F),     // Cf
    (0x3000, 0x3000),     // Zs  IDEOGRAPHIC SPACE
    (0xD800, 0xF8FF),     // Cs + Co     (Cs unreachable from a Rust `char`)
    (0xFEFF, 0xFEFF),     // Cf  ZERO WIDTH NO-BREAK SPACE
    (0xFFF9, 0xFFFB),     // Cf
    (0x110BD, 0x110BD),   // Cf
    (0x110CD, 0x110CD),   // Cf
    (0x13430, 0x1343F),   // Cf
    (0x1BCA0, 0x1BCA3),   // Cf
    (0x1D173, 0x1D17A),   // Cf
    (0xE0001, 0xE0001),   // Cf
    (0xE0020, 0xE007F),   // Cf
    (0xF0000, 0xFFFFD),   // Co  private use plane 15
    (0x100000, 0x10FFFD), // Co  private use plane 16
];

/// Whether CPython's `str.isprintable()` is false for `c`, for every code
/// point assigned on any interpreter djust supports.
///
/// See [`py_repr_string`] for the measurement behind "for every assigned code
/// point" and for the unassigned-code-point residual.
fn is_py_non_printable(c: char) -> bool {
    let cp = c as u32;
    // ASCII fast path: printable is U+0020..=U+007E, so only C0 and DEL are
    // escaped. This is also the only branch the overwhelming majority of real
    // strings ever reach.
    if cp < 0x80 {
        return cp < 0x20 || cp == 0x7F;
    }
    NON_PRINTABLE
        .binary_search_by(|&(lo, hi)| {
            if cp < lo {
                std::cmp::Ordering::Greater
            } else if cp > hi {
                std::cmp::Ordering::Less
            } else {
                std::cmp::Ordering::Equal
            }
        })
        .is_ok()
}

/// Python's `repr()` of a `str`, for every code point CPython spells the same
/// way on every interpreter this project supports.
///
/// The ONE definition of the quoted-string spelling (#1646): `Value::py_repr`
/// renders it for a nested string, and `djust_templates`' `pprint` port renders
/// it for every scalar it lays out. A second escaper here is how the `{{ list }}`
/// path and the `pprint` path drifted in the first place — `pprint` used a bare
/// `format!("'{s}'")` that escaped nothing at all.
///
/// # Which code points are escaped, and why a fixed table is right after all
///
/// CPython escapes every code point for which `str.isprintable()` is false,
/// and that predicate is Unicode-version data. This escaper originally stopped
/// at ASCII on the reasoning that the reference moves (the `striptags`
/// argument, #2273). It does move — and by MORE than #2292 measured. Across
/// djust's whole supported matrix, `python3.10`–`3.14` carry FIVE different
/// Unicode versions and disagree about **11130** code points, not the 5812 the
/// issue reported from a single 3.12-vs-3.14 pair (which also missed that 3.13
/// carries 15.1, not 15.0):
///
/// | interpreter | `unidata_version` | printable code points |
/// |---|---|---|
/// | 3.10 | 13.0.0 | 143680 |
/// | 3.11 | 14.0.0 | 144516 |
/// | 3.12 | 15.0.0 | 148998 |
/// | 3.13 | 15.1.0 | 149625 |
/// | 3.14 | 16.0.0 | 154810 |
///
/// But the drift has a SHAPE, and that is what makes a fixed table correct.
/// Measured end to end (13.0 → 16.0): of those 11130 code points, **11130
/// became printable and 0 became non-printable**. Every single change is a
/// code point going from *unassigned* (`Cn`) to assigned — 9473 `Lo`, 945
/// `So`, 183 `Mn` and so on. Nothing that was ever printable stopped being
/// printable, and nothing already assigned was reclassified.
///
/// So `not str.isprintable()` decomposes into two parts:
///
/// * the seven categories `Cc`, `Cf`, `Cs`, `Co`, `Zl`, `Zp`, `Zs` — which
///   over 13.0 → 16.0 gained **exactly one** member among already-assigned
///   code points, and that member is `U+0020 SPACE`, which Python
///   special-cases as printable anyway. This part is *stable*, and it is the
///   table in [`NON_PRINTABLE`];
/// * plus `Cn`, which is the entire moving part.
///
/// This escapes the seven categories and treats `Cn` as printable. That rule
/// reproduces `str.isprintable()` **exactly for every code point assigned on
/// every interpreter in the matrix** — the residual disagreement is the
/// unassigned space and nothing else, which is precisely where the
/// interpreters already disagree with each other. The claim is not asserted
/// here but recomputed against the running interpreter's own `unicodedata` by
/// `python/tests/test_py_repr_isprintable_table_2292.py`, so it goes red on
/// whichever runner it stops being true for.
///
/// Choosing "never escape `Cn`" over "escape `Cn` per some pinned Unicode
/// version" is deliberate: the former is version-INDEPENDENT, so djust's
/// output is identical on all five interpreters. Pinning a version would make
/// djust exact on one runner and wrong by up to 11130 code points on the
/// others — the failure #2292 was right to refuse.
///
/// **The documented residual**: an unassigned code point is emitted literally
/// where CPython emits `\uXXXX`. Unassigned code points do not occur in real
/// template data, and no fixed table can do better than this without becoming
/// wrong somewhere else.
///
/// Two representational notes. `Cs` (surrogates) is in the table for
/// completeness but is unreachable — a Rust `char` cannot hold one, so a lone
/// surrogate cannot cross the PyO3 boundary at all. And `U+0020` is excluded
/// from the table itself rather than special-cased at the call site.
///
/// See also the module docs of `djust_templates::pprint`.
pub fn py_repr_string(s: &str) -> String {
    // Python's quote rule: single quotes, UNLESS the string contains a `'` and
    // no `"` — then double quotes, with the `'` left unescaped.
    // `repr("a'b")` is `"a'b"`, not `'a\'b'`.
    let quote = if s.contains('\'') && !s.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::with_capacity(s.len() + 2);
    out.push(quote);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            _ if c == quote => {
                out.push('\\');
                out.push(c);
            }
            '\t' => out.push_str("\\t"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            // Everything else CPython considers non-printable. `\x1b` really
            // is spelled `\x1b` — there is no `\e`, and no `\v`/`\f`/`\a`/`\b`
            // either. The width of the escape is chosen by magnitude, exactly
            // as CPython's `unicode_repr` does it.
            _ if is_py_non_printable(c) => {
                let cp = c as u32;
                if cp < 0x100 {
                    out.push_str(&format!("\\x{cp:02x}"));
                } else if cp < 0x10000 {
                    out.push_str(&format!("\\u{cp:04x}"));
                } else {
                    out.push_str(&format!("\\U{cp:08x}"));
                }
            }
            _ => out.push(c),
        }
    }
    out.push(quote);
    out
}

impl Value {
    /// The `"__str__"` a serialized Python OBJECT carries, if this is one.
    ///
    /// `Value::Object` is two different Python things wearing one shape: a
    /// genuine `dict`, and any non-dict object the Python serializer flattened
    /// into a map. The marker that tells them apart is a `"__str__"` entry
    /// holding a string, which every model-serialization site stamps —
    /// `_serialize_model_safely`, the depth-limited FK and max-depth
    /// shorthands, `jit.py`'s identity-only subset, and the two
    /// `template/rendering.py` fallbacks. `"__model__"` looks like the more
    /// specific marker and is NOT usable: FOUR of those SIX sites omit it —
    /// only `_serialize_model_safely` and `jit.py`'s subset stamp it (#2322).
    ///
    /// The predicate was written out twice, in the two `Display` impls, before
    /// `length` needed it as well (#2294) — the point at which two copies
    /// becomes a drift class (#1646). One definition now; the callers are
    /// pinned by `test_object_str_is_the_only_model_marker_predicate`.
    pub fn object_str(&self) -> Option<&str> {
        match self {
            // A non-`String` `"__str__"` (an upstream bug producing
            // `"__str__": null`) is NOT a marker: `Display` falls back to dict
            // repr for it, so this must too or the two disagree.
            Value::Object(o) => match o.get("__str__") {
                Some(Value::String(s)) => Some(s.as_str()),
                _ => None,
            },
            _ => None,
        }
    }

    /// Python `str()`.
    ///
    /// Sibling of [`Value::py_repr`], and the same `Float`/`Decimal` split one
    /// nesting level out: `str()` is what Django's `@stringfilter` hands its
    /// 28 decorated built-ins, and what `mark_safe(obj)` — `SafeString(str(obj))`
    /// — makes of ANY input, which is `|safe` (#2303) and `|safeseq` (#2324).
    ///
    /// **Not `Display`, for two variants.** `Display` is Django's
    /// `numberformat.format()` — the RENDER form — which expands an exponent
    /// (#2214, #2258):
    ///
    /// ```text
    ///                      py_str()   Display
    /// 1e20                 1e+20      100000000000000000000
    /// Decimal("1E-9")      1E-9       0.000000001
    /// ```
    ///
    /// Django really does spell one number two ways depending on which path it
    /// takes, so djust needs both spellings and neither is a special case: the
    /// renderer keeps `Display`, and everything that wants Python's `str()`
    /// calls this. The coercion is free for a `Decimal` — `Value::Decimal`
    /// already CARRIES `str(Decimal)`, built from `ob.str()` at the PyO3
    /// boundary, and it is `Display` that applies the expansion.
    ///
    /// Every other variant's `Display` already IS Python's `str()`, including
    /// `Missing` (`""`, Django's `string_if_invalid` substituted before the
    /// chain runs) and `Object` (its `__str__` for a model, dict repr
    /// otherwise). ONE definition, so no caller re-derives the split (#1646).
    pub fn py_str(&self) -> String {
        match self {
            Value::Decimal(d) => d.clone(),
            Value::Float(f) => decimal::python_float_repr(*f),
            other => other.to_string(),
        }
    }

    /// Python `repr()`, used for values NESTED inside a container.
    ///
    /// `str(['a'])` is `"['a']"` while `str('a')` is `"a"` — a nested string is
    /// quoted, a top-level one is not. Containers therefore cannot reuse
    /// `Display` for their elements.
    ///
    /// The `Decimal`/`Float` arms are [`Value::py_str`]'s split with `repr`'s
    /// own wrapping applied: `repr(Decimal('19.99'))` is the constructor form,
    /// and a nested float is spelled by `repr` exactly as `py_str` spells a
    /// top-level one.
    pub fn py_repr(&self) -> String {
        match self {
            Value::String(s) | Value::SafeString(s) => py_repr_string(s),
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
            // Python's own `repr(o)`, carried since #2472. The delegation below
            // gave `display` — `str(o)` — so a datetime NESTED in a list or
            // dict rendered `[0:00:00]` where Django renders
            // `[datetime.timedelta(0)]`, and `{{ p|stringformat:"r" }}` (which
            // is this function, padded) rendered the same wrong spelling. The
            // fourth site of the str/repr split the `Decimal` arm two lines up
            // and the `Float` arm one line up already carry.
            Value::Encoded(e) => e.repr.clone(),
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
            Value::String(s) | Value::SafeString(s) => write!(f, "{s}"),
            // The DISPLAY spelling on both display paths — an `Encoded` is
            // exactly the `Value::String(str(o))` this used to be, plus the two
            // fields nothing outside `json_script` and the refusal filters
            // reads (#2448).
            Value::Encoded(e) => write!(f, "{}", e.display),
            // A dict VIEW joins the `[List]` placeholder rather than naming
            // itself (#2340), and that is deliberate: this arm is the
            // pre-#2203 rendering, and before #2340 a view WAS a
            // `Value::List`, so `[List]` is exactly what `{{ d.items }}`
            // printed here. Spelling it `dict_items([…])` under the flag would
            // make a legacy-rendering switch less legacy.
            //
            // The first version of this change did name it, on a comment
            // asserting "the container spelling is Python's on BOTH display
            // paths" — a prose invariant that had never been run. The gate-off
            // surfaced it as a surviving mutation and the test written to close
            // that gap failed on the first execution (CLAUDE.md #1867).
            Value::List(_)
            | Value::Tuple(_)
            | Value::NamedTuple { .. }
            | Value::DictView { .. } => write!(f, "[List]"),
            Value::Object(_) => match self.object_str() {
                Some(s) => write!(f, "{s}"),
                None => write!(f, "[Object]"),
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
            Value::String(s) | Value::SafeString(s) => write!(f, "{s}"),
            // See the `legacy_display` arm: the display spelling is `str(o)`
            // on both paths, and only `json_script` reads the other one.
            Value::Encoded(e) => write!(f, "{}", e.display),
            Value::List(items) => {
                let inner: Vec<String> = items.iter().map(Value::py_repr).collect();
                write!(f, "[{}]", inner.join(", "))
            }
            Value::NamedTuple {
                name,
                fields,
                items,
            } => {
                let inner: Vec<String> = fields
                    .iter()
                    .zip(items)
                    .map(|(field, item)| format!("{field}={}", item.py_repr()))
                    .collect();
                write!(f, "{name}({})", inner.join(", "))
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
            // `dict_items([('a', 1)])` — the container names itself, and its
            // elements go through `py_repr` like a list's. Measured against
            // Django rather than assumed, including that an EMPTY view still
            // prints `dict_keys([])` (#2340).
            //
            // This is not a cosmetic detail: a third of the filter registry
            // (`truncatewords`, `wordcount`, `linebreaks`, `stringformat`,
            // `striptags`, `pprint`, `escape`, `safe`, `yesno`, `make_list`)
            // operates on this exact text.
            Value::DictView { kind, items } => {
                let inner: Vec<String> = items.iter().map(Value::py_repr).collect();
                write!(f, "{}([{}])", kind.container_name(), inner.join(", "))
            }
            Value::Object(o) => match self.object_str() {
                // A model instance carries `__str__`; that keeps winning over
                // dict repr, which is how `{{ obj }}` renders a model.
                Some(s) => write!(f, "{s}"),
                None => {
                    let inner: Vec<String> = o
                        .iter()
                        // Keys go through `py_repr` too (#2203 review): a hand-rolled
                        // escaper here missed the BACKSLASH, so a key like `a\`
                        // emitted `{'a\': 1}` where the closing quote reads as
                        // escaped. Two escapers, one wrong.
                        .map(|(k, v)| format!("{}: {}", k.py_repr(), v.py_repr()))
                        .collect();
                    write!(f, "{{{}}}", inner.join(", "))
                }
            },
        }
    }
}

/// The `(key, value)` pairs of a Django `MultiValueDict` / `QueryDict`, one
/// per key with the LAST value — `QueryDict.__getitem__`'s rule, and what
/// Django renders for `{{ qd.a }}` on `?a=1&a=2` (#2556).
///
/// A `MultiValueDict` is a `dict` subclass whose STORAGE holds a list per
/// key behind a last-value `items()`. Every converter in this workspace
/// reaches a mapping through `cast::<PyDict>()` and iterates the storage, so
/// the raw object rendered `['1', '2']` for `{{ qd.a }}`, `''` for
/// `{{ qd.page|add:1 }}` and a false `{% if qd.page == '3' %}` (PR #2596
/// Stage 11). Rebuilding it as a plain dict in Python is not an option on
/// the plain-backend path: its raw-Python sidecar is derived from the SAME
/// dict the values are extracted from (`entry_sidecar`), and
/// `{% querystring my_qd a=2 %}` needs the real object there. So the
/// object stays raw at the boundary and the converters read it as Django
/// does.
///
/// `None` for anything that is not one: an exact `dict` costs a single type
/// check and no attribute lookup; a subclass is recognised by the
/// `getlist` + `lists` pair that `MultiValueDict` defines and a plain
/// mapping does not. One helper, three converters (`FromPyObject for Value`,
/// `python_to_value`, `python_to_json_value`) — the #1646 shape.
pub fn multi_value_dict_pairs<'py>(
    ob: &Bound<'py, PyAny>,
) -> Option<Vec<(Bound<'py, PyAny>, Bound<'py, PyAny>)>> {
    let d = ob.cast::<PyDict>().ok()?;
    if d.is_exact_instance_of::<PyDict>() {
        return None;
    }
    if !ob.hasattr("getlist").ok()? || !ob.hasattr("lists").ok()? {
        return None;
    }
    // Snapshotted before any recursive conversion, for the same reason the
    // plain-dict arms snapshot (#2510): `items()` runs Python.
    ob.call_method0("items")
        .ok()?
        .try_iter()
        .ok()?
        .map(|pair| {
            pair.ok()?
                .extract::<(Bound<'py, PyAny>, Bound<'py, PyAny>)>()
                .ok()
        })
        .collect()
}

/// A Python dict KEY, with its type kept (#2339).
///
/// Total by construction: every Python dict key is hashable, and a key whose
/// type this does not model still becomes an [`ObjectKey::Other`] carrying
/// both its `str()` and its `repr()`. That totality is the point — the
/// previous code returned `None` for a non-string key and dropped the WHOLE
/// dict to its own `repr`, so one exotic key made the entire mapping
/// un-iterable.
///
/// Ordering mirrors [`Value`]'s own extraction, and for the same reasons:
/// `bool` before `int` (a Python `bool` IS an `int`, so the `i64` arm would
/// swallow it and `{{ {True: 1} }}` would print `{1: 1}`), and `Decimal`
/// before `f64` (`extract::<f64>()` honours `Decimal.__float__`, #2214).
pub fn py_object_key(ob: &Bound<'_, PyAny>) -> ObjectKey {
    if ob.is_none() {
        return ObjectKey::None;
    }
    if let Ok(s) = ob.extract::<String>() {
        // BEFORE the numeric arms: a `str` never extracts as one, but keeping
        // the common case first avoids three failed extractions per key.
        return ObjectKey::Str(s);
    }
    if let Ok(b) = ob.extract::<bool>() {
        return ObjectKey::Bool(b);
    }
    if let Ok(n) = ob.extract::<i64>() {
        return ObjectKey::Int(n);
    }
    if ob.is_instance_of::<pyo3::types::PyInt>() {
        if let Ok(digits) = ob.str().and_then(|s| s.extract::<String>()) {
            return ObjectKey::BigInt(digits);
        }
    }
    if is_decimal(&ob.to_owned()) {
        if let Ok(d) = ob.str().and_then(|s| s.extract::<String>()) {
            return ObjectKey::Decimal(d);
        }
    }
    if let Ok(f) = ob.extract::<f64>() {
        return ObjectKey::Float(f);
    }
    if let Ok(t) = ob.cast::<pyo3::types::PyTuple>() {
        return ObjectKey::Tuple(t.iter().map(|item| py_object_key(&item)).collect());
    }
    ObjectKey::Other {
        display: ob
            .str()
            .and_then(|s| s.extract::<String>())
            .unwrap_or_default(),
        repr: ob
            .repr()
            .and_then(|s| s.extract::<String>())
            .unwrap_or_default(),
    }
}

/// Called only after the caller has established that the value is a Python string.
fn python_string_is_safe(value: &Bound<'_, PyAny>) -> bool {
    // Hot path: every context string and every nested list/dict string
    // crosses here. An exact `str` can never be `SafeData` (SafeString is a
    // subclass), so it needs no lookup at all; the class itself is resolved
    // once per process, not once per string (was an import + getattr per
    // value — 20k lookups for a 5k-row table with four string columns).
    static SAFE_DATA: pyo3::sync::PyOnceLock<Py<PyAny>> = pyo3::sync::PyOnceLock::new();
    let py = value.py();
    if value.get_type().is(py.get_type::<pyo3::types::PyString>()) {
        return false;
    }
    let Ok(cls) = SAFE_DATA.get_or_try_init(py, || {
        py.import("django.utils.safestring")
            .and_then(|m| m.getattr("SafeData"))
            .map(|c| c.unbind())
    }) else {
        return false;
    };
    value.is_instance(cls.bind(py)).unwrap_or(false)
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
            let safe = python_string_is_safe(&ob.to_owned());
            Ok(if safe {
                Value::SafeString(s)
            } else {
                Value::String(s)
            })
        } else if let Ok(tuple) = ob.cast::<pyo3::types::PyTuple>() {
            // BEFORE the sequence arm: a tuple extracts as `Vec<Value>` too, so
            // checking after it would render every tuple as a list (#2203).
            let items: Vec<Value> = tuple.extract()?;
            if let Ok(fields) = ob
                .getattr("_fields")
                .and_then(|f| f.extract::<Vec<String>>())
            {
                if fields.len() == items.len() {
                    return Ok(Value::NamedTuple {
                        name: ob.get_type().getattr("__name__")?.extract()?,
                        fields,
                        items,
                    });
                }
            }
            Ok(Value::Tuple(items))
        } else if let Ok(list) = ob.extract::<Vec<Value>>() {
            Ok(Value::List(list))
        } else if let Some(pairs) = multi_value_dict_pairs(&ob.to_owned()) {
            // A Django `QueryDict` / `MultiValueDict` BEFORE the dict arm:
            // last value per key, as Django resolves it (#2556). See the
            // helper for why the object arrives raw.
            let mut m: IndexMap<ObjectKey, Value> = IndexMap::with_capacity(pairs.len());
            for (k, v) in pairs {
                m.insert(py_object_key(&k), v.extract::<Value>()?);
            }
            Ok(Value::Object(m))
        } else if let Some(map) = ob.cast::<PyDict>().ok().and_then(|d| {
            // Iterated by hand rather than `extract::<IndexMap<..>>()`, because
            // extraction is exactly where Python's insertion order would be
            // lost — and no later re-sort can recover it (#2203). PyDict
            // iteration yields entries in insertion order.
            //
            // A NON-STRING key no longer rejects the whole dict (#2339). It
            // used to: the arm returned `None`, the conversion fell through to
            // the object handling below, and `{0: 1}` reached the renderer as
            // its own `repr` — so `{% for k in d %}` iterated that string BY
            // CHARACTER and `{{ d|length }}` counted 14. The key now carries
            // its type, so such a dict is a real mapping.
            // Snapshotted into an owned `Vec` BEFORE any recursive
            // `.extract::<Value>()` call (#2510). `d.iter()` is a LIVE PyO3
            // iterator directly over the dict; `.extract::<Value>()` can run
            // arbitrary Python (any dunder check on an unresolved
            // `SimpleLazyObject`, e.g. `__bool__`, triggers Django's lazy
            // `_setup()`). If that side effect mutates THIS SAME dict — which
            // is exactly what happens when a dict's own value is a lazy
            // object whose resolution writes back into the dict — the live
            // iterator's size check fails mid-iteration and PyO3 panics
            // ("dictionary changed size during iteration"). Collecting into a
            // `Vec` first fully drains the iterator before any Python
            // callback runs, so a later mutation has nothing left to
            // invalidate.
            let pairs: Vec<(Bound<'_, PyAny>, Bound<'_, PyAny>)> = d.iter().collect();
            let mut m: IndexMap<ObjectKey, Value> = IndexMap::with_capacity(pairs.len());
            for (k, v) in pairs {
                m.insert(py_object_key(&k), v.extract::<Value>().ok()?);
            }
            Some(m)
        }) {
            Ok(Value::Object(map))
        } else {
            // #2448: one of the four `datetime` types, whose
            // `DjangoJSONEncoder` spelling is not its `str()`.
            //
            // Placed HERE, in the fallback block, and not up with the `Decimal`
            // arm — which is where it reads more naturally and would have cost
            // every string in every context four `isinstance` calls. None of
            // the four extracts as an `f64`, a `String`, a tuple, a list or a
            // dict, so reaching this block loses nothing: before this arm
            // existed they fell all the way through to the final
            // `Ok(Value::String(ob.str()?))` at the bottom of it and arrived at
            // `json_script` already spelled wrong. The same measurement that
            // put `is_decimal` behind a cached type object (#2240 review) is
            // why this one is a single tuple-`isinstance` on the negative path.
            if let Some(encoded) = django_json_encoded(&ob.to_owned()) {
                return Ok(Value::Encoded(Box::new(encoded)));
            }
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
            // #2466/#2477/#2489: an object that no variant above models —
            // carried by the facts MEASURED from it, including its items.
            //
            // Placed BEFORE the `__dict__` bulk-dump arm since #2478, which is
            // the whole of that fix. It used to come AFTER, because routing an
            // attribute-carrying object through this carrier would have taken
            // `{{ obj.a }}` with it — an `Encoded` had no attributes. #2481
            // gave it some, so the objection is answered and the order can be
            // the one the SEMANTICS want: an object Python calls falsy — or
            // one that has an `__iter__` — is not a mapping of its attributes,
            // and the `__dict__` arm asserts that it is. `opaque_value` itself
            // declines the one shape that arm is genuinely right about (a
            // TRUTHY, NON-iterable object with public attributes), so the
            // ordering here does not decide it.
            //
            // Both serialization floors stay ABOVE this: `__djust_serialize__`
            // (#1986) and the raw-`Model` arm (#1986 vector 7) have already
            // claimed anything the denylist governs, so a model cannot reach
            // this arm and cannot have its floor fields dumped by it. That
            // ordering is asserted structurally rather than left to reading.
            if let Some(encoded) = opaque_value(&ob.to_owned()) {
                return Ok(Value::Encoded(Box::new(encoded)));
            }
            // For arbitrary Python objects (e.g. Django model instances), try to
            // extract public attributes from __dict__ so that template expressions
            // like `{{ obj.name }}` or `{{ obj.path }}` work without requiring
            // callers to manually convert to dicts.
            //
            // Reached now by exactly the shape `opaque_value` hands back: a
            // TRUTHY, NON-iterable object with public attributes. Those keep
            // their `Value::Object` exactly as before — retiring this arm is a
            // separate, much larger decision than #2477/#2489.
            if let Some(map) = public_dict_attrs(&ob.to_owned()) {
                if !map.is_empty() {
                    return Ok(Value::Object(map));
                }
            }
            Ok(Value::String(ob.str()?.to_string()))
        }
    }
}

/// The most items [`opaque_value`] will read out of an object that states no
/// `__len__` before DECLINING it (#2477/#2489).
///
/// A ceiling and not a truncation point: an object that yields more than this
/// keeps the terminal `Value::String(str(o))` path it already had, so the cap
/// can never produce a short collection. It exists because a class whose
/// `__iter__` returns `itertools.count()` is RE-iterable — the one-shot guard
/// above it does not catch that shape — and enumerating it would hang the
/// render. An object WITH a `__len__` is enumerated in full regardless of this
/// value, because it has stated its own bound and Django iterates all of it.
pub const OPAQUE_ITEM_CAP: usize = 100_000;

/// What [`opaque_gate`] measured from an object it CLAIMS (#2477/#2489).
///
/// Three facts, and none of them is an item: the gate answers "does this
/// object belong to the carrier" without converting anything, which is what
/// lets [`crosses_as_encoded`] ask the question cheaply and safely.
pub struct OpaqueFacts {
    /// `bool(o)` — Python's own answer.
    pub truthy: bool,
    /// `len(o)`, or `None` where it raises.
    pub len: Option<usize>,
    /// `iter(o)` succeeds.
    pub iterable: bool,
}

/// Does [`opaque_value`] claim this object, and what did it measure?
/// (#2477/#2489)
///
/// The GATE, split out from the payload build so there is exactly one
/// statement of it with two consumers — [`opaque_value`], which goes on to
/// convert the items and the attributes, and [`crosses_as_encoded`], which
/// only needs the answer. Two copies of a gate that decide the same question
/// about the same objects is the #1646 shape, and this one has four arms.
///
/// **Nothing here converts a value, and that is the whole reason it exists.**
/// The first version of `crosses_as_encoded` ran the REAL conversion —
/// `extract::<Value>()` — and asked whether a `Value::Encoded` came out. That
/// is exact, and it SEGFAULTED: the normalizer's fallback is where an ordinary
/// "presenter" object lands, and converting one eagerly walks its `__dict__`
/// into a raw `QuerySet` and `Manager` and down through their own `__dict__`s,
/// deep enough to overflow the stack — work the render path never does,
/// because it resolves through the protected walk one segment at a time. The
/// gate touches `bool(o)`, `iter(o)`, `len(o)` and the object's `__dict__`
/// KEYS, and stops.
///
/// See [`opaque_value`]'s doc for what each decline is and why.
fn opaque_gate(ob: &Bound<'_, PyAny>) -> Option<OpaqueFacts> {
    // Python's own answer, via `PyObject_IsTrue`, so a class overriding
    // `__bool__` or `__len__` is answered by the object rather than by this
    // function's idea of which types are containers.
    let truthy = ob.is_truthy().ok()?;
    // `PyObject_GetIter`, which builds an iterator and consumes nothing — a
    // generator is not advanced by being asked.
    let iterator = ob.try_iter().ok();
    let iterable = iterator.is_some();
    // `PyObject_Size`: `Ok` for anything with a `__len__`, `Err` otherwise.
    let len = ob.len().ok();
    if let Some(it) = iterator {
        // `iter(o) is o` — a one-shot iterator. Reading it here would consume
        // the caller's object, so it is NOT enumerated at conversion.
        //
        // Under ADR-027 (the shipped default) it is ADMITTED with `items`
        // left `None`: the `Encoded` carries a live handle, and the
        // `{% for %}` sink consumes it once through
        // [`Encoded::consume_live_items`] — Django's `list(values)` in
        // `ForNode.render`, row V (#2613). Before this a generator or
        // `MultiValueDict.lists()` fell to the terminal `str()` path and
        // `{% for %}` walked the REPR character by character: silent wrong
        // output. On the eager escape hatch there is no handle to consume
        // later, so the decline stands there (enumerating at conversion is a
        // new wrong answer, not an unfixed one).
        if it.as_any().is(ob) {
            return if resolve_lazy() {
                Some(OpaqueFacts {
                    truthy,
                    len,
                    iterable: true,
                })
            } else {
                None
            };
        }
        // An object that states a `__len__` has stated its own bound, and
        // Django iterates all of it — so there is nothing to check and the
        // walk is skipped, which is what keeps this gate O(1) for a `set` and
        // a `dict_keys`. Without one, walk to the cap and DECLINE past it:
        // a class whose `__iter__` returns `itertools.count()` is RE-iterable,
        // so the one-shot guard above does not catch it and enumerating it
        // would never return.
        //
        // Counted, not collected: the items are not converted here.
        if len.is_none() {
            let mut seen = 0usize;
            for item in it {
                if item.is_err() {
                    return None;
                }
                seen += 1;
                if seen > OPAQUE_ITEM_CAP {
                    return None;
                }
            }
        }
    }
    // The `__dict__` bulk-dump arm's cell, left to it. See the gate section of
    // [`opaque_value`]'s doc for why both qualifiers are load-bearing.
    //
    // The KEY-ONLY probe, and that is a measurement rather than a style: this
    // is the arm an ordinary truthy object with attributes takes, and building
    // the full map to decline it would convert every attribute value only for
    // the `__dict__` arm below to convert them again.
    //
    // LIFTED under ADR-027's flag (#2539): the whole point of the sink is that
    // an ordinary object crosses as itself — `{{ o }}` is `str(o)`, as Django
    // renders it — and reaches its attributes through a LIVE walk rather than
    // through a bulk dump of its `__dict__`. This one condition is the switch
    // between the two carriers, and it is what rows I / T / K3 / K4 turn on.
    // The ONE-SHOT iterator decline above is deliberately NOT lifted: reading
    // a generator at conversion time would consume the caller's object, which
    // is a NEW wrong answer rather than an unfixed one (row V, movement 3).
    if !resolve_lazy() && truthy && !iterable && has_public_dict_attrs(ob) {
        return None;
    }
    Some(OpaqueFacts {
        truthy,
        len,
        iterable,
    })
}

impl Encoded {
    /// Consume a ONE-SHOT iterator carried by the live handle into its items
    /// (#2613) — Django's `list(values)` in `ForNode.render`, run at the
    /// `{% for %}` sink rather than at conversion so that a generator the
    /// template never loops over is never advanced.
    ///
    /// `None` when there is nothing to consume: no handle (eager path, or a
    /// wire round-trip), not iterable, or the items were already enumerated
    /// at conversion (a re-iterable object). Bounded by [`OPAQUE_ITEM_CAP`]:
    /// past it the render RAISES rather than truncating — Django would hang
    /// on the same `itertools.count()`, so an error is the strictly better
    /// answer and a short list would be a silently wrong one. A raising
    /// `__next__` propagates as Django propagates it.
    ///
    /// A second call after exhaustion yields an empty list, exactly as a
    /// second `{% for %}` over the same generator is empty in Django.
    pub fn consume_live_items(&self) -> Option<PyResult<Vec<Value>>> {
        if !self.iterable || self.items.is_some() {
            return None;
        }
        let handle = self.live.as_ref()?;
        Some(Python::attach(|py| {
            let ob = handle.bind(py);
            let mut collected = Vec::new();
            for item in ob.try_iter()? {
                collected.push(item?.extract::<Value>()?);
                if collected.len() > OPAQUE_ITEM_CAP {
                    return Err(pyo3::exceptions::PyRuntimeError::new_err(format!(
                        "'{}' object yielded more than {} items in a {{% for %}} — \
                         an unbounded iterator cannot be rendered",
                        self.type_name, OPAQUE_ITEM_CAP
                    )));
                }
            }
            Ok(collected)
        }))
    }
}

/// Does this object cross into the renderer as a [`Value::Encoded`]?
/// (#2477/#2489)
///
/// The ONE statement of that question for `djust.serialization`, exported to
/// Python as `_rust.crosses_as_encoded`. Its Python caller — the final
/// fallback of `normalize_django_value` — used to stringify exactly the
/// objects this carrier holds exactly, so the LiveView path and the raw
/// `render_template` path answered differently for the same value.
///
/// **It asks the GATE, not the conversion.** The first version ran
/// `extract::<Value>()` and matched on the result, which is exact and is what
/// caught a `bytes` and a `deque` being claimed by PyO3's SEQUENCE extraction
/// long before the fallback block — a transcription of the last two arms had
/// said TRUE for both and regressed `{{ p }}` over `b"ab"` to `[97, 98]`. It
/// also SEGFAULTED, for the reason [`opaque_gate`] records. So the sequence
/// and mapping arms are probed SHALLOWLY here — `Vec<Bound<PyAny>>` collects
/// references and converts nothing — and the fallback's own arm is asked
/// through the gate.
///
/// EVERY pre-fallback arm is probed, in the impl's own order, and that is a
/// correction rather than thoroughness. The first version probed only the
/// sequence and mapping arms, on the argument that
/// `normalize_django_value` — the one caller — has its own branch for the
/// scalars far above the line that consults this. That is TRUE and it made the
/// predicate wrong as a general claim: the conversion-differential below
/// reported it answering `true` for `None`, `True`, `7`, `1.5`, `"ab"` and a
/// `Decimal`, six shapes it can never be asked about but would answer wrongly
/// if it were. A predicate whose correctness depends on which caller it has is
/// one the next caller breaks.
///
/// `test_the_two_answer_the_same_bit_for_every_shape` sweeps this against
/// `extract::<Value>()` itself, which is the anti-drift net a cheap probe
/// needs — and is what found those six.
pub fn crosses_as_encoded(ob: &Bound<'_, PyAny>) -> bool {
    // The arms ABOVE the fallback block, in the impl's own order, each probed
    // WITHOUT converting an element. The order matters here for the reason it
    // matters there: `bool` before `i64` (a Python `bool` IS an `int`), the
    // `i64` bound before `BigInt`, `Decimal` before `f64` (`extract::<f64>()`
    // honours `Decimal.__float__`, #2214), and `PyTuple` before the sequence
    // arm.
    if ob.is_none()
        || ob.extract::<bool>().is_ok()
        || ob.extract::<i64>().is_ok()
        || big_int_digits(ob).is_some()
        || is_decimal(ob)
        || ob.extract::<f64>().is_ok()
        || ob.extract::<String>().is_ok()
        || ob.cast::<pyo3::types::PyTuple>().is_ok()
    {
        return false;
    }
    // The datetime family, claimed inside the fallback block by
    // `django_json_encoded` — a tuple `isinstance` over four cached types.
    // BELOW the scalars, as it is in the impl.
    if django_json_encoded(ob).is_some() {
        return true;
    }
    // The last two arms above the fallback block: `Vec<Bound<PyAny>>` is every
    // sequence (`bytes`, a `deque`, a `range`, a class with an integer
    // `__getitem__`) and collects REFERENCES, converting nothing; `PyDict` is
    // every real mapping.
    if ob.extract::<Vec<Bound<'_, PyAny>>>().is_ok() {
        return false;
    }
    if ob.cast::<PyDict>().is_ok() {
        return false;
    }
    // Both serialization floors, which recurse rather than produce an
    // `Encoded` (#1986 and its vector 7). Cheap, and asked in the block's own
    // order.
    if ob.getattr("__djust_serialize__").is_ok() {
        return false;
    }
    if let Ok(models_mod) = ob.py().import("django.db.models") {
        if let Ok(model_cls) = models_mod.getattr("Model") {
            if ob.is_instance(&model_cls).unwrap_or(false) {
                return false;
            }
        }
    }
    opaque_gate(ob).is_some()
}

/// A Python object that no [`Value`] variant models (#2466, #2477, #2489).
///
/// This was `falsy_opaque` and claimed only Python-FALSY objects. `bool(set())`
/// is `False` in Python and was `True` here, because a `set` has no variant:
/// the conversion landed it on its final `Ok(Value::String(ob.str()?))` and it
/// arrived as the non-empty string `"set()"`, whose `is_truthy` is
/// `!s.is_empty()`. The same was true of `frozenset()`, `complex(0)`, an empty
/// `dict_keys` / `dict_values` / `dict_items`, and any user class with a
/// `__len__` returning 0 or a `__bool__` returning `False` — an OPEN set, which
/// is why this is answered by carrying `bool(o)` rather than by giving `set` a
/// variant. A one-type fix is the shape #2129 took five rounds over.
///
/// #2477/#2489 widened it, and the reason is that the truthiness split was
/// never a property of the CLASS — it was a property of what the carrier could
/// then answer. A `{'a'}` is the same kind of object as a `set()`; it was
/// declined only because the carrier had no way to say what it contains. Now it
/// does ([`Encoded::items`]), so the gate is about what is MEASURABLE rather
/// than about the sign of `bool(o)`:
///
/// ```text
/// {{ p|length }}   over {'a'}        Django 1   djust 32   (the repr's chars)
/// {% for x in p %} over {}.keys()    Django ''  djust one iteration per char
/// {{ p|length }}   over a falsy      Django 0   djust 15
///                  __iter__ class
/// ```
///
/// **Why `Encoded` and not a new variant.** `Encoded` already IS this carrier:
/// a Python object held by its `type_name` / `display` / `json` / `truthy`
/// spellings because the object itself cannot cross. #2448 built it for the
/// four `DjangoJSONEncoder` types, #2458 added the truthiness bit, #2481 the
/// attributes and #2477/#2489 the length and the items; each widens the set of
/// objects that use it and adds no mechanism. A new variant would be a second
/// carrier for one question (#1646), and would have to be classified at every
/// wildcard `match` arm in the workspace. [`Encoded::items`] carries the longer
/// form of the argument, including why [`Value::DictView`] — the one existing
/// variant with a collection's shape — is not it.
///
/// **`json` is `str(o)`, and that is not a lie by omission.** For an object
/// `DjangoJSONEncoder` cannot spell there is no encoder spelling to carry, and
/// `str(o)` is exactly what the `Value::String` path this replaces already
/// wrote into `json_script` — so that axis does not move. Django REFUSES
/// `{{ p|json_script:"x" }}` over a `set` (`Object of type set is not JSON
/// serializable`); that divergence is #2429's declined refusal direction,
/// unchanged here rather than grown.
///
/// # The gate, and what each arm of it declines
///
/// Every fact this struct carries must be measurable WITHOUT destroying the
/// caller's object and without running unbounded work. Three shapes fail that
/// and are declined, each keeping the terminal `Value::String(ob.str()?)` path
/// it has today — so a decline is never a REGRESSION, only an unfixed cell:
///
/// * **A one-shot iterator** — `iter(o) is o`. A generator, a `zip`, a `map`,
///   an `enumerate`. Enumerating it consumes the caller's object, so it is
///   never enumerated HERE. Under ADR-027 (the default) it is carried with a
///   live handle and `items: None`, and consumed once by the `{% for %}` sink
///   ([`Encoded::consume_live_items`], #2613) — Django's `list(values)`. On
///   the eager escape hatch it is declined outright, as #2466 named.
/// * **An unsized iterable longer than [`OPAQUE_ITEM_CAP`]** — an object with
///   no `__len__` whose iterator keeps yielding. `itertools.count()` is an
///   iterator and is declined by the arm above, but a class whose `__iter__`
///   RETURNS one is re-iterable and would hang here. Declined at the cap rather
///   than truncated: a truncated collection is a silently wrong answer, and a
///   decline is the answer this value already had. An object that states a
///   `__len__` is enumerated in full — it has told us the bound, and Django
///   iterates all of it.
/// * **A TRUTHY, NON-iterable object that IS a mapping of its attributes** —
///   the cell the `__dict__` bulk-dump arm below this one claims. Retiring that
///   arm is a much larger decision than this one (every service object,
///   presenter and plain instance in a template context goes through it), so it
///   is left exactly where it is. Note the two qualifiers: a FALSY such object
///   is claimed here, which is the whole of #2478, and an ITERABLE one is
///   claimed here too, because an object with `__iter__` is not a mapping of
///   its attributes — Django's `{% for %}` runs its `__iter__`, not its
///   `__dict__` — and [`Encoded::attrs`] keeps `{{ obj.a }}` resolving either
///   way.
///
/// A truthy, non-iterable, attribute-LESS object — `complex(1)`, a `__slots__`
/// instance — IS claimed, and that is a widening beyond either issue's measured
/// table. It is the same terminal `str()` arm and the same defect: `{{ p|length }}`
/// over `complex(1)` was `6`, the characters of `"(1+0j)"`, where Django says
/// `0`; `{{ p|escapeseq }}` rendered the six characters where Django raises.
/// Splitting the class by truthiness once was already the mistake this widening
/// corrects; splitting it by "does it happen to have a `__dict__`" would be the
/// same mistake one axis over, and the `__dict__` arm is declined above only
/// because retiring it is a genuinely different, larger change.
///
/// Fails CLOSED with [`django_json_encoded`] and [`is_decimal`]: a raising
/// `__bool__`, `__len__` or `__iter__` takes the value back to the string path
/// exactly as before.
///
/// Reached only in the fallback block, after every extraction has already
/// failed, so it costs a string / int / list / dict / model nothing. A `list`,
/// a `tuple`, a `dict` and anything with an integer `__getitem__` are extracted
/// by PyO3 well before this, which is why a `range` / `bytes` / `deque` never
/// arrives here.
///
/// # An object WITH attributes (#2478)
///
/// Until #2478 this arm was placed AFTER the `__dict__` bulk-dump, so a falsy
/// object carrying attributes became a NON-EMPTY `Value::Object` and answered
/// with the mapping rule:
///
/// ```text
/// class LenZeroWithAttrs:
///     def __init__(self): self.a = 1
///     def __len__(self):  return 0
///
/// {% if p %}              python False   django F   djust T
/// {{ p|length }}                         django 0   djust 1
/// {% for x in p %}                       django ''  djust '[a]'
/// {{ p }}                    django '<LenZeroWithAttrs object …>'   djust "{'a': 1}"
/// ```
///
/// The placement was deliberate and the reason was correct at the time: this
/// carrier had no attributes, so claiming the object would have taken
/// `{{ obj.a }}` with it. #2481 gave it an attribute map, which is why #2478
/// is a REORDER plus one field rather than a new rule — every cell above is
/// answered by a spelling this struct already carries, and `{{ obj.a }}` is
/// answered by the new one.
///
/// Note which cells the issue's own suggested remedy — a truthiness override
/// on `Value::Object` — would have reached: the FIRST only. `|length` and
/// `{% for %}` and `{{ p }}` read the MAPPING, not its truthiness, and the
/// `__dict__` arm's whole claim is that the object IS a mapping of its
/// attributes. Overriding one answer of a wrong carrier is the shape #2129
/// took five rounds over; moving the object to the right carrier answers all
/// of them at once.
pub fn opaque_value(ob: &Bound<'_, PyAny>) -> Option<Encoded> {
    let OpaqueFacts {
        truthy,
        len,
        iterable,
    } = opaque_gate(ob)?;
    // The items, converted — the half `opaque_gate` deliberately does NOT do.
    // `iter(o)` is asked again rather than carried across, because the gate
    // may have walked the first one to check the cap; a RE-iterable object
    // yields the same elements again and consumes nothing. A ONE-SHOT
    // iterator (`iter(o) is o`, admitted under ADR-027 — #2613) is left at
    // `None`: the `{% for %}` sink consumes it once through the live handle.
    let one_shot = ob.try_iter().is_ok_and(|it| it.as_any().is(ob));
    let items = if iterable && !one_shot {
        let it = ob.try_iter().ok()?;
        let mut collected = Vec::with_capacity(len.unwrap_or(0).min(64));
        for item in it {
            // A raising `__next__` fails closed, like every other probe here:
            // back to the string path, unchanged.
            collected.push(item.ok()?.extract::<Value>().ok()?);
        }
        Some(collected)
    } else {
        None
    };
    // `__name__`, NOT `__qualname__`, and for the reason `django_json_encoded`
    // records: a heap type's `tp_name` is the name it was created with, so a
    // class defined inside a function is `LenZero` where its `__qualname__`
    // says `outer.<locals>.LenZero`. CPython writes the former into
    // `'X' object is not iterable`.
    let type_name = ob
        .get_type()
        .getattr("__name__")
        .ok()?
        .extract::<String>()
        .ok()?;
    let text = ob.str().ok()?;
    let display_safe = python_string_is_safe(text.as_any());
    let display = text.extract::<String>().ok()?;
    // MEASURED, not `display.clone()` (#2472). For `set()`, `frozenset()` and
    // `complex(0)` the two spellings coincide, which is exactly why copying
    // `display` here would look correct on every builtin this function was
    // written for — and be wrong for the case it was widened to carry: a USER
    // class may define `__str__` and `__repr__` independently, so
    // `{{ p|pprint }}` over a `__bool__`-False instance renders whichever this
    // field holds. `repr()` is one call and answers it exactly.
    let repr = ob.repr().ok()?.extract::<String>().ok()?;
    // ADR-027's transient handle (#2539). `Bound::unbind` needs no GIL token
    // and makes no Python CALL — it is a refcount bump plus an `Arc`
    // allocation, so attaching one adds no Rust→Python crossing to the
    // per-render budget (#2532).
    let lazy = resolve_lazy();
    let live = if lazy {
        Some(std::sync::Arc::new(ob.clone().unbind()))
    } else {
        None
    };
    Some(Encoded {
        type_name,
        // No encoder spelling exists for these; `str(o)` is what the
        // `Value::String` path this replaces already wrote.
        json: display.clone(),
        display,
        truthy,
        len,
        iterable,
        repr,
        // No comparison key, and this is the ONE arm where that matters as a
        // decision rather than as a fallback (#2471/#2466). `django_json_encoded`
        // builds an `Encoded` whose Python type has a total order; this one
        // builds them for `set()`, `complex(0)` and arbitrary user classes,
        // whose orderings are partial, absent, or defined by the object. So
        // `python_partial_cmp` answers `None` for every pair either side of
        // which came from here — never equal, never ordered — which is byte
        // for byte what the `_ => false` wildcard answered for these values
        // before #2471, and what it still answered for them between #2466 and
        // this merge. Widening the comparison arm to reach them would need the
        // object's own `__eq__` — `set() == frozenset()` is True ACROSS type
        // names and `LenZero() == LenZero()` is False WITHIN one — so no
        // carried spelling decides it. Filed as #2480.
        cmp_key: None,
        live,
        display_safe,
        // The object's PUBLIC `__dict__` (#2478) — the same map, built by the
        // same function, that the `__dict__` bulk-dump arm below would have
        // built. That IS the fix: this arm now claims a falsy object WITH
        // attributes, and it can only do so without regressing `{{ obj.a }}`
        // because #2481 gave `Encoded` somewhere to put them.
        //
        // Empty for an object with no `__dict__` (a C type: `set`,
        // `frozenset`, `complex`, a `dict_keys`) — which is every value this
        // arm claimed before #2478, so their behaviour is unchanged.
        //
        // Built only on the CLAIMING path; the decline above asks
        // `has_public_dict_attrs` instead, which reads the keys and converts
        // no values.
        //
        // NOT built at all when a handle is attached (#2539). The handle is
        // the authority for every attribute lookup under ADR-027, so building
        // the map as well would be two mechanisms answering one question
        // (#1646) — and the wrong one would WIN, because `Context::get`'s step
        // 2 reads `attrs` and never auto-calls (Django resolves
        // `{{ d.value }}` on a callable object by CALLING `d` first, which no
        // eager map can express). It is also the recursion that segfaults:
        // `public_dict_attrs` converts every attribute VALUE with no visited
        // set, so an object whose `__dict__` reaches back to its own container
        // kills the process (#2516 row H).
        attrs: if lazy {
            IndexMap::new()
        } else {
            public_dict_attrs(ob).unwrap_or_default()
        },
        items,
        // The equality CONTRACT, measured from the live object (#2480). The
        // one producer that sets this: `django_json_encoded` leaves it `None`
        // because its four types already carry a real `cmp_key`, and this arm
        // is where `set()`, `complex(0)` and arbitrary user classes land.
        //
        // Fails to `None` on any probe error, like every other measurement
        // here — which restores the pre-#2480 answer (never equal) rather than
        // guessing one.
        eq_class: equality_class(ob),
    })
}

/// The Python types [`equality_class`] dispatches on, resolved once per
/// interpreter (#2480).
struct EqProtocols {
    /// `collections.abc.Set` — the ABC that DEFINES `__eq__` as
    /// `len(self) == len(other) and self <= other`.
    set_abc: Py<PyAny>,
    /// `numbers.Number`.
    number_abc: Py<PyAny>,
    /// The builtin `complex`, called to read a Number's two components.
    complex_cls: Py<PyAny>,
    /// `object.__eq__` and `object.__repr__`, for the `is`-identity probes
    /// that decide the identity arm.
    object_eq: Py<PyAny>,
    object_repr: Py<PyAny>,
}

/// Which of Python's equality contracts does this object obey? (#2480)
///
/// Measured, not derived — see [`Encoded::eq_class`] for why a type-name list
/// is wrong in BOTH directions and what each arm's contract is.
///
/// Fails CLOSED at every step, like [`django_json_encoded`] and [`is_decimal`]:
/// an unimportable `collections.abc`, an `isinstance` that raises through a
/// hostile `__class__`, a `complex(o)` that raises — every one answers `None`,
/// which is the pre-#2480 behaviour (never equal, never ordered) rather than a
/// guess.
fn equality_class(ob: &Bound<'_, PyAny>) -> Option<EqClass> {
    static PROTOCOLS: pyo3::sync::PyOnceLock<Option<EqProtocols>> = pyo3::sync::PyOnceLock::new();
    let py = ob.py();
    let protocols = PROTOCOLS
        .get_or_init(py, || {
            let object_type = py.get_type::<pyo3::types::PyAny>();
            Some(EqProtocols {
                set_abc: py
                    .import("collections.abc")
                    .ok()?
                    .getattr("Set")
                    .ok()?
                    .unbind(),
                number_abc: py.import("numbers").ok()?.getattr("Number").ok()?.unbind(),
                complex_cls: py.get_type::<pyo3::types::PyComplex>().into_any().unbind(),
                object_eq: object_type.getattr("__eq__").ok()?.unbind(),
                object_repr: object_type.getattr("__repr__").ok()?.unbind(),
            })
        })
        .as_ref()?;

    // Arm 1. A `set`, a `frozenset`, a `dict_keys`, a `dict_items` and every
    // user registration of the ABC. FIRST, because a Set's `__eq__` is the
    // ABC's and so can never be `object`'s — the arms are disjoint, and
    // ordering them this way says which contract is the specific one.
    if ob.is_instance(protocols.set_abc.bind(py)).unwrap_or(false) {
        return Some(EqClass::Set);
    }
    // Arm 2. `complex(o)` rather than `o.real` / `o.imag`, because a
    // `numbers.Number` registration is only required to be convertible — a
    // `Fraction` has no `.imag` until `complex()` gives it one. A raising
    // conversion declines the whole arm.
    if ob
        .is_instance(protocols.number_abc.bind(py))
        .unwrap_or(false)
    {
        let as_complex = protocols.complex_cls.bind(py).call1((ob,)).ok()?;
        let real = as_complex.getattr("real").ok()?.extract::<f64>().ok()?;
        let imag = as_complex.getattr("imag").ok()?.extract::<f64>().ok()?;
        return Some(EqClass::Number { real, imag });
    }
    // Arm 3. Default `__eq__` AND default `__repr__`. BOTH are load-bearing:
    // a `dict_values` has the first and not the second, and two DISTINCT empty
    // ones share the spelling `dict_values([])` — so `repr` would call them
    // equal where Python says they are not. Measured, not reasoned about.
    let ty = ob.get_type();
    let eq_is_default = ty
        .getattr("__eq__")
        .is_ok_and(|f| f.is(protocols.object_eq.bind(py)));
    let repr_is_default = ty
        .getattr("__repr__")
        .is_ok_and(|f| f.is(protocols.object_repr.bind(py)));
    if eq_is_default && repr_is_default {
        return Some(EqClass::Identity);
    }
    // Arm 4. Everything else — a class that overrides `__eq__` (only Python
    // can run it), and one with a custom `__repr__` whose spelling is not a
    // token. Never equal: the answer this carrier already gave.
    None
}

impl Encoded {
    /// Reconstitute Python's temporal types without interpreting repr as code.
    /// Which `datetime` type this encodes, decided in pure Rust — `None` for
    /// anything that is not a temporal value. Lets the renderer skip the
    /// Python round trip entirely for the common non-temporal `Encoded`.
    pub fn temporal_kind(&self) -> Option<&'static str> {
        let kind = if self.attrs.contains_key("days") && self.attrs.contains_key("microseconds") {
            "timedelta"
        } else if self.attrs.contains_key("year") {
            if self.attrs.contains_key("hour") {
                "datetime"
            } else {
                "date"
            }
        } else if self.attrs.contains_key("hour") && self.attrs.contains_key("microsecond") {
            "time"
        } else {
            return None;
        };
        // Restrict reconstruction to encoded temporal values, never arbitrary
        // objects with similarly named attributes.
        if !matches!(
            self.type_name.as_str(),
            "datetime.date" | "datetime.datetime" | "datetime.time" | "datetime.timedelta"
        ) && self.live.is_none()
        {
            return None;
        }
        Some(kind)
    }

    pub fn temporal_object<'py>(&self, py: Python<'py>) -> PyResult<Option<Bound<'py, PyAny>>> {
        let Some(kind) = self.temporal_kind() else {
            return Ok(None);
        };
        let module = py.import("datetime")?;
        if let Some(live) = &self.live {
            if live.bind(py).is_instance(&module.getattr(kind)?)? {
                return Ok(Some(live.bind(py).clone()));
            }
            return Ok(None);
        }
        let cls = module.getattr(kind)?;
        let result = if kind == "timedelta" {
            let kwargs = pyo3::types::PyDict::new(py);
            for name in ["days", "seconds", "microseconds"] {
                if let Some(value) = self.attrs.get(name) {
                    kwargs.set_item(name, value.clone().into_pyobject(py)?)?;
                }
            }
            cls.call((), Some(&kwargs))?
        } else {
            let value = cls.call_method1("fromisoformat", (&self.display,))?;
            let kwargs = pyo3::types::PyDict::new(py);
            if let Some(fold) = self.attrs.get("fold") {
                kwargs.set_item("fold", fold.clone().into_pyobject(py)?)?;
            }
            if let Some(zone) = self.attrs.get("tzinfo") {
                let name = zone.to_string();
                // Named zones retain transition rules across state persistence.
                let tz = py.import("zoneinfo")?.getattr("ZoneInfo")?.call1((&name,));
                if let Ok(tz) = tz {
                    kwargs.set_item("tzinfo", tz)?;
                } else {
                    // fromisoformat retains the offset, but not its custom name.
                    let offset = value.call_method0("utcoffset")?;
                    if !offset.is_none() {
                        if let Some(Value::String(name)) = self.attrs.get("tzname") {
                            kwargs.set_item(
                                "tzinfo",
                                module.getattr("timezone")?.call1((offset, name))?,
                            )?;
                        }
                    }
                }
            }
            if kwargs.is_empty() {
                value
            } else {
                value.call_method("replace", (), Some(&kwargs))?
            }
        };
        Ok(Some(result))
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
            Value::SafeString(s) => py
                .import("django.utils.safestring")?
                .call_method1("mark_safe", (s,)),
            // Temporal values retain their Python type for arithmetic and
            // custom filters. Only validated temporal handles cross here;
            // opaque objects and carried collections keep their display-string
            // contract, including the model-protection boundary (#2509).
            Value::Encoded(e) => match e.temporal_object(py)? {
                Some(value) => Ok(value),
                None => Ok(e.display.into_pyobject(py)?.to_owned().into_any()),
            },
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
            Value::NamedTuple {
                name,
                fields,
                items,
            } => {
                let cls = py
                    .import("collections")?
                    .getattr("namedtuple")?
                    .call1((name, fields))?;
                let args: Vec<_> = items
                    .into_iter()
                    .map(|v| v.into_pyobject(py))
                    .collect::<std::result::Result<Vec<_>, _>>()?;
                cls.call(pyo3::types::PyTuple::new(py, args)?, None)
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
            // Back to Python as a LIST. A real view cannot be rebuilt without
            // the dict it belongs to, and every consumer of this conversion
            // (custom filters, tag handlers) wants something it can iterate.
            Value::DictView { items, .. } => Value::List(items).into_pyobject(py),
            Value::Object(o) => {
                let py_dict = PyDict::new(py);
                for (k, v) in o {
                    // The key goes back as the Python object it came from, so
                    // a round trip through Rust does not silently restring an
                    // int-keyed dict (#2339). `ObjectKey::Other` is the one
                    // lossy case — the original object is gone, so its
                    // `str()` goes back as text.
                    py_dict.set_item(Value::from(k).into_pyobject(py)?, v.into_pyobject(py)?)?;
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
        let mut map: IndexMap<ObjectKey, Value> = IndexMap::new();
        map.insert("id".into(), Value::Integer(1));
        map.insert(
            "__str__".into(),
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
        let mut map: IndexMap<ObjectKey, Value> = IndexMap::new();
        map.insert("a".into(), Value::Integer(1));
        map.insert("b".into(), Value::Integer(2));
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
        let mut map: IndexMap<ObjectKey, Value> = IndexMap::new();
        map.insert("__str__".into(), Value::Missing);
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
        let mut map: IndexMap<ObjectKey, Value> = IndexMap::new();
        map.insert("__str__".into(), Value::String("".to_string()));
        let obj = Value::Object(map);
        assert_eq!(obj.to_string(), "");
    }

    /// `object_str()` answers the "is this map a serialized object?" question
    /// for every caller (#2294).
    #[test]
    fn test_object_str_is_the_model_marker_predicate() {
        let mut plain: IndexMap<ObjectKey, Value> = IndexMap::new();
        plain.insert("a".into(), Value::Integer(1));
        assert_eq!(Value::Object(plain.clone()).object_str(), None);

        let mut model = plain.clone();
        model.insert("__str__".into(), Value::String("bob".to_string()));
        assert_eq!(Value::Object(model).object_str(), Some("bob"));

        // A non-`String` `"__str__"` is NOT a marker: `Display` falls back to
        // dict repr for it, so the predicate must agree.
        for bad in [Value::Missing, Value::None, Value::Integer(7)] {
            let mut broken = plain.clone();
            broken.insert("__str__".into(), bad);
            assert_eq!(Value::Object(broken).object_str(), None);
        }

        // Every other variant is not an object.
        for v in [
            Value::Missing,
            Value::None,
            Value::Bool(true),
            Value::Integer(1),
            Value::Float(1.0),
            Value::String("__str__".to_string()),
            Value::List(vec![]),
            Value::Tuple(vec![]),
        ] {
            assert_eq!(v.object_str(), None, "{v:?}");
        }
    }

    /// `object_str()` is the ONLY place the marker is spelled (#1646/#1859).
    ///
    /// Load-bearing rather than decorative: the predicate was written out twice
    /// (once per `Display` impl) before `length` needed a third copy, which is
    /// the point at which duplication becomes a drift class. This fails the day
    /// a fourth caller open-codes it instead of calling the helper.
    #[test]
    fn test_the_str_marker_is_spelled_in_exactly_one_place() {
        let src = include_str!("lib.rs");
        let hits = src.matches("get(\"__str__\")").count();
        assert_eq!(
            hits, 1,
            "`get(\"__str__\")` appears {hits} times in djust_core/src/lib.rs; \
             it must appear ONLY inside `Value::object_str`. A second spelling \
             is a predicate that can drift from the one `length` and `Display` \
             share."
        );
    }

    /// Regression-lock: bare `[List]` fallback for lists unchanged.
    #[test]
    fn test_display_list_renders_python_repr() {
        // Was `"[List]"` — a placeholder, not a rendering. Django renders
        // `str([1, 2])` (#2203).
        let list = Value::List(vec![Value::Integer(1), Value::Integer(2)]);
        assert_eq!(list.to_string(), "[1, 2]");
    }

    /// `py_str` is Python's `str()`, which for a `Float`/`Decimal` is NOT the
    /// render form (#2324). Every row is CPython's answer.
    #[test]
    fn py_str_is_cpython_str_not_the_render_form() {
        for (value, expected) in [
            (Value::Float(1e20), "1e+20"),
            (Value::Float(1e-200), "1e-200"),
            (Value::Float(2.0), "2.0"),
            (Value::Float(f64::NAN), "nan"),
            (Value::Float(f64::INFINITY), "inf"),
            (Value::Decimal("1E-9".to_string()), "1E-9"),
            (Value::Decimal("19.99".to_string()), "19.99"),
        ] {
            assert_eq!(value.py_str(), expected, "py_str of {value:?}");
        }
        // And the render form still expands, which is the half `Display` owns.
        assert_eq!(Value::Float(1e20).to_string(), "100000000000000000000");
        assert_eq!(
            Value::Decimal("1E-9".to_string()).to_string(),
            "0.000000001"
        );
    }

    /// For every OTHER variant `py_str` is `Display`, so no caller needs to
    /// know which is which. A new variant whose `Display` is not its `str()`
    /// fails here rather than silently taking the wrong branch.
    #[test]
    fn py_str_is_display_for_every_variant_but_float_and_decimal() {
        let mut map: IndexMap<ObjectKey, Value> = IndexMap::new();
        map.insert("k".into(), Value::Integer(1));
        for value in [
            Value::Missing,
            Value::None,
            Value::Bool(true),
            Value::Bool(false),
            Value::Integer(-7),
            Value::BigInt("1000000000000000000000000000000".to_string()),
            Value::String("a < b".to_string()),
            Value::String(String::new()),
            Value::List(vec![Value::Integer(1), Value::String("a".to_string())]),
            Value::Tuple(vec![Value::String("a".to_string())]),
            Value::Object(map),
        ] {
            assert_eq!(
                value.py_str(),
                value.to_string(),
                "py_str diverged from Display for {value:?}"
            );
        }
    }

    /// `py_str` and `py_repr` are the SAME split one nesting level apart:
    /// `str('a')` is bare where `repr('a')` is quoted, and `str(Decimal(..))`
    /// is the digits where `repr` is the constructor form. Pinned together so
    /// a future edit to one has to answer for the other.
    #[test]
    fn py_str_and_py_repr_differ_exactly_where_python_does() {
        assert_eq!(Value::String("a".to_string()).py_str(), "a");
        assert_eq!(Value::String("a".to_string()).py_repr(), "'a'");
        assert_eq!(Value::Decimal("19.99".to_string()).py_str(), "19.99");
        assert_eq!(
            Value::Decimal("19.99".to_string()).py_repr(),
            "Decimal('19.99')"
        );
        // A float is spelled identically either way — `repr` IS `str` for a
        // float in Python 3, which is why both route through the same helper.
        assert_eq!(Value::Float(1e20).py_str(), Value::Float(1e20).py_repr());
    }
}
