//! The render environment as a VALUE (ADR-029, #2741).
//!
//! Every per-render Django setting Rust needs is a `thread_local!` cell —
//! `RESOLVE_LAZY` (this crate's `lib.rs`), `NUMBER_FORMAT` /
//! `UNLOCALIZED_NUMBER_FORMAT` (`locale.rs`), `ACTIVE_TZ`
//! (`djust_templates::timezone`) and the `{% localize %}` base scope
//! (`djust_templates::renderer`). Python pushes them per render
//! (`djust.render_env.apply_render_env`) on the thread it is about to render
//! on, and that is correct for every entry that renders on the pushing
//! thread.
//!
//! It is silently wrong for a render on a thread that never pushed: the
//! `ViewActor` under `use_actors = True` renders on a tokio worker and reads
//! every cell at its compiled default, so `template_resolve_lazy: False`,
//! `TIME_ZONE` and the locale's number format were all ignored there
//! (#2741, observed by the #2751 probe).
//!
//! ADR-029's decision is to carry the environment the way `template_auto_call`
//! is carried — as a field on the backend, applied by every render entry —
//! and this struct is that field's type. It is the SNAPSHOT of what the cells
//! held when Python pushed them; the cells stay the single reading mechanism
//! (the eight `resolve_lazy()` readers do not change), and the backend
//! installs the snapshot into them under a guard for the duration of one
//! render (`djust_templates::render_env::RenderEnvGuard`).
//!
//! The struct lives here rather than beside the guard because the guard needs
//! cells from BOTH crates (the timezone and l10n scope are `djust_templates`',
//! the rest are this crate's), and `djust_templates` depends on this crate,
//! not the other way round.

use crate::locale::NumberFormat;

/// One render's ambient Django settings, as pushed by
/// `djust.render_env.apply_render_env` (#2209 timezone, #2221 / #2266 number
/// formats, #2539 resolution flag) plus the `{% localize %}` base scope.
///
/// `Default` is what a thread that never pushed reads: `resolve_lazy` ON
/// (the shipped ADR-027 default — the literal is pinned against the Python
/// default by `test_the_rust_default_tracks_the_python_default`), no zone,
/// no number format, no forced l10n scope.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RenderEnv {
    /// ADR-027's lazy-resolution flag (`LIVEVIEW_CONFIG["template_resolve_lazy"]`).
    /// Rides on this struct only until ADR-027 Step 5 deletes the flag.
    pub resolve_lazy: bool,
    /// IANA zone name `date` / `time` formatting converts into, or `None`
    /// for "leave the value in the offset it arrived with" (`USE_TZ = False`).
    /// A name rather than a parsed zone so this crate needs no tz database;
    /// the guard parses it through `timezone::set_active_timezone`, whose
    /// unknown-zone answer (leave the previous value) it inherits.
    pub timezone: Option<String>,
    /// The active language's number format (`{{ n }}`, `floatformat`).
    pub number_format: Option<NumberFormat>,
    /// Django's `use_l10n=False` format (`floatformat`'s `u` suffix, #2266).
    pub unlocalized_number_format: Option<NumberFormat>,
    /// The `{% localize %}` scope a render STARTS in: `Some(false)` forces
    /// localization off before any tag runs, `None` is Django's default.
    /// Nothing in Python pushes this today (the stack is entered lexically by
    /// `{% localize %}`), so a captured environment always carries `None`;
    /// it is here so the guard can reset a stack a panicking render left
    /// populated, which is the same leak class as the other cells.
    pub use_l10n: Option<bool>,
}

impl Default for RenderEnv {
    fn default() -> Self {
        Self {
            resolve_lazy: crate::RESOLVE_LAZY_DEFAULT,
            timezone: None,
            number_format: None,
            unlocalized_number_format: None,
            use_l10n: None,
        }
    }
}
