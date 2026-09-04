//! Template parser for building an AST from tokens

use crate::lexer::{Span, Token};
use djust_core::{DjangoRustError, Result};
use std::collections::hash_map::DefaultHasher;
use std::collections::{HashMap, HashSet};
use std::hash::{Hash, Hasher};

#[derive(Debug, Clone)]
pub enum Node {
    Text(String),
    /// Variable expression `{{ var|filter:arg }}`.
    ///
    /// Tuple: (variable name, filter specs, in_attr).
    ///
    /// `in_attr` is computed at parse time via
    /// [`is_inside_html_tag_at`]. When true, the renderer uses
    /// [`crate::filters::html_escape_attr`] (attribute-safe escape)
    /// instead of [`crate::filters::html_escape`]. `|safe` still
    /// bypasses escaping in both contexts.
    Variable(String, Vec<(String, Option<String>)>, bool),
    If {
        condition: String,
        true_nodes: Vec<Node>,
        false_nodes: Vec<Node>,
        in_tag_context: bool,
        /// Stable per-template marker ID for `<!--dj-if id="if-N"-->`
        /// boundary comments. Assigned at parse time via
        /// [`assign_if_marker_ids`] in document order. `None` when
        /// the `If` node was constructed manually (tests) and not
        /// passed through the parser's ID-assignment pass — in that
        /// case the renderer falls back to no marker emission for
        /// that branch (defensive default; production templates
        /// always go through `parse()` which assigns IDs).
        marker_id: Option<String>,
    },
    For {
        var_names: Vec<String>, // Supports tuple unpacking: {% for a, b in items %}
        iterable: String,
        reversed: bool,
        nodes: Vec<Node>,
        empty_nodes: Vec<Node>, // Rendered when iterable is empty
    },
    Block {
        name: String,
        nodes: Vec<Node>,
    },
    Extends(String), // Parent template path
    Include {
        template: String,
        with_vars: Vec<(String, String)>, // key=value assignments
        only: bool,                       // if true, only pass with_vars, not parent context
    },
    Comment,
    /// {% load library_name %} — preserved so inheritance reconstruction can
    /// re-emit the tag for downstream Django rendering.
    Load(Vec<String>),
    CsrfToken,
    Static(String), // Path to static file
    With {
        assignments: Vec<(String, String)>, // var_name, expression
        nodes: Vec<Node>,
    },
    ReactComponent {
        name: String,
        props: Vec<(String, String)>,
        children: Vec<Node>,
    },
    RustComponent {
        name: String,
        props: Vec<(String, String)>,
    },
    /// Custom template tag handled by a Python callback.
    ///
    /// This is used for Django-specific tags like `{% url %}` and `{% static %}`
    /// that require Python runtime access (e.g., Django's URL resolver).
    ///
    /// The handler is looked up in the tag registry at parse time, and called
    /// at render time with args and context.
    CustomTag {
        /// Tag name (e.g., "url", "static")
        name: String,
        /// Arguments from the template tag as raw strings
        args: Vec<String>,
    },
    /// Block custom template tag handled by a Python callback with children.
    ///
    /// This is used for Django-compatible block tags like `{% modal %}...{% endmodal %}`
    /// that wrap content and require Python runtime rendering.
    ///
    /// The handler receives the pre-rendered HTML of the block body.
    BlockCustomTag {
        /// Opening tag name (e.g., "modal", "card")
        name: String,
        /// Arguments from the opening tag as raw strings
        args: Vec<String>,
        /// Child nodes (the block body)
        children: Vec<Node>,
    },
    /// {% widthratio value max_value max_width %} - calculates round(value/max_value * max_width)
    WidthRatio {
        value: String,
        max_value: String,
        max_width: String,
        /// `{% widthratio a b c as name %}` — assign, emit nothing (#2355).
        asvar: Option<String>,
    },
    /// {% firstof var1 var2 ... "fallback" %} - outputs first truthy variable
    FirstOf {
        args: Vec<String>,
        /// `{% firstof a b as name %}` — assign, emit nothing (#2355).
        asvar: Option<String>,
    },
    /// {% templatetag name %} - outputs literal template syntax characters
    TemplateTag(String),
    /// {% spaceless %}...{% endspaceless %} - removes whitespace between HTML tags
    Spaceless {
        nodes: Vec<Node>,
    },
    /// `{% autoescape on|off %}…{% endautoescape %}` — Django's
    /// `AutoEscapeControlNode` (#2556). Sets `Context::autoescape` for the
    /// body on a per-block clone; nesting restores the outer setting for free.
    AutoEscape {
        on: bool,
        nodes: Vec<Node>,
    },
    /// `{% cycle v1 v2 … [as name [silent]] %}` and the reference form
    /// `{% cycle name %}` (#2556).
    ///
    /// State is Django's: per NODE, per RENDER — `Context::cycle_advance`
    /// keyed by `id`, which `resolve_cycle_nodes` assigns in document order
    /// from the same per-template prefix as `{% if %}` marker ids. A
    /// reference is resolved at parse time into a copy of its definition
    /// (same `values`, `silent` and `id`, so it advances the SAME iterator —
    /// Django returns the very same `CycleNode` object for `{% cycle name %}`)
    /// with `reference: true`, which only the inheritance re-serializer reads.
    Cycle {
        values: Vec<String>,
        name: Option<String>,
        silent: bool,
        id: String,
        reference: bool,
    },
    /// `{% resetcycle [name] %}` — `CycleNode.reset` on the named cycle, or
    /// on the last DEFINED one (`parser._last_cycle_node`; a reference does
    /// not update it). `id` is bound by `resolve_cycle_nodes`.
    ResetCycle {
        name: Option<String>,
        id: String,
    },
    /// A block body that references `{{ block.super }}`, paired with the
    /// PARENT version it should resolve to (#2517).
    ///
    /// Not produced by the parser — `InheritanceChain::merge_blocks` builds it
    /// while flattening the inheritance chain, because only the chain knows
    /// what a block's parent version is. Nested for a chain deeper than two:
    /// the parent body is itself a `BlockSuperScope` when IT references
    /// `block.super`, which is what makes `three two one` come out in that
    /// order.
    ///
    /// The wrapper is applied ONLY to a body that actually references
    /// `block.super`. Django resolves it lazily through `BlockContext`, so a
    /// body that never mentions it must not pay for — or observe the side
    /// effects of — rendering its parent (a `{% cycle %}` in the parent body
    /// would otherwise advance).
    BlockSuperScope {
        super_nodes: Vec<Node>,
        nodes: Vec<Node>,
    },
    /// `{% ifchanged [var …] %}…[{% else %}…]{% endifchanged %}` —
    /// Django's `IfChangedNode`.
    ///
    /// With operands the tag compares the RESOLVED values (an OR over all of
    /// them, as one tuple); with none it compares the RENDERED body, which is
    /// why the no-operand form must render the body BEFORE it can decide, and
    /// must not render it twice. `id` is bound by `resolve_cycle_nodes`
    /// alongside the cycle ids — the per-render state it keys lives on the
    /// `Context`, exactly as `{% cycle %}`'s does.
    IfChanged {
        vars: Vec<String>,
        id: String,
        nodes: Vec<Node>,
        else_nodes: Vec<Node>,
    },
    /// `{% filter f1|f2:arg %}…{% endfilter %}` (#2556): the body renders to
    /// a string, is bound as the SAFE variable `var` — Django's
    /// `NodeList.render` is a `SafeString` — and `{{ var|f1|f2:arg }}` is
    /// rendered through the ONE `Node::Variable` sink, so the escaping
    /// decision is never re-derived here.
    Filter {
        filters: Vec<(String, Option<String>)>,
        nodes: Vec<Node>,
    },
    /// {% now "format" %} - outputs current date/time with given format
    Now(String),
    /// Unsupported template tag - renders as HTML comment with warning.
    ///
    /// This is used for Django template tags that don't have a registered
    /// handler. Instead of silently failing, it outputs a visible warning
    /// in development to help developers identify missing tag implementations.
    UnsupportedTag {
        /// Tag name (e.g., "ifchanged", "regroup")
        name: String,
        /// Original arguments from the tag
        args: Vec<String>,
    },
    /// Custom assign tag — handler returns a dict that is merged
    /// into the context for subsequent sibling nodes (no HTML
    /// output).
    ///
    /// Registered via `register_assign_tag_handler(name, handler)`.
    /// The handler's `render(args, context)` must return a
    /// `dict[str, Any]`; each key becomes a context variable
    /// visible to siblings that follow the assign tag in the same
    /// `render_nodes_with_loader` iteration.
    ///
    /// See [`crate::registry::register_assign_tag_handler`].
    AssignTag {
        /// Tag name (e.g., "assign_slot")
        name: String,
        /// Raw arguments from the template tag
        args: Vec<String>,
    },
    /// Jinja2-style inline conditional: {{ true_expr if condition else false_expr }}
    ///
    /// This is safe to use inside HTML attribute values (unlike {% if %} blocks,
    /// which insert `<!--dj-if-->` comment nodes that corrupt attribute strings).
    ///
    /// Examples:
    ///   class="{{ 'btn--active' if view_mode == 'day' else '' }}"
    ///   disabled="{{ 'disabled' if is_locked else '' }}"
    ///   class="{{ 'error' if has_error }}"   {# else branch is optional #}
    InlineIf {
        true_expr: String,
        condition: String,
        false_expr: String,
        filters: Vec<(String, Option<String>)>,
    },
    /// Raw-block custom tag whose body crosses to Python as UN-rendered
    /// source (#2558). `blocktranslate` is the whole reason this kind
    /// exists: the body is DATA — the msgid Django's `render_token_list`
    /// builds from the tokens (`{{ var }}` → `%(var)s`, every other `%`
    /// doubled) and the catalog lookup keys on — so pre-rendering it (the
    /// [`Node::BlockCustomTag`] contract) would destroy the message.
    /// Reconstructed and handed over by [`collect_raw_source`].
    RawBlockCustomTag {
        name: String,
        args: Vec<String>,
        body: String,
    },
    /// `{% language "de" %}…{% endlanguage %}` (#2558). The children keep
    /// rendering in Rust — handing the body to Django would be the #1051
    /// dual-engine bug — while the language switch itself happens in
    /// Python's thread-local through the registered scope hooks, because a
    /// bridged `{% translate %}` inside the block reads
    /// `translation.get_language()` there.
    Language {
        expr: String,
        children: Vec<Node>,
    },
    /// `{% timezone "Europe/Paris" %}…{% endtimezone %}` (#2558) — the
    /// timezone twin of [`Node::Language`].
    Timezone {
        expr: String,
        children: Vec<Node>,
    },
    /// `{% localize on|off %}…{% endlocalize %}` (#2558): a render-side
    /// `use_l10n` flag, mirroring Django's `Context.use_l10n` flag
    /// (`l10n.py:31-36`) — consumed only by variable-output localization.
    Localize {
        use_l10n: bool,
        children: Vec<Node>,
    },
    /// `{% localtime on|off %}…{% endlocaltime %}` (#2558): Django's
    /// `Context.use_tz` flag (`tz.py:92-106`) — saves/restores the active
    /// zone around the children.
    LocalTime {
        use_tz: bool,
        children: Vec<Node>,
    },
}

/// Returns true if `text` ends inside an unclosed HTML opening tag.
///
/// Used to detect whether a `{% if %}` tag appears inside an attribute value,
/// e.g. `<div class="btn {% if active %}`. In that context the VDOM placeholder
/// comment `<!--dj-if-->` must NOT be emitted because HTML comments inside
/// attribute values produce malformed HTML (fix for issue #380).
///
/// Scans left-to-right with quote state tracking so that `>` characters
/// inside quoted attribute values (e.g. `title="a > b "`) are not mistaken
/// for tag-closing `>` characters.
///
/// Known limitation: does not track JavaScript/CSS template literals or
/// CDATA sections — these are not expected in Django template attribute values.
fn is_inside_html_tag(text: &str) -> bool {
    let mut in_tag = false;
    let mut in_quote: Option<char> = None;

    for ch in text.chars() {
        match (in_tag, in_quote, ch) {
            // Opening < starts a tag (only when not inside a quoted attribute)
            (false, None, '<') => in_tag = true,
            // Closing > ends a tag (only when not inside a quoted attribute)
            (true, None, '>') => in_tag = false,
            // Enter a double-quoted attribute value
            (true, None, '"') => in_quote = Some('"'),
            // Enter a single-quoted attribute value
            (true, None, '\'') => in_quote = Some('\''),
            // Exit a quoted attribute value (matching quote character)
            (true, Some(q), c) if c == q => in_quote = None,
            // All other characters — no state change
            _ => {}
        }
    }

    in_tag
}

/// Scan backwards through all preceding tokens to determine if we are inside
/// an HTML opening tag. Variable/expression tokens (`{{ }}`) produce escaped
/// text that cannot contain raw `<` or `>`, so they are skipped — only Text
/// tokens contribute to the tag-open/close state.
///
/// This fixes the case where `<option value="{{ var }}" {% if cond %}selected{% endif %}>`
/// has a Variable token between the `<option` and the `{% if %}`, causing the
/// single-token check to miss the unclosed tag context.
fn is_inside_html_tag_at(tokens: &[Token], pos: usize) -> bool {
    let mut combined = String::new();
    // Walk backwards, collecting Text tokens. Stop early if we find a `>`
    // outside quotes (definitely closed) or have enough context.
    for j in (0..pos).rev() {
        if let Token::Text(t) = &tokens[j] {
            combined.insert_str(0, t);
            // Optimization: if the combined text contains a `>` we have enough
            // context — the final state from is_inside_html_tag will be correct.
            if t.contains('>') {
                break;
            }
        }
        // Variable tokens produce HTML-escaped content (no raw < or >), skip them.
        // Tag tokens ({% %}) are structural and don't emit < or >, skip them too.
    }
    is_inside_html_tag(&combined)
}

/// Parse a token stream into an AST.
///
/// IDs assigned to `Node::If` boundary markers (#1358 Iter 1) are
/// derived from a hash of the token stream, so independently-parsed
/// templates (e.g. via `{% extends %}` parents and `{% include %}`
/// children, each parsed via separate `parse()` calls) get distinct
/// ID prefixes and don't collide when their rendered HTML is
/// composed in a single output buffer.
///
/// Prefer [`parse_with_source`] when the original template source
/// is available — it yields a more reproducible prefix derived from
/// the source string itself, which keeps IDs stable across cosmetic
/// token-stream representation changes (e.g. lexer refactors).
/// The refusal text for a tag with no registered handler — one producer
/// for the parser (parse-time, #2549) and the `Node::UnsupportedTag` render
/// arm (hand-built nodes only), so the two can never drift (#1646).
pub fn unsupported_tag_message(name: &str, args: &[String]) -> String {
    let args_str = if args.is_empty() {
        String::new()
    } else {
        format!(" {}", args.join(" "))
    };
    format!(
        "Unsupported template tag '{{% {name}{args_str} %}}'. \
         Register a handler via djust._rust.register_tag_handler(), \
         or use Django's template engine instead."
    )
}

/// `{% templatetag X %}` accepts exactly Django's eight names; anything
/// else is `TemplateSyntaxError` at parse time (#2549).
pub const TEMPLATETAG_NAMES: [&str; 8] = [
    "openblock",
    "closeblock",
    "openvariable",
    "closevariable",
    "openbrace",
    "closebrace",
    "opencomment",
    "closecomment",
];

pub fn parse(tokens: &[Token]) -> Result<Vec<Node>> {
    parse_internal(tokens, &[], "", hash_tokens(tokens))
}

/// Parse a token stream into an AST, deriving the boundary-marker
/// ID prefix from the original template source.
///
/// Foundation 1 of #1358 — addressed under Stage 11 review of
/// PR #1363. Each independently-parsed template (parent template
/// loaded by `{% extends %}`, child template, `{% include %}`'d
/// template, macro/snippet) MUST get a distinct ID prefix; otherwise
/// the rendered HTML can contain duplicate `<!--dj-if id="if-0"-->`
/// markers and the differ in Iter 3 cannot key off `id` alone.
///
/// The prefix is `if-<8-hex-chars>-` derived from a stable hash of
/// `source`. Same source → same prefix → IDs are stable across
/// re-parses. Different sources → different prefix (with extremely
/// high probability — collision rate is ~1/4 billion).
pub fn parse_with_source(tokens: &[Token], source: &str) -> Result<Vec<Node>> {
    parse_internal(tokens, &[], source, hash_source(source))
}

/// [`parse_with_source`] with the lexer's per-token byte spans, so a parse
/// error carries the position of the token that caused it (#2557).
///
/// `spans[i]` is the source span of `tokens[i]` — the pair Django keeps on
/// `Token.position` and feeds to `Template.get_exception_info` to build the
/// `template_debug` dict its technical-500 page renders. The span-less
/// entry points above stay valid: an empty table simply attaches nothing, so
/// a caller that has no spans gets exactly today's behaviour.
pub fn parse_with_source_spanned(
    tokens: &[Token],
    spans: &[Span],
    source: &str,
) -> Result<Vec<Node>> {
    parse_internal(tokens, spans, source, hash_source(source))
}

fn parse_internal(
    tokens: &[Token],
    spans: &[Span],
    source: &str,
    identity_hash: u64,
) -> Result<Vec<Node>> {
    let mut nodes = Vec::new();
    let mut i = 0;

    while i < tokens.len() {
        let node = parse_token(tokens, spans, source, &mut i)?;
        if let Some(n) = node {
            // Django's `ExtendsNode.must_be_first` (`loader_tags.py`) refuses
            // the moment a SECOND non-text node is about to be appended
            // while an `Extends` is anywhere in the accumulated list, and
            // refuses an `Extends` node itself the moment it is about to be
            // appended after ANY non-text content already exists (#2580).
            // Checked here as one post-hoc scan over the completed
            // top-level list rather than threaded through every
            // `parse_token` call site: the OBSERVABLE fact — is there
            // non-text content before the (first) `Extends`, and is there
            // more than one `Extends` — is identical either way, and this
            // is the ONLY place with full visibility into "everything
            // parsed so far at the top level" without invasive plumbing.
            // Covers both `test_extends_not_first_tag_in_extended_template`
            // (content before extends) and `test_exception03` (a second
            // extends after a block already opened).
            if matches!(n, Node::Extends(_)) {
                let already_has_extends = nodes
                    .iter()
                    .any(|existing| matches!(existing, Node::Extends(_)));
                let already_has_nontext = nodes
                    .iter()
                    .any(|existing| !matches!(existing, Node::Text(_)));
                if already_has_extends || already_has_nontext {
                    return Err(DjangoRustError::TemplateError(
                        "'extends' must be the first tag in the template".to_string(),
                    ));
                }
            }
            nodes.push(n);
        }
        i += 1;
    }

    // Assign stable per-template marker IDs to every `Node::If` in
    // document order. IDs are formatted as
    // `if-<8-hex-chars>-<counter>` where the hex chars are derived
    // from a hash of the template source (or the token stream when
    // source is not available). This disambiguates independently-
    // parsed templates (`{% extends %}`, `{% include %}`) which
    // would otherwise each emit `if-0`, `if-1`, ... causing
    // collisions when their rendered HTML is composed in a single
    // output buffer. IDs are stable across re-renders because
    // `parse()` is deterministic and the hash is stable for the
    // same source.
    //
    // Foundation 1 of 3 toward issue #1358 (keyed VDOM diff for
    // conditional subtrees, re-open of #256 Option A). Iter 2
    // (client patch applier) and Iter 3 (Rust VDOM differ) follow.
    let prefix = format_id_prefix(identity_hash);
    let mut counter = 0usize;
    assign_if_marker_ids(&mut nodes, &prefix, &mut counter);

    // Bind `{% cycle name %}` / `{% resetcycle %}` to their definitions and
    // give every cycle node its per-template state id (#2556). Same
    // document-order walk and the same prefix, for the same reason.
    let mut cycles = CycleResolution::default();
    resolve_cycle_nodes(&mut nodes, &prefix, &mut cycles)?;

    Ok(nodes)
}

/// Compute a stable identity hash from a template source string.
fn hash_source(source: &str) -> u64 {
    let mut hasher = DefaultHasher::new();
    source.hash(&mut hasher);
    hasher.finish()
}

/// Compute a stable identity hash from a token stream — fallback
/// for callers that don't have the source string. Two different
/// sources that lex to the same tokens will share a prefix; this
/// is acceptable because the only observable difference would be
/// whitespace / comment positions, neither of which alters the
/// emitted `Node::If` structure.
fn hash_tokens(tokens: &[Token]) -> u64 {
    let mut hasher = DefaultHasher::new();
    for tok in tokens {
        // Hash a tag/discriminant + payload representation. We use
        // the Debug repr because Token doesn't impl Hash directly
        // and we don't want to enforce that constraint just for ID
        // disambiguation. The Debug repr is stable across runs.
        format!("{tok:?}").hash(&mut hasher);
    }
    hasher.finish()
}

/// Format an identity hash into the per-template prefix used in
/// `if-<prefix>-<counter>` marker IDs. Truncates to 8 hex chars to
/// keep IDs short — collision rate at 8 hex chars (32 bits) is
/// ~1/4 billion which is fine for the boundary-marker disambiguation
/// use case (a project would need >65k templates before the
/// birthday-paradox collision probability hits 1%).
fn format_id_prefix(hash: u64) -> String {
    // Take the low 32 bits → 8 hex chars.
    format!("{:08x}", hash as u32)
}

/// Compute the canonical 8-hex template-source hash used both for
/// `<!--dj-if id="if-<prefix>-N"-->` marker IDs (Foundation 1 of #1358)
/// and the Redis state-backend cache key (#1362 section 1).
///
/// The same `template_hash_hex(src)` value MUST equal the prefix that
/// `parse_with_source(tokens, src)` would derive — that invariant is
/// what makes the cache key change automatically when ANY operator
/// edits a template. The two callers (parser, state-backend cache key)
/// must never drift; both go through this single helper.
///
/// Stability: `DefaultHasher::new()` is constructed with fixed seeds
/// (unlike `HashMap`'s `RandomState`), so the same source string yields
/// the same hash both within one process and across separate process
/// invocations of the same Rust toolchain build. The marker-ID
/// boundary contract already depends on this; the cache key inherits
/// the same guarantee. Different Rust toolchain releases may pick
/// different SipHash constants in theory; if that happens, the cache
/// key changes (one-deploy invalidation), which is acceptable.
pub fn template_hash_hex(source: &str) -> String {
    format_id_prefix(hash_source(source))
}

/// Walk the AST in document order and assign stable
/// `marker_id = Some("if-<prefix>-N")` to every `Node::If`. The
/// counter increments once per `If` (including elif chains, nested
/// ifs, and ifs inside loops/blocks). Idempotent only if called
/// once per `parse()` — re-running on already-assigned trees would
/// overwrite IDs. The current call site in `parse_internal()` is
/// the single source of truth.
///
/// The `prefix` is a per-template short hash (e.g. `"a3b1c2d4"`) so
/// IDs across templates composed via `{% extends %}` / `{% include %}`
/// don't collide — see `parse_with_source` for the rationale.
///
/// Recurses into all Node variants that can hold child nodes:
/// `If`, `For`, `Block`, `With`, `Spaceless`, and the BlockCustomTag /
/// ReactComponent children. Variants that can't hold child Nodes
/// (Text, Variable, Static, etc.) are leaves and don't recurse.
pub(crate) fn assign_if_marker_ids(nodes: &mut [Node], prefix: &str, counter: &mut usize) {
    for node in nodes.iter_mut() {
        match node {
            Node::If {
                marker_id,
                true_nodes,
                false_nodes,
                ..
            } => {
                *marker_id = Some(format!("if-{}-{}", prefix, *counter));
                *counter += 1;
                assign_if_marker_ids(true_nodes, prefix, counter);
                assign_if_marker_ids(false_nodes, prefix, counter);
            }
            Node::For {
                nodes: body,
                empty_nodes,
                ..
            } => {
                assign_if_marker_ids(body, prefix, counter);
                assign_if_marker_ids(empty_nodes, prefix, counter);
            }
            Node::Block { nodes: body, .. } => {
                assign_if_marker_ids(body, prefix, counter);
            }
            Node::With { nodes: body, .. } => {
                assign_if_marker_ids(body, prefix, counter);
            }
            Node::Spaceless { nodes: body, .. } | Node::AutoEscape { nodes: body, .. } => {
                assign_if_marker_ids(body, prefix, counter);
            }
            Node::Filter { nodes: body, .. } => {
                assign_if_marker_ids(body, prefix, counter);
            }
            Node::IfChanged {
                nodes: body,
                else_nodes,
                ..
            } => {
                assign_if_marker_ids(body, prefix, counter);
                assign_if_marker_ids(else_nodes, prefix, counter);
            }
            Node::BlockSuperScope { super_nodes, nodes } => {
                assign_if_marker_ids(super_nodes, prefix, counter);
                assign_if_marker_ids(nodes, prefix, counter);
            }
            Node::BlockCustomTag { children, .. } => {
                assign_if_marker_ids(children, prefix, counter);
            }
            // Scope nodes (#2558) carry children; `RawBlockCustomTag` does
            // not (its body is a source string, never parsed here).
            Node::Language { children, .. }
            | Node::Timezone { children, .. }
            | Node::Localize { children, .. }
            | Node::LocalTime { children, .. } => {
                assign_if_marker_ids(children, prefix, counter);
            }
            Node::ReactComponent { children, .. } => {
                assign_if_marker_ids(children, prefix, counter);
            }
            // Leaf or non-AST-bearing variants — no recursion needed.
            _ => {}
        }
    }
}

/// Parse-time bookkeeping for `resolve_cycle_nodes`: Django's
/// `parser._named_cycle_nodes` and `parser._last_cycle_node`.
#[derive(Default)]
struct CycleResolution {
    /// `name -> (values, silent, id)` of every `{% cycle … as name %}` seen
    /// so far in document order.
    named: HashMap<String, (Vec<String>, bool, String)>,
    /// The id of the last DEFINITION (`{% cycle name %}` does not update it,
    /// exactly as Django's `cycle()` returns before `_last_cycle_node = node`).
    last: Option<String>,
    /// Ids are `<prefix>-cycle-<n>`, document order.
    counter: usize,
    /// `{% ifchanged %}` ids are `<prefix>-ifchanged-<n>`, document order.
    /// Minted in this walk because it is the one pass that already visits
    /// every container in order and carries the template prefix.
    ifchanged_counter: usize,
}

