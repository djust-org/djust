//! Django-compatible template filters

use chrono::{DateTime, Datelike, Timelike, Utc};
use djust_core::{Context, DjangoRustError, Result, Value};
use once_cell::sync::Lazy;
use regex::Regex;

use crate::filter_registry;

/// [`filter_int_arg`] from inside an `apply_builtin_filter` match arm.
///
/// The arm's type is `Result<Value>` but the FUNCTION's is
/// `Option<Result<Value>>`, so `?` does not compile here. This is the one
/// place that hand-off is written down.
/// [`pad_width`] from inside an `apply_builtin_filter` match arm, for the same
/// reason [`int_arg!`] exists: `?` targets the function's `Option`, not the
/// arm's `Result`.
macro_rules! pad_width {
    ($name:expr, $parsed:expr) => {
        match pad_width($name, $parsed) {
            Ok(width) => width,
            Err(e) => return Some(Err(e)),
        }
    };
}

macro_rules! int_arg {
    ($name:expr, $arg:expr, $quoted:expr, $type_error:expr, $missing:expr, $bad:expr) => {
        match filter_int_arg($name, $arg, $quoted, $type_error, $missing, $bad) {
            Ok(parsed) => parsed,
            Err(e) => return Some(Err(e)),
        }
    };
}

/// The safety of the value going INTO a filter, at Django's TWO granularities.
///
/// Django has both, and conflating them is the bug behind two separate
/// findings (#2281, #2283):
///
/// * `container` — the value itself is `SafeData` (`mark_safe()` in the view,
///   `|safe`, or an earlier safe-output filter). This is what
///   `conditional_escape` tests with `hasattr(value, "__html__")`, and it is
///   what `escape` must consult so it does not double-escape.
/// * `items` — the value is a SEQUENCE whose ELEMENTS are `SafeData` while the
///   sequence itself is not. This is exactly what `safeseq` and `escapeseq`
///   produce: `[mark_safe(o) for o in value]` marks the items, never the list.
///   `join` and `unordered_list` `conditional_escape` per ITEM, so they are the
///   two filters that can observe it.
///
/// Nothing here marks anything safe on its own — every field is *reported* by
/// the renderer, which is the only layer that knows a value's provenance. A
/// caller that cannot know passes [`InputSafety::default()`] (all `false`),
/// which is the escaping, conservative direction.
/// What the filter ARGUMENT's resolved Python TYPE says.
///
/// The dispatch table takes `Option<&str>`, so every argument arrives as text
/// and two Python objects that spell the same are indistinguishable there. Each
/// field is one bit that a filter's Django source branches on and that the text
/// cannot answer, computed ONCE at the resolution site in
/// [`apply_filter_full_safe`] — which is the last place the `Value` exists —
/// rather than pushed through 57 arms as a whole `Value`.
///
/// It is a struct rather than N parameters because that is what it already was
/// becoming: #2366 added the first bit and #2401 the second, and a third would
/// have taken `apply_builtin_filter` past clippy's argument limit.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub(crate) struct ArgType {
    /// `int(arg)` would be a **TypeError** rather than a ValueError (#2366).
    /// See [`int_arg_is_type_error`] for how the line is drawn and for the half
    /// the PyO3 extraction boundary has already erased.
    pub int_is_type_error: bool,
    /// The argument is Python `None` (#2401).
    ///
    /// `yesno`'s first statement is `if arg is None: arg = gettext(…)`, an
    /// IDENTITY test — and `str(None)` is `"None"`, so by the time the dispatch
    /// table sees the argument a bare `None` literal, a context variable bound
    /// to `None`, and the string `"None"` are the same three characters while
    /// Django answers differently for the third. Measured:
    /// `{{ None|yesno:None }}` is `maybe` (the default triple) and
    /// `{{ None|yesno:"None" }}` is `None` (one part, so the value comes back).
    ///
    /// A SPELLING fallback cannot express that, which is the same conclusion
    /// [`int_arg_is_type_error`]'s own "why there is no spelling fallback"
    /// section reaches from the other side.
    pub is_none: bool,
}

