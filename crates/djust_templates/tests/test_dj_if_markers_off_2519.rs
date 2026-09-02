//! #2519 — the `emit_dj_if_markers` render-time flag and the `liveview`
//! cargo feature.
//!
//! The `<!--dj-if-->` placeholder (#295) and the `<!--dj-if id="if-…"-->` /
//! `<!--/dj-if-->` pair (#1358/#1832) are the VDOM differ's keyed boundaries.
//! They are meaningful only on the LiveView path; on the plain
//! `DjustTemplateBackend` path they were bytes in a comment that Django never
//! emits (six `template_tests` FAILs of the shape `'<!--dj-if-->' != ''`).
//!
//! Two mechanisms, two halves of this file:
//!
//! - **The flag** (`Context::set_emit_dj_if_markers(false)`) is what the plain
//!   entries in `djust_live` set. With it off, NO marker form is emitted —
//!   including inside `{% include %}` and `{% include … only %}` (the one
//!   place the renderer builds a fresh `Context` mid-render, which must copy
//!   the flag).
//! - **The feature** (`liveview`, default on) gates compilation. Without it the
//!   helper the `Node::If` arm consults is a constant `false`, so even a
//!   default `Context` emits nothing, and `<RustX …/>` components are a
//!   `TemplateError`.
//!
//! The default-`Context` tests are the LiveView pin: with the feature on, the
//! bytes must be byte-identical to today's (the expected strings are lifted
//! from `python/tests/test_template_if_markers.py`).

use djust_core::{Context, Result, Value};
use djust_templates::inheritance::TemplateLoader;
use djust_templates::{lexer, parser, Template};
use indexmap::IndexMap;
use std::collections::HashMap;

/// Inline test loader — the same shape as `test_if_markers.rs`'s.
struct InMemLoader {
    templates: HashMap<String, String>,
}

impl InMemLoader {
    fn new() -> Self {
        Self {
            templates: HashMap::new(),
        }
    }

    fn add(&mut self, name: &str, source: &str) {
        self.templates.insert(name.to_string(), source.to_string());
    }
}

impl TemplateLoader for InMemLoader {
    fn load_template(&self, name: &str) -> Result<Vec<parser::Node>> {
        let source = self.templates.get(name).ok_or_else(|| {
            djust_core::DjangoRustError::TemplateError(format!("Template not found: {name}"))
        })?;
        let tokens = lexer::tokenize(source)?;
        parser::parse_with_source(&tokens, source)
    }
}

fn loader_with_includes() -> InMemLoader {
    let mut loader = InMemLoader::new();
    loader.add("inc_false_if.html", "{% if foo %}<b>foo</b>{% endif %}");
    loader.add("inc_false_if_text.html", "{% if foo %}foo{% endif %}");
    loader
}

fn ctx_with(pairs: &[(&str, Value)]) -> Context {
    let mut ctx = Context::new();
    for (k, v) in pairs {
        ctx.set((*k).to_string(), v.clone());
    }
    ctx
}

/// A `Context` with marker emission switched OFF — what the plain entries build.
fn ctx_off(pairs: &[(&str, Value)]) -> Context {
    let mut ctx = ctx_with(pairs);
    ctx.set_emit_dj_if_markers(false);
    ctx
}

fn items() -> Value {
    Value::List(vec![Value::Integer(1), Value::Integer(2)])
}

fn render(source: &str, ctx: &Context) -> String {
    Template::new(source)
        .expect("template should parse")
        .render(ctx)
        .expect("template should render")
}

fn render_with_loader(source: &str, ctx: &Context) -> String {
    Template::new(source)
        .expect("template should parse")
        .render_with_loader(ctx, &loader_with_includes())
        .expect("template should render")
}

/// `id="if-<8 hex>-N…"` → `id="if-N…"`, mirroring the Python helper of the same
/// name, so the LiveView pin can assert the exact legacy strings.
#[cfg(feature = "liveview")]
fn strip_prefix(rendered: &str) -> String {
    let mut out = String::with_capacity(rendered.len());
    let mut rest = rendered;
    while let Some(pos) = rest.find("id=\"if-") {
        let (head, tail) = rest.split_at(pos + "id=\"if-".len());
        out.push_str(head);
        // 8 hex chars + '-'
        let hex_ok = tail.len() > 9
            && tail[..8].bytes().all(|b| b.is_ascii_hexdigit())
            && tail.as_bytes()[8] == b'-';
        rest = if hex_ok { &tail[9..] } else { tail };
    }
    out.push_str(rest);
    out
}

