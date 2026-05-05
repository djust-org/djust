//! Template renderer that converts AST nodes to output strings

use crate::filters;
use crate::inheritance::TemplateLoader;
use crate::parser::Node;
use djust_components::Component;
use djust_core::{Context, DjangoRustError, Result, Value};
use once_cell::sync::Lazy;
use regex::Regex;
use std::collections::HashSet;

/// Regex for {% spaceless %}: matches whitespace between > and <
static SPACELESS_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r">\s+<").unwrap());

/// Returns ``true`` if the (parser-preserved) filter argument string is a
/// quoted literal — i.e. starts and ends with matching single or double
/// quotes. Used to drive the custom-filter fallback's arg-resolution
/// policy (#1121): quoted args are passed through as literals; bare
/// identifiers are first resolved against the template context.
fn is_quoted_arg(arg: &str) -> bool {
    arg.len() >= 2
        && ((arg.starts_with('"') && arg.ends_with('"'))
            || (arg.starts_with('\'') && arg.ends_with('\'')))
}

/// Returns ``true`` if any node in `nodes` may contribute element-level
/// HTML output (as opposed to text-only output). Used by the `Node::If`
/// renderer to decide whether to emit `<!--dj-if id="if-N"-->`/
/// `<!--/dj-if-->` boundary markers (Iter 1 of issue #1358).
///
/// Pure-text conditionals — branches that emit only `Node::Text`
/// fragments without HTML tags, `Node::Variable` (escaped output),
/// or `Node::InlineIf` (text expression) — don't need keyed
/// boundaries because text positions are inherently sibling-stable
/// in the rendered DOM. Element-bearing branches do, because the
/// VDOM differ in Iter 3 will key off these markers when
/// conditionals flip and subtree shapes change.
///
/// Conservative classification: any AST node that can possibly
/// produce a `<` character in its output (Custom tags, Components,
/// Block, Include, Static, etc.) is treated as element-bearing.
/// Misclassifying an exotic text-only path as element-bearing only
/// emits redundant comments (which browsers ignore) — the safe
/// direction.
fn nodes_contain_elements(nodes: &[Node]) -> bool {
    nodes.iter().any(node_is_element_bearing)
}

fn node_is_element_bearing(node: &Node) -> bool {
    match node {
        // Text nodes only contribute elements when their literal
        // content includes a `<`. A `<` strongly implies an HTML
        // tag; a `<` inside textual prose like "if 3 < 4" would
        // still be classified as element-bearing here, which is
        // safe (extra comment — no observable effect).
        Node::Text(s) => s.contains('<'),
        // Variable substitution is HTML-escaped at render time —
        // never produces raw element content.
        Node::Variable(_, _, _) => false,
        // Inline-if produces a single text expression (escaped or
        // not, but no structural HTML).
        Node::InlineIf { .. } => false,
        // Comments never contribute elements.
        Node::Comment => false,
        // `{% csrf_token %}` renders an `<input type="hidden" ...>`
        // element when a token is present (`renderer.rs` line ~750).
        // It MUST be classified as element-bearing so
        // `{% if request.method == "POST" %}{% csrf_token %}{% endif %}`
        // emits boundary markers (Stage 11 MUST-FIX on PR #1363).
        // It can render as the empty string when the context has no
        // token (LiveView re-renders without request context — see
        // #696), but the classifier is conservative: emitting an
        // unused marker pair is harmless (browsers ignore comments)
        // while a missed marker breaks Iter 3's differ.
        Node::CsrfToken => true,
        // Other static text-emitting tags — none produce elements
        // unless the user template itself surrounds them with HTML
        // (which would appear in adjacent Text nodes).
        Node::Now(_)
        | Node::WidthRatio { .. }
        | Node::FirstOf { .. }
        | Node::TemplateTag(_)
        | Node::Cycle { .. }
        | Node::Load(_)
        | Node::Extends(_)
        | Node::AssignTag { .. } => false,
        // Recurse into branches.
        Node::If {
            true_nodes,
            false_nodes,
            ..
        } => nodes_contain_elements(true_nodes) || nodes_contain_elements(false_nodes),
        Node::For {
            nodes, empty_nodes, ..
        } => nodes_contain_elements(nodes) || nodes_contain_elements(empty_nodes),
        Node::Block { nodes, .. } => nodes_contain_elements(nodes),
        Node::With { nodes, .. } => nodes_contain_elements(nodes),
        Node::Spaceless { nodes, .. } => nodes_contain_elements(nodes),
        // Conservative: tags that may or do produce HTML are treated
        // as element-bearing. Includes templates, components, and
        // any custom-rendered output the framework can't introspect.
        Node::Static(_)
        | Node::Include { .. }
        | Node::ReactComponent { .. }
        | Node::RustComponent { .. }
        | Node::CustomTag { .. }
        | Node::BlockCustomTag { .. }
        | Node::UnsupportedTag { .. } => true,
    }
}

pub fn render_nodes(nodes: &[Node], context: &Context) -> Result<String> {
    render_nodes_with_loader(nodes, context, None::<&NoOpLoader>)
}

/// Render nodes with an optional template loader for {% include %} support.
///
/// Supports `Node::AssignTag` by lazily cloning the incoming
/// `&Context` into an owned, mutable context the first time an
/// assign tag is encountered. All subsequent sibling nodes see the
/// assigned variables. Siblings preceding the assign tag are
/// rendered with the original (unmutated) context.
pub fn render_nodes_with_loader<L: TemplateLoader>(
    nodes: &[Node],
    context: &Context,
    loader: Option<&L>,
) -> Result<String> {
    let mut output = String::new();
    // Lazily materialised mutable copy of the context for assign-tag
    // effects. `None` until an assign tag forces a clone.
    let mut mutated: Option<Context> = None;

    for node in nodes {
        // Pick which context this node renders against.
        let active_ctx: &Context = match &mutated {
            Some(c) => c,
            None => context,
        };

        match node {
            Node::AssignTag { name, args } => {
                // Resolve variable references in args using the same
                // semantics as `Node::CustomTag`.
                let resolved_args: Vec<String> = args
                    .iter()
                    .map(|arg| resolve_tag_arg(arg, active_ctx))
                    .collect();

                let context_map = active_ctx.to_hashmap();
                // Forward the raw-Python sidecar so assign handlers
                // can reach Python-only context (request, view) the
                // same way `Node::CustomTag` handlers do (#1167).
                let raw_py = active_ctx.raw_py_objects();
                let updates = crate::registry::call_assign_handler_with_py_sidecar(
                    name,
                    &resolved_args,
                    &context_map,
                    raw_py,
                )
                .map_err(|e| {
                    DjangoRustError::TemplateError(format!("Assign tag '{name}' error: {e}"))
                })?;

                // Promote to owned context if we haven't already, then
                // merge the handler's returned dict.
                if mutated.is_none() {
                    mutated = Some(active_ctx.clone());
                }
                if let Some(ctx) = mutated.as_mut() {
                    for (k, v) in updates {
                        ctx.set(k, v);
                    }
                }
                // Assign tags emit no HTML.
            }
            _ => {
                output.push_str(&render_node_with_loader(node, active_ctx, loader)?);
            }
        }
    }

    Ok(output)
}

/// Resolve a tag argument the same way `Node::CustomTag` does.
///
/// - Quoted string literals are returned unchanged.
/// - `key=value` pairs resolve `value` against the context.
/// - Bare names are looked up in the context.
///
/// Used by both `CustomTag` (via its inline logic) and `AssignTag`.
fn resolve_tag_arg(arg: &str, context: &Context) -> String {
    let arg_trimmed = arg.trim();
    if (arg_trimmed.starts_with('"') && arg_trimmed.ends_with('"'))
        || (arg_trimmed.starts_with('\'') && arg_trimmed.ends_with('\''))
    {
        return arg.to_string();
    }
    if let Some(eq_pos) = arg.find('=') {
        let key = &arg[..eq_pos];
        let value = arg[eq_pos + 1..].trim();
        if (value.starts_with('"') && value.ends_with('"'))
            || (value.starts_with('\'') && value.ends_with('\''))
        {
            return arg.to_string();
        }
        return match context.get(value) {
            Some(resolved) => format!("{}={}", key, resolved),
            None => arg.to_string(),
        };
    }
    match context.get(arg_trimmed) {
        Some(resolved) => resolved.to_string(),
        None => arg.to_string(),
    }
}

/// Render all nodes and return full HTML plus per-node fragments.
///
/// Used on the first render to populate the per-node HTML cache.
/// Like [`render_nodes_with_loader`], supports context-mutating
/// [`Node::AssignTag`] siblings.
pub fn render_nodes_collecting<L: TemplateLoader>(
    nodes: &[Node],
    context: &Context,
    loader: Option<&L>,
) -> Result<(String, Vec<String>)> {
    let mut full_output = String::new();
    let mut fragments = Vec::with_capacity(nodes.len());
    let mut mutated: Option<Context> = None;

    for node in nodes {
        let active_ctx: &Context = match &mutated {
            Some(c) => c,
            None => context,
        };

        let frag = match node {
            Node::AssignTag { name, args } => {
                let resolved_args: Vec<String> = args
                    .iter()
                    .map(|arg| resolve_tag_arg(arg, active_ctx))
                    .collect();
                let context_map = active_ctx.to_hashmap();
                // Forward raw-Python sidecar (#1167).
                let raw_py = active_ctx.raw_py_objects();
                let updates = crate::registry::call_assign_handler_with_py_sidecar(
                    name,
                    &resolved_args,
                    &context_map,
                    raw_py,
                )
                .map_err(|e| {
                    DjangoRustError::TemplateError(format!("Assign tag '{name}' error: {e}"))
                })?;
                if mutated.is_none() {
                    mutated = Some(active_ctx.clone());
                }
                if let Some(ctx) = mutated.as_mut() {
                    for (k, v) in updates {
                        ctx.set(k, v);
                    }
                }
                String::new()
            }
            _ => render_node_with_loader(node, active_ctx, loader)?,
        };
        full_output.push_str(&frag);
        fragments.push(frag);
    }
    Ok((full_output, fragments))
}

/// Partial render: only re-render nodes whose deps overlap `changed_keys`.
///
/// Returns `(full_html, new_fragments, changed_indices)`.
/// Nodes whose deps are disjoint from `changed_keys` reuse their cached HTML.
pub fn render_nodes_partial<L: TemplateLoader>(
    nodes: &[Node],
    node_deps: &[HashSet<String>],
    context: &Context,
    loader: Option<&L>,
    changed_keys: &HashSet<String>,
    node_html_cache: &[String],
) -> Result<(String, Vec<String>, Vec<usize>)> {
    let mut full_output = String::new();
    let mut fragments = Vec::with_capacity(nodes.len());
    let mut changed_indices = Vec::new();
    // AssignTag produces `"*"` in its dep set (see extract_from_nodes)
    // so it always re-renders on any change; mutations propagate to
    // subsequent siblings via this optional cloned context.
    let mut mutated: Option<Context> = None;

    for (i, node) in nodes.iter().enumerate() {
        let active_ctx: &Context = match &mutated {
            Some(c) => c,
            None => context,
        };

        let needs_render = if let Some(deps) = node_deps.get(i) {
            deps.contains("*")
                || i >= node_html_cache.len()
                || deps.iter().any(|dep| changed_keys.contains(dep))
        } else {
            true
        };

        if needs_render {
            let html = match node {
                Node::AssignTag { name, args } => {
                    let resolved_args: Vec<String> = args
                        .iter()
                        .map(|arg| resolve_tag_arg(arg, active_ctx))
                        .collect();
                    let context_map = active_ctx.to_hashmap();
                    // Forward raw-Python sidecar (#1167).
                    let raw_py = active_ctx.raw_py_objects();
                    let updates = crate::registry::call_assign_handler_with_py_sidecar(
                        name,
                        &resolved_args,
                        &context_map,
                        raw_py,
                    )
                    .map_err(|e| {
                        DjangoRustError::TemplateError(format!("Assign tag '{name}' error: {e}"))
                    })?;
                    if mutated.is_none() {
                        mutated = Some(active_ctx.clone());
                    }
                    if let Some(ctx) = mutated.as_mut() {
                        for (k, v) in updates {
                            ctx.set(k, v);
                        }
                    }
                    String::new()
                }
                _ => render_node_with_loader(node, active_ctx, loader)?,
            };
            full_output.push_str(&html);
            fragments.push(html);
            changed_indices.push(i);
        } else {
            full_output.push_str(&node_html_cache[i]);
            fragments.push(node_html_cache[i].clone());
        }
    }

    Ok((full_output, fragments, changed_indices))
}

