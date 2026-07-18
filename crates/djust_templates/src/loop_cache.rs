//! Per-item loop render cache for large-list `render_with_diff` (issue #1967).
//!
//! # Problem
//!
//! `Node::For` re-renders the loop body from the AST UNCONDITIONALLY for every
//! item on every render. On a pure reorder of a 50-item keyed list (same item
//! content, shuffled order) the per-item subtree is rebuilt from scratch even
//! though the rendered bytes are byte-identical — only the parent's child
//! ordering changed. The render half dominates `render_with_diff`
//! (~8 µs/item, ~84% of cycle on a 50-item reorder).
//!
//! # Design
//!
//! A **persistent** content-hash → rendered-fragment cache that lives on the
//! `RustLiveView` backend across `render_with_diff` calls. On render, hash the
//! For-node body identity plus each item's loop-variable bindings (#2067); a cache HIT reuses the previously rendered
//! fragment, a MISS renders via the AST and inserts it. A reorder = all hits
//! (content unchanged), a content-change of item K = K misses, an append = only
//! the new item misses.
//!
//! Persistence is load-bearing: a per-render cache gives ZERO reorder benefit
//! because each item appears exactly once per render.
//!
//! ## Cacheability — two correctness gates
//!
//! A loop body is content-hash cacheable ONLY if its rendered output is fully
//! determined by the loop item(s). Two distinct gates enforce this:
//!
//! ### Gate 1 — position-dependent loop bodies
//!
//! If the loop body's rendered output depends on the item's POSITION (not just
//! its content), content-hash caching would emit STALE positions = wrong
//! output. In djust's Rust renderer the position-dependent constructs are:
//!
//! * `Node::If` inside the body — emits a `<!--dj-if id="if-<hash>-N-<index>"-->`
//!   marker whose `<index>` is the loop position (#1832).
//! * `Node::Cycle` — `{% cycle %}` advances by `__djust_cycle_counter`.
//! * a nested `Node::For` — composes `__djust_if_loop_path` from the outer
//!   index, so any dj-if inside the nested loop is position-dependent.
//! * a `{{ forloop.* }}` reference (counter/index/first/last/parentloop/…).
//!   The Rust renderer does not currently implement `forloop`, but a future
//!   implementation would make such a body position-dependent — so we treat
//!   any `forloop`-prefixed variable as position-dependent defensively.
//!
//! ### Gate 2 — outer-context reads (#1967 review 🔴)
//!
//! Even a position-INDEPENDENT body can read OUTER-context variables — a prefix,
//! a flag, a currency symbol, a localized label — e.g.
//! `{% for x in xs %}<li>{{ prefix }}-{{ x.name }}</li>{% endfor %}`,
//! `{% with label=flag %}…`, `{% firstof flag x.name %}`. Outer context is
//! constant WITHIN a single render but NOT across renders, and the cache is
//! PERSISTENT across renders — so when an outer var changes while the loop items
//! don't, a reorder (or even a no-op re-render) would serve STALE cached
//! fragments built with the old outer value. The cache is therefore restricted
//! to bodies that read NOTHING but the loop's bound variable name(s): a body
//! root resolving under a loop var (`x.name`/`x.price` → root `x`) is allowed;
//! any non-loop-rooted name (`prefix`, `flag`, `settings.X`) makes the body
//! non-cacheable (it falls back to normal per-item render = correct).
//!
//! [`LoopRenderCache::body_cacheable`] applies BOTH gates, scanning a For body
//! ONCE (the verdict is memoized per For-node body identity) and DISABLING the
//! cache (body rendered normally) for any non-cacheable body. The common
//! data-list case (`{{ item.field }}` only) is cacheable.

use crate::parser::Node;
use std::collections::hash_map::DefaultHasher;
use std::collections::{HashMap, HashSet};
use std::hash::{Hash, Hasher};

