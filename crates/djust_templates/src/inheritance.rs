//! Template inheritance support for Django-style {% extends %} and {% block %}
//!
//! This module handles:
//! - Detecting templates that use {% extends %}
//! - Loading parent templates recursively
//! - Building inheritance chains
//! - Merging blocks from child to parent
//! - Rendering final templates with block overrides

use crate::parser::Node;
use djust_core::{Context, DjangoRustError, Result};
use once_cell::sync::Lazy;
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, RwLock};
use std::time::SystemTime;

// Physical child lists shared by the immutable discovery walk and mutable
// override walk. Exhaustive matching makes new node variants require a
// traversal decision; no catch-all may silently hide a new container.
macro_rules! child_lists {
    ($node:expr) => {
        match $node {
            Node::If {
                true_nodes,
                false_nodes,
                ..
            } => [Some(true_nodes), Some(false_nodes)],
            Node::For {
                nodes, empty_nodes, ..
            } => [Some(nodes), Some(empty_nodes)],
            Node::IfChanged {
                nodes, else_nodes, ..
            } => [Some(nodes), Some(else_nodes)],
            Node::BlockSuperScope { super_nodes, nodes } => [Some(super_nodes), Some(nodes)],
            Node::Block { nodes, .. }
            | Node::With { nodes, .. }
            | Node::Spaceless { nodes }
            | Node::AutoEscape { nodes, .. }
            | Node::Filter { nodes, .. } => [Some(nodes), None],
            Node::ReactComponent { children, .. }
            | Node::BlockCustomTag { children, .. }
            | Node::Language { children, .. }
            | Node::Timezone { children, .. }
            | Node::Localize { children, .. }
            | Node::LocalTime { children, .. } => [Some(children), None],
            Node::Text(_)
            | Node::Variable(..)
            | Node::Extends(_)
            | Node::Include { .. }
            | Node::Comment
            | Node::Load(_)
            | Node::CsrfToken
            | Node::Static(_)
            | Node::RustComponent { .. }
            | Node::CustomTag { .. }
            | Node::WidthRatio { .. }
            | Node::FirstOf { .. }
            | Node::TemplateTag(_)
            | Node::Cycle { .. }
            | Node::ResetCycle { .. }
            | Node::Now(_)
            | Node::UnsupportedTag { .. }
            | Node::AssignTag { .. }
            | Node::InlineIf { .. }
            | Node::RawBlockCustomTag { .. } => [None, None],
        }
    };
}

/// Validate literal path syntax before any branch can be selected.
pub fn validate_relative_references(nodes: &[Node], name: &str) -> Result<()> {
    for node in nodes {
        let operand = match node {
            Node::Extends(token) => Some((token, name, false)),
            Node::Include {
                template, origin, ..
            } => Some((template, origin.as_deref().unwrap_or(name), true)),
            _ => None,
        };
        if let Some((token, origin, allow_recursion)) = operand {
            let bytes = token.as_bytes();
            let quoted = bytes.len() >= 2
                && matches!(bytes[0], b'\'' | b'"')
                && bytes[0] == bytes[bytes.len() - 1];
            if quoted && !crate::filter_lexer::has_unquoted_pipe(token) {
                construct_relative_path_allow_recursion(Some(origin), token, allow_recursion)?;
            }
        }
        for children in child_lists!(node).into_iter().flatten() {
            validate_relative_references(children, name)?;
        }
    }
    Ok(())
}

/// Prepare named nodes before rendering or storing them in a loader cache.
pub fn set_include_origins(nodes: &mut [Node], name: &str) -> Result<()> {
    validate_relative_references(nodes, name)?;
    attach_include_origins(nodes, name);
    Ok(())
}

/// Preserve the defining template name when includes move through inheritance.
fn attach_include_origins(nodes: &mut [Node], name: &str) {
    for node in nodes {
        if let Node::Include { origin, .. } = node {
            if origin.is_none() {
                *origin = Some(name.to_string());
            }
        }
        for children in child_lists!(node).into_iter().flatten() {
            attach_include_origins(children, name);
        }
    }
}

/// Represents a template in the inheritance chain
#[derive(Debug, Clone)]
pub struct TemplateLayer {
    pub nodes: Vec<Node>,
    pub blocks: HashMap<String, Vec<Node>>,
}

/// Represents a complete inheritance chain from child to root
#[derive(Debug)]
pub struct InheritanceChain {
    pub layers: Vec<TemplateLayer>, // Index 0 = child, last = root parent
    pub merged_blocks: HashMap<String, Vec<Node>>,
    pub parent_blocks: HashMap<String, Vec<Node>>, // Parent block content for {{ block.super }}
}

impl InheritanceChain {
    /// Create a new inheritance chain from parsed nodes
    pub fn new(nodes: Vec<Node>) -> Self {
        let layer = TemplateLayer {
            nodes: nodes.clone(),
            blocks: extract_blocks(&nodes),
        };

        InheritanceChain {
            layers: vec![layer],
            merged_blocks: HashMap::new(),
            parent_blocks: HashMap::new(),
        }
    }

    /// Check if this template uses extends
    pub fn uses_extends(&self) -> Option<&str> {
        // Check the most recently added layer (last in the chain)
        // This allows us to follow the inheritance chain upward
        if let Some(last_layer) = self.layers.last() {
            for node in &last_layer.nodes {
                if let Node::Extends(parent) = node {
                    return Some(parent);
                }
            }
        }
        None
    }

    /// Add a parent layer to the chain
    pub fn add_parent(&mut self, parent_nodes: Vec<Node>) {
        let parent_layer = TemplateLayer {
            nodes: parent_nodes.clone(),
            blocks: extract_blocks(&parent_nodes),
        };
        self.layers.push(parent_layer);
    }

    /// Merge blocks from all layers (child overrides parent)
    ///
    /// A body that references `{{ block.super }}` is wrapped in a
    /// [`Node::BlockSuperScope`] carrying the version it overrides, so the
    /// reference resolves at render time (#2517). Wrapping happens as the
    /// chain is walked root -> child, which makes the nesting fall out for a
    /// chain of any depth: layer 3's scope holds layer 2's scope, which holds
    /// layer 1's plain body.
    ///
    /// `parent_blocks` keeps the immediate-parent map it always did; it is the
    /// flat view, and nothing reads it for `block.super` any more.
    pub fn merge_blocks(&mut self) {
        let mut merged: HashMap<String, Vec<Node>> = HashMap::new();
        let mut parents: HashMap<String, Vec<Node>> = HashMap::new();

        // Start from root (parent) and work toward child (first layer)
        // Track parent content before it gets overridden by child
        for layer in self.layers.iter().rev() {
            for (name, nodes) in &layer.blocks {
                // If this block already exists, save current content as parent
                if let Some(existing) = merged.get(name) {
                    parents.insert(name.clone(), existing.clone());
                }
                let content = match merged.get(name) {
                    // Only a body that actually references `block.super` gets
                    // the wrapper: Django resolves it lazily, so an unwrapped
                    // body must not render its parent at all.
                    Some(previous) if nodes_reference_block_super(nodes) => {
                        vec![Node::BlockSuperScope {
                            super_nodes: previous.clone(),
                            nodes: nodes.clone(),
                        }]
                    }
                    _ => nodes.clone(),
                };
                merged.insert(name.clone(), content);
            }
        }

        self.merged_blocks = merged;
        self.parent_blocks = parents;
    }

    /// Get the root template nodes (furthest ancestor)
    pub fn get_root_nodes(&self) -> &[Node] {
        &self.layers.last().unwrap().nodes
    }

    /// Replace blocks in nodes with merged block content
    pub fn apply_block_overrides(&self, nodes: &[Node]) -> Vec<Node> {
        nodes
            .iter()
            .map(|node| self.apply_override_to_node(node))
            .collect()
    }

    fn apply_override_to_node(&self, node: &Node) -> Node {
        let mut overridden = match node {
            Node::Block { name, nodes } => Node::Block {
                name: name.clone(),
                nodes: self.merged_blocks.get(name).unwrap_or(nodes).clone(),
            },
            Node::Extends(_) => return Node::Comment,
            _ => node.clone(),
        };
        for children in child_lists!(&mut overridden).into_iter().flatten() {
            *children = self.apply_block_overrides(children);
        }
        overridden
    }
}

/// Does this node tree reference `{{ block.super }}`?
///
/// The reference is an ordinary dotted lookup — `Node::Variable("block.super")`
/// — so this is a plain walk rather than anything tag-aware. Used to decide
/// whether a block body needs its parent rendered at all (#2517).
fn nodes_reference_block_super(nodes: &[Node]) -> bool {
    nodes.iter().any(node_references_block_super)
}