/// No-op loader for when no loader is provided
struct NoOpLoader;

impl TemplateLoader for NoOpLoader {
    fn load_template(&self, _name: &str) -> Result<Vec<Node>> {
        Err(DjangoRustError::TemplateError(
            "Template loader not configured".to_string(),
        ))
    }
}

pub fn render_node_with_loader<L: TemplateLoader>(
    node: &Node,
    context: &Context,
    loader: Option<&L>,
) -> Result<String> {
    match node {
        Node::Text(text) => Ok(text.clone()),

        Node::Variable(var_name, filter_specs, in_attr) => {
            // `resolve` tries the normal value-stack path first, then
            // falls back to `getattr` on any PyObject sidecar attached
            // to the context (e.g. Django model instances).
            let mut value = context.resolve(var_name).unwrap_or(Value::Null);

            // Apply filters (pass context so date/time can read DATE_FORMAT etc.)
            for (filter_name, arg) in filter_specs {
                // Strip quotes from literal filter args at render time —
                // the parser preserves quotes so the dep-tracking
                // extractor can tell literals from bare identifiers
                // (issue #787). The quoting hint is preserved so the
                // custom-filter fallback (#1121) knows whether a bare
                // identifier should be context-resolved.
                let original = arg.as_deref();
                let arg_was_quoted = original.map(is_quoted_arg).unwrap_or(false);
                let stripped = original.map(crate::parser::strip_filter_arg_quotes);
                value = filters::apply_filter_full(
                    filter_name,
                    &value,
                    stripped,
                    Some(context),
                    arg_was_quoted,
                )?;
            }

            let text = value.to_string();

            // Auto-escape unless:
            // 1. |safe is the last filter (matches Django behavior)
            // 2. The variable is marked safe in the context (like Django's SafeData)
            // 3. A filter that produces already-escaped/safe output is in the chain
            //    (built-in safe_output_filters list OR a custom filter
            //    registered with ``is_safe=True`` per #1121).
            let safe_output_filters = [
                "safe",
                "safeseq",
                "force_escape",
                "json_script",
                "urlize",
                "urlizetrunc",
                "unordered_list",
            ];
            let is_safe = filter_specs.iter().any(|(name, _)| {
                safe_output_filters.contains(&name.as_str())
                    || crate::filter_registry::is_custom_filter_safe(name)
            }) || context.is_safe(var_name);
            if is_safe {
                Ok(text)
            } else if *in_attr {
                // Attribute-context escape: handles `"` → `&quot;`
                // and `'` → `&#x27;` in addition to the base
                // `&`/`<`/`>` escapes, so quoted attribute values
                // like `<a href="{{ url }}">` never break when the
                // value itself contains a quote.
                Ok(filters::html_escape_attr(&text))
            } else {
                Ok(filters::html_escape(&text))
            }
        }

        Node::InlineIf {
            true_expr,
            condition,
            false_expr,
            filters,
        } => {
            let expr = if evaluate_condition(condition, context)? {
                true_expr.as_str()
            } else {
                false_expr.as_str()
            };

            let mut value = get_value(expr, context)?;

            for (filter_name, arg) in filters {
                let original = arg.as_deref();
                let arg_was_quoted = original.map(is_quoted_arg).unwrap_or(false);
                let stripped = original.map(crate::parser::strip_filter_arg_quotes);
                value = filters::apply_filter_full(
                    filter_name,
                    &value,
                    stripped,
                    Some(context),
                    arg_was_quoted,
                )?;
            }

            let text = value.to_string();
            let safe_output_filters = [
                "safe",
                "safeseq",
                "force_escape",
                "json_script",
                "urlize",
                "urlizetrunc",
                "unordered_list",
            ];
            let is_safe = filters.iter().any(|(name, _)| {
                safe_output_filters.contains(&name.as_str())
                    || crate::filter_registry::is_custom_filter_safe(name)
            }) || context.is_safe(expr);
            if is_safe {
                Ok(text)
            } else {
                Ok(filters::html_escape(&text))
            }
        }

        Node::If {
            condition,
            true_nodes,
            false_nodes,
            in_tag_context,
            marker_id,
        } => {
            let condition_result = evaluate_condition(condition, context)?;

            // Render the body that fires (truthy/falsy branch).
            let body = if condition_result {
                render_nodes_with_loader(true_nodes, context, loader)?
            } else if false_nodes.is_empty() {
                if *in_tag_context {
                    // Inside an HTML attribute value: a comment node would produce
                    // malformed HTML (e.g. class="btn <!--dj-if-->"). Emit empty
                    // string instead. Fix for issue #380.
                    String::new()
                } else if !nodes_contain_elements(true_nodes)
                    && !nodes_contain_elements(false_nodes)
                {
                    // Pure-text conditional with no else: keep the legacy
                    // single-comment placeholder (issue #295 / DJE-053).
                    // Element-bearing branches drop into the dj-if pair
                    // path below, which serves the same sibling-stability
                    // role (closing tag adjacent to opening).
                    "<!--dj-if-->".to_string()
                } else {
                    // Element-bearing if with no else and false condition:
                    // emit empty body inside the wrapping pair below.
                    String::new()
                }
            } else {
                render_nodes_with_loader(false_nodes, context, loader)?
            };

            // Decide whether to wrap in `<!--dj-if id="if-N"-->` /
            // `<!--/dj-if-->` boundary markers. Wrap iff:
            //   - NOT in an HTML attribute context (comments would
            //     break attribute strings, issue #380).
            //   - At least one branch is element-bearing (text-only
            //     conditionals don't need keyed boundaries — text
            //     positions are sibling-stable already).
            //   - The parser assigned a marker_id (production
            //     templates always go through `parser::parse()`
            //     which assigns IDs in document order).
            //
            // Foundation 1 of 3 toward issue #1358 (keyed VDOM diff
            // for conditional subtrees, re-open of #256 Option A).
            // Iter 2 (client patch applier) and Iter 3 (Rust VDOM
            // differ) follow in subsequent PRs. The markers are
            // metadata only — browsers ignore HTML comments — so
            // this iter is zero-observable-behavior.
            if !*in_tag_context
                && (nodes_contain_elements(true_nodes) || nodes_contain_elements(false_nodes))
            {
                if let Some(id) = marker_id {
                    return Ok(format!("<!--dj-if id=\"{id}\"-->{body}<!--/dj-if-->"));
                }
            }

            Ok(body)
        }

        Node::For {
            var_names,
            iterable,
            reversed,
            nodes,
            empty_nodes,
        } => {
            // Use context.resolve() so dotted iterables walk getattr
            // across Python/Django model boundaries (e.g. `{% for x in
            // user.orders %}` resolving through a DB relation). Issue
            // #806 — previously only context.get() was consulted, which
            // only hits the value-stack so getattr-backed iterables
            // silently rendered as empty.
            let iterable_value = context
                .resolve(iterable)
                .or_else(|| context.get(iterable).cloned())
                .unwrap_or(Value::Null);

            match iterable_value {
                Value::List(items) => {
                    // If list is empty, render the {% empty %} block
                    if items.is_empty() {
                        return render_nodes_with_loader(empty_nodes, context, loader);
                    }

                    let mut output = String::new();
                    let mut ctx = context.clone();

                    // Create an iterator with indices, reversing if needed
                    let items_vec = items;
                    let indices_and_items: Vec<(usize, Value)> = if *reversed {
                        items_vec.into_iter().enumerate().rev().collect()
                    } else {
                        items_vec.into_iter().enumerate().collect()
                    };

                    // Save outer cycle counter for nested loop support
                    let saved_cycle_counter = ctx.get("__djust_cycle_counter").cloned();

                    for (counter, (index, item)) in indices_and_items.into_iter().enumerate() {
                        // Set __djust_cycle_counter for {% cycle %} tag support
                        ctx.set(
                            "__djust_cycle_counter".to_string(),
                            Value::Integer(counter as i64),
                        );

                        // Handle tuple unpacking: {% for a, b in items %}
                        if var_names.len() == 1 {
                            // Single variable: {% for item in items %}
                            ctx.set(var_names[0].clone(), item);
                            // Track loop mapping for safe key resolution
                            ctx.set_loop_mapping(var_names[0].clone(), iterable.clone(), index);
                        } else {
                            // Multiple variables: {% for key, value in items %}
                            // Expect item to be a list/tuple
                            match &item {
                                Value::List(tuple_items) => {
                                    // Unpack tuple items into separate variables
                                    for (i, var_name) in var_names.iter().enumerate() {
                                        if i < tuple_items.len() {
                                            ctx.set(var_name.clone(), tuple_items[i].clone());
                                        } else {
                                            // If tuple has fewer items than var names, set to Null
                                            ctx.set(var_name.clone(), Value::Null);
                                        }
                                    }
                                }
                                _ => {
                                    // If item is not a list, set all vars to Null except first
                                    ctx.set(var_names[0].clone(), item.clone());
                                    for var_name in &var_names[1..] {
                                        ctx.set(var_name.clone(), Value::Null);
                                    }
                                }
                            }
                        }
                        output.push_str(&render_nodes_with_loader(nodes, &ctx, loader)?);
                    }

                    // Restore outer cycle counter (for nested loops)
                    if let Some(saved) = saved_cycle_counter {
                        ctx.set("__djust_cycle_counter".to_string(), saved);
                    }

                    // Clear loop mappings after the loop
                    for var_name in var_names {
                        ctx.clear_loop_mapping(var_name);
                    }

                    Ok(output)
                }
                _ => {
                    // If not a list (null, etc.), render the empty block
                    render_nodes_with_loader(empty_nodes, context, loader)
                }
            }
        }

        Node::Block { name: _, nodes } => {
            // For now, just render the block content
            // In a full implementation, this would handle template inheritance
            render_nodes_with_loader(nodes, context, loader)
        }

        Node::Include {
            template,
            with_vars,
            only,
        } => {
            // Load and render the included template
            if let Some(loader) = loader {
                // Remove quotes from template name if present
                let name = template.trim_matches(|c| c == '"' || c == '\'');
                let nodes = loader.load_template(name)?;

                // Create context for included template
                let mut include_context = if *only {
                    // Only use with_vars, not parent context
                    Context::new()
                } else {
                    // Start with parent context
                    context.clone()
                };

                // Apply with_vars assignments
                for (key, value_expr) in with_vars {
                    // Resolve value from parent context or use as literal
                    let value = context.get(value_expr).cloned().unwrap_or_else(|| {
                        // Check if it's a string literal
                        if (value_expr.starts_with('"') && value_expr.ends_with('"'))
                            || (value_expr.starts_with('\'') && value_expr.ends_with('\''))
                        {
                            Value::String(value_expr[1..value_expr.len() - 1].to_string())
                        } else {
                            Value::String(value_expr.clone())
                        }
                    });
                    include_context.set(key.clone(), value);
                }

                render_nodes_with_loader(&nodes, &include_context, Some(loader))
            } else {
                // No loader available — silently omit ({% include %} without a loader
                // is valid in tests where only a fragment is rendered)
                Ok(String::new())
            }
        }

        Node::ReactComponent {
            name,
            props,
            children,
        } => {
            // Render React component as data attributes for client-side hydration
            let mut output = String::new();
            output.push_str(&format!("<div data-react-component=\"{name}\""));

            // Add props as data attributes
            if !props.is_empty() {
                output.push_str(" data-react-props='");
                let props_json: Vec<String> = props
                    .iter()
                    .map(|(k, v)| {
                        // Resolve Django template variable syntax: {{ var.path }}
                        let resolved_value = if v.starts_with("{{") && v.ends_with("}}") {
                            // Extract variable name from {{ ... }}
                            let var_name = v.trim_start_matches("{{").trim_end_matches("}}").trim();

                            // Try to resolve from context
                            if let Some(ctx_value) = context.get(var_name) {
                                ctx_value.to_string()
                            } else {
                                // Keep the original template syntax for Python-side resolution
                                v.clone()
                            }
                        } else if let Some(ctx_value) = context.get(v) {
                            // Direct variable reference (no {{ }})
                            ctx_value.to_string()
                        } else {
                            v.clone()
                        };
                        format!("\"{}\":\"{}\"", k, resolved_value.replace('"', "\\\""))
                    })
                    .collect();
                output.push_str(&format!("{{{}}}", props_json.join(",")));
                output.push('\'');
            }

            output.push('>');

            // Render children
            for child in children {
                output.push_str(&render_node_with_loader(child, context, loader)?);
            }

            output.push_str("</div>");
            Ok(output)
        }

        Node::RustComponent { name, props } => {
            // Render Rust component server-side
            render_rust_component(name, props, context)
        }

        Node::CsrfToken => {
            // Render CSRF token hidden input if a real token is available.
            // When no token is in context (e.g., LiveView re-render without
            // request context), render nothing so client.js falls through to
            // reading the CSRF cookie instead. Previously rendered a
            // "CSRF_TOKEN_NOT_PROVIDED" placeholder that poisoned client.js's
            // CSRF lookup, causing HTTP fallback 403 errors. (#696)
            let token = context
                .get("csrf_token")
                .map(|v| v.to_string())
                .filter(|t| !t.is_empty());

            match token {
                Some(t) => {
                    let escaped = filters::html_escape(&t);
                    Ok(format!(
                        "<input type=\"hidden\" name=\"csrfmiddlewaretoken\" value=\"{escaped}\">"
                    ))
                }
                None => Ok(String::new()),
            }
        }

        Node::Static(path) => {
            // Render static file URL
            // Get STATIC_URL from context (should be provided by Django)
            let static_url = context
                .get("STATIC_URL")
                .map(|v| v.to_string())
                .unwrap_or_else(|| "/static/".to_string());

            Ok(format!("{static_url}{path}"))
        }

        Node::With { assignments, nodes } => {
            // Create new context with assigned variables
            let mut new_context = context.clone();

            // Process assignments
            for (var_name, expression) in assignments {
                // Try to evaluate expression from context
                // For now, we'll just look up the expression as a variable name
                // In full Django, this would support complex expressions
                let value = context
                    .get(expression)
                    .cloned()
                    .unwrap_or_else(|| Value::String(expression.clone()));
                new_context.set(var_name.clone(), value);
            }

            // Render children with new context
            render_nodes_with_loader(nodes, &new_context, loader)
        }

        Node::Extends(_) => {
            // Extends should be handled at template level, not during node rendering
            // This is a marker node that triggers inheritance processing
            Err(DjangoRustError::TemplateError(
                "{% extends %} must be processed at template level, not during rendering"
                    .to_string(),
            ))
        }

        Node::Comment => Ok(String::new()),
        Node::Load(_) => Ok(String::new()), // No-op at render time; preserved for reconstruction

        Node::WidthRatio {
            value,
            max_value,
            max_width,
        } => {
            // {% widthratio value max_value max_width %} → round(value / max_value * max_width)
            let val = get_value(value, context)?.to_f64().unwrap_or(0.0);
            let max_val = get_value(max_value, context)?.to_f64().unwrap_or(0.0);
            let max_w = get_value(max_width, context)?.to_f64().unwrap_or(0.0);

            if max_val == 0.0 {
                Ok("0".to_string())
            } else {
                let result = (val / max_val * max_w).round() as i64;
                Ok(result.to_string())
            }
        }

        Node::FirstOf { args } => {
            // {% firstof var1 var2 ... "fallback" %} → first truthy value
            // Uses get_value for dotted path support (e.g., user.name)
            for arg in args {
                let val = get_value(arg.trim(), context)?;
                if val.is_truthy() {
                    return Ok(filters::html_escape(&val.to_string()));
                }
            }
            Ok(String::new())
        }

        Node::TemplateTag(name) => {
            // {% templatetag openblock %} → {%
            let output = match name.as_str() {
                "openblock" => "{%",
                "closeblock" => "%}",
                "openvariable" => "{{",
                "closevariable" => "}}",
                "openbrace" => "{",
                "closebrace" => "}",
                "opencomment" => "{#",
                "closecomment" => "#}",
                _ => {
                    return Err(DjangoRustError::TemplateError(format!(
                        "Unknown templatetag argument: '{name}'"
                    )));
                }
            };
            Ok(output.to_string())
        }

        Node::Spaceless { nodes } => {
            // {% spaceless %}...{% endspaceless %} → remove whitespace between HTML tags
            let content = render_nodes_with_loader(nodes, context, loader)?;
            // Remove whitespace between > and <
            Ok(SPACELESS_RE.replace_all(&content, "><").to_string())
        }

        Node::Cycle { values, name: _ } => {
            // {% cycle val1 val2 ... %} → cycles through values using __djust_cycle_counter
            // Named cycles (as name) are parsed but silent references are unsupported
            // (renderer receives &Context, can't store cycle state).
            // Note: cycle outside a for loop always returns the first value (no counter).
            if values.is_empty() {
                return Ok(String::new());
            }
            let counter = context
                .get("__djust_cycle_counter")
                .and_then(|v| match v {
                    Value::Integer(i) => Some(*i as usize),
                    _ => None,
                })
                .unwrap_or(0);
            let idx = counter % values.len();
            let val = &values[idx];
            // Resolve via get_value for dotted path and literal support
            let resolved = get_value(val.trim(), context)?;
            let output = if matches!(resolved, Value::Null) {
                // Unresolved variable — output the raw name (Django behavior)
                filters::html_escape(val.trim())
            } else {
                filters::html_escape(&resolved.to_string())
            };
            // Named cycles ({% cycle ... as name %}) are parsed but the name is not
            // stored in context — the renderer receives &Context (immutable). The cycle
            // value is still computed correctly each iteration; only the "silent reference"
            // form ({% cycle name %} outside the cycle definition) is unsupported.
            Ok(output)
        }

        Node::Now(format) => {
            // {% now "Y-m-d" %} → current date/time
            let now = chrono::Local::now();
            Ok(django_date_format(&now, format))
        }

        Node::UnsupportedTag { name, args } => {
            // Build tag signature for error message
            let args_str = if args.is_empty() {
                String::new()
            } else {
                format!(" {}", args.join(" "))
            };
            let tag_sig = format!("{{% {name}{args_str} %}}");

            // Return an error so callers can fall back to Django's template engine.
            // Previously this output an HTML comment, which produced silently wrong
            // output. Raising an error allows Python wrappers with Django fallback
            // (e.g. _render_template_with_fallback) to recover gracefully.
            Err(DjangoRustError::TemplateError(format!(
                "Unsupported template tag '{tag_sig}'. \
                 Register a handler via djust._rust.register_tag_handler(), \
                 or use Django's template engine instead."
            )))
        }

        Node::BlockCustomTag {
            name,
            args,
            children,
        } => {
            // Render children first to get block content
            let content = render_nodes_with_loader(children, context, loader)?;

            // Resolve variable references in args (same as CustomTag below)
            let resolved_args: Vec<String> = args
                .iter()
                .map(|arg| {
                    let arg_trimmed = arg.trim();
                    if (arg_trimmed.starts_with('"') && arg_trimmed.ends_with('"'))
                        || (arg_trimmed.starts_with('\'') && arg_trimmed.ends_with('\''))
                    {
                        arg.clone()
                    } else if let Some(eq_pos) = arg.find('=') {
                        let key = &arg[..eq_pos];
                        let value = arg[eq_pos + 1..].trim();
                        if (value.starts_with('"') && value.ends_with('"'))
                            || (value.starts_with('\'') && value.ends_with('\''))
                        {
                            arg.clone()
                        } else {
                            match context.get(value) {
                                Some(resolved) => format!("{}={}", key, resolved),
                                None => arg.clone(),
                            }
                        }
                    } else {
                        match context.get(arg_trimmed) {
                            Some(resolved) => resolved.to_string(),
                            None => arg.clone(),
                        }
                    }
                })
                .collect();

            let context_map = context.to_hashmap();

            // Forward raw-Python sidecar so block handlers can reach
            // Python-only context (request, view) the same way
            // ``Node::CustomTag`` handlers do (#1167).
            let raw_py = context.raw_py_objects();
            crate::registry::call_block_handler_with_py_sidecar(
                name,
                &resolved_args,
                &content,
                &context_map,
                raw_py,
            )
            .map_err(|e| {
                DjangoRustError::TemplateError(format!("Block tag '{}' error: {}", name, e))
            })
        }

        Node::AssignTag { name, args } => {
            // When an AssignTag is rendered individually (outside of
            // render_nodes_with_loader's sibling-aware loop) we still
            // invoke the handler for its side-effects but discard the
            // result — there's no way to propagate context mutations
            // without a sibling to pass them to. Emits empty string.
            let resolved_args: Vec<String> = args
                .iter()
                .map(|arg| resolve_tag_arg(arg, context))
                .collect();
            let context_map = context.to_hashmap();
            // Forward raw-Python sidecar (#1167).
            let raw_py = context.raw_py_objects();
            crate::registry::call_assign_handler_with_py_sidecar(
                name,
                &resolved_args,
                &context_map,
                raw_py,
            )
            .map_err(|e| {
                DjangoRustError::TemplateError(format!("Assign tag '{name}' error: {e}"))
            })?;
            Ok(String::new())
        }

        Node::CustomTag { name, args } => {
            // Call Python handler for custom tags (e.g., {% url %}, {% static %})
            //
            // The handler is looked up in the registry and called with:
            // - args: The raw arguments from the template tag
            // - context: The current template context (converted to Python dict)
            //
            // The handler must return a string to be inserted in the output.

            // First, resolve any variable references in args.
            // For scalar values (strings, ints, floats, bools) we inline
            // the value.  For lists and objects we serialize to JSON so the
            // Python handler can recover the structured data from the arg
            // string (plain `.to_string()` would produce the opaque
            // placeholders "[List]" / "[Object]").
            fn value_to_arg_string(v: &Value) -> String {
                match v {
                    Value::List(_) | Value::Object(_) => {
                        serde_json::to_string(v).unwrap_or_else(|_| v.to_string())
                    }
                    _ => v.to_string(),
                }
            }

            let resolved_args: Vec<String> = args
                .iter()
                .map(|arg| {
                    // Check if arg is a variable reference (not a string literal)
                    let arg_trimmed = arg.trim();
                    if (arg_trimmed.starts_with('"') && arg_trimmed.ends_with('"'))
                        || (arg_trimmed.starts_with('\'') && arg_trimmed.ends_with('\''))
                    {
                        // String literal - keep as-is
                        arg.clone()
                    } else if let Some(eq_pos) = arg.find('=') {
                        // Named parameter: key=value
                        let key = &arg[..eq_pos];
                        let value = arg[eq_pos + 1..].trim();
                        if (value.starts_with('"') && value.ends_with('"'))
                            || (value.starts_with('\'') && value.ends_with('\''))
                        {
                            // Value is a string literal
                            arg.clone()
                        } else {
                            // Value is a variable (possibly with filters) - try to resolve
                            match get_value(value, context) {
                                Ok(resolved) => {
                                    format!("{}={}", key, value_to_arg_string(&resolved))
                                }
                                Err(_) => arg.clone(),
                            }
                        }
                    } else {
                        // Might be a variable (possibly with filters) - try to resolve
                        match get_value(arg_trimmed, context) {
                            Ok(resolved) => value_to_arg_string(&resolved),
                            Err(_) => arg.clone(),
                        }
                    }
                })
                .collect();

            // Convert context to HashMap for the handler
            let context_map = context.to_hashmap();

            // Call the Python handler. We forward the optional
            // raw-Python sidecar (``request``, ``view``, …) so handlers
            // like ``live_render`` (#1145) that need access to Python
            // objects in the parent's render context can pick them up
            // from the ``context`` dict alongside the JSON-friendly
            // values. Existing handlers ignore extra keys so this is
            // backward compatible.
            let raw_py = context.raw_py_objects();
            crate::registry::call_handler_with_py_sidecar(
                name,
                &resolved_args,
                &context_map,
                raw_py,
            )
            .map_err(|e| {
                DjangoRustError::TemplateError(format!("Custom tag '{}' error: {}", name, e))
            })
        }
    }
}

