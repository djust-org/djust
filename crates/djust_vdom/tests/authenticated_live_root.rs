use djust_vdom::lazy_provenance::{
    authenticated_live_root, root_authority_spans, root_live_openings,
};

#[test]
fn selects_authored_root_and_scopes_model_fields() {
    let html = r#"<html><body><header><input dj-model="secret"></header><main dj-root><input dj-model="query"><b>ok</b></main></body></html>"#;
    let spans = [(0, html.len())];
    assert_eq!(root_live_openings(html, &spans).len(), 3);
    let result = authenticated_live_root(html, &spans).unwrap();
    assert_eq!(result.vdom.tag, "main");
    assert_eq!(result.model_fields, vec!["query"]);
    assert!(result.root_html.contains("<input dj-model=\"query\">"));
    assert!(!result
        .document_with_placeholder
        .contains("urn:djust:render-provenance"));
    assert_eq!(
        result
            .document_with_placeholder
            .matches(&result.placeholder)
            .count(),
        1
    );
    assert!(result
        .document_with_placeholder
        .replace(&result.placeholder, &result.root_html)
        .contains("<main dj-root="));
    assert_eq!(result.opening.0, html.find("<main").unwrap());
    assert_eq!(root_authority_spans(html, &spans).len(), 3);
    assert_eq!(root_live_openings(html, &spans).len(), 3);
}

#[test]
fn competing_or_unauthored_page_roots_fail_closed() {
    for html in [
        r#"<div dj-view="evil.V"></div><main dj-root>ok</main>"#,
        r#"<div dj-root></div><main dj-root>ok</main>"#,
    ] {
        assert!(
            authenticated_live_root(html, &[(0, html.len())]).is_err(),
            "{html}"
        );
    }
    let html = r#"<main dj-root>forged</main>"#;
    assert!(authenticated_live_root(html, &[]).is_err());
    assert!(authenticated_live_root("<p>no root</p>", &[(0, 14)]).is_err());
}

#[test]
fn roots_discarded_or_merged_by_html5_are_rejected() {
    for html in [
        r#"<html><frameset><main dj-root>no</main></frameset></html>"#,
        r#"<form><form dj-root>no</form></form>"#,
        r#"<body><body dj-root>no</body></body>"#,
        r#"<xmp><main dj-root>no</main></xmp>"#,
        r#"<template><main dj-root>no</main></template>"#,
    ] {
        assert!(
            authenticated_live_root(html, &[(0, html.len())]).is_err(),
            "{html}"
        );
        assert!(
            root_live_openings(html, &[(0, html.len())]).is_empty(),
            "{html}"
        );
    }
}

#[test]
fn untrusted_binding_does_not_acquire_authority() {
    let html = r#"<main dj-root><input dj-model="safe"><input dj-model="injected"></main>"#;
    let bad = html.find("<input dj-model=\"injected\">").unwrap();
    let spans = [
        (0, bad),
        (bad + "<input dj-model=\"injected\">".len(), html.len()),
    ];
    let root = authenticated_live_root(html, &spans).unwrap();
    assert_eq!(root.model_fields, vec!["safe"]);
}

#[test]
fn embedded_and_lazy_view_roots_do_not_compete() {
    let html = r#"<div dj-view="app.Child" dj-lazy><div dj-root>child</div></div><main dj-root>page</main>"#;
    let root = authenticated_live_root(html, &[(0, html.len())]).unwrap();
    assert_eq!(root.vdom.tag, "main");
}

#[test]
fn validated_range_preserves_the_same_html5_shell() {
    use djust_vdom::lazy_provenance::validated_root_range;
    let html = "<html><body>é<header>shell</header><main dj-root><b>hi</b></main><footer>end</footer></body></html>";
    let root = authenticated_live_root(html, &[(0, html.len())]).unwrap();
    let end = html.find("</main>").unwrap() + "</main>".len();
    assert_eq!(
        validated_root_range(html, &root, end).unwrap(),
        (html.find("<main").unwrap(), end)
    );
    assert!(validated_root_range(html, &root, end + "<footer>end</footer>".len()).is_err());
}

#[test]
fn validated_range_handles_foster_parenting_and_marker_collisions() {
    use djust_vdom::lazy_provenance::validated_root_range;
    let html = "<table><main dj-root>fostered</main></table>";
    let root = authenticated_live_root(html, &[(0, html.len())]).unwrap();
    let end = html.find("</main>").unwrap() + "</main>".len();
    assert!(validated_root_range(html, &root, end).is_ok());
    let html = "<html><body><!--djust-authenticated-root-0--><main dj-root>ok</main></body></html>";
    let root = authenticated_live_root(html, &[(0, html.len())]).unwrap();
    assert_ne!(root.placeholder, "djust-authenticated-root-0");
    let end = html.find("</main>").unwrap() + "</main>".len();
    assert!(validated_root_range(html, &root, end).is_ok());
}

