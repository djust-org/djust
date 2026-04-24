//! djust - Reactive server-side rendering for Django
//!
//! This is the main crate that ties together templates, virtual DOM, and
//! provides Python bindings for reactive server-side rendering.

// PyResult type annotations are required by PyO3 API
#![allow(clippy::useless_conversion)]
// Parameter only used in recursion for Python value conversion
#![allow(clippy::only_used_in_recursion)]
// TODO: Migrate to IntoPyObject when pyo3 stabilizes the new API
// See: https://pyo3.rs/v0.23.0/migration
// TEMP REMOVED: #![allow(deprecated)]

// Actor system module
pub mod actors;

// Fast model serialization for N+1 query prevention
pub mod model_serializer;

use actors::{ActorSupervisor, SessionActorHandle};
use dashmap::DashMap;
use djust_core::{Context, Value};
use djust_templates::inheritance::FilesystemTemplateLoader;
use djust_templates::Template;
use djust_vdom::{
    cache_ignore_subtree_html, diff, parse_html, parse_html_continue, reset_id_counter,
    splice_ignore_subtrees, sync_ids, try_text_only_vdom_update_inplace, VNode,
};
use once_cell::sync::Lazy;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList, PyTuple};
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

/// Global template cache - parse once, reuse for all sessions
/// Using Arc<Template> for cheap cloning across threads
static TEMPLATE_CACHE: Lazy<DashMap<String, Arc<Template>>> = Lazy::new(DashMap::new);

/// Global supervisor for managing actor lifecycle
/// Created once with 1-hour TTL
static SUPERVISOR: Lazy<Arc<ActorSupervisor>> =
    Lazy::new(|| Arc::new(ActorSupervisor::new(Duration::from_secs(3600))));

/// Flag to track if supervisor background tasks have been started
static SUPERVISOR_STARTED: Lazy<std::sync::atomic::AtomicBool> =
    Lazy::new(|| std::sync::atomic::AtomicBool::new(false));

/// Ensure supervisor background tasks are started (idempotent)
fn ensure_supervisor_started() {
    use tracing::info;

    if !SUPERVISOR_STARTED.swap(true, std::sync::atomic::Ordering::SeqCst) {
        // First time - start background tasks
        let ttl_secs = SUPERVISOR.stats().ttl_secs;
        info!(
            ttl_secs = ttl_secs,
            cleanup_interval_secs = 60,
            health_check_interval_secs = 30,
            "Starting ActorSupervisor background tasks"
        );
        SUPERVISOR.clone().start();
    }
}

/// Serializable representation of RustLiveViewBackend for Redis storage
#[derive(Serialize, Deserialize)]
struct SerializableViewState {
    template_source: String,
    state: HashMap<String, Value>,
    last_vdom: Option<VNode>,
    version: u64,
    timestamp: f64, // Unix timestamp for session age tracking
}

/// Per-phase timing from render_with_diff()
#[derive(Debug, Clone)]
struct RenderTiming {
    render_ms: f64,
    parse_ms: f64,
    diff_ms: f64,
    serialize_ms: f64,
    total_ms: f64,
    html_len: usize,
}

/// A LiveView component that manages state and rendering (Rust backend)
#[pyclass(name = "RustLiveView")]
pub struct RustLiveViewBackend {
    template_source: String,
    state: HashMap<String, Value>,
    last_vdom: Option<VNode>,
    /// Cached HTML from the last render, used for text-only fast path detection.
    /// Not serialized — transient cache that's rebuilt on next render.
    last_html: Option<String>,
    /// Version number incremented on each render, used for VDOM synchronization
    version: u64,
    /// Unix timestamp when this view was last serialized (for session age tracking)
    timestamp: f64,
    /// Template directories for {% include %} tag support
    template_dirs: Vec<PathBuf>,
    /// Keys whose values should skip auto-escaping (SafeString from Python)
    safe_keys: HashSet<String>,
    /// Per-phase timing from the last render_with_diff() call
    last_render_timing: Option<RenderTiming>,
    /// Per-node HTML cache for partial template rendering
    node_html_cache: Vec<String>,
    /// Context keys that changed since the last render (None = full render)
    changed_keys: Option<HashSet<String>>,
    /// Maps template node index → (VDOM path, djust_id) for text-only fragments.
    /// Built after first render by matching fragment text to VDOM text nodes.
    fragment_text_map: Option<HashMap<usize, (Vec<usize>, String)>>,
    /// Flat list of every VDOM text node paired with its byte range in the
    /// pre-hydration HTML. Sorted by `html_start`. Used by the text-region
    /// fast path to O(log N) locate which text node owns a given byte
    /// offset, skipping the per-event linear scan of HTML + VDOM walk.
    ///
    /// Built after each full html5ever parse; cleared when the VDOM
    /// structure is invalidated (update_template, clear_state, full
    /// re-render where `last_vdom` is replaced). Subsequent fast-path
    /// renders don't modify the index because they only change text
    /// content, not text-node positions or count.
    text_node_index: Option<Vec<TextNodeEntry>>,
    /// Sidecar map of raw Python objects keyed by top-level context
    /// name. Populated by Python's `set_raw_py_values` so the Rust
    /// template engine can fall back to `getattr` for attributes
    /// that are not JSON-serializable (e.g. Django model instances).
    /// Not persisted across MessagePack serialize/deserialize —
    /// Python re-populates it on each sync cycle.
    raw_py_values: Option<HashMap<String, PyObject>>,
}

#[derive(Clone, Debug)]
struct TextNodeEntry {
    /// Byte offset in the pre-hydration HTML where this text node begins.
    html_start: usize,
    /// Byte offset where the HTML text run ends (exclusive).
    html_end: usize,
    /// VDOM path to this text node.
    path: Vec<usize>,
    /// Decoded text content as stored in the VDOM (may differ from the
    /// raw HTML bytes if the source had entities like `&amp;`).
    text: String,
    /// dj-id of the enclosing element (empty for un-ided text nodes).
    djust_id: String,
}

#[pymethods]
impl RustLiveViewBackend {
    #[new]
    #[pyo3(signature = (template_source, template_dirs=None))]
    fn new(template_source: String, template_dirs: Option<Vec<String>>) -> Self {
        Self {
            template_source,
            state: HashMap::new(),
            last_vdom: None,
            last_html: None,
            version: 0,
            timestamp: 0.0, // Will be set on first serialization
            template_dirs: template_dirs
                .unwrap_or_default()
                .into_iter()
                .map(PathBuf::from)
                .collect(),
            safe_keys: HashSet::new(),
            last_render_timing: None,
            node_html_cache: Vec::new(),
            changed_keys: None,
            fragment_text_map: None,
            text_node_index: None,
            raw_py_values: None,
        }
    }

    /// Tell Rust which context keys changed since the last render.
    ///
    /// When set, `render_with_diff` / `render_binary_diff` will only re-render
    /// template nodes whose dependencies overlap these keys.
    /// Set or merge changed context keys for the next render cycle.
    /// Called from Python after _sync_state_to_rust() detects which keys changed.
    /// Merges with any previously set keys (supports multiple sync calls before render).
    fn set_changed_keys(&mut self, keys: Vec<String>) {
        match &mut self.changed_keys {
            Some(existing) => {
                existing.extend(keys);
            }
            None => {
                self.changed_keys = Some(keys.into_iter().collect());
            }
        }
    }

    /// Set template directories for {% include %} tag support
    fn set_template_dirs(&mut self, dirs: Vec<String>) {
        self.template_dirs = dirs.into_iter().map(PathBuf::from).collect();
    }

    /// Set a state variable
    fn set_state(&mut self, key: String, value: Value) {
        self.state.insert(key, value);
    }

    /// Update state with a dictionary
    fn update_state(&mut self, updates: HashMap<String, Value>) {
        self.state.extend(updates);
    }

    /// Mark context keys as safe (skip auto-escaping).
    /// Called from Python when SafeString values are detected.
    fn mark_safe_keys(&mut self, keys: Vec<String>) {
        self.safe_keys.extend(keys);
    }

    /// Attach a map of raw Python objects for `getattr`-fallback
    /// lookups. Called from Python's `_sync_state_to_rust()` for
    /// context values that are not JSON-serializable (e.g. Django
    /// model instances). The template engine falls back to
    /// `getattr(obj, "field")` when a nested key like
    /// `{{ user.username }}` cannot be resolved via the normal
    /// value-stack path. An empty dict clears the sidecar.
    fn set_raw_py_values(&mut self, values: HashMap<String, PyObject>) {
        if values.is_empty() {
            self.raw_py_values = None;
        } else {
            self.raw_py_values = Some(values);
        }
    }

    /// Update the template source while preserving VDOM state
    /// This allows dynamic templates to change without losing diffing capability
    fn update_template(&mut self, new_template_source: String) {
        self.template_source = new_template_source;
        self.node_html_cache = Vec::new(); // Invalidate partial render cache
        self.last_html = None; // Invalidate text fast path cache
        self.fragment_text_map = None; // Invalidate fragment→VDOM map
        self.text_node_index = None; // Invalidate text-region fast-path index
    }

    /// Clear the partial-render fragment cache, forcing the next render to
    /// do a full collecting render. Keeps `last_vdom` intact so the diff
    /// baseline is preserved. Used by the partial-render correctness harness
    /// in tests to produce a control output for byte-equality comparison.
    fn clear_fragment_cache(&mut self) {
        self.node_html_cache = Vec::new();
        self.last_html = None;
        self.fragment_text_map = None;
        self.text_node_index = None;
    }

    /// Get current state
    fn get_state(&self, py: Python) -> PyResult<PyObject> {
        let dict = PyDict::new(py);
        for (k, v) in &self.state {
            dict.set_item(k, v.into_pyobject(py)?)?;
        }
        Ok(dict.into())
    }

    /// Render the template and return HTML
    fn render(&mut self) -> PyResult<String> {
        // Invalidate partial render cache — render() bypasses the diff pipeline
        // so the cache would be stale for the next render_with_diff() call.
        self.node_html_cache = Vec::new();
        self.last_html = None; // Invalidate text fast path cache
        self.text_node_index = None; // Invalidate text-region fast-path index

        // Get template from cache or parse and cache it
        let template_arc = if let Some(cached) = TEMPLATE_CACHE.get(&self.template_source) {
            cached.clone()
        } else {
            let template = Template::new(&self.template_source)?;
            let arc = Arc::new(template);
            TEMPLATE_CACHE.insert(self.template_source.clone(), arc.clone());
            arc
        };

        let mut context = Context::from_dict(self.state.clone());
        for key in &self.safe_keys {
            context.mark_safe(key.clone());
        }
        // Attach PyObject sidecar so `{{ model.attr }}` falls back
        // to `getattr` when `attr` isn't in the JSON-serialized state.
        if let Some(raw) = &self.raw_py_values {
            let cloned: HashMap<String, PyObject> = Python::with_gil(|py| {
                raw.iter()
                    .map(|(k, v)| (k.clone(), v.clone_ref(py)))
                    .collect()
            });
            context.set_raw_py_objects(cloned);
        }

        // Use template loader for {% include %} support
        let loader = FilesystemTemplateLoader::new(self.template_dirs.clone());
        let html = template_arc.render_with_loader(&context, &loader)?;
        Ok(html)
    }

