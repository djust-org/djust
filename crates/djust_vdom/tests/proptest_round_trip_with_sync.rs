//! Proptest-randomized multi-cycle `sync_ids` round-trip torture (#1413).
//!
//! ## What this exercises
//!
//! This is the proptest-randomized counterpart to
//! `torture_round_trip_with_sync.rs` (#1412). Where #1412 uses a
//! handful of hand-crafted scenarios (three-branch tab toggle,
//! five-boundary independent toggles, long alternation, same-tag
//! siblings), this file generates RANDOM multi-step sequences of
//! `dj-if` boundary toggles + inner-element mutations and asserts the
//! `assert_handles_resolve` invariant on every cycle.
//!
//! ## The invariant under test
//!
//! For every emitted patch in every cycle, every `djust_id` referenced
//! as a targeting handle (`d`, `child_d`, `ref_d`) MUST be present in
//! the client tracker. The client tracker is a `VNode` evolved by
//! faithfully applying every emitted patch via the harness `apply_all`.
//!
//! If a handle isn't present, the production client's
//! `querySelector('[dj-id="X"]')` returns `null` and the patch silently
//! drops — content from a prior render persists. This is the #1408
//! class of bug.
//!
//! ## What's complementary vs bug-trigger
//!
//! The hand-crafted `torture_five_boundary_independent_toggles` in
//! #1412 is the canonical bug-trigger for #1408. This proptest file is
//! a hardener: 64 cases × 5-20 steps explore configurations that the
//! hand-crafted scenarios don't cover (random toggle masks, random
//! inner mutations, random tree shapes), broadening regression cover
//! beyond what enumeration could practically reach.

use djust_vdom::diff::sync_ids;
use djust_vdom::{diff, VNode};
use proptest::prelude::*;

mod common;
use common::{
    apply_all, assert_handles_resolve, dj_if_close, dj_if_open, elem, elem_with_text, IdGen,
};

// =============================================================================
// Step model
// =============================================================================

/// One mutation step in a generated sequence.
#[derive(Debug, Clone)]
enum Step {
    /// Toggle boundary `i` on or off.
    ToggleBoundary(usize),
    /// Modify the inner text of boundary `i`'s first inner element
    /// (only takes effect if that boundary is currently on).
    MutateInnerText(usize, String),
    /// Insert an extra element into boundary `i`'s body
    /// (only takes effect if that boundary is currently on).
    InsertInner(usize),
    /// Remove the last element from boundary `i`'s body
    /// (only takes effect if that boundary is on and has >1 inner element).
    RemoveInner(usize),
}

fn arb_step(num_boundaries: usize) -> BoxedStrategy<Step> {
    let bnd = 0usize..num_boundaries;
    prop_oneof![
        bnd.clone().prop_map(Step::ToggleBoundary),
        (bnd.clone(), "[a-z0-9 ]{1,12}").prop_map(|(i, t)| Step::MutateInnerText(i, t)),
        bnd.clone().prop_map(Step::InsertInner),
        bnd.prop_map(Step::RemoveInner),
    ]
    .boxed()
}

// =============================================================================
// World model — evolves through steps and is rebuilt as a VNode per cycle
// =============================================================================

#[derive(Debug, Clone)]
struct Boundary {
    /// Whether the boundary is "on" (renders its body).
    on: bool,
    /// Text of each inner element (1..=4 entries when on).
    inner_texts: Vec<String>,
}

#[derive(Debug, Clone)]
struct World {
    boundaries: Vec<Boundary>,
}

impl World {
    fn new(num_boundaries: usize, inner_per_boundary: usize) -> Self {
        let mut boundaries = Vec::with_capacity(num_boundaries);
        for i in 0..num_boundaries {
            let inner_texts = (0..inner_per_boundary)
                .map(|j| format!("bnd{}-init-{}", i, j))
                .collect();
            boundaries.push(Boundary {
                on: true,
                inner_texts,
            });
        }
        Self { boundaries }
    }