fn node_references_block_super(node: &Node) -> bool {
    /// Does any of these expression strings mention it?
    fn any_expr<'a>(exprs: impl IntoIterator<Item = &'a str>) -> bool {
        exprs.into_iter().any(|e| e.contains("block.super"))
    }

    match node {
        // ---- expression-bearing leaves ------------------------------------
        Node::Variable(name, filters, _) => {
            name.trim() == "block.super"
                || any_expr(filters.iter().filter_map(|(_, a)| a.as_deref()))
        }
        Node::InlineIf {
            true_expr,
            condition,
            false_expr,
            filters,
        } => {
            any_expr([true_expr.as_str(), condition.as_str(), false_expr.as_str()])
                || any_expr(filters.iter().filter_map(|(_, a)| a.as_deref()))
        }
        Node::FirstOf { args, .. }
        | Node::CustomTag { args, .. }
        | Node::AssignTag { args, .. }
        | Node::UnsupportedTag { args, .. } => any_expr(args.iter().map(String::as_str)),
        Node::Cycle { values, .. } => any_expr(values.iter().map(String::as_str)),
        Node::WidthRatio {
            value,
            max_value,
            max_width,
            ..
        } => any_expr([value.as_str(), max_value.as_str(), max_width.as_str()]),
        Node::Include {
            template,
            with_vars,
            ..
        } => any_expr([template.as_str()]) || any_expr(with_vars.iter().map(|(_, v)| v.as_str())),

        // ---- containers, with their own expression fields ------------------
        Node::If {
            condition,
            true_nodes,
            false_nodes,
            ..
        } => {
            any_expr([condition.as_str()])
                || nodes_reference_block_super(true_nodes)
                || nodes_reference_block_super(false_nodes)
        }
        Node::For {
            iterable,
            nodes,
            empty_nodes,
            ..
        } => {
            any_expr([iterable.as_str()])
                || nodes_reference_block_super(nodes)
                || nodes_reference_block_super(empty_nodes)
        }
        Node::With { assignments, nodes } => {
            any_expr(assignments.iter().map(|(_, v)| v.as_str()))
                || nodes_reference_block_super(nodes)
        }
        Node::Filter { filters, nodes } => {
            any_expr(filters.iter().filter_map(|(_, a)| a.as_deref()))
                || nodes_reference_block_super(nodes)
        }
        Node::Block { nodes, .. }
        | Node::Spaceless { nodes, .. }
        | Node::AutoEscape { nodes, .. } => nodes_reference_block_super(nodes),
        Node::IfChanged {
            vars,
            nodes,
            else_nodes,
            ..
        } => {
            any_expr(vars.iter().map(String::as_str))
                || nodes_reference_block_super(nodes)
                || nodes_reference_block_super(else_nodes)
        }
        Node::BlockSuperScope { super_nodes, nodes } => {
            nodes_reference_block_super(super_nodes) || nodes_reference_block_super(nodes)
        }
        Node::Localize { children, .. } | Node::LocalTime { children, .. } => {
            nodes_reference_block_super(children)
        }
        Node::BlockCustomTag { args, children, .. } => {
            any_expr(args.iter().map(String::as_str)) || nodes_reference_block_super(children)
        }
        Node::ReactComponent {
            props, children, ..
        } => {
            any_expr(props.iter().map(|(_, v)| v.as_str())) || nodes_reference_block_super(children)
        }
        Node::RustComponent { props, .. } => any_expr(props.iter().map(|(_, v)| v.as_str())),
        // `{% blocktranslate with s=block.super %}` — the operands are in
        // `args`, and the BODY is raw source that can name it in a placeholder.
        Node::RawBlockCustomTag { args, body, .. } => {
            any_expr(args.iter().map(String::as_str)) || any_expr([body.as_str()])
        }
        // `{% language block.super %}` / `{% timezone block.super %}` — the
        // scope OPERAND, which the container arm only recursed past.
        Node::Language { expr, children } | Node::Timezone { expr, children } => {
            any_expr([expr.as_str()]) || nodes_reference_block_super(children)
        }

        // ---- variants that carry no expression at all ---------------------
        //
        // Listed EXPLICITLY, with no `_` arm, so the compiler refuses to build
        // when a variant is added without deciding this question. The previous
        // version ended in `_ => false` under a comment claiming the list was
        // "exhaustive by construction" — it was not, and three variants fell
        // through it: `{% blocktranslate with s=block.super %}` rendered EMPTY,
        // `{% language block.super %}` silently no-opped, and
        // `{% timezone block.super %}` raised. A claim a comment makes and the
        // compiler does not is the failure mode this arm removes.
        //
        // The default direction stays "do not render the parent": an earlier
        // `_ => true` traded a silent under-render for an EAGER parent render,
        // which advances a `{% cycle %}` in the parent block and can raise
        // `TemplateDoesNotExist` on a template that worked.
        Node::Text(_)
        | Node::Comment
        | Node::Load(_)
        | Node::TemplateTag(_)
        | Node::CsrfToken
        | Node::Static(_)
        | Node::Now(_)
        | Node::Extends(_)
        | Node::ResetCycle { .. } => false,
    }
}

/// Extract all {% block %} tags from nodes and map them by name
fn extract_blocks(nodes: &[Node]) -> HashMap<String, Vec<Node>> {
    let mut blocks = HashMap::new();

    for node in nodes {
        extract_blocks_recursive(node, &mut blocks);
    }

    blocks
}

fn extract_blocks_recursive(node: &Node, blocks: &mut HashMap<String, Vec<Node>>) {
    // Django SimpleBlockNode inherits SimpleNode.child_nodelists = (). Its
    // body renders nested blocks, but does not declare descendant overrides.
    if matches!(node, Node::BlockCustomTag { .. }) {
        return;
    }
    if let Node::Block { name, nodes } = node {
        blocks.insert(name.clone(), nodes.clone());
    }
    for children in child_lists!(node).into_iter().flatten() {
        for child in children {
            extract_blocks_recursive(child, blocks);
        }
    }
}

/// Trait for loading parent templates
/// This will be implemented by the Python integration layer
pub trait TemplateLoader {
    fn load_template(&self, name: &str) -> Result<Vec<Node>>;

    /// Identity of the first source this loader would select, when available.
    fn template_origin(&self, _name: &str) -> Option<String> {
        None
    }

    /// Load an ancestor while excluding origins already in this extends chain.
    /// Loaders without origin support retain their existing depth-limit behavior.
    fn load_template_skipping(
        &self,
        name: &str,
        _skip: &[String],
    ) -> Result<(Vec<Node>, Option<String>)> {
        Ok((self.load_template(name)?, None))
    }

    /// Like [`load_template`](Self::load_template), but returns a
    /// reference-counted, immutable slice whose ALLOCATION is stable across
    /// calls when the underlying source is unchanged (#2074).
    ///
    /// The `{% for %}` loop-render cache (#2067) keys each For-node's body
    /// by identity — `(nodes.as_ptr() as usize, nodes.len())`
    /// (`renderer.rs::content_hash` call site) — so it can distinguish
    /// sibling loops that share a loop-variable name. That identity is only
    /// meaningful if the SAME body allocation is seen across renders; a
    /// loader that reparses from scratch on every call (the default
    /// [`load_template`](Self::load_template) contract) hands back a fresh
    /// `Vec<Node>` — and therefore a fresh pointer — every time, so a
    /// `{% for %}` loop living inside an `{% include %}` never accumulates
    /// cache hits.
    ///
    /// The default implementation just wraps [`load_template`](Self::load_template)
    /// in a fresh `Arc` each call — correct (no cross-render identity is
    /// promised or required) for loaders that don't need cross-render
    /// stability, e.g. in-memory test loaders and other loaders with no
    /// stable backing store to key a cache off of. [`FilesystemTemplateLoader`]
    /// overrides this with a real mtime-keyed cache — see its impl for why
    /// the cache must be loader-instance-EXTERNAL (a process-global static,
    /// not a `&self` field).
    fn load_template_cached(&self, name: &str) -> Result<std::sync::Arc<[Node]>> {
        let mut nodes = self.load_template(name)?;
        set_include_origins(&mut nodes, name)?;
        Ok(std::sync::Arc::from(nodes))
    }
}

/// Django's `loader_tags.construct_relative_path` (#2517).
///
/// `{% extends "./two.html" %}` in `dir1/one.html` means `dir1/two.html`, and
/// `../one.html` means `one.html`. A name that does not start with `./` or
/// `../` is returned unchanged, so this is a no-op for every absolute name.
///
/// Django raises rather than escaping the template root, and refuses a
/// relative path that resolves to the template ITSELF (that is a guaranteed
/// infinite `{% extends %}` loop, and the message names it as such). Both
/// refusals are Django's, message for message.
pub fn construct_relative_path(
    current_template_name: Option<&str>,
    relative_name: &str,
) -> Result<String> {
    construct_relative_path_allow_recursion(current_template_name, relative_name, false)
}