    /// Render and compute diff from last render
    /// Returns a tuple of (html, patches_json, version)
    fn render_with_diff(&mut self) -> PyResult<(String, Option<String>, u64)> {
        use std::time::Instant;

        let t_start = Instant::now();

        // Get template from cache or parse and cache it
        let template_arc = if let Some(cached) = TEMPLATE_CACHE.get(&self.template_source) {
            cached.clone()
        } else {
            let template = Template::new(&self.template_source)?;
            let arc = Arc::new(template);
            TEMPLATE_CACHE.insert(self.template_source.clone(), arc.clone());
            arc
        };

        let mut context = Context::from_dict(self.state.clone());
        for key in &self.safe_keys {
            context.mark_safe(key.clone());
        }
        // Attach PyObject sidecar so `{{ model.attr }}` falls back
        // to `getattr` when `attr` isn't in the JSON-serialized state.
        if let Some(raw) = &self.raw_py_values {
            let cloned: HashMap<String, PyObject> = Python::with_gil(|py| {
                raw.iter()
                    .map(|(k, v)| (k.clone(), v.clone_ref(py)))
                    .collect()
            });
            context.set_raw_py_objects(cloned);
        }

        // Phase 1: Template render (partial if cache available)
        let t_render_start = Instant::now();
        let loader = FilesystemTemplateLoader::new(self.template_dirs.clone());

        // Resolve {% extends %} inheritance once (cached on Template via OnceLock)
        if template_arc.uses_extends() {
            template_arc.resolve_inheritance(&loader)?;
        }

        // Track old fragments for text-fast-path comparison
        let old_node_cache = self.node_html_cache.clone();
        let (html, changed_indices) =
            if !self.node_html_cache.is_empty() && self.changed_keys.is_some() {
                // Partial render: only re-render nodes whose deps changed
                let changed = self.changed_keys.take().unwrap_or_default();
                let (html, fragments, changed_indices) = template_arc.render_with_loader_partial(
                    &context,
                    &loader,
                    &changed,
                    &self.node_html_cache,
                )?;
                self.node_html_cache = fragments;
                (html, changed_indices)
            } else {
                // Full render: first render or no change info
                self.changed_keys = None;
                let (html, fragments) =
                    template_arc.render_with_loader_collecting(&context, &loader)?;
                self.node_html_cache = fragments;
                (html, vec![])
            };
        let render_ms = t_render_start.elapsed().as_secs_f64() * 1000.0;

        // Phase 2: HTML parse to VDOM
        // Text-fast-path: if ALL changed fragments are plain text (no HTML tags),
        // skip html5ever + diff entirely and produce SetText patches directly.
        let t_parse_start = Instant::now();
        let text_fast_path = if !changed_indices.is_empty() && self.last_vdom.is_some() {
            // Check if all changed fragments are plain text
            let mut all_text = true;
            let mut text_changes: Vec<(usize, String, String)> = Vec::new();
            for &idx in &changed_indices {
                let old_frag = old_node_cache.get(idx).map(|s| s.as_str()).unwrap_or("");
                let new_frag = self
                    .node_html_cache
                    .get(idx)
                    .map(|s| s.as_str())
                    .unwrap_or("");
                if old_frag != new_frag {
                    // Check if both fragments are plain text (no HTML tags)
                    if old_frag.contains('<') || new_frag.contains('<') {
                        all_text = false;
                        break;
                    }
                    text_changes.push((idx, old_frag.to_string(), new_frag.to_string()));
                }
            }
            if all_text && !text_changes.is_empty() {
                // Use the fragment text map to produce patches directly.
                // First verify all fragments have mappings, then apply.
                if let Some(ref frag_map) = self.fragment_text_map {
                    let all_mapped = text_changes
                        .iter()
                        .all(|(idx, _, _)| frag_map.contains_key(idx));
                    if all_mapped {
                        let mut vdom = self.last_vdom.take().unwrap();
                        let mut patches = Vec::new();
                        for (idx, _old_text, new_text) in &text_changes {
                            let (path, djust_id) = frag_map.get(idx).unwrap();
                            if let Some(node) = get_vdom_node_mut(&mut vdom, path) {
                                node.text = Some(new_text.clone());
                                node.cached_html = None;
                            }
                            let d = if djust_id.is_empty() {
                                None
                            } else {
                                Some(djust_id.clone())
                            };
                            patches.push(djust_vdom::Patch::SetText {
                                path: path.clone(),
                                d,
                                text: new_text.clone(),
                            });
                        }
                        Some((vdom, patches))
                    } else {
                        None
                    }
                } else {
                    None
                }
            } else {
                None
            }
        } else {
            None
        };

        // Text-region fast path: if text-fast-path can't fire (because
        // changed fragments contain tags), maybe the diff between the
        // FULL old and new HTML is still a single text span. Common for
        // a value change inside a `{% for %}` loop body — the whole
        // loop re-renders, but the actual byte diff is tiny.
        // Borrow-split trick: take the index out so we can pass it mutably
        // while also borrowing self.last_vdom / self.last_html. Replaced
        // at the end of this block regardless of hit/miss.
        let text_region_fast_path: Option<(VNode, Vec<djust_vdom::Patch>)> =
            if text_fast_path.is_none() {
                if let (Some(old_vdom), Some(old_html), Some(mut index)) = (
                    self.last_vdom.as_ref(),
                    self.last_html.as_ref(),
                    self.text_node_index.take(),
                ) {
                    let result = try_text_region_fast_path(old_html, &html, old_vdom, &mut index);
                    self.text_node_index = Some(index);
                    result
                } else {
                    None
                }
            } else {
                None
            };

        let mut took_full_parse = false;
        let (mut new_vdom, patches, parse_ms, diff_ms) =
            if let Some((vdom, text_patches)) = text_fast_path {
                let parse_ms = t_parse_start.elapsed().as_secs_f64() * 1000.0;
                let patches_json = if text_patches.is_empty() {
                    Some("[]".to_string())
                } else {
                    Some(serde_json::to_string(&text_patches).map_err(|e| {
                        PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string())
                    })?)
                };
                // The original text-fast-path mutates VDOM text nodes but
                // doesn't know about our byte-position index — invalidate
                // it so the NEXT render rebuilds rather than silently
                // relying on the content-equality safety net in
                // try_text_region_fast_path to catch stale offsets.
                self.text_node_index = None;
                (vdom, patches_json, parse_ms, 0.0)
            } else if let Some((vdom, text_patches)) = text_region_fast_path {
                let parse_ms = t_parse_start.elapsed().as_secs_f64() * 1000.0;
                let patches_json = if text_patches.is_empty() {
                    Some("[]".to_string())
                } else {
                    Some(serde_json::to_string(&text_patches).map_err(|e| {
                        PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string())
                    })?)
                };
                (vdom, patches_json, parse_ms, 0.0)
            } else {
                took_full_parse = true;
                // Full html5ever parse
                let new_vdom = if self.last_vdom.is_some() {
                    parse_html_continue(&html).map_err(|e| {
                        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string())
                    })?
                } else {
                    parse_html(&html).map_err(|e| {
                        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string())
                    })?
                };
                let parse_ms = t_parse_start.elapsed().as_secs_f64() * 1000.0;

                // Splice ignore subtrees
                let mut new_vdom = new_vdom;
                if let Some(old_vdom) = &self.last_vdom {
                    splice_ignore_subtrees(old_vdom, &mut new_vdom);
                }

                // VDOM diff
                let t_diff_start = Instant::now();
                let patches = if let Some(old_vdom) = &self.last_vdom {
                    let patches = diff(old_vdom, &new_vdom);
                    sync_ids(old_vdom, &mut new_vdom);
                    if !patches.is_empty() {
                        Some(serde_json::to_string(&patches).map_err(|e| {
                            PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string())
                        })?)
                    } else {
                        Some("[]".to_string())
                    }
                } else {
                    None
                };
                let diff_ms = t_diff_start.elapsed().as_secs_f64() * 1000.0;
                (new_vdom, patches, parse_ms, diff_ms)
            };

        // Phase 4: HTML serialization
        let t_serial_start = Instant::now();
        let hydrated_html = new_vdom.to_html();
        let serialize_ms = t_serial_start.elapsed().as_secs_f64() * 1000.0;

        let total_ms = t_start.elapsed().as_secs_f64() * 1000.0;

        // Store timing for Python to read
        self.last_render_timing = Some(RenderTiming {
            render_ms,
            parse_ms,
            diff_ms,
            serialize_ms,
            total_ms,
            html_len: html.len(),
        });

        // Cache HTML for dj-update="ignore" subtrees so subsequent
        // to_html() calls skip serialization for those sections.
        cache_ignore_subtree_html(&mut new_vdom);

        // Cache the rendered HTML for text-only fast path on next render
        self.last_html = Some(html);

        self.last_vdom = Some(new_vdom);
        self.version += 1;

        // Build fragment→VDOM text node map for text-fast-path on subsequent renders.
        // Match each plain-text fragment to a VDOM text node by content.
        if self.fragment_text_map.is_none() && !self.node_html_cache.is_empty() {
            if let Some(ref vdom) = self.last_vdom {
                self.fragment_text_map = Some(build_fragment_text_map(&self.node_html_cache, vdom));
            }
        }

        // Rebuild the text-region fast-path index whenever we just went
        // through the full html5ever parse (structure may have changed)
        // or no index exists yet. Fast-path renders only change text
        // CONTENT — positions and count stay stable — so the old index
        // remains valid and we skip the rebuild.
        if took_full_parse || self.text_node_index.is_none() {
            if let (Some(ref html_str), Some(ref vdom)) = (&self.last_html, &self.last_vdom) {
                let index = build_text_node_index(html_str, vdom);
                self.text_node_index = if index.is_empty() { None } else { Some(index) };
            }
        }

        Ok((hydrated_html, patches, self.version))
    }

    /// Render and return patches as MessagePack bytes
    fn render_binary_diff(&mut self, py: Python) -> PyResult<(String, Option<PyObject>, u64)> {
        use std::time::Instant;

        let t_start = Instant::now();

        // Get template from cache or parse and cache it
        let template_arc = if let Some(cached) = TEMPLATE_CACHE.get(&self.template_source) {
            cached.clone()
        } else {
            let template = Template::new(&self.template_source)?;
            let arc = Arc::new(template);
            TEMPLATE_CACHE.insert(self.template_source.clone(), arc.clone());
            arc
        };

        let mut context = Context::from_dict(self.state.clone());
        for key in &self.safe_keys {
            context.mark_safe(key.clone());
        }
        // Attach PyObject sidecar so `{{ model.attr }}` falls back
        // to `getattr` when `attr` isn't in the JSON-serialized state.
        if let Some(raw) = &self.raw_py_values {
            let cloned: HashMap<String, PyObject> = Python::with_gil(|py| {
                raw.iter()
                    .map(|(k, v)| (k.clone(), v.clone_ref(py)))
                    .collect()
            });
            context.set_raw_py_objects(cloned);
        }

        // Phase 1: Template render (partial if cache available)
        let t_render_start = Instant::now();
        let loader = FilesystemTemplateLoader::new(self.template_dirs.clone());

        let html = if !self.node_html_cache.is_empty()
            && self.changed_keys.is_some()
            && !template_arc.uses_extends()
        {
            let changed = self.changed_keys.take().unwrap_or_default();
            let (html, fragments, _changed_indices) = template_arc.render_with_loader_partial(
                &context,
                &loader,
                &changed,
                &self.node_html_cache,
            )?;
            self.node_html_cache = fragments;
            html
        } else {
            self.changed_keys = None;
            let (html, fragments) =
                template_arc.render_with_loader_collecting(&context, &loader)?;
            self.node_html_cache = fragments;
            html
        };
        let render_ms = t_render_start.elapsed().as_secs_f64() * 1000.0;

        // Phase 2: HTML parse to VDOM
        // Try text-only fast path: mutate old VDOM in-place if only text changed.
        // Falls back to html5ever if structural changes detected.
        let t_parse_start = Instant::now();
        let mut new_vdom = if let (Some(ref old_html), Some(_)) = (&self.last_html, &self.last_vdom)
        {
            let old_html = old_html.clone();
            let mut vdom = self.last_vdom.take().unwrap();
            if try_text_only_vdom_update_inplace(&mut vdom, &old_html, &html) {
                vdom
            } else {
                // Structural change — fall back to full html5ever parse.
                // Store the old vdom back temporarily for the diff phase.
                self.last_vdom = Some(vdom);
                parse_html_continue(&html)
                    .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?
            }
        } else if self.last_vdom.is_some() {
            parse_html_continue(&html)
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?
        } else {
            parse_html(&html)
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?
        };
        let parse_ms = t_parse_start.elapsed().as_secs_f64() * 1000.0;

        // Splice old VDOM subtrees for dj-update="ignore" nodes
        if let Some(old_vdom) = &self.last_vdom {
            splice_ignore_subtrees(old_vdom, &mut new_vdom);
        }

        // Phase 3: VDOM diff
        let t_diff_start = Instant::now();
        let patches_bytes = if let Some(old_vdom) = &self.last_vdom {
            let patches = diff(old_vdom, &new_vdom);
            sync_ids(old_vdom, &mut new_vdom);
            if !patches.is_empty() {
                let bytes = rmp_serde::to_vec(&patches)
                    .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;
                Some(PyBytes::new(py, &bytes).into())
            } else {
                let empty: Vec<djust_vdom::Patch> = Vec::new();
                let bytes = rmp_serde::to_vec(&empty)
                    .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;
                Some(PyBytes::new(py, &bytes).into())
            }
        } else {
            None
        };
        let diff_ms = t_diff_start.elapsed().as_secs_f64() * 1000.0;

        // Phase 4: HTML serialization
        let t_serial_start = Instant::now();
        let hydrated_html = new_vdom.to_html();
        let serialize_ms = t_serial_start.elapsed().as_secs_f64() * 1000.0;

        let total_ms = t_start.elapsed().as_secs_f64() * 1000.0;

        self.last_render_timing = Some(RenderTiming {
            render_ms,
            parse_ms,
            diff_ms,
            serialize_ms,
            total_ms,
            html_len: html.len(),
        });

        cache_ignore_subtree_html(&mut new_vdom);

        // Cache the rendered HTML for text-only fast path on next render
        self.last_html = Some(html);

        self.last_vdom = Some(new_vdom);
        self.version += 1;

        Ok((hydrated_html, patches_bytes, self.version))
    }

    /// Reset the view state
    fn reset(&mut self) {
        self.last_vdom = None;
        self.last_html = None;
        self.version = 0;
        self.node_html_cache = Vec::new();
        self.changed_keys = None;
        self.fragment_text_map = None;
        self.text_node_index = None;
        // Reset ID counter so next render starts fresh
        reset_id_counter();
    }

    /// Serialize the RustLiveView state to MessagePack bytes
    ///
    /// This enables efficient state persistence to Redis or other storage backends.
    /// Uses MessagePack for compact binary serialization (~30-40% smaller than JSON).
    /// Includes current timestamp for session age tracking.
    ///
    /// Returns: Python bytes object containing the serialized state with timestamp
    fn serialize_msgpack(&self, py: Python) -> PyResult<PyObject> {
        // Get current timestamp
        let ts = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs_f64();

        // Convert to serializable struct
        let serializable = SerializableViewState {
            template_source: self.template_source.clone(),
            state: self.state.clone(),
            last_vdom: self.last_vdom.clone(),
            version: self.version,
            timestamp: ts,
        };

        // Serialize to MessagePack bytes
        let bytes = rmp_serde::to_vec(&serializable).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
                "MessagePack serialization error: {e}"
            ))
        })?;
        Ok(PyBytes::new(py, &bytes).into())
    }

    /// Deserialize a RustLiveView from MessagePack bytes
    ///
    /// Reconstructs a complete RustLiveView instance from bytes previously
    /// serialized with serialize_msgpack().
    ///
    /// Args:
    ///     bytes: Python bytes object containing MessagePack data
    ///
    /// Returns: RustLiveView instance with restored state
    #[staticmethod]
    fn deserialize_msgpack(bytes: &[u8]) -> PyResult<Self> {
        let serializable: SerializableViewState = rmp_serde::from_slice(bytes).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
                "MessagePack deserialization error: {e}"
            ))
        })?;

        // Convert back to RustLiveViewBackend
        // Note: template_dirs must be re-set after deserialization via set_template_dirs()
        Ok(Self {
            template_source: serializable.template_source,
            state: serializable.state,
            last_vdom: serializable.last_vdom,
            last_html: None, // Transient cache — rebuilt on next render
            version: serializable.version,
            timestamp: serializable.timestamp,
            template_dirs: Vec::new(),
            safe_keys: HashSet::new(),
            last_render_timing: None,
            node_html_cache: Vec::new(),
            changed_keys: None,
            fragment_text_map: None,
            text_node_index: None,
            raw_py_values: None,
        })
    }

    /// Get per-phase timing from the last render_with_diff() call.
    /// Returns a dict with render_ms, parse_ms, diff_ms, serialize_ms, total_ms, html_len.
    fn get_render_timing(&self) -> Option<HashMap<String, f64>> {
        self.last_render_timing.as_ref().map(|t| {
            let mut m = HashMap::new();
            m.insert("render_ms".to_string(), t.render_ms);
            m.insert("parse_ms".to_string(), t.parse_ms);
            m.insert("diff_ms".to_string(), t.diff_ms);
            m.insert("serialize_ms".to_string(), t.serialize_ms);
            m.insert("total_ms".to_string(), t.total_ms);
            m.insert("html_len".to_string(), t.html_len as f64);
            m
        })
    }

    /// Get the timestamp when this view was last serialized
    ///
    /// Returns: Unix timestamp (seconds since epoch)
    fn get_timestamp(&self) -> f64 {
        self.timestamp
    }
}

