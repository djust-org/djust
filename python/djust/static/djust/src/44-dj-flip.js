
// dj-flip — FLIP-technique list reorder animations
// (v0.6.0, phase 2d — animations milestone finale).
//
// FLIP = First, Last, Invert, Play (Paul Lewis, 2015). When a child
// element reorders within its [dj-flip] parent, we:
//   1. First — read rect BEFORE the reorder (cached on install + after
//      every settled animation).
//   2. Last — read rect AFTER the mutation.
//   3. Invert — apply `transform: translate(-dx, -dy)` with
//      `transition: none`, which visually pins the child at its old
//      position even though the DOM has already moved.
//   4. Play — on the next animation frame, clear the transform and
//      populate `transition: transform <duration>ms <easing>`. The
//      browser animates back to identity = the new position.
//
// This is a hook-free integration: we install a per-parent
// MutationObserver(childList, subtree:false), same architecture as
// 43-dj-transition-group.js. The VDOM patcher's `MoveChild` patch
// calls `insertBefore()` on the same DOM node, so the node's identity
// is preserved and our WeakMap rect cache survives reorders.
//
// Usage:
//   <ul dj-flip>
//     {% for item in items %}<li id="item-{{ item.id }}">{{ item.label }}</li>{% endfor %}
//   </ul>
//
// Tunables (on the parent):
//   dj-flip-duration="500"               — transition duration in ms (default 300).
//   dj-flip-easing="ease-in"             — CSS easing function
//                                          (default cubic-bezier(.2,.8,.2,1)).
//
// Accessibility: respects `prefers-reduced-motion: reduce` — when true,
// we skip all transforms and return silently. Matches dj-transition's
// philosophy of degrading to an instant change.
//
// Keyed lists: FLIP animates items whose DOM identity is preserved by
// the VDOM diff. Rust's `diff_keyed_children` emits `MoveChild` only for
// items with stable `id=` (or `dj-id`); without them the diff falls
// back to delete+insert, for which FLIP correctly no-ops (no cached
// First rect to invert against).
//
// Nested [dj-flip] parents are isolated: each installs its own
// MutationObserver with subtree:false so the outer parent does not see
// the inner parent's childList mutations.
//
// SVG / table children are out of v1 scope — `transform:translate()`
// on non-block/flex children has weird behavior. Document, don't
// enforce.

