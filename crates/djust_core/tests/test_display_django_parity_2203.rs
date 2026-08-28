//! `impl Display for Value` must render what Django renders (#2203).
//!
//! Every expected value here was taken from a live Django 5.2 render of
//! `{{ v }}`, not from what this implementation produces.
//!
//! ## Why this is not just cosmetics
//!
//! `Display` is the lookup key for `{% if x in dict %}` (`renderer.rs`), so the
//! divergence was a live correctness bug: `{% if True in d %}` MISSED where
//! Django HIT, because the key was `"true"` and Python had written `"True"`.
//!
//! ## The `Null` split
//!
//! Django distinguishes two things djust collapsed into one `Value::Missing`:
//!
//! | | Django | why |
//! |---|---|---|
//! | absent key/attr | `""` | `string_if_invalid` |
//! | present, is `None` | `"None"` | `str(None)` |
//!
//! `renderer.rs`'s `resolve(...)?.unwrap_or(Value::Missing)` folded *missing* into
//! the same variant as Python `None`, and `CallOutcome::Empty` — an
//! `alters_data` refusal or a serialization-floor denial — used it too, with a
//! comment naming it as `string_if_invalid`.
//!
//! So `Null` is renamed `Missing` (keeping the render-as-empty semantics every
//! existing caller relies on, including security denials) and a separate `None`
//! variant carries Python's `None`. Mapping the old `Null` to `"None"` without
//! this split would have made every missing variable render the literal text
//! `None` — diverging from Django in the opposite direction, and putting text
//! where a refused password field used to render nothing.
//!
//! ## Gating
//!
//! All of this is behind `LIVEVIEW_CONFIG['django_value_repr']` (default ON),
//! because it changes rendered output for every template using these types.
//! A template embedding a bool in a script block (`var f = {{ flag }};`) needs
//! `|yesno:"true,false"` or `json_script` under Django semantics, so the flag
//! is the one-line escape while such templates are migrated.

use djust_core::Value;
use indexmap::IndexMap;

/// The parity table. Left: the `Value`. Right: what Django renders for it.
fn django_expectations() -> Vec<(Value, &'static str)> {
    vec![
        // Absent — Django's `string_if_invalid`, NOT "None".
        (Value::Missing, ""),
        // Present and None.
        (Value::None, "None"),
        (Value::Bool(true), "True"),
        (Value::Bool(false), "False"),
        (Value::Integer(42), "42"),
        (Value::Integer(0), "0"),
        // Python renders an integral float with its `.0`.
        (Value::Float(1.0), "1.0"),
        (Value::Float(0.0), "0.0"),
        (Value::Float(1.5), "1.5"),
        (Value::String("x".into()), "x"),
        (Value::String(String::new()), ""),
    ]
}

#[test]
fn scalars_render_exactly_as_django_renders_them() {
    let _g = FlagGuard::on();
    for (value, expected) in django_expectations() {
        assert_eq!(value.to_string(), expected, "for {value:?}");
    }
}

#[test]
fn a_list_renders_as_a_python_list() {
    let _g = FlagGuard::on();
    assert_eq!(
        Value::List(vec![Value::Integer(1), Value::Integer(2)]).to_string(),
        "[1, 2]"
    );
    assert_eq!(Value::List(vec![]).to_string(), "[]");
}

#[test]
fn strings_inside_a_container_are_repr_quoted() {
    let _g = FlagGuard::on();
    // `str(['a'])` is `"['a']"` — a nested string gets quoted even though a
    // top-level one does not. That str/repr distinction is the whole reason
    // containers cannot simply reuse `Display` for their elements.
    assert_eq!(
        Value::List(vec![Value::String("a".into()), Value::String("b".into())]).to_string(),
        "['a', 'b']"
    );
}

#[test]
fn a_nested_container_renders_recursively() {
    let _g = FlagGuard::on();
    assert_eq!(
        Value::List(vec![
            Value::List(vec![Value::Integer(1)]),
            Value::List(vec![Value::Integer(2)]),
        ])
        .to_string(),
        "[[1], [2]]"
    );
}

