//! Template renderer that converts AST nodes to output strings

use crate::filters;
use crate::inheritance::TemplateLoader;
use crate::parser::Node;
use crate::registry::TagArg;
#[cfg(feature = "liveview")]
use djust_components::Component;
use djust_core::{Context, DjangoRustError, Encoded, EqClass, Result, Value};
use once_cell::sync::Lazy;
use regex::Regex;
use std::collections::HashSet;

/// Should this render emit the `<!--dj-if-->` VDOM markers (#2519)?
///
/// Two bodies, one seam. With the `liveview` feature (the default) the
/// answer is the render-time flag on the `Context` — `true` on the LiveView
/// path, `false` on the plain entries. Without the feature the engine is
/// the plain Django backend and the markers are never built, whatever the
/// `Context` says. The `Node::If` arm branches on this ONE helper so the
/// legacy placeholder (#295) and the boundary pair (#1358/#1832) cannot
/// drift apart (#1646).
#[cfg(feature = "liveview")]
#[inline]
fn dj_if_markers_enabled(ctx: &Context) -> bool {
    ctx.emit_dj_if_markers()
}

#[cfg(not(feature = "liveview"))]
#[inline]
fn dj_if_markers_enabled(_ctx: &Context) -> bool {
    false
}

/// Regex for {% spaceless %}: matches whitespace between > and <
static SPACELESS_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r">\s+<").unwrap());

/// Built-in filters whose output is already HTML-safe (escaped or
/// HTML-producing) and so must NOT be auto-escaped again when they are the
/// last filter in a chain. Single source of truth shared by the
/// `Node::Variable`, `Node::InlineIf`, and `get_value_safe` (`{% firstof %}` /
/// `{% cycle %}`) render paths — hoisted from three inline copies to prevent
/// parallel-path drift (CLAUDE.md #1646, issue #1692). Mirrors Django's
/// `is_safe`/`needs_autoescape` semantics. NAME-based check is additive: it
/// only ever marks MORE values safe, and only for these established names —
/// never under-escapes a plain/unknown filter's output.
/// Membership here is earned by ESCAPING THE INPUT INTERNALLY, not by producing
/// markup. Django's `is_safe=True` on a markup-producing filter always comes
/// paired with an `escape()` inside the filter body; a name added here whose
/// filter does not escape becomes an XSS sink for every template that uses it.
/// `linebreaks`/`linebreaksbr` were added in #2259 together with the escape in
/// `filters::linebreaks`/`linebreaksbr` — one change, never separable.
///
/// #2284 made that internal escape CONDITIONAL for the four names Django
/// registers `needs_autoescape=True` (`linebreaks`, `linebreaksbr`, `urlize`,
/// `urlizetrunc`), and the membership survives because the reason widened
/// rather than weakened. Each of those four now emits safe output under BOTH
/// arms of `autoescape = not input_was_safe`:
///
/// * `input_was_safe == false` — the filter escapes its input, as before. This
///   is every value that reached the filter without `mark_safe` / `|safe`,
///   i.e. all hostile input.
/// * `input_was_safe == true` — the escape is skipped, but only because the
///   context or an earlier safe filter already declared the value safe. Django
///   makes exactly this trade in `defaultfilters.linebreaks` et al.
///
/// So the invariant this list depends on — "nothing unescaped that was not
/// deliberately marked safe reaches the page through these names" — is
/// unchanged. A future `needs_autoescape` name added here must satisfy the same
/// two-arm reading, not just the first.
///
/// `linenumbers` was deliberately NOT here until #2291, on the argument that it
/// escapes per line where djust escaped the whole output and the two are
/// byte-identical because everything it adds is escape-invariant. That was
/// true, and beside the point: it holds only while the render-time escape
/// actually RUNS, and a later `|safe` suppresses exactly that. It now escapes
/// inside `add_linenumbers` and is listed below — the escape and the grant are
/// one change, and neither is correct without the other.
///
/// `escape` joined this list in the same commit that made it escape its own
/// input (#2281): Django's `escape_filter` is `conditional_escape` and returns
/// a `SafeString`, so the grant is earned inside the filter body — and, as with
/// every other name here, may not be present without it. It is the same defect
/// as `linenumbers` above, found by the same sweep.
///
/// `join` is deliberately NOT here even though Django's returns
/// `mark_safe(data)`, because Django's returns the value UNTOUCHED and unsafe
/// on the `TypeError` a non-iterable raises. A name in this list cannot express
/// "safe on one branch", so `join` reports per call through
/// `filters::builtin_produced_safe` instead.
///
/// `safeseq` LEFT this list in #2283. Django's `safeseq` is
/// `[mark_safe(obj) for obj in value]` — it marks the ITEMS and never the
/// sequence, so `{{ items|safeseq }}` rendered on its own escapes the list's
/// `repr` in Django while djust emitted it raw. The item-level grant it really
/// carries now lives in [`ITEM_SAFE_OUTPUT_FILTERS`].
const SAFE_OUTPUT_FILTERS: [&str; 10] = [
    "safe",
    "escape",
    "force_escape",
    "json_script",
    "urlize",
    "urlizetrunc",
    "unordered_list",
    "linebreaks",
    "linebreaksbr",
    // Joined the list with #2291: it now escapes each line inside the filter
    // body, which is what earns the grant — see `add_linenumbers`.
    "linenumbers",
];

/// Django's THIRD safety granularity: filters whose output is a sequence of
/// `SafeData` ITEMS while the sequence object itself is an ordinary list
/// (#2283).
///
/// `safeseq` is `[mark_safe(obj) for obj in value]` and `escapeseq` is
/// `[conditional_escape(obj) for obj in value]`. Neither calls `mark_safe` on
/// the list, so:
///
/// * rendering the sequence directly escapes its `repr` — the sequence is not
///   `SafeData`, which is why neither name belongs in [`SAFE_OUTPUT_FILTERS`];
/// * `join` and `unordered_list` DO see the grant, because they are the two
///   built-ins that `conditional_escape` per item rather than escaping their
///   whole output.
///
/// That is the entire observable difference, and it is what makes
/// `{{ items|safeseq|join:", " }}` — the documented reason `safeseq` exists —
/// emit its items live while `{{ items|safeseq }}` does not.
const ITEM_SAFE_OUTPUT_FILTERS: [&str; 2] = ["safeseq", "escapeseq"];

/// Filters that hand back the SAME item objects they were given, so an
/// item-level grant survives them — `{{ l|safeseq|slice:":3"|join:"" }}` is
/// live in Django because `slice` returns the very `SafeString`s `safeseq`
/// made.
///
/// Deliberately SHORT. Anything not named here drops the grant, which is the
/// escaping direction: a filter that rebuilds items (`make_list`, which splits
/// the sequence's `repr` into fresh plain characters) must not inherit it.
///
/// `dictsort` / `dictsortreversed` were here for one review round and are a
/// worked example of why the list stays short. They preserve item identity in
/// Django — but only on the path Django reaches, and Django's body is
/// `except (AttributeError, TypeError): return ""`, so for a list of STRINGS
/// (every quoted key) it destroys the sequence entirely. djust's `dictsort`
/// does not reproduce that failure, so it returned the list intact and carried
/// the grant onto items Django had already thrown away:
/// `{{ hostile|safeseq|dictsort:"x"|join:"" }}` was LIVE in djust and `''` in
/// Django, on data nothing had ever marked safe — 32 such cells across the two
/// names, ten argument spellings and both consumers.
///
/// Only a bare-integer key (`dictsort:0`) reaches Django's sorting path for a
/// list of strings, so the grant this bought was worth ~nothing and cost an
/// XSS.
///
/// `filters::dictsort_resolve_all` has since given `dictsort` Django's
/// `except (AttributeError, TypeError): return ""` branch, which retires the
/// class at its root — the sequence Django discarded is discarded here too, in
/// BOTH chain orders rather than only `safeseq|dictsort`. That makes re-adding
/// these names *permissible* and not *necessary*, so they stay out: the only
/// cell it would buy is `{{ l|safeseq|dictsort:0|join }}`, and the residual
/// divergence there is djust escaping where Django does not — the safe
/// direction, and unchanged from before #2283.
const ITEM_SAFETY_PRESERVING_FILTERS: [&str; 1] = ["slice"];

/// Does the value this filter produced have safe ITEMS?
///
/// The companion of [`filter_output_is_safe`] for the item granularity, and
/// like it, ASSIGNED per filter rather than OR-ed over the chain — a filter
/// that is neither a producer nor a preserver re-taints the items, exactly as
/// a plain filter re-taints the container.
fn filter_output_items_are_safe(
    filter_name: &str,
    input_items_were_safe: bool,
    input_was_safe: bool,
) -> bool {
    // `!input_was_safe` is Django's collapse, not a hedge. `safeseq` builds a
    // list of `SafeString`s; `FilterExpression.resolve` then applies the
    // `is_safe=True` arm, and `mark_safe(list)` is `SafeString(str(list))` — a
    // STRING of the list's repr. So when the INPUT was already safe, Django's
    // value stops being a sequence at all and the item-level grant is gone
    // with it; only when the input was unsafe does the plain list of safe
    // items survive to reach `join` / `unordered_list`. Granting items when
    // the input was safe made `{{ l|safe|safeseq|unordered_list }}` emit raw
    // `<` characters where Django escapes them.
    (ITEM_SAFE_OUTPUT_FILTERS.contains(&filter_name) && !input_was_safe)
        || (input_items_were_safe && ITEM_SAFETY_PRESERVING_FILTERS.contains(&filter_name))
}

/// Django's `is_safe=True` built-in filters — the ones that **preserve** the
/// safety they were **given**.
///
/// This is a DIFFERENT PROPERTY from [`SAFE_OUTPUT_FILTERS`] and the two lists
/// must never be merged (#2274):
///
/// * `SAFE_OUTPUT_FILTERS` — "marks its own output safe UNCONDITIONALLY",
///   because the filter escapes its input internally and then emits markup of
///   its own. `urlize` is one; its output is safe whatever went in.
/// * this list — "returns safe output IF AND ONLY IF the input was already
///   safe". `lower` is one; `{{ p|lower }}` on hostile input is still escaped
///   at render time, and only `{{ p|safe|lower }}` comes out live.
///
/// A name may legitimately appear in both (`safe`, `linebreaks`, `urlize`, …):
/// Django registers them `is_safe=True` *and* they mark their own output, and
/// the unconditional arm simply wins first. The list below is therefore
/// Django's `is_safe=True` registry set VERBATIM — all 36 of them — which is
/// what makes it mechanically checkable rather than a judgement call.
/// `test_is_safe_set_matches_djangos_registry` in
/// `python/tests/test_safe_survives_is_safe_filter_2274.py` enumerates
/// `django.template.defaultfilters.register.filters` at test time and fails on
/// any drift in either direction, so a Django release that flips a flag is a
/// red test rather than a silent divergence.
///
/// NOT a licence to under-escape: this arm only ever fires when the value
/// reaching the filter was ALREADY exempt from escaping — either the context
/// marked it safe (`mark_safe()` in the view) or an earlier `|safe` /
/// safe-output filter did. Hostile input that was never marked safe is
/// unaffected by every name here.
const IS_SAFE_FILTERS: [&str; 36] = [
    "addslashes",
    "capfirst",
    "center",
    "escape",
    "escapeseq",
    "filesizeformat",
    "floatformat",
    "force_escape",
    "iriencode",
    "join",
    "json_script",
    "last",
    "linebreaks",
    "linebreaksbr",
    "linenumbers",
    "ljust",
    "lower",
    "phone2numeric",
    "pprint",
    "random",
    "rjust",
    "safe",
    "safeseq",
    "slice",
    "slugify",
    "stringformat",
    "striptags",
    "title",
    "truncatechars",
    "truncatechars_html",
    "truncatewords",
    "truncatewords_html",
    "unordered_list",
    "urlize",
    "urlizetrunc",
    "wordwrap",
];

/// Is the value a filter just produced exempt from auto-escaping?
///
/// **LAST filter wins.** Call this once per filter, assigning (never OR-ing)
/// the result, so a later plain filter re-taints — which is Django's rule:
/// `FilterExpression.resolve` marks the value safe only when THE FILTER IT JUST
/// RAN is `is_safe`, so `{{ p|linebreaks|upper }}` is escaped because `upper`
/// is registered `is_safe=False`.
///
/// `input_was_safe` is the safety of the value going IN — the seed is the
/// context's own `mark_safe` flag for the variable, and each iteration feeds
/// its own result forward. That is Django's second term, and its absence was
/// issue #2274: `django/template/base.py` reads
///
/// ```text
/// new_obj = func(obj, *arg_vals)
/// if getattr(func, "is_safe", False) and isinstance(obj, SafeData):
///     obj = mark_safe(new_obj)
/// ```
///
/// where `obj` is the INPUT. Without the input term `{{ p|safe|lower }}` came
/// out escaped — `|safe` was undone by the very next filter.
///
/// Extracted in #2259 because the three call sites had drifted and one of them
/// said in a comment that they had not: `get_value_safe` applied the name check
/// per filter (correct) while the `Node::Variable` and `Node::InlineIf` arms
/// applied it as `filters.iter().any(...)` over the WHOLE chain, which marks the
/// output safe when a safe filter appears anywhere — even in the middle. That
/// made `{{ p|urlize|upper }}` and `{{ p|safe|upper }}` diverge from Django on
/// an unmodified build, and adding `linebreaks` to the list above would have
/// widened the same divergence to a fourth name instead of leaving it where it
/// was. One helper, three callers, no room to drift again (#1646).
///
fn filter_output_is_safe(filter_name: &str, produced_safe: bool, input_was_safe: bool) -> bool {
    // `produced_safe` is a genuine runtime `SafeString` — a custom filter that
    // `mark_safe()`d its result without the static `is_safe=True` flag (#1660).
    produced_safe
        || SAFE_OUTPUT_FILTERS.contains(&filter_name)
        // Django's case 1 (#2274): `is_safe=True` AND the input was `SafeData`.
        // BOTH terms are load-bearing — dropping `input_was_safe` would mark
        // `{{ hostile|lower }}` safe and is a direct XSS.
        //
        // The same rule applied to a PROJECT filter (#2548): a custom filter's
        // `is_safe=True` is "keeps a safe input safe", never "makes the output
        // safe". Until #2548 the custom-filter term stood on its own, outside
        // this conjunction, so `{{ hostile|shout }}` came out raw for any
        // plain-return `is_safe=True` filter (and for `humanize`'s `intcomma`).
        // One arm for both name sources: a future fourth term has to pick a
        // side of `input_was_safe &&`, and the only terms outside it are the
        // two that EARN the grant — a runtime `SafeString`, or a built-in that
        // escapes internally.
        || (input_was_safe
            && (IS_SAFE_FILTERS.contains(&filter_name)
                || crate::filter_registry::is_custom_filter_safe(filter_name)))
}

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
/// The safety grant one `{% for %}` item carries, at the two granularities a
/// loop can bind (#2361).
///
/// `second` exists because `{% for k, v in d.items %}` binds the two halves of
/// a `(key, value)` pair to separate names, and only the VALUE half can carry
/// a mark — a dict key is never `mark_safe`d, and `{{ k }}` must stay escaped.
/// Nothing wider is modelled: a normalised sequence's items are dict values or
/// 2-tuples, and there is no third shape for a provenance lookup to describe.
#[derive(Clone, Copy, Default)]
struct ItemGrant {
    /// The item bound to a single loop variable.
    whole: bool,
    /// The SECOND component, when the item is a `(key, value)` pair.
    second: bool,
}

/// Per-ITEM safety for a `{% for %}` over a dict VIEW, looked up BY KEY (#2361).
///
/// # The two spellings this reconciles
///
/// `rust_bridge._collect_safe_keys` walks the context and writes one dotted
/// path per `SafeString` it finds, spelling a dict's paths **by key name** —
/// `{"a": mark_safe(…)}` under `p` becomes `p.a`. The loop's positional
/// mapping ([`Context::set_loop_mapping`]) instead asserts the loop variable
/// IS `<iterable>.<index>`, so for `{% for v in p.values %}` it would look for
/// `p.values.0`. Neither spelling is wrong; they simply never produce the same
/// string, so the mark was missed and the value escaped.
///
/// This resolves the grant the way the collector wrote it: item `i` came from
/// key `k`, so its grant lives at `<prefix>.<k>`. That is why the positional
/// mapping is still correctly refused for a normalised operand — and why this
/// does NOT reintroduce the #2334 collision it is refused for. That collision
/// is a POSITIONAL lookup landing on a NAMED path: give a dict a key spelled
/// `"1"` whose value is marked, and a by-index mapping resolves the SECOND
/// key's mark. Here the lookup is by name on both sides, so a key can only
/// ever resolve its own value's grant.
///
/// # Four narrowings, each the escaping direction
///
/// * **The operand carries no filter.** `iterable.contains('|')` means the
///   `Value` is a filter's output and its provenance is unknown.
/// * **The expression really is `<prefix>.values` / `<prefix>.items`, and
///   `<prefix>` really resolves to the `Value::Object` the view is of.** A
///   `DictView` reaching this arm any other way has no known prefix, so it
///   gets nothing.
/// * **A key containing a `.` is refused.** `_collect_safe_keys` writes
///   `f"{prefix}.{key}"`, so `p.a.b` is BOTH `{"a.b": …}` and
///   `{"a": {"b": …}}` and no lookup can tell them apart. Refusing escapes.
/// * **The granted item is a `Value::String`.** `mark_safe_keys` accumulates
///   and is never cleared (#2300), so a path marked in one render survives
///   into the next; a later render putting a container at that key must not
///   inherit the grant. The same narrowing, for the same reason, guards
///   [`Context::items_are_safe`].
fn dict_view_item_grants(
    iterable: &str,
    kind: djust_core::DictViewKind,
    items: &[Value],
    context: &Context,
) -> Vec<ItemGrant> {
    use djust_core::DictViewKind;

    // `Keys` grants nothing: a key is not a thing `mark_safe` can mark.
    let suffix = match kind {
        DictViewKind::Values => "values",
        DictViewKind::Items => "items",
        DictViewKind::Keys => return Vec::new(),
    };

    let expr = iterable.trim();
    if expr.contains('|') {
        return Vec::new();
    }
    let Some((prefix, last)) = expr.rsplit_once('.') else {
        return Vec::new();
    };
    if last != suffix {
        return Vec::new();
    }
    let Some(Value::Object(map)) = context.get(prefix) else {
        return Vec::new();
    };

    map.keys()
        .take(items.len())
        .enumerate()
        .map(|(i, key)| {
            let key_text = key.to_display_string();
            if key_text.contains('.') {
                return ItemGrant::default();
            }
            // `is_safe` rather than a raw `safe_keys` probe so a dict reached
            // through a loop alias resolves too: inside
            // `{% for row in rows %}`, `row.a` resolves to `rows.<i>.a`.
            if !context.is_safe(&format!("{prefix}.{key_text}")) {
                return ItemGrant::default();
            }
            match kind {
                DictViewKind::Values => ItemGrant {
                    whole: matches!(items.get(i), Some(Value::String(_))),
                    second: false,
                },
                // Each item is a 2-`Tuple` `(key, value)`. The PAIR is not
                // safe — `{{ x }}` over it renders a tuple repr Django
                // escapes — only its second element is.
                DictViewKind::Items => ItemGrant {
                    whole: false,
                    second: matches!(
                        items.get(i),
                        Some(Value::Tuple(parts)) if matches!(parts.get(1), Some(Value::String(_)))
                    ),
                },
                DictViewKind::Keys => ItemGrant::default(),
            }
        })
        .collect()
}

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
        | Node::RawBlockCustomTag { .. }
        | Node::Language { .. }
        | Node::Timezone { .. }
        | Node::Localize { .. }
        | Node::LocalTime { .. }
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

        match sibling_updates(node, active_ctx, loader)? {
            Some(effect) => {
                // Promote to owned context if we haven't already, then merge.
                if mutated.is_none() {
                    mutated = Some(active_ctx.clone());
                }
                if let Some(ctx) = mutated.as_mut() {
                    for binding in effect.bindings {
                        // `bind`, not `set` + `mark_safe`: `bind` REVOKES any
                        // stale grant on the name first, so a `{% … as x %}`
                        // landing on a name the context had marked cannot
                        // inherit that grant and emit its value RAW (#2361).
                        // The flag is per binding rather than a constant
                        // `false`: an assign handler's `Value` crosses PyO3
                        // with no safety channel and is honestly unsafe, while
                        // `{% firstof … as v %}` binds what Django binds —
                        // `render_value_in_context(...)`, a `SafeString`
                        // (#2355).
                        ctx.bind(binding.name, binding.value, binding.safe);
                    }
                }
                // A context-mutating tag emits no HTML — except a bridged
                // library tag, which may emit AND bind (#2547).
                output.push_str(&effect.html);
            }
            None => {
                output.push_str(&render_node_with_loader(node, active_ctx, loader)?);
            }
        }
    }

    Ok(output)
}

/// The context updates a node contributes to the siblings that FOLLOW it, or
/// `None` for a node that renders normally.
///
/// One definition for all three sibling-aware loops — `render_nodes_with_loader`,
/// `render_nodes_collecting`, `render_nodes_partial` — which carried three
/// hand-copied `Node::AssignTag` arms before #2355. Adding a second kind of
/// context-mutating node would have made that four copies of two arms each, so
/// the copies are retired rather than extended (CLAUDE.md #1646).
///
/// `Some(effect)` carries the bindings AND the HTML the node emits. For
/// Django's assignment tags and `{% … as var %}` the HTML is empty; a bridged
/// library tag (#2547) may do both — `{% counter %}` emits, `{% one_param 37
/// as out %}` binds, and Django's own node decides which — so the effect
/// carries both rather than the call sites assuming "binds ⇒ silent".
fn sibling_updates<L: TemplateLoader>(
    node: &Node,
    context: &Context,
    loader: Option<&L>,
) -> Result<Option<SiblingEffect>> {
    match node {
        // A bridged Django library tag that declared `RETURNS_BINDINGS`
        // (#2547). Both arms below call the SAME helper the standalone
        // `render_node_with_loader` arms call — the helper decides between
        // the bindings-returning and the plain registry call, so a
        // library tag renders identically whether or not it has a sibling
        // to hand its bindings to.
        Node::CustomTag { name, args } if crate::registry::tag_handler_returns_bindings(name) => {
            let (html, bindings) = call_custom_tag(name, args, context)?;
            Ok(Some(SiblingEffect { html, bindings }))
        }
        Node::BlockCustomTag {
            name,
            args,
            children,
        } if crate::registry::block_handler_returns_bindings(name) => {
            let (html, bindings) = call_block_custom_tag(name, args, children, context, loader)?;
            Ok(Some(SiblingEffect { html, bindings }))
        }
        Node::RawBlockCustomTag { name, args, body } => {
            // Every raw-block handler declares RETURNS_BINDINGS by
            // construction (the kind exists for the #2547 bridge), so there
            // is no non-bindings twin to shadow this arm (#2129).
            let (html, bindings) = call_raw_block_tag(name, args, body, context)?;
            Ok(Some(SiblingEffect { html, bindings }))
        }
        Node::Cycle {
            values,
            name: Some(cycle_name),
        } => {
            // `{% cycle … as c %}` (#2558): render the current operand AND
            // persist the named state so `{% cycle c %}` advances. First use
            // seeds the state; later uses read it.
            let (state_key, vals_key) = cycle_state_keys(cycle_name);
            let idx = match context.get(&state_key) {
                Some(Value::Integer(i)) => *i as usize,
                _ => 0,
            };
            let idx = idx % values.len();
            let (html, safe) = resolve_cycle_operand(&values[idx], context)?;
            let current = Value::String(html.clone());
            Ok(Some(SiblingEffect {
                html,
                bindings: vec![
                    SiblingBinding {
                        name: cycle_name.clone(),
                        value: current,
                        safe,
                    },
                    SiblingBinding {
                        name: state_key,
                        value: Value::Integer(((idx + 1) % values.len()) as i64),
                        safe: false,
                    },
                    SiblingBinding {
                        name: vals_key,
                        value: Value::List(
                            values
                                .iter()
                                .map(|v| Value::String(v.trim().to_string()))
                                .collect(),
                        ),
                        safe: false,
                    },
                ],
            }))
        }
        Node::Cycle { values, name: None }
            if values.len() == 1 && django_literal(values[0].trim()).is_none() =>
        {
            // `{% cycle c %}` — Django's named-cycle REFERENCE (#2558).
            // Advances the named cycle `c` when one is in flight; without
            // one, falls through to the historical behaviour (resolve `c`
            // as a variable → "" on a miss).
            let reference = values[0].trim();
            match advance_named_cycle(reference, context)? {
                Some((html, bindings)) => Ok(Some(SiblingEffect { html, bindings })),
                None => Ok(None),
            }
        }
        Node::AssignTag { name, args } => {
            // Resolve variable references in args, mirroring only the JSON
            // *encoding* of `Node::CustomTag` (structured list/object values
            // survive as JSON instead of collapsing to "[List]"). NB: the
            // *resolution mechanism* is not identical — `CustomTag` uses
            // `get_value` (filter-aware, e.g. `x|upper`), whereas
            // `resolve_tag_arg` uses `resolve_tag_operand`. Keyword/name
            // operands the handler declares literal (RESOLVE_ARG_POSITIONS)
            // are passed raw (#2041).
            let resolved_args = plain_args(resolve_assign_tag_args(name, args, context));
            let context_map = context.to_hashmap();
            // Forward the raw-Python sidecar so assign handlers can reach
            // Python-only context (request, view) the same way
            // `Node::CustomTag` handlers do (#1167).
            let raw_py = context.raw_py_objects();
            let updates = crate::registry::call_assign_handler_with_py_sidecar(
                name,
                &resolved_args,
                &context_map,
                raw_py,
            )
            .map_err(|e| {
                DjangoRustError::TemplateError(format!("Assign tag '{name}' error: {e}"))
            })?;
            Ok(Some(SiblingEffect::silent(
                updates
                    .into_iter()
                    .map(|(name, value)| SiblingBinding {
                        name,
                        value,
                        safe: false,
                    })
                    .collect(),
            )))
        }
        // `{% widthratio a b c as name %}` and `{% firstof a b as name %}`
        // (#2355). Django binds the SAME string it would otherwise have
        // rendered — already escaped, for `firstof` — so the two forms cannot
        // disagree about the value, and the computation is the one function
        // the render arm calls.
        Node::WidthRatio {
            value,
            max_value,
            max_width,
            asvar: Some(name),
        } => Ok(Some(SiblingEffect::silent(vec![SiblingBinding {
            name: name.clone(),
            value: Value::String(width_ratio(value, max_value, max_width, context)?),
            // Django binds `str(round(...))` — a PLAIN `str`, not a
            // `SafeString`. Measured, and it differs from `firstof` below.
            safe: false,
        }]))),
        Node::FirstOf {
            args,
            asvar: Some(name),
        } => Ok(Some(SiblingEffect::silent(vec![SiblingBinding {
            name: name.clone(),
            value: Value::String(first_of(args, context)?.unwrap_or_default()),
            // `FirstOfNode` binds `render_value_in_context(...)`, which is a
            // `SafeString` — measured, not assumed. Without the grant
            // `{{ v }}` escapes an already-escaped string and renders
            // `&amp;lt;b&amp;gt;` where Django renders `&lt;b&gt;`.
            safe: true,
        }]))),
        _ => Ok(None),
    }
}

/// The context keys a NAMED cycle (`{% cycle … as c %}`) persists through
/// (#2558): the operand list and the next index, so a later bare
/// `{% cycle c %}` — Django's named-cycle reference — advances instead of
/// resolving `c` as an (empty) variable. Persisted through the SAME
/// sibling-binding channel `Node::AssignTag` uses.
fn cycle_state_keys(name: &str) -> (String, String) {
    (
        format!("__djust_cycle_state_{name}"),
        format!("__djust_cycle_vals_{name}"),
    )
}

/// Resolve ONE cycle operand — literals first (`django_literal`,
/// #2558: `_("Password")` is a translatable literal, not a variable), then
/// the failure-ignoring resolver, with the same escape rules the anonymous
/// path has always applied. Returns the rendered text and whether it is a
/// runtime `SafeString`.
fn resolve_cycle_operand(val: &str, context: &Context) -> Result<(String, bool)> {
    let val = val.trim();
    let (resolved, runtime_safe) = match django_literal(val) {
        Some((value, safe)) => (value, safe),
        None => get_value_safe_ignoring_failures(val, context)?,
    };
    let output = if matches!(resolved, Value::Missing) {
        // Django's `FilterExpression` miss under `ignore_failures` is `""`.
        String::new()
    } else if runtime_safe {
        resolved.to_string()
    } else {
        filters::html_escape(&resolved.to_string())
    };
    Ok((output, runtime_safe))
}

/// Advance a NAMED cycle: read the persisted operand list + index, resolve
/// the current operand, and return the html plus the bindings the NEXT
/// sibling needs (the cycle var → the value, the persisted state).
fn advance_named_cycle(
    name: &str,
    context: &Context,
) -> Result<Option<(String, Vec<SiblingBinding>)>> {
    let (state_key, vals_key) = cycle_state_keys(name);
    let Some(vals) = context.get(&vals_key) else {
        return Ok(None);
    };
    let list: Vec<String> = match vals {
        Value::List(items) => items
            .iter()
            .map(|v| match v {
                Value::String(s) => s.clone(),
                other => other.to_string(),
            })
            .collect(),
        _ => return Ok(None),
    };
    if list.is_empty() {
        return Ok(None);
    }
    let idx = match context.get(&state_key) {
        Some(Value::Integer(i)) => (*i as usize) % list.len(),
        _ => 0,
    };
    let (html, safe) = resolve_cycle_operand(&list[idx], context)?;
    let current = Value::String(html.clone());
    Ok(Some((
        html,
        vec![
            SiblingBinding {
                name: name.to_string(),
                value: current,
                safe,
            },
            SiblingBinding {
                name: state_key,
                value: Value::Integer(((idx + 1) % list.len()) as i64),
                safe: false,
            },
        ],
    )))
}

/// One name a context-mutating node binds for the siblings that follow it.
struct SiblingBinding {
    name: String,
    value: Value,
    /// Django bound a `SafeString` here, so `{{ name }}` must not re-escape.
    safe: bool,
}

/// What a sibling-aware node contributes: the HTML it emits and the names it
/// binds for the siblings that follow it (#2547).
struct SiblingEffect {
    html: String,
    bindings: Vec<SiblingBinding>,
}

impl SiblingEffect {
    /// Binds and emits nothing — Django's assignment tags and `{% … as var %}`.
    fn silent(bindings: Vec<SiblingBinding>) -> Self {
        Self {
            html: String::new(),
            bindings,
        }
    }
}

/// Call the inline handler for a [`Node::CustomTag`] — the ONE site that
/// resolves its args and picks the registry call (#2547).
///
/// A handler that declared `RETURNS_BINDINGS` (a bridged Django library tag)
/// is called through the bindings-returning entry, whose Python exceptions
/// cross WHOLE; every other handler takes the historical path, bytes
/// unchanged. Both `render_node_with_loader`'s standalone arm and
/// `sibling_updates`' binding arm come through here, so the two cannot
/// resolve an argument differently (#1646).
fn call_custom_tag(
    name: &str,
    args: &[String],
    context: &Context,
) -> Result<(String, Vec<SiblingBinding>)> {
    // Resolve any variable references in args. Scalars inline; lists and
    // objects are JSON-encoded through the shared `value_to_arg_string`
    // (#1646, #2042). This path keeps its own filter-aware `get_value`
    // resolver (e.g. `x|upper`), unlike the plain-context-lookup
    // `resolve_tag_arg` shared by AssignTag / BlockCustomTag. A handler may
    // DECLARE `RESOLVE_ARG_POSITIONS` and take some positions as literal
    // TOKENS instead (#2423); both rules apply, in that order, through
    // `resolve_custom_tag_args`.
    let resolved_args = resolve_custom_tag_args(name, args, context);
    let context_map = context.to_hashmap();
    // The optional raw-Python sidecar (``request``, ``view``, …) so handlers
    // like ``live_render`` (#1145) can reach Python objects from the parent's
    // render context. Existing handlers ignore extra keys.
    let raw_py = context.raw_py_objects();
    if crate::registry::tag_handler_returns_bindings(name) {
        let (html, bindings) = crate::registry::call_handler_with_bindings(
            name,
            &resolved_args,
            &context_map,
            raw_py,
            &context.safe_key_paths(),
        )?;
        return Ok((html, bindings.into_iter().map(sibling_binding).collect()));
    }
    let html =
        crate::registry::call_handler_with_py_sidecar(name, &resolved_args, &context_map, raw_py)
            .map_err(|e| {
            DjangoRustError::TemplateError(format!("Custom tag '{}' error: {}", name, e))
        })?;
    Ok((html, Vec::new()))
}

