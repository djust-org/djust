//! Tests for `<!--dj-if id="if-N"-->` boundary markers around `{% if %}`
//! blocks containing element nodes.
//!
//! Foundation 1 of 3 toward issue #1358 (re-open of #256 Option A —
//! keyed VDOM diff for conditional subtrees). This iter adds the
//! marker emission only — the client-side patch applier (Iter 2) and
//! the Rust VDOM differ that uses the markers as keyed boundaries
//! (Iter 3) follow in subsequent PRs.
//!
//! Marker shape (Option B — pair per `Node::If`):
//!
//! ```html
//! <!--dj-if id="if-N"-->...rendered body...<!--/dj-if-->
//! ```
//!
//! - IDs are assigned at parse time by `assign_if_marker_ids` walking
//!   the AST in document order. Stable across re-renders of the same
//!   parsed source.
//! - The `cond=` attribute is intentionally OMITTED for safety
//!   (condition strings could contain `--` or `>` that would close
//!   the comment early). Iter 3's differ keys off the `id` alone.
//! - Pure-text conditionals (text-only true/false bodies) skip
//!   emission — text positions are sibling-stable already.
//! - The legacy `<!--dj-if-->` placeholder for false-no-else
//!   (issue #295) is preserved unchanged for pure-text conditionals.
//! - HTML attribute context (`{% if %}` inside an unclosed tag) skips
//!   marker emission — comments inside attribute values produce
//!   malformed HTML (issue #380).

use djust_core::{Context, Value};
use djust_templates::Template;
use std::collections::HashSet;

fn render(source: &str, ctx: &Context) -> String {
    let t = Template::new(source).expect("template should parse");
    t.render(ctx).expect("template should render")
}

fn ctx_bool(key: &str, val: bool) -> Context {
    let mut c = Context::new();
    c.set(key.to_string(), Value::Bool(val));
    c
}

