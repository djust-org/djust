//! Core utilities and types for djust
//!
//! This crate provides foundational data structures and utilities used across
//! the djust ecosystem.

use indexmap::IndexMap;
use pyo3::prelude::*;
use pyo3::types::{PyAnyMethods, PyDict, PyList, PyString};
use serde::de::{self, MapAccess, SeqAccess, Visitor};
use serde::{Deserialize, Deserializer, Serialize};
use std::fmt;
use std::sync::atomic::{AtomicBool, Ordering};

pub mod context;
pub mod decimal;
pub mod errors;
pub mod locale;
pub mod object_key;
pub mod render_env;
pub mod serialization;

pub use context::{Context, SharedValues};
pub use errors::{DjangoRustError, Result};
pub use object_key::ObjectKey;
pub use render_env::RenderEnv;

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
    /// So that cell is unchanged by THIS field rather than closed by it, and is
    /// recorded as such rather than quietly widened.
    ///
    /// It is no longer empty, though. This said "`{{ p.max }}` stays empty
    /// where Django renders it" until `docs/architecture/VALUE_BOUNDARY.md`
    /// falsification-tested it (#1867): ADR-027's live handle closed the cell
    /// from the other side. `{{ p.max }}` renders
    /// `Dec. 31, 9999, 11:59 p.m.`, answered by [`Context::walk_live`] off the
    /// handle [`django_json_encoded`] attaches — not by this map, which still
    /// deliberately omits all three names.
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
    /// (#2539).
    ///
    /// Both producers — [`opaque_value`] and [`django_json_encoded`] —
    /// attach one unconditionally. (Until ADR-027 Step 5, #2628, the first
    /// was gated on the ADR-027 kill-switch flag and the SINK in
    /// `Context::resolve_without_builtins` was gated on it too; the flag and
    /// both gates are gone.)
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
    /// `FromPyObject` impl claim a `dict`, a tuple, anything with
    /// `__djust_serialize__` and any `Model` in arms ABOVE [`opaque_value`],
    /// so no dict, model or manager reaches this field.
    ///
    /// A `list` and a `QuerySet` DO reach it since #2717, and until then did
    /// not: the #2695 review exempted both from the conversion's decline at
    /// any length, because their declined spelling was wrong (66 MB of
    /// djust's own serialization dicts for `{{ rows }}` over a 100 001-row
    /// queryset). That exemption cost 4 GB on a 150 000-row table and is
    /// gone; the spelling is answered at the sink instead, by
    /// [`Encoded::declined_list_spelling`]. So a large `list`/`QuerySet` is
    /// now an ordinary declined sized sequence here, and the floor question
    /// below is asked of it too — see
    /// `TestTheFloorHoldsOnEveryShapeTheDeclineNewlyClaims`.
    ///
    /// An ordinary sized sequence — a `deque`, an `array`, a `range`, a duck
    /// type with a stated `__len__` — DOES arrive here past
    /// [`OPAQUE_ITEM_CAP`], and that is what #2695 changed: over a
    /// 100 001-element `deque` of models `{{ rows.0.password }}` is answered
    /// by [`Context::walk_live`] rather than by a `Value::List` whose
    /// elements were already denylist-filtered at the conversion. The floor
    /// is the same either way — `protect_sidecar_strict` re-wraps after every
    /// segment — and that is a MEASUREMENT rather than an inference:
    /// `TestTheSerializationFloorHoldsOnTheNewHandle` in
    /// `python/tests/test_sized_sequence_conversion_2695_2693.py` renders the
    /// denylisted field on both sides of the cap and asserts both are empty,
    /// with a non-vacuity case proving the padded collection really is a
    /// carrier — the case that caught the `list` exemption making the class
    /// vacuous.
    ///
    /// [`Context::walk_live`]: crate::Context::walk_live
    ///
    /// **On an [`opaque_value`] carrier, [`Encoded::attrs`] is EMPTY when this
    /// is `Some`.** The handle is the authority for attribute lookups there,
    /// and the eager `__dict__` dump it replaces is the unbounded recursion
    /// that segfaults on a reference cycle (#2516).
    ///
    /// It is NOT a property of the field. This said "when it is `Some`,
    /// `attrs` is EMPTY" without the qualifier until
    /// `docs/architecture/VALUE_BOUNDARY.md` falsification-tested it (#1867):
    /// a [`django_json_encoded`] carrier has BOTH — `attrs` populated from
    /// [`ENCODED_ATTR_NAMES`] + [`ENCODED_CALL_NAMES`], and a handle. Both
    /// answer on one value: `{{ q.year }}` comes from the map and
    /// `{{ q.resolution }}` from the handle, and gating the handle off leaves
    /// the first standing.
    pub live: Option<std::sync::Arc<Py<PyAny>>>,
    /// `str(o)` RAISED at conversion, so `display` is a stand-in and the
    /// `{{ o }}` sink must call `str()` on [`Encoded::live`] itself — and
    /// propagate what it raises, as Django does (ADR-027 Step 5, #2628;
    /// decided for `{{ p }}` by #2429). Set only by [`handle_only_encoded`].
    ///
    /// **TRANSIENT**, exactly like `live`: not serialized, not compared. A
    /// carrier that came back from a state round trip has no handle to defer
    /// to, and renders `display` — the stand-in — which is the same
    /// degradation `live`'s own round-trip limit already has (#2767).
    pub str_raised: bool,
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
/// What was NOT free was the RESULT: `utcoffset()` and `dst()` each return a
/// `timedelta`, and converting one through [`django_json_encoded`] again cost
/// ~3.7 µs — most of an aware datetime's 21 µs (#2740). Since #2770
/// [`collect_called_attrs`] builds those two nested values with
/// [`slim_timedelta_encoded`], the same `Encoded` from the limbs alone.
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
                                str_raised: false,
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
                                str_raised: false,
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
                                str_raised: false,
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
                                str_raised: false,
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
                                str_raised: false,
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
                                str_raised: false,
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
                                str_raised: false,
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
            .map(|(_, names)| collect_called_attrs(ob, names, types.timedelta.bind(py)))
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
        str_raised: false,
    })
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
        let value = if *name == "tzinfo" {
            slim_tzinfo(&attr)
        } else {
            attr.extract::<Value>().ok()
        };
        let Some(value) = value else {
            continue;
        };
        map.insert(ObjectKey::Str((*name).to_string()), value);
    }
    map
}