/// Is the resolved filter argument Python `None`? (#2401)
///
/// `None` at the resolution site means the argument was a QUOTED literal, which
/// is a `str` and never Python `None` — the same reading
/// [`int_arg_is_type_error`]'s `None` arm takes.
pub(crate) fn arg_is_python_none(resolved: Option<&Value>) -> bool {
    matches!(resolved, Some(Value::None))
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct InputSafety {
    /// The value itself is `SafeData`.
    pub container: bool,
    /// The value is a sequence whose ITEMS are `SafeData`.
    pub items: bool,
}

/// Iterate a template value the way Python's `for x in value` does — the ONE
/// place that decision lives (#2283).
///
/// Five filters (`join`, `safeseq`, `escapeseq`, `unordered_list`, `random`)
/// each grew their own `Value::List | Value::Tuple` match and fell through to
/// the input for everything else, so every one of them returned a shape Django
/// never produces for a string. They are the same question asked five times,
/// which is why the answer is a function rather than five correct copies
/// (#1646).
///
/// * `String` — Python iterates a `str` as its CHARACTERS. This is the whole
///   of #2283.
/// * `List` / `Tuple` — the elements.
/// * `Object` — a Python `dict` iterates its KEYS.
/// * `Missing` — an absent variable. Django substitutes `string_if_invalid`
///   (`""`) BEFORE the filter runs, so what Django's filter iterates is the
///   empty string: `{{ absent|safeseq }}` is `[]`, not a `TypeError`.
/// * anything else — `None`, i.e. Python would raise `TypeError`. Callers
///   fail SOFT (djust does not 500 a page over a filter) but must ESCAPE what
///   they hand back if they hold an unconditional safe-output grant (#2274).
///
/// **Every `Value` variant that can carry markup is on the `Some` side**, which
/// is what makes #2285's escape on the `None` side a no-op today — see
/// `every_non_iterable_variant_is_markup_free` for the pin.
/// `len(value)` with Python's semantics, or `None` where Python RAISES
/// `TypeError` — the length half of [`iter_values`] (#2387).
///
/// Two call sites need this and they disagree about the FALLBACK, which is why
/// it returns an `Option` rather than a `usize`:
///
/// * `{{ p|length }}` — Django's `defaultfilters.length` is `len(value)` under
///   `except TypeError: return 0`;
/// * `{% for a, b in p %}` — `ForNode.render` is `len(item)` under
///   `except TypeError: len_item = 1`, and then RAISES if that does not equal
///   the loop-variable count.
///
/// Collapsing the two into one "length rule with a fallback baked in" would
/// have made one of them wrong; splitting the fallback out keeps the part they
/// genuinely share in one place (#1646).
///
/// `len()` of a Python `str` counts CODE POINTS; `str::len()` counts BYTES
/// (#2279). `é` is 2 bytes and `中` is 3, so every non-ASCII string measured
/// long — `{{ "中<b"|length }}` gave 5 where Django gives 3. Code points, NOT
/// graphemes: Python's `len("👍🏽")` is 2 (the emoji plus its skin-tone
/// modifier) and `len("👨\u{200d}👩\u{200d}👧")` is 5. `char` IS a Unicode
/// scalar value, so `chars().count()` is Python's answer and a
/// grapheme-cluster count would be a different, wronger one.
pub fn python_len(value: &Value) -> Option<usize> {
    match value {
        Value::String(s) => Some(s.chars().count()),
        Value::List(l) | Value::Tuple(l) => Some(l.len()),
        // `len(d.keys())` is the entry count, not the repr's length.
        Value::DictView { items, .. } => Some(items.len()),
        // A `dict` answers `len(dict)`; a serialized model RAISES, because
        // `len(model)` is a `TypeError` (#2294). Both are `Value::Object`, so
        // the arm has to tell them apart, and the marker is the shared
        // `object_str()` predicate — the same one `{{ obj }}` uses to decide
        // whether a map renders as its `__str__` or as a dict repr. Reusing it
        // is what keeps `{{ p }}` and `{{ p|length }}` from disagreeing about
        // what `p` IS (#1646).
        Value::Object(o) => {
            if value.object_str().is_some() {
                None
            } else {
                Some(o.len())
            }
        }
        // `Missing` included: it is an ABSENT key, and `len()` of the thing
        // that was not there is not a number. `|length` still answers 0 via its
        // own fallback, as it did when this arm was `_ => 0`.
        _ => None,
    }
}

pub fn iter_values(value: &Value) -> Option<Vec<Value>> {
    match value {
        Value::String(s) => Some(s.chars().map(|c| Value::String(c.to_string())).collect()),
        Value::List(items) | Value::Tuple(items) => Some(items.clone()),
        // A dict iterates its KEYS, each as the value it actually is — an
        // `Integer` key must render `0`, not `"0"` (#2339).
        Value::Object(map) => Some(djust_core::object_key::dict_iteration_values(map)),
        // A view is iterable in Python, so every filter that iterates gets it
        // here: `|join`, `|unordered_list`, `|safeseq`, `|escapeseq`, and the
        // truthiness probe `join` uses. The filters where Python RAISES
        // instead (`|random`, `|json_script`) guard at their own arm rather
        // than being carved out of this sink — a view IS a sequence, and it is
        // subscripting that it refuses (#2340).
        Value::DictView { items, .. } => Some(items.clone()),
        Value::Missing => Some(Vec::new()),
        _ => None,
    }
}

/// Rebuild a sequence in the SHAPE it arrived in — the counterpart of
/// [`iter_values`], and the one place "does this filter collapse a tuple?" is
/// answered (#2321).
///
/// Python and Django both preserve the container. `("a", "b", "c")[:2]` is a
/// TUPLE, and Django's `slice` filter is a bare `value[bits]` passthrough, so
/// `{{ p|slice:":2" }}` renders `('a', 'b')` where djust rendered
/// `['a', 'b']`. The difference is only ever visible when the sliced value is
/// rendered DIRECTLY — every consumer that iterates (`join`, `unordered_list`,
/// `{% for %}`) sees the same elements either way — which is exactly why the
/// sequence-filter suites, which compose `slice` with `join`, agreed for as
/// long as they did.
///
/// **Not every sequence filter should call this.** Four others build a
/// `Value::List` unconditionally and are RIGHT to:
///
/// * `dictsort` / `dictsortreversed` — Django is `sorted(...)`, and
///   `sorted(tuple)` is a list in Python.
/// * `make_list` — Django is `@stringfilter` + `list(value)`, so the tuple is
///   already `str(value)` by the time the list is built.
/// * `safeseq` / `escapeseq` — Django's bodies are list COMPREHENSIONS.
///
/// The decision is per-filter and belongs to Django's implementation, not to a
/// blanket rule; `TestEveryRebuildSiteIsAccountedFor` in
/// `python/tests/test_sequence_shape_preservation_2317_2321.py` pins the split
/// mechanically so a new sequence filter has to state which column it is in.
///
/// Non-sequence inputs fall back to a list. No caller can reach that arm today
/// — `slice`'s `String` branch returns earlier and every other variant returns
/// the input unchanged — so it is a total-function default rather than a
/// behaviour: making it `value.clone()` would silently discard `items`.
fn rebuild_like(input: &Value, items: Vec<Value>) -> Value {
    match input {
        Value::Tuple(_) => Value::Tuple(items),
        _ => Value::List(items),
    }
}

/// Django's `django.utils.html.conditional_escape`: escape unless already safe.
fn conditional_escape(value: &Value, already_safe: bool) -> String {
    if already_safe {
        value.to_string()
    } else {
        html_escape(&value.to_string())
    }
}

/// Django's `is_safe=True` arm, applied to a sequence a filter just BUILT.
///
/// `FilterExpression.resolve` runs `mark_safe(new_obj)` when the filter is
/// `is_safe=True` and the INPUT was `SafeData` — and `mark_safe` of a list is
/// `SafeString(str(list))`, a STRING of the repr. So `safeseq` / `escapeseq`
/// stop returning a sequence at all once their input was safe, which is why
/// `{{ p|escape|safeseq|slice:":3" }}` is three CHARACTERS of the repr in
/// Django and not a three-element list. Modelling it here is what keeps the
/// item-level grant from surviving a collapse Django already performed
/// (`renderer::filter_output_items_are_safe` refuses the grant on the same
/// condition).
fn collapse_if_input_safe(built: Value, input_was_safe: bool) -> Value {
    if input_was_safe {
        Value::String(built.to_string())
    } else {
        built
    }
}

/// Per-CALL safety for a built-in, for the filters a NAME-based list cannot
/// answer for (#2281).
///
/// [`renderer::SAFE_OUTPUT_FILTERS`] says "this NAME always marks its own
/// output safe" and [`renderer::IS_SAFE_FILTERS`] says "this NAME preserves the
/// safety it was given". Four of Django's built-ins fit neither, because their
/// answer depends on which BRANCH of the filter body ran:
///
/// * `join` — `mark_safe(data)` on success, `return value` (untouched, unsafe)
///   on the `TypeError` that a non-iterable raises.
/// * `cut` — `if safe and arg != ";": return mark_safe(value)`. The `";"`
///   carve-out is Django's, because cutting a semicolon can split `&lt;` into
///   live `&lt`.
/// * `default` / `default_if_none` — `return value or arg` hands back the INPUT
///   OBJECT when it is truthy, `SafeData` and all.
/// * `add` — the concatenation branch is `SafeString.__add__`, which returns a
///   `SafeString` only when the right-hand side is also `SafeData`. A template
///   LITERAL is (`Variable.literal` is `mark_safe`d); a context-resolved
///   identifier is not, which is what `arg_was_quoted` distinguishes.
///
/// This rides the existing `produced_safe` channel — the one a custom filter's
/// runtime `mark_safe()` uses (#1660) — so no third safety list is created.
/// Every arm requires `input_safety.container` except `join`, which mints its
/// own safety by escaping every item it emits.
fn builtin_produced_safe(
    filter_name: &str,
    value: &Value,
    arg: Option<&str>,
    arg_was_quoted: bool,
    arg_type: ArgType,
    result: &Value,
    input_safety: InputSafety,
) -> bool {
    match filter_name {
        // The GRANT is load-bearing: `join` escapes every item it emits, and
        // without it the render escapes the joined text a second time.
        //
        // The CONDITION — `is_some()` rather than a bare `true` — is Django's
        // and is currently UNOBSERVABLE, which is worth stating rather than
        // pretending a test covers it. `false` here can only apply to a value
        // `iter_values` refused, and after #2283 that is a number, a bool,
        // `None` or a `Decimal`/`BigInt` digit string — none of which contain a
        // character escaping would change, so granting them safety is a no-op.
        // A gate-off mutating this arm to `true` correctly reddens nothing.
        // It is kept for the same reason #2285's fall-through escape is: the
        // day a non-iterable `Value` variant can carry markup, this is the
        // line that already says the right thing.
        "join" => iter_values(value).is_some(),
        "cut" => input_safety.container && arg != Some(";"),
        // Two branches with two different provenances (#2389).
        //
        // Truthy input => Django returned the INPUT OBJECT itself, so the
        // grant is the input's.
        //
        // Falsy input => Django returned the ARGUMENT, and a QUOTED argument
        // is `SafeData`. `FilterExpression.resolve` is explicit:
        //
        //     for lookup, arg in args:
        //         if not lookup:
        //             arg_vals.append(mark_safe(arg))
        //         else:
        //             arg_vals.append(arg.resolve(context))
        //
        // — a CONSTANT argument is `mark_safe`d, a resolved one is a plain
        // `str`. So `{{ ""|default:"<b>" }}` emits LIVE `<b>` and
        // `{{ ""|default:q }}` with `q = "<b>"` escapes it. djust escaped
        // both, which is the OVER-escaping direction: a lost capability,
        // never a leak, which is why #2389 was filed separately from #2376
        // rather than folded into it.
        //
        // `arg_was_quoted` is exactly Django's `not lookup`: the renderer
        // computes it with `is_quoted_arg` on the ORIGINAL token, and the
        // variable-argument channel is measured clean on both engines (no
        // built-in emits a resolved argument live, on either side).
        //
        // The third arm of `is_safe` — `{{ ""|default:"<b>"|upper }}` — comes
        // along for free: this grant becomes the next filter's
        // `input_safety.container`, which is the same channel a `mark_safe`d
        // context value rides.
        "default" => {
            if value.is_truthy() {
                input_safety.container
            } else {
                arg_was_quoted
            }
        }
        "default_if_none" => {
            if matches!(value, Value::None) {
                arg_was_quoted
            } else {
                input_safety.container
            }
        }
        "add" => input_safety.container && arg_was_quoted && matches!(result, Value::String(_)),
        // The three EXTRACTORS, and the THIRD consumer of the item grant
        // (#2299). Django's bodies are `value[0]`, `value[-1]` and
        // `random.choice(value)` — each hands back the ELEMENT OBJECT, so when
        // the elements are `SafeData` the item grant becomes the RESULT's own
        // container safety and `render_value_in_context`'s
        // `conditional_escape` honours it. That is structurally different from
        // `join` / `unordered_list`, which consume the SAME grant per element
        // inside their own body and emit a joined string — which is why
        // #2287's seed reached those two and left these three escaping.
        //
        // `container` is deliberately NOT a term here. Django's `first` is
        // registered `is_safe=False`, and its `last` / `random` get the
        // `is_safe=True` arm through `renderer::IS_SAFE_FILTERS` already; a
        // `container` term would additionally mark `{{ p|safe|first }}` safe,
        // where Django's `SafeString[0]` is a plain `str` and the render
        // escapes it. That is the permissive direction, so it stays out.
        //
        // No shape narrowing, and that is a finding rather than an omission:
        // EVERY producer of `items` already guarantees that every element is
        // safe, so whichever one is extracted is safe.
        //
        //   * `Context::items_are_safe` — `List`/`Tuple` only, non-empty,
        //     every index in `safe_keys`, every element a `String`.
        //   * `renderer::filter_output_items_are_safe` — `safeseq` /
        //     `escapeseq`, which are `[… for o in value]` over the WHOLE
        //     sequence, plus `slice`, which hands back the same objects.
        //
        // The one shape that arrives here without being a sequence is the
        // `safeseq`/`escapeseq` non-iterable arm — an int, a float, `None`, a
        // `Decimal`/`BigInt` — which returns an escaped DIGIT STRING and still
        // carries the name-based grant. All three extractors answer a string
        // with one of its characters (`first`/`last` in their own arms,
        // `random` through `iter_values`), and no digit is a character
        // escaping would change, so the grant there is the same no-op the
        // `join` arm above documents. A `Value::Object` cannot reach here at
        // all: `items_are_safe` refuses it, and both sequence filters have
        // already turned it into a `List` of its keys.
        "first" | "last" | "random" => input_safety.items,
        // `get_digit`'s `except ValueError: return value` (#2403) — the input
        // object, `SafeData` and all, exactly as `default`'s truthy branch is.
        // Django registers it `is_safe=False`, so `renderer::IS_SAFE_FILTERS`
        // is not what makes `{{ p|get_digit:1 }}` live over a `mark_safe`d
        // value; this arm is.
        //
        // The condition is the SAME split the dispatch arm takes, and the two
        // exits it does NOT cover are deliberate:
        //
        //   * `if arg < 1` returns `int(value)` — a Python `int`, never
        //     `SafeData`, so no grant;
        //   * landing on the `-` of a negative returns the input here while
        //     Django RAISES (`int('-')`), so there is no Django answer to match
        //     and withholding is the escaping direction. That divergence is
        //     already documented in the dispatch arm.
        "get_digit" => {
            input_safety.container && get_digit_returns_input(value, arg, arg_was_quoted)
        }
        // `yesno`'s `if len(bits) < 2: return value` (#2401) — the same
        // return-the-input shape. Django's own docstring calls it "Invalid
        // arg", and it is the ONLY exit that does not build a string from the
        // argument's parts, which are plain `str`s even when the argument was a
        // `SafeString` (`SafeString.split(",")` does not propagate the marker).
        "yesno" => input_safety.container && yesno_returns_input(arg, arg_type),
        _ => false,
    }
}

/// Does Django's `get_digit` hand back its INPUT OBJECT? (#2403)
///
/// Mirrors the `"get_digit"` dispatch arm's first two exits. It is a second
/// reading of the same rule rather than a shared helper because the arm needs
/// the parsed values it computes on the way and this needs only the verdict —
/// the same shape `default`'s `value.is_truthy()` arm above already has. The
/// coupling is pinned behaviourally: `TestGetDigitsPassThroughBranch` sweeps
/// every argument spelling over a `mark_safe`d input, so a change to one and
/// not the other goes red.
///
/// `arg_int_is_type_error` is passed as `false` and that is not an
/// approximation: when it is TRUE the dispatch arm returns `Err`, and
/// `Result::map` never calls [`builtin_produced_safe`] on an `Err`, so this
/// function is only ever reached on the `ValueError` side where the flag
/// cannot change the answer.
fn get_digit_returns_input(value: &Value, arg: Option<&str>, arg_was_quoted: bool) -> bool {
    match filter_int_arg(
        "get_digit",
        arg,
        arg_was_quoted,
        false,
        0,
        BadArg::ReturnInput,
    ) {
        // `int(arg)` parsed; the input comes back only if `int(value)` raised.
        Ok(Some(_)) => int_digits_of(value, false).is_none(),
        // `int(arg)` raised before `value` was rebound.
        Ok(None) => true,
        Err(_) => false,
    }
}

/// Does Django's `yesno` hand back its INPUT OBJECT? (#2401)
///
/// `bits = arg.split(",")` with fewer than two parts. A missing argument
/// defaults to the three-part `"yes,no,maybe"` and so never reaches it.
fn yesno_returns_input(arg: Option<&str>, arg_type: ArgType) -> bool {
    yesno_bits(arg, arg_type).is_some_and(|bits| bits.len() < 2)
}

/// Django's `bits` for `yesno` — `arg.split(",")` after its `arg is None`
/// default, or `None` where Django's own `arg` is `None` (#2401).
///
/// One statement of the rule, read by the dispatch arm and by
/// [`builtin_produced_safe`], which must agree about which branch ran.
fn yesno_bits(arg: Option<&str>, arg_type: ArgType) -> Option<Vec<&str>> {
    match arg {
        // `if arg is None: arg = gettext("yes,no,maybe")` — for an ABSENT
        // argument and for one that resolved to Python `None` alike. The
        // string `"None"` is NOT this branch, which is the whole of why the
        // bit is threaded rather than sniffed off the text.
        None => None,
        Some(_) if arg_type.is_none => None,
        Some(a) => Some(a.split(',').collect()),
    }
}

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
///
/// Passes [`InputSafety::default()`] — every field `false`, the SAFE default.
/// This entry point has no view of the render chain, so it cannot know whether
/// the caller had `mark_safe`d the value or its items; reporting "not safe"
/// makes the four `needs_autoescape` filters escape, which is what they did
/// unconditionally before #2284, and makes `escape` / `join` /
/// `unordered_list` escape too (#2281, #2283). Only
/// [`apply_filter_full_safe`]'s renderer call sites, which track the chain's
/// safety, ever report `true` — which is why `InputSafety` derives `Default`
/// rather than offering a bare constructor: a new call site that cannot answer
/// the question fails CLOSED.
pub fn apply_filter_full(
    filter_name: &str,
    value: &Value,
    arg: Option<&str>,
    context: Option<&Context>,
    arg_was_quoted: bool,
) -> Result<Value> {
    apply_filter_full_safe(
        filter_name,
        value,
        arg,
        context,
        arg_was_quoted,
        InputSafety::default(),
    )
    .map(|(v, _)| v)
}

/// Like [`apply_filter_full`] but also reports whether the produced value is a
/// runtime ``SafeString`` (Django ``mark_safe`` / a value with ``__html__``).
///
/// MOST built-in filters never produce a runtime-safe value — their output is a
/// plain string, and the renderer's name-based ``safe_output_filters`` list
/// governs built-in safe filters like ``safe``/``urlize``. The exceptions are
/// the four whose safety depends on WHICH BRANCH ran rather than on their name;
/// see [`builtin_produced_safe`] (#2281). A *custom* filter is
/// runtime-safe iff its Python result has ``__html__``. The renderer threads
/// this out so a value a filter explicitly ``mark_safe()``d at runtime bypasses
/// auto-escaping — matching Django's ``render_value_in_context`` (escape iff the
/// *final* value lacks ``__html__``), even when the filter is not decorated
/// ``is_safe=True`` (#1660). A later plain-returning filter re-taints, because
/// it overwrites this flag with ``false``.
///
/// # `input_safety` — Django's `needs_autoescape` term (#2284), at both
/// granularities (#2283)
///
/// Django registers four built-ins ``needs_autoescape=True`` — `linebreaks`,
/// `linebreaksbr`, `urlize`, `urlizetrunc` — and each opens with
///
/// ```text
/// autoescape = autoescape and not isinstance(value, SafeData)
/// ```
///
/// so it **skips its own internal escape** when the value handed to it was
/// already safe. djust has no `{% autoescape %}` block (the tag is rejected by
/// the parser), so the first term is pinned `true` and the expression reduces
/// to `not input_safety.container` — which is the half that is observable today
/// and was missing: `{{ p|safe|linebreaks }}` rendered
/// `<p>&lt;b&gt;x&lt;/b&gt;</p>` where Django renders `<p><b>x</b></p>`.
///
/// `input_safety.container` is #2284's `input_was_safe` parameter under a new
/// name: the renderer's `runtime_safe` **before** this filter runs — the same
/// value `filter_output_is_safe` consumes as its input term (#2274), so the two
/// halves of Django's `SafeData` reading are driven off one piece of state
/// rather than two that can drift (#1646).
///
/// `input_safety.items` is the SECOND granularity, and it exists because three
/// of Django's built-ins read a different question. `escape` is
/// `conditional_escape` and reads the container (#2281). `join` and
/// `unordered_list` `conditional_escape` per ELEMENT, so what they read is
/// whether `safeseq` / `escapeseq` marked the items — which Django never does
/// to the sequence itself (#2283). One bool cannot answer both, and two
/// adjacent bools would be a transposition nothing could catch, so they travel
/// as one struct. See [`InputSafety`].
///
/// This does **not** loosen escaping for hostile input: both fields are `true`
/// only when the context `mark_safe`d the value, or an earlier `|safe` /
/// safe-output / item-safe filter marked it. Anything that was never marked
/// safe still takes the escape, which is what keeps the four names' membership
/// of `renderer::SAFE_OUTPUT_FILTERS` earned — see the `linebreaks` doc
/// comment.
pub fn apply_filter_full_safe(
    filter_name: &str,
    value: &Value,
    arg: Option<&str>,
    context: Option<&Context>,
    arg_was_quoted: bool,
    input_safety: InputSafety,
) -> Result<(Value, bool)> {
    // The ARGUMENT COUNT, before anything else touches the argument (#2400).
    //
    // FIRST, and that is Django's order rather than a convenience: `args_check`
    // runs inside `FilterExpression.__init__`, so it fires before any resolution
    // — `{% if p|upper:missingvar %}` is a `TemplateSyntaxError` in Django and
    // never a `VariableDoesNotExist`. Putting this below the resolve would swap
    // the two errors for every wrong-arity cell whose argument is also a miss.
    //
    // This is the CALL-time bound. The `{{ }}` path has already been refused at
    // PARSE time by `parser::parse_filter_specs` (Django's own timing), so the
    // cells that reach here are the TAG operands — `{% if p|f:"x" %}`,
    // `{% for x in p|f %}` — which `renderer::get_value_safe` splits out of a
    // raw string at render time and which no parse-time check can see. The two
    // sites take DIFFERENT bounds, which is why `filter_arity` exposes two
    // functions: for the five `needs_autoescape` built-ins Django COMPILES
    // `{{ p|urlize:"x" }}` and raises `TypeError` when the call happens, and a
    // parse-time refusal there would refuse a template Django accepts.
    if let Some(message) =
        crate::filter_arity::call_time_arity_error(filter_name, u8::from(arg.is_some()))
    {
        return Err(DjangoRustError::TemplateError(message));
    }
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
    //   * ``Ok(None)`` — a lookup MISS. RAISES, as Django does (#2328).
    //     Django's ``FilterExpression.resolve`` protects only the MAIN
    //     variable with ``string_if_invalid``; each ARGUMENT goes through a
    //     bare ``arg.resolve(context)`` whose ``VariableDoesNotExist`` nothing
    //     catches. Measured: all TWENTY-NINE argument-taking built-ins raise
    //     for `{{ p|f:missingvar }}`, and djust degraded silently for all
    //     twenty-nine — `{{ n|pluralize:es }}` rendered the literal text "es",
    //     which is exactly the silent-wrong-output class this whole area keeps
    //     producing. Earlier comments here defended the fallback as protection
    //     against a site-wide 500 on upgrade; that reasoning is superseded,
    //     because ``LiveViewConsumer.receive`` already catches a render error
    //     and sends a safe error frame WITHOUT dropping the socket, so the
    //     degradation decision is made once, at the transport, in the place
    //     that can be environment-aware.
    //
    //     A LITERAL never reaches the lookup — see ``is_literal_filter_arg``.
    //     This is Django's own split: `{{ p|add:7 }}` is a float/int literal
    //     and resolves without a context at all, while `{{ p|add:seven }}` is
    //     a variable and raises.
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
    //
    // The resolved `Value` is the LAST place the argument's Python type is
    // visible: one line below, `to_string()` turns it into the `&str` the
    // dispatch table takes, and `int(a_list)` and `int("[1, 2]")` raise
    // different exceptions (#2366). So the one bit that depends on the type —
    // "is `int(arg)` a TypeError?" — is computed here and threaded, rather
    // than the whole `Value` being pushed through 57 filter arms.
    let mut resolved_type: Option<Value> = None;
    let resolved_arg: Option<String> = match (arg, arg_was_quoted, context) {
        (Some(a), false, Some(ctx)) => match ctx.resolve(a)? {
            Some(v) => {
                let text = v.to_string();
                resolved_type = Some(v);
                Some(text)
            }
            None if !is_literal_filter_arg(a) => {
                return Err(DjangoRustError::VariableDoesNotExist(format!(
                    "filter '{filter_name}' argument {a:?} does not resolve — Django \
                     raises VariableDoesNotExist here"
                )));
            }
            None => None,
        },
        _ => None,
    };
    let builtin_arg = resolved_arg.as_deref().or(arg);
    let arg_type = ArgType {
        int_is_type_error: int_arg_is_type_error(resolved_type.as_ref()),
        is_none: arg_is_python_none(resolved_type.as_ref()),
    };

    // Built-ins take precedence over custom filters (mirrors the original
    // dispatch order). A built-in hit reports safety through
    // `builtin_produced_safe`, which answers `false` for all but four names.
    // `arg_was_quoted` reaches the dispatch table because `add` needs it: a
    // quoted "1.5" is a STRING to Python's int() (which raises), while an
    // unquoted 1.5 is a float literal (which truncates). See that arm (#2203).
    if let Some(builtin) = apply_builtin_filter(
        filter_name,
        value,
        builtin_arg,
        context,
        arg_was_quoted,
        arg_type,
        input_safety,
    ) {
        return builtin.map(|v| {
            let safe = builtin_produced_safe(
                filter_name,
                value,
                builtin_arg,
                arg_was_quoted,
                arg_type,
                &v,
                input_safety,
            );
            (v, safe)
        });
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
    //
    // ``input_safety`` is the OTHER half of Django's `needs_autoescape`
    // contract and was the whole of #2290: the `autoescape` kwarg was already
    // correct, but the value crossed into Python as a bare `str`, so
    // `autoescape and not isinstance(value, SafeData)` could never take its
    // second branch. `filter_registry::mark_input_safety` restores the marker
    // on the way in — for the CONTAINER and, for a sequence, per ITEM.
    if let Some(result) = filter_registry::apply_custom_filter(
        filter_name,
        value,
        arg,
        context,
        arg_was_quoted,
        true,
        input_safety,
    ) {
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
/// all 29 are implemented here and 28 of them are listed below. The list is not
/// a judgement call about which filters "feel string-shaped" — it is a
/// transcript of Django's decorators, and
/// ``python/tests/test_string_filter_stringification_2250.py`` re-derives the
/// set by introspecting the live ``defaultfilters`` registry, so a filter
/// Django adds to (or removes from) the decorator fails that test rather than
/// drifting silently.
///
/// **The one deliberate omission — ``escape``.** Django's returns a string;
/// djust's is ``conditional_escape`` (#2281), so it necessarily stringifies —
/// but through ``Display``, the RENDER form, and not through ``str(Decimal)``,
/// which is what this coercion selects. Taking the coercion is not free: it
/// changes the TYPE flowing down the rest of the chain, and
/// ``{{ d|escape|floatformat }}`` regressed in 1,168 measured cells. Teaching
/// ``floatformat`` to parse strings was tried and is worse (it cannot reproduce
/// Django's >200-digit passthrough or its NaN/inf handling from an ``f64``, and
/// broke 538 cells of ``{{ d|upper|floatformat }}``). That residue is measured
/// and tracked in #2257.
///
/// **``safe`` joined the list in #2303.** It used to be the second omission, on
/// the same reasoning — a no-op returning the value unchanged, so a ``Decimal``
/// stayed a ``Decimal`` and localized at the render site. But Django's
/// ``mark_safe(obj)`` is ``SafeString(str(obj))``: it stringifies, and it is
/// this coercion's ``str()`` and not ``Display`` that it wants. Listing it here
/// is what makes ``{{ d|safe }}`` Django's ``1E-9`` rather than the localized
/// ``0,000000001`` — and it is ONE mechanism rather than a second stringify
/// living in the ``"safe"`` arm, which would shadow this one.
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
    "safe",
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
    // What the argument's resolved Python TYPE says, which the `&str` above no
    // longer carries. See [`ArgType`].
    arg_type: ArgType,
    input_safety: InputSafety,
) -> Option<Result<Value>> {
    // Rebound rather than read through the struct at each site: `int_arg!`'s
    // call shape is pinned mechanically by
    // `python/tests/test_filter_argument_contract_2328.py::
    // TestChokepointIsTheOnlyParser`, and the pin is about the SET of call
    // sites and their policies rather than the spelling — so the eleven macro
    // calls keep the identifier they had.
    let arg_int_is_type_error = arg_type.int_is_type_error;
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
    // `Float` takes the SAME split, for the same reason (#2258). `str(1e20)` is
    // `1e+20`; `{{ f }}` renders `100000000000000000000`, because
    // `numberformat.format` converts an exponent-form float to a `Decimal` and
    // expands it up to Django's 200-digit cut-off. Django really does spell one
    // float two ways depending on which path it takes, and so must this: the
    // renderer keeps `Display`, and the string filters get `repr`.
    //
    // Before #2258 this arm could not have helped — `Display` was Rust's `{}`,
    // which is neither spelling — so the earlier version of this comment
    // recorded the gap ("diverges for reasons that show up in `{{ v }}` too")
    // rather than closing it. With `Display` correct, the coercion is the other
    // half.
    //
    // Every remaining variant's `Display` already IS Python's `str()`, which is
    // why this asks `py_str` rather than spelling the two arms here. That
    // helper (`djust_core`, beside `py_repr`) is the ONE place the split is
    // written down: `safeseq` needs the same `str()` per ITEM (#2324), and two
    // copies of a rule is the #1646 shape.
    let coerced: Value;
    let value: &Value = match value {
        Value::Decimal(_) | Value::Float(_) if is_string_filter(filter_name) => {
            coerced = Value::String(value.py_str());
            &coerced
        }
        _ => value,
    };
    let result: Result<Value> = match filter_name {
        "upper" => Ok(Value::String(value.to_string().to_uppercase())),
        "lower" => Ok(Value::String(value.to_string().to_lowercase())),
        "title" => Ok(Value::String(crate::truncate::title(&value.to_string()))),
        "length" => {
            // Django's filter is `len(value)` inside `except TypeError: return 0`,
            // so the fallback is 0 HERE and 1 in `{% for %}`'s unpacking arm
            // (`ForNode.render` writes `except TypeError: len_item = 1`). The
            // shared half — what `len()` answers when it does not raise — is
            // stated once in [`python_len`] and the two call sites pick their
            // own fallback, so a future `len()` refinement cannot land on one
            // and miss the other (#1646, #2387).
            Ok(Value::Integer(python_len(value).unwrap_or(0) as i64))
        }
        "default" => {
            // default filter with argument
            if value.is_truthy() {
                Ok(value.clone())
            } else {
                Ok(Value::String(arg.unwrap_or("").to_string()))
            }
        }
        // Django's `escape_filter` is `conditional_escape(value)` — EAGER, and
        // it returns a `SafeString`, so the escaped text is what the NEXT
        // filter in the chain sees (#2281).
        //
        // This was a no-op that left the escape to render time, which is
        // correct for `{{ p|escape }}` alone and wrong for every chain:
        // `{{ p|escape|upper }}` upper-cased the RAW value where Django
        // upper-cases `&lt;` to `&LT;`, `{{ p|escape|striptags }}` stripped
        // tags Django had already turned into inert text — and
        // `{{ p|escape|safe }}` emitted the raw payload, because `|safe`
        // suppressed the deferred escape that was the only escaping left.
        // That last one is a LIVE XSS: the idiom reads as "escape it, then
        // it is safe to emit", which is precisely what Django's semantics
        // make true, and djust turned it into `|safe` on attacker input.
        //
        // `escape` is in `renderer::SAFE_OUTPUT_FILTERS` — a grant this arm
        // earns by escaping its input here, the same way `force_escape` does.
        // The difference between the two is exactly the `input_safety.container`
        // check: `escape` is `conditional_escape` (a `SafeString` passes
        // through), `force_escape` is `escape` (a `SafeString` is escaped
        // AGAIN). `{{ p|safe|escape }}` and `{{ p|safe|force_escape }}` are
        // the cells that tell them apart.
        "escape" => Ok(Value::String(conditional_escape(
            value,
            input_safety.container,
        ))),
        // Django's `mark_safe(obj)` is `SafeString(str(obj))` — it does not
        // merely MARK the value, it changes its TYPE before the rest of the
        // chain sees it. So `|safe` is a stringify, for every variant, and the
        // downstream filters read the string.
        //
        // The container half landed first (#2283): `{{ l|safe|slice:":3" }}` is
        // `['<` in Django, three characters of the list's repr, and not a
        // three-element list. The rendered bytes of `{{ l|safe }}` alone are
        // identical either way (`Display` for a list IS its repr), so it was
        // invisible until the sequence filters started iterating — keeping the
        // list let `{{ l|safe|safeseq|... }}` reach the ITEM-safety grant with
        // the list's own elements and emit them live where Django escapes
        // characters of the repr.
        //
        // The scalar half is #2303, the same edit one variant over (#1646). Two
        // spellings it has to get right, and neither is a special case here:
        //
        //   * `str()`, not the RENDER form. `{{ p|safe }}` with `p = 1e20` is
        //     `1e+20` in Django and `100000000000000000000` under `Display`,
        //     because `Display` is `numberformat.format()`, which expands the
        //     exponent — and likewise `Decimal("1E-9")`. That is exactly the
        //     `@stringfilter` coercion at the top of this function, so `safe`
        //     is listed in `STRING_FILTERS` and arrives here ALREADY a
        //     `Value::String`. One mechanism, not two.
        //   * `Value::Missing` is `""`, not `"None"`. Django substitutes
        //     `string_if_invalid` for an absent variable BEFORE the chain runs,
        //     so `mark_safe` there sees `""`; putting the literal text `None`
        //     on the page would be worse than the pass-through this replaces.
        //     `Display` for `Missing` is already `""` — that is the whole of
        //     why the variant exists (#2203) — so this needs no special case
        //     either, and `test_the_literal_text_None_never_reaches_the_page`
        //     pins it.
        //
        // What this costs: the value no longer LOCALIZES at the render site,
        // because it is a string by then. That matches Django — `localize()`
        // leaves a `str` alone, and Django's `mark_safe` already stringified —
        // and it is why `{{ d|safe }}` on a `Decimal` under a German locale
        // stops emitting `0,000000001` (#2257 residue 1, for `safe`).
        "safe" => Ok(Value::String(value.to_string())),
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
        // Django, verbatim:
        //
        //     try:
        //         if autoescape: value = [conditional_escape(v) for v in value]
        //         data = conditional_escape(arg).join(value)
        //     except TypeError:
        //         return value
        //     return mark_safe(data)
        //
        // Three properties, and djust had none of them (#2283):
        //   * it ITERATES, so a string joins its CHARACTERS;
        //   * it `conditional_escape`s each item, so `|safeseq` upstream is
        //     honoured and a plain sequence is escaped HERE rather than at
        //     render time — which is what earns `join` its place in
        //     `renderer::SAFE_OUTPUT_FILTERS`;
        //   * the SEPARATOR gets `conditional_escape(arg)`, which is NOT the
        //     same as "escape it". A QUOTED filter argument is `SafeData` —
        //     `Variable.__init__` does
        //     `self.literal = mark_safe(unescape_string_literal(var))` — so
        //     `{{ l|join:"<br>" }}` renders a real `<br>`. A BARE identifier is
        //     resolved from the context (#2202), is not `SafeData`, and IS
        //     escaped. `arg_was_quoted` is exactly that distinction, and it is
        //     the same fact the `add` arm of `builtin_produced_safe` relies on.
        //
        //     Escaping unconditionally was a regression, not just a wrong
        //     comment. `main`'s `join` joined RAW and let the render escape the
        //     result, which lands on Django's bytes whenever a later `|safe`
        //     suppresses that render escape — 34 such cells. They are invisible
        //     unless the differential's separator carries HTML, which is why
        //     `FILTER_ARGS["join"]` is now `"<br>"`.
        //
        // The `TypeError` branch — an int, a float, `None` — is where Django
        // returns the value untouched and UNSAFE, so the render escapes it.
        // djust holds an unconditional safe-output grant by then, so it must
        // escape here to land on the same bytes (#2274).
        "join" => {
            let raw_sep = arg.unwrap_or(", ");
            // `conditional_escape(arg)`: a template LITERAL is already safe.
            let separator = if arg_was_quoted {
                raw_sep.to_string()
            } else {
                html_escape(raw_sep)
            };
            match iter_values(value) {
                Some(items) => {
                    let strings: Vec<String> = items
                        .iter()
                        .map(|v| conditional_escape(v, input_safety.items))
                        .collect();
                    Ok(Value::String(strings.join(&separator)))
                }
                // Django's `except TypeError: return value` — the value
                // UNCHANGED, and NOT `mark_safe`d, so the render escapes it.
                // Returning an escaped STRING here instead would change the
                // TYPE an int/None presents to the rest of the chain, and
                // `{{ n|join:", "|length }}` measured it (0 in Django, 2 for
                // `"42"`). `builtin_produced_safe` withholds the grant on
                // exactly this branch, which is why the value can stay raw.
                None => Ok(value.clone()),
            }
        }
        "truncatewords" => {
            match int_arg!(
                filter_name,
                arg,
                arg_was_quoted,
                arg_int_is_type_error,
                10,
                BadArg::ReturnInput
            ) {
                Some(n) => Ok(Value::String(crate::truncate::text_words(
                    &value.to_string(),
                    n,
                    Some(WORDS_TRUNCATE),
                ))),
                None => Ok(value.clone()),
            }
        }
        "truncatechars" => {
            match int_arg!(
                filter_name,
                arg,
                arg_was_quoted,
                arg_int_is_type_error,
                20,
                BadArg::ReturnInput
            ) {
                Some(n) => Ok(Value::String(crate::truncate::text_chars(
                    &value.to_string(),
                    n,
                    None,
                ))),
                None => Ok(value.clone()),
            }
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
            // The time between the value and the ARGUMENT, which defaults to
            // now (#2344). The argument was discarded here until then, so
            // `{{ then|timesince:other }}` silently answered "since now"
            // whatever `other` was.
            timesince_or_until(filter_name, value, arg, arg_was_quoted, false)
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
            // DIGIT STRINGS, not a machine integer. `int()` is unbounded in
            // Python, and every fixed width tried here has been the wrong shape:
            // `as_f64()` on a `Value::Decimal` is a binary double, so `int()`
            // was off by one from 2^53 up (`Decimal('9007199254740993')|add:1`
            // gave back 9007199254740993); `as i64` saturated from 2^63 up, so
            // the add overflowed and the filter returned its input UNCHANGED
            // (#2253); and the `i128` that replaced it does the same thing at 39
            // digits (#2260). `9`×60 `|add:1` was CORRECT on main only by
            // coincidence — it had arrived as the double `1e60`, whose expansion
            // is exactly the sum the filter was declining to compute.
            //
            // So the width is gone: `add_int_digits` is arbitrary-precision, and
            // the only way to reach the fail-soft below is an operand `int()`
            // itself would refuse.
            //
            // Non-finite floats are refused rather than saturated. `int(inf)`
            // raises `OverflowError` in Python — uncaught by Django's
            // `except (ValueError, TypeError)` — so there is no answer to
            // agree with, and `i64::MAX` was a fabricated number where the
            // fail-soft below at least returns the value it was given.
            let arg_value = arg.map(|s| Value::String(s.to_string()));
            // The VALUE is a real typed value, never a template literal, so its
            // float coercion is always allowed. Only the ARGUMENT's quoting is
            // in question.
            let lhs = int_digits_of(value, true);
            // A bare `True`/`False` first, through the ONE helper that states
            // that rule (#2347/#1646). `int_digits_of` is `int()` for a
            // NUMERIC spelling and answers `None` for the text `"True"` — which
            // is what a resolved builtin arrives as, since the argument channel
            // is `Option<&str>`. Without this arm `{{ p|add:True }}` fell to
            // the concatenation branch and rendered its input unchanged (5)
            // where Django computes `int(5) + int(True)` = 6, while
            // `{{ p|center:True }}` was already right because `center` reads
            // its argument through #2328's `python_int_arg`, the helper's other
            // caller. Two `int()`s, one of which knew the rule: #1646.
            //
            // `add` cannot simply use `python_int_arg`: that returns an `i64`
            // and `add` carries exact DIGITS, because a sum past `i64`
            // saturated and silently returned the input unchanged (#2253,
            // #2260). The shared piece is the rule, not the parse.
            let rhs = arg
                .and_then(|a| bare_bool_arg_as_int(a, arg_was_quoted))
                .map(|n| n.to_string())
                .or_else(|| {
                    arg_value
                        .as_ref()
                        .and_then(|a| int_digits_of(a, !arg_was_quoted))
                });
            match lhs.zip(rhs) {
                // A sum outside `i64` is carried as its exact digits rather than
                // being thrown away: `Value::Integer` is an i64 and Python's is
                // not, so `{{ p|add:1 }}` on a 20-digit `DecimalField` had no
                // Integer to return and silently returned its input (#2253).
                //
                // `BigInt`, not `Decimal` (#2260). Django's first branch is
                // `int(value) + int(arg)` and an `int` is what it returns, so a
                // `Decimal` here was the nearest exact-digit variant available
                // rather than the right type: it rendered identically but
                // spelled itself `Decimal('...')` under `pprint`, quoted itself
                // under `json_script`, and left the process as a
                // `decimal.Decimal`.
                Some((a, b)) => {
                    let sum = djust_core::decimal::add_int_digits(&a, &b);
                    Ok(match sum.parse::<i64>() {
                        Ok(n) => Value::Integer(n),
                        Err(_) => Value::BigInt(sum),
                    })
                }
                None => match (value, arg) {
                    // Concatenation branch.
                    (Value::String(s), Some(a)) => Ok(Value::String(format!("{s}{a}"))),
                    // Django's third branch: `except Exception: return ""`.
                    //
                    // This returned the value UNCHANGED until #2359, on the
                    // argument that "turning a rendered value into silent
                    // emptiness on upgrade is the silent-wrong-output class
                    // this engine keeps having to fix". Measuring the class
                    // inverted the argument: echoing is the MORE PERMISSIVE
                    // direction — it puts the unfiltered input on the page
                    // where Django puts nothing — and the values that reach
                    // here are exactly the ones Django decided have no sum
                    // and no concatenation (`None`, a list, a dict, a tuple).
                    // Rendering them is not "preserving" anything; it is
                    // rendering a Python repr into a page that asked for a
                    // number.
                    _ => Ok(Value::String(String::new())),
                },
            }
        }
        "pluralize" => Ok(Value::String(pluralize(value, arg.unwrap_or("s")))),
        "slugify" => {
            // slugify filter: converts to URL-friendly slug
            Ok(Value::String(crate::truncate::slugify(&value.to_string())))
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
        // Django's body, statement for statement (#2401):
        //
        //     if arg is None:      arg = gettext("yes,no,maybe")
        //     bits = arg.split(",")
        //     if len(bits) < 2:    return value            # Invalid arg.
        //     try:                 yes, no, maybe = bits
        //     except ValueError:   yes, no, maybe = bits[0], bits[1], bits[1]
        //     if value is None:    return maybe
        //     if value:            return yes
        //     return no
        //
        // The previous version ran a three-way branch of its own over a MIX of
        // the argument's parts and the built-in defaults, and diverged on four
        // independent axes at once — which is why this is a transcription of
        // the body rather than four repairs:
        //
        //   * a one-part argument fell through to `yes`/`no`/`maybe` defaults
        //     where Django returns the VALUE (`{{ True|yesno:"only" }}` was
        //     `only`, Django says `True`);
        //   * a FALSY-but-not-`None` value took the `maybe` arm, where Django
        //     reserves that for `None` alone (`{{ ""|yesno }}` was `maybe`,
        //     Django says `no`) — including an ABSENT variable, which Django
        //     has already replaced with `string_if_invalid` (`""`) before the
        //     filter runs, so it is falsy rather than `None`;
        //   * a FOUR-part argument read `bits[2]` for `None`, where Django's
        //     unpack raises for any length that is not exactly three and falls
        //     back to `bits[1]` for both 2 and 4+;
        //   * `Value::Bool(false)` had its own arm answering `no` while every
        //     other falsy shape answered `maybe`, so the arm looked correct
        //     from the one input a curated test reaches for.
        //
        // `Value::None` and `Value::Missing` are NOT the same row here.
        // `Missing` is Django's `string_if_invalid` substitution — measured
        // `{{ absent|yesno:"a,b,c" }}` is `b`, the FALSE arm — so only a real
        // `None` takes `maybe`. `default_if_none` makes the same split.
        "yesno" => {
            let bits =
                yesno_bits(arg, arg_type).unwrap_or_else(|| "yes,no,maybe".split(',').collect());
            if bits.len() < 2 {
                // Invalid arg: the result IS the input, so its safety grant is
                // the result's — see `builtin_produced_safe`'s `yesno` arm.
                return Some(Ok(value.clone()));
            }
            let (yes, no, maybe) = if bits.len() == 3 {
                (bits[0], bits[1], bits[2])
            } else {
                (bits[0], bits[1], bits[1])
            };
            let chosen = if matches!(value, Value::None) {
                maybe
            } else if value.is_truthy() {
                yes
            } else {
                no
            };
            Ok(Value::String(chosen.to_string()))
        }
        "linebreaks" => {
            // linebreaks filter: converts newlines to <p> and <br> tags.
            // `needs_autoescape=True` (#2284) — see `apply_filter_full_safe`.
            Ok(Value::String(linebreaks(
                &value.to_string(),
                !input_safety.container,
            )))
        }
        "linebreaksbr" => {
            // linebreaksbr filter: converts newlines to <br> tags.
            // `needs_autoescape=True` (#2284) — see `apply_filter_full_safe`.
            Ok(Value::String(linebreaksbr(
                &value.to_string(),
                !input_safety.container,
            )))
        }
        "cut" => {
            // cut filter: removes all occurrences of arg from string
            let remove_str = arg.unwrap_or("");
            Ok(Value::String(value.to_string().replace(remove_str, "")))
        }
        "divisibleby" => {
            // Django is `int(value) % int(arg) == 0`, so it reads the VALUE and
            // not the type: `{{ "42"|divisibleby:"2" }}` is `True`. This arm
            // matched `Value::Integer` alone, which made a numeric STRING
            // `False` — and #2303's `|safe` stringify turns every integer into
            // one, so `{{ n|safe|divisibleby:"2" }}` would have started
            // answering `False` where it (and Django) said `True`. Found by the
            // two-build differential, not by inspection.
            //
            // Django RAISES on anything `int()` rejects, and since #2328 so
            // does this: the ARGUMENT goes through the one chokepoint, which
            // also brings `int()`'s whitespace, sign and `_` spellings with it.
            // (The VALUE below keeps its fail-soft `False`; that is the other
            // half of Django's `int(value) % int(arg)` and a separate question.)
            let divisor = int_arg!(
                filter_name,
                arg,
                arg_was_quoted,
                arg_int_is_type_error,
                1,
                BadArg::Raise
            )
            .unwrap_or(1);
            // A divisor of ZERO is Python's `ZeroDivisionError` (#2346). This
            // arm answered `False`, which is a guard Django's
            // `int(value) % int(arg)` does not have — and the divergence was
            // reachable two ways: `divisibleby:"0"` always, and
            // `divisibleby:False` only since #2328 made `int(False)` be `0`, as
            // Python has it.
            //
            // Asked BEFORE the value, matching `%`'s own order: Python
            // evaluates `int(value)` first but raises on the operator, so a
            // value djust cannot read still reaches the division. Keeping the
            // value's fail-soft `False` for a divisor of zero would answer a
            // question Django never gets to.
            if divisor == 0 {
                return Some(Err(DjangoRustError::TemplateError(format!(
                    "filter '{filter_name}' is int(value) % int(arg), and a divisor of \
                     zero is a ZeroDivisionError — Django raises here too"
                ))));
            }
            let dividend = match value {
                Value::Integer(n) => Some(*n),
                Value::String(s) => s.trim().parse::<i64>().ok(),
                _ => None,
            };
            Ok(Value::Bool(match dividend {
                Some(n) => n % divisor == 0,
                None => false,
            }))
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
            // Returns a `Result` since #2328: `int(None)` is a TypeError past
            // its `except ValueError`, and that raise has to happen at the
            // argument-parse point INSIDE the module, because Django parses the
            // value first and a value that fails never reaches `int(arg)`.
            crate::floatformat::floatformat(value, arg, arg_was_quoted, arg_int_is_type_error)
        }
        "filesizeformat" => {
            // Django coerces with `int(bytes_)` and formats EVERY input,
            // falling back to `0 bytes` rather than echoing the value (#2264).
            // #2260's own arm here is superseded by that rewrite, which already
            // routes every variant through one `filesize_to_int` — so `BigInt`
            // belongs there, next to `Decimal`, and that is where it is.
            Ok(Value::String(format_filesize(value)))
        }
        // `random` is `lambda value: random.choice(value)` — it INDEXES, so a
        // string yields one CHARACTER and a dict one key (#2283). Returning
        // the whole string was not "a random pick of one element", it was the
        // sequence itself.
        "random" => {
            // `random.choice(d.keys())` raises `TypeError` — a view is not
            // subscriptable — and Django does not catch it. Measured against
            // all three kinds; djust renders nothing rather than raising,
            // which is never more permissive (#2340).
            if matches!(value, Value::DictView { .. }) {
                return Some(Ok(Value::Missing));
            }
            match iter_values(value) {
                Some(items) if !items.is_empty() => {
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
                // An EMPTY sequence: `random.choice([])` raises `IndexError`.
                Some(_) => Ok(Value::Missing),
                // Not iterable at all: Python raises `TypeError`; djust fails
                // soft with the value unchanged. `random` holds no safe-output
                // grant, so the render still escapes this.
                None => Ok(value.clone()),
            }
        }
        "timeuntil" => {
            // The same computation as `timesince` with the operands swapped,
            // and the same ARGUMENT rule (#2344).
            timesince_or_until(filter_name, value, arg, arg_was_quoted, true)
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
                        "|date filter parse failed; rendering Django's own answer",
                    );
                    // This returned `value.clone()` until #2359 — the MORE
                    // PERMISSIVE direction, since it put the unfiltered input
                    // on the page for every `{{ p|date }}` over a string, an
                    // int, a list or a dict. Django's answer is usually `""`
                    // and is not ALWAYS: see `django_literal_only_format`.
                    // The djust EXTENSION is untouched — a value that parses
                    // still formats, which is not optional, because a Python
                    // `date` reaches this renderer as its ISO string
                    // (`Value` has no date variant).
                    Ok(Value::String(django_literal_only_format(value, format_str)))
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
                        "|time filter parse failed; rendering Django's own answer",
                    );
                    // `except (AttributeError, TypeError): return ""` — see
                    // the `date` arm above for why the echo was the wrong
                    // default, and `django_literal_only_format` for why the
                    // answer is not unconditionally empty. `TimeFormat` and
                    // `DateFormat` share one `Formatter.format`, so the rule
                    // is the same function for both filters (#1646).
                    Ok(Value::String(django_literal_only_format(value, format_str)))
                }
            }
        }
        // Django, verbatim:
        //
        //     try:    return sorted(value, key=_property_resolver(arg))
        //     except (AttributeError, TypeError): return ""
        //
        // djust had the sort and NOT the failure branch, returning the input
        // UNCHANGED where Django destroys it. That is a security defect once
        // anything downstream can grant safety, and #2283 made two things
        // downstream do exactly that: `{{ l|dictsort:"k"|safeseq|unordered_list }}`
        // emitted raw markup on a list Django had already thrown away, on data
        // nothing ever marked safe.
        //
        // The point fix was to keep `dictsort` out of
        // `renderer::ITEM_SAFETY_PRESERVING_FILTERS` — which closes
        // `safeseq|dictsort` and leaves `dictsort|safeseq`, the same class one
        // step over. The failure branch closes BOTH orders, and every future
        // one, because the value Django discarded is discarded here too.
        "dictsort" | "dictsortreversed" => {
            let sort_key = arg.unwrap_or("name");
            // An UNQUOTED integer literal is an `int` to Python, so
            // `itemgetter(n)` indexes. A quoted one is a `str` and looks up a
            // key. `arg_was_quoted` is the only thing that separates them, and
            // `{{ p|dictsort:0 }}` vs `{{ p|dictsort:"1" }}` on the same list
            // of strings is the cell that proves it.
            let index = if arg_was_quoted {
                None
            } else {
                sort_key.parse::<usize>().ok()
            };
            let sorted = match value {
                // A dict VIEW sorts (#2340). Django's `dictsort` is
                // `sorted(value, key=…)` and `sorted()` takes ANY iterable, so
                // `{{ d.values|dictsort:"k" }}` is a real, working idiom — and
                // it returns a LIST, which the `Ok(Value::List(items))` below
                // already produces.
                //
                // The or-pattern audit first classified this arm's `_ => None`
                // as CORRECT, on the measurement `{{ p.items|dictsort:'0' }}`
                // -> `''`. That is a case where DJANGO ALSO FAILS — a quoted
                // `'0'` is a key lookup and a tuple has no key `'0'` — so one
                // arg value said "agree" about a filter that diverges for
                // every arg that resolves. The exhaustive filter sweep missed
                // it for the same reason: `FILTER_ARGS` carries ONE arg per
                // filter, which is a curated sample wearing a sweep's clothes.
                Value::List(items) | Value::Tuple(items) | Value::DictView { items, .. } => {
                    match index {
                        Some(n) => dictsort_by_index(items, n),
                        None => dictsort_by_key(items, sort_key),
                    }
                }
                // Not a sequence at all: `sorted()` raises `TypeError`.
                _ => None,
            };
            match sorted {
                Some(mut items) => {
                    if filter_name == "dictsortreversed" {
                        items.reverse();
                    }
                    Ok(Value::List(items))
                }
                None => Ok(Value::String(String::new())),
            }
        }
        "urlencode" => {
            // `quote(value, safe=arg)`, and `quote`'s own default safe set is
            // `"/"` — NOT the empty string. `urlencode:""` is the form that
            // percent-encodes a path separator (#2262).
            Ok(Value::String(crate::truncate::urlencode(
                &value.to_string(),
                arg,
            )))
        }
        "stringformat" => {
            // stringformat filter: formats value using Python %-style format spec
            // Usage: {{ value|stringformat:"s" }} → "%s" % value
            // The argument is the format spec WITHOUT the leading %
            let spec = arg.unwrap_or("s");
            Ok(Value::String(crate::stringformat::apply(value, spec)))
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
            // Django is `wrap(value, int(arg))`, and `int()` raises for a
            // non-numeric argument. #2293 recorded djust's historical 75
            // default here as a deliberate divergence; #2328 closed it — the
            // argument goes through the one chokepoint and raises, as Django
            // does. A PARSED width of <= 0 is a different case again: that is
            // Django's own `_wrap_chunks` guard, inside `textwrap::wrap`.
            let width = int_arg!(
                filter_name,
                arg,
                arg_was_quoted,
                arg_int_is_type_error,
                75,
                BadArg::Raise
            )
            .unwrap_or(75);
            crate::textwrap::wrap(&value.to_string(), width)
                .map(Value::String)
                .map_err(|e| DjangoRustError::TemplateError(e.to_string()))
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
            // `value.ljust(int(arg))` — `int()` raises, so the argument takes
            // the chokepoint's `Raise` arm (#2328).
            let width = pad_width!(
                filter_name,
                int_arg!(
                    filter_name,
                    arg,
                    arg_was_quoted,
                    arg_int_is_type_error,
                    0,
                    BadArg::Raise
                )
            );
            let s = value.to_string();
            // NOT `format!("{s:<width$}")`, which was here: Rust's format spec
            // holds its width in a `u16`, so a width of **65536 or more** — one
            // past `u16::MAX` — panics with "Formatting argument out of range".
            // `{{ p|ljust:"65536" }}` therefore aborted the render with a
            // `PanicException`, whose MRO is `BaseException` directly: it does
            // not inherit from `Exception` at all, so it walks past the
            // consumer's `except Exception` and kills the SESSION rather than
            // producing an error frame. Confirmed in release as well as debug.
            //
            // The width must PARSE to reach the panic, which is what makes this
            // easy to miss: `ljust:"999999999999999999999"` is 21 digits, past
            // `usize::MAX`, so the old `parse::<usize>()` failed and fell back
            // to width 0. `ljust:"18446744073709551615"` — one digit shorter,
            // exactly `usize::MAX` — parses, and panicked.
            //
            // Padding explicitly is what `center` below already does, which is
            // why `center` never had the bug.
            let len = s.chars().count();
            Ok(Value::String(if width <= len {
                s
            } else {
                format!("{s}{}", " ".repeat(width - len))
            }))
        }
        "rjust" => {
            // `value.rjust(int(arg))` — see `ljust` for both halves: the
            // chokepoint argument and why the padding is explicit (#2328).
            let width = pad_width!(
                filter_name,
                int_arg!(
                    filter_name,
                    arg,
                    arg_was_quoted,
                    arg_int_is_type_error,
                    0,
                    BadArg::Raise
                )
            );
            let s = value.to_string();
            let len = s.chars().count();
            Ok(Value::String(if width <= len {
                s
            } else {
                format!("{}{s}", " ".repeat(width - len))
            }))
        }
        "center" => {
            // center filter: `value.center(int(arg))`.
            //
            // NOT `format!("{s:^width$}")`, which was here: Rust's `^` puts the
            // SMALLER half of an odd margin on the left, unconditionally.
            // CPython's `str.center` is
            //
            //     marg = width - len(self)
            //     left = marg / 2 + (marg & width & 1)
            //
            // — so an odd margin biases left only when the WIDTH is also odd
            // (#2294). `'ab'.center(5)` is `'  ab '`, not `' ab  '`.
            //
            // The two agree whenever the margin is even, which is why a curated
            // table sampling `'a'.center(4)` and `'abc'.center(6)` finds
            // nothing. Both halves of the formula are load-bearing and are
            // gate-off tested separately.
            let width = pad_width!(
                filter_name,
                int_arg!(
                    filter_name,
                    arg,
                    arg_was_quoted,
                    arg_int_is_type_error,
                    0,
                    BadArg::Raise
                )
            );
            let s = value.to_string();
            // Code points, like `len()` — and like `{:^}`, which also counted
            // chars, so this is not the byte-vs-char defect (#2279).
            let len = s.chars().count();
            Ok(Value::String(if width <= len {
                s
            } else {
                let marg = width - len;
                let left = marg / 2 + (marg & width & 1);
                format!("{}{s}{}", " ".repeat(left), " ".repeat(marg - left))
            }))
        }
        "make_list" => {
            // make_list filter: split string into list of characters
            let s = value.to_string();
            let chars: Vec<Value> = s.chars().map(|c| Value::String(c.to_string())).collect();
            Ok(Value::List(chars))
        }
        "json_script" => {
            // `json.dumps(d.keys())` raises `TypeError`. Same treatment as
            // `random` above (#2340).
            if matches!(value, Value::DictView { .. }) {
                return Some(Ok(Value::Missing));
            }
            // json_script filter: wrap value as JSON inside <script id="..."> tag
            let element_id = arg.unwrap_or("data");
            let json_str = value_to_json(value);
            let safe_json = json_escape_for_script(&json_str);
            // Django builds the tag with `format_html`, whose interpolation is
            // `conditional_escape(element_id)` — so a QUOTED-literal id goes in
            // RAW (a constant filter argument is `mark_safe`d by
            // `FilterExpression.resolve`) and a resolved one is escaped.
            //
            // The third filter that lets a constant argument's safety reach the
            // page, and the one #2389's own list of candidates does not name —
            // found by running all 57 built-ins against a hostile literal
            // argument rather than by reading the bodies. `yesno` and
            // `pluralize`, which that list DOES name, split their argument with
            // `str.split`, and a `SafeString` split yields plain `str`s; `join`
            // already agreed, because `conditional_escape(arg)` leaves the
            // separator alone on both engines.
            //
            // No new attacker surface: `arg_was_quoted` is true only for a
            // literal written in the TEMPLATE SOURCE, which the author already
            // controls as completely as any raw HTML they type. The variable
            // channel still escapes, which is the half that can carry data.
            let safe_id = if arg_was_quoted {
                element_id.to_string()
            } else {
                html_escape(element_id)
            };
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
            // linenumbers filter: prepend line numbers to each line.
            // `needs_autoescape=True` (#2284) — see `apply_filter_full_safe`.
            Ok(Value::String(add_linenumbers(
                &value.to_string(),
                !input_safety.container,
            )))
        }
        "get_digit" => {
            // Django indexes `str(int(value))`, NOT the rendered value:
            //
            //     arg = int(arg); value = int(value)   # ValueError -> value
            //     if arg < 1: return value
            //     try: return int(str(value)[-arg])
            //     except IndexError: return 0
            //
            // Which is why `{{ 1e-200|get_digit:3 }}` is `0` and not a digit of
            // the rendering: `int(1e-200)` is `0`, so there is no third digit.
            // Reading the rendered string instead was invisible while `Display`
            // expanded every float — it answered `0` from the 200 zeros — and
            // became wrong the moment #2258 made `{{ 1e-200 }}` render `1e-200`,
            // whose third-from-last character is `2`. The #2260 differential
            // caught it as a regression against `main`.
            //
            // `except ValueError: return value` and `if arg < 1: return value`
            // are NOT the same answer, and an earlier version of this comment
            // said they were (#2403). `value = int(value)` runs INSIDE the
            // `try`, BEFORE the `arg < 1` test, so:
            //
            //   * `int(arg)` raised   -> `value` was never rebound; the INPUT
            //                            OBJECT comes back, `SafeData` and all;
            //   * `int(value)` raised -> same, the input object;
            //   * `arg < 1`           -> the CONVERTED int comes back, so
            //                            `{{ False|get_digit:0 }}` is `0` and
            //                            not `False`, and `{{ 1.5|get_digit:0 }}`
            //                            is `1` and not `1.5`. Measured.
            //
            // The distinction decides the safety grant as well as the text —
            // an `int` is never `SafeData` — so the two exits are spelled
            // separately here and `builtin_produced_safe`'s `yesno`-shaped
            // two-branch arm answers off the same split.
            let Some(n) = int_arg!(
                filter_name,
                arg,
                arg_was_quoted,
                arg_int_is_type_error,
                0,
                BadArg::ReturnInput
            ) else {
                return Some(Ok(value.clone()));
            };
            // `int(value)` raised: Django returns the value unchanged.
            let Some(d) = int_digits_of(value, false) else {
                return Some(Ok(value.clone()));
            };
            if n < 1 {
                return Some(Ok(int_value_of(&d)));
            }
            let n = n as usize;
            // `str(value)` INCLUDES the sign, and Django indexes into that,
            // so `-123` has four characters. Out of range is `0`; landing on
            // the `-` raises in Django (`int('-')`) and returns the value
            // unchanged here, the same fail-soft posture the rest of this
            // module takes rather than 500ing (documented divergence).
            Ok(match d.as_bytes().get(d.len().wrapping_sub(n)) {
                Some(b) if b.is_ascii_digit() => Value::String((*b as char).to_string()),
                Some(_) => value.clone(),
                None => Value::String("0".to_string()),
            })
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
            // Django's `pprint` filter is `pprint.pformat(value)` — which WRAPS
            // at width 80. The single-line builder this replaced diverged by
            // every newline and every indent space above that width (#2277).
            Ok(Value::String(crate::pprint::pformat(value)))
        }
        // `[mark_safe(obj) for obj in value]`. `mark_safe(obj)` is
        // `SafeString(str(obj))`, so it does not merely MARK an item — it
        // replaces it with its `str()`, for every input (#2324). A list of ints
        // comes out a list of STRINGS, which shows up two ways: directly, since
        // a list renders its elements through `repr` (`{{ p|safeseq }}` on
        // `[1, 2]` is `['1', '2']`), and through a LATER filter, since the
        // item's type is still readable — `{{ p|safeseq|unordered_list }}`
        // nested a `<ul>` for a sublist where Django emits the string
        // `mark_safe` made of it.
        //
        // `py_str`, NOT `to_string`: `Display` is Django's
        // `numberformat.format()`, so `str(1e20)` is `1e+20` where `{{ f }}`
        // renders `100000000000000000000`. Same helper the `@stringfilter`
        // coercion at the top of this function uses — `|safe` is that coercion
        // applied to the scalar case (#2303), and this is the same rule per
        // item. One mechanism, not two (#1646).
        //
        // It marks the ITEMS, never the list — which is why `safeseq` is NOT in
        // `SAFE_OUTPUT_FILTERS` and is in `renderer::ITEM_SAFE_OUTPUT_FILTERS`
        // instead (#2283). The stringify does not move that grant: the items
        // were emitted unescaped by `join`/`unordered_list` before and after,
        // and Django emits the same bytes. Rendering
        // the sequence directly therefore escapes its `repr`, exactly as
        // Django does; the grant is only visible to `join` /
        // `unordered_list`, the two filters that `conditional_escape` per item.
        //
        // Iterating is what makes the string case a LIST of characters rather
        // than the string itself. The non-iterable arm is #2274's escape and
        // it is still load-bearing: an int, a float, a bool or `None` still
        // reaches it, Python still raises `TypeError` there, and djust still
        // fails soft — so the value must be escaped rather than handed back.
        "safeseq" => match iter_values(value) {
            Some(items) => Ok(collapse_if_input_safe(
                Value::List(
                    items
                        .iter()
                        .map(|item| Value::String(item.py_str()))
                        .collect(),
                ),
                input_safety.container,
            )),
            None => Ok(Value::String(html_escape(&value.to_string()))),
        },
        // `[conditional_escape(obj) for obj in value]` — same iteration, and
        // the escaped items are `SafeString`s, so `escapeseq` is item-safe too
        // and `{{ l|escapeseq|join:", " }}` must not escape a second time.
        //
        // `conditional_escape` and not `html_escape` (#2287): Django's is
        // CONDITIONAL, so items that were ALREADY `SafeData` pass through
        // untouched. That branch was unreachable until the context started
        // reporting item safety — before it, `input_safety.items` could only
        // be set by `safeseq`/`escapeseq` themselves, and
        // `filter_output_items_are_safe` refuses the grant when the container
        // was safe, so nothing could hand `escapeseq` pre-safe items. With the
        // #2287 seed a view's `[mark_safe(x), …]` reaches it, and escaping
        // those a second time made `{{ p|escapeseq|join:", " }}` emit
        // `&amp;lt;b&amp;gt;` where Django emits `<b>`.
        "escapeseq" => match iter_values(value) {
            Some(items) => Ok(collapse_if_input_safe(
                Value::List(
                    items
                        .iter()
                        .map(|item| Value::String(conditional_escape(item, input_safety.items)))
                        .collect(),
                ),
                input_safety.container,
            )),
            None => Ok(Value::String(html_escape(&value.to_string()))),
        },
        "urlize" => {
            // urlize filter: convert URLs and emails to clickable links.
            // `needs_autoescape=True` (#2284) — see `apply_filter_full_safe`.
            Ok(Value::String(urlize(
                &value.to_string(),
                None,
                !input_safety.container,
            )))
        }
        "urlizetrunc" => {
            // urlizetrunc filter: like urlize but truncates displayed URL.
            // `needs_autoescape=True` (#2284) — see `apply_filter_full_safe`.
            //
            // Django is `urlize(value, trim_url_limit=int(limit))`, a bare
            // `int()`, so the argument takes the chokepoint's `Raise` arm
            // (#2328). A NEGATIVE limit reaches `Truncator.chars(-3)`, which
            // keeps nothing — clamping to 0 is the same answer. Before this,
            // `parse::<usize>` refused a negative and the URL was not truncated
            // at all.
            //
            // Deliberately NOT `pad_width`: this limit is a COMPARISON bound,
            // never an allocation, so the `MAX_PAD_WIDTH` cap that stops the
            // pad filters from asking for an unbounded `repeat` would be a
            // divergence here for nothing. A huge limit simply means "do not
            // truncate", which is what Django does too.
            //
            // NOT `arg.map(|_| int_arg!(..))`: the macro's `return` would leave
            // the CLOSURE, not the filter, so the error would be swallowed into
            // the `Option`. Same trap as the `?`-in-a-match-arm one above.
            let parsed = int_arg!(
                filter_name,
                arg,
                arg_was_quoted,
                arg_int_is_type_error,
                0,
                BadArg::Raise
            );
            let limit = arg.map(|_| parsed.unwrap_or(0).max(0) as usize);
            Ok(Value::String(urlize(
                &value.to_string(),
                limit,
                !input_safety.container,
            )))
        }
        // Django's `list_formatter` wraps each item in `<li>` through
        // `conditional_escape`, so a string becomes one `<li>` per CHARACTER
        // (#2283) and `|safeseq` upstream suppresses the per-item escape.
        //
        // The non-iterable arm is #2274's escape, still load-bearing for the
        // scalar shapes: `unordered_list` holds an unconditional grant in
        // `SAFE_OUTPUT_FILTERS`, earned by the per-item escape below, and a
        // value that produces no `<li>` at all must not ride that grant raw.
        "unordered_list" => match iter_values(value) {
            Some(items) => Ok(Value::String(unordered_list(&items, 1, input_safety.items))),
            None => Ok(Value::String(html_escape(&value.to_string()))),
        },
        "truncatechars_html" => {
            match int_arg!(
                filter_name,
                arg,
                arg_was_quoted,
                arg_int_is_type_error,
                20,
                BadArg::ReturnInput
            ) {
                Some(n) => Ok(Value::String(crate::truncate::html_chars(
                    &value.to_string(),
                    n,
                    None,
                ))),
                None => Ok(value.clone()),
            }
        }
        "truncatewords_html" => {
            match int_arg!(
                filter_name,
                arg,
                arg_was_quoted,
                arg_int_is_type_error,
                10,
                BadArg::ReturnInput
            ) {
                Some(n) => Ok(Value::String(crate::truncate::html_words(
                    &value.to_string(),
                    n,
                    Some(WORDS_TRUNCATE),
                ))),
                None => Ok(value.clone()),
            }
        }
        // Not a built-in — signal the caller to try the custom-filter
        // registry. (The custom fallback lives in ``apply_filter_full_safe``
        // so it can capture the result's runtime safeness, #1660.)
        _ => return None,
    };
    Some(result)
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

/// Django passes `truncate=" …"` to both `truncatewords` filters and lets
/// `truncatechars` take `Truncator`'s default. The leading space is genuine.
const WORDS_TRUNCATE: &str = " \u{2026}";

/// What Django does when a filter's numeric argument is not one.
///
/// The two arms are not a style choice — they are the two shapes Django's own
/// source takes, and which one a filter has is observable:
///
/// ```python
/// def center(value, arg):        return value.center(int(arg))   # ValueError escapes
/// def truncatechars(value, arg):
///     try:    length = int(arg)
///     except ValueError:  return value                           # caught
/// ```
///
/// Measured against Django 5.2 for every argument-taking built-in; the table
/// is `python/tests/test_filter_argument_contract_2328.py`.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub(crate) enum BadArg {
    /// Django writes the bare `int(arg)` and lets `ValueError` escape, so the
    /// render fails: `center`, `ljust`, `rjust`, `divisibleby`, `wordwrap`,
    /// `urlizetrunc`.
    Raise,
    /// Django wraps it in `except ValueError: return value`, so the filter
    /// hands back its INPUT untouched: `truncatechars`, `truncatewords`,
    /// `truncatechars_html`, `truncatewords_html`, `get_digit`, `floatformat`.
    ReturnInput,
}

/// **THE** numeric-filter-argument chokepoint (#2328).
///
/// Before this, TWELVE dispatch arms read their argument as a number through
/// FOUR different parsers: six spelled
/// `arg.and_then(|s| s.parse::<usize>().ok()).unwrap_or(N)` inline with its own `N`,
/// `wordwrap` had a seventh spelling of the same thing, the `truncate_arg`
/// helper served four more, and `floatformat::parse_int_like` was a fourth
/// parser in its own module. They
/// and they disagreed with Django and with each other in four separate ways at
/// once — `int(" 5 ")` is 5 and `parse` refuses it, `int("1_0")` is 10 and
/// `parse` refuses it, `int(2.7)` is 2 for an UNQUOTED float literal while
/// `int("2.7")` raises, and an unparseable argument silently became the
/// per-filter default where Django raises. Fixing twelve filters in twelve
/// places is the #1646 drift this codebase has paid for repeatedly, and the
/// thirteenth filter — the next one anybody adds — would not have got the fix.
/// So every built-in that reads its argument as a number parses it here.
///
/// The pin is mechanical, not a comment:
/// `python/tests/test_filter_argument_contract_2328.py::TestChokepointIsTheOnlyParser`
/// fails if a bare `parse::<..>()` on the argument reappears in the dispatch
/// table.
///
/// # The `arg_was_quoted` term
///
/// `{{ x|center:2.7 }}` hands Python the FLOAT `2.7`, and `int(2.7)` is `2`.
/// `{{ x|center:"2.7" }}` hands it the STRING, and `int("2.7")` raises. One
/// character of template syntax decides between truncation and a 500, so the
/// quoting hint is load-bearing rather than cosmetic — the same term `add`
/// (#2203) and `floatformat` already carry.
///
/// # Return shape
///
/// * `Ok(Some(n))` — parsed, or the argument was absent and `missing` applies.
/// * `Ok(None)` — unparseable under [`BadArg::ReturnInput`]; the caller must
///   return its input value unchanged.
/// * `Err(..)` — unparseable under [`BadArg::Raise`]. Surfaces to Python as
///   `RuntimeError: Template error: …` naming the filter and the argument,
///   through the same `?` chain every other filter error takes.
///
/// A MISSING argument is a `TemplateSyntaxError` at Django's PARSE time — a
/// different mechanism (arity, before any filter runs), so it is out of scope
/// here and each caller keeps its historical default.
///
/// # Why the dispatch table reaches this through a macro
///
/// `apply_builtin_filter` returns `Option<Result<Value>>`, so a `?` inside a
/// match arm would target the OPTION and not compile — the same trap the
/// `slice` arm's comment records. [`int_arg!`] is the one spelling of the
/// `Err` hand-off, so ten call sites cannot each invent their own (and get one
/// of them wrong, which is the shape of this whole issue).
pub(crate) fn filter_int_arg(
    filter_name: &str,
    arg: Option<&str>,
    arg_was_quoted: bool,
    arg_int_is_type_error: bool,
    missing: i64,
    on_bad: BadArg,
) -> Result<Option<i64>> {
    let Some(raw) = arg else {
        return Ok(Some(missing));
    };
    // #2366 generalizes what #2328 special-cased for one spelling. Every
    // `ReturnInput` filter's Django source catches `ValueError` ONLY, and
    // `int()` raises **TypeError** — which nothing catches — for anything that
    // is neither a string nor a number. `None` was the first such argument to
    // be noticed; a list, a tuple and a dict are the rest of the same rule,
    // and asking the argument's TYPE answers all of them at once. See
    // `int_arg_is_type_error` for what the string-valued dispatch boundary can
    // and cannot still see.
    if arg_int_is_type_error {
        return Err(int_type_error(filter_name));
    }
    match python_int_arg(raw, arg_was_quoted) {
        Some(n) => Ok(Some(n)),
        None => match on_bad {
            BadArg::ReturnInput => Ok(None),
            BadArg::Raise => Err(DjangoRustError::TemplateError(format!(
                "filter '{filter_name}' needs an integer argument, and int({raw:?}) \
                 is a ValueError — Django raises here too"
            ))),
        },
    }
}

/// Is this bare filter argument something Django resolves WITHOUT a context
/// lookup — so a lookup miss is not a `VariableDoesNotExist` (#2328)?
///
/// Three separate mechanisms in Django answer "yes", and all three had to be
/// enumerated or the raise would have broken working templates:
///
/// 1. **A numeric literal**, `django.template.base.Variable.__init__`:
///    ```python
///    if "." in var or "e" in var.lower():
///        self.literal = float(var)
///        if var[-1] == ".":  raise ValueError    # "2." is NOT a literal
///    else:
///        self.literal = int(var)
///    ```
///    That exact branch is why `7`, `-3`, `+3`, `7.5`, `.5`, `1e3`, `1_0` and
///    `07` are literals while `7.`, `0x10`, `nan` and `inf` are lookups —
///    verified cell by cell against Django 5.2.
/// 2. **`_("…")`**, the translation marker, which `Variable` unwraps to a
///    quoted literal.
///
/// `True` / `False` / `None` used to be listed here as a third mechanism, and
/// are NOT any more (#2347). They are not literals — they are keys of
/// `django.template.context.builtins` — and now that `Context::resolve`
/// carries them, `ctx.resolve("True")` answers `Some(Value::Bool(true))` and
/// the resolve-miss branch that consults this predicate is never reached for
/// them. Keeping the arm would have been a second mechanism shadowing the
/// first: unreachable, so no test could tell whether it or the resolution was
/// doing the work (#2233). Gating the resolution off now makes a bare `True`
/// argument RAISE, which is the correct signal that the two are coupled.
///
/// A QUOTED argument never reaches this — `arg_was_quoted` short-circuits the
/// whole resolution — so only the bare spellings are listed.
fn is_literal_filter_arg(a: &str) -> bool {
    if a.starts_with("_(") && a.ends_with(')') {
        return true;
    }
    if a.contains('.') || a.contains(['e', 'E']) {
        return python_float(a).is_some();
    }
    python_int(a).is_some()
}

/// Does `int(this argument)` raise **TypeError** rather than ValueError?
///
/// The distinction is observable, and it is the whole of #2366: every
/// `ReturnInput` filter's Django source catches `ValueError` ONLY —
///
/// ```python
/// def truncatechars(value, arg):
///     try:    length = int(arg)
///     except ValueError:  return value      # a TypeError escapes
/// ```
///
/// — so `{{ p|truncatechars:some_list }}` **raises** while
/// `{{ p|truncatechars:"nope" }}` returns its input. `int()` raises TypeError
/// for anything that is neither a string nor a number: `None`, a list, a
/// tuple, a dict, a model instance.
///
/// #2328 asked this question for the one spelling it had noticed, a bare
/// `None`, with a predicate named after that spelling. This is the same
/// question asked of the TYPE, which subsumes it: `None` resolves to
/// [`Value::None`] since #2347, so the resolved-value arm answers the `None`
/// case too and there is one mechanism rather than two on the same half.
///
/// # What the string boundary can still see, and what was lost before it
///
/// #2366 framed the choice as "either the dispatch table learns the argument's
/// original type, or this is a bounded wire residue". Measuring it shows the
/// dichotomy is false, and where the line actually falls is a finding:
///
/// * A list, a tuple and a dict reach [`Context::resolve`] as
///   [`Value::List`] / [`Value::Tuple`] / [`Value::Object`]. Their type is
///   intact at the resolution site — one line above where it used to be
///   stringified — so this predicate answers for them.
/// * A `datetime`, a `date`, a `time`, a `set` and an arbitrary object are
///   already [`Value::String`] by then. Their type was lost at the **PyO3
///   extraction boundary**, not at the dispatch table — `{{ q }}` on a
///   `datetime` renders 19 characters and `{{ q|length }}` answers 19. No
///   amount of threading below that boundary can recover it, which is why the
///   `datetime` the issue's own headline uses is the half that stays.
///
/// # Why there is no spelling fallback
///
/// A first pass carried one — `arg == Some("None") && !arg_was_quoted` — for
/// "the call sites that resolve nothing". Gating it off changed **nothing**:
/// every renderer call site passes `Some(context)` (pinned mechanically in
/// `python/tests/test_int_argument_type_2366.py::
/// TestEveryRendererCallSiteResolvesItsArgument`), so `resolved` is `None`
/// only when `arg_was_quoted` is true — and the fallback answered `false`
/// there by construction. A branch whose only correct output is `false` is a
/// second mechanism that can never fire, which CLAUDE.md's v1.1.1-2 rule says
/// to delete rather than test around. The structural pin is what keeps the
/// deletion safe, and unlike the dead branch it can actually go red.
pub(crate) fn int_arg_is_type_error(resolved: Option<&Value>) -> bool {
    match resolved {
        // Stated as what `int()` ACCEPTS, which is how CPython states it —
        // "a string, a bytes-like object or a real number" — rather than as
        // the list of things it refuses. Two reasons, and the second is the
        // load-bearing one:
        //
        // * it is the same sentence as the `TypeError` message, so a reader
        //   can check it against the interpreter without translating;
        // * a NEW `Value` variant then defaults to "`int()` refuses it", which
        //   is the conservative direction. The refusal list would default a
        //   new container to "`int()` accepts it" and silently return the
        //   input where Django raises — this bug, one variant later.
        Some(value) => !matches!(
            value,
            Value::String(_)
                | Value::Integer(_)
                | Value::Float(_)
                | Value::Bool(_)
                | Value::Decimal(_)
                | Value::BigInt(_)
        ),
        // Nothing was resolved, so the argument is a QUOTED literal — and
        // `int("None")` is an ordinary ValueError, which every caller's own
        // policy already handles.
        None => false,
    }
}

/// The error [`int_arg_is_type_error`] implies, worded once.
///
/// Two call sites — [`filter_int_arg`] and the `floatformat` arm, whose own
/// `parse_arg` returns an `Option` with no room for an error.
pub(crate) fn int_type_error(filter_name: &str) -> DjangoRustError {
    DjangoRustError::TemplateError(format!(
        "filter '{filter_name}' needs an integer argument, and int() of that \
         argument is a TypeError — Django raises here too, past its \
         except-ValueError"
    ))
}

/// The widest padding a template argument may ask `center`/`ljust`/`rjust` to
/// materialise.
///
/// Mirrors [`crate::floatformat::MAX_PLACES`] and exists for the same reason,
/// with one difference that raises the stakes: **a Rust allocation failure is a
/// process ABORT, not a catchable error.** Python's answers here are a
/// `MemoryError` or, past `ssize_t`, an `OverflowError` — both fail the render
/// and both are catchable. `String::repeat` has no such answer, so a width a
/// template can name must never reach an allocator unbounded.
///
/// One megabyte of padding is past any real template. Django will keep going
/// well beyond it (`ljust:"9999999999"` really does build a ten-gigabyte
/// string), so this is a deliberate divergence at widths nothing renders.
const MAX_PAD_WIDTH: i64 = 1_000_000;

/// A parsed width as a `usize`: clamped at zero, refused past [`MAX_PAD_WIDTH`].
///
/// `str.center(-5)`, `ljust(-5)` and `rjust(-5)` are all the string unchanged
/// in CPython, and so is a width of 0, so a negative collapses to 0 with no
/// loss. Takes the chokepoint's `Option` directly because the three width
/// filters all pass a `missing` default, so `None` is unreachable for them.
///
/// The upper guard is #2328's own regression, caught by a boundary test added
/// after review could not reproduce the panic this replaced. [`python_int`]
/// SATURATES past `isize` rather than failing — deliberately, because for
/// `slice` a magnitude past `isize` selects the same elements — so routing the
/// pad filters through it turned `{{ p|ljust:"99999999999999999999999" }}` from
/// a harmless width-0 no-op (the old `parse::<usize>()` simply failed) into a
/// request for `isize::MAX` spaces, which aborts the process. Saturation is
/// right for the parser and wrong for this consumer; the cap is where those
/// meet.
fn pad_width(filter_name: &str, parsed: Option<i64>) -> Result<usize> {
    let width = parsed.unwrap_or(0).max(0);
    if width > MAX_PAD_WIDTH {
        return Err(DjangoRustError::TemplateError(format!(
            "filter '{filter_name}' asks for a width of {width}, past djust's \
             {MAX_PAD_WIDTH} cap — Python answers a MemoryError or an OverflowError \
             for a width this large, and an unbounded Rust allocation would abort \
             the process rather than raise"
        )));
    }
    Ok(width as usize)
}

/// `int(arg)` for a filter argument, honouring the quoting hint.
///
/// [`python_int`] is CPython's `int(str)` — whitespace, a sign and `_` between
/// digits — and an UNQUOTED float literal additionally truncates, because
/// Django's `Variable` has already turned it into a Python `float` by the time
/// `int()` sees it.
/// A bare `True` / `False` argument as the INTEGER Python reads it (#2347).
///
/// `bool` IS an `int` in Python, and #2347 made djust's `Context` carry
/// Django's `True`/`False`/`None` builtins, so a bare `True` now RESOLVES —
/// but it resolves to a `Value::Bool`, and the built-in filter argument
/// channel is `Option<&str>`, so what arrives at a filter is `str(True)`, the
/// text `"True"`. Django's filter receives the object and `int(True)` is 1.
/// This is the rule that recovers the integer from the text.
///
/// **Why #2347 did not delete this.** The issue predicted that resolving the
/// builtins would make this coercion redundant. Measured after doing exactly
/// that, it does not: the resolved `Value::Bool(true)` is stringified back to
/// `"True"` at `apply_filter_full_safe`'s resolution site, so every built-in
/// still sees the text and nothing on the argument axis moved — 69 divergent
/// cells before the resolve fix, 69 after. Only the CUSTOM-filter channel
/// (`filter_registry`, which hands the resolved value to Python via
/// `into_pyobject`) gets the real `bool`. The gate-off proves it: reverting
/// this helper reddens argument cells with the builtins in place.
///
/// **One statement of the rule, two callers** (#1646). [`python_int_arg`] is
/// #2328's chokepoint for every numeric filter argument, and `add` has its own
/// `int()` — `int_digits_of`, which is arbitrary-precision because `add` must
/// carry a sum past `i64` (#2253/#2260) and so cannot share the chokepoint's
/// body. Before this helper existed only the chokepoint knew the bool rule,
/// which is why `{{ p|add:True }}` was 5 where Django says 6 while
/// `{{ p|center:True }}` was already right.
///
/// `None` is deliberately absent: `int(None)` is a `TypeError` in Python, so
/// falling through to the caller's own policy is the right answer.
fn bare_bool_arg_as_int(raw: &str, arg_was_quoted: bool) -> Option<i64> {
    if arg_was_quoted {
        // `int("True")` raises — a QUOTED argument is a `str` to Python, and
        // the coercion is for the BOOL, not for its name.
        return None;
    }
    match raw {
        "True" => Some(1),
        "False" => Some(0),
        _ => None,
    }
}

fn python_int_arg(raw: &str, arg_was_quoted: bool) -> Option<i64> {
    if let Some(n) = python_int(raw) {
        return Some(n as i64);
    }
    if arg_was_quoted {
        // `int("2.5")` raises: a quoted argument is a `str` to Python. So does
        // `int("True")` — the coercion below is for the BOOL, not its name.
        return None;
    }
    if let Some(n) = bare_bool_arg_as_int(raw, arg_was_quoted) {
        return Some(n);
    }
    // Django's `Variable.__init__` reads a bare `2.7` as a float only when it
    // contains a `.` or an `e`; `int()` then truncates toward zero.
    if !raw.contains('.') && !raw.contains(['e', 'E']) {
        return None;
    }
    match python_float(raw) {
        Some(f) if f.is_finite() => Some(f.trunc() as i64),
        _ => None,
    }
}

/// `float(x)` for the spellings a bare template literal can carry.
///
/// Rust's `parse::<f64>()` differs from Python's `float()` twice, and both
/// differences are reachable from a template: `float()` accepts `_` between
/// digits (`1_0.5`), and Rust accepts `inf`/`nan` spellings that Django's
/// `Variable` never treats as literals (it reaches `float()` only when the
/// text holds a `.` or an `e`, so `nan` and `inf` take the `int()` branch and
/// fail there).
fn python_float(raw: &str) -> Option<f64> {
    let t = raw.trim();
    let lower = t.to_ascii_lowercase();
    if lower.contains("inf") || lower.contains("nan") {
        return None;
    }
    // `_` is legal only between digits, exactly as in `python_int`.
    let bytes = t.as_bytes();
    let mut cleaned = String::with_capacity(t.len());
    for (i, &b) in bytes.iter().enumerate() {
        if b == b'_' {
            let prev = i > 0 && bytes[i - 1].is_ascii_digit();
            let next = bytes.get(i + 1).is_some_and(u8::is_ascii_digit);
            if !prev || !next {
                return None;
            }
        } else {
            cleaned.push(b as char);
        }
    }
    // Django's own guard: `float("2.")` succeeds in Python but `Variable`
    // rejects a trailing `.`, so `{{ x|add:2. }}` is a lookup, not a literal.
    if cleaned.ends_with('.') {
        return None;
    }
    cleaned.parse::<f64>().ok()
}

/// Django's `slice`: `value[slice(*bits)]`, where `bits` is
/// `[None if not x else int(x) for x in str(arg).split(":")]` and ANY of
/// `ValueError` / `TypeError` / `KeyError` returns the input unchanged.
///
/// It is a passthrough to Python, so every Python slice rule applies and none
/// of them can be approximated. The pre-#2326 code parsed at most two parts
/// and CLAMPED instead of wrapping, which got seven of ten specs wrong in the
/// two directions a template author notices: `{{ p|slice:":-1" }}` (drop the
/// last) rendered NOTHING and `{{ p|slice:"-3:" }}` (last three) rendered
/// EVERYTHING. Patching those two cases would have left the rest — a one-part
/// spec is `slice(stop)` so `"2"` means `[:2]` and not `[2:]`, a `:step` was
/// parsed and discarded, a negative step never reversed — so this reproduces
/// the algorithm rather than the answers (v1.1.1-2 retro: value-by-value fixes
/// on a semantics gap do not converge).
fn apply_slice(value: &Value, slice_str: &str) -> Result<Value> {
    let Some((start, stop, step)) = parse_slice_arg(slice_str) else {
        // Django's `except (ValueError, TypeError, KeyError): return value`.
        return Ok(value.clone());
    };

    match value {
        Value::String(s) => {
            let chars: Vec<char> = s.chars().collect();
            let picked: String = slice_positions(start, stop, step, chars.len())
                .into_iter()
                .map(|i| chars[i])
                .collect();
            Ok(Value::String(picked))
        }
        Value::List(items) | Value::Tuple(items) => {
            let picked: Vec<Value> = slice_positions(start, stop, step, items.len())
                .into_iter()
                .map(|i| items[i].clone())
                .collect();
            // Through `rebuild_like` on BOTH branches: `()` and `[]` are
            // different reprs, so the empty result is as shape-sensitive as
            // the populated one (#2321).
            Ok(rebuild_like(value, picked))
        }
        // Django slices whatever it is handed; an int, a float, `None`, a dict
        // and a bool all raise `TypeError` and come back unchanged.
        _ => Ok(value.clone()),
    }
}

/// `int(x)` as CPython spells it, for the subset a template literal can carry.
///
/// Rust's `str::parse::<isize>()` is NOT `int()`, and each difference below is
/// a real divergence confirmed against Django 5.2 rather than a hypothetical:
///
/// * surrounding whitespace is accepted — `slice:" 1 : 2 "` is `['b']`;
/// * a leading `+` is accepted — `slice:"+1:"` drops the first element;
/// * single underscores BETWEEN digits are accepted — `slice:"1_0:"` is
///   `int` 10, so on a 4-element list it is `[]`. Rejecting it would return
///   the input UNCHANGED, i.e. render every element where Django renders none;
/// * a value past `isize` is exact in Python, but every such value is also
///   past any representable `len`, so saturating at the bound picks the same
///   elements — `slice:"99999999999999999999999999:"` is `[]` either way.
///
/// Returns `None` for anything `int()` would raise on, which is the whole
/// filter's fail-silently path. Note a lone-space part is NOT empty and NOT an
/// int, so `slice:"1: "` returns the input unchanged.
fn python_int(s: &str) -> Option<isize> {
    let t = s.trim();
    let (negative, digits) = match t.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, t.strip_prefix('+').unwrap_or(t)),
    };
    if digits.is_empty() {
        return None;
    }
    // `int()` allows `_` only between digits: `_1`, `1_` and `1__0` all raise.
    let mut cleaned = String::with_capacity(digits.len());
    let bytes = digits.as_bytes();
    for (i, &b) in bytes.iter().enumerate() {
        if b == b'_' {
            let prev_digit = i > 0 && bytes[i - 1].is_ascii_digit();
            let next_digit = bytes.get(i + 1).is_some_and(u8::is_ascii_digit);
            if !prev_digit || !next_digit {
                return None;
            }
        } else if b.is_ascii_digit() {
            cleaned.push(b as char);
        } else {
            return None;
        }
    }
    // Saturate rather than fail: see the doc comment — a magnitude past `isize`
    // is past every `len`, so the bound selects the same elements.
    Some(match cleaned.parse::<isize>() {
        Ok(n) => {
            if negative {
                -n
            } else {
                n
            }
        }
        Err(_) => {
            if negative {
                isize::MIN
            } else {
                isize::MAX
            }
        }
    })
}

