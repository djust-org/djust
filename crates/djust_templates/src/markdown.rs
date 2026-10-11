//! Safe server-side Markdown rendering for streaming LLM output.
//!
//! This module wraps `pulldown-cmark` to convert Markdown source into HTML,
//! with security-critical defaults for use with `{% djust_markdown %}`:
//!
//! - **Raw HTML in the source is NEVER rendered** (`Options::ENABLE_HTML` is
//!   never set). HTML tags appear as escaped text.
//! - `javascript:` URLs in links/images are neutralised.
//! - A **provisional-line** split lets streaming LLM output render a partially
//!   typed line as plain (escaped) text instead of mid-syntax malformed HTML.
//! - Input is capped at `MAX_MD_INPUT_BYTES`; beyond that the source is
//!   returned verbatim inside an escaped `<pre>`.
//! - Container nesting (block quotes, lists, list items) is bounded by
//!   `MAX_MD_NESTING`, so the depth of the output, and with it the cost of
//!   parsing and diffing it downstream, stays predictable.
//!
//! Public API: [`render_markdown`], [`RenderOpts`], [`MAX_MD_INPUT_BYTES`],
//! [`MAX_MD_NESTING`].

use pulldown_cmark::{html, CowStr, Event, LinkType, Options, Parser, Tag, TagEnd};

/// Maximum input size in bytes. Inputs larger than this are returned as
/// an escaped `<pre class="djust-md-toobig">` block without invoking the
/// Markdown parser — a belt-and-braces DoS guard.
pub const MAX_MD_INPUT_BYTES: usize = 10 * 1024 * 1024;

/// Most container elements (`<blockquote>`, `<ul>`/`<ol>`, `<li>`) the
/// rendered HTML keeps open at once.
///
/// A container that starts while this many are already open is rendered
/// without its own tags: its content (paragraphs, text, code, inline markup)
/// stays in place inside the innermost kept container, so no text is lost
/// and the output never nests deeper than this. Each `>` or list level costs
/// one or two levels, so ordinary documents (a handful of levels) never come
/// near it; it keeps the size of the rendered tree, and the time spent
/// parsing and diffing it, proportional to the length of the source.
pub const MAX_MD_NESTING: usize = 32;

/// Render-time options for [`render_markdown`].
///
/// Booleans default to the sensible on/off for LLM-chat use:
///
/// | field | default |
/// |---|---|
/// | `provisional` | `true` |
/// | `tables` | `true` |
/// | `strikethrough` | `true` |
/// | `task_lists` | `false` |
///
/// Raw HTML embedded in the Markdown source is **always** escaped — there is
/// no knob to enable `Options::ENABLE_HTML`.
#[derive(Debug, Clone, Copy)]
pub struct RenderOpts {
    /// If true, split the trailing line off as "provisional" and render
    /// it as escaped plain text — prevents mid-token flicker while streaming.
    pub provisional: bool,
    /// Enable GFM-style tables.
    pub tables: bool,
    /// Enable GFM `~~strikethrough~~`.
    pub strikethrough: bool,
    /// Enable GFM task lists (`- [ ] item`).
    pub task_lists: bool,
}

impl Default for RenderOpts {
    fn default() -> Self {
        Self {
            provisional: true,
            tables: true,
            strikethrough: true,
            task_lists: false,
        }
    }
}

impl RenderOpts {
    fn to_pulldown(self) -> Options {
        let mut o = Options::empty();
        if self.tables {
            o.insert(Options::ENABLE_TABLES);
        }
        if self.strikethrough {
            o.insert(Options::ENABLE_STRIKETHROUGH);
        }
        if self.task_lists {
            o.insert(Options::ENABLE_TASKLISTS);
        }
        // SECURITY: Options::ENABLE_HTML is NEVER set. Raw HTML in the
        // source is escaped by pulldown-cmark automatically.
        o
    }
}

