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
//! * `Node::Cycle` / `Node::ResetCycle` — `{% cycle %}` state is per node per render (#2556).
//! * a nested `Node::For` — composes `Context::dj_if_loop_path` from the outer
//!   index, so any dj-if inside the nested loop is position-dependent. This
//!   refuses the OUTER body only. The inner `{% for %}` is a loop of its own
//!   and goes through both gates with its own loop variable(s): an inner body
//!   that reads nothing but the inner variable (`{% for t in r.tags %}<i>{{ t
//!   }}</i>{% endfor %}`) IS cached, and is correct to cache, because its
//!   output is fully determined by `t`. An inner body that reads the outer
//!   item, outer context or `forloop` is refused by the same gates as any
//!   other body (`test_loop_cache_nested_for_1967.py` checks this
//!   differentially).
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
use std::hash::{BuildHasher, Hash, Hasher};

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
    ///
    /// LiveView-only (#2519): the parsed subtrees are spliced into the VDOM
    /// diff baseline by `render_with_diff`; the plain engine has no VDOM.
    #[cfg(feature = "liveview")]
    parsed: HashMap<u64, Vec<djust_vdom::VNode>>,
    /// Per-render item manifest (#1970): one entry per loop item rendered this
    /// render, in document order, recorded ONLY for parse-cache-eligible items
    /// (foster-safe item root tag). Consumed by `render_with_diff` to splice
    /// cached subtrees in for placeholder-emitted items. Cleared at render start.
    manifest: Vec<ManifestEntry>,
    /// Debug parse-cache reuse counter (cached subtrees spliced this render).
    #[cfg(feature = "liveview")]
    parse_hits: u64,
    /// Debug parse-cache miss counter (item subtrees parsed this render).
    #[cfg(feature = "liveview")]
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
    /// The index-keyed per-item store (#4325), with its own switch.
    item_store: LoopItemStore,
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
        let mut cache = Self {
            enabled,
            ..Default::default()
        };
        // The index-keyed item store (#4325) is on unless switched off.
        cache.item_store.enabled = true;
        cache
    }

    /// The index-keyed per-item store (#4325).
    pub fn item_store(&self) -> &LoopItemStore {
        &self.item_store
    }

    /// The index-keyed per-item store (#4325), mutably.
    pub fn item_store_mut(&mut self) -> &mut LoopItemStore {
        &mut self.item_store
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
            #[cfg(feature = "liveview")]
            self.parsed.clear();
            self.manifest.clear();
        }
    }

    /// Drop all cached fragments and the cacheability memoization, preserving
    /// the `enabled` flag. Called when the template AST changes
    /// (`update_template`) — the cached fragments AND the body-pointer-keyed
    /// verdicts both reference the OLD AST and must not survive.
    pub fn clear(&mut self) {
        self.item_store.clear();
        self.fragments.clear();
        self.seen_this_render.clear();
        self.cacheable_verdict.clear();
        self.hits = 0;
        self.misses = 0;
        // #1970: the parsed subtrees also reference the OLD template AST and
        // must not survive a template change; the manifest is per-render.
        #[cfg(feature = "liveview")]
        {
            self.parsed.clear();
            self.parse_hits = 0;
            self.parse_misses = 0;
        }
        self.manifest.clear();
    }

    /// Begin a render: reset the per-render bookkeeping.
    pub fn begin_render(&mut self) {
        self.item_store.begin_render();
        self.seen_this_render.clear();
        self.hits = 0;
        self.misses = 0;
        // #1970: the item manifest is per-render — clear it at the start so a
        // stale manifest can never leak into the next render's parse path,
        // even when a render takes a fast path that never reads it.
        self.manifest.clear();
        #[cfg(feature = "liveview")]
        {
            self.parse_hits = 0;
            self.parse_misses = 0;
        }
        // #1970 security: fresh per-render nonce so the placeholder sentinel tag
        // (`dj-pc-<nonce>`) is unforgeable by `|safe`/`mark_safe` item content.
        self.nonce = next_render_nonce();
    }

    /// Prune stale entries after a render: keep only the hashes seen this
    /// render so the cache size tracks the current item set (bounds memory).
    pub fn prune(&mut self) {
        self.item_store.prune();
        if !self.enabled {
            return;
        }
        let seen = &self.seen_this_render;
        self.fragments.retain(|k, _| seen.contains(k));
        // #1970: prune the parse cache by the same seen-this-render set so its
        // size tracks the current item set (bounds memory) — symmetric with
        // the render-fragment prune.
        #[cfg(feature = "liveview")]
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

    /// Heap bytes the cache holds between renders (#3998): the rendered
    /// fragments and, with the `liveview` feature, the parsed subtrees. The
    /// per-render manifest is counted too; it is empty between renders.
    pub fn retained_bytes(&self) -> usize {
        use djust_core::retained::table_bytes;
        use std::mem::size_of;
        let mut total = self.item_store.retained_bytes()
            + table_bytes(self.fragments.capacity(), size_of::<(u64, String)>())
            + self.fragments.values().map(String::capacity).sum::<usize>()
            + table_bytes(self.seen_this_render.capacity(), size_of::<u64>())
            + table_bytes(
                self.cacheable_verdict.capacity(),
                size_of::<((usize, usize), bool)>(),
            )
            + self.manifest.capacity() * size_of::<ManifestEntry>()
            + self
                .manifest
                .iter()
                .map(|entry| entry.item_html.capacity())
                .sum::<usize>();
        #[cfg(feature = "liveview")]
        {
            total += table_bytes(
                self.parsed.capacity(),
                size_of::<(u64, Vec<djust_vdom::VNode>)>(),
            ) + self
                .parsed
                .values()
                .map(|roots| {
                    roots
                        .iter()
                        .map(|root| root.retained_bytes().total())
                        .sum::<usize>()
                })
                .sum::<usize>();
        }
        total
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
    #[cfg(feature = "liveview")]
    pub fn has_parsed(&self, hash: u64) -> bool {
        self.parsed.contains_key(&hash)
    }

    /// Without the `liveview` feature there is no parse cache, so the For arm
    /// never sees a HIT and never emits a `<dj-pc-…>` placeholder (#2519).
    #[cfg(not(feature = "liveview"))]
    pub fn has_parsed(&self, _hash: u64) -> bool {
        false
    }

    /// Look up the cached parsed subtree roots for `hash` (clone on hit).
    /// Bumps the parse-hit counter on a hit. The returned roots have their
    /// dj-ids re-assigned by the caller's pre-order re-walk.
    #[cfg(feature = "liveview")]
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
    #[cfg(feature = "liveview")]
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
    #[cfg(feature = "liveview")]
    pub fn parse_hits(&self) -> u64 {
        self.parse_hits
    }

    /// Item-subtree parse count for the current render (debug / tests).
    #[cfg(feature = "liveview")]
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
    #[cfg(feature = "liveview")]
    pub fn bump_parse_hits(&mut self, n: u64) {
        self.parse_hits += n;
    }

    /// Bump the parse-miss counter.
    #[cfg(feature = "liveview")]
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
///
/// `Context::emit_dj_if_markers` (#2519) is deliberately NOT part of this key,
/// and the plain and LiveView render paths share this cache (same AST
/// pointers, thread-local guard inherited by nested renders). That is safe
/// only because a body whose bytes depend on the switch contains a
/// `Node::If`, and [`body_is_position_dependent`] rejects every `Node::If`
/// body before it can reach the fragment or parse cache — so no
/// marker-dependent bytes are ever stored. If a `Node::If` body ever becomes
/// cacheable, the switch must join this key.
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

/// Is this item PARSE-CACHE-eligible? Foster-safe (above) AND its first
/// element is block-level (#2999).
///
/// Whitespace between two inline-level siblings is a kept `" "` node in the
/// VDOM, so the whitespace at an item's edges depends on the item's
/// neighbours — which a cached, separately-parsed subtree cannot see. An
/// item that starts with an inline-level element (`<span>`, `<a>`, …) would
/// be refused at splice time (`djust_vdom::splice_loop_placeholders`, which
/// also checks the LAST root) and cost a wasted reduced parse, so it is not
/// offered to the parse cache at all. Its render-cache entry is unaffected.
pub fn item_html_is_parse_cache_eligible(item_html: &str) -> bool {
    if !item_html_is_foster_safe(item_html) {
        return false;
    }
    match first_element_tag(item_html) {
        Some(tag) => !djust_core::html_whitespace::is_inline_level_tag(&tag),
        None => false,
    }
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
/// are hashed in INSERTION order — this doc said "sorted for
/// order-independence" and was contradicted by the `Object` arm's own
/// comment ever since #2203 made rendering insertion-ordered. Corrected
/// rather than left, since it sits above the arm a #2240 regression hid in.
fn hash_value(value: &djust_core::Value, hasher: &mut DefaultHasher) {
    use djust_core::Value;
    match value {
        Value::Missing => 0u8.hash(hasher),
        // A DISTINCT tag from Missing (#2203): the two now render differently
        // ("" vs "None"), so sharing a tag would let a cached fragment rendered
        // from one be served for the other.
        Value::None => 7u8.hash(hasher),
        // Tag 9, distinct from both Float and String: a Decimal renders and
        // encodes differently from either, so sharing a tag would let a cached
        // fragment built from one be served for the other (#2214).
        //
        // Hashes the DIGIT STRING, not a parsed float — two Decimals differing
        // beyond f64 precision are different values and must not collide.
        Value::Decimal(d) => {
            9u8.hash(hasher);
            d.hash(hasher);
        }
        // Tag 10, distinct from Decimal, String, Integer and Float (#2260).
        // Sharing tag 9 with Decimal would let a fragment rendered from
        // `Decimal('123')` be served for the int `123` — they render the same
        // but do not SERIALIZE the same (one leaves as a `decimal.Decimal`,
        // the other as an `int`) and `pprint`/`json_script` spell them
        // differently. The tag is what keeps that separate; hashing the digit
        // string, not a parsed double, is what keeps two big ints differing
        // past f64 precision apart.
        Value::BigInt(d) => {
            10u8.hash(hasher);
            d.hash(hasher);
        }
        // Tag 11, distinct from String (#2448) — and the distinctness is the
        // point, not bookkeeping. A `timedelta(seconds=90)` and the literal
        // string `"0:01:30"` RENDER identically, so sharing the string tag
        // would let a fragment cached from one be served for the other; they
        // are different values to `json_script`, which spells the timedelta
        // `"P0DT00H01M30S"`. All three fields are hashed for the same reason
        // the cache-by-struct rule states (#247): pruning one later is a
        // one-line change, adding one later is a cache-poisoning bug.
        Value::Encoded(e) => {
            11u8.hash(hasher);
            e.type_name.hash(hasher);
            e.display().hash(hasher);
            e.display_safe().hash(hasher);
            e.json().hash(hasher);
        }
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
        Value::SafeString(s) => {
            13u8.hash(hasher);
            s.hash(hasher);
        }
        // Tag 14 (#3947): a model marker and a plain string of the same text make a map a model
        // or a dict, so a fragment cached for one must not be served for the other.
        Value::ModelName(s) => {
            14u8.hash(hasher);
            s.hash(hasher);
        }
        Value::String(s) => {
            4u8.hash(hasher);
            s.hash(hasher);
        }
        Value::Tuple(items) => {
            // Distinct from List: a tuple renders "(1, 2)" where a list renders
            // "[1, 2]", so they must not share a cache key (#2203).
            8u8.hash(hasher);
            items.len().hash(hasher);
            for item in items {
                hash_value(item, hasher);
            }
        }
        Value::NamedTuple {
            name,
            fields,
            items,
        } => {
            12u8.hash(hasher);
            name.hash(hasher);
            fields.hash(hasher);
            items.len().hash(hasher);
            for item in items {
                hash_value(item, hasher);
            }
        }
        Value::List(items) => {
            5u8.hash(hasher);
            items.len().hash(hasher);
            for item in items {
                hash_value(item, hasher);
            }
        }
        // Its OWN tag, and the KIND in it: a view renders
        // `dict_keys([…])` where a list of the same items renders `[…]`, and
        // `dict_keys` differs from `dict_values`. Sharing the list tag would
        // let a cached fragment rendered for one be served for the other
        // (#2340) — the same argument the Missing/None split above makes.
        Value::DictView { kind, items } => {
            9u8.hash(hasher);
            (*kind as u8).hash(hasher);
            items.len().hash(hasher);
            for item in items {
                hash_value(item, hasher);
            }
        }
        Value::Object(map) => {
            6u8.hash(hasher);
            map.len().hash(hasher);
            // INSERTION order, not sorted (#2203). Sorting was correct while
            // dict order could not affect output, but `Object` is now an
            // IndexMap that RENDERS in insertion order — so two dicts with the
            // same pairs in different orders render differently. An
            // order-independent hash would let a fragment cached from one be
            // served for the other: a stale hit producing wrong output.
            for (k, v) in map.iter() {
                k.hash(hasher);
                hash_value(v, hasher);
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
/// * `Node::Cycle` / `Node::ResetCycle` — `{% cycle %}` advances on every render
///   of the node and `{% resetcycle %}` rewinds it (#2556).
/// * a nested `Node::For` — composes the dj-if loop path from the outer index.
/// * a `{{ forloop.* }}` variable reference (defensive — see module docs).
/// * `Node::CustomTag` / `Node::BlockCustomTag` / `Node::AssignTag` /
///   `Node::Include` /
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
        Node::Located { nodes, .. } => body_is_position_dependent(nodes),
        // Direct position-dependent constructs.
        Node::If { .. } => true,
        Node::Cycle { .. } | Node::ResetCycle { .. } => true,
        // `{% ifchanged %}` carries state ACROSS iterations of the loop it
        // sits in — the same reason `{% cycle %}` is listed. Caching a body
        // that contains one and replaying it would freeze the comparison.
        Node::IfChanged { .. } => true,
        // The parent body it carries can hold anything, including a
        // `{% cycle %}`; treat it as opaque rather than proving otherwise.
        Node::BlockSuperScope { .. } => true,
        // Nested loop composes the if-loop-path. Refuses THIS body; the inner
        // loop's own body is gated separately when the inner For renders.
        Node::For { .. } => true,
        // Opaque / Python-backed nodes — we cannot prove position-independence.
        Node::PartialCall { .. }
        | Node::PartialDef { .. }
        | Node::CustomTag { .. }
        | Node::BlockCustomTag { .. }
        | Node::RawBlockCustomTag { .. }
        | Node::Language { .. }
        | Node::Timezone { .. }
        | Node::Localize { .. }
        | Node::LocalTime { .. }
        | Node::AssignTag { .. }
        | Node::Include { .. }
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
        Node::AutoEscape { nodes, .. } => body_is_position_dependent(nodes),
        Node::Filter { nodes, .. } => body_is_position_dependent(nodes),
        // Leaf / position-independent nodes.
        Node::Text(_)
        | Node::Comment
        | Node::Load(_)
        | Node::CsrfToken
        | Node::DjustAudio
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

// ---------------------------------------------------------------------------
// Index-keyed per-item store (#4325).
//
// The content-hash cache above refuses any body with a `{% if %}`, a nested
// loop or a `forloop` read, because it serves a fragment at a DIFFERENT
// position than the one it was rendered at. This store never does: it keeps,
// per loop, the output of the item at each INDEX, and serves it again only at
// that same index, for an item whose value hashes the same, in a loop whose
// length and every other input to the body's output are the same. Under
// those conditions the dj-if marker ids (`…-<index>`), the `forloop`
// counters and `forloop.parentloop` are what they were, so the bytes are too.
//
// What the body's output can depend on, and how each is covered:
//
// * the item value: a keyed 128-bit hash of it (`item_hash`), plain data only
//   (`None` for a live handle, a model, an encoded object: rendered as usual);
// * index, counter, length: the slot index, and the length in the entry;
// * the body itself: a structural hash of the AST (`body_signature`), so a
//   re-parsed include at a reused address cannot serve the old body's bytes;
// * everything else (autoescape, `string_if_invalid`, marker switch and
//   prefix paths, timezone and number formats, the `{% localize %}` scope):
//   the `env` string the renderer builds, which resets the entry when it
//   changes;
// * context reads: only bodies that read nothing but the loop variables and
//   `forloop`, with no tag whose output depends on anything else
//   (`body_is_item_storable`), and only for an outermost loop with no safety
//   mark under its iterable (checked by the renderer each execution);
// * a nested `{% for %}` (#4325 WP4): storable when its iterable and body
//   read only the enclosing loops' variables, its own and `forloop` (each
//   loop variable only inside the loop that binds it). While an item that
//   will be stored renders, the store is `storing` and a nested loop does not
//   use the content cache (#1967/#1970), so no parse-cache placeholder can
//   be stored with the item. `djust._per_turn` `loop_item_nested` switches
//   it (`RustLiveView.set_loop_item_store_nested`);
// * Python: an item whose render made a live read is never stored.
//
// The store is on by default and switched by
// `RustLiveView.set_loop_item_store_enabled` (`djust._per_turn`
// `loop_item_render`).
// ---------------------------------------------------------------------------

/// One loop's stored items.
#[derive(Debug, Default)]
struct StoredLoop {
    env: u64,
    len: usize,
    /// The store's render number when this loop last rendered.
    last_render: u64,
    items: Vec<Option<(u128, String)>>,
}

/// The index-keyed per-item store. See the section comment above.
#[derive(Debug, Default)]
pub struct LoopItemStore {
    enabled: bool,
    /// Testing only: 1 serves a stored item whatever its value now is, 2
    /// ignores the environment and length, 3 hashes a list inside an item
    /// by its kind only (an in-place change to a row's inner list is then
    /// missed), 4 skips the read check of a body holding a nested loop (an
    /// inner loop reading outer context is then stored). Lets the
    /// differential tests prove they see a broken store.
    sabotage: u8,
    /// Bodies with a nested `{% for %}` are refused (#4325 WP4 switched off).
    refuse_nested: bool,
    /// Depth of item renders whose output will be stored: a nested loop
    /// rendering under one must not emit parse-cache placeholders.
    storing: u32,
    keys: (
        std::collections::hash_map::RandomState,
        std::collections::hash_map::RandomState,
    ),
    loops: HashMap<u64, StoredLoop>,
    verdicts: HashMap<u64, bool>,
    /// Renders begun, to age out loops that stopped rendering.
    render_no: u64,
    hits: u64,
    misses: u64,
}

/// A loop not rendered for this many renders is dropped. A partial render
/// that reuses a whole loop's cached node does not render it, so the window
/// is wide: dropping only costs a full re-render of that loop later.
const ITEM_STORE_MAX_IDLE_RENDERS: u64 = 64;

impl LoopItemStore {
    pub fn is_enabled(&self) -> bool {
        self.enabled
    }

    pub fn set_enabled(&mut self, enabled: bool) {
        self.enabled = enabled;
        if !enabled {
            self.clear();
        }
    }

    #[doc(hidden)]
    pub fn set_sabotage(&mut self, mode: u8) {
        if self.sabotage != mode {
            // Mode 4 changes verdicts, so none may outlive a switch.
            self.verdicts.clear();
        }
        self.sabotage = mode;
    }

    /// Whether bodies with a nested `{% for %}` may be stored (#4325 WP4;
    /// on by default). Changing it drops every verdict and stored item.
    pub fn set_nested_enabled(&mut self, enabled: bool) {
        if self.refuse_nested == enabled {
            self.refuse_nested = !enabled;
            self.clear();
        }
    }

    pub fn nested_enabled(&self) -> bool {
        !self.refuse_nested
    }

    /// An item whose output will be stored starts rendering.
    pub fn enter_storing(&mut self) {
        self.storing += 1;
    }

    /// ... and has finished (whether or not it rendered).
    pub fn leave_storing(&mut self) {
        self.storing = self.storing.saturating_sub(1);
    }

    /// Inside the render of an item that will be stored: a nested loop must
    /// render its items in full, without the content cache's placeholders.
    pub fn is_storing(&self) -> bool {
        self.storing > 0
    }

    pub fn clear(&mut self) {
        self.loops.clear();
        self.verdicts.clear();
    }

    pub fn hits(&self) -> u64 {
        self.hits
    }

    pub fn misses(&self) -> u64 {
        self.misses
    }

    /// Approximate heap bytes held (stored item HTML plus slots).
    pub fn retained_bytes(&self) -> usize {
        self.loops
            .values()
            .map(|l| {
                l.items.len() * std::mem::size_of::<Option<(u128, String)>>()
                    + l.items
                        .iter()
                        .map(|i| i.as_ref().map_or(0, |(_, h)| h.capacity()))
                        .sum::<usize>()
            })
            .sum()
    }

    fn begin_render(&mut self) {
        self.render_no += 1;
        self.hits = 0;
        self.misses = 0;
        self.storing = 0;
    }

    fn prune(&mut self) {
        let now = self.render_no;
        self.loops
            .retain(|_, l| now.saturating_sub(l.last_render) < ITEM_STORE_MAX_IDLE_RENDERS);
    }

    /// Open one execution of a loop: `None` when the body is not storable.
    /// `site` is the loop's identity (body signature, iterable, variable
    /// names, marker paths); `env` everything else its output depends on.
    pub fn begin_loop(
        &mut self,
        body: &[Node],
        var_names: &[String],
        site: &str,
        env: &str,
        len: usize,
    ) -> Option<u64> {
        if !self.enabled {
            return None;
        }
        let mut body_hasher = self.keys.0.build_hasher();
        hash_body(body, &mut body_hasher);
        var_names.hash(&mut body_hasher);
        let body_sig = body_hasher.finish();
        let storable = *self.verdicts.entry(body_sig).or_insert_with(|| {
            if self.sabotage == 4 && !self.refuse_nested && body.iter().any(node_holds_for) {
                // Sabotage 4: a nested body's reads are not checked.
                body.iter().all(|n| node_is_item_storable(n, true))
            } else {
                body_is_item_storable_in(body, var_names, !self.refuse_nested)
            }
        });
        if !storable {
            return None;
        }
        let mut key_hasher = self.keys.0.build_hasher();
        body_sig.hash(&mut key_hasher);
        site.hash(&mut key_hasher);
        let key = key_hasher.finish();
        let env = self.keys.1.hash_one(env);
        // A loop rendered twice in one page with the same identity keeps the
        // first execution's items; the second only reads them.
        let now = self.render_no;
        let entry = self.loops.entry(key).or_default();
        let first_this_render = entry.last_render != now;
        entry.last_render = now;
        if (entry.env != env || entry.len != len) && self.sabotage != 2 {
            if !first_this_render {
                return None;
            }
            entry.env = env;
            entry.len = len;
            entry.items.clear();
        }
        entry.items.resize_with(len, || None);
        Some(key)
    }

    /// The keyed 128-bit hash of a plain item value; `None` when the value
    /// is not plain data (it is then rendered and never stored).
    ///
    /// One walk of the value feeds both keyed hashers (#4325 C2): the same
    /// two 64-bit halves as hashing it once per key, at half the walking.
    pub fn item_hash(&self, value: &djust_core::Value) -> Option<u128> {
        let mut tee = TeeHasher(self.keys.0.build_hasher(), self.keys.1.build_hasher());
        if self.sabotage == 3 {
            if !hash_plain_sabotaged(value, &mut tee) {
                return None;
            }
        } else if !hash_plain(value, &mut tee) {
            return None;
        }
        Some(((tee.0.finish() as u128) << 64) | tee.1.finish() as u128)
    }

    /// Hand the stored output for `index` to `emit` if it was rendered from
    /// an equal value; `true` on a hit. The stored HTML is borrowed, not
    /// cloned, so a hit costs no allocation (#4325 C2).
    pub fn lookup_with(
        &mut self,
        key: u64,
        index: usize,
        hash: u128,
        emit: impl FnOnce(&str),
    ) -> bool {
        let found = self
            .loops
            .get(&key)
            .and_then(|l| l.items.get(index))
            .and_then(|slot| slot.as_ref())
            .filter(|(h, _)| *h == hash || self.sabotage == 1)
            .map(|(_, html)| html.as_str());
        let hit = match found {
            Some(html) => {
                emit(html);
                true
            }
            None => false,
        };
        if hit {
            self.hits += 1;
        } else {
            self.misses += 1;
        }
        hit
    }

    pub fn store(&mut self, key: u64, index: usize, hash: u128, html: String) {
        if let Some(slot) = self
            .loops
            .get_mut(&key)
            .and_then(|l| l.items.get_mut(index))
        {
            *slot = Some((hash, html));
        }
    }

    pub fn forget(&mut self, key: u64, index: usize) {
        if let Some(slot) = self
            .loops
            .get_mut(&key)
            .and_then(|l| l.items.get_mut(index))
        {
            *slot = None;
        }
    }
}

/// Built-in filters whose output depends on something other than their input
/// and arguments (the clock, the active language, randomness), or that a
/// project filter may shadow. A body using one is not storable.
fn filter_is_item_storable(name: &str) -> bool {
    crate::filter_arity::builtin_arity(name).is_some()
        && !crate::filter_registry::is_registered_custom_filter(name)
        && !matches!(
            name,
            "date"
                | "time"
                | "timesince"
                | "timeuntil"
                | "naturaltime"
                | "naturalday"
                | "floatformat"
                | "filesizeformat"
                | "yesno"
                | "random"
                | "localtime"
                | "utc"
                | "timezone"
                | "pluralize"
        )
}

/// Every `|name` in an expression string is a storable filter. Conservative:
/// a `|` inside a string literal is read as a filter too.
fn expr_filters_storable(expr: &str) -> bool {
    expr.split('|').skip(1).all(|rest| {
        let name: String = rest
            .trim_start()
            .chars()
            .take_while(|c| c.is_alphanumeric() || *c == '_')
            .collect();
        filter_is_item_storable(&name)
    })
}

/// Is this loop body safe for the index-keyed store, given its loop
/// variables? Only node kinds whose output is a function of the values they
/// read, reading only the loop variables and `forloop`. A nested
/// `{% for %}` is allowed (#4325 WP4).
pub fn body_is_item_storable(nodes: &[Node], var_names: &[String]) -> bool {
    body_is_item_storable_in(nodes, var_names, true)
}

/// [`body_is_item_storable`], with nested loops allowed only when `nested`.
fn body_is_item_storable_in(nodes: &[Node], var_names: &[String], nested: bool) -> bool {
    if !nodes.iter().all(|n| node_is_item_storable(n, nested)) {
        return false;
    }
    if nested && nodes.iter().any(node_holds_for) {
        return reads_only_bound(nodes, var_names);
    }
    roots_are_bound(nodes, var_names)
}

fn roots_are_bound(nodes: &[Node], bound: &[String]) -> bool {
    crate::parser::body_root_var_names(nodes)
        .iter()
        .all(|root| root == "forloop" || bound.iter().any(|v| v == root))
}

/// Whether `node` is or holds a `{% for %}`.
fn node_holds_for(node: &Node) -> bool {
    matches!(node, Node::For { .. })
        || node
            .child_lists()
            .into_iter()
            .flatten()
            .any(|children| children.iter().any(node_holds_for))
}

/// The scoped form of [`roots_are_bound`] for a body with nested loops
/// (#4325 WP4). The root-name extraction keeps a loop's variables among the
/// roots it reports, so a body reading `t` inside `{% for t in r.tags %}`
/// would read as reading an outer `t`. This walks the loops instead: a
/// loop's iterable and `{% empty %}` block are checked against the names
/// bound around it, and its body against those plus its own variables. A
/// `{% with %}` holding a loop binds its names for its body the same way.
/// Every other node is checked whole when it holds no loop, or by its own
/// expressions (its children emptied) and then its children.
fn reads_only_bound(nodes: &[Node], bound: &[String]) -> bool {
    nodes.iter().all(|node| {
        if !node_holds_for(node) {
            return roots_are_bound(std::slice::from_ref(node), bound);
        }
        match node {
            Node::For {
                var_names,
                iterable,
                reversed,
                nodes,
                empty_nodes,
            } => {
                let head = Node::For {
                    var_names: Vec::new(),
                    iterable: iterable.clone(),
                    reversed: *reversed,
                    nodes: Vec::new(),
                    empty_nodes: Vec::new(),
                };
                let mut inner = bound.to_vec();
                inner.extend(var_names.iter().cloned());
                roots_are_bound(std::slice::from_ref(&head), bound)
                    && reads_only_bound(nodes, &inner)
                    && reads_only_bound(empty_nodes, bound)
            }
            Node::With { assignments, nodes } => {
                let head = Node::With {
                    assignments: assignments.clone(),
                    nodes: Vec::new(),
                };
                let mut inner = bound.to_vec();
                inner.extend(assignments.iter().map(|(name, _)| name.clone()));
                roots_are_bound(std::slice::from_ref(&head), bound)
                    && reads_only_bound(nodes, &inner)
            }
            other => {
                let mut head = other.clone();
                for children in head.child_lists_mut().into_iter().flatten() {
                    children.clear();
                }
                roots_are_bound(std::slice::from_ref(&head), bound)
                    && other
                        .child_lists()
                        .into_iter()
                        .flatten()
                        .all(|children| reads_only_bound(children, bound))
            }
        }
    })
}

/// Every loop variable a nested `{% for %}` in `nodes` binds (#4325 WP4),
/// for the renderer's check that no Python object is bound under a loop
/// name.
pub fn nested_loop_var_names(nodes: &[Node]) -> Vec<String> {
    let mut names = Vec::new();
    fn walk(nodes: &[Node], names: &mut Vec<String>) {
        for node in nodes {
            if let Node::For { var_names, .. } = node {
                names.extend(var_names.iter().cloned());
            }
            for children in node.child_lists().into_iter().flatten() {
                walk(children, names);
            }
        }
    }
    walk(nodes, &mut names);
    names
}

fn node_is_item_storable(node: &Node, nested: bool) -> bool {
    let storable = |nodes: &[Node]| nodes.iter().all(|n| node_is_item_storable(n, nested));
    match node {
        Node::Located { nodes, .. } => storable(nodes),
        Node::Text(_) | Node::Comment | Node::Load(_) | Node::TemplateTag(_) => true,
        Node::Variable(expr, filters, _) => {
            expr_filters_storable(expr)
                && filters.iter().all(|(name, arg)| {
                    filter_is_item_storable(name)
                        && arg.as_deref().is_none_or(expr_filters_storable)
                })
        }
        Node::If {
            condition,
            true_nodes,
            false_nodes,
            ..
        } => expr_filters_storable(condition) && storable(true_nodes) && storable(false_nodes),
        // A nested loop (#4325 WP4), when allowed. Its own content cache
        // (#1967/#1970) can emit parse-cache PLACEHOLDERS (`<dj-pc-…>`) in
        // place of an inner item, which `render_with_diff` splices back from
        // that render's manifest; stored with the outer item they were
        // served later with no manifest entry behind them, so the page
        // showed literal `<dj-pc-…>` elements. The renderer therefore turns
        // the content cache off for a loop nested in an item being stored
        // (`LoopItemStore::is_storing`). What the loop reads is checked by
        // `reads_only_bound`.
        Node::For {
            iterable,
            nodes,
            empty_nodes,
            ..
        } if nested => expr_filters_storable(iterable) && storable(nodes) && storable(empty_nodes),
        Node::With { assignments, nodes } => {
            assignments.iter().all(|(_, e)| expr_filters_storable(e)) && storable(nodes)
        }
        Node::Spaceless { nodes } | Node::AutoEscape { nodes, .. } => storable(nodes),
        Node::Filter { filters, nodes } => {
            filters.iter().all(|(name, arg)| {
                filter_is_item_storable(name) && arg.as_deref().is_none_or(expr_filters_storable)
            }) && storable(nodes)
        }
        Node::InlineIf {
            true_expr,
            condition,
            false_expr,
            filters,
        } => {
            expr_filters_storable(true_expr)
                && expr_filters_storable(condition)
                && expr_filters_storable(false_expr)
                && filters.iter().all(|(name, arg)| {
                    filter_is_item_storable(name)
                        && arg.as_deref().is_none_or(expr_filters_storable)
                })
        }
        Node::FirstOf { args, asvar: None } => args.iter().all(|a| expr_filters_storable(a)),
        Node::WidthRatio {
            value,
            max_value,
            max_width,
            asvar: None,
        } => [value, max_value, max_width]
            .iter()
            .all(|e| expr_filters_storable(e)),
        // Everything else reads state outside the body (cycle and ifchanged
        // carry it across iterations, include and static read the context or
        // settings, now reads the clock, custom and Python tags anything).
        _ => false,
    }
}

/// A structural hash of a body: every field of every storable node kind.
/// Non-storable kinds hash their discriminant only; such a body is refused
/// by `body_is_item_storable` before any item is stored.
fn hash_body<H: Hasher>(nodes: &[Node], h: &mut H) {
    nodes.len().hash(h);
    for node in nodes {
        std::mem::discriminant(node).hash(h);
        match node {
            Node::Located { nodes, .. } => hash_body(nodes, h),
            Node::Text(t) | Node::TemplateTag(t) => t.hash(h),
            Node::Load(names) => names.hash(h),
            Node::Variable(expr, filters, in_attr) => {
                expr.hash(h);
                filters.hash(h);
                in_attr.hash(h);
            }
            Node::If {
                condition,
                true_nodes,
                false_nodes,
                in_tag_context,
                marker_id,
            } => {
                condition.hash(h);
                in_tag_context.hash(h);
                marker_id.hash(h);
                hash_body(true_nodes, h);
                hash_body(false_nodes, h);
            }
            Node::For {
                var_names,
                iterable,
                reversed,
                nodes,
                empty_nodes,
            } => {
                var_names.hash(h);
                iterable.hash(h);
                reversed.hash(h);
                hash_body(nodes, h);
                hash_body(empty_nodes, h);
            }
            Node::With { assignments, nodes } => {
                assignments.hash(h);
                hash_body(nodes, h);
            }
            Node::Spaceless { nodes } => hash_body(nodes, h),
            Node::AutoEscape { on, nodes } => {
                on.hash(h);
                hash_body(nodes, h);
            }
            Node::Filter { filters, nodes } => {
                filters.hash(h);
                hash_body(nodes, h);
            }
            Node::InlineIf {
                true_expr,
                condition,
                false_expr,
                filters,
            } => {
                true_expr.hash(h);
                condition.hash(h);
                false_expr.hash(h);
                filters.hash(h);
            }
            Node::FirstOf { args, asvar } => {
                args.hash(h);
                asvar.hash(h);
            }
            Node::WidthRatio {
                value,
                max_value,
                max_width,
                asvar,
            } => {
                value.hash(h);
                max_value.hash(h);
                max_width.hash(h);
                asvar.hash(h);
            }
            _ => {}
        }
    }
}

/// Feeds every write to two hashers, so one walk of a value yields both keyed
/// halves of [`LoopItemStore::item_hash`] (#4325 C2). Each stable `Hasher`
/// method is forwarded to the same method of both, so each half is exactly
/// what hashing the value into that hasher alone produces (`write_str` and
/// `write_length_prefix` are unstable to override; their defaults route
/// through the forwarded methods, as `DefaultHasher`'s own do).
struct TeeHasher<A, B>(A, B);

macro_rules! tee_forward {
    ($($method:ident: $ty:ty),* $(,)?) => {
        $(
            #[inline]
            fn $method(&mut self, i: $ty) {
                self.0.$method(i);
                self.1.$method(i);
            }
        )*
    };
}

impl<A: Hasher, B: Hasher> Hasher for TeeHasher<A, B> {
    #[inline]
    fn write(&mut self, bytes: &[u8]) {
        self.0.write(bytes);
        self.1.write(bytes);
    }

    tee_forward!(
        write_u8: u8,
        write_u16: u16,
        write_u32: u32,
        write_u64: u64,
        write_u128: u128,
        write_usize: usize,
        write_i8: i8,
        write_i16: i16,
        write_i32: i32,
        write_i64: i64,
        write_i128: i128,
        write_isize: isize,
    );

    /// Not a combined hash: callers read the two halves directly.
    fn finish(&self) -> u64 {
        self.0.finish()
    }
}

/// Hash a plain data value with a type tag per kind; `false` for anything
/// that is not plain data (a live handle, an encoded object, a model, a
/// dict view or named tuple, or a map with a non-scalar key).
fn hash_plain<H: Hasher>(value: &djust_core::Value, h: &mut H) -> bool {
    use djust_core::{ObjectKey, Value};
    match value {
        Value::None => 0u8.hash(h),
        Value::Bool(b) => {
            1u8.hash(h);
            b.hash(h);
        }
        Value::Integer(i) => {
            2u8.hash(h);
            i.hash(h);
        }
        Value::Float(f) => {
            3u8.hash(h);
            f.to_bits().hash(h);
        }
        Value::String(s) => {
            4u8.hash(h);
            s.hash(h);
        }
        Value::SafeString(s) => {
            5u8.hash(h);
            s.hash(h);
        }
        Value::Decimal(s) => {
            6u8.hash(h);
            s.hash(h);
        }
        Value::BigInt(s) => {
            7u8.hash(h);
            s.hash(h);
        }
        Value::List(items) | Value::Tuple(items) => {
            (if matches!(value, Value::List(_)) {
                8u8
            } else {
                9u8
            })
            .hash(h);
            items.len().hash(h);
            for item in items {
                if !hash_plain(item, h) {
                    return false;
                }
            }
        }
        Value::Object(map) => {
            10u8.hash(h);
            map.len().hash(h);
            for (key, item) in map {
                match key {
                    ObjectKey::Str(s) => {
                        0u8.hash(h);
                        s.hash(h);
                    }
                    ObjectKey::Int(i) => {
                        1u8.hash(h);
                        i.hash(h);
                    }
                    ObjectKey::Bool(b) => {
                        2u8.hash(h);
                        b.hash(h);
                    }
                    ObjectKey::None => 3u8.hash(h),
                    _ => return false,
                }
                if !hash_plain(item, h) {
                    return false;
                }
            }
        }
        _ => return false,
    }
    true
}

/// [`hash_plain`] broken on purpose (sabotage 3, #4325 WP4): a list or
/// tuple below the top level hashes as its kind only, so an in-place change
/// to a row's inner list keeps the row's hash.
fn hash_plain_sabotaged<H: Hasher>(value: &djust_core::Value, h: &mut H) -> bool {
    use djust_core::{ObjectKey, Value};
    match value {
        Value::Object(map) => {
            10u8.hash(h);
            map.len().hash(h);
            for (key, item) in map {
                match key {
                    ObjectKey::Str(s) => s.hash(h),
                    _ => return hash_plain(value, h),
                }
                match item {
                    Value::List(_) | Value::Tuple(_) => 99u8.hash(h),
                    other => {
                        if !hash_plain(other, h) {
                            return false;
                        }
                    }
                }
            }
            true
        }
        other => hash_plain(other, h),
    }
}

/// Run `f` with the active item store, if one is installed and enabled.
/// Independent of the content-hash cache's own switch.
pub fn with_active_item_store<R>(f: impl FnOnce(&mut LoopItemStore) -> R) -> Option<R> {
    let ptr = ACTIVE_LOOP_CACHE.with(|c| c.get());
    if ptr.is_null() {
        return None;
    }
    // SAFETY: as for `with_active_cache`.
    let cache: &mut LoopRenderCache = unsafe { &mut *ptr };
    if !cache.item_store.enabled {
        return None;
    }
    Some(f(&mut cache.item_store))
}

#[cfg(test)]
mod item_store_one_walk_4325 {
    //! #4325 C2: `item_hash` walks a value once through a tee of both keyed
    //! hashers, and a hit hands the stored HTML over by borrow. Both must
    //! decide exactly as the two-walk, cloning versions did.
    use super::{hash_plain, LoopItemStore, TeeHasher};
    use djust_core::{ObjectKey, Value};
    use indexmap::IndexMap;
    use std::hash::{BuildHasher, Hasher};

    /// The pre-C2 `item_hash`: one full walk per key.
    fn two_walks(store: &LoopItemStore, value: &Value) -> Option<u128> {
        let mut a = store.keys.0.build_hasher();
        let mut b = store.keys.1.build_hasher();
        if !hash_plain(value, &mut a) || !hash_plain(value, &mut b) {
            return None;
        }
        Some(((a.finish() as u128) << 64) | b.finish() as u128)
    }

    fn values() -> Vec<Value> {
        let mut row = IndexMap::new();
        row.insert(ObjectKey::Str("id".into()), Value::Integer(-7));
        row.insert(ObjectKey::Int(3), Value::Float(-0.0));
        row.insert(ObjectKey::Bool(true), Value::Bool(false));
        row.insert(ObjectKey::None, Value::None);
        row.insert(
            ObjectKey::Str("tags".into()),
            Value::List(vec![
                Value::String("été ↑".into()),
                Value::SafeString("<b>x</b>".into()),
                Value::Tuple(vec![
                    Value::Decimal("1.50".into()),
                    Value::BigInt("1".repeat(40)),
                ]),
            ]),
        );
        vec![
            Value::None,
            Value::Bool(true),
            Value::Integer(i64::MIN),
            Value::Float(f64::NAN),
            Value::String(String::new()),
            Value::String("a\u{0}b".into()),
            Value::List(vec![]),
            Value::Object(row),
        ]
    }

    #[test]
    fn the_tee_hash_equals_one_walk_per_key() {
        let store = LoopItemStore::default();
        for value in values() {
            assert_eq!(
                store.item_hash(&value),
                two_walks(&store, &value),
                "{value:?}"
            );
            assert!(store.item_hash(&value).is_some());
        }
        // Not plain data: refused by both.
        let mut map = IndexMap::new();
        map.insert(ObjectKey::Str("m".into()), Value::ModelName("app.M".into()));
        let model = Value::Object(map);
        assert_eq!(store.item_hash(&model), two_walks(&store, &model));
    }

    #[test]
    fn every_forwarded_write_reaches_both_halves_as_it_would_alone() {
        let store = LoopItemStore::default();
        let mut tee = TeeHasher(store.keys.0.build_hasher(), store.keys.1.build_hasher());
        let mut a = store.keys.0.build_hasher();
        let mut b = store.keys.1.build_hasher();
        macro_rules! both {
            ($($call:ident($v:expr)),*) => {$(
                tee.$call($v);
                a.$call($v);
                b.$call($v);
            )*};
        }
        both!(
            write(b"bytes"),
            write_u8(1),
            write_u16(2),
            write_u32(3),
            write_u64(4),
            write_u128(5),
            write_usize(6),
            write_i8(-1),
            write_i16(-2),
            write_i32(-3),
            write_i64(-4),
            write_i128(-5),
            write_isize(-6)
        );
        std::hash::Hash::hash("a str", &mut tee);
        std::hash::Hash::hash("a str", &mut a);
        std::hash::Hash::hash("a str", &mut b);
        assert_eq!(tee.0.finish(), a.finish());
        assert_eq!(tee.1.finish(), b.finish());
    }

    #[test]
    fn lookup_with_serves_the_stored_bytes_and_counts_as_before() {
        let mut store = LoopItemStore::default();
        store.set_enabled(true);
        store.begin_render();
        let key = 42;
        store
            .loops
            .entry(key)
            .or_default()
            .items
            .resize_with(2, || None);
        let value = Value::String("v".into());
        let hash = store.item_hash(&value).expect("plain");
        store.store(key, 0, hash, "<li>v</li>".into());

        let mut out = String::new();
        assert!(store.lookup_with(key, 0, hash, |h| out.push_str(h)));
        assert_eq!(out, "<li>v</li>");
        // A different value, an empty slot, an unknown loop: misses, no emit.
        let other = store.item_hash(&Value::String("w".into())).expect("plain");
        assert!(!store.lookup_with(key, 0, other, |_| panic!("emitted on a miss")));
        assert!(!store.lookup_with(key, 1, hash, |_| panic!("emitted on a miss")));
        assert!(!store.lookup_with(7, 0, hash, |_| panic!("emitted on a miss")));
        assert_eq!((store.hits(), store.misses()), (1, 3));
        // Sabotage 1 still serves whatever the value now is.
        store.set_sabotage(1);
        assert!(store.lookup_with(key, 0, other, |_| {}));
    }
}

#[cfg(test)]
mod nested_bodies_4325 {
    //! #4325 WP4: a body holding a nested `{% for %}` is storable when every
    //! name it reads is bound by an enclosing loop of the item, in scope.
    use super::{body_is_item_storable, body_is_item_storable_in, nested_loop_var_names};
    use crate::parser::Node;

    /// The body of the template's first (outermost) `{% for %}`.
    fn outer_body(src: &str) -> Vec<Node> {
        let tokens = crate::lexer::tokenize(src).expect("tokenize");
        let nodes = crate::parser::parse(&tokens).expect("parse");
        fn find(nodes: &[Node]) -> Option<Vec<Node>> {
            nodes.iter().find_map(|n| match n {
                Node::For { nodes, .. } => Some(nodes.clone()),
                other => other
                    .child_lists()
                    .into_iter()
                    .flatten()
                    .find_map(|c| find(c)),
            })
        }
        find(&nodes).expect("a for loop")
    }

    fn storable(src: &str) -> bool {
        body_is_item_storable(&outer_body(src), &["r".to_owned()])
    }

    #[test]
    fn a_nested_loop_over_the_item_is_storable() {
        for src in [
            "{% for r in items %}<ul>{% for t in r.tags %}<li>{{ t }}</li>{% endfor %}</ul>{% endfor %}",
            "{% for r in items %}{% for t in r.tags %}{% if t %}{{ t.x }}{% endif %}\
             {{ forloop.parentloop.counter }}{% empty %}none{{ r.a }}{% endfor %}{% endfor %}",
            "{% for r in items %}{% for a, b in r.pairs reversed %}{{ a }}={{ b|default:r.z }}\
             {% for c in b %}{{ c }}{{ a }}{% endfor %}{% endfor %}{% endfor %}",
            "{% for r in items %}{% with ts=r.tags %}{% for t in ts %}{{ t }}{% endfor %}\
             {% endwith %}{% endfor %}",
            "{% for r in items %}{% if r.on %}{% for t in r.tags|dictsort:\"k\" %}{{ t.k }}\
             {% endfor %}{% endif %}{% endfor %}",
        ] {
            assert!(storable(src), "{src}");
        }
    }

    #[test]
    fn a_nested_loop_reading_anything_else_is_refused() {
        for src in [
            // The iterable is outer context.
            "{% for r in items %}{% for t in all_tags %}{{ t }}{% endfor %}{% endfor %}",
            // The inner body reads outer context.
            "{% for r in items %}{% for t in r.tags %}{{ prefix }}{{ t }}{% endfor %}{% endfor %}",
            // A filter argument is outer context.
            "{% for r in items %}{% for t in r.tags %}{{ t|default:fallback }}{% endfor %}{% endfor %}",
            // An inner loop variable read after its loop is the outer `t`.
            "{% for r in items %}{% for t in r.tags %}{{ t }}{% endfor %}{{ t }}{% endfor %}",
            // ... or in the `{% empty %}` block, where it is not bound.
            "{% for r in items %}{% for t in r.tags %}{% empty %}{{ t }}{% endfor %}{% endfor %}",
            // ... or in the iterable of its own loop.
            "{% for r in items %}{% for t in t.children %}{{ t }}{% endfor %}{% endfor %}",
            // A with-bound name read outside its with.
            "{% for r in items %}{% with ts=r.tags %}{% for t in ts %}{{ t }}{% endfor %}\
             {% endwith %}{{ ts }}{% endfor %}",
            // A tag that carries state across items, inside the inner loop.
            "{% for r in items %}{% for t in r.tags %}{% cycle 'a' 'b' %}{% endfor %}{% endfor %}",
            // A clock-dependent filter on the inner iterable.
            "{% for r in items %}{% for t in r.tags|random %}{{ t }}{% endfor %}{% endfor %}",
        ] {
            assert!(!storable(src), "{src}");
        }
    }

    #[test]
    fn switched_off_a_nested_loop_is_refused_and_flat_bodies_are_unchanged() {
        let nested =
            outer_body("{% for r in items %}{% for t in r.tags %}{{ t }}{% endfor %}{% endfor %}");
        let r = ["r".to_owned()];
        assert!(body_is_item_storable_in(&nested, &r, true));
        assert!(!body_is_item_storable_in(&nested, &r, false));
        for (src, expected) in [
            (
                "{% for r in items %}{% if r.on %}{{ r.a }}{% endif %}{% endfor %}",
                true,
            ),
            (
                "{% for r in items %}{{ prefix }}{{ r.a }}{% endfor %}",
                false,
            ),
            (
                "{% for r in items %}{% with x=r.a %}{{ x }}{% endwith %}{% endfor %}",
                true,
            ),
        ] {
            let body = outer_body(src);
            assert_eq!(body_is_item_storable_in(&body, &r, true), expected, "{src}");
            assert_eq!(
                body_is_item_storable_in(&body, &r, false),
                expected,
                "{src}"
            );
        }
    }

    #[test]
    fn nested_loop_var_names_lists_every_inner_binding() {
        let body = outer_body(
            "{% for r in items %}{% if r.on %}{% for a, b in r.p %}{% for c in b %}{% endfor %}\
             {% endfor %}{% endif %}{% endfor %}",
        );
        assert_eq!(nested_loop_var_names(&body), ["a", "b", "c"]);
    }
}
