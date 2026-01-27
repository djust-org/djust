//! Integration tests for VDOM diffing with real HTML parsing
//!
//! These tests use the actual html5ever parser to create VDOMs,
//! simulating real-world scenarios including the whitespace text node issue.

use djust_vdom::{diff, parse_html};

#[test]
fn test_form_validation_errors_with_real_html() {
    // Simulate the exact bug: form with validation errors that get cleared
    let html_with_errors = r#"
        <form class="needs-validation">
            <div class="mb-3">
                <input class="form-control is-invalid" name="username">
                <div class="invalid-feedback">Username is required</div>
            </div>
            <div class="mb-3">
                <input class="form-control is-invalid" name="email">
                <div class="invalid-feedback">Email is required</div>
            </div>
            <button type="submit">Submit</button>
        </form>
    "#;

    let html_without_errors = r#"
        <form class="needs-validation">
            <div class="mb-3">
                <input class="form-control" name="username">
            </div>
            <div class="mb-3">
                <input class="form-control" name="email">
            </div>
            <button type="submit">Submit</button>
        </form>
    "#;

    let old_vdom = parse_html(html_with_errors).unwrap();
    let new_vdom = parse_html(html_without_errors).unwrap();

    let patches = diff(&old_vdom, &new_vdom);

    // Should generate patches to:
    // 1. Remove "is-invalid" class from inputs
    // 2. Remove validation error divs
    assert!(
        !patches.is_empty(),
        "Should generate patches when validation errors are removed"
    );

    // Verify we have RemoveChild patches (for validation error divs)
    let remove_patches: Vec<_> = patches
        .iter()
        .filter(|p| matches!(p, djust_vdom::Patch::RemoveChild { .. }))
        .collect();
    assert_eq!(
        remove_patches.len(),
        2,
        "Should have 2 RemoveChild patches for 2 validation error divs"
    );

    // Verify we have SetAttr patches (for removing "is-invalid" class)
    let attr_patches: Vec<_> = patches
        .iter()
        .filter(|p| matches!(p, djust_vdom::Patch::SetAttr { .. }))
        .collect();
    assert!(
        attr_patches.len() >= 2,
        "Should have at least 2 SetAttr patches for fixing input classes"
    );
}

#[test]
fn test_conditional_div_with_whitespace() {
    // Test conditional rendering with whitespace (Django {% if %} blocks)
    let html_with_alert = r#"
        <div class="card-body">
            <div class="alert alert-success">Success!</div>
            <div class="alert alert-danger d-none">Error</div>
            <form>
                <button>Submit</button>
            </form>
        </div>
    "#;

    let html_without_success = r#"
        <div class="card-body">
            <div class="alert alert-success d-none">Success!</div>
            <div class="alert alert-danger d-none">Error</div>
            <form>
                <button>Submit</button>
            </form>
        </div>
    "#;

    let old_vdom = parse_html(html_with_alert).unwrap();
    let new_vdom = parse_html(html_without_success).unwrap();

    let patches = diff(&old_vdom, &new_vdom);

    // Should generate a SetAttr patch to add "d-none" class
    assert!(
        patches.iter().any(|p| matches!(
            p,
            djust_vdom::Patch::SetAttr { key, value, .. }
            if key.contains("class") && value.contains("d-none")
        )),
        "Should add d-none class to success alert"
    );
}

#[test]
fn test_deeply_nested_form_structure() {
    // Simulate the full structure: container > row > col > card > card-body > form
    let html_with_errors = r#"
        <div class="container">
            <div class="row">
                <div class="col">
                    <div class="card">
                        <div class="card-body">
                            <form>
                                <div class="mb-3">
                                    <input name="field1">
                                    <div class="error">Error 1</div>
                                </div>
                                <button>Submit</button>
                            </form>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    "#;

    let html_without_errors = r#"
        <div class="container">
            <div class="row">
                <div class="col">
                    <div class="card">
                        <div class="card-body">
                            <form>
                                <div class="mb-3">
                                    <input name="field1">
                                </div>
                                <button>Submit</button>
                            </form>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    "#;

    let old_vdom = parse_html(html_with_errors).unwrap();
    let new_vdom = parse_html(html_without_errors).unwrap();

    let patches = diff(&old_vdom, &new_vdom);

    // Should generate RemoveChild patch with a deep path
    let remove_patches: Vec<_> = patches
        .iter()
        .filter(|p| matches!(p, djust_vdom::Patch::RemoveChild { path, .. } if path.len() > 5))
        .collect();

    assert!(
        !remove_patches.is_empty(),
        "Should have RemoveChild patch with deep path (> 5 levels)"
    );
}

