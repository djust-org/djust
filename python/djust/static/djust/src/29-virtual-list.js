
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

        // Snapshot the pre-rendered children as the full item pool.
        const originalChildren = Array.from(container.children).filter(
            el => el.nodeType === 1
        );

        // Inner shell that actually holds the visible slice. A spacer sibling
        // forces the container scroll height to the virtual length.
        const shell = document.createElement('div');
        shell.setAttribute('data-dj-virtual-shell', '');
        shell.style.position = 'relative';
        shell.style.willChange = 'transform';
        shell.style.transform = 'translateY(0px)';

        const spacer = document.createElement('div');
        spacer.setAttribute('data-dj-virtual-spacer', '');
        spacer.style.width = '1px';
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

    function initVirtualLists(root) {
        const scope = root || document;
        const containers = scope.querySelectorAll
            ? scope.querySelectorAll('[dj-virtual]')
            : [];
        containers.forEach(container => {
            if (!STATE.has(container)) setup(container);
        });
    }

    function refreshVirtualList(container) {
        const state = STATE.get(container);
        if (!state) return;
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
        }
        render(state);
    }

    function teardownVirtualList(container) {
        const state = STATE.get(container);
        if (!state) return;
        container.removeEventListener('scroll', state.onScroll);
        if (state.resizeObserver) state.resizeObserver.disconnect();
        if (state.itemObserver) state.itemObserver.disconnect();
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
        STATE.delete(container);
    }

    window.djust = window.djust || {};
    window.djust.initVirtualLists = initVirtualLists;
    window.djust.refreshVirtualList = refreshVirtualList;
    window.djust.teardownVirtualList = teardownVirtualList;
})();
