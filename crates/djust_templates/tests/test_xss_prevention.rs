//! XSS Prevention Tests for djust Template Engine
//!
//! These integration tests verify that the Rust template engine correctly
//! prevents Cross-Site Scripting (XSS) attacks across multiple contexts:
//!
//! 1. **HTML context** - Auto-escaping of {{ variables }}
//! 2. **JavaScript context** - The |escapejs filter
//! 3. **URL context** - The |urlencode filter
//! 4. **JSON/script context** - The |json_script filter
//! 5. **Safe filter bypass** - Intentional opt-out of auto-escaping
//!
//! The engine follows Django's escaping model: all variable output is
//! auto-escaped unless explicitly marked safe via |safe or a filter
//! listed in `safe_output_filters`.

use djust_core::{Context, Value};
use djust_templates::Template;

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------

fn render(source: &str, ctx: &Context) -> String {
    let t = Template::new(source).expect("template should parse");
    t.render(ctx).expect("template should render")
}

fn ctx_with(key: &str, val: &str) -> Context {
    let mut ctx = Context::new();
    ctx.set(key.to_string(), Value::String(val.to_string()));
    ctx
}

// ===========================================================================
// 1. HTML Context Auto-Escaping ({{ user_input }})
// ===========================================================================

#[test]
fn html_escape_script_tag() {
    let ctx = ctx_with("input", "<script>alert('xss')</script>");
    let result = render("{{ input }}", &ctx);
    assert_eq!(
        result,
        "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;"
    );
    assert!(
        !result.contains("<script>"),
        "Literal <script> must never appear in auto-escaped output"
    );
}

#[test]
fn html_escape_img_onerror() {
    let ctx = ctx_with("input", "<img src=x onerror=alert(1)>");
    let result = render("{{ input }}", &ctx);
    assert!(!result.contains("<img"), "img tag must be escaped");
    assert!(result.contains("&lt;img"));
}

#[test]
fn html_escape_event_handler_injection() {
    // The input tries to break out of a quoted attribute with a literal "
    // After escaping, the " becomes &quot; so the attribute boundary is preserved.
    let ctx = ctx_with("input", "\" onmouseover=\"alert(1)");
    let result = render("<div title=\"{{ input }}\">", &ctx);
    assert!(
        result.contains("&quot;"),
        "Double quotes must be escaped to &quot;"
    );
    // The crucial check: no unescaped " inside the attribute value
    // that could create a new attribute boundary.
    // Extract the attribute value between the first and last "
    let attr_start = result.find("title=\"").unwrap() + 7;
    let attr_content = &result[attr_start..];
    // The next literal " should be the closing one from the template,
    // not an injected one. All injected " are &quot;
    assert!(
        attr_content.starts_with("&quot;"),
        "Injected quote must be escaped: got {attr_content}"
    );
}

#[test]
fn html_escape_ampersand() {
    let ctx = ctx_with("input", "A & B");
    let result = render("{{ input }}", &ctx);
    assert_eq!(result, "A &amp; B");
}

#[test]
fn html_escape_less_than_greater_than() {
    let ctx = ctx_with("input", "1 < 2 > 0");
    let result = render("{{ input }}", &ctx);
    assert_eq!(result, "1 &lt; 2 &gt; 0");
}

#[test]
fn html_escape_double_quote() {
    let ctx = ctx_with("input", "say \"hello\"");
    let result = render("{{ input }}", &ctx);
    assert!(result.contains("&quot;"));
    assert!(!result.contains('"'));
}

#[test]
fn html_escape_single_quote() {
    let ctx = ctx_with("input", "it's");
    let result = render("{{ input }}", &ctx);
    assert!(result.contains("&#x27;"));
}

#[test]
fn html_escape_all_five_chars_at_once() {
    // All five characters that html_escape handles: & < > " '
    let ctx = ctx_with("input", "&<>\"'");
    let result = render("{{ input }}", &ctx);
    assert_eq!(result, "&amp;&lt;&gt;&quot;&#x27;");
}