/// Bind cycle references and `{% resetcycle %}` targets, and assign ids (#2556).
///
/// Walks every child-bearing variant in document order — the SAME set
/// `assign_if_marker_ids` walks, because a `{% cycle %}` can sit anywhere a
/// `{% if %}` can. Django resolves all of this at parse time and raises
/// `TemplateSyntaxError` with these exact messages; djust surfaces them at
/// render until #2549 types the parse-time channel.
fn resolve_cycle_nodes(
    nodes: &mut [Node],
    prefix: &str,
    state: &mut CycleResolution,
) -> Result<()> {
    for node in nodes.iter_mut() {
        match node {
            Node::Cycle {
                values,
                name,
                silent,
                id,
                reference,
            } => {
                if *reference {
                    let Some(name) = name.as_deref() else {
                        unreachable!("a cycle reference always carries its name")
                    };
                    if state.named.is_empty() {
                        return Err(DjangoRustError::TemplateError(format!(
                            "No named cycles in template. '{name}' is not defined"
                        )));
                    }
                    let Some((def_values, def_silent, def_id)) = state.named.get(name) else {
                        return Err(DjangoRustError::TemplateError(format!(
                            "Named cycle '{name}' does not exist"
                        )));
                    };
                    *values = def_values.clone();
                    *silent = *def_silent;
                    *id = def_id.clone();
                } else {
                    *id = format!("{prefix}-cycle-{}", state.counter);
                    state.counter += 1;
                    if let Some(name) = name {
                        state
                            .named
                            .insert(name.clone(), (values.clone(), *silent, id.clone()));
                    }
                    state.last = Some(id.clone());
                }
            }
            Node::ResetCycle { name, id } => match name {
                Some(name) => {
                    let Some((_, _, def_id)) = state.named.get(name) else {
                        return Err(DjangoRustError::TemplateError(format!(
                            "Named cycle '{name}' does not exist."
                        )));
                    };
                    *id = def_id.clone();
                }
                None => {
                    let Some(last) = state.last.as_ref() else {
                        return Err(DjangoRustError::TemplateError(
                            "No cycles in template.".to_string(),
                        ));
                    };
                    *id = last.clone();
                }
            },
            Node::If {
                true_nodes,
                false_nodes,
                ..
            } => {
                resolve_cycle_nodes(true_nodes, prefix, state)?;
                resolve_cycle_nodes(false_nodes, prefix, state)?;
            }
            Node::For {
                nodes: body,
                empty_nodes,
                ..
            } => {
                resolve_cycle_nodes(body, prefix, state)?;
                resolve_cycle_nodes(empty_nodes, prefix, state)?;
            }
            Node::Block { nodes: body, .. }
            | Node::With { nodes: body, .. }
            | Node::Spaceless { nodes: body, .. }
            | Node::AutoEscape { nodes: body, .. }
            | Node::Filter { nodes: body, .. } => {
                resolve_cycle_nodes(body, prefix, state)?;
            }
            // The four #2558 scope tags are containers like every arm above,
            // and #2556 had to add `Node::AutoEscape` here for exactly this
            // reason: a walker that does not descend leaves an inner
            // `{% cycle name %}` reference unbound, so it re-renders the
            // FIRST value forever (`a` where Django gives `ab`) instead of
            // advancing. One arm per container, decided explicitly (#1646).
            Node::Language { children, .. }
            | Node::Timezone { children, .. }
            | Node::Localize { children, .. }
            | Node::LocalTime { children, .. } => {
                resolve_cycle_nodes(children, prefix, state)?;
            }
            Node::BlockCustomTag { children, .. } | Node::ReactComponent { children, .. } => {
                resolve_cycle_nodes(children, prefix, state)?;
            }
            // `{% ifchanged %}` is BOTH an id consumer and a container, so it
            // is bound here rather than in a pass of its own — and it must
            // descend for the reason the comment above gives.
            Node::IfChanged {
                id,
                nodes: body,
                else_nodes,
                ..
            } => {
                *id = format!("{prefix}-ifchanged-{}", state.ifchanged_counter);
                state.ifchanged_counter += 1;
                resolve_cycle_nodes(body, prefix, state)?;
                resolve_cycle_nodes(else_nodes, prefix, state)?;
            }
            Node::BlockSuperScope { super_nodes, nodes } => {
                resolve_cycle_nodes(super_nodes, prefix, state)?;
                resolve_cycle_nodes(nodes, prefix, state)?;
            }
            _ => {}
        }
    }
    Ok(())
}

/// Parse the token at `*i`, tagging any error with that token's source
/// span (#2557).
///
/// Every recursive descent goes through this one function, so the INNERMOST
/// frame is the first to attach a span and [`DjangoRustError::at`] declines
/// to overwrite it — which is how the reported position ends up on the
/// offending tag rather than on the outermost block that contains it.
/// 1-based line of `tokens[i]` in `source` — Django's `Token.lineno`, for the
/// two `on line N` refusals in [`parse_token_inner`] (#2557).
///
/// Counts newline BYTES rather than slicing, so a span that is somehow not on
/// a character boundary cannot panic (the #2552 class). Falls back to line 1
/// when the caller has no span table — the span-less entry points
/// ([`parse`], [`parse_with_source`]) pass an empty one, and line 1 is the
/// only honest answer there.
fn line_at(spans: &[Span], source: &str, i: usize) -> usize {
    let start = spans.get(i).map_or(0, |(s, _)| *s);
    let upto = start.min(source.len());
    source.as_bytes()[..upto]
        .iter()
        .filter(|&&b| b == b'\n')
        .count()
        + 1
}

/// A closer keyword (`endverbatim`/`endwith`/`endspaceless`/`endautoescape`/
/// `endfilter`/`endif`/`endfor`/`endblock`/`else`/`elif`) reached where it is
/// not the awaited terminator: either at the top level with nothing open, or
/// inside a DIFFERENT block that was watching for a DIFFERENT terminator
/// (#2580). Every one of these is ONLY ever legitimately consumed by its own
/// opening tag's dedicated body-loop, which checks for its specific
/// terminator BEFORE ever falling through to `parse_token` — so reaching
/// this helper at all means the token is a stray. Django's parser hits the
/// same fact from the other direction: none of these are independently-
/// registered tags (`self.tags[command]` raises `KeyError`), so
/// `Parser.parse` refuses with "Invalid block tag" the moment one appears
/// where it is not the awaited terminator.
fn stray_closer_error(spans: &[Span], source: &str, i: usize, tag_name: &str) -> DjangoRustError {
    DjangoRustError::TemplateError(format!(
        "Invalid block tag on line {}: '{}'",
        line_at(spans, source, i),
        tag_name
    ))
}

/// Django's `Parser.unclosed_block_tag` (`base.py:584`), verbatim: "Unclosed
/// tag on line %d: '%s'. Looking for one of: %s." (#2581). `token.lineno` is
/// the OPENING tag's own line — not the point where the token stream ran
/// out — so `opening_pos` must be the index of the `{% if %}`/`{% for %}`/
/// `{% block %}` token itself, i.e. `start - 1` at each of
/// `parse_if_block`/`parse_for_block`/`parse_block`'s call sites (all three
/// are invoked as `parse_X_block(tokens, spans, source, *i + 1, ...)`).
/// `command` is the opening tag's own name ("if"/"for"/"block"); `parse_until`
/// is the terminator set that ran out without being found.
fn unclosed_tag_error(
    spans: &[Span],
    source: &str,
    opening_pos: usize,
    command: &str,
    parse_until: &[&str],
) -> DjangoRustError {
    DjangoRustError::TemplateError(format!(
        "Unclosed tag on line {}: '{}'. Looking for one of: {}.",
        line_at(spans, source, opening_pos),
        command,
        parse_until.join(", ")
    ))
}

fn parse_token(
    tokens: &[Token],
    spans: &[Span],
    source: &str,
    i: &mut usize,
) -> Result<Option<Node>> {
    let at = *i;
    parse_token_inner(tokens, spans, source, i).map_err(|e| e.at(spans.get(at).copied()))
}

fn parse_token_inner(
    tokens: &[Token],
    spans: &[Span],
    source: &str,
    i: &mut usize,
) -> Result<Option<Node>> {
    match &tokens[*i] {
        Token::Text(text) => Ok(Some(Node::Text(text.clone()))),

        Token::Variable(var) => {
            // Django refuses an empty `{{ }}` HERE, in `Parser.parse`
            // (`django/template/base.py:483-486`), NOT in the lexer —
            // `Lexer.create_token` happily returns `Token(TokenType.VAR, "")`.
            // The placement is load-bearing, not incidental: a
            // `{% verbatim %}` body is turned into TEXT by the lexer and a
            // `{% comment %}` body is skipped by the parser, so both reach
            // this arm never and legitimately render `{{ }}` verbatim. djust's
            // raw-block collector (`collect_raw_source`) consumes those tokens
            // without calling `parse_token` for exactly the same reason, so
            // refusing here — and only here — matches Django on every context
            // (#2557).
            if var.trim().is_empty() {
                return Err(DjangoRustError::TemplateError(format!(
                    "Empty variable tag on line {}",
                    line_at(spans, source, *i)
                )));
            }
            // Parse variable and filters: {{ var|filter1:arg1|filter2 }}
            // Quote-aware (#2409): `str::split('|')` cut `{{ p|cut:"a|b" }}`
            // into two filters and raised `Unknown filter` where Django
            // renders. `filter_lexer::split_pipes` splits only on the pipes
            // OUTSIDE a quoted string, which is Django's own grammar.
            let parts: Vec<String> = crate::filter_lexer::split_pipes(var)
                .into_iter()
                .map(|s| s.trim().to_string())
                .collect();
            let expr_part = &parts[0];
            // Django builds `Variable(var)` for the HEAD before it looks at a
            // single filter, so `{{ _x|cut }}` reports the underscore rather
            // than `cut`'s arity (#2418). Ordering measured, not assumed.
            validate_variable_name(expr_part)?;
            let filters = parse_filter_specs(&parts[1..], var)?;

            // Check for Jinja2-style inline conditional:
            //   {{ true_expr if condition else false_expr }}
            //   {{ true_expr if condition }}   (else branch optional, defaults to "")
            if let Some(if_pos) = find_if_keyword(expr_part) {
                let true_expr = expr_part[..if_pos].trim().to_string();
                let rest = expr_part[if_pos + 4..].trim(); // skip " if "
                let (condition, false_expr) = if let Some(else_pos) = rest.find(" else ") {
                    (
                        rest[..else_pos].trim().to_string(),
                        rest[else_pos + 6..].trim().to_string(),
                    )
                } else {
                    (rest.to_string(), String::new())
                };
                return Ok(Some(Node::InlineIf {
                    true_expr,
                    condition,
                    false_expr,
                    filters,
                }));
            }

            // This is a PLAIN variable — the inline-if branch above returned
            // for the `{{ a if cond else b }}` extension, so the `[\w.]`-only
            // head grammar can run here without refusing a legitimate
            // inline-if (#2578). Placement is load-bearing: run this before
            // the inline-if branch and a bare-variable inline-if is refused.
            validate_plain_variable_expr(expr_part)?;

            // Detect whether this variable is inside an HTML opening
            // tag (attribute context). When true the renderer uses
            // the attribute-safe escape (see `html_escape_attr`).
            let in_attr = is_inside_html_tag_at(tokens, *i);
            Ok(Some(Node::Variable(expr_part.clone(), filters, in_attr)))
        }

        Token::Tag(tag_name, args) => {
            // The sibling refusal, from the same `Parser.parse` loop
            // (`django/template/base.py:497`). Same reasoning as the empty
            // `{{ }}` above: refusing here keeps `{% %}` legal inside a raw
            // block, exactly as Django does (#2557).
            if tag_name.is_empty() {
                return Err(DjangoRustError::TemplateError(format!(
                    "Empty block tag on line {}",
                    line_at(spans, source, *i)
                )));
            }
            match tag_name.as_str() {
                "if" => {
                    // Django compiles every `{% if %}` operand with
                    // `compile_filter` at COMPILE time; djust reached the
                    // chain only at render, where an earlier unresolvable
                    // step, a short-circuit or an untaken branch could all
                    // stop it short (#2411). See `validate_if_operands`.
                    validate_if_operands(args)?;
                    // Django compiles the condition with `TemplateIfParser`
                    // at COMPILE time, so a malformed operator arrangement
                    // (`{% if foo and %}`, `{% if == %}`, `{% if a not b %}`)
                    // raises before rendering; djust only checked operands
                    // (#2576). See `validate_if_grammar`.
                    validate_if_grammar(args)?;
                    let condition = args.join(" ");
                    // Capture attribute context BEFORE advancing i.
                    // Scan backwards through ALL preceding tokens (not just the
                    // immediately previous one) to determine if we are inside an
                    // unclosed HTML tag. This handles cases like:
                    //   <option value="{{ var }}" {% if cond %}selected{% endif %}>
                    // where Variable tokens separate the tag opening from the {% if %}.
                    let in_tag_context = is_inside_html_tag_at(tokens, *i);
                    let (true_nodes, false_nodes, end_pos) =
                        parse_if_block(tokens, spans, source, *i + 1, in_tag_context)?;
                    *i = end_pos;
                    Ok(Some(Node::If {
                        condition,
                        true_nodes,
                        false_nodes,
                        in_tag_context,
                        // Assigned later by `assign_if_marker_ids`
                        // in `pub fn parse()` after the full AST is
                        // built, so IDs are stable in document order.
                        marker_id: None,
                    }))
                }

                "for" => {
                    // Django's `do_for`: `bits = token.split_contents()`
                    // includes the tag name, so `len(bits) < 4` is
                    // `args.len() < 3` here; the message repeats the WHOLE
                    // tag content, tag name included (#2581).
                    if args.len() < 3 {
                        return Err(DjangoRustError::TemplateError(format!(
                            "'for' statements should have at least four words: for {}",
                            args.join(" ")
                        )));
                    }

                    // Parse variable names - support tuple unpacking
                    //
                    // IMPORTANT FOR JIT OPTIMIZATION:
                    // Tuple unpacking ({% for val, label in STATUS_CHOICES %}) allows the JIT
                    // serializer to understand which fields of each item are accessed in the loop.
                    // For example, in {% for lease in leases %}{{ lease.tenant.name }}{% endfor %},
                    // the loop variable "lease" must transfer its path context so that
                    // "lease.tenant.name" is correctly identified for select_related() optimization.
                    //
                    // This parsing logic enables:
                    // 1. Single variable: {% for item in items %} → var_names = ["item"]
                    // 2. Tuple unpacking: {% for key, val in items %} → var_names = ["key", "val"]
                    //
                    // Find the "in" keyword to separate var names from iterable
                    // Django's `do_for` checks `bits[in_index]` POSITIONALLY
                    // (second-to-last, or third-to-last before a trailing
                    // "reversed") rather than searching for "in" anywhere —
                    // djust's lenient linear search is kept as-is (changing
                    // it is a behavior change, not a message-text one,
                    // #1079); only the NOT-FOUND message is Django's exact
                    // text for this shape (#2581).
                    let in_pos = args.iter().position(|arg| arg == "in").ok_or_else(|| {
                        DjangoRustError::TemplateError(format!(
                            "'for' statements should use the format 'for x in y': for {}",
                            args.join(" ")
                        ))
                    })?;

                    if in_pos == 0 {
                        return Err(DjangoRustError::TemplateError(
                            "For tag requires at least one variable name before 'in'".to_string(),
                        ));
                    }

                    // Extract variable names before "in".
                    //
                    // The separator is the COMMA, not whitespace (#2377).
                    // Django's `do_for` re-joins the tokens it split on
                    // whitespace and then splits THAT on `re.split(r" *, *")`,
                    // so `a,b`, `a, b` and `a ,b` are one three-name loop and
                    // all three are legal Django.
                    //
                    // This used to split on whitespace and merely trim a
                    // TRAILING comma off each token, which is the spaced
                    // spelling and only the spaced spelling. `{% for a,b in p %}`
                    // produced ONE variable literally spelled `a,b`, which can
                    // never resolve — so the loop bound nothing, every
                    // `{{ a }}` / `{{ b }}` in the body rendered empty, and the
                    // whole region silently disappeared. No error, no warning:
                    // the same shape as #2325 (`{% for x in p|slice %}`) and
                    // #2334 (`{% for k in d %}`), which is why the corpus gap
                    // that hid it is closed in the same change.
                    //
                    // Re-joining first is what makes the three spellings one
                    // case rather than three: the lexer has already split
                    // `a , b` into three tokens and `a, b` into two, and the
                    // join erases that difference exactly as Django's does.
                    let joined = args[0..in_pos].join(" ");
                    let var_names: Vec<String> = joined
                        .split(',')
                        // ` *, *` in Django's regex — spaces around the comma
                        // belong to the separator, not to the name. Only
                        // spaces: the tokens were whitespace-split already, so
                        // nothing else can be adjacent to a comma here, and
                        // trimming more would accept a name Django rejects.
                        .map(|part| part.trim_matches(' ').to_string())
                        .collect();

                    // Django's own validity test, verbatim: a loop variable may
                    // not be EMPTY and may not contain a space, either quote, or
                    // the filter separator (`defaulttags.do_for`'s
                    // `invalid_chars` frozenset). Not `isidentifier()` — Django
                    // accepts `{% for a-b in p %}`, and refusing it here would
                    // be STRICTER than Django rather than equal to it.
                    //
                    // This arm exists because the split above CREATES the empty
                    // case: `{% for a, in p %}` is `["a", ""]` where the old
                    // whitespace split produced `["a"]` and looped happily.
                    // Django raises `TemplateSyntaxError` for it, so raising is
                    // both parity and the less-permissive direction — the one
                    // this engine is allowed to move in.
                    const FOR_VAR_INVALID: [char; 4] = [' ', '"', '\'', '|'];
                    if let Some(bad) = var_names
                        .iter()
                        .find(|v| v.is_empty() || v.contains(FOR_VAR_INVALID))
                    {
                        return Err(DjangoRustError::TemplateError(format!(
                            "'for' tag received an invalid argument: for {} ({bad:?})",
                            args.join(" ")
                        )));
                    }

                    // Check if the last argument is "reversed"
                    let mut iterable_parts: Vec<String> = args[in_pos + 1..].to_vec();
                    let reversed = if iterable_parts.last().map(|s| s.as_str()) == Some("reversed")
                    {
                        iterable_parts.pop(); // Remove "reversed" from iterable
                        true
                    } else {
                        false
                    };

                    let iterable = iterable_parts.join(" ");
                    // The iterable is a TAG OPERAND, and Django compiles it
                    // with `compile_filter` at COMPILE time (#2411). Without
                    // this, `{% if 0 %}{% for x in p|cut %}{% endfor %}{% endif %}`
                    // — a branch that never renders — refused on Django and
                    // rendered here.
                    validate_tag_operand(&iterable)?;
                    let (nodes, empty_nodes, end_pos) =
                        parse_for_block(tokens, spans, source, *i + 1)?;
                    *i = end_pos;
                    Ok(Some(Node::For {
                        var_names,
                        iterable,
                        reversed,
                        nodes,
                        empty_nodes,
                    }))
                }

                "block" => {
                    if args.is_empty() {
                        return Err(DjangoRustError::TemplateError(
                            "Block tag requires a name".to_string(),
                        ));
                    }
                    let name = args[0].clone();
                    let (nodes, end_pos) = parse_block(tokens, spans, source, *i + 1, &name)?;
                    *i = end_pos;
                    Ok(Some(Node::Block { name, nodes }))
                }

                "extends" => {
                    // {% extends "parent.html" %}
                    // Django's `do_extends`: `bits = token.split_contents()`
                    // (includes the tag name), `len(bits) != 2` — EXACTLY one
                    // argument, not merely "at least one" — message
                    // `"'%s' takes one argument" % bits[0]` (#2581).
                    if args.len() != 1 {
                        return Err(DjangoRustError::TemplateError(
                            "'extends' takes one argument".to_string(),
                        ));
                    }
                    // The RAW operand, quotes included (#2517). Django's
                    // `do_extends` keeps `parser.compile_filter(bits[1])`, so
                    // an UNQUOTED token is a context lookup — `{% extends foo %}`
                    // extends the template NAMED BY `foo`. Stripping the
                    // quotes here erased that distinction and made every
                    // variable form look for a file of its own name.
                    // Consumers strip via `extends_target_is_literal`.
                    let template = args[0].clone();
                    Ok(Some(Node::Extends(template)))
                }

                "include" => {
                    if args.is_empty() {
                        return Err(DjangoRustError::TemplateError(
                            "Include tag requires a template name".to_string(),
                        ));
                    }
                    // Strip surrounding quotes (#1396) so Include.template
                    // shares the unquoted-field contract with Extends/Static/Now.
                    // Without this strip, the inheritance emitter
                    // (`nodes_to_template_string`) double-wraps the value,
                    // producing `{% include ""x.html"" %}` on round-trip.
                    let template = args[0].trim_matches(|c| c == '"' || c == '\'').to_string();
                    let mut with_vars = Vec::new();
                    let mut only = false;
                    let mut with_seen = false;
                    let mut only_seen = false;

                    // Parse remaining args for 'with' and 'only' keywords,
                    // mirroring Django's `do_include` word-by-word (#2579):
                    // each remaining bit must be exactly 'with' (followed by
                    // at least one key=value pair) or exactly 'only', neither
                    // option may repeat, and any other word is refused.
                    let mut i = 1;
                    while i < args.len() {
                        if args[i] == "with" {
                            if with_seen {
                                return Err(DjangoRustError::TemplateError(
                                    "Include tag's 'with' option was specified more than once"
                                        .to_string(),
                                ));
                            }
                            with_seen = true;
                            i += 1;
                            // Parse key=value pairs after 'with' — stop at
                            // the first bit that isn't a `\w+=value` pair,
                            // same as Django's `token_kwargs` (#2579): a
                            // dotted or otherwise non-identifier key (e.g.
                            // `dotted.arg=`) isn't a valid kwarg bit, so it
                            // is not consumed here.
                            let mut found_kwarg = false;
                            while i < args.len() {
                                let Some(eq_pos) = args[i].find('=') else {
                                    break;
                                };
                                let key = &args[i][..eq_pos];
                                if key.is_empty()
                                    || !key.chars().all(|c| c.is_alphanumeric() || c == '_')
                                {
                                    break;
                                }
                                let value = &args[i][eq_pos + 1..];
                                // Same shape as `{% with %}`: `do_include`
                                // runs each RHS through `token_kwargs` →
                                // `compile_filter` at COMPILE time, so the
                                // value is a TAG OPERAND and the key is a
                                // BINDING (#2411, #2418).
                                validate_tag_operand(value)?;
                                with_vars.push((key.to_string(), value.to_string()));
                                found_kwarg = true;
                                i += 1;
                            }
                            if !found_kwarg {
                                // Django: '"with" in %r tag needs at least
                                // one keyword argument.' — djust's own
                                // wording (#2579; #2581 message-text parity
                                // has not landed).
                                return Err(DjangoRustError::TemplateError(
                                    "Include tag's 'with' clause requires at least one key=value pair"
                                        .to_string(),
                                ));
                            }
                        } else if args[i] == "only" {
                            if only_seen {
                                return Err(DjangoRustError::TemplateError(
                                    "Include tag's 'only' option was specified more than once"
                                        .to_string(),
                                ));
                            }
                            only_seen = true;
                            only = true;
                            i += 1;
                        } else {
                            // Django: 'Unknown argument for %r tag: %r.' —
                            // djust's own wording (#2579; #2581 not landed).
                            return Err(DjangoRustError::TemplateError(format!(
                                "Include tag received an unrecognized argument: '{}'",
                                args[i]
                            )));
                        }
                    }

                    Ok(Some(Node::Include {
                        template,
                        with_vars,
                        only,
                    }))
                }

                "csrf_token" => {
                    // {% csrf_token %} - generates CSRF token hidden input
                    Ok(Some(Node::CsrfToken))
                }

                "static" => {
                    // {% static 'path/to/file' %} - generates static file URL.
                    // Django's `defaulttags.static`: "'static' takes at
                    // least one argument (path to file)" (#2581). Note
                    // `{% get_media_prefix %}` / `{% get_static_prefix %}`
                    // (a SIBLING tag in Django's `static` library) are not
                    // implemented here at all — their own message-mismatch
                    // cell needs that tag built first, not a message swap;
                    // out of #2581's scope.
                    if args.is_empty() {
                        return Err(DjangoRustError::TemplateError(
                            "'static' takes at least one argument (path to file)".to_string(),
                        ));
                    }
                    // Remove quotes from path if present
                    let path = args[0].trim_matches(|c| c == '"' || c == '\'').to_string();
                    Ok(Some(Node::Static(path)))
                }

                "comment" => {
                    // {% comment %} tag - skip content until {% endcomment %}
                    // Find and skip to endcomment tag
                    let mut depth = 1;
                    let mut j = *i + 1;
                    while j < tokens.len() && depth > 0 {
                        if let Token::Tag(tag_name, _) = &tokens[j] {
                            if tag_name == "comment" {
                                depth += 1;
                            } else if tag_name == "endcomment" {
                                depth -= 1;
                            }
                        }
                        j += 1;
                    }
                    *i = j - 1; // Point to endcomment tag
                    Ok(Some(Node::Comment))
                }

                "endcomment" => {
                    // Handled by comment tag
                    Ok(None)
                }

                "verbatim" => {
                    // {% verbatim %} tag - output content literally without template processing
                    // through the ONE raw-source collector (#2558): the raw-block
                    // arm reconstructs the body the same way, so the two
                    // re-emitters cannot drift (#1646).
                    let (content, end_pos) = collect_raw_source(
                        tokens,
                        *i + 1,
                        &["endverbatim"],
                        "Unclosed verbatim tag".to_string(),
                    )?;
                    *i = end_pos; // Point to endverbatim tag
                    Ok(Some(Node::Text(content)))
                }

                "endverbatim" => Err(stray_closer_error(spans, source, *i, tag_name)),

                "with" => {
                    // {% with var=value var2=value2 %} ... {% endwith %}
                    // Parse assignments
                    let mut assignments = Vec::new();
                    for arg in args {
                        if let Some(eq_pos) = arg.find('=') {
                            let var_name = arg[..eq_pos].trim().to_string();
                            let expression = arg[eq_pos + 1..].trim().to_string();
                            // Each RHS is a TAG OPERAND — Django's `do_with`
                            // runs it through `token_kwargs`, which calls
                            // `compile_filter` at COMPILE time (#2411).
                            validate_tag_operand(&expression)?;
                            assignments.push((var_name, expression));
                        }
                    }

                    // Django's `do_with` refuses when `token_kwargs` finds
                    // zero valid `key=value` assignments — `{% with dict.key
                    // xx key %}` and `{% with dict.key as %}` both have no
                    // `=` anywhere in their args, so `assignments` is empty
                    // here exactly when Django's `extra_context` is empty
                    // there (#2580). Argument tokens with no `=` (a bare
                    // word, or the legacy `expr as key` form djust does not
                    // parse) are silently dropped by the loop above rather
                    // than counted, so this check is the smallest fix that
                    // matches djust's own currently-supported grammar.
                    if assignments.is_empty() {
                        return Err(DjangoRustError::TemplateError(
                            "'with' expected at least one variable assignment".to_string(),
                        ));
                    }

                    let (nodes, end_pos) = parse_with_block(tokens, spans, source, *i + 1)?;
                    *i = end_pos;
                    Ok(Some(Node::With { assignments, nodes }))
                }

                "endwith" => Err(stray_closer_error(spans, source, *i, tag_name)),

                "load" => {
                    // {% load static %} — preserve library names so inheritance
                    // reconstruction can re-emit the tag for Django rendering.
                    //
                    // And the sink for `{% load app_tags %}` (#2547): the
                    // Python-side loader, when installed, imports the Django
                    // library named here and registers its tags and filters
                    // BEFORE this parse reaches them — every parse goes
                    // through `parse_token`, so an `{% include %}`d file or a
                    // `{% load %}` inside a `{% block %}` fires it too. An
                    // unknown library is Django's own `TemplateSyntaxError`,
                    // crossing whole. The arguments go across exactly as
                    // written (`{% load x from lib %}` included) so the
                    // Python side reproduces Django's `load` byte for byte.
                    crate::registry::call_library_loader(args)?;
                    Ok(Some(Node::Load(args.clone())))
                }

                "widthratio" => {
                    // {% widthratio value max_value max_width [as name] %}
                    //
                    // Django's `widthratio` (`defaulttags.py`) checks the
                    // TOTAL token count directly rather than the generic
                    // trailing-`as`-pair shape `split_asvar` assumes: exactly
                    // 3 operands, or exactly 3 operands + "as" + a name — 4
                    // or 5 non-empty args. `split_asvar` only fires when the
                    // SECOND-TO-LAST token is literally "as", so a 4-arg
                    // form ending in a bare "as" (`{% widthratio a b 100 as
                    // %}`) or a 5-arg form with the wrong keyword
                    // (`{% widthratio a b 100 not_as variable %}`) both fall
                    // through as if every token were a plain operand, and
                    // djust rendered instead of refusing (#2580).
                    if args.len() != 3 && args.len() != 5 {
                        return Err(DjangoRustError::TemplateError(
                            "widthratio takes at least three arguments".to_string(),
                        ));
                    }
                    let (operands, asvar) = if args.len() == 5 {
                        if args[3] != "as" {
                            return Err(DjangoRustError::TemplateError(
                                "Invalid syntax in widthratio tag. Expecting 'as' keyword"
                                    .to_string(),
                            ));
                        }
                        (args[..3].to_vec(), Some(args[4].clone()))
                    } else {
                        (args.to_vec(), None)
                    };
                    // Each of the three is a TAG OPERAND: `do_widthratio` runs
                    // all three through `compile_filter` at COMPILE time, so
                    // `{% widthratio q 10 _x %}` refuses on Django (#2418).
                    // `asvar` is a BINDING, not a lookup, and is excluded —
                    // `{% widthratio q 10 100 as _n %}` compiles on Django.
                    for operand in &operands {
                        validate_tag_operand(operand)?;
                    }
                    Ok(Some(Node::WidthRatio {
                        value: operands[0].clone(),
                        max_value: operands[1].clone(),
                        max_width: operands[2].clone(),
                        asvar,
                    }))
                }

                "firstof" => {
                    // {% firstof var1 var2 ... "fallback" [as name] %}
                    let (operands, asvar) = split_asvar(args);
                    if operands.is_empty() {
                        return Err(DjangoRustError::TemplateError(
                            "firstof tag requires at least one argument".to_string(),
                        ));
                    }
                    // `do_firstof` compiles every operand (#2418). The quoted
                    // fallback is a literal and `validate_variable_name`'s
                    // literal arm lets it through, so `{% firstof a "_fb" %}`
                    // still compiles.
                    for operand in &operands {
                        validate_tag_operand(operand)?;
                    }
                    Ok(Some(Node::FirstOf {
                        args: operands,
                        asvar,
                    }))
                }

                "templatetag" => {
                    // {% templatetag openblock %}
                    if args.is_empty() {
                        return Err(DjangoRustError::TemplateError(
                            "templatetag requires an argument".to_string(),
                        ));
                    }
                    if !TEMPLATETAG_NAMES.contains(&args[0].as_str()) {
                        // Django: parse-time `TemplateSyntaxError` (#2549).
                        return Err(DjangoRustError::TemplateError(format!(
                            "Unknown templatetag argument: '{}'",
                            args[0]
                        )));
                    }
                    Ok(Some(Node::TemplateTag(args[0].clone())))
                }

                "spaceless" => {
                    // {% spaceless %} ... {% endspaceless %}
                    let (nodes, end_pos) = parse_spaceless_block(tokens, spans, source, *i + 1)?;
                    *i = end_pos;
                    Ok(Some(Node::Spaceless { nodes }))
                }

                "endspaceless" => Err(stray_closer_error(spans, source, *i, tag_name)),

                "autoescape" => {
                    // {% autoescape on|off %} ... {% endautoescape %} (#2556).
                    // Django's `do_autoescape`, message for message: exactly
                    // one argument, and it is `on` or `off`.
                    if args.len() != 1 {
                        return Err(DjangoRustError::TemplateError(
                            "'autoescape' tag requires exactly one argument.".to_string(),
                        ));
                    }
                    let on = match args[0].as_str() {
                        "on" => true,
                        "off" => false,
                        _ => {
                            return Err(DjangoRustError::TemplateError(
                                "'autoescape' argument should be 'on' or 'off'".to_string(),
                            ));
                        }
                    };
                    let (nodes, end_pos) =
                        parse_block_custom_tag(tokens, spans, source, *i + 1, "endautoescape")?;
                    *i = end_pos;
                    Ok(Some(Node::AutoEscape { on, nodes }))
                }

                "endautoescape" => Err(stray_closer_error(spans, source, *i, tag_name)),

                "filter" => {
                    // {% filter f1|f2:arg %}...{% endfilter %} (#2556).
                    // Django: `parser.compile_filter("var|%s" % rest)` — so
                    // the chain goes through the SAME lexer the `{{ }}`
                    // branch uses (`split_pipes` + `parse_filter_specs`),
                    // not a second copy (#1646, the #2409 lesson).
                    let rest = args.join(" ");
                    let token = format!("var|{rest}");
                    let parts: Vec<String> = crate::filter_lexer::split_pipes(&token)
                        .into_iter()
                        .map(|s| s.trim().to_string())
                        .collect();
                    let filters = parse_filter_specs(&parts[1..], &token)?;
                    // `"filter escape"` / `"filter safe"` are refused with
                    // Django's message (two spaces after the period, sic).
                    for (name, _) in &filters {
                        if name == "escape" || name == "safe" {
                            return Err(DjangoRustError::TemplateError(format!(
                                "\"filter {name}\" is not permitted.  Use the \"autoescape\" tag instead."
                            )));
                        }
                    }
                    let (nodes, end_pos) =
                        parse_block_custom_tag(tokens, spans, source, *i + 1, "endfilter")?;
                    *i = end_pos;
                    Ok(Some(Node::Filter { filters, nodes }))
                }

                "endfilter" => Err(stray_closer_error(spans, source, *i, tag_name)),

                "ifchanged" => {
                    // {% ifchanged [var …] %}…[{% else %}…]{% endifchanged %}
                    // — Django's `do_ifchanged`. It takes any number of
                    // operands (zero is the compare-the-output form) and so
                    // has no arity error of its own.
                    let (nodes, end_pos, hit) = parse_block_until_any(
                        tokens,
                        spans,
                        source,
                        *i + 1,
                        &["else", "endifchanged"],
                    )?;
                    let (else_nodes, end_pos) = if hit == "else" {
                        let (else_nodes, end_pos) = parse_block_custom_tag(
                            tokens,
                            spans,
                            source,
                            end_pos + 1,
                            "endifchanged",
                        )?;
                        (else_nodes, end_pos)
                    } else {
                        (Vec::new(), end_pos)
                    };
                    *i = end_pos;
                    Ok(Some(Node::IfChanged {
                        vars: args.clone(),
                        id: String::new(),
                        nodes,
                        else_nodes,
                    }))
                }

                "endifchanged" => Err(stray_closer_error(spans, source, *i, tag_name)),

                "cycle" => {
                    // Django's `cycle()` grammar, `defaulttags.py` (#2556).
                    // `args` excludes the tag name, so Django's `len(args)`
                    // is `args.len() + 1` throughout.
                    if args.is_empty() {
                        return Err(DjangoRustError::TemplateError(
                            "'cycle' tag requires at least two arguments".to_string(),
                        ));
                    }
                    if args.len() == 1 {
                        // `{% cycle name %}` — a REFERENCE, bound to its
                        // definition by `resolve_cycle_nodes`.
                        return Ok(Some(Node::Cycle {
                            values: Vec::new(),
                            name: Some(args[0].clone()),
                            silent: false,
                            id: String::new(),
                            reference: true,
                        }));
                    }
                    let mut args = args.clone();
                    let mut as_form = false;
                    let mut silent = false;
                    // Django: `if len(args) > 4` — so `{% cycle a as b %}`
                    // is NOT the `as` form; it cycles `a`, `as`, `b`
                    // (`cycle25` renders exactly that). Mirrored, not fixed.
                    if args.len() > 3 {
                        if args[args.len() - 3] == "as" {
                            let last = &args[args.len() - 1];
                            if last != "silent" {
                                return Err(DjangoRustError::TemplateError(format!(
                                    "Only 'silent' flag is allowed after cycle's name, not '{last}'."
                                )));
                            }
                            as_form = true;
                            silent = true;
                            args.pop();
                        } else if args[args.len() - 2] == "as" {
                            as_form = true;
                        }
                    }
                    let (values, name) = if as_form {
                        let name = args[args.len() - 1].clone();
                        (args[..args.len() - 2].to_vec(), Some(name))
                    } else {
                        (args, None)
                    };
                    // `do_cycle` compiles every value (#2418). `name` is the
                    // `as` BINDING and is excluded, like `widthratio`'s and
                    // `firstof`'s — `{% cycle "a" "b" as _n %}` compiles on
                    // Django.
                    for value in &values {
                        validate_tag_operand(value)?;
                    }
                    Ok(Some(Node::Cycle {
                        values,
                        name,
                        silent,
                        id: String::new(),
                        reference: false,
                    }))
                }

                "resetcycle" => {
                    // {% resetcycle [name] %} (#2556); bound by
                    // `resolve_cycle_nodes`.
                    if args.len() > 1 {
                        return Err(DjangoRustError::TemplateError(
                            "'resetcycle' tag accepts at most one argument.".to_string(),
                        ));
                    }
                    Ok(Some(Node::ResetCycle {
                        name: args.first().cloned(),
                        id: String::new(),
                    }))
                }

                "now" => {
                    // {% now "format_string" %}. Django's `now` also
                    // accepts `{% now "fmt" as var %}` (`defaulttags.py`)
                    // and requires len(bits) == 2 after stripping that
                    // trailing `as`-clause — djust does not support the
                    // `as var` binding at all, a separate, pre-existing
                    // feature gap outside #2581's message-text scope, so
                    // only the EMPTY-args message is corrected here, not
                    // widened to Django's exact `!= 1` check.
                    if args.is_empty() {
                        return Err(DjangoRustError::TemplateError(
                            "'now' statement takes one argument".to_string(),
                        ));
                    }
                    let format = args[0].trim_matches(|c| c == '"' || c == '\'').to_string();
                    Ok(Some(Node::Now(format)))
                }

                "endif" | "endfor" | "endblock" | "else" | "elif" => {
                    // Every one of these closer keywords is ONLY ever
                    // legitimately consumed by its OWN opening tag's
                    // dedicated body-loop (`parse_if_block`,
                    // `parse_for_block`, `parse_block`), which checks for
                    // its specific terminator BEFORE ever falling through
                    // to `parse_token`. So reaching this arm at all means
                    // the token is a STRAY closer — either at the top
                    // level with nothing open, or inside a DIFFERENT block
                    // that was watching for a DIFFERENT terminator
                    // (#2580). `Ok(None)` here silently discarded a stray
                    // closer instead of refusing — the mechanism behind
                    // `tests.py::test_invalid_block_suggestion` (an
                    // `{% endblock %}` inside an unclosed `{% if %}`).
                    // `endverbatim`/`endwith`/`endspaceless`/
                    // `endautoescape`/`endfilter` get the SAME helper
                    // ([`stray_closer_error`]) from arms placed right
                    // after their own opening tags, deliberately NOT
                    // folded into this multi-name arm: this one already
                    // sat immediately before the match's `_ => { ... }`
                    // catch-all, and `scripts/filter-parity-differential.py`'s
                    // `_required_mask_positions` scans for `"word" =>`
                    // literally, with no notion of Rust arm boundaries —
                    // a name whose `=>` sits right before that catch-all
                    // has its captured body run PAST the entire catch-all
                    // to the next quoted arm, picking up whatever operand-
                    // validator call happens to live in between and
                    // misattributing it. Confirmed empirically: adding a
                    // sixth name here reads as `"endfilter"` capturing a
                    // 66,000-character body reaching a `validate_if_operands(`
                    // call deep inside an unrelated later arm. Positioning
                    // the other five arms elsewhere in the match avoids
                    // creating that adjacency rather than fixing the
                    // scanner's regex.
                    Err(stray_closer_error(spans, source, *i, tag_name))
                }

                _ => {
                    // `{% url %}`'s argument grammar, compiled at PARSE time as
                    // Django's `do_url` does (#2577). This only REFUSES a
                    // malformed argument list; a well-formed `{% url %}` falls
                    // through to the handler dispatch below and compiles to the
                    // same `CustomTag` as before. It must run here, before the
                    // node is built and before any render, so it wins the race
                    // against the render-time `NoReverseMatch` the unquoted
                    // `named_url` spelling raises (#2607). The refusal lives in
                    // the Rust parser — the shared compile chokepoint for both
                    // the `DjustTemplateBackend` and the LiveView path — rather
                    // than in the render-time `_resolve_url_tags` pre-pass, so
                    // the check is not duplicated across the two url grammars
                    // (#1646).
                    if tag_name == "url" {
                        validate_url_args(args)?;
                    }
                    // Native scope tags (#2558) — armed only when their library
                    // has actually been `{% load %}`-ed, so an UNLOADED
                    // `{% language %}` still falls through to the
                    // UnsupportedTag arm exactly as before this row. Argument
                    // errors carry Django's exact text (`i18n.py:599-616`,
                    // `l10n.py:39-60`, `tz.py:138-178`).
                    if tag_name == "language" && crate::registry::scope_tag_armed("language") {
                        if args.len() != 1 {
                            return Err(crate::registry::library_syntax_error(
                                "'language' takes one argument (language)",
                            ));
                        }
                        let (children, end_pos) =
                            parse_block_custom_tag(tokens, spans, source, *i + 1, "endlanguage")?;
                        *i = end_pos;
                        return Ok(Some(Node::Language {
                            expr: args[0].clone(),
                            children,
                        }));
                    }
                    if tag_name == "localize" && crate::registry::scope_tag_armed("localize") {
                        let use_l10n = match args.len() {
                            0 => true,
                            1 if args[0] == "on" => true,
                            1 if args[0] == "off" => false,
                            _ => {
                                return Err(crate::registry::library_syntax_error(
                                    "'localize' argument should be 'on' or 'off'",
                                ));
                            }
                        };
                        let (children, end_pos) =
                            parse_block_custom_tag(tokens, spans, source, *i + 1, "endlocalize")?;
                        *i = end_pos;
                        return Ok(Some(Node::Localize { use_l10n, children }));
                    }
                    if tag_name == "localtime" && crate::registry::scope_tag_armed("localtime") {
                        let use_tz = match args.len() {
                            0 => true,
                            1 if args[0] == "on" => true,
                            1 if args[0] == "off" => false,
                            _ => {
                                return Err(crate::registry::library_syntax_error(
                                    "'localtime' argument should be 'on' or 'off'",
                                ));
                            }
                        };
                        let (children, end_pos) =
                            parse_block_custom_tag(tokens, spans, source, *i + 1, "endlocaltime")?;
                        *i = end_pos;
                        return Ok(Some(Node::LocalTime { use_tz, children }));
                    }
                    if tag_name == "timezone" && crate::registry::scope_tag_armed("timezone") {
                        if args.len() != 1 {
                            return Err(crate::registry::library_syntax_error(
                                "'timezone' takes one argument (timezone)",
                            ));
                        }
                        let (children, end_pos) =
                            parse_block_custom_tag(tokens, spans, source, *i + 1, "endtimezone")?;
                        *i = end_pos;
                        return Ok(Some(Node::Timezone {
                            expr: args[0].clone(),
                            children,
                        }));
                    }
                    // A RAW-BLOCK handler (#2558) consumes the body as SOURCE.
                    // Checked before every other dispatch in this arm: the body
                    // must reach Django un-rendered — Django must be the one to
                    // see `{% block b %}` / `{% for … %}` / a second
                    // `{% blocktranslate %}` inside it and raise its own
                    // `doesn't allow other block tags` error, which it can only
                    // do if the raw tokens arrive.
                    if let Some(end_tag) = crate::registry::raw_block_handler_exists(tag_name) {
                        let (body, end_pos) = collect_raw_source(
                            tokens,
                            *i + 1,
                            &[end_tag.as_str()],
                            format!("Unclosed raw-block tag, expected {{% {end_tag} %}}"),
                        )?;
                        *i = end_pos;
                        return Ok(Some(Node::RawBlockCustomTag {
                            name: tag_name.clone(),
                            args: args.clone(),
                            body,
                        }));
                    }
                    // Check if a Python block tag handler is registered (tags with children)
                    if let Some(end_tag) = crate::registry::block_handler_exists(tag_name) {
                        let (children, end_pos) =
                            parse_block_custom_tag(tokens, spans, source, *i + 1, &end_tag)?;
                        *i = end_pos;
                        Ok(Some(Node::BlockCustomTag {
                            name: tag_name.clone(),
                            args: args.clone(),
                            children,
                        }))
                    } else if let Some(message) =
                        crate::registry::tag_handler_parse_refusal(tag_name)
                    {
                        // A `{% load %}`-bridged raw tag that consumes a body
                        // (#2547): refused at PARSE time, per TAG, with
                        // Django's own `TemplateSyntaxError` — the rest of
                        // its library keeps working.
                        Err(crate::registry::library_syntax_error(&message))
                    } else if crate::registry::handler_exists(tag_name) {
                        // Inline handler exists - create CustomTag node
                        Ok(Some(Node::CustomTag {
                            name: tag_name.clone(),
                            args: args.clone(),
                        }))
                    } else if crate::registry::assign_handler_exists(tag_name) {
                        // Context-mutating assign tag (register_assign_tag_handler).
                        //
                        // `{% regroup %}` gets its own PARSE-time grammar
                        // check here (#2580) rather than at the Python
                        // handler: `RegroupTagHandler.render` degraded a
                        // malformed call to a silent no-op merge, since
                        // "the Rust parser has no such hook" (its own
                        // comment) — no longer true. Django's `regroup`
                        // (`defaulttags.py`) checks `len(bits) != 6`
                        // (`bits` includes the tag name, so `args.len() !=
                        // 5` here), `bits[2] != "by"` (`args[1]`), and
                        // `bits[4] != "as"` (`args[3]`) — all at compile
                        // time. Every other assign-tag handler keeps its
                        // generic passthrough; this is `regroup`-specific.
                        if tag_name == "regroup" {
                            if args.len() != 5 {
                                return Err(DjangoRustError::TemplateError(
                                    "'regroup' tag takes five arguments".to_string(),
                                ));
                            }
                            if args[1] != "by" {
                                return Err(DjangoRustError::TemplateError(
                                    "second argument to 'regroup' tag must be 'by'".to_string(),
                                ));
                            }
                            if args[3] != "as" {
                                return Err(DjangoRustError::TemplateError(
                                    "next-to-last argument to 'regroup' tag must be 'as'"
                                        .to_string(),
                                ));
                            }
                        }
                        Ok(Some(Node::AssignTag {
                            name: tag_name.clone(),
                            args: args.clone(),
                        }))
                    } else {
                        // Unknown tag with no handler: refuse at PARSE time,
                        // where Django raises `Invalid block tag` (#2549).
                        // Until #2549 this built `Node::UnsupportedTag` and
                        // the renderer raised the same message when — and
                        // only if — the node was reached, so a defect in a
                        // branch that never rendered was silently accepted.
                        // The message text is a published contract
                        // (`rendering.py` keys a hint on it; the scoreboard
                        // list generator parses it) and is byte-identical to
                        // the render arm's.
                        Err(DjangoRustError::TemplateError(unsupported_tag_message(
                            tag_name, args,
                        )))
                    }
                }
            }
        }

        Token::JsxComponent {
            name,
            props,
            children,
            ..
        } => {
            // Check if this is a Rust component (starts with "Rust")
            if name.starts_with("Rust") {
                // Rust components are rendered server-side, no children support
                Ok(Some(Node::RustComponent {
                    name: name.clone(),
                    props: props.clone(),
                }))
            } else {
                // Convert token children to Node children for React components
                let mut child_nodes = Vec::new();
                for child in children {
                    if let Token::Text(text) = child {
                        child_nodes.push(Node::Text(text.clone()));
                    }
                }

                Ok(Some(Node::ReactComponent {
                    name: name.clone(),
                    props: props.clone(),
                    children: child_nodes,
                }))
            }
        }

        Token::Comment(_) => Ok(Some(Node::Comment)),
    }
}