/// Resolve a [`Node::BlockCustomTag`]'s args, honouring the handler's
/// declared `RESOLVE_ARG_POSITIONS` policy (#2547).
///
/// The block twin of [`resolve_custom_tag_args`]. Until #2547 the block
/// registry resolved EVERY position unconditionally — the #1646 drift that
/// handed a bridged `{% div id=name %}` Django's parser the resolved VALUE of
/// `name`. A passthrough position is `TagArg::plain(token)` — no `SafeData`
/// grant, for the reason `resolve_custom_tag_args` documents at length (a
/// token is a NAME the handler will resolve, not bytes bound for the page).
/// Every block handler djust ships declares no policy → resolve all →
/// bytes unchanged.
fn resolve_block_tag_args(name: &str, args: &[String], context: &Context) -> Vec<TagArg> {
    let resolve_positions = crate::registry::block_handler_resolve_positions(name);
    args.iter()
        .enumerate()
        .map(|(position, arg)| {
            if resolve_positions
                .as_ref()
                .is_some_and(|declared| !declared.contains(&position))
            {
                return TagArg::plain(arg.clone());
            }
            // Through the SAME shared helper as `Node::AssignTag`, which
            // JSON-encodes structured values (#2042).
            TagArg::plain(resolve_tag_arg(arg, context))
        })
        .collect()
}

/// Render a [`Node::BlockCustomTag`]'s body and call its handler — the ONE
/// site for both the standalone arm and `sibling_updates` (#2547).
fn call_block_custom_tag<L: TemplateLoader>(
    name: &str,
    args: &[String],
    children: &[Node],
    context: &Context,
    loader: Option<&L>,
) -> Result<(String, Vec<SiblingBinding>)> {
    // Render children first to get block content
    let content = render_nodes_with_loader(children, context, loader)?;
    let resolved_args = resolve_block_tag_args(name, args, context);
    let context_map = context.to_hashmap();
    // Forward raw-Python sidecar so block handlers can reach Python-only
    // context (request, view) the same way ``Node::CustomTag`` handlers do
    // (#1167).
    let raw_py = context.raw_py_objects();
    if crate::registry::block_handler_returns_bindings(name) {
        let (html, bindings) = crate::registry::call_block_handler_with_bindings(
            name,
            &resolved_args,
            &content,
            &context_map,
            raw_py,
            &context.safe_key_paths(),
        )?;
        return Ok((html, bindings.into_iter().map(sibling_binding).collect()));
    }
    let html = crate::registry::call_block_handler_with_py_sidecar(
        name,
        &resolved_args,
        &content,
        &context_map,
        raw_py,
    )
    .map_err(|e| DjangoRustError::TemplateError(format!("Block tag '{}' error: {}", name, e)))?;
    Ok((html, Vec::new()))
}

/// Call a [`Node::RawBlockCustomTag`]'s handler — the ONE site for both the
/// standalone arm and `sibling_updates` (#2558).
///
/// The args cross as literal TOKENS (`TagArg::plain` — Django resolves them
/// itself, the `RESOLVE_ARG_POSITIONS = frozenset()` contract of every
/// bridged handler, #2547) and the body as the un-rendered source string.
fn call_raw_block_tag(
    name: &str,
    args: &[String],
    body: &str,
    context: &Context,
) -> Result<(String, Vec<SiblingBinding>)> {
    let plain: Vec<TagArg> = args.iter().map(|a| TagArg::plain(a.clone())).collect();
    let context_map = context.to_hashmap();
    let raw_py = context.raw_py_objects();
    let (html, bindings) = crate::registry::call_raw_block_handler_with_bindings(
        name,
        &plain,
        body,
        &context_map,
        raw_py,
        &context.safe_key_paths(),
    )?;
    Ok((html, bindings.into_iter().map(sibling_binding).collect()))
}

/// Resolve a scope node's operand (`"de"`, a variable, a filter chain) to
/// the value the Python scope hook receives (#2558): a string, or `None`.
///
/// Literals FIRST through the ONE literal recogniser (`django_literal`,
/// #2376): `{% language "de" %}` is a quoted literal, and the resolver
/// channels do not strip quotes — a miss here would hand the override an
/// empty string and silently deactivate instead of switching (fixed in the
/// first pass of this row after the probe showed `{% timezone
/// "Europe/Paris" %}` rendering UTC).
///
/// `None` and `""` are DIFFERENT operands to Django and stay different
/// here. `{% language None %}` resolves the context builtin to Python
/// `None`, and `translation.override(None)` DEACTIVATES (`get_language()`
/// is then `None`); a missing variable resolves to `string_if_invalid`,
/// `""`, and `translation.override("")` activates the fallback language
/// (`en-us` on the default settings). Measured on Django 5.2: `[None]`
/// against `[en-us]`. The first pass collapsed both to `""` and the Python
/// hook mapped `""` to `None` — two wrongs that happened to agree on the
/// scoreboard's one `{% language %}` cell. For `{% timezone %}` the split is
/// sharper still: `override(None)` deactivates while `override("")` raises
/// Django's own `ValueError` (`ZoneInfo keys must be normalized …`).
fn scope_operand_string(expr: &str, context: &Context) -> Option<String> {
    if let Some((value, _)) = django_literal(expr) {
        return Some(value_to_arg_string(&value));
    }
    match resolve_tag_operand_value(expr, context) {
        Some(Value::None) => None,
        Some(Value::Missing) | None => Some(String::new()),
        Some(value) => Some(value_to_arg_string(&value)),
    }
}

/// A scope hook's failure as the renderer reports it (#2558): a Python
/// exception raised INSIDE the hook (`ZoneInfoNotFoundError` for
/// `{% timezone "Bogus/Zone" %}`, Django's `ValueError` for `""`) crosses
/// WHOLE with its type, exactly as a bridged library tag's does (#2547);
/// only a registry failure is re-labelled as an engine error.
fn scope_hook_error(what: &str, err: DjangoRustError) -> DjangoRustError {
    match err {
        DjangoRustError::PythonException(_) => err,
        other => DjangoRustError::TemplateError(format!("{what} failed: {other}")),
    }
}

/// Render a [`Node::Language`] (#2558): enter the Python-side override,
/// render the children in Rust, exit on BOTH paths — a raising child must
/// not leak the language into the thread's next render.
fn render_language_scope<L: TemplateLoader>(
    expr: &str,
    children: &[Node],
    context: &Context,
    loader: Option<&L>,
) -> Result<String> {
    let lang = scope_operand_string(expr, context);
    let token = crate::registry::language_scope_enter(lang.as_deref())
        .map_err(|e| scope_hook_error("language scope enter", e))?;
    let result = render_nodes_with_loader(children, context, loader);
    if let Err(exit_err) = crate::registry::language_scope_exit(token.as_ref()) {
        if result.is_ok() {
            return Err(scope_hook_error("language scope exit", exit_err));
        }
    }
    result
}

/// Render a [`Node::Timezone`] (#2558) — the timezone twin of
/// [`render_language_scope`].
fn render_timezone_scope<L: TemplateLoader>(
    expr: &str,
    children: &[Node],
    context: &Context,
    loader: Option<&L>,
) -> Result<String> {
    let zone = scope_operand_string(expr, context);
    let token = crate::registry::timezone_scope_enter(zone.as_deref())
        .map_err(|e| scope_hook_error("timezone scope enter", e))?;
    let result = render_nodes_with_loader(children, context, loader);
    if let Err(exit_err) = crate::registry::timezone_scope_exit(token.as_ref()) {
        if result.is_ok() {
            return Err(scope_hook_error("timezone scope exit", exit_err));
        }
    }
    result
}

/// A registry [`crate::registry::HandlerBinding`] as a [`SiblingBinding`].
fn sibling_binding(binding: crate::registry::HandlerBinding) -> SiblingBinding {
    SiblingBinding {
        name: binding.name,
        value: binding.value,
        safe: binding.safe,
    }
}

/// Serialize a resolved template-tag argument [`Value`] to the string a
/// Python tag handler receives.
///
/// Scalars (`String`, `Integer`, `Float`, `Bool`, …) inline via
/// [`Value`]'s `Display`. **Lists and objects are JSON-encoded** so the
/// handler can recover the structured payload — a plain `to_string()`
/// would emit the opaque `"[List]"` / `"[Object]"` placeholder and lose
/// the data.
///
/// This is the single source of truth for arg encoding across ALL THREE
/// tag-dispatch paths — [`Node::CustomTag`], [`Node::AssignTag`] (via
/// [`resolve_tag_arg`]), and [`Node::BlockCustomTag`] (also via
/// [`resolve_tag_arg`]). Hoisted from two inline copies (one in
/// `resolve_tag_arg`, one in the `CustomTag` arm) plus a third path that
/// silently did NOT encode (`BlockCustomTag`) to retire the
/// `[List]`/`[Object]`-collapse parallel-path-drift class (CLAUDE.md
/// #1646, issue #2042).
fn value_to_arg_string(v: &Value) -> String {
    match v {
        // Tuple included: a structured arg must be JSON-encoded, not collapsed
        // to its Display form — the #2042 `[List]`-collapse class (#2203).
        //
        // And a dict VIEW (#2340), for exactly the same reason one variant
        // over: `{% regroup p.items by … %}` hands its source through here,
        // and before this arm existed the view fell to `_ => v.to_string()`
        // and the handler received the text `dict_items([…])` instead of the
        // rows. That is the #2042 collapse with a different placeholder — and
        // the compiler could not ask about it, because this match has a `_`.
        Value::List(_) | Value::Tuple(_) | Value::Object(_) | Value::DictView { .. } => {
            serde_json::to_string(v).unwrap_or_else(|_| v.to_string())
        }
        _ => v.to_string(),
    }
}

/// Resolve a [`Node::CustomTag`]'s args, honoring the handler's declared
/// `RESOLVE_ARG_POSITIONS` policy (#2423) and then #2416's `SafeData` rule.
///
/// The inline-tag twin of [`resolve_assign_tag_args`], and the ONE place the
/// two rules compose. They arrived from opposite directions — #2416 replaced
/// this arm's hand-rolled resolution with [`resolve_custom_tag_arg`], #2423
/// wrapped that same resolution in a policy check — so stating the order once,
/// here, is what keeps them from being re-derived differently at a second site.
///
/// # The order, and why the policy comes first
///
/// A declared-literal position short-circuits BEFORE any resolution. That is
/// the point of the policy: the handler asked for the token because resolution
/// is lossy for it. `{% render_slot slots.col.0.content %}` and a hostile
/// `{% render_slot p %}` are the same opaque string once flattened, and only
/// the un-resolved path can tell a slot's already-escaped content from a bare
/// context value.
///
/// # `safe` on the literal-passthrough path is FALSE, and that is a decision
///
/// The passthrough returns [`TagArg::plain`] — never `marked` — even though
/// the bytes are the template author's own, which is the exact argument #2416
/// uses to mint a `SafeString` for a RESOLVED quoted literal. The two cases
/// are not the same, and conflating them is the permissive direction:
///
/// * A resolved quoted literal is a **value**. `django_literal` hands back the
///   unescaped text and those exact bytes reach the page, which is why Django
///   marks them (`Variable.__init__` ends its quoted branch with
///   `mark_safe(unescape_string_literal(var))`). The grant describes bytes
///   that are already what the reader will see.
/// * A passthrough token is a **name**. `slots.col.0.content`, `p`,
///   `"slots.col.0"` — quotes included — are references the handler is about
///   to resolve into something else entirely. `SafeData` asserts "these bytes
///   are ready for the page"; that assertion is not true of a name, and it says
///   nothing whatever about the value the name resolves to. Mint it and a
///   handler that carries the marker forward onto its resolved value would let
///   a hostile `p` ride a grant issued for the one character `p` — which is
///   the class #2379 and #2421 closed, on the one handler (`render_slot`) that
///   made it framework-reachable with no `|safe` and no `mark_safe`.
///
/// Django has no rule to match here, because Django never hands a
/// `simple_tag` an un-resolved token at all — the policy is a djust extension
/// for handlers that must do their own resolution. With no reference
/// behaviour to copy, the escaping direction is the one to fail in.
///
/// It costs nothing: `render_slot`, the only handler that declares a policy,
/// does not read its argument's marker. It resolves the path itself and marks
/// its own RETURN, at the one exit where the path terminates in a slot
/// entry's `content` — a structural discriminator in Python, not a bit
/// carried across the boundary.
fn resolve_custom_tag_args(name: &str, args: &[String], context: &Context) -> Vec<TagArg> {
    let resolve_positions = crate::registry::tag_handler_resolve_positions(name);
    args.iter()
        .enumerate()
        .map(|(position, arg)| {
            if resolve_positions
                .as_ref()
                .is_some_and(|declared| !declared.contains(&position))
            {
                // A position the handler wants LITERAL. Passed exactly as the
                // template wrote it — quotes, dots and all — and with NO
                // grant. See the doc comment above for why the grant is
                // withheld from a token even though it is author-written.
                return TagArg::plain(arg.clone());
            }
            resolve_custom_tag_arg(arg, context)
        })
        .collect()
}

/// Resolve ONE [`Node::CustomTag`] operand into the `(text, SafeData)` pair
/// the Python handler receives (#2416).
///
/// # The two divergences this closes
///
/// Django's `SimpleNode.render` builds each operand with
/// `parser.compile_filter(bit)` and resolves it with
/// `FilterExpression.resolve(context)`, handing the handler the resolved
/// **object**. djust flattened every operand to a `String` through
/// [`value_to_arg_string`], which lost two things Django keeps:
///
/// 1. **The `SafeData` marker.** `{% ct_cond p %}` over
///    `p = mark_safe("<img …>")` — a handler whose body is the ordinary
///    defensive `conditional_escape(value)` — is a NO-OP in Django and the
///    markup renders; djust handed it a bare `str`, so the handler's own
///    escape fired. #2290's finding on the ARGUMENT side of the tag registry
///    rather than the filter registry.
///
/// 2. **A quoted literal's quotes.** Django's `Variable('"<b>"')` runs
///    `mark_safe(unescape_string_literal(var))`, so the literal loses its
///    surrounding quotes AND arrives as `SafeData`; djust passed the token
///    verbatim, so `{% ct_ident "<b>" %}` handed the handler the five
///    characters `"<b>"` and (since #2379) the return escape spelled them out
///    as `&quot;&lt;b&gt;&quot;`. This is not only a markup problem —
///    `{% t "post" %}` handed the handler `"post"` WITH the quotes, where
///    Django hands it `post`.
///
/// Both were MASKED before #2379: the marker was lost on the way in, the
/// bridge emitted the return raw on the way out, and the two wrongs cancelled.
///
/// # Why `get_value_safe` and not a second literal rule
///
/// [`get_value_safe`] already answers both questions — it ends at
/// [`django_literal`], the ONE place a bare token is recognized as a literal
/// and the ONE place the grant one carries is minted (#2376) — and it is the
/// same bool that decides whether `{{ p }}` escapes. Writing a literal rule
/// here would be a second mechanism shadowing the first (#2233); calling the
/// existing one is what makes `{% t "<b>" %}` and `{{ "<b>" }}` answer from
/// the same place by construction.
///
/// # What is deliberately NOT marked
///
/// * **A `key=value` composite.** The transported text is `key=<value>`, not
///   the value — marking it would mark the `key=` bytes too, and djust's
///   kwarg channel has no way to spell "the value half is safe". Django's
///   `simple_tag` passes a real kwarg, which this channel cannot represent at
///   all. Left over-escaping, and unchanged in every other respect.
/// * **Anything that is not a `Value::String`.** Django's `SafeData` is a
///   `str` subclass, so an `Integer`, `Float`, `Bool`, `None`, list or dict is
///   never `SafeData` — and a JSON-encoded container's brackets are structure
///   rather than markup. [`tag_arg`] enforces this, which is what keeps the
///   grant from widening past what Django grants.
/// * **An operand that did not resolve.** The raw token is kept, exactly as
///   before, with no grant.
fn resolve_custom_tag_arg(arg: &str, context: &Context) -> TagArg {
    let arg_trimmed = arg.trim();
    // The literal test comes FIRST, before the `key=value` split, exactly as it
    // did before this change: `{% t "a=b" %}` is one quoted literal and must
    // not be torn into a keyword operand.
    if strip_quotes(arg_trimmed).is_some() {
        return match get_value_safe(arg_trimmed, context) {
            Ok((value, safe)) => tag_arg(&value, safe),
            Err(_) => TagArg::plain(arg.to_string()),
        };
    }
    if let Some(eq_pos) = arg.find('=') {
        // Named parameter: key=value. Unchanged, and unmarked — see the doc
        // comment above for why the composite cannot carry the grant.
        let key = &arg[..eq_pos];
        let value = arg[eq_pos + 1..].trim();
        if strip_quotes(value).is_some() {
            return TagArg::plain(arg.to_string());
        }
        return match get_value(value, context) {
            Ok(resolved) => TagArg::plain(format!("{}={}", key, value_to_arg_string(&resolved))),
            Err(_) => TagArg::plain(arg.to_string()),
        };
    }
    match get_value_safe(arg_trimmed, context) {
        Ok((value, safe)) => tag_arg(&value, safe),
        Err(_) => TagArg::plain(arg.to_string()),
    }
}

/// A resolved [`Value`] plus its runtime-safe flag, as the tag-argument
/// channel transports them (#2416).
///
/// The `matches!` narrowing is the security boundary of the whole change and
/// is Django's own: `SafeString` is a `str` subclass, so ONLY a string can be
/// `SafeData`. Without it a `mark_safe`d LIST — which `Context::is_safe` can
/// legitimately answer `true` for — would hand the handler its JSON encoding
/// with a grant, i.e. mark bytes the renderer synthesized rather than bytes
/// anyone vouched for.
fn tag_arg(value: &Value, safe: bool) -> TagArg {
    let text = value_to_arg_string(value);
    if safe && matches!(value, Value::String(_)) {
        TagArg::marked(text)
    } else {
        TagArg::plain(text)
    }
}

/// Resolve an assign-tag argument against the render context.
///
/// - Quoted string literals are returned unchanged.
/// - `key=value` pairs resolve `value` against the context.
/// - Bare names present in the context inline their value; **lists and
///   objects are JSON-encoded** (via [`value_to_arg_string`]) so the
///   Python handler can recover the structured data — a plain
///   `to_string()` would emit the opaque `"[List]"` / `"[Object]"`
///   placeholder and lose the payload. This is what lets built-in
///   handlers like `regroup` receive the source list.
/// - Names **not** in the context are returned unchanged (kept literal),
///   so keyword operands such as regroup's `by` / `as` tokens and bare
///   attribute names survive rather than collapsing to an empty string.
/// - **Filter chains apply** (#2333) — see [`resolve_tag_operand`].
///
/// Shared by the [`Node::AssignTag`] and [`Node::BlockCustomTag`]
/// dispatch paths. [`Node::CustomTag`] keeps its own filter-aware
/// `get_value` resolver but shares the same [`value_to_arg_string`]
/// encoding.
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
        return match resolve_tag_operand(value, context) {
            Some(resolved) => format!("{key}={resolved}"),
            None => arg.to_string(),
        };
    }
    match resolve_tag_operand(arg_trimmed, context) {
        Some(resolved) => resolved,
        None => arg.to_string(),
    }
}

/// The one operand resolution for the assign-tag / block-custom-tag arg
/// channel. `None` means "did not resolve", and the caller keeps the raw
/// token — the contract that lets regroup's `by` / `as` / `<attr>` operands
/// through untouched.
///
/// **Filter-aware for a pipe-bearing expression** (#2333). Django compiles an
/// assign tag's source with `parser.compile_filter`, so
/// `{% regroup cities|dictsort:"country" by country as by_country %}` — close
/// to the canonical idiom, since Django's own `regroup` docs open by noting
/// the input usually needs sorting first — must apply the chain. Before this
/// the plain `context.get` asked for a variable literally NAMED
/// `cities|dictsort:"country"`, missed, and passed the template's own source
/// text to the handler, which decoded nothing: `{{ g|length }}` rendered `0`
/// and every `{% for %}` over the groups rendered nothing. That is the fourth
/// and last operand channel of the four #2325 enumerated (#1646).
///
/// The pipe is the guard, and it is load-bearing rather than a shortcut:
/// unlike the renderer's four sites, this channel's contract is "unresolved ⇒
/// pass the raw token", and `get_value`'s literal arms have no way to say
/// "unresolved" — they would answer `Bool(true)` for regroup's own `by`-style
/// keyword operand spelled `True`, `Integer(5)` for a bare `5`, and so turn a
/// literal token into a value. Routing only pipe-bearing expressions through
/// `get_value` changes exactly the cells that resolve to the raw source today.
fn resolve_tag_operand(expr: &str, context: &Context) -> Option<String> {
    resolve_tag_operand_value(expr, context).map(|value| value_to_arg_string(&value))
}

/// The resolution half of [`resolve_tag_operand`], before any encoding.
///
/// Split out (#2385) so the two arg channels — the historical
/// [`value_to_arg_string`] one and the value channel
/// [`value_channel_arg_string`] — resolve through ONE function and can only
/// ever differ in how they SERIALIZE the answer, never in what they resolve
/// (#1646). `None` still means "did not resolve", and every caller keeps the
/// raw token for it.
fn resolve_tag_operand_value(expr: &str, context: &Context) -> Option<Value> {
    if expr.contains('|') {
        // The runtime-safe flag `get_value_safe` also reports is discarded, as
        // at the renderer's four sites: this value is JSON-encoded and handed
        // to a Python handler whose output is escaped by the normal rules, so
        // a filtered operand can only ever be escaped at least as hard as the
        // raw token it replaces.
        return match get_value(expr, context) {
            // A miss anywhere in the chain leaves `Missing`, which is this
            // channel's "did not resolve" — the caller keeps the raw token,
            // exactly as an unknown bare name does today.
            Ok(Value::Missing) | Err(_) => None,
            Ok(value) => Some(value),
        };
    }
    // `Context::resolve`, NOT `Context::get` (#2368). `d.items` / `.keys` /
    // `.values` live in `resolve` — `dict_view` is only reachable from there,
    // which is where #2334 put it — so the pipe branch above saw a view and
    // this one did not. `{% regroup p.values by k as g %}` therefore fell to
    // the "unresolved ⇒ keep the raw token" contract, the handler decoded
    // nothing, and `{{ g|length }}` rendered `0`: silently, with no exception
    // and no warning. Same shape as #2333, one operand form over — that fix
    // made this channel FILTER-aware and left it dict-view-blind.
    //
    // `resolve` is strictly wider than `get`, and each thing it adds was
    // decided rather than inherited:
    //
    // * **the dict views** — the point of the change;
    // * **the raw-Python sidecar walk plus ADR-024's auto-call** — the same
    //   widening the pipe branch already has, since `get_value_safe` ends with
    //   a `context.resolve` fallback. A tag operand naming a model attribute
    //   resolved through `p|<filter>` and not through `p` alone, which is the
    //   #1646 split this closes;
    // * **`template_builtin`** — textually inert here. `Value::None` /
    //   `Bool(true)` / `Bool(false)` serialize back to `None` / `True` /
    //   `False`, byte-identical to the raw tokens the miss path would have
    //   passed.
    //
    // The keyword-operand hazard #2041's `RESOLVE_ARG_POSITIONS` exists to
    // prevent is unaffected: a handler that declares one (regroup declares
    // `{0}`) never routes its `by` / `<attr>` / `as` / `<var>` tokens through
    // this function at all — `resolve_assign_tag_args` passes them through raw
    // before this is reached. For a handler that declares none, every arg was
    // already being resolved through `get`; the only newly-shadowable spelling
    // is a name present ONLY in the raw-Python sidecar.
    //
    // `Err` is a miss, exactly as in the pipe branch: an exception raised
    // inside an auto-called method leaves the raw token rather than aborting
    // the render, which is what this channel's contract has always done for a
    // name it cannot answer.
    match context.resolve(expr) {
        Ok(Some(value)) => Some(value),
        Ok(None) | Err(_) => None,
    }
}

/// Serialize a resolved arg for a **value-channel** position — one the
/// handler declared in `RESOLVE_ARG_POSITIONS` (#2385).
///
/// Identical to [`value_to_arg_string`] except for [`Value::String`] and
/// [`Value::Bool`], which are JSON-encoded rather than inlined bare.
///
/// The quoting is not cosmetic; it is what makes the channel decodable at all.
/// This channel's contract is "unresolved ⇒ the caller keeps the raw token",
/// so a resolved string and an unresolved bare name arrived at the handler as
/// the SAME bytes: `{% regroup s by k as g %}` with `s = "ab"` and
/// `{% regroup nope by k as g %}` both handed it `ab` / `nope`. The handler
/// could only guess, and guessed by looking the text up as a context key —
/// which made `s = "q"` group over the UNRELATED variable `q` when one
/// existed, and rejected every real string source otherwise. Quoting the
/// string is the type tag that ambiguity needed.
///
/// Only `String` is treated this way, and the boundary is deliberate:
///
/// * `Decimal` / `BigInt` also serialize as JSON strings (their exact digits
///   would not survive a JSON number), so routing them through here would tell
///   the handler a `Decimal` is a sequence of characters — where Python raises
///   `TypeError: 'decimal.Decimal' object is not iterable`. Their `Display`
///   form is already unambiguous, so they keep it.
/// * `List` / `Tuple` / `Object` / `DictView` were ALREADY JSON — that is what
///   [`value_to_arg_string`] exists for — so nothing changes for them.
/// * `Bool` IS re-encoded, as of #2463, and the sentence that used to stand
///   here — "every other scalar's `Display` form (`42`, `True`, `None`,
///   `1.5`) is unambiguous against a bare name" — was false of exactly this
///   one. `42` and `1.5` ARE valid JSON, so the handler's `json.loads`
///   decodes them and refuses a non-iterable the way Django does; Python's
///   `True` / `False` are NOT, so `json.loads` raised, the handler took its
///   "this must be an unresolved bare name" branch, looked up a context key
///   called `True`, found nothing, and answered NO GROUPS. Django raises
///   `TypeError: 'bool' object is not iterable`. Encoding the bool as JSON
///   `true` / `false` is the same type tag the `String` arm above is, for the
///   same reason — a value that is not valid JSON is indistinguishable from a
///   token on this channel.
/// * `None` keeps its `Display` form. It is the one spelling where the
///   mis-decode is HARMLESS: the fallback lookup answers `None`, and `None`
///   is exactly what Django's `if obj_list is None` arm wants. Encoding it as
///   `null` would be more honest and change no output; left alone rather than
///   ridden along on a bug fix (#1079).
///
/// Reached only for a handler that DECLARES `RESOLVE_ARG_POSITIONS`, and only
/// at a position inside that set — the positions it declares are, by
/// construction, the ones it wants as VALUES rather than as tokens. A handler
/// that declares no policy is untouched.
fn value_channel_arg_string(v: &Value) -> String {
    match v {
        Value::String(s) => serde_json::to_string(s).unwrap_or_else(|_| s.clone()),
        // JSON `true` / `false`, not Python's `True` / `False` (#2463).
        Value::Bool(b) => if *b { "true" } else { "false" }.to_string(),
        // A carried COLLECTION, as its ITEMS (#2477/#2489).
        //
        // `{% regroup tags by k %}` over a `set` hands its source through
        // here, and a `Value::Encoded` fell to `value_to_arg_string`'s `_` arm
        // — so the handler received the text `{'a'}`, which is neither JSON
        // nor a variable name. `_decode_source` looked it up, missed, and
        // built ZERO groups where Django builds one. Django's `RegroupNode`
        // runs `groupby` over whatever the operand resolved to, which for a
        // set is its ELEMENTS.
        //
        // It only became reachable when the conversion learned to carry a
        // truthy collection: before that a `set` crossed as a `Value::String`
        // and took the arm above, so the handler decoded a string and iterated
        // its CHARACTERS — agreeing with Django by accident, on the same
        // mechanism that made `{{ p|length }}` count repr characters.
        //
        // HERE and not in `value_to_arg_string`, and that placement is the
        // whole of it: the two encoders have opposite consumers. This channel
        // is reached only for a position a handler DECLARED, which by
        // construction is one it wants as a VALUE and will decode. The general
        // encoder feeds a custom tag that renders its argument, and there the
        // display is what Django shows — `{% ct_ident tags %}` prints
        // `{'a'}`, not `["a"]`. Putting the arm in the general encoder moved
        // twenty-one `@ctag` cells the wrong way, which is how the split was
        // found.
        //
        // An `Encoded` with no items (a `datetime`, a `complex(0)`, a
        // zero-`__len__` class) falls through: there is nothing to encode, and
        // that cell is #2448's, unchanged here.
        Value::Encoded(e) if e.items.is_some() => {
            serde_json::to_string(&e.items).unwrap_or_else(|_| v.to_string())
        }
        _ => value_to_arg_string(v),
    }
}

/// [`resolve_tag_arg`] for a value-channel position (#2385).
///
/// Same resolution, [`value_channel_arg_string`] encoding. A quoted literal is
/// re-encoded rather than passed through verbatim, so that BOTH quote
/// spellings reach the handler as the same JSON — Django resolves
/// `{% regroup 'abc' … %}` and `{% regroup "abc" … %}` to the identical
/// `str`, and before this the single-quoted spelling was not valid JSON and
/// decoded as nothing.
fn resolve_tag_value_arg(arg: &str, context: &Context) -> String {
    let arg_trimmed = arg.trim();
    if let Some(literal) = strip_quotes(arg_trimmed) {
        return serde_json::to_string(literal).unwrap_or_else(|_| arg.to_string());
    }
    match resolve_tag_operand_value(arg_trimmed, context) {
        Some(value) => value_channel_arg_string(&value),
        None => arg.to_string(),
    }
}

/// The text inside a matching pair of single or double quotes, or `None`.
fn strip_quotes(token: &str) -> Option<&str> {
    let bytes = token.as_bytes();
    if bytes.len() >= 2
        && ((bytes[0] == b'"' && bytes[bytes.len() - 1] == b'"')
            || (bytes[0] == b'\'' && bytes[bytes.len() - 1] == b'\''))
    {
        return Some(&token[1..token.len() - 1]);
    }
    None
}

// Localize a bare number for output, leaving every other value untouched.
thread_local! {
    /// The `{% localize %}` scope stack (#2558): the innermost
    /// `{% localize on|off %}` block's flag, mirroring Django's
    /// `Context.use_l10n` flag. Entered/exited lexically by the
    /// `Node::Localize` arm in the same call frame.
    static USE_L10N_STACK: std::cell::RefCell<Vec<bool>> =
        const { std::cell::RefCell::new(Vec::new()) };
}

/// Is the innermost `{% localize %}` scope (if any) forcing l10n OFF?
fn use_l10n_forced_off() -> bool {
    USE_L10N_STACK.with(|s| s.borrow().last().copied() == Some(false))
}