/// `str(arg).split(":")` into the three `slice()` arguments.
///
/// `None` means the whole filter fails silently, which covers every way
/// `slice(*bits)` or the indexing that follows raises: a non-integer part,
/// MORE than three parts (`slice()` takes at most three, a `TypeError`), and
/// a zero step (`ValueError: slice step cannot be zero`, raised by the
/// indexing rather than the constructor — which is why it belongs here and
/// not in `slice_positions`).
#[allow(clippy::type_complexity)]
fn parse_slice_arg(arg: &str) -> Option<(Option<isize>, Option<isize>, Option<isize>)> {
    let parts: Vec<&str> = arg.split(':').collect();
    if parts.len() > 3 {
        return None;
    }
    let mut bits: [Option<isize>; 3] = [None; 3];
    for (i, part) in parts.iter().enumerate() {
        if part.is_empty() {
            continue; // Python's `if not x: bits.append(None)`.
        }
        bits[i] = Some(python_int(part)?);
    }
    // One part is `slice(stop)`, not `slice(start)`: `{{ p|slice:"2" }}` is
    // `p[:2]`. The pre-#2326 code read it as the START and so returned the
    // complement of what Django returns.
    if parts.len() == 1 {
        return Some((None, bits[0], None));
    }
    if bits[2] == Some(0) {
        return None;
    }
    Some((bits[0], bits[1], bits[2]))
}