/// Persistent per-item loop render cache.
///
/// Keyed by a content hash of the For-node body identity (#2067) plus the
/// loop-variable bindings the body reads (the item value) — each `{% for %}`
/// owns its keyspace. The map persists across `render_with_diff` calls (it is a
/// field on `RustLiveView`), which is what gives a pure reorder its O(changed)
/// win. After each render, [`LoopRenderCache::prune`] retains only the hashes
/// seen this render so the cache size tracks the current item count.
#[derive(Debug, Default)]
pub struct LoopRenderCache {
    /// Whether caching is enabled. Default-off (split-foundation #1122): when
    /// `false` the For-node caching path is not taken and behavior is
    /// byte-identical to before this feature.
    enabled: bool,
    /// content-hash → rendered fragment HTML.
    fragments: HashMap<u64, String>,
    /// Content-hashes seen during the CURRENT render. Used by [`prune`] to drop
    /// stale entries after the render completes. Cleared at render start.
    seen_this_render: HashSet<u64>,
    /// Memoized CACHEABILITY verdict per For-node body, keyed by the body
    /// slice's stable pointer+len identity. Avoids re-scanning the AST on every
    /// render. (The AST is immutable for a cached `Template`, so the body
    /// pointer is a stable identity for the lifetime of the process.) `true` →
    /// the body is content-hash cacheable; `false` → render normally. A body is
    /// cacheable iff it is NOT position-dependent AND it reads NOTHING but the
    /// loop's bound variable(s) — see [`LoopRenderCache::body_cacheable`].
    cacheable_verdict: HashMap<(usize, usize), bool>,
    /// Debug hit counter (cache reuse count this render). Only mutated when the
    /// cache is enabled. Read by tests to prove a reorder yields hits.
    hits: u64,
    /// Debug miss counter (AST renders this render).
    misses: u64,
    /// PARSE cache (#1970): content-hash → the item's PARSED VNode subtree
    /// roots, with dj-ids STRIPPED (re-assigned by a pre-order re-walk on the
    /// assembled tree in `render_with_diff`, so a cached subtree is
    /// position-agnostic — see the module docs and `RustLiveViewBackend`).
    /// Sibling of `fragments`, same content-hash key (an item that hit the
    /// render cache also hits the parse cache), same `prune`/`begin_render`/
    /// `clear`/`set_enabled(false)` lifecycle.
    parsed: HashMap<u64, Vec<djust_vdom::VNode>>,
    /// Per-render item manifest (#1970): one entry per loop item rendered this
    /// render, in document order, recorded ONLY for parse-cache-eligible items
    /// (foster-safe item root tag). Consumed by `render_with_diff` to splice
    /// cached subtrees in for placeholder-emitted items. Cleared at render start.
    manifest: Vec<ManifestEntry>,
    /// Debug parse-cache reuse counter (cached subtrees spliced this render).
    parse_hits: u64,
    /// Debug parse-cache miss counter (item subtrees parsed this render).
    parse_misses: u64,
    /// Per-render random nonce (#1970 security fix). The placeholder sentinel
    /// tag is `dj-pc-<nonce_hex>` — the nonce makes the sentinel UNFORGEABLE so
    /// a loop item that renders a *literal* `<dj-pc ...>` element via
    /// `|safe`/`mark_safe` can neither (a) be mistaken for a real placeholder
    /// (which would strip it + corrupt the reduced HTML during reconstruction)
    /// nor (b) splice a different cached item's subtree into its position
    /// (content-confusion via a crafted `h=`). Regenerated every render in
    /// `begin_render` so it can never be predicted across requests. 0 means
    /// "not yet set" (no render in progress); the placeholder path is inert.
    nonce: u64,
}

/// One loop item recorded in the per-render parse manifest (#1970).
#[derive(Debug, Clone)]
pub struct ManifestEntry {
    /// content-hash of the item (same key as the render + parse caches).
    pub hash: u64,
    /// `true` if the For arm emitted a `<dj-pc h=...>` PLACEHOLDER for this
    /// item (parse-cache HIT — a cached parsed subtree exists). `false` if it
    /// emitted the real item HTML (parse-cache MISS — djust_live parses it and
    /// populates the parse cache).
    pub placeholder: bool,
    /// The item's full rendered HTML. Always present so `render_with_diff` can
    /// (a) reconstruct the full HTML for the fallback / `last_html`, and
    /// (b) parse + cache the subtree on a miss.
    pub item_html: String,
}

impl LoopRenderCache {
    /// Create an enabled or disabled cache.
    pub fn new(enabled: bool) -> Self {
        Self {
            enabled,
            ..Default::default()
        }
    }

    /// Is caching enabled?
    pub fn is_enabled(&self) -> bool {
        self.enabled
    }