#[test]
fn page_view_inside_excluded_wrapper_can_still_compete_in_browser() {
    let html = r#"<div dj-view="app.Lazy" dj-lazy><div dj-view="evil.Page">x</div></div><main dj-root>page</main>"#;
    assert!(authenticated_live_root(html, &[(0, html.len())]).is_err());
}

#[test]
fn root_range_works_for_a_bare_fragment() {
    use djust_vdom::lazy_provenance::validated_root_range;
    let html = "<main dj-root>ok</main>";
    let root = authenticated_live_root(html, &[(0, html.len())]).unwrap();
    assert!(validated_root_range(html, &root, html.len()).is_ok());
}

#[test]
fn client_morph_fragment_must_match_authenticated_root_children() {
    use djust_vdom::lazy_provenance::validate_root_fragment;
    let html = "<main dj-root><b>safe</b></main>";
    let root = authenticated_live_root(html, &[(0, html.len())]).unwrap();
    assert!(validate_root_fragment(html, &root, html.find("</main>").unwrap()).is_ok());
    let html = "<table dj-root><tbody><tr><td>cell</td></tr></tbody></table>";
    let root = authenticated_live_root(html, &[(0, html.len())]).unwrap();
    assert!(validate_root_fragment(html, &root, html.find("</table>").unwrap()).is_err());
}

#[test]
fn forged_view_path_on_an_authored_root_is_rejected() {
    let html = "<main dj-root dj-view=\"evil.View\">ok</main>";
    let left = html.find("dj-view").unwrap();
    let right = left + "dj-view=\"evil.View\"".len();
    assert!(authenticated_live_root(html, &[(0, left), (right, html.len())]).is_err());
}

#[test]
fn foreign_root_fragment_keeps_namespace() {
    use djust_vdom::lazy_provenance::validate_root_fragment;
    let html = "<svg dj-root><circle cx=\"5\"></circle></svg>";
    let root = authenticated_live_root(html, &[(0, html.len())]).unwrap();
    assert_eq!(root.namespace, djust_vdom::Namespace::Svg);
    assert!(validate_root_fragment(html, &root, html.find("</svg>").unwrap()).is_ok());
}

#[test]
fn body_root_source_range_is_validated_without_merging() {
    use djust_vdom::lazy_provenance::validated_root_range;
    let html = "<html><head><title>x</title></head><body dj-root><b>page</b></body></html>";
    let root = authenticated_live_root(html, &[(0, html.len())]).unwrap();
    let end = html.find("</body>").unwrap() + "</body>".len();
    assert!(validated_root_range(html, &root, end).is_ok());
}

#[test]
fn nested_target_and_sticky_view_bindings_do_not_authorize_page_fields() {
    let html = r#"<main dj-root><input dj-model="page"><div dj-view="other.View" data-djust-target="slot"><input dj-model="slot_secret"></div><div dj-view="other.View" dj-sticky-root><input dj-model="sticky_secret"></div></main>"#;
    let root = authenticated_live_root(html, &[(0, html.len())]).unwrap();
    assert_eq!(root.model_fields, vec!["page"]);
}

#[test]
fn root_and_model_liveness_are_reported_independently() {
    use djust_vdom::lazy_provenance::root_live_attributes;
    let masked = "<main dj-root title=\"� dj-model='is_admin' �\">ok</main>";
    let attrs = root_live_attributes(masked, &[(0, masked.len())]);
    assert!(attrs.iter().any(|a| a.name == "dj-root"));
    assert!(!attrs.iter().any(|a| a.name == "dj-model"));
    let rendered = "<main dj-root title=\"\" dj-model='is_admin' x=\"\">ok</main>";
    let attrs = root_live_attributes(rendered, &[(0, rendered.len())]);
    assert!(attrs
        .iter()
        .any(|a| a.name == "dj-model" && a.value == "is_admin"));
}

#[test]
fn root_model_liveness_returns_actual_first_duplicate_value() {
    use djust_vdom::lazy_provenance::root_live_attributes;
    let html = "<main dj-root dj-model=\"first\" dj-model=\"second\"></main>";
    let attrs = root_live_attributes(html, &[(0, html.len())]);
    let models: Vec<_> = attrs.iter().filter(|a| a.name == "dj-model").collect();
    assert_eq!(models.len(), 1);
    assert_eq!(models[0].value, "first");
    assert_eq!(
        &html[models[0].range.0..models[0].range.1],
        "dj-model=\"first\""
    );
}