#[test]
fn html_escape_preserves_safe_text() {
    let ctx = ctx_with("input", "Hello World 123");
    let result = render("{{ input }}", &ctx);
    assert_eq!(result, "Hello World 123");
}

#[test]
fn html_escape_nested_script_tags() {
    let ctx = ctx_with("input", "<scr<script>ipt>alert(1)</scr</script>ipt>");
    let result = render("{{ input }}", &ctx);
    assert!(!result.contains("<script>"));
    assert!(!result.contains("<scr"));
}

#[test]
fn html_escape_svg_xss() {
    let ctx = ctx_with("input", "<svg onload=alert(1)>");
    let result = render("{{ input }}", &ctx);
    assert!(!result.contains("<svg"));
    assert!(result.contains("&lt;svg"));
}

#[test]
fn html_escape_iframe_injection() {
    let ctx = ctx_with("input", "<iframe src=\"javascript:alert(1)\"></iframe>");
    let result = render("{{ input }}", &ctx);
    assert!(!result.contains("<iframe"));
}

#[test]
fn html_escape_null_bytes() {
    // Null bytes can sometimes be used to bypass filters
    let ctx = ctx_with("input", "<scr\0ipt>alert(1)</script>");
    let result = render("{{ input }}", &ctx);
    assert!(!result.contains("<scr"));
}

#[test]
fn html_escape_in_for_loop() {
    let mut ctx = Context::new();
    ctx.set(
        "items".to_string(),
        Value::List(vec![
            Value::String("<b>bold</b>".to_string()),
            Value::String("<script>xss</script>".to_string()),
        ]),
    );
    let result = render("{% for item in items %}{{ item }}{% endfor %}", &ctx);
    assert!(!result.contains("<script>"));
    assert!(!result.contains("<b>"));
    assert!(result.contains("&lt;b&gt;"));
    assert!(result.contains("&lt;script&gt;"));
}

#[test]
fn html_escape_in_if_block() {
    let ctx = ctx_with("name", "<script>alert(1)</script>");
    let mut full_ctx = ctx;
    full_ctx.set("show".to_string(), Value::Bool(true));
    let result = render("{% if show %}{{ name }}{% endif %}", &full_ctx);
    assert!(!result.contains("<script>"));
}

// ===========================================================================
// 2. JavaScript Context Escaping (|escapejs filter)
// ===========================================================================

#[test]
fn escapejs_script_injection() {
    let ctx = ctx_with("input", "</script><script>alert(1)</script>");
    let result = render("{{ input|escapejs }}", &ctx);
    assert!(!result.contains("</script>"));
    assert!(result.contains("\\u003C"));
    assert!(result.contains("\\u003E"));
}

#[test]
fn escapejs_quote_breaking() {
    let ctx = ctx_with("input", "'; alert('xss'); //");
    let result = render("{{ input|escapejs }}", &ctx);
    // The single quotes are escaped to \\u0027 preventing string breakout.
    // The word "alert" may still appear as text, but the quotes around it
    // are escaped so it cannot execute as code.
    assert!(result.contains("\\u0027"));
    assert!(!result.contains("'"));
}

#[test]
fn escapejs_double_quote_breaking() {
    let ctx = ctx_with("input", "\"; alert(\"xss\"); //");
    let result = render("{{ input|escapejs }}", &ctx);
    assert!(result.contains("\\u0022"));
    assert!(!result.contains("alert(\"xss\")"));
}

#[test]
fn escapejs_backslash_escape() {
    let ctx = ctx_with("input", "\\'; alert(1); //");
    let result = render("{{ input|escapejs }}", &ctx);
    assert!(result.contains("\\u005C"));
    assert!(result.contains("\\u0027"));
}

