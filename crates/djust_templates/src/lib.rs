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

    /// Render with a custom template loader for inheritance and includes
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

            // Render the merged template with loader for includes
            render_nodes_with_loader(&final_nodes, context, Some(loader))
        } else {
            // No inheritance, render normally with loader for includes
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
    fn test_nested_block_inheritance() {
        // Test case: nested blocks across inheritance levels
        // This mirrors the docs template structure:
        // - base.html has {% block content %}
        // - base_docs.html extends base, defines content with nested {% block docs_content %}
        // - index.html extends base_docs, only overrides docs_content
        let mut loader = TestTemplateLoader::new();

        // Root template with outer block
        loader.add(
            "base.html",
            "<html>{% block content %}Base Content{% endblock %}</html>",
        );

        // Intermediate template that wraps inner block inside content block
        loader.add(
            "base_docs.html",
            "{% extends \"base.html\" %}{% block content %}<div class=\"wrapper\">{% block inner %}Default Inner{% endblock %}</div>{% endblock %}",
        );

        // Child template only overrides the inner nested block
        let child_source =
            "{% extends \"base_docs.html\" %}{% block inner %}Child Inner Content{% endblock %}";
        let child_template = Template::new(child_source).unwrap();

        let context = Context::new();
        let result = child_template
            .render_with_loader(&context, &loader)
            .unwrap();

        // Should have the wrapper div from base_docs.html
        assert!(
            result.contains("<div class=\"wrapper\">"),
            "Missing wrapper div"
        );
        // Should have the child's inner content
        assert!(
            result.contains("Child Inner Content"),
            "Missing child inner content"
        );
        // Should NOT have the default inner content
        assert!(
            !result.contains("Default Inner"),
            "Should not have default inner"
        );
        // Should NOT have the base content (it was overridden by base_docs)
        assert!(
            !result.contains("Base Content"),
            "Should not have base content"
        );
    }

    #[test]
    fn test_deeply_nested_block_inheritance() {
        // Test 4-level nesting to ensure recursion works at arbitrary depths:
        // level1 > level2 > level3 > child
        // Each level adds a nested block inside the previous level's block
        let mut loader = TestTemplateLoader::new();

        // Level 1: outermost block
        loader.add("level1.html", "{% block outer %}L1{% endblock %}");

        // Level 2: wraps a middle block inside outer
        loader.add(
            "level2.html",
            "{% extends \"level1.html\" %}{% block outer %}[{% block middle %}L2{% endblock %}]{% endblock %}",
        );

        // Level 3: wraps an inner block inside middle
        loader.add(
            "level3.html",
            "{% extends \"level2.html\" %}{% block middle %}({% block inner %}L3{% endblock %}){% endblock %}",
        );

        // Child: only overrides the innermost block
        let child_source = "{% extends \"level3.html\" %}{% block inner %}DEEP{% endblock %}";
        let child_template = Template::new(child_source).unwrap();

        let context = Context::new();
        let result = child_template
            .render_with_loader(&context, &loader)
            .unwrap();

        // Should have the full nested structure with child's content at the deepest level
        assert!(
            result.contains("[(DEEP)]"),
            "Expected '[(DEEP)]' but got: {}",
            result
        );
        // Should NOT have any of the default content from intermediate levels
        assert!(!result.contains("L1"), "Should not have L1 default");
        assert!(!result.contains("L2"), "Should not have L2 default");
        assert!(!result.contains("L3"), "Should not have L3 default");
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

    // Tests for {% include %} tag with variables (Issue #35)
    #[test]
    fn test_include_basic() {
        let mut loader = TestTemplateLoader::new();

        // Partial template to include
        loader.add("_header.html", "<header>{{ title }}</header>");

        // Main template
        let main_source = "{% include \"_header.html\" %}<main>Content</main>";
        let main_template = Template::new(main_source).unwrap();

        let mut context = Context::new();
        context.set("title".to_string(), Value::String("Welcome".to_string()));

        let result = main_template.render_with_loader(&context, &loader).unwrap();

        assert!(result.contains("<header>Welcome</header>"));
        assert!(result.contains("<main>Content</main>"));
    }

    #[test]
    fn test_include_with_vars() {
        let mut loader = TestTemplateLoader::new();

        // Post card partial that uses the `post` variable
        loader.add(
            "_post_card.html",
            "<article><h3>{{ post.title }}</h3><p>{{ post.excerpt }}</p></article>",
        );

        // Main template that passes variables with the `with` keyword
        let main_source =
            "{% for item in posts %}{% include \"_post_card.html\" with post=item %}{% endfor %}";
        let main_template = Template::new(main_source).unwrap();

        // Create posts data
        let mut post1 = std::collections::HashMap::new();
        post1.insert("title".to_string(), Value::String("First Post".to_string()));
        post1.insert(
            "excerpt".to_string(),
            Value::String("First excerpt".to_string()),
        );

        let mut post2 = std::collections::HashMap::new();
        post2.insert(
            "title".to_string(),
            Value::String("Second Post".to_string()),
        );
        post2.insert(
            "excerpt".to_string(),
            Value::String("Second excerpt".to_string()),
        );

        let mut context = Context::new();
        context.set(
            "posts".to_string(),
            Value::List(vec![Value::Object(post1), Value::Object(post2)]),
        );

        let result = main_template.render_with_loader(&context, &loader).unwrap();

        // Verify the included template rendered correctly with passed variables
        assert!(result.contains("<h3>First Post</h3>"));
        assert!(result.contains("<p>First excerpt</p>"));
        assert!(result.contains("<h3>Second Post</h3>"));
        assert!(result.contains("<p>Second excerpt</p>"));
    }

    #[test]
    fn test_include_with_multiple_vars() {
        let mut loader = TestTemplateLoader::new();

        // Partial that uses multiple passed variables
        loader.add(
            "_item.html",
            "<div class=\"{{ class }}\"><span>{{ label }}</span>: {{ value }}</div>",
        );

        // Main template passing multiple variables
        let main_source =
            "{% include \"_item.html\" with label=item_label value=item_value class=item_class %}";
        let main_template = Template::new(main_source).unwrap();

        let mut context = Context::new();
        context.set("item_label".to_string(), Value::String("Name".to_string()));
        context.set(
            "item_value".to_string(),
            Value::String("John Doe".to_string()),
        );
        context.set(
            "item_class".to_string(),
            Value::String("highlighted".to_string()),
        );

        let result = main_template.render_with_loader(&context, &loader).unwrap();

        assert!(result.contains("<div class=\"highlighted\">"));
        assert!(result.contains("<span>Name</span>"));
        assert!(result.contains("John Doe"));
    }

    #[test]
    fn test_include_with_only() {
        let mut loader = TestTemplateLoader::new();

        // Partial that tries to use both passed and parent context variables
        loader.add("_limited.html", "{{ passed_var }} - {{ parent_var }}");

        // Main template with "only" keyword
        let main_source = "{% include \"_limited.html\" with passed_var=value only %}";
        let main_template = Template::new(main_source).unwrap();

        let mut context = Context::new();
        context.set("value".to_string(), Value::String("Passed".to_string()));
        context.set(
            "parent_var".to_string(),
            Value::String("Should Not Appear".to_string()),
        );

        let result = main_template.render_with_loader(&context, &loader).unwrap();

        // The passed variable should render, but parent_var should be empty
        // because "only" restricts the context
        assert!(result.contains("Passed"));
        assert!(!result.contains("Should Not Appear"));
    }

    #[test]
    fn test_include_inherits_parent_context() {
        let mut loader = TestTemplateLoader::new();

        // Partial that uses both parent context and passed variable
        loader.add(
            "_greeting.html",
            "Hello {{ name }}, welcome to {{ site_name }}!",
        );

        // Main template without "only" - should inherit parent context
        let main_source = "{% include \"_greeting.html\" with name=user_name %}";
        let main_template = Template::new(main_source).unwrap();

        let mut context = Context::new();
        context.set("user_name".to_string(), Value::String("Alice".to_string()));
        context.set("site_name".to_string(), Value::String("MyApp".to_string()));

        let result = main_template.render_with_loader(&context, &loader).unwrap();

        // Both the passed variable and the parent context variable should render
        assert!(result.contains("Hello Alice"));
        assert!(result.contains("welcome to MyApp"));
    }

    #[test]
    fn test_include_in_nested_for_loop() {
        let mut loader = TestTemplateLoader::new();

        // Blog post partial - this is the exact case from Issue #35
        loader.add(
            "blog/_post_card.html",
            "<article><h3>{{ post.title }}</h3><p>{{ post.excerpt }}</p></article>",
        );

        // Main template from the issue
        let main_source = r#"{% for post in posts %}
    {% include "blog/_post_card.html" with post=post %}
{% endfor %}"#;
        let main_template = Template::new(main_source).unwrap();

        // Create test posts
        let mut post1 = std::collections::HashMap::new();
        post1.insert(
            "title".to_string(),
            Value::String("My Post Title".to_string()),
        );
        post1.insert(
            "excerpt".to_string(),
            Value::String("Post excerpt here...".to_string()),
        );

        let mut context = Context::new();
        context.set("posts".to_string(), Value::List(vec![Value::Object(post1)]));

        let result = main_template.render_with_loader(&context, &loader).unwrap();

        // Verify the exact expected output from Issue #35
        assert!(result.contains("<article>"));
        assert!(result.contains("<h3>My Post Title</h3>"));
        assert!(result.contains("<p>Post excerpt here...</p>"));
        assert!(result.contains("</article>"));
    }
}
