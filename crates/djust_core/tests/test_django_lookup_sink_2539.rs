//! ADR-027 movement 1 (#2539): the DORMANT Django-lookup sink,
//! `Context::walk_live`, unit-tested per rule of
//! `django.template.base.Variable._resolve_lookup` (django 5.2.16
//! `base.py:876-953`) against live Python objects.
//!
//! The helper is defined and tested here and routed NOWHERE — the structural
//! pin that `lookup_segment` does not call it is
//! `TestTheSinkIsDefinedButUnrouted2539` in
//! `python/tests/test_adr027_characterization_net_2539.py`. Movement 2 wires
//! it; this file is what the wiring is measured against.
//!
//! Fixtures are plain Python, compiled with `PyModule::from_code` — NO Django
//! import, so the test does not require Django on the embedded interpreter's
//! path (`protect_sidecar` and the ORM auto-call warning both fail soft when
//! `djust.serialization` / `django.db.models` cannot be imported).
//!
//! IMPORTANT — this file intentionally contains a SINGLE `#[test]`. cargo's
//! default harness runs `#[test]`s on parallel threads, and multiple threads
//! concurrently attaching to the embedded CPython interpreter deadlock
//! (`crates/djust_templates/tests/test_block_custom_tag_arg_json_2042.rs`).
//! Keep every case in one test; do NOT split without `--test-threads=1`.
//!
//! Run with an embeddable interpreter (#2072):
//! `PYO3_PYTHON="$(bash scripts/embeddable-python.sh)" cargo test -p djust_core --test test_django_lookup_sink_2539`

use djust_core::context::Walked;
use djust_core::{Context, DjangoRustError};
use pyo3::ffi::c_str;
use pyo3::prelude::*;
use pyo3::types::PyString;

const FIXTURES: &std::ffi::CStr = c_str!(
    r#"
class MyClass(list):
    """Django's `test_subscriptable_class` shape (ADR-027 row P): a CLASS in
    the context whose METACLASS has no `__getitem__` but which answers
    `__class_getitem__` — the call Django's step-1 guard never makes."""
    class_property = "Example property"
    do_not_call_in_templates = True
    class_getitem_calls = 0

    def __class_getitem__(cls, item):
        cls.class_getitem_calls += 1
        return list[item]


class Doodad:
    """Django's `test_callables.Doodad`."""
    def __init__(self, value):
        self.num_calls = 0
        self.value = value

    def __call__(self):
        self.num_calls += 1
        return {"the_value": self.value}


class DoodadAlters(Doodad):
    alters_data = True


class Keeper:
    def keep(self):
        return "kept"

    keep.do_not_call_in_templates = True


def needs_args(x):
    return x


def nullary_type_error():
    raise TypeError("raised INSIDE a nullary")


class Silent(Exception):
    silent_variable_failure = True


class NotSilent(Exception):
    silent_variable_failure = False


class Raiser:
    plain = "attr-plain"

    @property
    def attr_err(self):
        raise AttributeError("raised by a @property")

    @property
    def silent(self):
        raise Silent("quiet")

    @property
    def loud_false(self):
        raise NotSilent("explicit-false")

    def silent_method(self):
        raise Silent("quiet-method")


class GetItemRaiser:
    def __getitem__(self, key):
        raise RuntimeError("getitem authz")


class NpLike:
    foo = "attr-foo"

    def __getitem__(self, key):
        raise ValueError("bad index")


class Sub:
    sub = "deep"


class Nested:
    attr = Sub()


COUNTER = {"n": 0}


def counted():
    COUNTER["n"] += 1
    return "foo bar"


order_dict = {"0": "s", 0: "i"}
items_dict = {"items": "the-key"}
a_list = ["zero", "one"]
"#
);

/// Run the sink over `root` with `parts`, labelling the ORM warning with the
/// dotted path the way the current walk does.
fn walk<'py>(
    ctx: &Context,
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    parts: &[&str],
) -> djust_core::Result<Walked<'py>> {
    ctx.walk_live(py, root.clone(), parts, &parts.join("."))
}

/// `Walked::Object` whose value `str()`s to `expected`, else a labelled panic.
fn expect_str(label: &str, walked: djust_core::Result<Walked<'_>>, expected: &str) {
    match walked {
        Ok(Walked::Object(obj)) => {
            let shown: String = obj.str().expect("str()").extract().expect("extract str");
            assert_eq!(shown, expected, "{label}: wrong object");
        }
        Ok(Walked::Invalid) => panic!("{label}: got Invalid, expected {expected:?}"),
        Err(e) => panic!("{label}: got Err({e}), expected {expected:?}"),
    }
}

