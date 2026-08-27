//! Serialize access to the `django_value_repr` process-global (#2203).
//!
//! `DJANGO_VALUE_REPR` is one `AtomicBool` for the process and cargo runs the
//! tests in a binary on parallel threads, so a test that flips it races every
//! test that reads it. Taking the lock in only ONE state is not enough — that
//! made roughly one run in three red in `test_display_django_parity_2203.rs`,
//! including, memorably, the determinism guard failing non-deterministically —
//! so [`FlagGuard::on`] exists for read-only tests too.
//!
//! Shared rather than copied. `test_display_django_parity_2203.rs` and
//! `test_decimal_value_2214.rs` each grew their own copy, and #2260 was about
//! to add a third and a fourth: N correct copies, one of which the next file
//! forgets (#1646). A `tests/<name>/mod.rs` subdirectory is not itself compiled
//! as a test binary, which is what makes it shareable.
//!
//! `FLAG_LOCK` is a plain `Mutex`, so taking a second guard inside one test
//! DEADLOCKS. A test that needs both states takes one guard and calls
//! `set_django_value_repr` directly for the second.

static FLAG_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

pub struct FlagGuard(#[allow(dead_code)] std::sync::MutexGuard<'static, ()>);

impl FlagGuard {
    /// Hold the lock at the DEFAULT (on) state — for tests that only read.
    #[allow(dead_code)]
    pub fn on() -> Self {
        let g = FLAG_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        djust_core::set_django_value_repr(true);
        FlagGuard(g)
    }

    #[allow(dead_code)]
    pub fn off() -> Self {
        let g = FLAG_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        djust_core::set_django_value_repr(false);
        FlagGuard(g)
    }
}

impl Drop for FlagGuard {
    /// Restores the default even on panic — without this one genuine failure
    /// leaves the flag OFF and cascades into unrelated tests.
    fn drop(&mut self) {
        djust_core::set_django_value_repr(true);
    }
}