// ---------------------------------------------------------------------------
// The flag — plain-entry behaviour, feature-independent (runs under both
// `--features liveview` and `--no-default-features`).
// ---------------------------------------------------------------------------

mod flag_off {
    use super::*;

    #[test]
    fn issue_shape_if_elif_both_false_renders_empty() {
        let ctx = ctx_off(&[]);
        assert_eq!(
            render("{% if foo %}foo{% elif bar %}bar{% endif %}", &ctx),
            ""
        );
    }

    #[test]
    fn text_if_false_no_legacy_placeholder() {
        let ctx = ctx_off(&[]);
        assert_eq!(render("{% if foo %}x{% endif %}", &ctx), "");
    }

    #[test]
    fn text_if_true_is_bare_text() {
        let ctx = ctx_off(&[("foo", Value::Bool(true))]);
        assert_eq!(render("{% if foo %}x{% endif %}", &ctx), "x");
    }

    #[test]
    fn text_if_else_false_is_bare_else() {
        let ctx = ctx_off(&[]);
        assert_eq!(render("{% if foo %}x{% else %}y{% endif %}", &ctx), "y");
    }

    #[test]
    fn element_if_false_renders_empty_no_pair() {
        let ctx = ctx_off(&[]);
        assert_eq!(render("{% if foo %}<b>x</b>{% endif %}", &ctx), "");
    }

    #[test]
    fn element_if_true_is_bare_body_no_pair() {
        let ctx = ctx_off(&[("foo", Value::Bool(true))]);
        assert_eq!(render("{% if foo %}<b>x</b>{% endif %}", &ctx), "<b>x</b>");
    }

    #[test]
    fn element_if_else_false_is_bare_else_no_pair() {
        let ctx = ctx_off(&[]);
        assert_eq!(
            render("{% if foo %}<b>x</b>{% else %}<i>y</i>{% endif %}", &ctx),
            "<i>y</i>"
        );
    }

    #[test]
    fn for_nested_text_if_false_renders_empty() {
        let ctx = ctx_off(&[("items", items())]);
        assert_eq!(
            render(
                "{% for i in items %}{% if foo %}x{% endif %}{% endfor %}",
                &ctx
            ),
            ""
        );
    }

    #[test]
    fn for_nested_element_if_false_renders_empty() {
        let ctx = ctx_off(&[("items", items())]);
        assert_eq!(
            render(
                "{% for i in items %}{% if foo %}<b>x</b>{% endif %}{% endfor %}",
                &ctx
            ),
            ""
        );
    }

    #[test]
    fn for_nested_element_if_true_is_bare_bodies() {
        let ctx = ctx_off(&[("items", items())]);
        assert_eq!(
            render(
                "{% for i in items %}{% if i %}<b>{{ i }}</b>{% endif %}{% endfor %}",
                &ctx
            ),
            "<b>1</b><b>2</b>"
        );
    }

    #[test]
    fn if_in_attribute_false_unchanged() {
        // Already clean before #2519 (#380 `in_tag_context`); pinned so the
        // flag cannot regress it.
        let ctx = ctx_off(&[]);
        assert_eq!(
            render("<div class=\"a {% if foo %}b{% endif %}\"></div>", &ctx),
            "<div class=\"a \"></div>"
        );
    }

    #[test]
    fn wrapped_element_if_true_is_bare() {
        let ctx = ctx_off(&[("foo", Value::Integer(1))]);
        assert_eq!(
            render("<div>{% if foo %}<b>x</b>{% endif %}</div>", &ctx),
            "<div><b>x</b></div>"
        );
    }

    #[test]
    fn include_with_false_text_if_renders_empty() {
        let ctx = ctx_off(&[]);
        assert_eq!(
            render_with_loader("{% include 'inc_false_if_text.html' %}", &ctx),
            ""
        );
    }

