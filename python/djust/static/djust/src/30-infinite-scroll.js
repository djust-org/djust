
// ============================================================================
// dj-viewport-top / dj-viewport-bottom — Bidirectional infinite scroll (v0.5.0)
// ============================================================================
//
// Fire server events when the first or last child of a stream container
// enters the viewport. Phoenix 1.0 parity with phx-viewport-top /
// phx-viewport-bottom.
//
// Attributes on the stream/list container:
//   dj-viewport-top="event_name"       — fire when first child enters viewport
//   dj-viewport-bottom="event_name"    — fire when last child enters viewport
//   dj-viewport-threshold="0.1"        — IntersectionObserver threshold (default 0.1)
//
// Firing semantics: once-per-entry. A sentinel attribute
// `data-dj-viewport-fired` is set on the sentinel child (first or last) so
// the same element doesn't re-fire on scroll oscillation. To re-arm after
// firing, either (a) replace the sentinel child via a stream op (the new
// child has no sentinel attribute), or (b) call `djust.resetViewport(container)`
// from a client-side hook. There is no corresponding HTML attribute —
// re-arming is programmatic.
//
// Integration:
//   - djust.initInfiniteScroll(root)
//   - djust.teardownInfiniteScroll(container)
//   - djust.resetViewport(container) — clear fired sentinels (re-arm)

(function initInfiniteScrollModule() {
    const STATE = new WeakMap();
    const DEFAULT_THRESHOLD = 0.1;

    function parseFloatAttr(el, name, fallback) {
        const raw = el.getAttribute(name);
        if (raw == null || raw === '') return fallback;
        const n = parseFloat(raw);
        return Number.isFinite(n) && n >= 0 && n <= 1 ? n : fallback;
    }

    function dispatch(container, eventName, edge) {
        // Dispatch a CustomEvent for tests and hook-based handlers to
        // observe, then send to the server via the same public entry
        // point that dj-click / dj-change / dj-submit use
        // (11-event-handler.js exposes this as window.djust.handleEvent).
        const detail = { event: eventName, edge, target: container };
        container.dispatchEvent(new CustomEvent('dj-viewport', {
            bubbles: true,
            detail,
        }));
        if (window.djust && typeof window.djust.handleEvent === 'function') {
            try {
                window.djust.handleEvent(eventName, { edge });
            } catch (err) {
                if (globalThis.djustDebug) {
                    console.warn(
                        '[dj-viewport] handleEvent failed for %s: %s',
                        eventName,
                        err,
                    );
                }
            }
        } else if (globalThis.djustDebug) {
            console.warn(
                '[dj-viewport] window.djust.handleEvent not available — ' +
                    'event %s not sent to server',
                eventName,
            );
        }
    }

    function markFired(el) {
        if (el && el.setAttribute) el.setAttribute('data-dj-viewport-fired', 'true');
    }
    function hasFired(el) {
        return !!(el && el.getAttribute && el.getAttribute('data-dj-viewport-fired') === 'true');
    }

    function setup(container) {
        if (typeof IntersectionObserver === 'undefined') {
            if (globalThis.djustDebug) {
                console.warn('[dj-viewport] IntersectionObserver not available');
            }
            return;
        }

        const topEvent = container.getAttribute('dj-viewport-top');
        const bottomEvent = container.getAttribute('dj-viewport-bottom');
        if (!topEvent && !bottomEvent) return;

        const threshold = parseFloatAttr(container, 'dj-viewport-threshold', DEFAULT_THRESHOLD);

        const state = {
            container,
            topEvent,
            bottomEvent,
            threshold,
            observer: null,
            observedTop: null,
            observedBottom: null,
        };

        state.observer = new IntersectionObserver((entries) => {
            for (const entry of entries) {
                if (!entry.isIntersecting) continue;
                const target = entry.target;
                if (hasFired(target)) continue;
                markFired(target);

                if (target === state.observedTop && state.topEvent) {
                    dispatch(container, state.topEvent, 'top');
                } else if (target === state.observedBottom && state.bottomEvent) {
                    dispatch(container, state.bottomEvent, 'bottom');
                }
            }
        }, {
            root: null,
            threshold,
        });

        STATE.set(container, state);
        observeSentinels(state);
    }

    function observeSentinels(state) {
        const { container, observer } = state;
        const kids = Array.from(container.children).filter(el => el.nodeType === 1);
        const first = kids[0] || null;
        const last = kids[kids.length - 1] || null;

        if (state.observedTop && state.observedTop !== first) {
            observer.unobserve(state.observedTop);
            state.observedTop = null;
        }
        if (state.observedBottom && state.observedBottom !== last) {
            observer.unobserve(state.observedBottom);
            state.observedBottom = null;
        }

        if (state.topEvent && first && first !== state.observedTop) {
            observer.observe(first);
            state.observedTop = first;
        }
        if (state.bottomEvent && last && last !== state.observedBottom && last !== first) {
            observer.observe(last);
            state.observedBottom = last;
        }
    }

    function initInfiniteScroll(root) {
        const scope = root || document;
        const containers = scope.querySelectorAll
            ? scope.querySelectorAll('[dj-viewport-top], [dj-viewport-bottom]')
            : [];
        containers.forEach(container => {
            if (!STATE.has(container)) {
                setup(container);
            } else {
                // Re-scan sentinels: children may have changed after VDOM morph.
                observeSentinels(STATE.get(container));
            }
        });
    }

    function resetViewport(container) {
        const state = STATE.get(container);
        if (!state) return;
        if (state.observedTop) state.observedTop.removeAttribute('data-dj-viewport-fired');
        if (state.observedBottom) state.observedBottom.removeAttribute('data-dj-viewport-fired');
    }

    function teardownInfiniteScroll(container) {
        const state = STATE.get(container);
        if (!state) return;
        if (state.observer) state.observer.disconnect();
        STATE.delete(container);
    }

    window.djust = window.djust || {};
    window.djust.initInfiniteScroll = initInfiniteScroll;
    window.djust.resetViewport = resetViewport;
    window.djust.teardownInfiniteScroll = teardownInfiniteScroll;
})();