/// Replace any `id="if-<prefix>-N"` substring in the rendered output
/// with `id="if-N"` so the existing assertions stay readable. The
/// per-template prefix (introduced as a Stage 11 fix on PR #1363 to
/// disambiguate `{% extends %}` / `{% include %}` collisions) is
/// orthogonal to the per-template counter the legacy tests check.
/// Tests that need to assert the prefix shape directly use
/// `extract_marker_ids` instead.
fn strip_prefix(rendered: &str) -> String {
    let re = regex::Regex::new(r#"id="if-[0-9a-f]{8}-(\d+)""#).expect("regex");
    re.replace_all(rendered, r#"id="if-$1""#).to_string()
}

/// Return all `if-<prefix>-N` IDs present in the rendered output,
/// preserving order of first occurrence.
fn extract_marker_ids(rendered: &str) -> Vec<String> {
    let re = regex::Regex::new(r#"id="(if-[0-9a-f]{8}-\d+)""#).expect("regex");
    let mut seen = Vec::new();
    let mut set = HashSet::new();
    for cap in re.captures_iter(rendered) {
        let id = cap[1].to_string();
        if set.insert(id.clone()) {
            seen.push(id);
        }
    }
    seen
}

// ---------------------------------------------------------------------------
// Element-bearing if blocks emit boundary markers
// ---------------------------------------------------------------------------

#[test]
fn markers_emitted_around_simple_element_if_true() {
    let result = render(
        "{% if show %}<div>foo</div>{% endif %}",
        &ctx_bool("show", true),
    );
    assert_eq!(
        strip_prefix(&result),
        r#"<!--dj-if id="if-0"--><div>foo</div><!--/dj-if-->"#
    );
}

#[test]
fn markers_emitted_around_simple_element_if_false_no_else() {
    // Element-bearing if with no else and false condition: emit the
    // pair with empty body. The closing comment adjacent to the
    // opening serves the sibling-stability role that the legacy
    // single-comment placeholder served for text-only conditionals.
    let result = render(
        "{% if show %}<div>foo</div>{% endif %}",
        &ctx_bool("show", false),
    );
    assert_eq!(
        strip_prefix(&result),
        r#"<!--dj-if id="if-0"--><!--/dj-if-->"#
    );
}

#[test]
fn markers_emitted_around_element_if_else_true() {
    let result = render(
        "{% if show %}<div>A</div>{% else %}<span>B</span>{% endif %}",
        &ctx_bool("show", true),
    );
    assert_eq!(
        strip_prefix(&result),
        r#"<!--dj-if id="if-0"--><div>A</div><!--/dj-if-->"#
    );
}

#[test]
fn markers_emitted_around_element_if_else_false() {
    let result = render(
        "{% if show %}<div>A</div>{% else %}<span>B</span>{% endif %}",
        &ctx_bool("show", false),
    );
    assert_eq!(
        strip_prefix(&result),
        r#"<!--dj-if id="if-0"--><span>B</span><!--/dj-if-->"#
    );
}

#[test]
fn markers_around_when_only_true_branch_has_elements() {
    // True branch has elements; false branch is pure text. Both
    // renders should be wrapped because the if as a whole is
    // element-bearing.
    let template = "{% if show %}<div>yes</div>{% else %}plain text{% endif %}";

    let result_true = render(template, &ctx_bool("show", true));
    assert_eq!(
        strip_prefix(&result_true),
        r#"<!--dj-if id="if-0"--><div>yes</div><!--/dj-if-->"#
    );

    let result_false = render(template, &ctx_bool("show", false));
    assert_eq!(
        strip_prefix(&result_false),
        r#"<!--dj-if id="if-0"-->plain text<!--/dj-if-->"#
    );
}

// ---------------------------------------------------------------------------
// Pure-text conditionals — no markers emitted, legacy placeholder preserved
// ---------------------------------------------------------------------------

#[test]
fn no_markers_for_pure_text_if_true() {
    let result = render("{% if show %}foo{% endif %}", &ctx_bool("show", true));
    assert_eq!(result, "foo");
    assert!(!result.contains(r#"<!--dj-if id="#));
    assert!(!result.contains("<!--/dj-if-->"));
    assert!(extract_marker_ids(&result).is_empty());
}

#[test]
fn no_markers_for_pure_text_if_false_keeps_legacy_placeholder() {
    // Issue #295 / DJE-053 behavior preserved: text-only bodies
    // continue to emit `<!--dj-if-->` for sibling stability. The new
    // boundary markers are NOT layered on top.
    let result = render("{% if show %}foo{% endif %}", &ctx_bool("show", false));
    assert_eq!(result, "<!--dj-if-->");
    assert!(!result.contains(r#"<!--dj-if id="#));
    assert!(extract_marker_ids(&result).is_empty());
}

#[test]
fn no_markers_for_pure_text_if_else() {
    let result = render(
        "{% if show %}yes{% else %}no{% endif %}",
        &ctx_bool("show", false),
    );
    assert_eq!(result, "no");
    assert!(!result.contains("dj-if"));
}

// ---------------------------------------------------------------------------
// Elif chain — nested marker pairs (Option B: pair per Node::If)
// ---------------------------------------------------------------------------

#[test]
fn elif_chain_first_branch_fires() {
    // `if A elif B else` chain: parser nests an inner If(B) inside
    // outer's false_nodes. When A is true, only the outer If fires,
    // so we get one pair around A's body.
    let template =
        "{% if a %}<div>A</div>{% elif b %}<div>B</div>{% else %}<div>C</div>{% endif %}";
    let mut c = Context::new();
    c.set("a".to_string(), Value::Bool(true));
    c.set("b".to_string(), Value::Bool(false));
    let result = render(template, &c);
    assert_eq!(
        strip_prefix(&result),
        r#"<!--dj-if id="if-0"--><div>A</div><!--/dj-if-->"#
    );
}

#[test]
fn elif_chain_second_branch_fires() {
    // When elif's condition fires, both the outer (false-then-render-
    // false_nodes) and the inner If wrap.
    let template =
        "{% if a %}<div>A</div>{% elif b %}<div>B</div>{% else %}<div>C</div>{% endif %}";
    let mut c = Context::new();
    c.set("a".to_string(), Value::Bool(false));
    c.set("b".to_string(), Value::Bool(true));
    let result = render(template, &c);
    // Outer If(if-0) renders false_nodes (which is the inner If),
    // inner If(if-1) renders <div>B</div>.
    assert_eq!(
        strip_prefix(&result),
        r#"<!--dj-if id="if-0"--><!--dj-if id="if-1"--><div>B</div><!--/dj-if--><!--/dj-if-->"#
    );
}

#[test]
fn elif_chain_else_branch_fires() {
    let template =
        "{% if a %}<div>A</div>{% elif b %}<div>B</div>{% else %}<div>C</div>{% endif %}";
    let mut c = Context::new();
    c.set("a".to_string(), Value::Bool(false));
    c.set("b".to_string(), Value::Bool(false));
    let result = render(template, &c);
    // Outer (if-0, A false) renders inner; inner (if-1, B false)
    // renders C (its false_nodes).
    assert_eq!(
        strip_prefix(&result),
        r#"<!--dj-if id="if-0"--><!--dj-if id="if-1"--><div>C</div><!--/dj-if--><!--/dj-if-->"#
    );
}

// ---------------------------------------------------------------------------
// Nested ifs — distinct IDs in document order
// ---------------------------------------------------------------------------

#[test]
fn nested_if_distinct_ids() {
    let template = "{% if a %}<div>{% if b %}<span>x</span>{% endif %}</div>{% endif %}";
    let mut c = Context::new();
    c.set("a".to_string(), Value::Bool(true));
    c.set("b".to_string(), Value::Bool(true));
    let result = render(template, &c);
    // Outer is if-0, inner is if-1 (document order).
    assert_eq!(
        strip_prefix(&result),
        r#"<!--dj-if id="if-0"--><div><!--dj-if id="if-1"--><span>x</span><!--/dj-if--></div><!--/dj-if-->"#
    );
}

// ---------------------------------------------------------------------------
// {% for %}{% if %} — same ID across iterations (parse-time stable)
// ---------------------------------------------------------------------------

#[test]
fn for_if_iteration_stable_id() {
    // The if inside the for body has marker_id assigned ONCE at parse
    // time. Each loop iteration emits the SAME id.
    let template =
        "{% for i in items %}{% if i.show %}<div>{{ i.name }}</div>{% endif %}{% endfor %}";
    let mut c = Context::new();
    let mut item1 = std::collections::HashMap::new();
    item1.insert("show".to_string(), Value::Bool(true));
    item1.insert("name".to_string(), Value::String("a".to_string()));
    let mut item2 = std::collections::HashMap::new();
    item2.insert("show".to_string(), Value::Bool(true));
    item2.insert("name".to_string(), Value::String("b".to_string()));
    c.set(
        "items".to_string(),
        Value::List(vec![Value::Object(item1), Value::Object(item2)]),
    );
    let result = render(template, &c);
    // Both iterations use if-0 (the parser only saw ONE Node::If).
    assert_eq!(
        strip_prefix(&result),
        r#"<!--dj-if id="if-0"--><div>a</div><!--/dj-if--><!--dj-if id="if-0"--><div>b</div><!--/dj-if-->"#
    );
}

// ---------------------------------------------------------------------------
// ID stability across renders
// ---------------------------------------------------------------------------

#[test]
fn id_stable_across_renders() {
    let source = "{% if show %}<div>foo</div>{% endif %}";
    let template = Template::new(source).expect("template parses");
    let c1 = ctx_bool("show", true);
    let c2 = ctx_bool("show", true);
    let r1 = template.render(&c1).unwrap();
    let r2 = template.render(&c2).unwrap();
    assert_eq!(r1, r2);
    // After Stage 11 fix on PR #1363, IDs are `if-<8-hex-chars>-N`.
    // Strip the prefix to verify the legacy positional `if-0` claim.
    assert!(strip_prefix(&r1).contains(r#"<!--dj-if id="if-0"-->"#));
}

#[test]
fn id_stable_across_separate_template_instances() {
    let source = "{% if show %}<div>foo</div>{% endif %}";
    let t1 = Template::new(source).expect("first parse");
    let t2 = Template::new(source).expect("second parse");
    let c = ctx_bool("show", true);
    let r1 = t1.render(&c).unwrap();
    let r2 = t2.render(&c).unwrap();
    assert_eq!(r1, r2);
}

// ---------------------------------------------------------------------------
// HTML attribute context — markers SKIPPED (issue #380)
// ---------------------------------------------------------------------------

#[test]
fn no_markers_in_attribute_context() {
    // {% if %} inside an HTML attribute value must not emit comment
    // markers (existing or new). Existing behavior for issue #380 is
    // preserved.
    let template = r#"<a class="nav-link {% if active %}active{% endif %}">link</a>"#;

    let mut c = Context::new();
    c.set("active".to_string(), Value::Bool(false));
    let r_false = render(template, &c);
    assert!(
        !r_false.contains("dj-if"),
        "no dj-if comments allowed inside attribute: {r_false}"
    );

    c.set("active".to_string(), Value::Bool(true));
    let r_true = render(template, &c);
    assert!(
        !r_true.contains("dj-if"),
        "no dj-if comments allowed inside attribute even when truthy: {r_true}"
    );
    assert!(r_true.contains(r#"class="nav-link active""#));
}

// ---------------------------------------------------------------------------
// Sibling positions stable when condition flips
// ---------------------------------------------------------------------------

#[test]
fn sibling_position_stable_for_element_if() {
    // When condition flips truthy/falsy on an element-bearing if,
    // the marker pair anchors the same position. This is the property
    // Iter 3's VDOM differ relies on.
    let template = "<div>{% if show %}<span>A</span>{% endif %}<i>B</i></div>";
    let mut c = Context::new();
    c.set("show".to_string(), Value::Bool(false));
    let r_false = render(template, &c);
    assert_eq!(
        strip_prefix(&r_false),
        r#"<div><!--dj-if id="if-0"--><!--/dj-if--><i>B</i></div>"#
    );

    c.set("show".to_string(), Value::Bool(true));
    let r_true = render(template, &c);
    assert_eq!(
        strip_prefix(&r_true),
        r#"<div><!--dj-if id="if-0"--><span>A</span><!--/dj-if--><i>B</i></div>"#
    );
}

// ---------------------------------------------------------------------------
// Sequential ifs at top level get distinct IDs
// ---------------------------------------------------------------------------

#[test]
fn sequential_ifs_get_distinct_ids() {
    let template = "{% if a %}<div>X</div>{% endif %}{% if b %}<div>Y</div>{% endif %}";
    let mut c = Context::new();
    c.set("a".to_string(), Value::Bool(true));
    c.set("b".to_string(), Value::Bool(true));
    let result = render(template, &c);
    assert_eq!(
        strip_prefix(&result),
        r#"<!--dj-if id="if-0"--><div>X</div><!--/dj-if--><!--dj-if id="if-1"--><div>Y</div><!--/dj-if-->"#
    );
}

// ---------------------------------------------------------------------------
// Mutation-after-capture (Action #1039 discipline): re-rendering after
// changing the context must NOT produce different IDs for the same
// parsed template.
// ---------------------------------------------------------------------------

#[test]
fn id_unchanged_after_context_mutation() {
    let source = "{% if show %}<div>foo</div>{% endif %}";
    let template = Template::new(source).expect("parse");

    let mut c = Context::new();
    c.set("show".to_string(), Value::Bool(true));
    let r1 = template.render(&c).unwrap();

    // Mutate the same context — re-render the same template.
    c.set("show".to_string(), Value::Bool(false));
    let r2 = template.render(&c).unwrap();

    // Both must reference the same `if-<prefix>-0` ID — the marker is
    // keyed by parse-time position, not render state. We strip the
    // per-source prefix to assert the positional invariant.
    assert!(strip_prefix(&r1).contains(r#"id="if-0""#));
    assert!(strip_prefix(&r2).contains(r#"id="if-0""#));
    // And the prefixed IDs must be byte-identical between r1 and r2.
    assert_eq!(extract_marker_ids(&r1), extract_marker_ids(&r2));
}

// ---------------------------------------------------------------------------
// Cross-template ID uniqueness — Stage 11 finding on PR #1363
//
// `{% extends %}` parents and `{% include %}`'d children are parsed via
// SEPARATE `parser::parse_with_source` calls, each with its own counter
// starting at 0. Without a per-template prefix the rendered output would
// contain duplicate `id="if-0"` markers, breaking Iter 3's "differ keys
// off the id alone" contract. The prefix scheme (`if-<8-hex-chars>-N`)
// derives the prefix from each template's source so independently-parsed
// templates get distinct prefixes deterministically.
// ---------------------------------------------------------------------------

mod cross_template_ids {
    use super::*;
    use djust_core::Result;
    use djust_templates::inheritance::TemplateLoader;
    use djust_templates::lexer;
    use djust_templates::parser as tparser;
    use std::collections::HashMap;

    /// Inline test loader — same shape as the in-tree
    /// `TestTemplateLoader` in `lib.rs::tests` but exposed via the
    /// public surface so the integration test can use it.
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
        fn load_template(&self, name: &str) -> Result<Vec<tparser::Node>> {
            let source = self.templates.get(name).ok_or_else(|| {
                djust_core::DjangoRustError::TemplateError(format!("Template not found: {name}"))
            })?;
            let tokens = lexer::tokenize(source)?;
            tparser::parse_with_source(&tokens, source)
        }
    }

    #[test]
    fn extends_parent_and_child_get_distinct_id_prefixes() {
        // Parent and child both contain element-bearing `{% if %}`
        // blocks. Without a per-template prefix both would emit
        // `id="if-0"` and the rendered HTML would contain
        // duplicates — which would break Iter 3's differ contract.
        let mut loader = InMemLoader::new();
        loader.add(
            "parent.html",
            "<html><body>{% if show_header %}<header>H</header>{% endif %}\
             {% block content %}<p>parent default</p>{% endblock %}\
             {% if show_footer %}<footer>F</footer>{% endif %}</body></html>",
        );

        let child_source = "{% extends \"parent.html\" %}\
            {% block content %}{% if show_a %}<div>A</div>{% endif %}\
            {% if show_b %}<div>B</div>{% endif %}{% endblock %}";
        let child = Template::new(child_source).expect("child parses");

        let mut ctx = Context::new();
        ctx.set("show_header".to_string(), Value::Bool(true));
        ctx.set("show_footer".to_string(), Value::Bool(true));
        ctx.set("show_a".to_string(), Value::Bool(true));
        ctx.set("show_b".to_string(), Value::Bool(true));

        let result = child.render_with_loader(&ctx, &loader).unwrap();

        // 4 element-bearing if blocks total: header, footer (parent) +
        // A, B (child). All 4 must have unique IDs.
        let ids = extract_marker_ids(&result);
        assert_eq!(ids.len(), 4, "expected 4 unique IDs, got {ids:?}");
        let unique: std::collections::HashSet<_> = ids.iter().collect();
        assert_eq!(unique.len(), 4, "duplicate IDs in rendered output: {ids:?}");

        // Parent's ifs and child's ifs must use DIFFERENT prefixes
        // (since they're parsed from different source strings).
        let prefixes: std::collections::HashSet<&str> = ids
            .iter()
            .map(|id| {
                // id format: "if-<8hex>-<N>" — extract the 8-hex prefix
                let after = id.strip_prefix("if-").expect("prefix");
                let dash = after.find('-').expect("dash");
                &after[..dash]
            })
            .collect();
        assert_eq!(
            prefixes.len(),
            2,
            "parent + child must contribute 2 distinct prefixes, got {prefixes:?}"
        );
    }

    #[test]
    fn include_does_not_collide_with_parent_ids() {
        // Parent has an `{% if %}`, includes a partial that ALSO has
        // an `{% if %}`. Both would naively be `if-0` if the prefix
        // scheme didn't fix the collision.
        let mut loader = InMemLoader::new();
        loader.add(
            "_partial.html",
            "{% if shown %}<div>partial</div>{% endif %}",
        );

        let parent_source = "{% if show_outer %}<section>\
            {% include \"_partial.html\" %}</section>{% endif %}";
        let parent = Template::new(parent_source).expect("parent parses");

        let mut ctx = Context::new();
        ctx.set("show_outer".to_string(), Value::Bool(true));
        ctx.set("shown".to_string(), Value::Bool(true));

        let result = parent.render_with_loader(&ctx, &loader).unwrap();

        let ids = extract_marker_ids(&result);
        assert_eq!(ids.len(), 2, "expected 2 unique IDs, got {ids:?}");
        let unique: std::collections::HashSet<_> = ids.iter().collect();
        assert_eq!(
            unique.len(),
            2,
            "parent and included template emitted duplicate IDs: {ids:?}"
        );
    }

    #[test]
    fn nested_include_chain_keeps_ids_unique() {
        // Root → mid → leaf, each with one `{% if %}`. Three distinct
        // prefixes expected; three distinct IDs in the rendered output.
        let mut loader = InMemLoader::new();
        loader.add("_leaf.html", "{% if leaf_shown %}<i>leaf</i>{% endif %}");
        loader.add(
            "_mid.html",
            "{% if mid_shown %}<span>{% include \"_leaf.html\" %}</span>{% endif %}",
        );

        let root_source = "{% if root_shown %}<div>{% include \"_mid.html\" %}</div>{% endif %}";
        let root = Template::new(root_source).expect("root parses");

        let mut ctx = Context::new();
        ctx.set("root_shown".to_string(), Value::Bool(true));
        ctx.set("mid_shown".to_string(), Value::Bool(true));
        ctx.set("leaf_shown".to_string(), Value::Bool(true));

        let result = root.render_with_loader(&ctx, &loader).unwrap();

        let ids = extract_marker_ids(&result);
        assert_eq!(
            ids.len(),
            3,
            "root + mid + leaf should produce 3 unique IDs, got {ids:?}"
        );
    }

    #[test]
    fn id_prefix_stable_across_re_renders() {
        // Same source rendered twice must produce byte-identical IDs.
        // This is the property Iter 3's caching layer will rely on.
        let mut loader = InMemLoader::new();
        loader.add("_partial.html", "{% if x %}<i>p</i>{% endif %}");

        let parent =
            Template::new("{% if a %}<div>{% include \"_partial.html\" %}</div>{% endif %}")
                .unwrap();
        let mut ctx = Context::new();
        ctx.set("a".to_string(), Value::Bool(true));
        ctx.set("x".to_string(), Value::Bool(true));

        let r1 = parent.render_with_loader(&ctx, &loader).unwrap();
        let r2 = parent.render_with_loader(&ctx, &loader).unwrap();
        assert_eq!(
            extract_marker_ids(&r1),
            extract_marker_ids(&r2),
            "same source must yield byte-identical IDs across renders"
        );
    }
}

// ---------------------------------------------------------------------------
// {% csrf_token %} is element-bearing — Stage 11 MUST-FIX #2 on PR #1363
//
// `Node::CsrfToken` renders an `<input type="hidden" ...>` element when a
// token is present in the context. The `node_is_element_bearing`
// classifier previously treated it as text-only, so
// `{% if x %}{% csrf_token %}{% endif %}` would NOT emit boundary
// markers — leaving Iter 3's differ blind to that case.
// ---------------------------------------------------------------------------

#[test]
fn csrf_token_inside_if_emits_markers() {
    let mut c = Context::new();
    c.set("show".to_string(), Value::Bool(true));
    c.set(
        "csrf_token".to_string(),
        Value::String("abc123".to_string()),
    );
    let result = render("{% if show %}{% csrf_token %}{% endif %}", &c);
    // The csrf input must be wrapped by the dj-if marker pair
    // because csrf_token is element-bearing.
    let stripped = strip_prefix(&result);
    assert!(
        stripped.starts_with(r#"<!--dj-if id="if-0"-->"#),
        "expected dj-if marker to wrap csrf_token output, got: {result}"
    );
    assert!(
        stripped.ends_with("<!--/dj-if-->"),
        "expected dj-if closing marker, got: {result}"
    );
    assert!(
        result.contains(r#"<input type="hidden" name="csrfmiddlewaretoken""#),
        "expected csrf input element, got: {result}"
    );
}

#[test]
fn variable_only_inside_if_does_not_emit_markers() {
    // Regression guard for `Node::Variable` (text-only output).
    let mut c = Context::new();
    c.set("show".to_string(), Value::Bool(true));
    c.set("name".to_string(), Value::String("hello".to_string()));
    let result = render("{% if show %}{{ name }}{% endif %}", &c);
    assert_eq!(result, "hello");
    assert!(!result.contains("dj-if"));
}

#[test]
fn raw_html_input_inside_if_emits_markers() {
    // Regression guard: a literal `<input>` in the body must also
    // trip the element-bearing classifier (basic-element baseline
    // alongside the csrf_token fix).
    let mut c = Context::new();
    c.set("show".to_string(), Value::Bool(true));
    let result = render(r#"{% if show %}<input type="hidden">{% endif %}"#, &c);
    assert!(
        strip_prefix(&result).starts_with(r#"<!--dj-if id="if-0"-->"#),
        "<input> body must be element-bearing: {result}"
    );
}