fn expect_invalid(label: &str, walked: djust_core::Result<Walked<'_>>) {
    match walked {
        Ok(Walked::Invalid) => {}
        Ok(Walked::Object(obj)) => panic!("{label}: got Object({obj:?}), expected Invalid"),
        Err(e) => panic!("{label}: got Err({e}), expected Invalid"),
    }
}

/// `Err` carrying a Python exception of `type_name`, else a labelled panic.
fn expect_raises(
    label: &str,
    py: Python<'_>,
    walked: djust_core::Result<Walked<'_>>,
    type_name: &str,
) {
    match walked {
        Err(DjangoRustError::PythonException(err)) => {
            let got = err.get_type(py).name().expect("type name").to_string();
            assert_eq!(got, type_name, "{label}: wrong exception type");
        }
        Err(other) => panic!("{label}: got a non-Python error {other}, expected {type_name}"),
        Ok(Walked::Invalid) => panic!("{label}: got Invalid, expected {type_name} to propagate"),
        Ok(Walked::Object(obj)) => {
            panic!("{label}: got Object({obj:?}), expected {type_name} to propagate")
        }
    }
}

fn int_attr(obj: &Bound<'_, PyAny>, name: &str) -> i64 {
    obj.getattr(name).expect(name).extract().expect("int")
}