    #[test]
    fn include_with_false_element_if_renders_empty() {
        let ctx = ctx_off(&[]);
        assert_eq!(
            render_with_loader("{% include 'inc_false_if.html' %}", &ctx),
            ""
        );
    }

    /// `{% include … only %}` builds a FRESH `Context` (the one place the
    /// renderer does so mid-render) — it must copy the flag, or a plain page
    /// with an `only` include leaks again.
    #[test]
    fn include_only_with_false_element_if_renders_empty() {
        let ctx = ctx_off(&[]);
        assert_eq!(
            render_with_loader("{% include 'inc_false_if.html' only %}", &ctx),
            ""
        );
    }

    #[test]
    fn include_only_with_false_text_if_renders_empty() {
        let ctx = ctx_off(&[]);
        assert_eq!(
            render_with_loader("{% include 'inc_false_if_text.html' only %}", &ctx),
            ""
        );
    }

    #[test]
    fn include_with_only_true_branch_is_bare_body() {
        let ctx = ctx_off(&[]);
        assert_eq!(
            render_with_loader("{% include 'inc_false_if.html' with foo=1 only %}", &ctx),
            "<b>foo</b>"
        );
    }

    #[test]
    fn include_inside_false_if_renders_empty() {
        let ctx = ctx_off(&[]);
        assert_eq!(
            render_with_loader(
                "{% if outer %}{% include 'inc_false_if.html' %}{% endif %}",
                &ctx
            ),
            ""
        );
    }

    #[test]
    fn include_inside_true_if_is_bare_body() {
        let ctx = ctx_off(&[("outer", Value::Bool(true)), ("foo", Value::Bool(true))]);
        assert_eq!(
            render_with_loader(
                "{% if outer %}{% include 'inc_false_if.html' only %}{% endif %}",
                &ctx
            ),
            ""
        );
        assert_eq!(
            render_with_loader(
                "{% if outer %}{% include 'inc_false_if.html' %}{% endif %}",
                &ctx
            ),
            "<b>foo</b>"
        );
    }

    #[test]
    fn extends_child_and_parent_ifs_emit_nothing() {
        let mut loader = InMemLoader::new();
        loader.add(
            "parent.html",
            "<html>{% if show_header %}<header>H</header>{% endif %}\
             {% block content %}{% endblock %}</html>",
        );
        let child = Template::new(
            "{% extends \"parent.html\" %}\
             {% block content %}{% if show_a %}<div>A</div>{% endif %}{% endblock %}",
        )
        .expect("child parses");
        let ctx = ctx_off(&[("show_header", Value::Bool(true))]);
        let out = child.render_with_loader(&ctx, &loader).expect("renders");
        assert_eq!(out, "<html><header>H</header></html>");
    }

    #[test]
    fn flag_survives_clone() {
        let ctx = ctx_off(&[]);
        assert!(!ctx.emit_dj_if_markers());
        let cloned = ctx.clone();
        assert!(!cloned.emit_dj_if_markers(), "Clone must carry the flag");
        assert_eq!(render("{% if foo %}<b>x</b>{% endif %}", &cloned), "");
    }

    #[test]
    fn flag_round_trips_through_from_dict() {
        let mut map: IndexMap<String, Value> = IndexMap::new();
        map.insert("foo".to_string(), Value::Bool(false));
        let mut ctx = Context::from_dict(map);
        assert!(
            ctx.emit_dj_if_markers(),
            "default is ON (the LiveView path)"
        );
        ctx.set_emit_dj_if_markers(false);
        assert!(!ctx.emit_dj_if_markers());
        ctx.set_emit_dj_if_markers(true);
        assert!(ctx.emit_dj_if_markers());
    }
}

// ---------------------------------------------------------------------------
// The LiveView pin — a DEFAULT `Context` keeps emitting both marker forms,
// byte-identical to today's output. Feature ON only.
// ---------------------------------------------------------------------------

#[cfg(feature = "liveview")]
mod feature_on_default_context {
    use super::*;

    #[test]
    fn default_context_emits_markers_flag_is_on() {
        assert!(Context::new().emit_dj_if_markers());
    }

    #[test]
    fn legacy_placeholder_for_text_if_false() {
        let ctx = ctx_with(&[]);
        assert_eq!(render("{% if foo %}x{% endif %}", &ctx), "<!--dj-if-->");
        assert_eq!(
            render("{% if foo %}foo{% elif bar %}bar{% endif %}", &ctx),
            "<!--dj-if-->"
        );
    }

