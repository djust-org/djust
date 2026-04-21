
// ============================================================================
// dj-virtual — Virtual / windowed lists with DOM recycling (v0.5.0)
// ============================================================================
//
// Render only the visible slice of a large list. All items outside the
// viewport (plus overscan) are pulled out of the DOM on scroll; the container
// keeps a spacer element so the native scrollbar reflects the virtual length.
//
// Required attributes on the container:
//   dj-virtual="items_var_name"       — context variable driving the list
//   dj-virtual-item-height="48"       — fixed pixel height per item
//
// Optional:
//   dj-virtual-overscan="5"           — rows rendered above/below (default 3)
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

    function parseIntAttr(el, name, fallback) {
        const raw = el.getAttribute(name);
        if (raw == null || raw === '') return fallback;
        const n = parseInt(raw, 10);
        return Number.isFinite(n) && n >= 0 ? n : fallback;
    }

    function setup(container) {
        const itemHeight = parseIntAttr(container, 'dj-virtual-item-height', 0);
        if (!itemHeight) {
            if (globalThis.djustDebug) {
                console.warn('[dj-virtual] Missing or invalid dj-virtual-item-height on', container);
            }
            return;
        }
        const overscan = parseIntAttr(container, 'dj-virtual-overscan', DEFAULT_OVERSCAN);

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
            itemHeight,
            overscan,
            items: originalChildren, // data source: cloned DOM nodes
            visibleStart: 0,
            visibleEnd: 0,
            rafPending: false,
            onScroll: null,
            onResize: null,
            resizeObserver: null,
        };

        state.onScroll = () => requestFrame(state);
        state.onResize = () => requestFrame(state);

        container.addEventListener('scroll', state.onScroll, { passive: true });
        if (typeof ResizeObserver !== 'undefined') {
            state.resizeObserver = new ResizeObserver(state.onResize);
            state.resizeObserver.observe(container);
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

    function render(state) {
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
        }
        render(state);
    }

    function teardownVirtualList(container) {
        const state = STATE.get(container);
        if (!state) return;
        container.removeEventListener('scroll', state.onScroll);
        if (state.resizeObserver) state.resizeObserver.disconnect();
        STATE.delete(container);
    }

    window.djust = window.djust || {};
    window.djust.initVirtualLists = initVirtualLists;
    window.djust.refreshVirtualList = refreshVirtualList;
    window.djust.teardownVirtualList = teardownVirtualList;
})();