/// Render a Rust component by instantiating it and calling its render method
fn render_rust_component(
    name: &str,
    props: &[(String, String)],
    context: &Context,
) -> Result<String> {
    // Get framework from context or default to Bootstrap5
    let framework = context
        .get("_framework")
        .and_then(|v| {
            if let Value::String(s) = v {
                Some(s.as_str())
            } else {
                None
            }
        })
        .unwrap_or("bootstrap5");

    let fw = framework.parse().unwrap();

    // Match component name and instantiate
    match name {
        "RustButton" => {
            // Extract required props
            let id = get_prop("id", props, context)?;
            let label = get_prop("label", props, context)?;

            // Create button with basic props
            let mut button = djust_components::ui::Button::new(id, label);

            // Apply optional props
            if let Ok(variant_str) = get_prop("variant", props, context) {
                let variant = match variant_str.as_str() {
                    "secondary" => djust_components::ui::button::ButtonVariant::Secondary,
                    "success" => djust_components::ui::button::ButtonVariant::Success,
                    "danger" => djust_components::ui::button::ButtonVariant::Danger,
                    "warning" => djust_components::ui::button::ButtonVariant::Warning,
                    "info" => djust_components::ui::button::ButtonVariant::Info,
                    "light" => djust_components::ui::button::ButtonVariant::Light,
                    "dark" => djust_components::ui::button::ButtonVariant::Dark,
                    "link" => djust_components::ui::button::ButtonVariant::Link,
                    _ => djust_components::ui::button::ButtonVariant::Primary,
                };
                button.variant = variant;
            }

            if let Ok(size_str) = get_prop("size", props, context) {
                let size = match size_str.as_str() {
                    "sm" | "small" => djust_components::ui::button::ButtonSize::Small,
                    "lg" | "large" => djust_components::ui::button::ButtonSize::Large,
                    _ => djust_components::ui::button::ButtonSize::Medium,
                };
                button.size = size;
            }

            if let Ok(outline) = get_prop("outline", props, context) {
                button.outline = outline == "true" || outline == "True";
            }

            if let Ok(disabled) = get_prop("disabled", props, context) {
                button.disabled = disabled == "true" || disabled == "True";
            }

            if let Ok(full_width) = get_prop("fullWidth", props, context) {
                button.full_width = full_width == "true" || full_width == "True";
            }

            if let Ok(icon) = get_prop("icon", props, context) {
                button.icon = Some(icon);
            }

            if let Ok(on_click) = get_prop("onClick", props, context) {
                button.on_click = Some(on_click);
            }

            // Render the component
            button.render(fw).map_err(|e| {
                DjangoRustError::TemplateError(format!("Failed to render RustButton: {e}"))
            })
        }

        "RustInput" => {
            // Extract required props
            let id = get_prop("id", props, context)?;

            // Create input with basic props
            let mut input = djust_components::ui::Input::new(id);

            // Apply optional props
            if let Ok(input_type_str) = get_prop("inputType", props, context) {
                let input_type = match input_type_str.as_str() {
                    "email" => djust_components::ui::input::InputType::Email,
                    "password" => djust_components::ui::input::InputType::Password,
                    "number" => djust_components::ui::input::InputType::Number,
                    "tel" => djust_components::ui::input::InputType::Tel,
                    "url" => djust_components::ui::input::InputType::Url,
                    "search" => djust_components::ui::input::InputType::Search,
                    "date" => djust_components::ui::input::InputType::Date,
                    "time" => djust_components::ui::input::InputType::Time,
                    "datetime" => djust_components::ui::input::InputType::DateTime,
                    "color" => djust_components::ui::input::InputType::Color,
                    "file" => djust_components::ui::input::InputType::File,
                    _ => djust_components::ui::input::InputType::Text,
                };
                input.input_type = input_type;
            }

            if let Ok(size_str) = get_prop("size", props, context) {
                let size = match size_str.as_str() {
                    "sm" | "small" => djust_components::ui::input::InputSize::Small,
                    "lg" | "large" => djust_components::ui::input::InputSize::Large,
                    _ => djust_components::ui::input::InputSize::Medium,
                };
                input.size = size;
            }

            if let Ok(name) = get_prop("name", props, context) {
                input.name = Some(name);
            }

            if let Ok(value) = get_prop("value", props, context) {
                input.value = Some(value);
            }

            if let Ok(placeholder) = get_prop("placeholder", props, context) {
                input.placeholder = Some(placeholder);
            }

            if let Ok(disabled) = get_prop("disabled", props, context) {
                input.disabled = disabled == "true" || disabled == "True";
            }

            if let Ok(readonly) = get_prop("readonly", props, context) {
                input.readonly = readonly == "true" || readonly == "True";
            }

            if let Ok(required) = get_prop("required", props, context) {
                input.required = required == "true" || required == "True";
            }

            if let Ok(on_input) = get_prop("onInput", props, context) {
                input.on_input = Some(on_input);
            }

            if let Ok(on_change) = get_prop("onChange", props, context) {
                input.on_change = Some(on_change);
            }

            // Render the component
            input.render(fw).map_err(|e| {
                DjangoRustError::TemplateError(format!("Failed to render RustInput: {e}"))
            })
        }

        "RustText" => {
            // Extract required content prop
            let content = get_prop("content", props, context)?;

            // Create text with basic props
            let mut text = djust_components::ui::Text::new(content);

            // Apply optional props
            if let Ok(element_str) = get_prop("element", props, context) {
                let element = match element_str.as_str() {
                    "p" | "paragraph" => djust_components::ui::text::TextElement::Paragraph,
                    "span" => djust_components::ui::text::TextElement::Span,
                    "label" => djust_components::ui::text::TextElement::Label,
                    "div" => djust_components::ui::text::TextElement::Div,
                    "h1" => djust_components::ui::text::TextElement::H1,
                    "h2" => djust_components::ui::text::TextElement::H2,
                    "h3" => djust_components::ui::text::TextElement::H3,
                    "h4" => djust_components::ui::text::TextElement::H4,
                    "h5" => djust_components::ui::text::TextElement::H5,
                    "h6" => djust_components::ui::text::TextElement::H6,
                    _ => djust_components::ui::text::TextElement::Span,
                };
                text.element = element;
            }

            if let Ok(color_str) = get_prop("color", props, context) {
                let color = match color_str.as_str() {
                    "primary" => djust_components::ui::text::TextColor::Primary,
                    "secondary" => djust_components::ui::text::TextColor::Secondary,
                    "success" => djust_components::ui::text::TextColor::Success,
                    "danger" => djust_components::ui::text::TextColor::Danger,
                    "warning" => djust_components::ui::text::TextColor::Warning,
                    "info" => djust_components::ui::text::TextColor::Info,
                    "light" => djust_components::ui::text::TextColor::Light,
                    "dark" => djust_components::ui::text::TextColor::Dark,
                    "muted" => djust_components::ui::text::TextColor::Muted,
                    _ => djust_components::ui::text::TextColor::Dark,
                };
                text.color = Some(color);
            }

            if let Ok(weight_str) = get_prop("weight", props, context) {
                let weight = match weight_str.as_str() {
                    "bold" => djust_components::ui::text::FontWeight::Bold,
                    "light" => djust_components::ui::text::FontWeight::Light,
                    _ => djust_components::ui::text::FontWeight::Normal,
                };
                text.weight = weight;
            }

            if let Ok(for_input) = get_prop("forInput", props, context) {
                text.for_input = Some(for_input);
            }

            if let Ok(id) = get_prop("id", props, context) {
                text.id = Some(id);
            }

            // Render the component
            text.render(fw).map_err(|e| {
                DjangoRustError::TemplateError(format!("Failed to render RustText: {e}"))
            })
        }

        "RustCard" => {
            // Extract required body prop
            let body = get_prop("body", props, context)?;

            // Create card with basic props
            let mut card = djust_components::ui::Card::new(body);

            // Apply optional props
            if let Ok(variant_str) = get_prop("variant", props, context) {
                let variant = match variant_str.as_str() {
                    "primary" => djust_components::ui::card::CardVariant::Primary,
                    "secondary" => djust_components::ui::card::CardVariant::Secondary,
                    "success" => djust_components::ui::card::CardVariant::Success,
                    "danger" => djust_components::ui::card::CardVariant::Danger,
                    "warning" => djust_components::ui::card::CardVariant::Warning,
                    "info" => djust_components::ui::card::CardVariant::Info,
                    "light" => djust_components::ui::card::CardVariant::Light,
                    "dark" => djust_components::ui::card::CardVariant::Dark,
                    _ => djust_components::ui::card::CardVariant::Default,
                };
                card.variant = variant;
            }

            if let Ok(header) = get_prop("header", props, context) {
                card.header = Some(header);
            }

            if let Ok(footer) = get_prop("footer", props, context) {
                card.footer = Some(footer);
            }

            if let Ok(border) = get_prop("border", props, context) {
                card.border = border == "true" || border == "True";
            }

            if let Ok(shadow) = get_prop("shadow", props, context) {
                card.shadow = shadow == "true" || shadow == "True";
            }

            if let Ok(id) = get_prop("id", props, context) {
                card.id = Some(id);
            }

            // Render the component
            card.render(fw).map_err(|e| {
                DjangoRustError::TemplateError(format!("Failed to render RustCard: {e}"))
            })
        }

        "RustAlert" => {
            // Extract required message prop
            let message = get_prop("message", props, context)?;

            // Create alert with basic props
            let mut alert = djust_components::ui::Alert::new(message);

            // Apply optional props
            if let Ok(variant_str) = get_prop("variant", props, context) {
                let variant = match variant_str.as_str() {
                    "primary" => djust_components::ui::alert::AlertVariant::Primary,
                    "secondary" => djust_components::ui::alert::AlertVariant::Secondary,
                    "success" => djust_components::ui::alert::AlertVariant::Success,
                    "danger" => djust_components::ui::alert::AlertVariant::Danger,
                    "warning" => djust_components::ui::alert::AlertVariant::Warning,
                    "info" => djust_components::ui::alert::AlertVariant::Info,
                    "light" => djust_components::ui::alert::AlertVariant::Light,
                    "dark" => djust_components::ui::alert::AlertVariant::Dark,
                    _ => djust_components::ui::alert::AlertVariant::Info,
                };
                alert.variant = variant;
            }

            if let Ok(dismissible) = get_prop("dismissible", props, context) {
                alert.dismissible = dismissible == "true" || dismissible == "True";
            }

            if let Ok(icon) = get_prop("icon", props, context) {
                alert.icon = Some(icon);
            }

            if let Ok(id) = get_prop("id", props, context) {
                alert.id = Some(id);
            }

            // Render the component
            alert.render(fw).map_err(|e| {
                DjangoRustError::TemplateError(format!("Failed to render RustAlert: {e}"))
            })
        }

        "RustModal" => {
            // Extract required props
            let id = get_prop("id", props, context)?;
            let body = get_prop("body", props, context)?;

            // Create modal with basic props
            let mut modal = djust_components::ui::Modal::new(id, body);

            // Apply optional props
            if let Ok(title) = get_prop("title", props, context) {
                modal.title = Some(title);
            }

            if let Ok(footer) = get_prop("footer", props, context) {
                modal.footer = Some(footer);
            }

            if let Ok(size_str) = get_prop("size", props, context) {
                let size = match size_str.as_str() {
                    "small" | "sm" => djust_components::ui::modal::ModalSize::Small,
                    "medium" | "md" => djust_components::ui::modal::ModalSize::Medium,
                    "large" | "lg" => djust_components::ui::modal::ModalSize::Large,
                    "xl" | "extralarge" => djust_components::ui::modal::ModalSize::ExtraLarge,
                    _ => djust_components::ui::modal::ModalSize::Medium,
                };
                modal.size = size;
            }

            if let Ok(centered) = get_prop("centered", props, context) {
                modal.centered = centered == "true" || centered == "True";
            }

            if let Ok(scrollable) = get_prop("scrollable", props, context) {
                modal.scrollable = scrollable == "true" || scrollable == "True";
            }

            // Render the component
            modal.render(fw).map_err(|e| {
                DjangoRustError::TemplateError(format!("Failed to render RustModal: {e}"))
            })
        }

        "RustDropdown" => {
            // Extract required id prop
            let id = get_prop("id", props, context)?;

            // Create dropdown with basic props
            let mut dropdown = djust_components::ui::Dropdown::new(id);

            // Parse items from template
            // Expected format: items="[{'label': 'Option 1', 'value': 'opt1'}, ...]"
            if let Ok(items_str) = get_prop("items", props, context) {
                // Try to parse as JSON
                if let Ok(items_json) = serde_json::from_str::<Vec<serde_json::Value>>(&items_str) {
                    let mut items = Vec::new();
                    for item in items_json {
                        if let (Some(label), Some(value)) = (
                            item.get("label").and_then(|v| v.as_str()),
                            item.get("value").and_then(|v| v.as_str()),
                        ) {
                            items.push(djust_components::ui::dropdown::DropdownItem {
                                label: label.to_string(),
                                value: value.to_string(),
                            });
                        }
                    }
                    dropdown.items = items;
                }
            }

            // Apply optional props
            if let Ok(selected) = get_prop("selected", props, context) {
                dropdown.selected = Some(selected);
            }

            if let Ok(variant_str) = get_prop("variant", props, context) {
                let variant = match variant_str.as_str() {
                    "primary" => djust_components::ui::dropdown::DropdownVariant::Primary,
                    "secondary" => djust_components::ui::dropdown::DropdownVariant::Secondary,
                    "success" => djust_components::ui::dropdown::DropdownVariant::Success,
                    "danger" => djust_components::ui::dropdown::DropdownVariant::Danger,
                    "warning" => djust_components::ui::dropdown::DropdownVariant::Warning,
                    "info" => djust_components::ui::dropdown::DropdownVariant::Info,
                    "light" => djust_components::ui::dropdown::DropdownVariant::Light,
                    "dark" => djust_components::ui::dropdown::DropdownVariant::Dark,
                    _ => djust_components::ui::dropdown::DropdownVariant::Primary,
                };
                dropdown.variant = variant;
            }

            if let Ok(size_str) = get_prop("size", props, context) {
                let size = match size_str.as_str() {
                    "sm" | "small" => djust_components::ui::dropdown::DropdownSize::Small,
                    "lg" | "large" => djust_components::ui::dropdown::DropdownSize::Large,
                    _ => djust_components::ui::dropdown::DropdownSize::Medium,
                };
                dropdown.size = size;
            }

            if let Ok(disabled) = get_prop("disabled", props, context) {
                dropdown.disabled = disabled == "true" || disabled == "True";
            }

            if let Ok(placeholder) = get_prop("placeholder", props, context) {
                dropdown.placeholder = Some(placeholder);
            }

            // Render the component
            dropdown.render(fw).map_err(|e| {
                DjangoRustError::TemplateError(format!("Failed to render RustDropdown: {e}"))
            })
        }

        "RustTabs" => {
            // Extract required id prop
            let id = get_prop("id", props, context)?;

            // Create tabs with basic props
            let mut tabs = djust_components::ui::Tabs::new(id);

            // Parse tabs from template
            // Expected format: tabs="[{'id': 'tab1', 'label': 'Tab 1', 'content': 'Content 1'}, ...]"
            if let Ok(tabs_str) = get_prop("tabs", props, context) {
                // Try to parse as JSON
                if let Ok(tabs_json) = serde_json::from_str::<Vec<serde_json::Value>>(&tabs_str) {
                    let mut tabs_vec = Vec::new();
                    for tab in tabs_json {
                        if let (Some(tab_id), Some(label), Some(content)) = (
                            tab.get("id").and_then(|v| v.as_str()),
                            tab.get("label").and_then(|v| v.as_str()),
                            tab.get("content").and_then(|v| v.as_str()),
                        ) {
                            tabs_vec.push(djust_components::ui::tabs::TabItem {
                                id: tab_id.to_string(),
                                label: label.to_string(),
                                content: content.to_string(),
                            });
                        }
                    }
                    if !tabs_vec.is_empty() && tabs.active.is_empty() {
                        tabs.active = tabs_vec[0].id.clone();
                    }
                    tabs.tabs = tabs_vec;
                }
            }

            // Apply optional props
            if let Ok(active) = get_prop("active", props, context) {
                tabs.active = active;
            }

            if let Ok(variant_str) = get_prop("variant", props, context) {
                let variant = match variant_str.as_str() {
                    "pills" => djust_components::ui::tabs::TabVariant::Pills,
                    "underline" => djust_components::ui::tabs::TabVariant::Underline,
                    _ => djust_components::ui::tabs::TabVariant::Default,
                };
                tabs.variant = variant;
            }

            if let Ok(vertical) = get_prop("vertical", props, context) {
                tabs.vertical = vertical == "true" || vertical == "True";
            }

            // Render the component
            tabs.render(fw).map_err(|e| {
                DjangoRustError::TemplateError(format!("Failed to render RustTabs: {e}"))
            })
        }

        _ => Err(DjangoRustError::TemplateError(format!(
            "Unknown Rust component: {name}"
        ))),
    }
}