pub fn construct_relative_path_allow_recursion(
    current_template_name: Option<&str>,
    relative_name: &str,
    allow_recursion: bool,
) -> Result<String> {
    let new_name = relative_name.trim_matches(|c| c == '"' || c == '\'');
    if !(new_name.starts_with("./") || new_name.starts_with("../")) {
        return Ok(relative_name.to_string());
    }
    // Without a name for the CURRENT template there is nothing to be relative
    // to; Django is in the same position for a string-built template and
    // leaves the name alone.
    let Some(current) = current_template_name else {
        return Ok(relative_name.to_string());
    };

    let current = current.trim_start_matches('/');
    let dir = match current.rsplit_once('/') {
        Some((head, _)) => head,
        None => "",
    };

    // `posixpath.normpath(posixpath.join(dir, new_name))`, spelled out: `/`
    // is the separator on every template name regardless of host platform,
    // so this must NOT go through `std::path`.
    let joined = if dir.is_empty() {
        new_name.to_string()
    } else {
        format!("{dir}/{new_name}")
    };
    let mut parts: Vec<&str> = Vec::new();
    for segment in joined.split('/') {
        match segment {
            "" | "." => {}
            ".." => {
                if matches!(parts.last(), Some(&last) if last != "..") {
                    parts.pop();
                } else {
                    parts.push("..");
                }
            }
            other => parts.push(other),
        }
    }
    let normalized = parts.join("/");

    if normalized.starts_with("../") || normalized == ".." {
        return Err(DjangoRustError::TemplateSyntax(format!(
            "The relative path '{relative_name}' points outside the file hierarchy that template '{current}' is in."
        )));
    }
    if !allow_recursion && normalized == current {
        return Err(DjangoRustError::TemplateSyntax(format!(
            "The relative path '{relative_name}' was translated to template name '{normalized}', the same template in which the tag appears."
        )));
    }
    Ok(normalized)
}

/// Build complete inheritance chain by recursively loading parents
pub fn build_inheritance_chain<L: TemplateLoader>(
    nodes: Vec<Node>,
    loader: &L,
    max_depth: usize,
) -> Result<InheritanceChain> {
    build_inheritance_chain_from(nodes, loader, max_depth, None, None)
}

/// Resolve an `{% extends %}` operand to a template NAME (#2517).
///
/// Django compiles the operand as a `FilterExpression`, so a quoted token is
/// a literal and anything else is a context lookup. Without a context (the
/// `resolve_inheritance` pre-pass, which runs before any render) an unquoted
/// token is returned as-is — the same string the pre-#2517 code used, so that
/// path is unchanged.
fn resolve_extends_target(token: &str, context: Option<&Context>) -> Result<String> {
    let trimmed = token.trim();
    if let Some(context) = context {
        // Resolve the complete expression, including quoted initial operands.
        // Checking only its first and last quotes misreads 'base'|cut:'x'.
        return crate::renderer::resolve_extends_operand(trimmed, context);
    }
    // The context-free preprocessing API retains its literal-name behavior.
    let bytes = trimmed.as_bytes();
    let quoted = bytes.len() >= 2
        && ((bytes[0] == b'"' && bytes[bytes.len() - 1] == b'"')
            || (bytes[0] == b'\'' && bytes[bytes.len() - 1] == b'\''));
    Ok(if quoted {
        crate::parser::unescape_filter_arg_literal(trimmed).into_owned()
    } else {
        trimmed.to_string()
    })
}

/// [`build_inheritance_chain`] that knows the name of the template it starts
/// from, so `{% extends "./parent.html" %}` can resolve (#2517).
///
/// The name is threaded DOWN the chain: each layer's relative reference
/// resolves against the name of the template that declared it, which is what
/// makes `dir1/one.html` -> `./dir2/one.html` -> `../three.html` land on
/// `dir1/three.html` rather than on the root's directory.
pub fn build_inheritance_chain_from<L: TemplateLoader>(
    nodes: Vec<Node>,
    loader: &L,
    max_depth: usize,
    template_name: Option<&str>,
    context: Option<&Context>,
) -> Result<InheritanceChain> {
    let mut nodes = nodes;
    if let Some(name) = template_name {
        set_include_origins(&mut nodes, name)?;
    }
    let mut chain = InheritanceChain::new(nodes);
    let mut depth = 0;
    let mut current_name: Option<String> = template_name.map(str::to_string);
    let mut history: Vec<String> = template_name
        .and_then(|name| loader.template_origin(name))
        .into_iter()
        .collect();

    // Follow extends chain up to max_depth
    while depth < max_depth {
        if let Some(parent_name) = chain.uses_extends() {
            let parent_token = parent_name.to_string();
            let parent_name = resolve_extends_target(&parent_token, context)?;
            let quoted = parent_token.starts_with(['\'', '"'])
                && !crate::filter_lexer::has_unquoted_pipe(&parent_token);
            let parent_name = if quoted {
                // Validate the raw token for Django's diagnostic spelling,
                // then resolve the decoded literal for the filesystem lookup.
                construct_relative_path(current_name.as_deref(), &parent_token)?;
                construct_relative_path(current_name.as_deref(), &parent_name)?
            } else {
                parent_name
            };
            let (mut parent_nodes, origin) =
                loader.load_template_skipping(&parent_name, &history)?;
            if let Some(origin) = origin {
                history.push(origin);
            }
            set_include_origins(&mut parent_nodes, &parent_name)?;
            chain.add_parent(parent_nodes);
            current_name = Some(parent_name);
            depth += 1;
        } else {
            // No more parents
            break;
        }
    }

    if depth >= max_depth {
        return Err(DjangoRustError::TemplateError(format!(
            "Template inheritance depth limit ({max_depth}) exceeded - possible circular inheritance"
        )));
    }

    // Merge all blocks
    chain.merge_blocks();

    Ok(chain)
}

/// `(mtime-at-parse-time, parsed nodes)` — see [`PARSED_TEMPLATE_CACHE`].
type ParsedTemplateEntry = (SystemTime, Arc<[Node]>);

/// Process-global cache of parsed `{% include %}`-able template bodies,
/// keyed by the RESOLVED filesystem path and invalidated by mtime (#2074).
///
/// This is a process-wide `static`, NOT a `FilesystemTemplateLoader`
/// instance field. Production (`crates/djust_live/src/lib.rs`) constructs a
/// FRESH `FilesystemTemplateLoader` on EVERY render call —
/// `let loader = FilesystemTemplateLoader::new(self.template_dirs.clone());`
/// appears in `render()`, `render_with_diff()`, and every other render
/// entry point — while the parsed parent `Template` and the
/// `LoopRenderCache` are held PERSISTENTLY across renders (the
/// `TEMPLATE_CACHE` static + the `self.loop_render_cache` instance field,
/// respectively). An instance-scoped cache field on
/// `FilesystemTemplateLoader` would be discarded and rebuilt on every
/// single render — the Arc it hands back would still get a fresh
/// allocation every render, and the #2067 loop-cache's body-identity key
/// would never see a repeat pointer. Only a cache that outlives the loader
/// instance can give an include's parsed body a stable identity across
/// renders. Mirrors the existing `Lazy<RwLock<HashMap<...>>>` pattern
/// already used for the filter/tag registries in this crate
/// (`filter_registry.rs`, `registry.rs`).
///
/// HVR/hot-reload tradeoff: pickup of an edited include is mtime-granularity-
/// bound. An include edited twice within a single coarse-mtime tick (some
/// filesystems have 1s mtime resolution) can serve the stale parse until the
/// NEXT mtime change — a minor HVR-robustness regression vs the pre-#2074
/// always-re-parse behavior. Acceptable because dev filesystems (APFS/ext4)
/// are sub-second and production templates are immutable.
static PARSED_TEMPLATE_CACHE: Lazy<RwLock<HashMap<PathBuf, ParsedTemplateEntry>>> =
    Lazy::new(|| RwLock::new(HashMap::new()));

/// Is this template name contained within its search directory?
///
/// djust's port of what Django's loaders get from `safe_join`
/// (`django/template/loaders/filesystem.py` raises `SuspiciousFileOperation`
/// and the loader skips that directory). Without it,
/// `FilesystemTemplateLoader::find_template` did a bare `dir.join(name)`, so a
/// name that walked out of the directory read whatever it landed on.
///
/// That was reachable only from a template author's literal until
/// `{% include %}` / `{% extends %}` began resolving an unquoted operand from
/// the render CONTEXT — at which point `{% include chosen %}` with a
/// context-supplied `chosen` becomes an arbitrary-file read. Measured before
/// this guard: `{% include t %}` with `t = "../../SECRET.txt"` rendered the
/// file's contents; Django answers `TemplateDoesNotExist` for the same input.
///
/// The check is LEXICAL and runs before any filesystem call, so it cannot be
/// defeated by a symlink race and does not require the path to exist. It
/// refuses:
///
/// * an absolute name (`/etc/passwd`, or a Windows drive/UNC prefix), and
/// * any name whose `..` segments pop above the search directory — at ANY
///   position, not merely as a prefix. `a/../../x` is refused exactly like
///   `../x`; the prefix-only reading is what left `construct_relative_path`'s
///   refusals incomplete.
///
/// A `..` that stays inside is allowed, because it names a real template:
/// `a/b/../c.html` is `a/c.html`.
fn template_name_is_contained(name: &str) -> bool {
    if name.is_empty() {
        return false;
    }
    // Absolute in the host's terms (covers `/x`, and `C:\x` / `\\host\share`
    // on Windows), or a bare leading separator.
    if std::path::Path::new(name).is_absolute() || name.starts_with('/') || name.starts_with('\\') {
        return false;
    }
    let mut depth: i32 = 0;
    // Split on BOTH separators: a Windows-style name reaches this on any host
    // (template names travel in template source, not from the local FS).
    for part in name.split(['/', '\\']) {
        match part {
            "" | "." => {}
            ".." => {
                depth -= 1;
                if depth < 0 {
                    return false;
                }
            }
            _ => depth += 1,
        }
    }
    true
}

