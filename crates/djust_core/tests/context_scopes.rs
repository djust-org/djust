use djust_core::{Context, Value};
use std::panic::{catch_unwind, AssertUnwindSafe};

#[test]
fn local_bindings_restore_values_and_grants() {
    let mut context = Context::new();
    context.bind("x".into(), Value::String("original".into()), true);
    context.with_scope(|inner| {
        inner.bind("x".into(), Value::String("hostile".into()), false);
        inner.bind("local".into(), Value::String("local".into()), true);
        assert!(!inner.is_safe("x"));
        assert!(inner.is_safe("local"));
    });
    assert!(matches!(context.get("x"), Some(Value::String(value)) if value == "original"));
    assert!(context.is_safe("x"));
    assert!(context.get("local").is_none());
    assert!(!context.is_safe("local"));
}

#[test]
fn upward_binding_updates_the_nearest_scope() {
    let mut context = Context::new();
    context.bind("x".into(), Value::String("original".into()), true);
    context.with_scope(|inner| {
        inner.bind_upward("x".into(), Value::String("new".into()), false);
        inner.with_scope(|nested| {
            nested.bind("x".into(), Value::String("shadow".into()), true);
            nested.bind_upward("x".into(), Value::String("local".into()), false);
            assert!(matches!(nested.get("x"), Some(Value::String(value)) if value == "local"));
        });
        assert!(matches!(inner.get("x"), Some(Value::String(value)) if value == "new"));
    });
    assert!(matches!(context.get("x"), Some(Value::String(value)) if value == "new"));
    assert!(!context.is_safe("x"));
}

#[test]
fn alias_revocation_is_local_to_a_shadowing_scope() {
    let mut context = Context::new();
    context.bind("p".into(), Value::String("text".into()), true);
    context.bind("q".into(), Value::String("text".into()), false);
    context.set_alias("q".into(), "p".into());
    assert!(context.is_safe("q"));
    context.with_scope(|inner| {
        inner.bind("p".into(), Value::String("different".into()), false);
        assert!(!inner.is_safe("q"));
    });
    assert!(context.is_safe("q"));
}

#[test]
fn panic_pops_local_frames_but_preserves_upward_writes() {
    let mut context = Context::new();
    context.bind("x".into(), Value::String("original".into()), true);
    context.begin_loop_scope();
    let outer_loop = context.loop_scope();
    let result = catch_unwind(AssertUnwindSafe(|| {
        context.with_scope(|inner| {
            inner.begin_loop_scope();
            assert_ne!(inner.loop_scope(), outer_loop);
            inner.bind("local".into(), Value::String("local".into()), true);
            inner.bind_upward("x".into(), Value::String("new".into()), false);
            panic!("exercise scope cleanup");
        });
    }));
    assert!(result.is_err());
    assert_eq!(context.loop_scope(), outer_loop);
    assert!(context.get("local").is_none());
    assert!(matches!(context.get("x"), Some(Value::String(value)) if value == "new"));
    assert!(!context.is_safe("x"));
}