// Public Rust API (for use by other Rust crates like djust_actors)
impl RustLiveViewBackend {
    /// Create a new RustLiveViewBackend (Rust API)
    pub fn new_rust(template_source: String) -> Self {
        Self::new(template_source, None)
    }

    /// Create new LiveView with template directories (Rust API)
    pub fn new_rust_with_dirs(template_source: String, template_dirs: Vec<String>) -> Self {
        Self::new(template_source, Some(template_dirs))
    }

    /// Update state (Rust API)
    pub fn update_state_rust(&mut self, updates: HashMap<String, Value>) {
        self.update_state(updates)
    }

    /// Render the template (Rust API)
    pub fn render_rust(&mut self) -> Result<String, djust_core::DjangoRustError> {
        self.render()
            .map_err(|e| djust_core::DjangoRustError::TemplateError(e.to_string()))
    }

    /// Render with diff (Rust API)
    /// Returns (html, patches_json, version)
    pub fn render_with_diff_rust(
        &mut self,
    ) -> Result<(String, Option<Vec<djust_vdom::Patch>>, u64), djust_core::DjangoRustError> {
        let (html, patches_json, version) = self
            .render_with_diff()
            .map_err(|e| djust_core::DjangoRustError::TemplateError(e.to_string()))?;

        let patches = if let Some(json) = patches_json {
            Some(
                serde_json::from_str(&json)
                    .map_err(|e| djust_core::DjangoRustError::TemplateError(e.to_string()))?,
            )
        } else {
            None
        };

        Ok((html, patches, version))
    }

    /// Reset the view state (Rust API)
    pub fn reset_rust(&mut self) {
        self.reset()
    }
}

/// Render Markdown to sanitised HTML.
///
/// Wraps [`djust_templates::markdown::render_markdown`] for Python. Raw HTML
/// is always escaped (`Options::ENABLE_HTML` is never set); `javascript:`,
/// `vbscript:`, and `data:` URL schemes in links/images are neutralised to
/// `#`. Inputs larger than 10 MiB are returned as an escaped `<pre>` without
/// hitting the parser.
///
/// Args:
///     src: Markdown source string.
///     provisional: Split the trailing unfinished line off as escaped text
///         (streaming-safe rendering). Default `True`.
///     tables: Enable GFM tables. Default `True`.
///     strikethrough: Enable `~~strikethrough~~`. Default `True`.
///     task_lists: Enable `- [ ]` / `- [x]` checkboxes. Default `False`.
///
/// Returns:
///     Sanitised HTML as a Python string.
#[pyfunction]
#[pyo3(
    name = "render_markdown",
    signature = (src, *, provisional=true, tables=true, strikethrough=true, task_lists=false)
)]
fn render_markdown_py(
    py: Python<'_>,
    src: String,
    provisional: bool,
    tables: bool,
    strikethrough: bool,
    task_lists: bool,
) -> PyResult<String> {
    let opts = djust_templates::markdown::RenderOpts {
        provisional,
        tables,
        strikethrough,
        task_lists,
    };
    Ok(py.allow_threads(|| djust_templates::markdown::render_markdown(&src, opts)))
}

/// Fast template rendering
#[pyfunction]
fn render_template(template_source: String, context: HashMap<String, Value>) -> PyResult<String> {
    // Get template from cache or parse and cache it
    let template_arc = if let Some(cached) = TEMPLATE_CACHE.get(&template_source) {
        cached.clone()
    } else {
        let template = Template::new(&template_source)?;
        let arc = Arc::new(template);
        TEMPLATE_CACHE.insert(template_source.clone(), arc.clone());
        arc
    };

    let ctx = Context::from_dict(context);
    let result = template_arc.render(&ctx)?;
    // Strip VDOM placeholder comments in standalone rendering
    Ok(result.replace("<!--dj-if-->", ""))
}

/// Fast template rendering with template directories for {% include %} support
///
/// This function extends render_template to support {% include %} tags by
/// providing template directories for the Rust renderer to load included templates.
///
/// # Arguments
/// * `template_source` - The template source string to render
/// * `context` - Template context variables
/// * `template_dirs` - List of directories to search for included templates
///
/// # Returns
/// The rendered HTML string
#[pyfunction]
#[pyo3(signature = (template_source, context, template_dirs, safe_keys=None))]
fn render_template_with_dirs(
    template_source: String,
    context: HashMap<String, Value>,
    template_dirs: Vec<String>,
    safe_keys: Option<Vec<String>>,
) -> PyResult<String> {
    use djust_templates::inheritance::FilesystemTemplateLoader;

    // Get template from cache or parse and cache it
    let template_arc = if let Some(cached) = TEMPLATE_CACHE.get(&template_source) {
        cached.clone()
    } else {
        let template = Template::new(&template_source)?;
        let arc = Arc::new(template);
        TEMPLATE_CACHE.insert(template_source.clone(), arc.clone());
        arc
    };

    let mut ctx = Context::from_dict(context);

    // Mark keys as safe (skip auto-escaping), like Django's SafeData
    if let Some(keys) = safe_keys {
        for key in keys {
            ctx.mark_safe(key);
        }
    }

    // Create filesystem template loader with the provided directories
    let dirs: Vec<PathBuf> = template_dirs.iter().map(PathBuf::from).collect();
    let loader = FilesystemTemplateLoader::new(dirs);

    // Render with the loader to support {% include %} tags
    Ok(template_arc.render_with_loader(&ctx, &loader)?)
}

