//! #3772 — `djust_core::template_call_guard`, the ONE statement of Django's
//! callable block, asked directly.
//!
//! The rule's ORDER is the thing a render test cannot see from the serializer
//! (a refused callable is omitted whichever marker refused it) and a swapped
//! order passed 133 tests unnoticed: `do_not_call_in_templates` is read FIRST,
//! so a callable carrying BOTH markers is used as-is (Django renders its repr)
//! and is not refused as `alters_data`. The matching render-level cases, with
//! Django's own engine as the control, are
//! `TestTheMarkerOrderIsDjangos3772` in
//! `python/tests/test_alters_data_toplevel_3772.py`.
//!
//! Fixtures are plain Python (`PyModule::from_code`), no Django import.
//!
//! IMPORTANT: a SINGLE `#[test]` — concurrent threads attaching to the
//! embedded CPython deadlock (see `test_django_lookup_sink_2539.rs`).
//!
//! Run with an embeddable interpreter (#2072):
//! `PYO3_PYTHON="$(bash scripts/embeddable-python.sh)" cargo test -p djust_core --test test_template_call_guard_3772`

use djust_core::{template_call_guard, TemplateCallGuard};
use pyo3::ffi::c_str;
use pyo3::prelude::*;

const FIXTURES: &std::ffi::CStr = c_str!(
    r#"
def unmarked():
    return "x"

def altering():
    return "x"
altering.alters_data = True

def uncalled():
    return "x"
uncalled.do_not_call_in_templates = True

def both():
    return "x"
both.alters_data = True
both.do_not_call_in_templates = True

def falsy_markers():
    return "x"
falsy_markers.alters_data = False
falsy_markers.do_not_call_in_templates = False

class RaisingMarker:
    @property
    def alters_data(self):
        raise RuntimeError("a descriptor that raises reads as falsy")
    def __call__(self):
        return "x"
"#
);

#[test]
fn the_guard_reads_each_marker_and_in_djangos_order() {
    Python::initialize();
    Python::attach(|py| {
        let m = PyModule::from_code(py, FIXTURES, c_str!("guard_3772.py"), c_str!("guard_3772"))
            .expect("fixtures compile");
        let guard = |name: &str| template_call_guard(&m.getattr(name).expect("fixture"));

        assert_eq!(guard("unmarked"), TemplateCallGuard::Callable);
        assert_eq!(guard("altering"), TemplateCallGuard::AltersData);
        assert_eq!(guard("uncalled"), TemplateCallGuard::UseAsIs);
        // THE ORDER: both markers -> use as-is, not refused. Swapping the two
        // reads turns this into `AltersData`.
        assert_eq!(guard("both"), TemplateCallGuard::UseAsIs);
        // `getattr(obj, name, False)`: a present-but-falsy marker is no marker.
        assert_eq!(guard("falsy_markers"), TemplateCallGuard::Callable);

        // A raising descriptor is falsy, as Django's `getattr(..., False)`
        // reads it, and must not propagate or refuse.
        let raising = m
            .getattr("RaisingMarker")
            .and_then(|c| c.call0())
            .expect("RaisingMarker()");
        assert_eq!(template_call_guard(&raising), TemplateCallGuard::Callable);

        // `may_call` is true only for `Callable`.
        assert!(TemplateCallGuard::Callable.may_call());
        assert!(!TemplateCallGuard::AltersData.may_call());
        assert!(!TemplateCallGuard::UseAsIs.may_call());
    });
}
