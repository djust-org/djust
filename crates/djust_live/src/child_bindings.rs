//! Translate verified child IDs across ignored-subtree reuse
//! and diff ID synchronization. The Python issuer validates registry ownership.
use djust_vdom::VNode;
use std::collections::{HashMap, HashSet};

type BindingPath = (Vec<usize>, String);

fn paths(
    node: &VNode,
    bindings: &HashMap<String, String>,
) -> Result<Vec<BindingPath>, &'static str> {
    fn visit(
        node: &VNode,
        path: &mut Vec<usize>,
        seen: &mut HashSet<String>,
        bindings: &HashMap<String, String>,
        out: &mut Vec<BindingPath>,
    ) -> Result<(), &'static str> {
        if let Some(id) = node.djust_id.as_ref() {
            if !seen.insert(id.clone()) {
                return Err("duplicate VDOM ID");
            }
            if let Some(binding) = bindings.get(id) {
                out.push((path.clone(), binding.clone()));
            }
        }
        for (i, child) in node.children.iter().enumerate() {
            path.push(i);
            visit(child, path, seen, bindings, out)?;
            path.pop();
        }
        Ok(())
    }
    let mut out = Vec::new();
    visit(
        node,
        &mut Vec::new(),
        &mut HashSet::new(),
        bindings,
        &mut out,
    )?;
    Ok(out)
}

/// Plans keys from this render and previously authenticated IDs through the
/// exact position-based algorithm used by `splice_ignore_subtrees`.
pub fn plan(
    old: Option<&VNode>,
    new: &VNode,
    new_by_id: &HashMap<String, String>,
    previous_ids: &HashSet<String>,
    authored_ignores: &HashSet<String>,
) -> Result<(Vec<BindingPath>, Vec<BindingPath>), &'static str> {
    let mut fresh = paths(new, new_by_id)?;
    if fresh.len() != new_by_id.len() {
        return Err("fresh child not present in VDOM");
    }
    // No delivered baseline means there are no prior VDOM nodes whose
    // ownership can be retained; discard the tentative HTTP bindings.
    let Some(old) = old else {
        return Ok((fresh, Vec::new()));
    };
    let old_by_id: HashMap<String, String> = previous_ids
        .iter()
        .map(|id| (id.clone(), id.clone()))
        .collect();
    let prior = paths(old, &old_by_id)?;
    if prior.len() != previous_ids.len() {
        return Err("previous child not present in VDOM");
    }
    let mut ignored = Vec::new();
    fn find(old: &VNode, new: &VNode, path: &mut Vec<usize>, ignored: &mut Vec<Vec<usize>>) {
        if old.tag != new.tag {
            return;
        }
        if new.attrs.get("dj-update").map(String::as_str) == Some("ignore") {
            ignored.push(path.clone());
            return;
        }
        for i in 0..old.children.len().min(new.children.len()) {
            path.push(i);
            find(&old.children[i], &new.children[i], path, ignored);
            path.pop();
        }
    }
    find(old, new, &mut Vec::new(), &mut ignored);
    let below =
        |path: &[usize], prefix: &[usize]| path.len() > prefix.len() && path.starts_with(prefix);
    fresh.retain(|(path, _)| !ignored.iter().any(|prefix| below(path, prefix)));
    let mut preserved = Vec::new();
    for (path, key) in prior {
        if let Some(prefix) = ignored.iter().find(|prefix| below(&path, prefix)) {
            let mut boundary = new;
            for index in prefix {
                boundary = &boundary.children[*index];
            }
            if !boundary
                .djust_id
                .as_ref()
                .is_some_and(|id| authored_ignores.contains(id))
            {
                return Err("unauthored ignore boundary cannot retain child ownership");
            }
            preserved.push((path, key));
        }
    }
    Ok((fresh, preserved))
}

pub fn rekey(node: &VNode, paths: Vec<BindingPath>) -> Result<Vec<(String, String)>, &'static str> {
    let mut seen = HashSet::new();
    let mut result = Vec::new();
    for (path, key) in paths {
        let mut target = node;
        for index in path {
            target = target.children.get(index).ok_or("VDOM path changed")?;
        }
        let id = target.djust_id.clone().ok_or("child has no VDOM ID")?;
        if !seen.insert(id.clone()) {
            return Err("duplicate VDOM ID");
        }
        result.push((key, id));
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn node(tag: &str, id: &str, children: Vec<VNode>) -> VNode {
        let mut result = VNode::element(tag);
        result.djust_id = Some(id.into());
        result.children = children;
        result
    }

    fn ignored(id: &str, children: Vec<VNode>) -> VNode {
        let mut result = node("section", id, children);
        result.attrs.insert("dj-update".into(), "ignore".into());
        result
    }

    #[test]
    fn only_copied_children_retain_binding_and_rekey_after_sync() {
        let old = node(
            "main",
            "0",
            vec![
                ignored("1", vec![node("div", "2", vec![])]),
                node("div", "3", vec![]),
            ],
        );
        let new = node(
            "main",
            "4",
            vec![
                ignored("5", vec![node("div", "6", vec![])]),
                node("div", "7", vec![]),
            ],
        );
        let fresh = HashMap::from([
            ("6".into(), "discarded".into()),
            ("7".into(), "fresh".into()),
        ]);
        let previous = HashSet::from(["2".into(), "3".into()]);
        let (fresh_paths, prior_paths) = plan(
            Some(&old),
            &new,
            &fresh,
            &previous,
            &HashSet::from(["5".into()]),
        )
        .unwrap();
        let mut delivered = new;
        djust_vdom::splice_ignore_subtrees(&old, &mut delivered);
        djust_vdom::sync_ids(&old, &mut delivered);
        assert_eq!(
            rekey(&delivered, fresh_paths).unwrap(),
            vec![("fresh".into(), "3".into())]
        );
        assert_eq!(
            rekey(&delivered, prior_paths).unwrap(),
            vec![("2".into(), "2".into())]
        );
    }

    #[test]
    fn rendered_ignore_attribute_is_not_retention_authority() {
        let old = ignored("0", vec![node("div", "1", vec![])]);
        let new = ignored("2", vec![]);
        assert_eq!(
            plan(
                Some(&old),
                &new,
                &HashMap::new(),
                &HashSet::from(["1".into()]),
                &HashSet::new()
            )
            .unwrap_err(),
            "unauthored ignore boundary cannot retain child ownership"
        );
    }

    #[test]
    fn changed_host_tag_does_not_copy_or_retain_children() {
        let old = ignored("0", vec![node("div", "1", vec![])]);
        let mut new = ignored("2", vec![]);
        new.tag = "article".into();
        assert!(plan(
            Some(&old),
            &new,
            &HashMap::new(),
            &HashSet::from(["1".into()]),
            &HashSet::new()
        )
        .unwrap()
        .1
        .is_empty());
    }

    #[test]
    fn missing_and_duplicate_binding_nodes_are_rejected() {
        let duplicate = node(
            "main",
            "0",
            vec![node("div", "1", vec![]), node("div", "1", vec![])],
        );
        assert_eq!(
            plan(
                None,
                &duplicate,
                &HashMap::new(),
                &HashSet::new(),
                &HashSet::new()
            )
            .unwrap_err(),
            "duplicate VDOM ID"
        );
        assert_eq!(
            plan(
                None,
                &node("main", "0", vec![]),
                &HashMap::from([("1".into(), "key".into())]),
                &HashSet::new(),
                &HashSet::new()
            )
            .unwrap_err(),
            "fresh child not present in VDOM"
        );
    }
}