    #[test]
    fn pair_for_element_if_true_and_false() {
        let on = ctx_with(&[("show", Value::Bool(true))]);
        assert_eq!(
            strip_prefix(&render("{% if show %}<div>foo</div>{% endif %}", &on)),
            "<!--dj-if id=\"if-0\"--><div>foo</div><!--/dj-if-->"
        );
        let off = ctx_with(&[("show", Value::Bool(false))]);
        assert_eq!(
            strip_prefix(&render("{% if show %}<div>foo</div>{% endif %}", &off)),
            "<!--dj-if id=\"if-0\"--><!--/dj-if-->"
        );
    }

    #[test]
    fn pair_for_element_if_else() {
        let tmpl = "{% if show %}<div>A</div>{% else %}<span>B</span>{% endif %}";
        assert_eq!(
            strip_prefix(&render(tmpl, &ctx_with(&[("show", Value::Bool(true))]))),
            "<!--dj-if id=\"if-0\"--><div>A</div><!--/dj-if-->"
        );
        assert_eq!(
            strip_prefix(&render(tmpl, &ctx_with(&[("show", Value::Bool(false))]))),
            "<!--dj-if id=\"if-0\"--><span>B</span><!--/dj-if-->"
        );
    }

    #[test]
    fn for_nested_pair_carries_loop_path_1832() {
        let ctx = ctx_with(&[("items", items())]);
        assert_eq!(
            strip_prefix(&render(
                "{% for i in items %}{% if i %}<b>{{ i }}</b>{% endif %}{% endfor %}",
                &ctx
            )),
            "<!--dj-if id=\"if-0-0\"--><b>1</b><!--/dj-if--><!--dj-if id=\"if-0-1\"--><b>2</b><!--/dj-if-->"
        );
    }

    #[test]
    fn include_and_include_only_propagate_markers() {
        let ctx = ctx_with(&[]);
        assert_eq!(
            render_with_loader("{% include 'inc_false_if_text.html' %}", &ctx),
            "<!--dj-if-->"
        );
        assert_eq!(
            render_with_loader("{% include 'inc_false_if_text.html' only %}", &ctx),
            "<!--dj-if-->"
        );
        assert_eq!(
            strip_prefix(&render_with_loader(
                "{% include 'inc_false_if.html' only %}",
                &ctx
            )),
            "<!--dj-if id=\"if-0\"--><!--/dj-if-->"
        );
    }

    #[test]
    fn rust_component_renders_with_the_feature() {
        let out = render("<RustButton id=\"a\" label=\"b\"/>", &ctx_with(&[]));
        assert!(out.contains("<button"), "got {out:?}");
    }
}

// ---------------------------------------------------------------------------
// Without the feature: the engine is the plain Django backend. A DEFAULT
// `Context` emits nothing and `<RustX …/>` is a template error.
// ---------------------------------------------------------------------------

#[cfg(not(feature = "liveview"))]
mod feature_off {
    use super::*;

    #[test]
    fn default_context_emits_no_markers_without_the_feature() {
        // The flag lives in djust_core and still defaults to ON — the feature
        // makes the renderer ignore it.
        assert!(Context::new().emit_dj_if_markers());
        let ctx = ctx_with(&[]);
        assert_eq!(render("{% if foo %}x{% endif %}", &ctx), "");
        assert_eq!(render("{% if foo %}<b>x</b>{% endif %}", &ctx), "");
        let on = ctx_with(&[("foo", Value::Bool(true))]);
        assert_eq!(render("{% if foo %}<b>x</b>{% endif %}", &on), "<b>x</b>");
        assert_eq!(
            render_with_loader("{% include 'inc_false_if.html' only %}", &ctx),
            ""
        );
    }

    #[test]
    fn rust_component_is_a_template_error_without_the_feature() {
        let t = Template::new("<RustButton id=\"a\" label=\"b\"/>").expect("parses");
        let err = t.render(&ctx_with(&[])).expect_err("must not render");
        let msg = err.to_string();
        assert!(
            msg.contains("liveview"),
            "error should name the feature: {msg}"
        );
    }
}
