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
use std::collections::HashMap;

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

/// A compiled Django template
#[pyclass]
pub struct Template {
    nodes: Vec<Node>,
    source: String,
}

impl Template {
    pub fn new(source: &str) -> Result<Self> {
        let tokens = lexer::tokenize(source)?;
        let nodes = parser::parse(&tokens)?;

        Ok(Self {
            nodes,
            source: source.to_string(),
        })
    }

    pub fn render(&self, context: &Context) -> Result<String> {
        self.render_with_loader(context, &NoOpTemplateLoader)
    }

    /// Render with a custom template loader for inheritance and {% include %} support
    pub fn render_with_loader<L: TemplateLoader>(
        &self,
        context: &Context,
        loader: &L,
    ) -> Result<String> {
        // Check if template uses inheritance
        let uses_extends = self
            .nodes
            .iter()
            .any(|node| matches!(node, Node::Extends(_)));

        if uses_extends {
            // Build inheritance chain
            let chain = build_inheritance_chain(self.nodes.clone(), loader, 10)?;

            // Get root template nodes with block overrides applied
            let root_nodes = chain.get_root_nodes();
            let final_nodes = chain.apply_block_overrides(root_nodes);

            // Render the merged template with loader for {% include %} support
            render_nodes_with_loader(&final_nodes, context, Some(loader))
        } else {
            // No inheritance, render with loader for {% include %} support
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
    Ok(template.render(&ctx)?)
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
}