/// Render Markdown source to a sanitised HTML string.
///
/// Safe by construction: `ENABLE_HTML` is never set, so tags in `src` are
/// escaped. Long input is bounded by [`MAX_MD_INPUT_BYTES`].
pub fn render_markdown(src: &str, opts: RenderOpts) -> String {
    if src.is_empty() {
        return String::new();
    }
    if src.len() > MAX_MD_INPUT_BYTES {
        return format!("<pre class=\"djust-md-toobig\">{}</pre>", html_escape(src));
    }

    let (stable, trailing) = if opts.provisional {
        split_provisional(src)
    } else {
        (src, "")
    };

    let parser = Parser::new_ext(stable, opts.to_pulldown());
    // SECURITY: pulldown-cmark still emits Event::Html / Event::InlineHtml
    // even when Options::ENABLE_HTML is unset — its push_html writer just
    // passes those events through verbatim. To actually escape raw HTML
    // in the source, we map those events to Text events, and we strip
    // `javascript:` (and similar) URLs from link/image destinations.
    let mut nesting = NestingCap::default();
    let safe_events = parser
        .map(sanitise_event)
        .filter_map(|event| nesting.filter(event));
    let mut out = String::with_capacity(stable.len() + 64);
    html::push_html(&mut out, safe_events);

    if !trailing.is_empty() {
        out.push_str("<p class=\"djust-md-provisional\">");
        out.push_str(&html_escape(trailing));
        out.push_str("</p>");
    }
    out
}

/// Split `src` into `(stable, trailing)` — stable is handed to pulldown-cmark,
/// trailing is rendered as escaped plain text.
///
/// The trailing split only happens when the last line looks mid-token:
/// odd count of `**`, single `*`, backticks, unclosed brackets, or a dangling
/// `](`. Otherwise everything is stable.
pub(crate) fn split_provisional(src: &str) -> (&str, &str) {
    if src.is_empty() || src.ends_with('\n') {
        return (src, "");
    }
    if inside_unclosed_fence(src) {
        // pulldown-cmark handles unclosed fenced code gracefully on its own.
        return (src, "");
    }
    let last_nl = src.rfind('\n').map(|i| i + 1).unwrap_or(0);
    let trailing = &src[last_nl..];
    // A trailing line that is itself a code-fence delimiter (```) completes a
    // BALANCED fence — `inside_unclosed_fence` already returned false above, so
    // the ``` count is even, making this trailing ``` a CLOSING marker, not an
    // unterminated inline-code span. Without this guard `looks_unterminated`
    // counts its 3 backticks as odd and wrongly splits the closing fence off,
    // re-rendering it as a provisional `<p>```</p>` (#1998). Keep the whole
    // block stable.
    if trailing.trim_start().starts_with("```") {
        return (src, "");
    }
    if looks_unterminated(trailing) {
        (&src[..last_nl], trailing)
    } else {
        (src, "")
    }
}

fn inside_unclosed_fence(src: &str) -> bool {
    src.lines()
        .filter(|l| l.trim_start().starts_with("```"))
        .count()
        % 2
        == 1
}

fn looks_unterminated(line: &str) -> bool {
    // Unclosed bold (`**`).
    let bold = line.matches("**").count();
    if bold % 2 == 1 {
        return true;
    }
    // Unclosed italic (`*`), after stripping `**` pairs.
    let star = line.replace("**", "").matches('*').count();
    if star % 2 == 1 {
        return true;
    }
    // Unclosed inline code (`` ` ``).
    let tick = line.matches('`').count();
    if tick % 2 == 1 {
        return true;
    }
    has_unclosed_bracket(line)
}

fn has_unclosed_bracket(line: &str) -> bool {
    let mut depth: i32 = 0;
    for ch in line.chars() {
        match ch {
            '[' => depth += 1,
            ']' => depth -= 1,
            _ => {}
        }
    }
    if depth > 0 {
        return true;
    }
    // Dangling `](` (link in progress: `[text](...`).
    if let Some(idx) = line.rfind("](") {
        if !line[idx + 2..].contains(')') {
            return true;
        }
    }
    false
}