/// Compute diff between two HTML strings
#[pyfunction]
fn diff_html(old_html: String, new_html: String) -> PyResult<String> {
    let old = parse_html(&old_html)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;
    let new = parse_html(&new_html)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;

    let patches = diff(&old, &new);
    serde_json::to_string(&patches)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))
}

/// Fast JSON serialization for Python objects
/// Converts Python list/dict to JSON string using Rust's serde_json
///
/// Benefits:
/// - Releases Python GIL during serialization (better for concurrent workloads)
/// - More memory efficient for large datasets
/// - Similar performance to Python json.dumps for small datasets
#[pyfunction]
fn fast_json_dumps(py: Python, obj: &Bound<'_, PyAny>) -> PyResult<String> {
    // Convert Python object to serde_json::Value
    let value = python_to_json_value(py, obj)?;

    // Release GIL and serialize to JSON string
    py.allow_threads(|| {
        serde_json::to_string(&value).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
                "JSON serialization error: {e}"
            ))
        })
    })
}

/// Helper function to convert Python objects to serde_json::Value
fn python_to_json_value(py: Python, obj: &Bound<'_, PyAny>) -> PyResult<serde_json::Value> {
    use serde_json::Value as JsonValue;

    if obj.is_none() {
        Ok(JsonValue::Null)
    } else if let Ok(b) = obj.extract::<bool>() {
        Ok(JsonValue::Bool(b))
    } else if let Ok(i) = obj.extract::<i64>() {
        Ok(JsonValue::Number(i.into()))
    } else if let Ok(f) = obj.extract::<f64>() {
        Ok(serde_json::Number::from_f64(f)
            .map(JsonValue::Number)
            .unwrap_or(JsonValue::Null))
    } else if let Ok(s) = obj.extract::<String>() {
        Ok(JsonValue::String(s))
    } else if let Ok(list) = obj.downcast::<PyList>() {
        let mut vec = Vec::new();
        for item in list.iter() {
            vec.push(python_to_json_value(py, &item)?);
        }
        Ok(JsonValue::Array(vec))
    } else if let Ok(dict) = obj.downcast::<PyDict>() {
        let mut map = serde_json::Map::new();
        for (key, value) in dict.iter() {
            let key_str = key.extract::<String>()?;
            map.insert(key_str, python_to_json_value(py, &value)?);
        }
        Ok(JsonValue::Object(map))
    } else {
        // Try to convert to string as fallback
        let s = obj.str()?.extract::<String>()?;
        Ok(JsonValue::String(s))
    }
}

/// Resolve template inheritance
///
/// Given a template path and list of template directories, resolves
/// {% extends %} and {% block %} tags to produce a final merged template string.
///
/// # Arguments
/// * `template_path` - Path to the child template (e.g., "products.html")
/// * `template_dirs` - List of directories to search for templates
///
/// # Returns
/// The merged template string with all inheritance resolved
#[pyfunction]
fn resolve_template_inheritance(
    template_path: String,
    template_dirs: Vec<String>,
) -> PyResult<String> {
    use djust_templates::inheritance::resolve_template_inheritance as resolve;

    // Convert string paths to PathBuf
    let dirs: Vec<PathBuf> = template_dirs.iter().map(PathBuf::from).collect();

    // Resolve inheritance using AST-based implementation
    let resolved = resolve(&template_path, &dirs)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;

    Ok(resolved)
}

// ============================================================================
// Actor System Python Bindings
// ============================================================================

use pyo3_async_runtimes::tokio::future_into_py;

/// Python wrapper for SessionActorHandle
///
/// This class provides async methods that can be called from Python's asyncio.
#[pyclass(name = "SessionActorHandle")]
pub struct SessionActorHandlePy {
    handle: SessionActorHandle,
}

#[pymethods]
impl SessionActorHandlePy {
    /// Mount a view (Phase 6: Now returns view_id for routing)
    ///
    /// Creates a ViewActor, initializes its state, and renders the initial HTML.
    ///
    /// Args:
    ///     view_path (str): Python path to the LiveView class (e.g. "app.views.Counter")
    ///     params (dict): Initial state parameters
    ///     python_view (Optional[Any]): Python LiveView instance for event handler callbacks
    ///
    /// Returns:
    ///     dict: {"html": str, "session_id": str, "view_id": str}
    #[pyo3(signature = (view_path, params, python_view=None))]
    fn mount<'py>(
        &self,
        py: Python<'py>,
        view_path: String,
        params: &Bound<'py, PyDict>,
        python_view: Option<Py<PyAny>>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let handle = self.handle.clone();

        // Convert Python dict to Rust HashMap<String, Value>
        let params_rust = python_dict_to_hashmap(params)?;

        future_into_py(py, async move {
            let result = handle
                .mount(view_path, params_rust, python_view)
                .await
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;

            Python::with_gil(|py| -> PyResult<PyObject> {
                let dict = PyDict::new(py);
                dict.set_item("html", result.html)?;
                dict.set_item("session_id", result.session_id)?;
                dict.set_item("view_id", result.view_id)?; // Phase 6: Return view_id
                Ok(dict.unbind().into())
            })
        })
    }

    /// Handle an event (Phase 6: Now supports view_id routing)
    ///
    /// Routes the event to the appropriate ViewActor and returns the resulting
    /// VDOM patches or full HTML.
    ///
    /// Args:
    ///     event_name (str): Name of the event (e.g. "increment", "submit_form")
    ///     params (dict): Event parameters
    ///     view_id (Optional[str]): View ID for routing. If None, routes to first view (backward compat)
    ///
    /// Returns:
    ///     dict: {"patches": Optional[str], "html": Optional[str], "version": int}
    #[pyo3(signature = (event_name, params, view_id=None))]
    fn event<'py>(
        &self,
        py: Python<'py>,
        event_name: String,
        params: &Bound<'py, PyDict>,
        view_id: Option<String>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let handle = self.handle.clone();

        // Convert Python dict to Rust HashMap<String, Value>
        let params_rust = python_dict_to_hashmap(params)?;

        future_into_py(py, async move {
            let result = handle
                .event(event_name, params_rust, view_id)
                .await
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;

            Python::with_gil(|py| -> PyResult<PyObject> {
                let dict = PyDict::new(py);

                // Add patches if available
                if let Some(patches) = result.patches {
                    let patches_json = serde_json::to_string(&patches).map_err(|e| {
                        PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string())
                    })?;
                    dict.set_item("patches", patches_json)?;
                } else {
                    dict.set_item("patches", py.None())?;
                }

                // Add html if available
                if let Some(html) = result.html {
                    dict.set_item("html", html)?;
                } else {
                    dict.set_item("html", py.None())?;
                }

                dict.set_item("version", result.version)?;
                Ok(dict.unbind().into())
            })
        })
    }

    /// Unmount a specific view (Phase 6)
    ///
    /// Shuts down a specific ViewActor and removes it from the session.
    ///
    /// Args:
    ///     view_id (str): The UUID of the view to unmount
    ///
    /// Returns:
    ///     None
    fn unmount<'py>(&self, py: Python<'py>, view_id: String) -> PyResult<Bound<'py, PyAny>> {
        let handle = self.handle.clone();

        future_into_py(py, async move {
            handle
                .unmount(view_id)
                .await
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;
            Ok(())
        })
    }

    /// Health check ping
    ///
    /// Verifies that the session actor is still responsive.
    ///
    /// Returns:
    ///     None
    fn ping<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let handle = self.handle.clone();

        future_into_py(py, async move {
            handle
                .ping()
                .await
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;
            Ok(())
        })
    }

    /// Shutdown the session gracefully
    ///
    /// Shuts down all child ViewActors and then the SessionActor itself.
    fn shutdown<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let handle = self.handle.clone();

        future_into_py(py, async move {
            handle.shutdown().await;
            Ok(())
        })
    }

    // ========================================================================
    // Phase 8: Component Management Python API
    // ========================================================================

    /// Create a component in a specific view (Phase 8)
    ///
    /// Args:
    ///     view_id (str): ID of the view to create the component in
    ///     component_id (str): Unique identifier for the component
    ///     template_string (str): Template for rendering the component
    ///     initial_props (dict): Initial component state/props
    ///     python_component (Optional[Any]): Python component instance for event handlers (Phase 8.2)
    ///
    /// Returns:
    ///     str: Initial rendered HTML of the component
    #[pyo3(signature = (view_id, component_id, template_string, initial_props, python_component=None))]
    fn create_component<'py>(
        &self,
        py: Python<'py>,
        view_id: String,
        component_id: String,
        template_string: String,
        initial_props: &Bound<'py, PyDict>,
        python_component: Option<Py<PyAny>>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let handle = self.handle.clone();
        let props_rust = python_dict_to_hashmap(initial_props)?;

        future_into_py(py, async move {
            let html = handle
                .create_component(
                    view_id,
                    component_id,
                    template_string,
                    props_rust,
                    python_component,
                )
                .await
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;

            Ok(html)
        })
    }

    /// Route event to a specific component (Phase 8)
    ///
    /// Args:
    ///     view_id (str): ID of the view containing the component
    ///     component_id (str): ID of the component to send event to
    ///     event_name (str): Name of the event handler to call
    ///     params (dict): Event parameters
    ///
    /// Returns:
    ///     str: Rendered HTML after the component handles the event
    fn component_event<'py>(
        &self,
        py: Python<'py>,
        view_id: String,
        component_id: String,
        event_name: String,
        params: &Bound<'py, PyDict>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let handle = self.handle.clone();
        let params_rust = python_dict_to_hashmap(params)?;

        future_into_py(py, async move {
            let html = handle
                .component_event(view_id, component_id, event_name, params_rust)
                .await
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;

            Ok(html)
        })
    }

    /// Update props for a specific component (Phase 8)
    ///
    /// Args:
    ///     view_id (str): ID of the view containing the component
    ///     component_id (str): ID of the component to update
    ///     props (dict): New props to merge into component state
    ///
    /// Returns:
    ///     str: Rendered HTML after updating props
    fn update_component_props<'py>(
        &self,
        py: Python<'py>,
        view_id: String,
        component_id: String,
        props: &Bound<'py, PyDict>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let handle = self.handle.clone();
        let props_rust = python_dict_to_hashmap(props)?;

        future_into_py(py, async move {
            let html = handle
                .update_component_props(view_id, component_id, props_rust)
                .await
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;

            Ok(html)
        })
    }

    /// Remove a component (Phase 8)
    ///
    /// Args:
    ///     view_id (str): ID of the view containing the component
    ///     component_id (str): ID of the component to remove
    ///
    /// Returns:
    ///     None
    fn remove_component<'py>(
        &self,
        py: Python<'py>,
        view_id: String,
        component_id: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let handle = self.handle.clone();

        future_into_py(py, async move {
            handle
                .remove_component(view_id, component_id)
                .await
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;

            Ok(())
        })
    }

    /// Get the session ID
    #[getter]
    fn session_id(&self) -> String {
        self.handle.session_id().to_string()
    }
}

