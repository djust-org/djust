//! #3769 — `DisplayText`, the `str(o)` of an `Encoded` that is asked for when a sink first reads
//! it instead of when the object crosses.
//!
//! What this pins, each as a case that goes red when ONLY the mechanism it names is removed:
//!
//! * nothing is asked at construction, at `clone`, at `Debug`, or by an equality between two texts
//!   of the SAME object (the saving: a form the template never prints is never rendered);
//! * the first read asks exactly once and every later read, on this value or on ANY clone of it,
//!   taken before or after the read, is the kept answer (a `Value` is cloned each time the context
//!   hands it to a lookup, so a cell per clone would render `{{ form }}{{ form }}` twice);
//! * `safe` and `raised` are the facts of the SAME single `str()` (no second call);
//! * a `str()` that RAISES yields the stand-in and `raised`, and does not propagate: the `{{ o }}`
//!   sink is what re-raises it (`python/tests/test_form_lazy_display_3769.py`).
//!
//! Fixtures are plain Python (`PyModule::from_code`), no Django import.
//!
//! IMPORTANT: a SINGLE `#[test]` — concurrent threads attaching to the embedded CPython deadlock
//! (see `test_django_lookup_sink_2539.rs`).
//!
//! Run with an embeddable interpreter (#2072):
//! `PYO3_PYTHON="$(bash scripts/embeddable-python.sh)" cargo test -p djust_core --test test_display_text_lazy_3769`

use djust_core::{values_structurally_equal, DisplayText, Encoded, Value};
use pyo3::ffi::c_str;
use pyo3::prelude::*;
use std::sync::Arc;

const FIXTURES: &std::ffi::CStr = c_str!(
    r#"
from django_stub import SafeString

class Counting:
    def __init__(self, text="rendered", safe=False):
        self.calls = 0
        self.text = text
        self.safe = safe
    def __str__(self):
        self.calls += 1
        return SafeString(self.text) if self.safe else self.text

class Raising:
    def __init__(self):
        self.calls = 0
    def __str__(self):
        self.calls += 1
        raise RuntimeError("boom")
"#
);

/// `django.utils.safestring.SafeString` is a `str` subclass registered as `SafeData`; the engine
/// asks `isinstance(text, SafeData)`. Plain Python cannot import Django here, so a stub module with
/// the same two names stands in, and `python_string_is_safe` imports `django.utils.safestring`
/// — which this test installs as that stub.
const STUB: &std::ffi::CStr = c_str!(
    r#"
import sys, types
class SafeData: pass
class SafeString(str, SafeData): pass
django = types.ModuleType("django"); utils = types.ModuleType("django.utils")
safestring = types.ModuleType("django.utils.safestring")
safestring.SafeData = SafeData; safestring.SafeString = SafeString
django.utils = utils; utils.safestring = safestring
sys.modules.setdefault("django", django)
sys.modules.setdefault("django.utils", utils)
sys.modules.setdefault("django.utils.safestring", safestring)
django_stub = types.ModuleType("django_stub"); django_stub.SafeString = SafeString
sys.modules["django_stub"] = django_stub
"#
);

fn calls(obj: &Py<PyAny>) -> usize {
    Python::attach(|py| obj.bind(py).getattr("calls").unwrap().extract().unwrap())
}

