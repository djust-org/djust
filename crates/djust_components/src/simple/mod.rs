//! Simple stateless components optimized for pure Rust performance.
//!
//! These components are PyO3 classes that render directly in Rust
//! without template parsing, providing ~1μs rendering time.

pub mod alert;
pub mod avatar;
pub mod badge;
pub mod button;
pub mod card;
pub mod divider;
pub mod icon;
pub mod modal;
pub mod progress;
pub mod range;
pub mod spinner;
pub mod switch;
pub mod textarea;
pub mod toast;
pub mod tooltip;

pub use alert::RustAlert;
pub use avatar::RustAvatar;
pub use badge::RustBadge;
pub use button::RustButton;
pub use card::RustCard;
pub use divider::RustDivider;
pub use icon::RustIcon;
pub use modal::RustModal;
pub use progress::RustProgress;
pub use range::RustRange;
pub use spinner::RustSpinner;
pub use switch::RustSwitch;
pub use textarea::RustTextArea;
pub use toast::RustToast;
pub use tooltip::RustTooltip;
