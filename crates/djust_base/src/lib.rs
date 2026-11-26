//! The Python-free base of the djust Rust workspace.
//!
//! This crate holds what `djust_vdom` needs from the rest of the workspace:
//! the shared error type, the HTML whitespace and raw-text rules, and one
//! size helper. With the default features it has no PyO3 dependency, so a
//! Rust host with no Python interpreter can depend on `djust_vdom` with
//! `default-features = false`.
//!
//! `djust_core` re-exports every item here under its old path
//! (`djust_core::errors`, `djust_core::html_whitespace`, `djust_core::raw_text`,
//! `djust_core::retained::table_bytes`), and always enables the `python`
//! feature, which adds the `PyErr` conversions and the
//! `DjangoRustError::PythonException` variant.

pub mod errors;
pub mod html_whitespace;
pub mod raw_text;

pub use errors::{DjangoRustError, Result};

pub mod retained {
    /// Heap bytes of a hashbrown table (`HashMap`, `HashSet`, `AHashMap`) with
    /// `capacity` usable slots of `entry` bytes each: the bucket array plus one
    /// control byte per bucket and one trailing control group.
    pub fn table_bytes(capacity: usize, entry: usize) -> usize {
        if capacity == 0 {
            return 0;
        }
        let buckets = (capacity * 8 / 7 + 1).next_power_of_two();
        buckets * (entry + 1) + 16
    }
}