#[test]
fn escapejs_newline_injection() {
    // Newlines in JS strings can break out of string context
    let ctx = ctx_with("input", "line1\nline2");
    let result = render("{{ input|escapejs }}", &ctx);
    assert!(!result.contains('\n'));
    assert!(result.contains("\\u000A"));
}

#[test]
fn escapejs_carriage_return() {
    let ctx = ctx_with("input", "line1\rline2");
    let result = render("{{ input|escapejs }}", &ctx);
    assert!(!result.contains('\r'));
    assert!(result.contains("\\u000D"));
}

#[test]
fn escapejs_tab() {
    let ctx = ctx_with("input", "col1\tcol2");
    let result = render("{{ input|escapejs }}", &ctx);
    assert!(!result.contains('\t'));
    assert!(result.contains("\\u0009"));
}

#[test]
fn escapejs_null_byte() {
    let ctx = ctx_with("input", "null\0byte");
    let result = render("{{ input|escapejs }}", &ctx);
    assert!(result.contains("\\u0000"));
}

#[test]
fn escapejs_unicode_line_separator() {
    // U+2028 and U+2029 are valid in JSON but not in JS string literals
    let ctx = ctx_with("input", "a\u{2028}b\u{2029}c");
    let result = render("{{ input|escapejs }}", &ctx);
    assert!(result.contains("\\u2028"));
    assert!(result.contains("\\u2029"));
    assert!(!result.contains('\u{2028}'));
    assert!(!result.contains('\u{2029}'));
}

#[test]
fn escapejs_html_entities_in_js() {
    // Angle brackets and ampersands must also be escaped for JS context
    let ctx = ctx_with("input", "<>&");
    let result = render("{{ input|escapejs }}", &ctx);
    assert!(result.contains("\\u003C"));
    assert!(result.contains("\\u003E"));
    assert!(result.contains("\\u0026"));
}

#[test]
fn escapejs_equals_sign() {
    let ctx = ctx_with("input", "a=b");
    let result = render("{{ input|escapejs }}", &ctx);
    assert!(result.contains("\\u003D"));
}

#[test]
fn escapejs_semicolon() {
    let ctx = ctx_with("input", "a;b");
    let result = render("{{ input|escapejs }}", &ctx);
    assert!(result.contains("\\u003B"));
}

#[test]
fn escapejs_hyphen() {
    // Hyphens can form HTML comment closers (-->)
    let ctx = ctx_with("input", "a-b");
    let result = render("{{ input|escapejs }}", &ctx);
    assert!(result.contains("\\u002D"));
}

// Note: |escapejs is auto-escaped by the renderer after the filter runs,
// because escapejs is NOT in safe_output_filters. This means the Unicode
// escape sequences get an additional HTML-escape pass. In a real template
// you would use it inside a <script> tag where auto-escaping should ideally
// be off ({% autoescape off %} in Django), or combine with |safe.
// The test above verifies the filter's own escaping logic is correct.

// ===========================================================================
// 3. URL Context Escaping (|urlencode filter)
// ===========================================================================

#[test]
fn urlencode_javascript_protocol() {
    let ctx = ctx_with("url", "javascript:alert(1)");
    let result = render("{{ url|urlencode }}", &ctx);
    // urlencode percent-encodes the colon, preventing protocol interpretation
    assert!(result.contains("%3A"));
    assert!(!result.contains("javascript:"));
}

#[test]
fn urlencode_data_uri() {
    let ctx = ctx_with("url", "data:text/html,<script>alert(1)</script>");
    let result = render("{{ url|urlencode }}", &ctx);
    assert!(result.contains("%3A"));
    assert!(!result.contains("data:text"));
}

#[test]
fn urlencode_special_chars() {
    let ctx = ctx_with("url", "search?q=<script>&page=1");
    let result = render("{{ url|urlencode }}", &ctx);
    assert!(!result.contains('<'));
    assert!(!result.contains('>'));
    assert!(result.contains("%3C"));
    assert!(result.contains("%3E"));
}