/// Get a prop value, resolving template variables if needed
fn get_prop(key: &str, props: &[(String, String)], context: &Context) -> Result<String> {
    for (k, v) in props {
        if k == key {
            // Resolve Django template variable syntax: {{ var.path }}
            if v.starts_with("{{") && v.ends_with("}}") {
                let var_name = v.trim_start_matches("{{").trim_end_matches("}}").trim();

                if let Some(ctx_value) = context.get(var_name) {
                    return Ok(ctx_value.to_string());
                }
            } else if let Some(ctx_value) = context.get(v) {
                // Direct variable reference (no {{ }})
                return Ok(ctx_value.to_string());
            } else {
                // Literal value
                return Ok(v.clone());
            }
        }
    }

    Err(DjangoRustError::TemplateError(format!(
        "Missing required prop: {key}"
    )))
}

fn evaluate_condition(condition: &str, context: &Context) -> Result<bool> {
    let condition = condition.trim();

    // Handle simple boolean values
    if condition == "true" || condition == "True" {
        return Ok(true);
    }
    if condition == "false" || condition == "False" {
        return Ok(false);
    }

    // Handle "or" (lowest precedence - split first)
    // Use " or " with spaces to avoid matching variable names containing "or"
    if let Some(pos) = condition.find(" or ") {
        let left = &condition[..pos];
        let right = &condition[pos + 4..];
        return Ok(evaluate_condition(left, context)? || evaluate_condition(right, context)?);
    }

    // Handle "and" (higher precedence than "or")
    if let Some(pos) = condition.find(" and ") {
        let left = &condition[..pos];
        let right = &condition[pos + 5..];
        return Ok(evaluate_condition(left, context)? && evaluate_condition(right, context)?);
    }

    // Handle variable lookups
    if let Some(value) = context.get(condition) {
        return Ok(value.is_truthy());
    }

    // Handle negation
    if let Some(rest) = condition.strip_prefix("not ") {
        return Ok(!evaluate_condition(rest, context)?);
    }

    // Handle comparisons
    if condition.contains("==") {
        let parts: Vec<&str> = condition.split("==").map(|s| s.trim()).collect();
        if parts.len() == 2 {
            let left = get_value(parts[0], context)?;
            let right = get_value(parts[1], context)?;
            return Ok(values_equal(&left, &right));
        }
    }

    if condition.contains("!=") {
        let parts: Vec<&str> = condition.split("!=").map(|s| s.trim()).collect();
        if parts.len() == 2 {
            let left = get_value(parts[0], context)?;
            let right = get_value(parts[1], context)?;
            return Ok(!values_equal(&left, &right));
        }
    }

    // Handle >= (must be before > to avoid false match)
    if condition.contains(">=") {
        let parts: Vec<&str> = condition.split(">=").map(|s| s.trim()).collect();
        if parts.len() == 2 {
            let left = get_value(parts[0], context)?;
            let right = get_value(parts[1], context)?;
            return Ok(compare_values(&left, &right) >= 0);
        }
    }

    // Handle <= (must be before < to avoid false match)
    if condition.contains("<=") {
        let parts: Vec<&str> = condition.split("<=").map(|s| s.trim()).collect();
        if parts.len() == 2 {
            let left = get_value(parts[0], context)?;
            let right = get_value(parts[1], context)?;
            return Ok(compare_values(&left, &right) <= 0);
        }
    }

    // Handle "in" operator: {% if item in list %}
    if condition.contains(" in ") {
        let parts: Vec<&str> = condition.splitn(2, " in ").map(|s| s.trim()).collect();
        if parts.len() == 2 {
            let needle = get_value(parts[0], context)?;
            let haystack = get_value(parts[1], context)?;
            return match haystack {
                Value::List(items) => Ok(items.iter().any(|item| values_equal(&needle, item))),
                Value::String(s) => {
                    if let Value::String(n) = &needle {
                        Ok(s.contains(n.as_str()))
                    } else {
                        Ok(false)
                    }
                }
                Value::Object(map) => {
                    // Django: "x in dict" checks dict keys
                    let key = needle.to_string();
                    Ok(map.contains_key(&key))
                }
                _ => Ok(false),
            };
        }
    }

    // Handle > (greater than)
    if condition.contains(" > ") {
        let parts: Vec<&str> = condition.split(" > ").map(|s| s.trim()).collect();
        if parts.len() == 2 {
            let left = get_value(parts[0], context)?;
            let right = get_value(parts[1], context)?;
            return Ok(compare_values(&left, &right) > 0);
        }
    }

    // Handle < (less than)
    if condition.contains(" < ") {
        let parts: Vec<&str> = condition.split(" < ").map(|s| s.trim()).collect();
        if parts.len() == 2 {
            let left = get_value(parts[0], context)?;
            let right = get_value(parts[1], context)?;
            return Ok(compare_values(&left, &right) < 0);
        }
    }

    // Default to false for unknown conditions
    Ok(false)
}