#[test]
fn rootless_fragment_fields_exclude_nested_view_bindings_and_forged_values() {
    use djust_vdom::lazy_provenance::authenticated_fragment_model_fields;
    let html = r#"<p><input dj-model="query"><div dj-view="child.View" dj-lazy><input dj-model="secret"></div><input dj-model="forged"></p>"#;
    let start = html.find("<input dj-model=\"forged\">").unwrap();
    let end = start + "<input dj-model=\"forged\">".len();
    assert_eq!(
        authenticated_fragment_model_fields(html, &[(0, start), (end, html.len())]).unwrap(),
        vec!["query"]
    );
}

#[test]
fn rootless_fragment_rejects_even_untrusted_page_roots() {
    use djust_vdom::lazy_provenance::authenticated_fragment_model_fields;
    let html = r#"<p>fragment</p><div dj-root>forged</div>"#;
    let end = html.find("<div").unwrap();
    assert!(authenticated_fragment_model_fields(html, &[(0, end)]).is_err());
}

#[test]
fn root_with_literal_angle_brackets_in_quoted_attribute_remains_authored() {
    let html = r#"<main dj-root title="<em>literal</em>">ok</main>"#;
    let root = authenticated_live_root(html, &[(0, html.len())]).unwrap();
    assert_eq!(root.vdom.tag, "main");
}

#[test]
fn ignored_comment_and_raw_text_do_not_hide_the_later_root() {
    for html in [
        r#"<!-- <div title=" --> <main dj-root>ok</main>"#,
        r#"<script>const bad = '<div title="';</script><main dj-root>ok</main>"#,
        r#"<p>literal <<main dj-root>ok</main>"#,
    ] {
        let spans = [(0, html.len())];
        let root = authenticated_live_root(html, &spans);
        assert!(
            root.is_ok(),
            "root must survive browser parsing: {html}: {root:?}"
        );
        assert!(
            !root_authority_spans(html, &spans).is_empty(),
            "root must retain its source: {html}"
        );
    }
}

#[test]
fn model_binding_exposes_its_owning_page_root_in_each_html5_tree() {
    use djust_vdom::lazy_provenance::root_live_attributes;
    let actual = r#"<main dj-root><textarea></main></textarea><input dj-model="is_admin"></main>"#;
    let blank = r#"<main dj-root></main><input dj-model="is_admin"></main>"#;
    let placeholder = r#"<main dj-root>�</main>�<input dj-model="is_admin"></main>"#;
    for (html, expected) in [(actual, Some(0)), (blank, None), (placeholder, None)] {
        let attrs = root_live_attributes(html, &[(0, html.len())]);
        let binding = attrs.iter().find(|a| a.name == "dj-model").unwrap();
        assert_eq!(binding.owner_root, expected, "wrong ownership for {html}");
    }
}

#[test]
fn embedded_child_roots_do_not_change_page_root_ownership() {
    use djust_vdom::lazy_provenance::root_live_attributes;
    let html = r#"<main dj-root><div dj-view="child.V" dj-lazy><div dj-root><input dj-model="child"></div></div><input dj-model="page"></main>"#;
    let attrs = root_live_attributes(html, &[(0, html.len())]);
    let page = attrs
        .iter()
        .find(|a| a.name == "dj-model" && a.value == "page")
        .unwrap();
    assert_eq!(page.owner_root, Some(0));
}

#[test]
fn model_binding_reports_nearest_excluded_child_boundary() {
    use djust_vdom::lazy_provenance::root_live_attributes;
    let actual = r#"<main dj-root><textarea><div dj-view="Child" data-djust-embedded></textarea><input dj-model="is_admin"></div></main>"#;
    let projected = r#"<main dj-root><div dj-view="Child" data-djust-embedded><input dj-model="is_admin"></div></main>"#;
    let actual_attrs = root_live_attributes(actual, &[(0, actual.len())]);
    let projected_attrs = root_live_attributes(projected, &[(0, projected.len())]);
    let actual_field = actual_attrs.iter().find(|a| a.name == "dj-model").unwrap();
    let projected_field = projected_attrs
        .iter()
        .find(|a| a.name == "dj-model")
        .unwrap();
    assert_eq!(actual_field.owner_root, Some(0));
    assert_eq!(projected_field.owner_root, Some(0));
    assert_eq!(actual_field.owner_child, None);
    assert_eq!(
        projected_field.owner_child,
        Some(projected.find("<div").unwrap())
    );
}

