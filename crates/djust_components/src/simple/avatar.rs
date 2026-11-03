use pyo3::prelude::*;

#[pyclass(name = "RustAvatar")]
pub struct RustAvatar {
    src: Option<String>,
    alt: String,
    initials: Option<String>,
    size: String,
    shape: String,
    status: Option<String>,
}

#[pymethods]
impl RustAvatar {
    #[new]
    #[pyo3(signature = (src=None, alt="", initials=None, size="md", shape="circle", status=None))]
    pub fn new(
        src: Option<String>,
        alt: &str,
        initials: Option<String>,
        size: &str,
        shape: &str,
        status: Option<String>,
    ) -> Self {
        // Limit initials to 2 characters and uppercase
        let initials = initials.map(|s| s.chars().take(2).collect::<String>().to_uppercase());

        Self {
            src,
            alt: alt.to_string(),
            initials,
            size: size.to_string(),
            shape: shape.to_string(),
            status,
        }
    }

    /// Render avatar to HTML string (Bootstrap 5)
    pub fn render(&self) -> String {
        // Size mapping
        let size_style = match self.size.as_str() {
            "xs" => "width: 1.5rem; height: 1.5rem;",
            "sm" => "width: 2rem; height: 2rem;",
            "md" => "width: 3rem; height: 3rem;",
            "lg" => "width: 4rem; height: 4rem;",
            "xl" => "width: 6rem; height: 6rem;",
            _ => "width: 3rem; height: 3rem;",
        };

        // Shape class
        let shape_class = if self.shape == "circle" {
            "rounded-circle"
        } else {
            "rounded"
        };

        let mut html =
            format!(r#"<div class="position-relative d-inline-block" style="{size_style}">"#);

        // Image or initials
        if let Some(ref src) = self.src {
            html.push_str(&format!(
                r#"
    <img src="{}" alt="{}" class="w-100 h-100 object-fit-cover {}">"#,
                image_src_attr(src),
                html_escape(&self.alt),
                shape_class
            ));
        } else if let Some(ref initials) = self.initials {
            let initials = html_escape(initials);
            html.push_str(&format!(
                r#"
    <div class="w-100 h-100 bg-primary text-white d-flex align-items-center justify-content-center {shape_class}">
        <span class="fw-bold">{initials}</span>
    </div>"#
            ));
        }

        // Status indicator
        if let Some(ref status) = self.status {
            let status_class = match status.as_str() {
                "online" => "bg-success",
                "offline" => "bg-secondary",
                "busy" => "bg-danger",
                "away" => "bg-warning",
                _ => "bg-secondary",
            };

            html.push_str(&format!(
                r#"
    <span class="position-absolute bottom-0 end-0 p-1 {status_class} border border-white rounded-circle"></span>"#
            ));
        }

        html.push_str("\n</div>");
        html
    }

    pub fn __str__(&self) -> String {
        self.render()
    }
}

#[inline]
fn html_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#x27;")
}

/// URL schemes an image source may use besides relative / scheme-less URLs.
const ALLOWED_URL_SCHEMES: [&str; 6] = ["http", "https", "mailto", "tel", "ftp", "ftps"];

/// Prepare a URL for a quoted `<img src>` attribute.
///
/// Mirrors `djust.components.utils.url_attr(value, image=True)`: the value is
/// trimmed and HTML-escaped; an inline `data:image/...` URI is kept; any other
/// URL whose scheme is not a plain navigation scheme (`javascript:`,
/// `vbscript:`, `data:` and so on, including variants padded with control
/// characters or whitespace) becomes `#`.
fn image_src_attr(value: &str) -> String {
    let s = value.trim();
    if s.is_empty() {
        return String::new();
    }
    // Browsers ignore ASCII control characters and spaces when reading a scheme.
    let probe: String = s
        .chars()
        .filter(|c| (*c as u32) > 0x20)
        .collect::<String>()
        .to_ascii_lowercase();
    if is_data_image(&probe) {
        return html_escape(s);
    }
    if probe.starts_with("javascript:")
        || probe.starts_with("vbscript:")
        || probe.starts_with("data:")
    {
        return "#".to_string();
    }
    if let Some(scheme) = url_scheme(s) {
        if !ALLOWED_URL_SCHEMES.contains(&scheme.as_str()) {
            return "#".to_string();
        }
    }
    html_escape(s)
}

/// `^data:image/[a-z0-9.+-]+[;,]` on an already lowercased value.
fn is_data_image(probe: &str) -> bool {
    let Some(rest) = probe.strip_prefix("data:image/") else {
        return false;
    };
    let subtype_len = rest
        .chars()
        .take_while(|c| {
            c.is_ascii_lowercase() || c.is_ascii_digit() || matches!(c, '.' | '+' | '-')
        })
        .count();
    subtype_len > 0 && matches!(rest[subtype_len..].chars().next(), Some(';') | Some(','))
}