/// The `tzinfo` slot of a `datetime` / `time`, carried as `str(tz)` (#2770).
///
/// `None` for a naive value stays a [`Value::None`] — the `"None"` Django
/// renders for `{{ p.tzinfo }}`, pinned across the state round trip since
/// #2484. An aware value's zone is carried as ONE string, `str(tz)`, which is
/// every reader's whole need:
///
/// - `context::lookup_segment` renders `{{ p.tzinfo }}` — Django renders
///   `str(tz)` there (`Europe/Berlin`, `UTC`, `UTC+09:00`, a custom class's
///   `<MyTz object at 0x…>`), so the carried string IS the cell.
/// - `{% if p.tzinfo %}` — a `tzinfo` object is truthy and a non-empty
///   `str()` is too. An empty `str(tz)` would answer False where Python says
///   True; no stdlib zone spells itself empty, and a custom one that does is
///   the same cell it was under the old carrier (an `Encoded` whose `truthy`
///   was measured — the one answer this slims away, recorded here).
/// - [`Encoded::temporal_object`] restores the zone from the string:
///   `ZoneInfo(name)` where the name is a key, else the `tzname` slot.
///
/// Before #2770 this slot was the FULL conversion of the `tzinfo` object —
/// an [`opaque_value`] `Encoded` of a `ZoneInfo`, some 3 µs of `str` / `repr`
/// / `isinstance` / equality-class probing per aware datetime — of which the
/// three readers above consumed exactly `display`. Under the flag-off escape
/// hatch a `tzinfo` with a `__dict__` even took the bulk-dump arm and rendered
/// as a dict where Django renders `str(tz)`; the string carrier closes that
/// cell as a side effect (`test_encoded_attributes_2481.py`).
///
/// This is the one msgpack-SHAPE change of #2770: the nested `ENCODED_TAG`
/// map at `attrs["tzinfo"]` becomes a plain string. The reader side accepts
/// BOTH — `temporal_object` reads an old-shape `Encoded` through its
/// `Display`, which is the same `str(tz)` — so a state entry written by a
/// pre-#2770 process restores under this one
/// (`test_aware_datetime_slim_2770.py`).
///
/// Fails SOFT like every other name here: a `__str__` that raises skips the
/// slot rather than storing a guess.
fn slim_tzinfo(attr: &Bound<'_, PyAny>) -> Option<Value> {
    if attr.is_none() {
        return Some(Value::None);
    }
    Some(Value::String(attr.str().ok()?.extract::<String>().ok()?))
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
///
/// # The `timedelta` results are built SLIM (#2770)
///
/// `utcoffset()` and `dst()` return a `timedelta`, and before #2770 each went
/// through the full [`django_json_encoded`] recursion — `str()`, `repr()`,
/// `DjangoJSONEncoder().default()`, `bool()`, three limb reads, a
/// `total_seconds()` call and an `isinstance` sweep, ~3.7 µs apiece and the
/// biggest single line in an aware datetime's 21 µs conversion (#2740's
/// measurement). [`slim_timedelta_encoded`] builds the SAME `Encoded` — every
/// slot, byte for byte on the wire — from the three limbs in Rust, so the
/// msgpack shape of this map does not change for those two names; only the
/// interpreter round-trips do. An exact `timedelta` is what the C
/// implementation returns from both methods; a subclass, or a limb outside
/// the exact-`f64` range, declines the slim path and takes the full one.
fn collect_called_attrs(
    ob: &Bound<'_, PyAny>,
    names: &[&str],
    timedelta_cls: &Bound<'_, PyAny>,
) -> IndexMap<ObjectKey, Value> {
    let mut map = IndexMap::with_capacity(names.len());
    for name in names {
        let Ok(method) = ob.getattr(*name) else {
            continue;
        };
        let Ok(result) = method.call0() else {
            continue;
        };
        let value = match slim_timedelta_encoded(&result, timedelta_cls) {
            Some(slim) => Value::Encoded(Box::new(slim)),
            None => match result.extract::<Value>() {
                Ok(value) => value,
                Err(_) => continue,
            },
        };
        map.insert(ObjectKey::Str((*name).to_string()), value);
    }
    map
}

/// The largest magnitude, in microseconds, at which `n as f64 / 1e6` is
/// CPython's own `total_seconds()` bit for bit: below `2**53` the integer is
/// exact as a float and `long_true_divide` takes the same one-rounding path.
/// A `utcoffset()` / `dst()` is bounded to a day (`8.64e10` µs), so the slim
/// path never declines in practice; the bound is what makes the transcription
/// PROVABLY equal rather than usually equal.
const SLIM_TIMEDELTA_EXACT_MICROS: i64 = 1 << 53;

/// The [`Encoded`] that [`django_json_encoded`] would build for an EXACT
/// `datetime.timedelta`, built from its three limbs without asking the
/// interpreter for any of the four strings (#2770).
///
/// `None` — "take the full path" — for anything that is not exactly the C
/// `timedelta` (a subclass may override `__str__` / `__bool__` / `__repr__`,
/// which the full path measures and this one cannot), or whose magnitude is
/// past [`SLIM_TIMEDELTA_EXACT_MICROS`].
///
/// Every slot is the one the full path measures, and the equality is PINNED
/// two ways rather than reasoned about: `test_slim_timedelta_2770.rs` sweeps
/// random limbs against live `str()` / `repr()` / `bool()` /
/// `total_seconds()`, and `test_aware_datetime_slim_2770.py` asserts the
/// nested `attrs["utcoffset"]` payload of an aware datetime is byte-equal to
/// the payload of the same `timedelta` converted at top level — which is the
/// full path, `DjangoJSONEncoder` spelling included.
///
/// The live handle IS attached: the value being slimmed is a real
/// `timedelta` the caller already holds, so `{{ p.utcoffset.resolution }}`
/// keeps answering off the handle exactly as before (#2741). The handle is a
/// refcount, not a conversion.
pub fn slim_timedelta_encoded(
    ob: &Bound<'_, PyAny>,
    timedelta_cls: &Bound<'_, PyAny>,
) -> Option<Encoded> {
    if !ob.get_type().is(timedelta_cls) {
        return None;
    }
    let (days, micros_of_day) = timedelta_limbs(ob)?;
    let seconds = micros_of_day / 1_000_000;
    let microseconds = micros_of_day % 1_000_000;
    let total_micros = days
        .checked_mul(86_400_000_000)?
        .checked_add(micros_of_day)?;
    if total_micros.abs() >= SLIM_TIMEDELTA_EXACT_MICROS {
        return None;
    }
    let mut attrs = IndexMap::with_capacity(4);
    attrs.insert(ObjectKey::Str("days".to_string()), Value::Integer(days));
    attrs.insert(
        ObjectKey::Str("seconds".to_string()),
        Value::Integer(seconds),
    );
    attrs.insert(
        ObjectKey::Str("microseconds".to_string()),
        Value::Integer(microseconds),
    );
    attrs.insert(
        ObjectKey::Str("total_seconds".to_string()),
        Value::Float(total_micros as f64 / 1e6),
    );
    Some(Encoded {
        type_name: "datetime.timedelta".to_string(),
        display: timedelta_str(days, seconds, microseconds),
        json: timedelta_iso_string(total_micros),
        truthy: total_micros != 0,
        len: None,
        iterable: false,
        repr: timedelta_repr(days, seconds, microseconds),
        cmp_key: Some(CmpKey {
            domain: CMP_DOMAIN_TIMEDELTA,
            hi: days,
            lo: micros_of_day,
        }),
        attrs,
        items: None,
        eq_class: None,
        live: Some(std::sync::Arc::new(ob.clone().unbind())),
        display_safe: false,
        str_raised: false,
    })
}

/// CPython's `timedelta.__str__`, on the normalised limbs:
/// `[-]D day[s], ]H:MM:SS[.ffffff]`.
fn timedelta_str(days: i64, seconds: i64, microseconds: i64) -> String {
    let (hh, rem) = (seconds / 3600, seconds % 3600);
    let (mm, ss) = (rem / 60, rem % 60);
    let mut s = String::with_capacity(32);
    if days != 0 {
        s.push_str(&days.to_string());
        s.push_str(if days.abs() == 1 { " day, " } else { " days, " });
    }
    s.push_str(&format!("{hh}:{mm:02}:{ss:02}"));
    if microseconds != 0 {
        s.push_str(&format!(".{microseconds:06}"));
    }
    s
}

/// CPython's `timedelta.__repr__` (3.7+ keyword form):
/// `datetime.timedelta(days=…, seconds=…, microseconds=…)`, naming only the
/// non-zero limbs and `datetime.timedelta(0)` for none.
fn timedelta_repr(days: i64, seconds: i64, microseconds: i64) -> String {
    let mut args = Vec::with_capacity(3);
    if days != 0 {
        args.push(format!("days={days}"));
    }
    if seconds != 0 {
        args.push(format!("seconds={seconds}"));
    }
    if microseconds != 0 {
        args.push(format!("microseconds={microseconds}"));
    }
    if args.is_empty() {
        args.push("0".to_string());
    }
    format!("datetime.timedelta({})", args.join(", "))
}

/// Django's `duration_iso_string`: `[-]P{D}DT{HH}H{MM}M{SS}[.ffffff]S`, with
/// a NEGATIVE delta negated first and the sign carried as a prefix — the
/// normalisation the `DjangoJSONEncoder` docs above call out as one of the
/// three transcriptions worth not doing by hand. It is done here on the
/// TOTAL, so the sign question is one `abs()` rather than a per-limb branch.
fn timedelta_iso_string(total_micros: i64) -> String {
    let sign = if total_micros < 0 { "-" } else { "" };
    let total = total_micros.abs();
    let days = total / 86_400_000_000;
    let rem = total % 86_400_000_000;
    let seconds = rem / 1_000_000;
    let microseconds = rem % 1_000_000;
    let (hh, rem_s) = (seconds / 3600, seconds % 3600);
    let (mm, ss) = (rem_s / 60, rem_s % 60);
    let ms = if microseconds != 0 {
        format!(".{microseconds:06}")
    } else {
        String::new()
    };
    format!("{sign}P{days}DT{hh:02}H{mm:02}M{ss:02}{ms}S")
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
    /// The operand a sink that spells this value WHOLE must use, when the
    /// value itself cannot spell it (#2717).
    ///
    /// The ONE routing point for [`Encoded::declined_list_spelling`], read by
    /// the three sinks that render a container whole — `{{ v }}`, `pprint`
    /// and `json_script` — at FOUR call sites, because `{{ v }}` reaches it
    /// twice: `renderer::localize_if_number` short-circuits `Display` for a
    /// non-temporal carrier and returns `Encoded::display` directly, so
    /// patching `Display` alone left the actual `{{ v }}` cell unchanged.
    /// That fourth site was found by grepping the SINK rather than by listing
    /// the callers it seemed to have. `None` for every other value and for every
    /// other carrier, which is the common case and costs one integer compare;
    /// `None` too when the live walk RAISES, so a caller keeps whatever it
    /// would have rendered before this existed rather than growing an error
    /// path (`Display` has no error channel, and the two filters answer their
    /// own `str()`/JSON spelling as they always did).
    ///
    /// Pinned as a SET rather than as a floor by
    /// `test_the_container_spelling_sinks_are_the_three_named` (#1125): a
    /// fourth sink that spells a value whole has to be added here, or it
    /// silently renders djust's own serialization dicts for a declined
    /// queryset.
    pub fn container_spelling(&self) -> Option<Value> {
        match self {
            Value::Encoded(e) => e.declined_list_spelling()?.ok(),
            _ => None,
        }
    }

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
            //
            // EXCEPT for a carrier the conversion declined for LENGTH whose
            // spelling is its items' list repr — a `list`, a `QuerySet`, a
            // djust queryset proxy (#2717). `str(o)` for those is a `list`
            // repr djust never renders (66 MB of its own identity dicts for a
            // serialised queryset), so the items are read from the live handle
            // HERE, at the sink, rather than enumerated at binding time for
            // every cell. `declined_list_spelling` answers `None` in one
            // integer compare for everything else, which is what keeps this
            // arm cheap; a Python failure mid-walk also answers the old
            // `display`, because `Display` has no error channel and an
            // unchanged cell beats a panic.
            Value::Encoded(e) => match self.container_spelling() {
                Some(spelled) => write!(f, "{spelled}"),
                None => write!(f, "{}", e.display),
            },
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

/// `str(o)` as a Rust `String`, crossing a lone surrogate as U+FFFD (#2555).
///
/// A CPython `str` may hold a lone UTF-16 surrogate — `surrogateescape`
/// decoding of a filename, a header or subprocess output produces one — and
/// it is a legal `str` that only fails when ENCODED. `extract::<String>()`
/// is that encode, so it fails; the value then fell through every scalar arm
/// to the fallback block, where a `str` is a truthy, re-iterable, sized
/// object whose items are one-character `str`s that fail the same way, and
/// the conversion recursed into itself until the stack overflowed (SIGSEGV).
///
/// Django renders the surrogate str as itself and only its HTTP encoding
/// raises. Rust cannot hold the code point at all, so this is the choice
/// Python's own `errors="replace"` makes — the ONE character becomes U+FFFD
/// and everything around it survives — rather than a raise, which would turn
/// a filename in a listing into a 500 for the whole page.
fn py_str_lossy(ob: &Bound<'_, PyAny>) -> PyResult<String> {
    py_string_lossy(&ob.str()?)
}

/// A `str` as a Rust `String`, one U+FFFD per lone surrogate (#2555).
///
/// The fast path is PyO3's zero-copy UTF-8 view, which every well-formed
/// string takes; only a string that fails it pays for one Python call:
/// `encode("utf-8", "surrogatepass")`, which spells each lone surrogate as
/// its own three-byte sequence (`ED A0..BF xx`), and
/// [`surrogatepass_bytes_to_string`] folds each such sequence into ONE
/// U+FFFD. Two spellings were rejected on measurement: PyO3's
/// `to_string_lossy` replaces each of the three BYTES (`"\udcc0x"` came out
/// `"���x"`), and `encode("utf-8", "replace")` substitutes `?`. A UTF-16
/// round trip was the first fix and is ALSO wrong, one axis over: two
/// adjacent lone surrogates that happen to form a valid pair are JOINED by
/// UTF-16 decoding into one astral character, so `"😀"` — two
/// Python code points, `len` 2 — rendered as `'😀'` with `|length` 1 (the
/// #2673 review). Per-code-point replacement is the rule, and the
/// `surrogatepass` spelling honours it because CPython never joins there.
fn py_string_lossy(s: &Bound<'_, PyString>) -> PyResult<String> {
    if let Ok(text) = s.to_cow() {
        return Ok(text.into_owned());
    }
    let encoded = s.call_method1("encode", ("utf-8", "surrogatepass"))?;
    let bytes: &[u8] = encoded.extract()?;
    Ok(surrogatepass_bytes_to_string(bytes))
}

/// `surrogatepass` UTF-8 bytes to a `String`, one U+FFFD per encoded lone
/// surrogate (#2555). Every other byte sequence is valid UTF-8 by
/// construction (CPython produced it), so the only invalid runs are the
/// three-byte surrogate spellings, and each is replaced as a unit.
fn surrogatepass_bytes_to_string(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len());
    let mut rest = bytes;
    loop {
        match std::str::from_utf8(rest) {
            Ok(tail) => {
                out.push_str(tail);
                return out;
            }
            Err(e) => {
                let valid = e.valid_up_to();
                // `valid_up_to` is by definition a valid prefix; the fallback
                // arm is unreachable and kept only so nothing here can panic.
                out.push_str(std::str::from_utf8(&rest[..valid]).unwrap_or(""));
                out.push('\u{FFFD}');
                // A `surrogatepass` surrogate is exactly three bytes; anything
                // else that is invalid (which CPython does not emit) skips the
                // bytes the decoder rejected, or one byte if it rejected none.
                let skip = if rest.len() >= valid + 3 && rest[valid] == 0xED {
                    3
                } else {
                    e.error_len().unwrap_or(1).max(1)
                };
                rest = &rest[(valid + skip).min(rest.len())..];
            }
        }
    }
}

/// The elements of a Python object that is a BOUNDED sequence, or `None`
/// (#2572).
///
/// The ONE gate for both sequence sinks — the `Value::List` arm of the
/// conversion and [`crosses_as_encoded`]'s probe of it — so the two cannot
/// drift (#1646). Both used to call PyO3's `extract::<Vec<_>>()`, which
/// accepts anything `PySequence_Check` accepts and then drives it through
/// `iter()`. For a class that defines `__getitem__` and nothing else that is
/// CPython's legacy sequence protocol: `o[0]`, `o[1]`, … until `IndexError`.
/// An object whose `__getitem__` never raises is therefore iterated forever,
/// and the render hung. Django never iterates such an object for `{{ v }}`
/// — it resolves `{{ v.0 }}` by ONE `__getitem__` call and renders `{{ v }}`
/// as `str(v)`.
///
/// The termination rule is the object's own stated bound: a sequence with no
/// `__len__` has not stated one and is DECLINED here (it keeps the fallback
/// block, whose iteration walk is already capped by [`OPAQUE_ITEM_CAP`]);
/// one that has is read through `iter()` exactly as before and declined the
/// moment it yields PAST its bound — a decline, not a truncation, so the cap
/// can never produce a short list (#2129: a rule about the operation, not a
/// list of shapes). A `list`, `tuple`, `range`, `bytes`, `deque`, `array`,
/// numpy array and evaluated `QuerySet` all state a length and cross exactly
/// as they did — up to [`OPAQUE_ITEM_CAP`]. See
/// [`stated_len_is_too_large_to_enumerate`] for what happens past it and why
/// enumerating a stated billion at BINDING time is the wrong half of the
/// rule (#2678, #2695).
///
/// A `str` is refused, as PyO3's `Vec` extraction refuses it: the `str` arm
/// sits above the sequence arm and claims every string first.
///
/// The membership test is `PySequence_Check` — the one PyO3's `Vec`
/// extraction used — and deliberately NOT `cast::<PySequence>()`, which
/// PyO3 answers with `isinstance(o, collections.abc.Sequence)`. That
/// stricter test would silently move every unregistered user class with
/// `__getitem__` and `__len__` out of the list arm, which is a second
/// behaviour change this fix has no reason to make.
/// Is this object's stated `__len__` too large to spend at the CONVERSION?
/// (#2678, widened to every sized sequence by #2695.)
///
/// The ONE statement of that question, read by both sites that would
/// otherwise pay `len` calls up front — [`bounded_sequence_items`] and
/// [`opaque_gate`] — so the conversion's two halves cannot disagree about
/// which objects are enumerated (#1646).
///
/// TWO conditions, and both are load-bearing:
///
/// * **The stated length is past [`OPAQUE_ITEM_CAP`].** Then materialising
///   the object costs one `Value` per item before the template has said what
///   it wants, and `range(10**9)` is thirty gigabytes for a `{{ v.0 }}` that
///   Django answers with a single `__getitem__` (#2695). Django never
///   enumerates at binding time — `{{ v.0 }}`, `{{ v|length }}` and
///   `{{ v }}` are `current[0]`, `len(v)` and `str(v)` — so declining here
///   is what Django parity looks like, not a safeguard against it.
///
///   #2678's first version capped on `len` alone and REGRESSED
///   `list(range(100_001))`: `{% for %}` raised, `|first` raised, `|join`
///   raised. Its second version therefore also required the object to have
///   no `__iter__` — which fixed the regression by never claiming `range`
///   at all, and so left #2695's hang exactly as it found it. The bound
///   belongs on BOTH halves of the same rule and this is only the first:
///   the conversion declines, and the SINKS then walk the live object under
///   [`Encoded::live_walk_terminates`], which is where "can the walk end"
///   is asked. `collections.deque(range(100_001))` crosses as a carrier here
///   and still renders every item at `{% for %}`, because its walk
///   terminates.
///
/// The decline only improves on the hang because ADR-027's carrier holds a
/// LIVE HANDLE, so `{{ v }}`, `{{ v.0 }}`, `{{ v|length }}` and `{% if v %}`
/// are answered from the object itself. (Until ADR-027 Step 5 — #2628 — this
/// predicate also required the ADR-027 kill-switch flag, because the
/// eager escape hatch had no handle and a decline there landed on `str(o)`;
/// the hatch is gone and the cap is the whole condition.)
///
/// ONE condition and not two. A `list` and a Django `QuerySet` were
/// EXEMPT from #2695 until #2717, because their declined spelling was wrong
/// — `{{ rows }}` over a 100 001-row queryset rendered 66 MB of djust's own
/// identity dicts where the same queryset one row shorter rendered
/// `[qs0, qs1, qs2]`. That exemption cost 4 GB on a 150 000-row table
/// (`{{ v|length }}`: 4 437 MB with it, 565 MB without), so #2717 closed the
/// spelling defect instead of paying for it — see
/// [`Encoded::declined_list_spelling`], which answers the three container
/// sinks from the live handle. Nothing is exempt now.
fn stated_len_is_too_large_to_enumerate(len: usize) -> bool {
    len > OPAQUE_ITEM_CAP
}

/// Is this object's SPELLING its ITEMS' list repr, rather than its own
/// `str(o)`? (#2717, closing the #2695-review exemption.)
///
/// The ONE statement of that question, asked by
/// [`Encoded::declined_list_spelling`] at the three container sinks that
/// spell a value whole — `{{ v }}`, `|pprint`, `|json_script` — for a
/// carrier the conversion DECLINED to enumerate. Two shapes answer yes:
///
/// * a **`list`**. `str(list)` IS its elements' repr, so a declined list has
///   to be spelled from its elements or not at all.
/// * a Django **`QuerySet`**. djust has never matched Django's
///   `<QuerySet [...]>` here — a queryset in a djust context is serialised
///   to a list of identity dicts, so `{{ rows }}` is `[qs0, qs1, qs2]` — and
///   that spelling must not change at 100 000 rows.
///
/// **Not a `_SidecarQuerySetProxy`, and it does not need to be.** The object
/// the `{{ v }}` conversion actually sees for a queryset IS that proxy —
/// `Context::walk_live` runs `protect_sidecar_strict` over the root — so a
/// third `hasattr("__djust_serialize__")` arm here looks obviously required.
/// It is dead: the `FromPyObject` fallback block claims a
/// `__djust_serialize__` object one arm ABOVE [`opaque_value`] and converts
/// the `list` it hands back, so the proxy never becomes a carrier and this
/// predicate is never asked about one. That arm shipped in #2717's first
/// pass and was removed when its gate-off failed nothing;
/// `crosses_as_encoded` over a padded proxy answers `False` on both sides of
/// the cap, which is the same fact from the outside and is asserted in
/// `TestTheFloorHoldsOnEveryShapeTheDeclineNewlyClaims::test_the_sweep_is_not_vacuous`.
///
/// **Why this used to be an exemption instead.** #2695 declined every sized
/// sequence past the cap; its review found that declining these three
/// spelled `{{ rows }}` over a 100 001-row queryset as
/// `[{'id': 1, 'pk': 1, '__str__': 'qs0', '__model__': 'User', …}]` — 66 MB
/// of djust's own serialization dicts — where the same queryset one row
/// shorter rendered `[qs0, qs1, qs2]`, and answered `{{ rows.0 }}` with
/// `''`. So the review exempted a `list` and a `QuerySet` from the decline
/// at any length, which fixed the spelling and cost 4 GB: on an UNEVALUATED
/// `User.objects.all()` over a real 150 000-row table, `{{ v|length }}` was
/// 4 437 MB / 13.4 s with the exemption and 565 MB / 10.1 s without it
/// (#2717's own measurement; the issue's, on the same shape, was
/// 4 650 / 593 MB). `len()` sinks `_fetch_all()` — about 140 MB of rows —
/// and NOT their conversion into [`Value`]s, which is the other ~4 GB.
///
/// #2717 keeps the spelling and drops the cost by moving the enumeration to
/// the three sinks that need it: the decline now applies to these shapes
/// too, and the sinks re-derive the list through
/// [`Encoded::consume_live_items`] — the SAME walk `{% for %}` and `|join`
/// already use, so the spelling is the `Value::List` one by construction
/// rather than by transcription (#1646). The other half, `{{ rows.0 }}`,
/// was `_SidecarQuerySetProxy` having no `__getitem__` for the live walk to
/// subscript; it has one now.
///
/// Nothing else answers yes, and that is the point of naming the SPELLING
/// rather than a size: a `tuple` never reaches here (the tuple arm above
/// [`bounded_sequence_items`] claims it), and `range` / `bytes` / `array` /
/// `deque` / a numpy array / a duck type with a stated `__len__` each spell
/// their own container — `str(range(3))` is `range(0, 3)` — so their
/// declined carrier is already right and must NOT be re-spelled as a list
/// (#2704).
fn spelling_is_the_items_list_repr(ob: &Bound<'_, PyAny>) -> bool {
    if ob.is_instance_of::<PyList>() {
        return true;
    }
    // A cached `sys.modules` lookup, and only ever reached FROM HERE once
    // the stated length is already past the cap. (The other caller,
    // `list_repr_is_this_objects_own_spelling`, asks it at any length — but
    // only for an object `PySequence_Check` already claimed and the `PyList`
    // arm already declined, so a dict, a model and an ordinary object never
    // pay it.)
    is_django_queryset(ob)
}

/// `isinstance(o, django.db.models.QuerySet)`, through a cached `sys.modules`
/// lookup. ONE statement of the test, because two functions ask it about the
/// same objects for two different reasons, and a second copy is the #1646
/// shape:
///
/// * [`list_repr_is_this_objects_own_spelling`] (#2704) asks at ANY length,
///   to decide which conversion ARM claims the object;
/// * [`spelling_is_the_items_list_repr`] (#2717) asks only PAST
///   [`OPAQUE_ITEM_CAP`], to decide what an already-declined carrier SPELLS.
///
/// The second caller was `len_call_already_materialised_the_items` until
/// #2717 renamed it and inverted its job (it named the shapes EXEMPT from
/// the cap; its successor names the shapes whose spelling is their items'
/// list repr). Both callers survived that change, so the "two reasons"
/// above is still two — a claim worth re-checking rather than inheriting,
/// since a rename that drops a caller would leave this comment true-looking
/// and false.
fn is_django_queryset(ob: &Bound<'_, PyAny>) -> bool {
    ob.py()
        .import("django.db.models")
        .and_then(|m| m.getattr("QuerySet"))
        .and_then(|cls| ob.is_instance(&cls))
        .unwrap_or(false)
}

/// Would `[a, b, c]` be this object's OWN spelling? (#2704)
///
/// `Value::List`'s `Display` is a **list** repr, so every object that crosses
/// through [`bounded_sequence_items`] renders `{{ v }}` as `[3, 1, 2]`.
/// Django renders `str(o)`, and for every non-`list` sequence that is the
/// container's own spelling instead: `range(0, 3)`, `deque([3, 1, 2])`,
/// `array('i', [3, 1, 2])`, `b'\x03\x01\x02'`, `<X object at 0x…>`. Measured
/// against Django 5.2 for all five, in
/// `python/tests/test_sized_sequence_conversion_2695_2693.py`.
///
/// #2695 made the divergence LENGTH-DEPENDENT rather than uniform, which is
/// worse: past [`OPAQUE_ITEM_CAP`] the same shapes decline into the carrier
/// and DO render `str(o)`, so `range(3)` was `[0, 1, 2]` and `range(10**9)`
/// was `range(0, 1000000000)`. This retires the length-dependence by asking
/// the SPELLING question at every length instead — the carrier is what a
/// non-`list` sequence gets, and the cap only decides whether its items are
/// read here or at the sink.
///
/// TWO exemptions, and each is a different reason (a third — the eager
/// escape hatch, which had no live handle to decline onto — was deleted with
/// the ADR-027 kill-switch flag in Step 5, #2628):
///
/// * **A `list`.** `str([3, 1, 2])` IS `[3, 1, 2]`, so there is nothing to
///   fix; a `list` is the one shape whose Django answer this arm already
///   spells. (A `tuple` never reaches here — the tuple arm above
///   [`bounded_sequence_items`] claims it and `Value::Tuple` spells itself.)
/// * **A Django `QuerySet`.** Its Django spelling is `<QuerySet [...]>`, so
///   it IS divergent, and this arm is what keeps djust's own answer
///   (`[qs0, qs1, qs2]`) for one.
///
///   The reason USED to be a hazard: the `render_template` path hands the
///   conversion a `_SidecarQuerySetProxy` that had no `__getitem__`, so a
///   declined queryset answered `{{ rows.0 }}` with `''`. #2717 gave the
///   proxy one, so that hazard is gone and this comment would be asserting
///   a defect that no longer exists.
///
///   What keeps the arm is narrower and outlives the fix: #2717 governs what
///   a carrier DECLINED FOR LENGTH spells — [`Encoded::declined_list_spelling`]
///   opens with `len > OPAQUE_ITEM_CAP` — while this arm governs which arm
///   claims a queryset at ANY length. Drop it and a THREE-row queryset
///   crosses as a carrier, whose `{{ rows }}` is `str(...)` rather than
///   `[qs0, qs1, qs2]`, with nothing in #2717 to re-spell it.
///   `TestARealQuerySetIsSpelledTheSameOnBothSidesOfTheCap` pins the two
///   sides against each other and
///   `TestASmallQuerySetIsUnmovedByTheDeclineChange2717` pins the small side
///   against Django directly, because "the two agree" cannot catch both
///   moving together. Retiring this arm is its own change with its own
///   before/after, not a side effect of a length fix (CLAUDE.md #1079).
fn list_repr_is_this_objects_own_spelling(ob: &Bound<'_, PyAny>) -> bool {
    if ob.is_instance_of::<PyList>() {
        return true;
    }
    is_django_queryset(ob)
}

fn bounded_sequence_items<'py>(ob: &Bound<'py, PyAny>) -> Option<Vec<Bound<'py, PyAny>>> {
    if ob.is_instance_of::<PyString>() {
        return None;
    }
    // SAFETY: `ob` is a live, GIL-held reference; `PySequence_Check` only
    // reads the type's slots and cannot fail.
    if unsafe { pyo3::ffi::PySequence_Check(ob.as_ptr()) } == 0 {
        return None;
    }
    if !list_repr_is_this_objects_own_spelling(ob) {
        // #2704: a `deque` / `range` / `array` / `bytes` / sized user class
        // crosses as the CARRIER instead, whose `{{ v }}` is `str(o)` and
        // whose item sinks read the live object.
        //
        // BELOW `PySequence_Check` deliberately: the `QuerySet` half of that
        // predicate is a `sys.modules` import plus an `isinstance`, and every
        // dict, model and ordinary object reaches this function. Behind the
        // slot check only a real sequence pays it, and a `list` — the common
        // one — is answered by the `PyList` arm before the import.
        return None;
    }
    let len = ob.len().ok()?;
    if stated_len_is_too_large_to_enumerate(len) {
        return None;
    }
    let mut items = Vec::with_capacity(len.min(OPAQUE_ITEM_CAP));
    for item in ob.try_iter().ok()? {
        if items.len() == len {
            return None;
        }
        items.push(item.ok()?);
    }
    Some(items)
}