    fn apply_step(&mut self, step: &Step) {
        match step {
            Step::ToggleBoundary(i) => {
                if let Some(b) = self.boundaries.get_mut(*i) {
                    b.on = !b.on;
                }
            }
            Step::MutateInnerText(i, t) => {
                if let Some(b) = self.boundaries.get_mut(*i) {
                    if b.on {
                        if let Some(first) = b.inner_texts.first_mut() {
                            *first = t.clone();
                        }
                    }
                }
            }
            Step::InsertInner(i) => {
                if let Some(b) = self.boundaries.get_mut(*i) {
                    if b.on && b.inner_texts.len() < 8 {
                        b.inner_texts
                            .push(format!("ins-bnd{}-{}", i, b.inner_texts.len()));
                    }
                }
            }
            Step::RemoveInner(i) => {
                if let Some(b) = self.boundaries.get_mut(*i) {
                    if b.on && b.inner_texts.len() > 1 {
                        b.inner_texts.pop();
                    }
                }
            }
        }
    }

    fn build(&self, c: &IdGen) -> VNode {
        let parent = elem("div", c);
        let mut children = Vec::new();
        for (i, b) in self.boundaries.iter().enumerate() {
            children.push(dj_if_open(&format!("if-bnd-{}", i)));
            if b.on {
                for t in &b.inner_texts {
                    children.push(elem_with_text("div", t, c));
                }
            }
            children.push(dj_if_close());
            // Stable non-boundary sibling between each pair, with a
            // varied tag so the harness exercises mixed sibling shapes.
            let stable_tag = match i % 3 {
                0 => "span",
                1 => "p",
                _ => "em",
            };
            children.push(elem_with_text(stable_tag, &format!("sib-{}", i), c));
        }
        parent.with_children(children)
    }
}

// =============================================================================
// Cycle helper — matches the production loop and #1412's run_cycle
// =============================================================================

fn run_cycle(label: &str, last_vdom: &mut VNode, client: &mut VNode, new_vdom: VNode) {
    let mut new_vdom = new_vdom;

    let patches = diff(last_vdom, &new_vdom);
    assert_handles_resolve(&patches, client, label);
    apply_all(client, &patches, &new_vdom);

    sync_ids(last_vdom, &mut new_vdom);
    *last_vdom = new_vdom;
}

// =============================================================================
// Proptest cases
// =============================================================================

proptest! {
    #![proptest_config(ProptestConfig {
        cases: 64,
        max_shrink_iters: 256,
        .. ProptestConfig::default()
    })]

    /// Property: across any random sequence of boundary toggles +
    /// inner mutations, every emitted patch's targeting handle resolves
    /// in the client tracker.
    #[test]
    fn proptest_random_toggles_handles_always_resolve(
        num_boundaries in 2usize..=5,
        inner_per_boundary in 1usize..=4,
        steps in prop::collection::vec(arb_step(5), 5..=20),
    ) {
        let c = IdGen::new();
        let mut world = World::new(num_boundaries, inner_per_boundary);

        // Re-bound steps to valid boundary indices for this case
        // (arb_step uses the max 5 for generation; clamp here).
        let initial = world.build(&c);
        let mut last_vdom = initial.clone();
        let mut client = initial;

        for (i, step) in steps.iter().enumerate() {
            // Filter steps that reference out-of-range boundaries.
            let in_range = match step {
                Step::ToggleBoundary(b)
                | Step::MutateInnerText(b, _)
                | Step::InsertInner(b)
                | Step::RemoveInner(b) => *b < num_boundaries,
            };
            if !in_range {
                continue;
            }
            world.apply_step(step);
            let new_vdom = world.build(&c);
            run_cycle(
                &format!("step_{}_{:?}", i, step),
                &mut last_vdom,
                &mut client,
                new_vdom,
            );
        }
    }
}