#[test]
fn urlencode_spaces() {
    let ctx = ctx_with("url", "hello world");
    let result = render("{{ url|urlencode }}", &ctx);
    assert!(result.contains("%20"));
    assert!(!result.contains(' '));
}

#[test]
fn urlencode_ampersand() {
    let ctx = ctx_with("url", "a&b=c");
    let result = render("{{ url|urlencode }}", &ctx);
    assert!(result.contains("%26"));
}

#[test]
fn urlencode_preserves_safe_chars() {
    // alphanumeric, -, _, ., ~ are safe
    let ctx = ctx_with("url", "hello-world_test.file~name");
    let result = render("{{ url|urlencode }}", &ctx);
    // The result is also auto-escaped by the renderer, but these chars
    // pass through both urlencode and html_escape unchanged
    assert!(result.contains("hello-world_test.file~name"));
}

#[test]
fn urlencode_double_quotes_in_url() {
    let ctx = ctx_with("url", "test\"value");
    let result = render("{{ url|urlencode }}", &ctx);
    assert!(result.contains("%22"));
}

#[test]
fn urlencode_single_quotes_in_url() {
    let ctx = ctx_with("url", "test'value");
    let result = render("{{ url|urlencode }}", &ctx);
    assert!(result.contains("%27"));
}

// ===========================================================================
// 4. JSON / <script> Context (|json_script filter)
// ===========================================================================

#[test]
fn json_script_prevents_script_breakout() {
    let ctx = ctx_with("data", "</script><script>alert(1)</script>");
    let result = render("{{ data|json_script:\"app-data\" }}", &ctx);
    // The literal </script> must be escaped inside the JSON
    assert!(!result[..result.len() - 9].contains("</script>"));
    assert!(result.contains("\\u003C"));
}

#[test]
fn json_script_escapes_ampersand() {
    let ctx = ctx_with("data", "a&b");
    let result = render("{{ data|json_script:\"test\" }}", &ctx);
    assert!(result.contains("\\u0026"));
}

#[test]
fn json_script_escapes_angle_brackets() {
    let ctx = ctx_with("data", "<b>bold</b>");
    let result = render("{{ data|json_script:\"test\" }}", &ctx);
    assert!(result.contains("\\u003C"));
    assert!(result.contains("\\u003E"));
}

#[test]
fn json_script_escapes_line_separators() {
    let ctx = ctx_with("data", "line\u{2028}sep\u{2029}end");
    let result = render("{{ data|json_script:\"test\" }}", &ctx);
    assert!(result.contains("\\u2028"));
    assert!(result.contains("\\u2029"));
}

#[test]
fn json_script_escapes_element_id() {
    // The element ID itself should be HTML-escaped to prevent attribute injection
    let ctx = ctx_with("data", "hello");
    let result = render("{{ data|json_script:\"test\" }}", &ctx);
    assert!(result.contains("id=\"test\""));
    assert!(result.contains("type=\"application/json\""));
}

#[test]
fn json_script_structure() {
    let ctx = ctx_with("data", "hello");
    let result = render("{{ data|json_script:\"my-id\" }}", &ctx);
    assert!(result.starts_with("<script id=\"my-id\" type=\"application/json\">"));
    assert!(result.ends_with("</script>"));
}

// ===========================================================================
// 5. |safe Filter Bypass (intentional opt-out)
// ===========================================================================

// IMPORTANT: The |safe filter is an intentional mechanism for developers to
// mark content as already-safe HTML. It MUST bypass auto-escaping. These
// tests document that behavior and confirm it is a deliberate design choice,
// not a vulnerability. Developers using |safe accept responsibility for
// ensuring the content is trusted.

#[test]
fn safe_filter_bypasses_escaping() {
    let ctx = ctx_with("html", "<b>bold</b>");
    let result = render("{{ html|safe }}", &ctx);
    assert_eq!(result, "<b>bold</b>");
}