    /// Enable or disable caching at runtime (wired from the Python flag).
    pub fn set_enabled(&mut self, enabled: bool) {
        self.enabled = enabled;
        if !enabled {
            // Drop any retained fragments so a later re-enable starts clean and
            // a disabled cache holds no memory.
            self.fragments.clear();
            self.seen_this_render.clear();
            self.cacheable_verdict.clear();
            // #1970 parse cache + manifest follow the same default-off invariant.
            self.parsed.clear();
            self.manifest.clear();
        }
    }

    /// Drop all cached fragments and the cacheability memoization, preserving
    /// the `enabled` flag. Called when the template AST changes
    /// (`update_template`) — the cached fragments AND the body-pointer-keyed
    /// verdicts both reference the OLD AST and must not survive.
    pub fn clear(&mut self) {
        self.fragments.clear();
        self.seen_this_render.clear();
        self.cacheable_verdict.clear();
        self.hits = 0;
        self.misses = 0;
        // #1970: the parsed subtrees also reference the OLD template AST and
        // must not survive a template change; the manifest is per-render.
        self.parsed.clear();
        self.manifest.clear();
        self.parse_hits = 0;
        self.parse_misses = 0;
    }

    /// Begin a render: reset the per-render bookkeeping.
    pub fn begin_render(&mut self) {
        self.seen_this_render.clear();
        self.hits = 0;
        self.misses = 0;
        // #1970: the item manifest is per-render — clear it at the start so a
        // stale manifest can never leak into the next render's parse path,
        // even when a render takes a fast path that never reads it.
        self.manifest.clear();
        self.parse_hits = 0;
        self.parse_misses = 0;
        // #1970 security: fresh per-render nonce so the placeholder sentinel tag
        // (`dj-pc-<nonce>`) is unforgeable by `|safe`/`mark_safe` item content.
        self.nonce = next_render_nonce();
    }

    /// Prune stale entries after a render: keep only the hashes seen this
    /// render so the cache size tracks the current item set (bounds memory).
    pub fn prune(&mut self) {
        if !self.enabled {
            return;
        }
        let seen = &self.seen_this_render;
        self.fragments.retain(|k, _| seen.contains(k));
        // #1970: prune the parse cache by the same seen-this-render set so its
        // size tracks the current item set (bounds memory) — symmetric with
        // the render-fragment prune.
        self.parsed.retain(|k, _| seen.contains(k));
    }

    /// Cache reuse count for the current render (debug / tests).
    pub fn hits(&self) -> u64 {
        self.hits
    }

    /// AST-render count for the current render (debug / tests).
    pub fn misses(&self) -> u64 {
        self.misses
    }

    /// Number of retained fragments (debug / tests).
    pub fn len(&self) -> usize {
        self.fragments.len()
    }

    /// Whether the fragment store is empty (debug / tests).
    pub fn is_empty(&self) -> bool {
        self.fragments.is_empty()
    }

    /// Look up a fragment by content hash, recording the hash as seen this
    /// render and bumping the hit counter on a hit.
    pub fn get(&mut self, hash: u64) -> Option<String> {
        self.seen_this_render.insert(hash);
        match self.fragments.get(&hash) {
            Some(html) => {
                self.hits += 1;
                Some(html.clone())
            }
            None => None,
        }
    }

    /// Insert a freshly rendered fragment under its content hash, recording the
    /// hash as seen this render and bumping the miss counter.
    pub fn insert(&mut self, hash: u64, html: String) {
        self.seen_this_render.insert(hash);
        self.misses += 1;
        self.fragments.insert(hash, html);
    }

    // -----------------------------------------------------------------------
    // #1970 parse cache + per-render manifest.
    //
    // The render cache above turns a reorder into O(changed) RENDER work; this
    // turns it into O(changed) PARSE work too. The For arm records a per-item
    // manifest while rendering; `render_with_diff` consumes it to splice cached
    // parsed subtrees in for the placeholder-emitted items, then re-assigns all
    // dj-ids by a pre-order re-walk so the assembled tree is byte-identical to a
    // full html5ever parse. See `RustLiveViewBackend::render_with_diff`.
    // -----------------------------------------------------------------------

    /// Does the parse cache already hold a parsed subtree for `hash`?
    /// Used by the For arm to decide HIT (emit a placeholder) vs MISS (emit the
    /// real item HTML so djust_live parses + caches it).
    pub fn has_parsed(&self, hash: u64) -> bool {
        self.parsed.contains_key(&hash)
    }