#[test]
fn a_tuple_renders_with_parentheses() {
    let _g = FlagGuard::on();
    // Python distinguishes `(1, 2)` from `[1, 2]`; a single `List` variant
    // could not, so a tuple rendered as a list.
    assert_eq!(
        Value::Tuple(vec![Value::Integer(1), Value::Integer(2)]).to_string(),
        "(1, 2)"
    );
}

#[test]
fn a_dict_renders_in_insertion_order() {
    let _g = FlagGuard::on();
    // The reason `Object` had to stop being a `HashMap`: its iteration order is
    // randomised per process, so dict repr was non-deterministic — the same
    // template could render `{'a': 1, 'b': 2}` and `{'b': 2, 'a': 1}` on
    // successive runs. Python dicts are insertion-ordered.
    let mut m = IndexMap::new();
    m.insert("a".into(), Value::Integer(1));
    m.insert("b".into(), Value::Integer(2));
    assert_eq!(Value::Object(m).to_string(), "{'a': 1, 'b': 2}");

    // Insertion order, not sorted order — build it backwards to tell them apart.
    let mut rev = IndexMap::new();
    rev.insert("b".into(), Value::Integer(2));
    rev.insert("a".into(), Value::Integer(1));
    assert_eq!(Value::Object(rev).to_string(), "{'b': 2, 'a': 1}");
}

#[test]
fn dict_rendering_is_stable_across_repeated_construction() {
    let _g = FlagGuard::on();
    // Guard against a regression to `HashMap`: with one, this loop produced a
    // different string on most iterations.
    let build = || {
        let mut m = IndexMap::new();
        for k in ["alpha", "beta", "gamma", "delta", "epsilon"] {
            m.insert(k.into(), Value::Integer(1));
        }
        Value::Object(m).to_string()
    };
    let first = build();
    for _ in 0..20 {
        assert_eq!(build(), first, "dict repr must be deterministic");
    }
}

#[test]
fn an_object_with_a_dunder_str_still_uses_it() {
    let _g = FlagGuard::on();
    // Guard: a Django model instance carries `__str__`, and that must keep
    // winning over dict repr — it is how `{{ obj }}` renders a model.
    let mut m = IndexMap::new();
    m.insert("__str__".into(), Value::String("Model object".into()));
    m.insert("pk".into(), Value::Integer(1));
    assert_eq!(Value::Object(m).to_string(), "Model object");
}

#[test]
fn missing_and_none_are_distinct_values() {
    let _g = FlagGuard::on();
    // The distinction the split exists for. If these ever collapse again, the
    // security-denial path (`CallOutcome::Empty` -> Missing) starts rendering
    // the literal text "None" where it used to render nothing.
    assert_ne!(Value::Missing.to_string(), Value::None.to_string());
    assert_eq!(Value::Missing.to_string(), "");
    assert_eq!(Value::None.to_string(), "None");
}

#[test]
fn both_missing_and_none_stay_falsy() {
    let _g = FlagGuard::on();
    // Guard: `{% if %}` semantics must not shift. Python's `None` is falsy and
    // so is an absent variable, so both remain false regardless of rendering.
    assert!(!Value::Missing.is_truthy());
    assert!(!Value::None.is_truthy());
}

// ---------------------------------------------------------------------------
// The kill-switch. Gate-off found the flag gate itself was untested: removing
// it failed nothing, because every test above runs on the default-ON path.
// A flag with no OFF-path test is decorative (#1859) — it can stop working and
// the suite stays green.
// ---------------------------------------------------------------------------

// The serial guard moved to `tests/value_repr_flag/mod.rs` in #2260, which was
// about to add a third and a fourth copy of it. Its module docs carry the
// rationale this comment used to: the flag is process-global, so EVERY test
// that reads `Display` must hold the lock, not only the ones that toggle.
mod value_repr_flag;
use value_repr_flag::FlagGuard;

