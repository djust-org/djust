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


# --- movement 2 (#2539) -------------------------------------------------
class Mutator:
    """`alters_data` MID-path: Django substitutes `string_if_invalid` and
    keeps walking, so `{{ o.delete.isupper }}` is `"".isupper()` -> False."""
    def delete(self):
        raise AssertionError("alters_data was CALLED")

    delete.alters_data = True


class Underscored:
    """`Variable.__init__` refuses a leading-underscore bit BEFORE any lookup
    (base.py:845-849), so neither of these is reachable in Django."""
    _secret = "LEAKED"
    public = "fine"


class Holder:
    def __init__(self, inner):
        self.inner = inner


class IndexAttrError:
    """A REAL `__getitem__` that raises `AttributeError` under an integer
    index. Django's step-3 tuple is (IndexError, ValueError, KeyError,
    TypeError) — `AttributeError` is NOT in it, so this propagates."""
    def __getitem__(self, key):
        raise AttributeError("getitem authz")


class Cls:
    cls_attr = "class-level"

    def __repr__(self):
        return "<Cls>"


class Plain:
    def __init__(self):
        self.inst_attr = "in-dict"

    def __repr__(self):
        return "<Plain>"


def install_fake_serialization(mode):
    """Put a `djust.serialization` in `sys.modules` so `protect_sidecar_strict`
    finds a floor to fail closed about. `mode` is 'raise' or 'passthrough'."""
    import sys, types
    pkg = sys.modules.get("djust")
    if pkg is None:
        pkg = types.ModuleType("djust")
        sys.modules["djust"] = pkg
    mod = types.ModuleType("djust.serialization")

    def _protect_sidecar_value(obj):
        if mode == "raise":
            raise RuntimeError("floor enforcement broke")
        return obj

    mod._protect_sidecar_value = _protect_sidecar_value
    sys.modules["djust.serialization"] = mod
    pkg.serialization = mod