#[test]
fn the_text_is_asked_for_once_when_first_read_and_never_before() {
    Python::initialize();
    Python::attach(|py| {
        PyModule::from_code(py, STUB, c_str!("stub_3769.py"), c_str!("stub_3769")).expect("stub");
    });
    let module: Py<PyModule> = Python::attach(|py| {
        PyModule::from_code(py, FIXTURES, c_str!("lazy_3769.py"), c_str!("lazy_3769"))
            .expect("fixtures compile")
            .unbind()
    });
    let make = |class: &str, args: &str| -> Py<PyAny> {
        Python::attach(|py| {
            let src = std::ffi::CString::new(format!("{class}({args})")).unwrap();
            py.eval(src.as_c_str(), Some(&module.bind(py).dict()), None)
                .expect("fixture")
                .unbind()
        })
    };

    // ---- nothing is asked at construction, clone, Debug, or an equality of the same object ----
    let obj = make("Counting", "");
    let handle = Arc::new(obj.clone_ref_for_test());
    let text = DisplayText::deferred(handle.clone(), "<standin>".to_string());
    assert!(text.is_deferred());
    assert!(!text.is_forced(), "construction must not ask");
    assert_eq!(calls(&obj), 0, "construction must not call str()");
    let before_clone = text.clone();
    assert_eq!(calls(&obj), 0, "clone must not call str()");
    let _ = format!("{text:?}");
    assert_eq!(calls(&obj), 0, "Debug must not call str()");
    let same_object = DisplayText::deferred(handle.clone(), "<standin>".to_string());
    assert!(text == same_object, "two texts of ONE object are equal");
    assert_eq!(
        calls(&obj),
        0,
        "== between two texts of one object must not call str()"
    );
    assert!(!text.is_forced() && !same_object.is_forced());

    // ---- a structural `==` of two Encoded of ONE object does not render it (json mirrors display) ----
    let carrier = |text: DisplayText| {
        Value::Encoded(Box::new(Encoded {
            type_name: "F".into(),
            display: text,
            json: None,
            truthy: true,
            len: None,
            iterable: true,
            repr: "<F>".into(),
            cmp_key: None,
            attrs: Default::default(),
            items: None,
            eq_class: None,
            live: Some(handle.clone()),
        }))
    };
    let (ca, cb) = (
        carrier(DisplayText::deferred(handle.clone(), "<standin>".into())),
        carrier(DisplayText::deferred(handle.clone(), "<standin>".into())),
    );
    assert!(values_structurally_equal(&ca, &cb));
    assert_eq!(
        calls(&obj),
        0,
        "Encoded == must not read json() of two mirrors, which would force the text"
    );

    // ---- the first read asks exactly once; the answer is kept ----
    assert_eq!(text.text(), "rendered");
    assert!(!text.safe() && !text.raised());
    assert_eq!(text.text(), "rendered");
    assert_eq!(
        calls(&obj),
        1,
        "safe / raised / a second text() are the SAME single str()"
    );
    assert!(text.is_forced());

    // ---- a clone shares the answer, whether it was taken before or after the read ----
    let after_clone = text.clone();
    assert!(
        after_clone.is_forced(),
        "a clone of a forced text is forced"
    );
    assert_eq!(after_clone.text(), "rendered");
    assert!(
        before_clone.is_forced(),
        "a clone taken BEFORE the read sees the read"
    );
    assert_eq!(before_clone.text(), "rendered");
    assert_eq!(
        calls(&obj),
        1,
        "one str() for the text and every clone of it"
    );

    // ---- safety is the fact of the same str() ----
    let safe_obj = make("Counting", "'<b>x</b>', True");
    let safe_text = DisplayText::deferred(Arc::new(safe_obj.clone_ref_for_test()), String::new());
    assert!(safe_text.safe(), "str(o) was a SafeString");
    assert_eq!(safe_text.text(), "<b>x</b>");
    assert_eq!(calls(&safe_obj), 1);

    // ---- an equality between two DIFFERENT objects asks both, and compares the answers ----
    let a = make("Counting", "'same'");
    let b = make("Counting", "'same'");
    let (ta, tb) = (
        DisplayText::deferred(Arc::new(a.clone_ref_for_test()), String::new()),
        DisplayText::deferred(Arc::new(b.clone_ref_for_test()), String::new()),
    );
    assert!(ta == tb);
    assert_eq!((calls(&a), calls(&b)), (1, 1));
    let c = make("Counting", "'other'");
    let tc = DisplayText::deferred(Arc::new(c.clone_ref_for_test()), String::new());
    assert!(ta != tc);

    // ---- a str() that raises: the stand-in, `raised`, nothing propagates, asked once ----
    let boom = make("Raising", "");
    let raised = DisplayText::deferred(
        Arc::new(boom.clone_ref_for_test()),
        "<Raising object>".into(),
    );
    assert!(!raised.is_forced());
    assert_eq!(raised.text(), "<Raising object>");
    assert!(raised.raised(), "the {{ o }} sink re-raises on this");
    assert!(!raised.safe());
    assert_eq!(calls(&boom), 1, "the failure is kept like an answer");

    // ---- the teardown lets go of the object WITHOUT asking it ----
    let rel = make("Counting", "");
    let rel_handle = Arc::new(rel.clone_ref_for_test());
    let mut unasked = DisplayText::deferred(rel_handle.clone(), "<standin>".to_string());
    assert_eq!(
        Arc::strong_count(&rel_handle),
        2,
        "the text holds a handle of its own"
    );
    unasked.release_handle();
    assert_eq!(
        Arc::strong_count(&rel_handle),
        1,
        "release_handle drops that handle"
    );
    assert_eq!(calls(&rel), 0, "release_handle must not call str()");
    assert!(!unasked.is_deferred());
    assert_eq!(
        unasked.text(),
        "<standin>",
        "an unasked text keeps the stand-in"
    );
    assert!(!unasked.raised() && !unasked.safe());
    let mut asked = DisplayText::deferred(rel_handle.clone(), "<standin>".to_string());
    assert_eq!(asked.text(), "rendered");
    asked.release_handle();
    assert_eq!(Arc::strong_count(&rel_handle), 1);
    assert_eq!(asked.text(), "rendered", "an asked text keeps its answer");
    assert_eq!(calls(&rel), 1);
    let mut eager_text = DisplayText::eager("e".into(), true);
    eager_text.release_handle();
    assert!(eager_text.safe(), "an eager text is untouched");

    // ---- an eager text never asks and is never `raised` unless built as the stand-in ----
    let eager = DisplayText::eager("t".into(), true);
    assert!(eager.is_forced() && !eager.is_deferred() && eager.safe() && !eager.raised());
    let standin = DisplayText::raised_standin("s".into());
    assert!(standin.raised() && !standin.safe());
    assert!(DisplayText::from("x") == DisplayText::from("x".to_string()));
    assert!(DisplayText::eager("x".into(), true) != DisplayText::eager("x".into(), false));
}

trait CloneRefForTest {
    fn clone_ref_for_test(&self) -> Py<PyAny>;
}

impl CloneRefForTest for Py<PyAny> {
    fn clone_ref_for_test(&self) -> Py<PyAny> {
        Python::attach(|py| self.clone_ref(py))
    }
}
