//! Result-bound authored UTF-8 byte intervals. No ambient collector (#3252).
use std::ops::{Deref, Range};

/// Output operations shared by the ordinary and provenance-aware renderer.
/// Monomorphization keeps interval allocation and copying out of plain renders.
pub trait RenderOutput: Default + From<String> + Deref<Target = str> + std::fmt::Display {
    const TRACKED: bool;
    fn authored(text: &str) -> Self;
    /// Literal-derived expression bytes affect context, but never grant authority.
    fn context_literal(text: String) -> Self {
        text.into()
    }
    fn captured(text: String, _literal: Option<String>) -> Self {
        text.into()
    }
    fn context_neutral(text: String) -> Self {
        text.into()
    }
    /// Drop start-tag authority while carrying original literal context.
    fn flatten(self, text: String) -> Self {
        text.into()
    }
    fn flattened_literals(&mut self, _literals: Vec<Option<String>>) {}
    fn from_authored_output(output: djust_core::context::AuthoredOutput) -> Self;
    fn append(&mut self, child: &Self);
    fn push_str(&mut self, text: &str);
    fn source_text(text: &str, _source: &SourceText) -> Self {
        Self::authored(text)
    }
    fn has_origin(&self) -> bool {
        false
    }
    fn identify(&mut self, _identity: &str) {}
    fn loop_identity(&mut self, _context: &djust_core::Context) {}
}
impl RenderOutput for String {
    const TRACKED: bool = false;
    fn authored(text: &str) -> Self {
        text.to_owned()
    }
    fn from_authored_output(output: djust_core::context::AuthoredOutput) -> Self {
        output.0
    }
    fn append(&mut self, child: &Self) {
        self.push_str(child);
    }
    fn push_str(&mut self, text: &str) {
        String::push_str(self, text);
    }
}