/// Neutralise dangerous URL schemes by replacing them with `#`.
///
/// Matches (case-insensitive, with optional surrounding whitespace):
/// `javascript:`, `vbscript:`, `data:`.
///
/// We block `data:` in both Link and Image destinations (stored as the
/// same `CowStr` path); images could theoretically accept `data:image/*`,
/// but it's safer to over-block since `data:image/svg+xml` can execute
/// scripts.
fn neutralise_url(url: &CowStr<'_>) -> CowStr<'static> {
    let trimmed = url.trim_start();
    let lower = trimmed.to_ascii_lowercase();
    if lower.starts_with("javascript:")
        || lower.starts_with("vbscript:")
        || lower.starts_with("data:")
    {
        return CowStr::Borrowed("#");
    }
    CowStr::Boxed(url.to_string().into_boxed_str())
}

/// Map one parser event to a sanitised form.
/// - Raw HTML events become Text (so they are HTML-escaped by the writer).
/// - Link / Image destinations pointing at dangerous schemes are rewritten.
fn sanitise_event(event: Event<'_>) -> Event<'_> {
    match event {
        // Raw HTML — escape by re-routing through Text.
        Event::Html(s) => Event::Text(s),
        Event::InlineHtml(s) => Event::Text(s),
        // Links / images — scrub hostile schemes.
        Event::Start(Tag::Link {
            link_type,
            dest_url,
            title,
            id,
        }) => Event::Start(Tag::Link {
            link_type,
            dest_url: neutralise_url(&dest_url),
            title,
            id,
        }),
        Event::Start(Tag::Image {
            link_type,
            dest_url,
            title,
            id,
        }) => Event::Start(Tag::Image {
            link_type,
            dest_url: neutralise_url(&dest_url),
            title,
            id,
        }),
        other => other,
    }
}

/// Tracks how many containers are open and drops the tags of those beyond
/// [`MAX_MD_NESTING`].
///
/// Whether a container keeps its tags depends only on how many containers
/// enclose it, so its start and end events always get the same answer and
/// every kept `<li>`/`<ul>`/`<blockquote>` stays balanced. A run of dropped
/// tags becomes one line break, so the text on either side stays apart.
#[derive(Default)]
struct NestingCap {
    open: usize,
    separated: bool,
}

impl NestingCap {
    fn filter<'a>(&mut self, event: Event<'a>) -> Option<Event<'a>> {
        let keep = match event {
            Event::Start(Tag::BlockQuote(_) | Tag::List(_) | Tag::Item) => {
                self.open += 1;
                self.open <= MAX_MD_NESTING
            }
            Event::End(TagEnd::BlockQuote(_) | TagEnd::List(_) | TagEnd::Item) => {
                self.open = self.open.saturating_sub(1);
                self.open < MAX_MD_NESTING
            }
            _ => true,
        };
        if keep {
            self.separated = false;
            Some(event)
        } else if self.separated {
            None
        } else {
            self.separated = true;
            Some(Event::SoftBreak)
        }
    }
}

/// Silence the unused-variant lint emitted by some toolchains when
/// `LinkType` is imported only for exhaustive matching.
#[allow(dead_code)]
fn _linktype_probe(_: LinkType) {}

