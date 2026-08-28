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
        || crate::filter_registry::is_custom_filter_safe(filter_name)
        // Django's case 1 (#2274): `is_safe=True` AND the input was `SafeData`.
        // BOTH terms are load-bearing — dropping `input_was_safe` would mark
        // `{{ hostile|lower }}` safe and is a direct XSS.
        || (input_was_safe && IS_SAFE_FILTERS.contains(&filter_name))
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
                // Resolve variable references in args, mirroring only the
                // JSON *encoding* of `Node::CustomTag` (structured
                // list/object values survive as JSON instead of collapsing
                // to "[List]"). NB: the *resolution mechanism* is not
                // identical — `CustomTag` uses `get_value` (filter-aware,
                // e.g. `x|upper`), whereas `resolve_tag_arg` uses plain
                // `context.get` (no filter support), consistent with
                // regroup's documented "no filter expressions" limitation.
                // Keyword/name operands the handler declares literal
                // (RESOLVE_ARG_POSITIONS) are passed raw (#2041).
                let resolved_args = resolve_assign_tag_args(name, args, active_ctx);
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
        Value::List(_) | Value::Tuple(_) | Value::Object(_) => {
            serde_json::to_string(v).unwrap_or_else(|_| v.to_string())
        }
        _ => v.to_string(),
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
            Ok(value) => Some(value_to_arg_string(&value)),
        };
    }
    context.get(expr).map(value_to_arg_string)
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
/// Localize a bare number for output, leaving every other value untouched.
///
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
        Value::Integer(_) | Value::Float(_) | Value::Decimal(_) | Value::BigInt(_) => {
            djust_core::locale::localize_number(&value.to_string())
        }
        _ => value.to_string(),
    }
}