(function () {


    const _FLIP_ATTR = "dj-flip";
    const _DURATION_ATTR = "dj-flip-duration";
    const _EASING_ATTR = "dj-flip-easing";
    const _DEFAULT_DURATION = 300;
    const _DEFAULT_EASING = "cubic-bezier(.2,.8,.2,1)";

    // Per-parent bookkeeping. We use a Map (not WeakMap) for the parent
    // registry so tests can probe `_observerCount()`; the MutationObserver
    // anchor itself keeps the parent element alive, so the WeakMap
    // idiom doesn't buy us anything here.
    const _installedParents = new Map(); // Element -> { observer, rectCache }
    let _rootObserverInstalled = false;

    // Rect cache — WeakMap so rects don't leak when a child is GC'd.
    // We keep ONE global cache rather than per-parent because children
    // can (theoretically) move between parents, and the WeakMap does not
    // grow unbounded.
    const _rectCache = new WeakMap(); // Element -> DOMRect

    function _reducedMotion() {
        if (typeof matchMedia !== "function") return false;
        try {
            return matchMedia("(prefers-reduced-motion: reduce)").matches;
        } catch (_e) {
            return false;
        }
    }

    function _parseDuration(parent) {
        const raw = parent.getAttribute && parent.getAttribute(_DURATION_ATTR);
        if (raw === null || raw === undefined || raw === "") return _DEFAULT_DURATION;
        // Use Number() rather than parseInt(raw, 10) — parseInt accepts
        // trailing garbage ("300abc" → 300) which is a silent footgun.
        // Number() requires the WHOLE string to be a valid number or it
        // returns NaN, which we then reject via Number.isFinite().
        const n = Number(raw);
        if (!Number.isFinite(n) || n < 0) return _DEFAULT_DURATION;
        // Clamp to a sane upper bound — a 60-second FLIP is almost
        // certainly a typo, and a runaway `transition` ties up the
        // element's inline style indefinitely.
        if (n > 30000) return 30000;
        return n;
    }

    function _parseEasing(parent) {
        const raw = parent.getAttribute && parent.getAttribute(_EASING_ATTR);
        if (!raw) return _DEFAULT_EASING;
        // Light validation: reject anything with a semicolon or quote —
        // those would break out of the transition shorthand. Accepted
        // chars match CSS timing-function syntax (letters, digits,
        // hyphens, dots, parens, commas, whitespace).
        if (/[;"'<>]/.test(raw)) return _DEFAULT_EASING;
        return raw;
    }

    function _snapshotRects(parent) {
        // Read rects for every direct child and cache them. Called once
        // on install and again after each animation cycle so the "First"
        // rects for the NEXT reorder are fresh.
        const children = Array.prototype.slice.call(parent.children);
        for (let i = 0; i < children.length; i++) {
            // eslint-disable-next-line security/detect-object-injection
            const c = children[i];
            if (c.nodeType !== 1) continue;
            try {
                _rectCache.set(c, c.getBoundingClientRect());
            } catch (_e) {
                // Detached node or exotic child — skip.
            }
        }
    }

    function _requestAnimationFrame(fn) {
        if (typeof globalThis.requestAnimationFrame === "function") {
            return globalThis.requestAnimationFrame(fn);
        }
        return setTimeout(fn, 16);
    }

    function _animateReorder(parent) {
        // Called when the MutationObserver on `parent` sees a childList
        // mutation. Read the new rects, diff against cache, inverse-
        // translate the movers, then kick off the transition on the
        // next frame.
        if (_reducedMotion()) {
            // Refresh cache so future animations (after the preference
            // flips back) have fresh "First" rects.
            _snapshotRects(parent);
            return;
        }

        const duration = _parseDuration(parent);
        const easing = _parseEasing(parent);
        const children = Array.prototype.slice.call(parent.children);
        const movers = [];

        // Pass 1 — READ. Collect new rects for every child with a cached
        // prior rect. Doing all reads before any writes avoids layout
        // thrashing (read-read-read, then write-write-write).
        for (let i = 0; i < children.length; i++) {
            // eslint-disable-next-line security/detect-object-injection
            const c = children[i];
            if (c.nodeType !== 1) continue;
            const prev = _rectCache.get(c);
            if (!prev) continue; // New child — no First rect to invert.
            let next;
            try {
                next = c.getBoundingClientRect();
            } catch (_e) {
                continue;
            }
            const dx = prev.left - next.left;
            const dy = prev.top - next.top;
            if (dx === 0 && dy === 0) continue; // Didn't move.
            movers.push({ el: c, dx: dx, dy: dy });
        }

        // Pass 2 — WRITE the inverse. Pin each mover at its old position
        // with transition:none so the layout change is invisible to the
        // user. We preserve any pre-existing transition style so we can
        // restore it at cleanup time.
        for (let i = 0; i < movers.length; i++) {
            // eslint-disable-next-line security/detect-object-injection
            const m = movers[i];
            m.prevTransition = m.el.style.transition;
            m.prevTransform = m.el.style.transform;
            m.el.style.transition = "none";
            m.el.style.transform = "translate(" + m.dx + "px, " + m.dy + "px)";
        }

        // Pass 3 — PLAY on the next frame. Browser commits the inverse
        // transform, then we clear it back to identity under a
        // transition. Setting `transform = ''` lets the browser animate
        // from the inverted position to identity, which visually
        // matches the DOM's actual new position.
        _requestAnimationFrame(function () {
            for (let i = 0; i < movers.length; i++) {
                // eslint-disable-next-line security/detect-object-injection
                const m = movers[i];
                m.el.style.transition = "transform " + duration + "ms " + easing;
                m.el.style.transform = "";
            }
        });

        // Cleanup — after the transition should have finished, strip
        // our inline styles so the element is clean for subsequent
        // patches. Use duration + 50 ms margin; jsdom never fires
        // transitionend so we cannot rely on that event.
        const cleanupDelay = duration + 50;
        setTimeout(function () {
            for (let i = 0; i < movers.length; i++) {
                // eslint-disable-next-line security/detect-object-injection
                const m = movers[i];
                // Only clear if our values are still there — a subsequent
                // reorder might already have overwritten them. We restore
                // BOTH transition and transform symmetrically: if the
                // author had `transform: rotate(5deg)` inline before
                // mount, we must put it back — otherwise FLIP would
                // permanently stomp author-specified transforms after
                // the first reorder.
                if (m.el.style.transform === "") {
                    m.el.style.transition = m.prevTransition || "";
                    m.el.style.transform = m.prevTransform || "";
                }
            }
            // Refresh cache for the next reorder — but ONLY if no child
            // is still mid-animation from a later reorder. If a second
            // reorder fired while this one was playing, reading rects now
            // would capture intermediate positions and corrupt the cache.
            // The LATER reorder's own cleanup timer will snapshot when
            // it's safe.
            const inFlight = Array.prototype.some.call(
                parent.children,
                function (el) {
                    return el.style && el.style.transition &&
                        el.style.transition.indexOf("transform") !== -1;
                }
            );
            if (!inFlight) _snapshotRects(parent);
        }, cleanupDelay);
    }

    function _install(parent) {
        if (!parent || parent.nodeType !== 1) return;
        if (_installedParents.has(parent)) return;
        _snapshotRects(parent);
        if (typeof MutationObserver === "undefined") {
            _installedParents.set(parent, { observer: null });
            return;
        }
        const observer = new MutationObserver(function (mutations) {
            let sawChild = false;
            for (let i = 0; i < mutations.length; i++) {
                // eslint-disable-next-line security/detect-object-injection
                if (mutations[i].type === "childList") {
                    sawChild = true;
                    break;
                }
            }
            if (!sawChild) return;
            _animateReorder(parent);
        });
        observer.observe(parent, { childList: true, subtree: false });
        _installedParents.set(parent, { observer: observer });
    }

    function _uninstall(parent) {
        if (!parent || parent.nodeType !== 1) return;
        const entry = _installedParents.get(parent);
        if (!entry) return;
        if (entry.observer) {
            entry.observer.disconnect();
        }
        _installedParents.delete(parent);
    }

    function _rescan() {
        if (typeof document === "undefined") return;
        document.querySelectorAll("[" + _FLIP_ATTR + "]").forEach(_install);
    }

    function _installRootObserver() {
        if (_rootObserverInstalled) return;
        _rootObserverInstalled = true;
        _rescan();
        if (typeof MutationObserver === "undefined") return;
        const rootObserver = new MutationObserver(function (mutations) {
            for (let i = 0; i < mutations.length; i++) {
                // eslint-disable-next-line security/detect-object-injection
                const m = mutations[i];
                if (m.type === "attributes" && m.attributeName === _FLIP_ATTR) {
                    if (m.target.hasAttribute(_FLIP_ATTR)) {
                        _install(m.target);
                    } else {
                        _uninstall(m.target);
                    }
                } else if (m.type === "childList") {
                    m.addedNodes.forEach(function (node) {
                        if (node.nodeType !== 1) return;
                        if (node.hasAttribute && node.hasAttribute(_FLIP_ATTR)) {
                            _install(node);
                        }
                        if (node.querySelectorAll) {
                            node.querySelectorAll("[" + _FLIP_ATTR + "]").forEach(_install);
                        }
                    });
                    m.removedNodes.forEach(function (node) {
                        if (node.nodeType !== 1) return;
                        if (node.hasAttribute && node.hasAttribute(_FLIP_ATTR)) {
                            _uninstall(node);
                        }
                        if (node.querySelectorAll) {
                            node.querySelectorAll("[" + _FLIP_ATTR + "]").forEach(_uninstall);
                        }
                    });
                }
            }
        });
        rootObserver.observe(document.documentElement, {
            attributes: true,
            attributeFilter: [_FLIP_ATTR],
            subtree: true,
            childList: true,
        });
    }

    if (typeof document !== "undefined") {
        if (document.readyState === "loading") {
            document.addEventListener("DOMContentLoaded", _installRootObserver);
        } else {
            _installRootObserver();
        }
    }

    globalThis.djust = globalThis.djust || {};
    globalThis.djust.flip = {
        _install: _install,
        _uninstall: _uninstall,
        _rescan: _rescan,
        _animateReorder: _animateReorder,
        _snapshotRects: _snapshotRects,
        _parseDuration: _parseDuration,
        _parseEasing: _parseEasing,
        _observerCount: function () { return _installedParents.size; },
        _installedParents: _installedParents,
        _rectCache: _rectCache,
    };
})();