fn html_escape(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for ch in s.chars() {
        match ch {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            '\'' => out.push_str("&#x27;"),
            _ => out.push(ch),
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    // ---------------------------------------------------------------
    // Basic rendering
    // ---------------------------------------------------------------

    #[test]
    fn test_basic_paragraph() {
        let out = render_markdown("Hello world\n", RenderOpts::default());
        assert!(out.contains("<p>"));
        assert!(out.contains("Hello world"));
    }

    #[test]
    fn test_heading_one() {
        let out = render_markdown("# Title\n", RenderOpts::default());
        assert!(out.contains("<h1>"));
        assert!(out.contains("Title"));
    }

    #[test]
    fn test_fenced_code() {
        let src = "```\nlet x = 1;\n```\n";
        let out = render_markdown(src, RenderOpts::default());
        assert!(out.contains("<pre>"));
        assert!(out.contains("<code>"));
        assert!(out.contains("let x = 1;"));
    }

    #[test]
    fn test_unterminated_fence() {
        // An unclosed ``` block should not panic; pulldown-cmark auto-closes.
        let src = "```\nlet x = 1;\n";
        let out = render_markdown(src, RenderOpts::default());
        assert!(out.contains("let x = 1;"));
    }

    // ---------------------------------------------------------------
    // ⭐ Rule tests (written RED first)
    // ---------------------------------------------------------------

    #[test]
    fn test_xss_payload_rendered_as_escaped_text() {
        // Classic XSS payload must not become a live <script>.
        let src = "before <script>alert('xss')</script> after\n";
        let out = render_markdown(src, RenderOpts::default());
        assert!(
            !out.contains("<script>"),
            "raw <script> MUST be escaped, got: {}",
            out
        );
        assert!(
            out.contains("&lt;script&gt;"),
            "expected escaped &lt;script&gt; in output, got: {}",
            out
        );
    }

    #[test]
    fn test_raw_html_is_escaped_not_rendered() {
        let src = "<img src=x onerror=alert(1)>\n";
        let out = render_markdown(src, RenderOpts::default());
        assert!(
            !out.contains("<img "),
            "raw <img> MUST NOT be rendered live, got: {}",
            out
        );
        assert!(out.contains("&lt;img"));
    }

    #[test]
    fn test_javascript_url_in_link_href_is_neutralized() {
        let src = "[click me](javascript:alert(1))\n";
        let out = render_markdown(src, RenderOpts::default());
        // pulldown-cmark's own escape logic already filters `javascript:`
        // href values — verify the attribute isn't present as an active URL.
        assert!(
            !out.contains("href=\"javascript:alert(1)\""),
            "javascript: href MUST be neutralised, got: {}",
            out
        );
    }

    #[test]
    fn test_javascript_url_rewritten_to_hash() {
        // STRICT form: verify the neutralised URL appears as "#".
        let src = "[x](javascript:alert(1))\n";
        let out = render_markdown(src, RenderOpts::default());
        assert!(
            out.contains("href=\"#\""),
            "javascript: href MUST be rewritten to '#', got: {}",
            out
        );
    }

    #[test]
    fn test_mixed_case_javascript_url_neutralized() {
        // Case-insensitive match on the scheme.
        let src = "[x](JavaScript:alert(1))\n";
        let out = render_markdown(src, RenderOpts::default());
        assert!(
            out.contains("href=\"#\""),
            "mixed-case javascript: URL MUST be neutralised to '#', got: {}",
            out
        );
    }

    #[test]
    fn test_leading_whitespace_javascript_url_neutralized() {
        // Leading whitespace must not defeat the scheme check.
        let src = "[x](  javascript:alert(1))\n";
        let out = render_markdown(src, RenderOpts::default());
        assert!(
            out.contains("href=\"#\""),
            "whitespace-prefixed javascript: URL MUST be neutralised to '#', got: {}",
            out
        );
    }

    #[test]
    fn test_vbscript_url_neutralized() {
        let src = "[x](vbscript:msg())\n";
        let out = render_markdown(src, RenderOpts::default());
        assert!(
            !out.contains("href=\"vbscript:"),
            "vbscript: href MUST NOT survive in output, got: {}",
            out
        );
        assert!(
            out.contains("href=\"#\""),
            "vbscript: href MUST be rewritten to '#', got: {}",
            out
        );
    }

    #[test]
    fn test_data_url_neutralized() {
        let src = "[x](data:text/html,<script>)\n";
        let out = render_markdown(src, RenderOpts::default());
        assert!(
            !out.contains("href=\"data:"),
            "data: href MUST NOT survive in output, got: {}",
            out
        );
        assert!(
            out.contains("href=\"#\""),
            "data: href MUST be rewritten to '#', got: {}",
            out
        );
    }

    #[test]
    fn test_image_with_javascript_src_neutralized() {
        let src = "![alt](javascript:alert(1))\n";
        let out = render_markdown(src, RenderOpts::default());
        assert!(
            !out.contains("src=\"javascript:"),
            "javascript: image src MUST NOT survive in output, got: {}",
            out
        );
        assert!(
            out.contains("src=\"#\""),
            "javascript: image src MUST be rewritten to '#', got: {}",
            out
        );
    }

    #[test]
    fn test_iframe_escaped() {
        let src = "<iframe src=x></iframe>\n";
        let out = render_markdown(src, RenderOpts::default());
        assert!(
            out.contains("&lt;iframe"),
            "iframe tag MUST be escaped, got: {}",
            out
        );
        assert!(
            !out.contains("<iframe"),
            "literal <iframe MUST NOT appear in output, got: {}",
            out
        );
    }

    #[test]
    fn test_provisional_mode_unclosed_bold_renders_as_text() {
        // "Hello **wor" — unclosed bold on the trailing line.
        let src = "Hello **wor";
        let out = render_markdown(src, RenderOpts::default());
        assert!(
            out.contains("djust-md-provisional"),
            "expected provisional wrapper in output: {}",
            out
        );
        assert!(
            !out.contains("<strong>"),
            "unclosed ** must not be rendered as <strong>, got: {}",
            out
        );
    }

    #[test]
    fn test_size_cap_exceeded_returns_escaped_literal() {
        // Build a source > MAX_MD_INPUT_BYTES containing a `<script>`
        // — must not reach the parser.
        let payload = "<script>a</script>";
        let mut src = String::with_capacity(MAX_MD_INPUT_BYTES + 64);
        while src.len() <= MAX_MD_INPUT_BYTES {
            src.push_str(payload);
        }
        let out = render_markdown(&src, RenderOpts::default());
        assert!(out.starts_with("<pre class=\"djust-md-toobig\">"));
        assert!(
            !out.contains("<script>"),
            "size-capped output must still escape HTML"
        );
        assert!(out.contains("&lt;script&gt;"));
    }

    // ---------------------------------------------------------------
    // Options
    // ---------------------------------------------------------------

    #[test]
    fn test_tables_enabled() {
        let src = "| a | b |\n| - | - |\n| 1 | 2 |\n";
        let out = render_markdown(src, RenderOpts::default());
        assert!(out.contains("<table>"));
    }

    #[test]
    fn test_strikethrough_enabled() {
        let out = render_markdown("~~gone~~\n", RenderOpts::default());
        assert!(out.contains("<del>") || out.contains("<s>"));
    }

    #[test]
    fn test_task_lists_off_by_default() {
        let src = "- [ ] todo\n- [x] done\n";
        let out = render_markdown(src, RenderOpts::default());
        // Default: no <input type="checkbox">.
        assert!(!out.contains("type=\"checkbox\""));
    }

    #[test]
    fn test_task_lists_on_when_enabled() {
        let src = "- [ ] todo\n- [x] done\n";
        let opts = RenderOpts {
            task_lists: true,
            ..Default::default()
        };
        let out = render_markdown(src, opts);
        assert!(out.contains("type=\"checkbox\""));
    }

    // ---------------------------------------------------------------
    // Provisional mode
    // ---------------------------------------------------------------

    #[test]
    fn test_provisional_off_no_split() {
        let src = "Hello **wor";
        let opts = RenderOpts {
            provisional: false,
            ..Default::default()
        };
        let out = render_markdown(src, opts);
        assert!(
            !out.contains("djust-md-provisional"),
            "provisional=false must never emit the wrapper"
        );
    }

    // ---------------------------------------------------------------
    // Edge cases
    // ---------------------------------------------------------------

    #[test]
    fn test_empty_input() {
        let out = render_markdown("", RenderOpts::default());
        assert_eq!(out, "");
    }

    #[test]
    fn test_unicode_roundtrip() {
        let out = render_markdown("héllo — 世界\n", RenderOpts::default());
        assert!(out.contains("héllo"));
        assert!(out.contains("世界"));
    }

    #[test]
    fn test_split_provisional_edge_cases() {
        // Trailing newline: fully stable.
        assert_eq!(split_provisional("foo\n"), ("foo\n", ""));
        // Unclosed bold on trailing.
        let (stable, tail) = split_provisional("line1\nHello **wor");
        assert_eq!(stable, "line1\n");
        assert_eq!(tail, "Hello **wor");
        // Unclosed inline code on trailing.
        let (stable, tail) = split_provisional("a\n`foo");
        assert_eq!(stable, "a\n");
        assert_eq!(tail, "`foo");
        // Unclosed bracket on trailing.
        let (stable, tail) = split_provisional("a\n[link");
        assert_eq!(stable, "a\n");
        assert_eq!(tail, "[link");
        // Dangling `](`.
        let (stable, tail) = split_provisional("a\n[text](http://ex");
        assert_eq!(stable, "a\n");
        assert_eq!(tail, "[text](http://ex");
        // Fully-formed trailing line stays stable.
        assert_eq!(split_provisional("ok"), ("ok", ""));
        // Empty.
        assert_eq!(split_provisional(""), ("", ""));
    }

    #[test]
    fn test_split_provisional_closing_fence_not_split_1998() {
        // #1998: a COMPLETE fenced block with no trailing newline. The closing
        // ``` line has 3 backticks (odd) and pre-fix `looks_unterminated` wrongly
        // treated it as an unterminated inline-code span and split it off. The
        // fences are balanced (even count), so the trailing ``` is a CLOSING
        // marker — the whole block must stay stable.
        let src = "```python\ndef greet():\n    pass\n```";
        assert_eq!(split_provisional(src), (src, ""));
        // A lone closing fence after content, no trailing newline.
        assert_eq!(split_provisional("```\ncode\n```"), ("```\ncode\n```", ""));
        // Still correctly provisional while the fence is genuinely OPEN
        // (odd count) — no regression to the streaming case.
        let (stable, tail) = split_provisional("```python\ndef greet():");
        assert_eq!(stable, "```python\ndef greet():");
        assert_eq!(tail, "");
    }

    #[test]
    fn test_provisional_complete_fence_no_trailing_newline_renders_code_1998() {
        // The end-to-end symptom: `{% djust_markdown %}` on a code-only artifact
        // (a complete fence, no surrounding prose, no trailing newline) used to
        // render the closing ``` as an escaped provisional paragraph instead of
        // completing the code block.
        let src = "```python\ndef greet():\n    pass\n```";
        let out = render_markdown(src, RenderOpts::default()); // provisional=true
        assert!(
            !out.contains("djust-md-provisional"),
            "the closing fence must not become a provisional paragraph (got: {out})"
        );
        assert!(
            out.contains("<pre>") && out.contains("<code"),
            "the whole block must render as a code block (got: {out})"
        );
        assert!(
            out.contains("language-python"),
            "the info string must survive (got: {out})"
        );
        assert!(
            !out.contains("<p>"),
            "no stray paragraph should be emitted (got: {out})"
        );
    }

    #[test]
    fn test_plain_text_url_is_not_autolinked() {
        // pulldown-cmark 0.12 does not expose a GFM_AUTOLINK /
        // ENABLE_AUTOLINK options flag, so bare URLs in plain text must
        // remain plain text (not wrapped in an <a> tag). This regression
        // test locks that current behavior — if a future pulldown-cmark
        // upgrade flips the autolink default on, this test will fail and
        // force us to re-evaluate the public surface of djust_markdown
        // (since the guide documents "no autolinks in plain text").
        let out = render_markdown(
            "Visit https://example.com for info\n",
            RenderOpts::default(),
        );
        assert!(
            !out.contains("<a href=\"https://example.com\">"),
            "plain-text URL should not be autolinked (got: {out})",
        );
        assert!(
            out.contains("https://example.com"),
            "plain-text URL should survive as literal text (got: {out})",
        );
    }

    // ---------------------------------------------------------------
    // Container nesting bound (MAX_MD_NESTING)
    // ---------------------------------------------------------------

    /// The same pipeline as `render_markdown` minus the nesting bound: what
    /// the renderer produced before the bound existed.
    fn render_unbounded(src: &str, opts: RenderOpts) -> String {
        let (stable, trailing) = if opts.provisional {
            split_provisional(src)
        } else {
            (src, "")
        };
        let parser = Parser::new_ext(stable, opts.to_pulldown());
        let mut out = String::new();
        html::push_html(&mut out, parser.map(sanitise_event));
        if !trailing.is_empty() {
            out.push_str("<p class=\"djust-md-provisional\">");
            out.push_str(&html_escape(trailing));
            out.push_str("</p>");
        }
        out
    }

    const CONTAINERS: [&str; 4] = ["blockquote", "ul", "ol", "li"];

    /// Walk the container tags of `html` with a stack: panics on a closing
    /// tag that does not match the innermost open one, and returns the
    /// deepest nesting reached plus the number of `<li>` / `</li>` seen.
    fn container_shape(html: &str) -> (usize, usize, usize) {
        let mut stack: Vec<&str> = Vec::new();
        let (mut deepest, mut li_open, mut li_close) = (0, 0, 0);
        let mut rest = html;
        while let Some(lt) = rest.find('<') {
            rest = &rest[lt + 1..];
            let closing = rest.starts_with('/');
            let body = if closing { &rest[1..] } else { rest };
            let end = body
                .find(|c: char| c == '>' || c.is_ascii_whitespace())
                .unwrap_or(body.len());
            let name = &body[..end];
            let Some(tag) = CONTAINERS.iter().find(|t| **t == name) else {
                continue;
            };
            if closing {
                assert_eq!(stack.pop(), Some(*tag), "unbalanced </{tag}>");
                if *tag == "li" {
                    li_close += 1;
                }
            } else {
                stack.push(tag);
                deepest = deepest.max(stack.len());
                if *tag == "li" {
                    li_open += 1;
                }
            }
        }
        assert!(stack.is_empty(), "unclosed containers: {stack:?}");
        (deepest, li_open, li_close)
    }

    fn quotes(depth: usize, text: &str) -> String {
        format!("{}{}\n", "> ".repeat(depth), text)
    }

    /// One bullet per level, each indented under the previous one.
    fn nested_list(depth: usize) -> String {
        (0..depth)
            .map(|i| format!("{}- item{}\n", "  ".repeat(i), i))
            .collect()
    }

    #[test]
    fn test_nesting_bound_constant() {
        assert_eq!(MAX_MD_NESTING, 32);
    }

    #[test]
    fn test_deep_quotes_render_fast_and_bounded() {
        let src = quotes(100_000, "bottom");
        let started = std::time::Instant::now();
        let out = render_markdown(&src, RenderOpts::default());
        let took = started.elapsed();
        assert!(
            took < std::time::Duration::from_secs(2),
            "100,000 nested quotes took {took:?}"
        );
        let (deepest, _, _) = container_shape(&out);
        assert_eq!(deepest, MAX_MD_NESTING);
        assert!(out.contains("bottom"));
        assert!(out.len() < 2_000, "output grew to {} bytes", out.len());
    }

    #[test]
    fn test_nested_quotes_past_the_bound_are_capped() {
        let src = quotes(MAX_MD_NESTING + 8, "deep text");
        let out = render_markdown(&src, RenderOpts::default());
        assert_eq!(out.matches("<blockquote>").count(), MAX_MD_NESTING);
        assert_eq!(out.matches("</blockquote>").count(), MAX_MD_NESTING);
        assert_eq!(container_shape(&out).0, MAX_MD_NESTING);
        assert!(out.contains("<p>deep text</p>"));
        assert_ne!(out, render_unbounded(&src, RenderOpts::default()));
    }

    #[test]
    fn test_deep_lists_keep_balanced_items_and_all_text() {
        for depth in [MAX_MD_NESTING / 2 + 1, 40, 200, 1_500] {
            let src = nested_list(depth);
            let out = render_markdown(&src, RenderOpts::default());
            let (deepest, li_open, li_close) = container_shape(&out);
            assert!(deepest <= MAX_MD_NESTING, "depth {depth}: {deepest}");
            assert_eq!(li_open, li_close, "depth {depth}");
            for i in 0..depth {
                let word = format!("item{i}");
                let hits = out
                    .split(|c: char| !c.is_ascii_alphanumeric())
                    .filter(|w| *w == word)
                    .count();
                assert_eq!(hits, 1, "depth {depth}: {word} appears {hits} times");
            }
        }
    }

    #[test]
    fn test_every_quote_level_keeps_its_text() {
        let src: String = (1..=300).map(|i| quotes(i, &format!("t{i}"))).collect();
        let out = render_markdown(&src, RenderOpts::default());
        assert!(container_shape(&out).0 <= MAX_MD_NESTING);
        for i in 1..=300 {
            let word = format!("t{i}");
            assert!(
                out.split(|c: char| !c.is_ascii_alphanumeric())
                    .any(|w| w == word),
                "{word} missing"
            );
        }
    }

    #[test]
    fn test_mixed_quotes_and_lists_bounded() {
        let mut src = String::new();
        for i in 0..200 {
            src.push_str(&"> ".repeat(i));
            src.push_str("- ");
            src.push_str(&format!("m{i}\n"));
        }
        let out = render_markdown(&src, RenderOpts::default());
        let (deepest, li_open, li_close) = container_shape(&out);
        assert!(deepest <= MAX_MD_NESTING);
        assert_eq!(li_open, li_close);
        for i in 0..200 {
            assert!(out.contains(&format!("m{i}")), "m{i} missing");
        }
    }

    #[test]
    fn test_nesting_within_the_bound_is_byte_identical() {
        let opts = RenderOpts::default();
        let mut cases: Vec<String> = Vec::new();
        for depth in 1..=MAX_MD_NESTING {
            cases.push(quotes(depth, "quoted *text* and `code`"));
            cases.push(format!(
                "{}\n\n{}para two\n",
                quotes(depth, "para one").trim_end(),
                "> ".repeat(depth)
            ));
        }
        // A list level is two containers (<ul> and <li>).
        for depth in 1..=MAX_MD_NESTING / 2 {
            cases.push(nested_list(depth));
            let loose: String = (0..depth)
                .map(|i| format!("{}1. item{i}\n\n", "   ".repeat(i)))
                .collect();
            cases.push(loose);
        }
        // Quotes holding lists, lists holding quotes, alerts, tasks.
        cases.push("> - a\n>   > b\n>   > - c\n>   >   1. d\n".into());
        cases.push("- a\n  > b\n  > > - c\n".into());
        cases.push("> [!NOTE]\n> > nested\n".into());
        cases.push("- [ ] todo\n  - [x] done\n".into());
        for src in &cases {
            assert_eq!(
                render_markdown(src, opts),
                render_unbounded(src, opts),
                "output changed for {src:?}"
            );
            let tl = RenderOpts {
                task_lists: true,
                provisional: false,
                ..opts
            };
            assert_eq!(render_markdown(src, tl), render_unbounded(src, tl));
        }
    }
}
