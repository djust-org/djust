
// ============================================================================
// dj-virtual — Virtual / windowed lists with DOM recycling (v0.5.0+)
// ============================================================================
//
// Render only the visible slice of a large list. All items outside the
// viewport (plus overscan) are pulled out of the DOM on scroll; the container
// keeps a spacer element so the native scrollbar reflects the virtual length.
//
// Required attributes on the container:
//   dj-virtual="items_var_name"       — context variable driving the list
//
// Height modes (pick one):
//   dj-virtual-item-height="48"       — FIXED pixel height per item
//   dj-virtual-variable-height        — VARIABLE heights (ResizeObserver)
//
// Optional:
//   dj-virtual-overscan="5"           — rows rendered above/below (default 3)
//   dj-virtual-estimated-height="60"  — default baseline for unmeasured
//                                       items in variable mode (default 50)
//   dj-virtual-key-attr="data-key"    — attribute on each item used as the
//                                       height-cache key in VARIABLE mode
//                                       (default "data-key"). Keeps cached
//                                       heights bound to their item when
//                                       the list is reordered; missing
//                                       attribute falls back to index.
//
// The container itself must have a fixed height (e.g. style="height: 600px")
// and `overflow: auto`. Direct children must be pre-rendered server-side for
// first paint; on hydration we move them under an inner shell that uses
// translateY() to position the visible slice, and recycle nodes on scroll.
//
// Integration:
//   - djust.initVirtualLists(root) — scan + set up new containers
//   - djust.refreshVirtualList(el) — forced recompute after VDOM morph
//   - djust.teardownVirtualList(el) — remove observers (test helper)
//
// Throttling: scroll handler wrapped in requestAnimationFrame (one compute
// per frame). No setTimeout debounce.