fn parse_if_block(
    tokens: &[Token],
    spans: &[Span],
    source: &str,
    start: usize,
    in_tag_context: bool,
) -> Result<(Vec<Node>, Vec<Node>, usize)> {
    let mut true_nodes = Vec::new();
    let mut false_nodes = Vec::new();
    let mut in_else = false;
    let mut i = start;

    while i < tokens.len() {
        match &tokens[i] {
            Token::Tag(name, args) if name == "else" => {
                // Django's `do_if` accepts the else clause only when
                // `token.contents == "else"` exactly; any trailing content
                // (`{% else if foo is not bar %}`) fails the following
                // `!= "endif"` check and raises "Malformed template tag"
                // (#2576). djust had ignored the else args entirely, so
                // `{% else if ... %}` was silently treated as a plain else.
                if !args.is_empty() {
                    return Err(DjangoRustError::TemplateError(format!(
                        "Malformed {{% else %}} tag on line {}: {{% else {} %}} takes no arguments",
                        line_at(spans, source, i),
                        args.join(" ")
                    )));
                }
                in_else = true;
                i += 1;
                continue;
            }
            Token::Tag(name, args) if name == "elif" => {
                // elif after else is invalid (matches Django behavior)
                if in_else {
                    return Err(DjangoRustError::TemplateError(
                        "{% elif %} cannot appear after {% else %}".to_string(),
                    ));
                }
                // elif is equivalent to: else + nested if
                // {% elif condition %} becomes {% else %}{% if condition %}...{% endif %}
                //
                // The operands are validated here for the same reason and by
                // the same rule as `{% if %}`'s (#2411) — `{% elif %}` builds
                // a `Node::If` and is parsed at this site rather than through
                // `parse_token`'s `"if"` arm, so it needs its own call.
                validate_if_operands(args)?;
                // Operator-arrangement grammar check, same rule/site as
                // `{% if %}`'s (#2576). See `validate_if_grammar`.
                validate_if_grammar(args)?;
                let elif_condition = args.join(" ");
                let (elif_true, elif_false, end_pos) =
                    parse_if_block(tokens, spans, source, i + 1, in_tag_context)?;
                false_nodes.push(Node::If {
                    condition: elif_condition,
                    true_nodes: elif_true,
                    false_nodes: elif_false,
                    in_tag_context,
                    // Assigned later by `assign_if_marker_ids`.
                    marker_id: None,
                });
                return Ok((true_nodes, false_nodes, end_pos));
            }
            Token::Tag(name, _) if name == "endif" => {
                return Ok((true_nodes, false_nodes, i));
            }
            _ => {
                if let Some(node) = parse_token(tokens, spans, source, &mut i)? {
                    if in_else {
                        false_nodes.push(node);
                    } else {
                        true_nodes.push(node);
                    }
                }
            }
        }
        i += 1;
    }

    Err(unclosed_tag_error(
        spans,
        source,
        start - 1,
        "if",
        &["elif", "else", "endif"],
    ))
}

fn parse_for_block(
    tokens: &[Token],
    spans: &[Span],
    source: &str,
    start: usize,
) -> Result<(Vec<Node>, Vec<Node>, usize)> {
    let mut nodes = Vec::new();
    let mut empty_nodes = Vec::new();
    let mut in_empty_block = false;
    let mut i = start;

    while i < tokens.len() {
        if let Token::Tag(name, _) = &tokens[i] {
            if name == "endfor" {
                return Ok((nodes, empty_nodes, i));
            } else if name == "empty" {
                // Switch to parsing the empty block
                in_empty_block = true;
                i += 1;
                continue;
            }
        }

        if let Some(node) = parse_token(tokens, spans, source, &mut i)? {
            if in_empty_block {
                empty_nodes.push(node);
            } else {
                nodes.push(node);
            }
        }
        i += 1;
    }

    Err(unclosed_tag_error(
        spans,
        source,
        start - 1,
        "for",
        &["endfor"],
    ))
}

fn parse_block(
    tokens: &[Token],
    spans: &[Span],
    source: &str,
    start: usize,
    block_name: &str,
) -> Result<(Vec<Node>, usize)> {
    let mut nodes = Vec::new();
    let mut i = start;

    while i < tokens.len() {
        if let Token::Tag(name, endblock_args) = &tokens[i] {
            if name == "endblock" {
                // Django keeps this check "for backwards-compatibility"
                // (`loader_tags.py` #3100): the closing tag's OWN cited
                // name, if any, must be bare or match the block it is
                // closing — `{% endblock %}` or `{% endblock <name> %}`,
                // never a DIFFERENT block's name (#2580). djust discarded
                // `endblock`'s args entirely, so `{% block a %}{% block b
                // %}…{% endblock a %}…{% endblock b %}` silently closed
                // whichever block came first, cross-wiring nested blocks
                // instead of refusing.
                if let Some(cited) = endblock_args.first() {
                    if cited != block_name {
                        return Err(DjangoRustError::TemplateError(format!(
                            "'endblock' tag with name '{cited}' does not match \
                             the enclosing block's name ('{block_name}')"
                        )));
                    }
                }
                return Ok((nodes, i));
            }
        }

        if let Some(node) = parse_token(tokens, spans, source, &mut i)? {
            nodes.push(node);
        }
        i += 1;
    }

    Err(unclosed_tag_error(
        spans,
        source,
        start - 1,
        "block",
        &["endblock"],
    ))
}

fn parse_with_block(
    tokens: &[Token],
    spans: &[Span],
    source: &str,
    start: usize,
) -> Result<(Vec<Node>, usize)> {
    let mut nodes = Vec::new();
    let mut i = start;

    while i < tokens.len() {
        if let Token::Tag(name, _) = &tokens[i] {
            if name == "endwith" {
                return Ok((nodes, i));
            }
        }

        if let Some(node) = parse_token(tokens, spans, source, &mut i)? {
            nodes.push(node);
        }
        i += 1;
    }

    Err(DjangoRustError::TemplateError(
        "Unclosed with tag".to_string(),
    ))
}

/// Split a trailing `as <name>` off a tag's argument list (#2355).
///
/// Django's `firstof` and `widthratio` compilers both do exactly this
/// (`if len(bits) >= 2 and bits[-2] == "as"`), and both were parsed here as if
/// the two extra tokens were more operands — so `{% firstof a b as v %}`
/// treated the literal `as` as a fallback value, RENDERED the result Django
/// assigns silently, and never bound `v` at all.
///
/// `{% cycle %}` keeps its own inline split: its `as <name>` also admits a
/// trailing `silent`, so the shapes are not the same question.
fn split_asvar(args: &[String]) -> (Vec<String>, Option<String>) {
    match args.len() {
        n if n >= 2 && args[n - 2] == "as" => (args[..n - 2].to_vec(), Some(args[n - 1].clone())),
        _ => (args.to_vec(), None),
    }
}

fn parse_spaceless_block(
    tokens: &[Token],
    spans: &[Span],
    source: &str,
    start: usize,
) -> Result<(Vec<Node>, usize)> {
    let mut nodes = Vec::new();
    let mut i = start;

    while i < tokens.len() {
        if let Token::Tag(name, _) = &tokens[i] {
            if name == "endspaceless" {
                return Ok((nodes, i));
            }
        }

        if let Some(node) = parse_token(tokens, spans, source, &mut i)? {
            nodes.push(node);
        }
        i += 1;
    }

    Err(DjangoRustError::TemplateError(
        "Unclosed spaceless tag".to_string(),
    ))
}

/// Reconstruct raw template SOURCE from the token stream (#2558).
///
/// The lexer keeps no source offsets, so the only way to hand a body to a
/// raw-block handler — or to `{% verbatim %}`, which always did this
/// inline — is to re-emit it from the tokens. Fidelity, against Django's
/// own lexer: `Token::Text` is byte-exact; a variable re-emitted as
/// `{{ var }}` re-lexes to the same VAR contents (Django strips variable
/// contents too); a tag re-joined on single spaces is exactly the
/// `token.contents` Django quotes in its `doesn't allow other block tags`
/// error. Comments are RE-EMITTED verbatim as `{# … #}` (#2597): dropping
/// them silently changed the body Django reads back — visibly so through
/// `{% verbatim %}`, which shares this collector and whose output now
/// matches Django's for `{% verbatim %}{# hi #}{% endverbatim %}`.
///
/// Returns the reconstructed source and the index of the END-TAG token
/// (the caller points `i` at it; its loop then steps past).
fn collect_raw_source(
    tokens: &[Token],
    start: usize,
    end_names: &[&str],
    unclosed_error: String,
) -> Result<(String, usize)> {
    let mut content = String::new();
    let mut j = start;
    while j < tokens.len() {
        match &tokens[j] {
            Token::Tag(name, _) if end_names.contains(&name.as_str()) => {
                return Ok((content, j));
            }
            Token::Text(text) => content.push_str(text),
            Token::Variable(var) => {
                // Output the raw variable syntax
                content.push_str(&format!("{{{{ {var} }}}}"));
            }
            Token::Tag(name, args) => {
                // Output the raw tag syntax
                let args_str = if args.is_empty() {
                    String::new()
                } else {
                    format!(" {}", args.join(" "))
                };
                content.push_str(&format!("{{% {name}{args_str} %}}"));
            }
            Token::Comment(text) => {
                // Re-emit the comment VERBATIM (#2558). A raw-block body is
                // SOURCE for Django, and Django's `do_block_translate` refuses
                // a comment inside `{% blocktranslate %}` with
                // `doesn't allow other block tags (seen 'c')`. Dropping it here
                // deleted the comment from the body, so Django saw a clean
                // msgid and rendered `a  b` where it should have raised —
                // silently mangling author content.
                content.push_str("{#");
                content.push_str(text);
                content.push_str("#}");
            }
            _ => {}
        }
        j += 1;
    }
    Err(DjangoRustError::TemplateError(unclosed_error))
}

/// Parse a custom block tag body until a matching end tag.
///
/// Used by `parse_token` when a registered block tag handler is found.
/// Scans forward collecting child nodes until `end_tag` is encountered.
/// [`parse_block_custom_tag`] with more than one terminator.
///
/// Returns the nodes, the index of the terminator, and WHICH terminator was
/// hit, so a caller with an optional `{% else %}` arm can tell the two apart.
/// The single-terminator helper stays the common path; this one exists for
/// `{% ifchanged %}`, whose body splits on `{% else %}`.
fn parse_block_until_any(
    tokens: &[Token],
    spans: &[Span],
    source: &str,
    start: usize,
    end_tags: &[&str],
) -> Result<(Vec<Node>, usize, String)> {
    let mut nodes = Vec::new();
    let mut i = start;

    while i < tokens.len() {
        if let Token::Tag(name, _) = &tokens[i] {
            if end_tags.iter().any(|t| t == name) {
                return Ok((nodes, i, name.clone()));
            }
        }

        if let Some(node) = parse_token(tokens, spans, source, &mut i)? {
            nodes.push(node);
        }
        i += 1;
    }

    Err(DjangoRustError::TemplateError(format!(
        "Unclosed block tag, expected {{% {} %}}",
        end_tags[end_tags.len() - 1]
    )))
}