    /// Look up the cached parsed subtree roots for `hash` (clone on hit).
    /// Bumps the parse-hit counter on a hit. The returned roots have their
    /// dj-ids re-assigned by the caller's pre-order re-walk.
    pub fn get_parsed(&mut self, hash: u64) -> Option<Vec<djust_vdom::VNode>> {
        match self.parsed.get(&hash) {
            Some(roots) => {
                self.parse_hits += 1;
                Some(roots.clone())
            }
            None => None,
        }
    }

    /// Insert freshly parsed subtree roots under `hash`, bumping the parse-miss
    /// counter. Caller passes the roots with dj-ids already stripped (or it does
    /// not matter — the assembled tree is re-walked).
    pub fn insert_parsed(&mut self, hash: u64, roots: Vec<djust_vdom::VNode>) {
        self.parse_misses += 1;
        self.parsed.insert(hash, roots);
    }

    /// Record one loop item in the per-render manifest (called by the For arm,
    /// in document order, only for parse-cache-eligible foster-safe items).
    pub fn record_manifest_item(&mut self, hash: u64, placeholder: bool, item_html: String) {
        self.manifest.push(ManifestEntry {
            hash,
            placeholder,
            item_html,
        });
    }

    /// Take the per-render manifest, leaving an empty one in place. Called once
    /// by `render_with_diff` after the render to drive the parse-cache splice.
    pub fn take_manifest(&mut self) -> Vec<ManifestEntry> {
        std::mem::take(&mut self.manifest)
    }

    /// Number of manifest entries recorded this render (debug / tests).
    pub fn manifest_len(&self) -> usize {
        self.manifest.len()
    }

    /// Parse-cache reuse count for the current render (debug / tests).
    pub fn parse_hits(&self) -> u64 {
        self.parse_hits
    }

    /// Item-subtree parse count for the current render (debug / tests).
    pub fn parse_misses(&self) -> u64 {
        self.parse_misses
    }

    /// The current render's placeholder nonce (#1970). The For arm reads this to
    /// emit `<dj-pc-<nonce> ...>`; `render_with_diff` reads it (via the manifest
    /// path) to match + splice ONLY this render's sentinels. 0 = no render.
    pub fn nonce(&self) -> u64 {
        self.nonce
    }

    /// Bump the parse-hit counter (used when a manifest placeholder is spliced
    /// from cache without going through `get_parsed`, keeping the probe honest).
    pub fn bump_parse_hits(&mut self, n: u64) {
        self.parse_hits += n;
    }

    /// Bump the parse-miss counter.
    pub fn bump_parse_misses(&mut self, n: u64) {
        self.parse_misses += n;
    }

    /// Cacheability verdict for a For body, memoized by body identity.
    ///
    /// `true` → the body's rendered output is fully determined by the loop
    /// item(s), so the persistent content-hash cache is SAFE to use. `false` →
    /// render normally (the cache would serve stale fragments).
    ///
    /// A body is cacheable iff BOTH hold:
    ///   1. it is NOT position-dependent (no `{% if %}` / `{% cycle %}` / nested
    ///      loop / `forloop.*` / opaque Python tag — see
    ///      [`body_is_position_dependent`]); AND
    ///   2. it reads NOTHING but the loop's bound variable name(s) — any
    ///      OUTER-context read (`{{ prefix }}`, `{% with l=flag %}`,
    ///      `{% firstof flag x %}`, `settings.X`) makes it non-cacheable
    ///      (#1967 review 🔴). Outer context is constant WITHIN a render but NOT
    ///      across renders, and the cache is persistent across renders — so a
    ///      reorder after an outer-var change would serve stale fragments.
    ///
    /// `var_names` is fixed for a given For-node body (both come from the same
    /// AST node), so memoizing by body identity alone is correct.
    pub fn body_cacheable(&mut self, body: &[Node], var_names: &[String]) -> bool {
        // (data-ptr, len) is a stable identity for an immutable AST slice held
        // by a cached `Template`. Two different bodies cannot share both unless
        // identical in memory, which is fine for a verdict that is a pure
        // function of (body, var_names) — and var_names is constant per body.
        let key = (body.as_ptr() as usize, body.len());
        if let Some(&v) = self.cacheable_verdict.get(&key) {
            return v;
        }
        let v = body_is_cacheable(body, var_names);
        self.cacheable_verdict.insert(key, v);
        v
    }
}