#[test]
fn walk_live_transcribes_djangos_resolve_lookup() {
    Python::initialize();
    Python::attach(|py| {
        let m = PyModule::from_code(py, FIXTURES, c_str!("sink_2539.py"), c_str!("sink_2539"))
            .expect("compile fixtures");
        let ctx = Context::new();
        let get = |name: &str| m.getattr(name).expect(name);
        let call = |name: &str, arg: i64| get(name).call1((arg,)).expect(name);

        // 1. The step-1 metaclass guard (row P; the gate-off case). Delete the
        //    `hasattr(type(current), "__getitem__")` guard and `get_item`
        //    honours `__class_getitem__`: the result is a `types.GenericAlias`
        //    (not a `str`) and the counter is 1. Asserted as a `PyString`
        //    instance so the failure is an assertion here rather than the
        //    conversion segfault the current walk hits.
        let my_class = get("MyClass");
        match walk(&ctx, py, &my_class, &["class_property"]) {
            Ok(Walked::Object(obj)) => {
                assert!(
                    obj.is_instance_of::<PyString>(),
                    "1 metaclass guard: item access reached __class_getitem__ — got {obj:?}"
                );
                let s: String = obj.extract().expect("str");
                assert_eq!(s, "Example property", "1 metaclass guard");
            }
            other => panic!("1 metaclass guard: {:?}", other.map(|_| ())),
        }
        assert_eq!(
            int_attr(&my_class, "class_getitem_calls"),
            0,
            "1 metaclass guard: __class_getitem__ was CALLED"
        );

        // 2. Order: item access BEFORE attribute access, with the segment as
        //    a STRING key (`d["0"]`, not `d[0]`); a dict key named `items`
        //    beats the `dict.items` method.
        expect_str(
            "2 order str-key",
            walk(&ctx, py, &get("order_dict"), &["0"]),
            "s",
        );
        expect_str(
            "2 order items-key",
            walk(&ctx, py, &get("items_dict"), &["items"]),
            "the-key",
        );

        // 3. Django's `test_callables` shapes. The ROOT is called before any
        //    segment is walked; `alters_data` refuses the call and the whole
        //    expression is invalid.
        let doodad = call("Doodad", 42);
        expect_invalid(
            "3 test_callable d.value",
            walk(&ctx, py, &doodad, &["value"]),
        );
        assert_eq!(
            int_attr(&doodad, "num_calls"),
            1,
            "3 test_callable: root call count"
        );
        expect_str(
            "3 d.the_value",
            walk(&ctx, py, &doodad, &["the_value"]),
            "42",
        );
        assert_eq!(
            int_attr(&doodad, "num_calls"),
            2,
            "3 the_value: root call count"
        );
        let alters = call("DoodadAlters", 42);
        expect_invalid(
            "3 test_alters_data d.value",
            walk(&ctx, py, &alters, &["value"]),
        );
        expect_invalid(
            "3 test_alters_data d.the_value",
            walk(&ctx, py, &alters, &["the_value"]),
        );
        assert_eq!(
            int_attr(&alters, "num_calls"),
            0,
            "3 alters_data: was CALLED"
        );

        // 4. `do_not_call_in_templates`: the bound method is returned AS IS
        //    (row A's Django answer — the caller renders `str()` of it).
        let keeper = get("Keeper").call0().expect("Keeper()");
        match walk(&ctx, py, &keeper, &["keep"]) {
            Ok(Walked::Object(obj)) => {
                assert!(obj.is_callable(), "4 do_not_call: not the bound method");
                assert!(
                    obj.hasattr("__func__").unwrap_or(false),
                    "4 do_not_call: not a bound method"
                );
                let shown: String = obj.str().expect("str").extract().expect("str");
                assert!(
                    shown.starts_with("<bound method Keeper.keep of "),
                    "4 do_not_call: {shown}"
                );
            }
            other => panic!("4 do_not_call: {:?}", other.map(|_| ())),
        }

        // 5. An args-required callable is invalid (Django's
        //    `signature().bind()` probe); a `TypeError` raised INSIDE a
        //    nullary is a real error and propagates.
        expect_invalid("5 args-required", walk(&ctx, py, &get("needs_args"), &[]));
        expect_raises(
            "5 TypeError inside nullary",
            py,
            walk(&ctx, py, &get("nullary_type_error"), &[]),
            "TypeError",
        );

        // 6. Step 2's `dir()` re-raise: a property raising `AttributeError`
        //    propagates because the name EXISTS; an absent name is invalid.
        let raiser = get("Raiser").call0().expect("Raiser()");
        expect_raises(
            "6 property AttributeError",
            py,
            walk(&ctx, py, &raiser, &["attr_err"]),
            "AttributeError",
        );
        expect_invalid("6 absent name", walk(&ctx, py, &raiser, &["absent"]));
        expect_str(
            "6 plain attr (control)",
            walk(&ctx, py, &raiser, &["plain"]),
            "attr-plain",
        );

        // 7. Step 1's catch set (#2506): `RuntimeError` from `__getitem__`
        //    propagates; `ValueError` (the numpy allowance) falls to step 2.
        let getitem_raiser = get("GetItemRaiser").call0().expect("GetItemRaiser()");
        expect_raises(
            "7 __getitem__ RuntimeError",
            py,
            walk(&ctx, py, &getitem_raiser, &["x"]),
            "RuntimeError",
        );
        let np_like = get("NpLike").call0().expect("NpLike()");
        expect_str(
            "7 __getitem__ ValueError falls to getattr",
            walk(&ctx, py, &np_like, &["foo"]),
            "attr-foo",
        );

        // 8. Step 3: the integer index, and its two ways of being invalid.
        let a_list = get("a_list");
        expect_str("8 list.0", walk(&ctx, py, &a_list, &["0"]), "zero");
        expect_invalid("8 list.5 IndexError", walk(&ctx, py, &a_list, &["5"]));
        expect_invalid("8 list.x non-integer", walk(&ctx, py, &a_list, &["x"]));

        // 9. Silence is decided by the attribute's TRUTH (#2508): truthy →
        //    invalid; explicit `False` → propagates; raised INSIDE an
        //    auto-called method → invalid (the outer handler wraps the call).
        expect_invalid("9 silent property", walk(&ctx, py, &raiser, &["silent"]));
        expect_raises(
            "9 silent_variable_failure=False",
            py,
            walk(&ctx, py, &raiser, &["loud_false"]),
            "NotSilent",
        );
        expect_invalid(
            "9 silent inside auto-called method",
            walk(&ctx, py, &raiser, &["silent_method"]),
        );

        // 10. The `auto_call` kill-switch: a callable is returned as-is and
        //     never called.
        let mut off = Context::new();
        off.set_auto_call(false);
        let counted = get("counted");
        match walk(&off, py, &counted, &[]) {
            Ok(Walked::Object(obj)) => assert!(obj.is_callable(), "10 kill-switch: was called"),
            other => panic!("10 kill-switch: {:?}", other.map(|_| ())),
        }
        let counter = get("COUNTER");
        assert_eq!(
            counter
                .get_item("n")
                .expect("n")
                .extract::<i64>()
                .expect("int"),
            0,
            "10 kill-switch: the callable was CALLED"
        );

        // 11. Nested segments; and the root auto-call on an empty `parts`.
        let nested = get("Nested").call0().expect("Nested()");
        expect_str(
            "11 nested attr.sub",
            walk(&ctx, py, &nested, &["attr", "sub"]),
            "deep",
        );
        expect_str(
            "11 root auto-call",
            walk(&ctx, py, &counted, &[]),
            "foo bar",
        );
        assert_eq!(
            counter
                .get_item("n")
                .expect("n")
                .extract::<i64>()
                .expect("int"),
            1,
            "11 root auto-call: called exactly once"
        );
    });
}