/// How deep [`Value`]'s conversion may recurse into an object before the
/// element it is looking at crosses as `str(o)` instead (#2624).
///
/// Every container arm converts its elements through the same `extract`,
/// so an object whose elements are objects like itself recurses without
/// bound — and NOT only a cycle, which the fallback block's `__dict__` walk
/// already guards. A `types.GenericAlias` is the shape that found it:
/// `{{ v.0 }}` over a `list`-subclass CLASS is Django's own
/// `current[int(bit)]`, which honours `__class_getitem__` and yields
/// `MyList[0]`; that alias is truthy, re-iterable, has no `__len__`, and
/// `iter()` yields a starred copy of itself — a FRESH object each level, so
/// no identity check ends it. The conversion built one carrier per level
/// until the stack overflowed (SIGSEGV) — on both settings of the since-
/// deleted ADR-027 kill-switch flag, because both walks ended in the
/// same conversion.
///
/// Python's own recursion limit (`sys.getrecursionlimit()`, 1000 by default):
/// the depth at which CPython itself refuses to `repr`, `json.dumps`,
/// `deepcopy` or `pickle` a nested structure, so no structure Python's own
/// tooling accepts can observe this ceiling. A first draft said 128 "sized
/// against a 512 KiB `sync_to_async` worker stack" — that stack does not
/// exist: every CPython thread is 16 MiB on macOS and 8 MiB under glibc,
/// `main` converted 5000 nested lists on a real thread, and 128 REGRESSED
/// `{{ v.0.0…}}` over a 150-deep list from `'leaf'` to `'['` (the #2673
/// review, which measured all three). The value has to fit the smallest
/// REAL stack: a 1000-deep chain of the heaviest per-level frame (the
/// `GenericAlias` case, `opaque_value` + `extract` per level) converts on an
/// 8 MiB thread with room to spare — `TestTheDepthCeiling` pins that.
///
/// Past it the element crosses as `str(o)` — the terminal arm every object
/// already had — so the top-level value keeps its carrier and renders as
/// Django does (`str(alias)`).
pub const MAX_CONVERSION_DEPTH: usize = 1000;

