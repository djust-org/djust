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
    for (value, expected) in django_expectations() {
        assert_eq!(value.to_string(), expected, "for {value:?}");
    }
}

#[test]
fn a_list_renders_as_a_python_list() {
    assert_eq!(
        Value::List(vec![Value::Integer(1), Value::Integer(2)]).to_string(),
        "[1, 2]"
    );
    assert_eq!(Value::List(vec![]).to_string(), "[]");
}

#[test]
fn strings_inside_a_container_are_repr_quoted() {
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
    // Python distinguishes `(1, 2)` from `[1, 2]`; a single `List` variant
    // could not, so a tuple rendered as a list.
    assert_eq!(
        Value::Tuple(vec![Value::Integer(1), Value::Integer(2)]).to_string(),
        "(1, 2)"
    );
}

#[test]
fn a_dict_renders_in_insertion_order() {
    // The reason `Object` had to stop being a `HashMap`: its iteration order is
    // randomised per process, so dict repr was non-deterministic — the same
    // template could render `{'a': 1, 'b': 2}` and `{'b': 2, 'a': 1}` on
    // successive runs. Python dicts are insertion-ordered.
    let mut m = IndexMap::new();
    m.insert("a".to_string(), Value::Integer(1));
    m.insert("b".to_string(), Value::Integer(2));
    assert_eq!(Value::Object(m).to_string(), "{'a': 1, 'b': 2}");

    // Insertion order, not sorted order — build it backwards to tell them apart.
    let mut rev = IndexMap::new();
    rev.insert("b".to_string(), Value::Integer(2));
    rev.insert("a".to_string(), Value::Integer(1));
    assert_eq!(Value::Object(rev).to_string(), "{'b': 2, 'a': 1}");
}

#[test]
fn dict_rendering_is_stable_across_repeated_construction() {
    // Guard against a regression to `HashMap`: with one, this loop produced a
    // different string on most iterations.
    let build = || {
        let mut m = IndexMap::new();
        for k in ["alpha", "beta", "gamma", "delta", "epsilon"] {
            m.insert(k.to_string(), Value::Integer(1));
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
    // Guard: a Django model instance carries `__str__`, and that must keep
    // winning over dict repr — it is how `{{ obj }}` renders a model.
    let mut m = IndexMap::new();
    m.insert("__str__".to_string(), Value::String("Model object".into()));
    m.insert("pk".to_string(), Value::Integer(1));
    assert_eq!(Value::Object(m).to_string(), "Model object");
}

#[test]
fn missing_and_none_are_distinct_values() {
    // The distinction the split exists for. If these ever collapse again, the
    // security-denial path (`CallOutcome::Empty` -> Missing) starts rendering
    // the literal text "None" where it used to render nothing.
    assert_ne!(Value::Missing.to_string(), Value::None.to_string());
    assert_eq!(Value::Missing.to_string(), "");
    assert_eq!(Value::None.to_string(), "None");
}

#[test]
fn both_missing_and_none_stay_falsy() {
    // Guard: `{% if %}` semantics must not shift. Python's `None` is falsy and
    // so is an absent variable, so both remain false regardless of rendering.
    assert!(!Value::Missing.is_truthy());
    assert!(!Value::None.is_truthy());
}
