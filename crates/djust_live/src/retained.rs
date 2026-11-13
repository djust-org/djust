//! What a view holds between turns, and the one thing views share (#3998).
//!
//! Each open socket keeps a `RustLiveViewBackend`, so its size is memory per
//! socket. [`report`] accounts for it per owner, deterministically (capacities
//! and `size_of`, see `djust_core::retained`). [`shared_source`] is why the
//! template source text is not among the owners.

use super::{RustLiveViewBackend, TextNodeEntry};
use djust_core::Value;
use djust_vdom::VNode;
use once_cell::sync::Lazy;
use pyo3::prelude::*;
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::Arc;

/// One copy of each template source text, shared by every view of it (#3998).
///
/// A view used to own a `String` copy of its template source, so a 50 KB
/// template cost 50 KB per open socket. A source is the same text for every
/// socket of a view class and holds nothing of any user (it is the template,
/// not a render of it), and an `Arc<str>` cannot be written through, so views
/// share it. `update_template` gives the view a different `Arc`; it never
/// changes the text another view reads.
///
/// The table itself holds one reference per source, and drops the sources no
/// view holds any more when it is pruned (hot reload in development would
/// otherwise keep every past version). See [`SourceTable`] for when.
static TEMPLATE_SOURCES: Lazy<std::sync::Mutex<SourceTable>> =
    Lazy::new(|| std::sync::Mutex::new(SourceTable::default()));

/// The table is not pruned below this many sources. Up to this many sources
/// that no view holds any more therefore stay for the life of the process;
/// that is the price of not scanning a small table, and it is bounded.
const SOURCE_TABLE_SLACK: usize = 64;

/// The shared source texts, pruned when the table has doubled.
///
/// Pruning is a scan of the whole table, under the one mutex every view
/// construction takes. Scanning on every new source once the table was past
/// the slack made N distinct live sources cost N scans of N entries. The scan
/// now runs only when the table has grown to twice what the last prune left
/// (never below the slack), so N new sources scan at most about 2N entries in
/// all, and the table holds at most twice the live sources plus the slack.
#[derive(Default)]
struct SourceTable {
    sources: HashSet<Arc<str>>,
    /// The size at which the next prune runs; 0 until the first use.
    prune_at: usize,
    /// Entries examined by every prune so far (what the cost test counts).
    scanned: usize,
}

impl SourceTable {
    fn shared(&mut self, source: &str) -> Arc<str> {
        if let Some(existing) = self.sources.get(source) {
            return existing.clone();
        }
        if self.sources.len() >= self.prune_at.max(SOURCE_TABLE_SLACK) {
            self.scanned += self.sources.len();
            self.sources.retain(|held| Arc::strong_count(held) > 1);
            self.prune_at = (self.sources.len() * 2).max(SOURCE_TABLE_SLACK);
        }
        let shared: Arc<str> = Arc::from(source);
        self.sources.insert(shared.clone());
        shared
    }
}

/// The shared copy of `source`.
pub(super) fn shared_source(source: &str) -> Arc<str> {
    let mut table = match TEMPLATE_SOURCES.lock() {
        Ok(table) => table,
        // A panic while the table was held leaves it intact (insert / retain
        // of whole entries); keep using it.
        Err(poisoned) => poisoned.into_inner(),
    };
    table.shared(source)
}

