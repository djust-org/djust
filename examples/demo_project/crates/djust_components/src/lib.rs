//! Pure Rust implementations of UI components for maximum performance.
//!
//! These components render directly in Rust without template parsing,
//! providing ~1μs rendering time vs ~5-10μs for hybrid template rendering.

use pyo3::prelude::*;
use serde::{Deserialize, Serialize};

pub mod badge;
pub mod button;

pub use badge::Badge;
pub use button::Button;

/// Re-export components for PyO3 module
pub fn register_components(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Badge>()?;
    m.add_class::<Button>()?;
    Ok(())
}