/// Filesystem-based template loader for production use
pub struct FilesystemTemplateLoader {
    template_dirs: Vec<std::path::PathBuf>,
}

impl FilesystemTemplateLoader {
    /// Create a new filesystem template loader with search directories
    pub fn new(template_dirs: Vec<std::path::PathBuf>) -> Self {
        Self { template_dirs }
    }

    /// Find a template file by searching through template directories
    fn find_template(&self, name: &str) -> Result<PathBuf> {
        self.find_template_skipping(name, &[])
    }

    fn find_template_skipping(&self, name: &str, skip: &[String]) -> Result<PathBuf> {
        let mut tried = Vec::new();
        let mut skipped = Vec::new();
        for dir in &self.template_dirs {
            // Check containment before joining or touching the filesystem.
            if !template_name_is_contained(name) {
                continue;
            }
            // Django safe_join uses absolute, lexically normalized names.
            // Do not canonicalize symlinks: distinct loader origins can point
            // at the same physical file and remain distinct in Django.
            let absolute = std::path::absolute(dir.join(name))?;
            let mut path = PathBuf::new();
            for component in absolute.components() {
                match component {
                    std::path::Component::ParentDir => {
                        path.pop();
                    }
                    std::path::Component::CurDir => {}
                    other => path.push(other.as_os_str()),
                }
            }
            let origin = path.to_string_lossy().into_owned();
            tried.push(origin.clone());
            if skip.contains(&origin) {
                skipped.push(origin);
                continue;
            }
            if path.is_file() {
                return Ok(path);
            }
        }
        if skipped.is_empty() {
            Err(DjangoRustError::TemplateNotFound {
                name: name.to_string(),
                tried,
            })
        } else {
            Err(DjangoRustError::TemplateHistoryNotFound {
                name: name.to_string(),
                tried,
                skipped,
            })
        }
    }

    fn parse_template_path(&self, name: &str, path: &std::path::Path) -> Result<Vec<Node>> {
        let source = std::fs::read_to_string(path).map_err(|e| {
            DjangoRustError::TemplateError(format!(
                "Failed to read template {}: {}",
                path.display(),
                e
            ))
        })?;
        let tokens = crate::lexer::tokenize(&source)?;
        let mut nodes = crate::parser::parse_with_source(&tokens, &source)
            .map_err(DjangoRustError::into_template_syntax)?;
        set_include_origins(&mut nodes, name)?;
        Ok(nodes)
    }
}

impl TemplateLoader for FilesystemTemplateLoader {
    fn load_template(&self, name: &str) -> Result<Vec<Node>> {
        self.parse_template_path(name, &self.find_template(name)?)
    }

    fn template_origin(&self, name: &str) -> Option<String> {
        self.find_template(name)
            .ok()
            .map(|path| path.to_string_lossy().into_owned())
    }

    fn load_template_skipping(
        &self,
        name: &str,
        skip: &[String],
    ) -> Result<(Vec<Node>, Option<String>)> {
        let path = self.find_template_skipping(name, skip)?;
        let nodes = self.parse_template_path(name, &path)?;
        Ok((nodes, Some(path.to_string_lossy().into_owned())))
    }

    /// Cached counterpart of [`load_template`](Self::load_template) — see
    /// [`TemplateLoader::load_template_cached`] and [`PARSED_TEMPLATE_CACHE`]
    /// for why this must be a process-global cache rather than a `&self`
    /// field. Keyed by the RESOLVED path (not the raw `name` argument) so
    /// two loader instances with different `template_dirs` search orders
    /// that resolve to the SAME file share the cache entry; invalidated by
    /// mtime so an on-disk edit (including a hot-reload save) is picked up
    /// on the next call without any explicit `clear()`/invalidation wiring.
    fn load_template_cached(&self, name: &str) -> Result<Arc<[Node]>> {
        use crate::lexer;
        use crate::parser;

        let path = self.find_template(name)?;
        let mtime = std::fs::metadata(&path)
            .and_then(|m| m.modified())
            .map_err(|e| {
                DjangoRustError::TemplateError(format!(
                    "Failed to stat template {}: {}",
                    path.display(),
                    e
                ))
            })?;

        // Fast path: a read-locked cache hit for the CURRENT mtime.
        {
            let cache = PARSED_TEMPLATE_CACHE.read().map_err(|e| {
                DjangoRustError::TemplateError(format!("Template parse cache lock: {e}"))
            })?;
            if let Some((cached_mtime, nodes)) = cache.get(&path) {
                if *cached_mtime == mtime {
                    return Ok(nodes.clone());
                }
            }
        }

        // Slow path: no entry, or the file changed since it was cached —
        // reparse and (re)populate. Uses the same `parse_with_source` call
        // as `load_template` so the boundary-marker ID prefix is identical
        // regardless of which method a caller used.
        let source = std::fs::read_to_string(&path).map_err(|e| {
            DjangoRustError::TemplateError(format!(
                "Failed to read template {}: {}",
                path.display(),
                e
            ))
        })?;
        let tokens = lexer::tokenize(&source)?;
        let mut nodes_vec = parser::parse_with_source(&tokens, &source)
            .map_err(DjangoRustError::into_template_syntax)?;
        set_include_origins(&mut nodes_vec, name)?;
        let arc: Arc<[Node]> = Arc::from(nodes_vec);

        let mut cache = PARSED_TEMPLATE_CACHE.write().map_err(|e| {
            DjangoRustError::TemplateError(format!("Template parse cache lock: {e}"))
        })?;
        cache.insert(path, (mtime, arc.clone()));
        Ok(arc)
    }
}

/// Convert AST nodes back to template string format (preserves Django syntax)
fn nodes_to_template_string(nodes: &[Node]) -> String {
    let mut output = String::new();
    for node in nodes {
        output.push_str(&node_to_template_string(node));
    }
    output
}