/// Bytes per owner: the body of `RustLiveView.retained_bytes()`.
pub(super) fn report(view: &RustLiveViewBackend) -> HashMap<String, usize> {
    use djust_core::retained::table_bytes;
    use std::mem::size_of;
    let mut out: HashMap<String, usize> = HashMap::new();
    let strings = |items: &Vec<String>| -> usize {
        items.capacity() * size_of::<String>() + items.iter().map(String::capacity).sum::<usize>()
    };
    let string_set = |items: &HashSet<String>| -> usize {
        table_bytes(items.capacity(), size_of::<String>())
            + items.iter().map(String::capacity).sum::<usize>()
    };
    // The source is shared (`TEMPLATE_SOURCES`): a view owns none of it.
    let template_source = 0usize;
    out.insert(
        "shared_template_source".to_string(),
        view.template_source.len(),
    );
    out.insert(
        "template_source_holders".to_string(),
        // The table's own reference is not a view.
        Arc::strong_count(&view.template_source).saturating_sub(1),
    );
    let state = table_bytes(view.state.capacity(), size_of::<(String, Value)>())
        + view
            .state
            .iter()
            .map(|(k, v)| k.capacity() + v.retained_bytes())
            .sum::<usize>();
    let tree = view
        .last_vdom
        .as_ref()
        .map(VNode::retained_bytes)
        .unwrap_or_default();
    let last_html = view.last_html.as_ref().map_or(0, String::capacity);
    let node_html_cache = strings(&view.node_html_cache);
    let text_node_index = view.text_node_index.as_ref().map_or(0, |index| {
        index.capacity() * size_of::<TextNodeEntry>()
            + index
                .iter()
                .map(|e| {
                    e.path.capacity() * size_of::<usize>()
                        + e.text.capacity()
                        + e.djust_id.capacity()
                })
                .sum::<usize>()
    });
    let fragment_text_map = view.fragment_text_map.as_ref().map_or(0, |map| {
        table_bytes(map.capacity(), size_of::<(usize, (Vec<usize>, String))>())
            + map
                .values()
                .map(|(path, id)| path.capacity() * size_of::<usize>() + id.capacity())
                .sum::<usize>()
    });
    let loop_render_cache = view.loop_render_cache.retained_bytes();
    let pending = view.pending_partial_page.as_ref().map_or(0, |page| {
        page.vdom.retained_bytes().total()
            + page.child_vdom_ids.capacity() * size_of::<(String, String)>()
            + string_set(&page.ignore_vdom_ids)
    });
    let other = size_of::<RustLiveViewBackend>()
        + strings(&view.authenticated_model_fields)
        + view.template_origin.as_ref().map_or(0, String::capacity)
        + view
            .authenticated_view_path
            .as_ref()
            .map_or(0, String::capacity)
        + view.template_dirs.capacity() * size_of::<PathBuf>()
        + view
            .template_dirs
            .iter()
            .map(|dir| dir.capacity())
            .sum::<usize>()
        + string_set(&view.safe_keys)
        + view.changed_keys.as_ref().map_or(0, string_set)
        + string_set(&view.accumulate_ids)
        + view.dj_if_id_namespace.capacity()
        + view.raw_py_values.as_ref().map_or(0, |map| {
            table_bytes(map.capacity(), size_of::<(String, Py<PyAny>)>())
                + map.keys().map(String::capacity).sum::<usize>()
        })
        + pending;
    out.insert("template_source".to_string(), template_source);
    out.insert("state".to_string(), state);
    out.insert("vdom".to_string(), tree.total());
    out.insert("vdom_nodes".to_string(), tree.nodes);
    out.insert("vdom_text_nodes".to_string(), tree.text_nodes);
    out.insert("vdom_node_structs".to_string(), tree.node_structs);
    out.insert("vdom_tags".to_string(), tree.tags);
    out.insert("vdom_attrs".to_string(), tree.attrs);
    out.insert("vdom_text".to_string(), tree.text);
    out.insert("vdom_ids".to_string(), tree.ids);
    out.insert("last_html".to_string(), last_html);
    out.insert("node_html_cache".to_string(), node_html_cache);
    out.insert("text_node_index".to_string(), text_node_index);
    out.insert("fragment_text_map".to_string(), fragment_text_map);
    out.insert("loop_render_cache".to_string(), loop_render_cache);
    out.insert("other".to_string(), other);
    out.insert(
        "total".to_string(),
        template_source
            + state
            + tree.total()
            + last_html
            + node_html_cache
            + text_node_index
            + fragment_text_map
            + loop_render_cache
            + other,
    );
    out
}

/// The template source text is one copy per source, not one per view (#3998).
#[cfg(test)]
mod tests {
    use super::super::*;
    use super::{SourceTable, SOURCE_TABLE_SLACK};

    fn view(source: &str) -> RustLiveViewBackend {
        RustLiveViewBackend::new_rust(source.to_string())
    }

    #[test]
    fn two_views_of_one_source_read_the_same_text() {
        let source = "<div dj-root>shared-source-a {{ n }}</div>";
        let first = view(source);
        let second = view(source);
        assert!(Arc::ptr_eq(&first.template_source, &second.template_source));
        // Two views and the table.
        assert_eq!(Arc::strong_count(&first.template_source), 3);
        assert_eq!(first.retained_bytes()["template_source"], 0);
        assert_eq!(
            first.retained_bytes()["shared_template_source"],
            source.len()
        );
        assert_eq!(first.retained_bytes()["template_source_holders"], 2);
    }