#[test]
fn the_flag_restores_the_previous_rendering_verbatim() {
    let _g = FlagGuard::off();
    // Exactly what djust rendered before #2203 — this is the escape hatch for
    // a template interpolating a bool into a script block, where `True` is a
    // JS ReferenceError.
    assert_eq!(Value::Bool(true).to_string(), "true");
    assert_eq!(Value::Bool(false).to_string(), "false");
    assert_eq!(Value::None.to_string(), "");
    assert_eq!(Value::Missing.to_string(), "");
    assert_eq!(Value::Float(1.0).to_string(), "1");
    assert_eq!(
        Value::List(vec![Value::Integer(1), Value::Integer(2)]).to_string(),
        "[List]"
    );
    assert_eq!(
        Value::Tuple(vec![Value::Integer(1), Value::Integer(2)]).to_string(),
        "[List]"
    );
    let mut m = IndexMap::new();
    m.insert("a".into(), Value::Integer(1));
    assert_eq!(Value::Object(m).to_string(), "[Object]");
}

#[test]
fn the_flag_getter_reports_what_the_setter_set() {
    // A setter with no getter cannot be tested end to end (#2017).
    let _g = FlagGuard::off();
    assert!(!djust_core::django_value_repr());
    djust_core::set_django_value_repr(true);
    assert!(djust_core::django_value_repr());
}

#[test]
fn a_dunder_str_object_renders_the_same_either_way() {
    // ONE guard for the whole body — `FLAG_LOCK` is a plain `Mutex`, so taking
    // it twice in a single test deadlocks. Toggle the flag directly instead of
    // constructing a second guard.
    let _g = FlagGuard::on();
    // Guard: a model instance must render via `__str__` regardless of the flag,
    // so flipping it never changes how `{{ obj }}` shows a model.
    let mut m = IndexMap::new();
    m.insert("__str__".into(), Value::String("Model object".into()));
    assert_eq!(Value::Object(m.clone()).to_string(), "Model object");

    djust_core::set_django_value_repr(false);
    assert_eq!(Value::Object(m).to_string(), "Model object");
    // `_g`'s Drop restores the default.
}

/// A dict VIEW names its container on the Django-parity path, and is
/// UNCHANGED on the legacy one (#2340).
///
/// The `Display` impl has two paths, and the first version of #2340 wrote the
/// naming arm into both — on a comment asserting "the container spelling is
/// Python's on BOTH display paths". That was a prose invariant nobody had run:
/// the gate-off surfaced the legacy arm as a surviving mutation, and this
/// test, written to close that gap, FAILED on its first execution with
/// `dict_items([[List]])` (CLAUDE.md #1867).
///
/// `legacy_display` is the pre-#2203 rendering, where every container is a
/// `[List]` / `[Object]` placeholder — and before #2340 a view WAS a
/// `Value::List`, so `[List]` is exactly what `{{ d.items }}` printed under
/// the flag. Naming it there would make a legacy-rendering switch less legacy,
/// so the arm joins the placeholder and this pins that it stays joined.
#[test]
fn a_dict_view_names_its_container_only_on_the_django_parity_path() {
    // ONE guard for the whole body — `FLAG_LOCK` is a plain `Mutex`, so taking
    // it twice in a single test deadlocks.
    let _g = FlagGuard::on();
    let cases = [
        (
            djust_core::DictViewKind::Keys,
            vec![Value::String("a".into())],
            "dict_keys(['a'])",
        ),
        (
            djust_core::DictViewKind::Values,
            vec![Value::Integer(1)],
            "dict_values([1])",
        ),
        (
            djust_core::DictViewKind::Items,
            vec![Value::Tuple(vec![
                Value::String("a".into()),
                Value::Integer(1),
            ])],
            "dict_items([('a', 1)])",
        ),
    ];
    for (kind, items, want) in &cases {
        let v = Value::DictView {
            kind: *kind,
            items: items.clone(),
        };
        assert_eq!(&v.to_string(), want, "django_value_repr ON, {kind:?}");
    }

    djust_core::set_django_value_repr(false);
    for (kind, items, _) in &cases {
        let v = Value::DictView {
            kind: *kind,
            items: items.clone(),
        };
        assert_eq!(
            v.to_string(),
            "[List]",
            "the legacy path is the PRE-#2203 rendering and a view was a \
             `Value::List` before #2340, so it must still print `[List]` \
             there — {kind:?}"
        );
    }
    // `_g`'s Drop restores the default.
}