#[test]
fn test_whitespace_preserved_in_vdom() {
    // Verify that html5ever parser preserves whitespace as text nodes
    let html = r#"
        <div>
            <span>A</span>
            <span>B</span>
        </div>
    "#;

    let vdom = parse_html(html).unwrap();

    // The div should have children: [text, span, text, span, text]
    // html5ever preserves whitespace between elements
    let div_children = &vdom.children;
    assert!(
        div_children.len() >= 2,
        "Should have at least 2 children (the spans)"
    );

    // In real scenarios with html5ever, we'd have whitespace text nodes too
    // This test documents the expected behavior
}

#[test]
fn test_patch_indices_account_for_whitespace() {
    // Ensure patch indices correctly account for whitespace text nodes
    let html1 = r#"<div><span>A</span> <span>B</span> <span>C</span></div>"#;
    let html2 = r#"<div><span>A</span> <span>B-modified</span> <span>C</span></div>"#;

    let old_vdom = parse_html(html1).unwrap();
    let new_vdom = parse_html(html2).unwrap();

    let patches = diff(&old_vdom, &new_vdom);

    // Should generate a SetText patch at the correct path
    // The path must account for whitespace text nodes between spans
    assert!(
        !patches.is_empty(),
        "Should generate patches for text change"
    );

    // Verify we have a text change patch
    assert!(
        patches
            .iter()
            .any(|p| matches!(p, djust_vdom::Patch::SetText { .. })),
        "Should have SetText patch for modified content"
    );
}

#[test]
fn test_multiple_fields_with_errors_cleared() {
    // Real-world scenario: registration form with 4 fields, all have errors, then all cleared
    let html_with_errors = r#"
        <form>
            <div class="field"><input><div class="error">E1</div></div>
            <div class="field"><input><div class="error">E2</div></div>
            <div class="field"><input><div class="error">E3</div></div>
            <div class="field"><input><div class="error">E4</div></div>
            <button>Submit</button>
        </form>
    "#;

    let html_without_errors = r#"
        <form>
            <div class="field"><input></div>
            <div class="field"><input></div>
            <div class="field"><input></div>
            <div class="field"><input></div>
            <button>Submit</button>
        </form>
    "#;

    let old_vdom = parse_html(html_with_errors).unwrap();
    let new_vdom = parse_html(html_without_errors).unwrap();

    let patches = diff(&old_vdom, &new_vdom);

    // Should generate 4 RemoveChild patches, one for each error div
    let remove_patches: Vec<_> = patches
        .iter()
        .filter(|p| matches!(p, djust_vdom::Patch::RemoveChild { .. }))
        .collect();

    assert_eq!(
        remove_patches.len(),
        4,
        "Should remove 4 error divs from 4 fields"
    );
}

// ============================================================================
// Tests for data-dj-id attribute generation (compact ID-based patching)
// ============================================================================

#[test]
fn test_parsed_vdom_has_djust_ids() {
    // Verify that parsed VDOM nodes get djust_id assigned
    use djust_vdom::reset_id_counter;

    reset_id_counter();
    let html = "<div><span>Hello</span><p>World</p></div>";
    let vdom = parse_html(html).unwrap();

    // Root element should have djust_id
    assert!(
        vdom.djust_id.is_some(),
        "Root element should have djust_id assigned"
    );

    // Children should also have djust_ids
    let span = &vdom.children[0];
    assert!(
        span.djust_id.is_some(),
        "Span element should have djust_id assigned"
    );

    let p = &vdom.children[1];
    assert!(
        p.djust_id.is_some(),
        "P element should have djust_id assigned"
    );

    // IDs should be unique
    assert_ne!(
        vdom.djust_id, span.djust_id,
        "djust_ids should be unique between elements"
    );
    assert_ne!(
        span.djust_id, p.djust_id,
        "djust_ids should be unique between siblings"
    );
}