/// Is this loop body content-hash CACHEABLE for the given loop variable names?
///
/// `true` iff the body is NOT position-dependent AND every top-level context
/// root it reads is one of `var_names` (the loop-bound variable(s)). See
/// [`LoopRenderCache::body_cacheable`] for the rationale. This is the conservative
/// (opaque-by-default) decision: any uncertainty → non-cacheable → correct.
pub fn body_is_cacheable(nodes: &[Node], var_names: &[String]) -> bool {
    if body_is_position_dependent(nodes) {
        return false;
    }
    // Cacheable only if the body reads NOTHING but the loop variable(s). A body
    // root that resolves under a loop var (e.g. `x.name`/`x.price` → root `x`)
    // is allowed; a NON-loop-rooted name (`prefix`, `flag`, `settings`) is not.
    let loop_vars: HashSet<&str> = var_names.iter().map(|s| s.as_str()).collect();
    crate::parser::body_root_var_names(nodes)
        .iter()
        .all(|root| loop_vars.contains(root.as_str()))
}

/// Build a content hash from the For-node body identity plus the
/// loop-variable bindings the body reads.
///
/// Hashes the variable name(s) and the item value(s) bound for this iteration.
/// This is only called for CACHEABLE bodies (see [`body_is_cacheable`]), which
/// read NOTHING but the loop variable(s) — so the loop bindings fully determine
/// the body's output *for a given body*. We also fold in the variable
/// NAMES so two loops over different vars don't collide.
///
/// `body_identity` is the For-node body slice's `(ptr, len)` identity — the
/// same stable identity [`LoopRenderCache::body_cacheable`] memoizes on
/// (immutable AST held by a cached `Template`; [`LoopRenderCache::clear`]
/// drops every entry on template change, so a re-parse can never serve stale
/// fragments through a reused allocation). Note the `clear`-on-template-change
/// invariant covers only the PARENT template's own For-bodies; `{% include %}`d
/// bodies live in `PARSED_TEMPLATE_CACHE` (`inheritance.rs`, #2074) and get
/// their staleness safety from the pointer-change-on-mtime mechanism instead
/// (an include edit bumps mtime → a fresh `Arc<[Node]>` → a new `ptr` → a new
/// `body_identity` key → a natural miss), NOT from `clear()`. Folding it in
/// isolates each For-node's keyspace: the `fragments`/`parsed` maps span EVERY
/// `{% for %}` in the view, and without it two sibling loops reusing the same
/// loop-var name over equal-content items collided and cross-rendered each
/// other's fragments (#2067).
///
/// `bindings` is the list of `(var_name, value)` pairs set for this iteration
/// (one for `{% for x in xs %}`, several for tuple unpacking).
pub fn content_hash(body_identity: (usize, usize), bindings: &[(&str, &djust_core::Value)]) -> u64 {
    let mut hasher = DefaultHasher::new();
    // Domain separator so an empty binding list still hashes distinctly.
    // (v2: #2067 folded the For-node body identity into the key.)
    "djust_loop_item_v2".hash(&mut hasher);
    body_identity.hash(&mut hasher);
    for (name, value) in bindings {
        name.hash(&mut hasher);
        hash_value(value, &mut hasher);
    }
    hasher.finish()
}

/// The dj-pc placeholder element tag PREFIX (#1970). The actual sentinel tag is
/// `dj-pc-<nonce_hex>` ([`placeholder_tag`]) — a per-render random nonce makes
/// the sentinel UNFORGEABLE so a loop item rendering a *literal* `<dj-pc ...>`
/// element via `|safe`/`mark_safe` can't be mistaken for a real placeholder
/// (security fix; see the `nonce` field doc). The prefix carries a hyphen so the
/// composed tag is a valid HTML custom-element name html5ever keeps intact.
pub const LOOP_PLACEHOLDER_TAG_PREFIX: &str = "dj-pc";

/// The full per-render sentinel tag name: `dj-pc-<nonce_hex>`.
pub fn placeholder_tag(nonce: u64) -> String {
    format!("{LOOP_PLACEHOLDER_TAG_PREFIX}-{nonce:x}")
}

/// Render a `<dj-pc-<nonce> h="<hash>">` placeholder element for a parse-cache
/// hit item. Both nonce and hash are lowercase hex so they round-trip through
/// html5ever (which lowercases tag names) unchanged.
pub fn render_loop_placeholder(hash: u64, nonce: u64) -> String {
    let tag = placeholder_tag(nonce);
    format!("<{tag} h=\"{hash:x}\"></{tag}>")
}

