//! Capturing and installing a [`RenderEnv`] (ADR-029, #2741).
//!
//! [`djust_core::RenderEnv`] is the value; this module is the two operations
//! on it that need every cell at once — and the cells span two crates, which
//! is why the operations live in the downstream one:
//!
//! * [`capture`] snapshots the CALLING thread's cells. Python calls it (via
//!   the backend's `capture_render_env`) right after
//!   `djust.render_env.apply_render_env` has pushed them, on the thread that
//!   pushed — so the snapshot is exactly what that push would have made a
//!   render on this thread read.
//! * [`RenderEnvGuard::install`] writes a snapshot INTO the cells of whatever
//!   thread the render is actually happening on — a tokio worker for the
//!   actor path — and its `Drop` puts the previous values back. That is the
//!   "SET, not scoped" fix `python/djust/render_env.py` documents as the
//!   contract: a render entry now leaves the cells as it found them.
//!
//! The guard is the same idiom as the four that already exist —
//! `DepthGuard` (`djust_core/src/lib.rs`), `UseL10nGuard` and
//! `ActiveTimezoneGuard` (`renderer.rs`), `LoopCacheGuard` (`loop_cache.rs`)
//! — differing only in that those scope a MID-render change back to the
//! pre-render push, while this one scopes the push itself. `Drop` runs on
//! the panic path too, exactly as theirs do (#2597).

use djust_core::locale::{
    number_format, set_number_format, set_unlocalized_number_format, unlocalized_number_format,
};
use djust_core::RenderEnv;

use crate::renderer::replace_use_l10n_stack;
use crate::timezone::{active_timezone_name, set_active_timezone};

/// Snapshot the calling thread's render cells.
///
/// The `{% localize %}` base scope is the BOTTOM of the stack (the value a
/// render starts in); anything above it is a lexical scope some in-flight
/// block pushed, which is not configuration and is not captured.
pub fn capture() -> RenderEnv {
    let stack = replace_use_l10n_stack(Vec::new());
    let use_l10n = stack.first().copied();
    replace_use_l10n_stack(stack);
    RenderEnv {
        timezone: active_timezone_name(),
        number_format: number_format(),
        unlocalized_number_format: unlocalized_number_format(),
        use_l10n,
    }
}

/// Write `env` into this thread's cells (no restore — see [`RenderEnvGuard`]).
///
/// The timezone keeps `set_active_timezone`'s contract: a name the bundled
/// tz database does not know leaves the previous zone in place rather than
/// failing the render, which is what the Python push does too.
fn apply(env: &RenderEnv) {
    set_active_timezone(env.timezone.as_deref());
    set_number_format(env.number_format.clone());
    set_unlocalized_number_format(env.unlocalized_number_format.clone());
    replace_use_l10n_stack(env.use_l10n.map(|flag| vec![flag]).unwrap_or_default());
}

/// Installs a [`RenderEnv`] into the current thread's cells for the guard's
/// lifetime and restores what was there on drop — including the full
/// `{% localize %}` stack, not just its base, so a render nested inside a
/// `{% localize %}` block of an outer render hands the block back intact.
///
/// Hold it across the whole render (`let _env = RenderEnvGuard::install(..)`
/// at the top of the entry). A render that never installs one reads the
/// cells as they are, which is the pre-ADR-029 behaviour every direct
/// `_rust.render_template` caller still gets.
#[must_use = "the environment is restored when the guard drops — bind it for the render's lifetime"]
pub struct RenderEnvGuard {
    prev: RenderEnv,
    prev_l10n_stack: Vec<bool>,
}

impl RenderEnvGuard {
    /// Capture the current cells, then install `env`.
    pub fn install(env: &RenderEnv) -> Self {
        let prev = capture();
        // `capture` put the stack back; take it again to own the whole thing.
        let prev_l10n_stack = replace_use_l10n_stack(Vec::new());
        apply(env);
        RenderEnvGuard {
            prev,
            prev_l10n_stack,
        }
    }
}

impl Drop for RenderEnvGuard {
    fn drop(&mut self) {
        apply(&self.prev);
        // `apply` reset the stack to the previous BASE; put the full previous
        // stack back over it.
        replace_use_l10n_stack(std::mem::take(&mut self.prev_l10n_stack));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use djust_core::locale::NumberFormat;

    fn fr() -> NumberFormat {
        NumberFormat {
            decimal_sep: ",".into(),
            thousand_sep: "\u{a0}".into(),
            grouping: vec![3, 0],
            use_grouping: true,
        }
    }

    /// Poison every cell, install a clean environment, and assert the poison
    /// comes back on drop — the whole point of the guard (ADR-029 §2 item 4).
    #[test]
    fn install_scopes_the_cells_and_drop_restores_the_poisoned_ones() {
        // Poison, the way a previous render on a pooled thread would leave
        // things (the #2728 shape: a `fr` push nobody restored).
        set_active_timezone(Some("Asia/Tokyo"));
        set_number_format(Some(fr()));
        set_unlocalized_number_format(Some(fr()));
        replace_use_l10n_stack(vec![false, true]);
        let poisoned = capture();
        assert_eq!(poisoned.timezone.as_deref(), Some("Asia/Tokyo"));
        assert_eq!(poisoned.use_l10n, Some(false));

        let clean = RenderEnv {
            timezone: Some("Europe/Paris".to_string()),
            ..RenderEnv::default()
        };
        {
            let _guard = RenderEnvGuard::install(&clean);
            assert_eq!(capture(), clean, "inside the guard the cells ARE the env");
            assert_eq!(number_format(), None);
        }
        // Everything back, including the stack above the base scope.
        assert_eq!(capture(), poisoned);
        assert_eq!(replace_use_l10n_stack(Vec::new()), vec![false, true]);

        // Leave the thread clean for whoever shares it.
        apply(&RenderEnv::default());
    }

    /// The restore runs while unwinding, like `UseL10nGuard` (#2597).
    #[test]
    fn drop_restores_on_the_panic_path_too() {
        apply(&RenderEnv::default());
        set_active_timezone(Some("America/New_York"));
        set_number_format(Some(fr()));
        let env = RenderEnv {
            timezone: Some("UTC".to_string()),
            number_format: None,
            ..RenderEnv::default()
        };
        let panicked = std::panic::catch_unwind(|| {
            let _guard = RenderEnvGuard::install(&env);
            assert_eq!(active_timezone_name().as_deref(), Some("UTC"));
            assert_eq!(number_format(), None);
            panic!("a child node blew up");
        });
        assert!(panicked.is_err());
        assert_eq!(active_timezone_name().as_deref(), Some("America/New_York"));
        assert_eq!(
            number_format(),
            Some(fr()),
            "the number format came back with the zone"
        );
        apply(&RenderEnv::default());
    }

    /// `Default` is what a fresh thread reads, so installing it is a no-op on
    /// a fresh thread — and `capture` on one equals it (the two literals
    /// agree, #1646).
    #[test]
    fn a_fresh_thread_captures_the_default() {
        let captured = std::thread::spawn(capture).join().unwrap();
        assert_eq!(captured, RenderEnv::default());
    }
}