/// The lowercased scheme of `s`, parsed the way `urllib.parse.urlsplit` does:
/// leading C0 control characters and space (`U+0000`..=`U+0020`) are stripped,
/// then tab / CR / LF are removed everywhere, then the scheme is a letter followed
/// by letters, digits, `+`, `-`, `.` before the first `:`.
///
/// The leading strip is what makes `"\x01blob:..."` read as `blob`, as it does for
/// Python and for a browser (#3748); without it the control character fails the
/// leading-letter test and the value is taken to have no scheme at all.
fn url_scheme(s: &str) -> Option<String> {
    let cleaned: String = s
        .trim_start_matches(|c: char| (c as u32) <= 0x20)
        .chars()
        .filter(|c| !matches!(c, '\t' | '\r' | '\n'))
        .collect();
    let colon = cleaned.find(':')?;
    let candidate = &cleaned[..colon];
    let mut chars = candidate.chars();
    let first = chars.next()?;
    if !first.is_ascii_alphabetic() {
        return None;
    }
    if !chars.all(|c| c.is_ascii_alphanumeric() || matches!(c, '+' | '-' | '.')) {
        return None;
    }
    Some(candidate.to_ascii_lowercase())
}

#[cfg(test)]
mod escaping_tests {
    use super::*;

    fn img(src: &str) -> String {
        RustAvatar::new(Some(src.to_string()), "", None, "md", "circle", None).render()
    }

    #[test]
    fn src_with_non_navigation_scheme_becomes_hash() {
        for src in [
            "javascript:alert(1)",
            "JavaScript:alert(1)",
            " java\tscript:alert(1)",
            "\u{1}javascript:alert(1)",
            "vbscript:msgbox(1)",
            "data:text/html,<b>x</b>",
            "custom-scheme:foo",
        ] {
            let html = img(src);
            assert!(html.contains(r##"<img src="#" "##), "{src:?} -> {html}");
        }
    }

    /// #3748: a leading C0 control character other than tab / CR / LF made `url_scheme`
    /// read "no scheme" (the control character fails the leading-letter test), so a
    /// non-navigation scheme reached `<img src>` raw. Python's `urlsplit` (and a
    /// browser) strip every leading C0 control and space first.
    #[test]
    fn leading_c0_control_does_not_hide_a_non_navigation_scheme() {
        let controls: Vec<char> = (0u32..=0x20).filter_map(char::from_u32).collect();
        for scheme in [
            "blob:https://e.com/x",
            "file:///etc/x",
            "intent://e.com/x#Intent;end",
        ] {
            for c in &controls {
                for src in [
                    format!("{c}{scheme}"),
                    format!("{c}{c}{scheme}"),
                    format!(" {c} {scheme}"),
                    format!("{c}{}", scheme.to_ascii_uppercase()),
                ] {
                    let html = img(&src);
                    assert!(html.contains(r##"<img src="#" "##), "{src:?} -> {html}");
                }
            }
        }
    }

    #[test]
    fn leading_c0_control_is_only_stripped_to_read_the_scheme() {
        // a navigation URL behind a leading control is still a navigation URL, and a
        // relative one is still relative; the value itself is printed as given
        assert!(img("\u{1}https://e.com/a.png").contains("https://e.com/a.png"));
        assert!(img("\u{1}/rel/a.png").contains("/rel/a.png"));
        assert_eq!(url_scheme("\u{1}blob:x").as_deref(), Some("blob"));
        assert_eq!(url_scheme("\u{1} \t\nBlob:x").as_deref(), Some("blob"));
        // only LEADING controls are stripped, and only up to U+0020
        assert_eq!(url_scheme("a\u{1}b:x"), None);
        assert_eq!(url_scheme("!blob:x"), None);
    }

    #[test]
    fn src_with_normal_url_is_kept_and_escaped() {
        assert!(img("/path?a=1&b=2").contains(r#"src="/path?a=1&amp;b=2""#));
        assert!(img("https://example.com/a.png").contains(r#"src="https://example.com/a.png""#));
        assert!(img("a.png").contains(r#"src="a.png""#));
        assert!(img("x\" onerror=\"y").contains(r#"src="x&quot; onerror=&quot;y""#));
    }

    #[test]
    fn inline_image_data_uri_is_kept() {
        let html = img("data:image/png;base64,iVBORw0KGgo=");
        assert!(html.contains(r#"src="data:image/png;base64,iVBORw0KGgo=""#));
        assert!(img("data:image/svg+xml,<svg>").contains(r#"src="data:image/svg+xml,&lt;svg&gt;""#));
    }

    #[test]
    fn initials_and_alt_are_html_escaped() {
        let html = RustAvatar::new(None, "", Some("<i".to_string()), "md", "circle", None).render();
        assert!(html.contains("<span class=\"fw-bold\">&lt;I</span>"));
        let html = RustAvatar::new(
            Some("a.png".to_string()),
            "x\" onerror=\"y",
            None,
            "md",
            "circle",
            None,
        )
        .render();
        assert!(html.contains("alt=\"x&quot; onerror=&quot;y\""));
    }

    #[test]
    fn plain_output_unchanged() {
        let html = RustAvatar::new(
            None,
            "",
            Some("jd".to_string()),
            "sm",
            "circle",
            Some("online".to_string()),
        )
        .render();
        assert!(html.contains("<span class=\"fw-bold\">JD</span>"));
        let html = img("/media/u.png");
        assert!(html.contains(
            r#"<img src="/media/u.png" alt="" class="w-100 h-100 object-fit-cover rounded-circle">"#
        ));
    }
}
