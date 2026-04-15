//! Fast Django-compatible template engine
//!
//! This crate provides a high-performance template engine that is compatible
//! with Django template syntax, including variables, filters, tags, and
//! template inheritance.

// PyResult type annotations are required by PyO3 API
#![allow(clippy::useless_conversion)]

use djust_core::{Context, DjangoRustError, Result, Value};
use once_cell::sync::Lazy;
use pyo3::prelude::*;
use regex::Regex;
use std::collections::{HashMap, HashSet};
use std::sync::OnceLock;

pub mod filters;
pub mod inheritance;
pub mod lexer;
pub mod parser;
pub mod registry;
pub mod renderer;
pub mod tags;

use inheritance::{build_inheritance_chain, TemplateLoader};
use parser::Node;
use renderer::render_nodes_with_loader;

// Re-export for JIT auto-serialization
pub use parser::extract_template_variables;

// These regexes may be used in future template parsing improvements
#[allow(dead_code)]
static VAR_REGEX: Lazy<Regex> = Lazy::new(|| Regex::new(r"\{\{([^}]+)\}\}").unwrap());

#[allow(dead_code)]
static TAG_REGEX: Lazy<Regex> = Lazy::new(|| Regex::new(r"\{%([^%]+)%\}").unwrap());

/// Cached result of `{% extends %}` inheritance resolution.
/// Stored in `OnceLock` on `Template` — computed once on first render,
/// shared across all sessions via `Arc<Template>` in `TEMPLATE_CACHE`.
struct ResolvedInheritance {
    final_nodes: Vec<Node>,
    final_node_deps: Vec<HashSet<String>>,
}

/// A compiled Django template
#[pyclass]
pub struct Template {
    nodes: Vec<Node>,
    source: String,
    /// Per-node dependency sets for partial rendering optimisation.
    node_deps: Vec<HashSet<String>>,
    /// Lazily resolved inheritance: final merged nodes + deps for `{% extends %}` templates.
    resolved: OnceLock<ResolvedInheritance>,
}

impl Template {
    pub fn new(source: &str) -> Result<Self> {
        let tokens = lexer::tokenize(source)?;
        let nodes = parser::parse(&tokens)?;
        let node_deps = parser::extract_per_node_deps(&nodes);

        Ok(Self {
            nodes,
            source: source.to_string(),
            node_deps,
            resolved: OnceLock::new(),
        })
    }

    /// Per-node dependency sets (top-level context variable names each node uses).
    pub fn node_deps(&self) -> &[HashSet<String>] {
        &self.node_deps
    }

    /// Returns `true` if this template uses `{% extends %}` inheritance.
    pub fn uses_extends(&self) -> bool {
        self.nodes
            .iter()
            .any(|node| matches!(node, Node::Extends(_)))
    }

    /// Resolve `{% extends %}` inheritance and cache the final merged nodes.
    /// No-op for templates without `{% extends %}`.
    /// Thread-safe: `OnceLock` ensures only one thread resolves.
    pub fn resolve_inheritance<L: TemplateLoader>(&self, loader: &L) -> Result<()> {
        if !self.uses_extends() || self.resolved.get().is_some() {
            return Ok(());
        }
        let chain = build_inheritance_chain(self.nodes.clone(), loader, 10)?;
        let root_nodes = chain.get_root_nodes();
        let final_nodes = chain.apply_block_overrides(root_nodes);
        let final_node_deps = parser::extract_per_node_deps(&final_nodes);
        // OnceLock::set returns Err if already set (race), which is fine
        let _ = self.resolved.set(ResolvedInheritance {
            final_nodes,
            final_node_deps,
        });
        Ok(())
    }

    /// Get the effective nodes for rendering.
    /// Returns resolved final nodes if inheritance was resolved, original nodes otherwise.
    fn effective_nodes(&self) -> &[Node] {
        if let Some(resolved) = self.resolved.get() {
            &resolved.final_nodes
        } else {
            &self.nodes
        }
    }

    /// Get the effective node deps for partial rendering.
    fn effective_node_deps(&self) -> &[HashSet<String>] {
        if let Some(resolved) = self.resolved.get() {
            &resolved.final_node_deps
        } else {
            &self.node_deps
        }
    }

    pub fn render(&self, context: &Context) -> Result<String> {
        self.render_with_loader(context, &NoOpTemplateLoader)
    }

    /// Render all nodes, returning full HTML and per-node fragment cache.
    pub fn render_with_loader_collecting<L: TemplateLoader>(
        &self,
        context: &Context,
        loader: &L,
    ) -> Result<(String, Vec<String>)> {
        renderer::render_nodes_collecting(self.effective_nodes(), context, Some(loader))
    }