/// CPython's `PySlice_AdjustIndices` plus the walk `list[::step]` performs.
///
/// The one place the index math lives, shared by the `String` and the sequence
/// branch of [`apply_slice`] — those two duplicated it before #2326 and are
/// exactly the pair that would drift apart again (#1646).
fn slice_positions(
    start: Option<isize>,
    stop: Option<isize>,
    step: Option<isize>,
    len: usize,
) -> Vec<usize> {
    let step = step.unwrap_or(1);
    debug_assert!(step != 0, "a zero step is rejected by parse_slice_arg");
    let len = len as isize;

    // With a negative step the walk runs downwards, so the defaults and the
    // clamp bounds invert: index -1 is "before the start" and len-1 is the
    // first element visited.
    let (lower, upper) = if step < 0 { (-1, len - 1) } else { (0, len) };

    let adjust = |v: isize| {
        if v < 0 {
            (v.saturating_add(len)).max(lower)
        } else {
            v.min(upper)
        }
    };
    let start = match start {
        None => {
            if step < 0 {
                upper
            } else {
                lower
            }
        }
        Some(v) => adjust(v),
    };
    let stop = match stop {
        None => {
            if step < 0 {
                lower
            } else {
                upper
            }
        }
        Some(v) => adjust(v),
    };

    let mut out = Vec::new();
    let mut i = start;
    while if step < 0 { i > stop } else { i < stop } {
        // The adjust/default arms above keep `i` inside `0..len` for every
        // position actually visited, so this cast never truncates a real index.
        if i >= 0 && i < len {
            out.push(i as usize);
        }
        match i.checked_add(step) {
            Some(next) => i = next,
            None => break,
        }
    }
    out
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

/// What the ARGUMENT of `timesince`/`timeuntil` says to measure against (#2344).
///
/// Django's argument is the comparison INSTANT, not a format or a width:
///
/// ```python
/// def timesince_filter(value, arg=None):
///     if not value: return ""
///     try:
///         if arg: return timesince(value, arg)
///         return timesince(value)
///     except (ValueError, TypeError):
///         return ""
/// ```
///
/// and `timesince` itself does
/// `if now and not isinstance(now, datetime): now = datetime(now.year, ...)`,
/// which is an **AttributeError** — NOT in the caught pair — for anything that
/// is not a date. So the three outcomes below are Django's three, and nothing
/// else.
enum ComparisonInstant {
    /// The argument was absent or FALSY. Django's `if arg:` / `if not now:`
    /// both fall through to the wall clock.
    Now,
    /// A parsed instant, already in the value's own frame.
    At(chrono::NaiveDateTime),
    /// One side aware and the other naive. `now - d` is a **TypeError**, which
    /// the filter DOES catch, so Django renders the empty string.
    Incomparable,
}

/// Is this argument FALSY in the sense Django's `if arg:` means (#2344)?
///
/// The argument reaches the dispatch table as a STRING — a quoted literal
/// verbatim, or a resolved context value through `Value`'s `Display` — so
/// Python's truthiness has to be recovered from the text plus the quoting hint.
/// A QUOTED argument is a `str`, and every non-empty `str` is truthy: that is
/// why `{{ p|timesince:"0" }}` raises in Django while `{{ p|timesince:0 }}`
/// measures from now, one character of template syntax apart. `arg_was_quoted`
/// is the whole of that distinction, the same term `add` and `floatformat`
/// carry.
///
/// Unquoted, every falsy spelling `Display` can produce is here — and `Display`
/// has TWO modes, which is easy to miss because only one of them is the
/// default. `django_value_repr` (on by default, #2203) spells a bool
/// `True`/`False`; `legacy_display` spells it Rust's `true`/`false` and renders
/// `None` as the empty string. Both spellings are accepted, because a rule that
/// only knew the default would answer differently under a flag whose whole
/// point is rendering parity.
///
/// So: the empty string (`Value::String("")`, `Value::Missing`, and legacy
/// `None`), `None`, `False`/`false`, a numeric zero in any spelling, and the
/// empty containers.
///
/// What is NOT recoverable, in either mode, is a value whose `Display` text is
/// shared by a truthy and a falsy object. `legacy_display` renders EVERY
/// sequence as `[List]`, so an empty list is indistinguishable from a full one
/// there — and both are non-dates, so Django raises for one and measures from
/// now for the other. `TestTheFalsinessResidueIsNamed` states that rather than
/// hoping it away, and pins the `Display` arm enumeration so a new variant has
/// to be considered here.
fn timesince_arg_is_falsy(arg: &str, arg_was_quoted: bool) -> bool {
    if arg.is_empty() {
        return true; // `""` is falsy whether it was quoted or not.
    }
    if arg_was_quoted {
        return false; // Every other `str` is truthy to Python.
    }
    matches!(
        arg,
        "None"
            | "False"
            | "false"
            | "[]"
            | "{}"
            | "()"
            // An EMPTY dict VIEW (#2340). `bool({}.items())` is False, and
            // `Display` spells the three views by name. Reached by
            // `{{ then|timesince:d.items }}` on an empty dict, which is an
            // ordinary template line.
            | "dict_items([])"
            | "dict_keys([])"
            | "dict_values([])"
    ) || python_float(arg).is_some_and(|f| f == 0.0)
}

/// The error a truthy non-date argument implies, worded once (#2344).
///
/// Two call sites — the quoted short-circuit and the parse failure — and one
/// definition, because two copies of a rule is the shape this filter pair was
/// already an instance of.
fn non_date_argument_error(filter_name: &str, arg: &str) -> DjangoRustError {
    DjangoRustError::TemplateError(format!(
        "filter '{filter_name}' compares against its argument, and {arg:?} is not a \
         date or datetime — Django raises AttributeError here, past its \
         except-(ValueError, TypeError)"
    ))
}

fn timesince_comparison_instant(
    filter_name: &str,
    arg: Option<&str>,
    arg_was_quoted: bool,
    value_dt: DateTime<chrono::FixedOffset>,
    value_aware: bool,
) -> Result<ComparisonInstant> {
    let Some(raw) = arg else {
        return Ok(ComparisonInstant::Now);
    };
    if timesince_arg_is_falsy(raw, arg_was_quoted) {
        return Ok(ComparisonInstant::Now);
    }
    // A QUOTED argument is a `str`, and Django accepts NO string here — not
    // even a date-shaped one, measured: `{{ p|timesince:"2020-01-01 15:30:00" }}`
    // is `AttributeError: 'SafeString' object has no attribute 'year'`.
    //
    // The rest of this function reads a date-SHAPED string as a datetime, and
    // that is not an inconsistency: it exists because a Python `datetime`
    // crosses into Rust as a string and has no other spelling — the same
    // convention the VALUE side has carried since #2203. A quoted literal is
    // authored template text that never came from Python, so the convention has
    // nothing to justify for it and Django's exact answer applies.
    if arg_was_quoted {
        return Err(non_date_argument_error(filter_name, raw));
    }
    // Truthy and not a date: Django's `now.year` raises AttributeError, which
    // `except (ValueError, TypeError)` does not catch. A raise here rather than
    // a plausible-looking duration is the whole point of #2344 — the previous
    // arms discarded the argument entirely, so `{{ then|timesince:"whenever" }}`
    // silently answered "since now".
    let Some((arg_dt, arg_aware, _time_only)) = parse_serialized_datetime(raw, false) else {
        return Err(non_date_argument_error(filter_name, raw));
    };
    if arg_aware != value_aware {
        // Django reconciles two AWARE operands with `now.astimezone(d.tzinfo)`
        // and does nothing at all for a mixed pair, so the subtraction raises
        // TypeError — caught, and the filter renders "".
        return Ok(ComparisonInstant::Incomparable);
    }
    Ok(ComparisonInstant::At(if value_aware {
        arg_dt.with_timezone(value_dt.offset()).naive_local()
    } else {
        arg_dt.naive_utc()
    }))
}

/// `timesince` and `timeuntil`, which differ only in the direction (#2344).
///
/// One body, because they are one computation in Django too — `timeuntil(d,
/// now)` is `timesince(d, now, reversed=True)` — and because the argument rule
/// they share is exactly the kind of thing that drifts when it is written twice
/// (#1646). Ordering matters and is Django's:
///
/// 1. the VALUE is read first. Django's `timesince` normalizes `d` before it
///    touches `now`, and an unreadable value here falls soft to the value
///    unchanged (djust's convention, #2227) WITHOUT the argument getting to
///    decide anything. Same ordering rule `floatformat` carries (#2328).
/// 2. then the argument, whose three outcomes are [`ComparisonInstant`]'s.
fn timesince_or_until(
    filter_name: &str,
    value: &Value,
    arg: Option<&str>,
    arg_was_quoted: bool,
    reversed: bool,
) -> Result<Value> {
    // Django's first statement, and the only half of this function that is not
    // a decision about an unreadable value (#2399):
    //
    //     if not value:
    //         return ""
    //
    // Python truthiness, so `""`, `None`, `0`, `0.0`, `Decimal("0")`, `False`,
    // `[]` and `{}` all land here — as does an ABSENT variable, which Django
    // has already replaced with `string_if_invalid` (`""`).
    if !value.is_truthy() {
        return Ok(Value::String(String::new()));
    }
    let datetime_str = value.to_string();
    // `allow_time_only = false`: a bare time has no instant to measure from
    // (#2227).
    //
    // This used to `return Ok(value.clone())` — it ECHOED the input onto the
    // page where Django emits nothing at all (#2399). Django's body is
    //
    //     try:                          return timesince(value)
    //     except (ValueError, TypeError): return ""
    //
    // and `timesince()` reaches `value.year` on its first line, so a truthy
    // non-datetime raises **AttributeError**, which NEITHER `except` catches.
    // Django refuses the render.
    //
    // Returning `""` here — mirroring what `date` and `time` do (#2383) —
    // would have been a THIRD answer, neither the echo's nor Django's, for
    // every truthy row. So this refuses, the way `{% for %}`'s unpack-arity
    // check does (#2387): the error crosses PyO3 as `RuntimeError` rather than
    // Django's `AttributeError`, as every djust render error does, and the
    // property both engines then share is that neither puts a page up.
    //
    // The falsy half is above and is unambiguous either way.
    //
    // The message names the FILTER and not the value. Every other raise in
    // this module quotes the template-authored ARGUMENT, which the page author
    // wrote; the value here is application data, and an error string travels
    // to logs and to `LiveViewConsumer`'s error frame.
    let Some((dt, aware, _time_only)) = parse_serialized_datetime(&datetime_str, false) else {
        return Err(DjangoRustError::TemplateError(format!(
            "filter '{filter_name}' needs a date, and its input is not one — Django \
             raises AttributeError here (timesince() reads value.year, which neither \
             of its excepts catches)"
        )));
    };
    let (then, now) =
        match timesince_comparison_instant(filter_name, arg, arg_was_quoted, dt, aware)? {
            ComparisonInstant::Now => now_and_then(dt, aware),
            ComparisonInstant::At(instant) => (
                if aware {
                    dt.naive_local()
                } else {
                    dt.naive_utc()
                },
                instant,
            ),
            // Django's caught TypeError: the filter renders "".
            ComparisonInstant::Incomparable => return Ok(Value::String(String::new())),
        };
    if !reversed {
        return Ok(Value::String(django_timesince(then, now)));
    }
    // A value that is not in the future yields `0 minutes`.
    if then <= now {
        return Ok(Value::String(zero_minutes()));
    }
    Ok(Value::String(django_timesince(now, then)))
}

/// The NON-BREAKING SPACE Django joins a file size to its unit with.
///
/// Spelled as an escape rather than as a literal U+00A0 in the source on
/// purpose: the two are visually identical, so a literal one is silently
/// destroyed by an editor that normalises whitespace, by a copy-paste through a
/// terminal, or by a well-meaning "trailing whitespace" fixer — and the
/// resulting output looks *exactly right* while being the wrong bytes. Same
/// character, same reasoning, as `timesince` (#2228).
const NBSP: &str = "\u{00A0}";

/// `{{ value|filesizeformat }}`, as `django/template/defaultfilters.py` writes
/// it (#2264).
///
/// The previous implementation diverged on EVERY value, on five independent
/// axes, of which the `as_f64` parse the issue was filed against is the only one
/// that needs an unusual input to see:
///
/// 1. **The separator.** Django ends with `avoid_wrapping(value)`, which is
///    `value.replace(" ", "\xa0")` — the number and the unit are joined by a
///    NON-BREAKING space. Every cell differed by this one byte, and a test
///    written with an ordinary space passes while shipping the wrong one. See
///    [`NBSP`].
/// 2. **Pluralization.** `ngettext("%(size)d byte", "%(size)d bytes", bytes_)`
///    says `1 byte`, not `1 bytes`.
/// 3. **Negatives.** Django takes the absolute value, formats that, and puts the
///    `-` back — so `-1024` is `-1.0 KB`. The old signed `bytes < KB` comparison
///    sent every negative into the bytes branch, rendering `-1024 bytes`.
/// 4. **Coercion.** Django's first statement is `int(bytes_)`, catching
///    `TypeError`/`ValueError`/`UnicodeDecodeError` into `0 bytes`. So `"1024"`
///    is `1.0 KB`, `None` and `"abc"` and a list are `0 bytes`, and `True` is
///    `1 byte`. The old version returned the value UNCHANGED for every
///    non-numeric type, so `{{ p|filesizeformat }}` rendered `None`.
/// 5. **Localization.** The `KB`-and-up branch formats through
///    `formats.number_format(round(value, 1), 1)`, which honours the active
///    locale's decimal separator AND `USE_THOUSAND_SEPARATOR` grouping —
///    measured: `de` gives `1,5 GB`, and grouping gives `1,024.0 KB`. The
///    `bytes` branch does NOT: it is a raw `%d`.
///
/// ## Two divergences that remain, deliberately
///
/// * **The unit names are translated by Django** and not here: `fr` renders
///   `1,5 Gio`, not `1,5 GB`. That is `gettext`, which the Rust engine has no
///   catalogue for; it is the same gap `{% trans %}` has and is not specific to
///   this filter.
/// * **Past `i128`** — about 1.7e38 bytes — this gives up and renders
///   `0 bytes`, where Python's unbounded ints keep counting. The same ceiling
///   `add` documents, reached only by a `Decimal` with a huge exponent.
fn format_filesize(value: &Value) -> String {
    let Some(bytes) = filesize_to_int(value) else {
        // `except (TypeError, ValueError, UnicodeDecodeError)` — Django formats
        // ZERO here, it does not echo the input.
        return avoid_wrapping(&format!("0 {}", byte_unit(0)));
    };

    const KB: u128 = 1 << 10;
    const MB: u128 = 1 << 20;
    const GB: u128 = 1 << 30;
    const TB: u128 = 1 << 40;
    const PB: u128 = 1 << 50;

    // `negative = bytes_ < 0; if negative: bytes_ = -bytes_`. `unsigned_abs`
    // rather than `-bytes` because `i128::MIN` has no positive counterpart.
    let negative = bytes < 0;
    let magnitude = bytes.unsigned_abs();

    let scaled = |unit: u128| filesize_number_format(magnitude as f64 / unit as f64);
    let formatted = if magnitude < KB {
        format!("{magnitude} {}", byte_unit(magnitude))
    } else if magnitude < MB {
        format!("{} KB", scaled(KB))
    } else if magnitude < GB {
        format!("{} MB", scaled(MB))
    } else if magnitude < TB {
        format!("{} GB", scaled(GB))
    } else if magnitude < PB {
        format!("{} TB", scaled(TB))
    } else {
        format!("{} PB", scaled(PB))
    };

    let signed = if negative {
        format!("-{formatted}")
    } else {
        formatted
    };
    avoid_wrapping(&signed)
}

/// `django.utils.html.avoid_wrapping` — `value.replace(" ", "\xa0")`.
///
/// EVERY space, not just the one before the unit. Django applies it to the whole
/// rendered string, so mirroring the whole-string replace (rather than
/// formatting an [`NBSP`] directly into the one place it is currently needed)
/// stays correct if the value ever grows a second space.
fn avoid_wrapping(value: &str) -> String {
    value.replace(' ', NBSP)
}

/// `ngettext("%(size)d byte", "%(size)d bytes", n)` for the English catalogue.
fn byte_unit(n: u128) -> &'static str {
    if n == 1 {
        "byte"
    } else {
        "bytes"
    }
}