/// Create a new session actor
///
/// This function creates a SessionActor, spawns it on the Tokio runtime,
/// and returns a handle wrapped for Python.
///
/// Args:
///     session_id (str): Unique identifier for this session
///
/// Returns:
///     SessionActorHandle: Handle to send messages to the actor
#[pyfunction]
pub fn create_session_actor(py: Python<'_>, session_id: String) -> PyResult<Bound<'_, PyAny>> {
    future_into_py(py, async move {
        // Ensure supervisor background tasks are started (idempotent)
        ensure_supervisor_started();

        // Use global supervisor to get or create session
        let handle = SUPERVISOR.get_or_create_session(session_id).await;

        Python::with_gil(|py| -> PyResult<PyObject> {
            Ok(Py::new(py, SessionActorHandlePy { handle })?.into_any())
        })
    })
}

/// Supervisor statistics exposed to Python
#[pyclass]
#[derive(Debug, Clone)]
pub struct SupervisorStatsPy {
    /// Number of active sessions
    #[pyo3(get)]
    pub active_sessions: usize,
    /// Time-to-live for idle sessions in seconds
    #[pyo3(get)]
    pub ttl_secs: u64,
}

/// Get actor system statistics
///
/// Returns statistics about the actor supervisor including active sessions
/// and configured TTL.
///
/// Returns:
///     SupervisorStats: Object with active_sessions and ttl_secs attributes
#[pyfunction]
pub fn get_actor_stats() -> SupervisorStatsPy {
    let stats = SUPERVISOR.stats();
    SupervisorStatsPy {
        active_sessions: stats.active_sessions,
        ttl_secs: stats.ttl_secs,
    }
}

// Helper functions for Python ↔ Rust conversion

/// Convert Python dict to Rust HashMap<String, Value>
fn python_dict_to_hashmap(dict: &Bound<'_, PyDict>) -> PyResult<HashMap<String, Value>> {
    let mut map = HashMap::new();

    for (key, value) in dict.iter() {
        let key_str = key.extract::<String>()?;
        let rust_value = python_to_value(&value)?;
        map.insert(key_str, rust_value);
    }

    Ok(map)
}

/// Convert Python object to Rust Value
fn python_to_value(obj: &Bound<'_, PyAny>) -> PyResult<Value> {
    // String
    if let Ok(s) = obj.extract::<String>() {
        return Ok(Value::String(s));
    }

    // Integer
    if let Ok(i) = obj.extract::<i64>() {
        return Ok(Value::Integer(i));
    }

    // Float
    if let Ok(f) = obj.extract::<f64>() {
        return Ok(Value::Float(f));
    }

    // Boolean
    if let Ok(b) = obj.extract::<bool>() {
        return Ok(Value::Bool(b));
    }

    // None
    if obj.is_none() {
        return Ok(Value::Null);
    }

    // List
    if let Ok(list) = obj.downcast::<PyList>() {
        let mut vec = Vec::new();
        for item in list.iter() {
            vec.push(python_to_value(&item)?);
        }
        return Ok(Value::List(vec));
    }

    // Dict - recursively convert nested values
    if let Ok(dict) = obj.downcast::<PyDict>() {
        let mut map = HashMap::new();
        for (key, value) in dict.iter() {
            let key_str = key.extract::<String>()?;
            map.insert(key_str, python_to_value(&value)?);
        }
        return Ok(Value::Object(map));
    }

    // Fallback: try to convert to string
    if let Ok(s) = obj.str() {
        let s_str: String = s.extract()?;
        return Ok(Value::String(s_str));
    }

    Err(PyErr::new::<pyo3::exceptions::PyTypeError, _>(
        "Cannot convert Python type to Value".to_string(),
    ))
}

/// Extract template variables for JIT auto-serialization.
///
/// Parses a Django template and returns a dictionary mapping variable names
/// to lists of their access paths. This enables automatic serialization of
/// only the required Django ORM fields for efficient Rust template rendering.
///
/// # Arguments
///
/// * `template` - Template source string
///
/// # Returns
///
/// Dictionary mapping variable names to lists of attribute paths:
/// - Root variables map to empty lists
/// - Nested variables map to their access paths
/// - Paths are deduplicated and sorted alphabetically
///
/// # Raises
///
/// * `ValueError` - If template cannot be parsed (malformed syntax)
///
/// # Behavior
///
/// - **Empty templates**: Returns empty dict `{}`
/// - **Malformed templates**: Raises `ValueError` with parsing error details
/// - **Duplicate paths**: Automatically deduplicated
/// - **Template tags**: Extracts from for/if/with/block tags
/// - **Filters**: Ignores filters but preserves variable paths
///
/// # Example
///
/// ```python
/// from djust._rust import extract_template_variables
///
/// # Basic usage
/// template = "{{ lease.property.name }} {{ lease.tenant.user.email }}"
/// vars = extract_template_variables(template)
/// # Returns: {"lease": ["property.name", "tenant.user.email"]}
///
/// # Empty template
/// vars = extract_template_variables("")
/// # Returns: {}
///
/// # Root variable (no path)
/// vars = extract_template_variables("{{ count }}")
/// # Returns: {"count": []}
///
/// # Malformed template
/// try:
///     vars = extract_template_variables("{% if x")
/// except ValueError as e:
///     print(f"Parse error: {e}")
/// ```
///
/// # Use Case
///
/// ```python
/// class LeaseView(LiveView):
///     template_string = '''
///         {% for lease in expiring_soon %}
///             {{ lease.property.name }}
///             {{ lease.tenant.user.email }}
///         {% endfor %}
///     '''
///
///     def mount(self, request):
///         # Extract required fields
///         vars = extract_template_variables(self.template_string)
///         # vars = {
///         #   'lease': ['property.name', 'tenant.user.email'],
///         #   'expiring_soon': []
///         # }
///
///         # Generate optimized query
///         self.expiring_soon = Lease.objects.select_related(
///             'property', 'tenant__user'
///         ).filter(end_date__lte=timezone.now() + timedelta(days=30))
/// ```
/// Path node for serializer field tree
#[derive(Debug, Clone)]
enum PathNode {
    Leaf,
    Object(std::collections::HashMap<String, PathNode>),
    List(std::collections::HashMap<String, PathNode>),
}

/// Convert serde_json::Value to Python object using PyO3
///
/// This is faster than serializing to JSON string and parsing back!
fn json_value_to_py(py: Python, value: &serde_json::Value) -> PyResult<PyObject> {
    match value {
        serde_json::Value::Null => Ok(py.None()),
        serde_json::Value::Bool(b) => Ok((*b).into_pyobject(py)?.to_owned().into_any().unbind()),
        serde_json::Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Ok(i.into_pyobject(py)?.to_owned().into_any().unbind())
            } else if let Some(f) = n.as_f64() {
                Ok(f.into_pyobject(py)?.to_owned().into_any().unbind())
            } else {
                Ok(py.None())
            }
        }
        serde_json::Value::String(s) => Ok(s.into_pyobject(py)?.to_owned().into_any().unbind()),
        serde_json::Value::Array(arr) => {
            let py_list = PyList::empty(py);
            for item in arr {
                py_list.append(json_value_to_py(py, item)?)?;
            }
            Ok(py_list.into())
        }
        serde_json::Value::Object(obj) => {
            let py_dict = PyDict::new(py);
            for (key, val) in obj {
                py_dict.set_item(key, json_value_to_py(py, val)?)?;
            }
            Ok(py_dict.into())
        }
    }
}

/// Serialize a QuerySet/list of Django objects based on field paths - FAST!
///
/// This is a Rust-based serializer that extracts only specified fields from Python objects,
/// bypassing Python's overhead for attribute access and JSON encoding.
///
/// Performance: 5-10x faster than Python json.dumps() with DjangoJSONEncoder
///
/// # Arguments
/// * `objects` - List of Python objects (Django model instances)
/// * `field_paths` - List of dot-separated paths (e.g., ["user.email", "active_leases.0.property.name"])
///
/// # Returns
/// Python list of dictionaries (not JSON string!)
///
/// # Example
/// ```python
/// from djust._rust import serialize_queryset
///
/// tenants = Tenant.objects.select_related('user').prefetch_related('active_leases__property')
/// paths = ['user.email', 'user.get_full_name', 'active_leases.0.property.name', 'phone']
/// result_list = serialize_queryset(tenants, paths)  # Returns Python list directly!
/// ```
#[pyfunction(name = "serialize_queryset")]
fn serialize_queryset_py(
    py: Python,
    objects: &Bound<'_, PyList>,
    field_paths: Vec<String>,
) -> PyResult<Py<PyList>> {
    // Parse paths into tree structure for efficient traversal
    let path_tree = build_field_tree(&field_paths);

    // Create Python list to hold results
    let result_list = PyList::empty(py);

    // Iterate over objects
    for obj in objects.iter() {
        let serialized = serialize_object_with_paths(py, &obj, &path_tree)?;
        // Convert serde_json::Value to Python dict
        let py_dict = json_value_to_py(py, &serialized)?;
        result_list.append(py_dict)?;
    }

    Ok(result_list.into())
}

/// Serialize entire context dict to JSON-compatible Python dict
///
/// Handles all Python types efficiently:
/// - Simple types (str, int, float, bool, None): pass through
/// - Lists/tuples: recursively serialize
/// - Dicts: recursively serialize
/// - Components: call .render() and wrap in {"render": ...}
/// - Django types (datetime, Decimal, UUID): convert to strings
#[pyfunction(name = "serialize_context")]
fn serialize_context_py(py: Python, context: &Bound<'_, PyDict>) -> PyResult<Py<PyDict>> {
    let result_dict = PyDict::new(py);

    for (key, value) in context.iter() {
        let key_str: String = key.extract()?;
        let serialized_value = serialize_python_value(py, &value)?;
        result_dict.set_item(key_str, serialized_value)?;
    }

    Ok(result_dict.into())
}