/// HTML and the disjoint authored runs in that exact output. Converting to a
/// value/String intentionally drops authority; filters and reinjection cannot
/// recover it, even when their output happens to be byte-identical.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Rendered {
    pub html: String,
    pub authored: Vec<Range<usize>>,
    pub origins: Vec<(usize, usize, usize, String)>,
    /// None means a cached/captured fragment lacks literal metadata: fail closed.
    pub literal_only: Option<String>,
    /// Authored opening byte in HTML -> corresponding literal-only byte.
    pub literal_offsets: Vec<(usize, usize)>,
}
impl Default for Rendered {
    fn default() -> Self {
        Self::from(String::new())
    }
}
impl Rendered {
    pub fn into_authored_output(self) -> djust_core::context::AuthoredOutput {
        (
            self.html,
            self.authored
                .into_iter()
                .map(|r| (r.start, r.end))
                .collect(),
            self.origins,
            djust_core::context::LiteralOutput {
                html: self.literal_only,
                openings: self.literal_offsets,
            },
        )
    }
    /// Recheck source liveness on the branch that actually rendered. Literal
    /// expression bytes participate, but other values cannot close that context.
    /// Non-literal values are blank for this authored-text computation.
    /// Do this once on the complete result, never on incomplete include/block
    /// fragments. The ordinary final-HTML survival check remains necessary.
    pub fn retain_live_authority(&mut self) {
        if self.origins.is_empty() {
            return;
        }
        #[cfg(feature = "liveview")]
        let starts: std::collections::HashSet<_> = self
            .literal_only
            .as_ref()
            .map(|literal| {
                djust_vdom::lazy_provenance::lazy_elements(literal, &[(0, literal.len())])
                    .into_iter()
                    .map(|e| e.start)
                    .collect()
            })
            .unwrap_or_default();
        #[cfg(not(feature = "liveview"))]
        let starts: std::collections::HashSet<usize> = Default::default();
        let offsets: std::collections::HashMap<_, _> =
            self.literal_offsets.iter().copied().collect();
        let rejected: std::collections::HashSet<_> = self
            .origins
            .iter()
            .filter(|(a, _, offset, _)| {
                *offset == 0 && !offsets.get(a).is_some_and(|offset| starts.contains(offset))
            })
            .map(|(a, _, _, _)| *a)
            .collect();
        if rejected.is_empty() {
            return;
        }
        let mut openings: Vec<_> = rejected.iter().copied().collect();
        openings.sort_unstable();
        // Removing the opening byte alone prevents both registration and later
        // composition from recovering a rejected candidate's authority.
        let mut authored = Vec::new();
        for span in &self.authored {
            let mut cursor = span.start;
            let left = openings.partition_point(|a| *a < span.start);
            let right = openings.partition_point(|a| *a < span.end);
            for &a in &openings[left..right] {
                if cursor < a {
                    authored.push(cursor..a);
                }
                cursor = a + 1;
            }
            if cursor < span.end {
                authored.push(cursor..span.end);
            }
        }
        self.authored = authored;
        self.origins.retain(|(a, _, offset, _)| {
            a.checked_sub(*offset)
                .is_some_and(|start| !rejected.contains(&start))
        });
    }
}
impl From<String> for Rendered {
    fn from(html: String) -> Self {
        Self {
            html,
            authored: Vec::new(),
            origins: Vec::new(),
            literal_only: Some(String::new()),
            literal_offsets: Vec::new(),
        }
    }
}
impl Deref for Rendered {
    type Target = str;
    fn deref(&self) -> &str {
        &self.html
    }
}
impl std::fmt::Display for Rendered {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.html)
    }
}
impl RenderOutput for Rendered {
    const TRACKED: bool = true;
    #[allow(clippy::single_range_in_vec_init)] // One byte run, not an integer list.
    fn authored(text: &str) -> Self {
        Self {
            html: text.to_owned(),
            literal_only: Some(text.to_owned()),
            literal_offsets: vec![(0, 0)],
            origins: if text.to_ascii_lowercase().contains("dj-view") {
                vec![(0, text.len(), 0, String::new())]
            } else {
                Vec::new()
            },
            authored: if text.is_empty() {
                Vec::new()
            } else {
                vec![0..text.len()]
            },
        }
    }
    fn captured(text: String, literal: Option<String>) -> Self {
        let mut result = Self::from(text);
        result.literal_only = literal;
        result
    }
    fn context_literal(text: String) -> Self {
        let mut result = Self::from(text);
        result.literal_only = Some(result.html.clone());
        result
    }
    fn source_text(text: &str, source: &SourceText) -> Self {
        let mut result = Self::authored(text);
        result.origins = source.origins.clone();
        result.literal_offsets = source
            .origins
            .iter()
            .filter(|(_, _, offset, _)| *offset == 0)
            .map(|(a, _, _, _)| (*a, *a))
            .collect();
        result.authored.clear();
        let mut cursor = 0;
        for &offset in &source.inert_openings {
            if cursor < offset {
                result.authored.push(cursor..offset);
            }
            cursor = offset + 1;
        }
        if cursor < text.len() {
            result.authored.push(cursor..text.len());
        }
        result
    }
    fn from_authored_output(output: djust_core::context::AuthoredOutput) -> Self {
        Self {
            html: output.0,
            authored: output.1.into_iter().map(|(a, b)| a..b).collect(),
            origins: output.2,
            literal_only: output.3.html,
            literal_offsets: output.3.openings,
        }
    }
    fn flatten(self, text: String) -> Self {
        Self::from_authored_output(djust_core::context::flatten_authored_output(
            self.into_authored_output(),
            text,
        ))
    }
    fn flattened_literals(&mut self, literals: Vec<Option<String>>) {
        // Resolver notifications belong to this node. Values are blank; a
        // flattened capture supplies its original authored bytes instead.
        for literal in literals {
            match (&mut self.literal_only, literal) {
                (Some(output), Some(literal)) => output.push_str(&literal),
                _ => self.literal_only = None,
            }
        }
    }
    fn append(&mut self, child: &Self) {
        let offset = self.html.len();
        match (&mut self.literal_only, &child.literal_only) {
            (Some(output), Some(literal)) => {
                let literal_offset = output.len();
                self.literal_offsets.extend(
                    child
                        .literal_offsets
                        .iter()
                        .map(|(a, b)| (a + offset, b + literal_offset)),
                );
                output.push_str(literal);
            }
            _ => self.literal_only = None,
        }
        self.authored.extend(
            child
                .authored
                .iter()
                .map(|r| r.start + offset..r.end + offset),
        );
        self.origins.extend(
            child
                .origins
                .iter()
                .map(|(a, b, c, id)| (a + offset, b + offset, *c, id.clone())),
        );
        self.html.push_str(&child.html);
    }
    fn push_str(&mut self, text: &str) {
        self.html.push_str(text);
    }
    fn has_origin(&self) -> bool {
        !self.origins.is_empty()
    }
    fn identify(&mut self, identity: &str) {
        for (_, _, _, id) in &mut self.origins {
            *id = format!("{identity}/{id}");
        }
    }
    fn loop_identity(&mut self, context: &djust_core::Context) {
        if self.origins.is_empty() {
            return;
        }
        self.identify(&format!("loop{}", context.dj_if_loop_path()));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{NoOpTemplateLoader, Template};
    use djust_core::{Context, Value};
    fn render(source: &str) -> Rendered {
        let mut ctx = Context::new();
        ctx.set("flag".into(), Value::Bool(true));
        ctx.set(
            "xs".into(),
            Value::List(vec![Value::Integer(1), Value::Integer(2)]),
        );
        ctx.set("extra".into(), Value::String("Δ".into()));
        Template::new(source)
            .unwrap()
            .render_with_provenance(&ctx, &NoOpTemplateLoader)
            .unwrap()
    }
    #[test]
    fn flatten_preserves_literal_bytes_and_drops_authority() {
        for replacement in ["évalue", "changed", ""] {
            let mut output = Rendered::authored("<script>");
            output.append(&Rendered::from("</script>".to_owned()));
            let flattened = output.flatten(replacement.into());
            assert_eq!(flattened.literal_only.as_deref(), Some("<script>"));
            assert!(flattened.authored.is_empty());
            assert!(flattened.origins.is_empty());
        }
    }

    #[test]
    fn provenance_flatten_site_inventory() {
        // Grep-style review prompt, not a proof of completeness.
        // New conversions must be classified and routed through the shared
        // helper before updating this inventory. Includes plain helper paths
        // used by custom body captures and the Context Value/bridge resolver.
        let mut inventory = String::new();
        for (name, source) in [
            ("renderer.rs", include_str!("renderer.rs")),
            ("lib.rs", include_str!("lib.rs")),
            (
                "djust_live/src/provenance_edits.rs",
                include_str!("../../djust_live/src/provenance_edits.rs"),
            ),
            (
                "djust_live/src/model_serializer.rs",
                include_str!("../../djust_live/src/model_serializer.rs"),
            ),
            (
                "djust_live/src/lib.rs",
                include_str!("../../djust_live/src/lib.rs"),
            ),
            (
                "djust_live/src/actors/view.rs",
                include_str!("../../djust_live/src/actors/view.rs"),
            ),
            (
                "djust_live/src/actors/supervisor.rs",
                include_str!("../../djust_live/src/actors/supervisor.rs"),
            ),
            (
                "djust_live/src/actors/session.rs",
                include_str!("../../djust_live/src/actors/session.rs"),
            ),
            (
                "djust_live/src/actors/mod.rs",
                include_str!("../../djust_live/src/actors/mod.rs"),
            ),
            (
                "djust_live/src/actors/messages.rs",
                include_str!("../../djust_live/src/actors/messages.rs"),
            ),
            (
                "djust_live/src/actors/error.rs",
                include_str!("../../djust_live/src/actors/error.rs"),
            ),
            (
                "djust_live/src/actors/component.rs",
                include_str!("../../djust_live/src/actors/component.rs"),
            ),
            ("registry.rs", include_str!("registry.rs")),
            (
                "context.rs",
                include_str!("../../djust_core/src/context.rs"),
            ),
            ("provenance.rs", include_str!("provenance.rs")),
        ] {
            inventory.push_str(name);
            inventory.push('\n');
            // Include helpers added after test modules; pin test conversions too.
            for line in source.lines() {
                let line = line.trim();
                if !line.starts_with("//")
                    && [
                        ".into()",
                        "::from(",
                        "output.0",
                        ".0.clone()",
                        "html.clone()",
                        "format!",
                        ".to_owned()",
                        ".to_string()",
                        ".html",
                        ".flatten(",
                        "into_authored_output(",
                        "flatten_authored_output(",
                        "render_nodes_with_loader_mut(",
                        "render_block_super(",
                        "record_provenance_flatten(",
                        "literal_only",
                        "literal_offsets",
                    ]
                    .iter()
                    .any(|needle| line.contains(needle))
                {
                    inventory.push_str(line);
                    inventory.push('\n');
                }
            }
        }
        assert_eq!(
            inventory,
            include_str!("../tests/fixtures/provenance_flatten_sites.txt"),
            "Review new flatten/conversion sites for mixed provenance before updating the pin"
        );
    }

    #[test]
    #[cfg(feature = "liveview")]
    fn random_registration_requires_two_independent_trees() {
        use html5ever::{parse_document, tendril::TendrilSink};
        use markup5ever_rcdom::{NodeData, RcDom};
        // Ordinary RcDom parsing: no production annotation, offset projection,
        // retain_live_authority, or lazy_elements used by the oracle.
        fn live(html: &str) -> bool {
            let dom = parse_document(RcDom::default(), Default::default()).one(html);
            let mut stack = vec![dom.document.clone()];
            while let Some(node) = stack.pop() {
                if let NodeData::Element { name, attrs, .. } = &node.data {
                    if (&*name.local) == "div"
                        && attrs.borrow().iter().any(|a| {
                            (&*a.name.local) == "id" && a.value.as_ref() == "property-candidate"
                        })
                    {
                        return true;
                    }
                }
                // template_contents is deliberately not traversed: inert.
                stack.extend(node.children.borrow().iter().cloned());
            }
            false
        }
        let pieces = [
            "",
            "é",
            "<!--",
            "-->",
            "<script>",
            "</script>",
            "<template>",
            "</template>",
            "<svg><title>",
            "</title></svg>",
            "<table>",
            "</table>",
            "<math><mtext>",
            "</mtext></math>",
            "<select>",
            "</select>",
            "<a x='",
            "'>",
            "<svg/>",
            "c=d<svg><desc><table><desc>x</DESC><math><svg/></svg x='>'>",
        ];
        const TAG: &str = "<div id='property-candidate' dj-view='app.Child' dj-lazy></div>";
        let mut seed = 0x3430_3442_u64;
        let mut registered = 0;
        for _ in 0..4000 {
            let mut choose = || {
                seed ^= seed << 13;
                seed ^= seed >> 7;
                seed ^= seed << 17;
                pieces[(seed as usize) % pieces.len()]
            };
            let mut source = String::new();
            let mut literal = String::new();
            let mut final_html = String::new();
            let mut ctx = Context::new();
            for i in 0..8 {
                let text = choose();
                if i % 3 == 0 {
                    let value = choose();
                    ctx.set(format!("value{i}"), Value::String(value.into()));
                    source.push_str(&format!("{{{{ value{i}|safe }}}}"));
                    final_html.push_str(value);
                } else if i % 3 == 1 {
                    // Select a template literal expression on the rendered branch.
                    ctx.set("flag".into(), Value::Bool(true));
                    source.push_str("{% if flag %}");
                    source.push_str(&format!(
                        "{{{{ {}|safe }}}}",
                        serde_json::to_string(text).unwrap()
                    ));
                    source.push_str("{% else %}<!--{% endif %}");
                    literal.push_str(text);
                    final_html.push_str(text);
                } else {
                    source.push_str("{% filter lower %}");
                    source.push_str(text);
                    source.push_str("{% endfilter %}");
                    // A flattened run contributes original authored bytes.
                    literal.push_str(text);
                    final_html.push_str(&text.to_lowercase());
                }
                if i == 4 {
                    source.push_str(TAG);
                    literal.push_str(TAG);
                    final_html.push_str(TAG);
                }
            }
            let output = Template::new(&source)
                .unwrap()
                .render_with_provenance(&ctx, &NoOpTemplateLoader)
                .unwrap();
            // Actual final HTML also includes renderer-owned branch markers.
            // The oracle parses those real bytes, independently of provenance.
            let final_html = &output.html;
            let elements = djust_vdom::lazy_provenance::lazy_elements(
                &output.html,
                &output
                    .authored
                    .iter()
                    .map(|r| (r.start, r.end))
                    .collect::<Vec<_>>(),
            );
            if !elements.is_empty() {
                registered += 1;
                assert_eq!(
                    output.literal_only.as_deref(),
                    Some(literal.as_str()),
                    "projection {source:?}"
                );
                assert!(
                    live(&literal),
                    "literal-only violation: {source:?} => {literal:?}"
                );
                assert!(
                    live(final_html),
                    "final-page violation: {source:?} => {final_html:?}"
                );
            }
        }
        #[derive(Clone)]
        struct Loader(std::collections::HashMap<String, String>);
        impl crate::TemplateLoader for Loader {
            fn shared_handle(&self) -> std::sync::Arc<dyn crate::TemplateLoader + Send + Sync> {
                std::sync::Arc::new(self.clone())
            }
            fn load_template(&self, name: &str) -> crate::Result<Vec<crate::parser::Node>> {
                Ok(Template::new(self.0.get(name).expect("property fixture"))?
                    .nodes
                    .clone())
            }
        }
        // Random capture/alias/filename/filter-argument templates. Construct the
        // oracle input from the selected operation, without reading sidecars.
        for trial in 0..1000 {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            let before = pieces[(seed as usize) % pieces.len()];
            let parent = pieces[((seed >> 12) as usize) % pieces.len()];
            let between = pieces[((seed >> 24) as usize) % pieces.len()];
            let after = pieces[((seed >> 36) as usize) % pieces.len()];
            let value = pieces[((seed >> 48) as usize) % pieces.len()];
            let mut ctx = Context::new();
            ctx.set("q".into(), Value::String(value.into()));
            ctx.set("empty".into(), Value::String(String::new()));
            ctx.set("truthy".into(), Value::String("</script>".into()));
            let (body, expected_literal) = match trial % 5 {
                0 => (format!("{before}{{% with s=block.super %}}{{{{ empty }}}}{between}{{{{ s }}}}{TAG}{after}{{% endwith %}}"),
                    format!("{before}{between}{parent}{TAG}{after}")),
                1 => (format!("{before}{{% with s=block.super %}}{{% with t=s %}}{between}{{{{ t }}}}{{{{ t }}}}{TAG}{after}{{% endwith %}}{{% endwith %}}"),
                    format!("{before}{between}{parent}{parent}{TAG}{after}")),
                2 => (format!("{before}{{% firstof block.super as s %}}{between}{{{{ s }}}}{TAG}{after}"),
                    format!("{before}{between}{parent}{TAG}{after}")),
                3 => (format!("{before}{{{{ truthy|default:block.super|safe }}}}{between}{TAG}{after}"),
                    format!("{before}{between}{TAG}{after}")),
                _ => (format!("{before}{{% include block.super %}}{between}{after}"),
                    format!("{before}{TAG}{between}{TAG}{after}")),
            };
            // Include selector uses rendered parent bytes as a filename, but its
            // bytes do not contribute to the included output's literal context.
            let parent_source = if trial % 5 == 4 {
                "leaf".to_owned()
            } else {
                format!("{parent}{{{{ q|safe }}}}")
            };
            ctx.set("filename".into(), Value::String("leaf".into()));
            let loader = Loader(std::collections::HashMap::from([
                (
                    "base".into(),
                    format!("{{% block body %}}{parent_source}{{% endblock %}}"),
                ),
                ("leaf".into(), format!("{{{{ q|safe }}}}{TAG}")),
            ]));
            let source = format!("{{% extends 'base' %}}{{% block body %}}{body}{{% endblock %}}");
            let output = Template::new(&source)
                .unwrap()
                .render_with_provenance(&ctx, &loader)
                .unwrap();
            let elements = djust_vdom::lazy_provenance::lazy_elements(
                &output.html,
                &output
                    .authored
                    .iter()
                    .map(|r| (r.start, r.end))
                    .collect::<Vec<_>>(),
            );
            if !elements.is_empty() {
                assert!(
                    live(&expected_literal),
                    "capture literal-only violation: {source:?} => {expected_literal:?}"
                );
                assert!(
                    live(&output.html),
                    "capture final-page violation: {source:?}"
                );
            }
        }
        println!("random registrations: {registered}");
        assert!(
            registered > 100,
            "property must exercise positive registrations"
        );
    }

    #[test]
    fn expression_context_never_grants_start_tag_authority() {
        let r = render("{{ \"<div dj-view='app.Child' dj-lazy></div>\"|safe }}");
        assert_eq!(r.literal_only.as_deref(), Some(r.html.as_str()));
        assert!(r.authored.is_empty());
        assert!(r.origins.is_empty());
    }

    #[test]
    fn literal_binding_closer_and_shadowing_affect_liveness() {
        let live = render("{{ \"<!--\" }}{% with o=\"-->\" %}{{ o|lower }}{% endwith %}<div dj-view='app.Child' dj-lazy></div>");
        #[cfg(feature = "liveview")]
        assert_eq!(live.origins.len(), 1);
        assert!(live.html.starts_with("<!---->"));
        let inert = render("{{ \"<!--\" }}{% with o=\"x\" %}{% with o=extra %}{{ o|safe }}{% endwith %}{% endwith %}<div dj-view='app.Child' dj-lazy></div>-->");
        assert!(inert.origins.is_empty());
    }

    #[test]
    fn conditional_markers_are_masked_without_breaking_context() {
        let mut ctx = Context::new();
        ctx.set("flag".into(), Value::Bool(true));
        let r = Template::new("{% if flag %}<div dj-view='app.Child' dj-lazy></div>{% endif %}<div dj-view='app.Child' dj-lazy></div>")
            .unwrap().render_with_provenance(&ctx, &NoOpTemplateLoader).unwrap();
        #[cfg(feature = "liveview")]
        {
            assert_eq!(r.origins.len(), 2);
            assert!(r.html.contains("<!--dj-if"));
        }
        assert!(!r.literal_only.as_ref().unwrap().contains("dj-if"));
    }

    #[test]
    fn utf8_conditional_loop_composition() {
        let r = render("é{% if flag %}{% for x in xs %}<div dj-view=\"app.Child\" class=\"{{ extra }}\" dj-lazy></div>{% endfor %}{% endif %}");
        let text: String = r.authored.iter().map(|s| &r.html[s.clone()]).collect();
        assert_eq!(text.matches("dj-lazy").count(), 2);
        assert!(!text.contains('Δ'));
        assert!(text.starts_with('é'));
        for span in &r.authored {
            assert!(r.html.is_char_boundary(span.start) && r.html.is_char_boundary(span.end));
        }
    }
    #[test]
    fn filters_and_reinjection_drop_authority() {
        for filter in ["force_escape", "striptags", "lower"] {
            let r = render(&format!(
                "{{% filter {filter} %}}<div dj-view=\"app.Child\" dj-lazy></div>{{% endfilter %}}"
            ));
            assert!(r.authored.is_empty(), "{filter}: {r:?}");
        }
    }
    #[test]
    fn false_branch_and_value_have_no_authority() {
        let r = render(
            "{% if missing %}<div dj-view=\"app.Child\" dj-lazy></div>{% endif %}{{ extra|safe }}",
        );
        assert!(r.authored.is_empty());
    }
}

/// Compile-time liveness in the source template, independent of rendered values.
#[derive(Debug, Clone, Default)]
pub struct SourceText {
    pub inert_openings: Vec<usize>,
    pub origins: Vec<(usize, usize, usize, String)>,
}

pub fn annotate_source(
    nodes: &mut [crate::parser::Node],
    tokens: &[crate::lexer::Token],
    spans: &[crate::lexer::Span],
    source: &str,
) {
    use crate::parser::Node;
    if spans.is_empty() {
        return;
    }
    fn sites(nodes: &mut [crate::parser::Node], index: &mut usize) {
        use crate::parser::Node;
        for node in nodes {
            if let Node::Located {
                nodes, lazy_site, ..
            } = node
            {
                if matches!(nodes.first(), Some(Node::Include { .. }))
                    || matches!(nodes.first(), Some(Node::Variable(name, _, _)) if name == "block.super")
                {
                    *lazy_site = Some(*index);
                    *index += 1;
                }
            }
            for children in node.child_lists_mut().into_iter().flatten() {
                sites(children, index);
            }
        }
    }
    sites(nodes, &mut 0);
    if !source.to_ascii_lowercase().contains("dj-lazy") {
        return;
    }
    // Same byte offsets as source; template syntax cannot terminate an HTML context.
    let mut masked = source.as_bytes().to_vec();
    let mut texts = Vec::new();
    let mut comment = false;
    for (token, &(a, b)) in tokens.iter().zip(spans) {
        if let crate::lexer::Token::Tag(name, _) = token {
            if name == "comment" {
                comment = true;
            }
            if name == "endcomment" {
                comment = false;
            }
        }
        if !comment && matches!(token, crate::lexer::Token::Text(_)) {
            texts.push((a, b));
        } else {
            masked[a..b].fill(b' ');
        }
    }
    // Only whole lexer byte ranges were replaced; no UTF-8 scalar is split.
    let masked = String::from_utf8(masked).unwrap_or_default();
    // Source annotation locates candidate authority spans only. Liveness is
    // decided once on the rendered literal-only branch, not all static branches.
    #[cfg(feature = "liveview")]
    let candidates = djust_vdom::lazy_provenance::authority_spans(&masked, &[(0, masked.len())]);
    #[cfg(not(feature = "liveview"))]
    let candidates: Vec<(usize, usize)> = Vec::new();
    let containers: Vec<_> = candidates
        .iter()
        .enumerate()
        .map(|(index, &(start, end))| {
            let hash = source.as_bytes()[start..end]
                .iter()
                .fold(0xcbf29ce484222325u64, |h, b| {
                    (h ^ u64::from(*b)).wrapping_mul(0x100000001b3)
                });
            (start, end, format!("container{index}:{hash:016x}"))
        })
        .collect();
    fn walk(
        nodes: &mut [Node],
        texts: &[(usize, usize)],
        cursor: &mut usize,
        containers: &[(usize, usize, String)],
        source: &std::sync::Arc<str>,
    ) {
        for node in nodes {
            if let Node::Text(text) = node {
                // Comment blocks can discard lexer text. Match the actual literal,
                // rather than assuming every text token reaches the AST.
                let matched = texts[*cursor..]
                    .iter()
                    .position(|&(a, b)| source.get(a..b) == Some(text.as_str()));
                let a = if let Some(index) = matched {
                    *cursor += index + 1;
                    texts[*cursor - 1].0
                } else {
                    // Synthetic/verbatim text without a lexer span fails closed.
                    usize::MAX
                };
                let mut metadata = SourceText::default();
                for (offset, _) in text.match_indices('<') {
                    if !containers
                        .iter()
                        .any(|(start, _, _)| a.checked_add(offset) == Some(*start))
                    {
                        metadata.inert_openings.push(offset);
                    }
                }
                for (start, end, id) in containers {
                    let left = (*start).max(a);
                    let right = (*end).min(a.saturating_add(text.len()));
                    if left < right {
                        metadata
                            .origins
                            .push((left - a, right - a, left - start, id.clone()));
                    }
                }
                let text = std::mem::take(text);
                let span = if a == usize::MAX {
                    (0, 0)
                } else {
                    (a, a + text.len())
                };
                *node = Node::Located {
                    nodes: vec![Node::Text(text)],
                    span,
                    registry_namespace: crate::registry_scope::current(),
                    source: source.clone(),
                    origin: None,
                    lazy_origin: None,
                    lazy_site: None,
                    lazy_text: Some(metadata),
                };
                continue;
            }
            for children in node.child_lists_mut().into_iter().flatten() {
                walk(children, texts, cursor, containers, source);
            }
        }
    }
    walk(
        nodes,
        &texts,
        &mut 0,
        &containers,
        &std::sync::Arc::from(source),
    );
}