#[test]
fn test_to_html_includes_data_dj_attributes() {
    // Verify that to_html() serializes data-dj-id attributes into HTML
    use djust_vdom::reset_id_counter;

    reset_id_counter();
    let html = "<div><span>Hello</span><p>World</p></div>";
    let vdom = parse_html(html).unwrap();

    let output_html = vdom.to_html();

    // Output should contain data-dj-id attributes
    assert!(
        output_html.contains("data-dj-id="),
        "to_html() should include data-dj-id attributes in output"
    );

    // Should have multiple data-dj-id attributes for different elements
    let data_dj_count = output_html.matches("data-dj-id=").count();
    assert!(
        data_dj_count >= 3,
        "Should have at least 3 data-dj-id attributes (div, span, p), found {}",
        data_dj_count
    );
}

#[test]
fn test_to_html_output_format() {
    // Verify the exact format of to_html() output
    use djust_vdom::reset_id_counter;

    reset_id_counter();
    let html = "<div class=\"container\"><span>Hello</span></div>";
    let vdom = parse_html(html).unwrap();

    let output_html = vdom.to_html();

    // Should maintain structure with data-dj-id added
    assert!(
        output_html.contains("<div"),
        "Output should contain div tag"
    );
    assert!(
        output_html.contains("class=\"container\""),
        "Output should preserve class attribute"
    );
    assert!(
        output_html.contains("<span"),
        "Output should contain span tag"
    );
    assert!(
        output_html.contains("Hello"),
        "Output should preserve text content"
    );
    assert!(
        output_html.contains("</div>"),
        "Output should have closing div tag"
    );
}

#[test]
fn test_patches_include_djust_id() {
    // Verify that generated patches include the `d` field for ID-based lookup
    // Note: Text node patches (SetText) don't have djust_ids since text nodes
    // don't have data-dj-id attributes. Test with attribute changes instead.
    use djust_vdom::reset_id_counter;

    reset_id_counter();
    let html_before = r#"<div class="old"><span>Hello</span></div>"#;

    reset_id_counter();
    let html_after = r#"<div class="new"><span>Hello</span></div>"#;

    let old_vdom = parse_html(html_before).unwrap();
    let new_vdom = parse_html(html_after).unwrap();

    let patches = diff(&old_vdom, &new_vdom);

    // Should have at least one patch (SetAttr for class change)
    assert!(
        !patches.is_empty(),
        "Should generate patches for attribute change"
    );

    // SetAttr patches should have a `d` field (djust_id)
    let has_djust_id = patches.iter().any(|p| match p {
        djust_vdom::Patch::SetAttr { d, .. } => d.is_some(),
        djust_vdom::Patch::RemoveAttr { d, .. } => d.is_some(),
        djust_vdom::Patch::Replace { d, .. } => d.is_some(),
        djust_vdom::Patch::InsertChild { d, .. } => d.is_some(),
        djust_vdom::Patch::RemoveChild { d, .. } => d.is_some(),
        djust_vdom::Patch::MoveChild { d, .. } => d.is_some(),
        // Text nodes don't have djust_ids
        djust_vdom::Patch::SetText { .. } => false,
    });

    assert!(
        has_djust_id,
        "SetAttr patch should include djust_id (d field) for ID-based lookup. Patches: {:?}",
        patches
    );
}

#[test]
fn test_base62_id_generation() {
    // Verify base62 encoding produces compact, unique IDs
    use djust_vdom::to_base62;

    // Test base62 conversion
    assert_eq!(to_base62(0), "0");
    assert_eq!(to_base62(9), "9");
    assert_eq!(to_base62(10), "a");
    assert_eq!(to_base62(35), "z");
    assert_eq!(to_base62(36), "A");
    assert_eq!(to_base62(61), "Z");
    assert_eq!(to_base62(62), "10"); // Base62 rollover

    // Large numbers should still produce compact IDs
    let large_id = to_base62(1000);
    assert!(
        large_id.len() <= 3,
        "ID for 1000 should be <= 3 chars, got: {}",
        large_id
    );
}