/// Recursively serialize a Python value to JSON-compatible form
fn serialize_python_value(py: Python, value: &Bound<'_, PyAny>) -> PyResult<PyObject> {
    // Fast path: None
    if value.is_none() {
        return Ok(py.None());
    }

    // Get type name for special type handling
    let type_name = value
        .get_type()
        .name()
        .map_or("unknown".to_string(), |s| s.to_string());

    // IMPORTANT: Check compound types (List, Tuple, Dict) BEFORE simple types!
    // Otherwise extract::<String>() will convert them to string repr

    // Lists and tuples: recursively serialize
    if let Ok(list) = value.downcast::<PyList>() {
        let result_list = PyList::empty(py);
        for item in list.iter() {
            let serialized = serialize_python_value(py, &item)?;
            result_list.append(serialized)?;
        }
        return Ok(result_list.into());
    }

    if let Ok(tuple) = value.downcast::<PyTuple>() {
        let result_list = PyList::empty(py);
        for item in tuple.iter() {
            let serialized = serialize_python_value(py, &item)?;
            result_list.append(serialized)?;
        }
        return Ok(result_list.into());
    }

    // Dicts: recursively serialize
    if let Ok(dict) = value.downcast::<PyDict>() {
        let result_dict = PyDict::new(py);
        for (k, v) in dict.iter() {
            let key_str: String = k.extract()?;
            let serialized = serialize_python_value(py, &v)?;
            result_dict.set_item(key_str, serialized)?;
        }
        return Ok(result_dict.into());
    }

    // Django QuerySet: Convert to list which triggers JIT serialization
    if type_name == "QuerySet" {
        // Call list() on the QuerySet to force evaluation
        if let Ok(list_fn) = py.eval(c"list", None, None) {
            if let Ok(as_list) = list_fn.call1((value,)) {
                return serialize_python_value(py, &as_list);
            }
        }
    }

    // Fast path: Simple types (str, int, float, bool)
    // These must come AFTER compound types to avoid converting them to strings
    if let Ok(s) = value.extract::<String>() {
        return Ok(s.into_pyobject(py)?.to_owned().into_any().unbind());
    }
    if let Ok(i) = value.extract::<i64>() {
        return Ok(i.into_pyobject(py)?.to_owned().into_any().unbind());
    }
    if let Ok(f) = value.extract::<f64>() {
        return Ok(f.into_pyobject(py)?.to_owned().into_any().unbind());
    }
    if let Ok(b) = value.extract::<bool>() {
        return Ok(b.into_pyobject(py)?.to_owned().into_any().unbind());
    }

    // Components: Check if object has 'render' method
    if value.hasattr("render")? {
        // Call .render() method
        let render_method = value.getattr("render")?;
        if render_method.is_callable() {
            match render_method.call0() {
                Ok(rendered) => {
                    // Wrap in {"render": ...} dict
                    let wrapper = PyDict::new(py);
                    let rendered_str: String = rendered.extract()?;
                    wrapper.set_item("render", rendered_str)?;
                    return Ok(wrapper.into());
                }
                Err(_) => {
                    // Render failed, fallback to str()
                    let str_repr = value.str()?.to_string();
                    return Ok(str_repr.into_pyobject(py)?.to_owned().into_any().unbind());
                }
            }
        }
    }

    // Django date/time types: convert to ISO strings
    // Check for common Django/Python datetime types
    if type_name == "datetime" || type_name == "date" || type_name == "time" {
        if let Ok(isoformat) = value.call_method0("isoformat") {
            let iso_str: String = isoformat.extract()?;
            return Ok(iso_str.into_pyobject(py)?.to_owned().into_any().unbind());
        }
    }

    // Decimal, UUID: convert to string
    if type_name == "Decimal" || type_name == "UUID" {
        let str_repr = value.str()?.to_string();
        return Ok(str_repr.into_pyobject(py)?.to_owned().into_any().unbind());
    }

    // Django model instances: serialize to dict using model_to_dict + methods
    if value.hasattr("_meta")? {
        // Use Django's model_to_dict to serialize the model fields
        if let Ok(forms_module) = py.import("django.forms.models") {
            if let Ok(model_to_dict_fn) = forms_module.getattr("model_to_dict") {
                if let Ok(model_dict) = model_to_dict_fn.call1((value,)) {
                    // Convert to PyDict so we can add method results
                    if let Ok(result_dict) = model_dict.downcast::<PyDict>() {
                        // Call specific, known-safe get_* methods commonly used in templates
                        // This is safer than calling all get_* methods which can cause infinite recursion
                        let safe_methods = vec![
                            "get_full_name",
                            "get_short_name",
                            "get_absolute_url",
                            "get_username",
                        ];

                        for method_name in safe_methods {
                            if let Ok(attr) = value.getattr(method_name) {
                                if attr.is_callable() {
                                    if let Ok(result) = attr.call0() {
                                        // Convert result to string to avoid recursive serialization
                                        if let Ok(result_str) = result.str() {
                                            let _ = result_dict.set_item(method_name, result_str);
                                        }
                                    }
                                }
                            }
                        }

                        // Recursively serialize the enhanced dict
                        return serialize_python_value(py, result_dict.as_any());
                    }
                }
            }
        }
    }

    // Fallback: convert to string representation
    let str_repr = value.str()?.to_string();
    Ok(str_repr.into_pyobject(py)?.to_owned().into_any().unbind())
}

/// Build a tree structure from flat field paths for efficient nested traversal
fn build_field_tree(paths: &[String]) -> std::collections::HashMap<String, PathNode> {
    use std::collections::HashMap;

    let mut root: HashMap<String, PathNode> = HashMap::new();

    for path in paths {
        let parts: Vec<&str> = path.split('.').collect();
        if parts.is_empty() {
            continue;
        }

        let mut current = &mut root;
        let mut i = 0;

        while i < parts.len() {
            let part = parts[i];

            // Check if next part is numeric index (list access)
            if i + 1 < parts.len() && parts[i + 1].parse::<usize>().is_ok() {
                // This is a list attribute
                let entry = current
                    .entry(part.to_string())
                    .or_insert_with(|| PathNode::List(HashMap::new()));

                match entry {
                    PathNode::List(nested_map) => {
                        // Skip the numeric index and continue with remaining parts
                        i += 2;
                        if i < parts.len() {
                            // Process remaining path within list items
                            let remaining_parts: Vec<&str> = parts[i..].to_vec();
                            let remaining_path = remaining_parts.join(".");

                            // Recursively add remaining path to nested map
                            let mut temp_map = std::mem::take(nested_map);
                            add_path_to_tree(&mut temp_map, &remaining_path);
                            *nested_map = temp_map;
                        }
                        break;
                    }
                    PathNode::Leaf => {
                        // Convert Leaf to List (handles case where 'active_leases' was inserted before 'active_leases.0.property.name')
                        *entry = PathNode::List(HashMap::new());
                        if let PathNode::List(nested_map) = entry {
                            // Skip the numeric index and continue with remaining parts
                            i += 2;
                            if i < parts.len() {
                                // Process remaining path within list items
                                let remaining_parts: Vec<&str> = parts[i..].to_vec();
                                let remaining_path = remaining_parts.join(".");

                                // Recursively add remaining path to nested map
                                let mut temp_map = std::mem::take(nested_map);
                                add_path_to_tree(&mut temp_map, &remaining_path);
                                *nested_map = temp_map;
                            }
                        }
                        break;
                    }
                    _ => {
                        // Other type mismatch - skip this path and continue to next
                        break;
                    }
                }
            } else {
                // Regular attribute
                if i == parts.len() - 1 {
                    // Leaf node
                    current.entry(part.to_string()).or_insert(PathNode::Leaf);
                } else {
                    // Intermediate node
                    let entry = current
                        .entry(part.to_string())
                        .or_insert_with(|| PathNode::Object(HashMap::new()));

                    match entry {
                        PathNode::Object(nested_map) => {
                            current = nested_map;
                        }
                        _ => {
                            return root; // Type mismatch
                        }
                    }
                }
                i += 1;
            }
        }
    }

    root
}

/// Helper to add a path to an existing tree
fn add_path_to_tree(tree: &mut std::collections::HashMap<String, PathNode>, path: &str) {
    use std::collections::HashMap;

    let parts: Vec<&str> = path.split('.').collect();
    if parts.is_empty() {
        return;
    }

    let mut current = tree;
    for (i, part) in parts.iter().enumerate() {
        if i == parts.len() - 1 {
            current.entry(part.to_string()).or_insert(PathNode::Leaf);
        } else {
            let entry = current
                .entry(part.to_string())
                .or_insert_with(|| PathNode::Object(HashMap::new()));
            match entry {
                PathNode::Object(nested_map) => {
                    current = nested_map;
                }
                _ => break,
            }
        }
    }
}

/// Serialize a single Python object based on path tree
fn serialize_object_with_paths(
    py: Python,
    obj: &Bound<'_, PyAny>,
    tree: &std::collections::HashMap<String, PathNode>,
) -> PyResult<serde_json::Value> {
    use serde_json::{Map, Value as JsonValue};

    let mut result = Map::new();

    for (attr_name, node) in tree {
        // Try to access attribute
        let attr_result = obj.getattr(attr_name.as_str());

        if attr_result.is_err() {
            continue; // Attribute doesn't exist, skip
        }

        let attr_value = attr_result?;

        // Check if None
        if attr_value.is_none() {
            result.insert(attr_name.clone(), JsonValue::Null);
            continue;
        }

        match node {
            PathNode::Leaf => {
                // Check if it's a callable (method) - try calling it first
                if attr_value.is_callable() {
                    // It's a method - try calling it
                    match attr_value.call0() {
                        Ok(method_result) => {
                            // Method call succeeded - use the result
                            result.insert(attr_name.clone(), python_to_json(py, &method_result)?);
                        }
                        Err(_) => {
                            // Method call failed - skip this attribute (don't insert null)
                            // This can happen if the method requires arguments
                            continue;
                        }
                    }
                } else {
                    // Not callable - it's a direct attribute value
                    result.insert(attr_name.clone(), python_to_json(py, &attr_value)?);
                }
            }
            PathNode::Object(nested_tree) => {
                // Nested object
                let nested_result = serialize_object_with_paths(py, &attr_value, nested_tree)?;
                result.insert(attr_name.clone(), nested_result);
            }
            PathNode::List(nested_tree) => {
                // Iterate over list and serialize each item
                let mut list_results = Vec::new();

                // For Django QuerySets/Managers, we need to evaluate them first
                // Try to call .all() if it exists (for QuerySets/Managers)
                let iterable = if let Ok(all_method) = attr_value.getattr("all") {
                    // It has .all() method - call it to get the QuerySet
                    if let Ok(queryset) = all_method.call0() {
                        // Convert QuerySet to Python list for iteration
                        if let Ok(list_func) = py.eval(c"list", None, None) {
                            if let Ok(py_list) = list_func.call1((queryset,)) {
                                py_list
                            } else {
                                attr_value.clone()
                            }
                        } else {
                            attr_value.clone()
                        }
                    } else {
                        attr_value.clone()
                    }
                } else {
                    attr_value.clone()
                };

                // Try to iterate
                if let Ok(iterator) = iterable.try_iter() {
                    for item_result in iterator {
                        let item_obj = match item_result {
                            Ok(obj) => obj,
                            Err(_) => continue,
                        };
                        let item_result = serialize_object_with_paths(py, &item_obj, nested_tree)?;
                        list_results.push(item_result);
                    }
                }

                result.insert(attr_name.clone(), JsonValue::Array(list_results));
            }
        }
    }

    Ok(JsonValue::Object(result))
}

/// Convert Python value to JSON value
fn python_to_json(_py: Python, value: &Bound<'_, PyAny>) -> PyResult<serde_json::Value> {
    // Handle None
    if value.is_none() {
        return Ok(serde_json::Value::Null);
    }

    // Try bool first (before int, since bool is subclass of int in Python)
    if let Ok(b) = value.extract::<bool>() {
        return Ok(serde_json::Value::Bool(b));
    }

    // Try int
    if let Ok(i) = value.extract::<i64>() {
        return Ok(serde_json::Value::Number(i.into()));
    }

    // Try float
    if let Ok(f) = value.extract::<f64>() {
        if let Some(num) = serde_json::Number::from_f64(f) {
            return Ok(serde_json::Value::Number(num));
        }
    }

    // Try string (covers str, datetime, UUID, etc via __str__)
    if let Ok(s) = value.extract::<String>() {
        return Ok(serde_json::Value::String(s));
    }

    // Fallback: convert to string
    match value.str() {
        Ok(s) => Ok(serde_json::Value::String(s.to_string())),
        Err(_) => Ok(serde_json::Value::Null),
    }
}