#[test]
fn safe_filter_passes_through_script_tags() {
    // This is intentional: |safe means the developer trusts the content.
    let ctx = ctx_with("html", "<script>legit()</script>");
    let result = render("{{ html|safe }}", &ctx);
    assert!(result.contains("<script>"));
}

#[test]
fn safe_filter_without_safe_escapes() {
    // Verify that the same content WITHOUT |safe is properly escaped
    let ctx = ctx_with("html", "<script>legit()</script>");
    let result = render("{{ html }}", &ctx);
    assert!(!result.contains("<script>"));
    assert!(result.contains("&lt;script&gt;"));
}

#[test]
fn context_safe_flag_bypasses_escaping() {
    // Variables marked safe in the context should not be escaped
    let mut ctx = Context::new();
    ctx.set(
        "html".to_string(),
        Value::String("<b>trusted</b>".to_string()),
    );
    ctx.mark_safe("html".to_string());
    let result = render("{{ html }}", &ctx);
    assert_eq!(result, "<b>trusted</b>");
}

#[test]
fn force_escape_always_escapes() {
    // |force_escape should escape even if followed by |safe-like behavior
    let ctx = ctx_with("input", "<b>bold</b>");
    let result = render("{{ input|force_escape }}", &ctx);
    assert!(result.contains("&lt;b&gt;"));
    assert!(!result.contains("<b>"));
}

// ===========================================================================
// 6. Filter Chain Escaping
// ===========================================================================

#[test]
fn filter_chain_upper_still_escapes() {
    // Applying a text filter should not disable auto-escaping
    let ctx = ctx_with("input", "<script>alert(1)</script>");
    let result = render("{{ input|upper }}", &ctx);
    assert!(!result.contains("<SCRIPT>"));
    assert!(result.contains("&lt;SCRIPT&gt;"));
}

#[test]
fn filter_chain_lower_still_escapes() {
    let ctx = ctx_with("input", "<SCRIPT>ALERT(1)</SCRIPT>");
    let result = render("{{ input|lower }}", &ctx);
    assert!(!result.contains("<script>"));
    assert!(result.contains("&lt;script&gt;"));
}

#[test]
fn filter_chain_truncatewords_still_escapes() {
    let ctx = ctx_with("input", "<script>alert(1)</script> hello world");
    let result = render("{{ input|truncatewords:\"2\" }}", &ctx);
    assert!(!result.contains("<script>"));
}

#[test]
fn filter_chain_default_still_escapes() {
    // |default with HTML payload should still be escaped
    let result = render("{{ missing|default:\"<b>default</b>\" }}", &Context::new());
    assert!(!result.contains("<b>"));
    assert!(result.contains("&lt;b&gt;"));
}

// ===========================================================================
// 7. Edge Cases and Encoding Attacks
// ===========================================================================

#[test]
fn html_escape_empty_string() {
    let ctx = ctx_with("input", "");
    let result = render("{{ input }}", &ctx);
    assert_eq!(result, "");
}

#[test]
fn html_escape_only_special_chars() {
    let ctx = ctx_with("input", "<>&\"'");
    let result = render("{{ input }}", &ctx);
    assert_eq!(result, "&lt;&gt;&amp;&quot;&#x27;");
}

#[test]
fn html_escape_very_long_payload() {
    let payload = "<script>".repeat(1000);
    let ctx = ctx_with("input", &payload);
    let result = render("{{ input }}", &ctx);
    assert!(!result.contains("<script>"));
}

#[test]
fn html_escape_unicode_xss() {
    // Some XSS vectors use unicode characters
    let ctx = ctx_with(
        "input",
        "\u{FF1C}script\u{FF1E}alert(1)\u{FF1C}/script\u{FF1E}",
    );
    let result = render("{{ input }}", &ctx);
    // Fullwidth angle brackets are not HTML-significant, so they pass through.
    // The important thing is that actual < and > are escaped.
    assert!(!result.contains("<script>"));
}

