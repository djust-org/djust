//! Validate authored byte runs against the final HTML5 tree (#3252).
//! The parser-private token annotation below never enters emitted HTML. It
//! correlates an actual start-tag token with the element the tree builder
//! creates; copied output text cannot supply a namespaced token annotation.
use html5ever::buffer_queue::BufferQueue;
use html5ever::tendril::StrTendril;
use html5ever::tokenizer::{StartTag, TagToken, Token, TokenSink, TokenSinkResult, Tokenizer};
use html5ever::tree_builder::{ElementFlags, NodeOrText, QuirksMode, TreeBuilder, TreeSink};
use html5ever::ExpandedName;
use html5ever::{Attribute, LocalName, Namespace, QualName};
use markup5ever_rcdom::{Handle, NodeData, RcDom};
use std::borrow::Cow;
use std::cell::{Cell, RefCell};
use std::collections::{HashMap, HashSet};
use std::ops::Range;

const TOKEN_NS: &str = "urn:djust:render-provenance:3252";

/// Only creation by the original token carries its annotation. HTML5 may
/// merge a second body/html token into an existing element, or reconstruct
/// formatting elements. Neither operation grants that element new authority.
#[derive(Default)]
struct AuthoredDom {
    dom: RcDom,
    created: RefCell<HashSet<String>>,
}
impl TreeSink for AuthoredDom {
    type Output = Self;
    type Handle = Handle;
    type ElemName<'a> = ExpandedName<'a>;
    fn finish(self) -> Self {
        self
    }
    fn parse_error(&self, msg: Cow<'static, str>) {
        self.dom.parse_error(msg);
    }
    fn get_document(&self) -> Handle {
        self.dom.get_document()
    }
    fn get_template_contents(&self, target: &Handle) -> Handle {
        self.dom.get_template_contents(target)
    }
    fn set_quirks_mode(&self, mode: QuirksMode) {
        self.dom.set_quirks_mode(mode);
    }
    fn same_node(&self, x: &Handle, y: &Handle) -> bool {
        self.dom.same_node(x, y)
    }
    fn elem_name<'a>(&self, target: &'a Handle) -> ExpandedName<'a> {
        match &target.data {
            NodeData::Element { name, .. } => name.expanded(),
            _ => unreachable!("element name requested for non-element"),
        }
    }
    fn create_element(
        &self,
        name: QualName,
        mut attrs: Vec<Attribute>,
        flags: ElementFlags,
    ) -> Handle {
        attrs.retain(|a| {
            (&*a.name.ns) != TOKEN_NS || self.created.borrow_mut().insert(a.value.to_string())
        });
        self.dom.create_element(name, attrs, flags)
    }
    fn create_comment(&self, text: StrTendril) -> Handle {
        self.dom.create_comment(text)
    }
    fn create_pi(&self, target: StrTendril, data: StrTendril) -> Handle {
        self.dom.create_pi(target, data)
    }
    fn append(&self, parent: &Handle, child: NodeOrText<Handle>) {
        self.dom.append(parent, child);
    }
    fn append_before_sibling(&self, sibling: &Handle, child: NodeOrText<Handle>) {
        self.dom.append_before_sibling(sibling, child);
    }
    fn append_based_on_parent_node(
        &self,
        element: &Handle,
        previous: &Handle,
        child: NodeOrText<Handle>,
    ) {
        self.dom
            .append_based_on_parent_node(element, previous, child);
    }
    fn append_doctype_to_document(
        &self,
        name: StrTendril,
        public_id: StrTendril,
        system_id: StrTendril,
    ) {
        self.dom
            .append_doctype_to_document(name, public_id, system_id);
    }
    fn add_attrs_if_missing(&self, target: &Handle, mut attrs: Vec<Attribute>) {
        attrs.retain(|a| (&*a.name.ns) != TOKEN_NS);
        self.dom.add_attrs_if_missing(target, attrs);
    }
    fn remove_from_parent(&self, target: &Handle) {
        self.dom.remove_from_parent(target);
    }
    fn reparent_children(&self, node: &Handle, new_parent: &Handle) {
        self.dom.reparent_children(node, new_parent);
    }
    fn is_mathml_annotation_xml_integration_point(&self, target: &Handle) -> bool {
        self.dom.is_mathml_annotation_xml_integration_point(target)
    }
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LazyElement {
    pub start: usize,
    pub end: usize,
    pub view_path: String,
    pub trigger: String,
}
struct RawTag {
    start: usize,
    end: usize,
    name: Range<usize>,
    attrs: HashMap<String, Range<usize>>,
    valid: bool,
}
fn whitespace(b: u8) -> bool {
    matches!(b, b' ' | b'\t' | b'\n' | b'\r' | 12)
}

