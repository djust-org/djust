//! Regression tests for issue #1254: promote diff-time warnings from
//! `vdom_trace!()` (gated on `DJUST_VDOM_TRACE=1`) to `tracing::warn!`
//! so they fire by default in production.
//!
//! - DJE-050: mixed keyed/unkeyed siblings (a common source of suboptimal
//!   keyed-diffing performance).
//! - DJE-051: duplicate `dj-key` siblings — the earlier sibling is
//!   silently overwritten in the key→index map and becomes invisible to
//!   the keyed diff algorithm.
//!
//! Both warnings used to print to stderr ONLY when the user set the
//! `DJUST_VDOM_TRACE=1` env var, which means production users almost
//! never saw them. Promoting them to `tracing::warn!` puts them in the
//! standard log stream and makes them surfaceable via `RUST_LOG=warn`.

use djust_vdom::{diff, VNode};
use std::sync::{Arc, Mutex};
use tracing::field::{Field, Visit};
use tracing::{Event, Subscriber};
use tracing_subscriber::layer::{Context, SubscriberExt};
use tracing_subscriber::Layer;

#[derive(Default)]
struct CapturedEvent {
    level: String,
    message: String,
}

#[derive(Default, Clone)]
struct CaptureLayer {
    events: Arc<Mutex<Vec<CapturedEvent>>>,
}

struct MessageVisitor<'a> {
    out: &'a mut String,
}

impl<'a> Visit for MessageVisitor<'a> {
    fn record_debug(&mut self, field: &Field, value: &dyn std::fmt::Debug) {
        if field.name() == "message" {
            self.out.push_str(&format!("{:?}", value));
        }
    }
    fn record_str(&mut self, field: &Field, value: &str) {
        if field.name() == "message" {
            self.out.push_str(value);
        }
    }
}

impl<S> Layer<S> for CaptureLayer
where
    S: Subscriber,
{
    fn on_event(&self, event: &Event<'_>, _ctx: Context<'_, S>) {
        let metadata = event.metadata();
        let mut message = String::new();
        let mut visitor = MessageVisitor { out: &mut message };
        event.record(&mut visitor);
        let mut events = self.events.lock().unwrap();
        events.push(CapturedEvent {
            level: metadata.level().to_string(),
            message,
        });
    }
}

fn run_with_capture<F: FnOnce()>(f: F) -> Vec<CapturedEvent> {
    let layer = CaptureLayer::default();
    let events = layer.events.clone();
    let subscriber = tracing_subscriber::registry().with(layer);
    tracing::subscriber::with_default(subscriber, f);
    let guard = events.lock().unwrap();
    guard
        .iter()
        .map(|e| CapturedEvent {
            level: e.level.clone(),
            message: e.message.clone(),
        })
        .collect()
}

/// DJE-050 — mixed keyed/unkeyed siblings should emit a `WARN`-level
/// tracing event by default.
#[test]
fn test_dje_050_mixed_keyed_unkeyed_emits_warn() {
    let old = VNode::element("ul").with_children(vec![VNode::element("li")
        .with_key("a")
        .with_child(VNode::text("a"))]);
    let new = VNode::element("ul").with_children(vec![
        VNode::element("li")
            .with_key("a")
            .with_child(VNode::text("a")),
        VNode::element("li").with_child(VNode::text("b")), // unkeyed
    ]);

    let captured = run_with_capture(|| {
        let _patches = diff(&old, &new);
    });

    let warn_events: Vec<_> = captured
        .iter()
        .filter(|e| e.level == "WARN" && e.message.contains("DJE-050"))
        .collect();
    assert!(
        !warn_events.is_empty(),
        "REGRESSION #1254: mixed keyed/unkeyed siblings must emit a \
         WARN-level tracing event tagged DJE-050. Got events: {:?}",
        captured
            .iter()
            .map(|e| (&e.level, &e.message))
            .collect::<Vec<_>>()
    );
    let msg = &warn_events[0].message;
    assert!(
        msg.contains("Mixed keyed/unkeyed"),
        "DJE-050 warning should describe the issue. Got: {:?}",
        msg
    );
}