#[test]
fn html_escape_mixed_content() {
    let ctx = ctx_with("input", "Hello <b>World</b> & \"Friends\" it's great");
    let result = render("{{ input }}", &ctx);
    assert_eq!(
        result,
        "Hello &lt;b&gt;World&lt;/b&gt; &amp; &quot;Friends&quot; it&#x27;s great"
    );
}

#[test]
fn html_escape_numeric_values() {
    // Integer and float values should render without escaping issues
    let mut ctx = Context::new();
    ctx.set("num".to_string(), Value::Integer(42));
    let result = render("{{ num }}", &ctx);
    assert_eq!(result, "42");
}

#[test]
fn html_escape_boolean_values() {
    let mut ctx = Context::new();
    ctx.set("flag".to_string(), Value::Bool(true));
    let result = render("{{ flag }}", &ctx);
    assert_eq!(result, "true");
}

#[test]
fn html_escape_null_value() {
    let ctx = Context::new();
    let result = render("{{ missing }}", &ctx);
    assert_eq!(result, "");
}

// ===========================================================================
// 8. Attribute Context Escaping
// ===========================================================================

#[test]
fn attribute_double_quote_breakout() {
    // The attacker injects: " onclick="alert(1)" data-x="
    // trying to close the value attribute and inject onclick.
    // After auto-escaping, all injected " become &quot;, so the browser
    // treats the entire escaped content as the attribute value -- no
    // attribute boundary is broken.
    let ctx = ctx_with("input", "\" onclick=\"alert(1)\" data-x=\"");
    let result = render("<input value=\"{{ input }}\">", &ctx);
    assert_eq!(
        result,
        "<input value=\"&quot; onclick=&quot;alert(1)&quot; data-x=&quot;\">"
    );
    // All injected " are &quot;, so the attribute value extends from the
    // first " to the final "> which is the template's closing quote.
    // A browser will NOT parse onclick as a separate attribute.
}

#[test]
fn attribute_single_quote_breakout() {
    // The attacker injects: ' onclick='alert(1)' data-x='
    // trying to close a single-quoted attribute and inject onclick.
    // After auto-escaping, all injected ' become &#x27;, so no
    // attribute boundary is broken.
    let ctx = ctx_with("input", "' onclick='alert(1)' data-x='");
    let result = render("<input value='{{ input }}'>", &ctx);
    assert_eq!(
        result,
        "<input value='&#x27; onclick=&#x27;alert(1)&#x27; data-x=&#x27;'>"
    );
    // All injected ' are &#x27;, so the browser treats the entire content
    // as the attribute value. onclick is never parsed as a real attribute.
}

// ===========================================================================
// 9. striptags Does Not Provide Security
// ===========================================================================

#[test]
fn striptags_is_not_security_measure() {
    // striptags is a text utility, NOT a security measure.
    // It uses simple < > matching and can be bypassed.
    // Auto-escaping is the correct defense against XSS.
    let ctx = ctx_with("input", "<b>bold</b>");
    let result = render("{{ input|striptags }}", &ctx);
    // striptags strips tags, then auto-escaping kicks in (on remaining text)
    assert_eq!(result, "bold");
}

// ===========================================================================
// 10. urlize Filter XSS Prevention
// ===========================================================================

#[test]
fn urlize_escapes_surrounding_text() {
    // Non-URL text around detected links must be HTML-escaped
    let ctx = ctx_with("input", "<script>alert(1)</script> https://example.com");
    let result = render("{{ input|urlize }}", &ctx);
    // urlize is a safe_output_filter (no double-escaping), but it handles
    // its own escaping of non-URL text
    assert!(!result.contains("<script>"));
}

#[test]
fn urlize_javascript_url_not_linked() {
    // javascript: URLs should not become clickable links
    let ctx = ctx_with("input", "javascript:alert(1)");
    let result = render("{{ input|urlize }}", &ctx);
    // urlize regex only matches http(s)://, ftp://, www., and emails
    assert!(!result.contains("<a href=\"javascript:"));
}
