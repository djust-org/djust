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
//!
//! Public API: [`render_markdown`], [`RenderOpts`], [`MAX_MD_INPUT_BYTES`].

use pulldown_cmark::{html, CowStr, Event, LinkType, Options, Parser, Tag};

/// Maximum input size in bytes. Inputs larger than this are returned as
/// an escaped `<pre class="djust-md-toobig">` block without invoking the
/// Markdown parser — a belt-and-braces DoS guard.
pub const MAX_MD_INPUT_BYTES: usize = 10 * 1024 * 1024;

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
    let safe_events = parser.map(sanitise_event);
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
}