thread_local! {
    /// The current [`Value`] conversion nesting on this thread. A
    /// thread-local because `extract` is a trait method with no parameter to
    /// thread a counter through (the same reason the deleted ADR-027
    /// kill-switch cell was one).
    static CONVERSION_DEPTH: std::cell::Cell<usize> = const { std::cell::Cell::new(0) };
}

/// Restores [`CONVERSION_DEPTH`] on every exit path — an early `?`, a Python
/// exception, a panic unwinding to the PyO3 boundary — so one failed
/// conversion cannot leave the thread's counter raised for the next render.
struct DepthGuard(usize);

impl Drop for DepthGuard {
    fn drop(&mut self) {
        CONVERSION_DEPTH.with(|c| c.set(self.0));
    }
}

impl<'py> FromPyObject<'_, 'py> for Value {
    // PyO3 0.29 reshaped FromPyObject: it now carries an associated `Error`
    // type and a single `extract(Borrowed<...>)` method (the old single-lifetime
    // `extract_bound(&Bound<...>)` was removed). `Borrowed` derefs to `Bound`,
    // so the body below is unchanged — method calls on `ob` auto-deref.
    type Error = PyErr;
    fn extract(ob: pyo3::Borrowed<'_, 'py, PyAny>) -> PyResult<Self> {
        // [`MAX_CONVERSION_DEPTH`]'s guard (#2624), INLINE rather than as a
        // wrapper around a second function: the structural pins that read
        // this conversion's body (`TestTheSinkHasExactlyTheCallersItClaims`)
        // find it by `fn extract(`, and a wrapper would hide the body from
        // them. `_restore` drops on every exit path below.
        let depth = CONVERSION_DEPTH.with(|c| c.get());
        if depth >= MAX_CONVERSION_DEPTH {
            return Ok(Value::String(py_str_lossy(&ob.to_owned())?));
        }
        CONVERSION_DEPTH.with(|c| c.set(depth + 1));
        let _restore = DepthGuard(depth);

        // NOTE (#2731): a [`TemplateObject`] — the shape a `Value::Encoded`
        // takes for a bridged tag handler — can come back through here, in a
        // handler's BINDING: `{% regroup %}` over a `frozenset` of
        // `frozenset`s puts one in every `GroupedResult.list`. There is
        // deliberately no unwrap arm for it. The wrapper answers `__str__` /
        // `__repr__` / `__len__` / `__iter__` / `__bool__` from the very facts
        // `opaque_value` measures, so re-measuring it reconstructs the same
        // `Encoded` — an unwrap arm was written, gate-off-tested, and found to
        // change no output for any reachable input while adding a downcast
        // attempt to EVERY value conversion in the workspace. Deleted rather
        // than kept as a decoration (#2233).
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
        } else if let Ok(s) = ob.cast::<PyString>() {
            // By TYPE, not by `extract::<String>()` succeeding: a `str`
            // holding a lone surrogate fails that extraction and used to fall
            // through to the fallback block, where it recursed into its own
            // characters until the stack overflowed (#2555). It crosses
            // lossily instead — see `py_string_lossy`.
            let s = py_string_lossy(&s)?;
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
        } else if let Some(list) = bounded_sequence_items(&ob.to_owned()).and_then(|items| {
            // Through the bounded gate, not `extract::<Vec<Value>>()`: an
            // object whose `__getitem__` never raises is an endless legacy
            // sequence to PyO3's iteration and hung the render (#2572). An
            // element that fails to convert declines the arm, exactly as the
            // failed `Vec` extraction did.
            items
                .iter()
                .map(|item| item.extract::<Value>().ok())
                .collect::<Option<Vec<Value>>>()
        }) {
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
                    //
                    // The list-of-dicts is a `list`, so past
                    // [`OPAQUE_ITEM_CAP`] it is DECLINED here like any other
                    // sized sequence and crosses as a carrier over that list
                    // (#2717). `{{ rows }}` / `|pprint` / `|json_script` then
                    // spell it through [`Encoded::declined_list_spelling`],
                    // which re-derives exactly these items from the live
                    // handle — so the rendered bytes are the same either side
                    // of the cap, and a template that asks only for
                    // `{{ rows|length }}` or `{{ rows.0 }}` never pays for
                    // them. Between the #2695 review and #2717 a `list` was
                    // EXEMPT from the decline for this reason and the whole
                    // list converted here at any length, which is the 4 GB
                    // #2717 measured.
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
            // The terminal arm, reached only when a probe in `opaque_value`
            // raised (`bool(o)`, `str(o)`, `repr(o)`, `type(o).__name__`, or
            // an item enumeration). The object still crosses WITH its live
            // handle — see [`handle_only_encoded`] — because the lookups
            // Django answers without those probes (`{{ o.attr }}`,
            // `{{ o.0 }}`, a refused mutator) must keep resolving against the
            // real object. The `__dict__` bulk-dump arm that used to sit here
            // (`Value::Object` from the object's public attributes) answered
            // exactly those lookups for a probe-failing object under the
            // default until ADR-027 Step 5 (#2628) deleted it with the
            // kill-switch flag; this is its Django-shaped replacement, and
            // `python/tests/test_sidecar_on_all_render_paths_2501.py`
            // (`TestComponentMutatorsAreNeverAutoCalled`,
            // `TestDjangosExceptionSetsAtEverySegment`) is what found the gap.
            Ok(Value::Encoded(Box::new(handle_only_encoded(
                &ob.to_owned(),
            ))))
        }
    }
}