fn get_value(expr: &str, context: &Context) -> Result<Value> {
    // Handle pipe filters in expressions (e.g., "project.id|stringformat:\"s\"")
    if expr.contains('|') {
        let parts: Vec<&str> = expr.splitn(2, '|').collect();
        let var_name = parts[0].trim();
        let filter_expr = parts[1].trim();

        // Resolve the base variable
        let mut value = get_value(var_name, context)?;

        // Parse and apply filters (handles chained filters too)
        for filter_part in filter_expr.split('|') {
            let filter_part = filter_part.trim();
            let (filter_name, arg, arg_was_quoted) = if let Some(colon_pos) = filter_part.find(':')
            {
                let name = &filter_part[..colon_pos];
                let raw_arg = filter_part[colon_pos + 1..].trim();
                let was_quoted = is_quoted_arg(raw_arg);
                let arg_str = if was_quoted {
                    raw_arg[1..raw_arg.len() - 1].to_string()
                } else {
                    raw_arg.to_string()
                };
                (name, Some(arg_str), was_quoted)
            } else {
                (filter_part, None, false)
            };

            value = filters::apply_filter_full(
                filter_name,
                &value,
                arg.as_deref(),
                Some(context),
                arg_was_quoted,
            )?;
        }

        return Ok(value);
    }

    // Try to get from context
    if let Some(value) = context.get(expr) {
        return Ok(value.clone());
    }

    // Try to parse as literal
    if expr == "True" || expr == "true" {
        return Ok(Value::Bool(true));
    }
    if expr == "False" || expr == "false" {
        return Ok(Value::Bool(false));
    }
    if expr == "None" || expr == "none" {
        return Ok(Value::Null);
    }

    if let Ok(i) = expr.parse::<i64>() {
        return Ok(Value::Integer(i));
    }

    if let Ok(f) = expr.parse::<f64>() {
        return Ok(Value::Float(f));
    }

    // String literal (remove quotes)
    if (expr.starts_with('"') && expr.ends_with('"'))
        || (expr.starts_with('\'') && expr.ends_with('\''))
    {
        return Ok(Value::String(expr[1..expr.len() - 1].to_string()));
    }

    Ok(Value::Null)
}

