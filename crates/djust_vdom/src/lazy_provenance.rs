//! Validate authored byte runs against the final HTML5 tree (#3252).
//! The parser-private token annotation below never enters emitted HTML. It
//! correlates an actual start-tag token with the element the tree builder
//! creates; copied output text cannot supply a namespaced token annotation.
use html5ever::buffer_queue::BufferQueue;
use html5ever::tendril::StrTendril;
use html5ever::tokenizer::{StartTag, TagToken, Token, TokenSink, TokenSinkResult, Tokenizer};
use html5ever::tree_builder::{ElementFlags, NodeOrText, QuirksMode, TreeBuilder, TreeSink};
use html5ever::{Attribute, LocalName, Namespace, QualName};
use html5ever::{ExpandedName, TokenizerResult};
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
#[derive(Clone)]
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

/// Locate a directly returned library child wrapper without conferring authority.
/// Attribute values are checked against HTML5 decoding, including escaped slots.
pub fn child_wrapper_opening_end(html: &str, slot: &str) -> Option<usize> {
    let raw = raw_tag(html, 0)?;
    if !raw.valid || !html[1..raw.name.end].eq_ignore_ascii_case("div") {
        return None;
    }
    // This is attribute decoding, not source authorization. Parse the one
    // lexical wrapper probe with the ordinary HTML5 parser; source authority
    // is checked separately when this exact issued opening enters the page.
    use html5ever::tendril::TendrilSink;
    let probe = format!("{}</div>", &html[..raw.end]);
    let document = html5ever::parse_document(RcDom::default(), Default::default()).one(probe);
    // Keep the owning document alive: RcDom's iterative Node::drop drains
    // descendant child lists even while traversal handles still exist.
    let mut pending = vec![document.document.clone()];
    while let Some(node) = pending.pop() {
        if let NodeData::Element { name, attrs, .. } = &node.data {
            if name.ns == html5ever::ns!(html) && &*name.local == "div" {
                let attrs = attrs.borrow();
                let matches = |name: &str| {
                    attrs.iter().any(|a| {
                        a.name.ns.is_empty() && &*a.name.local == name && a.value.as_ref() == slot
                    })
                };
                return ((has_attr(&attrs, "dj-view") && matches("data-djust-embedded"))
                    || (!has_attr(&attrs, "dj-view") && matches("dj-sticky-slot")))
                .then_some(raw.end);
            }
        }
        pending.extend(node.children.borrow().iter().cloned());
    }
    None
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
    root_mode: bool,
    issued_openings: Option<&'a HashSet<(usize, usize)>>,
}
impl TokenSink for LocatedTree<'_> {
    type Handle = Handle;
    fn process_token(&self, mut token: Token, line: u64) -> TokenSinkResult<Handle> {
        if let TagToken(ref mut tag) = token {
            if tag.kind == StartTag {
                let raw = if self.root_mode {
                    let start = self.tag_start.get();
                    if start < self.html.len() {
                        raw_tag(self.html, start)
                    } else {
                        None
                    }
                } else {
                    self.candidates.get(&self.cursor.get()).cloned()
                };
                if let Some(raw) = raw {
                    let correlated = raw.start == self.tag_start.get()
                        && raw.end == self.cursor.get()
                        && raw.valid
                        && self.html[raw.name.start + 1..raw.name.end]
                            .eq_ignore_ascii_case(tag.name.as_ref());
                    let common = covered(&raw.name, self.spans)
                        && covered(&(raw.end - 1..raw.end), self.spans);
                    let authorized = |name: &str| {
                        raw.attrs.get(name).is_some_and(|r| covered(r, self.spans))
                            && authority_matches(self.html, &raw, tag, name)
                    };
                    let authority = if self.root_mode {
                        ["dj-root", "dj-view", "dj-model", "dj-update"]
                            .iter()
                            .any(|name| authorized(name))
                    } else {
                        authorized("dj-view") && authorized("dj-lazy")
                    };
                    let issued = self
                        .issued_openings
                        .is_some_and(|ranges| ranges.contains(&(raw.start, raw.end)));
                    if correlated && ((common && authority) || issued) {
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
        // In root mode an outer start-tag remains pending while quoted
        // attributes contain '<'. Clear its start only once html5ever emits
        // a complete token, never on parse errors inside that tag.
        if self.root_mode
            && matches!(
                token,
                Token::TagToken(_)
                    | Token::CommentToken(_)
                    | Token::DoctypeToken(_)
                    | Token::CharacterTokens(_)
                    | Token::NullCharacterToken
                    | Token::EOFToken
            )
        {
            self.tag_start.set(usize::MAX);
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
    lazy_elements_impl::<true>(html, spans)
}

fn lazy_elements_impl<const BATCHED: bool>(
    html: &str,
    spans: &[(usize, usize)],
) -> Vec<LazyElement> {
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
        root_mode: false,
        issued_openings: None,
    };
    let tokenizer = Tokenizer::new(sink, Default::default());
    let input = BufferQueue::default();
    // Like html5ever's Parser::loop_until_done: script ends and encoding
    // indicators can suspend feed with bytes queued. Drain before changing
    // correlation offsets; the input is already decoded UTF-8.
    let feed_until_done = || {
        while !matches!(tokenizer.feed(&input), TokenizerResult::Done) {}
    };
    // Only a lexical lazy candidate can receive an annotation. Feed all other
    // HTML in bulk with correlation disabled; it still traverses the SAME
    // tokenizer and tree builder, including raw text, comments and templates.
    // Within candidate windows retain every delimiter boundary, including '<'
    // inside quoted attributes, so an inner spelling cannot borrow its token.
    let mut windows: Vec<_> = if BATCHED {
        tokenizer
            .sink
            .candidates
            .values()
            .filter(|raw| raw.attrs.contains_key("dj-view") && raw.attrs.contains_key("dj-lazy"))
            .map(|raw| (raw.start, raw.end))
            .collect()
    } else {
        vec![(0, html.len())]
    };
    windows.sort_unstable();
    let mut merged: Vec<(usize, usize)> = Vec::new();
    for (start, end) in windows {
        if let Some(last) = merged.last_mut().filter(|last| start <= last.1) {
            last.1 = last.1.max(end);
        } else {
            merged.push((start, end));
        }
    }
    let mut previous = 0;
    for (start, end) in merged {
        if previous < start {
            tokenizer.sink.cursor.set(usize::MAX);
            tokenizer.sink.tag_start.set(usize::MAX);
            input.push_back(StrTendril::from(&html[previous..start]));
            feed_until_done();
        }
        previous = start;
        for (relative, delimiter) in html[start..end].match_indices(['<', '>']) {
            let i = start + relative;
            if previous < i {
                tokenizer.sink.cursor.set(i);
                input.push_back(StrTendril::from(&html[previous..i]));
                feed_until_done();
            }
            if delimiter == "<" {
                tokenizer.sink.tag_start.set(i);
            }
            tokenizer.sink.cursor.set(i + 1);
            input.push_back(StrTendril::from(delimiter));
            feed_until_done();
            previous = i + 1;
        }
        if previous < end {
            tokenizer.sink.cursor.set(end);
            input.push_back(StrTendril::from(&html[previous..end]));
            feed_until_done();
            previous = end;
        }
    }
    if previous < html.len() {
        tokenizer.sink.cursor.set(usize::MAX);
        tokenizer.sink.tag_start.set(usize::MAX);
        input.push_back(StrTendril::from(&html[previous..]));
        feed_until_done();
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

/// Project only source ranges that intersect a potential root or binding token.
/// The result is not authority: `root_live_openings` validates the HTML5 tree.
pub fn root_authority_spans(html: &str, spans: &[(usize, usize)]) -> Vec<(usize, usize)> {
    let mut result = Vec::new();
    let mut previous = 0;
    for (start, _) in html.match_indices('<') {
        if start < previous {
            continue;
        }
        if let Some(raw) = raw_tag(html, start) {
            previous = raw.end;
            if ["dj-root", "dj-view", "dj-model", "dj-update"]
                .iter()
                .any(|name| raw.attrs.contains_key(*name))
            {
                for &(a, b) in spans {
                    if a.max(start) < b.min(raw.end) {
                        result.push((a.max(start), b.min(raw.end)));
                    }
                }
            }
        }
    }
    result
}

/// Parse a complete rendered document, annotating only tokenized candidate
/// openings whose relevant attributes and tag names are fully authored.
fn root_dom(html: &str, spans: &[(usize, usize)]) -> Option<Handle> {
    root_dom_impl::<true>(html, spans)
}

fn root_dom_impl<const BATCHED: bool>(html: &str, spans: &[(usize, usize)]) -> Option<Handle> {
    root_dom_with_issued::<BATCHED>(html, spans, None)
}

fn root_dom_with_issued<'a, const BATCHED: bool>(
    html: &'a str,
    spans: &'a [(usize, usize)],
    issued_openings: Option<&'a HashSet<(usize, usize)>>,
) -> Option<Handle> {
    let mut previous = 0;
    for &(start, end) in spans {
        if start < previous
            || start >= end
            || end > html.len()
            || !html.is_char_boundary(start)
            || !html.is_char_boundary(end)
        {
            return None;
        }
        previous = end;
    }
    let sink = LocatedTree {
        tree: TreeBuilder::new(AuthoredDom::default(), Default::default()),
        cursor: Cell::new(0),
        tag_start: Cell::new(usize::MAX),
        candidates: HashMap::new(),
        spans,
        html,
        root_mode: true,
        issued_openings,
    };
    let tokenizer = Tokenizer::new(sink, Default::default());
    let input = BufferQueue::default();
    let feed = || while !matches!(tokenizer.feed(&input), TokenizerResult::Done) {};
    // A later token can receive a provenance annotation only if its source
    // contains one of these ASCII attribute names. Track all bytes through the
    // last occurrence, then finish a pending token before bulk-feeding the
    // suffix. The untracked bytes still pass through the same HTML5 parser.
    // Reverse scanning each fixed-length spelling is linear even for malformed
    // tags with arbitrarily many '<' characters or unterminated quotes.
    let last_authority = if BATCHED {
        let bytes = html.as_bytes();
        [b"dj-root".as_slice(), b"dj-view", b"dj-model", b"dj-update"]
            .into_iter()
            .filter_map(|needle| {
                bytes
                    .windows(needle.len())
                    .rposition(|window| window.eq_ignore_ascii_case(needle))
                    .map(|index| index + needle.len())
            })
            .max()
    } else {
        Some(usize::MAX)
    };
    if last_authority.is_none() {
        tokenizer.sink.cursor.set(usize::MAX);
        tokenizer.sink.tag_start.set(usize::MAX);
        input.push_back(StrTendril::from(html));
        feed();
        tokenizer.end();
        return Some(tokenizer.sink.tree.sink.dom.document.clone());
    }
    let mut previous = 0;
    // Only tokenize each real start tag when html5ever emits it. Eagerly
    // reparsing every '<' in an unterminated quoted attribute is quadratic.
    for (i, delimiter) in html.match_indices(['<', '>']) {
        if previous < i {
            tokenizer.sink.cursor.set(i);
            input.push_back(StrTendril::from(&html[previous..i]));
            feed();
        }
        if delimiter == "<" && tokenizer.sink.tag_start.get() == usize::MAX {
            tokenizer.sink.tag_start.set(i);
        }
        tokenizer.sink.cursor.set(i + 1);
        input.push_back(StrTendril::from(delimiter));
        feed();
        // A literal preceding '<' can be emitted while this '<' is consumed
        // (e.g. '<<main'); its token ends the prior pending start, not this one.
        if delimiter == "<" && tokenizer.sink.tag_start.get() == usize::MAX {
            tokenizer.sink.tag_start.set(i);
        }
        previous = i + 1;
        if BATCHED
            && previous >= last_authority.unwrap_or(usize::MAX)
            && tokenizer.sink.tag_start.get() == usize::MAX
        {
            break;
        }
    }
    if previous < html.len() {
        tokenizer.sink.cursor.set(usize::MAX);
        tokenizer.sink.tag_start.set(usize::MAX);
        input.push_back(StrTendril::from(&html[previous..]));
        feed();
    }
    tokenizer.end();
    Some(tokenizer.sink.tree.sink.dom.document.clone())
}

fn source_opening(node: &Handle) -> Option<(usize, usize)> {
    let NodeData::Element { attrs, .. } = &node.data else {
        return None;
    };
    let attrs = attrs.borrow();
    let attr = attrs.iter().find(|a| (&*a.name.ns) == TOKEN_NS)?;
    let (start, end) = attr.value.split_once(':')?;
    Some((start.parse().ok()?, end.parse().ok()?))
}

/// Candidate source openings that survived the HTML5 tree builder. The caller
/// uses this on each of the blank and opaque projections before granting the
/// corresponding output bytes authority.
pub fn root_live_openings(html: &str, spans: &[(usize, usize)]) -> Vec<usize> {
    let Some(document) = root_dom(html, spans) else {
        return Vec::new();
    };
    live_openings_from_dom(&document)
}

fn live_openings_from_dom(document: &Handle) -> Vec<usize> {
    let mut result = Vec::new();
    let mut stack = vec![document.clone()];
    while let Some(node) = stack.pop() {
        if let Some((start, _)) = source_opening(&node) {
            result.push(start);
        }
        // Template contents are deliberately not reached: they are inert.
        stack.extend(node.children.borrow().iter().cloned());
    }
    result.sort_unstable();
    result.dedup();
    result
}

/// One independently authenticated authority-bearing attribute on a surviving
/// HTML5 opening. Its range covers only the attribute, not the entire tag.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LiveAuthorityAttribute {
    pub opening: usize,
    pub name: &'static str,
    pub value: String,
    pub range: (usize, usize),
    /// Authored page-root opening which owns this attribute in this tree.
    /// None means outside any eligible page root; a merged/untracked root
    /// uses `usize::MAX` and must fail closed.
    pub owner_root: Option<usize>,
    /// Nearest excluded embedded/lazy/sticky/target view boundary, if any.
    /// A merged/untracked boundary uses `usize::MAX` and must fail closed.
    pub owner_child: Option<usize>,
}

/// Return individually authenticated root/view/model attributes in the final
/// HTML5 tree. Call for actual output and both source-liveness projections;
/// callers must require the SAME name and value at the corresponding opening
/// in each projection. A live `dj-root` never authenticates another attribute.
pub fn root_live_attributes(html: &str, spans: &[(usize, usize)]) -> Vec<LiveAuthorityAttribute> {
    let Some(document) = root_dom(html, spans) else {
        return Vec::new();
    };
    live_attributes_from_dom(html, spans, &document)
}

/// Compute both liveness projections from the same HTML5 tree. The opening
/// and attribute results retain their independent authorization rules.
pub fn root_live_authority(
    html: &str,
    spans: &[(usize, usize)],
) -> (Vec<usize>, Vec<LiveAuthorityAttribute>) {
    let Some(document) = root_dom(html, spans) else {
        return (Vec::new(), Vec::new());
    };
    (
        live_openings_from_dom(&document),
        live_attributes_from_dom(html, spans, &document),
    )
}

fn live_attributes_from_dom(
    html: &str,
    spans: &[(usize, usize)],
    document: &Handle,
) -> Vec<LiveAuthorityAttribute> {
    let mut result = Vec::new();
    let mut stack = vec![(document.clone(), None, None, false)];
    while let Some((node, inherited_owner, inherited_child, inherited_embedded)) = stack.pop() {
        let mut owner_root = inherited_owner;
        let mut owner_child = inherited_child;
        let mut embedded_branch = inherited_embedded;
        if let NodeData::Element { name, attrs, .. } = &node.data {
            let attrs = attrs.borrow();
            let embedded = has_attr(&attrs, "dj-view")
                && (has_attr(&attrs, "data-djust-embedded") || has_attr(&attrs, "dj-lazy"));
            embedded_branch |= embedded;
            let excluded = embedded
                || has_attr(&attrs, "dj-sticky-root")
                || has_attr(&attrs, "data-djust-target");
            if has_attr(&attrs, "dj-view") && excluded {
                owner_child = Some(source_opening(&node).map_or(usize::MAX, |(start, _)| start));
            }
            let eligible = (has_attr(&attrs, "dj-view") && !excluded)
                || (has_attr(&attrs, "dj-root") && !excluded && !embedded_branch);
            if !matches!(&*name.local, "html" | "head") && eligible {
                // Unauthored candidates cannot confer an ancestor identity.
                owner_root = Some(source_opening(&node).map_or(usize::MAX, |(start, _)| start));
            }
        }
        if let Some((start, end)) = source_opening(&node) {
            if let Some(raw) = raw_tag(html, start).filter(|raw| raw.end == end) {
                for name in ["dj-root", "dj-view", "dj-model", "dj-update"] {
                    if dom_attr_authorized(html, spans, &node, name) {
                        if let Some(range) = raw.attrs.get(name) {
                            if let Some(value) = authority_value(html, &raw, name) {
                                result.push(LiveAuthorityAttribute {
                                    opening: start,
                                    name,
                                    value: value.to_owned(),
                                    range: (range.start, range.end),
                                    owner_root,
                                    owner_child,
                                });
                            }
                        }
                    }
                }
            }
        }
        // The template contents live on a separate inert document fragment.
        stack.extend(
            node.children
                .borrow()
                .iter()
                .cloned()
                .map(|child| (child, owner_root, owner_child, embedded_branch)),
        );
    }
    result.sort_unstable_by(|a, b| a.opening.cmp(&b.opening).then(a.range.cmp(&b.range)));
    result
}

#[derive(Debug, thiserror::Error)]
pub enum AuthenticatedRootError {
    #[error("invalid template provenance")]
    InvalidProvenance,
    #[error("expected exactly one LiveView page root")]
    MissingOrAmbiguous,
    #[error("the LiveView page root is not template-authored")]
    Unauthored,
    #[error("failed to serialize authenticated HTML5 tree")]
    Serialize,
    #[error("the root does not have an exact source range in the HTML5 tree")]
    UnsafeSourceRange,
    #[error("failed to create authenticated VDOM: {0}")]
    Vdom(String),
}

/// A validated root and a shell with a single server-side replacement marker.
/// The caller must replace `placeholder` before the shell is sent to a client.
#[derive(Debug)]
pub struct AuthenticatedRoot {
    pub opening: (usize, usize),
    pub root_html: String,
    pub document_with_placeholder: String,
    pub placeholder: String,
    pub vdom: crate::VNode,
    pub namespace: crate::Namespace,
    /// Render-issued child openings, associated with this exact VDOM's IDs.
    /// The caller must validate the issuance ledger before committing them.
    pub child_vdom_ids: Vec<(String, String)>,
    /// Independently authored, live ignore boundaries in this view.
    pub ignore_vdom_ids: HashSet<String>,
    pub model_fields: Vec<String>,
    fragment_context: QualName,
    fragment_attrs: Vec<Attribute>,
    children_html: String,
    trailing_whitespace_ignorable: bool,
}

fn has_attr(attrs: &[Attribute], name: &str) -> bool {
    attrs
        .iter()
        .any(|a| a.name.ns.is_empty() && &*a.name.local == name)
}

fn dom_attr_authorized(html: &str, spans: &[(usize, usize)], node: &Handle, name: &str) -> bool {
    let Some((start, end)) = source_opening(node) else {
        return false;
    };
    let Some(raw) = raw_tag(html, start) else {
        return false;
    };
    if raw.end != end
        || !raw.valid
        || !covered(&raw.name, spans)
        || !covered(&(raw.end - 1..raw.end), spans)
        || !raw
            .attrs
            .get(name)
            .is_some_and(|range| covered(range, spans))
    {
        return false;
    }
    let NodeData::Element { attrs, .. } = &node.data else {
        return false;
    };
    let attrs = attrs.borrow();
    attrs
        .iter()
        .find(|a| a.name.ns.is_empty() && &*a.name.local == name)
        .is_some_and(|a| authority_value(html, &raw, name) == Some(a.value.as_ref()))
}

fn serialize_node(
    node: Handle,
    scope: html5ever::serialize::TraversalScope,
) -> Result<String, AuthenticatedRootError> {
    use html5ever::serialize::{serialize, SerializeOpts};
    use markup5ever_rcdom::SerializableHandle;
    let mut bytes = Vec::new();
    serialize(
        &mut bytes,
        &SerializableHandle::from(node),
        SerializeOpts {
            traversal_scope: scope,
            ..Default::default()
        },
    )
    .map_err(|_| AuthenticatedRootError::Serialize)?;
    String::from_utf8(bytes).map_err(|_| AuthenticatedRootError::Serialize)
}

fn page_candidates(document: &Handle) -> Vec<(Handle, bool)> {
    let mut candidates = Vec::new();
    let mut stack = vec![(document.clone(), false, false)];
    while let Some((node, in_body, inside_embedded)) = stack.pop() {
        let mut body = in_body;
        let mut embedded_branch = inside_embedded;
        if let NodeData::Element { name, attrs, .. } = &node.data {
            let attrs = attrs.borrow();
            let tag = &*name.local;
            body |= name.ns == html5ever::ns!(html) && tag == "body";
            let embedded = has_attr(&attrs, "dj-view")
                && (has_attr(&attrs, "data-djust-embedded") || has_attr(&attrs, "dj-lazy"));
            embedded_branch |= embedded;
            let excluded = embedded
                || has_attr(&attrs, "dj-sticky-root")
                || has_attr(&attrs, "data-djust-target");
            // The client selects dj-view without excluding ancestors, so a
            // forged dj-view *inside* a lazy container still competes. A bare
            // dj-root in such a container is not stamped as a page view.
            let page_view = has_attr(&attrs, "dj-view") && !excluded;
            let stamped_root = has_attr(&attrs, "dj-root") && !excluded && !embedded_branch;
            if tag != "html" && tag != "head" && (page_view || stamped_root) {
                candidates.push((node.clone(), body));
            }
        }
        stack.extend(
            node.children
                .borrow()
                .iter()
                .rev()
                .cloned()
                .map(|n| (n, body, embedded_branch)),
        );
    }
    candidates
}

fn scoped_model_fields(html: &str, spans: &[(usize, usize)], scope: &Handle) -> Vec<String> {
    let mut fields = std::collections::BTreeSet::new();
    let mut nodes = vec![scope.clone()];
    while let Some(node) = nodes.pop() {
        let mut skip = false;
        if let NodeData::Element { attrs, .. } = &node.data {
            let attrs = attrs.borrow();
            if !std::rc::Rc::ptr_eq(&node, scope)
                && has_attr(&attrs, "dj-view")
                && (has_attr(&attrs, "data-djust-embedded")
                    || has_attr(&attrs, "dj-lazy")
                    || has_attr(&attrs, "data-djust-target")
                    || has_attr(&attrs, "dj-sticky-root"))
            {
                skip = true;
            }
            if !skip {
                let model = attrs
                    .iter()
                    .find(|a| a.name.ns.is_empty() && &*a.name.local == "dj-model")
                    .map(|a| a.value.to_string());
                drop(attrs);
                if let Some(field) = model.filter(|s| !s.is_empty()) {
                    if dom_attr_authorized(html, spans, &node, "dj-model") {
                        fields.insert(field);
                    }
                }
            }
        }
        if !skip {
            nodes.extend(node.children.borrow().iter().cloned());
        }
    }
    fields.into_iter().collect()
}

/// Return authored bindings from a rootless fragment, rejecting any page-view
/// candidate (including untrusted ones) rather than treating it as a fragment.
/// The supplied spans must already be filtered by B/P attribute liveness.
pub fn authenticated_fragment_model_fields(
    html: &str,
    spans: &[(usize, usize)],
) -> Result<Vec<String>, AuthenticatedRootError> {
    let document = root_dom(html, spans).ok_or(AuthenticatedRootError::InvalidProvenance)?;
    if !page_candidates(&document).is_empty() {
        return Err(AuthenticatedRootError::MissingOrAmbiguous);
    }
    Ok(scoped_model_fields(html, spans, &document))
}

/// Locate one real page root and its model bindings in the final HTML5 tree.
/// `spans` MUST already be filtered for blank/opaque source liveness; byte
/// coverage alone cannot establish authority in the presence of `|safe` output.
/// Caller controls the VDOM id counter before calling (reset for the first
/// render, advance for later diffs), as for `parse_html_continue`.
pub fn authenticated_live_root(
    html: &str,
    spans: &[(usize, usize)],
) -> Result<AuthenticatedRoot, AuthenticatedRootError> {
    authenticated_live_root_with_children(html, spans, &[])
}

/// Associate exact render-issued child wrapper openings with generated IDs.
/// Opening intervals grant location only; root and model authority still require
/// independently filtered template-source spans. Only direct child wrappers of
/// the authenticated root are accepted. Markup and slot strings cannot issue one.
pub fn authenticated_live_root_with_children(
    html: &str,
    spans: &[(usize, usize)],
    children: &[(usize, usize, String)],
) -> Result<AuthenticatedRoot, AuthenticatedRootError> {
    use html5ever::serialize::TraversalScope;
    use markup5ever_rcdom::Node;
    use std::rc::Rc;

    let mut ranges = HashSet::new();
    let mut keys = HashSet::new();
    let mut previous = 0;
    for (start, end, key) in children {
        if *start < previous
            || *start >= *end
            || *end > html.len()
            || !html.is_char_boundary(*start)
            || !html.is_char_boundary(*end)
            || key.is_empty()
            || key.len() > 128
            || !keys.insert(key.as_str())
            || !ranges.insert((*start, *end))
        {
            return Err(AuthenticatedRootError::InvalidProvenance);
        }
        previous = *end;
    }
    let document = if children.is_empty() {
        root_dom(html, spans)
    } else {
        root_dom_with_issued::<false>(html, spans, Some(&ranges))
    }
    .ok_or(AuthenticatedRootError::InvalidProvenance)?;
    let mut candidates = page_candidates(&document);
    if candidates.len() != 1 || !candidates[0].1 {
        return Err(AuthenticatedRootError::MissingOrAmbiguous);
    }
    let root = candidates.remove(0).0;
    if !dom_attr_authorized(html, spans, &root, "dj-root")
        && !dom_attr_authorized(html, spans, &root, "dj-view")
    {
        return Err(AuthenticatedRootError::Unauthored);
    }
    // A dynamic `dj-view` on an otherwise authored root changes which view
    // the browser mounts. It must never inherit authority from `dj-root`.
    if let NodeData::Element { attrs, .. } = &root.data {
        if has_attr(&attrs.borrow(), "dj-view")
            && !dom_attr_authorized(html, spans, &root, "dj-view")
        {
            return Err(AuthenticatedRootError::Unauthored);
        }
    }
    let opening = source_opening(&root).ok_or(AuthenticatedRootError::Unauthored)?;
    let fields = scoped_model_fields(html, spans, &root);
    let by_opening: HashMap<_, _> = children.iter().map(|(a, b, key)| ((*a, *b), key)).collect();
    let mut owners = HashMap::new();
    let mut ignore_owners = HashSet::new();
    {
        let mut pending = vec![(root.clone(), true)];
        while let Some((node, is_root)) = pending.pop() {
            if let NodeData::Element { name, attrs, .. } = &node.data {
                let attrs = attrs.borrow();
                if matches!(
                    &*name.local,
                    "template"
                        | "script"
                        | "style"
                        | "textarea"
                        | "title"
                        | "xmp"
                        | "iframe"
                        | "noembed"
                        | "noscript"
                ) {
                    continue;
                }
                if attrs.iter().any(|a| {
                    a.name.ns.is_empty() && &*a.name.local == "dj-update" && &*a.value == "ignore"
                }) && dom_attr_authorized(html, spans, &node, "dj-update")
                {
                    ignore_owners.insert(Rc::as_ptr(&node));
                }
                if let Some(key) = source_opening(&node).and_then(|range| by_opening.get(&range)) {
                    if is_root
                        || name.ns != html5ever::ns!(html)
                        || &*name.local != "div"
                        || !(has_attr(&attrs, "dj-view") && has_attr(&attrs, "data-djust-embedded")
                            || !has_attr(&attrs, "dj-view") && has_attr(&attrs, "dj-sticky-slot"))
                    {
                        return Err(AuthenticatedRootError::InvalidProvenance);
                    }
                    owners.insert(Rc::as_ptr(&node), (*key).clone());
                    continue;
                }
                if !is_root
                    && has_attr(&attrs, "dj-view")
                    && (has_attr(&attrs, "data-djust-embedded") || has_attr(&attrs, "dj-lazy"))
                {
                    continue;
                }
            }
            pending.extend(
                node.children
                    .borrow()
                    .iter()
                    .cloned()
                    .map(|child| (child, false)),
            );
        }
    }
    if owners.len() != children.len() {
        return Err(AuthenticatedRootError::InvalidProvenance);
    }
    // Parser-private annotations must never reach HTML or VDOM clients.
    let mut nodes = vec![document.clone()];
    while let Some(node) = nodes.pop() {
        if let NodeData::Element {
            attrs,
            template_contents,
            ..
        } = &node.data
        {
            attrs.borrow_mut().retain(|a| (&*a.name.ns) != TOKEN_NS);
            if let Some(contents) = template_contents.borrow().as_ref() {
                nodes.push(contents.clone());
            }
        }
        nodes.extend(node.children.borrow().iter().cloned());
    }
    let namespace = match &root.data {
        NodeData::Element { name, .. } if name.ns == html5ever::ns!(svg) => crate::Namespace::Svg,
        NodeData::Element { name, .. } if name.ns == html5ever::ns!(mathml) => {
            crate::Namespace::MathMl
        }
        _ => crate::Namespace::Html,
    };
    let (root_name, fragment_context, fragment_attrs) = match &root.data {
        NodeData::Element { name, attrs, .. } => {
            let context = if namespace == crate::Namespace::Html {
                QualName::new(None, html5ever::ns!(html), LocalName::from("div"))
            } else {
                name.clone()
            };
            let encoding = if namespace == crate::Namespace::Html {
                Vec::new()
            } else {
                attrs
                    .borrow()
                    .iter()
                    .filter(|a| a.name.ns.is_empty() && &*a.name.local == "encoding")
                    .cloned()
                    .collect()
            };
            (name.clone(), context, encoding)
        }
        _ => return Err(AuthenticatedRootError::Unauthored),
    };
    let children_html =
        serialize_node(root.clone(), TraversalScope::ChildrenOnly(Some(root_name)))?;
    // The VDOM drops a separate trailing whitespace-only text node under body.
    // A non-whitespace text node that absorbs trailing bytes is meaningful.
    let trailing_whitespace_ignorable =
        root.children
            .borrow()
            .last()
            .is_none_or(|node| match &node.data {
                NodeData::Text { contents } => contents.borrow().bytes().all(whitespace),
                _ => true,
            });
    let mut child_vdom_ids = Vec::new();
    let mut ignore_vdom_ids = HashSet::new();
    let vdom = if children.is_empty() && ignore_owners.is_empty() {
        crate::parser::handle_to_vnode(&root, 1)
    } else {
        crate::parser::handle_to_vnode_observed(&root, 1, &mut |node, id| {
            if ignore_owners.contains(&Rc::as_ptr(node)) {
                ignore_vdom_ids.insert(id.to_owned());
            }
            if let Some(key) = owners.get(&Rc::as_ptr(node)) {
                child_vdom_ids.push((key.clone(), id.to_owned()));
            }
        })
    }
    .map_err(|e| AuthenticatedRootError::Vdom(e.to_string()))?;
    if child_vdom_ids.len() != children.len() {
        return Err(AuthenticatedRootError::InvalidProvenance);
    }
    let root_html = serialize_node(root.clone(), TraversalScope::IncludeNode)?;

    let parent = root
        .parent
        .take()
        .and_then(|weak| weak.upgrade())
        .ok_or(AuthenticatedRootError::Serialize)?;
    // A text marker forces the HTML parser to create an implicit body for a
    // bare fragment. A body root itself needs a body-shaped marker instead:
    // replacing it with text would cause the parser to synthesize a new body.
    let body_root = matches!(&root.data, NodeData::Element { name, .. }
        if name.ns == html5ever::ns!(html) && &*name.local == "body");
    let mut suffix = 0_u64;
    let (placeholder, marker_content) = loop {
        let content = format!("djust-authenticated-root-{suffix}");
        let marker = if body_root {
            format!("<body>{content}</body>")
        } else {
            content.clone()
        };
        if !html.contains(&content) && !root_html.contains(&content) {
            break (marker, content);
        }
        suffix = suffix
            .checked_add(1)
            .ok_or(AuthenticatedRootError::Serialize)?;
    };
    let text_marker = Node::new(NodeData::Text {
        contents: RefCell::new(StrTendril::from(marker_content)),
    });
    let marker = if body_root {
        let element = Node::new(NodeData::Element {
            name: QualName::new(None, html5ever::ns!(html), LocalName::from("body")),
            attrs: RefCell::new(Vec::new()),
            template_contents: RefCell::new(None),
            mathml_annotation_xml_integration_point: false,
        });
        text_marker.parent.set(Some(Rc::downgrade(&element)));
        element.children.borrow_mut().push(text_marker);
        element
    } else {
        text_marker
    };
    {
        let mut children = parent.children.borrow_mut();
        let index = children
            .iter()
            .position(|item| Rc::ptr_eq(item, &root))
            .ok_or(AuthenticatedRootError::Serialize)?;
        children[index] = marker;
    }
    let document_with_placeholder = serialize_node(document, TraversalScope::ChildrenOnly(None))?;
    if document_with_placeholder.matches(&placeholder).count() != 1 {
        return Err(AuthenticatedRootError::Serialize);
    }
    Ok(AuthenticatedRoot {
        child_vdom_ids,
        ignore_vdom_ids,
        opening,
        root_html,
        document_with_placeholder,
        placeholder,
        vdom,
        namespace,
        model_fields: fields,
        fragment_context,
        fragment_attrs,
        children_html,
        trailing_whitespace_ignorable,
    })
}

/// Prove that a lexical source range removes exactly the authenticated DOM
/// node, with all other browser-visible nodes unchanged. On success callers can
/// retain the original source slices, including their provenance and origins.
/// Roots moved by HTML5 error recovery or with noncontiguous source fail closed.
pub fn validated_root_range(
    html: &str,
    root: &AuthenticatedRoot,
    end: usize,
) -> Result<(usize, usize), AuthenticatedRootError> {
    use html5ever::serialize::TraversalScope;
    use html5ever::tendril::TendrilSink;
    let start = root.opening.0;
    if start >= root.opening.1
        || end < root.opening.1
        || end > html.len()
        || !html.is_char_boundary(start)
        || !html.is_char_boundary(end)
        || html.contains(&root.placeholder)
    {
        return Err(AuthenticatedRootError::UnsafeSourceRange);
    }
    let mut shell = String::with_capacity(html.len() - (end - start) + root.placeholder.len());
    shell.push_str(&html[..start]);
    shell.push_str(&root.placeholder);
    shell.push_str(&html[end..]);
    let dom = html5ever::parse_document(RcDom::default(), Default::default())
        .from_utf8()
        .read_from(&mut shell.as_bytes())
        .map_err(|_| AuthenticatedRootError::UnsafeSourceRange)?;
    let serialized = serialize_node(dom.document.clone(), TraversalScope::ChildrenOnly(None))?;
    if serialized != root.document_with_placeholder {
        // HTML5 moves whitespace after </body> into the body's trailing text.
        // Our original-DOM substitution removed that whitespace with the
        // body, whereas reparsing the lexical shell attaches it to the marker
        // body. Ignore only HTML whitespace immediately after the unique marker;
        // other attributes, elements and text must still compare exactly.
        let normalized = root
            .placeholder
            .strip_prefix("<body>")
            .and_then(|value| value.strip_suffix("</body>"))
            .and_then(|marker| {
                let prefix = format!("<body>{marker}");
                let start = serialized.find(&prefix)?;
                let ws_start = start + prefix.len();
                let tail = &serialized[ws_start..];
                let whitespace_end = tail.bytes().take_while(|b| whitespace(*b)).count();
                if !tail[whitespace_end..].starts_with("</body>") {
                    return None;
                }
                let mut output = serialized.clone();
                output.replace_range(ws_start..ws_start + whitespace_end, "");
                Some(output)
            });
        if normalized.as_deref() != Some(root.document_with_placeholder.as_str()) {
            return Err(AuthenticatedRootError::UnsafeSourceRange);
        }
    }
    Ok((start, end))
}

/// Ensure the raw root's contents parse to the same child tree through the
/// client mount's innerHTML context. HTML roots use a temporary `<div>`, while
/// foreign roots use their own namespace and encoding, matching the client.
pub fn validate_root_fragment(
    html: &str,
    root: &AuthenticatedRoot,
    close_start: usize,
) -> Result<(), AuthenticatedRootError> {
    use html5ever::serialize::TraversalScope;
    use html5ever::tendril::TendrilSink;
    if close_start < root.opening.1
        || close_start > html.len()
        || !html.is_char_boundary(close_start)
        || !html.is_char_boundary(root.opening.1)
    {
        return Err(AuthenticatedRootError::UnsafeSourceRange);
    }
    let fragment = &html[root.opening.1..close_start];
    let dom = html5ever::parse_fragment(
        RcDom::default(),
        Default::default(),
        root.fragment_context.clone(),
        root.fragment_attrs.clone(),
        false,
    )
    .from_utf8()
    .read_from(&mut fragment.as_bytes())
    .map_err(|_| AuthenticatedRootError::UnsafeSourceRange)?;
    let wrapper = dom
        .document
        .children
        .borrow()
        .iter()
        .find(
            |child| matches!(&child.data, NodeData::Element { name, .. } if &*name.local == "html"),
        )
        .cloned()
        .ok_or(AuthenticatedRootError::UnsafeSourceRange)?;
    let actual = serialize_node(
        wrapper,
        TraversalScope::ChildrenOnly(Some(root.fragment_context.clone())),
    )?;
    if actual != root.children_html {
        // For a body root, whitespace after its lexical close is reattached to
        // the body by HTML5. It is insignificant only when it remains a
        // trailing whitespace-only text node, not when it extends real text.
        let is_body = root.placeholder.starts_with("<body>");
        let extra_is_whitespace = root
            .children_html
            .strip_prefix(&actual)
            .is_some_and(|extra| extra.bytes().all(whitespace));
        if !(is_body && extra_is_whitespace && root.trailing_whitespace_ignorable) {
            return Err(AuthenticatedRootError::UnsafeSourceRange);
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    const TAG: &str = "<div dj-view=\"app.Child\" dj-lazy=\"visible\"></div>";
    #[test]
    fn batched_feeding_matches_delimiter_feeding() {
        let fragments = [
            "",
            "<p>x</p>",
            "<meta charset=windows-1252>",
            "<meta http-equiv=content-type content=\"text/html; charset=utf-8\">",
            "<!--",
            "-->",
            "--!>",
            "<script>",
            "</script>",
            "<textarea>",
            "</textarea>",
            "<template>",
            "</template>",
            "<svg><![CDATA[",
            "]]></svg>",
            "<table>",
            "</table>",
            "<select>",
            "</select>",
            "<b>",
            "</b>",
            "é😀�",
            "<div title='",
            "'>",
            TAG,
        ];
        let mut seed = 3442_u64;
        for _ in 0..2000 {
            let mut html = String::new();
            for _ in 0..12 {
                seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1);
                html.push_str(fragments[(seed >> 32) as usize % fragments.len()]);
            }
            html.push_str(TAG);
            let spans = [(0, html.len())];
            assert_eq!(
                lazy_elements_impl::<true>(&html, &spans),
                lazy_elements_impl::<false>(&html, &spans),
                "{html:?}"
            );
        }
    }
    #[test]
    fn batched_feeding_drains_suspensions_before_correlating_tags() {
        for meta in [
            "<meta charset=windows-1252>",
            "<meta http-equiv=content-type content=\"text/html; charset=utf-8\">",
        ] {
            let prefix =
                format!("{meta}<div>unrelated</div><script>x</script><div>unrelated</div>");
            let html = format!("{prefix}{TAG}");
            let spans = [(prefix.len(), html.len())];
            let found = lazy_elements(&html, &spans);
            assert_eq!(found.len(), 1);
            assert_eq!(found[0].start, prefix.len());
            assert_eq!(found, lazy_elements_impl::<false>(&html, &spans));
        }
    }
    type RootDomSnapshot = (
        Vec<usize>,
        Vec<LiveAuthorityAttribute>,
        Vec<(Option<(usize, usize)>, bool)>,
        String,
    );
    #[test]
    fn direct_child_wrapper_opening_handles_complete_and_escaped_slots() {
        for (html, slot) in [
            ("<div dj-sticky-slot=\"root\"></div>", "root"),
            ("<div dj-view data-djust-embedded=\"root\">Root=1</div>", "root"),
            ("<div dj-view dj-sticky-view=\"root\" dj-sticky-root data-djust-embedded=\"root\">Root=1</div>", "root"),
            ("<div dj-view data-djust-embedded=\"a&amp;&gt;b\">Root=1</div>", "a&>b"),
        ] {
            let end = raw_tag(html, 0).unwrap().end;
            assert_eq!(child_wrapper_opening_end(html, slot), Some(end));
            assert_eq!(child_wrapper_opening_end(html, "other"), None);
        }
    }

    fn root_dom_snapshot<const BATCHED: bool>(
        html: &str,
        spans: &[(usize, usize)],
    ) -> RootDomSnapshot {
        let document = root_dom_impl::<BATCHED>(html, spans).unwrap();
        let openings = live_openings_from_dom(&document);
        let attrs = live_attributes_from_dom(html, spans, &document);
        let candidates = page_candidates(&document)
            .into_iter()
            .map(|(node, in_body)| (source_opening(&node), in_body))
            .collect();
        let serialized = serialize_node(
            document,
            html5ever::serialize::TraversalScope::ChildrenOnly(None),
        )
        .unwrap();
        (openings, attrs, candidates, serialized)
    }

    #[test]
    fn batched_root_dom_matches_full_delimiter_oracle() {
        let fragments = [
            "",
            "<p>x</p>",
            "<main dj-root>",
            "<input dj-model='value'>",
            "<div dj-view='app.Child' dj-lazy>",
            "<main DJ-ROOT title='x > <'>",
            "<input title='a > b' dj-model=value>",
            "</main>",
            "</div>",
            "<!--",
            "-->",
            "--!>",
            "<script>",
            "</script>",
            "<textarea>",
            "</textarea>",
            "<template>",
            "</template>",
            "<svg><![CDATA[",
            "]]></svg>",
            "<table>",
            "</table>",
            "<select>",
            "</select>",
            "<b>",
            "</b>",
            "é😀�",
            "<div title='",
            "'>",
            "<<main dj-root>",
            "<body dj-root>",
            "<body dj-view=x>",
            "<input dj-model=a dj-model=b>",
            "<meta charset=windows-1252>",
            "<meta http-equiv=content-type content='text/html; charset=utf-8'>",
        ];
        let mut seed = 6301_u64;
        for _ in 0..3000 {
            let mut html = String::new();
            for _ in 0..16 {
                seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1);
                html.push_str(fragments[(seed >> 32) as usize % fragments.len()]);
            }
            let boundaries: Vec<usize> = html
                .char_indices()
                .map(|(i, _)| i)
                .chain([html.len()])
                .collect();
            let a = boundaries[boundaries.len() / 3];
            let b = boundaries[2 * boundaries.len() / 3];
            let split = [(0, a), (b, html.len())]
                .into_iter()
                .filter(|(start, end)| start < end)
                .collect();
            let all = if html.is_empty() {
                vec![]
            } else {
                vec![(0, html.len())]
            };
            for spans in [all, vec![], split] {
                assert_eq!(
                    root_dom_snapshot::<true>(&html, &spans),
                    root_dom_snapshot::<false>(&html, &spans),
                    "{html:?}, {spans:?}"
                );
            }
        }
    }

    #[test]
    fn batched_root_dom_matches_quoted_and_unterminated_tags() {
        for html in [
            "<main dj-root title='foo > bar < quux'><p>body</p></main>",
            "<script><x a='</script><main dj-root><p>hello</p></main>",
            "<main dj-root title='unterminated > < < <",
            "<main dj-root><input dj-model=x><input dj-model=y>",
            "<body><main dj-root><span>text</span></main><p>suffix</p></body>",
            "<!-- dj-model > <div dj-root > --> <section>plain</section>",
        ] {
            let spans = [(0, html.len())];
            assert_eq!(
                root_dom_snapshot::<true>(html, &spans),
                root_dom_snapshot::<false>(html, &spans),
                "{html:?}"
            );
        }
    }

    #[test]
    fn batched_root_dom_matches_with_long_unterminated_quote() {
        let html = format!("<main dj-root title='{}", "<x > ".repeat(16_000));
        let spans = [(0, html.len())];
        assert_eq!(
            root_dom_snapshot::<true>(&html, &spans),
            root_dom_snapshot::<false>(&html, &spans)
        );
    }

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