// Lexical byte ranges only. HTML authority/survival comes from Tokenizer +
// TreeBuilder, not this range finder. First duplicate wins, as in HTML5.
fn raw_tag(html: &str, start: usize) -> Option<RawTag> {
    let b = html.as_bytes();
    let mut i = start + 1;
    if !b.get(i)?.is_ascii_alphabetic() {
        return None;
    }
    let name_start = start;
    while i < b.len() && !whitespace(b[i]) && !matches!(b[i], b'/' | b'>') {
        i += 1;
    }
    let name = name_start..i;
    let mut attrs = HashMap::new();
    let mut valid = true;
    loop {
        while i < b.len() && (whitespace(b[i]) || b[i] == b'/') {
            i += 1;
        }
        if *b.get(i)? == b'>' {
            return Some(RawTag {
                start,
                end: i + 1,
                name,
                attrs,
                valid,
            });
        }
        let attr_start = i;
        while i < b.len() && !whitespace(b[i]) && !matches!(b[i], b'=' | b'/' | b'>') {
            // Refuse malformed lexical attributes rather than disagree with
            // HTML5's error recovery about the range supplying authority.
            if matches!(b[i], b'<' | b'"' | b'\'' | 0) {
                valid = false;
            }
            i += 1;
        }
        if i == attr_start {
            // HTML5 permits '=' as the first character of a malformed
            // attribute name. Keep locating the outer token, but deny it.
            valid = false;
            i += 1;
            continue;
        }
        let attr = html[attr_start..i].to_ascii_lowercase();
        while i < b.len() && whitespace(b[i]) {
            i += 1;
        }
        if *b.get(i)? == b'=' {
            i += 1;
            while i < b.len() && whitespace(b[i]) {
                i += 1;
            }
            let quote = *b.get(i)?;
            if matches!(quote, b'"' | b'\'') {
                i += 1;
                while i < b.len() && b[i] != quote {
                    i += 1;
                }
                if i == b.len() {
                    return None;
                }
                i += 1;
            } else {
                while i < b.len() && !whitespace(b[i]) && b[i] != b'>' {
                    i += 1;
                }
            }
        }
        attrs.entry(attr).or_insert(attr_start..i);
    }
}
fn authority_value<'a>(html: &'a str, raw: &RawTag, name: &str) -> Option<&'a str> {
    let range = raw.attrs.get(name)?;
    let attr = &html[range.clone()];
    let end = attr
        .find(|c: char| c.is_ascii_whitespace() || c == '=')
        .unwrap_or(attr.len());
    if !attr[..end].eq_ignore_ascii_case(name) {
        return None;
    }
    let rest = attr[end..].trim_start_matches(|c: char| c.is_ascii_whitespace());
    let Some(value) = rest.strip_prefix('=') else {
        return Some("");
    };
    let value = value.trim_start_matches(|c: char| c.is_ascii_whitespace());
    if value.starts_with(['\'', '"']) {
        Some(&value[1..value.len() - 1])
    } else {
        Some(value)
    }
}
fn authority_matches(
    html: &str,
    raw: &RawTag,
    tag: &html5ever::tokenizer::Tag,
    name: &str,
) -> bool {
    let authored = authority_value(html, raw, name);
    tag.attrs
        .iter()
        .find(|a| a.name.ns.is_empty() && (&*a.name.local) == name)
        .is_some_and(|a| authored == Some(a.value.as_ref()))
}

/// Keep only candidate start-tag bytes for subsequent normalization. This is
/// a performance projection, never a grant: all returned bytes were authored.
pub fn authority_spans(html: &str, spans: &[(usize, usize)]) -> Vec<(usize, usize)> {
    let mut ranges = Vec::new();
    let mut previous = 0;
    for (start, _) in html.match_indices('<') {
        if start < previous {
            continue;
        }
        if let Some(raw) = raw_tag(html, start) {
            previous = raw.end;
            if raw.attrs.contains_key("dj-view") && raw.attrs.contains_key("dj-lazy") {
                ranges.push((start, raw.end));
            }
        }
    }
    let mut result = Vec::new();
    let mut index = 0;
    for (start, end) in ranges {
        while index < spans.len() && spans[index].1 <= start {
            index += 1;
        }
        for &(a, b) in &spans[index..] {
            if a >= end {
                break;
            }
            if a.max(start) < b.min(end) {
                result.push((a.max(start), b.min(end)));
            }
        }
    }
    result
}