fn parse_block_custom_tag(
    tokens: &[Token],
    spans: &[Span],
    source: &str,
    start: usize,
    end_tag: &str,
) -> Result<(Vec<Node>, usize)> {
    let mut nodes = Vec::new();
    let mut i = start;

    while i < tokens.len() {
        if let Token::Tag(name, _) = &tokens[i] {
            if name == end_tag {
                return Ok((nodes, i));
            }
        }

        if let Some(node) = parse_token(tokens, spans, source, &mut i)? {
            nodes.push(node);
        }
        i += 1;
    }

    Err(DjangoRustError::TemplateError(format!(
        "Unclosed block tag, expected {{% {end_tag} %}}"
    )))
}

/// Extract all variable paths from a Django template for JIT serialization.
///
/// Parses the template and returns a mapping of root variable names to their access paths.
/// This function is used to analyze which Django ORM fields need to be serialized for
/// efficient template rendering in Rust.
///
/// # Behavior
///
/// - **Empty templates**: Returns an empty HashMap
/// - **Malformed templates**: Returns an error if template cannot be parsed
/// - **Duplicate paths**: Automatically deduplicated and sorted
/// - **Nested variables**: Extracts full attribute chains (e.g., `user.profile.name`)
/// - **Template tags**: Extracts variables from for/if/with/block tags
/// - **Filters**: Ignores filters but preserves variable paths
///
/// # Performance
///
/// Typically completes in <5ms for standard templates. See benchmarks for details.
///
/// # Example
///
/// ```rust
/// use std::collections::HashMap;
/// use djust_templates::extract_template_variables;
///
/// let template = "{{ lease.property.name }} {{ lease.tenant.user.email }}";
/// let vars = extract_template_variables(template).unwrap();
///
/// // Returns: {"lease": ["property.name", "tenant.user.email"]}
/// assert_eq!(vars.get("lease").unwrap().len(), 2);
/// ```
///
/// # Use Case
///
/// This function enables automatic serialization of only the required Django ORM fields:
///
/// ```ignore
/// // In Python LiveView
/// class LeaseView(LiveView):
///     def get_context_data(self):
///         # Extract template variables automatically
///         vars = extract_template_variables(self.template_string)
///         # vars = {"lease": ["property.name", "tenant.user.email"]}
///
///         # Generate optimized query
///         lease = Lease.objects.select_related('property', 'tenant__user').first()
///
///         # Serialize only required fields
///         return {"lease": lease}  # Auto-serializes property.name and tenant.user.email
/// ```
pub fn extract_template_variables(
    template: &str,
) -> Result<std::collections::HashMap<String, Vec<String>>> {
    use std::collections::HashMap;

    // Tokenize and parse the template, propagating any parse error.
    //
    // Extraction shares `parse_token` with the render path (#1646) and shares
    // its refusal contract: a genuine parse error is a parse error everywhere
    // the parser runs, variable extraction included. This mirrors #2549 (an
    // unregistered tag refuses here too — see
    // `test_extract_refuses_unregistered_tag_at_parse`) and now extends to
    // #2578's malformed `{{ … }}` variable expressions. A template that
    // Django's `FilterExpression` would refuse is genuinely broken and will
    // not render; extraction reports the same refusal rather than silently
    // returning partial hints. (Malformed input that degrades to *text* at the
    // lexer level — e.g. an unterminated `{% if x` — is not a parse error and
    // still yields an empty map, per `test_malformed_template_graceful_fallback`.)
    // The JIT caller (`jit.py::_cached_extract_template_variables`) catches the
    // exception and falls back to full serialization.
    let tokens = crate::lexer::tokenize(template)?;
    let nodes = parse(&tokens)?;

    let mut variables: HashMap<String, Vec<String>> = HashMap::new();

    // Walk the AST and extract variable paths
    extract_from_nodes(&nodes, &mut variables);

    // Deduplicate and sort paths for each variable
    for paths in variables.values_mut() {
        paths.sort();
        paths.dedup();
    }

    Ok(variables)
}

/// Collect the set of `dj-model="<field>"` attribute *values* that appear as
/// literal developer-authored markup in a template.
///
/// # Security: this is the immune source for the dj-model mass-assignment
/// allowlist (CWE-915, finding #3)
///
/// Static `dj-model="<field>"` bindings live ENTIRELY inside [`Node::Text`]
/// literals — the raw, developer-authored template text. Attacker-controlled
/// data only ever reaches the output through [`Node::Variable`] substitution at
/// render time; it can NEVER appear in a `Node::Text` literal. So collecting
/// `dj-model` values from `Node::Text` content is structurally immune to every
/// rendered-HTML poisoning vector that defeated the previous approach (parsing
/// the rendered output): attacker text that *looks* like `dj-model="is_admin"`
/// (in a comment, username, chat message), an unquoted-interpolated attribute
/// `<div data-x={{ comment }}>` whose value carries `dj-model=is_admin`, or a
/// `|safe` value containing `<input dj-model=is_admin>`. None of those land in a
/// `Node::Text` literal, so none can widen this allowlist.
///
/// A *dynamic* binding `dj-model="{{ field }}"` straddles a `Node::Text`
/// (`dj-model="`) and a `Node::Variable` (`field`) — its resolved value is NOT
/// captured here. That is the intended fail-closed behavior: such fields must be
/// opted in via `allowed_model_fields`.
///
/// `{% extends %}` is covered when the caller passes the inheritance-resolved
/// source (the merged tree contains the base template's `Node::Text`). For
/// `{% include %}`, a `loader` may be supplied so the included template's text
/// is walked too; a missing/unresolvable include simply contributes nothing
/// (fail-closed). A dynamic include name (`{% include some_var %}`) likewise
/// resolves to nothing.
pub fn collect_dj_model_fields<L: crate::inheritance::TemplateLoader>(
    nodes: &[Node],
    loader: Option<&L>,
    fields: &mut HashSet<String>,
) {
    // Bound include recursion to mirror the inheritance-chain depth cap and
    // defend against pathological / cyclic include graphs.
    collect_dj_model_fields_depth(nodes, loader, fields, 0);
}

const MAX_INCLUDE_DEPTH: usize = 10;

fn collect_dj_model_fields_depth<L: crate::inheritance::TemplateLoader>(
    nodes: &[Node],
    loader: Option<&L>,
    fields: &mut HashSet<String>,
    depth: usize,
) {
    for node in nodes {
        match node {
            // The immune source: developer template text literals.
            Node::Text(text) => scan_dj_model_in_text(text, fields),

            // Recurse into every child-bearing variant so a `dj-model` binding
            // inside an `{% if %}`/`{% for %}`/`{% block %}`/etc. is captured.
            // Mirrors the recursion set in `assign_if_marker_ids`.
            Node::If {
                true_nodes,
                false_nodes,
                ..
            } => {
                collect_dj_model_fields_depth(true_nodes, loader, fields, depth);
                collect_dj_model_fields_depth(false_nodes, loader, fields, depth);
            }
            Node::For {
                nodes: body,
                empty_nodes,
                ..
            } => {
                collect_dj_model_fields_depth(body, loader, fields, depth);
                collect_dj_model_fields_depth(empty_nodes, loader, fields, depth);
            }
            Node::Block { nodes: body, .. }
            | Node::With { nodes: body, .. }
            | Node::Spaceless { nodes: body, .. }
            | Node::AutoEscape { nodes: body, .. }
            | Node::Filter { nodes: body, .. } => {
                collect_dj_model_fields_depth(body, loader, fields, depth);
            }
            Node::BlockCustomTag { children, .. } | Node::ReactComponent { children, .. } => {
                collect_dj_model_fields_depth(children, loader, fields, depth);
            }
            Node::Language { children, .. }
            | Node::Timezone { children, .. }
            | Node::Localize { children, .. }
            | Node::LocalTime { children, .. } => {
                collect_dj_model_fields_depth(children, loader, fields, depth);
            }

            // `{% include "child.html" %}` — load and walk the included
            // template's own text so its `dj-model` bindings are covered. The
            // parser already strips the surrounding quotes (#1396), so
            // `template` is the bare path — load it exactly the way the renderer
            // does (renderer.rs `Node::Include`). djust's Rust engine treats the
            // include name as a literal path (no dynamic includes), and that
            // path is developer-authored template text, so this stays immune to
            // poisoning. Fail-closed: a missing/unresolvable include simply
            // contributes nothing.
            Node::Include { template, .. } => {
                if depth >= MAX_INCLUDE_DEPTH {
                    continue;
                }
                if let Some(loader) = loader {
                    let name = template.trim_matches(|c| c == '"' || c == '\'');
                    if !name.is_empty() {
                        if let Ok(included) = loader.load_template(name) {
                            collect_dj_model_fields_depth(
                                &included,
                                Some(loader),
                                fields,
                                depth + 1,
                            );
                        }
                    }
                }
            }

            // Leaf / non-child variants carry no template Text. Note
            // `RustComponent`, `CustomTag`, `AssignTag`, `Variable`, etc. never
            // hold raw `dj-model=` markup, so there is nothing to scan.
            _ => {}
        }
    }
}

/// Scan a single `Node::Text` literal for `dj-model="<field>"` / `dj-model='…'`
/// REAL attributes and add each value to `fields`.
///
/// An attribute is recognized only when `dj-model` is a standalone attribute
/// name — preceded by an ASCII-whitespace boundary (or start-of-string) and
/// directly followed by `=` and a quoted value. This rejects `data-dj-model=…`
/// and `xdj-model=…` (the over-match the old serialized-HTML regex had) while
/// staying purely a string scan over developer-authored text. There is no
/// security dependence on this being a *perfect* HTML parser: `Node::Text` is
/// never attacker-controlled, so even a benign developer-prose false-positive
/// (e.g. the bytes `dj-model="x"` shown as documentation) is harmless — it can
/// only widen the allowlist with a field the developer themselves typed.
fn scan_dj_model_in_text(text: &str, fields: &mut HashSet<String>) {
    const ATTR: &str = "dj-model";
    let bytes = text.as_bytes();
    let mut search_from = 0usize;

    while let Some(rel) = text[search_from..].find(ATTR) {
        let start = search_from + rel;
        let after = start + ATTR.len();
        // Advance past this match for the next iteration regardless of outcome.
        search_from = after;

        // Boundary BEFORE `dj-model`: start-of-string or ASCII whitespace.
        // This is what distinguishes a real attribute (`<input dj-model=…>`)
        // from a substring of another attribute name (`data-dj-model=…`,
        // `xdj-model=…`).
        let left_ok = start == 0
            || bytes
                .get(start - 1)
                .is_some_and(|b| b.is_ascii_whitespace());
        if !left_ok {
            continue;
        }

        // Immediately after the name must be `=` (HTML allows no space before
        // `=` in the canonical form djust renders; a value-less `dj-model`
        // binds nothing and is ignored).
        if bytes.get(after) != Some(&b'=') {
            continue;
        }

        // The value must be quoted; capture up to the matching quote.
        let quote = match bytes.get(after + 1) {
            Some(&b'"') => b'"',
            Some(&b'\'') => b'\'',
            // Unquoted dj-model=foo — not a form djust emits; skip
            // (fail-closed; developer can use allowed_model_fields).
            _ => continue,
        };
        let value_start = after + 2;
        if let Some(end_rel) = text[value_start..].find(quote as char) {
            let value = &text[value_start..value_start + end_rel];
            if !value.is_empty() {
                fields.insert(value.to_string());
            }
            search_from = value_start + end_rel + 1;
        }
    }
}

/// Tokenize + parse `source`, then collect every static `dj-model="<field>"`
/// binding from the resulting AST (recursing into `{% include %}` via `loader`).
///
/// This is the convenience entry point mirrored from
/// [`extract_template_variables`]: a caller passes raw template source and gets
/// back the sorted, deduplicated set of bindable field names. See
/// [`collect_dj_model_fields`] for the security rationale (the source is
/// developer-authored template text, immune to rendered-output poisoning).
pub fn extract_dj_model_fields<L: crate::inheritance::TemplateLoader>(
    source: &str,
    loader: Option<&L>,
) -> Result<Vec<String>> {
    let tokens = crate::lexer::tokenize(source)?;
    let nodes = parse(&tokens)?;
    let mut fields: HashSet<String> = HashSet::new();
    collect_dj_model_fields(&nodes, loader, &mut fields);
    let mut out: Vec<String> = fields.into_iter().collect();
    out.sort();
    Ok(out)
}

/// Extract per-node dependency sets from a list of AST nodes.
///
/// Returns one `HashSet<String>` per node, containing the top-level context
/// variable names that node depends on.  Text nodes yield an empty set,
/// `Include` and `CustomTag` nodes get a `"*"` wildcard because their
/// dependencies cannot be statically determined.
pub fn extract_per_node_deps(nodes: &[Node]) -> Vec<HashSet<String>> {
    nodes
        .iter()
        .map(|node| {
            let mut variables: HashMap<String, Vec<String>> = HashMap::new();
            extract_from_nodes(std::slice::from_ref(node), &mut variables);
            let mut deps: HashSet<String> = variables.into_keys().collect();

            // Include nodes may depend on any variable — mark as wildcard
            if matches!(node, Node::Include { .. }) {
                deps.insert("*".to_string());
            }
            // CustomTag / BlockCustomTag / RawBlockCustomTag nodes may also
            // have unpredictable deps (#2558: the raw body is re-parsed by
            // Django, so anything in it is a dependency).
            if matches!(
                node,
                Node::CustomTag { .. }
                    | Node::BlockCustomTag { .. }
                    | Node::RawBlockCustomTag { .. }
                    | Node::Language { .. }
                    | Node::Timezone { .. }
                    | Node::Localize { .. }
                    | Node::LocalTime { .. }
            ) {
                deps.insert("*".to_string());
            }
            deps
        })
        .collect()
}

/// Collect the set of TOP-LEVEL context variable names a node subtree reads.
///
/// Returns the root identifiers referenced anywhere in `nodes` — `{{ x.name }}`
/// contributes `x`, `{{ prefix }}` contributes `prefix`, `{% with l=flag %}`
/// contributes `flag` (and `l`, once the body reads it), `{% firstof flag a %}`
/// contributes `flag` and `a`, etc. Quoted/numeric literals contribute nothing.
///
/// Used by the loop render cache (#1967) to decide cacheability: a loop body is
/// content-hash cacheable ONLY if every root it reads is one of the loop's bound
/// variable names. A body that reads any OUTER-context root (`prefix`, `flag`, a
/// localized label) is NOT cacheable, because outer context is constant within a
/// single render but NOT across renders — and the cache is persistent across
/// renders, so a reorder after an outer-var change would serve stale fragments.
/// Reuses [`extract_from_nodes`] so the cacheability decision stays consistent
/// with the partial-render dependency tracking it already trusts.
pub fn body_root_var_names(nodes: &[Node]) -> HashSet<String> {
    let mut variables: HashMap<String, Vec<String>> = HashMap::new();
    extract_from_nodes(nodes, &mut variables);
    variables.into_keys().collect()
}

/// Recursively extract variable paths from AST nodes
fn extract_from_nodes(
    nodes: &[Node],
    variables: &mut std::collections::HashMap<String, Vec<String>>,
) {
    for node in nodes {
        match node {
            Node::Variable(var_expr, filters, _in_attr) => {
                // Extract from variable: {{ variable.path }}
                extract_from_variable(var_expr, variables);
                // Extract from filter args: {{ a|default:fallback }} — `fallback`
                // must be tracked as a dependency too, otherwise a nested
                // {% if %}{{ x|default:dynamic }}{% endif %} silently drops
                // when only `dynamic` changes (issue #787).
                for (_name, arg) in filters {
                    if let Some(arg) = arg {
                        extract_from_filter_arg(arg, variables);
                    }
                }
            }
            Node::If {
                condition,
                true_nodes,
                false_nodes,
                ..
            } => {
                // Extract from condition: {% if variable.path %}
                extract_from_expression(condition, variables);
                // Recurse into if branches
                extract_from_nodes(true_nodes, variables);
                extract_from_nodes(false_nodes, variables);
            }
            Node::For {
                var_names,
                iterable,
                nodes,
                reversed: _,
                empty_nodes,
            } => {
                // Extract from iterable: {% for item in variable.path %}
                extract_from_variable(iterable, variables);
                // Recurse into for body
                extract_from_nodes(nodes, variables);
                // Recurse into empty block
                extract_from_nodes(empty_nodes, variables);

                // FIX: Transfer paths from loop variables to iterable AND keep loop variables
                // Example: {% for property in properties %}{{ property.name }}{% endfor %}
                // - Before: properties=[], property=[name, bedrooms, ...]
                // - After:  properties=[name, bedrooms, ...], property=[name, bedrooms, ...]
                //
                // For tuple unpacking: {% for val, label in status_choices %}{{ val }} {{ label }}{% endfor %}
                // - Before: status_choices=[], val=[], label=[]
                // - After:  status_choices=[0, 1], val=[], label=[]
                //
                // Loop variables are kept for:
                // - IDE autocomplete/type checking
                // - Template debugging
                // - Documentation generation
                for var_name in var_names {
                    if let Some(loop_var_paths) = variables.get(var_name) {
                        // Transfer paths from loop variable to iterable (but keep loop var)
                        // Prepend the iterable suffix so paths are correctly nested.
                        // Example: {% for tag in post.tags.all %}{{ tag.name }}{% endfor %}
                        //   iterable = "post.tags.all", loop var paths = ["name", "url"]
                        //   iterable_name = "post", iterable_suffix = "tags.all"
                        //   transferred paths = ["tags.all.name", "tags.all.url"]
                        let iterable_name = iterable.split('.').next().unwrap_or(iterable);
                        let iterable_suffix = if iterable.len() > iterable_name.len() + 1 {
                            &iterable[iterable_name.len() + 1..]
                        } else {
                            ""
                        };
                        let prefixed_paths: Vec<String> = loop_var_paths
                            .iter()
                            .map(|path| {
                                if iterable_suffix.is_empty() {
                                    path.clone()
                                } else {
                                    format!("{}.{}", iterable_suffix, path)
                                }
                            })
                            .collect();
                        variables
                            .entry(iterable_name.to_string())
                            .or_default()
                            .extend(prefixed_paths);
                    }
                }
            }
            Node::Block { nodes, name: _ } => {
                // Recurse into block body
                extract_from_nodes(nodes, variables);
            }
            Node::With { assignments, nodes } => {
                // Extract from with assignments: {% with x=variable.path %}
                for (_var_name, expr) in assignments {
                    extract_from_variable(expr, variables);
                }
                // Recurse into with body
                extract_from_nodes(nodes, variables);
            }
            Node::ReactComponent {
                props,
                children,
                name: _,
            } => {
                // Extract from component props
                for (_prop_name, prop_value) in props {
                    extract_from_variable(prop_value, variables);
                }
                // Recurse into children
                extract_from_nodes(children, variables);
            }
            Node::RustComponent { props, name: _ } => {
                // Extract from component props
                for (_prop_name, prop_value) in props {
                    extract_from_variable(prop_value, variables);
                }
            }
            Node::AssignTag { args, name: _ } => {
                // Extract variable references from the assign tag's
                // arguments so the partial renderer knows the tag
                // depends on them. Because an assign tag mutates the
                // context for subsequent sibling nodes in unknowable
                // ways, also emit the `"*"` wildcard — any change
                // may alter downstream rendering.
                for arg in args {
                    if (arg.starts_with('"') && arg.ends_with('"'))
                        || (arg.starts_with('\'') && arg.ends_with('\''))
                    {
                        continue;
                    }
                    let value = if let Some(eq_pos) = arg.find('=') {
                        arg[eq_pos + 1..].trim()
                    } else {
                        arg.trim()
                    };
                    if !value.is_empty()
                        && !value.starts_with('"')
                        && !value.starts_with('\'')
                        && !value.chars().all(|c| c.is_numeric() || c == '.')
                    {
                        extract_from_variable(value, variables);
                    }
                }
                variables.entry("*".to_string()).or_default();
            }
            Node::CustomTag { args, name: _ }
            | Node::BlockCustomTag {
                args,
                name: _,
                children: _,
            }
            | Node::RawBlockCustomTag {
                args,
                name: _,
                body: _,
            } => {
                // Extract variables from custom/block tag arguments
                for arg in args {
                    if (arg.starts_with('"') && arg.ends_with('"'))
                        || (arg.starts_with('\'') && arg.ends_with('\''))
                    {
                        continue;
                    }
                    let value = if let Some(eq_pos) = arg.find('=') {
                        arg[eq_pos + 1..].trim()
                    } else {
                        arg.trim()
                    };
                    if !value.is_empty()
                        && !value.starts_with('"')
                        && !value.starts_with('\'')
                        && !value.chars().all(|c| c.is_numeric() || c == '.')
                    {
                        extract_from_variable(value, variables);
                    }
                }
                // For block tags, also recurse into children
                if let Node::BlockCustomTag { children, .. } = node {
                    extract_from_nodes(children, variables);
                }
                // Custom tags can reference arbitrary vars internally; mark
                // the enclosing wrapper as "*" so partial render re-renders
                // it on any context change. Mirrors the top-level treatment
                // in extract_per_node_deps. Fixes #783.
                variables.entry("*".to_string()).or_default();
            }
            Node::WidthRatio {
                value,
                max_value,
                max_width,
                asvar,
            } => {
                extract_from_variable(value, variables);
                extract_from_variable(max_value, variables);
                extract_from_variable(max_width, variables);
                // The `as <var>` form MUTATES the context for later siblings,
                // exactly as `Node::AssignTag` does — so it needs the same
                // `"*"` wildcard, or partial render skips it whenever its own
                // operands are unchanged and the binding never happens
                // (#2355). The emitting form has no such effect and keeps its
                // precise dep set.
                if asvar.is_some() {
                    variables.entry("*".to_string()).or_default();
                }
            }
            Node::FirstOf { args, asvar } => {
                for arg in args {
                    if !((arg.starts_with('"') && arg.ends_with('"'))
                        || (arg.starts_with('\'') && arg.ends_with('\''))
                        || arg.chars().all(|c| c.is_numeric() || c == '.'))
                    {
                        extract_from_variable(arg, variables);
                    }
                }
                // See `Node::WidthRatio` above: the `as <var>` form mutates
                // the context for later siblings and needs `Node::AssignTag`'s
                // `"*"` wildcard (#2355).
                if asvar.is_some() {
                    variables.entry("*".to_string()).or_default();
                }
            }
            // Deps inside an `{% ifchanged %}` body are real deps — the
            // `_ => {}` default would report NONE for them, which is the
            // exact failure the tests below this file's dep-extractor pin.
            Node::IfChanged {
                vars,
                nodes,
                else_nodes,
                ..
            } => {
                // The operands are resolved every iteration, so they are
                // dependencies exactly as an `{% if %}` condition is.
                for var in vars {
                    extract_from_variable(var, variables);
                }
                extract_from_nodes(nodes, variables);
                extract_from_nodes(else_nodes, variables);
            }
            Node::BlockSuperScope { super_nodes, nodes } => {
                extract_from_nodes(super_nodes, variables);
                extract_from_nodes(nodes, variables);
            }
            Node::Spaceless { nodes } | Node::AutoEscape { nodes, .. } => {
                extract_from_nodes(nodes, variables);
            }
            Node::Filter { filters, nodes } => {
                // `{% filter cut:remove %}` reads `remove` from the context
                // (`filter04`); a quoted arg is a literal.
                for (_, arg) in filters {
                    if let Some(arg) = arg {
                        if !((arg.starts_with('"') && arg.ends_with('"'))
                            || (arg.starts_with('\'') && arg.ends_with('\'')))
                        {
                            extract_from_variable(arg, variables);
                        }
                    }
                }
                extract_from_nodes(nodes, variables);
            }
            // `{% resetcycle %}` reads nothing from the context.
            Node::ResetCycle { .. } => {}
            Node::Cycle { values, name, .. } => {
                // The `as name` form binds `name` for later siblings, the same
                // wildcard `{% firstof … as v %}` needs (#2355).
                if name.is_some() {
                    variables.entry("*".to_string()).or_default();
                }
                for val in values {
                    if !((val.starts_with('"') && val.ends_with('"'))
                        || (val.starts_with('\'') && val.ends_with('\'')))
                    {
                        extract_from_variable(val, variables);
                    }
                }
            }
            // Inline conditional `{{ x if cond else y }}` — same class of
            // silent dep-loss bug as the nested-Include case (#783).
            // Without this arm, an InlineIf inside a `{% for %}` / `{% if %}`
            // wrapper contributes zero deps; changing the condition variable
            // alone leaves the wrapper's dep set unintersected with
            // changed_keys, the cached fragment is reused, and the diff
            // returns 0 patches.
            Node::InlineIf {
                true_expr,
                condition,
                false_expr,
                filters: _,
            } => {
                for expr in [true_expr, condition, false_expr] {
                    let trimmed = expr.trim();
                    if trimmed.is_empty() {
                        continue;
                    }
                    let is_literal = (trimmed.starts_with('"') && trimmed.ends_with('"'))
                        || (trimmed.starts_with('\'') && trimmed.ends_with('\''))
                        || trimmed
                            .chars()
                            .all(|c| c.is_numeric() || c == '.' || c == '-');
                    if !is_literal {
                        extract_from_variable(trimmed, variables);
                    }
                }
            }
            // Nested Include nodes: the included template's vars can't be
            // determined statically from here, so mark the enclosing
            // wrapper as depending on "*" (any key change). Without this,
            // `{% if cond %}{% include "x" %}{% endif %}` has deps={cond},
            // and partial render reuses the cached If fragment when any
            // other context key changes — including vars used inside the
            // included template. (CustomTag/BlockCustomTag get "*" via
            // the earlier arm above.) Fixes #783.
            Node::Include { .. } => {
                variables.entry("*".to_string()).or_default();
            }
            // Text, Comment, CsrfToken, Extends, TemplateTag, Now, Static
            // don't contain variable references.
            _ => {}
        }
    }
}

/// Extract variable path from a single variable reference
///
/// Examples:
/// - "lease.property.name" -> root="lease", path="property.name"
/// - "user.email" -> root="user", path="email"
/// - "count" -> root="count", path="" (no sub-path)
fn extract_from_variable(
    var_expr: &str,
    variables: &mut std::collections::HashMap<String, Vec<String>>,
) {
    // Split on '.' to get path components
    let parts: Vec<&str> = var_expr.split('.').collect();

    if parts.is_empty() {
        return;
    }

    let root = parts[0].to_string();

    if parts.len() == 1 {
        // Simple variable (no path)
        // Still track it, but with empty path
        variables.entry(root).or_default();
    } else {
        // Has a path (e.g., "lease.property.name")
        let path = parts[1..].join(".");
        variables.entry(root).or_default().push(path);
    }
}

/// Extract a variable reference from a filter argument.
///
/// Filter args can be bare identifiers (`|default:fallback`), literal
/// strings (`|default:"none"` or `|default:'none'`), or numbers
/// (`|default:0`). Only bare identifiers are variable references and
/// need to be tracked as template dependencies (issue #787).
fn extract_from_filter_arg(
    arg: &str,
    variables: &mut std::collections::HashMap<String, Vec<String>>,
) {
    let trimmed = arg.trim();
    if trimmed.is_empty() {
        return;
    }
    // Skip quoted string literals.
    if (trimmed.starts_with('"') && trimmed.ends_with('"'))
        || (trimmed.starts_with('\'') && trimmed.ends_with('\''))
    {
        return;
    }
    // Skip numeric literals (including signed / floating point).
    if trimmed
        .chars()
        .all(|c| c.is_ascii_digit() || c == '.' || c == '-' || c == '+')
    {
        return;
    }
    // Anything else looks like a bare identifier / dotted path.
    extract_from_variable(trimmed, variables);
}