/// One function rather than the expression inlined twice, so the two
/// variable-output sites cannot drift (#1646).
fn localize_if_number(value: &Value) -> String {
    match value {
        // Decimal included: a German site must localize it the same way it
        // localizes a float — which it did before #2214, when a Decimal simply
        // WAS a float (#2221).
        //
        // NOT "renders as bare digits", which an earlier version of this
        // comment claimed: over Django's >200-digit cutoff a Decimal renders in
        // scientific form. `localize_number_with` used to bail on anything
        // holding an `e`, so `1.230E-250` stayed `1.230e-250` where Django
        // gives `1,230e-250`. Fixed in #2242 by mirroring Django's own
        // scientific branch — the coefficient goes through the same
        // localisation path and the exponent passes through verbatim.
        // `BigInt` included for the same reason `Integer` is: Django's
        // `numberformat.format` groups an `int` regardless of width, so a
        // German site — or an English one with `USE_THOUSAND_SEPARATOR` —
        // renders `12.345.678.901.234.567.890`. It reached here as a `Float`
        // before #2260 and so was already being grouped, just from the wrong
        // digits.
        //
        // A NON-FINITE `Decimal` renders here where Django 5.2 raises, and
        // that is a DECIDED divergence (#2460), not an oversight. Django's
        // `numberformat.format` reaches
        //
        //     _, digits, exponent = number.as_tuple()
        //     if abs(exponent) + len(digits) > 200:      # <- raises
        //
        // and `Decimal("Infinity").as_tuple().exponent` is the STRING `'F'`
        // (`'n'` for NaN, `'N'` for sNaN), so `abs('F')` is an unhandled
        // `TypeError: bad operand type for abs(): 'str'` and a bare
        // `{{ p }}` 500s the page. Four measurements say it is Django's bug
        // and not its policy, and all four are asserted in
        // `python/tests/test_decimal_special_render_decision_2460.py`:
        //
        //   1. `float("inf")` renders `inf` in Django perfectly happily — the
        //      same mathematical value, refused only on the `Decimal` branch;
        //   2. the line that raises is a >200-DIGIT scientific-notation
        //      cutoff, a performance guard, not a validity check;
        //   3. `"{:f}".format(Decimal("Infinity"))` is `"Infinity"` — the
        //      answer Django's own `else` arm computes one line below the
        //      guard, and byte-identical to what djust emits here. djust is
        //      not inventing a rendering; it is producing Django's;
        //   4. Django itself puts those characters on the page one filter
        //      over: `floatformat`, `stringformat:"s"`, `safe`, `escape`,
        //      `force_escape`, `title` and `linebreaks` all render `Infinity`
        //      for the same value.
        //
        // Matching the refusal would turn a rendered page into a 500 for a
        // value an ordinary `DecimalField` aggregate can hold, in exchange for
        // parity with a crash. Decided the way #2429 decided `json_script` —
        // and more easily, since `json.dumps`' refusal there is at least a
        // documented contract, where `abs('F')` is documented nowhere.
        //
        // `localize_plain` already passes these through untouched: its
        // digits-and-a-point guard rejects `Infinity`/`NaN`, so no locale can
        // put a thousand separator inside one.
        Value::Integer(_) | Value::Float(_) | Value::Decimal(_) | Value::BigInt(_) => {
            // Inside `{% localize off %}` (#2558) the raw triple is used —
            // Django's `render_value_in_context` localizes only when
            // `context.use_l10n` is on. The date half of the same block
            // (bare `{{ date }}`) is the #2221 piece-3 residue.
            if use_l10n_forced_off() {
                djust_core::locale::localize_number_unlocalized(&value.to_string(), false)
            } else {
                djust_core::locale::localize_number(&value.to_string())
            }
        }
        _ => value.to_string(),
    }
}

/// The token/value channels of the ASSIGN and BLOCK paths, unchanged by
/// #2416, expressed as [`TagArg`]s so all three registries take one argument
/// type (#1646).
///
/// Every position is `plain` — no `SafeData` grant — and that is a decision
/// rather than an omission. Those two channels have a contract the
/// [`Node::CustomTag`] one does not: "unresolved ⇒ the caller keeps the raw
/// token", which is what lets `{% regroup … by … as … %}`'s keyword operands
/// through and what `RESOLVE_ARG_POSITIONS` (#2041) and the JSON-quoting value
/// channel (#2385) are built on. A quoted literal there is deliberately passed
/// VERBATIM, quotes included, because the quotes are the type tag that makes
/// the channel decodable. Marking a resolved string safe there without first
/// settling that quoting convention would be half a change; an ASSIGN
/// handler's return is also a `dict[str, Value]` with no safety channel of its
/// own (`SiblingBinding.safe` is a hard `false`), so the grant would be
/// unobservable even if it were carried. Tracked as a named limit rather than
/// a silent one.
fn plain_args(texts: Vec<String>) -> Vec<TagArg> {
    texts.into_iter().map(TagArg::plain).collect()
}