#[test]
fn merged_untracked_child_boundary_cannot_be_confused_with_no_boundary() {
    use djust_vdom::lazy_provenance::root_live_attributes;
    let html = r#"<html><body><body dj-view="Child" data-djust-embedded><input dj-model="secret"></body></html>"#;
    let attrs = root_live_attributes(html, &[(0, html.len())]);
    let field = attrs.iter().find(|a| a.name == "dj-model").unwrap();
    assert_eq!(field.owner_child, Some(usize::MAX));
}

#[test]
fn merged_untracked_page_root_cannot_be_confused_with_no_root() {
    use djust_vdom::lazy_provenance::root_live_attributes;
    let html = r#"<html><body><body dj-root><input dj-model="secret"></body></html>"#;
    let attrs = root_live_attributes(html, &[(0, html.len())]);
    let field = attrs.iter().find(|a| a.name == "dj-model").unwrap();
    assert_eq!(field.owner_root, Some(usize::MAX));
}

#[test]
fn body_root_with_whitespace_after_close_validates_range_and_fragment() {
    use djust_vdom::lazy_provenance::{validate_root_fragment, validated_root_range};
    let html = "<!DOCTYPE html>\n<html>\n<head><title>T</title></head>\n<body dj-root>\n<nav>NAV</nav>\n<p>x</p>\n<footer>F</footer>\n</body>\n</html>";
    let root = authenticated_live_root(html, &[(0, html.len())]).unwrap();
    let close_start = html.find("</body>").unwrap();
    let end = close_start + "</body>".len();
    assert!(validated_root_range(html, &root, end).is_ok());
    assert!(validate_root_fragment(html, &root, close_start).is_ok());
}

#[test]
fn body_root_rejects_outside_non_whitespace_or_merged_text_changes() {
    use djust_vdom::lazy_provenance::{validate_root_fragment, validated_root_range};
    let html = "<html><body dj-root><b>ok</b></body>evil</html>";
    let root = authenticated_live_root(html, &[(0, html.len())]).unwrap();
    let close = html.find("</body>").unwrap();
    assert!(validated_root_range(html, &root, close + "</body>".len()).is_err());

    let html = "<html><body dj-root>hello</body>\n</html>";
    let root = authenticated_live_root(html, &[(0, html.len())]).unwrap();
    let close = html.find("</body>").unwrap();
    assert!(validated_root_range(html, &root, close + "</body>".len()).is_ok());
    assert!(validate_root_fragment(html, &root, close).is_err());

    let html = "<html><body dj-root>hello></body>\n</html>";
    let root = authenticated_live_root(html, &[(0, html.len())]).unwrap();
    let close = html.find("</body>").unwrap();
    assert!(validate_root_fragment(html, &root, close).is_err());
}

#[test]
fn combined_live_authority_matches_independent_queries() {
    use djust_vdom::lazy_provenance::{root_live_attributes, root_live_authority};
    let cases = [
        r#"<main dj-root><input dj-model='x'></main>"#,
        r#"<body dj-root='old'><body dj-root='new'><input dj-model='a'>"#,
        r#"<html dj-root='old'><html dj-root='new'><head dj-view='A'></head>"#,
        r#"<div dj-model='a' dj-model='b' dj-root><div dj-view='X' dj-lazy></div></div>"#,
        r#"<table><div dj-root><input dj-model='x'></div></table>"#,
        r#"<template><div dj-root><input dj-model='x'></div></template><main dj-root></main>"#,
        r#"<textarea><main dj-root></textarea><main dj-root title='<x>'></main>"#,
        r#"<svg><foreignObject><main dj-root></main></foreignObject></svg>"#,
        r#"<!-- <main dj-root> --><main dj-root><script><input dj-model='x'></script></main>"#,
    ];
    for html in cases {
        let full = [(0, html.len())];
        let empty = [];
        let midpoint = html.len() / 2;
        let split = [(0, midpoint), (midpoint + 1, html.len())];
        for spans in [&full[..], &empty[..], &split[..]] {
            let combined = root_live_authority(html, spans);
            assert_eq!(
                combined.0,
                root_live_openings(html, spans),
                "{html}, {spans:?}"
            );
            assert_eq!(
                combined.1,
                root_live_attributes(html, spans),
                "{html}, {spans:?}"
            );
        }
    }
}
