//! Deep-cascade `dj-if` torture (#1418).
//!
//! ## What this exercises
//!
//! Trees with 10+ levels of nested `dj-if` boundaries (think nested
//! `{% if %}` / `{% elif %}` / `{% else %}` chains), with the nested
//! chain placed at varying positions in the parent's children list
//! (first / middle / last). Toggling the deepest boundaries across
//! cycles tests that:
//!
//! - Boundary-id-keyed Insert/RemoveSubtree dispatch (#1358) resolves
//!   correctly at every level of nesting.
//! - `sync_ids` (#1408) keeps the `last_vdom` mirror consistent with
//!   the client tracker across many cycles, at depth.
//! - Patch handles always resolve (no #1408-class silent drops).
//!
//! ## Why depth matters
//!
//! Pre-#1358 (positional path tracking) and pre-#1408 (positional
//! `sync_ids`) walked siblings positionally; sibling shifts at one
//! level cascaded into mis-alignment at every deeper level. Depth-10+
//! cascades amplify any positional misalignment by a factor of 2^depth
//! across toggle patterns.
//!
//! ## What's complementary coverage
//!
//! Existing torture tests cover shallow chains and same-level
//! independent boundaries. This file covers DEEP cascades (10/12/15
//! levels), which exercise the recursion paths in `diff_nodes` and
//! `sync_ids_indexed` at depths that simpler scenarios never reach.

use djust_vdom::diff::sync_ids;
use djust_vdom::{diff, VNode};

mod common;
use common::{
    apply_all, assert_handles_resolve, dj_if_close, dj_if_open, elem, elem_with_text, IdGen,
};

// =============================================================================
// Cycle helper — matches the production loop
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
// Builders
// =============================================================================

/// Position at which to place the nested dj-if cascade within the
/// parent's children list.
#[derive(Copy, Clone, Debug)]
enum CascadePosition {
    First,
    Middle,
    Last,
}

/// Build a depth-N cascade of nested `dj-if` boundaries. `actives` is
/// a bitmask where bit `i` says whether level `i` is active (renders
/// its body) or empty.
///
/// The structure at each level is:
/// ```text
/// <!--dj-if id="if-level-{i}"-->
///   <div>level {i} body</div>
///   [next level, OR text leaf at deepest]
/// <!--/dj-if-->
/// ```
fn build_cascade(depth: usize, actives: u64, c: &IdGen) -> Vec<VNode> {
    fn inner(level: usize, depth: usize, actives: u64, c: &IdGen) -> Vec<VNode> {
        if level == depth {
            return vec![elem_with_text("span", &format!("leaf-level-{}", level), c)];
        }
        let mut out = vec![dj_if_open(&format!("if-cascade-{}", level))];
        let active = (actives >> level) & 1 != 0;
        if active {
            out.push(elem_with_text("div", &format!("body-level-{}", level), c));
            out.extend(inner(level + 1, depth, actives, c));
        }
        out.push(dj_if_close());
        out
    }
    inner(0, depth, actives, c)
}

/// Build a parent that contains stable siblings + a deep cascade at
/// the requested position.
fn build_tree(depth: usize, actives: u64, pos: CascadePosition, c: &IdGen) -> VNode {
    let parent = elem("div", c);

    let cascade = build_cascade(depth, actives, c);
    let leading = elem_with_text("header", "leading-stable", c);
    let trailing = elem_with_text("footer", "trailing-stable", c);
    let middle_sib = elem_with_text("p", "middle-stable", c);

    let children: Vec<VNode> = match pos {
        CascadePosition::First => {
            let mut v = cascade;
            v.push(leading);
            v.push(trailing);
            v
        }
        CascadePosition::Middle => {
            let mut v = vec![leading];
            v.extend(cascade);
            v.push(trailing);
            v
        }
        CascadePosition::Last => {
            let mut v = vec![leading, middle_sib];
            v.extend(cascade);
            v
        }
    };
    parent.with_children(children)
}

// =============================================================================
// Scenarios
// =============================================================================