/// Python's `int(x)` over a [`Value`], or `None` for what Django catches.
///
/// `i128`, not `i64`: `int(Decimal('12345678901234567890.123456789'))` is the
/// exact 20-digit integer, and routing it through `as_f64() as i64` saturated at
/// `i64::MAX` and rendered `8192.0 PB` where Django renders `10965.2 PB` — the
/// third cause #2264 reported. The `Decimal` arm delegates to
/// `decimal::to_i128_trunc` rather than re-deriving the truncation (#1646).
fn filesize_to_int(value: &Value) -> Option<i128> {
    match value {
        Value::Integer(n) => Some(*n as i128),
        // `int(True)` is 1. Django reaches this before any string handling.
        Value::Bool(b) => Some(i128::from(*b)),
        Value::Float(f) => {
            // `int(float)` truncates toward zero; `int(nan)` raises ValueError
            // and `int(inf)` raises OverflowError — which Django does NOT catch,
            // so it propagates as a 500. A filter here cannot raise, so both
            // land on the `0 bytes` fallback.
            // The bound is `i128::MAX` as a double — the largest magnitude the
            // `as i128` below can carry without saturating. NaN fails the range
            // test on its own, so `is_finite` is not spelled separately.
            const I128_MAX_AS_F64: f64 = 1.7014118346046923e38;
            let truncated = f.trunc();
            if (-I128_MAX_AS_F64..=I128_MAX_AS_F64).contains(&truncated) {
                Some(truncated as i128)
            } else {
                None
            }
        }
        Value::Decimal(d) => djust_core::decimal::parse_decimal_parts(d.trim())?.to_i128_trunc(),
        // `int()` of an int is itself (#2260). Without this arm a `BigInt` fell
        // to the wildcard and every value past `i64` rendered `0 bytes`, where
        // before the variant it had arrived as a `Float` and got a real answer.
        //
        // Past `i128` — 39 digits — the digits do not fit either, and falling
        // straight to `0 bytes` there is a regression at exactly `2**127`, which
        // a set comparison against `main` caught as two cells. So the overflow
        // delegates to the `Float` arm above, which is what the value used to
        // take: one definition of that bound rather than a second copy of the
        // constant (#1646). Beyond what a double can carry, both give up
        // together, as they did before.
        Value::BigInt(d) => d.parse::<i128>().ok().or_else(|| {
            d.parse::<f64>()
                .ok()
                .and_then(|f| filesize_to_int(&Value::Float(f)))
        }),
        Value::String(s) => python_int_from_str(s),
        // `int(None)`, `int([1, 2])`, `int({'a': 1})` — all TypeError.
        _ => None,
    }
}

/// Python's `int(str)`: surrounding whitespace, an optional sign, then digits
/// that may be separated by single underscores (`int("1_024")` is 1024).
///
/// Rejects `"19.99"` — Python's `int()` does not parse a decimal point, which is
/// why Django renders `0 bytes` for it and not `19 bytes`.
fn python_int_from_str(raw: &str) -> Option<i128> {
    let trimmed = raw.trim();
    let (neg, digits) = match trimmed.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, trimmed.strip_prefix('+').unwrap_or(trimmed)),
    };
    let mut cleaned = String::with_capacity(digits.len());
    let mut prev_was_digit = false;
    for ch in digits.chars() {
        if ch.is_ascii_digit() {
            cleaned.push(ch);
            prev_was_digit = true;
        } else if ch == '_' && prev_was_digit {
            // A separator must sit BETWEEN digits: `int("_1")` and `int("1__2")`
            // are both ValueError.
            prev_was_digit = false;
        } else {
            return None;
        }
    }
    // Also rejects an empty string and a bare sign — neither ends on a digit.
    if !prev_was_digit {
        return None;
    }
    let n = cleaned.parse::<i128>().ok()?;
    Some(if neg { -n } else { n })
}

/// Django's inner `filesize_number_format`:
/// `formats.number_format(round(value, 1), 1)`.
///
/// Three steps, each of which the naive `format!("{v:.1}")` gets wrong for some
/// input:
///
/// 1. `round(value, 1)` — correctly rounded to one decimal, ties to EVEN
///    (`2.25` KB is `2.2`, not `2.3`). Rust's `{:.1}` agrees on the rule, so a
///    round trip through a formatted parse is the cheapest faithful spelling.
/// 2. `str(...)` — Python's float repr, which switches to an EXPONENT for large
///    magnitudes; `numberformat.format` then re-reads that through
///    `Decimal(str(number))` and expands it with `"{:f}"`. The distinction is
///    real rather than theoretical: `{:.1}` prints the double's EXACT binary
///    expansion (`151115727451828646838272.0`) where Python prints the shortest
///    repr expanded (`151115727451828650000000.0`). `Value::Decimal`'s `Display`
///    IS that `"{:f}"` expansion, so it is reused rather than re-derived.
/// 3. `decimal_pos=1` — truncate the fraction to one place, then pad it to one.
///
/// Finally the digits go through the active locale, which is what makes `de`
/// render `1,5 GB` and `USE_THOUSAND_SEPARATOR` render `1,024.0 KB`.
fn filesize_number_format(value: f64) -> String {
    let rounded: f64 = format!("{value:.1}").parse().unwrap_or(value);
    let expanded = Value::Decimal(djust_core::decimal::python_float_repr(rounded)).to_string();

    let (int_part, dec_part) = match expanded.split_once('.') {
        Some((i, d)) => (i, d),
        None => (expanded.as_str(), ""),
    };
    // `dec_part = dec_part[:1]`, then `+= "0" * (1 - len(dec_part))` — take the
    // first decimal digit, or pad with a zero when there is none.
    let fraction = dec_part.chars().next().unwrap_or('0');
    djust_core::locale::localize_number(&format!("{int_part}.{fraction}"))
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
/// Django's answer when the value is not a date — which is NOT always `""`.
///
/// `dateformat.Formatter.format` splits the format string on UNESCAPED
/// specifier characters and touches the value only when it reaches one:
///
/// ```python
/// re_formatchars = re.compile(r"(?<!\\)([aAbcdDeEfFgGhHiIjlLmMnNoOPrsStTUuwWyYzZ])")
/// re_escaped = re.compile(r"\\(.)")
///
/// for i, piece in enumerate(re_formatchars.split(str(formatstr))):
///     if i % 2:
///         pieces.append(str(getattr(self, piece)()))   # AttributeError here
///     elif piece:
///         pieces.append(re_escaped.sub(r"\1", piece))
/// ```
///
/// So a format carrying **no** specifier never raises, and its literal text
/// comes back for a value the filter otherwise refuses: `{{ 0|date:"1-1" }}`
/// is `'1-1'` in Django and `{{ 0|date:"," }}` is `','`. The first specifier
/// raises `AttributeError`, which the filter swallows — discarding everything
/// accumulated before it — so it is all-or-nothing.
///
/// Found by the #2359 randomised sweep, not by reading the source: the fix's
/// first pass returned a flat `""` here and 296 of 4,000 cells disagreed.
///
/// **The specifier test is positional, not semantic.** The regex lookbehind is
/// `(?<!\)`, so a character preceded by a backslash is not a specifier *even
/// when that backslash was itself escaped*: `"\\Y"` carries no specifier and
/// renders `\Y`. Deciding it by "is this backslash an escape" instead gives
/// `""` there, which is the one case a hand-rolled unescape-then-scan gets
/// wrong.
///
/// `None` and `""` never reach the formatter at all — `if value in (None, "")`
/// is the filter's first line — so they answer `""` whatever the format says.
fn django_literal_only_format(value: &Value, format_str: &str) -> String {
    if matches!(value, Value::None | Value::Missing)
        || matches!(value, Value::String(s) if s.is_empty())
    {
        return String::new();
    }
    const SPECIFIERS: &str = "aAbcdDeEfFgGhHiIjlLmMnNoOPrsStTUuwWyYzZ";
    let chars: Vec<char> = format_str.chars().collect();
    for (i, ch) in chars.iter().enumerate() {
        if SPECIFIERS.contains(*ch) && (i == 0 || chars[i - 1] != '\\') {
            return String::new();
        }
    }
    // `re_escaped.sub(r"\1", piece)` over the whole string, which is one
    // piece precisely because no specifier split it.
    let mut out = String::with_capacity(format_str.len());
    let mut i = 0;
    while i < chars.len() {
        if chars[i] == '\\' && i + 1 < chars.len() {
            out.push(chars[i + 1]);
            i += 2;
        } else {
            out.push(chars[i]);
            i += 1;
        }
    }
    out
}

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

        compare_sort_values(&a_val, &b_val)
    });

    sorted_items
}

/// The one ordering used by every `dictsort` path (#1646).
fn compare_sort_values(a_val: &Value, b_val: &Value) -> std::cmp::Ordering {
    {
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
    }
}

/// Django's `_property_resolver(arg)` applied to every item, reporting only
/// whether it would RAISE — which is the half `dictsort` needs and the half
/// djust never had.
///
/// ```python
/// def _property_resolver(arg):
///     try:
///         float(arg)
///     except ValueError:
///         if VARIABLE_ATTRIBUTE_SEPARATOR + "_" in arg or arg[0] == "_":
///             raise AttributeError("Access to private variables is forbidden.")
///         ...  # dotted attribute path
///     else:
///         return itemgetter(arg)
/// ```
///
/// A NUMERIC key is `operator.itemgetter(n)` — it INDEXES, so `dictsort:0` over
/// a list of strings sorts them by first character and does NOT raise. That
/// case is why this cannot simply refuse every non-`Object` item: doing so
/// would turn a cell that agrees with Django into `""`.
///
/// A key that resolves for no item is Django's raise, and the caller returns
/// `""` there. Deliberately NARROWER than Django in one direction: djust does
/// not walk dotted paths, so `dictsort:"a.b"` over a dict-of-dicts answers
/// `None` here where Django would sort. That is the fail-CLOSED direction (the
/// value is discarded rather than emitted), and it is the same answer djust
/// already gave for the security-relevant shapes.
fn dictsort_by_key(items: &[Value], key: &str) -> Option<Vec<Value>> {
    // Django's `_property_resolver` refuses private access before touching any
    // item, so it raises even for an empty sequence.
    if key.starts_with('_') || key.contains("._") {
        return None;
    }
    // `itemgetter(key)` / `getattr` raises for any item that cannot resolve it,
    // and one raise discards the WHOLE sequence.
    for item in items {
        if matches!(get_dict_value(item, key), Value::Missing) {
            return None;
        }
    }
    Some(sort_dicts_by_key(items, key))
}

/// `operator.itemgetter(n)` — an UNQUOTED integer argument INDEXES.
///
/// djust previously returned the sequence unsorted here, because
/// `sort_dicts_by_key` resolves through `get_dict_value`, which answers
/// `Missing` for every non-`Object` and so compared every pair Equal.
/// `{{ ['ba','ab']|dictsort:0 }}` is `['ab', 'ba']` in Django and was
/// `['ba', 'ab']` here.
fn dictsort_by_index(items: &[Value], n: usize) -> Option<Vec<Value>> {
    let mut keyed: Vec<(Value, Value)> = Vec::with_capacity(items.len());
    for item in items {
        let k = match item {
            Value::String(s) => s.chars().nth(n).map(|c| Value::String(c.to_string())),
            Value::List(v) | Value::Tuple(v) => v.get(n).cloned(),
            // `itemgetter(0)` on a str-keyed dict is a `KeyError`, and on a
            // scalar a `TypeError`. Both are Django's raise.
            _ => None,
        }?;
        keyed.push((k, item.clone()));
    }
    keyed.sort_by(|a, b| compare_sort_values(&a.0, &b.0));
    Some(keyed.into_iter().map(|(_, item)| item).collect())
}

fn get_dict_value(value: &Value, key: &str) -> Value {
    match value {
        Value::Object(map) => map.get(key).cloned().unwrap_or(Value::Missing),
        _ => Value::Missing,
    }
}

/// `django.utils.text.normalize_newlines` — `re.sub(r"\r\n|\r|\n", "\n", ...)`.
///
/// Both `linebreaks` and `linebreaksbr` call it first, so `a\r\nb` renders
/// `a<br>b` and not `a\r<br>b` (#2259). `linenumbers` deliberately does NOT —
/// Django splits it on a bare `"\n"`.
fn normalize_newlines(s: &str) -> String {
    s.replace("\r\n", "\n").replace('\r', "\n")
}

/// `re.split(r"\n{2,}", value)` — split on a run of TWO OR MORE newlines.
///
/// Not `split("\n\n")`: `a\n\n\nb` is ONE separator to Django and two to a
/// literal split, which is how the old implementation grew a spurious
/// `<p><br>b</p>`. Runs are consumed whole, and empty pieces are KEPT (Django
/// does not filter them — `""` is one empty paragraph, and `"\n\n"` is two).
///
/// Byte indexing is safe because `\n` is ASCII, so every boundary it produces is
/// a `char` boundary.
fn split_on_blank_lines(s: &str) -> Vec<&str> {
    let bytes = s.as_bytes();
    let mut out = Vec::new();
    let mut start = 0usize;
    let mut i = 0usize;
    while i < bytes.len() {
        if bytes[i] != b'\n' {
            i += 1;
            continue;
        }
        let run_start = i;
        while i < bytes.len() && bytes[i] == b'\n' {
            i += 1;
        }
        if i - run_start >= 2 {
            out.push(&s[start..run_start]);
            start = i;
        }
    }
    out.push(&s[start..]);
    out
}

/// `{{ value|linebreaks }}` — `django.utils.html.linebreaks`, then `mark_safe`
/// (#2259), with the `autoescape` argument #2284 made real.
///
/// **This filter escapes its own input UNLESS that input was already
/// `SafeData`, and that is what earns it a place in
/// `renderer::SAFE_OUTPUT_FILTERS`.** (Read the `# autoescape` section below
/// before changing either half — the exemption rests on both arms, not on the
/// escape alone.) Django's registration is
/// `@register.filter("linebreaks", is_safe=True, needs_autoescape=True)`, whose
/// body is `mark_safe(linebreaks(value, autoescape))` — the markup it builds is
/// exempt from escaping precisely BECAUSE `escape(p)` has already been applied
/// to every paragraph of the user's text. Marking the output safe without that
/// inner escape would turn `{{ comment|linebreaks }}` into an XSS sink; the two
/// halves are one change and must never be separated.
///
/// # `autoescape` (#2284)
///
/// Django's registration is `needs_autoescape=True`, and the filter body opens
/// `autoescape = autoescape and not isinstance(value, SafeData)`. The engine has
/// no `{% autoescape %}` block (the tag is rejected by the parser), so the first
/// term is pinned true and the caller passes `!input_safety.container` — Django's
/// `SafeData` clause, which #2259 left unreproduced and #2284 closed. When it is
/// `false` the paragraph text is emitted verbatim, exactly as
/// `django.utils.html.linebreaks(value, autoescape=False)` does, so
/// `{{ p|safe|linebreaks }}` on `<b>x</b>` renders `<p><b>x</b></p>` rather than
/// escaping markup the view deliberately marked safe.
///
/// **The `SAFE_OUTPUT_FILTERS` membership survives that**, and the reason it
/// does is worth stating because it changed: the output is exempt from
/// auto-escaping because it is safe under BOTH arms — either this filter
/// escaped the input itself (`autoescape = true`, the hostile-input case), or
/// the input was already `SafeData` and the caller took responsibility for it
/// (`autoescape = false`). A value that was never marked safe still takes the
/// escape, so nothing that reaches a page unescaped today reaches it unescaped
/// after #2284.
///
/// Four defects beyond the escaping, all of which made `''` and multi-paragraph
/// text render wrong:
///
/// * paragraphs were split on a literal `"\n\n"` rather than `\n{2,}`;
/// * they were joined with `"\n"` where Django joins with `"\n\n"`;
/// * empty paragraphs were FILTERED OUT, so `''` rendered `''` where Django
///   renders `<p></p>`;
/// * `\r\n` was not normalized.
fn linebreaks(s: &str, autoescape: bool) -> String {
    let normalized = normalize_newlines(s);
    split_on_blank_lines(&normalized)
        .iter()
        // `"<p>%s</p>" % escape(p).replace("\n", "<br>")`. Escape FIRST, as
        // Django does — `escape` leaves `\n` alone, so the order is not
        // load-bearing, but mirroring it keeps the two readable side by side.
        .map(|p| {
            let body = if autoescape {
                html_escape(p)
            } else {
                (*p).to_string()
            };
            format!("<p>{}</p>", body.replace('\n', "<br>"))
        })
        .collect::<Vec<_>>()
        .join("\n\n")
}

/// `{{ value|linebreaksbr }}` — the same contract as [`linebreaks`] (#2259),
/// including its `needs_autoescape` handling (#2284).
///
/// The issue listed this one as a neighbour to *check* rather than to fix; it
/// diverges too, on both axes. It escapes its input and is `is_safe=True` in
/// Django exactly like `linebreaks`, and it needs the same
/// `normalize_newlines` — a single-line differential misses it only because it
/// emits no tag at all until the input contains a newline.
fn linebreaksbr(s: &str, autoescape: bool) -> String {
    let normalized = normalize_newlines(s);
    let body = if autoescape {
        html_escape(&normalized)
    } else {
        normalized
    };
    body.replace('\n', "<br>")
}

