use djust_vdom::parser::child_render_regions;
use std::collections::HashSet;

fn region(html: &str, whole_page: bool) -> Option<String> {
    child_render_regions(
        html,
        &HashSet::from(["child".to_owned()]),
        whole_page,
        false,
    )
    .expect("valid candidate map")
    .remove("child")
}

#[test]
fn main_root_owns_its_child() {
    assert_eq!(
        region(
            "<main dj-root><div dj-view data-djust-embedded=child></div></main>",
            true
        )
        .as_deref(),
        Some("root")
    );
}
#[test]
fn foster_parenting_matches_browser_ownership() {
    assert_eq!(region("<table><div dj-root><tr><td><div dj-view data-djust-embedded=child></div></td></tr></div></table>", true).as_deref(), Some("shell"));
}
#[test]
fn shell_child_stays_outside_root() {
    assert_eq!(
        region(
            "<div dj-view data-djust-embedded=child></div><main dj-root>body</main>",
            true
        )
        .as_deref(),
        Some("shell")
    );
}
#[test]
fn root_only_render_owns_surviving_children() {
    assert_eq!(
        region(
            "<section><div dj-view data-djust-embedded=child></div></section>",
            false
        )
        .as_deref(),
        Some("root")
    );
}
#[test]
fn inert_and_nested_child_markers_are_not_owned() {
    for html in ["<main dj-root><template><div dj-view data-djust-embedded=child></div></template></main>", "<main dj-root><div dj-view data-djust-embedded=outer><div dj-view data-djust-embedded=child></div></div></main>"] {
        assert_eq!(region(html, true), None);
    }
}
#[test]
fn duplicate_surviving_slots_fail_closed() {
    assert!(child_render_regions("<main dj-root><div dj-view data-djust-embedded=child></div><div dj-view data-djust-embedded=child></div></main>", &HashSet::from(["child".to_owned()]), true, false).is_err());
}

#[test]
fn owner_wrapper_does_not_steal_same_named_nested_child() {
    let regions = child_render_regions("<div dj-view data-djust-embedded=child><div dj-view data-djust-embedded=child></div></div>", &HashSet::from(["child".to_owned()]), false, true).unwrap();
    assert_eq!(regions.get("child").map(String::as_str), Some("root"));
}
#[test]
fn owner_wrapper_is_not_a_child_occurrence() {
    let regions = child_render_regions(
        "<div dj-view data-djust-embedded=child>no child output</div>",
        &HashSet::from(["child".to_owned()]),
        false,
        true,
    )
    .unwrap();
    assert!(regions.is_empty());
}

#[test]
fn foreign_raw_text_names_remain_excluded() {
    assert_eq!(region("<main dj-root><svg><title><div dj-view data-djust-embedded=child></div></title></svg></main>", true), None);
}