fn values_equal(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Null, Value::Null) => true,
        (Value::Bool(a), Value::Bool(b)) => a == b,
        (Value::Integer(a), Value::Integer(b)) => a == b,
        (Value::Float(a), Value::Float(b)) => (a - b).abs() < f64::EPSILON,
        (Value::String(a), Value::String(b)) => a == b,
        _ => false,
    }
}

/// Compare two values and return -1 (less), 0 (equal), or 1 (greater).
/// Returns 0 for incomparable types.
fn compare_values(a: &Value, b: &Value) -> i32 {
    match (a, b) {
        (Value::Integer(a), Value::Integer(b)) => a.cmp(b) as i32,
        (Value::Float(a), Value::Float(b)) => {
            if (a - b).abs() < f64::EPSILON {
                0
            } else if a < b {
                -1
            } else {
                1
            }
        }
        // Allow comparing integers and floats
        (Value::Integer(a), Value::Float(b)) => {
            let a_f = *a as f64;
            if (a_f - b).abs() < f64::EPSILON {
                0
            } else if a_f < *b {
                -1
            } else {
                1
            }
        }
        (Value::Float(a), Value::Integer(b)) => {
            let b_f = *b as f64;
            if (a - b_f).abs() < f64::EPSILON {
                0
            } else if *a < b_f {
                -1
            } else {
                1
            }
        }
        (Value::String(a), Value::String(b)) => a.cmp(b) as i32,
        // Null comparisons
        (Value::Null, Value::Null) => 0,
        // Incomparable types return 0 (treated as equal, so < and > fail)
        _ => 0,
    }
}

/// Convert a Value to f64 for arithmetic operations (widthratio)
trait ToF64 {
    fn to_f64(&self) -> Option<f64>;
}

impl ToF64 for Value {
    fn to_f64(&self) -> Option<f64> {
        match self {
            Value::Integer(i) => Some(*i as f64),
            Value::Float(f) => Some(*f),
            Value::String(s) => s.parse::<f64>().ok(),
            Value::Bool(b) => Some(if *b { 1.0 } else { 0.0 }),
            _ => None,
        }
    }
}