/// Generate a fresh per-render nonce. Uses the same `DefaultHasher` seeded with
/// process-varying inputs (`RandomState` default + a monotonic counter + the
/// current instant) — not a CSPRNG (we don't need cryptographic strength, just
/// unpredictability across renders so `|safe` item content can't pre-compute the
/// sentinel tag). The hash output is folded to avoid 0 (0 = "no render").
fn next_render_nonce() -> u64 {
    use std::hash::BuildHasher;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::time::{SystemTime, UNIX_EPOCH};
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let seq = COUNTER.fetch_add(1, Ordering::Relaxed);
    // RandomState is seeded per-process with OS entropy; hashing a unique
    // per-call payload through it yields an unpredictable, per-render value.
    let mut hasher = std::collections::hash_map::RandomState::new().build_hasher();
    seq.hash(&mut hasher);
    if let Ok(d) = SystemTime::now().duration_since(UNIX_EPOCH) {
        d.as_nanos().hash(&mut hasher);
    }
    let n = hasher.finish();
    // Never return 0 (the "no render in progress" sentinel value).
    if n == 0 {
        0x9e3779b97f4a7c15
    } else {
        n
    }
}

/// Is this item's rendered HTML PARSE-CACHE-eligible — i.e. will emitting a
/// `<dj-pc>` placeholder in its place be foster-parenting-SAFE?
///
/// html5ever foster-parents non-table content out of `<table>`/`<tbody>`/… and
/// drops stray content in `<select>`. A `<dj-pc>` placeholder (or the item it
/// stands in for) inside those containers would be relocated or dropped,
/// corrupting structure (proven during #1970 design). We cannot see the
/// container from the renderer (it emits a flat string with no ancestor
/// context), so we gate on the ITEM'S OWN first element tag as a proxy: an item
/// whose root is a table/select-family element (`<tr>`, `<td>`, `<option>`, …)
/// is necessarily inside a table/select container → NOT eligible. Any other
/// first tag (`<li>`, `<div>`, `<span>`, `<p>`, `<a>`, …) → eligible.
///
/// Conservative: an item with NO leading element (pure text, or leading
/// whitespace/comment then text) is NOT eligible (returns `false`) — only a
/// clean single leading element is spliceable. Multi-root items are still
/// recorded but `render_with_diff` validates the splice and falls back to a full
/// parse if anything is off, so this gate is a fast-reject, not the sole guard.
pub fn item_html_is_foster_safe(item_html: &str) -> bool {
    // Defense-in-depth (#1970 security): refuse eligibility for ANY item whose
    // rendered HTML contains the literal sentinel-tag prefix (`<dj-pc`), e.g. a
    // `|safe`/`mark_safe` item that renders a literal `<dj-pc ...>` element. The
    // per-render nonce already makes the sentinel unforgeable (such content can't
    // match THIS render's `dj-pc-<nonce>` tag), but refusing eligibility here is
    // a cheap belt-and-braces: the item just renders normally (parse MISS) — no
    // placeholder is emitted for it, so it can never collide with the splice.
    if contains_sentinel_prefix(item_html) {
        return false;
    }
    let first_tag = match first_element_tag(item_html) {
        Some(t) => t,
        None => return false,
    };
    !is_foster_unsafe_tag(&first_tag)
}

/// Does `html` contain the literal placeholder-tag prefix `<dj-pc` (case-
/// insensitive on the tag, since html5ever lowercases)? Used to fast-reject
/// parse-cache eligibility for items whose content embeds a literal sentinel.
fn contains_sentinel_prefix(html: &str) -> bool {
    let needle_open = format!("<{LOOP_PLACEHOLDER_TAG_PREFIX}");
    let needle_close = format!("</{LOOP_PLACEHOLDER_TAG_PREFIX}");
    // ASCII case-insensitive contains.
    let hay = html.to_ascii_lowercase();
    hay.contains(&needle_open) || hay.contains(&needle_close)
}