/// The most items the conversion will read out of ANY object, sized or not
/// (#2477/#2489, #2678).
///
/// A ceiling and not a truncation point: past it the items are never
/// enumerated at conversion, so the cap can never produce a short collection.
/// An unsized object that yields more than this, or a sized one whose stated
/// `__len__` exceeds it (`range(10**9)`, a `__len__` of `10**7` over a
/// never-raising `__getitem__`), is UNBOUNDED for this conversion: under
/// ADR-027 it crosses with a live handle and no items, and the sinks that
/// need the items — `{% for %}`, `|join`, `in` — read them through the handle
/// via [`Encoded::consume_live_items`], which RAISES past this same cap. On
/// the eager escape hatch there is no handle, and such an object keeps the
/// terminal `Value::String(str(o))` path it always had.
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
    /// The items CANNOT be enumerated at conversion without unbounded work
    /// (#2670, #2678): an unsized iterable whose walk passed
    /// [`OPAQUE_ITEM_CAP`], or a sized one whose stated `__len__` exceeds it.
    /// Only ever `true` under ADR-027, where a live handle carries the object
    /// and the sinks read it lazily; on the eager escape hatch such an object
    /// is declined instead.
    pub unbounded: bool,
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
    let mut unbounded = false;
    if let Some(it) = iterator {
        // `iter(o) is o` — a one-shot iterator. Reading it here would consume
        // the caller's object, so it is NOT enumerated at conversion.
        //
        // Under ADR-027 it is ADMITTED with `items` left `None`: the
        // `Encoded` carries a live handle, and the `{% for %}` sink consumes
        // it once through [`Encoded::consume_live_items`] — Django's
        // `list(values)` in `ForNode.render`, row V (#2613). Before this a
        // generator or `MultiValueDict.lists()` fell to the terminal `str()`
        // path and `{% for %}` walked the REPR character by character: silent
        // wrong output.
        if it.as_any().is(ob) {
            return Some(OpaqueFacts {
                truthy,
                len,
                iterable: true,
                unbounded: false,
            });
        }
        // An object that states a `__len__` has stated its own bound, and the
        // walk is skipped — which is what keeps this gate O(1) for a `set`
        // and a `dict_keys`. The exception is a bound too large to SPEND here
        // ([`stated_len_is_too_large_to_enumerate`], #2678 + #2695): it is
        // marked unbounded so `opaque_value` leaves `items` at `None` rather
        // than paying the billion reads that are the reported hang — for a
        // `range(10**9)` as much as for the liar whose `__getitem__` never
        // raises. Every sized collection UNDER the cap — `set`, `range(3)`,
        // a `deque`, a `list`, an evaluated `QuerySet` — is enumerated in
        // full exactly as before. Over the cap the ITEMS are read at the sink
        // instead, from the live handle, under the sink's own termination
        // rule ([`Encoded::live_walk_terminates`]); a `list` and a `QuerySet`
        // were EXEMPT from that between the #2695 review and #2717, and are
        // not any more (see [`spelling_is_the_items_list_repr`]).
        //
        // Without a `__len__` there is no bound at all, so walk to the cap: a
        // class whose `__iter__` returns `itertools.count()` is RE-iterable,
        // so the one-shot guard above does not catch it and enumerating it
        // would never return.
        //
        // Past the cap on either axis the object is UNBOUNDED for this
        // conversion (#2670, #2678). It is admitted with a live handle and no
        // items — `{{ v }}` is still `str(v)`, and `{{ v.0 }}` walks the real
        // object as Django's `current[int(bit)]` does, instead of indexing
        // the characters of `str(v)`.
        //
        // Counted, not collected: the items are not converted here.
        match len {
            Some(n) if stated_len_is_too_large_to_enumerate(n) => {
                unbounded = true;
            }
            Some(_) => {}
            None => {
                let mut seen = 0usize;
                for item in it {
                    if item.is_err() {
                        return None;
                    }
                    seen += 1;
                    if seen > OPAQUE_ITEM_CAP {
                        unbounded = true;
                        break;
                    }
                }
            }
        }
    }
    // An ordinary truthy, non-iterable object with attributes is CLAIMED
    // here — `{{ o }}` is `str(o)`, as Django renders it, and its attributes
    // are reached through the LIVE walk on the handle `opaque_value`
    // attaches. The decline that used to sit at this point (kill-switch off
    // AND truthy AND non-iterable AND a public `__dict__`, which handed such
    // an object to the `__dict__` bulk-dump arm on the escape hatch) was
    // deleted with the flag in ADR-027 Step 5 (#2628); rows I / T / K3 / K4
    // of the characterization net are what it turned on.
    Some(OpaqueFacts {
        truthy,
        len,
        iterable,
        unbounded,
    })
}

impl Encoded {
    /// Does a full walk of the live object END BY ITSELF? (#2695)
    ///
    /// The SINK half of the conversion's [`stated_len_is_too_large_to_enumerate`]
    /// — the two questions that #2678's second version tried to answer with
    /// one predicate, which is why it could only fix one of the two hangs.
    ///
    /// * The conversion asks "is the stated length too large to spend HERE",
    ///   and declines every sized sequence past [`OPAQUE_ITEM_CAP`].
    /// * This asks "can the walk end at all", and is what decides whether a
    ///   sink that genuinely needs every item may exceed the cap.
    ///
    /// TWO conditions, and both are load-bearing:
    ///
    /// * **`len(o)` succeeded at the conversion.** An object that never
    ///   stated a length has stated no bound: `itertools.count()`, a
    ///   generator, a class whose `__iter__` is endless. Those keep the cap
    ///   and RAISE past it — Django hangs on them, and an error is the
    ///   strictly better answer.
    /// * **The type has a real `__iter__`.** Without one, PyO3's iteration
    ///   is CPython's legacy sequence protocol — `o[0]`, `o[1]`, … until
    ///   `IndexError` — and nothing connects that walk to the stated
    ///   `__len__`. #2678's liar (a `__len__` of `10**9` over a
    ///   never-raising `__getitem__`) is exactly this shape and must keep
    ///   the cap; a `range` has a real iterator whose own exhaustion ends
    ///   the walk and must not.
    ///
    /// When both hold the walk is Django's own: `{% for %}` over
    /// `collections.deque(range(100_001))` renders all 100 001 items, and
    /// `{% for %}` over `range(10**9)` does not come back — which is measured Django
    /// behaviour for that same input, not a djust limitation (see the
    /// differential table in `python/tests/test_sized_sequence_conversion_2695_2693.py`).
    ///
    /// **The limit of the claim, stated rather than assumed.** Two conditions
    /// are what CPython lets us ask; they are not a proof. A class that
    /// states a `__len__` and whose `__iter__` never ends — a lying length —
    /// passes both and is walked without a bound. That shape does not come
    /// back today either: [`opaque_value`]'s enumeration has no cap of its
    /// own, so before #2695 it failed to return at the CONVERSION, for
    /// `{{ v|length }}` as much as for `{% for %}`. Moving the walk to the
    /// sink makes it strictly rarer (only a template that asks for every
    /// item pays it) and changes nothing else, so it is unfixed rather than
    /// newly broken — pinned as a measurement by
    /// `TestALyingLengthIsUnfixedRatherThanNewlyBroken`.
    fn live_walk_terminates(&self, ob: &Bound<'_, PyAny>) -> bool {
        self.len.is_some()
            && ob
                .get_type()
                .hasattr(pyo3::intern!(ob.py(), "__iter__"))
                .unwrap_or(false)
    }