fn resolve_assign_tag_args(name: &str, args: &[String], context: &Context) -> Vec<String> {
    let resolve_positions = crate::registry::assign_handler_resolve_positions(name);
    args.iter()
        .enumerate()
        .map(|(i, arg)| match &resolve_positions {
            // Handler opted into literal operands and this position is NOT
            // one it wants resolved: pass the raw token (Django parity —
            // no context shadowing possible).
            Some(positions) if !positions.contains(&i) => arg.clone(),
            // Declared-to-resolve position, or no policy (resolve all).
            _ => resolve_tag_arg(arg, context),
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

        let frag = match node {
            Node::AssignTag { name, args } => {
                // Resolve args (JSON-aware) honoring RESOLVE_ARG_POSITIONS,
                // as in render_nodes_with_loader (#2041).
                let resolved_args = resolve_assign_tag_args(name, args, active_ctx);
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
                    // Resolve args (JSON-aware) honoring RESOLVE_ARG_POSITIONS,
                    // as in render_nodes_with_loader (#2041).
                    let resolved_args = resolve_assign_tag_args(name, args, active_ctx);
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
            // falls back to `getattr` on any Py<PyAny> sidecar attached
            // to the context (e.g. Django model instances). The `?`
            // propagates exceptions raised inside an auto-called method
            // (ADR-024 Django parity); lookup misses stay `Ok(None)`.
            let mut value = context.resolve(var_name)?.unwrap_or(Value::Missing);

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
            let mut runtime_safe = context.is_safe(var_name);
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
            // 3. A filter that produces already-escaped/safe output is in the chain
            //    (built-in safe_output_filters list OR a custom filter
            //    registered with ``is_safe=True`` per #1121).
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

            let mut value = get_value(expr, context)?;

            // See the Variable arm: track the LAST filter's runtime safeness so
            // a custom filter that ``mark_safe()``s at runtime bypasses escaping
            // (#1660); a later plain filter re-taints. Seeded with the context's
            // own safety so the chain carries Django's input term (#2274).
            let mut runtime_safe = context.is_safe(expr);
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
            let iterable_value = get_value(iterable, context)?;

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
            let (iterable_value, normalised) = match iterable_value {
                Value::String(s) => (
                    Value::List(s.chars().map(|c| Value::String(c.to_string())).collect()),
                    true,
                ),
                Value::Object(map) => (
                    Value::List(map.keys().map(|k| Value::String(k.clone())).collect()),
                    true,
                ),
                other => (other, false),
            };

            match iterable_value {
                Value::List(items) | Value::Tuple(items) => {
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

                        // Handle tuple unpacking: {% for a, b in items %}
                        if var_names.len() == 1 {
                            // Single variable: {% for item in items %}
                            ctx.set(var_names[0].clone(), item);
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
                            // Expect item to be a list/tuple
                            match &item {
                                Value::List(tuple_items) | Value::Tuple(tuple_items) => {
                                    // Unpack tuple items into separate variables
                                    for (i, var_name) in var_names.iter().enumerate() {
                                        if i < tuple_items.len() {
                                            ctx.set(var_name.clone(), tuple_items[i].clone());
                                        } else {
                                            // If tuple has fewer items than var names, set to Null
                                            ctx.set(var_name.clone(), Value::Missing);
                                        }
                                    }
                                }
                                _ => {
                                    // If item is not a list, set all vars to Null except first
                                    ctx.set(var_names[0].clone(), item.clone());
                                    for var_name in &var_names[1..] {
                                        ctx.set(var_name.clone(), Value::Missing);
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
                    // Only use with_vars, not parent context
                    Context::new()
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
                for (key, value_expr) in with_vars {
                    let value = get_value(value_expr, context)?;
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
            // The runtime-safe flag is discarded here for the same reason as
            // in the `{% for %}` arm: `{% with q=p|safe %}{{ q }}{% endwith %}`
            // keeps over-escaping rather than gaining a new way to emit live
            // markup.
            for (var_name, expression) in assignments {
                let value = get_value(expression, context)?;
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
            // Uses get_value_safe for dotted path support (e.g., user.name)
            // AND to thread the runtime-safe flag (#1672, parallel-path per
            // CLAUDE.md #1646): a custom filter that `mark_safe()`s at runtime
            // (e.g. `{% firstof a|md %}`) must NOT be re-escaped, matching the
            // Variable/InlineIf arms (#1660). `runtime_safe` is true ONLY when
            // the LAST filter produced a genuine SafeString → fail-safe.
            for arg in args {
                let (val, runtime_safe) = get_value_safe(arg.trim(), context)?;
                if val.is_truthy() {
                    let text = val.to_string();
                    return Ok(if runtime_safe {
                        text
                    } else {
                        filters::html_escape(&text)
                    });
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
            // Resolve via get_value_safe for dotted path and literal support
            // AND to thread the runtime-safe flag (#1672, parallel-path per
            // CLAUDE.md #1646): a custom filter that `mark_safe()`s at runtime
            // (e.g. `{% cycle a|md ... %}`) must NOT be re-escaped, matching the
            // Variable/InlineIf arms (#1660). `runtime_safe` is true ONLY when
            // the LAST filter produced a genuine SafeString → fail-safe.
            let (resolved, runtime_safe) = get_value_safe(val.trim(), context)?;
            let output = if matches!(resolved, Value::Missing) {
                // Unresolved variable — output the raw name (Django behavior)
                filters::html_escape(val.trim())
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

            // Resolve variable references in args through the SAME shared
            // helper as `Node::AssignTag`. This inline resolver used to be a
            // hand-copied twin of `resolve_tag_arg` that (crucially) skipped
            // the JSON encoding, so a list/object arg collapsed to the opaque
            // "[List]" / "[Object]" placeholder. Routing through
            // `resolve_tag_arg` (which encodes via `value_to_arg_string`)
            // retires that parallel-path-drift class (CLAUDE.md #1646, #2042):
            // block handlers now receive the structured payload like the
            // CustomTag and AssignTag paths already do.
            let resolved_args: Vec<String> = args
                .iter()
                .map(|arg| resolve_tag_arg(arg, context))
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
            //
            // Resolve args (JSON-aware) honoring RESOLVE_ARG_POSITIONS,
            // as in render_nodes_with_loader (#2041).
            let resolved_args = resolve_assign_tag_args(name, args, context);
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

    // Handle Django identity operators "is" / "is not" (Django 4.0+).
    // " is not " MUST be checked before " is " because the former
    // contains the latter as a substring. Space-padded markers avoid
    // matching variable names that merely contain "is" (e.g. "analysis").
    if let Some(pos) = condition.find(" is not ") {
        let left = get_value(condition[..pos].trim(), context)?;
        let right = get_value(condition[pos + 8..].trim(), context)?;
        return Ok(!values_identity(&left, &right));
    }
    if let Some(pos) = condition.find(" is ") {
        let left = get_value(condition[..pos].trim(), context)?;
        let right = get_value(condition[pos + 4..].trim(), context)?;
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
            let left = get_value(parts[0], context)?;
            let right = get_value(parts[1], context)?;
            return Ok(try_compare(&left, &right).is_some_and(|c| c >= 0));
        }
    }

    // Handle <= (must be before < to avoid false match)
    if condition.contains("<=") {
        let parts: Vec<&str> = condition.split("<=").map(|s| s.trim()).collect();
        if parts.len() == 2 {
            let left = get_value(parts[0], context)?;
            let right = get_value(parts[1], context)?;
            return Ok(try_compare(&left, &right).is_some_and(|c| c <= 0));
        }
    }

    // Handle "in" operator: {% if item in list %}
    if condition.contains(" in ") {
        let parts: Vec<&str> = condition.splitn(2, " in ").map(|s| s.trim()).collect();
        if parts.len() == 2 {
            let needle = get_value(parts[0], context)?;
            let haystack = get_value(parts[1], context)?;
            return match haystack {
                Value::List(items) | Value::Tuple(items) => {
                    Ok(items.iter().any(|item| values_equal(&needle, item)))
                }
                Value::String(s) => {
                    if let Value::String(n) = &needle {
                        Ok(s.contains(n.as_str()))
                    } else {
                        Ok(false)
                    }
                }
                Value::Object(map) => {
                    // Django: "x in dict" checks dict keys.
                    //
                    // The needle is STRINGIFIED, which is not what Python does
                    // — `0 in {"0": 1}` is False there, because `0 == "0"` is
                    // False. The #2335 randomised sweep found it and it is
                    // NOT fixed here (#1079): djust's own wire format coerces
                    // every dict key to a string, so a view holding
                    // `{1234567: "x"}` reaches this arm as `{"1234567": …}`
                    // and the coercion is the only thing that keeps
                    // `{% if pk in d %}` working — a behaviour
                    // `test_localization_does_not_reach_dict_lookup_keys`
                    // (#2221) pins deliberately. Removing it moves djust
                    // TOWARDS Django for a string-keyed dict and AWAY from it
                    // for an int-keyed one, which is a design decision about
                    // the wire format rather than a comparison fix. Tracked
                    // at #2339; pinned in
                    // `TestKnownAdjacentDivergencesNotFixedHere`.
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
            return Ok(try_compare(&left, &right).is_some_and(|c| c > 0));
        }
    }

    // Handle < (less than)
    if condition.contains(" < ") {
        let parts: Vec<&str> = condition.split(" < ").map(|s| s.trim()).collect();
        if parts.len() == 2 {
            let left = get_value(parts[0], context)?;
            let right = get_value(parts[1], context)?;
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
    Ok(get_value(condition, context)?.is_truthy())
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
    // Handle pipe filters in expressions (e.g., "project.id|stringformat:\"s\"")
    if expr.contains('|') {
        let parts: Vec<&str> = expr.splitn(2, '|').collect();
        let var_name = parts[0].trim();
        let filter_expr = parts[1].trim();

        // Resolve the base variable
        let mut value = get_value(var_name, context)?;

        // Track the LAST filter's runtime safeness, mirroring the Variable arm
        // (#1660). A plain-returning filter after a runtime-safe one re-taints.
        // Seeded with the context's own safety so this arm carries Django's
        // input term too (#2274) — the third of the three sites, kept in step
        // with the other two by construction (#1646).
        let mut runtime_safe = context.is_safe(var_name);
        // See the Variable arm: item-level safety, seeded from the context
        // (#2283, #2287) — the third of the three sites, kept in step with the
        // other two by construction (#1646).
        let mut items_safe = context.items_are_safe(var_name);

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

    if let Ok(i) = expr.parse::<i64>() {
        return Ok((Value::Integer(i), false));
    }

    if let Ok(f) = expr.parse::<f64>() {
        return Ok((Value::Float(f), false));
    }

    // String literal (remove quotes)
    if (expr.starts_with('"') && expr.ends_with('"'))
        || (expr.starts_with('\'') && expr.ends_with('\''))
    {
        return Ok((Value::String(expr[1..expr.len() - 1].to_string()), false));
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
        (Value::Float(a), Value::Float(b)) => (a - b).abs() < f64::EPSILON,
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
            Some((a, b)) => (a - b).abs() < f64::EPSILON,
            None => false,
        },
        // Everything else keeps the pre-#2214 answer. A guarded arm is not
        // exhaustive, so this arm is still reachable — but no longer for
        // `(Float, Integer)`, which has had its own exact arms since #2243.
        _ => false,
    }
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
        (Value::Float(a), Value::Float(b)) => {
            if (a - b).abs() < f64::EPSILON {
                Some(0)
            } else if a < b {
                Some(-1)
            } else {
                Some(1)
            }
        }
        // Allow comparing integers and floats
        (Value::Integer(a), Value::Float(b)) => {
            let a_f = *a as f64;
            if (a_f - b).abs() < f64::EPSILON {
                Some(0)
            } else if a_f < *b {
                Some(-1)
            } else {
                Some(1)
            }
        }
        (Value::Float(a), Value::Integer(b)) => {
            let b_f = *b as f64;
            if (a - b_f).abs() < f64::EPSILON {
                Some(0)
            } else if *a < b_f {
                Some(-1)
            } else {
                Some(1)
            }
        }
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
            Some((a, b)) => {
                if (a - b).abs() < f64::EPSILON {
                    Some(0)
                } else if a < b {
                    Some(-1)
                } else {
                    Some(1)
                }
            }
            // Python cannot order this pair: `None`, so every operator is false.
            None => None,
        },
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

        let mut map = IndexMap::new();
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

        // Integer key converted to string for lookup.
        //
        // Python answers False here (`5 == "5"` is False) and the #2335
        // randomised sweep reports it; it is deliberately NOT changed. See the
        // `Value::Object` arm of the `in` operator for why — djust's wire
        // format coerces dict keys to strings, so this coercion is what keeps
        // `{% if pk in d %}` working (#2221), and removing it trades one
        // divergence for another. Tracked at #2339.
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

        let mut map = IndexMap::new();
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
        let mut user = IndexMap::new();
        user.insert("name".to_string(), Value::String("Alice".to_string()));
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
        map.insert("key".to_string(), Value::String("val".to_string()));
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
        obj.insert("key".to_string(), Value::String("val".to_string()));
        ctx.set("obj".to_string(), Value::Object(obj));
        ctx.set("count".to_string(), Value::Integer(42));
        ctx.set("name".to_string(), Value::String("hello".to_string()));
        ctx
    }

    #[test]
    fn value_to_arg_string_encodes_structured_but_not_scalars() {
        // The single source of truth for arg encoding: list/object -> JSON,
        // scalars -> Display.
        let list = Value::List(vec![Value::Integer(1), Value::Integer(2)]);
        assert_eq!(value_to_arg_string(&list), "[1,2]");
        let mut map = IndexMap::new();
        map.insert("key".to_string(), Value::String("val".to_string()));
        assert_eq!(value_to_arg_string(&Value::Object(map)), r#"{"key":"val"}"#);
        assert_eq!(value_to_arg_string(&Value::Integer(42)), "42");
        // Scalars go through Display, so this follows it to `True` (#2203).
        assert_eq!(value_to_arg_string(&Value::Bool(true)), "True");
        assert_eq!(value_to_arg_string(&Value::String("hi".to_string())), "hi");
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
        a.insert("a".to_string(), Value::Integer(1));
        a.insert("b".to_string(), Value::Integer(2));
        let mut b = indexmap::IndexMap::new();
        b.insert("b".to_string(), Value::Integer(2));
        b.insert("a".to_string(), Value::Integer(1));
        assert!(values_equal(&Value::Object(a.clone()), &Value::Object(b)));

        let mut c = indexmap::IndexMap::new();
        c.insert("a".to_string(), Value::Integer(1));
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