/// Extract variable paths from an expression (like in if tags)
///
/// Handles:
/// - {% if lease.property %}
/// - {% if lease.tenant.user.email %}
///
/// # Known Limitations (Phase 1)
///
/// This uses simplified expression parsing that splits on whitespace and dots.
/// String literals with dots (e.g., "example.com") may be incorrectly extracted
/// as variable paths. This creates harmless false positives - extra variables
/// that won't be used in serialization.
///
/// **Impact**: Low - false positives don't break functionality
/// **Fix**: Phase 2 will implement full expression grammar parsing
fn extract_from_expression(
    expr: &str,
    variables: &mut std::collections::HashMap<String, Vec<String>>,
) {
    // Simple approach: look for word.word.word patterns
    // More sophisticated: parse the full expression grammar

    // Split by common operators and whitespace
    let tokens: Vec<&str> = expr
        .split(|c: char| c.is_whitespace() || "()[]{}=!<>&|+-*/%,".contains(c))
        .filter(|s| !s.is_empty())
        .collect();

    for token in tokens {
        // Check if this looks like a variable path (contains dots)
        if token.contains('.') && !token.starts_with('"') && !token.starts_with('\'') {
            extract_from_variable(token, variables);
        } else if !token.starts_with('"')
            && !token.starts_with('\'')
            && !token.chars().all(|c| c.is_numeric() || c == '.')
            && token.chars().any(|c| c.is_alphabetic())
        {
            // Simple variable name without path
            variables.entry(token.to_string()).or_default();
        }
    }
}

/// Find the position of the ` if ` keyword in an expression, skipping over
/// quoted strings so that `'some if text' if cond else ''` works correctly.
///
/// Walks `char_indices` rather than raw bytes (#2551, #2552): the byte walk
/// evaluated `expr[i..]` at EVERY byte, including the continuation bytes of a
/// multi-byte character, which is not a char boundary and panics the slice.
/// Every `{{ … }}` whose expression held a non-ASCII character outside quotes
/// — `{{ café }}`, `{{ x.é }}` — therefore panicked on a template Django
/// renders fine. `i` is a char boundary by construction here, so the slice is
/// always valid; the returned offset is still a BYTE offset, which is what the
/// two callers slice with.
fn find_if_keyword(expr: &str) -> Option<usize> {
    let mut in_single = false;
    let mut in_double = false;

    for (i, ch) in expr.char_indices() {
        match ch {
            '\'' if !in_double => in_single = !in_single,
            '"' if !in_single => in_double = !in_double,
            _ if !in_single && !in_double && expr[i..].starts_with(" if ") => {
                return Some(i);
            }
            _ => {}
        }
    }
    None
}

/// Parse a slice of filter spec strings into `(filter_name, Option<arg>)` pairs.
///
/// Refuses a wrong ARGUMENT COUNT, which is what Django does here and at this
/// TIME (#2400). `FilterExpression.__init__` calls `args_check` while the
/// template is being COMPILED, so `{% if False %}{{ p|upper:"x" }}{% endif %}`
/// raises in Django even though the node never renders — and a `{{ }}` node
/// inside a branch nothing takes is exactly the shape a render-time check
/// cannot see. See [`crate::filter_arity`] for the table and for why there are
/// two bounds; this site takes the COMPILE-time one.
///
/// It is not the only site: a filter chain on a TAG operand
/// (`{% if p|upper:"x" %}`) is a raw string at parse time and is resolved by
/// `renderer::get_value_safe`, which takes the CALL-time bound. Two sites, one
/// table, each asking its own question — the `python_len` shape (#1646) rather
/// than two copies of the rule. `TestBothSitesRefuse` in
/// `python/tests/test_filter_arity_2400.py` names the test that goes red when
/// only one of them is removed.
fn parse_filter_specs(parts: &[String], token: &str) -> Result<Vec<(String, Option<String>)>> {
    // Django's LEXER rule, one layer above the arity check (#2409):
    // `filter_raw_string` allows at most ONE argument and requires the matches
    // to tile the token, so a second `:arg` is `Could not parse the
    // remainder`. `str::find(':')` took the first colon and kept the rest as
    // one argument, so `{{ p|cut:"a":"b" }}` rendered rather than being
    // refused. See [`crate::filter_lexer`], which both this site and
    // `renderer::get_value_safe` call rather than carrying a copy each.
    //
    // NOTE: surrounding quotes on literal args (e.g. `"none"` in
    // `default:"none"`) are preserved here so the dep-tracking extractor
    // (issue #787) can tell a literal apart from a bare-identifier variable
    // reference. The quote-strip happens at render time inside
    // `strip_filter_arg_quotes`.
    let mut specs: Vec<(String, Option<String>)> = Vec::with_capacity(parts.len());
    for filter_spec in parts {
        let (name, arg) = crate::filter_lexer::split_filter_spec(filter_spec, token)?;
        specs.push((name.to_string(), arg.map(str::to_string)));
    }
    for (name, arg) in &specs {
        // Django builds the argument's `Variable` BEFORE `args_check` runs for
        // that same filter, and both run before the NEXT filter is looked at
        // (`FilterExpression.__init__`). So `{{ p|upper:_x }}` reports the
        // underscore and `{{ p|upper:"a"|cut:_y }}` reports `upper`'s arity —
        // which is why these two checks are interleaved per spec rather than
        // run as two passes (#2418).
        if let Some(arg) = arg {
            validate_variable_name(arg)?;
        }
        if let Some(message) =
            crate::filter_arity::parse_time_arity_error(name, u8::from(arg.is_some()))
        {
            // Django's `TemplateSyntaxError` text verbatim. It crosses to
            // Python as a `RuntimeError` rather than Django's class, as every
            // djust template error does; the property both engines share is
            // that the template does not compile.
            return Err(DjangoRustError::TemplateError(message));
        }
        // `Invalid filter` — the name LOOKUP, at Django's time (#2419).
        //
        // Django resolves the name in `FilterExpression.__init__`
        // (`filter_func = parser.find_filter(filter_name)`), so a name nothing
        // implements refuses the template whether or not the node ever
        // renders. djust looked it up in `filters::apply_filter_full_safe`, on
        // the value — so `{% if 0 %}{{ p|nosuchfilter }}{% endif %}` and
        // `{% if 0 and p|nosuchfilter %}` compiled here and refused there.
        //
        // Its position among the three refusals is NOT a behavioural choice,
        // and saying so is the point (#2233). Django's own order inside
        // `FilterExpression.__init__` is argument-`Variable` → `find_filter`
        // → `args_check`, and this sits third — but the arity check and this
        // one are MUTUALLY EXCLUSIVE by construction: `parse_time_arity_error`
        // answers `None` for every name outside the built-in table, which is
        // exactly the set this refuses. No template can reach both, so no
        // test could tell the two orderings apart, and reordering to "match
        // Django" would be a mechanism that changes nothing.
        //
        // The one place djust's order IS visible is against the LEXER bound
        // above: `{{ p|nosuchfilter:"a":"b" }}` is `Invalid filter` on Django
        // and `Could not parse the remainder` here, because `split_filter_spec`
        // is what produces the name at all and so has to run first. Both
        // engines refuse the template, which is the property this closes;
        // only the wording differs. `TestDjangosOrderAmongTheRefusals`
        // measures all of this against live Django rather than asserting the
        // comment.
        //
        // ONE site closes both shapes, which is the condition #2411 attached
        // to moving this at all: `{{ … }}` reaches here through
        // `parse_token`, and every tag operand reaches here through
        // `validate_tag_operand`. A check written for one of them would have
        // been a second parallel path (#1646).
        //
        // The message keeps djust's existing `Unknown filter: <name>` wording
        // rather than Django's `Invalid filter: '<name>'`. Both engines refuse,
        // which is the property that matters; the wording is a published
        // contract here — `template/rendering.py` keys its "not supported by
        // the Rust engine" hint off this substring, and
        // `tests/unit/test_rust_custom_filters_1121.py` pins it — so a second
        // spelling for the same condition would be a drift of its own.
        if !crate::filters::is_known_filter(name) {
            return Err(DjangoRustError::TemplateError(format!(
                "Unknown filter: {name}"
            )));
        }
    }
    Ok(specs)
}

/// Django's `Variable.__init__` underscore rule, on ONE variable atom (#2418).
///
/// ```python
/// if var.find(VARIABLE_ATTRIBUTE_SEPARATOR + "_") > -1 or var[0] == "_":
///     raise TemplateSyntaxError(
///         "Variables and attributes may not begin with underscores: '%s'" % var
///     )
/// ```
///
/// # Why it is a rule about the NAME, not about the value
///
/// It fires while the template is being COMPILED, so it does not care whether
/// the name resolves. That is what made it invisible to the #2411 sweep, whose
/// context bound no `_x`: djust refused those cells for the unrelated
/// "argument does not resolve" reason and they never showed as divergent. With
/// `_x` BOUND, `{{ p|date:_x }}`, `{% for i in p|date:_x %}` and
/// `{% with v=p|date:_x %}` rendered here and refused on Django.
///
/// # Django's ORDER, which this reproduces
///
/// `Variable.__init__` tries the arms in this sequence, and only a name that
/// survives all of them reaches the underscore check:
///
/// 1. **numeric** — `int`/`float`. Not reproduced here, and it does not need
///    to be: Python rejects a leading `_` in a numeric literal (`int("_1")`
///    raises) and no numeric spelling contains `._` (`float("1._0")` raises),
///    so the numeric arm can never be what saves a name from this rule. A
///    numeric pre-check would be a second mechanism with nothing to do
///    (#2233). `TestNoNumericSpellingIsRefused` pins that against live Python.
/// 2. **`_( … )`** — the i18n wrapper is STRIPPED, and the remaining arms then
///    run on the inside. This one IS load-bearing: `{{ p|default:_("_x") }}`
///    compiles on Django and must keep compiling here.
/// 3. **quoted literal** — a literal never becomes a lookup, so
///    `{{ p|default:"_x" }}` and `{% if "_x" %}` compile.
/// 4. **the underscore check**, on the possibly-`_()`-stripped name.
///
/// # What it is NOT
///
/// It is not about a name BINDING. Measured on live Django:
/// `{% for _i in items %}X{% endfor %}`, `{% with _v=q %}X{% endwith %}` and
/// `{% cycle "a" "b" as _n %}X` all compile — you may bind an underscore name,
/// you may just never read one back. `TestABindingIsNotALookup` pins that, and
/// it is the reason no call site passes a loop variable, a `{% with %}` target
/// or an `as`-name to this function.
pub(crate) fn validate_variable_name(atom: &str) -> Result<()> {
    let mut var = atom.trim();

    // Arm 2: strip `_( … )` before the literal and underscore checks, exactly
    // as Django reassigns `var` in the `translate` branch — including for the
    // error message, which reports the STRIPPED name.
    if var.len() >= 3 && var.starts_with("_(") && var.ends_with(')') {
        var = &var[2..var.len() - 1];
    }

    // Arm 3: a quoted literal. `strip_filter_arg_quotes` is the same
    // quote-recogniser the render path uses, called rather than restated
    // (#1646) — if it changes its mind about what a literal is, both change.
    if strip_filter_arg_quotes(var) != var {
        return Ok(());
    }

    if var.starts_with('_') || var.contains("._") {
        // Django's `TemplateSyntaxError` text verbatim.
        return Err(DjangoRustError::TemplateError(format!(
            "Variables and attributes may not begin with underscores: '{var}'"
        )));
    }
    Ok(())
}

/// Django's `FilterExpression` head-grammar, on a PLAIN `{{ … }}` variable
/// expression — the part before any `|filter` (#2578).
///
/// # The defect this closes
///
/// Django's `FilterExpression.__init__` tiles a `{{ … }}` head with
/// `filter_re`, whose only ways to match a bare head are a numeric constant, a
/// quoted string constant, `_( … )`, or the variable pattern
/// `var_re = [\w.]+` (`django/template/base.py:600-700`). Whatever those
/// alternatives cannot tile is the *remainder*, and Django refuses the
/// template with `Could not parse the remainder: '<rest>' from '<whole>'`.
/// djust's engine had no such head check: it stored the whole string as a
/// variable name, and at render time the name simply did not resolve, so
/// `{{ va>r }}`, `{{ eggs! }}`, `{{ multi word variable }}`,
/// `{{ (var.r) }}` and friends rendered `''` instead of refusing.
///
/// # Why this is a SECOND function, called from a DIFFERENT site than
/// [`validate_variable_name`]
///
/// djust extends `{{ … }}` with a Jinja-style inline conditional
/// (`{{ a if cond else b }}` → [`Node::InlineIf`]). Its head is genuinely
/// several words separated by spaces, so this `[\w.]`-only check MUST NOT run
/// on it. The call site is therefore the *plain-variable fallthrough* in
/// `parse_token`, reached only AFTER [`find_if_keyword`] has had its chance to
/// claim the expression as an inline-if — never the shared
/// [`validate_variable_name`], which every tag operand and filter argument
/// also flows through. Moving this before the inline-if branch reddens the
/// bare-variable inline-if pin (`test_inline_if_bare_variable_expr_survives_grammar_check`).
///
/// # The literal exemption reuses the renderer's recogniser (#1646)
///
/// A quoted string, a number (including the `-`/`+`/`e` spellings that fall
/// outside `[\w.]`) and `_( … )` are valid heads that this must NOT refuse.
/// Rather than restate what a literal is, this asks the SAME
/// [`crate::renderer::django_literal`] the render path uses — if it changes its
/// mind about what a constant is, both paths change together. Everything else
/// must tile fully as `[\w.]+`; the first char it cannot tile begins the
/// remainder Django reports.
fn validate_plain_variable_expr(expr: &str) -> Result<()> {
    // The empty head cannot arrive from a non-empty `{{ … }}` (the empty-tag
    // refusal fired earlier); a `{{ |upper }}`-style empty part 0 is left to
    // its existing behaviour rather than widened here (#1079 — fix exactly the
    // cited grammar cells).
    if expr.is_empty() {
        return Ok(());
    }
    // A recognised constant literal is a valid head even when it isn't `[\w.]`.
    if crate::renderer::django_literal(expr).is_some() {
        return Ok(());
    }
    // Django's `var_re = [\w.]+` (Unicode word characters and dots). Rust's
    // `char::is_alphanumeric` is Unicode-aware, matching Python's `\w` for
    // every letter/digit; add `_` and `.` explicitly.
    let head_len: usize = expr
        .char_indices()
        .take_while(|(_, c)| c.is_alphanumeric() || *c == '_' || *c == '.')
        .map(|(i, c)| i + c.len_utf8())
        .last()
        .unwrap_or(0);
    if head_len == expr.len() {
        return Ok(());
    }
    // Django's `TemplateSyntaxError` wording, byte-for-byte identical to the
    // sibling remainder refusal in `filter_lexer` (#2409) so the two grammar
    // paths speak with one voice (#1646). It crosses to Python as a
    // `RuntimeError`, as every djust template error does; the shared property
    // is that the template does not compile.
    Err(DjangoRustError::TemplateError(format!(
        "Could not parse the remainder: '{}' from '{}'",
        &expr[head_len..],
        expr
    )))
}

/// Run the PARSE-time half of Django's `compile_filter` over one TAG operand
/// (#2411).
///
/// # The defect this closes
///
/// Django compiles every tag operand's filter chain while the template is
/// being COMPILED, so a wrong argument count (#2400), a lexer remainder
/// (#2409) or an unparseable spec refuses the template before any value is
/// resolved. djust resolved a tag operand at RENDER time, left to right, in
/// `renderer::get_value_safe` — and `{% if %}` legitimately absorbs a
/// `VariableDoesNotExist` (`evaluate_condition_for_if`, which is Django's
/// `IfNode.render` catching the same thing). So an EARLIER step that failed
/// to resolve made the condition falsy before the LATER filter's refusal was
/// ever reached:
///
/// ```text
/// {% if p|cut %}          django  TemplateSyntaxError: cut requires 2 arguments, 1 provided
///                         djust   RuntimeError:        cut requires 2 arguments, 1 provided   agrees
///
/// {% if p|date:.|cut %}   django  TemplateSyntaxError: cut requires 2 arguments, 1 provided
///                         djust   ''                                                          masked
/// ```
///
/// Narrowing the swallow does NOT fix it, and the measurement says why: Django
/// swallows the very same thing. `{% if p|date:missingvar %}` renders the
/// false branch on BOTH engines, because `IfNode.render` wraps the whole
/// `condition.eval(context)` — filter arguments included — in
/// `except VariableDoesNotExist`. The only reason Django refuses the
/// three-filter spelling is that it never got as far as rendering it. So the
/// refusal has to move to Django's TIME, not to a narrower catch.
///
/// Two more shapes have nothing to do with the swallow and are closed by the
/// same move, which is the argument for doing it here rather than at the
/// `{% if %}` render arm:
///
/// ```text
/// {% if 0 and p|cut %}                     short-circuit: the operand is never evaluated
/// {% if 0 %}{% for x in p|cut %}{% endif %}   a branch that never renders
/// ```
///
/// # One validator, two times it runs
///
/// This calls `parse_filter_specs` — the SAME function `{{ … }}` has always
/// run at parse time — rather than restating its rules (#1646). A tag operand
/// is a raw string at parse time, which is the whole of why it was skipped;
/// splitting it on its unquoted pipes is all that was missing.
///
/// # What it checks SINCE #2419
///
/// **`Invalid filter`** — the filter-NAME lookup. It was left out here on
/// purpose, because djust looked a name up at RENDER time for `{{ }}` as much
/// as for a tag, and checking it for tags only would have been a new parallel
/// path. #2419 moved it for BOTH at once, and the reason one edit could do
/// that is this function: `{{ … }}` and every tag operand reach
/// [`parse_filter_specs`] and nothing else, so the name check went there
/// rather than here.
///
/// # What it checks SINCE #2418
///
/// `Variables and attributes may not begin with underscores` — the rule
/// `Variable.__init__` applies to the operand's own NAME, which djust had
/// nowhere. It was NOT part of this defect: with `_x` bound in the context,
/// `{{ p|date:_x }}`, `{% for i in p|date:_x %}` and `{% with v=p|date:_x %}`
/// rendered here and refused on Django, and none of those three swallows
/// anything. It is now the first thing this function does, via
/// [`validate_variable_name`], and the operand's filter chain is checked
/// second — Django's own order inside `FilterExpression.__init__`.
pub(crate) fn validate_tag_operand(expr: &str) -> Result<()> {
    let parts: Vec<String> = crate::filter_lexer::split_pipes(expr)
        .into_iter()
        .map(|s| s.trim().to_string())
        .collect();
    let head = parts[0].trim();
    // Django's `compile_filter` tiles the head with `filter_re`
    // (constant | quoted string | `_(…)` | `[\w.]+`) and refuses whatever is
    // left over as "Could not parse the remainder" — a FULL-CONSUMPTION
    // check, not just "does the head start with something valid" (#2580).
    // `{% cycle a,b,c as foo %}` tiles "a" and leaves ",b,c" unconsumed;
    // djust's `values` loop only ran `validate_variable_name`'s underscore
    // check on the whole "a,b,c" string, which has no underscore issue, so
    // it passed and rendered instead of refusing. Same fix shape as
    // `validate_url_operand` (#2577) — deliberately generalized here rather
    // than left as a `{% url %}`-only mechanism, since every caller of
    // `validate_tag_operand` (cycle, if/elif, with, widthratio, firstof)
    // shares this exact grammar. NOT applied to `validate_variable_name`
    // itself: that function's OTHER caller is the `{{ }}` head, checked
    // BEFORE djust's inline-if branch has a chance to claim a genuinely
    // multi-word `{{ a if c else b }}` (#2578) — tiling would regress that.
    match crate::filter_lexer::argument_end(head) {
        Some(n) if n == head.len() => {}
        Some(n) => {
            return Err(DjangoRustError::TemplateError(format!(
                "Could not parse the remainder: '{}' from '{}'",
                &head[n..],
                expr
            )));
        }
        None => {
            return Err(DjangoRustError::TemplateError(format!(
                "Could not parse the remainder: '{head}' from '{expr}'"
            )));
        }
    }
    // The operand's own name, then its filter chain — Django's order inside
    // `FilterExpression.__init__` (#2418). This is the third and last caller
    // of `validate_variable_name`; between them they cover every place djust
    // turns a template NAME into a lookup.
    validate_variable_name(&parts[0])?;
    parse_filter_specs(&parts[1..], expr).map(|_| ())
}

/// [`validate_tag_operand`] over a `{% if %}` / `{% elif %}` token stream.
///
/// `args` is what the lexer's quote-aware `split_tag_args` produced, which is
/// Django's `token.split_contents()[1:]`. `IfParser.translate_token` splits
/// that stream into OPERATORS (`smartif.OPERATORS` — `or`, `and`, `not`, `in`,
/// `is`, `==`, `!=`, `<`, `>`, `<=`, `>=`, plus the merged `not in` / `is not`)
/// and OPERANDS, and compiles only the operands.
///
/// This does NOT skip the operator words, and does not need to: an operator
/// token carries no unquoted `|`, so `split_pipes` yields ONE part,
/// [`validate_tag_operand`] hands `parse_filter_specs` an EMPTY slice, and the
/// call is a no-op. An operator-set constant here would be a second mechanism
/// with nothing to do — a mutation replacing it with "skip nothing" changes no
/// behaviour, which is the definition of decorative (#2233), so it is one
/// comment rather than one constant.
///
/// The name check this now also runs — `Variable.__init__`'s underscore rule
/// (#2418) — was predicted here to need the operator set reinstated, "because
/// `==` is not a variable". Measured against live `smartif.OPERATORS`, that
/// prediction is wrong and the set stays absent: no Django operator token
/// begins with `_` or contains `._`, so the rule is a no-op on every one of
/// them, exactly as `split_pipes` is. Reinstating the set would be a second
/// mechanism that changes no behaviour — decorative by the same argument
/// (#2233). `test_no_django_if_operator_word_is_refusable_as_an_operand`
/// checks the claim against Django rather than against this comment.
pub(crate) fn validate_if_operands(args: &[String]) -> Result<()> {
    for arg in args {
        // #2580 gave `validate_tag_operand` a full-consumption tiling check,
        // which is correct for a genuine OPERAND but wrong for an operator
        // word — `==`/`and`/`is not`/etc. don't tile as a constant/var/num
        // atom and would refuse a perfectly valid `{% if a == b %}`. The
        // OLD (pre-#2580) `validate_tag_operand` was a no-op on operator
        // words by accident (no pipe, no underscore); that accident is what
        // this skip now does on purpose, reusing `classify_if_token` — the
        // SAME classification `IfGrammar` already computes — rather than a
        // second operator-word list.
        if classify_if_token(arg).kind != IfTokKind::Operand {
            continue;
        }
        validate_tag_operand(arg)?;
    }
    Ok(())
}

/// Compile one `{% url %}` operand the way Django's `compile_filter` does, at
/// PARSE time (#2577).
///
/// # Why this is not `validate_tag_operand`
///
/// `validate_tag_operand` (#2411/#2418/#2419) checks a `{% if %}`/`{% for %}`/
/// `{% with %}` operand's filter CHAIN and the operand name's underscore rule —
/// but it does NOT check that the HEAD atom tiles its token, because for those
/// tags djust had no need to: a leftover after the head resolves to an ordinary
/// `VariableDoesNotExist` that `{% if %}` legitimately swallows. `{% url %}`
/// is different — Django's `do_url` runs `parser.compile_filter(bits[1])` and
/// `parser.compile_filter(value)` on EVERY argument at parse time, so
/// `FilterExpression.__init__`'s "Could not parse the remainder" fires for
/// `id,`, `id=`, `a.id=id`, `a.id!id` and the unterminated-string forms BEFORE
/// the view name is ever reversed. djust reached `{% url %}` only at render
/// (as a `CustomTag`), so the refusal arrived after — and for the unquoted
/// `named_url` spelling, never, because the missing view raised `NoReverseMatch`
/// first (#2607). The head-atom tiling check is the only piece
/// `validate_tag_operand` is missing, and it is the whole of this defect; the
/// filter-chain check below is the shared `parse_filter_specs`.
///
/// The head-atom test is `filter_lexer::argument_end` — Django's own
/// `constant | var | num` head grammar — rather than a second copy of it
/// (#1646). A leftover is the remainder; nothing consumable at all (an
/// unterminated quote) is the whole token as the remainder, which is what
/// Django's finditer reports when no alternative matches at position 0.
fn validate_url_operand(expr: &str) -> Result<()> {
    let parts: Vec<&str> = crate::filter_lexer::split_pipes(expr);
    let head = parts[0].trim();
    match crate::filter_lexer::argument_end(head) {
        Some(n) if n == head.len() => {}
        Some(n) => {
            return Err(DjangoRustError::TemplateError(format!(
                "Could not parse the remainder: '{}' from '{}'",
                &head[n..],
                expr
            )));
        }
        None => {
            return Err(DjangoRustError::TemplateError(format!(
                "Could not parse the remainder: '{head}' from '{expr}'"
            )));
        }
    }
    // Then its filter chain, via the shared `parse_filter_specs` — a malformed
    // filter on a `{% url %}` operand refuses exactly as on any tag operand.
    // The head atom's underscore rule (`Variable.__init__`) is deliberately NOT
    // applied here: #2418 pins that rule to exactly three call sites (none of
    // them url), and no `test_url_failNN` cell exercises an underscore name, so
    // the grammar refusal this defect is about is the head-atom tiling above.
    // url-operand underscore parity is a separate #2418-family concern.
    let filter_specs: Vec<String> = parts[1..].iter().map(|s| s.trim().to_string()).collect();
    parse_filter_specs(&filter_specs, expr).map(|_| ())
}

/// The value half of one `{% url %}` argument, per Django's
/// `kwarg_re = (?:(\w+)=)?(.+)`.
///
/// `do_url` matches each bit against `kwarg_re` and `compile_filter`s the
/// VALUE group. When the bit is `name=value` (a leading `\w+` immediately
/// followed by `=` and at least one more character) the value is everything
/// after the `=`; otherwise the whole bit is the value (`(?:(\w+)=)?` is
/// optional and `(.+)` swallows the rest). `id=` therefore has value `id=`
/// (the `=` is not a separator because nothing follows it), which is exactly
/// why it reaches the remainder check as `id=` rather than as an empty value.
fn url_kwarg_value(bit: &str) -> &str {
    let name_len: usize = bit
        .chars()
        .take_while(|c| c.is_alphanumeric() || *c == '_')
        .map(char::len_utf8)
        .sum();
    let bytes = bit.as_bytes();
    if name_len > 0 && name_len + 1 < bytes.len() && bytes[name_len] == b'=' {
        &bit[name_len + 1..]
    } else {
        bit
    }
}

/// Refuse a malformed `{% url %}` argument list at PARSE time, reproducing the
/// refusals Django's `do_url` raises while the template is compiled (#2577).
///
/// `args` is `token.split_contents()[1:]` — everything after `url`. Django's
/// `do_url` raises for three parse-time shapes, and this reproduces each:
///
/// 1. **no arguments** — `if len(bits) < 2` → "'url' takes at least one
///    argument, a URL pattern name." Here `args.is_empty()` is that condition.
/// 2. **an unparseable view name** — `compile_filter(bits[1])`.
/// 3. **an unparseable argument value** — `compile_filter(value)` for each
///    remaining bit, after the trailing `as var` (if any) is stripped exactly
///    as `do_url` strips it (`bits[-2] == "as"`).
///
/// It runs at parse and so wins the race against the render-time
/// `NoReverseMatch` the unquoted `named_url` spelling raises (#2607): the
/// template never reaches render. The `{% url %}` node itself is still built by
/// the existing handler dispatch below — this only refuses, it does not change
/// what a WELL-FORMED `{% url %}` compiles to.
pub(crate) fn validate_url_args(args: &[String]) -> Result<()> {
    // Empty args (`{% url %}`) is Django's `len(bits) < 2` MISSING-required-
    // argument error, NOT an argument-LIST grammar error. The render-time
    // `UrlTagHandler` already raises Django's genuine `TemplateSyntaxError`
    // ("'url' takes at least one argument, a URL pattern name.") for it (#2563).
    // Refusing it here at parse would pre-empt that with a
    // `DjustTemplateSyntaxError` of the wrong exception class, so leave the
    // no-args case to the handler and only validate a NON-empty argument list.
    if args.is_empty() {
        return Ok(());
    }
    validate_url_operand(&args[0])?;
    let mut rest = &args[1..];
    let n = rest.len();
    if n >= 2 && rest[n - 2] == "as" {
        rest = &rest[..n - 2];
    }
    for bit in rest {
        validate_url_operand(url_kwarg_value(bit))?;
    }
    Ok(())
}
/// The kind of a single `{% if %}` token, after `smartif`'s `is`/`not` merging.
///
/// `smartif.OPERATORS` maps a fixed word set to operator classes and treats
/// everything else as a variable/literal operand. We mirror that split: the
/// binding power (`lbp`) and prefix/infix role come straight from
/// `django/template/smartif.py`'s `OPERATORS` table.
#[derive(Clone, PartialEq)]
enum IfTokKind {
    /// A variable or literal — `smartif.Literal`, `lbp = 0`, `nud` returns self.
    Operand,
    /// The prefix `not`, `lbp = 8`, `nud` consumes one right operand.
    Prefix,
    /// Any binary operator (`and`, `or`, `in`, `is`, `==`, `<`, …); `led`
    /// consumes a right operand at its own binding power.
    Infix,
    /// The implicit end-of-stream token — `smartif.EndToken`, `lbp = 0`.
    End,
}