fn covered(range: &Range<usize>, spans: &[(usize, usize)]) -> bool {
    let mut cursor = range.start;
    let first = spans.partition_point(|&(_, end)| end <= cursor);
    for &(start, end) in &spans[first..] {
        if end <= cursor {
            continue;
        }
        if start > cursor {
            return false;
        }
        cursor = end;
        if cursor >= range.end {
            return true;
        }
    }
    false
}
struct LocatedTree<'a> {
    tree: TreeBuilder<Handle, AuthoredDom>,
    cursor: Cell<usize>,
    tag_start: Cell<usize>,
    candidates: HashMap<usize, RawTag>,
    spans: &'a [(usize, usize)],
    html: &'a str,
}
impl TokenSink for LocatedTree<'_> {
    type Handle = Handle;
    fn process_token(&self, mut token: Token, line: u64) -> TokenSinkResult<Handle> {
        if let TagToken(ref mut tag) = token {
            if tag.kind == StartTag {
                if let Some(raw) = self.candidates.get(&self.cursor.get()) {
                    let view = raw.attrs.get("dj-view");
                    let lazy = raw.attrs.get("dj-lazy");
                    if view.is_some()
                        && lazy.is_some()
                        && raw.start == self.tag_start.get()
                        && raw.valid
                        && self.html[raw.name.start + 1..raw.name.end]
                            .eq_ignore_ascii_case(tag.name.as_ref())
                        && covered(&raw.name, self.spans)
                        && covered(&(raw.end - 1..raw.end), self.spans)
                        && view.is_some_and(|r| covered(r, self.spans))
                        && lazy.is_some_and(|r| covered(r, self.spans))
                        && authority_matches(self.html, raw, tag, "dj-view")
                        && authority_matches(self.html, raw, tag, "dj-lazy")
                    {
                        tag.attrs.push(Attribute {
                            name: QualName::new(
                                None,
                                Namespace::from(TOKEN_NS),
                                LocalName::from("source"),
                            ),
                            value: StrTendril::from(format!("{}:{}", raw.start, raw.end)),
                        });
                    }
                }
            }
        }
        self.tree.process_token(token, line)
    }
    fn end(&self) {
        self.tree.end();
    }
    fn adjusted_current_node_present_but_not_in_html_namespace(&self) -> bool {
        self.tree
            .adjusted_current_node_present_but_not_in_html_namespace()
    }
}