/// Scenario 1: depth-10 cascade in the MIDDLE of children. Toggle the
/// deepest boundary off/on then the second-deepest off/on across many
/// cycles. Asserts handles resolve every cycle.
#[test]
fn deep_cascade_depth_10_middle_position() {
    let c = IdGen::new();
    let depth = 10;
    let pos = CascadePosition::Middle;
    let all_on: u64 = (1u64 << depth) - 1; // bits 0..depth-1 set
    let initial = build_tree(depth, all_on, pos, &c);
    let mut last_vdom = initial.clone();
    let mut client = initial;

    // Iterate through toggling each level off and back on, in order
    // deepest-first.
    for level in (0..depth).rev() {
        let actives_off = all_on & !(1u64 << level);
        run_cycle(
            &format!("d10_mid_level_{}_off", level),
            &mut last_vdom,
            &mut client,
            build_tree(depth, actives_off, pos, &c),
        );
        run_cycle(
            &format!("d10_mid_level_{}_on", level),
            &mut last_vdom,
            &mut client,
            build_tree(depth, all_on, pos, &c),
        );
    }
}

/// Scenario 2: depth-12 cascade in the FIRST-child position. Toggle
/// pairs of adjacent levels simultaneously across cycles. Exercises
/// path tracking when the cascade is at the start of the children
/// list (no leading siblings to absorb misalignment).
#[test]
fn deep_cascade_depth_12_first_position() {
    let c = IdGen::new();
    let depth = 12;
    let pos = CascadePosition::First;
    let all_on: u64 = (1u64 << depth) - 1;
    let initial = build_tree(depth, all_on, pos, &c);
    let mut last_vdom = initial.clone();
    let mut client = initial;

    // Toggle adjacent pairs of levels off + back on.
    for level in (0..depth - 1).step_by(2) {
        let actives_pair_off = all_on & !((1u64 << level) | (1u64 << (level + 1)));
        run_cycle(
            &format!("d12_first_pair_{}_{}_off", level, level + 1),
            &mut last_vdom,
            &mut client,
            build_tree(depth, actives_pair_off, pos, &c),
        );
        run_cycle(
            &format!("d12_first_pair_{}_{}_on", level, level + 1),
            &mut last_vdom,
            &mut client,
            build_tree(depth, all_on, pos, &c),
        );
    }
}

/// Scenario 3: depth-15 cascade in the LAST-child position. Sweep
/// through several pseudo-random toggle masks across 12 cycles.
/// Exercises path tracking when the cascade tail has no trailing
/// siblings (errors accumulate without a stable trailing anchor).
#[test]
fn deep_cascade_depth_15_last_position() {
    let c = IdGen::new();
    let depth = 15;
    let pos = CascadePosition::Last;
    let all_on: u64 = (1u64 << depth) - 1;
    let initial = build_tree(depth, all_on, pos, &c);
    let mut last_vdom = initial.clone();
    let mut client = initial;

    // Pseudo-random masks (within the depth-15 bit range)
    let masks: [u64; 12] = [
        all_on,
        all_on & !0b101_0101_0101_0101,
        all_on & !0b010_1010_1010_1010,
        all_on & !0b111_0000_1111_0000,
        all_on & !0b000_1111_0000_1111,
        all_on & !0b110_1100_1101_1011,
        all_on & !0b001_0011_0010_0100,
        all_on & !0b100_0000_0000_0001,
        all_on,
        0, // all off
        all_on & 0b001_0010_0100_1001,
        all_on,
    ];

    for (i, mask) in masks.iter().enumerate() {
        run_cycle(
            &format!("d15_last_mask_{}_{:015b}", i, mask & all_on),
            &mut last_vdom,
            &mut client,
            build_tree(depth, *mask, pos, &c),
        );
    }
}

/// Scenario 4: depth-10 cascade in the MIDDLE, but with the DEEPEST
/// boundary flipping every cycle (boundary at the bottom of a long
/// recursion chain is the most likely to be mis-targeted by positional
/// alignment bugs).
#[test]
fn deep_cascade_depth_10_deepest_only_alternates() {
    let c = IdGen::new();
    let depth = 10;
    let pos = CascadePosition::Middle;
    let deepest_bit = 1u64 << (depth - 1);
    let all_on: u64 = (1u64 << depth) - 1;
    let alternate_off = all_on & !deepest_bit;

    let initial = build_tree(depth, all_on, pos, &c);
    let mut last_vdom = initial.clone();
    let mut client = initial;

    for i in 0..16 {
        let mask = if i % 2 == 0 { alternate_off } else { all_on };
        run_cycle(
            &format!("d10_deepest_alt_{}", i),
            &mut last_vdom,
            &mut client,
            build_tree(depth, mask, pos, &c),
        );
    }
}