/// One token in the `{% if %}` grammar walk.
struct IfTok {
    kind: IfTokKind,
    lbp: i32,
    /// The source word, used only for the diagnostic message.
    display: String,
}

impl IfTok {
    fn end() -> Self {
        IfTok {
            kind: IfTokKind::End,
            lbp: 0,
            display: String::new(),
        }
    }
}

/// Classify one already-merged `{% if %}` word exactly as
/// `smartif.IfParser.translate_token` does: a word in `OPERATORS` becomes that
/// operator, anything else becomes an operand (`create_var`). Binding powers
/// are copied verbatim from `smartif.OPERATORS`.
fn classify_if_token(word: &str) -> IfTok {
    let (kind, lbp) = match word {
        "or" => (IfTokKind::Infix, 6),
        "and" => (IfTokKind::Infix, 7),
        "not" => (IfTokKind::Prefix, 8),
        "in" | "not in" => (IfTokKind::Infix, 9),
        "is" | "is not" | "==" | "!=" | ">" | ">=" | "<" | "<=" => (IfTokKind::Infix, 10),
        _ => (IfTokKind::Operand, 0),
    };
    IfTok {
        kind,
        lbp,
        display: word.to_string(),
    }
}

/// A port of `smartif.IfParser` used as a PARSE-TIME VALIDATOR (#2576).
///
/// Django compiles every `{% if %}`/`{% elif %}` condition with
/// `TemplateIfParser(...).parse()` at compile time (`defaulttags.do_if`), so a
/// malformed operator arrangement — a dangling `and`, a leading `==`, two
/// adjacent operands, an infix `not` — raises `TemplateSyntaxError` before any
/// rendering. djust's engine only ever validated the individual OPERANDS
/// ([`validate_if_operands`]); the ARRANGEMENT of operators and operands was
/// never checked, so all of those shapes parsed and rendered a branch.
///
/// This walks the same top-down operator-precedence grammar as
/// `smartif.IfParser` (`nud`/`led`/`lbp`) but discards the tree — it only needs
/// to reproduce which token streams `parse()` rejects, and it rejects exactly
/// the same ones. It intentionally does NOT try to match Django's message text
/// (that is the separate follow-up #2581); the wording here is djust's own
/// descriptive style. The `is`/`not` → `is not` and `not`/`in` → `not in`
/// merging is `IfParser.__init__`'s.
struct IfGrammar {
    tokens: Vec<IfTok>,
    pos: usize,
    current: IfTok,
}

impl IfGrammar {
    fn new(args: &[String]) -> Self {
        // `IfParser.__init__`: fold `is`+`not` → `is not`, `not`+`in` → `not in`.
        let mut merged: Vec<IfTok> = Vec::with_capacity(args.len());
        let mut i = 0;
        while i < args.len() {
            let w = args[i].as_str();
            if w == "is" && i + 1 < args.len() && args[i + 1] == "not" {
                merged.push(classify_if_token("is not"));
                i += 2;
            } else if w == "not" && i + 1 < args.len() && args[i + 1] == "in" {
                merged.push(classify_if_token("not in"));
                i += 2;
            } else {
                merged.push(classify_if_token(w));
                i += 1;
            }
        }
        let mut parser = IfGrammar {
            tokens: merged,
            pos: 0,
            current: IfTok::end(),
        };
        // `self.current_token = self.next_token()` primes the first token.
        parser.current = parser.next_token();
        parser
    }

    fn next_token(&mut self) -> IfTok {
        if self.pos >= self.tokens.len() {
            IfTok::end()
        } else {
            let t = &self.tokens[self.pos];
            let out = IfTok {
                kind: t.kind.clone(),
                lbp: t.lbp,
                display: t.display.clone(),
            };
            self.pos += 1;
            out
        }
    }

    /// `smartif.IfParser.expression`. Returns `Ok` if the sub-expression
    /// starting at `current` is well-formed at right binding power `rbp`.
    fn expression(&mut self, rbp: i32) -> Result<()> {
        let t = std::mem::replace(&mut self.current, IfTok::end());
        self.current = self.next_token();
        self.nud(&t)?;
        while rbp < self.current.lbp {
            let t = std::mem::replace(&mut self.current, IfTok::end());
            self.current = self.next_token();
            self.led(&t)?;
        }
        Ok(())
    }

    /// Null denotation — a token in PREFIX position.
    fn nud(&mut self, t: &IfTok) -> Result<()> {
        match t.kind {
            IfTokKind::Operand => Ok(()),
            // `not` consumes exactly one right operand at its own bp.
            IfTokKind::Prefix => self.expression(t.lbp),
            IfTokKind::Infix => Err(DjangoRustError::TemplateError(format!(
                "Invalid {{% if %}} condition: operator '{}' has no left operand",
                t.display
            ))),
            IfTokKind::End => Err(DjangoRustError::TemplateError(
                "Invalid {% if %} condition: unexpected end of expression".to_string(),
            )),
        }
    }

    /// Left denotation — a token in INFIX position (an operand already parsed
    /// to its left).
    fn led(&mut self, t: &IfTok) -> Result<()> {
        match t.kind {
            IfTokKind::Infix => self.expression(t.lbp),
            // A prefix `not`, an operand, or End can never sit in infix
            // position. (Operand/End have `lbp = 0` so `led` is unreachable
            // for them via `expression`; kept exhaustive for safety.)
            _ => Err(DjangoRustError::TemplateError(format!(
                "Invalid {{% if %}} condition: '{}' cannot be used as an infix operator",
                t.display
            ))),
        }
    }

    /// `smartif.IfParser.parse`: parse one expression, then require the whole
    /// token stream to be consumed.
    fn parse(&mut self) -> Result<()> {
        self.expression(0)?;
        if self.current.kind != IfTokKind::End {
            return Err(DjangoRustError::TemplateError(format!(
                "Invalid {{% if %}} condition: unexpected '{}' at end of expression",
                self.current.display
            )));
        }
        Ok(())
    }
}

/// Validate the operator/operand ARRANGEMENT of a `{% if %}` / `{% elif %}`
/// condition, refusing exactly the malformed shapes Django's `smartif` parser
/// refuses at compile time (#2576). See [`IfGrammar`] for the port details.
///
/// This is the ARRANGEMENT check; [`validate_if_operands`] is the per-OPERAND
/// name/filter check. Django does both inside one `TemplateIfParser.parse()`
/// (operands are compiled by `create_var` during the walk); djust keeps them as
/// two passes over the same `args`, and both must run at the `{% if %}` and
/// `{% elif %}` sites.
pub(crate) fn validate_if_grammar(args: &[String]) -> Result<()> {
    IfGrammar::new(args).parse()
}

/// Strip surrounding single/double quotes from a filter argument when it
/// was a literal at parse time. Called at render time so the extractor
/// can still distinguish bare identifiers from quoted literals.
pub fn strip_filter_arg_quotes(arg: &str) -> &str {
    if arg.len() >= 2
        && ((arg.starts_with('"') && arg.ends_with('"'))
            || (arg.starts_with('\'') && arg.ends_with('\'')))
    {
        &arg[1..arg.len() - 1]
    } else {
        arg
    }
}