/// DJE-051 — duplicate `dj-key` siblings should emit a `WARN`-level
/// tracing event by default.
#[test]
fn test_dje_051_duplicate_keys_emits_warn() {
    let old = VNode::element("ul").with_children(vec![
        VNode::element("li")
            .with_key("dup")
            .with_child(VNode::text("a")),
        VNode::element("li")
            .with_key("other")
            .with_child(VNode::text("b")),
    ]);
    // Duplicate key in NEW children — the diff function builds a key→index
    // map for new children and the duplicate triggers DJE-051.
    let new = VNode::element("ul").with_children(vec![
        VNode::element("li")
            .with_key("dup")
            .with_child(VNode::text("a")),
        VNode::element("li")
            .with_key("dup")
            .with_child(VNode::text("b")),
    ]);

    let captured = run_with_capture(|| {
        let _patches = diff(&old, &new);
    });

    let warn_events: Vec<_> = captured
        .iter()
        .filter(|e| e.level == "WARN" && e.message.contains("DJE-051"))
        .collect();
    assert!(
        !warn_events.is_empty(),
        "REGRESSION #1254: duplicate dj-key siblings must emit a \
         WARN-level tracing event tagged DJE-051. Got events: {:?}",
        captured
            .iter()
            .map(|e| (&e.level, &e.message))
            .collect::<Vec<_>>()
    );
    let msg = &warn_events[0].message;
    assert!(
        msg.contains("Duplicate") || msg.contains("duplicate"),
        "DJE-051 warning should mention duplicates. Got: {:?}",
        msg
    );
    assert!(
        msg.contains("dup"),
        "DJE-051 warning should include the duplicate key value. Got: {:?}",
        msg
    );
}

/// DJE-051 — duplicate keys in the OLD children should also emit a
/// WARN-level event (the audit cited the old-children loop too).
#[test]
fn test_dje_051_duplicate_keys_in_old_children_emits_warn() {
    let old = VNode::element("ul").with_children(vec![
        VNode::element("li")
            .with_key("dup")
            .with_child(VNode::text("a")),
        VNode::element("li")
            .with_key("dup")
            .with_child(VNode::text("b")),
    ]);
    let new = VNode::element("ul").with_children(vec![
        VNode::element("li")
            .with_key("dup")
            .with_child(VNode::text("a")),
        VNode::element("li")
            .with_key("other")
            .with_child(VNode::text("b")),
    ]);

    let captured = run_with_capture(|| {
        let _patches = diff(&old, &new);
    });

    let warn_events: Vec<_> = captured
        .iter()
        .filter(|e| e.level == "WARN" && e.message.contains("DJE-051"))
        .collect();
    assert!(
        !warn_events.is_empty(),
        "REGRESSION #1254: duplicate dj-key in old children must emit \
         a WARN-level tracing event tagged DJE-051. Got events: {:?}",
        captured
            .iter()
            .map(|e| (&e.level, &e.message))
            .collect::<Vec<_>>()
    );
}

/// Negative case — well-formed keyed diffing emits no DJE-050/051 warning.
#[test]
fn test_clean_keyed_diff_emits_no_warnings() {
    let old = VNode::element("ul").with_children(vec![
        VNode::element("li")
            .with_key("a")
            .with_child(VNode::text("a")),
        VNode::element("li")
            .with_key("b")
            .with_child(VNode::text("b")),
    ]);
    let new = VNode::element("ul").with_children(vec![
        VNode::element("li")
            .with_key("b")
            .with_child(VNode::text("b")),
        VNode::element("li")
            .with_key("a")
            .with_child(VNode::text("a")),
    ]);

    let captured = run_with_capture(|| {
        let _patches = diff(&old, &new);
    });

    let dje_events: Vec<_> = captured
        .iter()
        .filter(|e| {
            e.level == "WARN" && (e.message.contains("DJE-050") || e.message.contains("DJE-051"))
        })
        .collect();
    assert!(
        dje_events.is_empty(),
        "well-formed keyed diff should emit no DJE-050/051 warnings. \
         Got: {:?}",
        dje_events
            .iter()
            .map(|e| (&e.level, &e.message))
            .collect::<Vec<_>>()
    );
}
