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
    fn context_neutral(text: String) -> Self {
        text.into()
    }
    /// Byte-changing mixed parent output makes subsequent authority uncertain.
    fn context_uncertain(text: String) -> Self {
        text.into()
    }
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
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct Rendered {
    pub html: String,
    pub authored: Vec<Range<usize>>,
    pub origins: Vec<(usize, usize, usize, String)>,
    /// All emitted literal bytes, including context-opening markup with no authority.
    pub literals: Vec<Range<usize>>,
    /// Renderer-owned markers remain masked, and cannot end a trusted context run.
    pub neutral: Vec<Range<usize>>,
    /// First byte at which a transformed mixed parent loses context boundaries.
    pub authority_cutoff: Option<usize>,
}
impl Rendered {
    /// Recheck source liveness on the branch that actually rendered. Literal
    /// expression bytes participate, but other values cannot close that context.
    /// Non-literal values are blank for this authored-text computation.
    /// Do this once on the complete result, never on incomplete include/block
    /// fragments. The ordinary final-HTML survival check remains necessary.
    pub fn retain_live_authority(&mut self) {
        if self.origins.is_empty() {
            return;
        }
        let mut masked = vec![b' '; self.html.len()];
        for span in &self.literals {
            masked[span.clone()].copy_from_slice(&self.html.as_bytes()[span.clone()]);
        }
        // Literal spans contain whole UTF-8 scalars; opaque gaps are ASCII.
        let masked = String::from_utf8(masked).unwrap_or_default();
        #[cfg(feature = "liveview")]
        let live = djust_vdom::lazy_provenance::lazy_elements(&masked, &[(0, masked.len())]);
        #[cfg(not(feature = "liveview"))]
        let live: Vec<SourceContainer> = Vec::new();
        let starts: std::collections::HashSet<_> = live.iter().map(|e| e.start).collect();
        let rejected: std::collections::HashSet<_> = self
            .origins
            .iter()
            .filter(|(a, _, offset, _)| {
                *offset == 0
                    && (!starts.contains(a)
                        || self.authority_cutoff.is_some_and(|cutoff| *a >= cutoff))
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
            literals: Vec::new(),
            neutral: Vec::new(),
            authority_cutoff: None,
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
            neutral: Vec::new(),
            authority_cutoff: None,
            literals: if text.is_empty() {
                Vec::new()
            } else {
                vec![0..text.len()]
            },
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
    fn context_neutral(text: String) -> Self {
        let len = text.len();
        let mut result = Self::from(text);
        if len != 0 {
            result.neutral.push(0..len);
        }
        result
    }
    fn context_literal(text: String) -> Self {
        let len = text.len();
        let mut result = Self::from(text);
        if len != 0 {
            result.literals.push(0..len);
        }
        result
    }
    fn source_text(text: &str, source: &SourceText) -> Self {
        let mut result = Self::authored(text);
        result.origins = source.origins.clone();
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
            literals: output.3.into_iter().map(|(a, b)| a..b).collect(),
            neutral: output.4.into_iter().map(|(a, b)| a..b).collect(),
            authority_cutoff: output.5,
        }
    }
    fn context_uncertain(text: String) -> Self {
        let mut result = Self::from(text);
        result.authority_cutoff = Some(0);
        result
    }
    fn append(&mut self, child: &Self) {
        let offset = self.html.len();
        if let Some(cutoff) = child.authority_cutoff {
            let cutoff = offset + cutoff;
            self.authority_cutoff =
                Some(self.authority_cutoff.map_or(cutoff, |old| old.min(cutoff)));
        }
        self.neutral.extend(
            child
                .neutral
                .iter()
                .map(|r| r.start + offset..r.end + offset),
        );
        self.literals.extend(
            child
                .literals
                .iter()
                .map(|r| r.start + offset..r.end + offset),
        );
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
    fn expression_context_never_grants_start_tag_authority() {
        let r = render("{{ \"<div dj-view='app.Child' dj-lazy></div>\"|safe }}");
        assert_eq!(r.literals, vec![0..r.html.len()]);
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
            assert!(!r.neutral.is_empty());
        }
        #[cfg(not(feature = "liveview"))]
        assert!(r.neutral.is_empty());
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
    #[cfg(feature = "liveview")]
    let live = djust_vdom::lazy_provenance::lazy_elements(&masked, &[(0, masked.len())]);
    #[cfg(not(feature = "liveview"))]
    let live: Vec<SourceContainer> = Vec::new();
    let containers: Vec<_> = live
        .iter()
        .enumerate()
        .map(|(index, e)| {
            // Fixed FNV-1a algorithm: stable across hosts and Rust versions.
            let hash = source.as_bytes()[e.start..e.end]
                .iter()
                .fold(0xcbf29ce484222325u64, |h, b| {
                    (h ^ u64::from(*b)).wrapping_mul(0x100000001b3)
                });
            (e.start, e.end, format!("container{index}:{hash:016x}"))
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

#[cfg(not(feature = "liveview"))]
struct SourceContainer {
    start: usize,
    end: usize,
}