    /// Consume a ONE-SHOT iterator carried by the live handle into its items
    /// (#2613) — Django's `list(values)` in `ForNode.render`, run at the
    /// `{% for %}` sink rather than at conversion so that a generator the
    /// template never loops over is never advanced.
    ///
    /// `None` when there is nothing to consume: no handle (eager path, or a
    /// wire round-trip), not iterable, or the items were already enumerated
    /// at conversion (a re-iterable object). Bounded by [`OPAQUE_ITEM_CAP`]
    /// UNLESS [`Encoded::live_walk_terminates`] — past it the render RAISES
    /// rather than truncating, because Django would hang on the same
    /// `itertools.count()` and a short list would be a silently wrong
    /// answer. A raising `__next__` propagates as Django propagates it.
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
            let capped = !self.live_walk_terminates(ob);
            let mut collected = Vec::new();
            for item in ob.try_iter()? {
                collected.push(item?.extract::<Value>()?);
                if capped && collected.len() > OPAQUE_ITEM_CAP {
                    return Err(self.unbounded_error());
                }
            }
            Ok(collected)
        }))
    }

    /// The `Value::List` the conversion DECLINED to build, materialised at
    /// the container sink instead of at binding time (#2717).
    ///
    /// `{{ v }}`, `|pprint` and `|json_script` are the three sinks that spell
    /// a value WHOLE. Every other sink over a declined carrier reads only
    /// what it needs — `|length` is `self.len`, `{{ v.0 }}` is
    /// [`Encoded::live_get_item`], `|slice` is
    /// [`Encoded::live_get_slice`], `{% for %}` / `|join` / `in` are
    /// [`Encoded::consume_live_items`] — so those three are the whole of what
    /// the #2695-review exemption was protecting, and this is what replaces
    /// it.
    ///
    /// **Only for an object whose spelling IS its items' list repr** —
    /// [`spelling_is_the_items_list_repr`], the same predicate that used to
    /// name the exemption, so the rule has one statement (#1646). A `range`,
    /// a `deque`, an `array`, a `bytes` and a sized user class each spell
    /// their OWN container (`str(range(3))` is `range(0, 3)`), and re-spelling
    /// one as a list is the #2704 defect in reverse; they answer `None` here
    /// and keep `display`.
    ///
    /// **Cheap when it does not apply.** The `len` gate is a plain integer
    /// compare and is FIRST: nothing but a sized sequence past
    /// [`OPAQUE_ITEM_CAP`] can be a declined one, so an ordinary carrier —
    /// a `datetime`, a `set`, a `complex`, a user object — never reaches the
    /// Python probe. That matters because `Display for Value` calls this on
    /// every `{{ carrier }}` in every render.
    ///
    /// **The items are the SAME ones the value stack would have held**,
    /// because this reuses [`Encoded::consume_live_items`] — the walk
    /// `{% for %}` already uses — rather than transcribing a second
    /// enumeration. So the spelling either side of the cap is equal by
    /// construction, which is exactly what
    /// `TestARealQuerySetIsSpelledTheSameOnBothSidesOfTheCap` asserts.
    ///
    /// What it buys, measured on an UNEVALUATED `User.objects.all()` over a
    /// real 150 000-row table: `{{ v|length }}` falls from 4 437 MB / 13.4 s
    /// to 565 MB / 10.1 s, and `{{ v.0 }}` from 2 982 MB / 13.3 s to
    /// 148 MB / 0.7 s, because neither cell reaches this method. The three
    /// cells that DO reach it pay what they always paid — they emit 60-100 MB
    /// of HTML, so the enumeration is not the expensive half of them.
    pub fn declined_list_spelling(&self) -> Option<PyResult<Value>> {
        if !self.len.is_some_and(|n| n > OPAQUE_ITEM_CAP) {
            return None;
        }
        let handle = self.live.as_ref()?;
        if !Python::attach(|py| spelling_is_the_items_list_repr(handle.bind(py))) {
            return None;
        }
        Some(self.consume_live_items()?.map(Value::List))
    }

    /// Python's `needle in o` over the live handle, consuming only as far as
    /// the FIRST match (#2674).
    ///
    /// `in` short-circuits in Python, and over a one-shot iterator that is
    /// observable: `{% if 1 in g %}` then `{{ g|join:"," }}` renders `T|2`
    /// in Django for `g = iter([1, 2])`, because the `in` stopped after the
    /// first element. [`Encoded::consume_live_items`] would spend the whole
    /// iterator and render `T|`. Same guards, same cap, same handle — only
    /// the stopping rule differs, which is why it is a sibling of that
    /// method rather than a caller-side `take_while`.
    ///
    /// This is the walk Python falls back to when the type has no
    /// `__contains__`; [`Encoded::live_contains`] is the other half and must
    /// be tried FIRST, or `{% if 987654321 in range(10**9) %}` walks nine
    /// hundred million items to reach an answer `range.__contains__` has in
    /// constant time (#2695).
    ///
    /// `None` when there is nothing to consume, exactly as its sibling.
    pub fn consume_live_match(
        &self,
        mut matches: impl FnMut(&Value) -> bool,
    ) -> Option<PyResult<bool>> {
        if !self.iterable || self.items.is_some() {
            return None;
        }
        let handle = self.live.as_ref()?;
        Some(Python::attach(|py| {
            let ob = handle.bind(py);
            let capped = !self.live_walk_terminates(ob);
            let mut seen = 0usize;
            for item in ob.try_iter()? {
                if matches(&item?.extract::<Value>()?) {
                    return Ok(true);
                }
                seen += 1;
                if capped && seen > OPAQUE_ITEM_CAP {
                    return Err(self.unbounded_error());
                }
            }
            Ok(false)
        }))
    }

    /// Python's own `needle in o`, for a type that answers it WITHOUT
    /// iterating (#2695).
    ///
    /// `range(10**9).__contains__(987654321)` is arithmetic; `set` and
    /// `dict_keys` hash. Django evaluates `x in value` in Python and gets
    /// those answers for free, so walking the object here is not merely slow
    /// — for a stated billion it is the reported hang, on a cell Django
    /// renders instantly.
    ///
    /// Gated on the type actually defining `__contains__`, and that gate is
    /// what keeps this safe for a one-shot iterator: without the slot,
    /// CPython's `in` falls back to iterating and CONSUMES the caller's
    /// generator up to the match, which is precisely the behaviour
    /// [`Encoded::consume_live_match`] already reproduces with djust's own
    /// element comparison. So the two are exclusive rather than layered —
    /// this arm handles the objects Python answers without a walk, its
    /// sibling handles the ones it answers with one.
    ///
    /// `None` when there is nothing to ask: no handle, not iterable, items
    /// already enumerated, no `__contains__`, or a needle that has no
    /// faithful Python spelling.
    pub fn live_contains(&self, needle: &Value) -> Option<PyResult<bool>> {
        if !self.iterable || self.items.is_some() {
            return None;
        }
        let handle = self.live.as_ref()?;
        Python::attach(|py| {
            let ob = handle.bind(py);
            if !ob
                .get_type()
                .hasattr(pyo3::intern!(py, "__contains__"))
                .unwrap_or(false)
            {
                return None;
            }
            // A needle that cannot cross back exactly is left to the walking
            // sibling: `Value`'s `IntoPyObject` spells a carried object as a
            // string, and `"<map object …>" in o` is a different question.
            let py_needle = match needle {
                Value::None
                | Value::Bool(_)
                | Value::Integer(_)
                | Value::Float(_)
                | Value::String(_)
                | Value::SafeString(_) => needle.into_pyobject(py).ok()?,
                _ => return None,
            };
            Some(ob.contains(py_needle))
        })
    }

    /// Python's `o[index]` over the live handle — Django's `first` / `last`
    /// filters and its `Variable._resolve_lookup` integer step (#2695).
    ///
    /// `Ok(None)` is an `IndexError`, which is the ONE exception Django's
    /// `first` and `last` catch (`except IndexError: return ""`). Every
    /// other Python failure is the caller's to spell, and the caller has
    /// only [`crate::Value`]-level error variants — so a `TypeError` (`o` is
    /// not subscriptable) and a `KeyError` are told apart here and anything
    /// else is reported as the not-subscriptable case.
    ///
    /// For `range(10**9)` this is `{{ v|first }}` -> `0` and
    /// `{{ v|last }}` -> `999999999`, both in constant time, both Django's
    /// own answers.
    ///
    /// Answers from the OBJECT whether or not `items` were enumerated
    /// (#2704). It used to refuse a carrier holding `items`, as a stand-in
    /// for "not subscriptable" — true only while the sole item-holding
    /// carriers were a `set` / `frozenset` / `dict_keys`. #2704 moves every
    /// non-`list` sequence onto the carrier at any length, so `range(3)` and
    /// a `deque` hold items AND subscript, and the stand-in answered
    /// `TypeError: 'range' object is not subscriptable` where Django answers
    /// `0`. A `set` still refuses, now because CPython raises rather than
    /// because a field was read as a proxy. Whether a caller asks at all is
    /// `filters::carrier_answers_subscripts_from_the_live_object` — one
    /// statement, two sinks, rather than a copy of the rule here (#1646).
    pub fn live_get_item(&self, index: i64) -> Option<PyResult<Option<Value>>> {
        let handle = self.live.as_ref()?;
        Some(Python::attach(|py| match handle.bind(py).get_item(index) {
            Ok(found) => Ok(Some(found.extract::<Value>()?)),
            Err(e) if e.is_instance_of::<pyo3::exceptions::PyIndexError>(py) => Ok(None),
            Err(e) => Err(e),
        }))
    }

    /// Python's `o[start:stop:step]` over the live handle — Django's `slice`
    /// filter, which is a bare `value[slice(*bits)]` passthrough (#2695).
    ///
    /// `None` when there is no handle to slice and `Some(Err(..))` when
    /// Python raises — both of which the caller answers with Django's
    /// `except (ValueError, TypeError, KeyError): return value`, i.e. the
    /// input unchanged. That is what a `generator` gets, on both engines, and
    /// what a `deque` gets on both (`deque[0:3]` is a `TypeError` in CPython).
    ///
    /// Load-bearing because the conversion now declines to enumerate a sized
    /// sequence past [`OPAQUE_ITEM_CAP`]: without this arm
    /// `{{ v|slice:":3" }}` over `collections.deque(range(100_001))` — or over
    /// `range(10**9)` — returned the WHOLE carrier unchanged, where Django
    /// renders three items.
    ///
    /// Slices whether or not `items` were enumerated — see
    /// [`Encoded::live_get_item`] for why that stand-in had to go (#2704).
    /// The container the slice returns is the OBJECT's, which is the whole
    /// point: `range(3)[0:3]` is `range(0, 3)` and not `[0, 1, 2]`.
    pub fn live_get_slice(
        &self,
        start: Option<isize>,
        stop: Option<isize>,
        step: Option<isize>,
    ) -> Option<PyResult<Value>> {
        let handle = self.live.as_ref()?;
        Some(Python::attach(|py| {
            // Built through Python's own `slice(...)` rather than
            // `PySlice::new`, which cannot spell `None`. The difference is
            // observable for a negative step: `o[::-1]` starts at the END
            // while `o[0::-1]` is a single element, so substituting `0` for
            // an absent start would answer a different question.
            let slice = py
                .get_type::<pyo3::types::PySlice>()
                .call1((start, stop, step))?;
            handle.bind(py).get_item(slice)?.extract::<Value>()
        }))
    }

    /// The ONE spelling of "this object yielded more than the cap", shared by
    /// the two walking sinks so their messages cannot drift (#1646).
    fn unbounded_error(&self) -> PyErr {
        pyo3::exceptions::PyRuntimeError::new_err(format!(
            "'{}' object yielded more than {} items — \
             an unbounded iterable cannot be rendered",
            self.type_name, OPAQUE_ITEM_CAP
        ))
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
        // By type, as the impl's `str` arm is (#2555): a lone surrogate
        // fails `extract::<String>()` and would answer TRUE here while the
        // conversion answers `Value::String`.
        || ob.is_instance_of::<PyString>()
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
    // The last two arms above the fallback block: `bounded_sequence_items` is
    // every BOUNDED sequence (`bytes`, a `deque`, a `range`, a class with an
    // integer `__getitem__` AND a `__len__`) and collects REFERENCES,
    // converting nothing — the same gate the impl's list arm uses, so an
    // unbounded legacy sequence (#2572) is declined by both; `PyDict` is
    // every real mapping.
    if bounded_sequence_items(ob).is_some() {
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
/// * **An iterable longer than [`OPAQUE_ITEM_CAP`]** — an object with no
///   `__len__` whose iterator keeps yielding, or one whose stated `__len__`
///   exceeds the cap (#2678). `itertools.count()` is an iterator and is
///   handled by the arm above, but a class whose `__iter__` RETURNS one is
///   re-iterable and would hang here; a `__len__` of `10**7` over a
///   never-raising `__getitem__` would too. Never truncated — a truncated
///   collection is a silently wrong answer. Under ADR-027 it is carried with
///   a live handle and `items: None` (#2670): `{{ v }}` is `str(v)`,
///   `{{ v.0 }}` walks the real object, and the item sinks read through the
///   handle up to the cap. On the eager escape hatch it is declined.
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
/// a `tuple`, a `dict` and anything with an integer `__getitem__` AND a
/// `__len__` are extracted well before this ([`bounded_sequence_items`]),
/// which is why a `range` / `bytes` / `deque` never arrives here. A legacy
/// sequence with NO `__len__` does arrive (#2572), and is what the
/// [`OPAQUE_ITEM_CAP`] walk below is for.
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
        unbounded,
    } = opaque_gate(ob)?;
    // The items, converted — the half `opaque_gate` deliberately does NOT do.
    // `iter(o)` is asked again rather than carried across, because the gate
    // may have walked the first one to check the cap; a RE-iterable object
    // yields the same elements again and consumes nothing. A ONE-SHOT
    // iterator (`iter(o) is o`, admitted under ADR-027 — #2613) is left at
    // `None`: the `{% for %}` sink consumes it once through the live handle.
    // An UNBOUNDED object (#2670, #2678) is left at `None` for the same
    // reason: the sinks read it through the handle, up to the cap.
    let one_shot = ob.try_iter().is_ok_and(|it| it.as_any().is(ob));
    let items = if iterable && !one_shot && !unbounded {
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
    // Lossy, like every other `str()` crossing (#2555): a `__str__` that
    // yields a lone surrogate keeps its carrier rather than declining it.
    let display = py_string_lossy(&text).ok()?;
    // MEASURED, not `display.clone()` (#2472). For `set()`, `frozenset()` and
    // `complex(0)` the two spellings coincide, which is exactly why copying
    // `display` here would look correct on every builtin this function was
    // written for — and be wrong for the case it was widened to carry: a USER
    // class may define `__str__` and `__repr__` independently, so
    // `{{ p|pprint }}` over a `__bool__`-False instance renders whichever this
    // field holds. `repr()` is one call and answers it exactly.
    let repr = py_string_lossy(&ob.repr().ok()?).ok()?;
    // ADR-027's transient handle (#2539). `Bound::unbind` needs no GIL token
    // and makes no Python CALL — it is a refcount bump plus an `Arc`
    // allocation, so attaching one adds no Rust→Python crossing to the
    // per-render budget (#2532). Unconditional since Step 5 (#2628) deleted
    // the ADR-027 kill-switch flag that used to gate it.
    let live = Some(std::sync::Arc::new(ob.clone().unbind()));
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
        str_raised: false,
        // EMPTY: the handle is the authority for every attribute lookup under
        // ADR-027, so building an eager `__dict__` map as well would be two
        // mechanisms answering one question (#1646) — and the wrong one would
        // WIN, because `Context::get`'s step 2 reads `attrs` and never
        // auto-calls (Django resolves `{{ d.value }}` on a callable object by
        // CALLING `d` first, which no eager map can express). The eager map
        // was also the recursion that segfaulted: it converted every
        // attribute VALUE with no visited set, so an object whose `__dict__`
        // reached back to its own container killed the process (#2516 row H).
        // The `__dict__` builder (#2478) was deleted in ADR-027
        // Step 5 (#2628); `django_json_encoded` is now the one producer of a
        // non-empty `attrs`.
        attrs: IndexMap::new(),
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

/// The carrier for an object one of [`opaque_value`]'s probes REFUSED to
/// measure (ADR-027 Step 5, #2628).
///
/// `opaque_value` fails closed on a raising `__bool__`, `__str__`,
/// `__repr__`, `__next__` or `type(o).__name__`. Until Step 5 such an object
/// fell to the eager `__dict__` bulk dump, which — by never calling any of
/// those — still answered `{{ o.attr }}`, refused `{{ o.mutator }}`, and let
/// a raising `__getattr__` PROPAGATE from the sidecar walk exactly as Django's
/// `_resolve_lookup` does. Deleting that arm without this one turned a
/// `LiveComponent` whose `__str__` renders a template it does not have into a
/// render-time `ValueError` on `{{ c.mount }}`, and silenced a
/// `__getattr__`-raising object's `RuntimeError` on `{{ o.0 }}` into a
/// character of its repr (`Context::string_index`).
///
/// So the object keeps its LIVE HANDLE, and every measurement is best-effort:
/// the probe that raised is answered by the next spelling down (`str` →
/// `repr` → `<TypeName object>`; `bool` → truthy; no length, no items). What
/// Django would raise on — `{{ o }}` over a raising `__str__`, `{% if o %}`
/// over a raising `__bool__` — renders the fallback instead of raising, which
/// is the one cell this carrier does not make Django-exact; it is no worse
/// than the dict dump it replaces on that cell and strictly better on every
/// lookup that walks the handle.
fn handle_only_encoded(ob: &Bound<'_, PyAny>) -> Encoded {
    let type_name = ob
        .get_type()
        .getattr("__name__")
        .ok()
        .and_then(|n| n.extract::<String>().ok())
        .unwrap_or_else(|| "object".to_string());
    let text = ob.str().ok();
    let str_raised = text.is_none();
    let display_safe = text
        .as_ref()
        .is_some_and(|t| python_string_is_safe(t.as_any()));
    let repr = ob
        .repr()
        .ok()
        .and_then(|r| py_string_lossy(&r).ok())
        .unwrap_or_else(|| format!("<{type_name} object>"));
    let display = text
        .and_then(|t| py_string_lossy(&t).ok())
        .unwrap_or_else(|| repr.clone());
    Encoded {
        type_name,
        json: display.clone(),
        display,
        truthy: ob.is_truthy().unwrap_or(true),
        len: None,
        iterable: false,
        repr,
        cmp_key: None,
        live: Some(std::sync::Arc::new(ob.clone().unbind())),
        display_safe,
        str_raised,
        attrs: IndexMap::new(),
        items: None,
        eq_class: None,
    }
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
                // `str(tz)` since #2770; a pre-#2770 state entry carries the
                // zone's full `Encoded`, whose `Display` is the same string —
                // so both shapes restore (rolling deploy, #2770 (d)).
                let name = match zone {
                    Value::String(name) => name.clone(),
                    other => other.to_string(),
                };
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

/// A [`Value::Encoded`] crossing into a bridged Python tag handler AS AN
/// OBJECT rather than as its `str()` (#2731).
///
/// # Why this exists
///
/// A `Value` is inert data. An arbitrary Python object crosses the PyO3
/// boundary as a `Value::Encoded` — the facts measured from it — and the ONE
/// place a dotted segment is answered against those facts is
/// [`context::lookup_segment`], which reads [`Encoded::attrs`]. That is how
/// `{{ r.group }}` resolves.
///
/// A bridged Django tag (`{% regroup %}`, `{% url %}`, …) does NOT resolve its
/// operands through the renderer: it is handed a flat Python dict and Django's
/// own `Variable._resolve_lookup` walks it. `IntoPyObject for Value` turns a
/// non-temporal `Encoded` into `e.display` — a `str` — so `_resolve_lookup`
/// was walking a STRING, every segment missed, and the tag silently produced
/// its "resolved to nothing" answer: `{% regroup rows by group %}` over a list
/// of objects built ONE group with `grouper=None` and an empty `list`, and
/// `{% url 'v' rows.0.pk %}` raised `NoReverseMatch`. Neither raised on the
/// stateless path, where the raw-Python sidecar happens to carry the live list
/// and overwrite the flattened entry.
///
/// This type closes that by making the object cross as an object whose lookups
/// go through `lookup_segment` — the SAME step, so the tag bridge and the
/// renderer cannot drift (#1646). It is not a general-purpose export: it is
/// built only by [`value_into_handler_pyobject`], for the two cases that
/// function's doc comment names.
///
/// It answers `__str__` / `__repr__` / `__len__` / `__iter__` / `__bool__`
/// from the very facts [`opaque_value`] measures, which is why
/// `impl FromPyObject for Value` needs no unwrap arm for it: a wrapper that
/// comes back in a handler's binding re-measures to the same `Encoded`.
#[pyclass(name = "TemplateObject", module = "djust._rust")]
pub struct TemplateObject {
    value: Value,
}

impl TemplateObject {
    fn segment<'py>(&self, py: Python<'py>, part: &str) -> PyResult<Option<Bound<'py, PyAny>>> {
        match context::lookup_segment(&self.value, part) {
            Some(found) => Ok(Some(value_into_handler_pyobject(py, found.clone())?)),
            None => Ok(None),
        }
    }

    fn items(&self) -> Option<&Vec<Value>> {
        match &self.value {
            Value::Encoded(e) => e.items.as_ref(),
            _ => None,
        }
    }

    fn live_handle(&self) -> Option<&std::sync::Arc<Py<PyAny>>> {
        match &self.value {
            Value::Encoded(e) => e.live.as_ref(),
            _ => None,
        }
    }
}

#[pymethods]
impl TemplateObject {
    /// Django's `_resolve_lookup` step 1 (mapping access). A miss is a
    /// `KeyError`, which is what makes it fall through to `getattr`.
    fn __getitem__<'py>(
        &self,
        py: Python<'py>,
        key: &Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let part = key.str()?.extract::<String>()?;
        match self.segment(py, &part)? {
            Some(found) => Ok(found),
            None => Err(pyo3::exceptions::PyKeyError::new_err(part)),
        }
    }

    /// Django's `_resolve_lookup` step 2 (`getattr`). Reached only for names
    /// Python could not find on the type, so the dunders below are unaffected.
    fn __getattr__<'py>(&self, py: Python<'py>, name: &str) -> PyResult<Bound<'py, PyAny>> {
        match self.segment(py, name)? {
            Some(found) => Ok(found),
            None => Err(pyo3::exceptions::PyAttributeError::new_err(
                name.to_string(),
            )),
        }
    }

    /// `str(o)`, measured at the conversion — the whole point of not handing
    /// the handler a `dict` of the attributes instead.
    fn __str__(&self) -> String {
        self.value.to_string()
    }

    fn __repr__(&self) -> String {
        self.value.py_repr()
    }

    /// `bool(o)`, measured at the conversion — NOT derived from `__len__`,
    /// for the reason [`Encoded::truthy`] documents.
    fn __bool__(&self) -> bool {
        self.value.is_truthy()
    }

    /// `len(o)` where Python answered one, else the enumerated item count.
    /// A `TypeError` where Python raises.
    fn __len__(&self) -> PyResult<usize> {
        let carried = match &self.value {
            Value::Encoded(e) => e.len,
            _ => None,
        };
        carried
            .or_else(|| self.items().map(|items| items.len()))
            .ok_or_else(|| {
                pyo3::exceptions::PyTypeError::new_err(format!(
                    "object of type '{}' has no len()",
                    self.value_type_name()
                ))
            })
    }

    /// The object's items.
    ///
    /// Normally [`Encoded::items`], enumerated at the conversion — already
    /// `Value`s, so a model among them is the floor-filtered dict
    /// `normalize_django_value` made.
    ///
    /// `items` is `None` for the two carriers the conversion declined to
    /// enumerate: a ONE-SHOT iterator (a generator — enumerating it would
    /// consume it) and an object whose stated length is past
    /// [`OPAQUE_ITEM_CAP`]. Those fall back to asking the live object, which
    /// is what Django's own handler would have done (`RegroupNode` runs
    /// `groupby` over whatever the operand resolved to), and every element
    /// goes through the SAME conversion the enumerated branch uses — so the
    /// floor reaches a model at any depth, on both sides of the cap.
    ///
    /// Raising here reached a render entry point as a 500 (PR #2734 review):
    /// `{% regroup %}` over a list of 100 001 rows raised
    /// `TypeError: 'list' object is not iterable` out of `render()` where it
    /// used to render (wrongly, but render). The fallback is what makes it
    /// render CORRECTLY instead of either.
    ///
    /// A one-shot iterator is consumed by this, exactly as Django consumes it.
    /// An iterator with no end does not terminate — also exactly as Django's
    /// `groupby`/`list` does not.
    fn __iter__<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let py_list = PyList::empty(py);
        if let Some(items) = self.items() {
            for item in items {
                py_list.append(value_into_handler_pyobject(py, item.clone())?)?;
            }
            return py_list.as_any().try_iter().map(|it| it.into_any());
        }
        let Some(handle) = self.live_handle() else {
            return Err(pyo3::exceptions::PyTypeError::new_err(format!(
                "'{}' object is not iterable",
                self.value_type_name()
            )));
        };
        // `try_iter` raises Python's own "not iterable" TypeError for an
        // object that is not, which is the message to keep.
        for item in handle.bind(py).try_iter()? {
            // Through `extract::<Value>()` and back — the SAME conversion the
            // `items` branch above hands `value_into_handler_pyobject`, since
            // `Encoded::items` are exactly `item.extract::<Value>()` measured
            // at the conversion. The two branches therefore differ only in
            // WHEN the `Value` was measured, never in what the element becomes
            // (#1646).
            //
            // That convergence is the floor (PR #2734 review round 3). The
            // first version applied `protect_sidecar_strict` — the LEAF floor —
            // once per element, which protects a model IN the element position
            // and cannot see one held INSIDE it. So the floor was
            // DISCONTINUOUS at [`OPAQUE_ITEM_CAP`]:
            //
            // ```text
            // [[user]] * 3        {% regroup rows by 0.password %}  ->  ""
            // [[user]] * 100_001  {% regroup rows by 0.password %}  ->  the hash
            // ```
            //
            // The `Value` conversion descends: its `List` / `Object` arms
            // recurse per element, and a `Model` at any depth routes through
            // `normalize_django_value`'s denylist. A plain object still ends at
            // the same leaf floor, because `value_into_handler_pyobject`'s live
            // arm applies it.
            py_list.append(value_into_handler_pyobject(py, item?.extract::<Value>()?)?)?;
        }
        py_list.as_any().try_iter().map(|it| it.into_any())
    }

    /// Two wrappers are equal when the objects behind them measured the same
    /// (`values_structurally_equal`, which for an `Encoded` is
    /// [`Encoded::eq`]'s own equality contract). Anything else — a `str`, a
    /// `dict`, the live object — is NOT equal: the wrapper stands for a Python
    /// object and cannot claim equality with its own `str()`.
    fn __eq__(&self, other: &Bound<'_, PyAny>) -> bool {
        match other.extract::<PyRef<'_, TemplateObject>>() {
            Ok(other) => values_structurally_equal(&self.value, &other.value),
            Err(_) => false,
        }
    }

    /// Consistent with [`TemplateObject::__eq__`], and REQUIRED because
    /// declaring `__eq__` makes PyO3 set `__hash__ = None` (PR #2734 review).
    /// A `str` used to arrive here, so a `{% load %}`ed handler doing
    /// `set(values)` or using a value as a dict key would have started raising
    /// `TypeError: unhashable type`.
    ///
    /// `Encoded`'s own `PartialEq` compares `type_name` / `display` / `repr`
    /// among other fields, so two equal wrappers agree on all three and this
    /// cannot hash them apart. It may hash UNEQUAL values together, which is
    /// what a hash is allowed to do.
    fn __hash__(&self) -> u64 {
        use std::hash::{Hash, Hasher};
        let mut hasher = std::collections::hash_map::DefaultHasher::new();
        match &self.value {
            Value::Encoded(e) => {
                e.type_name.hash(&mut hasher);
                e.display.hash(&mut hasher);
                e.repr.hash(&mut hasher);
            }
            other => other.to_string().hash(&mut hasher),
        }
        hasher.finish()
    }

    /// Pickle / `copy.deepcopy` degrade the wrapper to the `str` that used to
    /// arrive here (PR #2734 review).
    ///
    /// A pyclass with no `__reduce__` is unpicklable, and `copy.deepcopy`
    /// falls through to `__reduce_ex__`, so a handler that deep-copied its
    /// context — or a `simple_tag` that pickled a value into a cache — would
    /// have started raising. `str(o)` is exactly what this position held
    /// before the fix, so the degraded form is the OLD behaviour rather than a
    /// new one; a copy is data, and the live object it stood for cannot be
    /// carried into a pickle anyway.
    fn __reduce__(&self) -> (Py<PyAny>, (String,)) {
        Python::attach(|py| {
            (
                py.get_type::<PyString>().into_any().unbind(),
                (self.value.to_string(),),
            )
        })
    }
}