/// Extract the lowercased tag name of the FIRST element in `html`, skipping
/// leading whitespace. Returns `None` if the first non-whitespace byte is not
/// the start of an element open tag (`<` followed by an ASCII letter).
fn first_element_tag(html: &str) -> Option<String> {
    let bytes = html.as_bytes();
    let mut i = 0;
    while i < bytes.len() && bytes[i].is_ascii_whitespace() {
        i += 1;
    }
    if i >= bytes.len() || bytes[i] != b'<' {
        return None;
    }
    i += 1;
    // Reject `</`, `<!`, `<?`.
    if i >= bytes.len() || !bytes[i].is_ascii_alphabetic() {
        return None;
    }
    let start = i;
    while i < bytes.len() {
        let c = bytes[i];
        if c.is_ascii_alphanumeric() || c == b'-' {
            i += 1;
        } else {
            break;
        }
    }
    Some(html[start..i].to_ascii_lowercase())
}

/// table/select-family tags whose presence as an item root means the item lives
/// inside a foster-parenting container (`<table>`/`<select>` insertion modes).
fn is_foster_unsafe_tag(tag: &str) -> bool {
    matches!(
        tag,
        "tr" | "td"
            | "th"
            | "tbody"
            | "thead"
            | "tfoot"
            | "caption"
            | "colgroup"
            | "col"
            | "option"
            | "optgroup"
    )
}

/// Stable structural hash of a [`djust_core::Value`].
///
/// `Value` does not derive `Hash` (it holds f64 / nested maps), so we hash a
/// canonical byte encoding. Floats are hashed by their bit pattern; map keys
/// are sorted for order-independence.
fn hash_value(value: &djust_core::Value, hasher: &mut DefaultHasher) {
    use djust_core::Value;
    match value {
        Value::Null => 0u8.hash(hasher),
        Value::Bool(b) => {
            1u8.hash(hasher);
            b.hash(hasher);
        }
        Value::Integer(i) => {
            2u8.hash(hasher);
            i.hash(hasher);
        }
        Value::Float(f) => {
            3u8.hash(hasher);
            // Canonicalize NaN/-0.0 so equal-rendering floats hash equally.
            let bits = if f.is_nan() {
                f64::NAN.to_bits()
            } else if *f == 0.0 {
                0.0f64.to_bits()
            } else {
                f.to_bits()
            };
            bits.hash(hasher);
        }
        Value::String(s) => {
            4u8.hash(hasher);
            s.hash(hasher);
        }
        Value::List(items) => {
            5u8.hash(hasher);
            items.len().hash(hasher);
            for item in items {
                hash_value(item, hasher);
            }
        }
        Value::Object(map) => {
            6u8.hash(hasher);
            map.len().hash(hasher);
            // Sort keys for a deterministic, order-independent hash.
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            for k in keys {
                k.hash(hasher);
                hash_value(&map[k], hasher);
            }
        }
    }
}

/// Does this loop-body subtree render POSITION-dependent output?
///
/// Returns `true` (→ caching DISABLED for this body) if the subtree contains
/// any of:
///
/// * `Node::If` — emits a position-indexed dj-if marker id (#1832).
/// * `Node::Cycle` — `{% cycle %}` advances by loop position.
/// * a nested `Node::For` — composes the dj-if loop path from the outer index.
/// * a `{{ forloop.* }}` variable reference (defensive — see module docs).
/// * `Node::CustomTag` / `Node::BlockCustomTag` / `Node::AssignTag` /
///   `Node::Include` / `Node::RustComponent` / `Node::ReactComponent` /
///   `Node::UnsupportedTag` — opaque Python/component-backed output we cannot
///   prove is position-independent, so treat as non-cacheable
///   (conservative = correct).
///
/// Recurses into every child-node-bearing variant, mirroring the recursion in
/// [`crate::parser::assign_if_marker_ids`].
pub fn body_is_position_dependent(nodes: &[Node]) -> bool {
    nodes.iter().any(node_is_position_dependent)
}