/// `django.utils.html.strip_tags`, ported in `crate::htmlparser` (#2273).
///
/// The scan this replaced treated EVERY `<` as opening a tag, so `a < b`
/// rendered as `a ` -- everything from the `<` to the next `>` (or to the end
/// of the input) was deleted. Django runs a real `HTMLParser`, which emits a
/// `<` that is not followed by a letter / `/` / `!` / `?` as data, and repeats
/// the strip until the tag count stops falling.
fn strip_tags(s: &str) -> String {
    crate::htmlparser::strip_tags(s)
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

/// Is this string safe to emit as a bare JSON integer literal?
///
/// JSON's `int` grammar: an optional `-`, then either `0` alone or a non-zero
/// leading digit. `str(int)` always satisfies it; a forged binary tag need not,
/// which is the whole reason this exists (see [`value_to_json`]).
fn is_json_int_literal(s: &str) -> bool {
    let body = s.strip_prefix('-').unwrap_or(s);
    if !body.bytes().all(|b| b.is_ascii_digit()) || body.is_empty() {
        return false;
    }
    // No leading zeros: `-0`, `007` and `0` are not all legal, and `str(int)`
    // emits none of the illegal ones.
    body == "0" || !body.starts_with('0')
}

/// A float the way `json.dumps` writes one — which is `repr`, then three
/// names JSON's grammar does not have (#2270).
///
/// CPython's encoder is `float.__repr__` for a finite value and the literal
/// strings `NaN` / `Infinity` / `-Infinity` otherwise, because `json.dumps`
/// defaults to `allow_nan=True` and `json_script` does not override it —
/// `django.utils.html.json_script` calls `json.dumps(value, cls=encoder or
/// DjangoJSONEncoder)`, and `DjangoJSONEncoder` overrides only `default()`.
///
/// **The non-finite spellings are a decision, not a transcription, and this is
/// the reasoning.** `Infinity` and `NaN` are Python extensions to JSON; neither
/// is in ECMA-404, and `JSON.parse('{"x": Infinity}')` throws
/// `SyntaxError: Unexpected token 'I'` in every browser. So djust emitting them
/// puts a body in `<script type="application/json">` that the client cannot
/// parse. The alternative — `null`, which is what `JSON.stringify` does — is
/// valid JSON and is rejected here for two reasons:
///
/// 1. **It is silently lossy where `Infinity` is loudly wrong.** A client
///    reading `null` cannot tell an infinity from a `None`; a client reading
///    `Infinity` gets an exception at the parse site with the offending token
///    named. And Python's own `json.loads` accepts `Infinity`, so a body
///    round-tripped server-side still carries the value.
/// 2. **djust would be the only one of the two that changed the data.** Django
///    is the contract this engine reproduces; a `json_script` that answers
///    `null` where Django answers `Infinity` is a divergence a user cannot see
///    until the value differs, which is the failure shape this whole drain
///    exists to remove.
///
/// This is deliberately NOT the #2241 reasoning, which also concerned invalid
/// JSON in a script body, because the two cases differ in both halves. There,
/// Django emitted VALID JSON (`json.dumps` escapes a control character in a
/// key) and djust did not, so parity and validity pointed the same way; and the
/// mechanism was structure INJECTION — an unescaped `"` or newline in an
/// attacker-reachable key ends the string and starts new JSON. `Infinity` is a
/// fixed six-byte token chosen by this function from the float's own class,
/// carries no attacker payload, and cannot inject structure. Divergence was the
/// defect in #2241; here parity is what a strict parser rejects, so the
/// consequence is recorded — `test_infinity_is_django_parity_and_is_not_`
/// `parseable_json` in `python/tests/test_pprint_json_script_float_2270.py` —
/// rather than papered over.
fn json_float_body(f: f64) -> String {
    if f.is_nan() {
        return "NaN".to_string();
    }
    if f.is_infinite() {
        return if f < 0.0 { "-Infinity" } else { "Infinity" }.to_string();
    }
    djust_core::decimal::python_float_repr(f)
}

fn value_to_json(value: &Value) -> String {
    match value {
        // Both are JSON `null`: JSON cannot distinguish absent from None.
        Value::Missing | Value::None => "null".to_string(),
        // A dict view is NOT JSON-serializable: `json.dumps(d.keys())` raises
        // `TypeError`, and so does Django's `{{ d.keys|json_script:"i" }}`.
        // `null` is the closest honest answer — the `json_script` arm refuses
        // the whole filter before reaching here, so this is only the nested
        // case (#2340).
        Value::DictView { .. } => "null".to_string(),
        Value::Bool(b) => {
            if *b {
                "true".to_string()
            } else {
                "false".to_string()
            }
        }
        Value::Integer(n) => n.to_string(),
        // `json.dumps`'s float, which is `repr` plus three non-finite names —
        // see [`json_float_body`] for why `Infinity` is emitted knowing
        // `JSON.parse` rejects it (#2270). Rust's `{}` was wrong on BOTH
        // halves: it wrote `1.0` as `1`, changing a JSON float into a JSON
        // integer for every integral value, and spelled the infinities `inf`,
        // which is neither valid JSON nor what Django emits.
        Value::Float(f) => json_float_body(*f),
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
        // `json.dumps(12345678901234567890)` is a BARE number with every digit —
        // JSON's grammar has no precision ceiling, and quoting it would change
        // the type client code reads (#2260). But a `Value::BigInt` reaches here
        // able to hold an arbitrary string for exactly the reason the `Decimal`
        // arm above documents — the binary tag's payload is unvalidated — so the
        // digits go out bare ONLY when they are digits, and anything else takes
        // the escaped-string path rather than injecting JSON structure into a
        // `<script type="application/json">` body.
        Value::BigInt(d) if is_json_int_literal(d) => d.clone(),
        Value::BigInt(d) => format!("\"{}\"", json_string_body(d)),
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
                    // A JSON key is a string whatever the Python key was —
                    // `json.dumps({0: 1})` is `'{"0": 1}'` in CPython too, so
                    // stringifying here is the FAITHFUL encoding, not a
                    // shortcut around the typed key (#2339).
                    let key_json = format!("\"{}\"", json_string_body(&k.to_display_string()));
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

    /// Four quoted-string sites — the `BigInt` fallback, `Decimal`, `String`, the
    /// object key — one helper.
    ///
    /// The partial chain #2241 fixed survived a convergence that had already NAMED
    /// the gap. A comment naming a gap does not close it; a count that goes red when
    /// another chain appears does (#1646/#1859) — and it did exactly that when
    /// #2260 added the `BigInt` arm, whose non-digit fallback is a quoted string
    /// for the same unvalidated-tag-payload reason `Decimal`'s is.
    #[test]
    fn value_to_json_escapes_every_string_through_the_one_helper() {
        let body = body_tokens();
        let n = count_ident(&body, "json_string_body");
        assert_eq!(
            n, 4,
            "value_to_json should escape exactly its four quoted-string sites \
             (the BigInt fallback, Decimal, String, the object key) through \
             json_string_body; found {n}"
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
            (4, 0),
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
            4,
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

/// The float→string sink SET for the whole crate, pinned from Rust's own token
/// stream (#2270, following #2249's cure for the same pin done as text).
///
/// #2258 fixed three of five sinks and #2270 the other two, and the only reason
/// the other two were found at all is that someone re-ran the grep. The grep is
/// `format!("{f}")` over `crates/djust_templates/src/`; a grep nobody re-runs is
/// not a net, so it is mechanised here.
///
/// **What is pinned is the SET, not a floor.** A count-`>=` pin passes forever
/// once satisfied; #2233's caller pin grew 2 → 3 precisely because it asserted
/// the exact set, and the omission it caught was a real render path. So a new
/// `Value::Float` arm that spells the binding itself fails this test until it is
/// classified, which is the "next omission fails loudly" the issue asks for.
///
/// **Scope is the DIRECTORY, read at test time**, not this file. The two #2270
/// sinks were both in `filters.rs`, but the class is not: the crate's other
/// float→string arm is in `floatformat.rs` (#2253), and #2258's `Display` and
/// `py_repr` sites are in `djust_core` — a sibling crate this pin cannot see,
/// which is exactly why it must at least see every file of its own. Reading the
/// directory means a NEW file in this crate is covered the day it is added,
/// which an `include_str!` list of names is not. The read is asserted, so a
/// moved or renamed directory fails rather than silently scanning nothing
/// (#2249's third no-op mode).
///
/// **The rule is about the OPERATION** (#2129): an arm that binds a float and
/// turns it into a string must route through one of the approved reprs. It is
/// deliberately not a ban on the literal text `format!("{f}")`, which is
/// name-dependent — `Value::Float(x) => format!("{x}")` is the same defect and
/// the same grep misses it.
#[cfg(test)]
mod float_sink_set {
    use proc_macro2::{Delimiter, Spacing, TokenStream, TokenTree};
    use std::path::{Path, PathBuf};
    use std::str::FromStr;

    /// The reprs a float is allowed to become a string through.
    ///
    /// `python_float_repr` is CPython's `repr` (`{{ f|upper }}`, `pprint`,
    /// `floatformat`'s give-up path, `py_repr`); `python_float_trunc_digits` is
    /// `int(f)`'s digits (`stringformat:"d"`); `json_float_body` is
    /// `json.dumps`'s float, which is `repr` plus three non-finite names.
    const APPROVED: &[&str] = &[
        "python_float_repr",
        "python_float_trunc_digits",
        "json_float_body",
    ];

    /// Identifiers that mean "this arm produced a string from the binding".
    const STRINGIFIERS: &[&str] = &["format", "to_string", "push_str", "write"];

    fn src_dir() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("src")
    }

    /// `djust_core/src`, scanned alongside this crate's own since #2324.
    ///
    /// The `@stringfilter` coercion used to spell `str(float)` inline here, and
    /// #2324 moved it into `Value::py_str` — `djust_core`, beside `py_repr` —
    /// because `safeseq` needs the same spelling per ITEM and two copies of one
    /// rule is the #1646 shape. A sink that moves out of the scan is a sink
    /// that stops being pinned, so the scan follows it: `py_str`, `py_repr` and
    /// `Display` are now the three float→string sinks this test covers there.
    fn core_src_dir() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("crates/djust_templates has a parent")
            .join("djust_core")
            .join("src")
    }

    fn rust_files() -> Vec<PathBuf> {
        fn walk(dir: &Path, out: &mut Vec<PathBuf>) {
            let entries = std::fs::read_dir(dir).unwrap_or_else(|e| {
                panic!("cannot read {}: {e} — did the crate move?", dir.display())
            });
            for entry in entries {
                let path = entry.expect("a readable dir entry").path();
                if path.is_dir() {
                    walk(&path, out);
                } else if path.extension().is_some_and(|e| e == "rs") {
                    out.push(path);
                }
            }
        }
        let mut out = Vec::new();
        walk(&src_dir(), &mut out);
        walk(&core_src_dir(), &mut out);
        out.sort();
        out
    }

    /// The path relative to `crates/`, e.g. `djust_core/src/lib.rs`.
    ///
    /// Crate-qualified since the scan grew a second crate (#2324): both
    /// `djust_templates` and `djust_core` have a `lib.rs`, and a bare file name
    /// would report the two indistinguishably.
    fn file_name(p: &Path) -> String {
        let crates_root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("crates/djust_templates has a parent");
        p.strip_prefix(crates_root)
            .unwrap_or(p)
            .to_string_lossy()
            .replace('\\', "/")
    }

    fn count_ident(toks: &[TokenTree], name: &str) -> usize {
        let mut n = 0;
        for tok in toks {
            match tok {
                TokenTree::Ident(id) if *id == name => n += 1,
                TokenTree::Group(g) => {
                    n += count_ident(&g.stream().into_iter().collect::<Vec<_>>(), name)
                }
                _ => {}
            }
        }
        n
    }

    /// Is `toks[i..]` the start of `=>`?
    fn is_fat_arrow(toks: &[TokenTree], i: usize) -> bool {
        matches!((toks.get(i), toks.get(i + 1)),
            (Some(TokenTree::Punct(a)), Some(TokenTree::Punct(b)))
                if a.as_char() == '=' && a.spacing() == Spacing::Joint && b.as_char() == '>')
    }

    fn is_top_level_comma(tok: &TokenTree) -> bool {
        matches!(tok, TokenTree::Punct(p) if p.as_char() == ',')
    }

    /// Every `Value::Float(<binding>) => …` arm, as (binding, body tokens).
    ///
    /// `Value::Float(_)` is excluded: it binds nothing, so it cannot spell the
    /// float — `renderer.rs`'s `localize_number` arm reaches the value through
    /// `value.to_string()`, which is `Display`, and `Display` is the one
    /// spelling already correct by construction (#2258 fixed it in
    /// `djust_core`, out of this pin's reach).
    fn float_arms(toks: &[TokenTree]) -> Vec<(String, Vec<TokenTree>)> {
        let mut found = Vec::new();
        for (i, tok) in toks.iter().enumerate() {
            if let TokenTree::Group(g) = tok {
                let inner: Vec<TokenTree> = g.stream().into_iter().collect();
                found.extend(float_arms(&inner));
            }
            // `Value` `:` `:` `Float` `(binding)` — `::` is two Punct tokens,
            // the first `Joint`.
            let is_head = matches!(
                (&toks[i], toks.get(i + 1), toks.get(i + 2), toks.get(i + 3)),
                (
                    TokenTree::Ident(v),
                    Some(TokenTree::Punct(c1)),
                    Some(TokenTree::Punct(c2)),
                    Some(TokenTree::Ident(f)),
                ) if *v == "Value"
                    && c1.as_char() == ':'
                    && c1.spacing() == Spacing::Joint
                    && c2.as_char() == ':'
                    && *f == "Float"
            );
            if !is_head {
                continue;
            }
            let Some(TokenTree::Group(pat)) = toks.get(i + 4) else {
                continue;
            };
            if pat.delimiter() != Delimiter::Parenthesis {
                continue;
            }
            let bound: Vec<TokenTree> = pat.stream().into_iter().collect();
            let binding = match bound.as_slice() {
                [TokenTree::Ident(id)] if *id != "_" => id.to_string(),
                _ => continue,
            };
            // Forward to this arm's `=>`, stopping at a top-level `,` (which
            // means the `Value::Float(x)` was not an arm head at all — a call
            // argument, say).
            let mut j = i + 5;
            while j < toks.len() && !is_fat_arrow(toks, j) && !is_top_level_comma(&toks[j]) {
                j += 1;
            }
            if j >= toks.len() || !is_fat_arrow(toks, j) {
                continue;
            }
            let body_start = j + 2;
            // A block arm IS its brace group; an expression arm runs to the
            // next top-level comma. A comma nested inside a group is invisible
            // here, because a group is one token.
            let body: Vec<TokenTree> = match toks.get(body_start) {
                Some(TokenTree::Group(g)) if g.delimiter() == Delimiter::Brace => {
                    vec![TokenTree::Group(g.clone())]
                }
                _ => toks[body_start..]
                    .iter()
                    .take_while(|t| !is_top_level_comma(t))
                    .cloned()
                    .collect(),
            };
            found.push((binding, body));
        }
        found
    }

    /// Every arm that turns a bound float into a string, as (file, helper).
    fn stringifying_arms(files: &[PathBuf]) -> Vec<(String, String)> {
        let mut out = Vec::new();
        for path in files {
            let src = std::fs::read_to_string(path).expect("a readable .rs file");
            let toks: Vec<TokenTree> = TokenStream::from_str(&src)
                .unwrap_or_else(|e| panic!("{} must lex as Rust: {e}", path.display()))
                .into_iter()
                .collect();
            for (_binding, body) in float_arms(&toks) {
                let stringifies = STRINGIFIERS.iter().any(|s| count_ident(&body, s) > 0);
                let approved: Vec<&str> = APPROVED
                    .iter()
                    .copied()
                    .filter(|h| count_ident(&body, h) > 0)
                    .collect();
                if let Some(helper) = approved.first() {
                    out.push((file_name(path), (*helper).to_string()));
                } else if stringifies {
                    out.push((file_name(path), "RUST_DISPLAY".to_string()));
                }
            }
        }
        out.sort();
        out
    }

    /// The pin. Every entry is a float→string sink; the SET is exact.
    ///
    /// `filters.rs` × `python_float_repr` is the `@stringfilter` coercion
    /// (#2258); `pprint.rs` × `python_float_repr` is `pprint`'s flat repr, which
    /// moved out of `filters.rs` when the wrapping port landed (#2270, #2277).
    /// Adding a sixth sink without classifying it fails here.
    #[test]
    fn every_float_to_string_sink_routes_through_an_approved_repr() {
        let files = rust_files();
        assert!(
            files.len() >= 10,
            "only {} .rs files under {} — the directory read is not seeing the crate",
            files.len(),
            src_dir().display()
        );
        for expect in [
            "djust_templates/src/filters.rs",
            "djust_templates/src/floatformat.rs",
            "djust_templates/src/renderer.rs",
            "djust_templates/src/loop_cache.rs",
            "djust_templates/src/stringformat.rs",
            // The second crate, scanned since #2324 moved a sink into it.
            "djust_core/src/lib.rs",
        ] {
            assert!(
                files.iter().any(|p| file_name(p) == expect),
                "{expect} is missing from the scan — the pin would not see a sink in it"
            );
        }

        let found = stringifying_arms(&files);
        let expected: Vec<(String, String)> = [
            // `Display`'s legacy_display arm: the frozen pre-#2203 rendering,
            // reached only when `django_value_repr()` is OFF. Rust's `{}` is
            // the WRONG spelling and is the point — the flag exists to restore
            // the old bytes. The one RUST_DISPLAY entry this set allows; a
            // second one is the #2258/#2270 defect.
            ("djust_core/src/lib.rs", "RUST_DISPLAY"),
            // `py_str` (#2324), `py_repr` (#2258) and `Display`'s live arm —
            // the three places a `Value::Float` becomes text in djust_core.
            ("djust_core/src/lib.rs", "python_float_repr"),
            ("djust_core/src/lib.rs", "python_float_repr"),
            ("djust_core/src/lib.rs", "python_float_repr"),
            ("djust_templates/src/filters.rs", "json_float_body"),
            // `filters.rs` lost its own `python_float_repr` entry in #2324: the
            // `@stringfilter` coercion no longer binds the float, it asks
            // `Value::py_str` — which is why the scan grew djust_core rather
            // than shrinking.
            (
                "djust_templates/src/filters.rs",
                "python_float_trunc_digits",
            ),
            ("djust_templates/src/floatformat.rs", "python_float_repr"),
            ("djust_templates/src/pprint.rs", "python_float_repr"),
            // `%d` / `%i` / `%u`'s argument rule (#2358): CPython truncates a
            // finite float toward zero and raises for a non-finite, which is
            // exactly `python_float_trunc_digits`. The `%e`/`%f`/`%g` arm in
            // the same file does NOT appear here, because it binds the `f64`
            // for arithmetic rather than spelling it — Rust's `{:.*}` and
            // `{:.*e}` are C's, which is what those conversions are.
            (
                "djust_templates/src/stringformat.rs",
                "python_float_trunc_digits",
            ),
        ]
        .iter()
        .map(|(f, h)| ((*f).to_string(), (*h).to_string()))
        .collect();

        assert_eq!(
            found, expected,
            "the float→string sink set moved. Every `Value::Float(x) => …` arm \
             that spells the float must route through one of {APPROVED:?}; an \
             entry reading RUST_DISPLAY is a new sink using Rust's `{{}}`, which \
             is the #2258/#2270 defect. If the change is legitimate, update the \
             expected set here — deliberately, so the next omission is visible."
        );
    }

    /// Empirical canary (#1459) — the pin must SEE the exact defect it exists for.
    ///
    /// Not a re-implementation: the fixture runs through the same `float_arms` +
    /// `count_ident` the pin uses. Both #2270 sinks are re-introduced verbatim,
    /// plus the renamed-binding variant a text grep for `format!("{f}")` misses.
    #[test]
    fn a_reintroduced_rust_display_sink_is_reported() {
        let fixture = "\
            fn sink(value: &Value) -> String {\n\
                match value {\n\
                    Value::Integer(n) => n.to_string(),\n\
                    Value::Float(f) => format!(\"{f}\"),\n\
                    Value::String(s) => s.clone(),\n\
                }\n\
            }\n\
            fn renamed(value: &Value) -> String {\n\
                match value {\n\
                    Value::Float(x) => format!(\"{x}\"),\n\
                    _ => String::new(),\n\
                }\n\
            }\n\
            fn ok(value: &Value) -> String {\n\
                match value {\n\
                    Value::Float(f) => djust_core::decimal::python_float_repr(*f),\n\
                    _ => String::new(),\n\
                }\n\
            }\n\
            fn numeric(value: &Value) -> Option<f64> {\n\
                match value {\n\
                    Value::Float(f) => Some(*f),\n\
                    _ => None,\n\
                }\n\
            }\n";
        let toks: Vec<TokenTree> = TokenStream::from_str(fixture)
            .expect("the canary fixture must lex")
            .into_iter()
            .collect();
        let arms = float_arms(&toks);
        assert_eq!(arms.len(), 4, "the locator missed an arm: {arms:?}");

        let classify = |body: &[TokenTree]| -> Option<String> {
            if let Some(h) = APPROVED.iter().find(|h| count_ident(body, h) > 0) {
                return Some((*h).to_string());
            }
            if STRINGIFIERS.iter().any(|s| count_ident(body, s) > 0) {
                return Some("RUST_DISPLAY".to_string());
            }
            None
        };
        let verdicts: Vec<Option<String>> = arms.iter().map(|(_, b)| classify(b)).collect();
        assert_eq!(
            verdicts,
            vec![
                Some("RUST_DISPLAY".to_string()),
                Some("RUST_DISPLAY".to_string()),
                Some("python_float_repr".to_string()),
                None,
            ],
            "the pin cannot tell a Rust-`{{}}` sink from an approved one"
        );
    }

    /// Prose naming the banned shape must not move the pin, in either direction.
    ///
    /// This is why the pin lexes instead of grepping: `filters.rs` and
    /// `floatformat.rs` BOTH carry `format!("{f}")` inside doc comments that
    /// explain why it is wrong, and the text grep the issue cites counts them.
    #[test]
    fn a_sink_named_in_a_comment_or_a_string_is_not_a_sink() {
        let prose = "\
            /// Why this is not `format!(\"{f}\")`: see #2258.\n\
            fn f(value: &Value) -> String {\n\
                match value {\n\
                    // was: Value::Float(f) => format!(\"{f}\"),\n\
                    Value::Float(f) => {\n\
                        let _msg = \"never Value::Float(f) => format!(\\\"{f}\\\")\";\n\
                        djust_core::decimal::python_float_repr(*f)\n\
                    }\n\
                    _ => String::new(),\n\
                }\n\
            }\n";
        // The mutation IS real: raw text names the banned shape three times.
        assert_eq!(
            prose.matches("format!(\\\"{f}\\\")").count()
                + prose.matches("format!(\"{f}\")").count(),
            3
        );

        let toks: Vec<TokenTree> = TokenStream::from_str(prose)
            .expect("the canary fixture must lex")
            .into_iter()
            .collect();
        let arms = float_arms(&toks);
        assert_eq!(arms.len(), 1, "a commented-out arm was counted: {arms:?}");
        assert_eq!(count_ident(&arms[0].1, "python_float_repr"), 1);
        assert_eq!(
            count_ident(&arms[0].1, "format"),
            0,
            "`format!` inside a string literal was counted as a call"
        );
    }

    /// The body is the arm's, and stops at the arm's end.
    ///
    /// The failure this guards is a body that runs on into the NEXT arm and
    /// borrows its helper — which would make a real sink look approved.
    #[test]
    fn an_arm_body_does_not_borrow_the_next_arm_s_helper() {
        let fixture = "\
            fn f(value: &Value) -> String {\n\
                match value {\n\
                    Value::Float(f) => format!(\"{f}\"),\n\
                    Value::Decimal(d) => djust_core::decimal::python_float_repr(0.0),\n\
                }\n\
            }\n";
        let toks: Vec<TokenTree> = TokenStream::from_str(fixture)
            .expect("the canary fixture must lex")
            .into_iter()
            .collect();
        let arms = float_arms(&toks);
        assert_eq!(arms.len(), 1);
        assert_eq!(
            count_ident(&arms[0].1, "python_float_repr"),
            0,
            "the body ran past its own arm into the next one"
        );
        assert_eq!(count_ident(&arms[0].1, "format"), 1);
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

/// `{{ value|linenumbers }}` — Django's `("%0" + width + "d. %s")`.
///
/// **ZERO padding, not space padding** (#2259). Django builds the format string
/// as `"%0" + width + "d"`, so an 11-line input numbers `01.` through `11.`; the
/// previous `{:>width$}` produced ` 1.`, which is invisible until the input
/// crosses ten lines. Checked as a neighbour of `linebreaks` because the issue
/// asked for it — and the escaping half of that check came back clean while this
/// one did not.
///
/// **Deliberately NOT added to `renderer::SAFE_OUTPUT_FILTERS`,** even though
/// Django registers it `is_safe=True`. Django escapes each line and marks the
/// join safe; this escapes the whole rendered string afterwards. The two are
/// byte-identical, because everything this filter ADDS — digits, `.`, a space,
/// the `\n` join — is escape-invariant, and `escape` operates per character.
/// Verified against Django over `<`, `&`, `"`, `'` and a multi-line mix. Adding
/// it to the safe list WITHOUT moving the escape inside would stop the input
/// being escaped at all and open an XSS hole for a one-cell cosmetic gain.
fn add_linenumbers(s: &str, autoescape: bool) -> String {
    let lines: Vec<&str> = s.split('\n').collect();
    let width = lines.len().to_string().len();
    lines
        .iter()
        .enumerate()
        .map(|(i, line)| {
            // Escape each LINE inside the filter, as Django does, rather than
            // leaving it to the render-time auto-escape (#2291).
            //
            // The previous version relied on that render-time escape and
            // `SAFE_OUTPUT_FILTERS` documented the exclusion deliberately,
            // arguing the two were byte-identical "because everything it adds
            // is escape-invariant". True — and beside the point: the argument
            // holds only while the render-time escape actually RUNS. A later
            // `|safe` suppresses it, and then nothing had escaped the input at
            // all, so `{{ p|linenumbers|safe }}` emitted attacker markup live.
            //
            // Same shape as `linebreaks` (#2269): the escape moves inside and
            // the name joins `SAFE_OUTPUT_FILTERS` as ONE inseparable change.
            // Doing either alone is a bug in opposite directions — the escape
            // without the grant double-escapes, the grant without the escape is
            // the XSS.
            let body = if autoescape {
                html_escape(line)
            } else {
                (*line).to_string()
            };
            format!("{:0width$}. {body}", i + 1)
        })
        .collect::<Vec<_>>()
        .join("\n")
}

/// `str(int(value))`, or `None` where Python's `int()` would raise.
///
/// ONE definition for every filter that needs Python's `int()` — `add`,
/// `get_digit`, `stringformat:"d"` — rather than the per-filter re-derivation
/// that let `add` and `stringformat` disagree about the same value for two
/// releases (#2253, #2265, #1646).
///
/// `string_float_ok` decides only what a STRING may become. Python's
/// `int("1.5")` RAISES, which is what sends `{{ "1.5"|add:"1.5" }}` to the
/// concatenation branch; a float LITERAL is a different thing and the template
/// layer distinguishes the two by quoting, so `add` passes `true` for the
/// unquoted case. `get_digit` always passes `false` — it has no literal
/// operand.
///
/// Leading zeros survive: `add_int_digits` normalizes them and `get_digit`'s
/// only consumer indexes from the RIGHT, so neither cares.
/// Django's `pluralize`, whole (#2359).
///
/// ```python
/// if "," not in arg:
///     arg = "," + arg
/// bits = arg.split(",")
/// if len(bits) > 2:
///     return ""
/// singular_suffix, plural_suffix = bits[:2]
/// try:
///     return singular_suffix if float(value) == 1 else plural_suffix
/// except ValueError:   # Invalid string that's not a number.
///     pass
/// except TypeError:    # Value isn't a string or a number; maybe a list?
///     try:
///         return singular_suffix if len(value) == 1 else plural_suffix
///     except TypeError:
///         pass
/// return ""
/// ```
///
/// What was here had an `Integer` arm, a sequence arm and
/// `_ => plural_suffix`, which is three of the four answers Django can give
/// and never the empty one. Two consequences, and the second is the sharper:
///
/// * `{{ True|pluralize }}` rendered `'s'` where Django renders `''`, because
///   `float(True)` is `1.0`. Per-VALUE, not per-type: `False` AGREED, which
///   is why an arm keyed on "is it a bool" would have been the wrong shape.
/// * the comma form was **entirely unimplemented** — `{{ n|pluralize:"y,ies" }}`
///   rendered the literal text `y,ies`. That is not a separate bug so much as
///   the same missing rule: the argument is a suffix PAIR, and the catch-all
///   never split it.
///
/// **The two `except` arms are NOT the same arm.** A `ValueError` — a string
/// that is not a number — falls straight to `""`, and does not try `len()`.
/// So `{{ "abc"|pluralize }}` is `''` while `{{ l|pluralize }}` on a 3-list
/// is the plural suffix, even though both are "sized things that are not
/// numbers". Reading the two `pass`es as one is the obvious mistake here, and
/// it is measured against live Django rather than inferred.
fn pluralize(value: &Value, arg: &str) -> String {
    let owned;
    let arg = if arg.contains(',') {
        arg
    } else {
        owned = format!(",{arg}");
        owned.as_str()
    };
    let bits: Vec<&str> = arg.split(',').collect();
    if bits.len() > 2 {
        return String::new();
    }
    let singular = bits[0].to_string();
    let plural = bits.get(1).copied().unwrap_or("").to_string();
    let pick = |is_one: bool| {
        if is_one {
            singular.clone()
        } else {
            plural.clone()
        }
    };

    // `float(value)`, and the three outcomes it has.
    match value {
        Value::Integer(n) => return pick(*n == 1),
        Value::Float(f) => return pick(*f == 1.0),
        Value::Bool(b) => return pick(*b),
        Value::Decimal(d) | Value::BigInt(d) => {
            return match d.parse::<f64>() {
                Ok(f) => pick(f == 1.0),
                // `float(Decimal(...))` cannot fail for a real Decimal, so
                // this is the unreachable-in-practice arm; `""` is the
                // conservative answer either way.
                Err(_) => String::new(),
            };
        }
        // A STRING is `float(s)`, and on failure it is the `ValueError` arm —
        // which does NOT fall through to `len()`. `float` accepts surrounding
        // whitespace, `inf` and `nan`, and so does Rust's parse.
        Value::String(s) => {
            return match s.trim().parse::<f64>() {
                Ok(f) => pick(f == 1.0),
                Err(_) => String::new(),
            };
        }
        // `len(value)`, the `TypeError` arm. A dict VIEW answers `len()` in
        // Python, so it counts its entries like any other sized value
        // (#2340).
        Value::List(l) | Value::Tuple(l) | Value::DictView { items: l, .. } => {
            return pick(l.len() == 1)
        }
        Value::Object(map) => return pick(map.len() == 1),
        // `None` and an absent variable have neither a `float()` nor a
        // `len()`. Django's final `return ""`.
        Value::None | Value::Missing => {}
    }
    String::new()
}

fn int_digits_of(value: &Value, string_float_ok: bool) -> Option<String> {
    let from_digits = |s: &str| -> Option<String> {
        let t = s.trim();
        let body = t.strip_prefix(['-', '+']).unwrap_or(t);
        if body.is_empty() || !body.bytes().all(|b| b.is_ascii_digit()) {
            return None;
        }
        Some(if t.starts_with('-') {
            format!("-{body}")
        } else {
            body.to_string()
        })
    };
    match value {
        Value::Integer(n) => Some(n.to_string()),
        // Exact, and truncating toward zero: `int(1.5)` is 1 and `int(1e300)`
        // is the binary value's 301 digits, not `10**300`. `None` for a
        // non-finite, which `int()` refuses too.
        Value::Float(f) => djust_core::decimal::python_float_trunc_digits(*f),
        // `int(Decimal('19.99'))` is 19 — truncation, on the EXACT digits.
        Value::Decimal(d) => djust_core::decimal::parse_decimal_parts(d)
            .and_then(|p| p.to_int_digits_trunc(djust_core::decimal::PY_INT_MAX_STR_DIGITS)),
        // Already `str(int)`; `int()` of an int is itself (#2260).
        Value::BigInt(d) => Some(d.clone()),
        // `int(True)` is 1 in Python.
        Value::Bool(b) => Some(if *b { "1" } else { "0" }.to_string()),
        Value::String(s) => from_digits(s).or_else(|| {
            string_float_ok
                .then(|| s.trim().parse::<f64>().ok())
                .flatten()
                .and_then(djust_core::decimal::python_float_trunc_digits)
        }),
        // `int(None)`, `int([1])`, `int({})` all raise.
        _ => None,
    }
}

/// The `Value` a `str(int(x))` digit string denotes — Python's `int` object.
///
/// [`int_digits_of`] answers `str(int(value))` because that is what every
/// digit-INDEXING caller needs. A caller that must hand back the CONVERTED
/// NUMBER instead (`get_digit`'s `if arg < 1: return value`, #2403) needs the
/// int itself, so the rest of the chain sees a number rather than its spelling:
/// `{{ p|get_digit:0|add:1 }}` is arithmetic in Django, not concatenation.
///
/// `BigInt` for anything past `i64`, which is the variant that already carries
/// an out-of-range Python int as its digits.
fn int_value_of(digits: &str) -> Value {
    match digits.parse::<i64>() {
        Ok(n) => Value::Integer(n),
        Err(_) => Value::BigInt(digits.to_string()),
    }
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

/// `{{ value|urlize }}` / `{{ value|urlizetrunc:n }}`.
///
/// # `autoescape` (#2284)
///
/// Django registers both `needs_autoescape=True` and threads the flag into
/// `django.utils.html.Urlizer`, which reads it as `autoescape and not
/// safe_input`. Everything the filter *quotes from the user* — the text between
/// matches, the trailing punctuation, the anchor's display text — is escaped
/// only when that is true.
///
/// The one escape that is **unconditional** is the `href`. Django writes
/// `self.url_template % {"href": escape(url), ...}` outside the `if autoescape`
/// branch, because the attribute value has to survive being placed inside
/// `href="…"` whatever the surrounding policy is. Making it conditional would
/// let a `mark_safe`d `http://x/"onmouseover=alert(1)` break out of the
/// attribute — a genuine XSS that Django does not have. It is kept
/// unconditional here for exactly that reason.
///
/// As with [`linebreaks`], `autoescape=false` is reachable only when the value
/// was already `SafeData`, so the `SAFE_OUTPUT_FILTERS` membership stays
/// earned: hostile input that nothing marked safe still takes every escape.
fn urlize(text: &str, trunc_limit: Option<usize>, autoescape: bool) -> String {
    // Django's `escape(...)` applied only where its `if autoescape and not
    // safe_input:` branch applies. One helper rather than an `if` at each of
    // the four sites, so a future site cannot forget the flag (#1646).
    let maybe_escape = |s: &str| -> String {
        if autoescape {
            html_escape(s)
        } else {
            s.to_string()
        }
    };

    let mut result = String::new();
    let mut last_end = 0;

    for m in URLIZE_RE.find_iter(text) {
        // Non-URL text between matches — user text, so it follows the flag.
        result.push_str(&maybe_escape(&text[last_end..m.start()]));

        let matched = m.as_str();

        // Determine if this is an email or a URL
        if matched.contains('@') && !matched.starts_with("http") {
            // Email — the href is ALWAYS escaped (attribute context); the
            // display text follows the flag.
            let safe_href = html_escape(matched);
            let display = maybe_escape(&truncate_url_display(matched, trunc_limit));
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
            // ALWAYS escaped — attribute context, as Django does.
            let safe_href = html_escape(&href_clean);
            let display = maybe_escape(&truncate_url_display(&display_raw, trunc_limit));
            let safe_trailing = maybe_escape(&trailing);
            result.push_str(&format!(
                "<a href=\"{safe_href}\" rel=\"nofollow\">{display}</a>{safe_trailing}"
            ));
        }

        last_end = m.end();
    }

    // Remaining text after the last match — user text, so it follows the flag.
    result.push_str(&maybe_escape(&text[last_end..]));
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

/// `Urlizer.trim_url`, which is NOT `Truncator` (#2346).
///
/// ```python
/// def trim_url(self, x, *, limit):
///     if limit is None or len(x) <= limit:
///         return x
///     return "%s…" % x[: max(0, limit - 1)]
/// ```
///
/// Two things this got wrong, and they compound: it appended three ASCII dots
/// where Django appends one `…`, and it reserved THREE characters for them
/// where Django reserves one. So `urlizetrunc:"5"` on `http://example.com/aaaa`
/// gave `ht...` where Django gives `http…` — every `urlizetrunc` cell in the
/// differential's sweep differed, for this reason alone.
///
/// Same ellipsis fix that landed for `truncatechars` in #2203; it never reached
/// `urlize`, which is parallel-path drift on a CONSTANT (#1646). The two are
/// deliberately not sharing a code path even so: `Truncator.chars` normalizes
/// to NFC, skips combining characters and subtracts the truncation text's own
/// visible length (`calculate_truncate_chars_length`), while `trim_url` is a
/// plain code-point slice. Routing this through `truncate::text_chars` would be
/// tidier and would not be Django.
fn truncate_url_display(s: &str, limit: Option<usize>) -> String {
    match limit {
        Some(n) if s.chars().count() > n => {
            let truncated: String = s.chars().take(n.saturating_sub(1)).collect();
            format!("{truncated}{URL_TRUNCATE}")
        }
        _ => s.to_string(),
    }
}

/// The single character `Urlizer.trim_url` appends, spelled as an escape.
///
/// A literal `…` and a literal `...` are hard to tell apart in a diff, which is
/// how the three-dot spelling survived #2203's fix to the neighbouring filter.
const URL_TRUNCATE: &str = "\u{2026}";

fn unordered_list(items: &[Value], depth: usize, items_are_safe: bool) -> String {
    let indent = "\t".repeat(depth);
    let mut result = Vec::new();

    let mut i = 0;
    while i < items.len() {
        let item = &items[i];

        // Is the next item this item's sublist? Django's `walk_items` asks
        // `isinstance(next_item, (list, tuple, types.GeneratorType))` — BOTH
        // sequence types, not just a list (#2317). Matching `Value::List`
        // alone rendered a tuple as its own `<li>` holding the escaped tuple
        // repr, where Django nests a `<ul>`.
        //
        // The generator arm has no djust equivalent: a generator does not
        // survive the crossing into Rust as anything but its elements, so
        // there is no third variant to match.
        let sublist = if i + 1 < items.len() {
            match &items[i + 1] {
                Value::List(sub) | Value::Tuple(sub) => {
                    i += 1; // consume the sublist
                    Some(sub)
                }
                _ => None,
            }
        } else {
            None
        };

        let escaped_item = conditional_escape(item, items_are_safe);
        match sublist {
            Some(sub) if !sub.is_empty() => {
                let sub_content = unordered_list(sub, depth + 1, items_are_safe);
                // Django's `list_formatter` (`defaultfilters.py`) builds the
                // wrapper from the PARENT's `indent` and passes `tabs + 1` only
                // to the recursive call, so the `<ul>`/`</ul>` sit at the
                // parent's depth and only the `<li>`s inside step in:
                //
                //     sublist = "\n%s<ul>\n%s\n%s</ul>\n%s" % (
                //         indent, list_formatter(children, tabs + 1), indent, indent)
                //
                // Every one of the four `%s` indents is the parent's, which is
                // also why the closing `</li>` below already used `indent`.
                //
                // Fixed in #2306 (#2301) by @alexsmolya; the randomised sweep
                // in `python/tests/test_unordered_list_indent_2301.py` is what
                // pins it across nesting shapes.
                result.push(format!(
                    "{indent}<li>{escaped_item}\n{indent}<ul>\n{sub_content}\n{indent}</ul>\n{indent}</li>"
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

    /// Every `Value` variant, so a new one cannot be added without a decision.
    fn every_variant() -> Vec<Value> {
        let mut map = IndexMap::new();
        map.insert(
            djust_core::ObjectKey::Str("a".to_string()),
            Value::Integer(1),
        );
        map.insert(
            djust_core::ObjectKey::Str("b".to_string()),
            Value::Integer(2),
        );
        let mut model = IndexMap::new();
        model.insert(
            djust_core::ObjectKey::Str("__str__".to_string()),
            Value::String("Model #1".to_string()),
        );
        vec![
            Value::Missing,
            Value::None,
            Value::Bool(true),
            Value::Integer(7),
            Value::Float(1.5),
            Value::Decimal("1.50".to_string()),
            Value::BigInt("123456789012345678901".to_string()),
            Value::String("中é".to_string()),
            Value::String(String::new()),
            Value::List(vec![Value::Integer(1), Value::Integer(2)]),
            Value::Tuple(vec![Value::Integer(1)]),
            Value::Object(map),
            Value::Object(model),
            Value::DictView {
                kind: djust_core::DictViewKind::Keys,
                items: vec![Value::Integer(1), Value::Integer(2), Value::Integer(3)],
            },
        ]
    }

    /// The `{% for a, b in … %}` unpack arm relies on this and says so (#2387).
    ///
    /// It checks arity with [`python_len`] and then unpacks with
    /// [`iter_values`], so if a variant ever answered a length without being
    /// iterable — or answered a DIFFERENT count — the arm's non-sequence
    /// branch would silently bind `Missing` to the tail names instead of
    /// raising. This is the pin that makes "total by construction" mechanical
    /// rather than a comment.
    #[test]
    fn python_len_agrees_with_iter_values() {
        for value in every_variant() {
            if let Some(len) = python_len(&value) {
                let items = iter_values(&value).unwrap_or_else(|| {
                    panic!("python_len answered {len} for {value:?} but iter_values refused it")
                });
                assert_eq!(
                    items.len(),
                    len,
                    "python_len and iter_values disagree about {value:?}"
                );
            }
        }
    }

    /// `len(model)` raises in Python, and both maps are `Value::Object`.
    #[test]
    fn python_len_refuses_a_serialized_model_but_not_a_dict() {
        let mut dict = IndexMap::new();
        dict.insert(
            djust_core::ObjectKey::Str("a".to_string()),
            Value::Integer(1),
        );
        assert_eq!(python_len(&Value::Object(dict)), Some(1));

        let mut model = IndexMap::new();
        model.insert(
            djust_core::ObjectKey::Str("__str__".to_string()),
            Value::String("Model #1".to_string()),
        );
        let model = Value::Object(model);
        assert!(model.object_str().is_some(), "fixture is not a model");
        assert_eq!(python_len(&model), None);
    }

    /// The two fallbacks are the call sites' own — `|length` says 0, the
    /// unpack arm says 1, and `python_len` says neither.
    #[test]
    fn python_len_answers_none_where_python_raises() {
        for value in [
            Value::Missing,
            Value::None,
            Value::Bool(true),
            Value::Integer(7),
            Value::Float(1.5),
            Value::Decimal("1.50".to_string()),
            Value::BigInt("1".to_string()),
        ] {
            assert_eq!(python_len(&value), None, "{value:?}");
            // …and `|length` still prints its own 0 for every one of them.
            assert_eq!(
                apply_filter("length", &value, None).unwrap().to_string(),
                "0"
            );
        }
        // Code points, not bytes (#2279): "中é" is 5 bytes.
        assert_eq!(python_len(&Value::String("中é".to_string())), Some(2));
    }

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

    /// `escape` is the one Django `@stringfilter` djust EXCLUDES (#2257).
    ///
    /// Pinned so the exclusion stays a decision. `escape` stopped being a no-op
    /// in #2281 (it is now `conditional_escape`, eager, so the rest of the chain
    /// sees the ESCAPED text), which means it necessarily stringifies. It
    /// stringifies through `Display` — the RENDER form, `0.000000001` — and NOT
    /// through `str(Decimal)`, `1E-9`, which is what `is_string_filter` would
    /// have selected. That keeps the rendered bytes of `{{ d|escape }}` exactly
    /// what they were before #2281 and leaves #2257's decision to #2257.
    ///
    /// `safe` was the second name here until #2303. It now TAKES the coercion,
    /// which is what makes `{{ d|safe }}` Django's `1E-9`; the `safe` row below
    /// is the gate-off for the `escape` row, since the two used to be spelled
    /// identically and a reader should be able to see that they no longer are.
    #[test]
    fn escape_is_excluded_from_the_coercion_and_safe_is_not() {
        assert!(!is_string_filter("escape"));
        assert!(is_string_filter("safe"));
        let value = Value::Decimal("1E-9".to_string());
        let got = apply_filter("safe", &value, None).unwrap();
        assert!(
            matches!(&got, Value::String(s) if s == "1E-9"),
            "|safe gave {got:?}"
        );
        let got = apply_filter("escape", &value, None).unwrap();
        assert_eq!(got.to_string(), "0.000000001", "|escape gave {got:?}");
    }

    /// `|safe` is `SafeString(str(value))` for EVERY variant (#2303).
    ///
    /// One row per `Value` arm, because the bug was in whichever arm the table
    /// did not enumerate — the container arm landed in #2283 and the scalar
    /// arms stayed a no-op. `Missing` is the row that has to be `""` and not
    /// `"None"`: Django substitutes `string_if_invalid` before the filter runs.
    #[test]
    fn safe_stringifies_every_variant_the_way_python_str_does() {
        let rows: &[(Value, &str)] = &[
            (Value::Missing, ""),
            (Value::None, "None"),
            (Value::Bool(true), "True"),
            (Value::Bool(false), "False"),
            (Value::Integer(42), "42"),
            (Value::Float(1.5), "1.5"),
            // `str(1e20)` is `1e+20`; `{{ p }}` renders the expansion.
            (Value::Float(1e20), "1e+20"),
            (Value::Decimal("1E-9".to_string()), "1E-9"),
            (
                Value::BigInt("12345678901234567890".to_string()),
                "12345678901234567890",
            ),
            (Value::String("<b>x</b>".to_string()), "<b>x</b>"),
            (
                Value::List(vec![Value::String("a".to_string()), Value::Integer(1)]),
                "['a', 1]",
            ),
        ];
        for (value, expected) in rows {
            let got = apply_filter("safe", value, None).unwrap();
            assert!(
                matches!(&got, Value::String(s) if s == expected),
                "|safe on {value:?} gave {got:?}, wanted String({expected:?})"
            );
        }
        // The RENDER form of the two exponent-carrying variants differs from
        // `str()` — without this the two rows above prove nothing.
        assert_eq!(Value::Float(1e20).to_string(), "100000000000000000000");
        assert_eq!(
            Value::Decimal("1E-9".to_string()).to_string(),
            "0.000000001"
        );
    }

    /// The set is Django's, transcribed — 28 of its 29, minus `escape`.
    ///
    /// A count pin rather than a floor (#1125): adding a name without deciding
    /// about it fails here, and the Python test re-derives the set from the live
    /// Django registry so a name that is not really a `@stringfilter` fails
    /// there.
    #[test]
    fn the_string_filter_set_is_the_twenty_eight_it_claims() {
        assert_eq!(STRING_FILTERS.len(), 28);
        let mut sorted = STRING_FILTERS.to_vec();
        sorted.sort_unstable();
        sorted.dedup();
        assert_eq!(sorted.len(), 28, "duplicate entry in STRING_FILTERS");
        assert_eq!(sorted, STRING_FILTERS, "STRING_FILTERS is not sorted");
    }

    #[test]
    fn test_length_filter() {
        let value = Value::List(vec![Value::Integer(1), Value::Integer(2)]);
        let result = apply_filter("length", &value, None).unwrap();
        assert!(matches!(result, Value::Integer(2)));
    }

    /// Every `Value` variant, sorted by whether [`iter_values`] iterates it —
    /// and the answer to "is #2285's non-iterable escape still load-bearing?"
    ///
    /// #2285 added `html_escape` to the `safeseq` / `unordered_list`
    /// fall-through because those two hold an unconditional safe-output grant
    /// and were handing a hostile STRING back raw under it. #2283 moved every
    /// markup-carrying variant — `String`, `List`, `Tuple`, `Object` — onto the
    /// iterating side, so the only values that still reach that escape are
    /// numeric, boolean, `None` and `Decimal`/`BigInt` digit strings, none of
    /// which can contain a character `html_escape` changes.
    ///
    /// The escape is therefore a NO-OP for every input reachable today. It is
    /// kept, and this test is why: it enumerates the enum, so ANY future
    /// variant that lands on the non-iterating side has to be classified here —
    /// and if that variant can carry markup, the escape is load-bearing again
    /// the moment it exists. Deleting the escape would make that a silent XSS
    /// instead of a red test.
    /// The compiler-checked half of the claim. This `match` has NO wildcard,
    /// so adding a `Value` variant fails to build until it is named here — and
    /// the sample list below must then grow to keep `seen.len()` at the pinned
    /// count. `iter_values` itself ends in `_ => None`, so it cannot provide
    /// that guarantee on its own; without this function the test's
    /// "exhaustive by construction" claim was decorative, and deleting sample
    /// entries left it green (#1859).
    fn variant_name(v: &Value) -> &'static str {
        match v {
            Value::Missing => "Missing",
            Value::None => "None",
            Value::Bool(_) => "Bool",
            Value::Integer(_) => "Integer",
            Value::Float(_) => "Float",
            Value::String(_) => "String",
            Value::List(_) => "List",
            Value::Tuple(_) => "Tuple",
            Value::Object(_) => "Object",
            Value::DictView { .. } => "DictView",
            Value::Decimal(_) => "Decimal",
            Value::BigInt(_) => "BigInt",
        }
    }

    #[test]
    fn every_non_iterable_variant_is_markup_free() {
        // Exhaustive in BOTH directions: `variant_name`'s wildcard-free `match`
        // rejects a new variant at compile time, and the distinct-name count
        // below rejects a DELETED sample at test time.
        let variants = [
            Value::Missing,
            Value::None,
            Value::Bool(true),
            Value::Integer(-42),
            Value::Float(1.5),
            Value::String("<b>".to_string()),
            Value::List(vec![Value::String("<b>".to_string())]),
            Value::Tuple(vec![Value::String("<b>".to_string())]),
            Value::Object(Default::default()),
            // On the ITERATING side (#2340): a view is iterable in Python, so
            // `|join` / `|safeseq` / `|unordered_list` see its elements — which
            // is also why its markup never reaches #2285's fall-through escape.
            Value::DictView {
                kind: djust_core::DictViewKind::Keys,
                items: vec![Value::String("<b>".to_string())],
            },
            Value::Decimal("1E-9".to_string()),
            Value::BigInt("9".repeat(40)),
        ];
        let mut iterating = 0;
        for v in &variants {
            match iter_values(v) {
                Some(_) => iterating += 1,
                None => {
                    let s = v.to_string();
                    assert_eq!(
                        html_escape(&s),
                        s,
                        "{v:?} is NOT iterated and CAN carry markup — #2285's \
                         escape on the fall-through is load-bearing again, and \
                         this test must say so rather than be deleted",
                    );
                }
            }
        }
        assert_eq!(
            iterating, 6,
            "the iterating set is exactly String / List / Tuple / Object / \
             Missing / DictView — a change here is a change to what #2283 fixed",
        );
        // The half that makes "exhaustive" true rather than aspirational: every
        // arm `variant_name` can return is exercised by a sample. Deleting a
        // sample drops this count; adding a `Value` variant fails to compile in
        // `variant_name` first.
        let seen: std::collections::BTreeSet<&str> = variants.iter().map(variant_name).collect();
        assert_eq!(
            seen.len(),
            12,
            "every `Value` variant needs a sample above; saw {seen:?}",
        );
    }

    // ---- slice: Python's own semantics (#2326) ---------------------------

    /// `slice_positions` IS `PySlice_AdjustIndices` plus the walk, so the
    /// reference is `list(range(len))[a:b:c]` — asserted against literal
    /// expected values here, and swept against LIVE Python in
    /// `python/tests/test_filtered_operands_and_slice_2325_2326.py::
    /// TestSliceRandomised::test_random_specs_match_pythons_own_slice`.
    #[test]
    fn slice_positions_matches_python() {
        // (start, stop, step, len, expected) — named so clippy's
        // type-complexity lint has something to point at.
        type Case = (
            Option<isize>,
            Option<isize>,
            Option<isize>,
            usize,
            &'static [usize],
        );
        let cases: &[Case] = &[
            // Defaults.
            (None, None, None, 4, &[0, 1, 2, 3]),
            (None, None, None, 0, &[]),
            // Non-negative, the only shape the pre-#2326 code got right.
            (Some(1), Some(3), None, 4, &[1, 2]),
            (Some(3), Some(1), None, 4, &[]),
            // Negative indices WRAP; they do not clamp to 0.
            (Some(-1), None, None, 4, &[3]),
            (None, Some(-1), None, 4, &[0, 1, 2]),
            (Some(-2), Some(-1), None, 4, &[2]),
            // Out of range still clamps, in both directions.
            (Some(-99), None, None, 4, &[0, 1, 2, 3]),
            (None, Some(99), None, 4, &[0, 1, 2, 3]),
            (Some(99), None, None, 4, &[]),
            (None, Some(-99), None, 4, &[]),
            // A step SELECTS rather than being discarded.
            (None, None, Some(2), 4, &[0, 2]),
            (Some(1), None, Some(2), 4, &[1, 3]),
            (None, Some(3), Some(2), 4, &[0, 2]),
            // A negative step reverses AND swaps the defaults.
            (None, None, Some(-1), 4, &[3, 2, 1, 0]),
            (None, None, Some(-2), 4, &[3, 1]),
            (Some(3), Some(1), Some(-1), 4, &[3, 2]),
            (Some(-1), None, Some(-1), 4, &[3, 2, 1, 0]),
            // A negative step over an empty sequence must not underflow.
            (None, None, Some(-1), 0, &[]),
            // Saturating bounds: the magnitudes `python_int` produces for a
            // bigint literal must not overflow the walk.
            (Some(isize::MIN), Some(isize::MAX), Some(1), 3, &[0, 1, 2]),
            (Some(isize::MAX), Some(isize::MIN), Some(-1), 3, &[2, 1, 0]),
        ];
        for &(start, stop, step, len, want) in cases {
            assert_eq!(
                slice_positions(start, stop, step, len),
                want,
                "slice({start:?}, {stop:?}, {step:?}) over len {len}"
            );
        }
    }

    /// A one-part spec is `slice(stop)` — the single most consequential rule,
    /// because reading it as the START returns the exact complement.
    #[test]
    fn parse_slice_arg_reads_one_part_as_the_stop() {
        assert_eq!(parse_slice_arg("2"), Some((None, Some(2), None)));
        assert_eq!(parse_slice_arg("-1"), Some((None, Some(-1), None)));
        // Two and three parts read positionally, as `slice()` does.
        assert_eq!(parse_slice_arg("1:2"), Some((Some(1), Some(2), None)));
        assert_eq!(parse_slice_arg("1:2:3"), Some((Some(1), Some(2), Some(3))));
        // An empty part is Python's `if not x: bits.append(None)`.
        assert_eq!(parse_slice_arg(""), Some((None, None, None)));
        assert_eq!(parse_slice_arg(":"), Some((None, None, None)));
        assert_eq!(parse_slice_arg("::"), Some((None, None, None)));
        assert_eq!(parse_slice_arg(":2"), Some((None, Some(2), None)));
    }

    /// `None` is the whole filter's fail-silently path: Django returns the
    /// input unchanged for anything `slice(*bits)` or the indexing raises on.
    #[test]
    fn parse_slice_arg_rejects_what_python_raises_on() {
        // More than three parts: `slice()` takes at most three (TypeError).
        assert_eq!(parse_slice_arg(":::"), None);
        assert_eq!(parse_slice_arg("1:2:3:4"), None);
        // A zero step: ValueError, raised by the indexing.
        assert_eq!(parse_slice_arg("::0"), None);
        assert_eq!(parse_slice_arg("1:2:0"), None);
        // Not an int.
        assert_eq!(parse_slice_arg("x"), None);
        assert_eq!(parse_slice_arg("1:x"), None);
        assert_eq!(parse_slice_arg("1.5:"), None);
        // A lone-space part is NOT empty and NOT an int.
        assert_eq!(parse_slice_arg("1: "), None);
    }

    /// `python_int` is CPython's `int()`, not Rust's `parse::<isize>()`.
    ///
    /// Each accepted form below is one Rust would reject and Django accepts;
    /// the underscore row is the one that fails OPEN if unsupported —
    /// rejecting `1_0` returns the input unchanged, rendering every element
    /// where Django renders none.
    #[test]
    fn python_int_is_cpythons_int() {
        assert_eq!(python_int("1"), Some(1));
        assert_eq!(python_int("-1"), Some(-1));
        assert_eq!(python_int("+1"), Some(1));
        assert_eq!(python_int(" 1 "), Some(1));
        assert_eq!(python_int(" -2 "), Some(-2));
        assert_eq!(python_int("1_0"), Some(10));
        assert_eq!(python_int("1_000_000"), Some(1_000_000));
        assert_eq!(python_int("0"), Some(0));

        // `int()` allows `_` only BETWEEN digits.
        assert_eq!(python_int("_1"), None);
        assert_eq!(python_int("1_"), None);
        assert_eq!(python_int("1__0"), None);
        assert_eq!(python_int("-_1"), None);
        // Everything else `int()` raises on.
        assert_eq!(python_int(""), None);
        assert_eq!(python_int(" "), None);
        assert_eq!(python_int("x"), None);
        assert_eq!(python_int("1.5"), None);
        assert_eq!(python_int("--1"), None);
        assert_eq!(python_int("1x"), None);

        // A magnitude past `isize` saturates rather than failing: Python's int
        // is exact, but every such value is past any representable `len`, so
        // the bound selects the same elements.
        assert_eq!(python_int("99999999999999999999999999"), Some(isize::MAX));
        assert_eq!(python_int("-99999999999999999999999999"), Some(isize::MIN));
    }

    /// The container rule (#2321) survives every spec #2326 unlocked, and a
    /// non-sequence comes back untouched as Django's `TypeError` arm does.
    #[test]
    fn apply_slice_preserves_shape_across_the_new_specs() {
        let items = || {
            vec![
                Value::String("a".into()),
                Value::String("b".into()),
                Value::String("c".into()),
            ]
        };
        for spec in ["-1:", ":-1", "::2", "::-1", "2", "1:2:1"] {
            assert!(
                matches!(
                    apply_slice(&Value::Tuple(items()), spec).unwrap(),
                    Value::Tuple(_)
                ),
                "slice:{spec} of a tuple must stay a tuple"
            );
            assert!(
                matches!(
                    apply_slice(&Value::List(items()), spec).unwrap(),
                    Value::List(_)
                ),
                "slice:{spec} of a list must stay a list"
            );
        }
        // An unparseable spec returns the input, container and all.
        assert_eq!(
            apply_slice(&Value::Tuple(items()), "::0")
                .unwrap()
                .to_string(),
            "('a', 'b', 'c')"
        );
        // A non-sequence is Django's `TypeError` arm: unchanged.
        assert_eq!(
            apply_slice(&Value::Integer(7), ":2").unwrap().to_string(),
            "7"
        );
    }

    /// A string slices by CHARACTER, through the same helper, so a multibyte
    /// input can neither panic on a byte boundary nor answer differently from
    /// the list of its own characters.
    #[test]
    fn apply_slice_of_a_string_walks_codepoints() {
        let s = Value::String("héllo→".into());
        assert_eq!(apply_slice(&s, "::-1").unwrap().to_string(), "→olléh");
        assert_eq!(apply_slice(&s, ":2").unwrap().to_string(), "hé");
        assert_eq!(apply_slice(&s, "-1:").unwrap().to_string(), "→");
        assert_eq!(apply_slice(&s, "::2").unwrap().to_string(), "hlo");
    }

    /// `rebuild_like` is [`iter_values`]'s counterpart: iteration decides what
    /// a filter READS, this decides what it HANDS BACK (#2321).
    #[test]
    fn rebuild_like_returns_the_container_it_was_given() {
        let items = || vec![Value::Integer(1)];
        assert!(matches!(
            rebuild_like(&Value::Tuple(vec![]), items()),
            Value::Tuple(_)
        ));
        assert!(matches!(
            rebuild_like(&Value::List(vec![]), items()),
            Value::List(_)
        ));
        // Empty in, empty out — `()` and `[]` are different reprs, so the
        // empty case is as shape-sensitive as the populated one.
        assert_eq!(
            rebuild_like(&Value::Tuple(vec![]), Vec::new()).to_string(),
            "()"
        );
        assert_eq!(
            rebuild_like(&Value::List(vec![]), Vec::new()).to_string(),
            "[]"
        );
    }

    /// The elements are carried through untouched. A "shape-preserving"
    /// rebuild that dropped or reordered items would pass the matches above.
    #[test]
    fn rebuild_like_does_not_touch_the_items() {
        let built = rebuild_like(
            &Value::Tuple(vec![Value::String("ignored".to_string())]),
            vec![Value::Integer(1), Value::String("<b>".to_string())],
        );
        assert_eq!(built.to_string(), "(1, '<b>')");
    }

    /// `escape` is EAGER — `conditional_escape`, exactly as Django registers it
    /// (#2281). It was a no-op deferring to render-time auto-escaping, which is
    /// indistinguishable for `{{ p|escape }}` alone and wrong for every chain:
    /// the next filter saw the raw value, and `{{ p|escape|safe }}` emitted it.
    #[test]
    fn test_escape_filter_escapes_eagerly() {
        let value = Value::String("<script>alert('xss')</script>".to_string());
        let result = apply_filter("escape", &value, None).unwrap();
        assert_eq!(
            result.to_string(),
            "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;"
        );
    }

    /// The half that separates `escape` from `force_escape`: a value already
    /// safe passes through untouched, so `{{ p|safe|escape }}` is not
    /// double-escaped. `apply_filter` cannot know the provenance, so this goes
    /// through the full entry point with the flag the renderer supplies.
    #[test]
    fn escape_does_not_double_escape_a_safe_value() {
        let value = Value::String("<b>hi</b>".to_string());
        let (got, _) = apply_filter_full_safe(
            "escape",
            &value,
            None,
            None,
            true,
            InputSafety {
                container: true,
                items: false,
            },
        )
        .unwrap();
        assert_eq!(got.to_string(), "<b>hi</b>");
        // `force_escape` is `escape()`, not `conditional_escape()` — it escapes
        // a `SafeString` too. That is the entire difference between the two.
        let (got, _) = apply_filter_full_safe(
            "force_escape",
            &value,
            None,
            None,
            true,
            InputSafety {
                container: true,
                items: false,
            },
        )
        .unwrap();
        assert_eq!(got.to_string(), "&lt;b&gt;hi&lt;/b&gt;");
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

        // `Missing` is an ABSENT variable, which Django has already replaced
        // with `string_if_invalid` (`""`) before the filter runs — falsy, and
        // not `None`. Corrected in #2401: this row pinned djust's own pre-fix
        // answer, and a live Django render disagrees
        // (`{{ absent|yesno:"a,b,c" }}` is `b`).
        let value = Value::Missing;
        let result = apply_filter("yesno", &value, Some("yeah,nope,dunno")).unwrap();
        assert_eq!(result.to_string(), "nope");

        // Python `None` is the row that takes the third part.
        let value = Value::None;
        let result = apply_filter("yesno", &value, Some("yeah,nope,dunno")).unwrap();
        assert_eq!(result.to_string(), "dunno");

        // Fewer than two parts: Django returns the VALUE (#2401).
        let value = Value::String("abc".to_string());
        let result = apply_filter("yesno", &value, Some("only")).unwrap();
        assert_eq!(result.to_string(), "abc");

        // Not exactly three parts: the unpack raises and `maybe` falls back to
        // `bits[1]` — for FOUR parts as well as for two.
        let value = Value::None;
        let result = apply_filter("yesno", &value, Some("a,b,c,d")).unwrap();
        assert_eq!(result.to_string(), "b");
        let result = apply_filter("yesno", &value, Some("a,b")).unwrap();
        assert_eq!(result.to_string(), "b");
    }

    #[test]
    fn test_get_digit_below_one_returns_the_converted_int() {
        // `value = int(value)` runs BEFORE `if arg < 1`, so this exit hands
        // back the CONVERTED number and not the input (#2403).
        let result = apply_filter("get_digit", &Value::Bool(false), Some("0")).unwrap();
        assert_eq!(result.to_string(), "0");
        let result = apply_filter("get_digit", &Value::Float(1.5), Some("0")).unwrap();
        assert_eq!(result.to_string(), "1");
        let result = apply_filter("get_digit", &Value::Float(1.5), Some("-1")).unwrap();
        assert_eq!(result.to_string(), "1");
        // …and it is a NUMBER, so the rest of a chain does arithmetic.
        let result = apply_filter("get_digit", &Value::Float(1.5), Some("0")).unwrap();
        assert!(matches!(result, Value::Integer(1)));
        // `int(value)` raising is the OTHER exit: the input, unchanged.
        let value = Value::String("abc".to_string());
        let result = apply_filter("get_digit", &value, Some("0")).unwrap();
        assert_eq!(result.to_string(), "abc");
    }

    #[test]
    fn test_timesince_refuses_an_unreadable_value_and_empties_a_falsy_one() {
        // Django's `if not value: return ""` (#2399).
        for falsy in [
            Value::String(String::new()),
            Value::None,
            Value::Missing,
            Value::Bool(false),
            Value::Integer(0),
            Value::List(vec![]),
        ] {
            let result = apply_filter("timesince", &falsy, None).unwrap();
            assert_eq!(
                result.to_string(),
                "",
                "falsy {falsy:?} must be the empty string"
            );
            let result = apply_filter("timeuntil", &falsy, None).unwrap();
            assert_eq!(result.to_string(), "");
        }
        // A truthy non-date reaches `value.year` in Django, which raises
        // `AttributeError` past both of its `except`s. It used to be ECHOED.
        for truthy in [
            Value::String("abc".to_string()),
            Value::Integer(5),
            Value::Bool(true),
            Value::List(vec![Value::String("a".to_string())]),
        ] {
            assert!(
                apply_filter("timesince", &truthy, None).is_err(),
                "timesince must refuse {truthy:?}"
            );
            assert!(apply_filter("timeuntil", &truthy, None).is_err());
        }
        // The message must not carry the value — it reaches logs and the
        // client's error frame.
        let payload = Value::String("<script>alert(1)</script>".to_string());
        let err = apply_filter("timesince", &payload, None).unwrap_err();
        assert!(!format!("{err:?}").contains("<script>"), "{err:?}");
    }

    #[test]
    fn test_linebreaks_filter() {
        // Was `contains("<p>")` + `contains("<br>")`, which is true of the old
        // output too — it passed while the paragraph JOIN was `\n` instead of
        // Django's `\n\n` (#2259). Exact bytes, so the join is pinned.
        let value = Value::String("Line 1\nLine 2\n\nParagraph 2".to_string());
        let result = apply_filter("linebreaks", &value, None).unwrap();
        assert_eq!(
            result.to_string(),
            "<p>Line 1<br>Line 2</p>\n\n<p>Paragraph 2</p>"
        );
    }

    #[test]
    fn test_linebreaksbr_filter() {
        let value = Value::String("Line 1\nLine 2\nLine 3".to_string());
        let result = apply_filter("linebreaksbr", &value, None).unwrap();
        assert_eq!(result.to_string(), "Line 1<br>Line 2<br>Line 3");
    }

    /// The half that makes the `SAFE_OUTPUT_FILTERS` membership safe (#2259).
    ///
    /// `linebreaks`/`linebreaksbr` are exempt from the renderer's auto-escape
    /// ONLY because they escape their own input. If this test goes red, the
    /// name must come out of `renderer::SAFE_OUTPUT_FILTERS` in the same commit
    /// — the two are one change.
    #[test]
    fn linebreaks_escapes_its_input_which_is_what_makes_marking_it_safe_safe() {
        let attack = Value::String("<img src=x onerror=alert(1)>\n</script><script>".to_string());
        for name in ["linebreaks", "linebreaksbr"] {
            let out = apply_filter(name, &attack, None).unwrap().to_string();
            assert!(
                !out.contains("<img") && !out.contains("<script") && !out.contains("</script"),
                "{name} leaked live markup: {out:?}"
            );
            assert!(
                out.contains("&lt;img src=x onerror=alert(1)&gt;"),
                "{name} did not escape the payload: {out:?}"
            );
            // The tags the FILTER generates stay live — that is the point.
            assert!(out.contains("<br>"), "{name} lost its own markup: {out:?}");
        }
    }

    /// The SECOND arm of that contract, added with #2284.
    ///
    /// The test above proves the escape happens; since #2284 the escape is
    /// CONDITIONAL, so proving it happens is only half of what
    /// `SAFE_OUTPUT_FILTERS` membership now rests on. This pins the other half
    /// in both directions: `autoescape=false` really does emit the input
    /// verbatim (or the flag is dead and #2284 is a no-op), and it is
    /// reachable ONLY through the flag — `apply_filter`, the entry point with
    /// no view of the chain, still escapes.
    ///
    /// If either assertion goes red, the four names must come out of
    /// `renderer::SAFE_OUTPUT_FILTERS` in the same commit.
    #[test]
    fn the_needs_autoescape_filters_skip_the_escape_only_when_told_to() {
        let attack = "<img src=x onerror=alert(1)>\nsecond line";

        // autoescape = false: verbatim. Django's `linebreaks(v, False)`.
        assert!(linebreaks(attack, false).contains("<img src=x onerror=alert(1)>"));
        assert!(linebreaksbr(attack, false).contains("<img src=x onerror=alert(1)>"));
        assert!(urlize(attack, None, false).contains("<img src=x onerror=alert(1)>"));

        // autoescape = true: escaped, and no live payload survives.
        for out in [
            linebreaks(attack, true),
            linebreaksbr(attack, true),
            urlize(attack, None, true),
        ] {
            assert!(!out.contains("<img"), "leaked live markup: {out:?}");
            assert!(
                out.contains("&lt;img src=x onerror=alert(1)&gt;"),
                "did not escape the payload: {out:?}"
            );
        }

        // And the flag is the ONLY way in: the chain-blind entry point escapes.
        let value = Value::String(attack.to_string());
        for name in ["linebreaks", "linebreaksbr", "urlize"] {
            let out = apply_filter(name, &value, None).unwrap().to_string();
            assert!(
                !out.contains("<img"),
                "{name} via apply_filter leaked: {out:?}"
            );
        }
    }

    /// `urlize`'s href escape is NOT conditional, because it lands inside
    /// `href="…"`. Django writes `escape(url)` outside its `if autoescape`
    /// branch for exactly this reason, and making it conditional along with
    /// the display text is the obvious way to write #2284 and an XSS Django
    /// does not have.
    #[test]
    fn urlize_escapes_the_href_even_when_autoescape_is_off() {
        let out = urlize("http://ex.com/?a=1&b=2", None, false);
        let href = out
            .split("href=\"")
            .nth(1)
            .and_then(|s| s.split('"').next())
            .unwrap_or_default();
        assert!(
            href.contains("&amp;"),
            "the href carried a raw & under autoescape=false: {out:?}"
        );
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
        // The separator is U+00A0, spelled as an escape so it survives an
        // editor (#2264). These three cells asserted a PLAIN space and passed
        // for the whole life of the filter while every rendered byte was wrong.
        let cases = [
            (Value::Integer(1024), "1.0\u{a0}KB"),
            (Value::Integer(1048576), "1.0\u{a0}MB"),
            (Value::Integer(500), "500\u{a0}bytes"),
            // Pluralization, and the negative that used to skip every unit.
            (Value::Integer(1), "1\u{a0}byte"),
            (Value::Integer(0), "0\u{a0}bytes"),
            (Value::Integer(-1), "-1\u{a0}byte"),
            (Value::Integer(-1024), "-1.0\u{a0}KB"),
            // `int(bytes_)` coercion: a string parses, everything else is 0.
            (Value::String("1024".into()), "1.0\u{a0}KB"),
            (Value::String("19.99".into()), "0\u{a0}bytes"),
            (Value::None, "0\u{a0}bytes"),
            (Value::Bool(true), "1\u{a0}byte"),
            (Value::List(vec![Value::Integer(1)]), "0\u{a0}bytes"),
            // Exact `i128` truncation, where `as_f64() as i64` saturated to
            // `8192.0 PB`.
            (
                Value::Decimal("12345678901234567890.123456789".into()),
                "10965.2\u{a0}PB",
            ),
        ];
        for (value, expected) in cases {
            let got = apply_filter("filesizeformat", &value, None)
                .unwrap()
                .to_string();
            assert_eq!(got, expected, "filesizeformat({value:?})");
        }
    }

    /// The nbsp is a distinct byte from a space — asserted structurally so a
    /// future edit cannot re-introduce the plain space and stay green (#2264).
    #[test]
    fn filesizeformat_joins_with_a_non_breaking_space_not_a_plain_one() {
        let out = apply_filter("filesizeformat", &Value::Integer(2048), None)
            .unwrap()
            .to_string();
        assert!(!out.contains(' '), "found a PLAIN space in {out:?}");
        assert!(out.contains('\u{a0}'), "no U+00A0 in {out:?}");
        assert_eq!(out.chars().filter(|c| *c == '\u{a0}').count(), 1);
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
        // #725 read "invalid date strings return original value (Django
        // convention)". The second half was never true — Django's `date` ends
        // `except AttributeError: return ""`, measured — and returning the
        // input was the more permissive direction, since it put unparsed
        // upstream data on the page. #2359 gave both arms Django's own answer.
        let invalid_date = Value::String("2026-13-45".to_string());
        let result = apply_filter("date", &invalid_date, Some("Y-m-d")).unwrap();
        assert_eq!(result.to_string(), "");

        let not_a_date = Value::String("not-a-date".to_string());
        let result = apply_filter("date", &not_a_date, Some("Y-m-d")).unwrap();
        assert_eq!(result.to_string(), "");

        // ...and "Django's own answer" is not unconditionally empty: a format
        // carrying no specifier never touches the value, so its literal text
        // comes back. `django_literal_only_format` states that rule; this is
        // the arm that would go unexercised if the failure path were a flat
        // `String::new()`.
        let result = apply_filter("date", &not_a_date, Some("1-1")).unwrap();
        assert_eq!(result.to_string(), "1-1");

        let empty = Value::String("".to_string());
        let result = apply_filter("date", &empty, Some("Y-m-d")).unwrap();
        assert_eq!(result.to_string(), "");

        let partial = Value::String("2026-03".to_string());
        let result = apply_filter("date", &partial, Some("Y-m-d")).unwrap();
        assert_eq!(result.to_string(), "");
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
        dict1.insert("name".into(), Value::String("Charlie".to_string()));
        dict1.insert("age".into(), Value::Integer(30));

        let mut dict2 = IndexMap::new();
        dict2.insert("name".into(), Value::String("Alice".to_string()));
        dict2.insert("age".into(), Value::Integer(25));

        let mut dict3 = IndexMap::new();
        dict3.insert("name".into(), Value::String("Bob".to_string()));
        dict3.insert("age".into(), Value::Integer(35));

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
        dict1.insert("name".into(), Value::String("Alice".to_string()));

        let mut dict2 = IndexMap::new();
        dict2.insert("name".into(), Value::String("Bob".to_string()));

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

        // The question mark is encoded; the SLASH is not (#2262). This case
        // previously asserted `path%2Fto%2F…`, which pinned the bug: Django's
        // filter is `quote(value, safe=…)` and `quote`'s own default safe set
        // is `"/"`. The argument form below is what encodes a separator.
        let value = Value::String("path/to/file?query=1".to_string());
        let result = apply_filter("urlencode", &value, None).unwrap();
        assert_eq!(result.to_string(), "path/to/file%3Fquery%3D1");

        let result = apply_filter("urlencode", &value, Some("")).unwrap();
        assert_eq!(result.to_string(), "path%2Fto%2Ffile%3Fquery%3D1");

        // An explicit safe set adds to the always-safe characters.
        let value = Value::String("a&b/c".to_string());
        let result = apply_filter("urlencode", &value, Some("&")).unwrap();
        assert_eq!(result.to_string(), "a&b%2Fc");
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

    /// The exponent carries a SIGN and at least two digits (#2358 group 3).
    ///
    /// This test asserted `1.23e3` — Rust's `{:e}`, which writes neither —
    /// and was green for as long as the bug existed, because it pinned
    /// djust's answer rather than CPython's. `"%.2e" % 1234.5` is
    /// `'1.23e+03'`.
    #[test]
    fn test_stringformat_filter_scientific() {
        let value = Value::Float(1234.5);
        let result = apply_filter("stringformat", &value, Some(".2e")).unwrap();
        assert_eq!(result.to_string(), "1.23e+03");

        let result = apply_filter("stringformat", &value, Some(".2E")).unwrap();
        assert_eq!(result.to_string(), "1.23E+03");
    }

    #[test]
    fn test_stringformat_filter_default() {
        // There is no default. `stringformat` takes a REQUIRED argument, so
        // Django refuses `{{ p|stringformat }}` at compile time — "stringformat
        // requires 2 arguments, 1 provided" — and this pinned djust rendering
        // it (#2400). Corrected rather than relaxed: the name kept, because the
        // question it asks ("what happens with no argument") still has an
        // answer, and the answer is a refusal.
        let value = Value::Integer(42);
        let err = apply_filter("stringformat", &value, None).unwrap_err();
        assert!(
            format!("{err:?}").contains("stringformat requires 2 arguments, 1 provided"),
            "{err:?}"
        );
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
        // Same as `test_stringformat_filter_default`: `wordwrap`'s argument is
        // required, so there is no no-argument behaviour to pin (#2400).
        let value = Value::String("short".to_string());
        let err = apply_filter("wordwrap", &value, None).unwrap_err();
        assert!(
            format!("{err:?}").contains("wordwrap requires 2 arguments, 1 provided"),
            "{err:?}"
        );
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

    /// `str.center`'s odd-margin tie-break (#2294).
    ///
    /// Rust's `{:^width$}` — what this arm used — always puts the SMALLER half
    /// on the left. CPython adds `(marg & width & 1)`, which biases left only
    /// when the width is odd too. Every row below has an ODD margin; the first
    /// three are the cells the issue reports and the last two are the
    /// even-width cases where the two rules coincide, kept so a "fix" that
    /// merely flips the bias fails here.
    #[test]
    fn test_center_uses_pythons_odd_margin_tie_break() {
        for (value, width, want) in [
            ("ab", 5usize, "  ab "),
            ("abcd", 5, " abcd"),
            ("ab", 3, " ab"),
            ("a", 4, " a  "),
            ("abc", 6, " abc  "),
            // Width <= len is the input unchanged, not a pad of zero.
            ("abcdef", 3, "abcdef"),
            ("abc", 3, "abc"),
            // Code points, not bytes: three-byte CJK still pads to 5 chars.
            ("\u{4e2d}", 5, "  \u{4e2d}  "),
            ("\u{4e2d}", 4, " \u{4e2d}  "),
        ] {
            let got = apply_filter(
                "center",
                &Value::String(value.to_string()),
                Some(&width.to_string()),
            )
            .unwrap()
            .to_string();
            assert_eq!(got, want, "{value:?}.center({width})");
        }
    }

    /// The `%s` spec grammar (#2294). The Python-side sweep in
    /// `python/tests/test_measuring_filter_parity_2294.py` is the exhaustive
    /// check against CPython; these rows pin the parser's own branches.
    #[test]
    fn test_stringformat_s_honours_flags_width_and_precision() {
        for (spec, want) in [
            ("s", "abcdef"),
            ("10s", "    abcdef"),
            ("-10s", "abcdef    "),
            // The `0` flag does NOT zero-pad `%s`, unlike `%d`.
            ("010s", "    abcdef"),
            // Accepted-and-ignored flags, repeated flags, flag after flag.
            ("+10s", "    abcdef"),
            (" 10s", "    abcdef"),
            ("#10s", "    abcdef"),
            ("--10s", "abcdef    "),
            // A bare `.` is precision ZERO.
            (".s", ""),
            ("10.s", "          "),
            (".3s", "abc"),
            ("10.3s", "       abc"),
            ("-10.3s", "abc       "),
            // `0` leads as a FLAG, so these are width 0.
            ("0s", "abcdef"),
            ("00s", "abcdef"),
            // Trailing length modifiers are accepted and ignored.
            ("hs", "abcdef"),
            ("10ls", "    abcdef"),
            ("10Ls", "    abcdef"),
            // Leading zeros do not count toward the digit-length limit.
            ("00000000000000000000010s", "    abcdef"),
            // One past each limit -> CPython `ValueError` -> Django's "".
            ("9223372036854775808s", ""),
            (".2147483648s", ""),
            // One below the precision limit is still accepted.
            (".2147483647s", "abcdef"),
            // The prefix holds another CONVERSION, so `%as` is `%a`
            // followed by the literal `s` and the answer is the ascii-repr
            // plus that letter. #2294 left these at `value.to_string()`
            // because it had no grammar to hand; #2358 gave it one, and both
            // rows moved to CPython's answer.
            ("as", "'abcdef's"),
            // `!` is not a flag, a digit, a `.`, a length modifier or a
            // conversion, so the parse stops on it: `unsupported format
            // character`.
            ("!s", ""),
        ] {
            let got = crate::stringformat::apply(&Value::String("abcdef".to_string()), spec);
            assert_eq!(got, want, "%{spec}");
        }
    }

    /// Precision and width are CODE-POINT counts, not byte counts.
    #[test]
    fn test_stringformat_s_counts_code_points() {
        let v = Value::String("\u{4e2d}\u{6587}\u{5b57}".to_string());
        assert_eq!(
            crate::stringformat::apply(&v, "6s"),
            "   \u{4e2d}\u{6587}\u{5b57}"
        );
        assert_eq!(crate::stringformat::apply(&v, ".2s"), "\u{4e2d}\u{6587}");
        assert_eq!(
            crate::stringformat::apply(&v, "5.2s"),
            "   \u{4e2d}\u{6587}"
        );
    }

    /// `length` of a `Value::Object` (#2294): a dict counts, a serialized
    /// object does not.
    #[test]
    fn test_length_of_an_object_uses_the_str_marker() {
        let mut dict = indexmap::IndexMap::new();
        dict.insert("a".into(), Value::Integer(1));
        dict.insert("b".into(), Value::Integer(2));
        assert_eq!(
            apply_filter("length", &Value::Object(dict.clone()), None)
                .unwrap()
                .to_string(),
            "2"
        );

        // A serialized model: `len(model)` raises `TypeError`, which Django's
        // `length` answers 0 to.
        let mut model = dict.clone();
        model.insert("__str__".into(), Value::String("bob".to_string()));
        assert_eq!(
            apply_filter("length", &Value::Object(model), None)
                .unwrap()
                .to_string(),
            "0"
        );

        // A non-string `"__str__"` is not a marker — `Display` falls back to
        // dict repr for it, so `length` must count it.
        let mut broken = dict.clone();
        broken.insert("__str__".into(), Value::None);
        assert_eq!(
            apply_filter("length", &Value::Object(broken), None)
                .unwrap()
                .to_string(),
            "3"
        );

        // An empty dict was already right and stays right.
        assert_eq!(
            apply_filter("length", &Value::Object(indexmap::IndexMap::new()), None)
                .unwrap()
                .to_string(),
            "0"
        );
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
        map.insert("a\nb\tc\rd\\e\"f".into(), Value::String("v".to_string()));
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
            map.insert(format!("k{c}").into(), Value::String(format!("v{c}")));
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

    /// `json.dumps`'s float, at the helper rather than through a render (#2270).
    ///
    /// Django-independent, so it holds in a Rust-only checkout: the three
    /// non-finite names are CPython's encoder constants and the finite arm is
    /// `repr`. The `1.0` row is the one the issue's table omits — Rust's `{}`
    /// wrote it as `1`, turning a JSON float into a JSON integer.
    #[test]
    fn json_float_body_is_repr_plus_the_three_non_finite_names_2270() {
        for (input, want) in [
            (0.0, "0.0"),
            (-0.0, "-0.0"),
            (1.0, "1.0"),
            (0.5, "0.5"),
            (1e15, "1000000000000000.0"),
            (1e16, "1e+16"),
            (1e20, "1e+20"),
            (1e300, "1e+300"),
            (-1e300, "-1e+300"),
            (1e-5, "1e-05"),
            (1e-4, "0.0001"),
            (5e-324, "5e-324"),
            (f64::INFINITY, "Infinity"),
            (f64::NEG_INFINITY, "-Infinity"),
        ] {
            assert_eq!(json_float_body(input), want, "json_float_body({input})");
        }
        assert_eq!(json_float_body(f64::NAN), "NaN");
    }

    /// The two sinks disagree on the non-finite values, so one helper cannot
    /// serve both — and Rust's `{}` was accidentally right on half of each.
    #[test]
    fn pprint_and_json_spell_the_non_finite_floats_differently_2270() {
        for (input, want_pprint, want_json) in [
            (f64::INFINITY, "inf", "Infinity"),
            (f64::NEG_INFINITY, "-inf", "-Infinity"),
        ] {
            assert_eq!(crate::pprint::pformat(&Value::Float(input)), want_pprint);
            assert_eq!(json_float_body(input), want_json);
        }
        assert_eq!(crate::pprint::pformat(&Value::Float(f64::NAN)), "nan");
        assert_eq!(json_float_body(f64::NAN), "NaN");
    }

    /// Both filters RECURSE, so the container arms are part of the surface.
    #[test]
    fn a_nested_float_takes_the_same_spelling_2270() {
        let nested = Value::List(vec![Value::Object(
            [(djust_core::ObjectKey::from("k"), Value::Float(1e20))]
                .into_iter()
                .collect(),
        )]);
        assert_eq!(crate::pprint::pformat(&nested), "[{'k': 1e+20}]");
        assert_eq!(value_to_json(&nested), "[{\"k\": 1e+20}]");

        let integral = Value::Tuple(vec![Value::Float(1.0), Value::Float(-0.0)]);
        assert_eq!(crate::pprint::pformat(&integral), "(1.0, -0.0)");
        assert_eq!(value_to_json(&integral), "[1.0, -0.0]");
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
        // Django's format is `"%0" + width + "d. %s"` — ZERO padded, not space
        // padded (#2259). This asserted ` 1. line`, which is what djust used to
        // emit and what Django never emits.
        let lines: Vec<&str> = (0..12).map(|_| "line").collect();
        let value = Value::String(lines.join("\n"));
        let result = apply_filter("linenumbers", &value, None).unwrap();
        let output = result.to_string();
        assert!(output.starts_with("01. line"), "{output:?}");
        assert!(output.contains("\n12. line"), "{output:?}");
        assert!(
            !output.contains(" 1. line"),
            "space padding is back: {output:?}"
        );
    }

    #[test]
    fn test_get_digit_filter() {
        // 1 = rightmost digit
        let value = Value::String("12345".to_string());
        let result = apply_filter("get_digit", &value, Some("1")).unwrap();
        assert_eq!(result.to_string(), "5");

        let result = apply_filter("get_digit", &value, Some("3")).unwrap();
        assert_eq!(result.to_string(), "3");

        // Out of range is `0`, NOT the original: Django's `except IndexError`
        // arm returns 0, and the `return value` arm is only for the `int(value)`
        // ValueError. Corrected in #2260 — this case had pinned djust's own
        // pre-fix behaviour, and a live Django render disagrees:
        // `{{ "12345"|get_digit:10 }}` is `0`.
        let result = apply_filter("get_digit", &value, Some("10")).unwrap();
        assert_eq!(result.to_string(), "0");

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
        // ONE `…`, and it costs ONE of the fifteen characters (#2346). This
        // read `s.contains("...")` and was the only test the three-dot
        // spelling had; `Urlizer.trim_url` appends `"%s…" % x[:max(0, limit-1)]`,
        // so the displayed text is `https://example` truncated to 14 plus the
        // ellipsis.
        assert!(s.contains(">https://exampl\u{2026}</a>"), "{s}");
        assert!(!s.contains("..."), "{s}");
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