impl TemplateObject {
    /// CPython's `tp_name` for the object this stands for — only ever an
    /// `Encoded`, but spelled defensively so the error text never lies.
    fn value_type_name(&self) -> String {
        match &self.value {
            Value::Encoded(e) => e.type_name.clone(),
            _ => "object".to_string(),
        }
    }
}

/// [`IntoPyObject`] for the TAG-BRIDGE sink, where an object must stay an
/// object (#2731).
///
/// Identical to `Value::into_pyobject` except that a non-temporal
/// [`Value::Encoded`] becomes a [`TemplateObject`] rather than its `str()`,
/// and the container variants recurse through here so a nested object is
/// reached too — `{% regroup rows by group %}` hands the handler a LIST of
/// objects, not an object.
///
/// A temporal `Encoded` still becomes a real `datetime` / `date` / `time` /
/// `timedelta`: that conversion is strictly better than any wrapper, and it is
/// what `{% now %}`-adjacent handlers and custom filters already receive.
///
/// # The two arms, and why both
///
/// An `Encoded` is handled in one of two ways, and they are disjoint by
/// construction — `encoded.live.is_some()` decides:
///
/// * **A live handle (ADR-027, #2539).** The object crosses AS ITSELF, floor-
///   protected through [`context::protect_sidecar_strict`] — the SAME handle
///   and the SAME floor [`Context::walk_live`] resolves `{{ r.group }}`
///   through. Django's own `_resolve_lookup` then walks the real object, so
///   the tag bridge cannot answer a dotted lookup differently from the
///   renderer (#1646). This is the arm the `{% regroup %}` / `{% url %}`
///   defect needed: `attrs` is EMPTY for an ordinary user object, so nothing
///   short of the live object can answer `by group`.
/// * **No live handle** — the ADR-027 kill-switch is off, or the value came
///   off the wire. The object crosses as a [`TemplateObject`], which answers
///   from [`Encoded::attrs`] / [`Encoded::items`] through
///   `context::lookup_segment`. That is strictly more than the `str()` this
///   used to send (a `set` becomes iterable; `{{ p.year }}` on a carried
///   temporal-family value resolves) and it exposes nothing a `Value` was not
///   already carrying.
///
/// A floor that fails to enforce (`protect_sidecar_strict` answering `None`)
/// falls to the wrapper rather than passing the raw object: the wrapper can
/// only ever expose `Encoded`'s measured facts, so the failure degrades to
/// less information, never to more.
///
/// # Which arm: the floor decides, by a PROPERTY rather than a type list
///
/// `_protect_sidecar_value` is the LEAF floor — it proxies a `Model`, a
/// `Manager` and a `QuerySet`, and returns anything else unchanged. That is
/// correct for [`Context::walk_live`], which re-protects after every segment.
/// This sink has no next segment: it hands the object straight to Python code,
/// which is precisely the second sink
/// `djust.serialization.build_render_sidecar`'s docstring names. So handing
/// over an object the floor could not see INSIDE would carry raw models past
/// it, and the tree pass that would fix that is an O(n) walk per bridged TAG
/// CALL — a `{% url %}` inside a `{% for %}` pays it per iteration.
///
/// The live arm is therefore taken exactly when the object is **not
/// iterable** — there is no "inside" for the leaf floor to miss. Every
/// ordinary user object is here, which is the case this fix is for. An
/// iterable takes the wrapper instead, and its `items` are `Value`s, so a
/// model among them is already the floor-filtered dict
/// `normalize_django_value` made.
///
/// **One property, not two.** The first version also took the live arm when
/// `protect_sidecar_strict` had TRANSFORMED the object, to keep a `QuerySet`
/// on its `_SidecarQuerySetProxy`. Gate-off measured that term as changing no
/// outcome, and the reason is structural rather than a coverage gap: the floor
/// transforms exactly `Model`, `Manager` and `QuerySet`
/// (`djust.serialization._protect_sidecar_value`); a `Model` and a `Manager`
/// are not iterable, so the first term already admits them, and a `QuerySet`
/// never reaches this arm at all — it converts through `__djust_serialize__`
/// to a `Value::List` of filtered dicts (measured: a handler receives a
/// `list`). A term that cannot decide is a decoration (#2233).
///
/// **This was an `isinstance` allowlist of five builtins, and it shipped five
/// leaks** (PR #2734 review): `collections.deque`, `dict_keys`, `dict_values`,
/// a generator and any custom `__len__`/`__iter__` class are not `list` /
/// `tuple` / `dict` / `set` / `frozenset`, so each took the live arm and
/// `{% regroup rows by password %}` rendered the hash. An allowlist of
/// container types is always one shape short; the property is not.
pub fn value_into_handler_pyobject(py: Python<'_>, value: Value) -> PyResult<Bound<'_, PyAny>> {
    match value {
        Value::Encoded(ref encoded) => {
            if let Some(temporal) = encoded.temporal_object(py)? {
                return Ok(temporal);
            }
            if let Some(handle) = encoded.live.as_ref() {
                if !encoded.iterable {
                    // `protect_sidecar_strict` answering `None` is the floor
                    // failing to enforce: fall to the wrapper, which can only
                    // ever expose `Encoded`'s measured facts.
                    if let Some(protected) =
                        context::protect_sidecar_strict(py, handle.bind(py).clone())
                    {
                        return Ok(protected);
                    }
                }
            }
            Ok(Bound::new(py, TemplateObject { value })?.into_any())
        }
        Value::List(items) => {
            let py_list = PyList::empty(py);
            for item in items {
                py_list.append(value_into_handler_pyobject(py, item)?)?;
            }
            Ok(py_list.into_any())
        }
        // A view crosses as a LIST for the same reason `into_pyobject` sends
        // one: it cannot be rebuilt without its dict, and every consumer here
        // wants something iterable.
        Value::DictView { items, .. } => value_into_handler_pyobject(py, Value::List(items)),
        Value::Tuple(items) => {
            let items: Vec<_> = items
                .into_iter()
                .map(|item| value_into_handler_pyobject(py, item))
                .collect::<PyResult<Vec<_>>>()?;
            Ok(pyo3::types::PyTuple::new(py, items)?.into_any())
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
                .map(|item| value_into_handler_pyobject(py, item))
                .collect::<PyResult<Vec<_>>>()?;
            cls.call(pyo3::types::PyTuple::new(py, args)?, None)
        }
        Value::Object(map) => {
            let py_dict = PyDict::new(py);
            for (key, item) in map {
                py_dict.set_item(
                    Value::from(key).into_pyobject(py)?,
                    value_into_handler_pyobject(py, item)?,
                )?;
            }
            Ok(py_dict.into_any())
        }
        other => other.into_pyobject(py),
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