fn node_is_position_dependent(node: &Node) -> bool {
    match node {
        // Direct position-dependent constructs.
        Node::If { .. } => true,
        Node::Cycle { .. } => true,
        Node::For { .. } => true, // nested loop composes the if-loop-path
        // Opaque / Python-backed nodes — we cannot prove position-independence.
        Node::CustomTag { .. }
        | Node::BlockCustomTag { .. }
        | Node::AssignTag { .. }
        | Node::Include { .. }
        | Node::RustComponent { .. }
        | Node::ReactComponent { .. }
        | Node::UnsupportedTag { .. } => true,
        // `{{ forloop.* }}` reference (defensive).
        Node::Variable(name, _filters, _in_attr) => {
            name == "forloop" || name.starts_with("forloop.")
        }
        Node::InlineIf {
            true_expr,
            condition,
            false_expr,
            ..
        } => {
            // InlineIf emits no position marker; it is position-dependent only
            // if one of its expressions references forloop.
            expr_references_forloop(true_expr)
                || expr_references_forloop(condition)
                || expr_references_forloop(false_expr)
        }
        // Container variants: recurse into children.
        Node::Block { nodes, .. } => body_is_position_dependent(nodes),
        Node::With { nodes, .. } => body_is_position_dependent(nodes),
        Node::Spaceless { nodes, .. } => body_is_position_dependent(nodes),
        // Leaf / position-independent nodes.
        Node::Text(_)
        | Node::Comment
        | Node::Load(_)
        | Node::CsrfToken
        | Node::Static(_)
        | Node::Extends(_)
        | Node::TemplateTag(_)
        | Node::Now(_)
        | Node::WidthRatio { .. }
        | Node::FirstOf { .. } => false,
    }
}

/// Cheap scan for a `forloop` identifier inside an InlineIf expression string.
fn expr_references_forloop(expr: &str) -> bool {
    expr.split(|c: char| !(c.is_alphanumeric() || c == '_' || c == '.'))
        .any(|tok| tok == "forloop" || tok.starts_with("forloop."))
}

// ---------------------------------------------------------------------------
// Thread-local plumbing.
//
// The cache is ambient state for a single recursive render traversal. Rather
// than thread a `&mut LoopRenderCache` through `render_nodes_with_loader` /
// `render_node_with_loader` and their ~22 recursive call sites (a large churn
// that is itself a correctness risk), we install the active cache into a
// thread-local for the duration of one render call and read it from the
// `Node::For` arm. Rendering is single-threaded per `render_with_diff` call
// (it runs synchronously inside a PyO3 method), so a thread-local is correct.
//
// Re-entrancy: a render can recurse into another `render_*` entry (e.g. a
// custom tag that renders a sub-template). [`LoopCacheGuard`] saves and
// restores the previous thread-local pointer so nested renders are correct —
// the inner render simply uses whatever cache (or none) is active for it.
// ---------------------------------------------------------------------------

use std::cell::Cell;

thread_local! {
    /// Raw pointer to the cache active for the current render scope, or null.
    /// Set/cleared only via [`LoopCacheGuard`]. Safe because the guard's
    /// lifetime strictly contains every render call that reads it, and
    /// rendering is synchronous + single-threaded.
    static ACTIVE_LOOP_CACHE: Cell<*mut LoopRenderCache> = const { Cell::new(std::ptr::null_mut()) };
}

/// RAII guard that installs `cache` as the active loop cache for the current
/// thread, restoring the previous value on drop.
pub struct LoopCacheGuard {
    prev: *mut LoopRenderCache,
}

impl LoopCacheGuard {
    /// Install `cache` as active. The guard must not outlive `cache`.
    pub fn install(cache: &mut LoopRenderCache) -> Self {
        let ptr: *mut LoopRenderCache = cache;
        let prev = ACTIVE_LOOP_CACHE.with(|c| c.replace(ptr));
        LoopCacheGuard { prev }
    }
}

impl Drop for LoopCacheGuard {
    fn drop(&mut self) {
        ACTIVE_LOOP_CACHE.with(|c| c.set(self.prev));
    }
}

/// Run `f` with mutable access to the active loop cache, if one is installed
/// AND enabled. Returns `None` (and does not call `f`) when no cache is active
/// or the active cache is disabled — the byte-identical default-off path.
///
/// # Safety
///
/// The raw pointer is only ever set by [`LoopCacheGuard::install`] from a live
/// `&mut LoopRenderCache` whose borrow strictly outlives the guard, and reads
/// happen synchronously on the same thread within that scope. No aliasing `&mut`
/// to the same cache exists during a render (the caller hands ownership to the
/// guard for the render's duration).
pub fn with_active_cache<R>(f: impl FnOnce(&mut LoopRenderCache) -> R) -> Option<R> {
    let ptr = ACTIVE_LOOP_CACHE.with(|c| c.get());
    if ptr.is_null() {
        return None;
    }
    // SAFETY: see function docs.
    let cache: &mut LoopRenderCache = unsafe { &mut *ptr };
    if !cache.enabled {
        return None;
    }
    Some(f(cache))
}
