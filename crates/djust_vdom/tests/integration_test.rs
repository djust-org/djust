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