(function initVirtualListModule() {
    const STATE = new WeakMap();
    const DEFAULT_OVERSCAN = 3;
    const DEFAULT_ESTIMATED_HEIGHT = 50;
    const DEFAULT_KEY_ATTR = 'data-key';

    // Build the stable cache key for an item in variable-height mode.
    // Prefer the configured data-key attribute so heights survive
    // reorders; fall back to the index string for back-compat with lists
    // that don't mark items with a key.
    function itemKey(state, node, idx) {
        if (state.keyAttr && node && node.nodeType === 1) {
            const k = node.getAttribute(state.keyAttr);
            if (k != null && k !== '') return 'k:' + k;
        }
        return 'i:' + idx;
    }

    function parseIntAttr(el, name, fallback) {
        const raw = el.getAttribute(name);
        if (raw == null || raw === '') return fallback;
        const n = parseInt(raw, 10);
        return Number.isFinite(n) && n >= 0 ? n : fallback;
    }

    function hasBoolAttr(el, name) {
        return el.hasAttribute(name);
    }

    function setup(container) {
        const fixedItemHeight = parseIntAttr(container, 'dj-virtual-item-height', 0);
        const variableMode = hasBoolAttr(container, 'dj-virtual-variable-height');

        // Mode detection:
        //  - fixed: dj-virtual-item-height set (> 0). variableMode ignored
        //    even if attribute is also present (fixed wins; deterministic).
        //  - variable: no valid item-height AND dj-virtual-variable-height is
        //    present.
        //  - otherwise: no-op (mirrors original behaviour).
        if (!fixedItemHeight && !variableMode) {
            if (globalThis.djustDebug) {
                console.warn(
                    '[dj-virtual] Missing dj-virtual-item-height and ' +
                    'dj-virtual-variable-height on', container
                );
            }
            return;
        }

        const overscan = parseIntAttr(container, 'dj-virtual-overscan', DEFAULT_OVERSCAN);
        const estimatedHeight = parseIntAttr(
            container, 'dj-virtual-estimated-height', DEFAULT_ESTIMATED_HEIGHT
        ) || DEFAULT_ESTIMATED_HEIGHT;
        // Attribute name whose value becomes the height-cache key. Empty
        // string (explicit opt-out) falls back to index-based keying.
        const keyAttrRaw = container.getAttribute('dj-virtual-key-attr');
        const keyAttr = keyAttrRaw == null ? DEFAULT_KEY_ATTR : keyAttrRaw;

        // Snapshot the pre-rendered children as the full item pool. Exclude
        // any stray shell/spacer markers (#2033): if a previous virtualization
        // was torn down but a marker element lingered, it must NEVER enter the
        // item pool and be rendered as a data row.
        const originalChildren = Array.from(container.children).filter(
            el => el.nodeType === 1 &&
                !el.hasAttribute('data-dj-virtual-shell') &&
                !el.hasAttribute('data-dj-virtual-spacer')
        );

        // Inner shell that actually holds the visible slice. A spacer sibling
        // forces the container scroll height to the virtual length.
        //
        // Layout contract (#1988): the shell is taken OUT of flow with
        // `position: absolute` (top/left/right: 0) so ONLY the spacer
        // contributes to `container.scrollHeight`. `position: relative` +
        // `transform` does NOT remove the shell from flow (transforms are a
        // paint-time effect per spec), so the shell's own rendered rows would
        // double-count against the spacer and leave dead space past the last
        // item. The container is made a positioned ancestor below (L121-123),
        // so the absolute shell anchors to the container and translateY()
        // still positions the visible slice; because it lives inside the
        // scroll container it scrolls with the content as expected.
        const shell = document.createElement('div');
        shell.setAttribute('data-dj-virtual-shell', '');
        shell.style.position = 'absolute';
        shell.style.top = '0';
        shell.style.left = '0';
        shell.style.right = '0';
        shell.style.willChange = 'transform';
        shell.style.transform = 'translateY(0px)';

        // The spacer alone defines the scrollable length. `flex-shrink: 0`
        // (#1988) keeps its explicit `style.height` honored inside a
        // `display: flex` container — a flex item's default `flex-shrink: 1`
        // otherwise crushes it to `offsetHeight: 0` once its height exceeds
        // the flex container's available space, silently killing the scroll.
        const spacer = document.createElement('div');
        spacer.setAttribute('data-dj-virtual-spacer', '');
        spacer.style.width = '1px';
        spacer.style.flexShrink = '0';
        spacer.style.pointerEvents = 'none';
        spacer.style.visibility = 'hidden';

        container.innerHTML = '';
        container.appendChild(shell);
        container.appendChild(spacer);
        if (getComputedStyle(container).position === 'static') {
            container.style.position = 'relative';
        }

        const state = {
            container,
            shell,
            spacer,
            // Identity of the view/data-source this virtualization belongs to
            // (#2033). Captured at setup so a later SPA-nav that repurposes the
            // SAME physical container for a DIFFERENT view (dj-virtual removed,
            // its source-var value changed, or the host view's dj-id changed)
            // can be detected and torn down instead of leaking stale rows.
            virtualValue: container.getAttribute('dj-virtual'),
            viewId: container.getAttribute('dj-id'),
            mode: fixedItemHeight ? 'fixed' : 'variable',
            itemHeight: fixedItemHeight,       // fixed mode only
            estimatedHeight,                   // variable mode fallback
            keyAttr,                           // variable mode: item key source
            heights: new Map(),                // variable mode: itemKey -> px
            offsets: null,                     // variable mode: prefix-sum cache (lazy)
            overscan,
            items: originalChildren,
            visibleStart: 0,
            visibleEnd: 0,
            rafPending: false,
            onScroll: null,
            onResize: null,
            resizeObserver: null,
            itemObserver: null,                // variable mode: per-item RO
            nodeToIndex: new WeakMap(),        // variable mode: reverse lookup
        };

        state.onScroll = () => requestFrame(state);
        state.onResize = () => requestFrame(state);

        container.addEventListener('scroll', state.onScroll, { passive: true });
        if (typeof ResizeObserver !== 'undefined') {
            state.resizeObserver = new ResizeObserver(state.onResize);
            state.resizeObserver.observe(container);

            if (state.mode === 'variable') {
                // Per-item observer: every item that scrolls into the window
                // gets measured; cache lookups drive the offset math.
                state.itemObserver = new ResizeObserver((entries) => {
                    let dirty = false;
                    for (const entry of entries) {
                        const node = entry.target;
                        const idx = state.nodeToIndex.get(node);
                        if (idx == null) continue;
                        // Prefer borderBox for layout-accurate measurement;
                        // fall back to getBoundingClientRect for older engines
                        // (jsdom's ResizeObserver stub often omits both).
                        let h = 0;
                        if (entry.borderBoxSize && entry.borderBoxSize.length) {
                            h = entry.borderBoxSize[0].blockSize;
                        } else if (entry.contentRect) {
                            h = entry.contentRect.height;
                        }
                        if (!h) h = node.getBoundingClientRect().height;
                        h = Math.round(h);
                        const cacheKey = itemKey(state, node, idx);
                        if (h > 0 && state.heights.get(cacheKey) !== h) {
                            state.heights.set(cacheKey, h);
                            dirty = true;
                        }
                    }
                    if (dirty) {
                        state.offsets = null; // invalidate prefix-sum cache
                        requestFrame(state);
                    }
                });
            }
        }

        STATE.set(container, state);
        render(state);
    }

    function requestFrame(state) {
        if (state.rafPending) return;
        state.rafPending = true;
        const raf = typeof requestAnimationFrame !== 'undefined'
            ? requestAnimationFrame
            : (cb) => setTimeout(cb, 16);
        raf(() => {
            state.rafPending = false;
            render(state);
        });
    }

    // --- variable-mode geometry helpers --------------------------------------

    function heightFor(state, idx) {
        // eslint-disable-next-line security/detect-object-injection
        const node = state.items[idx];
        const cacheKey = itemKey(state, node, idx);
        const cached = state.heights.get(cacheKey);
        if (cached != null) return cached;
        return state.estimatedHeight;
    }

    // Build the prefix-sum offset array lazily. `offsets[i]` = sum of heights
    // for items [0..i). `offsets[total]` = virtual total height.
    function ensureOffsets(state) {
        if (state.offsets) return state.offsets;
        const total = state.items.length;
        const offsets = new Float64Array(total + 1);
        let acc = 0;
        for (let i = 0; i < total; i++) {
            // eslint-disable-next-line security/detect-object-injection
            offsets[i] = acc;
            acc += heightFor(state, i);
        }
        // eslint-disable-next-line security/detect-object-injection
        offsets[total] = acc;
        state.offsets = offsets;
        return offsets;
    }

    // Binary search: first index whose offset + height > scrollTop.
    function firstVisibleIndex(offsets, scrollTop, total) {
        if (scrollTop <= 0 || total === 0) return 0;
        let lo = 0;
        let hi = total;
        while (lo < hi) {
            const mid = (lo + hi) >>> 1;
            if (offsets[mid + 1] <= scrollTop) lo = mid + 1;
            else hi = mid;
        }
        return Math.min(lo, Math.max(0, total - 1));
    }

    function render(state) {
        if (state.mode === 'variable') {
            renderVariable(state);
        } else {
            renderFixed(state);
        }
    }

    function renderFixed(state) {
        const { container, shell, spacer, itemHeight, overscan, items } = state;
        const total = items.length;

        spacer.style.height = (total * itemHeight) + 'px';

        if (total === 0) {
            shell.innerHTML = '';
            shell.style.transform = 'translateY(0px)';
            state.visibleStart = 0;
            state.visibleEnd = 0;
            return;
        }

        const viewportHeight = container.clientHeight || 0;
        const scrollTop = container.scrollTop || 0;

        const firstVisible = Math.floor(scrollTop / itemHeight);
        const visibleCount = Math.max(1, Math.ceil(viewportHeight / itemHeight));

        const start = Math.max(0, firstVisible - overscan);
        const end = Math.min(total, firstVisible + visibleCount + overscan);

        if (start === state.visibleStart && end === state.visibleEnd && shell.childElementCount === end - start) {
            // Window unchanged and DOM already populated — nothing to do.
            shell.style.transform = 'translateY(' + (start * itemHeight) + 'px)';
            return;
        }

        // Recycle by clearing the shell and re-attaching the slice nodes.
        // We reuse the real element references from `items` so frameworks /
        // tests can rely on identity across scrolls.
        shell.textContent = '';
        const frag = document.createDocumentFragment();
        for (let i = start; i < end; i++) {
            // eslint-disable-next-line security/detect-object-injection
            const node = items[i];
            node.style.height = itemHeight + 'px';
            node.style.boxSizing = 'border-box';
            frag.appendChild(node);
        }
        shell.appendChild(frag);
        shell.style.transform = 'translateY(' + (start * itemHeight) + 'px)';

        state.visibleStart = start;
        state.visibleEnd = end;
    }

    function renderVariable(state) {
        const { container, shell, spacer, overscan, items } = state;
        const total = items.length;

        if (total === 0) {
            spacer.style.height = '0px';
            shell.innerHTML = '';
            shell.style.transform = 'translateY(0px)';
            state.visibleStart = 0;
            state.visibleEnd = 0;
            return;
        }

        const offsets = ensureOffsets(state);
        // eslint-disable-next-line security/detect-object-injection
        spacer.style.height = offsets[total] + 'px';

        const viewportHeight = container.clientHeight || 0;
        const scrollTop = container.scrollTop || 0;

        const firstVisible = firstVisibleIndex(offsets, scrollTop, total);

        // Walk forward from firstVisible until we've covered viewportHeight.
        let end = firstVisible;
        let covered = 0;
        while (end < total && covered < viewportHeight) {
            covered += heightFor(state, end);
            end++;
        }
        // If we never reached viewportHeight (e.g. list shorter), end === total.
        end = Math.min(total, end + overscan);
        const start = Math.max(0, firstVisible - overscan);

        // Attach slice nodes, register with the per-item observer.
        shell.textContent = '';
        const frag = document.createDocumentFragment();
        // Disconnect + re-observe only items currently on-screen. Using a
        // single WeakMap (nodeToIndex) lets us update index bindings cheaply
        // when the slice shifts.
        if (state.itemObserver) {
            // We don't disconnect wholesale — ResizeObserver keeps per-target
            // entries, and re-calling observe() on the same node is a no-op.
            // We DO need to seed the nodeToIndex map so resize callbacks can
            // resolve node -> current index.
        }
        for (let i = start; i < end; i++) {
            // eslint-disable-next-line security/detect-object-injection
            const node = items[i];
            node.style.boxSizing = 'border-box';
            // DO NOT set a fixed height — variable mode lets content size
            // itself, and ResizeObserver reports back.
            state.nodeToIndex.set(node, i);
            frag.appendChild(node);
            if (state.itemObserver) {
                try {
                    state.itemObserver.observe(node);
                } catch (e) {
                    // Some jsdom versions throw on re-observe of same node.
                    if (globalThis.djustDebug) {
                        console.warn('[dj-virtual] itemObserver.observe failed', e);
                    }
                }
            } else {
                // No RO available (very old environment): read the height
                // synchronously so at least the prefix-sum converges after
                // the first render pass.
                const rect = typeof node.getBoundingClientRect === 'function'
                    ? node.getBoundingClientRect()
                    : null;
                if (rect && rect.height > 0) {
                    const h = Math.round(rect.height);
                    const cacheKey = itemKey(state, node, i);
                    if (state.heights.get(cacheKey) !== h) {
                        state.heights.set(cacheKey, h);
                        state.offsets = null; // recompute next frame
                    }
                }
            }
        }
        shell.appendChild(frag);
        // eslint-disable-next-line security/detect-object-injection
        shell.style.transform = 'translateY(' + offsets[start] + 'px)';

        state.visibleStart = start;
        state.visibleEnd = end;
    }

    // Is the container's managed shell/spacer still the pair this state owns
    // and still attached? (#1989 symptom 1) A server-driven re-render
    // (morphdom / VDOM patch) has no notion of client-side virtualization —
    // it diffs against the raw server `{% for %}` list and can replace the
    // container's children back to that list, DETACHING the shell/spacer or
    // REPURPOSING them into ordinary rows (stripping the marker attributes).
    // Either way the tracked state is stale and the container must be
    // re-initialised from its current (server-authored) children. The WeakMap
    // key (the container element) is unchanged across this, so the plain
    // "already tracked → skip" guard would leave it permanently
    // un-virtualized.
    function structureIntact(container, state) {
        // #2033: a repurposed container (dj-virtual removed, its source-var
        // value changed, or the host view's dj-id changed across SPA nav) is
        // NEVER "intact" — the same-node reuse path must not treat it as still
        // virtualized against the OLD view. Fail closed BEFORE the shell/spacer
        // checks so the caller re-establishes (or tears down) rather than
        // rendering the old pool into a stale shell.
        if (identityChanged(container, state)) return false;
        return (
            state.shell.parentNode === container &&
            state.shell.getAttribute('data-dj-virtual-shell') !== null &&
            state.spacer.parentNode === container &&
            state.spacer.getAttribute('data-dj-virtual-spacer') !== null
        );
    }

    // Has the container been repurposed for a DIFFERENT view / data source
    // since setup() captured its identity? (#2033) SPA-style navigation can
    // reuse the SAME physical container node across a view change instead of
    // remounting; nothing else tears the virtualization down. Any of:
    //   - dj-virtual attribute removed (the new view isn't virtualized), OR
    //   - dj-virtual value changed to a different source-var name, OR
    //   - the host view's dj-id changed (server stamps dj-id by template
    //     position, so it is stable across same-view re-renders and only
    //     differs across an actual view swap).
    // getAttribute() returns null for a removed attribute, so the value/id
    // comparisons cover the "removed" case too.
    function identityChanged(container, state) {
        return (
            container.getAttribute('dj-virtual') !== state.virtualValue ||
            container.getAttribute('dj-id') !== state.viewId
        );
    }

    // Drop observers/listeners and the STATE entry WITHOUT restoring the item
    // pool. Used when a server re-render has ALREADY replaced the container's
    // children with the authoritative list, so setup() can re-snapshot them
    // fresh. (teardownVirtualList, by contrast, restores the virtualized item
    // pool — that's the "dj-virtual attribute removed" case.)
    function detachState(container, state) {
        container.removeEventListener('scroll', state.onScroll);
        if (state.resizeObserver) state.resizeObserver.disconnect();
        if (state.itemObserver) state.itemObserver.disconnect();
        STATE.delete(container);
    }

    // Tear down a virtualization whose container was REPURPOSED for a different
    // view / data source across SPA navigation (#2033). Unlike
    // teardownVirtualList (author removed dj-virtual on an otherwise-unchanged
    // list → restore the full item pool), the server morph has ALREADY
    // authored the new view's content into the container, so we must NOT
    // restore the old item pool — that would re-inject the previous thread's
    // rows over the new view. We only strip the stale shell/spacer (which carry
    // the leaked, mis-offset old rows) and drop the STATE entry. Whatever the
    // morph left as loose children (the new view) is preserved; if the
    // container still carries dj-virtual, the caller re-runs setup() to
    // re-virtualize the NEW content fresh.
    function detachStaleState(container, state) {
        detachState(container, state);
        if (state.shell && state.shell.parentNode === container) {
            container.removeChild(state.shell);
        }
        if (state.spacer && state.spacer.parentNode === container) {
            container.removeChild(state.spacer);
        }
    }

    // Scan `scope` for tracked-but-now-stale containers and tear them down
    // (#2033). The WeakMap keyed on the container node is NOT iterable, so we
    // discover tracked containers via the never-removed shell marker
    // (data-dj-virtual-shell) and re-check the PARENT container's live identity
    // against what setup() captured. Containers that lost dj-virtual are NOT in
    // the [dj-virtual] selector reinitAfterDOMUpdate iterates, so this marker
    // scan is the ONLY path that reaps them.
    function reapStaleVirtualLists(scope) {
        if (!scope || !scope.querySelectorAll) return;
        scope.querySelectorAll('[data-dj-virtual-shell]').forEach((shell) => {
            const container = shell.parentNode;
            if (!container || container.nodeType !== 1) return;
            const state = STATE.get(container);
            // Only reap containers we actually track whose shell is the one we
            // own (avoid touching a foreign/duplicated marker).
            if (!state || state.shell !== shell) return;
            if (identityChanged(container, state)) {
                detachStaleState(container, state);
            }
        });
    }

    // Absorb "loose" element children a server re-render appended OUTSIDE the
    // shell/spacer wrapper (#1989 symptom 2 — e.g. a stream-appended chat
    // row) into the item pool, so they render INSIDE the shell and receive
    // subsequent patches instead of leaking as stray siblings whose finalize
    // patch never lands. Loose = any element child that is neither the shell
    // nor the spacer. Appended in document order (append-only assumption; a
    // mid-list server insert would land at the tail — strictly better than
    // leaking, and honestly noted in the guide). Returns whether anything was
    // absorbed.
    function absorbLooseChildren(container, state) {
        // #2033: never merge content across an identity change. If the
        // container was repurposed for a different view/data-source, the
        // previous thread's rows and the new thread's rows must NOT land in one
        // pool (they would window/render together at the same offset). The
        // callers reap stale containers before reaching here; this guard is
        // defensive so a cross-source pool merge can't happen even if absorb is
        // reached on a stale container.
        if (identityChanged(container, state)) return false;
        const loose = [];
        for (const node of Array.from(container.children)) {
            if (node === state.shell || node === state.spacer) continue;
            if (node.nodeType !== 1) continue;
            // Never absorb a stray shell/spacer marker as a data row.
            if (node.hasAttribute('data-dj-virtual-shell') ||
                node.hasAttribute('data-dj-virtual-spacer')) continue;
            loose.push(node);
        }
        if (loose.length === 0) return false;
        for (const node of loose) {
            // De-parent: render() re-attaches the visible slice into the
            // shell. Off-window items stay detached, held only in state.items.
            if (node.parentNode === container) container.removeChild(node);
            state.items.push(node);
        }
        // New items invalidate the current window so render() re-slices.
        state.visibleStart = -1;
        state.visibleEnd = -1;
        if (state.mode === 'variable') {
            state.offsets = null;
        }
        return true;
    }

    function initVirtualLists(root) {
        const scope = root || document;
        // #2033: FIRST reap containers tracked in STATE that have lost
        // dj-virtual or changed view identity across SPA navigation. These are
        // invisible to the [dj-virtual] selector below (an attr-loss container
        // no longer matches it), so the shell-marker scan is the only way to
        // tear their stale shell/spacer/state down. Reaping first also means a
        // still-virtualized-but-repurposed container (source value/dj-id
        // changed) falls into the `!state` → setup() branch below and is
        // re-virtualized fresh against the NEW content.
        reapStaleVirtualLists(scope);
        const containers = scope.querySelectorAll
            ? scope.querySelectorAll('[dj-virtual]')
            : [];
        containers.forEach(container => {
            const state = STATE.get(container);
            if (!state) {
                setup(container);
            } else if (!structureIntact(container, state)) {
                // #1989 symptom 1: a server re-render clobbered the managed
                // structure back to the raw list. Detach the stale state and
                // re-run setup against the fresh children instead of no-oping.
                detachState(container, state);
                setup(container);
            }
        });
    }

    function refreshVirtualList(container) {
        const state = STATE.get(container);
        if (!state) return;

        // #2033: the container was repurposed for a different view / data
        // source (dj-virtual removed, its source value changed, or the host
        // view's dj-id changed). Tear the stale virtualization down WITHOUT
        // restoring the old pool — the server morph already authored the new
        // view's content. If the container still carries dj-virtual, re-setup
        // fresh against that new content; otherwise it falls back to plain
        // (non-virtualized) rendering. Checked BEFORE the structure/self-heal
        // path so a repurposed container never re-snapshots the old rows.
        if (identityChanged(container, state)) {
            detachStaleState(container, state);
            if (container.getAttribute('dj-virtual') != null) {
                setup(container);
            }
            return;
        }

        // #1989 symptom 1: self-heal if a server re-render clobbered the
        // managed structure (idempotent with initVirtualLists' own check —
        // whichever runs first heals; the other becomes a no-op). This keeps
        // refreshVirtualList robust regardless of call order.
        if (!structureIntact(container, state)) {
            detachState(container, state);
            setup(container);
            return;
        }

        // Re-snapshot: after VDOM morph, the shell holds only the visible
        // slice. The full data source is whatever is currently in the shell
        // plus any new children added outside of virtualization. We support
        // an external update path: callers may set `container.__djVirtualItems`
        // to an array of HTMLElements to replace the item pool.
        const replacement = container.__djVirtualItems;
        if (Array.isArray(replacement)) {
            state.items = replacement.slice();
            delete container.__djVirtualItems;
            state.visibleStart = -1;
            state.visibleEnd = -1;
            // In variable mode, replacing the pool invalidates cached
            // heights keyed by index (item i may be a different item
            // now). Heights keyed by `data-key` survive reorders, so we
            // only drop index-keyed entries and keep `k:*` entries.
            if (state.mode === 'variable') {
                if (state.keyAttr) {
                    const preserved = new Map();
                    for (const [k, v] of state.heights) {
                        if (typeof k === 'string' && k.charCodeAt(0) === 107) {
                            // starts with 'k:' — data-key entry
                            preserved.set(k, v);
                        }
                    }
                    state.heights = preserved;
                } else {
                    state.heights = new Map();
                }
                state.offsets = null;
                state.nodeToIndex = new WeakMap();
            }
        } else {
            // No explicit replacement pool: auto-derive from the container's
            // real children so stream-appended rows (#1989 symptom 2) get
            // absorbed into the shell instead of leaking as stray siblings.
            absorbLooseChildren(container, state);
        }
        render(state);
    }

    function teardownVirtualList(container) {
        const state = STATE.get(container);
        if (!state) return;
        detachState(container, state);
        // Restore the pre-virtualization children and remove the shell/spacer.
        // Without this, removing `dj-virtual` from a live container leaves
        // the injected wrapper elements in place and shows only the
        // currently-visible slice — confusing for downstream consumers.
        try {
            container.textContent = '';
            const frag = document.createDocumentFragment();
            for (const node of state.items) frag.appendChild(node);
            container.appendChild(frag);
        } catch (e) {
            if (globalThis.djustDebug) {
                console.warn('[dj-virtual] teardown restore failed', e);
            }
        }
        // STATE entry already dropped by detachState() above.
    }

    window.djust = window.djust || {};
    window.djust.initVirtualLists = initVirtualLists;
    window.djust.refreshVirtualList = refreshVirtualList;
    window.djust.teardownVirtualList = teardownVirtualList;
})();
