use djust_vdom::lazy_provenance::{authenticated_live_root_with_children, AuthenticatedRootError};

const CHILD: &str = "<div dj-view data-djust-embedded=child>";
fn opening(html: &str, needle: &str) -> (usize, usize, String) {
    let start = html.find(needle).unwrap();
    (start, start + needle.len(), "issued".to_owned())
}

#[test]
fn opaque_issued_wrapper_binds_to_its_generated_id() {
    let html = format!("<main dj-root>é{CHILD}child</div></main>");
    let child = opening(&html, CHILD);
    let proof = [(0, child.0), (child.1, html.len())];
    let root = authenticated_live_root_with_children(&html, &proof, &[child]).unwrap();
    assert_eq!(root.child_vdom_ids.len(), 1);
    let node = root
        .vdom
        .children
        .iter()
        .find(|node| node.tag == "div")
        .unwrap();
    assert_eq!(
        root.child_vdom_ids[0],
        ("issued".to_owned(), node.djust_id.clone().unwrap())
    );
    assert!(!root.root_html.contains("urn:djust"));
    assert!(!root.vdom.to_html().contains("source="));
}

#[test]
fn inert_occurrence_cannot_bind_an_unrelated_matching_slot() {
    let html =
        format!("<main dj-root>{CHILD}decoy</div><template>{CHILD}issued</div></template></main>");
    let start = html.rfind(CHILD).unwrap();
    let issued = (start, start + CHILD.len(), "issued".to_owned());
    assert!(authenticated_live_root_with_children(&html, &[(0, html.len())], &[issued]).is_err());
}

#[test]
fn nested_foreign_wrapper_cannot_become_a_direct_child() {
    let html = format!(
        "<main dj-root><div dj-view data-djust-embedded=outer>{CHILD}nested</div></div></main>"
    );
    assert!(authenticated_live_root_with_children(
        &html,
        &[(0, html.len())],
        &[opening(&html, CHILD)]
    )
    .is_err());
}

#[test]
fn duplicate_keys_or_overlapping_openings_fail_closed() {
    let html = format!("<main dj-root>{CHILD}first</div>{CHILD}second</div></main>");
    let first = opening(&html, CHILD);
    let second_start = html.rfind(CHILD).unwrap();
    let second = (
        second_start,
        second_start + CHILD.len(),
        "issued".to_owned(),
    );
    assert!(authenticated_live_root_with_children(
        &html,
        &[(0, html.len())],
        &[first.clone(), second]
    )
    .is_err());
    assert!(authenticated_live_root_with_children(
        &html,
        &[(0, html.len())],
        &[first.clone(), first]
    )
    .is_err());
}

#[test]
fn issued_openings_never_grant_root_or_model_authority() {
    let html = "<main dj-root dj-model=admin></main>";
    let child = (0, html.find('>').unwrap() + 1, "issued".to_owned());
    assert!(matches!(
        authenticated_live_root_with_children(html, &[], &[child]),
        Err(AuthenticatedRootError::Unauthored)
    ));
}

#[test]
fn foster_parented_occurrence_outside_root_is_rejected() {
    let html = format!("<table><div dj-root><tr><td>{CHILD}outside</div></td></tr></div></table>");
    assert!(authenticated_live_root_with_children(
        &html,
        &[(0, html.len())],
        &[opening(&html, CHILD)]
    )
    .is_err());
}

#[test]
fn issued_child_content_does_not_supply_parent_model_fields() {
    let prefix = "<main dj-root>é";
    let opaque = format!("{CHILD}<input dj-model=admin></div>");
    let suffix = "<input dj-model=query></main>";
    let html = format!("{prefix}{opaque}{suffix}");
    let spans = [(0, prefix.len()), (prefix.len() + opaque.len(), html.len())];
    let root =
        authenticated_live_root_with_children(&html, &spans, &[opening(&html, CHILD)]).unwrap();
    assert_eq!(root.model_fields, ["query"]);
}

#[test]
fn a_child_below_the_vdom_depth_limit_cannot_claim_a_missing_id() {
    let nested = "<section>".repeat(djust_vdom::MAX_VDOM_DEPTH + 1);
    let close = "</section>".repeat(djust_vdom::MAX_VDOM_DEPTH + 1);
    let html = format!("<main dj-root>{nested}{CHILD}deep</div>{close}</main>");
    assert!(authenticated_live_root_with_children(
        &html,
        &[(0, html.len())],
        &[opening(&html, CHILD)]
    )
    .is_err());
}