    /// Partial render: re-render only nodes whose deps intersect `changed_keys`.
    ///
    /// Falls back to a full collecting render when `{% extends %}` hasn't been resolved.
    /// Returns `(full_html, new_fragment_cache, changed_node_indices)`.
    pub fn render_with_loader_partial<L: TemplateLoader>(
        &self,
        context: &Context,
        loader: &L,
        changed_keys: &HashSet<String>,
        node_html_cache: &[String],
    ) -> Result<(String, Vec<String>, Vec<usize>)> {
        if self.uses_extends() && self.resolved.get().is_none() {
            // Extends not yet resolved — fall back to full render
            let (html, fragments) =
                renderer::render_nodes_collecting(&self.nodes, context, Some(loader))?;
            let changed: Vec<usize> = (0..fragments.len()).collect();
            return Ok((html, fragments, changed));
        }

        renderer::render_nodes_partial(
            self.effective_nodes(),
            self.effective_node_deps(),
            context,
            Some(loader),
            changed_keys,
            node_html_cache,
        )
    }

    /// Render with a custom template loader for inheritance and {% include %} support
    pub fn render_with_loader<L: TemplateLoader>(
        &self,
        context: &Context,
        loader: &L,
    ) -> Result<String> {
        // Use cached resolved nodes if available
        if let Some(resolved) = self.resolved.get() {
            return render_nodes_with_loader(&resolved.final_nodes, context, Some(loader));
        }

        // Check if template uses inheritance
        if self.uses_extends() {
            // Build inheritance chain (not cached — call resolve_inheritance() first)
            let chain = build_inheritance_chain(self.nodes.clone(), loader, 10)?;
            let root_nodes = chain.get_root_nodes();
            let final_nodes = chain.apply_block_overrides(root_nodes);
            render_nodes_with_loader(&final_nodes, context, Some(loader))
        } else {
            render_nodes_with_loader(&self.nodes, context, Some(loader))
        }
    }
}

/// No-op template loader for templates without inheritance
struct NoOpTemplateLoader;

impl TemplateLoader for NoOpTemplateLoader {
    fn load_template(&self, name: &str) -> Result<Vec<Node>> {
        Err(DjangoRustError::TemplateError(format!(
            "Template loader not configured. Cannot load parent template: {name}"
        )))
    }
}

#[pymethods]
impl Template {
    #[new]
    fn py_new(source: &str) -> PyResult<Self> {
        Ok(Template::new(source)?)
    }

    fn py_render(&self, context_dict: HashMap<String, Value>) -> PyResult<String> {
        let context = Context::from_dict(context_dict);
        Ok(self.render(&context)?)
    }

    #[getter]
    fn source(&self) -> String {
        self.source.clone()
    }
}

/// Fast template rendering function for Python
#[pyfunction]
fn render_template(source: String, context: HashMap<String, Value>) -> PyResult<String> {
    let template = Template::new(&source)?;
    let ctx = Context::from_dict(context);
    let result = template.render(&ctx)?;
    // Strip VDOM placeholder comments in standalone rendering
    Ok(result.replace("<!--dj-if-->", ""))
}

