//! Fast Django-compatible template engine
//!
//! This crate provides a high-performance template engine that is compatible
//! with Django template syntax, including variables, filters, tags, and
//! template inheritance.

use django_rust_core::{Context, DjangoRustError, Result, Value};
use once_cell::sync::Lazy;
use pyo3::prelude::*;
use regex::Regex;
use std::collections::HashMap;

pub mod filters;
pub mod lexer;
pub mod parser;
pub mod renderer;
pub mod tags;

use lexer::Token;
use parser::Node;
use renderer::render_nodes;

static VAR_REGEX: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"\{\{([^}]+)\}\}").unwrap()
});

static TAG_REGEX: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"\{%([^%]+)%\}").unwrap()
});

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
        render_nodes(&self.nodes, context)
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
fn django_rust_templates(_py: Python, m: &PyModule) -> PyResult<()> {
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
}
