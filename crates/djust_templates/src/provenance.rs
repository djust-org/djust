//! Result-bound authored UTF-8 byte intervals. No ambient collector (#3252).
use std::ops::{Deref, Range};

/// Output operations shared by the ordinary and provenance-aware renderer.
/// Monomorphization keeps interval allocation and copying out of plain renders.
pub trait RenderOutput: Default + From<String> + Deref<Target = str> + std::fmt::Display {
    const TRACKED: bool;
    fn authored(text: &str) -> Self;
    fn from_authored_output(output: djust_core::context::AuthoredOutput) -> Self;
    fn append(&mut self, child: &Self);
    fn push_str(&mut self, text: &str);
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
}
impl From<String> for Rendered {
    fn from(html: String) -> Self {
        Self {
            html,
            authored: Vec::new(),
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
            authored: if text.is_empty() {
                Vec::new()
            } else {
                vec![0..text.len()]
            },
        }
    }
    fn from_authored_output(output: djust_core::context::AuthoredOutput) -> Self {
        Self {
            html: output.0,
            authored: output.1.into_iter().map(|(a, b)| a..b).collect(),
        }
    }
    fn append(&mut self, child: &Self) {
        let offset = self.html.len();
        self.authored.extend(
            child
                .authored
                .iter()
                .map(|r| r.start + offset..r.end + offset),
        );
        self.html.push_str(&child.html);
    }
    fn push_str(&mut self, text: &str) {
        self.html.push_str(text);
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