/// Return only real final-tree elements whose selected authority attributes
/// and tag name are fully covered by authored UTF-8 ranges. The tree is parsed
/// from the entire page, so comments, raw text, RCDATA, ignored tags and inert
/// template contents cannot register. No order/count match to authored source.
pub fn lazy_elements(html: &str, spans: &[(usize, usize)]) -> Vec<LazyElement> {
    if spans.is_empty()
        || !html
            .as_bytes()
            .windows(7)
            .any(|s| s.eq_ignore_ascii_case(b"dj-lazy"))
    {
        return Vec::new();
    }
    // Spans are expected sorted and disjoint. Fail closed on an invalid map.
    let mut previous = 0;
    for &(start, end) in spans {
        if start < previous
            || start >= end
            || end > html.len()
            || !html.is_char_boundary(start)
            || !html.is_char_boundary(end)
        {
            return Vec::new();
        }
        previous = end;
    }
    let mut candidates = HashMap::new();
    for (start, _) in html.match_indices('<') {
        if let Some(raw) = raw_tag(html, start) {
            // The outer lexical token wins, including tokens with no lazy
            // attributes. A quoted '<div ...>' cannot stand in for its tag.
            candidates.entry(raw.end).or_insert(raw);
        }
    }
    let sink = LocatedTree {
        tree: TreeBuilder::new(AuthoredDom::default(), Default::default()),
        cursor: Cell::new(0),
        tag_start: Cell::new(usize::MAX),
        candidates,
        spans,
        html,
    };
    let tokenizer = Tokenizer::new(sink, Default::default());
    let input = BufferQueue::default();
    // Feed at lexical delimiter boundaries, retaining exact offsets at every
    // possible tag emission. Runs without `<` or `>` cannot finish a tag, so
    // allocating a tendril for every Unicode scalar is unnecessary.
    let mut previous = 0;
    for (i, delimiter) in html.match_indices(['<', '>']) {
        if previous < i {
            tokenizer.sink.cursor.set(i);
            input.push_back(StrTendril::from(&html[previous..i]));
            let _ = tokenizer.feed(&input);
        }
        if delimiter == "<" {
            tokenizer.sink.tag_start.set(i);
        }
        tokenizer.sink.cursor.set(i + 1);
        input.push_back(StrTendril::from(delimiter));
        let _ = tokenizer.feed(&input);
        previous = i + 1;
    }
    if previous < html.len() {
        tokenizer.sink.cursor.set(html.len());
        input.push_back(StrTendril::from(&html[previous..]));
        let _ = tokenizer.feed(&input);
    }
    tokenizer.end();
    let mut result = Vec::new();
    let mut stack = vec![tokenizer.sink.tree.sink.dom.document.clone()];
    while let Some(node) = stack.pop() {
        if let NodeData::Element { attrs, .. } = &node.data {
            let attrs = attrs.borrow();
            let source = attrs.iter().find(|a| (&*a.name.ns) == TOKEN_NS);
            let view = attrs
                .iter()
                .find(|a| a.name.ns.is_empty() && (&*a.name.local) == "dj-view");
            let lazy = attrs
                .iter()
                .find(|a| a.name.ns.is_empty() && (&*a.name.local) == "dj-lazy");
            if let (Some(source), Some(view), Some(_lazy)) = (source, view, lazy) {
                if let Some((start, end)) = source.value.split_once(':') {
                    if let (Ok(start), Ok(end)) = (start.parse(), end.parse()) {
                        if !view.value.is_empty() {
                            result.push(LazyElement {
                                start,
                                end,
                                view_path: tokenizer
                                    .sink
                                    .candidates
                                    .get(&end)
                                    .and_then(|raw| authority_value(html, raw, "dj-view"))
                                    .unwrap_or("")
                                    .to_owned(),
                                trigger: tokenizer
                                    .sink
                                    .candidates
                                    .get(&end)
                                    .and_then(|raw| authority_value(html, raw, "dj-lazy"))
                                    .unwrap_or("")
                                    .to_owned(),
                            });
                        }
                    }
                }
            }
        }
        stack.extend(node.children.borrow().iter().cloned());
    }
    result.sort_by_key(|r| r.start);
    result.dedup_by_key(|r| r.start);
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    const TAG: &str = "<div dj-view=\"app.Child\" dj-lazy=\"visible\"></div>";
    #[test]
    fn final_tree_context() {
        for (before, after) in [
            ("<!--", "-->"),
            ("<script>let x='", "';</script>"),
            ("<style>", "</style>"),
            ("<textarea>", "</textarea>"),
            ("<title>", "</title>"),
            ("<template>", "</template>"),
        ] {
            let html = format!("{before}{TAG}{after}");
            assert!(
                lazy_elements(&html, &[(0, html.len())]).is_empty(),
                "{html}"
            );
        }
        let html = format!("é<!--{TAG}--><script>'{TAG}'</script>{TAG}");
        let found = lazy_elements(&html, &[(0, html.len())]);
        assert_eq!(found.len(), 1);
        assert_eq!(found[0].trigger, "visible");
        assert_eq!(
            &html[found[0].start..found[0].end],
            "<div dj-view=\"app.Child\" dj-lazy=\"visible\">"
        );
    }
    #[test]
    fn sideband_not_output_count_grants_authority() {
        let html = format!("{TAG}{TAG}{TAG}");
        let n = TAG.len();
        assert_eq!(lazy_elements(&html, &[(n, n * 2)]).len(), 1);
        assert_eq!(lazy_elements(&html, &[(0, n), (n * 2, n * 3)]).len(), 2);
        assert!(lazy_elements(&html, &[]).is_empty());
    }
    #[test]
    fn selected_duplicate_attribute_must_be_authored() {
        let html = "<div dj-view=\"app.Forged\" dj-view=\"app.Child\" dj-lazy></div>";
        let gap = html.find("app.Forged").unwrap();
        assert!(lazy_elements(html, &[(0, gap), (gap + 10, html.len())]).is_empty());
        let found = lazy_elements(html, &[(0, html.len())]);
        assert_eq!(found[0].view_path, "app.Forged");
    }
    #[test]
    fn quoted_tag_and_merged_body_cannot_borrow_authority() {
        let html = format!("<div title='{TAG}'></div>");
        assert!(lazy_elements(&html, &[(0, html.len())]).is_empty());
        let forged = "<body dj-view=\"app.Child\" dj-lazy>";
        let html = format!("{forged}<body dj-view=\"app.Child\" dj-lazy></body>");
        assert!(lazy_elements(&html, &[(forged.len(), html.len())]).is_empty());
    }
}