/// Resolve an [`Node::AssignTag`]'s args, honoring the handler's declared
/// `RESOLVE_ARG_POSITIONS` policy (#2041).
///
/// Django never resolves an assign tag's keyword/name operands (e.g.
/// `{% regroup <source> by <attr> as <var> %}`'s `by` / `<attr>` / `as` /
/// `<var>`) against the outer context — only the source *expression*. The
/// Rust engine historically resolved *every* arg via [`resolve_tag_arg`],
/// so a context variable named like the `<attr>` token (djust auto-exposes
/// public view attrs) shadowed the per-item lookup: `<attr>` arrived as
/// that variable's value instead of the literal attribute name, and the
/// grouping was silently wrong.
///
/// A handler opts into literal operands by declaring a
/// `RESOLVE_ARG_POSITIONS` set (`{0}` for `regroup` — resolve only the
/// source). Positions in the set are resolved via [`resolve_tag_arg`]
/// (JSON-encoding structured values); every other position is passed
/// through as a raw token. When the handler declares no policy the set is
/// `None` and every arg is resolved — the historical default, unchanged
/// for any future assign tag that doesn't opt in.
///
/// This is the single arg-resolution entry point for ALL FOUR assign-tag
/// dispatch sites (`render_nodes_with_loader`, `render_nodes_collecting`,
/// `render_nodes_partial`, and the individual `render_node_with_loader`
/// arm) — the #1646 parallel-path cure, so the operand-mask can never drift
/// between them.
fn resolve_assign_tag_args(name: &str, args: &[String], context: &Context) -> Vec<String> {
    let resolve_positions = crate::registry::assign_handler_resolve_positions(name);
    args.iter()
        .enumerate()
        .map(|(i, arg)| match &resolve_positions {
            // Handler opted into literal operands and this position is NOT
            // one it wants resolved: pass the raw token (Django parity —
            // no context shadowing possible).
            Some(positions) if !positions.contains(&i) => arg.clone(),
            // A position the handler DECLARED: a value channel, so a resolved
            // `String` arrives JSON-quoted and is distinguishable from the raw
            // token an unresolved operand leaves behind (#2385).
            Some(_) => resolve_tag_value_arg(arg, context),
            // No policy at all — resolve every arg the historical way. The
            // encoding is unchanged for these handlers.
            None => resolve_tag_arg(arg, context),
        })
        .collect()
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

        let frag = match sibling_updates(node, active_ctx, loader)? {
            Some(effect) => {
                if mutated.is_none() {
                    mutated = Some(active_ctx.clone());
                }
                if let Some(ctx) = mutated.as_mut() {
                    for binding in effect.bindings {
                        // `bind`, not `set` + `mark_safe`: `bind` REVOKES any
                        // stale grant on the name first, so a `{% … as x %}`
                        // landing on a name the context had marked cannot
                        // inherit that grant and emit its value RAW (#2361).
                        // The flag is per binding rather than a constant
                        // `false`: an assign handler's `Value` crosses PyO3
                        // with no safety channel and is honestly unsafe, while
                        // `{% firstof … as v %}` binds what Django binds —
                        // `render_value_in_context(...)`, a `SafeString`
                        // (#2355).
                        ctx.bind(binding.name, binding.value, binding.safe);
                    }
                }
                effect.html
            }
            None => render_node_with_loader(node, active_ctx, loader)?,
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
            let html = match sibling_updates(node, active_ctx, loader)? {
                Some(effect) => {
                    if mutated.is_none() {
                        mutated = Some(active_ctx.clone());
                    }
                    if let Some(ctx) = mutated.as_mut() {
                        for binding in effect.bindings {
                            // See the sibling loop above: `bind` revokes a
                            // stale grant on the name first, and the flag is
                            // per binding (#2361 + #2355).
                            ctx.bind(binding.name, binding.value, binding.safe);
                        }
                    }
                    effect.html
                }
                None => render_node_with_loader(node, active_ctx, loader)?,
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
            // A LITERAL is decided before any lookup, exactly as Django does
            // it (#2376). `Variable.__init__` runs at COMPILE time and a
            // quoted or numeric token never becomes a `lookups` tuple at all,
            // so a context key spelled `5` does NOT shadow `{{ 5 }}` — Django
            // renders `5` there, and measured against 5.2.16 djust rendered
            // the key's value. This arm had NO literal handling of any kind,
            // which is why `{{ "hello" }}`, `{{ 5 }}` and `{{ 5.5 }}` all
            // resolved through the context, missed, and rendered EMPTY.
            //
            // The bool is the grant: `Variable.__init__` `mark_safe`s the
            // quoted branch, so `{{ "<b>" }}` is live markup in Django. It
            // SEEDS the filter chain below rather than being OR-ed in at the
            // end, so `{{ "<b>"|upper }}` re-taints and comes out escaped —
            // which is what Django does, `upper` being `is_safe=False`.
            //
            // `resolve` (the miss path) tries the normal value-stack first,
            // then falls back to `getattr` on any Py<PyAny> sidecar attached
            // to the context (e.g. Django model instances). The `?`
            // propagates exceptions raised inside an auto-called method
            // (ADR-024 Django parity); lookup misses stay `Ok(None)`.
            let literal = django_literal(var_name);
            let literal_safe = literal.as_ref().is_some_and(|(_, safe)| *safe);
            let mut value = match literal {
                Some((value, _)) => value,
                None => context.resolve(var_name)?.unwrap_or(Value::Missing),
            };

            // Apply filters (pass context so date/time can read DATE_FORMAT etc.)
            //
            // `runtime_safe` tracks whether the LAST filter produced a runtime
            // ``SafeString`` (Django ``mark_safe`` / ``__html__``). A later
            // plain-returning filter re-taints it (resets to false), matching
            // Django's final-value escape semantics (#1660).
            //
            // SEEDED with the context's own safety, and fed forward through the
            // chain, because Django's rule reads the filter's INPUT (#2274).
            // The seed is why `{{ p|safe|lower }}` stays live and why
            // `{{ marked_safe_in_the_view|lower }}` does too — and, in the
            // other direction, why `{{ marked_safe_in_the_view|upper }}` is now
            // ESCAPED: `upper` is registered `is_safe=False` in Django
            // precisely because upper-casing `&lt;` yields `&LT;`, which every
            // browser still decodes to `<`.
            //
            // A quoted literal seeds it TRUE, and does so INSTEAD of the
            // context lookup rather than in addition to it (#2376): the token
            // is not a name, so `is_safe(name)` has nothing to answer about
            // and OR-ing the two would be a second mechanism shadowing the
            // first. `literal_safe` is false for a NUMBER — Django marks only
            // the quoted branch.
            let mut runtime_safe = if literal_safe {
                true
            } else {
                context.is_safe(var_name)
            };
            // Django's item granularity, seeded from the CONTEXT (#2287).
            // A view passing `[mark_safe(x), …]` has marked the ITEMS and not
            // the list, so `is_safe` above answers `false` for the container
            // while `join` / `unordered_list` must still emit each item live.
            // `Context::items_are_safe` is where every narrowing that keeps
            // this from out-permitting Django lives — read its doc comment
            // before widening it. Seeded `false` for anything that is not a
            // fully-marked sequence, which is the escaping direction.
            let mut items_safe = context.items_are_safe(var_name);
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
                let (new_value, produced_safe) = filters::apply_filter_full_safe(
                    filter_name,
                    &value,
                    stripped,
                    Some(context),
                    arg_was_quoted,
                    // Django's `needs_autoescape` input term (#2284), widened to
                    // its two granularities (#2283). Read BEFORE the assignment
                    // below, so both fields describe the value going IN — the
                    // same `obj` Django's `isinstance(obj, SafeData)` reads,
                    // plus whether its ELEMENTS are the ones marked.
                    filters::InputSafety {
                        container: runtime_safe,
                        items: items_safe,
                    },
                )?;
                value = new_value;
                // Captured BEFORE the reassignment below: both rules read the
                // safety of the value that went IN, and the item rule reads it
                // after `runtime_safe` has already been overwritten otherwise.
                let input_was_safe = runtime_safe;
                // ASSIGNED, not OR-ed: the LAST filter decides (#2259) — but the
                // value it is assigned FROM includes the previous iteration, which
                // is Django's `isinstance(obj, SafeData)` input term (#2274).
                runtime_safe = filter_output_is_safe(filter_name, produced_safe, input_was_safe);
                // The item granularity, tracked the same way (#2283). Only
                // `join` / `unordered_list` read it; only `safeseq` /
                // `escapeseq` produce it.
                items_safe = filter_output_items_are_safe(filter_name, items_safe, input_was_safe);
            }

            // #2221: localize a bare number on its way into the page, which is
            // exactly where Django does it (`render_value_in_context` calls
            // `localize`). Deliberately NOT in `impl Display for Value`, even
            // though that is where the rendering lives: `Display` is also the
            // lookup key for `{% if x in dict %}` (#2203), so a separator there
            // would turn `1234567` into `1,234,567` and break every such lookup
            // against a dict Python keyed without one.
            //
            // Only `Integer`, `Float` and `Decimal` — a `String` that happens
            // to hold digits is the user's own text, and a filter that already
            // returned a localized string (`floatformat`) must not be localized
            // twice. (`Decimal` since #2214; this comment said "Only Integer
            // and Float" at both sites for six review rounds — the same
            // comment-narrower-than-the-code shape that let the equality
            // widening leak past two reviews.)
            //
            // Applied at BOTH variable-output sites (`Node::Variable` and the
            // inline-if expression), which are byte-identical and were found
            // only by counting the matches rather than by reading the diff.
            let text = localize_if_number(&value);

            // Auto-escape unless:
            // 1. |safe is the last filter (matches Django behavior)
            // 2. The variable is marked safe in the context (like Django's SafeData)
            // 3. The LAST filter earned the grant: a built-in that escapes
            //    internally (`SAFE_OUTPUT_FILTERS`), or an `is_safe=True` filter
            //    — built-in OR custom (#1121) — whose INPUT was already safe
            //    (#2274, #2548). A static `is_safe=True` alone never grants it.
            // 4. The final value is a runtime ``SafeString`` — a custom filter
            //    ``mark_safe()``d its result at runtime without the static
            //    ``is_safe=True`` flag (#1660). Additive: only ever marks MORE
            //    values safe, and only when the LAST filter's output is safe.
            // `runtime_safe` now carries the name-based check too, applied
            // per filter rather than as an `any()` over the whole chain — see
            // `filter_output_is_safe`.
            // No trailing `|| context.is_safe(var_name)` any more (#2274): the
            // context flag is now the SEED of the loop above, so a filter can
            // re-taint it exactly as Django's `obj = new_obj` branch does.
            // OR-ing it back here would make the flag un-re-taintable and leave
            // `{{ marked_safe|upper }}` MORE permissive than Django.
            let is_safe = runtime_safe;
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
            let expr = if evaluate_condition_for_if(condition, context)? {
                true_expr.as_str()
            } else {
                false_expr.as_str()
            };

            // `get_value_safe`, not `get_value`: the bool beside the value is
            // what carries a quoted literal's grant (#2376). For every
            // NON-literal expression this arm can see — it never contains a
            // pipe, since the parser puts the filters in `filters` — the bool
            // it returns IS `context.is_safe(expr)`, which is what this line
            // read before, so nothing that resolved through the context
            // changes answer.
            let (mut value, seeded_safe) = get_value_safe(expr, context)?;

            // See the Variable arm: track the LAST filter's runtime safeness so
            // a custom filter that ``mark_safe()``s at runtime bypasses escaping
            // (#1660); a later plain filter re-taints. Seeded with the context's
            // own safety so the chain carries Django's input term (#2274).
            let mut runtime_safe = seeded_safe;
            // See the Variable arm: item-level safety, seeded from the context
            // (#2283, #2287) — the second of the three sites.
            let mut items_safe = context.items_are_safe(expr);
            for (filter_name, arg) in filters {
                let original = arg.as_deref();
                let arg_was_quoted = original.map(is_quoted_arg).unwrap_or(false);
                let stripped = original.map(crate::parser::strip_filter_arg_quotes);
                let (new_value, produced_safe) = filters::apply_filter_full_safe(
                    filter_name,
                    &value,
                    stripped,
                    Some(context),
                    arg_was_quoted,
                    // Django's `needs_autoescape` input term (#2284) — the
                    // second of the three sites, kept in step by construction.
                    filters::InputSafety {
                        container: runtime_safe,
                        items: items_safe,
                    },
                )?;
                value = new_value;
                // Captured BEFORE the reassignment below: both rules read the
                // safety of the value that went IN, and the item rule reads it
                // after `runtime_safe` has already been overwritten otherwise.
                let input_was_safe = runtime_safe;
                // ASSIGNED, not OR-ed: the LAST filter decides (#2259) — but the
                // value it is assigned FROM includes the previous iteration, which
                // is Django's `isinstance(obj, SafeData)` input term (#2274).
                runtime_safe = filter_output_is_safe(filter_name, produced_safe, input_was_safe);
                // The item granularity, tracked the same way (#2283). Only
                // `join` / `unordered_list` read it; only `safeseq` /
                // `escapeseq` produce it.
                items_safe = filter_output_items_are_safe(filter_name, items_safe, input_was_safe);
            }

            // #2221: localize a bare number on its way into the page, which is
            // exactly where Django does it (`render_value_in_context` calls
            // `localize`). Deliberately NOT in `impl Display for Value`, even
            // though that is where the rendering lives: `Display` is also the
            // lookup key for `{% if x in dict %}` (#2203), so a separator there
            // would turn `1234567` into `1,234,567` and break every such lookup
            // against a dict Python keyed without one.
            //
            // Only `Integer`, `Float` and `Decimal` — a `String` that happens
            // to hold digits is the user's own text, and a filter that already
            // returned a localized string (`floatformat`) must not be localized
            // twice. (`Decimal` since #2214; this comment said "Only Integer
            // and Float" at both sites for six review rounds — the same
            // comment-narrower-than-the-code shape that let the equality
            // widening leak past two reviews.)
            //
            // Applied at BOTH variable-output sites (`Node::Variable` and the
            // inline-if expression), which are byte-identical and were found
            // only by counting the matches rather than by reading the diff.
            let text = localize_if_number(&value);
            // Same shape as the Variable arm — see `filter_output_is_safe`.
            // Seeded, not OR-ed — see the Variable arm (#2274).
            let is_safe = runtime_safe;
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
            let condition_result = evaluate_condition_for_if(condition, context)?;
            // #2519: the plain-backend path (and the engine built without the
            // `liveview` feature) renders `{% if %}` exactly as Django does —
            // no placeholder, no boundary pair. One switch for both forms.
            let markers = dj_if_markers_enabled(context);

            // Render the body that fires (truthy/falsy branch).
            let body = if condition_result {
                render_nodes_with_loader(true_nodes, context, loader)?
            } else if false_nodes.is_empty() {
                if *in_tag_context || !markers {
                    // Inside an HTML attribute value: a comment node would produce
                    // malformed HTML (e.g. class="btn <!--dj-if-->"). Emit empty
                    // string instead. Fix for issue #380. Same answer when
                    // markers are off (#2519).
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
            if markers
                && !*in_tag_context
                && (nodes_contain_elements(true_nodes) || nodes_contain_elements(false_nodes))
            {
                if let Some(id) = marker_id {
                    // Append the per-iteration loop path (#1832) so an
                    // `{% if %}` rendered inside a `{% for %}` gets a UNIQUE
                    // id per iteration (the parser-assigned `if-<hash>-N` is
                    // the SAME compile-time ordinal for every iteration, so
                    // without this suffix the id is duplicated N times,
                    // producing unpairable MoveSubtree patches on re-render).
                    // Outside any loop the path is absent/empty, so the id is
                    // unchanged `if-<hash>-N` (backward compatible). Inside a
                    // loop iteration the open marker becomes
                    // `if-<hash>-N-<path>` (e.g. `if-<hash>-0-0`); the close
                    // marker stays `<!--/dj-if-->` (it carries no id). The id
                    // is treated as an opaque string by the Rust differ and
                    // the JS client — neither parses the `-N` structure.
                    let loop_path = match context.get("__djust_if_loop_path") {
                        Some(Value::String(s)) if !s.is_empty() => s.as_str(),
                        _ => "",
                    };
                    return Ok(format!(
                        "<!--dj-if id=\"{id}{loop_path}\"-->{body}<!--/dj-if-->"
                    ));
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
            // Through `get_value` — the one filter-aware expression resolver
            // — so `{% for x in p|slice:":2" %}` applies the filter chain
            // instead of looking up a variable literally NAMED `p|slice:":2"`,
            // missing, and rendering an empty loop (#2325). Django resolves
            // this operand with a `FilterExpression`, the same object `{{ }}`
            // uses, and the silent-empty-region failure that drift produced is
            // the worst shape a template engine has.
            //
            // `get_value` keeps the `Context::resolve` getattr walk that this
            // arm previously called directly (#806, `{% for x in user.orders %}`
            // over a DB relation) — it is the last arm of `get_value_safe`.
            //
            // The runtime-safe flag `get_value_safe` also reports is
            // deliberately DISCARDED: honouring it would emit loop items
            // unescaped, which is the one direction this fix must not move
            // (`{% for x in p|safe %}` over-escapes, as it did before).
            let iterable_value = get_value_ignoring_failures(iterable, context)?;

            // Python iterates a string by CHARACTER, and #2325's own repro
            // table needs it: `{% for x in p|upper %}` over `"ab"` is `AB` in
            // Django, and `upper`/`join`/`first`/`last` all hand this arm a
            // string. Without it the filter now resolves correctly and the
            // loop STILL renders nothing, which is the same silent-empty
            // symptom one step further along. Normalised here rather than as a
            // fourth match arm so the string shares the whole loop body —
            // `{% empty %}`, `reversed`, `{% cycle %}`, nested loops — instead
            // of growing a parallel copy of it (#1646).
            //
            // A dict iterates its KEYS, by the same argument (#2334): a
            // `Value::Object` otherwise fell to the `_ =>` arm below and
            // rendered the `{% empty %}` block, so `{% for k in d %}` — and,
            // once `d.items` resolves, everything reached through it — was the
            // same silent-empty region. `{{ d|length }}`, `{{ d|join }}` and
            // `{% if k in d %}` have all agreed with Django on a dict all
            // along; `{% for %}` was the one iteration sink that did not.
            //
            // `normalised` records that the sequence being iterated is NOT the
            // resolved value's own indexable elements. See the safe-key
            // mapping below, which must not be registered in that case.
            //
            // `derived_grants` is the per-ITEM safety a normalised sequence
            // carries (#2361). The positional loop mapping below is refused
            // for exactly these shapes, so without it a `mark_safe` value
            // reached through `d.values` / `d.items` had no channel at all and
            // came out escaped. It is derived from the operand's own
            // PROVENANCE — the dict key each item came from — never from its
            // position, which is what keeps the #2334 hostile-key collision
            // closed. Empty for a non-normalised operand, where the loop
            // mapping is the (legitimate) channel.
            let (iterable_value, normalised, derived_grants) = match iterable_value {
                Value::String(s) => (
                    Value::List(s.chars().map(|c| Value::String(c.to_string())).collect()),
                    true,
                    Vec::new(),
                ),
                Value::Object(map) => (
                    // Each key as the VALUE it is, not its text: an int key
                    // binds `Value::Integer` so `{{ k }}` renders `0` and
                    // `{% if k == 0 %}` is true (#2339).
                    Value::List(djust_core::object_key::dict_iteration_values(&map)),
                    true,
                    // A KEY carries no mark — only the value beside it can —
                    // so a bare dict loop grants nothing. Left empty rather
                    // than filled with `false`s: absent and all-false are the
                    // same answer, and `item_grant` reads a short vec as
                    // "no grant".
                    Vec::new(),
                ),
                // A dict VIEW iterates its own items (#2340). `normalised`
                // stays true for the same reason a dict's does: the loop is
                // iterating something built from the resolved value, not that
                // value's own indexable elements — which is exactly what the
                // safe-key mapping below must not assume.
                Value::DictView { kind, items } => {
                    let grants = dict_view_item_grants(iterable, kind, &items, context);
                    (Value::List(items), true, grants)
                }
                // A container with no `Value` variant — `set()`, `{'a'}`, a
                // `dict_keys`, a class whose `__len__` is 0, a falsy
                // `__iter__` class (#2466, #2477/#2489). This arm is Django's
                // `ForNode` transcribed:
                //
                // ```python
                // if not hasattr(values, "__len__"):
                //     values = list(values)
                // if len(values) < 1:
                //     -> the {% empty %} block
                // ```
                //
                // — so `len(o) == 0` renders `{% empty %}` WITHOUT asking
                // whether the object is iterable at all (a class with only a
                // zero `__len__` renders empty on Django too), and anything
                // else iterates the ITEMS the conversion enumerated.
                //
                // An `Encoded` with neither falls through to `other` and
                // reaches the refusal arm below, which is right for every
                // member that reaches it: a `datetime`, a `complex(0)`, a
                // `__bool__`-False class with no `__len__` — `list(o)` raises
                // for all of them. The message names `e.type_name`, so it is
                // CPython's own.
                //
                // The set of shapes normalised here is a SUPERSET of the set
                // `filters::iter_values` answers `Some` for, by exactly the
                // zero-`__len__`-and-not-iterable shape — the two questions
                // Django asks in two places (#2466). Pinned, with that one
                // exemption named, by `for_iterability_agrees_with_iter_values`.
                Value::Encoded(ref e) if e.len == Some(0) => {
                    (Value::List(Vec::new()), true, Vec::new())
                }
                Value::Encoded(ref e) if e.items.is_some() => {
                    // No safety grant on any item, exactly as the `Object`
                    // arm above: these came from an arbitrary Python object
                    // through `extract::<Value>()` and carry no mark, so
                    // over-escaping is the direction to fail in.
                    let items = e.items.clone().unwrap_or_default();
                    (Value::List(items), true, Vec::new())
                }
                other => (other, false, Vec::new()),
            };

            match iterable_value {
                Value::List(items) | Value::Tuple(items) => {
                    // If list is empty, render the {% empty %} block
                    if items.is_empty() {
                        return render_nodes_with_loader(empty_nodes, context, loader);
                    }

                    let mut output = String::new();
                    let mut ctx = context.clone();

                    // The SUBTREE half of `Context::bind`, hoisted out of the
                    // iteration (#2361/#2363). Every iteration binds the same
                    // names, so the shadowed OUTER grants each would clear are
                    // the same ones — clearing them once is identical in
                    // effect and turns an O(N·len(safe_keys)) scan into one.
                    // The per-item `set_safety` below is the O(1) half.
                    //
                    // Without this, `{% for p in hostile %}{{ p }}{% endfor %}`
                    // with `p` marked in the context emitted the hostile items
                    // RAW: the loop bound the value and inherited the stale
                    // by-name grant.
                    for var_name in var_names {
                        ctx.revoke_safe_subtree(var_name);
                    }

                    // `forloop` is about to become this loop's own dict, so
                    // whatever a context variable of that name was granted
                    // goes with it — the same argument, and the same call,
                    // that binding a loop variable makes one line up (#2402).
                    // `{% for a in p %}{{ forloop }}{% endfor %}` over a
                    // context carrying a marked `forloop` would otherwise
                    // answer `is_safe("forloop")` from the shadowed grant and
                    // emit this dict unescaped; the dict is engine-built, but
                    // the ALIAS half of the revoke is what additionally keeps
                    // `{% with q=forloop %}` from resolving through a stale
                    // `forloop -> <marked path>`. Over-escaping is the
                    // direction to fail in.
                    ctx.revoke_safe_subtree("forloop");

                    // Create an iterator with indices, reversing if needed
                    let items_vec = items;
                    let indices_and_items: Vec<(usize, Value)> = if *reversed {
                        items_vec.into_iter().enumerate().rev().collect()
                    } else {
                        items_vec.into_iter().enumerate().collect()
                    };

                    // Django's `forloop` (#2402). `ForNode.render` opens with
                    //
                    //     if "forloop" in context: parentloop = context["forloop"]
                    //     else: parentloop = {}
                    //
                    // and then, once the sequence is known non-empty, writes
                    // `context["forloop"] = {"parentloop": parentloop}` and
                    // updates six counters per iteration. Every one of those
                    // seven names was UNBOUND here, so `{{ forloop.counter }}`
                    // missed and rendered `string_if_invalid` — a numbered
                    // list with no numbers, `{% if forloop.first %}` never
                    // true, `{% if not forloop.last %},{% endif %}` a comma
                    // after every element. Silent under-render, the shape this
                    // area keeps producing (#2325, #2334, #2377).
                    //
                    // Read out of `context`, not `ctx`: Django captures the
                    // parent BEFORE `context.push()`, and for a nested loop
                    // that parent is the enclosing loop's own dict (the outer
                    // `Node::For` rendered this body against its `ctx`).
                    // Absent at the outermost level, where Django's
                    // `parentloop` is an empty dict — NOT missing, so
                    // `{{ forloop.parentloop }}` renders `{}` and
                    // `{{ forloop.parentloop.counter }}` renders nothing.
                    let parentloop = match context.get("forloop") {
                        Some(value) => value.clone(),
                        None => Value::Object(Default::default()),
                    };
                    // Built once and mutated in place per iteration.
                    // `IndexMap::insert` on a present key keeps its POSITION,
                    // so the insertion order below is the render order of
                    // `{{ forloop }}` itself — which Django spells
                    // `{'parentloop': …, 'counter0': …, 'counter': …,
                    // 'revcounter': …, 'revcounter0': …, 'first': …,
                    // 'last': …}` and is a comparable output, not an internal
                    // detail.
                    let len_values = indices_and_items.len() as i64;
                    let mut loop_dict: indexmap::IndexMap<djust_core::ObjectKey, Value> =
                        indexmap::IndexMap::with_capacity(7);
                    loop_dict.insert("parentloop".into(), parentloop);

                    // Save outer cycle counter for nested loop support
                    let saved_cycle_counter = ctx.get("__djust_cycle_counter").cloned();

                    // Save outer dj-if loop path for nested-loop composition
                    // and per-iteration uniqueness of `{% if %}` marker ids
                    // (#1832). The parent path (empty outside any loop) is
                    // read once; each iteration appends `-<index>` so a
                    // `{% if %}` rendered inside this loop gets a UNIQUE id
                    // per iteration that is also STABLE across re-renders
                    // that don't change loop structure. Uses the ORIGINAL
                    // item index (not the enumerate counter) so ids stay
                    // stable under `{% for ... reversed %}`. Mirrors the
                    // cycle-counter save/restore pattern above/below.
                    let parent_if_loop_path = match ctx.get("__djust_if_loop_path") {
                        Some(Value::String(s)) => s.clone(),
                        _ => String::new(),
                    };
                    let saved_if_loop_path = ctx.get("__djust_if_loop_path").cloned();

                    // Per-item render cache (#1967). Enabled only when:
                    //   (a) a `LoopRenderCache` is installed for this render
                    //       (via `LoopCacheGuard`) AND it is enabled (the
                    //       Python `loop_render_cache_enabled` flag), and
                    //   (b) the loop body is CACHEABLE — i.e. it is NOT
                    //       position-dependent (no `{% if %}` / `{% cycle %}` /
                    //       nested loop / forloop reference / opaque Python tag)
                    //       AND it reads NOTHING but the loop variable(s). A body
                    //       that reads any OUTER-context var (`{{ prefix }}`,
                    //       `{% with l=flag %}`, `{% firstof flag x %}`) is NOT
                    //       cacheable: outer context is constant within a render
                    //       but not across renders, and the cache is persistent
                    //       across renders, so a reorder after an outer-var
                    //       change would serve stale fragments (#1967 review 🔴).
                    //       See `loop_cache::body_is_cacheable`.
                    // When disabled, `loop_caching_enabled` is `false` and the
                    // render path below is byte-identical to before #1967.
                    let loop_caching_enabled = crate::loop_cache::with_active_cache(|cache| {
                        cache.body_cacheable(nodes, var_names)
                    })
                    .unwrap_or(false);

                    for (counter, (index, item)) in indices_and_items.into_iter().enumerate() {
                        // Set __djust_cycle_counter for {% cycle %} tag support
                        ctx.set(
                            "__djust_cycle_counter".to_string(),
                            Value::Integer(counter as i64),
                        );

                        // Set the per-iteration dj-if loop path (#1832).
                        // Composes for nested loops: an inner For reads this
                        // (non-empty) path and appends its own `-<index>`,
                        // yielding e.g. `-3-2`.
                        ctx.set(
                            "__djust_if_loop_path".to_string(),
                            Value::String(format!("{parent_if_loop_path}-{index}")),
                        );

                        // The six per-iteration counters, in Django's own
                        // arithmetic (#2402). `counter` is the ITERATION
                        // ordinal, not the item's original index: Django
                        // reverses `values` and THEN enumerates, so under
                        // `{% for x in p reversed %}` the first item rendered
                        // is `counter == 1` / `first == True` while its
                        // `index` — which `__djust_if_loop_path` uses, and
                        // deliberately — is the LAST one. Using `index` here
                        // would agree on every forward loop and silently
                        // reverse the numbering on a reversed one.
                        loop_dict.insert("counter0".into(), Value::Integer(counter as i64));
                        loop_dict.insert("counter".into(), Value::Integer(counter as i64 + 1));
                        loop_dict.insert(
                            "revcounter".into(),
                            Value::Integer(len_values - counter as i64),
                        );
                        loop_dict.insert(
                            "revcounter0".into(),
                            Value::Integer(len_values - counter as i64 - 1),
                        );
                        loop_dict.insert("first".into(), Value::Bool(counter == 0));
                        loop_dict
                            .insert("last".into(), Value::Bool(counter as i64 == len_values - 1));
                        // BEFORE the loop-variable binding below, so a loop
                        // whose variable is literally named `forloop`
                        // (`{% for forloop in p %}`) binds the ITEM and wins —
                        // which is Django's order: `loop_dict` is written at
                        // the top of the iteration and `context[loopvar]` at
                        // the bottom.
                        ctx.set("forloop".to_string(), Value::Object(loop_dict.clone()));

                        // The grant this item carries (#2361). Non-empty only
                        // for a NORMALISED operand, where the positional
                        // mapping below is refused and this by-key lookup is
                        // the only channel. The two are mutually exclusive by
                        // construction — `derived_grants` is populated only
                        // when `normalised`, the mapping registered only when
                        // `!normalised` — so they can never disagree about one
                        // item, which is what keeps this from being two
                        // mechanisms shadowing each other.
                        let grant = derived_grants.get(index).copied().unwrap_or_default();

                        // Handle tuple unpacking: {% for a, b in items %}
                        if var_names.len() == 1 {
                            // Single variable: {% for item in items %}
                            ctx.set(var_names[0].clone(), item);
                            // The O(1) half of `Context::bind` (the subtree
                            // half is hoisted above the loop). `false` REVOKES,
                            // so an unmarked item after a marked one is
                            // escaped rather than inheriting its neighbour's
                            // grant.
                            ctx.set_safety(&var_names[0], grant.whole);
                            // Track loop mapping for safe key resolution —
                            // but ONLY for a bare variable path. The mapping
                            // asserts `item` IS `<iterable>.<index>`, which
                            // `Context::is_safe` then looks up in `safe_keys`;
                            // once a filter is in play that correspondence is
                            // false (`slice` shifts indices, `dictsort`
                            // reorders), so establishing it could resolve a
                            // safety mark belonging to a DIFFERENT element
                            // (#2325). Registering nothing costs only
                            // over-escaping, which is the direction to fail in.
                            //
                            // `!normalised` is the same argument for the same
                            // reason, and for a dict it is a LIVE XSS rather
                            // than a theoretical one (#2334). `_collect_safe_keys`
                            // writes a dict's paths BY KEY NAME (`d.<key>`),
                            // while this mapping asserts `k` is `d.<index>`.
                            // Give a dict a key spelled `"1"` whose value is
                            // `mark_safe(...)` and `safe_keys` holds `d.1`; the
                            // loop's SECOND key — an entirely different string,
                            // and attacker-controlled if keys are user data —
                            // then resolves that mark and is emitted UNESCAPED.
                            // A string operand cannot collide the same way
                            // (`_collect_safe_keys` never descends into a str)
                            // but the correspondence is just as false, so both
                            // normalised shapes are excluded by one condition.
                            if !normalised && !iterable.contains('|') {
                                ctx.set_loop_mapping(var_names[0].clone(), iterable.clone(), index);
                            }
                        } else {
                            // Multiple variables: {% for key, value in items %}
                            //
                            // Django's `ForNode.render` checks ARITY before it
                            // unpacks anything, and RAISES on a mismatch:
                            //
                            //     try:               len_item = len(item)
                            //     except TypeError:  len_item = 1
                            //     if num_loopvars != len_item:
                            //         raise ValueError("Need %d values to unpack
                            //             in for loop; got %d. " % (...))
                            //     unpacked_vars = dict(zip(self.loopvars, item))
                            //
                            // djust padded the extra names with `Value::Missing`
                            // and rendered — so `{% for a, b in p %}` over
                            // `"abc"` rendered three iterations of `[a=][b=]`
                            // where Django refuses the template outright (#2387).
                            // That is MORE permissive than Django, and silently:
                            // the region rendered, with the variables empty.
                            let len_item = filters::python_len(&item).unwrap_or(1);
                            if len_item != var_names.len() {
                                // Django's message verbatim, trailing space and
                                // all. It crosses to Python as a `RuntimeError`
                                // rather than Django's `ValueError`, as EVERY
                                // djust render error does (see
                                // `DjangoRustError`); what matters is that the
                                // render is refused rather than silently padded.
                                return Err(DjangoRustError::TemplateError(format!(
                                    "Need {} values to unpack in for loop; got {}. ",
                                    var_names.len(),
                                    len_item
                                )));
                            }
                            match &item {
                                Value::List(tuple_items) | Value::Tuple(tuple_items) => {
                                    // `zip(self.loopvars, item)`, and the arity
                                    // check above is what makes it total: the
                                    // two lengths are now equal by construction,
                                    // so there is no short-item branch to pad.
                                    for (i, (var_name, part)) in
                                        var_names.iter().zip(tuple_items.iter()).enumerate()
                                    {
                                        // The grant for THIS component
                                        // (#2361). Two provenances, and the
                                        // same mutual exclusion as the
                                        // single-variable branch:
                                        //
                                        // * NORMALISED — a `d.items` pair,
                                        //   whose second half is the dict
                                        //   VALUE. `derived_grants` looked
                                        //   it up by key name.
                                        // * NOT normalised — a genuine
                                        //   sequence of tuples, where
                                        //   `_collect_safe_keys` wrote the
                                        //   component's own positional path
                                        //   `<expr>.<index>.<i>`. The
                                        //   correspondence is real here
                                        //   (both sides positional), which
                                        //   is exactly what it is not for a
                                        //   dict, so no #2334 collision:
                                        //   this is the tuple-unpacking
                                        //   twin of the loop mapping the
                                        //   single-variable branch
                                        //   registers.
                                        //
                                        // A filtered operand gets nothing,
                                        // for the reason the loop mapping
                                        // is refused one (`slice` shifts
                                        // indices, `dictsort` reorders).
                                        let part_safe = if normalised {
                                            i == 1 && grant.second
                                        } else if iterable.contains('|') {
                                            false
                                        } else {
                                            matches!(part, Value::String(_))
                                                && context
                                                    .is_safe(&format!("{iterable}.{index}.{i}"))
                                        };
                                        ctx.set(var_name.clone(), part.clone());
                                        ctx.set_safety(var_name, part_safe);
                                        // The alias for the paths BENEATH
                                        // this component (#2375).
                                        // `set_safety` above grants the
                                        // component ITSELF; `{{ b.z }}`
                                        // needs `b.z -> <expr>.<index>.<i>.z`,
                                        // which only an alias can express.
                                        //
                                        // Registered under EXACTLY the
                                        // condition `part_safe`'s own
                                        // positional lookup uses, and for
                                        // exactly the same reason: the
                                        // correspondence is real only when
                                        // both sides are positional. A
                                        // NORMALISED source (a dict view)
                                        // is refused because
                                        // `_collect_safe_keys` spells a
                                        // dict BY KEY NAME and this
                                        // asserts an INDEX — the #2334
                                        // collision, which is a live XSS
                                        // for attacker-controlled keys —
                                        // and a FILTERED one because
                                        // `slice` shifts and `dictsort`
                                        // reorders.
                                        if !normalised && !iterable.contains('|') {
                                            // Against `ctx`: the loop body
                                            // renders there, so an outer
                                            // loop's alias on the iterable
                                            // name is the one that applies
                                            // — the same context
                                            // `set_loop_mapping` uses one
                                            // branch over.
                                            let base = ctx.alias_path(iterable);
                                            ctx.set_alias(
                                                var_name.clone(),
                                                format!("{base}.{index}.{i}"),
                                            );
                                        }
                                    }
                                }
                                other => {
                                    // Not a sequence, but its `len()` matched —
                                    // so Django unpacks it too, because `zip`
                                    // ITERATES the item rather than indexing it.
                                    // `{% for a, b in p %}` over `[{"x": 1,
                                    // "y": 2}]` binds `a="x"`, `b="y"` (a dict
                                    // iterates its keys) and over `["ab"]` binds
                                    // `a="a"`, `b="b"`. djust bound the whole
                                    // item to the first name and `Missing` to
                                    // the rest, so the dict case rendered its
                                    // own repr into `{{ a }}`.
                                    //
                                    // Total by construction: every variant
                                    // `python_len` answers `Some` for is one
                                    // `iter_values` answers `Some` for, with the
                                    // same count — the pair is pinned by
                                    // `python_len_agrees_with_iter_values`.
                                    //
                                    // One shape is exempt there and cannot
                                    // reach here (#2466): a Python class with
                                    // only `__len__` has a length and is not
                                    // iterable, but `falsy_opaque` only ever
                                    // builds it with length 0, and the arity
                                    // check above refuses any item whose length
                                    // is not `var_names.len()` — which is never
                                    // 0. So the `unwrap_or_default()` below
                                    // still cannot silently pad.
                                    //
                                    // NO safety grant on any component, and that
                                    // is deliberate rather than an omission.
                                    // `_collect_safe_keys` spells a dict BY KEY
                                    // NAME, so the positional `<expr>.<index>.<i>`
                                    // lookup the sequence arm uses would be the
                                    // #2334 collision — live XSS for
                                    // attacker-controlled keys — and it never
                                    // descends into a `str` at all. Over-escaping
                                    // is the direction to fail in.
                                    let parts = filters::iter_values(other).unwrap_or_default();
                                    for (var_name, part) in var_names.iter().zip(parts) {
                                        ctx.set(var_name.clone(), part);
                                        ctx.set_safety(var_name, false);
                                    }
                                }
                            }
                        }
                        if loop_caching_enabled {
                            // Build a content hash from the loop-variable
                            // bindings the body reads (the item value). The
                            // body is position-INDEPENDENT (checked above), so
                            // its output is fully determined by these bindings
                            // plus the constant-across-iterations outer context.
                            let bindings: Vec<(&str, &Value)> = var_names
                                .iter()
                                .filter_map(|name| ctx.get(name).map(|v| (name.as_str(), v)))
                                .collect();
                            // Fold the For-node body identity into the key so
                            // sibling loops sharing a loop-var name over
                            // equal-content items can't cross-render (#2067).
                            let hash = crate::loop_cache::content_hash(
                                (nodes.as_ptr() as usize, nodes.len()),
                                &bindings,
                            );

                            // Cache HIT → reuse the previously rendered
                            // fragment (a reorder is all hits). MISS → render
                            // via the AST and insert.
                            let cached =
                                crate::loop_cache::with_active_cache(|cache| cache.get(hash))
                                    .flatten();
                            // The item's full rendered HTML (from the render
                            // cache on a hit, freshly rendered on a miss). This
                            // is what the PARSE cache (#1970) records in its
                            // per-render manifest as `item_html` — used by
                            // `render_with_diff` both to populate the parse cache
                            // (on a parse-miss) and to reconstruct the full HTML
                            // for the fallback / `last_html`.
                            let item_html = match cached {
                                Some(html) => html,
                                None => {
                                    let html = render_nodes_with_loader(nodes, &ctx, loader)?;
                                    crate::loop_cache::with_active_cache(|cache| {
                                        cache.insert(hash, html.clone())
                                    });
                                    html
                                }
                            };

                            // Parse cache (#1970): a SECOND cache keyed by the
                            // SAME content hash, holding the item's PARSED VNode
                            // subtree so a reorder skips html5ever-parse too. We
                            // gate on the item's rendered root tag being
                            // foster-parenting-SAFE (not a `<tr>`/`<option>`/…),
                            // because the placeholder we emit (`<dj-pc>`) would
                            // be relocated/dropped by html5ever inside a
                            // table/select container, corrupting structure. When
                            // eligible AND the parse cache already holds this
                            // item's subtree, emit a tiny `<dj-pc h=..>`
                            // PLACEHOLDER instead of the full item HTML — the
                            // assembled string `output` becomes a REDUCED HTML
                            // that html5ever parses cheaply; `render_with_diff`
                            // then splices the cached subtree back in. Otherwise
                            // emit the real item HTML (a parse MISS that
                            // `render_with_diff` parses + caches). Either way the
                            // manifest records the item so `render_with_diff` can
                            // reconstruct the full HTML and validate the splice.
                            let foster_safe =
                                crate::loop_cache::item_html_is_foster_safe(&item_html);
                            let parse_hit = foster_safe
                                && crate::loop_cache::with_active_cache(|cache| {
                                    cache.has_parsed(hash)
                                })
                                .unwrap_or(false);
                            let mut recorded = false;
                            if foster_safe {
                                recorded = crate::loop_cache::with_active_cache(|cache| {
                                    cache.record_manifest_item(hash, parse_hit, item_html.clone());
                                })
                                .is_some();
                            }
                            if recorded && parse_hit {
                                // Parse-cache HIT: emit the lightweight
                                // placeholder tagged with THIS render's nonce
                                // (`<dj-pc-<nonce> h=..>`) so it is unforgeable
                                // by `|safe` item content; the full item HTML
                                // lives in the manifest for reconstruction.
                                let nonce =
                                    crate::loop_cache::with_active_cache(|cache| cache.nonce())
                                        .unwrap_or(0);
                                output.push_str(&crate::loop_cache::render_loop_placeholder(
                                    hash, nonce,
                                ));
                            } else {
                                // Parse-cache MISS (or non-eligible item): emit
                                // the real item HTML. `render_with_diff` parses
                                // it and (for recorded eligible items) caches the
                                // resulting subtree.
                                output.push_str(&item_html);
                            }
                        } else {
                            output.push_str(&render_nodes_with_loader(nodes, &ctx, loader)?);
                        }
                    }

                    // Restore outer cycle counter (for nested loops)
                    if let Some(saved) = saved_cycle_counter {
                        ctx.set("__djust_cycle_counter".to_string(), saved);
                    }

                    // Restore outer dj-if loop path (#1832). There is no
                    // public Context::remove for an arbitrary key, so when
                    // there was no parent path we reset to the empty string,
                    // which Node::If treats as "no path" (it appends only a
                    // non-empty path). Otherwise restore the saved value.
                    match saved_if_loop_path {
                        Some(saved) => ctx.set("__djust_if_loop_path".to_string(), saved),
                        None => ctx.set(
                            "__djust_if_loop_path".to_string(),
                            Value::String(String::new()),
                        ),
                    }

                    // Clear loop mappings after the loop
                    for var_name in var_names {
                        ctx.clear_loop_mapping(var_name);
                    }

                    Ok(output)
                }
                // Django REFUSES a non-iterable operand, and this arm used to
                // render the `{% empty %}` block for every one of them (#2382).
                //
                // `ForNode.render` is precise about which shapes reach which
                // answer:
                //
                //     values = self.sequence.resolve(context, ignore_failures=True)
                //     if values is None:
                //         values = []
                //     if not hasattr(values, "__len__"):
                //         values = list(values)          # <- TypeError here
                //     len_values = len(values)
                //     if len_values < 1:
                //         return self.nodelist_empty.render(context)
                //
                // So `None` — and an operand that does not resolve, which
                // `ignore_failures=True` turns into `None` — becomes `[]` and
                // takes the empty branch. Everything else without a `__len__`
                // goes through `list()`, which raises for a value that is not
                // iterable. A `bool`, an `int`, a `float` and a `Decimal` all
                // land there; the issue that surfaced this framed it as a bool
                // problem, and measuring the axis showed it is about
                // non-iterables and not about falsiness.
                //
                // Raising matches Django, and matches the posture three fixes
                // took in the same week: #2328 (an unparseable filter
                // argument), #2387 (`{% for %}`'s own unpack arity) and #2400
                // (a wrong argument count) all chose Django's refusal over
                // silent degradation, in development and in production alike.
                // What djust rendered instead was not "less" — it was the
                // WRONG branch, with no signal anywhere that the operand was a
                // scalar.
                Value::Missing | Value::None => {
                    // Django's `values is None` / `ignore_failures` arm. Not
                    // folded into the raise below: these two are the reason
                    // this class is about non-iterables rather than falsiness,
                    // and they AGREE today.
                    render_nodes_with_loader(empty_nodes, context, loader)
                }
                other => Err(DjangoRustError::TemplateError(format!(
                    "'{}' object is not iterable",
                    python_type_name_for_iteration(&other)
                ))),
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
                // Use the CACHED loader method (#2074) so the parsed body's
                // allocation is STABLE across renders — the `{% for %}`
                // loop-render cache (#2067) keys each For-node's body by
                // identity (`nodes.as_ptr()`), which only matches across
                // renders when the same allocation is reused. The
                // `{% extends %}` inheritance path (`build_inheritance_chain`)
                // intentionally stays on `load_template` — it needs an
                // owned, mutable `Vec<Node>` for block-merging and has no
                // per-render identity requirement.
                let nodes = loader.load_template_cached(name)?;

                // Create context for included template
                let mut include_context = if *only {
                    // Only use with_vars, not parent context. The render-time
                    // switches are not "context", they are the render's: the
                    // fresh Context must carry the parent's dj-if marker
                    // setting or a plain page with an `only` include leaks
                    // the markers again (#2519). (`auto_call` is not carried
                    // here either — pre-existing, tracked in the #2519
                    // follow-up issue.)
                    let mut fresh = Context::new();
                    fresh.set_emit_dj_if_markers(context.emit_dj_if_markers());
                    fresh
                } else {
                    // Start with parent context
                    context.clone()
                };

                // Apply with_vars assignments through the same filter-aware
                // resolver as `{% with %}` and the `{% for %}` operand — this
                // is the third spelling of one bare-variable lookup, and it
                // carried the same raw-text fallback, so
                // `{% include "x" with q=p|upper %}` passed the literal string
                // `p|upper` into the included template (#2325). `get_value`
                // handles the quoted-literal case this arm open-coded.
                //
                // The grant travels with the value here too (#2363): this is
                // the third spelling of one binding, and a fix that reached
                // only `{% with %}` would be the parallel-path drift the
                // resolution fix already had to repair once (CLAUDE.md #1646).
                // With `only`, `include_context` is fresh and carries no
                // grants, so `bind`'s revoke half is a no-op there and its
                // grant half is the whole of the work.
                // The sub-path alias, by the same rule and through the same
                // door as `{% with %}` (#2375). Deciding this site EXPLICITLY
                // rather than by omission is the #1646 discipline: it is the
                // same operation under a third spelling. Under `only` the
                // fresh context carries no grants, so every alias resolves
                // against an empty set and costs nothing.
                let mut pending: Vec<(String, String)> = Vec::new();
                for (key, value_expr) in with_vars {
                    let (value, runtime_safe) = get_value_safe(value_expr, context)?;
                    include_context.bind(key.clone(), value, runtime_safe);
                    if let Some(path) = bare_dotted_path(value_expr) {
                        pending.push((key.clone(), context.alias_path(path)));
                    }
                }
                let bound: Vec<&str> = with_vars.iter().map(|(k, _)| k.as_str()).collect();
                register_binding_aliases(&mut include_context, pending, &bound);

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

        #[cfg(feature = "liveview")]
        Node::RustComponent { name, props } => {
            // Render Rust component server-side
            render_rust_component(name, props, context)
        }

        #[cfg(not(feature = "liveview"))]
        Node::RustComponent { name, .. } => Err(DjangoRustError::TemplateError(format!(
            "<{name} /> components require the `liveview` feature of djust_templates (#2519)"
        ))),

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

            // Process assignments through the same filter-aware resolver the
            // `{% for %}` operand uses (#2325). Django's `{% with %}` resolves
            // each assignment with a `FilterExpression`; the bare
            // `context.get(expression)` here was wrong three ways at once, and
            // its fallback made the failure LOUDER than an empty region:
            //
            //   {% with q=p|upper %}   rendered the literal text `p|upper`
            //   {% with q="lit" %}     rendered `&quot;lit&quot;`, quotes and all
            //   {% with q=nope %}      rendered the variable NAME `nope`
            //
            // — because a miss fell back to `Value::String(expression)`, i.e.
            // it echoed the template's own source into the page. `get_value`
            // returns `Value::Missing` for a genuine miss, which renders
            // empty, as Django's `string_if_invalid` default does.
            //
            // The runtime-safe flag TRAVELS with the value (#2363). It used to
            // be discarded here, which meant `{% with q=p|linebreaks %}` bound
            // the `Value` and dropped the `bool` beside it — so `{{ q }}`
            // escaped exactly what `{{ p|linebreaks }}` emits live, for every
            // safe-output filter including `|safe` itself. The grant is the
            // one `filter_output_is_safe` already computes for the EMIT path,
            // so binding it is parity rather than a new capability: this arm
            // now grants precisely what `{{ p|f }}` one line over already did.
            //
            // `Context::bind` REPLACES the grant rather than adding to it,
            // which is what closes the opposite-direction defect measuring
            // this one turned up: with `p` marked in the context,
            // `{% with p=hostile %}{{ p }}{% endwith %}` inherited the stale
            // by-name grant and emitted the hostile value RAW.
            // The grant on the paths BENEATH each bound name (#2375).
            // `bind` moves it at the NAME granularity, and
            // `_collect_safe_keys` writes a dict's marks at `p.<key>` — so
            // `{{ q.a }}` asked `is_safe("q.a")`, which nothing had ever
            // written, and the marked value came out escaped.
            //
            // An ALIAS rather than a copy: `q -> p` makes `is_safe` rewrite the
            // whole dotted path in `O(1)`, where copying every `p.…` entry to
            // `q.…` is `O(len(safe_keys))` per bind and still would not survive
            // a third level.
            //
            // Collected here and registered AFTER every bind, by
            // `register_binding_aliases` — read its docs before touching the
            // order. Two things depend on it: `bind` REVOKES the alias on the
            // name it binds, so registering inside the loop would be undone by
            // a later assignment to the same name; and an alias may not target
            // a name this same tag rebinds.
            //
            // The path is expanded against `context`, the OUTER one. Django
            // resolves every assignment in a `{% with %}` against the outer
            // context (`WithNode.render` builds the whole `values` dict before
            // `context.update`), so `b` in `{% with a=p b=a %}` binds the OUTER
            // `a`.
            let mut pending: Vec<(String, String)> = Vec::new();
            for (var_name, expression) in assignments {
                let (value, runtime_safe) = get_value_safe(expression, context)?;
                new_context.bind(var_name.clone(), value, runtime_safe);
                if let Some(path) = bare_dotted_path(expression) {
                    pending.push((var_name.clone(), context.alias_path(path)));
                }
            }
            let bound: Vec<&str> = assignments.iter().map(|(n, _)| n.as_str()).collect();
            register_binding_aliases(&mut new_context, pending, &bound);

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
            asvar,
        } => {
            let result = width_ratio(value, max_value, max_width, context)?;
            // `as <var>` renders NOTHING (Django's `WidthRatioNode.render`).
            // The ASSIGNMENT half lives in `sibling_updates`, which only the
            // sibling-aware loops can apply — a lone node has no sibling to
            // hand a mutated context to, exactly as `Node::AssignTag` here
            // invokes its handler and discards the updates.
            Ok(if asvar.is_some() {
                String::new()
            } else {
                result
            })
        }

        Node::FirstOf { args, asvar } => {
            // `as <var>` renders NOTHING; the assignment is `sibling_updates`'
            // job, exactly as for `Node::WidthRatio` above.
            if asvar.is_some() {
                return Ok(String::new());
            }
            Ok(first_of(args, context)?.unwrap_or_default())
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
            // Resolve via get_value_safe for dotted path and literal support
            // AND to thread the runtime-safe flag (#1672, parallel-path per
            // CLAUDE.md #1646): a custom filter that `mark_safe()`s at runtime
            // (e.g. `{% cycle a|md ... %}`) must NOT be re-escaped, matching the
            // Variable/InlineIf arms (#1660). `runtime_safe` is true ONLY when
            // the LAST filter produced a genuine SafeString → fail-safe.
            // `_("…")` literals first (#2558): the translatable literal is
            // not a variable, and the failure-ignoring resolver answers
            // Missing for it — `{% cycle c %}` over `_()` operands rendered
            // "" where Django renders the translated text (i18n14).
            let (resolved, runtime_safe) = match django_literal(val.trim()) {
                Some((value, true)) => (value, true),
                Some((value, false)) => (value, false),
                None => get_value_safe_ignoring_failures(val.trim(), context)?,
            };
            let output = if matches!(resolved, Value::Missing) {
                // An unresolved operand renders NOTHING, and the comment this
                // replaces claimed the opposite ("output the raw name (Django
                // behavior)"). Django compiles each `{% cycle %}` operand with
                // `compile_filter`, and a `FilterExpression` whose variable is
                // missing resolves to `string_if_invalid` — `""` by default.
                // Measured: `{% cycle nope 'z' %}` renders `""` in Django and
                // rendered `nope` here, putting the template's own source text
                // on the page. That is the #2325 echo symptom, in the one tag
                // whose operands the corpus did not build a cell for (#2355).
                String::new()
            } else if runtime_safe {
                resolved.to_string()
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
            // Since #2549 the parser refuses an unregistered tag itself, so
            // no parsed template contains this node; it is reachable only
            // from a hand-built tree. Same message, one producer.
            Err(DjangoRustError::TemplateError(
                crate::parser::unsupported_tag_message(name, args),
            ))
        }

        Node::BlockCustomTag {
            name,
            args,
            children,
        } => {
            // Body render + arg resolution + the registry call live in ONE
            // helper shared with `sibling_updates` (#2547). Arg resolution
            // goes through the SAME shared `resolve_tag_arg` as
            // `Node::AssignTag` — this arm used to carry a hand-copied twin
            // that skipped the JSON encoding, collapsing list/object args to
            // "[List]" / "[Object]" (CLAUDE.md #1646, #2042) — and honours
            // the handler's `RESOLVE_ARG_POSITIONS` policy like the other two
            // registries. A standalone render has no sibling to hand
            // bindings to, so they are dropped here, as the AssignTag arm
            // below drops its updates.
            let (html, _bindings) = call_block_custom_tag(name, args, children, context, loader)?;
            Ok(html)
        }

        Node::AssignTag { name, args } => {
            // When an AssignTag is rendered individually (outside of
            // render_nodes_with_loader's sibling-aware loop) we still
            // invoke the handler for its side-effects but discard the
            // result — there's no way to propagate context mutations
            // without a sibling to pass them to. Emits empty string.
            //
            // Resolve args (JSON-aware) honoring RESOLVE_ARG_POSITIONS,
            // as in render_nodes_with_loader (#2041).
            let resolved_args = plain_args(resolve_assign_tag_args(name, args, context));
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
            // placeholders "[List]" / "[Object]"). The JSON encoding is the
            // shared module-level `value_to_arg_string` — the single source
            // of truth for all three tag-dispatch paths (#1646, #2042). This
            // path keeps its own filter-aware `get_value` resolver (e.g.
            // `x|upper`), unlike the plain-context-lookup `resolve_tag_arg`
            // shared by AssignTag / BlockCustomTag.
            //
            // A handler may DECLARE `RESOLVE_ARG_POSITIONS` and take some
            // positions as literal TOKENS instead (#2423). Both rules apply,
            // in that order, through `resolve_custom_tag_args` — inside
            // `call_custom_tag`, the ONE site shared with `sibling_updates`
            // (#2547). A standalone render has no sibling to hand bindings
            // to, so they are dropped here, as the AssignTag arm above drops
            // its updates.
            let (html, _bindings) = call_custom_tag(name, args, context)?;
            Ok(html)
        }

        Node::RawBlockCustomTag { name, args, body } => {
            // The raw-body kind (#2558). A standalone render has no sibling
            // to hand bindings to, so they are dropped here, exactly as the
            // `Node::CustomTag` arm above drops its own.
            let (html, _bindings) = call_raw_block_tag(name, args, body, context)?;
            Ok(html)
        }

        Node::Language { expr, children } => render_language_scope(expr, children, context, loader),

        Node::Timezone { expr, children } => render_timezone_scope(expr, children, context, loader),

        Node::Localize { use_l10n, children } => {
            // Django's `LocalizeNode` toggles `context.use_l10n` and
            // restores it after (`l10n.py:31-36`); the restore runs on the
            // error path too. A lexical scope entered and exited in the
            // same call frame needs no Context plumbing — one thread-local
            // stack is the whole mechanism (#2558).
            USE_L10N_STACK.with(|s| s.borrow_mut().push(*use_l10n));
            let result = render_nodes_with_loader(children, context, loader);
            USE_L10N_STACK.with(|s| {
                s.borrow_mut().pop();
            });
            result
        }

        Node::LocalTime { use_tz, children } => {
            // Django's `LocalTimeNode` toggles `context.use_tz` (`tz.py:92-106`).
            // `off` clears the active zone so aware datetimes stop converting
            // inside the block; `on` leaves whatever the render env pushed
            // (the restore is the saved value either way).
            let prev = crate::timezone::active_timezone_name();
            if !*use_tz {
                crate::timezone::set_active_timezone(None);
            }
            let result = render_nodes_with_loader(children, context, loader);
            if !*use_tz {
                crate::timezone::set_active_timezone(prev.as_deref());
            }
            result
        }
    }
}

/// Render a Rust component by instantiating it and calling its render method
#[cfg(feature = "liveview")]
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
#[cfg(feature = "liveview")]
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

/// [`evaluate_condition`] with Django's `IfNode.render` error policy.
///
/// ```python
/// try:
///     match = condition.eval(context)
/// except VariableDoesNotExist:
///     match = None
/// ```
///
/// `{% if %}` — alone among the constructs that take a filtered operand — turns
/// an unresolvable variable into a FALSY condition rather than a render error.
/// `{{ }}`, `{% for %}`, `{% with %}` and `{% ifchanged %}` all propagate,
/// verified against Django 5.2 (`scratch` probe in #2328).
///
/// The catch is narrow on purpose: Django does NOT catch the `ValueError` an
/// unparseable filter argument raises, so `{% if p|center:"nope" %}` still
/// fails. That is why the resolve miss carries its own error variant — with one
/// "template error" kind, this would swallow genuine failures too.
///
/// Applied to djust's own `{% if %}`-shaped inline conditional as well: two
/// spellings of one construct answering differently is the drift this codebase
/// keeps paying for (#1646).
/// The Python type name CPython puts in `'X' object is not iterable` (#2382).
///
/// A thin alias for [`filters::python_type_name`] since #2451, and the alias
/// is the point: this function WAS a four-arm copy of that question, and #2451
/// needed the same answer for seven filters. Two spellings of one fact is the
/// drift this codebase keeps paying for (#1646), so there is one.
///
/// The four shapes the copy covered are exactly the ones that reach
/// `{% for %}`'s refusal arm — `String`, `Object`, `DictView`, `List` and
/// `Tuple` are normalised or iterated above, and `Missing` / `None` take
/// Django's empty branch — so the wider answer is unreachable from here and
/// every message this arm can emit is byte-identical to what it emitted
/// before. `test_the_for_refusal_messages_are_unchanged_by_the_unification`
/// is the pin.
fn python_type_name_for_iteration(value: &Value) -> &str {
    filters::python_type_name(value)
}

fn evaluate_condition_for_if(condition: &str, context: &Context) -> Result<bool> {
    match evaluate_condition(condition, context) {
        Err(DjangoRustError::VariableDoesNotExist(_)) => Ok(false),
        other => other,
    }
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
            let left = get_value_ignoring_failures(parts[0], context)?;
            let right = get_value_ignoring_failures(parts[1], context)?;
            return Ok(values_equal(&left, &right));
        }
    }

    if condition.contains("!=") {
        let parts: Vec<&str> = condition.split("!=").map(|s| s.trim()).collect();
        if parts.len() == 2 {
            let left = get_value_ignoring_failures(parts[0], context)?;
            let right = get_value_ignoring_failures(parts[1], context)?;
            return Ok(!values_equal(&left, &right));
        }
    }

    // Handle Django identity operators "is" / "is not" (Django 4.0+).
    // " is not " MUST be checked before " is " because the former
    // contains the latter as a substring. Space-padded markers avoid
    // matching variable names that merely contain "is" (e.g. "analysis").
    if let Some(pos) = condition.find(" is not ") {
        let left = get_value_ignoring_failures(condition[..pos].trim(), context)?;
        let right = get_value_ignoring_failures(condition[pos + 8..].trim(), context)?;
        return Ok(!values_identity(&left, &right));
    }
    if let Some(pos) = condition.find(" is ") {
        let left = get_value_ignoring_failures(condition[..pos].trim(), context)?;
        let right = get_value_ignoring_failures(condition[pos + 4..].trim(), context)?;
        return Ok(values_identity(&left, &right));
    }

    // Handle >= (must be before > to avoid false match)
    //
    // `is_some_and`, not `unwrap_or(0) >= 0` (#2338). An incomparable pair is
    // `None`, and Django answers False for it — Python raises `TypeError` and
    // `{% if %}` catches it. Defaulting to 0 reads "cannot be ordered" as
    // "equal" and answers True, which is the permissive direction: a
    // `{% if x >= threshold %}` gate opened on operands with no ordering at
    // all. Its mirror below must stay in step — fixing one and leaving the
    // other is the same bug reflected (#1646).
    if condition.contains(">=") {
        let parts: Vec<&str> = condition.split(">=").map(|s| s.trim()).collect();
        if parts.len() == 2 {
            let left = get_value_ignoring_failures(parts[0], context)?;
            let right = get_value_ignoring_failures(parts[1], context)?;
            return Ok(try_compare(&left, &right).is_some_and(|c| c >= 0));
        }
    }

    // Handle <= (must be before < to avoid false match)
    if condition.contains("<=") {
        let parts: Vec<&str> = condition.split("<=").map(|s| s.trim()).collect();
        if parts.len() == 2 {
            let left = get_value_ignoring_failures(parts[0], context)?;
            let right = get_value_ignoring_failures(parts[1], context)?;
            return Ok(try_compare(&left, &right).is_some_and(|c| c <= 0));
        }
    }

    // Handle "in" operator: {% if item in list %}
    if condition.contains(" in ") {
        let parts: Vec<&str> = condition.splitn(2, " in ").map(|s| s.trim()).collect();
        if parts.len() == 2 {
            let needle = get_value_ignoring_failures(parts[0], context)?;
            let haystack = get_value_ignoring_failures(parts[1], context)?;
            return match haystack {
                Value::List(items) | Value::Tuple(items) => {
                    Ok(items.iter().any(|item| values_equal(&needle, item)))
                }
                // `'a' in d.keys()` / `1 in d.values()` / `('a', 1) in
                // d.items()` all work in Python, by the same element
                // comparison a list uses (#2340).
                Value::DictView { items, .. } => {
                    Ok(items.iter().any(|item| values_equal(&needle, item)))
                }
                // A CARRIED collection, by the same element comparison
                // (#2477/#2489). `{% if tag in tags %}` over a `set` is
                // ordinary Django, and it worked here by ACCIDENT before the
                // carrier existed: a truthy set crossed as
                // `Value::String("{'a'}")` and fell to the `Value::String`
                // arm, which is a SUBSTRING match — so `{% if 'a' in tags %}`
                // was true for `{'ab'}` too, and for the characters of the
                // repr's punctuation. The element comparison is Python's.
                //
                // An `Encoded` with no items (a `datetime`, a `complex(0)`,
                // a zero-`__len__` class) falls through to `_ => false`, which
                // is what `x in dt` does in Python: `TypeError`, and djust's
                // `if` fails soft rather than raising.
                Value::Encoded(ref e) if e.items.is_some() => Ok(e
                    .items
                    .as_ref()
                    .is_some_and(|items| items.iter().any(|item| values_equal(&needle, item)))),
                Value::String(s) => {
                    if let Value::String(n) = &needle {
                        Ok(s.contains(n.as_str()))
                    } else {
                        Ok(false)
                    }
                }
                Value::Object(map) => {
                    // Django: "x in dict" checks dict keys — BY VALUE, as
                    // Python does, not by `Display` (#2339).
                    //
                    // This used to be `map.contains_key(&needle.to_string())`,
                    // so `{% if 0 in d %}` opened on a `"0"` key: a gate
                    // deciding on a coincidence of formatting. It was left
                    // that way on the premise that djust's wire format
                    // coerced every dict key to a string, making the
                    // coercion the only thing keeping `{% if pk in d %}`
                    // working against an int-keyed mapping.
                    //
                    // The premise was false. The render path has no JSON hop
                    // — the live Python dict reaches PyO3 directly — so an
                    // int-keyed dict was never string-keyed here; it was not
                    // a `Value::Object` at ALL, and `{% if pk in d %}`
                    // answered False on it already. Now that `ObjectKey`
                    // carries the key's type, both answers are Python's at
                    // once. See `crates/djust_core/src/object_key.rs`.
                    //
                    // A needle Python could not hash either (a list, a dict)
                    // yields `None` and misses, rather than matching
                    // something by its text.
                    Ok(djust_core::ObjectKey::from_value(&needle)
                        .is_some_and(|k| map.contains_key(&k)))
                }
                _ => Ok(false),
            };
        }
    }

    // Handle > (greater than)
    if condition.contains(" > ") {
        let parts: Vec<&str> = condition.split(" > ").map(|s| s.trim()).collect();
        if parts.len() == 2 {
            let left = get_value_ignoring_failures(parts[0], context)?;
            let right = get_value_ignoring_failures(parts[1], context)?;
            return Ok(try_compare(&left, &right).is_some_and(|c| c > 0));
        }
    }

    // Handle < (less than)
    if condition.contains(" < ") {
        let parts: Vec<&str> = condition.split(" < ").map(|s| s.trim()).collect();
        if parts.len() == 2 {
            let left = get_value_ignoring_failures(parts[0], context)?;
            let right = get_value_ignoring_failures(parts[1], context)?;
            return Ok(try_compare(&left, &right).is_some_and(|c| c < 0));
        }
    }

    // A bare filter expression is a VALUE, not an operator form: nothing above
    // matched because there is no operator to match, and defaulting to `false`
    // made `{% if p|slice:":1" %}` take the `{% else %}` branch on a non-empty
    // list (#2325). Django resolves the condition with a `FilterExpression`
    // and tests its truthiness.
    //
    // Deliberately LAST, not next to the `context.get(condition)` arm above:
    // an operator form can contain a pipe too (`{% if p|length == 3 %}`,
    // `{% if not p|slice:":0" %}`), and both of those already work by reaching
    // their own arm first. Resolving on the presence of a `|` any earlier
    // would capture them and hand `get_value` a whole comparison to look up as
    // one variable name.
    // Nothing above matched, so this condition is not an operator form at all
    // — it is a VALUE, and Django tests its truthiness after resolving it with
    // a `FilterExpression`. Returning a bare `false` here instead is what made
    // `{% if p|slice:":1" %}` take the `{% else %}` branch on a non-empty list
    // (#2325); the `context.get(condition)` arm near the top of this function
    // only ever handled a plain variable name.
    //
    // Deliberately LAST. Every operator form reaches its own arm first, which
    // is what keeps `{% if p|length == 3 %}` and `{% if not p|slice:":0" %}`
    // working — resolving any earlier would hand `get_value` a whole
    // comparison to look up as one variable name. Position is the guard here,
    // so this needs no "does it contain a pipe" test of its own: an unmatched
    // condition with no pipe resolves through the same path and yields
    // `Value::Missing` (falsy) exactly as the old `Ok(false)` did, while one
    // backed by the raw-Python sidecar now answers like `{{ }}` does rather
    // than being silently false.
    Ok(get_value_ignoring_failures(condition, context)?.is_truthy())
}

/// Register the sub-path aliases for ONE multi-assignment binding tag
/// (`{% with %}`, `{% include … with %}`), after every bind in it has run.
///
/// `pending` is `(bound name, already-expanded target path)`; `bound` is every
/// name this tag assigns.
///
/// # The exclusion, and why it is not a nicety
///
/// An alias is looked up in the NEW context's `safe_keys`, while its target
/// path describes the OUTER context. Every later rebinding retires it —
/// `Context::revoke_safe_subtree` sweeps the alias TARGETS — but a rebinding
/// that happened EARLIER IN THE SAME TAG cannot be swept, because the alias
/// did not exist yet. So:
///
/// ```text
/// {% with a=p b=a %}{{ b }}{% endwith %}     p marked, outer a hostile
/// ```
///
/// binds `a` to `p`'s marked value (`set_safety("a", true)` — the NAME `a` is
/// now marked in the new context) and binds `b` to the OUTER `a`, which is
/// hostile. An alias `b -> a` then reads `a`'s BRAND-NEW mark and `{{ b }}`
/// went to the page RAW. Measured; Django escapes.
///
/// The rule that closes it is about the OPERATION rather than the values
/// (#2129): **an alias may not target a name this same binding tag rebinds.**
/// Skipping costs only over-escaping, which is the direction to fail in.
fn register_binding_aliases(ctx: &mut Context, pending: Vec<(String, String)>, bound: &[&str]) {
    for (name, target) in pending {
        let head = target.split('.').next().unwrap_or(target.as_str());
        if bound.contains(&head) {
            continue;
        }
        ctx.set_alias(name, target);
    }
}

/// The expression, when it is a BARE DOTTED PATH into the context — and
/// therefore NAMES the very value a binding is about to bind (#2375).
///
/// This is the guard on every [`Context::set_alias`] call, and it is a
/// security boundary rather than a tidiness check. An alias asserts
/// "`name` IS the value at `<path>`", and [`Context::is_safe`] then resolves
/// `name.<rest>` against `safe_keys` as `<path>.<rest>`. That is true for
/// `{% with q=p %}` and FALSE the moment anything transforms the value:
///
/// * a FILTER — `{% with q=p|dictsort:"a" %}` reorders, `|slice` shifts, so
///   `q.0` is not `p.0`. `_collect_safe_keys` wrote the marks against the
///   ORIGINAL order, and resolving through them would grant a mark belonging
///   to a DIFFERENT element. For a dict whose keys are user data that is a
///   live XSS, not a theoretical one — the same shape #2334 refused the loop
///   mapping for;
/// * a LITERAL or an operator — there is no context path to alias at all.
///
/// So: every segment is `[A-Za-z0-9_]`, non-empty, and the FIRST segment
/// starts with a letter or `_`. A leading digit is refused because `5` and
/// `5.5` are literals, not paths; a numeric segment LATER is fine and
/// necessary, since `_collect_safe_keys` spells a list's marks positionally
/// (`p.0.a`).
///
/// Refusing too much costs only over-escaping, which is the direction to fail
/// in; refusing too little emits raw HTML.
fn bare_dotted_path(expr: &str) -> Option<&str> {
    let expr = expr.trim();
    let mut segments = expr.split('.');
    let first = segments.next()?;
    let head_ok = first
        .chars()
        .next()
        .is_some_and(|c| c.is_ascii_alphabetic() || c == '_');
    let all_ok = expr
        .split('.')
        .all(|seg| !seg.is_empty() && seg.chars().all(|c| c.is_ascii_alphanumeric() || c == '_'));
    (head_ok && all_ok).then_some(expr)
}

/// Django's `Variable.__init__` LITERAL branch — the ONE place a bare token is
/// recognized as a literal, and the ONE place the safety grant one carries is
/// minted (#2376).
///
/// Returns `(value, is_safe)`, or `None` when the token is a variable LOOKUP.
///
/// # Why one function
///
/// djust had two resolvers that could see a bare token — `get_value_safe`
/// (the `{% if %}` / `{% with %}` / `{% firstof %}` / `{% cycle %}` operand
/// channel) and the `Node::Variable` / `Node::InlineIf` emit arms — and only
/// the first had literal arms at all. So `{% if "<b>" %}` was right,
/// `{% with q="<b>" %}` resolved, and `{{ "<b>" }}` rendered the EMPTY STRING:
/// the text vanished rather than appearing escaped. `{{ 5 }}` and `{{ 5.5 }}`
/// were empty for the same reason — the defect is the whole literal surface,
/// not the quoted spelling the issue happened to name. Same
/// two-resolvers-one-blind split #2347 found for `True` / `False` / `None`,
/// and the cure is the same: state the rule once and call it from both.
///
/// # The grant
///
/// `Variable.__init__` ends its quoted branch with
/// `self.literal = mark_safe(unescape_string_literal(var))`, so a quoted
/// literal IS `SafeData` and `{{ "<b>" }}` renders LIVE markup in Django. That
/// is why this returns a bool rather than a bare `Value`: resolving the
/// literal WITHOUT the grant renders `&lt;b&gt;`, a third answer that is
/// neither the bug's nor Django's. The two belong in one change and therefore
/// in one function.
///
/// It is not a new attack surface. The string being marked is the TEMPLATE
/// AUTHOR's own source text, never context data — a template built from user
/// input is already an RCE, in Django exactly as here.
///
/// A NUMBER is not marked. Django only calls `mark_safe` on the quoted branch,
/// and a number has no markup to protect anyway.
///
/// # Django's order, and why the `.` and `e` test comes first
///
/// ```text
/// if "." in var or "e" in var.lower():
///     self.literal = float(var)
///     if var[-1] == ".":      # "2." is invalid
///         raise ValueError
/// else:
///     self.literal = int(var)
/// ```
///
/// The `e`/`.` gate is what keeps `inf` and `nan` — which BOTH `float()` and
/// Rust's `f64` parser accept — from becoming literals: neither carries a `.`
/// or an `e`, so both take the `int` arm, fail, and resolve as variables.
/// Measured against Django 5.2.16: `{{ inf }}` renders empty in both engines.
/// Dropping the gate and parsing `f64` first would silently turn a variable
/// named `inf` into a float, which is why the gate is reproduced rather than
/// simplified.
///
/// # Known narrower than Django, deliberately
///
/// Python's `int()` / `float()` accept digit separators (`1_000`) and
/// non-ASCII digits; Rust's parsers do not. Those tokens stay variable
/// lookups and render empty, which is what they did before this function
/// existed — narrower is the direction a literal recognizer may fail in,
/// because the alternative is inventing a value Django would not.
pub(crate) fn django_literal(expr: &str) -> Option<(Value, bool)> {
    // The `_("…")` translatable literal (#2558, `base.py:833-840`). Django
    // marks the inner literal SAFE and translates at resolve time, doubling
    // `%` first — `{{ _("100%") }}` renders `100%%` on Django, its own quirk
    // (`base.py:862`: the doubling is undone by `TranslateNode`, not by the
    // variable). Reproduced, not "fixed". The translator is consulted per
    // RENDER so the active language is read live; with none installed the
    // %-doubled msgid comes back, which is Django's `USE_I18N=False`
    // answer (`gettext_lazy` with no activation).
    if expr.starts_with("_(") && expr.ends_with(')') && expr.len() > 4 {
        let inner = &expr[2..expr.len() - 1];
        let quote = inner.chars().next()?;
        if (quote == '"' || quote == '\'') && inner.len() >= 2 && inner.ends_with(quote) {
            let unescaped = inner[quote.len_utf8()..inner.len() - quote.len_utf8()]
                .replace(&format!("\\{quote}"), &quote.to_string())
                .replace("\\\\", "\\");
            let msgid = unescaped.replace('%', "%%");
            let translated =
                crate::registry::translate_msgid(&msgid).unwrap_or_else(|| msgid.clone());
            return Some((Value::String(translated), true));
        }
    }
    if expr.contains('.') || expr.contains(['e', 'E']) {
        // `"2."` is invalid — Django re-raises after the successful `float()`.
        if !expr.ends_with('.') {
            if let Ok(f) = expr.parse::<f64>() {
                return Some((Value::Float(f), false));
            }
        }
    } else if let Ok(i) = expr.parse::<i64>() {
        return Some((Value::Integer(i), false));
    } else if let Some(digits) = big_int_literal(expr) {
        // Python's `int()` has no width, so `{{ 99999999999999999999999 }}`
        // renders every digit in Django. Past `i64` that is a `Value::BigInt`,
        // whose invariant is "`str(int)` — an optional `-` then ASCII digits",
        // which `big_int_literal` establishes rather than assumes.
        return Some((Value::BigInt(digits), false));
    }

    // The quoted literal. Django's `unescape_string_literal` requires a
    // matching quote at BOTH ends; a lone `"` never arrives, because Django's
    // own `FilterExpression` refuses to parse it (`Could not parse the
    // remainder`) — measured, not assumed — so a two-character minimum is the
    // real contract and the slice below cannot underflow.
    let quote = expr.chars().next()?;
    if (quote != '"' && quote != '\'') || expr.len() < 2 || !expr.ends_with(quote) {
        return None;
    }
    let inner = &expr[quote.len_utf8()..expr.len() - quote.len_utf8()];
    // `s[1:-1].replace(r"\<quote>", quote).replace(r"\\", "\\")`, in Django's
    // order.
    //
    // The order is NOT observable, and that is measured rather than assumed:
    // over every string on `{a, \, "}` up to length 6 the two orders differ
    // for 113 of them, and Django's own `FilterExpression` parses ZERO — every
    // distinguishing shape contains `\\"`, at which `strdq` terminates the
    // constant and the remainder fails to parse. So there is deliberately no
    // test that pins the order, and `test_the_unescape_order_is_unobservable`
    // is the proof of why rather than the absence of one: gating this order
    // off is a provable semantic no-op, not missing coverage.
    //
    // Django's order is kept anyway. A future lexer change that admitted the
    // `\\"` shape would make it observable, and matching the reference
    // implementation costs nothing.
    let unescaped = inner
        .replace(&format!("\\{quote}"), &quote.to_string())
        .replace("\\\\", "\\");
    Some((Value::String(unescaped), true))
}

/// A filter ARGUMENT that is an `_()` literal, translated (#2558).
///
/// The filter-argument channel strips surrounding quotes before it reaches
/// the filters, so `_("Password")` arrives WHOLE and never hits
/// [`django_literal`] the way a `{{ }}` expression does. ONE helper, called
/// at the ONE entry every builtin filter's argument flows through
/// (`filters::apply_filter_full_safe`) plus the custom-filter literal arm
/// (`filter_registry.rs`, which already consults `django_literal`) — so a
/// fourth arg site cannot appear blind (the #1125 count-pin in
/// `test_i18n_tags_bridge_2558.py` enforces the call).
///
/// `Some(translated)` only for the exact `_(`…`)` shape; every other
/// argument (quoted literal, bare name, number) passes through unchanged.
pub(crate) fn translate_underscore_arg(arg: &str) -> Option<String> {
    if !arg.starts_with("_(") || !arg.ends_with(')') || arg.len() <= 4 {
        return None;
    }
    match django_literal(arg) {
        // The `safe` grant is irrelevant on this channel; only the value is.
        Some((Value::String(translated), _)) => Some(translated),
        _ => None,
    }
}

/// `[-]digits` too large for `i64`, as [`Value::BigInt`] requires (#2260).
///
/// Only reached after the `i64` parse has failed, so a `Some` here really is
/// "past `i64`". A leading `+` is STRIPPED rather than kept: `BigInt`'s
/// `Display` writes its string back verbatim, and Django renders `int("+1…")`
/// without the sign.
fn big_int_literal(expr: &str) -> Option<String> {
    let (sign, body) = match expr.strip_prefix('-') {
        Some(rest) => ("-", rest),
        None => ("", expr.strip_prefix('+').unwrap_or(expr)),
    };
    if body.is_empty() || !body.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    // `int("007")` is `7`; keeping the zeros would render a number Django
    // does not. Strip them, and keep one digit so `"000"` stays `"0"`.
    let trimmed = body.trim_start_matches('0');
    let body = if trimmed.is_empty() { "0" } else { trimmed };
    Some(format!("{sign}{body}"))
}

/// [`get_value`] under Django's `ignore_failures=True` (#2528, ADR-027).
///
/// `FilterExpression.resolve` (`base.py:720-726`) turns a resolution failure
/// into **None** rather than `string_if_invalid` when the caller passes
/// `ignore_failures=True`, and exactly five tags do: `{% if %}`
/// (`defaulttags.py:886`), `{% for %}` (`:194`), `{% cycle %}` (`:153`),
/// `{% firstof %}` (`:271`) and `{% regroup %}` (`:365`, `:368`).
///
/// **Every other operand resolves STRICTLY**, and that set is not a detail:
/// `{% with %}`, `{% include … with %}`, a tag's own arguments and a filter's
/// arguments all pass the default `ignore_failures=False`, so a miss there is
/// `string_if_invalid` and NOT `None`. The first version of #2539 applied the
/// substitution inside the shared pipe branch, which claimed all of them —
/// making `{% with x=y|default_if_none:'D' %}` render `D` where Django renders
/// the empty string.
fn get_value_ignoring_failures(expr: &str, context: &Context) -> Result<Value> {
    get_value_safe_inner(expr, context, true).map(|(value, _)| value)
}

/// [`get_value_safe`] under Django's `ignore_failures=True` — see
/// [`get_value_ignoring_failures`] for which callers may use it.
fn get_value_safe_ignoring_failures(expr: &str, context: &Context) -> Result<(Value, bool)> {
    get_value_safe_inner(expr, context, true)
}

fn get_value(expr: &str, context: &Context) -> Result<Value> {
    // Thin wrapper that discards the runtime-safe flag. Most callers
    // (condition operators, progress-bar math, etc.) only need the `Value`
    // and never reach the auto-escape decision, so they stay on this
    // signature. The `{% firstof %}` / `{% cycle %}` emit path uses
    // `get_value_safe` directly to honour runtime SafeStrings (#1672).
    // Mirrors the `apply_filter_full` / `apply_filter_full_safe` shape in
    // `filters.rs` — single pipe-loop source of truth, no parallel drift.
    get_value_safe(expr, context).map(|(value, _)| value)
}

/// Like [`get_value`] but also reports whether the produced value is a runtime
/// ``SafeString`` (a custom filter that ``mark_safe()``d its output at runtime).
///
/// `runtime_safe` tracks the LAST filter's runtime safeness: a later
/// plain-returning filter re-taints (resets to false), matching Django's
/// final-value escape semantics and the Variable/InlineIf render arms (#1660).
///
/// NOTE (#1672, parallel-path threading per CLAUDE.md #1646): the
/// `{% firstof %}` / `{% cycle %}` emit path consumes this bool to skip
/// auto-escaping for a runtime-SafeString value — closing the parity gap
/// where the old `get_value` dropped the flag (over-escape, fail-SAFE / no
/// XSS) while the Variable/InlineIf arms honoured it. The bool originates ONLY
/// from `apply_filter_full_safe`, which returns `true` solely for a genuine
/// `str`-subclass with `__html__` (the #1660 XSS-hardened check), so this can
/// only ever mark MORE values safe — never under-escape a plain value.
fn get_value_safe(expr: &str, context: &Context) -> Result<(Value, bool)> {
    get_value_safe_inner(expr, context, false)
}

/// The shared body of the four resolvers above. `ignore_failures` is Django's
/// `FilterExpression.resolve` parameter and is threaded rather than read from
/// the flag, because it is a property of the CALLER's tag, not of the render.
fn get_value_safe_inner(
    expr: &str,
    context: &Context,
    ignore_failures: bool,
) -> Result<(Value, bool)> {
    // Handle pipe filters in expressions (e.g., "project.id|stringformat:\"s\"")
    //
    // The split is QUOTE-AWARE (#2409). `expr.contains('|')` and
    // `splitn(2, '|')` cut `{% if p|cut:"a|b" %}` inside its own quoted
    // argument, and the loop below took the first colon so
    // `{% for x in p|cut:"a":"b" %}` silently accepted a SECOND argument that
    // Django's lexer refuses. Both sites — this one and
    // `parser::parse_filter_specs` — go through `filter_lexer` rather than
    // carrying a copy of the rule each (#1646); a `{{ }}`-only fix would have
    // left `{% if %}`, `{% for %}` and `{% with %}` over-permissive, which is
    // what #2409's measurement across the four shapes shows.
    let pipe_parts = crate::filter_lexer::split_pipes(expr);
    if pipe_parts.len() > 1 {
        let var_name = pipe_parts[0].trim();

        // Resolve the base variable, KEEPING its safety flag (#2416).
        //
        // Track the LAST filter's runtime safeness, mirroring the Variable arm
        // (#1660). A plain-returning filter after a runtime-safe one re-taints.
        // Seeded with the base's own safety so this arm carries Django's input
        // term too (#2274) — the third of the three sites, kept in step with
        // the other two by construction (#1646).
        //
        // The seed is this RECURSIVE call rather than `context.is_safe(var_name)`
        // because the base can be a quoted LITERAL, which `Variable.__init__`
        // `mark_safe`s and `is_safe` cannot answer about — it is not a name.
        // With the name-only seed, `{% firstof "<B>"|lower %}` came out
        // ESCAPED where Django emits live markup (`lower` is registered
        // `is_safe=True`, so a safe input stays safe), while the `{{ }}` arm —
        // which seeds from `django_literal`'s own bool — was already right.
        // Same two-resolvers-one-blind split #2376 closed for the bare literal,
        // one filter along. The recursion terminates: `var_name` is
        // `split_pipes`'s FIRST part and so contains no pipe.
        let (mut value, mut runtime_safe) =
            get_value_safe_inner(var_name, context, ignore_failures)?;
        // Django's `ignore_failures=True` substitutes **None**, not "missing"
        // (#2528, ADR-027). `FilterExpression.resolve` (`base.py:720-726`)
        // reads
        //
        // ```python
        // except VariableDoesNotExist:
        //     if ignore_failures:
        //         obj = None
        // ```
        //
        // and every consumer of THIS function passes it: `{% if %}`
        // (`defaulttags.py:886`), `{% for %}` (`:194`), `{% cycle %}`
        // (`:153`), `{% firstof %}` (`:271`) and `{% regroup %}` (`:365`).
        // The filter chain then runs over `None` — so
        // `{% if x|default_if_none:y %}` with `x` undefined is Django's `y`
        // and was djust's "no", because the chain ran over `Value::Missing`
        // and `default_if_none` correctly refused to fire for it.
        //
        // Only the FILTERED operand is affected, which is the whole of the
        // observable difference: with no filter in the chain `Missing` and
        // `None` are both falsy to every consumer above, and substituting one
        // for the other where the value is EMITTED (`{% firstof %}`) would
        // change bytes for no Django reason.
        //
        // Gated on the flag with the rest of the movement: it is a behaviour
        // change, and this movement's contract is flag-OFF byte identity.
        if ignore_failures && djust_core::resolve_lazy() && matches!(value, Value::Missing) {
            value = Value::None;
        }
        // See the Variable arm: item-level safety, seeded from the context
        // (#2283, #2287) — the third of the three sites, kept in step with the
        // other two by construction (#1646).
        let mut items_safe = context.items_are_safe(var_name);

        // Parse and apply filters (handles chained filters too)
        for filter_part in &pipe_parts[1..] {
            let filter_part = filter_part.trim();
            let (filter_name, raw_arg) = crate::filter_lexer::split_filter_spec(filter_part, expr)?;
            let (arg, arg_was_quoted) = match raw_arg {
                Some(raw_arg) => {
                    let was_quoted = is_quoted_arg(raw_arg);
                    let arg_str = if was_quoted {
                        raw_arg[1..raw_arg.len() - 1].to_string()
                    } else {
                        raw_arg.to_string()
                    };
                    (Some(arg_str), was_quoted)
                }
                None => (None, false),
            };

            // Thread the (Value, bool) shape out so callers in the firstof/cycle
            // emit path can honour runtime SafeStrings (#1672, follow-up to
            // #1660). Built-ins report it too, for the four whose safety is
            // per-call rather than per-name (`filters::builtin_produced_safe`).
            let (new_value, produced_safe) = filters::apply_filter_full_safe(
                filter_name,
                &value,
                arg.as_deref(),
                Some(context),
                arg_was_quoted,
                // Django's `needs_autoescape` input term (#2284) — the third of
                // the three sites. `{% firstof p|safe|linebreaks %}` gets the
                // same answer the `{{ … }}` arms do, or this is #1646 again.
                filters::InputSafety {
                    container: runtime_safe,
                    items: items_safe,
                },
            )?;
            value = new_value;
            // Captured before the reassignment — see the Variable arm (#2283).
            let input_was_safe = runtime_safe;
            // This arm always had LAST-filter semantics and its comment
            // claimed the other two matched it. They did not, until #2259
            // extracted `filter_output_is_safe` and pointed all three at it.
            runtime_safe = filter_output_is_safe(filter_name, produced_safe, input_was_safe);
            items_safe = filter_output_items_are_safe(filter_name, items_safe, input_was_safe);
        }

        return Ok((value, runtime_safe));
    }

    // Try to get from context
    if let Some(value) = context.get(expr) {
        // The context's own `mark_safe` flag, NOT a hard `false` (#2274). The
        // pipe branch above seeds `runtime_safe` from exactly this, so leaving
        // this arm at `false` would mean `{% firstof v %}` escapes while
        // `{% firstof v|lower %}` — one identity filter later — does not.
        // Django's `render_value_in_context` runs `conditional_escape`, which
        // honours `SafeData` with or without a filter.
        return Ok((value.clone(), context.is_safe(expr)));
    }

    // Django's `True` / `False` / `None` have NO arm here, deliberately (#2347).
    //
    // They used to: this function spelled them inline while `Context::resolve`
    // — the resolver `{{ }}` output and the filter-argument channels use — had
    // no arm at all, which is why `{% if True %}` was right and `{{ True }}`
    // rendered the empty string. Same three names, two resolvers, one of them
    // wrong: #1646.
    //
    // The fix put them in `Context::resolve`, and this function ALREADY ends
    // with a `context.resolve(expr)` fallback — so an arm here would be a
    // second mechanism shadowing the first. It was measured as exactly that:
    // with the arm present, gating it off reddened only a source-grep pin,
    // because every behavioural case still resolved through the fallback
    // (#2129/#2135). Deleted rather than tested around (#2233), which leaves
    // one statement of the rule and makes the `{% if %}` / `{% with %}` /
    // `{% firstof %}` operands and `{{ }}` answer from the same place by
    // construction rather than by agreement.
    //
    // Precedence is unchanged and is Django's: `context.get` above wins,
    // because `builtins` is `Context.dicts[0]` and `__getitem__` walks
    // `reversed(self.dicts)`, so a user variable named `True` shadows it.
    //
    // The LOWERCASE spellings below are a djust extension rather than Django
    // parity — `{% if true %}` is an undefined variable to Django and answers
    // False — so they stay HERE, in the tag-operand resolver where they have
    // always been, and are deliberately absent from `template_builtin`, which
    // is exactly the Django set. `Value::None` for `none`, not `Missing`: the
    // two are distinct (#2203) and this spelling denotes the singleton.
    match expr {
        "true" => return Ok((Value::Bool(true), false)),
        "false" => return Ok((Value::Bool(false), false)),
        "none" => return Ok((Value::None, false)),
        _ => {}
    }

    // The numeric and quoted literals, through the ONE helper that recognizes
    // them (#2376). This arm used to spell them inline — an `i64` parse, an
    // `f64` parse, and a quote-strip that reported `false` for safety — while
    // `Node::Variable` had no literal arm AT ALL. Two resolvers, one of them
    // blind: `{% if "<b>" %}` was right and `{{ "<b>" }}` rendered the EMPTY
    // STRING. Exactly #2347's shape, three literal kinds over.
    if let Some(literal) = django_literal(expr) {
        return Ok(literal);
    }

    // Last resort: the getattr walk over raw Python objects that
    // `Context::resolve` adds on top of `Context::get` (#806). The `{{ }}`
    // arm has always used it, so without this arm `{% for x in user.orders %}`
    // resolved a DB relation and `{% for x in user.orders|slice:":5" %}` — the
    // same expression through this function, since #2325 routes the tag here —
    // would not. Placed AFTER the literal arms so nothing that resolves today
    // changes, and so the GIL round-trip it costs is paid only on a genuine
    // miss rather than on every `{% if a == 5 %}` operand.
    if let Some(value) = context.resolve(expr)? {
        return Ok((value, context.is_safe(expr)));
    }

    Ok((Value::Missing, false))
}

/// Does this pair involve an EXACT-DIGIT numeric — `Decimal` or `BigInt`?
/// (#2214, #2260)
///
/// Guards the equality widening so it cannot reach `(Float, Integer)`, which
/// has its own arms — `_ => false` when this guard was written, exact since
/// #2243, and an epsilon in neither case. Ordering (`<`, `>`) needs no such
/// guard: `try_compare` already carried explicit `(Float, Integer)` and
/// `(Integer, Float)` arms before this change, and every remaining combination
/// `numeric_pair` admits involves one of the two exact-digit variants, which
/// have no arms of their own — so nothing without one reaches its wildcard.
fn is_decimal_pair(a: &Value, b: &Value) -> bool {
    // `BigInt` too (#2260): it is the other exact-digit numeric variant, it has
    // no arm of its own here either, and the reason is the same one — before
    // the variant it was a `Float` and reached the `(Float, Integer)` arms.
    let wide = |v: &Value| matches!(v, Value::Decimal(_) | Value::BigInt(_));
    wide(a) || wide(b)
}

/// Both operands as f64, but ONLY when both are genuinely numeric (#2214).
///
/// Deliberately narrower than `ToF64`, which also parses strings: widening `==`
/// and `<`/`>` to strings would make `{% if "5" == 5 %}` true, where Django
/// says false. This exists so a Decimal compares against an Integer or a Float
/// — which it did before the variant, when it was a Float — without opening
/// that door.
fn numeric_pair(a: &Value, b: &Value) -> Option<(f64, f64)> {
    // `BigInt` is admitted for the same reason `Decimal` is, and NOT admitting
    // it was a real regression the #2260 differential caught: a Python int past
    // `i64` used to arrive as a `Float` and take the `(Float, Integer)` arm, so
    // `{% if p > 10 %}` answered `gt`. As a `BigInt` with no arm it fell to this
    // wildcard, got `None`, and the ordering came back 0 — "equal", so BOTH
    // `>` and `<` were false and the template silently took the wrong branch.
    // Exactly the #2244 hole, one variant over.
    let numeric = |v: &Value| {
        matches!(
            v,
            Value::Integer(_) | Value::Float(_) | Value::Decimal(_) | Value::BigInt(_)
        )
    };
    if numeric(a) && numeric(b) {
        Some((a.as_f64()?, b.as_f64()?))
    } else {
        None
    }
}

/// `i64 == f64` with Python's exact semantics (#2243).
///
/// The obvious spelling is `a as f64 == b`, and it is wrong above 2^53, where
/// the cast rounds: `9007199254740993 as f64` IS `9007199254740992.0`, so the
/// comparison answers true for two values Python calls different. Python
/// compares an int to a float without converting either — an int equals a float
/// only when the float is a whole number naming that same integer — so this
/// goes the other way and converts the FLOAT, which is always exact when it
/// succeeds.
///
/// A non-finite or fractional float can equal no integer; neither can a whole
/// float outside `i64`'s range, which is the only case the range guard exists
/// for (`b as i64` saturates rather than wrapping, so without it `1e300` would
/// compare equal to `i64::MAX`).
fn int_eq_float(a: i64, b: f64) -> bool {
    if !b.is_finite() || b.fract() != 0.0 {
        return false;
    }
    // -2^63 exactly; its negation is 2^63, one past `i64::MAX`. Both are exact
    // in f64, so the bounds are precise rather than approximate.
    let min = i64::MIN as f64;
    if b < min || b >= -min {
        return false;
    }
    b as i64 == a
}

/// A bool as the integer Python says it is, or `None` for anything else (#2244).
///
/// `bool` subclasses `int` in Python: `True` IS `1` numerically, so `True == 1`,
/// `False == 0`, `True > 0` and `True == Decimal('1')` are all true and Django
/// says so. Substituting the integer — rather than adding a pairwise arm per
/// numeric type — is exactly what Python does, and it routes a bool through the
/// SAME arm its integer value takes, so the two can never drift (#1646).
///
/// The two traps `int_eq_float` documents do not bite here, and it is worth
/// saying why rather than inheriting its guards blind: a bool is only ever 0 or
/// 1, so there is no float residue near it to mistake for zero and nothing
/// anywhere near 2^53 to round. The substitution inherits the exact comparison
/// regardless, by going THROUGH the Integer arms rather than around them.
fn bool_as_int(v: &Value) -> Option<Value> {
    match v {
        Value::Bool(b) => Some(Value::Integer(i64::from(*b))),
        _ => None,
    }
}

fn values_equal(a: &Value, b: &Value) -> bool {
    // A bool against a NON-bool is compared as the integer it is (#2244).
    //
    // Bool-vs-bool is excluded because it has its own arm below with the same
    // answer — excluding it keeps that arm live rather than dead, and bounds
    // this at one substitution per side (after either, that operand is an
    // `Integer` and cannot match again).
    match (bool_as_int(a), bool_as_int(b)) {
        (Some(_), Some(_)) | (None, None) => {}
        (Some(a), None) => return values_equal(&a, b),
        (None, Some(b)) => return values_equal(a, &b),
    }
    match (a, b) {
        // BOTH variants, same as `values_identity` below (#2203 review). The
        // `None` literal resolves to `Value::None` and Python None converts to
        // it, so an arm matching only `Missing` made `{% if x == None %}`
        // unconditionally FALSE — a regression against Django and against the
        // previous release. `values_identity` got this arm and `values_equal`,
        // nineteen lines above it in the same file, did not: #1646 drift
        // between two functions one commit touched.
        (Value::Missing | Value::None, Value::Missing | Value::None) => true,
        (Value::Bool(a), Value::Bool(b)) => a == b,
        (Value::Integer(a), Value::Integer(b)) => a == b,
        (Value::Float(a), Value::Float(b)) => floats_equal(*a, *b),
        // Mixed int/float, EXACTLY — `{% if x == 0 %}` on `0.0` (#2243).
        //
        // `try_compare` has carried `(Integer, Float)` and `(Float, Integer)`
        // arms all along, so `{% if x > 0 %}` was right for a float while
        // `{% if x == 0 %}` fell to `_ => false` and was unconditionally wrong.
        // Django says true, because Python does.
        //
        // NOT the epsilon those ordering arms use, and not the one two lines
        // above: an absolute tolerance here answers true for `0.1 + 0.2 - 0.3`
        // (`5.55e-17`), a float residue silently taking the wrong branch. That
        // shipped briefly in #2240 and was reverted as a worse bug than this
        // one. `(Float, Float)` keeps its epsilon — a separate question (#1079).
        (Value::Integer(a), Value::Float(b)) => int_eq_float(*a, *b),
        (Value::Float(a), Value::Integer(b)) => int_eq_float(*b, *a),
        (Value::String(a), Value::String(b)) => a == b,
        // Two sequences of the SAME kind: same length, pairwise equal (#2335).
        // Without this arm two lists fell to `_ => false` and were never equal
        // — not even to themselves — so `{% if a == b %}` silently took the
        // `{% else %}` branch, the direction that HIDES content.
        //
        // Recursion, not element-wise `==`, is what carries the numeric
        // widening down: `[1] == [1.0]` and `[True] == [1]` are both true in
        // Python, and both go through the same `bool_as_int` / `int_eq_float`
        // arms above rather than a second copy of those rules (#1646).
        //
        // List-against-Tuple is deliberately NOT matched. Python's `[1] ==
        // (1,)` is **False**, so a "both are sequences" arm would be wrong in
        // the one direction a curated test is least likely to cover; the
        // randomised differential in
        // `test_dict_iteration_and_sequence_equality_2334_2335.py` samples the
        // cross-type case explicitly.
        (Value::List(a), Value::List(b)) | (Value::Tuple(a), Value::Tuple(b)) => {
            a.len() == b.len() && a.iter().zip(b.iter()).all(|(x, y)| values_equal(x, y))
        }
        // Python compares dicts by key/value pairs, ORDER-INDEPENDENTLY:
        // `{"a": 1, "b": 2} == {"b": 2, "a": 1}` is true. `IndexMap`'s own
        // `PartialEq` agrees, but is spelled out here so the element
        // comparison recurses through `values_equal` (numeric widening again)
        // rather than through `Value`'s — which it does not derive.
        (Value::Object(a), Value::Object(b)) => {
            a.len() == b.len()
                && a.iter()
                    .all(|(k, v)| b.get(k).is_some_and(|other| values_equal(v, other)))
        }
        // Two members of the datetime family (#2471). Without this arm two
        // `Encoded`s fell to `_ => false` and were never equal — not even to
        // themselves — so `{% if a == b %}` on a datetime against ITSELF took
        // the `{% else %}` branch, the direction that HIDES content. The exact
        // shape #2335 fixed for lists, one variant later; `Value::Encoded`
        // arrived in #2448 and got neither this arm nor `try_compare`'s.
        //
        // Through `encoded_equal`, which reaches the ordering for every class
        // that HAS one — so equality is `Some(Equal)` and not a second rule
        // that could drift from the ordering one, the failure #2244/#2243/#2335
        // each had. A pair Python refuses (a `date` against a `datetime`, a
        // naive against an aware, a `set` against a `complex`) answers `None`
        // there and so stays `false` here, which is what Django answers and
        // what this wildcard already answered before the arm existed.
        (Value::Encoded(a), Value::Encoded(b)) => encoded_equal(a, b),
        // An opaque NUMBER against a real one (#2480). `complex(0) == 0` is
        // True in Python and Django, and no `(Encoded, Encoded)` arm reaches
        // it. A `Bool` has already become an `Integer` at the top of this
        // function, so `{% if p == True %}` on a `complex(1)` lands here too.
        //
        // Deliberately NOT extended to `Decimal` or `BigInt`: both are exact
        // types whose Python comparison against a `complex` is exact as well,
        // and an `f64` cannot answer it. Those cells stay open and are pinned
        // as declined rather than answered approximately.
        (Value::Encoded(e), Value::Integer(i)) | (Value::Integer(i), Value::Encoded(e)) => {
            encoded_equals_integer(e, *i)
        }
        (Value::Encoded(e), Value::Float(f)) | (Value::Float(f), Value::Encoded(e)) => {
            matches!(e.eq_class, Some(EqClass::Number { real, imag }) if imag == 0.0 && real == *f)
        }
        // Pairs involving a DECIMAL, and only those. Without this
        // `{% if p == 19.99 %}` went false the moment a Decimal stopped being a
        // Float (#2214).
        //
        // The `is_decimal_pair` restriction is load-bearing and was missing.
        // `numeric_pair` alone also catches `(Float, Integer)` — no Decimal in
        // sight — which on the previous release fell to `_ => false` and now
        // took an absolute `f64::EPSILON` tolerance. That silently changed
        // `{% if delta == 0 %}` for ordinary float residues: `0.1 + 0.2 - 0.3`
        // is `5.55e-17`, which Django calls non-zero and this called zero.
        //
        // A scope leak, not a design choice — the comment here even said "every
        // pair involving a Decimal" while the code did more, which is how it
        // survived review twice (#1079, #1867).
        _ if is_decimal_pair(a, b) => match numeric_pair(a, b) {
            Some((a, b)) => floats_equal(a, b),
            None => false,
        },
        // Everything else keeps the pre-#2214 answer. A guarded arm is not
        // exhaustive, so this arm is still reachable — but no longer for
        // `(Float, Integer)`, which has had its own exact arms since #2243.
        _ => false,
    }
}

/// Python's ordering for two [`Encoded`]s, or `None` where Python refuses
/// (#2480).
///
/// **The ONE reader of `Encoded::python_partial_cmp`**, and the one place the
/// `Encoded` half of `==` / `<` / `<=` / `>` / `>=` is decided. Its three
/// callers — [`values_equal`] (through [`encoded_equal`]), [`try_compare`] and
/// `filters::compare_sort_values` — reach every class through here, so an
/// operator cannot drift from its neighbours (#1646). That is the same
/// property `python_partial_cmp` had before this wrapper existed; the wrapper
/// exists because the [`EqClass::Set`] order compares carried ITEMS with
/// Django's `==`, which is [`values_equal`] — a function `djust_core` does not
/// have and deliberately will not grow (`Value` has no `PartialEq`).
///
/// # The classes that order, and the ones that must not
///
/// | class | `==` | `<` `<=` `>` `>=` |
/// |---|---|---|
/// | `EqClass::Set` | items, both ways | SUBSET, a real partial order |
/// | `EqClass::Number` | components | **never** — `complex(0) < complex(0)` RAISES |
/// | `EqClass::Identity` | the repr token | **never** — `object() < object()` RAISES |
/// | `None` (the datetime family) | `cmp_key` | `cmp_key` |
///
/// So the Number and Identity classes answer `None` HERE and are answered by
/// [`encoded_equal`] instead. Giving them a key to reach equality — the
/// obvious one-line version of this fix — would turn
/// `{% if p <= q %}` on a `complex(0)` from Django's `N` into `Y`, trading
/// eight closed cells for a new divergence.
///
/// A cross-class pair is `None`, which is right: `set() < complex(0)` raises
/// in Python and `set() == complex(0)` is False.
pub(crate) fn encoded_partial_cmp(a: &Encoded, b: &Encoded) -> Option<std::cmp::Ordering> {
    match (a.eq_class, b.eq_class) {
        (Some(EqClass::Set), Some(EqClass::Set)) => set_partial_cmp(a, b),
        // Either side carrying a class that does not order — including a Set
        // against a datetime — answers `None`, the pre-#2480 answer.
        (Some(_), _) | (_, Some(_)) => None,
        // The datetime family, unchanged: `Encoded::python_partial_cmp` is
        // still the whole of it.
        (None, None) => a.python_partial_cmp(b),
    }
}

/// Python's `==` for two [`Encoded`]s (#2480).
///
/// Equality goes through [`encoded_partial_cmp`] for every class that HAS an
/// ordering, so those two can never disagree; the two equality-only classes
/// are the arms here, and they are the reason this is a separate function
/// rather than a `== Some(Equal)` at the call site.
fn encoded_equal(a: &Encoded, b: &Encoded) -> bool {
    match (a.eq_class, b.eq_class) {
        // Two numbers: the components, exactly. `f64`'s own `==` is Python's
        // — `0.0 == -0.0` is true on both, and a NaN component is equal to
        // nothing, which is also Python's answer.
        (
            Some(EqClass::Number { real: ra, imag: ia }),
            Some(EqClass::Number { real: rb, imag: ib }),
        ) => ra == rb && ia == ib,
        // Two identity-semantics objects: the default `repr` carries the
        // address, so the token IS the identity. See `Encoded::eq_class` for
        // the address-reuse caveat and why it cannot bite within one render.
        (Some(EqClass::Identity), Some(EqClass::Identity)) => a.repr == b.repr,
        // Everything else — two Sets, two datetimes, and every CROSS-class
        // pair (which answers `None` there, i.e. false, exactly as Python
        // says `set() != complex(0)`).
        _ => encoded_partial_cmp(a, b) == Some(std::cmp::Ordering::Equal),
    }
}

/// The most items either side may carry before [`set_partial_cmp`] DECLINES.
///
/// Containment without a hash is quadratic, and a `set` states its own length
/// so `opaque_value` enumerates it in full however large it is — a template
/// comparing two 100k-element sets would otherwise do 10^10 [`values_equal`]
/// calls inside a render. Past this the answer is `None`: the pre-#2480
/// behaviour (never equal, never ordered), which is a cell left open rather
/// than a wrong answer.
///
/// A hash-based version is not available here: the items are [`Value`]s, whose
/// equality is [`values_equal`] — which equates `1` with `1.0` and so is not a
/// hash-compatible relation without a canonicalisation this fix does not need.
const SET_COMPARE_CAP: usize = 1_000;

/// Python's `set` ordering — CONTAINMENT, which is partial (#2480).
///
/// `collections.abc.Set` defines `__le__` as "every element of self is in
/// other" and `__eq__` as `len(self) == len(other) and self <= other`, so both
/// directions of containment answer all five operators at once:
///
/// ```text
/// a ⊆ b and b ⊆ a  ->  Equal      {'a'} == {'a'},  set() == frozenset()
/// a ⊆ b only       ->  Less       set() < {'a'}
/// b ⊆ a only       ->  Greater
/// neither          ->  None       {1} vs {2}: all four operators False
/// ```
///
/// `None` for the incomparable case is exactly Python's answer, and is why the
/// partial order can live in an `Option<Ordering>` at all — every caller
/// already renders `None` as "false for all four ordering operators", which is
/// what `{% if {1} < {2} %}` must do.
///
/// Element equality is [`values_equal`] and not a structural compare, because
/// Python's is: `{1} == {1.0}` is True.
fn set_partial_cmp(a: &Encoded, b: &Encoded) -> Option<std::cmp::Ordering> {
    // A Set is iterable by construction, so `items` is `Some` for every value
    // this crate builds. It can be `None` for one restored from a wire width
    // that predates the field — which also predates `eq_class`, so this is
    // unreachable today and is a decline rather than a panic.
    let (Some(a_items), Some(b_items)) = (&a.items, &b.items) else {
        return None;
    };
    if a_items.len() > SET_COMPARE_CAP || b_items.len() > SET_COMPARE_CAP {
        return None;
    }
    let contains = |hay: &[Value], needle: &Value| hay.iter().any(|x| values_equal(x, needle));
    let a_in_b = a_items.iter().all(|x| contains(b_items, x));
    let b_in_a = b_items.iter().all(|x| contains(a_items, x));
    match (a_in_b, b_in_a) {
        (true, true) => Some(std::cmp::Ordering::Equal),
        (true, false) => Some(std::cmp::Ordering::Less),
        (false, true) => Some(std::cmp::Ordering::Greater),
        (false, false) => None,
    }
}

/// Does this opaque NUMBER equal a Python `int`? (#2480)
///
/// EXACTLY, which is what Python does: `complex(2**53) == 2**53 + 1` is
/// **False** even though `float(2**53 + 1)` rounds to the value the complex
/// holds. So the comparison runs in the integer domain — the carried `f64` is
/// converted back to an `i64` and must round-trip — rather than casting the
/// `i64` to an `f64`, which would answer True for that pair.
fn encoded_equals_integer(e: &Encoded, i: i64) -> bool {
    let Some(EqClass::Number { real, imag }) = e.eq_class else {
        return false;
    };
    // Through [`int_eq_float`] rather than a second spelling of it. That
    // function already IS "Python's exact int-against-float", including the two
    // traps a hand-rolled version gets wrong — the `2**53 + 1` rounding and the
    // saturating `as i64` at `1e300` — and a belt-and-braces copy beside it is
    // one fix plus one decoration that no test can separate (v1.1.1-2 rule 3).
    imag == 0.0 && int_eq_float(i, real)
}

/// Django identity comparison for the `is` / `is not` template operators.
///
/// Mirrors Python's `is`: identity holds only for the singletons
/// `None`, `True`, and `False`. Arbitrary equal values (`5 is 5`,
/// `"a" is "a"`) are NOT contractually identical — CPython interning is
/// an implementation detail templates must not rely on — so non-singleton
/// types always return false. This is intentionally stricter than
/// [`values_equal`].
fn values_identity(a: &Value, b: &Value) -> bool {
    match (a, b) {
        // `Missing` and `None` are DIFFERENT values (#2203) but both satisfy
        // `is None`. Django reaches the same place from the other side: an
        // absent variable resolves to `None` in its expression machinery, so
        // `{% if absent is None %}` is true there too. Keeping them distinct
        // for RENDERING while equating them for `is None` is what preserves
        // both behaviours.
        (Value::Missing | Value::None, Value::Missing | Value::None) => true,
        (Value::Bool(a), Value::Bool(b)) => a == b,
        // Non-singletons: Python `is` is not identity-stable; treat as false.
        _ => false,
    }
}

/// Order two floats as Python does — the ONE place that decides it (#2349).
///
/// Four arms of [`try_compare`] used to spell
/// `if (a - b).abs() < f64::EPSILON { 0 } else if a < b { -1 } else { 1 }`
/// inline, and that idiom is **undefined for a non-finite operand**:
/// `(inf - inf)` is NaN and every comparison against NaN is false, so the
/// tolerance answered "not equal" and the chain fell through its `else` to
/// "greater". Every NaN pair therefore answered TRUE for `>` and `>=`, where
/// Python answers False for all four operators — 22 of #2349's 26 divergent
/// cells.
///
/// # `is_nan`, NOT `!is_finite`
///
/// A NaN is not the same thing as a pair Python refuses to order. `"a" >= 1`
/// RAISES `TypeError`, which Django catches and resolves False — that is
/// #2338, and [`try_compare`] answers `None` for it. `float("nan") >= float("nan")`
/// raises nothing: it evaluates, and returns False. Different mechanism, same
/// vehicle — `None` means "all four operators are false", which is exactly
/// Python's answer for a NaN.
///
/// `±inf` orders NORMALLY in Python (`-inf < 1 < inf` are all True), so the
/// guard is `is_nan` and not `!is_finite`. Getting that wrong would trade 22
/// divergent cells for a different set.
///
/// # Why exact `==` comes before the epsilon
///
/// `inf == inf` is True in Python and the epsilon cannot say so. The exact
/// check is a no-op for a finite pair — `a == b` implies `|a - b| == 0`, which
/// the epsilon already calls equal — so it changes nothing except the case the
/// epsilon is undefined for.
///
/// Whether the epsilon should exist for FINITE floats at all is an older and
/// separate question (#2243 documents it as deliberately out of scope), and it
/// stays out of scope here: this function keeps that behaviour byte for byte
/// for any pair of finite floats.
fn order_floats(a: f64, b: f64) -> Option<i32> {
    if a.is_nan() || b.is_nan() {
        return None;
    }
    if a == b {
        return Some(0);
    }
    // The tolerance, unguarded — and that is a decision, not an oversight.
    //
    // A first version wrote `a.is_finite() && b.is_finite() && …` here. The
    // gate-off then found the guard SURVIVED its own mutation, which is a
    // question rather than a pass (#2129/#2135 + the v1.1.1-2 rule): it turned
    // out to be a provable semantic no-op, so it was deleted rather than
    // tested around (#2233).
    //
    // The proof: control only reaches this line when neither operand is NaN
    // AND `a != b`. If either is `±inf` then `a - b` cannot be NaN — that
    // needs `inf - inf` with the SAME sign, which `a != b` excludes — so it is
    // `±inf`, and `inf < f64::EPSILON` is false. The tolerance therefore
    // cannot fire for a non-finite pair whatever it is written as, and the
    // guard only implied a hazard that does not exist.
    if (a - b).abs() < f64::EPSILON {
        return Some(0);
    }
    Some(if a < b { -1 } else { 1 })
}

/// Are two floats equal as Python has it — the ONE place that decides it
/// (#2349).
///
/// [`values_equal`]'s `(Float, Float)` arm and its `is_decimal_pair` wildcard
/// both spelled the epsilon inline, and `inf == inf` came out **False** where
/// Django and Python say True: `(inf - inf)` is NaN, so the tolerance test is
/// false. `{% if x == y %}` on two infinities silently took the `{% else %}`
/// branch, and `float("inf")` is an ordinary value a view can hold.
///
/// The NaN answer was right only BY ACCIDENT. `nan == nan` is False in Python,
/// and the epsilon produced False for the same undefined-comparison reason that
/// made `inf` wrong — so a future change to the tolerance would have flipped it
/// silently with no test failing. The branch below makes the right answer
/// intentional: for a non-finite operand, IEEE `==` IS Python's answer —
/// `nan == nan` false, `inf == inf` true, `inf == 1.0` false, `inf == -inf`
/// false.
///
/// The epsilon for finite floats is untouched and out of scope (#2243).
fn floats_equal(a: f64, b: f64) -> bool {
    if !a.is_finite() || !b.is_finite() {
        return a == b;
    }
    (a - b).abs() < f64::EPSILON
}

/// Order two values as Python does: `Some(-1 | 0 | 1)`, or **`None` when
/// Python cannot order them at all** (#2338).
///
/// The `None` is the whole point, and returning an `i32` with 0 standing in for
/// it is the bug this replaced. 0 reproduced Django exactly for `>` and `<` —
/// Python raises `TypeError`, Django's `{% if %}` catches it and answers False,
/// and 0 makes both of those false — but `>=` and `<=` read the same 0 as
/// "equal" and answered **True** for every pair with no ordering arm: a string
/// against an int, a list against a tuple, a dict against anything, two
/// `None`s. Silent, and permissive: `{% if x >= threshold %}` opened its gate
/// on operands that cannot be compared.
///
/// So there is deliberately NO `compare_values(a, b) -> i32` wrapper left. One
/// existed briefly in #2335 and was removed before merge precisely because,
/// with every caller reading only the `i32`, the `Option` was observationally
/// equivalent to 0 — a second mechanism shadowing the first. The `Option` is
/// only worth having if the operators consume it, so all four do
/// (`is_some_and`) and nothing else may re-collapse it.
fn try_compare(a: &Value, b: &Value) -> Option<i32> {
    // Ordering has the same hole `values_equal` had, from the other side
    // (#2244): there is no `Bool` arm here at all, so `{% if flag > 0 %}` fell
    // to `numeric_pair`, which admits only the numeric variants, returned
    // `None`, and yielded 0 — "equal", so BOTH `>` and `<` were false while
    // `>=` and `<=` were both true. Django says `True > 0`.
    //
    // #2260 hit exactly this again with `BigInt`, which was likewise not on
    // that admitted list: the hole is a per-variant one, and it reopens for
    // every variant added without a decision here.
    //
    // Unlike `values_equal` there is no bool-vs-bool arm to defer to, so this
    // covers that pair too: `{% if a > b %}` on `True`/`False` was 0/"equal"
    // and is now 1, which is what Django answers.
    match (bool_as_int(a), bool_as_int(b)) {
        (None, None) => {}
        (Some(a), Some(b)) => return try_compare(&a, &b),
        (Some(a), None) => return try_compare(&a, b),
        (None, Some(b)) => return try_compare(a, &b),
    }
    match (a, b) {
        (Value::Integer(a), Value::Integer(b)) => Some(a.cmp(b) as i32),
        (Value::Float(a), Value::Float(b)) => order_floats(*a, *b),
        // Allow comparing integers and floats
        (Value::Integer(a), Value::Float(b)) => order_floats(*a as f64, *b),
        (Value::Float(a), Value::Integer(b)) => order_floats(*a, *b as f64),
        (Value::String(a), Value::String(b)) => Some(a.cmp(b) as i32),
        // Lexicographic, as Python orders two sequences of the same kind
        // (#2335): the first differing element decides, and if one is a prefix
        // of the other the shorter is smaller. The mirror of the `values_equal`
        // arm above, and it must stay in step with it — the two answered
        // differently for a list on the previous release, which is the shape
        // #2244 and #2243 both had.
        //
        // Cross-kind (list vs tuple) and `Object` are deliberately absent:
        // Python RAISES `TypeError` for both, so they fall to the wildcard and
        // come back `None` — false for all four of `<`, `>`, `<=`, `>=`, which
        // is what Django answers.
        //
        // Python's own algorithm, not an approximation of it: it walks with
        // `==`, and ONLY AN EQUAL PAIR CONTINUES the walk. That one rule
        // carries both halves.
        //
        // `[{}, 1] < [{}, 2]` is True in Python even though two dicts cannot
        // be ORDERED — they never need to be, because they are equal, so the
        // walk moves on. And an unequal pair DECIDES the comparison, whatever
        // it answers — including the `None` an incomparable pair yields, which
        // propagates out of the WHOLE comparison rather than being read as a
        // tie. `[[], 'a', ('b',)] >= [1]` is False in Python for the same
        // reason `>` is: the first pair raises, and the length never enters it.
        //
        // The first draft asked for an ordering FIRST and continued whenever
        // it answered 0 — which conflates "equal" with "incomparable" and
        // falls through to the length tie-break, so
        // `[[], 'a', ('b',)] > [1]` answered True (3 elements beats 1) where
        // Django answers False. The randomised differential caught it in 27 of
        // 28,500 cells; no curated case here had the shape. Returning `None`
        // rather than 0 is what keeps that closed on the `>=` / `<=` side too,
        // where a 0 would have re-opened it as "equal" (#2338).
        (Value::List(a), Value::List(b)) | (Value::Tuple(a), Value::Tuple(b)) => {
            for (x, y) in a.iter().zip(b.iter()) {
                if values_equal(x, y) {
                    continue;
                }
                return try_compare(x, y);
            }
            Some(a.len().cmp(&b.len()) as i32)
        }
        // Two members of the datetime family (#2471). The mirror of
        // `values_equal`'s arm and the SAME call, so the two cannot answer
        // differently — the drift #2244, #2243 and #2335 each shipped once.
        //
        // Without it `{% if a < b %}` on two `timedelta`s fell to
        // `numeric_pair`, which admits no `Encoded`, and answered `None` — so
        // `<` and `>` were both false and the template silently took the wrong
        // branch. A pair Python cannot order stays `None`, which is Django's
        // own answer (`smart_if` swallows the `TypeError` to False).
        (Value::Encoded(a), Value::Encoded(b)) => encoded_partial_cmp(a, b).map(|ord| ord as i32),
        // No `(Missing, Missing) => 0` arm, and its absence is deliberate
        // (#2338): Python's `None < None` RAISES, so Django answers False for
        // all four operators, and the 0 this used to return made `>=` and `<=`
        // both true. `{% if a >= b %}` over two variables the context does not
        // define now answers False, as Django does. `==` is unaffected — it
        // goes through `values_equal`, where `Missing`/`None` ARE equal,
        // matching Django's `ignore_failures` resolution of an absent variable
        // to `None`.
        //
        // Any remaining numeric pair — which is every pair involving a Decimal
        // or a BigInt (#2214, #2260). This is the `{% if p > 10 %}` case:
        // without it the fallthrough is `None`, `>` and `<` both fail, and the
        // template silently takes the wrong branch. It is the second of the two
        // regressions measured against serializing a Decimal as a plain string.
        _ => match numeric_pair(a, b) {
            Some((a, b)) => order_floats(a, b),
            // Python cannot order this pair: `None`, so every operator is false.
            None => None,
        },
    }
}

/// `{% firstof %}`'s value — the first truthy operand, already escaped.
///
/// `None` means every operand was falsy, which renders as the empty string and
/// assigns the empty string. Extracted from the render arm so the `as <var>`
/// form computes it exactly once, from one definition, rather than the arm and
/// the assignment growing their own copies (CLAUDE.md #1646).
///
/// Uses `get_value_safe` for dotted-path support (e.g. `user.name`) AND to
/// thread the runtime-safe flag (#1672): a custom filter that `mark_safe()`s
/// at runtime (e.g. `{% firstof a|md %}`) must NOT be re-escaped, matching the
/// `Variable` / `InlineIf` arms (#1660). `runtime_safe` is true ONLY when the
/// LAST filter produced a genuine `SafeString` → fail-safe.
fn first_of(args: &[String], context: &Context) -> Result<Option<String>> {
    for arg in args {
        let (val, runtime_safe) = get_value_safe_ignoring_failures(arg.trim(), context)?;
        if val.is_truthy() {
            let text = val.to_string();
            return Ok(Some(if runtime_safe {
                text
            } else {
                filters::html_escape(&text)
            }));
        }
    }
    Ok(None)
}

/// `{% widthratio %}`'s value, arm for arm with Django's `WidthRatioNode`.
///
/// Before #2355 this was `to_f64().unwrap_or(0.0)` three times over, so every
/// non-numeric operand answered `0` where Django answers the empty string —
/// 16,006 of the 17,298 cells the `widthratio` shape builds. The corpus could
/// not report it because it built no `widthratio` cell at all.
///
/// Django's order is load-bearing and reproduced here rather than tidied:
///
/// * `int(max_width)` happens FIRST, and its failure is a
///   `TemplateSyntaxError` — not the empty string. A raise is the harder
///   answer, and it is Django's.
/// * `float(value)` / `float(max_value)` failing is a `ValueError`, caught,
///   and the result is `""`. `Value::Missing` reaches that arm the same way
///   Django's does: `FilterExpression` swallows `VariableDoesNotExist` into
///   `string_if_invalid` (`""`), and `float("")` raises.
/// * a zero divisor is `"0"`, and it is checked as `== 0.0` so `-0.0` is
///   included — Python raises `ZeroDivisionError` for both.
/// * a non-finite ratio is `""`: `round(inf)` raises `OverflowError` and
///   `round(nan)` raises `ValueError`, and Django catches both.
fn width_ratio(value: &str, max_value: &str, max_width: &str, context: &Context) -> Result<String> {
    let max_w = py_int(&get_value(max_width, context)?).ok_or_else(|| {
        DjangoRustError::TemplateError("widthratio final argument must be a number".to_string())
    })?;
    let (Some(val), Some(max_val)) = (
        get_value(value, context)?.to_f64(),
        get_value(max_value, context)?.to_f64(),
    ) else {
        return Ok(String::new());
    };
    if max_val == 0.0 {
        return Ok("0".to_string());
    }
    Ok(py_round_to_string(val / max_val * max_w).unwrap_or_default())
}

/// `int(v)` for the one operand Django applies it to, or `None` for a raise.
///
/// Deliberately NOT `to_f64().map(|f| f as i64)`: Python's `int()` rejects a
/// string that merely LOOKS numeric (`int("100.6")` is a `ValueError` while
/// `float("100.6")` is fine), and that difference is the whole reason Django
/// treats this operand's failure as a syntax error rather than an empty
/// render.
///
/// Through the `int(value)` chokepoint since #2435. This was a FOURTH spelling
/// of `int()` and had drifted from the other three in two measurable ways:
/// `s.trim().parse::<i64>()` refused `"1_0"`, which Python reads as 10, and it
/// carried the result in an `i64` — so `{% widthratio 10 2 p %}` on a 31-digit
/// Python `int` rendered `46116860184273879040`, a number that appears nowhere
/// in the calculation, where Django renders `4999999999999999817948147482624`.
/// An `f64` is the honest carrier: Django's own `(value / max_value) *
/// max_width` promotes this operand to a float anyway.
///
/// Django's `except (ValueError, TypeError)` around this `int()` does not name
/// `OverflowError`, so `{% widthratio 10 2 inf %}` raises that instead of the
/// syntax error. Both engines refuse the template; only the exception's name
/// differs, and it is named here rather than modelled.
fn py_int(value: &Value) -> Option<f64> {
    crate::filters::python_int_value(value)
        .ok()
        .and_then(|digits| digits.parse::<f64>().ok())
}

/// `str(round(x))`, or `None` where Python's `round` raises.
///
/// `f64::round` is half-AWAY-FROM-ZERO and Python's `round` is
/// half-to-EVEN, so they disagree on every exact `.5`: `round(2.5)` is `2` in
/// Python and `3` in Rust. Measured, not remembered — `{% widthratio 1 2 5 %}`
/// renders `2` in Django and rendered `3` here.
///
/// The halfway correction is guarded on `|x| < 2^52` because above that a
/// double has no fractional part at all, so the branch cannot apply and
/// `x.trunc() as i64` would be a saturating cast rather than a rounding one.
/// The result is formatted with `{:.0}` on an already-integral float, which
/// prints a double's exact integer value however large — Python's `str(int)`
/// does the same, and `1e20|first`-shaped inputs really do reach here.
fn py_round_to_string(x: f64) -> Option<String> {
    if !x.is_finite() {
        return None; // OverflowError for inf, ValueError for nan — both caught.
    }
    let truncated = x.trunc();
    let rounded = if x.abs() < 4_503_599_627_370_496.0 && (x - truncated).abs() == 0.5 {
        let low = truncated;
        let high = truncated + x.signum();
        if (low as i64) % 2 == 0 {
            low
        } else {
            high
        }
    } else {
        x.round()
    };
    let text = format!("{rounded:.0}");
    // `format!("{:.0}", -0.0)` is `"-0"`; Python's `str(round(-0.4))` is `"0"`.
    Some(if text == "-0" { "0".to_string() } else { text })
}

#[cfg(test)]
mod asvar_standalone_tests {
    //! `render_node_with_loader` on an `as <var>` node emits NOTHING (#2355).
    //!
    //! Every template-reachable path routes these nodes through
    //! `sibling_updates` instead — a `{% for %}` body, an `{% if %}` branch, a
    //! `{% with %}` block and a `{% spaceless %}` block all render their
    //! children with a sibling-aware loop, so the render arm's `asvar` check
    //! never fires from a template. It is kept for the same reason
    //! `Node::AssignTag`'s standalone arm is — a direct caller of this public
    //! function has no sibling to hand a mutated context to, and the one
    //! honest answer is the empty string rather than the value Django
    //! assigns silently. These are the tests that make that arm reachable, so
    //! it is a mechanism rather than a decoration.

    use super::*;

    fn render(node: &Node, context: &Context) -> String {
        render_node_with_loader::<NoOpLoader>(node, context, None).unwrap()
    }

    #[test]
    fn widthratio_with_asvar_renders_nothing_when_rendered_alone() {
        let mut context = Context::new();
        context.set("p".to_string(), Value::Integer(5));
        let with_asvar = Node::WidthRatio {
            value: "p".into(),
            max_value: "10".into(),
            max_width: "100".into(),
            asvar: Some("w".into()),
        };
        let without = Node::WidthRatio {
            value: "p".into(),
            max_value: "10".into(),
            max_width: "100".into(),
            asvar: None,
        };
        // The gate-off sibling: the same node WITHOUT `as` must render the
        // value, so "renders nothing" cannot pass for an unrelated reason.
        assert_eq!(render(&without, &context), "50");
        assert_eq!(render(&with_asvar, &context), "");
    }

    #[test]
    fn an_asvar_node_carries_the_assign_tags_wildcard_dependency() {
        //! A context-mutating node must always re-render under partial render.
        //!
        //! `Node::AssignTag` emits `"*"` for exactly this reason — it mutates
        //! the context for LATER SIBLINGS, so skipping it because its own
        //! operands are unchanged means the binding never happens and every
        //! sibling that reads the name sees nothing. The `as <var>` forms have
        //! the identical effect and so need the identical dep (#2355), which
        //! the first version of that fix missed.
        //!
        //! The EMITTING form is the gate-off sibling: it has no such effect
        //! and must keep its precise dep set, so `"*"` there would be a
        //! silent performance regression on every `{% widthratio %}` in a
        //! partially-rendered template.
        use crate::parser::extract_per_node_deps;

        let emitting = vec![
            Node::WidthRatio {
                value: "p".into(),
                max_value: "10".into(),
                max_width: "100".into(),
                asvar: None,
            },
            Node::FirstOf {
                args: vec!["p".into()],
                asvar: None,
            },
        ];
        for deps in extract_per_node_deps(&emitting) {
            assert!(
                !deps.contains("*"),
                "an emitting form must not be a wildcard"
            );
            assert!(deps.contains("p"), "and must still depend on its operand");
        }

        let assigning = vec![
            Node::WidthRatio {
                value: "p".into(),
                max_value: "10".into(),
                max_width: "100".into(),
                asvar: Some("w".into()),
            },
            Node::FirstOf {
                args: vec!["p".into()],
                asvar: Some("v".into()),
            },
        ];
        for deps in extract_per_node_deps(&assigning) {
            assert!(deps.contains("*"), "an `as <var>` form must be a wildcard");
        }
    }

    #[test]
    fn firstof_with_asvar_renders_nothing_when_rendered_alone() {
        let mut context = Context::new();
        context.set("p".to_string(), Value::String("<b>".to_string()));
        let with_asvar = Node::FirstOf {
            args: vec!["p".into(), "'F'".into()],
            asvar: Some("v".into()),
        };
        let without = Node::FirstOf {
            args: vec!["p".into(), "'F'".into()],
            asvar: None,
        };
        assert_eq!(render(&without, &context), "&lt;b&gt;");
        assert_eq!(render(&with_asvar, &context), "");
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
            // `.trim()` because Python's `float(" 5 ")` is 5.0 — and the one
            // caller is `width_ratio`, whose contract is Python's `float()`.
            //
            // The underscores go through the shared rule (#2435): `float()`
            // accepts `_` between digits, so `{% widthratio "1_0" 2 100 %}` is
            // 500 in Django, and a bare `parse::<f64>()` refused it and
            // rendered nothing.
            Value::String(s) => crate::filters::strip_python_underscores(s.trim())
                .and_then(|cleaned| cleaned.parse::<f64>().ok()),
            Value::Bool(b) => Some(if *b { 1.0 } else { 0.0 }),
            // Delegates rather than re-parsing: `Value::as_f64` is the one
            // definition of what a Decimal is worth numerically (#1646).
            Value::Decimal(_) | Value::BigInt(_) => self.as_f64(),
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
    use indexmap::IndexMap;

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

    // Asserts `<!--dj-if-->` from a default Context: LiveView pin (#2519).
    #[cfg(feature = "liveview")]
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

    // Asserts `<!--dj-if-->` from a default Context: LiveView pin (#2519).
    #[cfg(feature = "liveview")]
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

    // Asserts `<!--dj-if-->` from a default Context: LiveView pin (#2519).
    #[cfg(feature = "liveview")]
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

    // Asserts `<!--dj-if-->` from a default Context: LiveView pin (#2519).
    #[cfg(feature = "liveview")]
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

    // Asserts `<!--dj-if-->` from a default Context: LiveView pin (#2519).
    #[cfg(feature = "liveview")]
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

    // Asserts `<!--dj-if-->` from a default Context: LiveView pin (#2519).
    #[cfg(feature = "liveview")]
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

    // Asserts `<!--dj-if-->` from a default Context: LiveView pin (#2519).
    #[cfg(feature = "liveview")]
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

    // Asserts `<!--dj-if-->` from a default Context: LiveView pin (#2519).
    #[cfg(feature = "liveview")]
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

    // Asserts `<!--dj-if-->` from a default Context: LiveView pin (#2519).
    #[cfg(feature = "liveview")]
    #[test]
    fn test_if_in_dict() {
        // Django: "x in dict" checks dict keys
        let tokens = tokenize("{% if key in mydict %}found{% endif %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();

        let mut map = IndexMap::new();
        map.insert("2".into(), Value::Bool(true));
        map.insert("5".into(), Value::String("hello".to_string()));
        context.set("mydict".to_string(), Value::Object(map));

        // Key exists → found
        context.set("key".to_string(), Value::String("2".to_string()));
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "found");

        // Key does not exist → placeholder
        context.set("key".to_string(), Value::String("99".to_string()));
        // Fix for DJE-053: false {% if %} blocks emit placeholder comment, not empty string
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "<!--dj-if-->");

        // An INTEGER needle does NOT match a STRING key, as Python's
        // `5 == "5"` is False (#2339).
        //
        // This asserted "found" until #2339, on the premise that djust's wire
        // format coerced dict keys to strings and the coercion was the only
        // thing keeping `{% if pk in d %}` alive. Measuring it showed the
        // render path has no such coercion — an int-keyed dict was not a
        // mapping at all — so the coercion protected nothing and only made
        // this cell wrong.
        context.set("key".to_string(), Value::Integer(5));
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "<!--dj-if-->");

        // …and an INT-keyed dict IS matched by an int needle, which is the
        // case the coercion was said to protect and never did.
        let mut int_keyed = IndexMap::new();
        int_keyed.insert(djust_core::ObjectKey::Int(5), Value::Bool(true));
        context.set("mydict".to_string(), Value::Object(int_keyed));
        context.set("key".to_string(), Value::Integer(5));
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "found");
        context.set("key".to_string(), Value::String("5".to_string()));
        assert_eq!(render_nodes(&nodes, &context).unwrap(), "<!--dj-if-->");
    }

    #[test]
    fn test_if_filter_in_dict() {
        // Tests: {% if val|stringformat:"s" in mydict %}
        let tokens =
            tokenize(r#"{% if val|stringformat:"s" in mydict %}found{% else %}nope{% endif %}"#)
                .unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();

        let mut map = IndexMap::new();
        map.insert("42".into(), Value::Bool(true));
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

    // Asserts `<!--dj-if-->` from a default Context: LiveView pin (#2519).
    #[cfg(feature = "liveview")]
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

    // Asserts `<!--dj-if-->` from a default Context: LiveView pin (#2519).
    #[cfg(feature = "liveview")]
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

    // Asserts `<!--dj-if-->` from a default Context: LiveView pin (#2519).
    #[cfg(feature = "liveview")]
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
        let mut user = IndexMap::new();
        user.insert("name".into(), Value::String("Alice".to_string()));
        context.set("user".to_string(), Value::Object(user));
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "Alice");
    }

    // ---- #1692: firstof/cycle must honor the NAME-BASED safe_output_filters
    // whitelist (safe/urlize/...), completing #1660→#1672. ----

    #[test]
    fn test_firstof_safe_filter_not_double_escaped() {
        // {% firstof x|safe %} must NOT be re-escaped — `safe` is a name-based
        // safe_output_filter (matches the Variable arm).
        let tokens = tokenize("{% firstof x|safe %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("x".to_string(), Value::String("<b>hi</b>".to_string()));
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "<b>hi</b>");
    }

    #[test]
    fn test_cycle_urlize_filter_not_double_escaped() {
        // {% cycle x|urlize %} — urlize produces its own <a href=...> HTML; it
        // must not be re-escaped (urlize is a name-based safe_output_filter).
        let tokens = tokenize("{% cycle x|urlize %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set(
            "x".to_string(),
            Value::String("Visit https://example.com".to_string()),
        );
        let result = render_nodes(&nodes, &context).unwrap();
        // urlize's <a href="..."> must survive verbatim, not become &lt;a ...
        assert!(
            result.contains("<a href=\"https://example.com\""),
            "urlize output was re-escaped: {result}"
        );
        assert!(
            !result.contains("&lt;a"),
            "urlize output was double-escaped: {result}"
        );
    }

    #[test]
    fn test_firstof_nonsafe_filter_still_escaped() {
        // {% firstof x|upper %} — `upper` is NOT a safe_output_filter, so HTML
        // in its output must STILL be escaped (fail-safe: only whitelisted
        // names skip escaping).
        let tokens = tokenize("{% firstof x|upper %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("x".to_string(), Value::String("<b>hi</b>".to_string()));
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "&lt;B&gt;HI&lt;/B&gt;");
    }

    #[test]
    fn test_firstof_safe_then_plain_filter_re_taints() {
        // LAST-filter semantics: `{% firstof x|safe|upper %}` — `upper` is the
        // last filter and is NOT safe, so the value is re-tainted and escaped.
        let tokens = tokenize("{% firstof x|safe|upper %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set("x".to_string(), Value::String("<b>hi</b>".to_string()));
        let result = render_nodes(&nodes, &context).unwrap();
        assert_eq!(result, "&lt;B&gt;HI&lt;/B&gt;");
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

    // Asserts `<!--dj-if-->` from a default Context: LiveView pin (#2519).
    #[cfg(feature = "liveview")]
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
        let mut map = IndexMap::new();
        map.insert("key".into(), Value::String("val".to_string()));
        let obj = Value::Object(map);
        let json = serde_json::to_string(&obj).unwrap();
        assert_eq!(json, r#"{"key":"val"}"#);
    }

    #[test]
    fn test_value_scalar_to_string_not_json() {
        // Scalars should use to_string(), not JSON serialization
        assert_eq!(Value::Integer(42).to_string(), "42");
        // `True`, not `"true"` — Python's `str(True)` (#2203).
        assert_eq!(Value::Bool(true).to_string(), "True");
        assert_eq!(Value::String("hello".to_string()).to_string(), "hello");
    }

    // ---- #2042: shared arg-encoding helper contract -----------------------
    // These pin the shared `value_to_arg_string` / `resolve_tag_arg` helpers
    // that ALL THREE tag-dispatch paths (CustomTag, AssignTag, BlockCustomTag)
    // now route through. `resolve_tag_arg` is the exact function the AssignTag
    // and BlockCustomTag arms invoke, so these unit tests exercise that path's
    // resolution logic directly (Python-free). The end-to-end BlockCustomTag
    // dispatch is covered in tests/test_block_custom_tag_arg_json_2042.rs.

    fn obj_ctx() -> Context {
        let mut ctx = Context::new();
        ctx.set(
            "items".to_string(),
            Value::List(vec![
                Value::Integer(1),
                Value::Integer(2),
                Value::Integer(3),
            ]),
        );
        let mut obj = IndexMap::new();
        obj.insert("key".into(), Value::String("val".to_string()));
        ctx.set("obj".to_string(), Value::Object(obj));
        ctx.set("count".to_string(), Value::Integer(42));
        ctx.set("name".to_string(), Value::String("hello".to_string()));
        ctx
    }

    /// `{% for %}`'s own normalisation and `filters::iter_values` answer
    /// RELATED questions, and this pins the exact relation (#1646, #2466).
    ///
    /// The `Node::For` arm does NOT call `iter_values` — it has its own match
    /// that turns a `String` / `Object` / `DictView` / `sized_empty` `Encoded`
    /// into a `List` and lets everything else fall to the refusal arm. Two
    /// implementations of "what does this value iterate to", which is the
    /// shape that keeps coming back here. They agree today; this is what goes
    /// red when one of them grows a shape the other does not.
    ///
    /// They are NOT the same question, and Django is why. `ForNode.render`
    /// reads `__len__` when the object has one; the iterating filters are
    /// comprehensions and call `iter()`. So the for-arm's iterable set is
    /// `iter_values`'s Some-set PLUS `Value::None` (Django's `values is None`
    /// arm) PLUS an `Encoded` that is `sized_empty` without being `iterable` —
    /// a class with a zero `__len__` and no `__iter__`, which renders the
    /// `{% empty %}` block here and raises from `|safeseq`, exactly as it does
    /// on Django.
    ///
    /// Written as an EQUIVALENCE over that stated relation, so a REMOVED arm
    /// reddens it as loudly as an added one — and the `(sized_empty,
    /// !iterable)` sample is what makes the extra term reachable rather than
    /// decorative.
    #[test]
    fn for_iterability_agrees_with_iter_values() {
        let samples = vec![
            Value::Missing,
            Value::None,
            Value::Bool(true),
            Value::Integer(7),
            Value::Float(1.5),
            Value::Decimal("1.50".to_string()),
            Value::BigInt("123456789012345678901".to_string()),
            Value::String("ab".to_string()),
            Value::String(String::new()),
            Value::List(vec![Value::Integer(1)]),
            Value::List(Vec::new()),
            Value::Tuple(vec![Value::Integer(1)]),
            Value::Object(IndexMap::new()),
            Value::DictView {
                kind: djust_core::DictViewKind::Keys,
                items: vec![Value::Integer(1)],
            },
            // A datetime: no `__len__`, `list(dt)` raises, so BOTH probes must
            // say "not iterable".
            Value::Encoded(Box::new(djust_core::Encoded {
                type_name: "datetime.datetime".to_string(),
                display: "2020-01-01 03:04:05".to_string(),
                json: "2020-01-01T03:04:05".to_string(),
                truthy: true,
                len: None,
                iterable: false,
                repr: "datetime.datetime(2020, 1, 1, 3, 4, 5)".to_string(),
                cmp_key: Some(djust_core::CmpKey {
                    domain: djust_core::CMP_DOMAIN_DATETIME_NAIVE,
                    hi: 737425,
                    lo: 11_045_000_000,
                }),
                attrs: Default::default(),
                items: None,
                eq_class: None,
                live: None,
            })),
            // A `set()`: `len` 0 and iterable, so both probes say
            // "iterates to nothing".
            Value::Encoded(Box::new(djust_core::Encoded {
                type_name: "set".to_string(),
                display: "set()".to_string(),
                json: "set()".to_string(),
                truthy: false,
                len: Some(0),
                iterable: true,
                repr: "set()".to_string(),
                cmp_key: None,
                attrs: Default::default(),
                items: Some(vec![]),
                eq_class: None,
                live: None,
            })),
            // A `{'a'}`: truthy, `len` 1, and its item carried (#2477/#2489).
            // Without it every `Encoded` sample here is EMPTY, and the sweep
            // could not tell "the for-arm reads the items" from "the for-arm
            // always renders nothing".
            Value::Encoded(Box::new(djust_core::Encoded {
                type_name: "set".to_string(),
                display: "{'a'}".to_string(),
                json: "{'a'}".to_string(),
                truthy: true,
                len: Some(1),
                iterable: true,
                repr: "{'a'}".to_string(),
                cmp_key: None,
                attrs: Default::default(),
                items: Some(vec![Value::String("a".to_string())]),
                eq_class: None,
                live: None,
            })),
            // A falsy `__iter__` class with NO `__len__`: Django's `ForNode`
            // has no length to read, so it `list()`s the object and renders
            // the item. `len` is `None` here and the for-arm must still
            // iterate — which is why that arm reads `items`, not `len`.
            Value::Encoded(Box::new(djust_core::Encoded {
                type_name: "FalsyIterable".to_string(),
                display: "FalsyIterable()".to_string(),
                json: "FalsyIterable()".to_string(),
                truthy: false,
                len: None,
                iterable: true,
                repr: "FalsyIterable()".to_string(),
                cmp_key: None,
                attrs: Default::default(),
                items: Some(vec![Value::String("x".to_string())]),
                eq_class: None,
                live: None,
            })),
            // A zero-`__len__` class with no `__iter__`: `{% for %}` renders
            // the empty branch, `iter_values` refuses. The one sample that
            // makes the two questions distinguishable.
            Value::Encoded(Box::new(djust_core::Encoded {
                type_name: "LenZero".to_string(),
                display: "<LenZero object>".to_string(),
                json: "<LenZero object>".to_string(),
                truthy: false,
                len: Some(0),
                iterable: false,
                repr: "<LenZero object>".to_string(),
                cmp_key: None,
                attrs: Default::default(),
                items: None,
                eq_class: None,
                live: None,
            })),
        ];
        // `Value::None` and `Value::Missing` are Django's `values is None`
        // arm: `ForNode` turns them into `[]` and renders the empty branch,
        // and `iter_values` answers `Some(vec![])` for `Missing` for the same
        // reason. `None` is the one place the two spellings differ, and it
        // differs in the SAME direction (both render), so it is exercised
        // through the rendered output rather than excluded.
        let mut refusing = 0;
        for value in &samples {
            let mut ctx = Context::new();
            ctx.set("p".to_string(), value.clone());
            // The node is built rather than parsed: this test module has no
            // parser entry point, and the arm under test is the renderer's.
            let node = Node::For {
                var_names: vec!["x".to_string()],
                iterable: "p".to_string(),
                reversed: false,
                nodes: vec![Node::Text("[item]".to_string())],
                empty_nodes: vec![Node::Text("[empty]".to_string())],
            };
            let rendered = render_node_with_loader::<NoOpLoader>(&node, &ctx, None);
            let refuses = rendered.is_err();
            let for_iterable = filters::iter_values(value).is_some()
                || matches!(value, Value::None)
                || matches!(value, Value::Encoded(e) if e.len == Some(0) || e.items.is_some());
            let expected = !for_iterable;
            assert_eq!(
                refuses, expected,
                "{{% for %}} and iter_values disagree about {value:?}: \
                 for-refuses={refuses}, iter_values-refuses={expected}",
            );
            if refuses {
                refusing += 1;
            }
        }
        // The canary: the sweep is not vacuous in either direction. Six of the
        // samples refuse and eleven do not, so a change that made EVERYTHING
        // refuse — or nothing — cannot pass by making the equality trivially
        // true.
        assert_eq!(refusing, 6, "expected exactly six refusing samples");
        assert_eq!(samples.len(), 19);
        // Non-vacuity for the extra term: the zero-`len`-and-not-iterable
        // sample must be one the two probes DISAGREE about, or the relation
        // above is indistinguishable from a plain equality.
        let split = samples
            .iter()
            .filter(|v| {
                matches!(v, Value::Encoded(e) if e.len == Some(0) && e.items.is_none() && !e.iterable)
            })
            .count();
        assert_eq!(
            split, 1,
            "the asymmetric sample is what makes this test able to fail"
        );
        // And non-vacuity for the ITEMS half (#2477/#2489): at least one
        // sample must carry items, or the for-arm's second clause is dead and
        // a mutation deleting it would go unnoticed.
        let carrying = samples
            .iter()
            .filter(|v| matches!(v, Value::Encoded(e) if e.items.as_ref().is_some_and(|i| !i.is_empty())))
            .count();
        assert_eq!(
            carrying, 2,
            "the item-carrying samples are what pin the for-arm"
        );
    }

    #[test]
    fn value_to_arg_string_encodes_structured_but_not_scalars() {
        // The single source of truth for arg encoding: list/object -> JSON,
        // scalars -> Display.
        let list = Value::List(vec![Value::Integer(1), Value::Integer(2)]);
        assert_eq!(value_to_arg_string(&list), "[1,2]");
        let mut map = IndexMap::new();
        map.insert("key".into(), Value::String("val".to_string()));
        assert_eq!(value_to_arg_string(&Value::Object(map)), r#"{"key":"val"}"#);
        assert_eq!(value_to_arg_string(&Value::Integer(42)), "42");
        // Scalars go through Display, so this follows it to `True` (#2203).
        assert_eq!(value_to_arg_string(&Value::Bool(true)), "True");
        assert_eq!(value_to_arg_string(&Value::String("hi".to_string())), "hi");
    }

    // ---- #2416: the CustomTag argument channel's (text, SafeData) pair -----

    #[test]
    fn tag_arg_marks_only_a_safe_string_value() {
        // The security boundary of #2416, spelled as a table. `SafeString` is a
        // `str` subclass in Django, so ONLY a string can be `SafeData` — and a
        // container's JSON encoding is structure the renderer synthesized
        // rather than bytes anyone vouched for.
        let s = Value::String("<b>".to_string());
        assert!(tag_arg(&s, true).safe, "a safe string must be marked");
        assert!(!tag_arg(&s, false).safe, "an unmarked string must not be");
        for value in [
            Value::Integer(5),
            Value::Float(1.5),
            Value::Bool(true),
            Value::None,
            Value::List(vec![Value::String("<b>".to_string())]),
        ] {
            assert!(
                !tag_arg(&value, true).safe,
                "a non-string was marked: {value:?}"
            );
        }
        // The text is `value_to_arg_string`'s, marked or not — this carries
        // only the safety BIT, never a different encoding.
        assert_eq!(tag_arg(&s, true).text, "<b>");
        assert_eq!(tag_arg(&s, false).text, "<b>");
    }

    #[test]
    fn resolve_custom_tag_arg_unquotes_and_marks_a_literal() {
        let ctx = obj_ctx();
        // Django's `Variable.__init__` strips the quotes AND `mark_safe`s.
        for token in ["\"<b>\"", "'<b>'"] {
            let arg = resolve_custom_tag_arg(token, &ctx);
            assert_eq!(arg.text, "<b>", "{token}");
            assert!(arg.safe, "{token}");
        }
        // A literal with no markup loses its quotes too — the half a
        // markup-bearing literal could not show.
        assert_eq!(resolve_custom_tag_arg("\"post\"", &ctx).text, "post");
        // The literal test runs BEFORE the `key=value` split, so a literal
        // carrying an `=` stays one argument.
        let eq = resolve_custom_tag_arg("\"a=b\"", &ctx);
        assert_eq!(eq.text, "a=b");
        assert!(eq.safe);
        // A NUMBER is not marked: Django `mark_safe`s only the quoted branch.
        let five = resolve_custom_tag_arg("5", &ctx);
        assert_eq!(five.text, "5");
        assert!(!five.safe);
    }

    #[test]
    fn resolve_custom_tag_arg_carries_the_contexts_grant() {
        let mut ctx = obj_ctx();
        ctx.set("marked".to_string(), Value::String("<b>".to_string()));
        ctx.mark_safe("marked".to_string());
        assert!(resolve_custom_tag_arg("marked", &ctx).safe);
        // …and only for the granted name.
        assert!(!resolve_custom_tag_arg("name", &ctx).safe);
        // An unresolved name keeps its raw token and no grant.
        let miss = resolve_custom_tag_arg("nope", &ctx);
        assert!(!miss.safe);
    }

    #[test]
    fn resolve_custom_tag_arg_never_marks_a_kwarg_composite() {
        // The transported text is `key=<value>`, not the value; marking it
        // would mark the `key=` bytes too. Left over-escaping, and unchanged
        // in every other respect.
        let mut ctx = obj_ctx();
        ctx.set("marked".to_string(), Value::String("<b>".to_string()));
        ctx.mark_safe("marked".to_string());
        let kw = resolve_custom_tag_arg("k=marked", &ctx);
        assert_eq!(kw.text, "k=<b>");
        assert!(!kw.safe);
        // A quoted kwarg value is still passed verbatim, quotes included.
        assert_eq!(resolve_custom_tag_arg("k=\"v\"", &ctx).text, "k=\"v\"");
        // And the structured encoding is untouched.
        assert_eq!(
            resolve_custom_tag_arg("rows=items", &ctx).text,
            "rows=[1,2,3]"
        );
    }

    #[test]
    fn every_handler_arg_construction_site_is_accounted_for() {
        // The caller SET, not a floor (#1125 / the v1.1.1-2 "grep for the
        // SINK" rule). Three dispatch arms build the `Vec<TagArg>` a registry
        // call receives, and exactly ONE of them carries the marker; a fourth
        // arm added later without a decision fails here rather than silently
        // shipping an unmarked channel.
        // Scanned over the PRODUCTION half only: this file's own test module
        // contains each of these strings as an assertion literal, and counting
        // those too makes every number 2 and the pin meaningless.
        let whole = include_str!("renderer.rs");
        let (src, tests) = whole
            .split_once("\n#[cfg(test)]\n")
            .expect("the test-module boundary moved; this pin scans the wrong half");
        assert!(
            tests.contains("every_handler_arg_construction_site_is_accounted_for"),
            "the split landed in the wrong place"
        );
        assert_eq!(
            src.matches("resolve_custom_tag_args(name, args, context)")
                .count(),
            1,
            "the CustomTag arm builds its args at exactly one site"
        );
        assert_eq!(
            src.matches("resolve_custom_tag_arg(arg, context)").count(),
            1,
            "`resolve_custom_tag_args` is the ONE caller of the marker-carrying \
             per-operand resolver"
        );
        // The #2423 policy short-circuit is INSIDE that site, and it hands back
        // a `plain` arg. Pinned mechanically because it is the security
        // decision of the #2416/#2423 merge: a passthrough token is a NAME the
        // handler will resolve, not bytes bound for the page, so it must never
        // carry a grant. An edit that switched it to `marked` — or dropped the
        // branch entirely, taking the policy with it — passes every behavioural
        // test `render_slot` has, because `render_slot` does not read its
        // argument's marker.
        // Two since #2547: the inline resolver and its block twin
        // (`resolve_block_tag_args`) — and NEITHER may mint a grant.
        assert_eq!(
            src.matches("return TagArg::plain(arg.clone());").count(),
            2,
            "the literal-passthrough positions (inline + block) must hand back an UNMARKED arg"
        );
        assert_eq!(
            src.matches("TagArg::marked(arg.clone())").count(),
            0,
            "a passthrough token must never carry a grant"
        );
        assert!(
            src.contains("tag_handler_resolve_positions(name)"),
            "the CustomTag arm stopped consulting the RESOLVE_ARG_POSITIONS policy"
        );
        assert_eq!(
            src.matches("TagArg::plain(resolve_tag_arg(arg, context))")
                .count(),
            1,
            "the BlockCustomTag arm"
        );
        assert_eq!(
            src.matches("plain_args(resolve_assign_tag_args(name, args, context))")
                .count(),
            2,
            "the two AssignTag arms — the sibling-aware loop and the standalone render"
        );
        // And no arm may go back to building a bare `Vec<String>` for a
        // registry call: the type is what makes the decision explicit.
        assert!(
            !src.contains("let resolved_args: Vec<String>"),
            "a dispatch arm is building untyped args again"
        );
    }

    #[test]
    fn resolve_tag_arg_json_encodes_list_and_object() {
        let ctx = obj_ctx();
        // Bare list / object names JSON-encode (not "[List]" / "[Object]").
        assert_eq!(resolve_tag_arg("items", &ctx), "[1,2,3]");
        assert_eq!(resolve_tag_arg("obj", &ctx), r#"{"key":"val"}"#);
    }

    #[test]
    fn resolve_tag_arg_scalars_unchanged() {
        let ctx = obj_ctx();
        assert_eq!(resolve_tag_arg("count", &ctx), "42");
        assert_eq!(resolve_tag_arg("name", &ctx), "hello");
    }

    #[test]
    fn resolve_tag_arg_kwarg_value_json_encoded() {
        let ctx = obj_ctx();
        assert_eq!(resolve_tag_arg("rows=items", &ctx), "rows=[1,2,3]");
        // Scalar key=value stays Display-formatted.
        assert_eq!(resolve_tag_arg("n=count", &ctx), "n=42");
    }

    #[test]
    fn value_channel_json_encodes_a_string_and_a_bool() {
        // A `String` is JSON-encoded so the handler can tell it from the raw
        // token an unresolved operand leaves behind (#2385).
        assert_eq!(
            value_channel_arg_string(&Value::String("ab".to_string())),
            r#""ab""#
        );
        // Embedded quotes/backslashes survive as JSON escapes, so the decode
        // gives back the original text.
        assert_eq!(
            value_channel_arg_string(&Value::String("a\"b\\c".to_string())),
            r#""a\"b\\c""#
        );
        // A `Decimal` also serializes as a JSON *string*, and must NOT take
        // that arm — a `Decimal` is not iterable in Python.
        assert_eq!(
            value_channel_arg_string(&Value::Decimal("1.5".to_string())),
            "1.5"
        );
        assert_eq!(
            value_channel_arg_string(&Value::BigInt("123456789012345678901".to_string())),
            "123456789012345678901"
        );
        // A `Bool` IS re-encoded, as of #2463: Python's `True` is not valid
        // JSON, so the handler's `json.loads` raised on it and it took the
        // "unresolved bare name" branch — answering NO GROUPS where Django
        // raises `TypeError: 'bool' object is not iterable`.
        assert_eq!(value_channel_arg_string(&Value::Bool(true)), "true");
        assert_eq!(value_channel_arg_string(&Value::Bool(false)), "false");
        assert_ne!(
            value_channel_arg_string(&Value::Bool(true)),
            value_to_arg_string(&Value::Bool(true))
        );
        // Everything else is byte-identical to the historical encoding.
        // `None` is here deliberately: its `Display` spelling is not JSON
        // either, but the mis-decode is HARMLESS — the handler's fallback
        // answers `None`, which is exactly Django's `if obj_list is None`
        // arm. Left alone rather than ridden along (#1079).
        for v in [
            Value::Integer(42),
            Value::None,
            Value::Float(1.5),
            Value::List(vec![Value::Integer(1)]),
        ] {
            assert_eq!(value_channel_arg_string(&v), value_to_arg_string(&v));
        }
    }

    #[test]
    fn resolve_tag_value_arg_distinguishes_a_string_from_a_raw_token() {
        let ctx = obj_ctx();
        // `name` holds "hello": quoted, so the handler reads a string.
        assert_eq!(resolve_tag_value_arg("name", &ctx), r#""hello""#);
        // An unresolved bare name keeps this channel's raw-token contract —
        // the two were indistinguishable before #2385.
        assert_eq!(resolve_tag_value_arg("nope", &ctx), "nope");
        // Structured values are unchanged (they were already JSON).
        assert_eq!(resolve_tag_value_arg("items", &ctx), "[1,2,3]");
        assert_eq!(resolve_tag_value_arg("count", &ctx), "42");
        // BOTH quote spellings of a literal normalize to the same JSON, which
        // is what Django's `Variable` does with them.
        assert_eq!(resolve_tag_value_arg("\"abc\"", &ctx), r#""abc""#);
        assert_eq!(resolve_tag_value_arg("'abc'", &ctx), r#""abc""#);
    }

    #[test]
    fn strip_quotes_needs_a_matching_pair() {
        assert_eq!(strip_quotes("\"ab\""), Some("ab"));
        assert_eq!(strip_quotes("'ab'"), Some("ab"));
        assert_eq!(strip_quotes("\"\""), Some(""));
        assert_eq!(strip_quotes("ab"), None);
        assert_eq!(strip_quotes("\"ab"), None);
        assert_eq!(strip_quotes("ab\""), None);
        // Not a pair: a lone quote must not be read as an empty literal.
        assert_eq!(strip_quotes("\""), None);
        assert_eq!(strip_quotes("'"), None);
        // Mismatched styles are not a pair either.
        assert_eq!(strip_quotes("\"ab'"), None);
    }

    #[test]
    fn resolve_tag_arg_unknown_name_and_quoted_literal_kept() {
        let ctx = obj_ctx();
        // Keyword operand not in context (regroup `by` / `as`) stays literal.
        assert_eq!(resolve_tag_arg("by", &ctx), "by");
        // Quoted string literal is passed through unchanged.
        assert_eq!(resolve_tag_arg("'grouper'", &ctx), "'grouper'");
        assert_eq!(resolve_tag_arg("\"grouper\"", &ctx), "\"grouper\"");
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
        // The literal denotes Python's None singleton, so it resolves to
        // `Value::None` — NOT `Missing`, which means an ABSENT variable
        // (#2203). Both still satisfy `is None`; see `values_identity`.
        let val = get_value("None", &context).unwrap();
        assert!(
            matches!(val, Value::None),
            "None literal should be Value::None"
        );
        let val = get_value("none", &context).unwrap();
        assert!(
            matches!(val, Value::None),
            "none literal should be Value::None"
        );
    }

    #[test]
    fn test_get_value_context_shadows_literal() {
        // A context variable named "True" should take precedence over the literal
        let mut context = Context::new();
        context.set("True".to_string(), Value::String("not a bool".to_string()));
        let val = get_value("True", &context).unwrap();
        assert_eq!(val.to_string(), "not a bool");
    }

    // ----- #1483: `is` / `is not` identity operators -----

    fn render_if(template: &str, vars: Vec<(&str, Value)>) -> String {
        let tokens = tokenize(template).unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        for (k, v) in vars {
            context.set(k.to_string(), v);
        }
        render_nodes(&nodes, &context).unwrap()
    }

    #[test]
    fn test_values_identity() {
        // Null/Null -> true (Python `None is None`)
        assert!(values_identity(&Value::Missing, &Value::Missing));
        // Bool/Bool -> matches value (Python `True is True`, `False is False`)
        assert!(values_identity(&Value::Bool(true), &Value::Bool(true)));
        assert!(values_identity(&Value::Bool(false), &Value::Bool(false)));
        assert!(!values_identity(&Value::Bool(true), &Value::Bool(false)));
        // Mismatched singletons -> false
        assert!(!values_identity(&Value::Missing, &Value::Bool(false)));
        assert!(!values_identity(&Value::Bool(true), &Value::Missing));
        // Non-singletons -> always false (CPython interning is not contractual)
        assert!(!values_identity(&Value::Integer(5), &Value::Integer(5)));
        assert!(!values_identity(&Value::Float(1.0), &Value::Float(1.0)));
        assert!(!values_identity(
            &Value::String("a".to_string()),
            &Value::String("a".to_string())
        ));
    }

    #[test]
    fn test_if_is_none_true() {
        let result = render_if(
            "{% if val is None %}empty{% else %}filled{% endif %}",
            vec![("val", Value::Missing)],
        );
        assert_eq!(result, "empty");
    }

    #[test]
    fn test_if_is_none_false() {
        // 0 is not None — identity, not truthiness
        let result = render_if(
            "{% if val is None %}empty{% else %}filled{% endif %}",
            vec![("val", Value::Integer(0))],
        );
        assert_eq!(result, "filled");
    }

    #[test]
    fn test_if_is_not_none_true() {
        let result = render_if(
            "{% if some_float is not None %}set{% else %}unset{% endif %}",
            vec![("some_float", Value::Float(12.3))],
        );
        assert_eq!(result, "set");
    }

    #[test]
    fn test_if_is_not_none_false() {
        let result = render_if(
            "{% if val is not None %}set{% else %}unset{% endif %}",
            vec![("val", Value::Missing)],
        );
        assert_eq!(result, "unset");
    }

    #[test]
    fn test_if_is_true_singleton() {
        let result = render_if(
            "{% if flag is True %}yes{% else %}no{% endif %}",
            vec![("flag", Value::Bool(true))],
        );
        assert_eq!(result, "yes");
    }

    #[test]
    fn test_if_is_false_singleton() {
        let result = render_if(
            "{% if flag is False %}off{% else %}on{% endif %}",
            vec![("flag", Value::Bool(false))],
        );
        assert_eq!(result, "off");
    }

    #[test]
    fn test_if_is_not_true() {
        let result = render_if(
            "{% if flag is not True %}not-true{% else %}true{% endif %}",
            vec![("flag", Value::Bool(false))],
        );
        assert_eq!(result, "not-true");
    }

    #[test]
    fn test_if_is_non_singleton_not_identical() {
        // Python identity semantics: `5 is 5` does NOT contractually hold.
        let result = render_if(
            "{% if a is b %}same{% else %}diff{% endif %}",
            vec![("a", Value::Integer(5)), ("b", Value::Integer(5))],
        );
        assert_eq!(result, "diff");
    }

    #[test]
    fn test_if_is_combined_with_and() {
        // `is` / `is not` compose with the lower-precedence `and`.
        let result = render_if(
            "{% if a is not None and b is None %}match{% else %}nomatch{% endif %}",
            vec![("a", Value::Integer(7)), ("b", Value::Missing)],
        );
        assert_eq!(result, "match");
    }

    #[test]
    fn test_if_is_not_checked_before_is() {
        // Substring-ordering invariant: `x is not None` must be parsed as
        // `x  (is not)  None`, NOT `x  (is)  (not None)`. With val set to a
        // non-None value, `is not None` -> true. If " is " matched first,
        // the right operand would be "not None" and resolve incorrectly.
        let result = render_if(
            "{% if val is not None %}set{% else %}unset{% endif %}",
            vec![("val", Value::Integer(1))],
        );
        assert_eq!(result, "set");
    }

    #[test]
    fn test_if_variable_named_with_is_substring_no_false_match() {
        // A variable named "analysis" contains "is" but must not false-match
        // the operator branch — space-padding guards against this.
        let result = render_if(
            "{% if analysis %}has-analysis{% else %}none{% endif %}",
            vec![("analysis", Value::Bool(true))],
        );
        assert_eq!(result, "has-analysis");
    }

    // ---- #1722: context-processor vars must propagate into {% include %} ----
    //
    // Reporter symptom: a SafeString context-processor var ({{ theme_panel }})
    // renders correctly at the TOP LEVEL of a template but renders EMPTY inside
    // a nested {% include %} partial. A plain (non-safe) view-attr var
    // ({{ nav_items }}) DOES reach the same include — so the divergence is
    // specific to SafeString-marked values, not to includes in general.

    /// A tiny in-memory template loader for include tests.
    struct MapLoader {
        templates: std::collections::HashMap<String, Vec<Node>>,
    }

    impl MapLoader {
        fn new(entries: &[(&str, &str)]) -> Self {
            let mut templates = std::collections::HashMap::new();
            for (name, source) in entries {
                let tokens = tokenize(source).unwrap();
                let nodes = parse(&tokens).unwrap();
                templates.insert((*name).to_string(), nodes);
            }
            Self { templates }
        }
    }

    impl crate::inheritance::TemplateLoader for MapLoader {
        fn load_template(&self, name: &str) -> Result<Vec<Node>> {
            self.templates.get(name).cloned().ok_or_else(|| {
                DjangoRustError::TemplateError(format!("template not found: {name}"))
            })
        }
    }

    #[test]
    fn test_1722_safe_var_renders_at_top_level() {
        // Baseline: a SafeString-marked HTML var renders unescaped at top level.
        let tokens = tokenize("{{ theme_panel }}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set(
            "theme_panel".to_string(),
            Value::String("<div class=\"panel\">x</div>".to_string()),
        );
        context.mark_safe("theme_panel".to_string());
        let loader = MapLoader::new(&[]);
        let result = render_nodes_with_loader(&nodes, &context, Some(&loader)).unwrap();
        assert_eq!(result, "<div class=\"panel\">x</div>");
    }

    #[test]
    fn test_1722_safe_var_propagates_into_include() {
        // THE BUG: the same SafeString var, used inside an {% include %}, must
        // render non-empty (and unescaped) just like at top level.
        let loader = MapLoader::new(&[("_partial.html", "[{{ theme_panel }}]")]);
        let tokens = tokenize("{% include \"_partial.html\" %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set(
            "theme_panel".to_string(),
            Value::String("<div class=\"panel\">x</div>".to_string()),
        );
        context.mark_safe("theme_panel".to_string());
        let result = render_nodes_with_loader(&nodes, &context, Some(&loader)).unwrap();
        assert_eq!(
            result, "[<div class=\"panel\">x</div>]",
            "SafeString context-processor var must propagate into {{% include %}} unescaped (#1722)"
        );
    }

    #[test]
    fn test_1722_discriminator_plain_var_reaches_include() {
        // Discriminator from the report: a plain (non-safe) var DOES reach the
        // include. This guards against a regression that would "fix" #1722 by
        // breaking the working plain-var path.
        let loader = MapLoader::new(&[("_partial.html", "[{{ nav_items }}]")]);
        let tokens = tokenize("{% include \"_partial.html\" %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set(
            "nav_items".to_string(),
            Value::String("home,about".to_string()),
        );
        let result = render_nodes_with_loader(&nodes, &context, Some(&loader)).unwrap();
        assert_eq!(result, "[home,about]");
    }

    #[test]
    fn test_1722_safe_var_propagates_into_nested_include() {
        // Reporter's real shape: base -> include _start -> include _sidebar,
        // with the SafeString var used in the deepest partial.
        let loader = MapLoader::new(&[
            ("_start.html", "{% include \"_sidebar.html\" %}"),
            ("_sidebar.html", "<aside>{{ theme_panel }}</aside>"),
        ]);
        let tokens = tokenize("{% include \"_start.html\" %}").unwrap();
        let nodes = parse(&tokens).unwrap();
        let mut context = Context::new();
        context.set(
            "theme_panel".to_string(),
            Value::String("<b>P</b>".to_string()),
        );
        context.mark_safe("theme_panel".to_string());
        let result = render_nodes_with_loader(&nodes, &context, Some(&loader)).unwrap();
        assert_eq!(result, "<aside><b>P</b></aside>");
    }

    // -- #2243: mixed int/float equality -----------------------------------
    //
    // The Django differential lives in
    // `python/tests/test_float_int_equality_2243.py`; these pin the boundary
    // cases at the function, where the values are exact rather than routed
    // through a template literal.

    #[test]
    fn test_2243_int_eq_float_is_exact_not_epsilon() {
        assert!(int_eq_float(0, 0.0));
        assert!(int_eq_float(0, -0.0));
        assert!(int_eq_float(19, 19.0));
        assert!(int_eq_float(-19, -19.0));

        // Residues float arithmetic actually produces. An epsilon tolerance
        // calls the first two zero; Python does not.
        assert!(!int_eq_float(0, 0.1 + 0.2 - 0.3));
        assert!(!int_eq_float(0, 1.0 - 0.9 - 0.1));
        assert!(!int_eq_float(0, 1e-17));
        assert!(!int_eq_float(0, f64::MIN_POSITIVE));
        assert!(!int_eq_float(0, 5e-324));
    }

    #[test]
    fn test_2243_int_eq_float_beyond_f64_precision() {
        // `9007199254740993 as f64` IS `9007199254740992.0`, so the obvious
        // `a as f64 == b` spelling answers true here. Python answers false.
        assert!(!int_eq_float(9007199254740993, 9007199254740992.0));
        assert!(int_eq_float(9007199254740992, 9007199254740992.0));
        assert!(!int_eq_float((1i64 << 62) + 1, (1u64 << 62) as f64));
        assert!(int_eq_float(1i64 << 62, (1u64 << 62) as f64));
    }

    #[test]
    fn test_2243_int_eq_float_rejects_out_of_range_and_non_finite() {
        // `b as i64` saturates, so without the range guard these compare equal.
        assert!(!int_eq_float(i64::MAX, 1e300));
        assert!(!int_eq_float(i64::MIN, -1e300));
        assert!(!int_eq_float(i64::MAX, 9_223_372_036_854_775_808.0)); // 2^63
        assert!(int_eq_float(i64::MIN, -9_223_372_036_854_775_808.0)); // -2^63

        assert!(!int_eq_float(0, f64::NAN));
        assert!(!int_eq_float(0, f64::INFINITY));
        assert!(!int_eq_float(0, f64::NEG_INFINITY));
        assert!(!int_eq_float(0, 0.5));
        assert!(!int_eq_float(19, 19.5));
    }

    #[test]
    fn test_2243_values_equal_wires_both_operand_orders() {
        // Half a two-sided guard pinned is half a guard (#1859).
        assert!(values_equal(&Value::Float(0.0), &Value::Integer(0)));
        assert!(values_equal(&Value::Integer(0), &Value::Float(0.0)));
        assert!(!values_equal(&Value::Float(0.5), &Value::Integer(0)));
        assert!(!values_equal(&Value::Integer(0), &Value::Float(0.5)));

        // The arms this did not touch.
        assert!(values_equal(&Value::Integer(1), &Value::Integer(1)));
        assert!(values_equal(&Value::Float(1e-17), &Value::Float(0.0))); // epsilon, unchanged
        assert!(!values_equal(
            &Value::String("5".to_string()),
            &Value::Integer(5)
        ));
        assert!(!values_equal(&Value::None, &Value::Integer(0)));
        // Was pinned as-is here, NOT as correct, and #2244 corrected it: Django
        // says `True == 1` is true, since `bool` subclasses `int`.
        assert!(values_equal(&Value::Bool(true), &Value::Integer(1)));
    }

    // -----------------------------------------------------------------------
    // #2244 — a bool IS an integer to Python, in both comparison functions.
    // -----------------------------------------------------------------------

    #[test]
    fn test_2244_bool_as_int_substitutes_only_bools() {
        // `Value` has no `PartialEq`, so match the variant rather than compare.
        assert!(matches!(
            bool_as_int(&Value::Bool(true)),
            Some(Value::Integer(1))
        ));
        assert!(matches!(
            bool_as_int(&Value::Bool(false)),
            Some(Value::Integer(0))
        ));
        // Everything else is left alone — the substitution must not widen to
        // strings or None, which Django compares as themselves (#1079).
        assert!(bool_as_int(&Value::Integer(1)).is_none());
        assert!(bool_as_int(&Value::Float(1.0)).is_none());
        assert!(bool_as_int(&Value::String("1".to_string())).is_none());
        assert!(bool_as_int(&Value::None).is_none());
        assert!(bool_as_int(&Value::Missing).is_none());
    }

    #[test]
    fn test_2244_values_equal_compares_a_bool_as_its_integer() {
        // Both operand orders — half a two-sided guard pinned is half a guard
        // (#1859).
        let t = Value::Bool(true);
        let f = Value::Bool(false);
        assert!(values_equal(&t, &Value::Integer(1)));
        assert!(values_equal(&Value::Integer(1), &t));
        assert!(values_equal(&f, &Value::Integer(0)));
        assert!(values_equal(&Value::Integer(0), &f));
        assert!(!values_equal(&t, &Value::Integer(0)));
        assert!(!values_equal(&f, &Value::Integer(1)));
        assert!(!values_equal(&t, &Value::Integer(2)));

        // Floats reach the exact `int_eq_float` arms, not an epsilon.
        assert!(values_equal(&t, &Value::Float(1.0)));
        assert!(values_equal(&Value::Float(0.0), &f));
        assert!(values_equal(&Value::Float(-0.0), &f));
        assert!(!values_equal(&t, &Value::Float(1.5)));
        assert!(!values_equal(&f, &Value::Float(f64::NAN)));

        // NOT widened: Django says `"1" == True` and `None == False` are both
        // false, and both were false before this.
        assert!(!values_equal(&t, &Value::String("1".to_string())));
        assert!(!values_equal(&Value::String("True".to_string()), &t));
        assert!(!values_equal(&f, &Value::None));
        assert!(!values_equal(&Value::Missing, &f));
    }

    #[test]
    fn test_2244_values_equal_bool_vs_bool_is_untouched() {
        // The arm the substitution deliberately skips, so it stays live.
        assert!(values_equal(&Value::Bool(true), &Value::Bool(true)));
        assert!(values_equal(&Value::Bool(false), &Value::Bool(false)));
        assert!(!values_equal(&Value::Bool(true), &Value::Bool(false)));
        assert!(!values_equal(&Value::Bool(false), &Value::Bool(true)));
    }

    #[test]
    fn test_2244_try_compare_orders_a_bool_as_its_integer() {
        assert_eq!(try_compare(&Value::Bool(true), &Value::Integer(0)), Some(1));
        assert_eq!(
            try_compare(&Value::Integer(0), &Value::Bool(true)),
            Some(-1)
        );
        assert_eq!(
            try_compare(&Value::Bool(false), &Value::Integer(1)),
            Some(-1)
        );
        assert_eq!(
            try_compare(&Value::Integer(1), &Value::Bool(false)),
            Some(1)
        );
        assert_eq!(try_compare(&Value::Bool(true), &Value::Integer(1)), Some(0));
        assert_eq!(
            try_compare(&Value::Bool(false), &Value::Integer(0)),
            Some(0)
        );

        assert_eq!(try_compare(&Value::Bool(true), &Value::Float(0.5)), Some(1));
        assert_eq!(
            try_compare(&Value::Float(0.5), &Value::Bool(true)),
            Some(-1)
        );
        assert_eq!(
            try_compare(&Value::Bool(false), &Value::Float(0.0)),
            Some(0)
        );

        // No bool-vs-bool arm existed here, so this pair was 0 — "equal" — and
        // `{% if a > b %}` was false for `True > False`. Python orders them.
        assert_eq!(
            try_compare(&Value::Bool(true), &Value::Bool(false)),
            Some(1)
        );
        assert_eq!(
            try_compare(&Value::Bool(false), &Value::Bool(true)),
            Some(-1)
        );
        assert_eq!(try_compare(&Value::Bool(true), &Value::Bool(true)), Some(0));

        // Incomparable — and `None` rather than the `Some(0)` this used to
        // return (#2338). Python raises for `True > "1"`, so all four operators
        // are false; `Some(0)` made `>=` and `<=` answer True.
        assert_eq!(
            try_compare(&Value::Bool(true), &Value::String("1".to_string())),
            None
        );
        assert_eq!(try_compare(&Value::Bool(true), &Value::None), None);
    }

    #[test]
    fn test_2244_values_identity_does_not_widen() {
        // The asymmetry to preserve: `True is 1` is FALSE in Python, so `is`
        // must NOT get the substitution `==` and `<`/`>` just got.
        assert!(!values_identity(&Value::Bool(true), &Value::Integer(1)));
        assert!(!values_identity(&Value::Integer(1), &Value::Bool(true)));
        assert!(!values_identity(&Value::Bool(false), &Value::Integer(0)));
        assert!(!values_identity(&Value::Integer(0), &Value::Bool(false)));
        assert!(!values_identity(&Value::Bool(true), &Value::Float(1.0)));
        assert!(!values_identity(&Value::Bool(false), &Value::Float(0.0)));
        // `is` between two bools is unchanged — they are singletons.
        assert!(values_identity(&Value::Bool(true), &Value::Bool(true)));
        assert!(!values_identity(&Value::Bool(true), &Value::Bool(false)));
    }

    // -- #2335: sequence comparison ------------------------------------

    fn l(items: Vec<Value>) -> Value {
        Value::List(items)
    }

    fn t(items: Vec<Value>) -> Value {
        Value::Tuple(items)
    }

    #[test]
    fn test_2335_two_sequences_of_the_same_kind_compare_structurally() {
        assert!(values_equal(&l(vec![]), &l(vec![])));
        assert!(values_equal(
            &l(vec![Value::Integer(1), Value::Integer(2)]),
            &l(vec![Value::Integer(1), Value::Integer(2)])
        ));
        assert!(!values_equal(
            &l(vec![Value::Integer(1)]),
            &l(vec![Value::Integer(1), Value::Integer(1)])
        ));
        assert!(values_equal(
            &t(vec![Value::Integer(1)]),
            &t(vec![Value::Integer(1)])
        ));
        // Nested.
        assert!(values_equal(
            &l(vec![Value::Integer(1), l(vec![Value::Integer(2)])]),
            &l(vec![Value::Integer(1), l(vec![Value::Integer(2)])])
        ));
    }

    #[test]
    fn test_2335_a_list_is_never_equal_to_a_tuple() {
        // Python: `[1] == (1,)` is False. A "both are sequences" arm would be
        // wrong here, in the direction a curated table is least likely to probe.
        assert!(!values_equal(
            &l(vec![Value::Integer(1)]),
            &t(vec![Value::Integer(1)])
        ));
        assert!(!values_equal(
            &t(vec![Value::Integer(1)]),
            &l(vec![Value::Integer(1)])
        ));
        assert_eq!(
            try_compare(&l(vec![Value::Integer(1)]), &t(vec![Value::Integer(2)])),
            None,
            "cross-kind ordering is a TypeError in Python; None is what makes all four operators false"
        );
    }

    #[test]
    fn test_2335_elements_widen_through_the_scalar_arms() {
        // Recursion, not element-wise `==`: the #2243 / #2244 widening arms
        // are REACHED rather than re-implemented.
        assert!(values_equal(
            &l(vec![Value::Integer(1)]),
            &l(vec![Value::Float(1.0)])
        ));
        assert!(values_equal(
            &l(vec![Value::Bool(true)]),
            &l(vec![Value::Integer(1)])
        ));
        assert!(values_equal(
            &l(vec![Value::None]),
            &l(vec![Value::Missing])
        ));
        assert!(!values_equal(
            &l(vec![Value::Integer(1)]),
            &l(vec![Value::Float(1.5)])
        ));
    }

    #[test]
    fn test_2335_dicts_compare_by_pairs_ignoring_order() {
        let mut a = indexmap::IndexMap::new();
        a.insert("a".into(), Value::Integer(1));
        a.insert("b".into(), Value::Integer(2));
        let mut b = indexmap::IndexMap::new();
        b.insert("b".into(), Value::Integer(2));
        b.insert("a".into(), Value::Integer(1));
        assert!(values_equal(&Value::Object(a.clone()), &Value::Object(b)));

        let mut c = indexmap::IndexMap::new();
        c.insert("a".into(), Value::Integer(1));
        assert!(!values_equal(&Value::Object(a), &Value::Object(c)));
    }

    #[test]
    fn test_2335_ordering_is_lexicographic() {
        assert_eq!(
            try_compare(&l(vec![Value::Integer(1)]), &l(vec![Value::Integer(2)])),
            Some(-1)
        );
        assert_eq!(
            try_compare(&l(vec![Value::Integer(2)]), &l(vec![Value::Integer(1)])),
            Some(1)
        );
        assert_eq!(
            try_compare(
                &l(vec![Value::Integer(1), Value::Integer(2)]),
                &l(vec![Value::Integer(1)])
            ),
            Some(1),
            "a prefix is smaller"
        );
        assert_eq!(
            try_compare(&l(vec![Value::Integer(1)]), &l(vec![Value::Integer(1)])),
            Some(0)
        );
    }

    #[test]
    fn test_2335_only_an_equal_pair_continues_the_walk() {
        // Python compares with `==` FIRST, so equal-but-unorderable elements
        // do not stop the walk...
        let d = Value::Object(indexmap::IndexMap::new());
        assert_eq!(
            try_compare(
                &l(vec![d.clone(), Value::Integer(1)]),
                &l(vec![d.clone(), Value::Integer(2)])
            ),
            Some(-1)
        );
        // ...and an UNEQUAL pair DECIDES, even when it cannot be ordered.
        // Continuing past it — which is what asking for an ordering first and
        // treating its 0 as a tie does — falls through to the length tie-break
        // and answers "greater" (3 elements beats 1), which Django does not.
        // The randomised differential caught exactly this on the first draft.
        //
        // `None`, not `Some(0)` (#2338): an incomparable element makes the
        // WHOLE comparison incomparable, so `>=` cannot read it back as a tie
        // and answer True. That is the length tie-break bug again, one operator
        // over — and it is what a plain `i32` return could not express.
        assert_eq!(
            try_compare(
                &l(vec![l(vec![]), Value::String("a".into()), d]),
                &l(vec![Value::Integer(1)])
            ),
            None
        );
    }

    #[test]
    fn test_2338_an_incomparable_pair_is_none_not_a_tie() {
        // The contract the `Option` exists for: a pair Python refuses to order
        // is `None`, so `>`, `<`, `>=` and `<=` are ALL false — which is what
        // Django answers, because Python raises `TypeError` and `{% if %}`
        // catches it. Returning 0 made `>=` and `<=` read it as "equal".
        assert_eq!(
            try_compare(&Value::String("a".into()), &Value::Integer(1)),
            None
        );
        // `None`/`Missing` too, and they are the pair most likely to be
        // mistaken for a legitimate tie: `values_equal` DOES call them equal
        // (Django resolves an absent variable to `None`, so `==` is True), but
        // Python's `None < None` still raises, so ORDERING them is `None`.
        // The two functions disagree here on purpose.
        assert_eq!(try_compare(&Value::Missing, &Value::Missing), None);
        assert_eq!(try_compare(&Value::None, &Value::None), None);
        assert_eq!(try_compare(&Value::None, &Value::Integer(1)), None);
        assert!(values_equal(&Value::Missing, &Value::Missing));
        // A dict against anything, including itself.
        let d = Value::Object(indexmap::IndexMap::new());
        assert_eq!(try_compare(&d, &d), None);
        // And the orderable pairs still answer, so `None` is not swallowing
        // everything.
        assert_eq!(
            try_compare(&Value::Integer(1), &Value::Integer(2)),
            Some(-1)
        );
        assert_eq!(
            try_compare(&Value::String("b".into()), &Value::String("a".into())),
            Some(1)
        );
    }
}
