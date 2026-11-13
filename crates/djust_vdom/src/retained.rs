//! Deterministic accounting of the heap a [`VNode`] tree owns (#3998).
//!
//! Computed from capacities and `size_of`, never read from the allocator, so
//! the same tree reports the same bytes on every machine. See
//! `djust_core::retained` for what that does and does not include.

use crate::VNode;
use djust_core::retained::table_bytes;
use std::mem::size_of;

/// What a tree holds, split by what holds it.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct TreeBytes {
    /// Every node in the tree, the root included.
    pub nodes: usize,
    /// Nodes whose `tag` is `#text` or `#comment`.
    pub text_nodes: usize,
    /// The node structs themselves: `nodes * size_of::<VNode>()`, plus the
    /// unused capacity of the `children` vectors.
    pub node_structs: usize,
    /// Tag names.
    pub tags: usize,
    /// Attribute tables and the names and values in them.
    pub attrs: usize,
    /// Text content.
    pub text: usize,
    /// `dj-id`s, keys and cached HTML.
    pub ids: usize,
}

impl TreeBytes {
    pub fn total(&self) -> usize {
        self.node_structs + self.tags + self.attrs + self.text + self.ids
    }
}

impl VNode {
    /// The heap this node and everything under it owns, plus the node itself.
    pub fn retained_bytes(&self) -> TreeBytes {
        let mut out = TreeBytes {
            node_structs: size_of::<VNode>(),
            ..TreeBytes::default()
        };
        self.add_retained(&mut out);
        out
    }

    fn add_retained(&self, out: &mut TreeBytes) {
        out.nodes += 1;
        if self.tag.starts_with('#') {
            out.text_nodes += 1;
        }
        out.tags += self.tag.capacity();
        out.attrs += table_bytes(self.attrs.capacity(), size_of::<(String, String)>())
            + self
                .attrs
                .iter()
                .map(|(k, v)| k.capacity() + v.capacity())
                .sum::<usize>();
        out.text += self.text.as_ref().map_or(0, String::capacity);
        out.ids += self.key.as_ref().map_or(0, String::capacity)
            + self.djust_id.as_ref().map_or(0, String::capacity)
            + self.cached_html.as_ref().map_or(0, String::capacity);
        out.node_structs += self.children.capacity() * size_of::<VNode>();
        for child in &self.children {
            child.add_retained(out);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_leaf_is_one_node_struct_and_its_strings() {
        let node = VNode::text("hello");
        let bytes = node.retained_bytes();
        assert_eq!(bytes.nodes, 1);
        assert_eq!(bytes.text_nodes, 1);
        assert_eq!(bytes.node_structs, size_of::<VNode>());
        assert!(bytes.text >= 5);
    }

    #[test]
    fn children_are_counted_once_each() {
        let mut parent = VNode::element("ul");
        for i in 0..100 {
            let mut li = VNode::element("li");
            li.children.push(VNode::text(format!("row {i}")));
            parent.children.push(li);
        }
        let bytes = parent.retained_bytes();
        assert_eq!(bytes.nodes, 201);
        assert_eq!(bytes.text_nodes, 100);
        assert!(bytes.node_structs >= 201 * size_of::<VNode>());
        assert_eq!(
            bytes.total(),
            bytes.node_structs + bytes.tags + bytes.attrs + bytes.text + bytes.ids
        );
    }
}