/// Convert Django date format characters to chrono strftime format.
///
/// Django uses PHP-style single-character format codes (e.g., "Y" for 4-digit year).
/// This converts the most common ones to chrono's strftime equivalents.
fn django_date_format(dt: &chrono::DateTime<chrono::Local>, django_fmt: &str) -> String {
    let mut result = String::new();
    let chars = django_fmt.chars();
    let mut escaped = false;

    for c in chars {
        if escaped {
            result.push(c);
            escaped = false;
            continue;
        }
        if c == '\\' {
            escaped = true;
            continue;
        }
        match c {
            // Day
            'd' => result.push_str(&dt.format("%d").to_string()), // 01-31
            'j' => result.push_str(&dt.format("%-d").to_string()), // 1-31
            'D' => result.push_str(&dt.format("%a").to_string()), // Mon
            'l' => result.push_str(&dt.format("%A").to_string()), // Monday
            // Month
            'm' => result.push_str(&dt.format("%m").to_string()), // 01-12
            'n' => result.push_str(&dt.format("%-m").to_string()), // 1-12
            'M' => result.push_str(&dt.format("%b").to_string()), // Jan
            'F' => result.push_str(&dt.format("%B").to_string()), // January
            // Year
            'Y' => result.push_str(&dt.format("%Y").to_string()), // 2024
            'y' => result.push_str(&dt.format("%y").to_string()), // 24
            // Time
            'H' => result.push_str(&dt.format("%H").to_string()), // 00-23
            'i' => result.push_str(&dt.format("%M").to_string()), // 00-59
            's' => result.push_str(&dt.format("%S").to_string()), // 00-59
            'G' => result.push_str(&dt.format("%-H").to_string()), // 0-23
            'g' => result.push_str(&dt.format("%-I").to_string()), // 1-12
            'A' => result.push_str(&dt.format("%p").to_string()), // AM/PM
            'P' => {
                // Django's P format: "1 a.m.", "noon", "midnight"
                let hour = dt.format("%-I").to_string().parse::<u32>().unwrap_or(0);
                let minute = dt.format("%M").to_string();
                let ampm = if dt.format("%P").to_string() == "am" {
                    "a.m."
                } else {
                    "p.m."
                };
                if minute == "00" {
                    if hour == 12 && ampm == "p.m." {
                        result.push_str("noon");
                    } else if hour == 12 && ampm == "a.m." {
                        result.push_str("midnight");
                    } else {
                        result.push_str(&format!("{} {}", hour, ampm));
                    }
                } else {
                    result.push_str(&format!("{}:{} {}", hour, minute, ampm));
                }
            }
            // Week/day-of-week
            'w' => result.push_str(&dt.format("%w").to_string()), // 0 (Sun) - 6 (Sat)
            'W' => result.push_str(&dt.format("%V").to_string()), // ISO week number
            'S' => {
                // English ordinal suffix: st, nd, rd, th
                let day = dt.format("%-d").to_string().parse::<u32>().unwrap_or(0);
                let suffix = match day {
                    1 | 21 | 31 => "st",
                    2 | 22 => "nd",
                    3 | 23 => "rd",
                    _ => "th",
                };
                result.push_str(suffix);
            }
            't' => {
                // Days in the month (28-31)
                let month = dt.format("%-m").to_string().parse::<u32>().unwrap_or(1);
                let year = dt.format("%Y").to_string().parse::<i32>().unwrap_or(2000);
                let days = match month {
                    1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
                    4 | 6 | 9 | 11 => 30,
                    2 => {
                        if (year % 4 == 0 && year % 100 != 0) || year % 400 == 0 {
                            29
                        } else {
                            28
                        }
                    }
                    _ => 30,
                };
                result.push_str(&days.to_string());
            }
            'L' => {
                // Leap year: True or False
                let year = dt.format("%Y").to_string().parse::<i32>().unwrap_or(2000);
                let is_leap = (year % 4 == 0 && year % 100 != 0) || year % 400 == 0;
                result.push_str(if is_leap { "True" } else { "False" });
            }
            // Timezone
            'e' => result.push_str(&dt.format("%Z").to_string()),
            // ISO 8601
            'c' => result.push_str(&dt.format("%Y-%m-%dT%H:%M:%S%:z").to_string()),
            // RFC 2822
            'r' => result.push_str(&dt.format("%a, %d %b %Y %H:%M:%S %z").to_string()),
            // Unix timestamp
            'U' => result.push_str(&dt.timestamp().to_string()),
            // Other
            'N' => result.push_str(&dt.format("%b.").to_string()), // Month abbrev AP style
            _ => result.push(c), // Pass through unrecognized chars (colons, spaces, etc.)
        }
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::lexer::tokenize;
    use crate::parser::parse;

    #[test]
    fn test_render_text() {
        let nodes = vec![Node::Text("Hello".to_string())];
        let context = Context::new();
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "Hello");
    }

    #[test]
    fn test_render_variable() {
        let nodes = vec![Node::Variable("name".to_string(), vec![], false)];
        let mut context = Context::new();
        context.set("name".to_string(), Value::String("World".to_string()));
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "World");
    }

    #[test]
    fn test_render_if_true() {
        let tokens = tokenize("{% if show %}visible{% endif %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("show".to_string(), Value::Bool(true));
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "visible");
    }

    #[test]
    fn test_render_for() {
        let tokens = tokenize("{% for item in items %}{{ item }}{% endfor %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set(
            "items".to_string(),
            Value::List(vec![
                Value::String("a".to_string()),
                Value::String("b".to_string()),
                Value::String("c".to_string()),
            ]),
        );
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "abc");
    }

    #[test]
    fn test_render_for_reversed() {
        let tokens = tokenize("{% for item in items reversed %}{{ item }}{% endfor %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set(
            "items".to_string(),
            Value::List(vec![
                Value::String("a".to_string()),
                Value::String("b".to_string()),
                Value::String("c".to_string()),
            ]),
        );
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "cba");
    }

    #[test]
    fn test_render_for_reversed_numbers() {
        let tokens = tokenize("{% for num in numbers reversed %}{{ num }},{% endfor %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set(
            "numbers".to_string(),
            Value::List(vec![
                Value::Integer(1),
                Value::Integer(2),
                Value::Integer(3),
            ]),
        );
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "3,2,1,");
    }

    #[test]
    fn test_render_for_normal_not_affected() {
        // Ensure normal for loops still work
        let tokens = tokenize("{% for item in items %}{{ item }}{% endfor %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set(
            "items".to_string(),
            Value::List(vec![
                Value::String("x".to_string()),
                Value::String("y".to_string()),
                Value::String("z".to_string()),
            ]),
        );
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "xyz");
    }

    #[test]
    fn test_render_for_empty_with_items() {
        // Test that empty block is NOT rendered when list has items
        let tokens =
            tokenize("{% for item in items %}{{ item }}{% empty %}No items{% endfor %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set(
            "items".to_string(),
            Value::List(vec![
                Value::String("a".to_string()),
                Value::String("b".to_string()),
            ]),
        );
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "ab");
        assert!(!result.contains("No items"));
    }

    #[test]
    fn test_render_for_empty_without_items() {
        // Test that empty block IS rendered when list is empty
        let tokens =
            tokenize("{% for item in items %}{{ item }}{% empty %}No items{% endfor %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("items".to_string(), Value::List(vec![]));
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "No items");
    }

    #[test]
    fn test_render_for_empty_null_iterable() {
        // Test that empty block IS rendered when iterable is null/missing
        let tokens =
            tokenize("{% for item in items %}{{ item }}{% empty %}No items{% endfor %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let context = Context::new(); // items not set
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "No items");
    }

    #[test]
    fn test_render_for_empty_complex_content() {
        // Test that empty block can contain complex HTML
        let template = r#"{% for property in properties %}<tr><td>{{ property.name }}</td></tr>{% empty %}<tr><td colspan="6">No properties found. <a href="/add">Add property</a></td></tr>{% endfor %}"#;
        let tokens = tokenize(template).unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("properties".to_string(), Value::List(vec![]));
        let result = render_nodes(&nodes, &context).unwrap();
        assert!(result.contains("No properties found"));
        assert!(result.contains("<a href=\"/add\">"));
        assert!(result.contains("colspan=\"6\""));
    }

    #[test]
    fn test_csrf_token_tag() {
        let tokens = tokenize("{% csrf_token %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set(
            "csrf_token".to_string(),
            Value::String("test-csrf-token-123".to_string()),
        );
        let result = render_nodes(&nodes, &context).unwrap();
        assert!(result.contains("<input type=\"hidden\""));
        assert!(result.contains("name=\"csrfmiddlewaretoken\""));
        assert!(result.contains("value=\"test-csrf-token-123\""));
    }

    #[test]
    fn test_csrf_token_tag_without_token_renders_empty() {
        // #696: When no CSRF token is in context, render nothing so
        // client.js falls through to reading the CSRF cookie instead.
        let tokens = tokenize("{% csrf_token %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let context = Context::new();
        let result = render_nodes(&nodes, &context).unwrap();
        assert!(
            result.is_empty(),
            "Expected empty output without csrf_token in context, got: {result}"
        );
        assert!(
            !result.contains("CSRF_TOKEN_NOT_PROVIDED"),
            "Must not contain placeholder"
        );
    }

    #[test]
    fn test_static_tag() {
        let tokens = tokenize("{% static 'css/style.css' %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set(
            "STATIC_URL".to_string(),
            Value::String("/static/".to_string()),
        );
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "/static/css/style.css");
    }

    #[test]
    fn test_static_tag_custom_url() {
        let tokens = tokenize("{% static \"images/logo.png\" %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set(
            "STATIC_URL".to_string(),
            Value::String("https://cdn.example.com/static/".to_string()),
        );
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "https://cdn.example.com/static/images/logo.png");
    }

    #[test]
    fn test_comment_tag() {
        let tokens = tokenize("Before{% comment %}Hidden content{% endcomment %}After").unwrap();
        let nodes = parse(&tokens).unwrap();
        let context = Context::new();
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "BeforeAfter");
    }

    #[test]
    fn test_with_tag() {
        let tokens = tokenize("{% with greeting=message %}{{ greeting }}{% endwith %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set(
            "message".to_string(),
            Value::String("Hello World".to_string()),
        );
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "Hello World");
    }

    #[test]
    fn test_with_tag_multiple_vars() {
        let tokens = tokenize("{% with a=x b=y %}{{ a }} and {{ b }}{% endwith %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("x".to_string(), Value::String("foo".to_string()));
        context.set("y".to_string(), Value::String("bar".to_string()));
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "foo and bar");
    }

    #[test]
    fn test_with_tag_scoping() {
        // Test that variables inside with don't affect outer context
        let tokens =
            tokenize("{{ name }}{% with name=other %}{{ name }}{% endwith %}{{ name }}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("name".to_string(), Value::String("outer".to_string()));
        context.set("other".to_string(), Value::String("inner".to_string()));
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "outerinnerouter");
    }

    #[test]
    fn test_if_and_operator() {
        let tokens = tokenize("{% if a and b %}yes{% endif %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("a".to_string(), Value::Bool(true));
        context.set("b".to_string(), Value::Bool(true));
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "yes");

        context.set("b".to_string(), Value::Bool(false));
        // Fix for DJE-053: false {% if %} blocks emit placeholder comment, not empty string
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "<!--dj-if-->");
    }

    #[test]
    fn test_if_or_operator() {
        let tokens = tokenize("{% if a or b %}yes{% endif %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("a".to_string(), Value::Bool(false));
        context.set("b".to_string(), Value::Bool(true));
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "yes");

        context.set("b".to_string(), Value::Bool(false));
        // Fix for DJE-053: false {% if %} blocks emit placeholder comment, not empty string
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "<!--dj-if-->");
    }

    #[test]
    fn test_if_not_and_not() {
        let tokens = tokenize("{% if not a and not b %}empty{% endif %}").unwrap();
        let nodes = parse(&tokens).unwrap();

        // Both falsy -> should show
        let mut context = Context::new();
        context.set("a".to_string(), Value::List(vec![]));
        context.set("b".to_string(), Value::String(String::new()));
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "empty");

        // a truthy -> should not show
        context.set("a".to_string(), Value::List(vec![Value::Integer(1)]));
        // Fix for DJE-053: false {% if %} blocks emit placeholder comment, not empty string
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "<!--dj-if-->");
    }

    #[test]
    fn test_if_mixed_and_or_precedence() {
        // "and" binds tighter than "or": a or b and c == a or (b and c)
        let tokens = tokenize("{% if a or b and c %}yes{% endif %}").unwrap();
        let nodes = parse(&tokens).unwrap();

        // a=false, b=true, c=false -> false or (true and false) -> false
        let mut context = Context::new();
        context.set("a".to_string(), Value::Bool(false));
        context.set("b".to_string(), Value::Bool(true));
        context.set("c".to_string(), Value::Bool(false));
        // Fix for DJE-053: false {% if %} blocks emit placeholder comment, not empty string
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "<!--dj-if-->");

        // a=true, b=false, c=false -> true or (false and false) -> true
        context.set("a".to_string(), Value::Bool(true));
        context.set("b".to_string(), Value::Bool(false));
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "yes");
    }

    #[test]
    fn test_if_chained_and() {
        let tokens = tokenize("{% if a and b and c %}yes{% endif %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("a".to_string(), Value::Bool(true));
        context.set("b".to_string(), Value::Bool(true));
        context.set("c".to_string(), Value::Bool(true));
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "yes");

        context.set("b".to_string(), Value::Bool(false));
        // Fix for DJE-053: false {% if %} blocks emit placeholder comment, not empty string
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "<!--dj-if-->");
    }

    #[test]
    fn test_if_not_with_or() {
        // not a or b == (not a) or b
        let tokens = tokenize("{% if not a or b %}yes{% endif %}").unwrap();
        let nodes = parse(&tokens).unwrap();

        // a=true, b=false -> (not true) or false -> false
        let mut context = Context::new();
        context.set("a".to_string(), Value::Bool(true));
        context.set("b".to_string(), Value::Bool(false));
        // Fix for DJE-053: false {% if %} blocks emit placeholder comment, not empty string
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "<!--dj-if-->");

        // a=true, b=true -> (not true) or true -> true
        context.set("b".to_string(), Value::Bool(true));
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "yes");

        // a=false, b=false -> (not false) or false -> true
        context.set("a".to_string(), Value::Bool(false));
        context.set("b".to_string(), Value::Bool(false));
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "yes");
    }

    #[test]
    fn test_if_in_list() {
        let tokens = tokenize("{% if item in items %}found{% endif %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("item".to_string(), Value::String("b".to_string()));
        context.set(
            "items".to_string(),
            Value::List(vec![
                Value::String("a".to_string()),
                Value::String("b".to_string()),
                Value::String("c".to_string()),
            ]),
        );
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "found");

        context.set("item".to_string(), Value::String("z".to_string()));
        // Fix for DJE-053: false {% if %} blocks emit placeholder comment, not empty string
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "<!--dj-if-->");
    }

    #[test]
    fn test_if_in_string() {
        let tokens = tokenize("{% if sub in text %}found{% endif %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("sub".to_string(), Value::String("world".to_string()));
        context.set("text".to_string(), Value::String("hello world".to_string()));
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "found");

        context.set("sub".to_string(), Value::String("xyz".to_string()));
        // Fix for DJE-053: false {% if %} blocks emit placeholder comment, not empty string
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "<!--dj-if-->");
    }

    #[test]
    fn test_if_in_dict() {
        // Django: "x in dict" checks dict keys
        let tokens = tokenize("{% if key in mydict %}found{% endif %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();

        let mut map = std::collections::HashMap::new();
        map.insert("2".to_string(), Value::Bool(true));
        map.insert("5".to_string(), Value::String("hello".to_string()));
        context.set("mydict".to_string(), Value::Object(map));

        // Key exists → found
        context.set("key".to_string(), Value::String("2".to_string()));
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "found");

        // Key does not exist → placeholder
        context.set("key".to_string(), Value::String("99".to_string()));
        // Fix for DJE-053: false {% if %} blocks emit placeholder comment, not empty string
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "<!--dj-if-->");

        // Integer key converted to string for lookup
        context.set("key".to_string(), Value::Integer(5));
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "found");
    }

    #[test]
    fn test_if_filter_in_dict() {
        // Tests: {% if val|stringformat:"s" in mydict %}
        let tokens =
            tokenize(r#"{% if val|stringformat:"s" in mydict %}found{% else %}nope{% endif %}"#)
                .unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();

        let mut map = std::collections::HashMap::new();
        map.insert("42".to_string(), Value::Bool(true));
        context.set("mydict".to_string(), Value::Object(map));

        // Integer value, filter converts to string "42", should match dict key
        context.set("val".to_string(), Value::Integer(42));
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "found");

        // Non-matching value
        context.set("val".to_string(), Value::Integer(99));
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "nope");
    }

    #[test]
    fn test_auto_escape_variable() {
        // {{ var }} should auto-escape HTML special characters
        let tokens = tokenize("{{ content }}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set(
            "content".to_string(),
            Value::String("<script>alert(\"xss\")</script>".to_string()),
        );
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(
            result,
            "&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;"
        );
    }

    #[test]
    fn test_safe_filter_skips_escape() {
        // {{ var|safe }} should NOT auto-escape
        let tokens = tokenize("{{ content|safe }}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set(
            "content".to_string(),
            Value::String("<b>bold</b>".to_string()),
        );
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "<b>bold</b>");
    }

    #[test]
    fn test_escape_filter_with_auto_escape() {
        // {{ var|escape }} should produce same result as {{ var }}
        let tokens = tokenize("{{ content|escape }}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set(
            "content".to_string(),
            Value::String("<b>\"hi\"</b>".to_string()),
        );
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "&lt;b&gt;&quot;hi&quot;&lt;/b&gt;");
    }

    #[test]
    fn test_auto_escape_preserves_plain_text() {
        // Plain text without HTML chars should be unchanged
        let tokens = tokenize("Hello {{ name }}!").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("name".to_string(), Value::String("World".to_string()));
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "Hello World!");
    }

    // Tests for issue #295: VDOM diff bug with {% if %} removing elements

    #[test]
    fn test_if_false_emits_placeholder() {
        // When {% if %} is false with no {% else %}, should emit comment placeholder
        let tokens = tokenize("{% if show %}content{% endif %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("show".to_string(), Value::Bool(false));
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "<!--dj-if-->");
    }

    #[test]
    fn test_if_true_no_placeholder() {
        // When {% if %} is true, should render normally without placeholder
        let tokens = tokenize("{% if show %}content{% endif %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("show".to_string(), Value::Bool(true));
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "content");
    }

    #[test]
    fn test_if_with_else_no_placeholder() {
        // When {% if %} has {% else %}, should not emit placeholder (else content is rendered)
        let tokens = tokenize("{% if show %}true{% else %}false{% endif %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("show".to_string(), Value::Bool(false));
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "false");
        assert!(!result.contains("<!--dj-if-->"));
    }

    #[test]
    fn test_if_siblings_with_placeholder() {
        // Test that placeholder maintains sibling positions
        let template = "<div>{% if show %}item1{% endif %}<span>item2</span></div>";
        let tokens = tokenize(template).unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("show".to_string(), Value::Bool(false));
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "<div><!--dj-if--><span>item2</span></div>");
    }

    #[test]
    fn test_multiple_if_blocks_with_placeholders() {
        // Test multiple conditional blocks
        let template = "{% if a %}A{% endif %}{% if b %}B{% endif %}{% if c %}C{% endif %}";
        let tokens = tokenize(template).unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("a".to_string(), Value::Bool(false));
        context.set("b".to_string(), Value::Bool(true));
        context.set("c".to_string(), Value::Bool(false));
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "<!--dj-if-->B<!--dj-if-->");
    }

    // Tests for newly implemented Django template tags

    #[test]
    fn test_widthratio_basic() {
        let tokens = tokenize("{% widthratio value max_value max_width %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("value".to_string(), Value::Integer(175));
        context.set("max_value".to_string(), Value::Integer(200));
        context.set("max_width".to_string(), Value::Integer(100));
        let result = render_nodes(&nodes, &context).unwrap();
        // 175/200 * 100 = 87.5, rounds to 88
        assert_eq!(result, "88");
    }

    #[test]
    fn test_widthratio_zero_max() {
        let tokens = tokenize("{% widthratio value max_value 100 %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("value".to_string(), Value::Integer(50));
        context.set("max_value".to_string(), Value::Integer(0));
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "0");
    }

    #[test]
    fn test_widthratio_progress_bar() {
        // The exact use case from issue #329
        let tokens =
            tokenize("<div style=\"width: {% widthratio value total 100 %}%\"></div>").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("value".to_string(), Value::Integer(75));
        context.set("total".to_string(), Value::Integer(100));
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "<div style=\"width: 75%\"></div>");
    }

    #[test]
    fn test_firstof_first_truthy() {
        let tokens = tokenize("{% firstof var1 var2 var3 %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("var2".to_string(), Value::String("hello".to_string()));
        context.set("var3".to_string(), Value::String("world".to_string()));
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "hello");
    }

    #[test]
    fn test_firstof_fallback() {
        let tokens = tokenize(r#"{% firstof var1 var2 "fallback" %}"#).unwrap();
        let nodes = parse(&tokens).unwrap();
        let context = Context::new();
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "fallback");
    }

    #[test]
    fn test_firstof_escapes_html() {
        let tokens = tokenize("{% firstof var1 %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set(
            "var1".to_string(),
            Value::String("<script>xss</script>".to_string()),
        );
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "&lt;script&gt;xss&lt;/script&gt;");
    }

    #[test]
    fn test_templatetag_openblock() {
        let tokens = tokenize("{% templatetag openblock %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let context = Context::new();
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "{%");
    }

    #[test]
    fn test_templatetag_openvariable() {
        let tokens = tokenize("{% templatetag openvariable %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let context = Context::new();
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "{{");
    }

    #[test]
    fn test_templatetag_all_types() {
        for (name, expected) in [
            ("openblock", "{%"),
            ("closeblock", "%}"),
            ("openvariable", "{{"),
            ("closevariable", "}}"),
            ("openbrace", "{"),
            ("closebrace", "}"),
            ("opencomment", "{#"),
            ("closecomment", "#}"),
        ] {
            let tokens = tokenize(&format!("{{% templatetag {name} %}}")).unwrap();
            let nodes = parse(&tokens).unwrap();
            let context = Context::new();
            assert_eq!(
                render_nodes(&nodes, &context).unwrap(),
                expected,
                "templatetag {name} failed"
            );
        }
    }

    #[test]
    fn test_spaceless() {
        let tokens =
            tokenize("{% spaceless %}<p>\n  <a href=\"foo\">Foo</a>\n</p>{% endspaceless %}")
                .unwrap();
        let nodes = parse(&tokens).unwrap();
        let context = Context::new();
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "<p><a href=\"foo\">Foo</a></p>");
    }

    #[test]
    fn test_spaceless_preserves_text_spaces() {
        let tokens = tokenize("{% spaceless %}<p> Hello World </p>{% endspaceless %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let context = Context::new();
        let result = render_nodes(&nodes, &context).unwrap();
        // Spaces inside text content should be preserved
        assert_eq!(result, "<p> Hello World </p>");
    }

    #[test]
    fn test_cycle_in_for_loop() {
        let tokens =
            tokenize("{% for item in items %}<tr class=\"{% cycle 'row1' 'row2' %}\">{{ item }}</tr>{% endfor %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set(
            "items".to_string(),
            Value::List(vec![
                Value::String("a".to_string()),
                Value::String("b".to_string()),
                Value::String("c".to_string()),
            ]),
        );
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(
            result,
            "<tr class=\"row1\">a</tr><tr class=\"row2\">b</tr><tr class=\"row1\">c</tr>"
        );
    }

    #[test]
    fn test_cycle_nested_for_loops() {
        // Inner loop cycle should not clobber outer loop cycle
        let tokens = tokenize(
            "{% for x in outer %}{% cycle 'A' 'B' %}{% for y in inner %}{% cycle '1' '2' '3' %}{% endfor %}{% endfor %}"
        ).unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set(
            "outer".to_string(),
            Value::List(vec![
                Value::String("a".to_string()),
                Value::String("b".to_string()),
            ]),
        );
        context.set(
            "inner".to_string(),
            Value::List(vec![
                Value::String("x".to_string()),
                Value::String("y".to_string()),
            ]),
        );
        let result = render_nodes(&nodes, &context).unwrap();
        // Outer: A(0), B(1). Inner always: 1(0), 2(1)
        assert_eq!(result, "A12B12");
    }

    #[test]
    fn test_firstof_dotted_path() {
        let tokens = tokenize("{% firstof user.name \"anonymous\" %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        let mut user = std::collections::HashMap::new();
        user.insert("name".to_string(), Value::String("Alice".to_string()));
        context.set("user".to_string(), Value::Object(user));
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "Alice");
    }

    #[test]
    fn test_now_basic_format() {
        // Test that {% now %} produces non-empty output with basic format
        let tokens = tokenize("{% now \"Y\" %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let context = Context::new();
        let result = render_nodes(&nodes, &context).unwrap();
        // Should be a 4-digit year
        assert_eq!(result.len(), 4);
        assert!(result.chars().all(|c| c.is_numeric()));
    }

    // Tests for issue #380: {% if %} inside HTML attribute values

    #[test]
    fn test_if_in_attribute_false_emits_empty_not_comment() {
        // #380: {% if %} inside attribute value must not emit <!--dj-if-->
        // when condition is false — that would produce malformed HTML.
        let template = r#"<div class="btn {% if active %}active{% endif %}"></div>"#;
        let tokens = tokenize(template).unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("active".to_string(), Value::Bool(false));
        let result = render_nodes(&nodes, &context).unwrap();
        assert!(
            !result.contains("<!--dj-if-->"),
            "comment must not appear in attribute: {result}"
        );
        assert!(
            result.contains(r#"class="btn ""#),
            "expected empty class suffix: {result}"
        );
    }

    #[test]
    fn test_if_in_attribute_true_renders_content() {
        // #380: When condition is true the normal branch must still render.
        let template = r#"<div class="btn {% if active %}active{% endif %}"></div>"#;
        let tokens = tokenize(template).unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("active".to_string(), Value::Bool(true));
        let result = render_nodes(&nodes, &context).unwrap();
        assert!(
            result.contains(r#"class="btn active""#),
            "expected active class: {result}"
        );
    }

    #[test]
    fn test_if_in_text_node_still_emits_comment() {
        // #380: Outside attribute context the <!--dj-if--> VDOM anchor must be preserved.
        let template = "<div>{% if show %}yes{% endif %}</div>";
        let tokens = tokenize(template).unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("show".to_string(), Value::Bool(false));
        let result = render_nodes(&nodes, &context).unwrap();
        assert!(
            result.contains("<!--dj-if-->"),
            "VDOM anchor must be present in text context: {result}"
        );
    }

    #[test]
    fn test_if_in_attribute_with_gt_in_value() {
        // Fix for review issue #2: bare > inside an attribute value must not
        // trick is_inside_html_tag() into thinking we are outside the tag.
        // e.g. title="a > b {% if show %}text{% endif %}" with show=False
        // must produce title="a > b " not title="a > b <!--dj-if-->".
        let template = r#"<div title="a > b {% if show %}text{% endif %}"></div>"#;
        let tokens = tokenize(template).unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("show".to_string(), Value::Bool(false));
        let result = render_nodes(&nodes, &context).unwrap();
        assert!(
            !result.contains("<!--dj-if-->"),
            "comment must not appear in attribute with > in value: {result}"
        );
        assert!(
            result.contains(r#"title="a > b ""#),
            "expected clean attribute value: {result}"
        );
    }

    #[test]
    fn test_if_in_attribute_with_single_quote_gt() {
        // Same check with single-quoted attribute value.
        let template = r#"<div title='x > y {% if show %}yes{% endif %}'></div>"#;
        let tokens = tokenize(template).unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("show".to_string(), Value::Bool(false));
        let result = render_nodes(&nodes, &context).unwrap();
        assert!(
            !result.contains("<!--dj-if-->"),
            "comment must not appear in single-quoted attribute with > in value: {result}"
        );
    }

    #[test]
    fn test_elif_in_attribute_both_false_emits_empty_not_comment() {
        // #382: {% if a %}...{% elif b %}...{% endif %} inside an attribute value
        // with both a=false and b=false must emit "" not "<!--dj-if-->".
        let template = r#"<div class="{% if a %}one{% elif b %}two{% endif %}"></div>"#;
        let tokens = tokenize(template).unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("a".to_string(), Value::Bool(false));
        context.set("b".to_string(), Value::Bool(false));
        let result = render_nodes(&nodes, &context).unwrap();
        assert!(
            !result.contains("<!--dj-if-->"),
            "comment must not appear in attribute when elif is false: {result}"
        );
        assert!(
            result.contains(r#"class="""#),
            "expected empty attribute value: {result}"
        );
    }

    #[test]
    fn test_elif_in_attribute_elif_branch_renders() {
        // #382: when a=false and b=true, the elif branch content must render.
        let template = r#"<div class="{% if a %}one{% elif b %}two{% endif %}"></div>"#;
        let tokens = tokenize(template).unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("a".to_string(), Value::Bool(false));
        context.set("b".to_string(), Value::Bool(true));
        let result = render_nodes(&nodes, &context).unwrap();
        assert!(
            result.contains(r#"class="two""#),
            "elif true branch must render in attribute: {result}"
        );
    }

    #[test]
    fn test_multiple_elif_in_attribute_all_false_emits_empty_not_comment() {
        // #382: 3-branch elif chain inside an attribute with a=b=c=false must
        // emit "" not "<!--dj-if-->". Verifies in_tag_context propagates through
        // all recursive parse_if_block calls, not just the first elif.
        let template = r#"<div class="{% if a %}a{% elif b %}b{% elif c %}c{% endif %}"></div>"#;
        let tokens = tokenize(template).unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("a".to_string(), Value::Bool(false));
        context.set("b".to_string(), Value::Bool(false));
        context.set("c".to_string(), Value::Bool(false));
        let result = render_nodes(&nodes, &context).unwrap();
        assert!(
            !result.contains("<!--dj-if-->"),
            "comment must not appear in attribute with 3-branch elif all false: {result}"
        );
        assert!(
            result.contains(r#"class="""#),
            "expected empty attribute value: {result}"
        );
    }

    #[test]
    fn test_value_list_serializes_as_json() {
        // value_to_arg_string should serialize Value::List as JSON
        // so Python tag handlers receive structured data, not "[List]"
        let list = Value::List(vec![
            Value::String("a".to_string()),
            Value::Integer(1),
            Value::Bool(true),
        ]);
        let json = serde_json::to_string(&list).unwrap();
        assert_eq!(json, r#"["a",1,true]"#);
    }

    #[test]
    fn test_value_object_serializes_as_json() {
        // value_to_arg_string should serialize Value::Object as JSON
        let mut map = std::collections::HashMap::new();
        map.insert("key".to_string(), Value::String("val".to_string()));
        let obj = Value::Object(map);
        let json = serde_json::to_string(&obj).unwrap();
        assert_eq!(json, r#"{"key":"val"}"#);
    }

    #[test]
    fn test_value_scalar_to_string_not_json() {
        // Scalars should use to_string(), not JSON serialization
        assert_eq!(Value::Integer(42).to_string(), "42");
        assert_eq!(Value::Bool(true).to_string(), "true");
        assert_eq!(Value::String("hello".to_string()).to_string(), "hello");
    }

    #[test]
    fn test_get_value_with_filter() {
        // get_value should resolve variables and apply pipe filters
        let mut context = Context::new();
        context.set(
            "items".to_string(),
            Value::List(vec![
                Value::String("a".to_string()),
                Value::String("b".to_string()),
                Value::String("c".to_string()),
            ]),
        );
        let result = get_value("items|length", &context).unwrap();
        assert_eq!(result.to_string(), "3");
    }

    #[test]
    fn test_get_value_with_chained_filters() {
        // get_value should handle chained filters like var|filter1|filter2
        let mut context = Context::new();
        context.set("name".to_string(), Value::String("hello".to_string()));
        let result = get_value("name|upper", &context).unwrap();
        assert_eq!(result.to_string(), "HELLO");
    }

    #[test]
    fn test_get_value_without_filter() {
        // get_value should still resolve plain variables
        let mut context = Context::new();
        context.set("count".to_string(), Value::Integer(42));
        let result = get_value("count", &context).unwrap();
        assert_eq!(result.to_string(), "42");
    }

    #[test]
    fn test_get_value_boolean_true_literal() {
        let context = Context::new();
        let val = get_value("True", &context).unwrap();
        assert!(
            matches!(val, Value::Bool(true)),
            "True should resolve to Bool(true)"
        );
        let val = get_value("true", &context).unwrap();
        assert!(
            matches!(val, Value::Bool(true)),
            "true should resolve to Bool(true)"
        );
    }

    #[test]
    fn test_get_value_boolean_false_literal() {
        let context = Context::new();
        let val = get_value("False", &context).unwrap();
        assert!(
            matches!(val, Value::Bool(false)),
            "False should resolve to Bool(false)"
        );
        let val = get_value("false", &context).unwrap();
        assert!(
            matches!(val, Value::Bool(false)),
            "false should resolve to Bool(false)"
        );
    }

    #[test]
    fn test_get_value_none_literal() {
        let context = Context::new();
        let val = get_value("None", &context).unwrap();
        assert!(matches!(val, Value::Null), "None should resolve to Null");
        let val = get_value("none", &context).unwrap();
        assert!(matches!(val, Value::Null), "none should resolve to Null");
    }

    #[test]
    fn test_get_value_context_shadows_literal() {
        // A context variable named "True" should take precedence over the literal
        let mut context = Context::new();
        context.set("True".to_string(), Value::String("not a bool".to_string()));
        let val = get_value("True", &context).unwrap();
        assert_eq!(val.to_string(), "not a bool");
    }
}