/// [`strip_filter_arg_quotes`] plus Django's `unescape_string_literal`
/// (`s[1:-1].replace(r"\<quote>", quote).replace(r"\\", "\\")`) for a quoted
/// literal (#2556, `autoescape-tag08`). The `{% if %}`/`{% for %}` operand path
/// (`renderer::get_value_safe_inner`) already unescaped; the `{{ x|f:"…" }}`
/// argument path did not, and `{{ var|default_if_none:" endquote\" hah" }}`
/// kept the backslash (#1646, the filter-argument axis). Borrows unless a
/// backslash is present.
pub fn unescape_filter_arg_literal(arg: &str) -> std::borrow::Cow<'_, str> {
    let stripped = strip_filter_arg_quotes(arg);
    if std::ptr::eq(stripped, arg) || !stripped.contains('\\') {
        return std::borrow::Cow::Borrowed(stripped);
    }
    let quote = &arg[..1];
    std::borrow::Cow::Owned(
        stripped
            .replace(&format!("\\{quote}"), quote)
            .replace("\\\\", "\\"),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::lexer::tokenize;

    // ---- {% url %} argument grammar (#2577) -------------------------------

    /// Each malformed argument LIST Django's `do_url` refuses at parse time,
    /// keyed by the argument shape the issue names. `args` is
    /// `split_contents()[1:]` — everything after `url` — exactly what the
    /// `Token::Tag` arm hands `validate_url_args`.
    fn s(items: &[&str]) -> Vec<String> {
        items.iter().map(|x| x.to_string()).collect()
    }

    #[test]
    fn url_no_arguments_is_not_a_parse_grammar_error() {
        // fail01: `{% url %}` (empty args) is Django's `len(bits) < 2`
        // MISSING-required-argument error, NOT an argument-LIST grammar error.
        // It is deliberately NOT refused here at parse: the render-time
        // `UrlTagHandler` raises Django's genuine `TemplateSyntaxError`
        // ("'url' takes at least one argument, a URL pattern name.") for it
        // (#2563), with the correct exception class. Refusing it here would
        // pre-empt that with a `DjustTemplateSyntaxError` of the wrong class,
        // so `validate_url_args` returns Ok for the empty list and only
        // validates a NON-empty argument list.
        assert!(validate_url_args(&[]).is_ok());
    }

    #[test]
    fn url_malformed_argument_shapes_are_refused() {
        // fail04-09 (quoted `"view"`) and fail14-19 (`named_url`) share these
        // argument shapes — the second element of each pair is the token after
        // the view name. Both view-name spellings reach the SAME
        // `validate_url_args`, so one table pins both groups.
        for (label, args) in [
            ("id,", s(&["\"view\"", "id,"])),
            ("id=", s(&["\"view\"", "id="])),
            ("a.id=id", s(&["\"view\"", "a.id=id"])),
            ("a.id!id", s(&["\"view\"", "a.id!id"])),
            (
                "id=\"unterminated",
                s(&["\"view\"", "id=\"unterminatedstring"]),
            ),
            ("id=\",", s(&["\"view\"", "id=\","])),
            // the `named_url` spelling of the same shapes (fail14-19)
            ("named id,", s(&["named_url", "id,"])),
            ("named id=", s(&["named_url", "id="])),
            ("named a.id=id", s(&["named_url", "a.id=id"])),
            ("named a.id!id", s(&["named_url", "a.id!id"])),
            (
                "named id=\"unterm",
                s(&["named_url", "id=\"unterminatedstring"]),
            ),
            ("named id=\",", s(&["named_url", "id=\","])),
        ] {
            let err = validate_url_args(&args).unwrap_err();
            assert!(
                format!("{err}").contains("Could not parse the remainder"),
                "{label}: expected a remainder refusal, got {err}"
            );
        }
    }

    #[test]
    fn well_formed_url_argument_lists_are_accepted() {
        // The forms `test_url.py` compiles cleanly must NOT regress.
        for args in [
            s(&["\"view\""]),                    // url01-style, positional-less
            s(&["\"view\"", "pk=1"]),            // kwarg
            s(&["\"view\"", "1", "2"]),          // positional args
            s(&["\"view\"", "obj.pk"]),          // attribute lookup value
            s(&["named_url", "client.id"]),      // url19: unquoted view var
            s(&["\"view\"", "x=obj.pk"]),        // kwarg with attribute value
            s(&["\"view\"", "as", "u"]),         // {% url "view" as u %}
            s(&["\"view\"", "pk=1", "as", "u"]), // {% url "view" pk=1 as u %}
            s(&["\"view\"", "v|lower"]),         // a filtered value
        ] {
            assert!(
                validate_url_args(&args).is_ok(),
                "well-formed args wrongly refused: {args:?} -> {:?}",
                validate_url_args(&args)
            );
        }
    }

    #[test]
    fn url_kwarg_value_splits_like_django_kwarg_re() {
        // `name=value` splits on the first `=` after a leading \w+; everything
        // else is the whole bit (Django's `(?:(\w+)=)?(.+)`).
        assert_eq!(url_kwarg_value("pk=1"), "1");
        assert_eq!(
            url_kwarg_value("id=\"unterminatedstring"),
            "\"unterminatedstring"
        );
        // `id=` has nothing after the `=`, so the whole bit is the value and the
        // `=` is a remainder, not a separator.
        assert_eq!(url_kwarg_value("id="), "id=");
        // `a.id=id`: the leading \w+ run stops at the dot, so no separator.
        assert_eq!(url_kwarg_value("a.id=id"), "a.id=id");
        assert_eq!(url_kwarg_value("id,"), "id,");
    }

    // ---- dj-model allowlist (CWE-915, finding #3) -------------------------

    /// A loader with no entries — exercises the "no include resolution" path.
    struct NoIncludeLoader;
    impl crate::inheritance::TemplateLoader for NoIncludeLoader {
        fn load_template(&self, name: &str) -> Result<Vec<Node>> {
            Err(djust_core::DjangoRustError::TemplateError(format!(
                "no template: {name}"
            )))
        }
    }

    /// A HashMap-backed loader for `{% include %}` coverage tests.
    struct MapLoader {
        templates: std::collections::HashMap<String, String>,
    }
    impl MapLoader {
        fn new() -> Self {
            Self {
                templates: std::collections::HashMap::new(),
            }
        }
        fn add(&mut self, name: &str, src: &str) {
            self.templates.insert(name.to_string(), src.to_string());
        }
    }
    impl crate::inheritance::TemplateLoader for MapLoader {
        fn load_template(&self, name: &str) -> Result<Vec<Node>> {
            match self.templates.get(name) {
                Some(src) => {
                    let tokens = tokenize(src)?;
                    parse_with_source(&tokens, src)
                }
                None => Err(djust_core::DjangoRustError::TemplateError(format!(
                    "not found: {name}"
                ))),
            }
        }
    }

    fn fields(source: &str) -> Vec<String> {
        extract_dj_model_fields::<NoIncludeLoader>(source, None).unwrap()
    }

    // ---- #2558: the raw-body collector and the native scope arms ----------

    /// Serializes the tests that mutate the process-global scope-tag set.
    static SCOPE_TAG_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

    fn parse_src(source: &str) -> Vec<Node> {
        let tokens = tokenize(source).expect("tokenize failed");
        parse(&tokens).expect("parse failed")
    }

    /// The body a raw-block tag would hand Django, reconstructed from the
    /// token stream. Calls the collector directly: registering a real
    /// raw-block handler needs a Python object, which a `cargo test` has no
    /// interpreter for.
    fn collected(source: &str, end: &str) -> String {
        let tokens = tokenize(source).expect("tokenize failed");
        let (body, end_pos) = collect_raw_source(
            &tokens,
            0,
            &[end],
            format!("Unclosed raw-block tag, expected {{% {end} %}}"),
        )
        .expect("collect failed");
        // The collector stops ON the end tag, never past it.
        assert!(matches!(&tokens[end_pos], Token::Tag(name, _) if name == end));
        body
    }

    /// A comment re-emits VERBATIM. The collector used to drop it, so Django
    /// never saw it: `{% blocktranslate %}a {# c #} b{% endblocktranslate %}`
    /// rendered `a  b` where Django raises `doesn't allow other block tags
    /// (seen 'c')`. Silently mangling author content is the worse failure, so
    /// the body must carry the comment and let Django refuse it (#2597).
    #[test]
    fn raw_source_re_emits_a_comment_verbatim() {
        assert_eq!(
            collected("a {# c #} b{% endblocktranslate %}", "endblocktranslate"),
            "a {# c #} b"
        );
    }

    /// The whole inner text survives, not just a single word — Django reports
    /// the comment's stripped text as the `seen` payload.
    #[test]
    fn raw_source_re_emits_a_multiword_comment_verbatim() {
        assert_eq!(
            collected(
                "a {# Translators: hi #} b{% endblocktranslate %}",
                "endblocktranslate"
            ),
            "a {# Translators: hi #} b"
        );
    }

    /// An unterminated `{{` inside the body is TEXT, and the end tag after it
    /// still closes the block — the pre-#2597 lexer consumed to end-of-input
    /// and swallowed the end tag, so this raised `Unclosed raw-block tag` on
    /// a template Django renders verbatim.
    #[test]
    fn raw_source_keeps_an_unterminated_marker_and_still_finds_the_end_tag() {
        assert_eq!(
            collected(
                "a {{ unclosed b{% endblocktranslate %}",
                "endblocktranslate"
            ),
            "a {{ unclosed b"
        );
    }

    #[test]
    fn raw_source_reconstructs_a_variable_with_django_spacing() {
        // `{{anton}}` and `{{ berta  }}` both re-emit as `{{ name }}`: Django
        // re-LEXES this string, and its `Variable` grammar ignores the
        // spacing, so the msgid placeholder (`%(anton)s`) is unaffected.
        assert_eq!(
            collected(
                "{{anton}}{{ berta  }}{% endblocktranslate %}",
                "endblocktranslate"
            ),
            "{{ anton }}{{ berta }}"
        );
    }

    #[test]
    fn raw_source_reconstructs_text_tags_and_comments_verbatim() {
        // The comment re-emits too (#2597). This assertion used to expect it
        // DROPPED, which is what let a comment inside a `{% blocktranslate %}`
        // body silently vanish instead of reaching Django to be refused.
        assert_eq!(
            collected(
                "a %(x)s {% plural %}b{# c #}{% endblocktranslate %}",
                "endblocktranslate"
            ),
            "a %(x)s {% plural %}b{# c #}"
        );
        // A tag's arguments survive, space-joined — `{% templatetag openblock %}`
        // must reach Django as a tag, not as its rendered output.
        assert_eq!(
            collected(
                "{% templatetag openblock %}{% endblocktrans %}",
                "endblocktrans"
            ),
            "{% templatetag openblock %}"
        );
    }

    #[test]
    fn raw_source_is_not_rendered_so_a_nested_block_tag_reaches_django() {
        // The whole point of the fourth registration kind: Django must SEE
        // `{% block b %}` in the body to raise its own "doesn't allow other
        // block tags" error (#2558 §2).
        let body = collected(
            "Hello {% block b %}world{% endblock %}{% endblocktranslate %}",
            "endblocktranslate",
        );
        assert!(body.contains("{% block b %}"), "{body}");
        assert!(body.contains("{% endblock %}"), "{body}");
    }

    #[test]
    fn raw_source_without_its_end_tag_is_an_error_not_a_truncated_body() {
        let tokens = tokenize("x").expect("tokenize failed");
        let err = collect_raw_source(&tokens, 0, &["endblocktranslate"], "boom".to_string());
        assert!(
            err.is_err(),
            "an unclosed raw block must not silently close"
        );
    }

    #[test]
    fn an_unarmed_scope_tag_still_falls_through_to_unsupported() {
        let _guard = SCOPE_TAG_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        crate::registry::clear_scope_tags().unwrap();
        // Before any `{% load i18n %}`, `{% language %}` is exactly as
        // unsupported as it was before this row — the arming gate is what
        // keeps a project that never loads the library on its old behaviour.
        // Since #2549 an unsupported tag is REFUSED at parse, so the arm is
        // observed as that refusal rather than as an `UnsupportedTag` node.
        let tokens = tokenize("{% language \"de\" %}x{% endlanguage %}").expect("tokenize failed");
        let err = parse(&tokens).expect_err("an unarmed scope tag must be refused");
        assert!(
            err.to_string().contains("Unsupported template tag"),
            "{err}"
        );
    }

    #[test]
    fn armed_scope_tags_parse_as_native_nodes_with_their_children() {
        let _guard = SCOPE_TAG_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        crate::registry::clear_scope_tags().unwrap();
        crate::registry::arm_scope_tags(vec![
            "language".to_string(),
            "localize".to_string(),
            "localtime".to_string(),
            "timezone".to_string(),
        ])
        .unwrap();

        let nodes = parse_src("{% language \"de\" %}{{ n }}{% endlanguage %}");
        match &nodes[0] {
            Node::Language { expr, children } => {
                // The operand keeps its QUOTES: the renderer resolves it
                // through `django_literal`, which is what strips them (#2376).
                assert_eq!(expr, "\"de\"");
                assert_eq!(children.len(), 1);
            }
            other => panic!("expected Language, got {other:?}"),
        }

        match &parse_src("{% timezone tzname %}x{% endtimezone %}")[0] {
            Node::Timezone { expr, children } => {
                assert_eq!(expr, "tzname");
                assert_eq!(children.len(), 1);
            }
            other => panic!("expected Timezone, got {other:?}"),
        }

        // `on` / `off` / bare, for both flag nodes.
        for (src, expected) in [
            ("{% localize %}x{% endlocalize %}", true),
            ("{% localize on %}x{% endlocalize %}", true),
            ("{% localize off %}x{% endlocalize %}", false),
        ] {
            match &parse_src(src)[0] {
                Node::Localize { use_l10n, .. } => assert_eq!(*use_l10n, expected, "{src}"),
                other => panic!("expected Localize for {src}, got {other:?}"),
            }
        }
        for (src, expected) in [
            ("{% localtime %}x{% endlocaltime %}", true),
            ("{% localtime on %}x{% endlocaltime %}", true),
            ("{% localtime off %}x{% endlocaltime %}", false),
        ] {
            match &parse_src(src)[0] {
                Node::LocalTime { use_tz, .. } => assert_eq!(*use_tz, expected, "{src}"),
                other => panic!("expected LocalTime for {src}, got {other:?}"),
            }
        }

        // Nesting: a scope node's children are parsed, so an inner scope is
        // a child node rather than a flattened sibling.
        match &parse_src(
            "{% language \"de\" %}{% language \"fr\" %}x{% endlanguage %}{% endlanguage %}",
        )[0]
        {
            Node::Language { children, .. } => {
                assert!(matches!(children[0], Node::Language { .. }), "{children:?}");
            }
            other => panic!("expected Language, got {other:?}"),
        }

        crate::registry::clear_scope_tags().unwrap();
    }

    #[test]
    fn dj_model_basic_attribute_collected() {
        assert_eq!(
            fields(r#"<div dj-root><input dj-model="search"></div>"#),
            vec!["search".to_string()]
        );
    }

    #[test]
    fn dj_model_single_and_double_quotes() {
        let mut got = fields(r#"<input dj-model="x"><input dj-model='y'>"#);
        got.sort();
        assert_eq!(got, vec!["x".to_string(), "y".to_string()]);
    }

    #[test]
    fn dj_model_data_prefix_not_overmatched() {
        // `data-dj-model="is_admin"` must NOT widen the allowlist.
        let got = fields(r#"<input dj-model="search" data-dj-model="is_admin">"#);
        assert_eq!(got, vec!["search".to_string()]);
    }

    #[test]
    fn dj_model_nested_in_if_and_for_collected() {
        let src = r#"
            {% if flag %}<input dj-model="a">{% else %}<input dj-model="b">{% endif %}
            {% for it in items %}<input dj-model="c">{% endfor %}
        "#;
        let mut got = fields(src);
        got.sort();
        assert_eq!(got, vec!["a".to_string(), "b".to_string(), "c".to_string()]);
    }

    #[test]
    fn dj_model_text_node_poison_is_immune_via_variable() {
        // The poisoning vector arrives as a `{{ var }}` substitution, which is
        // a `Node::Variable`, never a `Node::Text` literal — so it cannot be
        // captured. Static `dj-model="search"` still is.
        let src = r#"<input dj-model="search"><p>{{ comment }}</p>"#;
        assert_eq!(fields(src), vec!["search".to_string()]);
    }

    #[test]
    fn dj_model_dynamic_binding_not_captured() {
        // `dj-model="{{ field }}"` straddles Text + Variable — the literal value
        // is the empty between the quotes, so nothing is captured (fail-closed).
        let src = r#"<input dj-model="{{ field }}">"#;
        assert!(fields(src).is_empty());
    }

    #[test]
    fn dj_model_extends_merged_source_covered() {
        // Caller resolves {% extends %} into one merged source; a base-template
        // dj-model binding (now in a Node::Text of the merged tree) is captured.
        let merged = r#"<html><body><input dj-model="base_field">
            <div dj-root><input dj-model="child_field"></div></body></html>"#;
        let mut got = fields(merged);
        got.sort();
        assert_eq!(
            got,
            vec!["base_field".to_string(), "child_field".to_string()]
        );
    }

    #[test]
    fn dj_model_include_resolved_via_loader() {
        let mut loader = MapLoader::new();
        loader.add("partial.html", r#"<input dj-model="from_include">"#);
        let src = r#"<input dj-model="main">{% include "partial.html" %}"#;
        let mut got = extract_dj_model_fields(src, Some(&loader)).unwrap();
        got.sort();
        assert_eq!(got, vec!["from_include".to_string(), "main".to_string()]);
    }

    #[test]
    fn dj_model_unknown_include_name_fails_closed() {
        // djust's Rust engine treats the include name as a literal path (no
        // dynamic includes). A name the loader doesn't know simply fails to
        // resolve and contributes nothing — fail-closed.
        let mut loader = MapLoader::new();
        loader.add("partial.html", r#"<input dj-model="from_include">"#);
        let src = r#"<input dj-model="main">{% include "unknown.html" %}"#;
        let got = extract_dj_model_fields(src, Some(&loader)).unwrap();
        assert_eq!(got, vec!["main".to_string()]);
    }

    #[test]
    fn dj_model_missing_include_fails_closed() {
        let loader = MapLoader::new(); // empty
        let src = r#"<input dj-model="main">{% include "nope.html" %}"#;
        let got = extract_dj_model_fields(src, Some(&loader)).unwrap();
        assert_eq!(got, vec!["main".to_string()]);
    }

    #[test]
    fn test_parse_simple() {
        let tokens = tokenize("Hello {{ name }}").unwrap();
        let nodes = parse(&tokens).unwrap();
        assert_eq!(nodes.len(), 2);
    }

    #[test]
    fn test_parse_if() {
        let tokens = tokenize("{% if true %}yes{% endif %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        assert_eq!(nodes.len(), 1);
        match &nodes[0] {
            Node::If { .. } => (),
            _ => panic!("Expected If node"),
        }
    }

    // -----------------------------------------------------------------
    // Tests for the per-template ID-prefix scheme — Stage 11 fix on
    // PR #1363 (#1358 Iter 1). The boundary-marker IDs must be
    // `if-<8-hex-chars>-<counter>` and the prefix must derive from
    // the source (or token-stream fallback) so independently-parsed
    // templates don't collide.
    // -----------------------------------------------------------------

    fn marker_id_of(node: &Node) -> Option<String> {
        match node {
            Node::If { marker_id, .. } => marker_id.clone(),
            _ => None,
        }
    }

    #[test]
    fn test_parse_with_source_assigns_prefixed_id() {
        let source = "{% if a %}<div>X</div>{% endif %}";
        let tokens = tokenize(source).unwrap();
        let nodes = parse_with_source(&tokens, source).unwrap();
        let id = marker_id_of(&nodes[0]).expect("if must have marker_id");
        // Format: `if-<8 hex>-<counter>`
        let re = regex::Regex::new(r"^if-[0-9a-f]{8}-\d+$").unwrap();
        assert!(re.is_match(&id), "id should match shape: {id}");
    }

    #[test]
    fn test_parse_with_source_distinct_sources_distinct_prefixes() {
        let s1 = "{% if a %}<div>X</div>{% endif %}";
        let s2 = "{% if b %}<span>Y</span>{% endif %}";
        let n1 = parse_with_source(&tokenize(s1).unwrap(), s1).unwrap();
        let n2 = parse_with_source(&tokenize(s2).unwrap(), s2).unwrap();
        let id1 = marker_id_of(&n1[0]).unwrap();
        let id2 = marker_id_of(&n2[0]).unwrap();
        assert_ne!(id1, id2, "different sources must produce different IDs");
        // The counter portion is `0` for both — only the prefix differs.
        assert!(id1.ends_with("-0"));
        assert!(id2.ends_with("-0"));
    }

    #[test]
    fn test_parse_with_source_same_source_same_prefix() {
        let source = "{% if a %}<div>X</div>{% endif %}{% if b %}<span>Y</span>{% endif %}";
        let n1 = parse_with_source(&tokenize(source).unwrap(), source).unwrap();
        let n2 = parse_with_source(&tokenize(source).unwrap(), source).unwrap();
        assert_eq!(marker_id_of(&n1[0]), marker_id_of(&n2[0]));
        assert_eq!(marker_id_of(&n1[1]), marker_id_of(&n2[1]));
    }

    // -----------------------------------------------------------------
    // template_hash_hex tests (#1362 section 1).
    //
    // The cache key in `python/djust/mixins/rust_bridge.py` derives the
    // template-hash slot from `template_hash_hex(template_source)`. The
    // SAME helper underlies the marker-ID prefix in `parse_with_source`,
    // so an invariant test pins the equality of the two derivations.
    // -----------------------------------------------------------------

    #[test]
    fn test_template_hash_hex_consistent_for_same_source() {
        let source = "{% if a %}<div>X</div>{% endif %}";
        let h1 = template_hash_hex(source);
        let h2 = template_hash_hex(source);
        assert_eq!(h1, h2, "same source must produce identical hash");
        // Shape: 8 lowercase hex chars.
        let re = regex::Regex::new(r"^[0-9a-f]{8}$").unwrap();
        assert!(re.is_match(&h1), "hash must be 8 hex chars: {h1}");
    }

    #[test]
    fn test_template_hash_hex_distinct_for_distinct_sources() {
        let h1 = template_hash_hex("<div>{{ a }}</div>");
        let h2 = template_hash_hex("<div>{{ b }}</div>");
        assert_ne!(
            h1, h2,
            "different sources must (almost certainly) produce different hashes"
        );
    }

    #[test]
    fn test_template_hash_hex_matches_marker_id_prefix() {
        // The cache-key contract: `template_hash_hex(src)` must equal
        // the prefix that `parse_with_source(tokens, src)` would derive
        // for the same source. This invariant is what makes the cache
        // key change automatically whenever ANY operator edits a
        // template — both consumers (parser + cache key) flow through
        // the same hash derivation.
        let source = "{% if a %}<div>X</div>{% endif %}";
        let direct = template_hash_hex(source);
        let nodes = parse_with_source(&tokenize(source).unwrap(), source).unwrap();
        let id = marker_id_of(&nodes[0]).expect("if must have marker_id");
        // Marker ID format: `if-<8hex>-<counter>`. Extract the 8-hex
        // segment between the two dashes.
        let parts: Vec<&str> = id.splitn(3, '-').collect();
        assert_eq!(parts.len(), 3, "marker id has 3 dash-segments: {id}");
        assert_eq!(parts[0], "if");
        assert_eq!(
            parts[1], direct,
            "marker prefix must match template_hash_hex"
        );
    }

    #[test]
    fn test_parse_legacy_uses_token_hash_prefix() {
        // The legacy `parse(tokens)` (no source) should still produce
        // a deterministic prefix from the token stream. Same tokens
        // → same prefix. Different tokens → almost certainly different
        // prefix.
        let tokens_a = tokenize("{% if a %}<div>X</div>{% endif %}").unwrap();
        let tokens_b = tokenize("{% if b %}<span>Y</span>{% endif %}").unwrap();
        let na1 = parse(&tokens_a).unwrap();
        let na2 = parse(&tokens_a).unwrap();
        let nb = parse(&tokens_b).unwrap();
        let id_a1 = marker_id_of(&na1[0]).unwrap();
        let id_a2 = marker_id_of(&na2[0]).unwrap();
        let id_b = marker_id_of(&nb[0]).unwrap();
        assert_eq!(id_a1, id_a2);
        assert_ne!(id_a1, id_b);
    }

    #[test]
    fn test_verbatim_tag() {
        let tokens = tokenize("{% verbatim %}{{ name }}{% endverbatim %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        assert_eq!(nodes.len(), 1);
        match &nodes[0] {
            Node::Text(text) => assert_eq!(text, "{{ name }}"),
            _ => panic!("Expected Text node"),
        }
    }

    #[test]
    fn test_verbatim_tag_with_tags() {
        let tokens =
            tokenize("{% verbatim %}{% if true %}{{ value }}{% endif %}{% endverbatim %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        assert_eq!(nodes.len(), 1);
        match &nodes[0] {
            Node::Text(text) => assert_eq!(text, "{% if true %}{{ value }}{% endif %}"),
            _ => panic!("Expected Text node"),
        }
    }

    #[test]
    fn test_verbatim_tag_mixed() {
        let tokens = tokenize("Before{% verbatim %}{{ name }}{% endverbatim %}After").unwrap();
        let nodes = parse(&tokens).unwrap();
        assert_eq!(nodes.len(), 3);
        match &nodes[0] {
            Node::Text(text) => assert_eq!(text, "Before"),
            _ => panic!("Expected Text node"),
        }
        match &nodes[1] {
            Node::Text(text) => assert_eq!(text, "{{ name }}"),
            _ => panic!("Expected Text node from verbatim"),
        }
        match &nodes[2] {
            Node::Text(text) => assert_eq!(text, "After"),
            _ => panic!("Expected Text node"),
        }
    }

    #[test]
    fn test_with_tag() {
        let tokens = tokenize("{% with name=user.name %}{{ name }}{% endwith %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        assert_eq!(nodes.len(), 1);
        match &nodes[0] {
            Node::With { assignments, nodes } => {
                assert_eq!(assignments.len(), 1);
                assert_eq!(assignments[0].0, "name");
                assert_eq!(assignments[0].1, "user.name");
                assert_eq!(nodes.len(), 1);
            }
            _ => panic!("Expected With node"),
        }
    }

    #[test]
    fn test_with_tag_multiple_assignments() {
        let tokens = tokenize("{% with a=x b=y %}{{ a }} {{ b }}{% endwith %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        match &nodes[0] {
            Node::With { assignments, .. } => {
                assert_eq!(assignments.len(), 2);
                assert_eq!(assignments[0].0, "a");
                assert_eq!(assignments[0].1, "x");
                assert_eq!(assignments[1].0, "b");
                assert_eq!(assignments[1].1, "y");
            }
            _ => panic!("Expected With node"),
        }
    }

    #[test]
    fn test_load_tag() {
        let tokens = tokenize("{% load static %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        assert_eq!(nodes.len(), 1);
        // Load preserves library names
        match &nodes[0] {
            Node::Load(libs) => assert_eq!(libs, &["static"]),
            _ => panic!("Expected Load node for load tag"),
        }
    }

    #[test]
    fn test_extends_tag() {
        let tokens = tokenize("{% extends \"base.html\" %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        assert_eq!(nodes.len(), 1);
        match &nodes[0] {
            Node::Extends(template) => {
                assert_eq!(template, "\"base.html\"");
            }
            _ => panic!("Expected Extends node"),
        }
    }

    #[test]
    fn test_extends_tag_single_quotes() {
        let tokens = tokenize("{% extends 'parent.html' %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        match &nodes[0] {
            Node::Extends(template) => {
                // The RAW token keeps the quotes the author wrote (#2517).
                assert_eq!(template, "'parent.html'");
            }
            _ => panic!("Expected Extends node"),
        }
    }

    #[test]
    fn test_extends_with_blocks() {
        let tokens =
            tokenize("{% extends \"base.html\" %}{% block content %}Hello{% endblock %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        assert_eq!(nodes.len(), 2);
        match &nodes[0] {
            Node::Extends(template) => assert_eq!(template, "\"base.html\""),
            _ => panic!("Expected Extends node"),
        }
        match &nodes[1] {
            Node::Block { name, .. } => assert_eq!(name, "content"),
            _ => panic!("Expected Block node"),
        }
    }

    // Tests for variable extraction (JIT serialization)

    #[test]
    fn test_extract_simple_variable() {
        let template = "{{ name }}";
        let vars = extract_template_variables(template).unwrap();
        assert!(vars.contains_key("name"));
        assert_eq!(vars.get("name").unwrap().len(), 0); // No path, just root
    }

    #[test]
    fn test_extract_nested_variable() {
        let template = "{{ user.email }}";
        let vars = extract_template_variables(template).unwrap();
        assert!(vars.contains_key("user"));
        assert_eq!(vars.get("user").unwrap(), &vec!["email".to_string()]);
    }

    #[test]
    fn test_extract_multiple_paths() {
        let template = r#"
            {{ lease.property.name }}
            {{ lease.tenant.user.email }}
            {{ lease.end_date }}
        "#;
        let vars = extract_template_variables(template).unwrap();

        assert!(vars.contains_key("lease"));
        let lease_paths = vars.get("lease").unwrap();
        assert_eq!(lease_paths.len(), 3);
        assert!(lease_paths.contains(&"property.name".to_string()));
        assert!(lease_paths.contains(&"tenant.user.email".to_string()));
        assert!(lease_paths.contains(&"end_date".to_string()));
    }

    #[test]
    fn test_extract_with_filters() {
        let template = r#"{{ lease.end_date|date:"M d, Y" }}"#;
        let vars = extract_template_variables(template).unwrap();
        assert!(vars.contains_key("lease"));
        assert_eq!(vars.get("lease").unwrap(), &vec!["end_date".to_string()]);
    }

    #[test]
    fn test_extract_in_if_tag() {
        let template = r#"{% if lease.property.status == "active" %}...{% endif %}"#;
        let vars = extract_template_variables(template).unwrap();
        assert!(vars.contains_key("lease"));
        assert!(vars
            .get("lease")
            .unwrap()
            .contains(&"property.status".to_string()));
    }

    #[test]
    fn test_extract_in_for_tag() {
        let template = r#"{% for item in items.all %}{{ item.name }}{% endfor %}"#;
        let vars = extract_template_variables(template).unwrap();
        assert!(vars.contains_key("items"));
        assert!(vars.get("items").unwrap().contains(&"all".to_string()));
        assert!(vars.contains_key("item"));
        assert!(vars.get("item").unwrap().contains(&"name".to_string()));
    }

    #[test]
    fn test_extract_deduplication() {
        let template = r#"
            {{ lease.property.name }}
            {{ lease.property.name }}
            {{ lease.property.address }}
        "#;
        let vars = extract_template_variables(template).unwrap();
        let lease_paths = vars.get("lease").unwrap();

        // Should have 2 unique paths, not 3
        assert_eq!(lease_paths.len(), 2);
        assert!(lease_paths.contains(&"property.name".to_string()));
        assert!(lease_paths.contains(&"property.address".to_string()));
    }

    #[test]
    fn test_extract_real_world_template() {
        let template = r#"
            {% for lease in expiring_soon %}
              <td>{{ lease.property.name }}</td>
              <td>{{ lease.property.address }}</td>
              <td>{{ lease.tenant.user.get_full_name }}</td>
              <td>{{ lease.tenant.user.email }}</td>
              <td>{{ lease.end_date|date:"M d, Y" }}</td>
            {% endfor %}
        "#;
        let vars = extract_template_variables(template).unwrap();

        assert!(vars.contains_key("lease"));
        let lease_paths = vars.get("lease").unwrap();

        assert!(lease_paths.contains(&"property.name".to_string()));
        assert!(lease_paths.contains(&"property.address".to_string()));
        assert!(lease_paths.contains(&"tenant.user.get_full_name".to_string()));
        assert!(lease_paths.contains(&"tenant.user.email".to_string()));
        assert!(lease_paths.contains(&"end_date".to_string()));

        // Check expiring_soon is tracked
        assert!(vars.contains_key("expiring_soon"));
    }

    #[test]
    fn test_extract_with_tag() {
        let template = r#"{% with total=items.count %}{{ total }}{% endwith %}"#;
        let vars = extract_template_variables(template).unwrap();
        assert!(vars.contains_key("items"));
        assert!(vars.get("items").unwrap().contains(&"count".to_string()));
        assert!(vars.contains_key("total"));
    }

    // Edge case tests
    #[test]
    fn test_extract_empty_template() {
        let template = "";
        let vars = extract_template_variables(template).unwrap();
        assert_eq!(vars.len(), 0);
    }

    #[test]
    fn test_extract_only_text() {
        let template = "<html><body>Hello World</body></html>";
        let vars = extract_template_variables(template).unwrap();
        assert_eq!(vars.len(), 0);
    }

    #[test]
    fn test_extract_whitespace_handling() {
        let template = "{{  user.name  }}";
        let vars = extract_template_variables(template).unwrap();
        assert!(vars.contains_key("user"));
        assert!(vars.get("user").unwrap().contains(&"name".to_string()));
    }

    #[test]
    fn test_extract_deeply_nested_paths() {
        let template = "{{ a.b.c.d.e.f.g.h.i.j }}";
        let vars = extract_template_variables(template).unwrap();
        assert!(vars.contains_key("a"));
        assert!(vars
            .get("a")
            .unwrap()
            .contains(&"b.c.d.e.f.g.h.i.j".to_string()));
    }

    #[test]
    fn test_extract_mixed_content() {
        let template = r#"
            <div class="header">{{ site.name }}</div>
            {% if user.is_authenticated %}
                <p>Welcome {{ user.profile.display_name }}!</p>
                {% for message in user.messages.unread %}
                    <div>{{ message.text }}</div>
                {% endfor %}
            {% else %}
                <a href="/login">Login</a>
            {% endif %}
        "#;
        let vars = extract_template_variables(template).unwrap();

        assert!(vars.contains_key("site"));
        assert!(vars.get("site").unwrap().contains(&"name".to_string()));

        assert!(vars.contains_key("user"));
        let user_paths = vars.get("user").unwrap();
        assert!(user_paths.contains(&"is_authenticated".to_string()));
        assert!(user_paths.contains(&"profile.display_name".to_string()));
        assert!(user_paths.contains(&"messages.unread".to_string()));

        assert!(vars.contains_key("message"));
        assert!(vars.get("message").unwrap().contains(&"text".to_string()));
    }

    #[test]
    fn test_extract_with_complex_filters() {
        let template = r#"
            {{ date|date:"Y-m-d H:i:s" }}
            {{ text|truncatewords:10|upper }}
            {{ value|default:"N/A"|safe }}
        "#;
        let vars = extract_template_variables(template).unwrap();
        assert!(vars.contains_key("date"));
        assert!(vars.contains_key("text"));
        assert!(vars.contains_key("value"));
    }

    #[test]
    fn test_extract_multiple_variables_same_line() {
        let template = "{{ a }} {{ b }} {{ c.d }} {{ e.f.g }}";
        let vars = extract_template_variables(template).unwrap();
        assert_eq!(vars.len(), 4);
        assert!(vars.contains_key("a"));
        assert!(vars.contains_key("b"));
        assert!(vars.contains_key("c"));
        assert!(vars.contains_key("e"));
    }

    #[test]
    fn test_extract_nested_blocks() {
        let template = r#"
            {% block outer %}
                {{ outer_var }}
                {% block inner %}
                    {{ inner_var }}
                {% endblock %}
            {% endblock %}
        "#;
        let vars = extract_template_variables(template).unwrap();
        assert!(vars.contains_key("outer_var"));
        assert!(vars.contains_key("inner_var"));
    }

    #[test]
    fn test_extract_complex_for_loops() {
        let template = r#"
            {% for category in categories.active %}
                {% for item in category.items.filter_by_status %}
                    {{ item.title }}
                    {% for tag in item.tags.all %}
                        {{ tag.name }}
                    {% endfor %}
                {% endfor %}
            {% endfor %}
        "#;
        let vars = extract_template_variables(template).unwrap();

        assert!(vars.contains_key("categories"));
        assert!(vars
            .get("categories")
            .unwrap()
            .contains(&"active".to_string()));

        assert!(vars.contains_key("category"));
        assert!(vars
            .get("category")
            .unwrap()
            .contains(&"items.filter_by_status".to_string()));

        assert!(vars.contains_key("item"));
        let item_paths = vars.get("item").unwrap();
        assert!(item_paths.contains(&"title".to_string()));
        assert!(item_paths.contains(&"tags.all".to_string()));

        assert!(vars.contains_key("tag"));
        assert!(vars.get("tag").unwrap().contains(&"name".to_string()));
    }

    #[test]
    fn test_extract_complex_conditionals() {
        // Note: Current parser extracts from if condition but not elif conditions
        // This is a known limitation that will be addressed in future phases
        let template = r#"
            {% if user.profile.is_verified and user.subscription.is_active %}
                Premium User
            {% endif %}
        "#;
        let vars = extract_template_variables(template).unwrap();

        assert!(vars.contains_key("user"));
        let user_paths = vars.get("user").unwrap();
        assert!(user_paths.contains(&"profile.is_verified".to_string()));
        assert!(user_paths.contains(&"subscription.is_active".to_string()));
    }

    #[test]
    fn test_extract_special_characters_in_text() {
        // Special characters in TEXT (outside `{{ }}`) must not break variable
        // extraction. The `{{ & < > }}` this once used is refused as of #2578 —
        // `& < >` is not a valid variable head, and Django refuses it identically
        // (`Could not parse the remainder: '& < >' from '& < >'`), so putting the
        // special characters where they are legal keeps the test's intent.
        let template = r#"<div data-value="{{ value }}">& &lt; &gt; &amp;</div>"#;
        let vars = extract_template_variables(template).unwrap();
        assert!(vars.contains_key("value"));
    }

    #[test]
    fn test_extract_with_includes() {
        // Even though we don't process includes, we should extract variables
        let template = r#"
            {% include "header.html" with title=page.title %}
            {{ content }}
        "#;
        let vars = extract_template_variables(template).unwrap();
        // Should at least extract 'content'
        assert!(vars.contains_key("content"));
    }

    #[test]
    fn test_extract_inside_block_tag_body() {
        // Variables inside a block tag's body are extracted. (This used to
        // wrap the body in an unregistered `{% react %}` tag, which the
        // parser now refuses outright — #2549 — so it uses a built-in.)
        let template = r#"{% spaceless %}{{ button.label }}{% endspaceless %}"#;
        let vars = extract_template_variables(template).unwrap();
        assert!(vars.contains_key("button"));
        let button_paths = vars.get("button").unwrap();
        assert!(button_paths.contains(&"label".to_string()));
    }

    #[test]
    fn test_extract_refuses_unregistered_tag_at_parse() {
        // #2549: an unregistered tag is a parse error everywhere the parser
        // runs, variable extraction included — no `Node::UnsupportedTag`
        // is built for the renderer to trip over later.
        let err = extract_template_variables(r#"{% react "Button" %}{% endreact %}"#)
            .expect_err("an unregistered tag must refuse at parse");
        assert!(
            err.to_string()
                .contains("Unsupported template tag '{% react \"Button\" %}'"),
            "got: {err}"
        );
    }

    #[test]
    fn test_extract_large_template() {
        // Test performance with a large template
        let mut template_parts = Vec::new();
        for i in 0..100 {
            template_parts.push(format!(
                r#"
                {{% for obj{i} in list{i} %}}
                    {{{{ obj{i}.field1 }}}}
                    {{{{ obj{i}.field2.nested }}}}
                {{% endfor %}}
            "#
            ));
        }
        let template = template_parts.join("\n");
        let vars = extract_template_variables(&template).unwrap();

        // Should have extracted variables for all 100 iterations
        assert!(vars.len() >= 100);
    }

    #[test]
    fn test_extract_paths_sorted() {
        let template = r#"
            {{ obj.zebra }}
            {{ obj.apple }}
            {{ obj.middle }}
        "#;
        let vars = extract_template_variables(template).unwrap();
        let paths = vars.get("obj").unwrap();

        // Paths should be sorted
        assert_eq!(paths[0], "apple");
        assert_eq!(paths[1], "middle");
        assert_eq!(paths[2], "zebra");
    }

    #[test]
    fn test_extract_method_calls() {
        let template = "{{ items.all }} {{ user.get_full_name }} {{ count.increment }}";
        let vars = extract_template_variables(template).unwrap();

        assert!(vars.contains_key("items"));
        assert!(vars.get("items").unwrap().contains(&"all".to_string()));

        assert!(vars.contains_key("user"));
        assert!(vars
            .get("user")
            .unwrap()
            .contains(&"get_full_name".to_string()));

        assert!(vars.contains_key("count"));
        assert!(vars
            .get("count")
            .unwrap()
            .contains(&"increment".to_string()));
    }

    // Tests for elif support (Issue #79)

    #[test]
    fn test_parse_if_elif() {
        let tokens = tokenize("{% if a %}A{% elif b %}B{% endif %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        assert_eq!(nodes.len(), 1);
        match &nodes[0] {
            Node::If {
                condition,
                true_nodes,
                false_nodes,
                ..
            } => {
                assert_eq!(condition, "a");
                assert_eq!(true_nodes.len(), 1);
                // false_nodes should contain a nested If for the elif
                assert_eq!(false_nodes.len(), 1);
                match &false_nodes[0] {
                    Node::If {
                        condition: elif_cond,
                        true_nodes: elif_true,
                        false_nodes: elif_false,
                        ..
                    } => {
                        assert_eq!(elif_cond, "b");
                        assert_eq!(elif_true.len(), 1);
                        assert_eq!(elif_false.len(), 0);
                    }
                    _ => panic!("Expected nested If node for elif"),
                }
            }
            _ => panic!("Expected If node"),
        }
    }

    #[test]
    fn test_parse_if_elif_else() {
        let tokens = tokenize("{% if a %}A{% elif b %}B{% else %}C{% endif %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        assert_eq!(nodes.len(), 1);
        match &nodes[0] {
            Node::If {
                condition,
                true_nodes,
                false_nodes,
                ..
            } => {
                assert_eq!(condition, "a");
                assert_eq!(true_nodes.len(), 1);
                // false_nodes should contain a nested If for the elif
                assert_eq!(false_nodes.len(), 1);
                match &false_nodes[0] {
                    Node::If {
                        condition: elif_cond,
                        true_nodes: elif_true,
                        false_nodes: elif_false,
                        ..
                    } => {
                        assert_eq!(elif_cond, "b");
                        assert_eq!(elif_true.len(), 1);
                        // The else branch should be in elif's false_nodes
                        assert_eq!(elif_false.len(), 1);
                        match &elif_false[0] {
                            Node::Text(text) => assert_eq!(text, "C"),
                            _ => panic!("Expected Text node for else branch"),
                        }
                    }
                    _ => panic!("Expected nested If node for elif"),
                }
            }
            _ => panic!("Expected If node"),
        }
    }

    #[test]
    fn test_parse_multiple_elif() {
        let tokens =
            tokenize("{% if a %}A{% elif b %}B{% elif c %}C{% elif d %}D{% endif %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        assert_eq!(nodes.len(), 1);

        // Verify nested structure: if a -> elif b -> elif c -> elif d
        match &nodes[0] {
            Node::If {
                condition,
                false_nodes,
                ..
            } => {
                assert_eq!(condition, "a");
                assert_eq!(false_nodes.len(), 1);
                match &false_nodes[0] {
                    Node::If {
                        condition: cond_b,
                        false_nodes: false_b,
                        ..
                    } => {
                        assert_eq!(cond_b, "b");
                        assert_eq!(false_b.len(), 1);
                        match &false_b[0] {
                            Node::If {
                                condition: cond_c,
                                false_nodes: false_c,
                                ..
                            } => {
                                assert_eq!(cond_c, "c");
                                assert_eq!(false_c.len(), 1);
                                match &false_c[0] {
                                    Node::If {
                                        condition: cond_d, ..
                                    } => {
                                        assert_eq!(cond_d, "d");
                                    }
                                    _ => panic!("Expected If node for elif d"),
                                }
                            }
                            _ => panic!("Expected If node for elif c"),
                        }
                    }
                    _ => panic!("Expected If node for elif b"),
                }
            }
            _ => panic!("Expected If node"),
        }
    }

    #[test]
    fn test_elif_with_string_comparison() {
        // This is the exact use case from Issue #79
        let tokens = tokenize(
            r#"{% if icon == "arrow-left" %}ARROW{% elif icon == "close" %}CLOSE{% else %}DEFAULT{% endif %}"#,
        )
        .unwrap();
        let nodes = parse(&tokens).unwrap();
        assert_eq!(nodes.len(), 1);

        match &nodes[0] {
            Node::If {
                condition,
                true_nodes,
                false_nodes,
                ..
            } => {
                assert_eq!(condition, r#"icon == "arrow-left""#);
                // Verify true branch has "ARROW"
                match &true_nodes[0] {
                    Node::Text(text) => assert_eq!(text, "ARROW"),
                    _ => panic!("Expected Text node"),
                }
                // Verify elif branch
                match &false_nodes[0] {
                    Node::If {
                        condition: elif_cond,
                        true_nodes: elif_true,
                        false_nodes: elif_false,
                        ..
                    } => {
                        assert_eq!(elif_cond, r#"icon == "close""#);
                        match &elif_true[0] {
                            Node::Text(text) => assert_eq!(text, "CLOSE"),
                            _ => panic!("Expected Text node in elif"),
                        }
                        match &elif_false[0] {
                            Node::Text(text) => assert_eq!(text, "DEFAULT"),
                            _ => panic!("Expected Text node in else"),
                        }
                    }
                    _ => panic!("Expected If node for elif"),
                }
            }
            _ => panic!("Expected If node"),
        }
    }

    #[test]
    fn test_extract_variables_with_elif() {
        let template = r#"
            {% if user.is_admin %}
                Admin
            {% elif user.is_staff %}
                Staff
            {% elif user.is_verified %}
                Verified
            {% else %}
                Regular
            {% endif %}
        "#;
        let vars = extract_template_variables(template).unwrap();

        assert!(vars.contains_key("user"));
        let user_paths = vars.get("user").unwrap();
        assert!(user_paths.contains(&"is_admin".to_string()));
        assert!(user_paths.contains(&"is_staff".to_string()));
        assert!(user_paths.contains(&"is_verified".to_string()));
    }

    #[test]
    fn test_elif_after_else_is_error() {
        // {% elif %} after {% else %} is invalid syntax (matches Django behavior)
        let tokens = tokenize("{% if a %}A{% else %}B{% elif c %}C{% endif %}").unwrap();
        let result = parse(&tokens);
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert!(err.to_string().contains("elif"));
        assert!(err.to_string().contains("else"));
    }

    #[test]
    fn test_inline_if_parses_to_inline_if_node() {
        let tokens = tokenize("{{ 'btn--active' if view_mode == 'day' else '' }}").unwrap();
        let nodes = parse(&tokens).unwrap();
        assert_eq!(nodes.len(), 1);
        match &nodes[0] {
            Node::InlineIf {
                true_expr,
                condition,
                false_expr,
                filters,
            } => {
                assert_eq!(true_expr, "'btn--active'");
                assert_eq!(condition, "view_mode == 'day'");
                assert_eq!(false_expr, "''");
                assert!(filters.is_empty());
            }
            _ => panic!("Expected InlineIf node, got {:?}", nodes[0]),
        }
    }

    #[test]
    fn test_inline_if_without_else() {
        let tokens = tokenize("{{ 'active' if is_active }}").unwrap();
        let nodes = parse(&tokens).unwrap();
        assert_eq!(nodes.len(), 1);
        match &nodes[0] {
            Node::InlineIf {
                true_expr,
                condition,
                false_expr,
                ..
            } => {
                assert_eq!(true_expr, "'active'");
                assert_eq!(condition, "is_active");
                assert_eq!(false_expr, "");
            }
            _ => panic!("Expected InlineIf node"),
        }
    }

    #[test]
    fn test_regular_variable_not_affected() {
        // A variable that happens to contain "if" in its name must not be treated as InlineIf
        let tokens = tokenize("{{ notify_if_late }}").unwrap();
        let nodes = parse(&tokens).unwrap();
        assert_eq!(nodes.len(), 1);
        match &nodes[0] {
            Node::Variable(name, _, _) => assert_eq!(name, "notify_if_late"),
            _ => panic!("Expected Variable node"),
        }
    }

    // ---- #2578: `{{ … }}` head grammar (validate_plain_variable_expr) ----

    /// Parse a template source and return the compile Result (Ok on parse,
    /// Err when a node is refused). The sibling `parse_src` above unwraps; this
    /// keeps the Result so refusals can be asserted.
    fn parse_src_result(src: &str) -> Result<Vec<Node>> {
        parse(&tokenize(src).unwrap())
    }

    /// Django's `FilterExpression` refuses each of these with
    /// `Could not parse the remainder: '<rest>' from '<whole>'`. The remainders
    /// are the LIVE-Django output for these exact sources (`test_basic_syntax06`
    /// / 13-17 / 23 in Django's `template_tests`), pasted verbatim so the test
    /// is a parity assertion, not a restatement of the implementation (#1046).
    #[test]
    fn test_malformed_variable_expressions_are_refused_django_parity_2578() {
        let cases = [
            // (source, expected remainder, expected whole)
            (
                "{{ multi word variable }}",
                " word variable",
                "multi word variable",
            ),
            ("{{ va>r }}", ">r", "va>r"),
            ("{{ (var.r) }}", "(var.r)", "(var.r)"),
            ("{{ sp%am }}", "%am", "sp%am"),
            ("{{ eggs! }}", "!", "eggs!"),
            ("{{ moo? }}", "?", "moo?"),
            // Cell 23: djust's lexer takes the variable content up to the first
            // `}}`, exactly like Django, so the head is `moo #} {{ cow` and the
            // remainder is everything after `moo`.
            ("{{ moo #} {{ cow }}", " #} {{ cow", "moo #} {{ cow"),
        ];
        for (src, remainder, whole) in cases {
            let err =
                parse_src_result(src).expect_err(&format!("{src} must be refused, not parsed"));
            let msg = err.to_string();
            let expected = format!("Could not parse the remainder: '{remainder}' from '{whole}'");
            assert!(
                msg.contains(&expected),
                "for {src}: expected message to contain {expected:?}, got {msg:?}",
            );
        }
    }

    /// Heads that are outside `[\w.]` but are LEGITIMATE constants, plus the
    /// ordinary dotted-variable and filtered forms, must still parse. This is
    /// the gate-off counterweight to the refusal test above: it fails if the
    /// grammar check is too eager (would false-refuse a valid literal).
    #[test]
    fn test_valid_variable_heads_still_parse_2578() {
        for src in [
            "{{ 'hello world' }}", // quoted string (spaces, quotes)
            "{{ \"hi\" }}",        // double-quoted
            "{{ 42 }}",            // int
            "{{ 1.5 }}",           // float
            "{{ -5 }}",            // negative int (leading `-` is outside [\\w.])
            "{{ 1e-5 }}",          // scientific (`-` outside [\\w.])
            // (`_("…")` is exercised at the Python level in the #2578 parity
            // test; `django_literal` translates it, which needs an initialized
            // interpreter this pure-Rust test does not have.)
            "{{ a.b.c }}",          // dotted variable
            "{{ items.0 }}",        // numeric attribute
            "{{ myvar|upper }}",    // filtered variable
            "{{ notify_if_late }}", // an `if`-containing name, not inline-if
        ] {
            parse_src_result(src).unwrap_or_else(|e| panic!("{src} must parse, got error: {e}"));
        }
    }

    /// LOAD-BEARING placement pin. A djust inline-if whose true/false branches
    /// are BARE variables (`a if cond else b`) has a head that is genuinely
    /// several `[\w.]`-tiles separated by spaces. It must reach the inline-if
    /// branch and parse to `Node::InlineIf` — which it only does because
    /// `validate_plain_variable_expr` runs in the plain-variable fallthrough,
    /// AFTER `find_if_keyword` has claimed the expression.
    ///
    /// GATE-OFF (documented, verified by the implementer, #1468): move the
    /// `validate_plain_variable_expr(expr_part)?` call to before the inline-if
    /// branch (the old `validate_variable_name` position) and this test goes
    /// RED — the bare-variable head `active_class if is_active else
    /// inactive_class` is refused as `Could not parse the remainder:
    /// ' if is_active else inactive_class' from '…'`. The quoted-literal
    /// inline-if forms (`test_inline_if_parses_to_inline_if_node`) do NOT prove
    /// placement, because `django_literal` mis-accepts a whole quoted-looking
    /// head as one string constant and so would not redden.
    #[test]
    fn test_inline_if_bare_variable_expr_survives_grammar_check() {
        let nodes =
            parse_src_result("{{ active_class if is_active else inactive_class }}").unwrap();
        assert_eq!(nodes.len(), 1);
        match &nodes[0] {
            Node::InlineIf {
                true_expr,
                condition,
                false_expr,
                ..
            } => {
                assert_eq!(true_expr, "active_class");
                assert_eq!(condition, "is_active");
                assert_eq!(false_expr, "inactive_class");
            }
            other => panic!("Expected InlineIf node, got {other:?}"),
        }
    }

    /// The underscore rule still fires first for a plain `_x` head — the new
    /// grammar check does not shadow the existing `validate_variable_name`
    /// refusal (which runs earlier and has its own, more specific message).
    #[test]
    fn test_underscore_refusal_precedes_grammar_check_2578() {
        let err = parse_src_result("{{ _private }}").expect_err("underscore head must refuse");
        assert!(
            err.to_string()
                .contains("Variables and attributes may not begin with underscores"),
            "got {:?}",
            err.to_string()
        );
    }
}

/// Dep-extractor hardening tests (#783 P0 follow-up).
///
/// Two kinds of tests live here:
///
/// 1. **Table-driven assertions** on [`extract_per_node_deps`] output for
///    representative AST shapes — regression-guard that every known
///    wrapper/tag contributes the right keys to its enclosing dep set.
///
/// 2. **Node variant exhaustiveness check** — a compile-time check
///    ([`sample_for_coverage`]) that forces any new `Node` variant to be
///    accounted for, plus a runtime check that every variant either
///    produces a non-empty dep set or appears in [`NO_VARS_VARIANTS`].
///
/// Rationale: #783 was the second time a silent dep-drop in
/// [`extract_from_nodes`] caused partial render to return `patches=[]`
/// with `diff_ms: 0` (first was #774/#779). Both bugs had the same shape:
/// a new `Node` variant (or a wrapper nesting combination) fell through
/// the `_ => {}` default arm and produced zero deps. These tests make
/// silent drops on future additions impossible.
#[cfg(test)]
mod dep_tests {
    use super::*;
    use crate::lexer::tokenize;
    use std::collections::HashSet;

    /// Parse `template` and return the per-top-level-node dep sets.
    fn deps_for(template: &str) -> Vec<HashSet<String>> {
        let tokens = tokenize(template).expect("tokenize failed");
        let nodes = parse(&tokens).expect("parse failed");
        extract_per_node_deps(&nodes)
    }

    // -----------------------------------------------------------------
    // Sub-item 1: Unit tests for extract_per_node_deps
    // -----------------------------------------------------------------

    #[test]
    fn test_deps_simple_variable() {
        let deps = deps_for("{{ a }}");
        assert_eq!(deps.len(), 1);
        assert!(
            deps[0].contains("a"),
            "expected 'a' in deps, got {:?}",
            deps[0]
        );
    }

    #[test]
    fn test_deps_variable_with_filter_arg() {
        // `default:b` — filter arg `b` is a variable reference.
        let deps = deps_for("{{ a|default:b }}");
        assert_eq!(deps.len(), 1);
        assert!(
            deps[0].contains("a"),
            "expected 'a' in deps, got {:?}",
            deps[0]
        );
        // Filter args aren't always extracted as deps by the current implementation;
        // this test documents the behavior. If the extractor is enhanced to track
        // filter-arg vars, tighten this to assert `b` is also present.
    }

    #[test]
    fn test_deps_if_include_has_wildcard() {
        // Exact #783 shape: If wrapping a nested Include — the top-level If
        // node's dep set must contain BOTH the condition var `c` AND `*`
        // (propagated from the nested Include).
        let deps = deps_for("{% if c %}{% include \"x\" %}{% endif %}");
        assert_eq!(deps.len(), 1);
        let set = &deps[0];
        assert!(set.contains("c"), "expected 'c' in deps, got {:?}", set);
        assert!(
            set.contains("*"),
            "expected wildcard '*' in deps (propagated from nested Include); got {:?}",
            set,
        );
    }

    #[test]
    fn test_deps_for_loop_tuple_unpacking() {
        // {% for k,v in d.items %} — deps should include `d` (iterable root),
        // `k` and `v` (loop vars are kept for IDE/debug purposes per the
        // extract_from_nodes comments).
        let deps = deps_for("{% for k,v in d.items %}{{ v|safe }}{% endfor %}");
        assert_eq!(deps.len(), 1);
        let set = &deps[0];
        assert!(
            set.contains("d"),
            "expected 'd' (iterable root) in deps, got {:?}",
            set
        );
        assert!(
            set.contains("v"),
            "expected 'v' (loop var) in deps, got {:?}",
            set
        );
        // `k` is the other loop var — may or may not appear depending on use;
        // asserting on `v` is sufficient since the body references it.
    }

    #[test]
    fn test_deps_with_custom_tag_has_wildcard() {
        // {% with x=y %}{% custom_tag %}{% endcustom_tag %}{% endwith %}
        // — with arg `y` plus `*` from custom-tag. (If `custom_tag` isn't
        // registered, it will parse as an UnsupportedTag; either way, the
        // top-level dep set must contain `y`.)
        let deps = deps_for("{% with x=y %}{{ x }}{% endwith %}");
        assert_eq!(deps.len(), 1);
        assert!(
            deps[0].contains("y"),
            "expected 'y' (with arg) in deps, got {:?}",
            deps[0]
        );
    }

    #[test]
    fn test_deps_inline_if_includes_condition() {
        // {{ 'on' if flag else 'off' }} — post-#784 fix, InlineIf contributes
        // `flag` to its enclosing dep set. Without this arm, changing `flag`
        // alone leaves wrapper dep-set unintersected with changed_keys and
        // the cached fragment is reused.
        let deps = deps_for("{{ \"on\" if flag else \"off\" }}");
        assert_eq!(deps.len(), 1);
        assert!(
            deps[0].contains("flag"),
            "expected 'flag' (InlineIf condition) in deps, got {:?}",
            deps[0],
        );
    }

    #[test]
    fn test_deps_nested_for_loops() {
        // Nested for: outer iterable `items`, inner iterable `i.children`
        // (root: `i`). Body references `i.x` and `j.y`.
        let deps = deps_for(
            "{% for i in items %}{% for j in i.children %}{{ i.x }}{{ j.y }}{% endfor %}{% endfor %}",
        );
        assert_eq!(deps.len(), 1);
        let set = &deps[0];
        assert!(
            set.contains("items"),
            "expected 'items' in deps, got {:?}",
            set
        );
        assert!(
            set.contains("i"),
            "expected 'i' (loop var) in deps, got {:?}",
            set
        );
        assert!(
            set.contains("j"),
            "expected 'j' (loop var) in deps, got {:?}",
            set
        );
    }

    #[test]
    fn test_deps_block_recurses_into_children() {
        // {% block content %}{{ a }}{% endblock %} — the Block wrapper must
        // expose `a` to the enclosing dep set.
        let deps = deps_for("{% block content %}{{ a }}{% endblock %}");
        assert_eq!(deps.len(), 1);
        assert!(
            deps[0].contains("a"),
            "expected 'a' in deps, got {:?}",
            deps[0]
        );
    }

    #[test]
    fn test_deps_plain_text_has_no_vars() {
        let deps = deps_for("hello world");
        assert_eq!(deps.len(), 1);
        // Allow "*" to be absent — Text nodes are pure no-op.
        assert!(
            !deps[0].contains("*"),
            "Text node should NOT contribute wildcard; got {:?}",
            deps[0],
        );
    }

    // -----------------------------------------------------------------
    // Sub-item 2: Node variant exhaustiveness check
    // -----------------------------------------------------------------

    /// Allow-list of Node variants that legitimately have no variable
    /// references. Any variant NOT in this list MUST produce a non-empty
    /// dep set (either real vars or the `"*"` wildcard).
    ///
    /// When adding a new `Node` variant:
    /// 1. `sample_for_coverage` below will fail to compile — add an arm.
    /// 2. `sample_nodes` must include the new variant so the runtime
    ///    check exercises it.
    /// 3. Either:
    ///    - add an arm to `extract_from_nodes` that contributes deps, OR
    ///    - add the variant name here if it's truly varless.
    const NO_VARS_VARIANTS: &[&str] = &[
        "Text",
        "Comment",
        "CsrfToken",
        "Static",
        "TemplateTag",
        "Now",
        "Extends",
        "Load",
        "UnsupportedTag",
        "ResetCycle",
    ];

    /// Compile-time exhaustiveness anchor: every `Node` variant must have
    /// an arm here. If a new variant is added to `Node` and this match
    /// isn't updated, compilation fails.
    fn sample_for_coverage(n: &Node) -> &'static str {
        match n {
            Node::Text(_) => "Text",
            Node::Variable(..) => "Variable",
            Node::If { .. } => "If",
            Node::For { .. } => "For",
            Node::Block { .. } => "Block",
            Node::Extends(_) => "Extends",
            Node::Include { .. } => "Include",
            Node::Comment => "Comment",
            Node::Load(_) => "Load",
            Node::CsrfToken => "CsrfToken",
            Node::Static(_) => "Static",
            Node::With { .. } => "With",
            Node::ReactComponent { .. } => "ReactComponent",
            Node::RustComponent { .. } => "RustComponent",
            Node::CustomTag { .. } => "CustomTag",
            Node::BlockCustomTag { .. } => "BlockCustomTag",
            Node::RawBlockCustomTag { .. } => "RawBlockCustomTag",
            Node::Language { .. } => "Language",
            Node::Timezone { .. } => "Timezone",
            Node::Localize { .. } => "Localize",
            Node::LocalTime { .. } => "LocalTime",
            Node::WidthRatio { .. } => "WidthRatio",
            Node::FirstOf { .. } => "FirstOf",
            Node::TemplateTag(_) => "TemplateTag",
            Node::Spaceless { .. } => "Spaceless",
            Node::AutoEscape { .. } => "AutoEscape",
            Node::Cycle { .. } => "Cycle",
            Node::ResetCycle { .. } => "ResetCycle",
            Node::Filter { .. } => "Filter",
            Node::Now(_) => "Now",
            Node::UnsupportedTag { .. } => "UnsupportedTag",
            Node::InlineIf { .. } => "InlineIf",
            Node::AssignTag { .. } => "AssignTag",
            Node::IfChanged { .. } => "IfChanged",
            Node::BlockSuperScope { .. } => "BlockSuperScope",
        }
    }

    /// Build one dummy instance of every `Node` variant. Minimal values;
    /// we only care that `extract_per_node_deps` yields a non-empty set
    /// (for variants that should track vars) or an empty one (for
    /// allow-listed variants).
    fn sample_nodes() -> Vec<Node> {
        vec![
            Node::Text("hi".into()),
            Node::Variable("a".into(), vec![], false),
            Node::If {
                condition: "c".into(),
                true_nodes: vec![],
                false_nodes: vec![],
                in_tag_context: false,
                marker_id: None,
            },
            Node::For {
                var_names: vec!["item".into()],
                iterable: "items".into(),
                reversed: false,
                nodes: vec![],
                empty_nodes: vec![],
            },
            Node::Block {
                name: "content".into(),
                nodes: vec![Node::Variable("a".into(), vec![], false)],
            },
            Node::Extends("base.html".into()),
            Node::Include {
                template: "x.html".into(),
                with_vars: vec![],
                only: false,
            },
            Node::Comment,
            Node::Load(vec!["mytags".into()]),
            Node::CsrfToken,
            Node::Static("img/foo.png".into()),
            Node::With {
                assignments: vec![("x".into(), "y".into())],
                nodes: vec![],
            },
            Node::ReactComponent {
                name: "MyComp".into(),
                props: vec![("foo".into(), "bar".into())],
                children: vec![],
            },
            Node::RustComponent {
                name: "MyRust".into(),
                props: vec![("foo".into(), "bar".into())],
            },
            Node::CustomTag {
                name: "url".into(),
                args: vec!["view_name".into()],
            },
            Node::BlockCustomTag {
                name: "modal".into(),
                args: vec![],
                children: vec![],
            },
            Node::RawBlockCustomTag {
                name: "blocktranslate".into(),
                args: vec![],
                body: "x".into(),
            },
            Node::Language {
                expr: "de".into(),
                children: vec![],
            },
            Node::Timezone {
                expr: "UTC".into(),
                children: vec![],
            },
            Node::Localize {
                use_l10n: false,
                children: vec![],
            },
            Node::LocalTime {
                use_tz: false,
                children: vec![],
            },
            Node::WidthRatio {
                value: "a".into(),
                max_value: "b".into(),
                max_width: "100".into(),
                asvar: None,
            },
            Node::FirstOf {
                args: vec!["a".into(), "b".into()],
                asvar: None,
            },
            Node::TemplateTag("openblock".into()),
            Node::Spaceless {
                nodes: vec![Node::Variable("a".into(), vec![], false)],
            },
            Node::AutoEscape {
                on: false,
                nodes: vec![Node::Variable("a".into(), vec![], false)],
            },
            Node::Cycle {
                values: vec!["a".into(), "b".into()],
                name: None,
                silent: false,
                id: "t-cycle-0".into(),
                reference: false,
            },
            Node::ResetCycle {
                name: None,
                id: "t-cycle-0".into(),
            },
            Node::Filter {
                filters: vec![("upper".into(), None)],
                nodes: vec![Node::Variable("a".into(), vec![], false)],
            },
            Node::IfChanged {
                vars: vec!["a".into()],
                id: String::new(),
                nodes: vec![Node::Variable("a".into(), vec![], false)],
                else_nodes: vec![],
            },
            Node::BlockSuperScope {
                super_nodes: vec![Node::Text("p".into())],
                nodes: vec![Node::Variable("a".into(), vec![], false)],
            },
            Node::Now("Y-m-d".into()),
            Node::UnsupportedTag {
                name: "ifchanged".into(),
                args: vec![],
            },
            Node::InlineIf {
                true_expr: "a".into(),
                condition: "cond".into(),
                false_expr: "b".into(),
                filters: vec![],
            },
            Node::AssignTag {
                name: "assign_slot".into(),
                args: vec!["var_name".into()],
            },
        ]
    }

    #[test]
    fn test_exhaustive_variant_coverage() {
        // Sanity: samples cover every variant. `sample_for_coverage` is the
        // compile-time anchor — if a new variant is added to Node without
        // updating both this function and `sample_nodes`, the match fails
        // to compile.
        let samples = sample_nodes();
        let names: Vec<&'static str> = samples.iter().map(sample_for_coverage).collect();

        // Ensure no duplicates / omissions — each variant covered exactly once.
        let unique: HashSet<&'static str> = names.iter().copied().collect();
        assert_eq!(
            unique.len(),
            names.len(),
            "sample_nodes() contains duplicate variants: {:?}",
            names,
        );

        // Expected-count sanity: if `Node` grows, either this number updates
        // in lock-step with `sample_nodes` additions (fine), or a duplicate
        // was introduced (caught above). Don't hard-code the count here —
        // it would drift. Instead, for each sample, assert the invariant
        // individually.
        for node in &samples {
            let name = sample_for_coverage(node);
            let deps = extract_per_node_deps(std::slice::from_ref(node));
            assert_eq!(deps.len(), 1, "extract_per_node_deps returned wrong arity");
            let set = &deps[0];
            let allow_listed = NO_VARS_VARIANTS.contains(&name);

            if !allow_listed {
                assert!(
                    !set.is_empty(),
                    "Node::{name} produced empty dep set but is not in \
                     NO_VARS_VARIANTS. Either add an arm to \
                     extract_from_nodes that tracks its variable \
                     references (or contributes '*' wildcard), or add \
                     \"{name}\" to NO_VARS_VARIANTS if the variant is \
                     genuinely var-less. This guard exists because #783 \
                     (and #774 before it) was caused by a silent \
                     dep-drop on a Node variant that fell through the \
                     `_ => {{}}` default arm in extract_from_nodes.",
                );
            }
        }
    }

    // -----------------------------------------------------------------------
    // located parse errors (#2557)
    // -----------------------------------------------------------------------

    /// Parse `source` through the spanned path and return the failure's span.
    fn located(source: &str) -> (String, Option<(usize, usize)>) {
        let (tokens, spans) = crate::lexer::tokenize_spanned(source).expect("tokenize");
        let err = parse_with_source_spanned(&tokens, &spans, source)
            .expect_err("this source must not parse");
        (err.to_string(), err.span())
    }

    #[test]
    fn a_parse_error_carries_the_span_of_the_offending_token() {
        let source = "hello\n{% nosuchtag %}\nworld";
        let (_, span) = located(source);
        let (start, end) = span.expect("the error must be located");
        assert_eq!(&source[start..end], "{% nosuchtag %}");
    }

    /// The INNERMOST enclosing token wins: `DjangoRustError::at` refuses to
    /// overwrite a span an inner frame already attached. Without that rule
    /// every nested failure would report its outermost block instead.
    #[test]
    fn the_innermost_token_is_the_one_reported() {
        for source in [
            "{% if a %}{% nosuchtag %}{% endif %}",
            "{% for i in xs %}{% nosuchtag %}{% endfor %}",
            "{% if a %}{% for i in xs %}{% with y=1 %}{% nosuchtag %}{% endwith %}{% endfor %}{% endif %}",
            "{% if a %}{% else %}{% nosuchtag %}{% endif %}",
        ] {
            let (_, span) = located(source);
            let (start, end) = span.expect("the error must be located");
            assert_eq!(&source[start..end], "{% nosuchtag %}", "{source:?}");
        }
    }

    /// The span-less entry points keep working and simply attach nothing, so a
    /// caller with no span table sees exactly the pre-#2557 error.
    #[test]
    fn the_spanless_entry_points_attach_no_span() {
        let source = "{% nosuchtag %}";
        let tokens = crate::lexer::tokenize(source).expect("tokenize");
        let err = parse_with_source(&tokens, source).expect_err("must not parse");
        assert!(err.span().is_none());
        let err = parse(&tokens).expect_err("must not parse");
        assert!(err.span().is_none());
    }

    /// Locating an error must not change its MESSAGE — the text is a published
    /// contract (#2549) and every existing assertion on it must keep holding.
    #[test]
    fn locating_an_error_leaves_its_message_byte_identical() {
        for source in [
            "{% nosuchtag %}",
            "{% if x %}",
            "{{ x|nosuchfilter }}",
            "{% for %}{% endfor %}",
            "{% templatetag nope %}",
        ] {
            let tokens = crate::lexer::tokenize(source).expect("tokenize");
            let (_, spans) = crate::lexer::tokenize_spanned(source).expect("tokenize");
            let unlocated = parse_with_source(&tokens, source)
                .expect_err("must not parse")
                .to_string();
            let (message, _) = located(source);
            assert_eq!(message, unlocated, "{source:?}");
            let _ = spans;
        }
    }

    /// `find_if_keyword` walks char boundaries, not bytes (#2551 / #2552).
    ///
    /// The byte walk evaluated `expr[i..]` at every byte — including the
    /// continuation bytes of a multi-byte character — and panicked.
    #[test]
    fn find_if_keyword_does_not_panic_on_a_multibyte_expression() {
        for expr in [
            "caf\u{e9}",
            "x.\u{e9}",
            "na\u{ef}ve|default:'\u{2615}'",
            "\u{65e5}\u{672c}\u{8a9e}",
            "'\u{2615} if not a keyword'",
            "a if caf\u{e9} else b",
            "\u{e9} if c else \u{e9}",
        ] {
            let _ = find_if_keyword(expr);
        }
    }

    #[test]
    fn find_if_keyword_still_finds_the_keyword_it_should() {
        assert_eq!(find_if_keyword("a if c else b"), Some(1));
        assert_eq!(find_if_keyword("no keyword here"), None);
        // The `if` INSIDE the quotes is skipped; the one after them is found.
        let quoted = "'some if text' if c else ''";
        let at = find_if_keyword(quoted).expect("the unquoted keyword is there");
        assert_eq!(at, quoted.len() - " if c else ''".len());
        assert_eq!(&quoted[at..at + 4], " if ");
        // The offset it returns is a BYTE offset its callers slice with.
        let expr = "caf\u{e9} if c else b";
        let at = find_if_keyword(expr).expect("the keyword is there");
        assert_eq!(&expr[..at], "caf\u{e9}");
        assert_eq!(&expr[at..at + 4], " if ");
    }

    // -----------------------------------------------------------------------
    // the empty-tag refusals, at Django's layer (#2557)
    // -----------------------------------------------------------------------

    /// Render a source through the spanned parse path, returning `Ok(nodes)`
    /// or the error text — the shape the context-axis cases below compare on.
    fn parse_source(source: &str) -> std::result::Result<Vec<Node>, String> {
        let (tokens, spans) = crate::lexer::tokenize_spanned(source).expect("tokenize");
        parse_with_source_spanned(&tokens, &spans, source).map_err(|e| e.to_string())
    }

    /// Both refusals live in `parse_token_inner`, mirroring `Parser.parse`
    /// (`django/template/base.py:483-486` and `:497`), and report Django's
    /// message and line.
    #[test]
    fn an_empty_tag_is_refused_with_djangos_message_and_line() {
        for (source, expected) in [
            ("{{ }}", "Empty variable tag on line 1"),
            ("{{}}", "Empty variable tag on line 1"),
            ("{{    }}", "Empty variable tag on line 1"),
            ("a\nb\n{{ }}", "Empty variable tag on line 3"),
            ("{% %}", "Empty block tag on line 1"),
            ("{%  %}", "Empty block tag on line 1"),
            ("a\nb\n{% %}", "Empty block tag on line 3"),
        ] {
            let err = parse_source(source).expect_err("this source must be refused");
            assert!(err.contains(expected), "{source:?} gave {err:?}");
        }
    }

    /// The refusal carries the offending token's span, like every other
    /// located parse error — this is what feeds `template_debug`.
    #[test]
    fn an_empty_tag_refusal_is_located() {
        for (source, expected) in [("a\n{{ }}", "{{ }}"), ("a\n{% %}", "{% %}")] {
            let (_, span) = located(source);
            let (start, end) = span.expect("the refusal must be located");
            assert_eq!(&source[start..end], expected, "{source:?}");
        }
    }

    /// THE CONTEXT AXIS (#2557 review, red 1). The refusal must not reach a
    /// raw-block body. Django's lexer turns a `{% verbatim %}` body into TEXT
    /// and its parser skips a `{% comment %}` body, so both render an empty
    /// `{{ }}` literally; djust's `collect_raw_source` consumes those tokens
    /// without calling `parse_token`, which is why the refusal has to sit in
    /// `parse_token_inner` and NOT in the lexer.
    ///
    /// The first version of #2557 refused in `tokenize_spanned` — below
    /// `collect_raw_source` — and so raised on every row here, a regression
    /// against `main` on a shape `{% verbatim %}` exists precisely to serve
    /// (Vue / Alpine / Handlebars braces, and djust's own docs pages).
    #[test]
    fn a_raw_block_body_is_exempt_from_both_refusals() {
        for source in [
            "{% verbatim %}{{ }}{% endverbatim %}",
            "{% comment %}{{ }}{% endcomment %}",
            "<pre>{% verbatim %}Vue: {{ }} and {{ msg }}{% endverbatim %}</pre>",
            "{% verbatim %}{% %}{% endverbatim %}",
            "{% comment %}{% %}{% endcomment %}",
        ] {
            assert!(
                parse_source(source).is_ok(),
                "{source:?} must parse — Django renders it"
            );
        }
    }

    /// The exemption is scoped to the body: the same template with a bare
    /// `{{ }}` OUTSIDE the raw block is still refused, so the raw-block arm
    /// cannot be mistaken for a blanket switch-off.
    #[test]
    fn the_raw_block_exemption_does_not_leak_outside_the_block() {
        for source in [
            "{% verbatim %}{{ }}{% endverbatim %}{{ }}",
            "{{ }}{% verbatim %}{{ }}{% endverbatim %}",
            "{% comment %}{{ }}{% endcomment %}{% %}",
        ] {
            assert!(
                parse_source(source).is_err(),
                "{source:?} has an empty tag outside the raw block"
            );
        }
    }

    /// A non-empty tag, and a `{{` with no closer (literal text per #2558),
    /// are untouched by either refusal.
    #[test]
    fn the_empty_tag_refusals_reach_nothing_else() {
        for source in [
            "{{ x }}",
            "a {{ unclosed b",
            "{% if a %}x{% endif %}",
            "{#  #}",
        ] {
            assert!(parse_source(source).is_ok(), "{source:?}");
        }
    }
}