/// Convert a single node back to template string format
fn node_to_template_string(node: &Node) -> String {
    match node {
        Node::Text(text) => text.clone(),
        Node::Variable(var_name, filters, _in_attr) => {
            let mut result = format!("{{{{ {var_name} ");
            for (filter_name, arg) in filters {
                if let Some(arg) = arg {
                    // Emit arg verbatim — `parse_filter_specs` preserves any
                    // surrounding quotes (literal-vs-identifier disambiguation
                    // for the dep-tracking extractor, see #787). Wrapping in
                    // an extra `"…"` would double-quote literals like
                    // `|date:"M d, Y"` to `|date:""M d, Y""`, and the second
                    // strip pass would leave the inner `"…"` baked into the
                    // format spec — surfacing as `&quot;Apr 25, 2026&quot;`
                    // in the rendered output of inheritance-resolved
                    // templates (#1081).
                    result.push_str(&format!("|{filter_name}:{arg} "));
                } else {
                    result.push_str(&format!("|{filter_name} "));
                }
            }
            result.push_str("}}");
            result
        }
        Node::InlineIf {
            true_expr,
            condition,
            false_expr,
            filters,
        } => {
            let mut result = format!("{{{{ {true_expr} if {condition}");
            if !false_expr.is_empty() {
                result.push_str(&format!(" else {false_expr}"));
            }
            for (filter_name, arg) in filters {
                if let Some(arg) = arg {
                    // Same #1081 fix as Variable above — emit arg verbatim
                    // since `parse_filter_specs` preserves surrounding quotes.
                    result.push_str(&format!("|{filter_name}:{arg}"));
                } else {
                    result.push_str(&format!("|{filter_name}"));
                }
            }
            result.push_str(" }}");
            result
        }
        Node::Block { name, nodes } => {
            let mut result = format!("{{% block {name} %}}");
            result.push_str(&nodes_to_template_string(nodes));
            result.push_str("{% endblock %}");
            result
        }
        Node::If {
            condition,
            true_nodes,
            false_nodes,
            ..
        } => {
            let mut result = format!("{{% if {condition} %}}");
            result.push_str(&nodes_to_template_string(true_nodes));
            if !false_nodes.is_empty() {
                result.push_str("{% else %}");
                result.push_str(&nodes_to_template_string(false_nodes));
            }
            result.push_str("{% endif %}");
            result
        }
        Node::For {
            var_names,
            iterable,
            reversed,
            nodes,
            empty_nodes,
        } => {
            let var_names_str = var_names.join(", ");
            let mut result = format!("{{% for {var_names_str} in {iterable}");
            if *reversed {
                result.push_str(" reversed");
            }
            result.push_str(" %}");
            result.push_str(&nodes_to_template_string(nodes));
            if !empty_nodes.is_empty() {
                result.push_str("{% empty %}");
                result.push_str(&nodes_to_template_string(empty_nodes));
            }
            result.push_str("{% endfor %}");
            result
        }
        Node::With { assignments, nodes } => {
            let mut result = String::from("{% with ");
            for (i, (key, value)) in assignments.iter().enumerate() {
                if i > 0 {
                    result.push(' ');
                }
                result.push_str(&format!("{key}={value}"));
            }
            result.push_str(" %}");
            result.push_str(&nodes_to_template_string(nodes));
            result.push_str("{% endwith %}");
            result
        }
        Node::Comment => String::new(), // Comments are stripped
        Node::Load(libs) => {
            // Preserve {% load %} tags so downstream Django rendering can resolve them
            format!("{{% load {} %}}", libs.join(" "))
        }
        Node::Extends(_) => String::new(), // Extends is already processed
        Node::Include {
            template,
            with_vars,
            only,
            ..
        } => {
            let mut result = format!("{{% include {template}");
            if !with_vars.is_empty() {
                result.push_str(" with");
                for (key, value) in with_vars {
                    result.push_str(&format!(" {key}={value}"));
                }
            }
            if *only {
                result.push_str(" only");
            }
            result.push_str(" %}");
            result
        }
        Node::CsrfToken => "{% csrf_token %}".to_string(),
        Node::Static(path) => format!("{{% static \"{path}\" %}}"),
        Node::ReactComponent { .. } => {
            // React components should be preserved as-is if possible
            // For now, skip them as they're handled separately
            String::new()
        }
        Node::RustComponent { .. } => {
            // Rust components should be preserved as-is if possible
            // For now, skip them as they're handled separately
            String::new()
        }
        Node::CustomTag { name, args } => {
            // Reconstruct custom tag: {% tagname arg1 arg2 %}
            let mut result = format!("{{% {name}");
            for arg in args {
                result.push(' ');
                result.push_str(arg);
            }
            result.push_str(" %}");
            result
        }
        Node::UnsupportedTag { name, args } => {
            // Reconstruct unsupported tag as-is for debugging
            let mut result = format!("{{% {name}");
            for arg in args {
                result.push(' ');
                result.push_str(arg);
            }
            result.push_str(" %}");
            result
        }
        Node::WidthRatio {
            value,
            max_value,
            max_width,
            asvar,
        } => {
            let tail = asvar
                .as_deref()
                .map(|v| format!(" as {v}"))
                .unwrap_or_default();
            format!("{{% widthratio {value} {max_value} {max_width}{tail} %}}")
        }
        Node::FirstOf { args, asvar } => {
            let tail = asvar
                .as_deref()
                .map(|v| format!(" as {v}"))
                .unwrap_or_default();
            format!("{{% firstof {}{tail} %}}", args.join(" "))
        }
        Node::TemplateTag(name) => {
            format!("{{% templatetag {name} %}}")
        }
        Node::Spaceless { nodes } => {
            let mut result = "{% spaceless %}".to_string();
            result.push_str(&nodes_to_template_string(nodes));
            result.push_str("{% endspaceless %}");
            result
        }
        Node::BlockSuperScope { nodes, .. } => {
            // Round-tripping to SOURCE cannot express the paired parent body;
            // the scope is a merge-time construct and a reparse rebuilds it
            // from the chain. Emitting the child body alone is what the
            // pre-#2517 flattening emitted for the same input.
            nodes_to_template_string(nodes)
        }
        Node::IfChanged {
            vars,
            nodes,
            else_nodes,
            ..
        } => {
            // The id is NOT re-emitted: `resolve_cycle_nodes` assigns it on
            // the reparse, exactly as it does for `{% cycle %}` above.
            let mut result = if vars.is_empty() {
                "{% ifchanged %}".to_string()
            } else {
                format!("{{% ifchanged {} %}}", vars.join(" "))
            };
            result.push_str(&nodes_to_template_string(nodes));
            if !else_nodes.is_empty() {
                result.push_str("{% else %}");
                result.push_str(&nodes_to_template_string(else_nodes));
            }
            result.push_str("{% endifchanged %}");
            result
        }
        Node::AutoEscape { on, nodes } => {
            let mut result = format!("{{% autoescape {} %}}", if *on { "on" } else { "off" });
            result.push_str(&nodes_to_template_string(nodes));
            result.push_str("{% endautoescape %}");
            result
        }
        Node::Cycle {
            values,
            name,
            silent,
            reference,
            ..
        } => {
            // A reference re-serializes as the reference it was, so the
            // re-parse binds it to the ONE definition again (#2556); the
            // state id is reassigned by that re-parse.
            if *reference {
                return format!("{{% cycle {} %}}", name.as_deref().unwrap_or_default());
            }
            let mut result = format!("{{% cycle {}", values.join(" "));
            if let Some(n) = name {
                result.push_str(&format!(" as {n}"));
                if *silent {
                    result.push_str(" silent");
                }
            }
            result.push_str(" %}");
            result
        }
        Node::ResetCycle { name, .. } => match name {
            Some(n) => format!("{{% resetcycle {n} %}}"),
            None => "{% resetcycle %}".to_string(),
        },
        Node::Filter { filters, nodes } => {
            let chain: Vec<String> = filters
                .iter()
                .map(|(name, arg)| match arg {
                    Some(arg) => format!("{name}:{arg}"),
                    None => name.clone(),
                })
                .collect();
            let mut result = format!("{{% filter {} %}}", chain.join("|"));
            result.push_str(&nodes_to_template_string(nodes));
            result.push_str("{% endfilter %}");
            result
        }
        Node::Now(format) => {
            format!("{{% now \"{format}\" %}}")
        }
        Node::BlockCustomTag {
            name,
            args,
            children,
        } => {
            // Reconstruct block custom tag: {% tagname args %}...{% endtagname %}
            let mut result = format!("{{% {name}");
            for arg in args {
                result.push(' ');
                result.push_str(arg);
            }
            result.push_str(" %}");
            result.push_str(&nodes_to_template_string(children));
            result.push_str(&format!("{{% end{name} %}}"));
            result
        }
        Node::RawBlockCustomTag { name, args, body } => {
            // Reconstruct raw-block tag (#2558): the body IS source, so it
            // re-emits verbatim between the open and end tags.
            let mut result = format!("{{% {name}");
            for arg in args {
                result.push(' ');
                result.push_str(arg);
            }
            result.push_str(" %}");
            result.push_str(body);
            result.push_str(&format!("{{% end{name} %}}"));
            result
        }
        Node::Language { expr, children } => {
            let mut result = format!("{{% language {expr} %}}");
            result.push_str(&nodes_to_template_string(children));
            result.push_str("{% endlanguage %}");
            result
        }
        Node::Timezone { expr, children } => {
            let mut result = format!("{{% timezone {expr} %}}");
            result.push_str(&nodes_to_template_string(children));
            result.push_str("{% endtimezone %}");
            result
        }
        Node::Localize { use_l10n, children } => {
            let arg = if *use_l10n { "on" } else { "off" };
            let mut result = format!("{{% localize {arg} %}}");
            result.push_str(&nodes_to_template_string(children));
            result.push_str("{% endlocalize %}");
            result
        }
        Node::LocalTime { use_tz, children } => {
            let arg = if *use_tz { "on" } else { "off" };
            let mut result = format!("{{% localtime {arg} %}}");
            result.push_str(&nodes_to_template_string(children));
            result.push_str("{% endlocaltime %}");
            result
        }
        Node::AssignTag { name, args } => {
            // Reconstruct assign tag: {% tagname args %}
            let mut result = format!("{{% {name}");
            for arg in args {
                result.push(' ');
                result.push_str(arg);
            }
            result.push_str(" %}");
            result
        }
    }
}