/// Build a map from template node index to VDOM text node path.
/// For each fragment that's plain text (no HTML tags), find the matching
/// text node in the VDOM by walking it depth-first and matching content.
/// Attempt a text-region fast path: compute common prefix/suffix on the
/// full pre-hydration HTML and, if the divergence is a single text span,
/// patch the corresponding VDOM text node in place. Skips html5ever.
///
/// Returns `Some((new_vdom, patches))` if the fast path can be taken.
/// Returns `None` if the diff crosses a tag boundary, spans multiple
/// text nodes, contains HTML entities that would confuse the offset
/// math, or any validation fails — in which case the caller falls back
/// to the full html5ever path.
fn try_text_region_fast_path(
    old_html: &str,
    new_html: &str,
    old_vdom: &VNode,
    text_node_index: &mut [TextNodeEntry],
) -> Option<(VNode, Vec<djust_vdom::Patch>)> {
    // Without an index we can't resolve pfx → VDOM path cheaply. The
    // caller should have built one after the previous full-parse render.
    if text_node_index.is_empty() {
        return None;
    }
    let old_bytes = old_html.as_bytes();
    let new_bytes = new_html.as_bytes();
    let old_len = old_bytes.len();
    let new_len = new_bytes.len();
    let min_len = old_len.min(new_len);

    // Common prefix (bytewise)
    let mut pfx = 0;
    while pfx < min_len && old_bytes[pfx] == new_bytes[pfx] {
        pfx += 1;
    }

    // Common suffix (cannot overlap the prefix)
    let mut sfx = 0;
    let max_sfx = min_len - pfx;
    while sfx < max_sfx && old_bytes[old_len - 1 - sfx] == new_bytes[new_len - 1 - sfx] {
        sfx += 1;
    }

    // Snap pfx and sfx back to UTF-8 char boundaries. Common prefix can
    // stop mid-multibyte-char when the diverging char shares leading
    // bytes (e.g. '↑' vs '↓': both start 0xE2 0x86). Slicing &str on
    // a non-boundary byte index would panic.
    while pfx > 0 && !old_html.is_char_boundary(pfx) {
        pfx -= 1;
    }
    while pfx > 0 && !new_html.is_char_boundary(pfx) {
        pfx -= 1;
    }
    // Re-snap sfx for both strings so old_len - sfx and new_len - sfx
    // are both char boundaries.
    while sfx > 0
        && (!old_html.is_char_boundary(old_len - sfx) || !new_html.is_char_boundary(new_len - sfx))
    {
        sfx -= 1;
    }

    // If nothing differs (shouldn't happen when changed_keys is set, but
    // be defensive), there's no patch to produce.
    if pfx == old_len && pfx == new_len {
        return Some((old_vdom.clone(), Vec::new()));
    }

    // Defensive: ensure slicing ranges are valid after snapping.
    if pfx > old_len - sfx || pfx > new_len - sfx {
        return None;
    }

    let old_mid = &old_html[pfx..old_len - sfx];
    let new_mid = &new_html[pfx..new_len - sfx];

    // Safety: middle must be tag-free on BOTH sides. Tag chars here mean
    // the structure actually changed, not just a text value.
    if old_mid.as_bytes().iter().any(|&b| b == b'<' || b == b'>')
        || new_mid.as_bytes().iter().any(|&b| b == b'<' || b == b'>')
    {
        return None;
    }

    // After-region boundary: the char right after the diff must be
    // inside the same text node (not open-tag), otherwise multiple text
    // nodes are affected. If the byte at `after_idx` is '<', we're
    // cleanly at the text/tag boundary — valid. If it's something else
    // (e.g. more text), that's also fine (diff is in the middle).
    // If it's '>' we've likely misaligned; bail.
    let after_idx = old_len - sfx;
    if after_idx < old_len && old_bytes[after_idx] == b'>' {
        return None;
    }

    // Binary-search the pre-built text-node index for the entry whose
    // HTML byte range contains `pfx`. The index is sorted by
    // `html_start`, so we find the last entry with `html_start <= pfx`
    // and confirm it covers the position.
    let entry_idx = {
        use std::cmp::Ordering;
        match text_node_index.binary_search_by(|e| {
            if pfx < e.html_start {
                Ordering::Greater
            } else if pfx >= e.html_end {
                Ordering::Less
            } else {
                Ordering::Equal
            }
        }) {
            Ok(i) => i,
            Err(_) => return None, // pfx falls inside a tag, outside any text node
        }
    };
    let entry = &text_node_index[entry_idx];

    // Compute where in the text node (byte offset within its HTML text)
    // the diff starts.
    let offset_in_text = pfx - entry.html_start;

    // Diff span must fit entirely within this text node in HTML terms.
    if pfx + old_mid.len() > entry.html_end {
        return None;
    }

    let path = entry.path.clone();
    let old_text = entry.text.clone();
    let djust_id = entry.djust_id.clone();
    let old_html_end = entry.html_end;

    // Entity-decoded text in the VDOM may differ from the raw HTML
    // bytes. Validate that the byte-offset we computed matches actual
    // VDOM text content. If entities shift positions, bail.
    let end_in_text = offset_in_text + old_mid.len();
    if end_in_text > old_text.len()
        || !old_text.is_char_boundary(offset_in_text)
        || !old_text.is_char_boundary(end_in_text)
    {
        return None;
    }
    if &old_text[offset_in_text..end_in_text] != old_mid {
        return None;
    }

    // Build the new text by swapping just the diff span inside this
    // text node. Boundaries already validated above.
    let mut new_text =
        String::with_capacity(old_text.len() + new_mid.len().saturating_sub(old_mid.len()));
    new_text.push_str(&old_text[..offset_in_text]);
    new_text.push_str(new_mid);
    new_text.push_str(&old_text[end_in_text..]);

    // Clone the old VDOM and apply the edit in place.
    let mut new_vdom = old_vdom.clone();
    {
        let node = get_vdom_node_mut(&mut new_vdom, &path)?;
        node.text = Some(new_text.clone());
        node.cached_html = None;
    }

    // Keep the index in sync for subsequent fast-path renders: the
    // changed entry grows/shrinks by (new_mid - old_mid) bytes, and
    // every downstream entry's HTML range shifts by the same amount.
    let new_html_end = old_html_end + new_mid.len() - old_mid.len();
    text_node_index[entry_idx].text = new_text.clone();
    text_node_index[entry_idx].html_end = new_html_end;
    if new_mid.len() != old_mid.len() {
        let delta_add = new_mid.len() as isize - old_mid.len() as isize;
        for e in &mut text_node_index[entry_idx + 1..] {
            e.html_start = (e.html_start as isize + delta_add) as usize;
            e.html_end = (e.html_end as isize + delta_add) as usize;
        }
    }

    let d = if djust_id.is_empty() {
        None
    } else {
        Some(djust_id)
    };
    let patch = djust_vdom::Patch::SetText {
        path,
        d,
        text: new_text,
    };
    Some((new_vdom, vec![patch]))
}

fn starts_with_ci(slice: &[u8], prefix: &[u8]) -> bool {
    slice.len() >= prefix.len()
        && slice
            .iter()
            .zip(prefix.iter())
            .all(|(a, b)| a.eq_ignore_ascii_case(b))
}

/// Locate the byte offset in `html` immediately after the opening tag
/// of the first element bearing a `dj-root` or `dj-view` attribute.
/// Returns None if no such element is found.
///
/// Used to align the scanner's starting point with `find_root` in the
/// VDOM parser, which begins the VDOM tree at that same element. Without
/// this alignment, text nodes inside `<head>` (titles, meta, etc.) from
/// a base template would be counted by the scanner but not appear in
/// the VDOM, breaking the 1:1 text-node mapping.
fn find_dj_root_content_range(html: &str) -> Option<(usize, usize)> {
    let bytes = html.as_bytes();
    // Find the OPEN tag of the dj-root element and capture its tag name.
    let mut i = 0;
    let (open_end, tag_name) = loop {
        if i >= bytes.len() {
            return None;
        }
        if bytes[i] != b'<' {
            i += 1;
            continue;
        }
        let mut j = i + 1;
        while j < bytes.len() && bytes[j] != b'>' {
            j += 1;
        }
        if j >= bytes.len() {
            return None;
        }
        let tag_body = &bytes[i + 1..j];
        if tag_body.is_empty() || tag_body[0] == b'/' || tag_body[0] == b'!' {
            i = j + 1;
            continue;
        }
        let has_marker = tag_body
            .windows(8)
            .any(|w| w.eq_ignore_ascii_case(b" dj-root") || w.eq_ignore_ascii_case(b" dj-view"));
        if !has_marker {
            i = j + 1;
            continue;
        }
        let name_end = tag_body
            .iter()
            .position(|&c| c == b' ' || c == b'\t' || c == b'\n' || c == b'/' || c == b'>')
            .unwrap_or(tag_body.len());
        let name = tag_body[..name_end].to_ascii_lowercase();
        break (j + 1, name);
    };

    // Now walk forward, balancing open/close tags of the same name, to
    // find the matching closing tag. Returns the byte offset of that
    // closing tag's `<`.
    let mut depth: usize = 1;
    let mut k = open_end;
    while k < bytes.len() {
        if bytes[k] != b'<' {
            k += 1;
            continue;
        }
        let mut m = k + 1;
        while m < bytes.len() && bytes[m] != b'>' {
            m += 1;
        }
        if m >= bytes.len() {
            return None;
        }
        let tag_body = &bytes[k + 1..m];
        if tag_body.is_empty() || tag_body[0] == b'!' {
            k = m + 1;
            continue;
        }
        let is_close = tag_body[0] == b'/';
        let name_start = if is_close { 1 } else { 0 };
        let name_end = tag_body[name_start..]
            .iter()
            .position(|&c| c == b' ' || c == b'\t' || c == b'\n' || c == b'/' || c == b'>')
            .map(|n| name_start + n)
            .unwrap_or(tag_body.len());
        let this_name = tag_body[name_start..name_end].to_ascii_lowercase();
        if this_name == tag_name {
            if is_close {
                depth -= 1;
                if depth == 0 {
                    return Some((open_end, k));
                }
            } else {
                depth += 1;
            }
        }
        k = m + 1;
    }
    None
}

