/*!
Type-safe HTML builder for components.

Provides a fluent API for building HTML without string concatenation or XSS risks.
*/

use std::fmt::Write as _;

/// Type-safe HTML builder
pub struct HtmlBuilder {
    output: String,
}

impl HtmlBuilder {
    /// Create a new HTML builder
    pub fn new() -> Self {
        Self {
            output: String::with_capacity(256),
        }
    }

    /// Start a tag
    pub fn start_tag(mut self, tag: &str) -> Self {
        write!(self.output, "<{tag}").unwrap();
        self
    }

    /// Add an attribute
    pub fn attr(mut self, key: &str, value: &str) -> Self {
        let escaped = html_escape(value);
        write!(self.output, " {key}=\"{escaped}\"").unwrap();
        self
    }

    /// Add a conditional attribute
    pub fn attr_if(self, condition: bool, key: &str, value: &str) -> Self {
        if condition {
            self.attr(key, value)
        } else {
            self
        }
    }

    /// Add a class
    pub fn class(mut self, class: &str) -> Self {
        if !class.is_empty() {
            write!(self.output, " class=\"{}\"", html_escape(class)).unwrap();
        }
        self
    }

    /// Add multiple classes
    pub fn classes(self, classes: &[&str]) -> Self {
        self.class(&classes.join(" "))
    }

    /// Add conditional class
    pub fn class_if(self, condition: bool, class: &str) -> Self {
        if condition {
            self.class(class)
        } else {
            self
        }
    }

    /// Add an ID
    pub fn id(mut self, id: &str) -> Self {
        if !id.is_empty() {
            write!(self.output, " id=\"{}\"", html_escape(id)).unwrap();
        }
        self
    }

    /// Add inline styles
    pub fn style(mut self, style: &str) -> Self {
        if !style.is_empty() {
            write!(self.output, " style=\"{}\"", html_escape(style)).unwrap();
        }
        self
    }

    /// Add a data attribute
    pub fn data(mut self, key: &str, value: &str) -> Self {
        write!(self.output, " data-{}=\"{}\"", key, html_escape(value)).unwrap();
        self
    }

    /// Close the opening tag
    pub fn close_start(mut self) -> Self {
        self.output.push('>');
        self
    }

    /// Add text content (HTML-escaped)
    pub fn text(mut self, text: &str) -> Self {
        self.output.push_str(&html_escape(text));
        self
    }

    /// Add raw HTML (NOT escaped - use with caution!)
    pub fn raw(mut self, html: &str) -> Self {
        self.output.push_str(html);
        self
    }

    /// Close a tag
    pub fn end_tag(mut self, tag: &str) -> Self {
        write!(self.output, "</{tag}>").unwrap();
        self
    }

    /// Add a self-closing tag
    pub fn self_closing_tag(mut self, tag: &str) -> Self {
        write!(self.output, "<{tag} />").unwrap();
        self
    }

    /// Build the final HTML string
    pub fn build(self) -> String {
        self.output
    }
}

impl Default for HtmlBuilder {
    fn default() -> Self {
        Self::new()
    }
}

/// Helper to create a complete element
pub fn element(tag: &str) -> ElementBuilder {
    ElementBuilder {
        tag: tag.to_string(),
        attrs: Vec::new(),
        classes: Vec::new(),
        children: Vec::new(),
        self_closing: false,
    }
}

/// Builder for complete HTML elements
pub struct ElementBuilder {
    tag: String,
    attrs: Vec<(String, String)>,
    classes: Vec<String>,
    children: Vec<String>,
    self_closing: bool,
}

impl ElementBuilder {
    pub fn attr(mut self, key: impl Into<String>, value: impl Into<String>) -> Self {
        self.attrs.push((key.into(), value.into()));
        self
    }

    pub fn class(mut self, class: impl Into<String>) -> Self {
        self.classes.push(class.into());
        self
    }