/// High-level function to resolve template inheritance from a file
/// Returns the merged template as a string (with Django template syntax preserved)
pub fn resolve_template_inheritance(
    template_path: &str,
    template_dirs: &[std::path::PathBuf],
) -> Result<String> {
    // Create loader
    let loader = FilesystemTemplateLoader::new(template_dirs.to_vec());

    // Load initial template
    let initial_path = loader.find_template(template_path)?;
    let source = std::fs::read_to_string(&initial_path).map_err(|e| {
        DjangoRustError::TemplateError(format!(
            "Failed to read template {}: {}",
            initial_path.display(),
            e
        ))
    })?;

    // Parse initial template (use `parse_with_source` so the
    // boundary-marker ID prefix is derived from this template's
    // own source — matches the production loader, see #1358 Iter 1
    // Stage 11 fix).
    let tokens = crate::lexer::tokenize(&source)?;
    let nodes = crate::parser::parse_with_source(&tokens, &source)
        .map_err(DjangoRustError::into_template_syntax)?;

    // Check if template uses inheritance
    let uses_extends = nodes.iter().any(|node| matches!(node, Node::Extends(_)));

    if !uses_extends {
        // No inheritance, return source as-is
        return Ok(source);
    }

    // Build inheritance chain
    let chain = build_inheritance_chain(nodes, &loader, 10)?;

    // Get merged template nodes
    let root_nodes = chain.get_root_nodes();
    let final_nodes = chain.apply_block_overrides(root_nodes);

    // Convert AST back to template string (preserves {{ var }} syntax)
    Ok(nodes_to_template_string(&final_nodes))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_extract_blocks() {
        let nodes = vec![
            Node::Text("Before".to_string()),
            Node::Block {
                name: "content".to_string(),
                nodes: vec![Node::Text("Hello".to_string())],
            },
            Node::Text("After".to_string()),
        ];

        let blocks = extract_blocks(&nodes);
        assert_eq!(blocks.len(), 1);
        assert!(blocks.contains_key("content"));
    }

    #[test]
    fn test_uses_extends() {
        let nodes = vec![
            Node::Extends("base.html".to_string()),
            Node::Block {
                name: "content".to_string(),
                nodes: vec![],
            },
        ];

        let chain = InheritanceChain::new(nodes);
        assert_eq!(chain.uses_extends(), Some("base.html"));
    }

    #[test]
    fn test_no_extends() {
        let nodes = vec![Node::Text("Hello".to_string())];
        let chain = InheritanceChain::new(nodes);
        assert_eq!(chain.uses_extends(), None);
    }

    /// Helper: drive AST input from parser output, not direct construction.
    /// Round-trip identity tests for AST-shape contracts must use this so the
    /// AST under test matches what production parsers actually emit (notably
    /// `parse_filter_specs`'s outer-quote preservation contract — #787, #1081,
    /// #1388). Hand-built `Node::*` fixtures silently mask round-trip bugs.
    fn parse_source(source: &str) -> Vec<Node> {
        let tokens = crate::lexer::tokenize(source).unwrap();
        crate::parser::parse(&tokens).unwrap()
    }

    #[test]
    fn test_nodes_to_template_string_preserves_variables() {
        // Test that variables are preserved as {{ var }} not rendered.
        // Drive from parser output (#1388) so we exercise the same AST shape
        // production code sees.
        let nodes = parse_source("{{ name }} is here");

        let result = nodes_to_template_string(&nodes);

        // Should preserve Django template syntax
        assert!(result.contains("{{ name }}"));
        assert!(!result.contains("{{name}}")); // Should have spaces
        assert!(result.contains(" is here"));
    }

    #[test]
    fn test_nodes_to_template_string_preserves_filters() {
        // `parse_filter_specs` preserves surrounding quotes on literal args
        // (the dep-tracking extractor needs them — #787). Drive from parser
        // output (#1388) so we test the actual contract, not a hand-mirrored
        // approximation of it. PR #1086 found that hand-built `Node::Variable`
        // tests silently passed when real parser-shape input would round-trip
        // incorrectly.
        let nodes = parse_source("{{ price|floatformat:\"2\"|default:\"0.00\" }}");

        let result = nodes_to_template_string(&nodes);

        assert!(result.contains("{{ price"));
        // Args should round-trip verbatim — NOT double-wrapped in extra quotes.
        // `|floatformat:"2"` (correct) vs `|floatformat:""2""` (the #1081 bug).
        assert!(result.contains("|floatformat:\"2\""));
        assert!(!result.contains("|floatformat:\"\"2\"\""));
        assert!(result.contains("|default:\"0.00\""));
        assert!(!result.contains("|default:\"\"0.00\"\""));
        assert!(result.contains("}}"));
    }

    #[test]
    fn test_nodes_to_template_string_preserves_bare_identifier_filter_args() {
        // Bare identifiers (no quotes) must also round-trip as bare identifiers.
        // `parse_filter_specs` does not add quotes — emit verbatim. Required for
        // the dep-tracking extractor to keep treating bare-identifier args as
        // template dependencies (#787). Drive from parser output (#1388).
        let nodes = parse_source("{{ value|default:fallback }}");

        let result = nodes_to_template_string(&nodes);

        // Should round-trip as bare identifier — NOT wrapped in quotes.
        assert!(result.contains("|default:fallback"));
        assert!(!result.contains("|default:\"fallback\""));
    }

    #[test]
    fn test_round_trip_through_resolve_inheritance_preserves_date_filter_arg_1081() {
        // Regression for #1081: with template inheritance, a `|date:"M d, Y"`
        // filter would get re-quoted to `|date:""M d, Y""`, then the renderer's
        // strip-quotes pass would leave the inner `"M d, Y"` baked into the
        // format string, producing literal-quote-wrapped output
        // (`&quot;Apr 25, 2026&quot;`) instead of `Apr 25, 2026`.
        //
        // This test parses a child template that extends a parent, runs the
        // resolved template through the parser again, and asserts the date
        // filter's format arg is exactly `"M d, Y"` after the round-trip
        // (not `""M d, Y""`).

        // Tokenise + parse the merged-template output of resolve_inheritance.
        // We don't need to actually run resolve_inheritance — we just need to
        // confirm that nodes_to_template_string's output, when re-parsed,
        // preserves the date filter's arg shape.
        let original_source = "{{ c.filed_date|date:\"M d, Y\" }}";
        let tokens = crate::lexer::tokenize(original_source).unwrap();
        let nodes = crate::parser::parse(&tokens).unwrap();

        // Round-trip through nodes_to_template_string.
        let round_tripped = nodes_to_template_string(&nodes);

        // The output should NOT have doubled quotes around `M d, Y`.
        assert!(
            !round_tripped.contains("\"\"M d, Y\"\""),
            "filter arg got double-wrapped during round-trip: {round_tripped:?}"
        );
        // It SHOULD have exactly one pair of quotes — matching the original source.
        assert!(
            round_tripped.contains("|date:\"M d, Y\""),
            "filter arg lost its quotes during round-trip: {round_tripped:?}"
        );

        // Re-parse the round-tripped output. The date filter's arg
        // should still be `"M d, Y"` (with single pair of quotes), so
        // strip_filter_arg_quotes at render time produces `M d, Y` cleanly.
        let tokens2 = crate::lexer::tokenize(&round_tripped).unwrap();
        let nodes2 = crate::parser::parse(&tokens2).unwrap();
        match &nodes2[0] {
            Node::Variable(_, filters, _) => {
                assert_eq!(filters.len(), 1);
                let (name, arg) = &filters[0];
                assert_eq!(name, "date");
                assert_eq!(
                    arg.as_deref(),
                    Some("\"M d, Y\""),
                    "after round-trip + re-parse, arg should be the source-form '\"M d, Y\"', not '\"\"M d, Y\"\"'"
                );
            }
            other => panic!("expected Variable node, got {other:?}"),
        }
    }

    #[test]
    fn test_nodes_to_template_string_block_syntax() {
        // Drive from parser output (#1388).
        let nodes = parse_source("{% block content %}<p>{{ message }}</p>{% endblock %}");

        let result = nodes_to_template_string(&nodes);

        assert!(result.contains("{% block content %}"));
        assert!(result.contains("{{ message }}"));
        assert!(result.contains("{% endblock %}"));
    }

    #[test]
    fn test_nodes_to_template_string_if_else() {
        // Drive from parser output (#1388).
        let nodes =
            parse_source("{% if user.is_authenticated %}Welcome!{% else %}Please login{% endif %}");

        let result = nodes_to_template_string(&nodes);

        assert!(result.contains("{% if user.is_authenticated %}"));
        assert!(result.contains("Welcome!"));
        assert!(result.contains("{% else %}"));
        assert!(result.contains("Please login"));
        assert!(result.contains("{% endif %}"));
    }

    #[test]
    fn test_nodes_to_template_string_for_loop() {
        // Drive from parser output (#1388).
        let nodes = parse_source("{% for item in items %}{{ item.name }}{% endfor %}");

        let result = nodes_to_template_string(&nodes);

        assert!(result.contains("{% for item in items %}"));
        assert!(result.contains("{{ item.name }}"));
        assert!(result.contains("{% endfor %}"));
    }

    #[test]
    fn test_nodes_to_template_string_for_loop_reversed() {
        // Drive from parser output (#1388).
        let nodes = parse_source("{% for item in items reversed %}Item{% endfor %}");

        let result = nodes_to_template_string(&nodes);

        assert!(result.contains("{% for item in items reversed %}"));
    }

    #[test]
    fn test_nodes_to_template_string_with_tag() {
        // Drive from parser output (#1388).
        let nodes =
            parse_source("{% with total=price|add:tax discount=0.1 %}{{ total }}{% endwith %}");

        let result = nodes_to_template_string(&nodes);

        assert!(result.contains("{% with total=price|add:tax discount=0.1 %}"));
        assert!(result.contains("{{ total }}"));
        assert!(result.contains("{% endwith %}"));
    }

    #[test]
    fn test_nodes_to_template_string_csrf_token() {
        // Drive from parser output (#1388).
        let nodes = parse_source("{% csrf_token %}");
        let result = nodes_to_template_string(&nodes);
        assert_eq!(result, "{% csrf_token %}");
    }

    #[test]
    fn test_nodes_to_template_string_static() {
        // Drive from parser output (#1388).
        let nodes = parse_source("{% static \"css/style.css\" %}");
        let result = nodes_to_template_string(&nodes);
        assert_eq!(result, "{% static \"css/style.css\" %}");
    }

    #[test]
    fn test_nodes_to_template_string_include() {
        // Drive from parser output (#1388). Regression for #1396 — parser
        // stored Include.template with surrounding quotes, emitter wrapped
        // them in another pair, producing {% include ""partials/header.html"" %}.
        let nodes = parse_source("{% include \"partials/header.html\" %}");
        let result = nodes_to_template_string(&nodes);
        assert_eq!(result, "{% include \"partials/header.html\" %}");
    }

    #[test]
    fn test_nodes_to_template_string_now() {
        // Drive from parser output (#1388). Lock in Now round-trip
        // (audited under #1396 — Now strips quotes correctly at parse,
        // no double-wrap; defense-in-depth test against future regression).
        let nodes = parse_source("{% now \"Y-m-d\" %}");
        let result = nodes_to_template_string(&nodes);
        assert_eq!(result, "{% now \"Y-m-d\" %}");
    }

    #[test]
    fn test_nodes_to_template_string_complex_nested() {
        // Test a complex nested structure. Drive from parser output (#1388).
        let nodes = parse_source(
            "{% block content %}{% if items %}{% for item in items %}<li>{{ item.name|upper }}</li>{% endfor %}{% else %}<p>No items</p>{% endif %}{% endblock %}",
        );

        let result = nodes_to_template_string(&nodes);

        // Should preserve all nested structures
        assert!(result.contains("{% block content %}"));
        assert!(result.contains("{% if items %}"));
        assert!(result.contains("{% for item in items %}"));
        assert!(result.contains("{{ item.name |upper }}"));
        assert!(result.contains("{% else %}"));
        assert!(result.contains("<p>No items</p>"));
        assert!(result.contains("{% endfor %}"));
        assert!(result.contains("{% endif %}"));
        assert!(result.contains("{% endblock %}"));
    }

    #[test]
    fn test_template_not_found_error_lists_directories() {
        use tempfile::TempDir;

        // Create temporary directories
        let temp_dir1 = TempDir::new().unwrap();
        let temp_dir2 = TempDir::new().unwrap();
        let temp_dir3 = TempDir::new().unwrap();

        let dirs = vec![
            temp_dir1.path().to_path_buf(),
            temp_dir2.path().to_path_buf(),
            temp_dir3.path().to_path_buf(),
        ];

        let loader = FilesystemTemplateLoader::new(dirs.clone());

        // Try to find a template that doesn't exist
        let result = loader.find_template("nonexistent.html");

        // Should be an error
        assert!(result.is_err());

        // Extract error message
        let error_message = result.unwrap_err().to_string();

        // Should contain the template name
        assert!(error_message.contains("nonexistent.html"));

        // Should contain "Searched in:" header
        assert!(error_message.contains("Searched in:"));

        // Should list all three directories
        for dir in &dirs {
            let dir_str = dir.display().to_string();
            assert!(
                error_message.contains(&dir_str),
                "Error message should contain directory: {dir_str}\nActual message: {error_message}"
            );
        }

        // Should have proper formatting with bullet points
        assert!(error_message.contains("  - "));
    }

    #[test]
    fn test_nested_block_inheritance() {
        // Test that blocks nested inside other blocks are correctly overridden
        // Base template has: {% block body %}...{% block content %}DEFAULT{% endblock %}...{% endblock %}
        // Child only overrides: {% block content %}CHILD{% endblock %}
        // Expected: content should show CHILD, not DEFAULT

        // Parent nodes: {% block body %}<div>{% block content %}DEFAULT{% endblock %}</div>{% endblock %}
        let parent_nodes = vec![Node::Block {
            name: "body".to_string(),
            nodes: vec![
                Node::Text("<div>".to_string()),
                Node::Block {
                    name: "content".to_string(),
                    nodes: vec![Node::Text("DEFAULT".to_string())],
                },
                Node::Text("</div>".to_string()),
            ],
        }];

        // Child nodes: {% extends "base.html" %}{% block content %}CHILD{% endblock %}
        let child_nodes = vec![
            Node::Extends("base.html".to_string()),
            Node::Block {
                name: "content".to_string(),
                nodes: vec![Node::Text("CHILD".to_string())],
            },
        ];

        // Create inheritance chain manually
        let mut chain = InheritanceChain::new(child_nodes);
        chain.add_parent(parent_nodes);
        chain.merge_blocks();

        // Verify merged_blocks has both blocks
        assert!(
            chain.merged_blocks.contains_key("content"),
            "merged_blocks should have 'content'"
        );
        assert!(
            chain.merged_blocks.contains_key("body"),
            "merged_blocks should have 'body'"
        );

        // Verify content block has CHILD content
        let content_nodes = chain.merged_blocks.get("content").unwrap();
        let content_str = nodes_to_template_string(content_nodes);
        assert!(
            content_str.contains("CHILD"),
            "content block should contain CHILD, got: {}",
            content_str
        );
        assert!(
            !content_str.contains("DEFAULT"),
            "content block should NOT contain DEFAULT, got: {}",
            content_str
        );

        // Apply overrides to parent nodes
        let root_nodes = chain.get_root_nodes();
        let final_nodes = chain.apply_block_overrides(root_nodes);
        let result = nodes_to_template_string(&final_nodes);

        // Result should contain CHILD, not DEFAULT
        assert!(
            result.contains("CHILD"),
            "Result should contain CHILD, got: {}",
            result
        );
        assert!(
            !result.contains("DEFAULT"),
            "Result should NOT contain DEFAULT, got: {}",
            result
        );
    }

    #[test]
    fn test_deeply_nested_blocks() {
        // Test 3 levels of nesting: outer > middle > inner
        // Child only overrides inner block
        let parent_nodes = vec![Node::Block {
            name: "outer".to_string(),
            nodes: vec![
                Node::Text("<outer>".to_string()),
                Node::Block {
                    name: "middle".to_string(),
                    nodes: vec![
                        Node::Text("<middle>".to_string()),
                        Node::Block {
                            name: "inner".to_string(),
                            nodes: vec![Node::Text("PARENT_INNER".to_string())],
                        },
                        Node::Text("</middle>".to_string()),
                    ],
                },
                Node::Text("</outer>".to_string()),
            ],
        }];

        let child_nodes = vec![
            Node::Extends("base.html".to_string()),
            Node::Block {
                name: "inner".to_string(),
                nodes: vec![Node::Text("CHILD_INNER".to_string())],
            },
        ];

        let mut chain = InheritanceChain::new(child_nodes);
        chain.add_parent(parent_nodes);
        chain.merge_blocks();

        let root_nodes = chain.get_root_nodes();
        let final_nodes = chain.apply_block_overrides(root_nodes);
        let result = nodes_to_template_string(&final_nodes);

        assert!(
            result.contains("CHILD_INNER"),
            "Deeply nested block should be overridden, got: {}",
            result
        );
        assert!(
            !result.contains("PARENT_INNER"),
            "Parent's inner content should be replaced, got: {}",
            result
        );
        // Verify structure is preserved
        assert!(
            result.contains("<outer>") && result.contains("</outer>"),
            "Outer block structure should be preserved"
        );
        assert!(
            result.contains("<middle>") && result.contains("</middle>"),
            "Middle block structure should be preserved"
        );
    }

    #[test]
    fn test_multiple_nested_blocks_same_level() {
        // Parent has: {% block wrapper %}{% block left %}L{% endblock %}{% block right %}R{% endblock %}{% endblock %}
        // Child overrides only 'right'
        let parent_nodes = vec![Node::Block {
            name: "wrapper".to_string(),
            nodes: vec![
                Node::Block {
                    name: "left".to_string(),
                    nodes: vec![Node::Text("LEFT_PARENT".to_string())],
                },
                Node::Block {
                    name: "right".to_string(),
                    nodes: vec![Node::Text("RIGHT_PARENT".to_string())],
                },
            ],
        }];

        let child_nodes = vec![
            Node::Extends("base.html".to_string()),
            Node::Block {
                name: "right".to_string(),
                nodes: vec![Node::Text("RIGHT_CHILD".to_string())],
            },
        ];

        let mut chain = InheritanceChain::new(child_nodes);
        chain.add_parent(parent_nodes);
        chain.merge_blocks();

        let root_nodes = chain.get_root_nodes();
        let final_nodes = chain.apply_block_overrides(root_nodes);
        let result = nodes_to_template_string(&final_nodes);

        // Left should keep parent content
        assert!(
            result.contains("LEFT_PARENT"),
            "Non-overridden block should keep parent content, got: {}",
            result
        );
        // Right should have child content
        assert!(
            result.contains("RIGHT_CHILD"),
            "Overridden block should have child content, got: {}",
            result
        );
        assert!(
            !result.contains("RIGHT_PARENT"),
            "Overridden block should NOT have parent content, got: {}",
            result
        );
    }

    #[test]
    fn test_nested_blocks_with_control_structures() {
        // Test nested blocks inside if/for structures
        let parent_nodes = vec![Node::If {
            condition: "show_content".to_string(),
            true_nodes: vec![Node::Block {
                name: "content".to_string(),
                nodes: vec![Node::Text("PARENT_CONTENT".to_string())],
            }],
            false_nodes: vec![Node::Text("hidden".to_string())],
            in_tag_context: false,
            marker_id: None,
        }];

        let child_nodes = vec![
            Node::Extends("base.html".to_string()),
            Node::Block {
                name: "content".to_string(),
                nodes: vec![Node::Text("CHILD_CONTENT".to_string())],
            },
        ];

        let mut chain = InheritanceChain::new(child_nodes);
        chain.add_parent(parent_nodes);
        chain.merge_blocks();

        let root_nodes = chain.get_root_nodes();
        let final_nodes = chain.apply_block_overrides(root_nodes);
        let result = nodes_to_template_string(&final_nodes);

        assert!(
            result.contains("CHILD_CONTENT"),
            "Block inside if should be overridden, got: {}",
            result
        );
        assert!(
            !result.contains("PARENT_CONTENT"),
            "Parent content should be replaced, got: {}",
            result
        );
        // Control structure should be preserved
        assert!(
            result.contains("{% if show_content %}"),
            "If structure should be preserved, got: {}",
            result
        );
    }

    #[test]
    fn test_nested_blocks_in_for_loop() {
        // Test block nested inside for loop
        let parent_nodes = vec![Node::For {
            var_names: vec!["item".to_string()],
            iterable: "items".to_string(),
            reversed: false,
            nodes: vec![Node::Block {
                name: "item_content".to_string(),
                nodes: vec![Node::Text("PARENT_ITEM".to_string())],
            }],
            empty_nodes: vec![],
        }];

        let child_nodes = vec![
            Node::Extends("base.html".to_string()),
            Node::Block {
                name: "item_content".to_string(),
                nodes: vec![Node::Variable("item.name".to_string(), vec![], false)],
            },
        ];

        let mut chain = InheritanceChain::new(child_nodes);
        chain.add_parent(parent_nodes);
        chain.merge_blocks();

        let root_nodes = chain.get_root_nodes();
        let final_nodes = chain.apply_block_overrides(root_nodes);
        let result = nodes_to_template_string(&final_nodes);

        assert!(
            result.contains("{{ item.name"),
            "Block inside for should be overridden with variable, got: {}",
            result
        );
        assert!(
            !result.contains("PARENT_ITEM"),
            "Parent content should be replaced, got: {}",
            result
        );
        // For structure should be preserved
        assert!(
            result.contains("{% for item in items %}"),
            "For structure should be preserved, got: {}",
            result
        );
    }

    #[test]
    fn test_child_overrides_both_outer_and_inner() {
        // Child overrides both the outer and inner blocks
        let parent_nodes = vec![Node::Block {
            name: "outer".to_string(),
            nodes: vec![
                Node::Text("<outer>".to_string()),
                Node::Block {
                    name: "inner".to_string(),
                    nodes: vec![Node::Text("PARENT_INNER".to_string())],
                },
                Node::Text("</outer>".to_string()),
            ],
        }];

        let child_nodes = vec![
            Node::Extends("base.html".to_string()),
            Node::Block {
                name: "outer".to_string(),
                nodes: vec![
                    Node::Text("<custom-outer>".to_string()),
                    Node::Block {
                        name: "inner".to_string(),
                        nodes: vec![Node::Text("STILL_PARENT_INNER".to_string())],
                    },
                    Node::Text("</custom-outer>".to_string()),
                ],
            },
            Node::Block {
                name: "inner".to_string(),
                nodes: vec![Node::Text("CHILD_INNER".to_string())],
            },
        ];

        let mut chain = InheritanceChain::new(child_nodes);
        chain.add_parent(parent_nodes);
        chain.merge_blocks();

        let root_nodes = chain.get_root_nodes();
        let final_nodes = chain.apply_block_overrides(root_nodes);
        let result = nodes_to_template_string(&final_nodes);

        // Child's outer structure should be used
        assert!(
            result.contains("<custom-outer>"),
            "Child's outer structure should be used, got: {}",
            result
        );
        // Child's inner content should override
        assert!(
            result.contains("CHILD_INNER"),
            "Child's inner content should be used, got: {}",
            result
        );
        assert!(
            !result.contains("PARENT_INNER") && !result.contains("STILL_PARENT_INNER"),
            "No parent inner content should remain, got: {}",
            result
        );
    }

    #[test]
    fn test_resolve_template_inheritance_with_nested_blocks_filesystem() {
        use std::fs;
        use tempfile::TempDir;

        // Create temporary directory with test templates
        let temp_dir = TempDir::new().unwrap();
        let template_dir = temp_dir.path();

        // Create base.html with nested blocks (like real Django templates)
        let base_html = r#"<!DOCTYPE html>
<html>
<head><title>{% block title %}Default{% endblock %}</title></head>
<body>
    {% block body %}
    <div id="app">
        {% block content %}Default Content{% endblock %}
    </div>
    {% endblock %}
</body>
</html>"#;
        fs::write(template_dir.join("base.html"), base_html).unwrap();

        // Create child.html that only overrides content block
        let child_html = r#"{% extends "base.html" %}

{% block title %}Child Page{% endblock %}

{% block content %}
<div class="child-content">
    <h1>Hello from child!</h1>
</div>
{% endblock %}"#;
        fs::write(template_dir.join("child.html"), child_html).unwrap();

        // Resolve inheritance
        let result = resolve_template_inheritance("child.html", &[template_dir.to_path_buf()])
            .expect("Should resolve template inheritance");

        // Verify child content is present
        assert!(
            result.contains("Hello from child!"),
            "Child content should be in result, got: {}",
            result
        );
        assert!(
            result.contains("child-content"),
            "Child class should be in result, got: {}",
            result
        );

        // Verify parent default content is NOT present
        assert!(
            !result.contains("Default Content"),
            "Parent default content should be replaced, got: {}",
            result
        );

        // Verify title was overridden
        assert!(
            result.contains("Child Page"),
            "Title should be overridden, got: {}",
            result
        );

        // Verify structure is preserved
        assert!(
            result.contains("<html>") && result.contains("</html>"),
            "HTML structure should be preserved"
        );
        assert!(
            result.contains("<div id=\"app\">"),
            "App div should be preserved from body block"
        );
    }

    #[test]
    fn test_three_level_inheritance_filesystem() {
        use std::fs;
        use tempfile::TempDir;

        let temp_dir = TempDir::new().unwrap();
        let template_dir = temp_dir.path();

        // base.html - root template
        let base_html = r#"<!DOCTYPE html>
<html>
<body>
{% block wrapper %}
<div class="wrapper">
    {% block content %}BASE CONTENT{% endblock %}
</div>
{% endblock %}
</body>
</html>"#;
        fs::write(template_dir.join("base.html"), base_html).unwrap();

        // middle.html - extends base, overrides wrapper
        let middle_html = r#"{% extends "base.html" %}

{% block wrapper %}
<main class="middle-wrapper">
    {% block content %}MIDDLE CONTENT{% endblock %}
</main>
{% endblock %}"#;
        fs::write(template_dir.join("middle.html"), middle_html).unwrap();

        // child.html - extends middle, overrides content
        let child_html = r#"{% extends "middle.html" %}

{% block content %}CHILD CONTENT{% endblock %}"#;
        fs::write(template_dir.join("child.html"), child_html).unwrap();

        // Resolve inheritance
        let result = resolve_template_inheritance("child.html", &[template_dir.to_path_buf()])
            .expect("Should resolve 3-level inheritance");

        // Child content should be present
        assert!(
            result.contains("CHILD CONTENT"),
            "Child content should be in result, got: {}",
            result
        );

        // Middle's wrapper structure should be used
        assert!(
            result.contains("middle-wrapper"),
            "Middle's wrapper should be used, got: {}",
            result
        );

        // Base and middle default content should NOT be present
        assert!(
            !result.contains("BASE CONTENT"),
            "Base content should be replaced, got: {}",
            result
        );
        assert!(
            !result.contains("MIDDLE CONTENT"),
            "Middle content should be replaced, got: {}",
            result
        );

        // Base wrapper should NOT be present (overridden by middle)
        assert!(
            !result.contains("class=\"wrapper\""),
            "Base wrapper should be replaced by middle's, got: {}",
            result
        );
    }
}