    #[test]
    fn views_of_different_sources_do_not_share() {
        let first = view("<div dj-root>shared-source-b1</div>");
        let second = view("<div dj-root>shared-source-b2</div>");
        assert!(!Arc::ptr_eq(
            &first.template_source,
            &second.template_source
        ));
    }

    /// The stale-value case: one view changing its template must not change
    /// the text the other view renders from.
    #[test]
    fn changing_one_views_template_leaves_the_others_text_alone() {
        let source = "<div dj-root>shared-source-c {{ n }}</div>";
        let changed = "<div dj-root>shared-source-c CHANGED {{ n }}</div>";
        let mut first = view(source);
        let mut second = view(source);
        first.update_template(changed.to_string());
        assert_eq!(&*first.template_source, changed);
        assert_eq!(&*second.template_source, source);
        assert!(!Arc::ptr_eq(
            &first.template_source,
            &second.template_source
        ));

        let mut state = HashMap::new();
        state.insert("n".to_string(), Value::Integer(7));
        first.update_state_rust(state.clone());
        second.update_state_rust(state);
        let (first_html, _, _) = first.render_with_diff_detached().expect("render");
        let (second_html, _, _) = second.render_with_diff_detached().expect("render");
        assert!(first_html.contains("CHANGED"), "{first_html}");
        assert!(!second_html.contains("CHANGED"), "{second_html}");
        assert!(second_html.contains("shared-source-c 7"), "{second_html}");
    }

    #[test]
    fn a_restored_view_shares_the_text_too() {
        let source = "<div dj-root>shared-source-d</div>";
        let first = view(source);
        let serializable = SerializableViewState {
            template_source: first.template_source.to_string(),
            state: HashMap::new(),
            last_vdom: None,
            version: 1,
            timestamp: 0.0,
            authenticated_partial_root: false,
            authenticated_model_fields: Vec::new(),
            potential_authenticated_partial_root: false,
            template_origin: None,
            authenticated_view_path: None,
        };
        let bytes = rmp_serde::to_vec(&serializable).expect("encode");
        let restored = RustLiveViewBackend::deserialize_msgpack(&bytes).expect("decode");
        assert!(Arc::ptr_eq(
            &first.template_source,
            &restored.template_source
        ));
    }

    #[test]
    fn a_source_no_view_holds_leaves_the_table_at_the_next_prune() {
        let mut table = SourceTable::default();
        drop(table.shared("gone"));
        let kept: Vec<Arc<str>> = (0..SOURCE_TABLE_SLACK)
            .map(|i| table.shared(&format!("kept {i}")))
            .collect();
        assert!(!table.sources.contains("gone"));
        for held in &kept {
            assert!(table.sources.contains(&**held));
        }
    }

    #[test]
    fn a_small_table_is_never_scanned() {
        let mut table = SourceTable::default();
        let _kept: Vec<Arc<str>> = (0..SOURCE_TABLE_SLACK - 1)
            .map(|i| table.shared(&format!("small {i}")))
            .collect();
        assert_eq!(table.scanned, 0);
    }

    /// Counted, not timed: the entries every prune examined. With a prune on
    /// each new source past the slack this was about N * N / 2.
    #[test]
    fn distinct_live_sources_cost_a_linear_number_of_scanned_entries() {
        let scanned = |n: usize| -> usize {
            let mut table = SourceTable::default();
            let kept: Vec<Arc<str>> = (0..n).map(|i| table.shared(&format!("live {i}"))).collect();
            assert_eq!(table.sources.len(), kept.len());
            table.scanned
        };
        let (small, large) = (scanned(2_000), scanned(8_000));
        assert!(small > 0, "premise: the table was pruned at all");
        assert!(small <= 2 * 2_000 && large <= 2 * 8_000, "{small} {large}");
        assert!(
            large <= 5 * small,
            "four times the sources: {small} -> {large}"
        );
    }

    #[test]
    fn the_table_holds_at_most_twice_the_live_sources_plus_the_slack() {
        let mut table = SourceTable::default();
        let kept: Vec<Arc<str>> = (0..100)
            .map(|i| table.shared(&format!("held {i}")))
            .collect();
        for i in 0..10_000 {
            drop(table.shared(&format!("passing {i}")));
            assert!(table.sources.len() <= 2 * kept.len() + SOURCE_TABLE_SLACK + 1);
        }
    }
}
