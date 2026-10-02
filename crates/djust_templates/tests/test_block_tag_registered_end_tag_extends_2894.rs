//! #2894 — `{% extends %}` re-serialises a block custom tag with the end tag
//! the handler REGISTERED.
//!
//! `nodes_to_template_string` (the inheritance resolver's AST -> source step)
//! wrote `{% end{name} %}` for `Node::BlockCustomTag` and
//! `Node::RawBlockCustomTag`. A tag that names its own closing tag, such as
//! djust's `{% theme_card_block %}…{% end_theme_card_block %}`, came out as
//! `{% endtheme_card_block %}` and the resolved template failed to parse
//! ("expected 'end_theme_card_block'"), so the tag worked in a template with no
//! `{% extends %}` and broke in every page that had a base template.
//!
//! Drives the real public path (`resolve_template_inheritance`, which is what
//! `RustLiveView` uses for a `template_name`) with real handlers registered,
//! then re-parses the resolved source.
//!
//! IMPORTANT — a SINGLE `#[test]`: several threads attaching to the embedded
//! CPython interpreter concurrently deadlock (see
//! `test_block_custom_tag_arg_json_2042.rs`).

use djust_templates::inheritance::resolve_template_inheritance;
use djust_templates::registry;
use pyo3::ffi::c_str;
use pyo3::prelude::*;
use std::fs;

const HANDLERS: &std::ffi::CStr = c_str!(
    "class Wrap:\n    def render(self, args, content, context):\n        return content\n\nclass Raw:\n    def render(self, args, content, context):\n        return content\n"
);

fn handler(py: Python<'_>) -> Py<PyAny> {
    let module = PyModule::from_code(py, HANDLERS, c_str!("h2894.py"), c_str!("h2894"))
        .expect("compile handler module");
    module.getattr("Wrap").unwrap().call0().unwrap().unbind()
}

fn resolve(base: &str, child: &str) -> String {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("base.html"), base).unwrap();
    fs::write(dir.path().join("child.html"), child).unwrap();
    resolve_template_inheritance("child.html", &[dir.path().to_path_buf()])
        .expect("an inheritance chain holding the tag must resolve")
}

#[test]
fn a_block_tag_keeps_its_registered_end_tag_through_extends() {
    Python::initialize();
    Python::attach(|py| {
        registry::register_block_tag_handler(
            py,
            "b2894_own".into(),
            "end_b2894_own".into(),
            handler(py),
        )
        .expect("register block handler with its own end tag");
        registry::register_block_tag_handler(
            py,
            "b2894_std".into(),
            "endb2894_std".into(),
            handler(py),
        )
        .expect("register block handler with the standard end tag");
        registry::register_raw_block_tag_handler(
            py,
            "r2894_own".into(),
            "end_r2894_own".into(),
            handler(py),
        )
        .expect("register raw-block handler with its own end tag");
    });

    let base = "<main>{% block content %}{% endblock %}</main>";
    let child = "{% extends 'base.html' %}{% block content %}\
        {% b2894_own x=1 %}<p>{{ n }}</p>{% end_b2894_own %}\
        {% b2894_std %}std{% endb2894_std %}\
        {% r2894_own %}raw {{ not_a_var }}{% end_r2894_own %}\
        {% endblock %}";
    let resolved = resolve(base, child);

    // Each tag closes with the end tag it registered.
    assert!(resolved.contains("{% end_b2894_own %}"), "{resolved}");
    assert!(resolved.contains("{% endb2894_std %}"), "{resolved}");
    assert!(resolved.contains("{% end_r2894_own %}"), "{resolved}");
    // Not the `end{name}` spelling the serializer used to invent.
    assert!(!resolved.contains("endb2894_own"), "{resolved}");
    assert!(!resolved.contains("endr2894_own"), "{resolved}");

    // And the resolved template parses again, which is what failed before.
    let tokens = djust_templates::lexer::tokenize(&resolved).expect("lex resolved template");
    djust_templates::parser::parse(&tokens).expect("the resolved template must re-parse");
}