def uninstall_fake_serialization():
    import sys
    sys.modules.pop("djust.serialization", None)
    pkg = sys.modules.get("djust")
    if pkg is not None and getattr(pkg, "__file__", None) is None:
        sys.modules.pop("djust", None)
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
        // `alters_data` at the ROOT substitutes `""` and keeps walking
        // (#2539 movement 2, §6.1), so `.value` / `.the_value` on a `str`
        // is Django's `VariableDoesNotExist` — the SAME rendered cell as
        // before, reached the way Django reaches it.
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

        // 5. An args-required callable substitutes `string_if_invalid` and
        //    KEEPS WALKING (Django's `signature().bind()` probe, `base.py:930`
        //    — the loop assigns `current` and falls through to the next bit).
        //    With no bits left that IS the answer, so the terminal is `""`,
        //    not `Invalid` (#2539 movement 2, §6.1); a `TypeError` raised
        //    INSIDE a nullary is a real error and still propagates.
        expect_str(
            "5 args-required",
            walk(&ctx, py, &get("needs_args"), &[]),
            "",
        );
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

        // ================= movement 2 (#2539): the WIRING =================
        // Everything below runs with the sink ROUTED — the flag is a
        // thread-local, and this test is the only thing on this thread.

        // 12. `alters_data` MID-path continues from `string_if_invalid`
        //     (§6.1). Django's loop assigns `current = ""` and walks the next
        //     bit, so `{{ o.delete.isupper }}` is `False`, not empty. The
        //     refusal itself is unchanged: `delete` raises if ever CALLED.
        let mutator = get("Mutator").call0().expect("Mutator()");
        expect_str(
            "12 alters_data mid-path continues",
            walk(&ctx, py, &mutator, &["delete", "isupper"]),
            "False",
        );
        expect_str(
            "12 alters_data terminal is the empty string",
            walk(&ctx, py, &mutator, &["delete"]),
            "",
        );

        // 13. The leading-underscore refusal (§3.2), root-adjacent and
        //     mid-path. Called DIRECTLY, because djust's parser refuses the
        //     spelling first — which is exactly what makes this defence in
        //     depth rather than the only guard. The public sibling is the
        //     control: the walk itself works on this object.
        let underscored = get("Underscored").call0().expect("Underscored()");
        expect_invalid(
            "13 leading underscore, root-adjacent",
            walk(&ctx, py, &underscored, &["_secret"]),
        );
        expect_str(
            "13 public sibling (control)",
            walk(&ctx, py, &underscored, &["public"]),
            "fine",
        );
        let holder = get("Holder").call1((&underscored,)).expect("Holder()");
        expect_invalid(
            "13 leading underscore, mid-path",
            walk(&ctx, py, &holder, &["inner", "_secret"]),
        );
        expect_str(
            "13 mid-path public sibling (control)",
            walk(&ctx, py, &holder, &["inner", "public"]),
            "fine",
        );

        // 14. The floor's failure arm is CLOSED (§3.1). A
        //     `_protect_sidecar_value` that RAISES answers `Invalid` — the
        //     raw object must not flow on. The passthrough control proves the
        //     arm is reached in both directions and that installing a module
        //     is not itself what fails the walk.
        let install = get("install_fake_serialization");
        let uninstall = get("uninstall_fake_serialization");
        install
            .call1(("passthrough",))
            .expect("install passthrough");
        expect_str(
            "14 floor passthrough (control)",
            walk(&ctx, py, &underscored, &["public"]),
            "fine",
        );
        install.call1(("raise",)).expect("install raise");
        expect_invalid(
            "14 floor raises -> Invalid, never the raw object",
            walk(&ctx, py, &underscored, &["public"]),
        );
        uninstall.call0().expect("uninstall");
        expect_str(
            "14 floor unreachable -> passes through (embedder)",
            walk(&ctx, py, &underscored, &["public"]),
            "fine",
        );

        // 15. Django's step-3 catch set, STRICT (§3.3). An `AttributeError`
        //     from a real `__getitem__` under an INTEGER segment is outside
        //     `(IndexError, ValueError, KeyError, TypeError)` and propagates.
        //     The loose helper the pre-ADR walk still uses would swallow it.
        expect_raises(
            "15 strict step-3 catch set",
            py,
            walk(
                &ctx,
                py,
                &get("IndexAttrError").call0().expect("()"),
                &["0"],
            ),
            "AttributeError",
        );

        // 16. THE ROUTE (§2.4), through the real `Context::resolve`. A value
        //     that crossed under the flag carries a handle; a dotted lookup
        //     the value stack cannot answer walks it. The flag-OFF control is
        //     the same context and the same key, answering nothing — which is
        //     what makes this the switch and not the carriage.
        let cls_instance = get("Cls").call0().expect("Cls()");
        djust_core::set_resolve_lazy(true);
        let live_value: djust_core::Value = cls_instance
            .extract()
            .expect("16 conversion under the flag");
        match &live_value {
            djust_core::Value::Encoded(e) => {
                assert!(e.live.is_some(), "16: no handle attached under the flag");
                assert_eq!(e.display, "<Cls>", "16: the display is str(o)");
                assert!(
                    e.attrs.is_empty(),
                    "16: a handle-bearing value must carry NO eager attribute dump"
                );
            }
            other => panic!("16: expected an Encoded, got {other:?}"),
        }
        let routed = Context::from_dict([("o".to_string(), live_value.clone())]);
        assert_eq!(
            routed
                .resolve("o.cls_attr")
                .expect("16 resolve")
                .expect("16 resolved nothing")
                .to_string(),
            "class-level",
            "16 the route: a class attribute through the handle"
        );
        // R2: a missing segment is `Invalid`, which the caller renders empty.
        assert!(
            routed.resolve("o.absent").expect("16 resolve").is_none(),
            "16 the route: a missing segment must resolve to nothing"
        );
        djust_core::set_resolve_lazy(false);
        assert!(
            routed.resolve("o.cls_attr").expect("16 off").is_none(),
            "16 the SWITCH: the same value and key resolve nothing with the flag off"
        );

        // 17. The eager `__dict__` dump is what the handle REPLACES. With the
        //     flag off the same object crosses as a `Value::Object` of its
        //     attributes (or is declined into one); with it on, `{{ o }}` is
        //     `str(o)`. Both directions asserted so neither can drift.
        let plain = get("Plain").call0().expect("Plain()");
        let eager: djust_core::Value = plain.extract().expect("17 eager conversion");
        assert!(
            matches!(eager, djust_core::Value::Object(_)),
            "17: with the flag OFF an attribute-bearing object is bulk-dumped, got {eager:?}"
        );
        djust_core::set_resolve_lazy(true);
        let lazy: djust_core::Value = plain.extract().expect("17 lazy conversion");
        match &lazy {
            djust_core::Value::Encoded(e) => {
                assert_eq!(e.display, "<Plain>", "17: display");
                assert!(e.live.is_some(), "17: handle");
            }
            other => panic!("17: expected an Encoded under the flag, got {other:?}"),
        }
        djust_core::set_resolve_lazy(false);

        // 18. The handle is TRANSIENT. A msgpack round trip drops it and
        //     leaves every other field intact, so a state entry that came
        //     back from the backend never carries a stale object.
        let bytes = rmp_serde::to_vec(&lazy).expect("18 encode");
        let back: djust_core::Value = rmp_serde::from_slice(&bytes).expect("18 decode");
        match (&lazy, &back) {
            (djust_core::Value::Encoded(before), djust_core::Value::Encoded(after)) => {
                assert!(after.live.is_none(), "18: the handle survived the wire");
                assert_eq!(after.display, before.display, "18: display");
                assert_eq!(after.type_name, before.type_name, "18: type_name");
                assert_eq!(after.truthy, before.truthy, "18: truthy");
                // `PartialEq for Encoded` must IGNORE the handle, or a pin
                // that a value survived the codec unchanged could never pass.
                assert_eq!(
                    **after, **before,
                    "18: PartialEq must not compare the handle"
                );
            }
            other => panic!("18: {other:?}"),
        }
    });

    // 19. Lifetime: a handle-bearing `Value` clones with no GIL held and
    //     drops OUTSIDE `Python::attach`. Same profile as
    //     `Context::raw_py_objects`, which stores `Py<PyAny>` the same way.
    let escaped = Python::attach(|py| {
        let m = PyModule::from_code(py, FIXTURES, c_str!("sink_2539.py"), c_str!("sink_2539"))
            .expect("compile fixtures");
        djust_core::set_resolve_lazy(true);
        let value: djust_core::Value = m
            .getattr("Cls")
            .expect("Cls")
            .call0()
            .expect("Cls()")
            .extract()
            .expect("19 conversion");
        djust_core::set_resolve_lazy(false);
        value
    });
    let cloned = escaped.clone();
    drop(escaped);
    drop(cloned);
}
