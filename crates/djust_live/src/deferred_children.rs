//! Materialize children only after authenticating their page-root location.
use djust_templates::provenance::{DualRendered, Rendered};
use pyo3::prelude::*;

type DeferredEdit = (usize, usize, String, Option<(String, usize)>);

thread_local! {
    static ENABLED: std::cell::Cell<bool> = const { std::cell::Cell::new(false) };
}

/// Scoped by the synchronous Python renderer; never persisted in snapshots.
pub fn set_scope(enabled: bool) -> bool {
    ENABLED.with(|value| value.replace(enabled))
}

/// A replacement is opaque output: existing literal projections remain intact,
/// while source intervals and opening offsets follow the exact byte edits.
fn apply_to(rendered: &mut Rendered, html: &str, edits: &[(usize, usize, &str)]) {
    let spans: Vec<_> = rendered.authored.iter().map(|r| (r.start, r.end)).collect();
    let (value, spans, origins) =
        super::provenance_edits::apply(html, &spans, &rendered.origins, edits);
    let map = |position: usize| {
        let mut mapped = position;
        for &(start, end, value) in edits {
            if position < start {
                break;
            }
            if position < end {
                return None;
            }
            mapped = mapped - (end - start) + value.len();
        }
        Some(mapped)
    };
    rendered
        .literal_offsets
        .retain_mut(|(position, _)| match map(*position) {
            Some(value) => {
                *position = value;
                true
            }
            None => false,
        });
    rendered
        .blank_offsets
        .retain_mut(|(position, _)| match map(*position) {
            Some(value) => {
                *position = value;
                true
            }
            None => false,
        });
    rendered.html = value;
    rendered.authored = spans.into_iter().map(|(a, b)| a..b).collect();
    rendered.origins = origins;
}

pub fn materialize(
    output: &mut DualRendered,
    possible_partial_root: bool,
) -> PyResult<Vec<(usize, usize, String)>> {
    if !ENABLED.with(std::cell::Cell::get) {
        return Ok(Vec::new());
    }
    Python::attach(|py| {
        let mut issued: Vec<(usize, usize, String)> = Vec::new();
        let module = py.import("djust._deferred_children")?;
        while module
            .getattr("deferred_children_pending")?
            .call0()?
            .extract::<bool>()?
        {
            let mut proof = if output.prefix.literal_blocked {
                let mut proof = output.prefix.clone();
                proof.html = output.normal.html.clone();
                proof
            } else {
                output.normal.clone()
            };
            proof.retain_live_authority();
            let spans: Vec<_> = proof.authored.iter().map(|r| (r.start, r.end)).collect();
            // This parse must not consume IDs from the actual delivered VDOM.
            let saved = djust_vdom::get_id_counter();
            let root = djust_vdom::lazy_provenance::authenticated_live_root(&proof.html, &spans);
            djust_vdom::set_id_counter(saved);
            let page_root = match root {
                Ok(_) => true,
                Err(error) => {
                    if djust_vdom::lazy_provenance::authenticated_fragment_model_fields(
                        &proof.html,
                        &spans,
                    )
                    .is_err()
                    {
                        return Err(super::authenticated_root_error(error));
                    }
                    let projected_root = proof.literal_blocked
                        || proof.literal_only.is_none()
                        || proof.blank_only.is_none()
                        || [&proof.literal_only, &proof.blank_only]
                            .into_iter()
                            .flatten()
                            .any(|html| {
                                !html.is_empty()
                                && djust_vdom::lazy_provenance::authenticated_fragment_model_fields(
                                    html,
                                    &[(0, html.len())],
                                )
                                .is_err()
                            });
                    if possible_partial_root || projected_root {
                        return Err(super::authenticated_root_error(error));
                    }
                    false
                }
            };
            // The Python ledger validates every private occurrence before invoking
            // a mount. It returns replacements only; it cannot grant source proof.
            let edits: Vec<DeferredEdit> = module
                .getattr("materialize_deferred_children")?
                .call1((&output.normal.html, page_root))?
                .extract()?;
            if edits.len() != 1 {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "invalid deferred child progress",
                ));
            }
            let html = output.normal.html.clone();
            let mut cursor = 0;
            for (start, end, _, _) in &edits {
                if *start < cursor
                    || *start > *end
                    || *end > html.len()
                    || !html.is_char_boundary(*start)
                    || !html.is_char_boundary(*end)
                {
                    return Err(pyo3::exceptions::PyValueError::new_err(
                        "invalid deferred child byte edits",
                    ));
                }
                cursor = *end;
            }
            let (start, end, text, child) = &edits[0];
            for (a, b, _) in &mut issued {
                if *b <= *start {
                    continue;
                }
                if *a < *end {
                    return Err(pyo3::exceptions::PyValueError::new_err(
                        "overlapping issued child opening",
                    ));
                }
                *a = *a - (end - start) + text.len();
                *b = *b - (end - start) + text.len();
            }
            if let Some((key, opening_end)) = child {
                if *opening_end == 0
                    || *opening_end > text.len()
                    || !text.is_char_boundary(*opening_end)
                {
                    return Err(pyo3::exceptions::PyValueError::new_err(
                        "invalid issued child opening",
                    ));
                }
                issued.push((*start, start + opening_end, key.clone()));
            }
            let edits: Vec<_> = edits
                .iter()
                .map(|(a, b, text, _)| (*a, *b, text.as_str()))
                .collect();
            let observed: Vec<_> = output.observed.iter().map(|r| (r.start, r.end)).collect();
            let (_, observed, _) =
                super::provenance_edits::apply(&html, &observed, &Vec::new(), &edits);
            output.observed = observed.into_iter().map(|(a, b)| a..b).collect();
            apply_to(&mut output.normal, &html, &edits);
            apply_to(&mut output.prefix, &html, &edits);
        }
        issued.sort_unstable_by_key(|(a, b, _)| (*a, *b));
        Ok(issued)
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use djust_templates::provenance::RenderOutput;

    #[test]
    fn opaque_replacements_remap_only_output_offsets() {
        let mut rendered = Rendered::authored("é");
        rendered.append(&Rendered::from("TOKEN".to_owned()));
        rendered.append(&Rendered::authored(
            "<main dj-root dj-view=app.Page></main>",
        ));
        let html = rendered.html.clone();
        let original = rendered.literal_only.clone();
        let root_projection = rendered.literal_offsets.last().map(|(_, p)| *p);
        apply_to(&mut rendered, &html, &[(2, 7, "<div>保全</div>")]);
        let start = "é<div>保全</div>".len();
        assert!(rendered.html.starts_with("é<div>保全</div><main"));
        assert_eq!(rendered.literal_only, original);
        assert_eq!(
            rendered.literal_offsets.last().copied(),
            root_projection.map(|p| (start, p))
        );
        assert_eq!(rendered.authored.last().map(|r| r.start), Some(start));
    }
}