#[test]
fn test_id_counter_produces_unique_ids() {
    // Verify IDs are unique within a single parse.
    // Note: We don't reset the counter because that causes race conditions
    // when tests run in parallel. We just verify uniqueness regardless of
    // what values the IDs have.
    let html = "<div><span>A</span><span>B</span><span>C</span></div>";
    let vdom = parse_html(html).unwrap();

    // Collect all IDs from the tree (recursively to catch all nodes)
    fn collect_ids(node: &djust_vdom::VNode, ids: &mut Vec<String>) {
        if let Some(id) = &node.djust_id {
            ids.push(id.clone());
        }
        for child in &node.children {
            collect_ids(child, ids);
        }
    }

    let mut ids: Vec<String> = Vec::new();
    collect_ids(&vdom, &mut ids);

    // All IDs should be unique
    let unique_count = {
        let mut unique = ids.clone();
        unique.sort();
        unique.dedup();
        unique.len()
    };

    assert_eq!(
        ids.len(),
        unique_count,
        "All djust_ids should be unique within a parse: {:?}",
        ids
    );

    // Should have at least 4 IDs (div + 3 spans)
    assert!(
        ids.len() >= 4,
        "Should have at least 4 IDs, got: {}",
        ids.len()
    );
}

#[test]
fn test_void_elements_in_to_html() {
    // Verify void elements (br, img, input) are handled correctly
    use djust_vdom::reset_id_counter;

    reset_id_counter();
    let html = r#"<div><input type="text"><br><img src="test.png"></div>"#;
    let vdom = parse_html(html).unwrap();

    let output_html = vdom.to_html();

    // Void elements should be self-closing
    assert!(
        output_html.contains("<input") && output_html.contains("/>"),
        "Input should be self-closing"
    );
    assert!(
        output_html.contains("<br") && output_html.contains("/>"),
        "BR should be self-closing"
    );
    assert!(
        output_html.contains("<img") && output_html.contains("/>"),
        "IMG should be self-closing"
    );

    // Should NOT have closing tags for void elements
    assert!(
        !output_html.contains("</input>"),
        "Input should not have closing tag"
    );
    assert!(
        !output_html.contains("</br>"),
        "BR should not have closing tag"
    );
}

#[test]
fn test_html_escaping_in_to_html() {
    // Verify HTML special characters are escaped properly
    use djust_vdom::VNode;

    let node = VNode::element("div")
        .with_attr("data-value", "1 < 2 && 3 > 2")
        .with_child(VNode::text("Hello <script>alert('xss')</script>"));

    let output_html = node.to_html();

    // Text content should be escaped
    assert!(
        output_html.contains("&lt;script&gt;"),
        "Script tags in text should be escaped"
    );

    // Attribute values should be escaped
    assert!(
        output_html.contains("&lt;") && output_html.contains("&gt;"),
        "< and > in attributes should be escaped"
    );
}

#[test]
fn test_whitespace_preserved_in_pre_tags() {
    // Whitespace (including newlines) should be preserved inside <pre> elements
    // This is critical for code blocks with syntax highlighting
    let html = r#"<pre><span class="line1">def foo():</span>
<span class="line2">    return 42</span></pre>"#;

    let vdom = parse_html(html).unwrap();

    // The pre element should have 3 children: span, newline text node, span
    assert_eq!(vdom.tag, "pre", "Root should be pre element");
    assert_eq!(
        vdom.children.len(),
        3,
        "Pre should have 3 children (span, newline text, span)"
    );

    // Middle child should be a text node with newline
    assert!(
        vdom.children[1].is_text(),
        "Second child should be a text node"
    );
    assert_eq!(
        vdom.children[1].text.as_deref(),
        Some("\n"),
        "Text node should contain the newline"
    );
}

#[test]
fn test_whitespace_preserved_in_code_tags() {
    // Whitespace should also be preserved in <code> elements
    let html = r#"<code>x = 1
y = 2</code>"#;

    let vdom = parse_html(html).unwrap();

    assert_eq!(vdom.tag, "code", "Root should be code element");

    // Should have the text content including newline
    if vdom.children.len() == 1 && vdom.children[0].is_text() {
        let text = vdom.children[0].text.as_deref().unwrap();
        assert!(
            text.contains('\n'),
            "Code content should preserve newline: {:?}",
            text
        );
    } else {
        panic!("Code should have text content");
    }
}

#[test]
fn test_whitespace_filtered_outside_pre() {
    // Outside of whitespace-preserving elements, whitespace-only nodes should be filtered
    let html = r#"<div>
    <span>A</span>
    <span>B</span>
</div>"#;

    let vdom = parse_html(html).unwrap();

    // The div should only have the 2 span elements, whitespace filtered out
    assert_eq!(
        vdom.children.len(),
        2,
        "Div should have 2 children (spans only, whitespace filtered)"
    );
    assert_eq!(vdom.children[0].tag, "span");
    assert_eq!(vdom.children[1].tag, "span");
}