    pub fn classes(mut self, classes: impl IntoIterator<Item = impl Into<String>>) -> Self {
        self.classes.extend(classes.into_iter().map(|c| c.into()));
        self
    }

    pub fn child(mut self, html: impl Into<String>) -> Self {
        self.children.push(html.into());
        self
    }

    pub fn text(mut self, text: impl AsRef<str>) -> Self {
        self.children.push(html_escape(text.as_ref()));
        self
    }

    pub fn self_closing(mut self) -> Self {
        self.self_closing = true;
        self
    }

    pub fn build(self) -> String {
        let mut html = String::with_capacity(256);

        html.push('<');
        html.push_str(&self.tag);

        if !self.classes.is_empty() {
            html.push_str(" class=\"");
            html.push_str(&html_escape(&self.classes.join(" ")));
            html.push('"');
        }

        for (key, value) in &self.attrs {
            write!(html, " {}=\"{}\"", key, html_escape(value)).unwrap();
        }

        if self.self_closing {
            html.push_str(" />");
            return html;
        }

        html.push('>');

        for child in &self.children {
            html.push_str(child);
        }

        write!(html, "</{}>", self.tag).unwrap();
        html
    }
}

/// Escape HTML special characters
pub(crate) fn html_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#39;")
}

/// Escape a value for use inside a single- or double-quoted JavaScript string
/// literal. Combine with [`html_escape`] when the script sits in an attribute.
pub(crate) fn js_string_escape(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\'' => out.push_str("\\'"),
            '"' => out.push_str("\\\""),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '<' => out.push_str("\\u003C"),
            '>' => out.push_str("\\u003E"),
            '\u{2028}' => out.push_str("\\u2028"),
            '\u{2029}' => out.push_str("\\u2029"),
            _ => out.push(c),
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_html_escape() {
        assert_eq!(html_escape("<script>"), "&lt;script&gt;");
        assert_eq!(html_escape("a & b"), "a &amp; b");
        assert_eq!(html_escape("\"quoted\""), "&quot;quoted&quot;");
    }

    #[test]
    fn test_html_builder() {
        let html = HtmlBuilder::new()
            .start_tag("div")
            .class("container")
            .id("main")
            .close_start()
            .text("Hello World")
            .end_tag("div")
            .build();

        assert_eq!(
            html,
            "<div class=\"container\" id=\"main\">Hello World</div>"
        );
    }

    #[test]
    fn test_element_builder() {
        let html = element("button")
            .class("btn")
            .class("btn-primary")
            .attr("type", "button")
            .text("Click me")
            .build();

        assert_eq!(
            html,
            "<button class=\"btn btn-primary\" type=\"button\">Click me</button>"
        );
    }

    #[test]
    fn test_self_closing() {
        let html = element("input")
            .attr("type", "text")
            .attr("placeholder", "Enter text")
            .self_closing()
            .build();

        assert_eq!(html, "<input type=\"text\" placeholder=\"Enter text\" />");
    }
}

#[cfg(test)]
mod escaping_tests {
    use super::*;

    #[test]
    fn class_and_id_values_are_escaped() {
        let html = HtmlBuilder::new()
            .start_tag("div")
            .class("a\" onmouseover=\"y")
            .id("i\"><img src=x>")
            .close_start()
            .end_tag("div")
            .build();
        assert!(!html.contains("a\" onmouseover"));
        assert!(!html.contains("<img"));
        assert!(html.contains("class=\"a&quot; onmouseover=&quot;y\""));

        let html = element("span").class("c\"><img src=x>").build();
        assert!(!html.contains("<img"));
        assert!(html.contains("class=\"c&quot;&gt;&lt;img src=x&gt;\""));
    }

    #[test]
    fn js_string_values_are_quoted() {
        assert_eq!(js_string_escape("plain-id_1"), "plain-id_1");
        assert_eq!(
            js_string_escape("a'b\"c\\d\ne</"),
            "a\\'b\\\"c\\\\d\\ne\\u003C/"
        );
    }
}