/// Python module for template functionality
#[pymodule]
fn djust_templates(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Template>()?;
    m.add_function(wrap_pyfunction!(render_template, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_simple_variable() {
        let template = Template::new("Hello {{ name }}!").unwrap();
        let mut context = Context::new();
        context.set("name".to_string(), Value::String("World".to_string()));

        let result = template.render(&context).unwrap();
        assert_eq!(result, "Hello World!");
    }

    #[test]
    fn test_missing_variable() {
        let template = Template::new("Hello {{ name }}!").unwrap();
        let context = Context::new();

        let result = template.render(&context).unwrap();
        assert_eq!(result, "Hello !");
    }

    // In-memory template loader for testing
    struct TestTemplateLoader {
        templates: HashMap<String, String>,
    }

    impl TestTemplateLoader {
        fn new() -> Self {
            Self {
                templates: HashMap::new(),
            }
        }

        fn add(&mut self, name: &str, source: &str) {
            self.templates.insert(name.to_string(), source.to_string());
        }
    }

    impl TemplateLoader for TestTemplateLoader {
        fn load_template(&self, name: &str) -> Result<Vec<Node>> {
            if let Some(source) = self.templates.get(name) {
                let tokens = lexer::tokenize(source)?;
                parser::parse(&tokens)
            } else {
                Err(DjangoRustError::TemplateError(format!(
                    "Template not found: {name}"
                )))
            }
        }
    }

    #[test]
    fn test_basic_inheritance() {
        let mut loader = TestTemplateLoader::new();

        // Base template
        loader.add(
            "base.html",
            "<html><head>{% block title %}Default{% endblock %}</head><body>{% block content %}{% endblock %}</body></html>",
        );

        // Child template
        let child_source =
            "{% extends \"base.html\" %}{% block title %}My Page{% endblock %}{% block content %}Hello World{% endblock %}";
        let child_template = Template::new(child_source).unwrap();

        let context = Context::new();
        let result = child_template
            .render_with_loader(&context, &loader)
            .unwrap();

        // Should have child's blocks in parent's structure
        assert!(result.contains("<html>"));
        assert!(result.contains("My Page"));
        assert!(result.contains("Hello World"));
    }

    #[test]
    fn test_inheritance_block_override() {
        let mut loader = TestTemplateLoader::new();

        loader.add(
            "base.html",
            "Header {% block content %}Default content{% endblock %} Footer",
        );

        let child = Template::new(
            "{% extends \"base.html\" %}{% block content %}Child content{% endblock %}",
        )
        .unwrap();

        let context = Context::new();
        let result = child.render_with_loader(&context, &loader).unwrap();

        assert!(result.contains("Header"));
        assert!(result.contains("Child content"));
        assert!(!result.contains("Default content"));
        assert!(result.contains("Footer"));
    }

    #[test]
    fn test_no_inheritance() {
        let template = Template::new("<html>{% block content %}Test{% endblock %}</html>").unwrap();
        let context = Context::new();
        let result = template.render(&context).unwrap();

        // Should render normally without inheritance
        assert_eq!(result, "<html>Test</html>");
    }

    #[test]
    fn test_multi_level_inheritance() {
        let mut loader = TestTemplateLoader::new();

        // Grandparent template
        loader.add(
            "grandparent.html",
            "{% block header %}Grandparent Header{% endblock %} | {% block content %}Grandparent Content{% endblock %}",
        );

        // Parent template extends grandparent, overrides only header
        loader.add(
            "parent.html",
            "{% extends \"grandparent.html\" %}{% block header %}Parent Header{% endblock %}",
        );

        // Child template extends parent, overrides only content
        let child_source =
            "{% extends \"parent.html\" %}{% block content %}Child Content{% endblock %}";
        let child_template = Template::new(child_source).unwrap();

        let context = Context::new();
        let result = child_template
            .render_with_loader(&context, &loader)
            .unwrap();

        // Should have parent's header and child's content
        assert!(result.contains("Parent Header"));
        assert!(result.contains("Child Content"));
        assert!(!result.contains("Grandparent Header"));
        assert!(!result.contains("Grandparent Content"));
    }

    #[test]
    fn test_inheritance_with_variables() {
        let mut loader = TestTemplateLoader::new();

        loader.add(
            "base.html",
            "{% block title %}{{ site_name }}{% endblock %} | {% block content %}{% endblock %}",
        );

        let child_source =
            "{% extends \"base.html\" %}{% block content %}Welcome {{ user }}{% endblock %}";
        let child_template = Template::new(child_source).unwrap();

        let mut context = Context::new();
        context.set(
            "site_name".to_string(),
            Value::String("My Site".to_string()),
        );
        context.set("user".to_string(), Value::String("John".to_string()));

        let result = child_template
            .render_with_loader(&context, &loader)
            .unwrap();

        assert!(result.contains("My Site"));
        assert!(result.contains("Welcome John"));
    }

    #[test]
    fn test_empty_block_override() {
        let mut loader = TestTemplateLoader::new();

        loader.add(
            "base.html",
            "Before {% block content %}Default Content{% endblock %} After",
        );

        // Child overrides with empty block
        let child_source = "{% extends \"base.html\" %}{% block content %}{% endblock %}";
        let child_template = Template::new(child_source).unwrap();

        let context = Context::new();
        let result = child_template
            .render_with_loader(&context, &loader)
            .unwrap();

        assert_eq!(result, "Before  After");
        assert!(!result.contains("Default Content"));
    }

    #[test]
    fn test_inheritance_with_for_loop() {
        let mut loader = TestTemplateLoader::new();

        loader.add("base.html", "<ul>{% block items %}{% endblock %}</ul>");

        let child_source = "{% extends \"base.html\" %}{% block items %}{% for item in items %}<li>{{ item }}</li>{% endfor %}{% endblock %}";
        let child_template = Template::new(child_source).unwrap();

        let mut context = Context::new();
        context.set(
            "items".to_string(),
            Value::List(vec![
                Value::String("A".to_string()),
                Value::String("B".to_string()),
                Value::String("C".to_string()),
            ]),
        );

        let result = child_template
            .render_with_loader(&context, &loader)
            .unwrap();

        assert!(result.contains("<ul>"));
        assert!(result.contains("<li>A</li>"));
        assert!(result.contains("<li>B</li>"));
        assert!(result.contains("<li>C</li>"));
        assert!(result.contains("</ul>"));
    }

    // ── Partial rendering tests ──────────────────────────────────────

    #[test]
    fn test_extract_per_node_deps() {
        let source = "Hello {{ name }}! {% if active %}Active{% endif %}{% for item in items %}{{ item }}{% endfor %}";
        let template = Template::new(source).unwrap();
        let deps = template.node_deps();

        // Node 0: Text("Hello ") — no deps
        assert!(deps[0].is_empty(), "Text node should have no deps");
        // Node 1: Variable("name") — deps = {"name"}
        assert!(
            deps[1].contains("name"),
            "Variable node should depend on 'name'"
        );
        // Node 2: Text("! ") — no deps
        assert!(deps[2].is_empty());
        // Node 3: If { condition: "active" ... } — deps include "active"
        assert!(deps[3].contains("active"));
        // Node 4: For { iterable: "items" ... } — deps include "items"
        assert!(deps[4].contains("items"));
    }

    #[test]
    fn test_render_nodes_collecting() {
        let source = "<p>{{ greeting }}</p> <span>{{ name }}</span>";
        let template = Template::new(source).unwrap();

        let mut context = Context::new();
        context.set("greeting".to_string(), Value::String("Hello".to_string()));
        context.set("name".to_string(), Value::String("World".to_string()));

        let (full, fragments) = template
            .render_with_loader_collecting(&context, &NoOpTemplateLoader)
            .unwrap();

        // Fragments should concatenate to full HTML
        let concatenated: String = fragments.iter().cloned().collect();
        assert_eq!(full, concatenated);
        assert!(full.contains("Hello"));
        assert!(full.contains("World"));
    }

    #[test]
    fn test_render_nodes_partial_skips_unchanged() {
        let source = "<p>{{ greeting }}</p><span>{{ name }}</span>";
        let template = Template::new(source).unwrap();

        let mut context = Context::new();
        context.set("greeting".to_string(), Value::String("Hello".to_string()));
        context.set("name".to_string(), Value::String("World".to_string()));

        // First: full collecting render to populate cache
        let (_full, fragments) = template
            .render_with_loader_collecting(&context, &NoOpTemplateLoader)
            .unwrap();

        // Now change only "name"
        context.set("name".to_string(), Value::String("Rust".to_string()));
        let changed_keys: HashSet<String> = ["name".to_string()].into_iter().collect();

        let (partial_html, new_fragments, changed_indices) = template
            .render_with_loader_partial(&context, &NoOpTemplateLoader, &changed_keys, &fragments)
            .unwrap();

        // The output should contain the new name
        assert!(partial_html.contains("Rust"));
        // The greeting node should NOT have been re-rendered (not in changed_indices)
        // Text nodes and the greeting variable node should be skipped
        // Only the name variable node should be in changed_indices
        assert!(
            !changed_indices.is_empty(),
            "At least one node should have been re-rendered"
        );
        // The "greeting" variable node (index 1) should NOT be in changed_indices
        assert!(
            !changed_indices.contains(&1),
            "greeting node should be cached, not re-rendered"
        );

        // Verify the partial render matches what a full render would produce
        let full_html = template
            .render_with_loader(&context, &NoOpTemplateLoader)
            .unwrap();
        assert_eq!(partial_html, full_html);

        // Verify new_fragments concatenate to the full output
        let concatenated: String = new_fragments.iter().cloned().collect();
        assert_eq!(partial_html, concatenated);
    }

    #[test]
    fn test_partial_render_text_nodes_never_rerender() {
        let source = "Static text {{ dynamic }}";
        let template = Template::new(source).unwrap();

        let mut context = Context::new();
        context.set("dynamic".to_string(), Value::String("v1".to_string()));

        let (_full, fragments) = template
            .render_with_loader_collecting(&context, &NoOpTemplateLoader)
            .unwrap();

        context.set("dynamic".to_string(), Value::String("v2".to_string()));
        let changed_keys: HashSet<String> = ["dynamic".to_string()].into_iter().collect();

        let (_html, _new_frags, changed_indices) = template
            .render_with_loader_partial(&context, &NoOpTemplateLoader, &changed_keys, &fragments)
            .unwrap();

        // Text node (index 0) should NOT be re-rendered
        assert!(
            !changed_indices.contains(&0),
            "Text node should never re-render"
        );
    }

    #[test]
    fn test_uses_extends() {
        let plain = Template::new("<p>Hello</p>").unwrap();
        assert!(!plain.uses_extends());

        let extending =
            Template::new("{% extends \"base.html\" %}{% block content %}X{% endblock %}").unwrap();
        assert!(extending.uses_extends());
    }
}
