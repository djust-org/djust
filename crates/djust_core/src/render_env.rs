//! The render environment as a VALUE (ADR-029, #2741).
//!
//! Every per-render Django setting Rust needs is a `thread_local!` cell —
//! `NUMBER_FORMAT` / `UNLOCALIZED_NUMBER_FORMAT` (`locale.rs`), `ACTIVE_TZ`
//! (`djust_templates::timezone`) and the `{% localize %}` base scope
//! (`djust_templates::renderer`). Python pushes them per render
//! (`djust.render_env.apply_render_env`) on the thread it is about to render
//! on, and that is correct for every entry that renders on the pushing
//! thread.
//!
//! It is silently wrong for a render on a thread that never pushed: the
//! `ViewActor` under `use_actors = True` renders on a tokio worker and reads
//! every cell at its compiled default, so `TIME_ZONE`, the locale's number
//! format — and, until ADR-027 Step 5 (#2628) deleted it, the
//! ADR-027 kill-switch flag — were all ignored there (#2741, observed by
//! the #2751 probe).
//!
//! ADR-029's decision is to carry the environment the way `template_auto_call`
//! is carried — as a field on the backend, applied by every render entry —
//! and this struct is that field's type. It is the SNAPSHOT of what the cells
//! held when Python pushed them; the cells stay the single reading mechanism,
//! and the backend installs the snapshot into them under a guard for the
//! duration of one render (`djust_templates::render_env::RenderEnvGuard`).
//!
//! The struct lives here rather than beside the guard because the guard needs
//! cells from BOTH crates (the timezone and l10n scope are `djust_templates`',
//! the rest are this crate's), and `djust_templates` depends on this crate,
//! not the other way round.

use crate::locale::NumberFormat;

/// One render's ambient Django settings, as pushed by
/// `djust.render_env.apply_render_env` (#2209 timezone, #2221 / #2266 number
/// formats) plus the `{% localize %}` base scope. (ADR-027's resolution flag
/// rode here until Step 5, #2628, deleted it.)
///
/// `Default` is what a thread that never pushed reads: no zone, no number
/// format, no forced l10n scope.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct RenderEnv {
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
