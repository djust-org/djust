//! Django Rust Live - Reactive server-side rendering for Django
//!
//! This is the main crate that ties together templates, virtual DOM, and
//! provides Python bindings for reactive server-side rendering.

use django_rust_core::{Context, Value};
use django_rust_templates::Template;
use django_rust_vdom::{diff, parse_html, VNode};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict};
use serde_json;
use std::collections::HashMap;

/// A LiveView component that manages state and rendering
#[pyclass]
pub struct LiveView {
    template_source: String,
    state: HashMap<String, Value>,
    last_vdom: Option<VNode>,
}

#[pymethods]
impl LiveView {
    #[new]
    fn new(template_source: String) -> Self {
        Self {
            template_source,
            state: HashMap::new(),
            last_vdom: None,
        }
    }

    /// Set a state variable
    fn set_state(&mut self, key: String, value: Value) {
        self.state.insert(key, value);
    }

    /// Update state with a dictionary
    fn update_state(&mut self, updates: HashMap<String, Value>) {
        self.state.extend(updates);
    }

    /// Get current state
    fn get_state(&self, py: Python) -> PyResult<PyObject> {
        let dict = PyDict::new_bound(py);
        for (k, v) in &self.state {
            dict.set_item(k, v.to_object(py))?;
        }
        Ok(dict.into())
    }

    /// Render the template and return HTML
    fn render(&mut self) -> PyResult<String> {
        let template = Template::new(&self.template_source)?;
        let context = Context::from_dict(self.state.clone());
        let html = template.render(&context)?;
        Ok(html)
    }

    /// Render and compute diff from last render
    /// Returns a tuple of (html, patches_json)
    fn render_with_diff(&mut self) -> PyResult<(String, Option<String>)> {
        let template = Template::new(&self.template_source)?;
        let context = Context::from_dict(self.state.clone());
        let html = template.render(&context)?;

        // Parse new HTML to VDOM
        let new_vdom = parse_html(&html).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string())
        })?;

        // Compute diff if we have a previous render
        let patches = if let Some(old_vdom) = &self.last_vdom {
            let patches = diff(old_vdom, &new_vdom);
            if !patches.is_empty() {
                Some(serde_json::to_string(&patches).map_err(|e| {
                    PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string())
                })?)
            } else {
                None
            }
        } else {
            None
        };

        self.last_vdom = Some(new_vdom);

        Ok((html, patches))
    }

    /// Render and return patches as MessagePack bytes
    fn render_binary_diff(&mut self, py: Python) -> PyResult<(String, Option<PyObject>)> {
        let template = Template::new(&self.template_source)?;
        let context = Context::from_dict(self.state.clone());
        let html = template.render(&context)?;

        let new_vdom = parse_html(&html).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string())
        })?;

        let patches_bytes = if let Some(old_vdom) = &self.last_vdom {
            let patches = diff(old_vdom, &new_vdom);
            if !patches.is_empty() {
                let bytes = rmp_serde::to_vec(&patches).map_err(|e| {
                    PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string())
                })?;
                Some(PyBytes::new_bound(py, &bytes).into())
            } else {
                None
            }
        } else {
            None
        };

        self.last_vdom = Some(new_vdom);

        Ok((html, patches_bytes))
    }

    /// Reset the view state
    fn reset(&mut self) {
        self.last_vdom = None;
    }
}

/// Fast template rendering
#[pyfunction]
fn render_template(template_source: String, context: HashMap<String, Value>) -> PyResult<String> {
    let template = Template::new(&template_source)?;
    let ctx = Context::from_dict(context);
    Ok(template.render(&ctx)?)
}

/// Compute diff between two HTML strings
#[pyfunction]
fn diff_html(old_html: String, new_html: String) -> PyResult<String> {
    let old = parse_html(&old_html).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string())
    })?;
    let new = parse_html(&new_html).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string())
    })?;

    let patches = diff(&old, &new);
    serde_json::to_string(&patches).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string())
    })
}

/// Python module
#[pymodule]
fn _rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<LiveView>()?;
    m.add_function(wrap_pyfunction!(render_template, m)?)?;
    m.add_function(wrap_pyfunction!(diff_html, m)?)?;
    Ok(())
}
