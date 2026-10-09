//! Linear byte editing for Python's tracked page composition (#3252).
use pyo3::prelude::*;
type Origins = Vec<(usize, usize, usize, String)>;
type Output = (String, Vec<(usize, usize)>, Origins);

fn apply(
    html: &str,
    spans: &[(usize, usize)],
    origins: &Origins,
    edits: &[(usize, usize, &str)],
) -> Output {
    let mut output = String::with_capacity(html.len());
    let mut ranges = Vec::new();
    let mut cursor = 0;
    for &(start, end, value) in edits {
        ranges.push((cursor, start, output.len()));
        output.push_str(&html[cursor..start]);
        output.push_str(value);
        cursor = end;
    }
    ranges.push((cursor, html.len(), output.len()));
    output.push_str(&html[cursor..]);
    let mut mapped = Vec::new();
    let mut mapped_origins = Vec::new();
    let mut si = 0;
    let mut oi = 0;
    for (start, end, offset) in ranges {
        while si < spans.len() && spans[si].1 <= start {
            si += 1;
        }
        let mut i = si;
        while i < spans.len() && spans[i].0 < end {
            let (a, b) = spans[i];
            let left = a.max(start);
            let right = b.min(end);
            if left < right {
                mapped.push((offset + left - start, offset + right - start));
            }
            i += 1;
        }
        while oi < origins.len() && origins[oi].1 <= start {
            oi += 1;
        }
        let mut i = oi;
        while i < origins.len() && origins[i].0 < end {
            let (a, b, source, id) = &origins[i];
            let left = (*a).max(start);
            let right = (*b).min(end);
            if left < right {
                mapped_origins.push((
                    offset + left - start,
                    offset + right - start,
                    source + left - a,
                    id.clone(),
                ));
            }
            i += 1;
        }
    }
    (output, mapped, mapped_origins)
}

#[pyfunction]
pub fn normalize_provenance_whitespace(
    html: &str,
    spans: Vec<(usize, usize)>,
    origins: Origins,
) -> Output {
    let bytes = html.as_bytes();
    let mut edits = Vec::new();
    let mut i = 0;
    while i < bytes.len() {
        if matches!(bytes[i], b' ' | b'\t' | b'\n' | b'\r' | 12) {
            let start = i;
            while i < bytes.len() && matches!(bytes[i], b' ' | b'\t' | b'\n' | b'\r' | 12) {
                i += 1;
            }
            if &html[start..i] != " " {
                edits.push((start, i, " "));
            }
        } else {
            i += 1;
        }
    }
    apply(html, &spans, &origins, &edits)
}
#[pyfunction]
pub fn collapse_provenance_whitespace(
    html: &str,
    spans: Vec<(usize, usize)>,
    origins: Origins,
    block_tags: Vec<String>,
) -> Output {
    let edits: Vec<_> = djust_core::html_whitespace::inter_tag_whitespace_edits(html, &block_tags)
        .into_iter()
        .map(|(a, b)| (a, b, ""))
        .collect();
    apply(html, &spans, &origins, &edits)
}

#[pyfunction]
pub fn lazy_authority_spans(html: &str, spans: Vec<(usize, usize)>) -> Vec<(usize, usize)> {
    djust_vdom::lazy_provenance::authority_spans(html, &spans)
}
