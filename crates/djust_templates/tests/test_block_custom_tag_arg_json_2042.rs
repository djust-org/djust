//! End-to-end regression test for issue #2042.
//!
//! `renderer.rs` had THREE tag-dispatch arg-resolution branches, but only
//! `Node::CustomTag` and `Node::AssignTag` JSON-encoded structured
//! (list/object) args. `Node::BlockCustomTag` used a hand-copied inline
//! resolver that skipped the JSON encoding, so a list/object arg collapsed
//! to the opaque `"[List]"` / `"[Object]"` placeholder and the Python block
//! handler lost the structured payload.
//!
//! The fix routes all three branches through one shared `value_to_arg_string`
//! (and folds BlockCustomTag onto the same `resolve_tag_arg` helper AssignTag
//! already uses) — the #1646 parallel-path cure.
//!
//! This test drives the REAL `Node::BlockCustomTag` dispatch path
//! (`render_nodes` -> `render_node_with_loader` -> BlockCustomTag arm -> arg
//! resolution -> `call_block_handler_with_py_sidecar` -> Python `render`) with
//! a registered block handler that echoes its first resolved arg, so the
//! assertion is on the exact string the handler received. A registered Python
//! handler is required to reach the resolution's downstream sink, so the test
//! embeds a CPython interpreter via `Python::initialize()`.
//!
//! IMPORTANT — this file intentionally contains a SINGLE `#[test]`. cargo's
//! default harness runs `#[test]`s on parallel threads, and multiple threads
//! concurrently attaching to the embedded CPython interpreter deadlock. Keep
//! all cases in one test (sequential `Python::attach`/detach on one thread);
//! do NOT split into several `#[test]` fns without `--test-threads=1`.

use djust_core::{Context, Value};
use djust_templates::parser::Node;
use djust_templates::registry;
use djust_templates::renderer::render_nodes;
use pyo3::ffi::c_str;
use pyo3::prelude::*;
use std::collections::HashMap;

/// Build the context shared by every case: a list, an object, and two scalars.
fn block_ctx() -> Context {
    let mut ctx = Context::new();
    ctx.set(
        "items".to_string(),
        Value::List(vec![
            Value::Integer(1),
            Value::Integer(2),
            Value::Integer(3),
        ]),
    );
    let mut obj = HashMap::new();
    obj.insert("key".to_string(), Value::String("val".to_string()));
    ctx.set("obj".to_string(), Value::Object(obj));
    ctx.set("count".to_string(), Value::Integer(42));
    ctx.set("name".to_string(), Value::String("hello".to_string()));
    ctx
}

/// Render a single `Node::BlockCustomTag` with the given args and return the
/// handler output (the echo handler returns its first resolved arg).
fn render_block(tag: &str, ctx: &Context, args: &[&str]) -> String {
    let node = Node::BlockCustomTag {
        name: tag.to_string(),
        args: args.iter().map(|a| a.to_string()).collect(),
        children: vec![],
    };
    render_nodes(std::slice::from_ref(&node), ctx).expect("render block custom tag")
}

#[test]
fn block_custom_tag_arg_resolution_json_encodes_structured_values() {
    // Embed CPython and register a block handler whose render() echoes args[0].
    let tag = "b2042_echo";
    Python::initialize();
    Python::attach(|py| {
        let module = PyModule::from_code(
            py,
            c_str!(
                "class EchoArg:\n    def render(self, args, content, context):\n        return args[0] if args else ''\n"
            ),
            c_str!("echo_block.py"),
            c_str!("echo_block"),
        )
        .expect("compile echo block handler module");
        let handler: Py<PyAny> = module.getattr("EchoArg").unwrap().call0().unwrap().unbind();
        registry::register_block_tag_handler(py, tag.to_string(), format!("end{tag}"), handler)
            .expect("register echo block handler");
    });

    let ctx = block_ctx();

    // --- Core #2042 fix: a bare list arg reaches the handler as JSON, not the
    // opaque "[List]" placeholder.
    let list_out = render_block(tag, &ctx, &["items"]);
    assert_eq!(
        list_out, "[1,2,3]",
        "block handler must receive the list as JSON (#2042), got {list_out:?}"
    );
    assert_ne!(
        list_out, "[List]",
        "list arg must NOT collapse to the opaque placeholder"
    );

    // --- A bare object arg reaches the handler as JSON, not "[Object]".
    let obj_out = render_block(tag, &ctx, &["obj"]);
    assert_eq!(
        obj_out, r#"{"key":"val"}"#,
        "block handler must receive the object as JSON (#2042), got {obj_out:?}"
    );
    assert_ne!(
        obj_out, "[Object]",
        "object arg must NOT collapse to the opaque placeholder"
    );

    // --- Scalars are unchanged by the refactor (behavior-preserving): an int
    // inlines as its Display string, exactly as before.
    assert_eq!(render_block(tag, &ctx, &["count"]), "42");
    assert_eq!(render_block(tag, &ctx, &["name"]), "hello");

    // --- The `key=value` form JSON-encodes a structured value too (the fix
    // applies to the named-parameter branch, not only bare names).
    let kwarg_out = render_block(tag, &ctx, &["rows=items"]);
    assert_eq!(
        kwarg_out, "rows=[1,2,3]",
        "key=value with a list value must JSON-encode the value (#2042), got {kwarg_out:?}"
    );
    // Scalar key=value stays Display-formatted.
    assert_eq!(render_block(tag, &ctx, &["n=count"]), "n=42");

    // --- A keyword operand NOT in the context is kept literal (regroup-style
    // `by` / `as` tokens survive) — the same keep-literal-on-miss semantics the
    // shared `resolve_tag_arg` gives AssignTag. Proves the fold onto the shared
    // helper preserved this contract for BlockCustomTag.
    assert_eq!(render_block(tag, &ctx, &["by"]), "by");
    // A quoted string literal is passed through unchanged.
    assert_eq!(render_block(tag, &ctx, &["'grouper'"]), "'grouper'");
}