/// Scan `html` and return the byte ranges of each text node that would
/// survive the VDOM parser's whitespace filter, in document order.
///
/// The returned vector mirrors the count and order of `collect_vdom_text_nodes`
/// so the Nth entry here corresponds to the Nth VDOM text node.
///
/// Special handling:
/// - Whitespace-preserving elements (`<pre>`, `<code>`, `<textarea>`):
///   all text inside them counts, including whitespace-only runs.
/// - Raw-text elements (`<script>`, `<style>`): the entire body is one
///   text node, and `<` inside is literal (not a tag). We skip to the
///   matching close tag as a single unit.
/// - Comments and doctypes: ignored.
///
/// Returns `None` on unterminated tags or other malformed input.
fn scan_html_text_runs(html: &str) -> Option<Vec<(usize, usize)>> {
    fn is_preserve_tag(name: &[u8]) -> bool {
        name.eq_ignore_ascii_case(b"pre")
            || name.eq_ignore_ascii_case(b"code")
            || name.eq_ignore_ascii_case(b"textarea")
    }
    fn is_raw_text_tag(name: &[u8]) -> bool {
        name.eq_ignore_ascii_case(b"script") || name.eq_ignore_ascii_case(b"style")
    }

    let bytes = html.as_bytes();
    let mut runs = Vec::new();
    let mut i = 0;
    let mut preserve_depth: usize = 0;
    let mut current_start: Option<usize> = None;
    let mut current_preserve = false;

    while i < bytes.len() {
        let b = bytes[i];
        if b == b'<' {
            // Close any in-progress text run first.
            if let Some(start) = current_start.take() {
                let run = &bytes[start..i];
                let all_ws = run.iter().all(|&c| c.is_ascii_whitespace());
                if !all_ws || current_preserve {
                    runs.push((start, i));
                }
            }

            let tag_end = {
                let mut j = i + 1;
                while j < bytes.len() && bytes[j] != b'>' {
                    j += 1;
                }
                j
            };
            if tag_end >= bytes.len() {
                return None;
            }
            let tag_body = &bytes[i + 1..tag_end];
            if !tag_body.is_empty() && tag_body[0] == b'!' {
                // Comment / doctype / CDATA — no text-node effect.
                i = tag_end + 1;
                continue;
            }
            let is_close = !tag_body.is_empty() && tag_body[0] == b'/';
            let name_start = if is_close { 1 } else { 0 };
            let name_end = tag_body[name_start..]
                .iter()
                .position(|&c| c == b' ' || c == b'\t' || c == b'\n' || c == b'/' || c == b'>')
                .map(|n| name_start + n)
                .unwrap_or(tag_body.len());
            let tag_name = &tag_body[name_start..name_end];

            if !is_close && is_raw_text_tag(tag_name) {
                // Raw-text element: body is a single text node. Find the
                // matching close tag and record the range between `>`
                // and `</tag>` as one run. `<` inside is literal.
                let body_start = tag_end + 1;
                let close_needle_upper = {
                    let mut v = b"</".to_vec();
                    v.extend(tag_name.iter().map(|c| c.to_ascii_lowercase()));
                    v
                };
                let mut search_pos = body_start;
                let close_at = loop {
                    if search_pos >= bytes.len() {
                        return None; // unterminated raw-text element
                    }
                    if bytes[search_pos] == b'<'
                        && starts_with_ci(&bytes[search_pos..], &close_needle_upper)
                    {
                        break search_pos;
                    }
                    search_pos += 1;
                };
                // Only emit a run if non-empty.
                if close_at > body_start {
                    runs.push((body_start, close_at));
                }
                // Skip past the close tag: find '>' after close_at.
                let mut k = close_at;
                while k < bytes.len() && bytes[k] != b'>' {
                    k += 1;
                }
                i = k.saturating_add(1);
                continue;
            }

            if is_preserve_tag(tag_name) {
                if is_close {
                    preserve_depth = preserve_depth.saturating_sub(1);
                } else {
                    preserve_depth += 1;
                }
            }
            i = tag_end + 1;
            continue;
        }

        if current_start.is_none() {
            current_start = Some(i);
            current_preserve = preserve_depth > 0;
        }
        i += 1;
    }

    // Trailing run (document ends with text, no closing tag).
    if let Some(start) = current_start {
        let run = &bytes[start..i];
        let all_ws = run.iter().all(|&c| c.is_ascii_whitespace());
        if !all_ws || current_preserve {
            runs.push((start, i));
        }
    }

    Some(runs)
}

/// Build the sorted `(html_start, html_end, path, text, djust_id)` index
/// used by `try_text_region_fast_path`. Returns empty if HTML and VDOM
/// text-node counts don't agree (e.g. due to `<script>`/`<style>` in
/// the document, which the scanner refuses to count).
fn build_text_node_index(html: &str, vdom: &VNode) -> Vec<TextNodeEntry> {
    // The VDOM is rooted at [dj-root] / [dj-view], so only text nodes
    // INSIDE that element are in the VDOM. When the template extends a
    // base, the pre-hydration HTML may include `<html><head><title>…`
    // before dj-root and `<footer>`/`<script>` siblings after it — all
    // of which have text the VDOM doesn't. Restrict the scanner's range
    // to the dj-root element's interior to keep the 1:1 mapping.
    let (scan_from, scan_to) = find_dj_root_content_range(html).unwrap_or((0, html.len()));
    let Some(runs) = scan_html_text_runs(&html[scan_from..scan_to]) else {
        return Vec::new();
    };
    let runs: Vec<(usize, usize)> = runs
        .into_iter()
        .map(|(s, e)| (s + scan_from, e + scan_from))
        .collect();

    let mut vdom_nodes: Vec<(Vec<usize>, String, String)> = Vec::new();
    collect_vdom_text_nodes(vdom, &mut vec![], &mut vdom_nodes);

    if runs.len() != vdom_nodes.len() {
        // Scanner and VDOM walker disagreed on count — fast-path can't
        // trust the 1:1 mapping. Fall through to full html5ever parse.
        return Vec::new();
    }

    runs.into_iter()
        .zip(vdom_nodes)
        .map(
            |((html_start, html_end), (path, text, djust_id))| TextNodeEntry {
                html_start,
                html_end,
                path,
                text,
                djust_id,
            },
        )
        .collect()
}

fn build_fragment_text_map(
    fragments: &[String],
    vdom: &VNode,
) -> HashMap<usize, (Vec<usize>, String)> {
    let mut map = HashMap::new();

    // Collect all text nodes from the VDOM with their paths
    let mut text_nodes: Vec<(Vec<usize>, String, String)> = Vec::new(); // (path, text, djust_id)
    collect_vdom_text_nodes(vdom, &mut vec![], &mut text_nodes);

    for (idx, frag) in fragments.iter().enumerate() {
        // Only map text-only fragments (no HTML tags)
        if frag.contains('<') || frag.is_empty() {
            continue;
        }
        // Find the first matching text node
        for (path, text, djust_id) in &text_nodes {
            if text == frag {
                map.insert(idx, (path.clone(), djust_id.clone()));
                break;
            }
        }
    }

    map
}

fn collect_vdom_text_nodes(
    node: &VNode,
    current_path: &mut Vec<usize>,
    entries: &mut Vec<(Vec<usize>, String, String)>,
) {
    // Only collect true text nodes. Comment nodes also have `text` set
    // (to store the comment body), so filter by `is_text()` to avoid
    // miscounting — e.g. `<!--dj-if-->` placeholders would otherwise
    // shift every subsequent text ordinal by one.
    if node.is_text() {
        if let Some(ref text) = node.text {
            let djust_id = node.djust_id.clone().unwrap_or_default();
            entries.push((current_path.clone(), text.clone(), djust_id));
        }
    }
    for (i, child) in node.children.iter().enumerate() {
        current_path.push(i);
        collect_vdom_text_nodes(child, current_path, entries);
        current_path.pop();
    }
}

fn get_vdom_node_mut<'a>(vdom: &'a mut VNode, path: &[usize]) -> Option<&'a mut VNode> {
    let mut node = vdom;
    for &idx in path {
        node = node.children.get_mut(idx)?;
    }
    Some(node)
}

#[pyfunction(name = "extract_template_variables")]
fn extract_template_variables_py(py: Python, template: String) -> PyResult<PyObject> {
    // Call Rust template parser
    let vars_map = djust_templates::extract_template_variables(&template).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("Template parsing error: {e}"))
    })?;

    // Convert Rust HashMap to Python dict
    let py_dict = PyDict::new(py);
    for (key, paths) in vars_map {
        let py_list = PyList::new(py, paths.iter().map(|s| s.as_str()))?;
        py_dict.set_item(key, py_list)?;
    }

    Ok(py_dict.into())
}

#[pymodule]
fn _rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RustLiveViewBackend>()?;
    m.add_function(wrap_pyfunction!(render_template, m)?)?;
    m.add_function(wrap_pyfunction!(render_template_with_dirs, m)?)?;
    m.add_function(wrap_pyfunction!(render_markdown_py, m)?)?;
    m.add_function(wrap_pyfunction!(diff_html, m)?)?;
    m.add_function(wrap_pyfunction!(fast_json_dumps, m)?)?;
    m.add_function(wrap_pyfunction!(resolve_template_inheritance, m)?)?;

    // Actor system exports
    m.add_class::<SessionActorHandlePy>()?;
    m.add_class::<SupervisorStatsPy>()?;
    m.add_function(wrap_pyfunction!(create_session_actor, m)?)?;
    m.add_function(wrap_pyfunction!(get_actor_stats, m)?)?;

    // Add pure Rust components (stateless, high-performance ~1μs rendering)
    m.add_class::<djust_components::RustAlert>()?;
    m.add_class::<djust_components::RustAvatar>()?;
    m.add_class::<djust_components::RustBadge>()?;
    m.add_class::<djust_components::RustButton>()?;
    m.add_class::<djust_components::RustCard>()?;
    m.add_class::<djust_components::RustDivider>()?;
    m.add_class::<djust_components::RustIcon>()?;
    m.add_class::<djust_components::RustModal>()?;
    m.add_class::<djust_components::RustProgress>()?;
    m.add_class::<djust_components::RustRange>()?;
    m.add_class::<djust_components::RustSpinner>()?;
    m.add_class::<djust_components::RustSwitch>()?;
    m.add_class::<djust_components::RustTextArea>()?;
    m.add_class::<djust_components::RustToast>()?;
    m.add_class::<djust_components::RustTooltip>()?;

    // JIT auto-serialization
    m.add_function(wrap_pyfunction!(extract_template_variables_py, m)?)?;
    m.add_function(wrap_pyfunction!(serialize_queryset_py, m)?)?;
    m.add_function(wrap_pyfunction!(serialize_context_py, m)?)?;

    // Fast model serialization (N+1 prevention)
    m.add_function(wrap_pyfunction!(
        model_serializer::serialize_models_fast,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        model_serializer::serialize_models_to_list,
        m
    )?)?;

    // Tag handler registry for custom template tags (url, static, etc.)
    m.add_function(wrap_pyfunction!(
        djust_templates::registry::register_tag_handler,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        djust_templates::registry::unregister_tag_handler,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        djust_templates::registry::has_tag_handler,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        djust_templates::registry::get_registered_tags,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        djust_templates::registry::clear_tag_handlers,
        m
    )?)?;

    // Block tag handler registry for block tags with children (modal, card, etc.)
    m.add_function(wrap_pyfunction!(
        djust_templates::registry::register_block_tag_handler,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        djust_templates::registry::unregister_block_tag_handler,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        djust_templates::registry::has_block_tag_handler,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        djust_templates::registry::clear_block_tag_handlers,
        m
    )?)?;

    // Assign tag handler registry (context-mutating tags). Returns a
    // dict that's merged into the context rather than HTML.
    m.add_function(wrap_pyfunction!(
        djust_templates::registry::register_assign_tag_handler,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        djust_templates::registry::unregister_assign_tag_handler,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        djust_templates::registry::has_assign_tag_handler,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        djust_templates::registry::clear_assign_tag_handlers,
        m
    )?)?;

    Ok(())
}
