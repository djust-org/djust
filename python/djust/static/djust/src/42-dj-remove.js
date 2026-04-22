
// dj-remove — declarative CSS exit transitions (v0.6.0, phase 2a)
//
// Phoenix JS.hide / phx-remove parity. When a VDOM patch, morph loop,
// or dj-update prune would physically remove an element that carries
// dj-remove="...", djust delays the removal until the CSS transition
// the attribute describes has completed (or a 600 ms fallback fires).
//
// Usage — three-token form (matches dj-transition):
//   <li dj-remove="opacity-100 transition-opacity-300 opacity-0">Toast</li>
//
// Usage — single-token short form:
//   <li dj-remove="fade-out">Toast</li>
//
// The three-token form is identical to dj-transition: phase 1 applies
// the start class, phase 2 (next animation frame) swaps in the active
// class, and phase 3 applies the end class that drives the transition.
// The single-token form applies one class and waits for transitionend.
//
// Lifecycle — the caller in 12-vdom-patch.js invokes
// window.djust.maybeDeferRemoval(node) instead of its usual removeChild
// / replaceChild / el.remove() call. If the element carries
// [dj-remove], we apply the exit classes but do NOT detach the node —
// the element stays connected during the animation so the transition
// actually plays. We physically remove it when `transitionend` fires
// (or when the fallback timer expires).
//
// Override the 600 ms fallback via dj-remove-duration="N" (ms).
//
// Descendants: if a [dj-remove] element has [dj-remove] descendants,
// ONLY the outer element is deferred — descendants travel with it.
// This matches Phoenix.
//
// Cancellation: if a subsequent patch strips the dj-remove attribute
// from a pending element, the pending removal cancels — the applied
// exit classes are stripped, the fallback timer clears, and the
// element stays mounted. Implemented via a per-element MutationObserver
// watching `dj-remove`.
//
// Unlike 41-dj-transition.js, this module does NOT install a
// document-level MutationObserver. dj-remove is a pull API — the patch
// applier reaches out to us via maybeDeferRemoval() — so we don't need
// to watch the DOM for new dj-remove attributes.

const _pendingRemovals = new WeakMap();   // Element -> { fallback, onEnd, observer, spec }
const _REMOVE_FALLBACK_MS = 600;

function _parseRemoveSpec(raw) {
    if (raw === null || raw === undefined) return null;
    const parts = String(raw).trim().split(/\s+/).filter(Boolean);
    if (parts.length === 0) return null;
    if (parts.length === 1) return { single: parts[0] };
    if (parts.length >= 3) return { start: parts[0], active: parts[1], end: parts[2] };
    // Two tokens — ambiguous, treat as invalid (matches dj-transition).
    return null;
}

function _durationFor(el) {
    const raw = el.getAttribute && el.getAttribute('dj-remove-duration');
    if (raw === null || raw === undefined || raw === '') return _REMOVE_FALLBACK_MS;
    const n = parseInt(raw, 10);
    if (!Number.isFinite(n)) return _REMOVE_FALLBACK_MS;
    // Clamp to a sane range so a malformed attribute can't leak a pending
    // removal indefinitely or fire before the frame has had a chance to paint.
    if (n < 0) return 0;
    if (n > 30000) return 30000;
    return n;
}

function _finalizeRemoval(el) {
    // Called when the animation completes or the fallback fires. Clean up
    // all pending state and physically detach the element. Guard against
    // the element having been moved or already detached by another patch.
    const state = _pendingRemovals.get(el);
    if (!state) return;
    if (state.fallback) clearTimeout(state.fallback);
    if (state.onEnd) el.removeEventListener('transitionend', state.onEnd);
    if (state.observer) state.observer.disconnect();
    _pendingRemovals.delete(el);
    if (el.parentNode) {
        el.parentNode.removeChild(el);
    }
}

function _cancelRemoval(el) {
    // Called when the dj-remove attribute is stripped from a pending
    // element. Revert applied classes, clear the timer, leave the
    // element where it was.
    const state = _pendingRemovals.get(el);
    if (!state) return;
    if (state.fallback) clearTimeout(state.fallback);
    if (state.onEnd) el.removeEventListener('transitionend', state.onEnd);
    if (state.observer) state.observer.disconnect();
    const spec = state.spec;
    if (spec) {
        if (spec.single) {
            el.classList.remove(spec.single);
        } else {
            el.classList.remove(spec.active);
            el.classList.remove(spec.end);
            el.classList.remove(spec.start);
        }
    }
    _pendingRemovals.delete(el);
}

function _runRemove(el, spec) {
    // Phase 1 — start state (three-token form only).
    if (!spec.single) {
        el.classList.add(spec.start);
    }

    const state = { spec: spec };
    _pendingRemovals.set(el, state);

    function onEnd(ev) {
        if (ev.target !== el) return;
        _finalizeRemoval(el);
    }
    state.onEnd = onEnd;
    el.addEventListener('transitionend', onEnd);

    // Fallback in case transitionend never fires.
    state.fallback = setTimeout(function () {
        _finalizeRemoval(el);
    }, _durationFor(el));

    // Watch for cancellation: if the dj-remove attribute is stripped
    // while the removal is pending, cancel and keep the element mounted.
    if (typeof MutationObserver !== 'undefined') {
        const observer = new MutationObserver(function (mutations) {
            for (const m of mutations) {
                if (m.type === 'attributes' && m.attributeName === 'dj-remove') {
                    if (!el.hasAttribute('dj-remove')) {
                        _cancelRemoval(el);
                        return;
                    }
                }
            }
        });
        observer.observe(el, { attributes: true, attributeFilter: ['dj-remove'] });
        state.observer = observer;
    }

    // Phase 2 + 3 — schedule on the next frame so the browser commits
    // the phase-1 layout before the transition classes land.
    const _raf = globalThis.requestAnimationFrame || function (cb) { return setTimeout(cb, 16); };
    _raf(function () {
        if (!_pendingRemovals.has(el)) return;  // Cancelled already.
        if (spec.single) {
            el.classList.add(spec.single);
        } else {
            el.classList.remove(spec.start);
            el.classList.add(spec.active);
            el.classList.add(spec.end);
        }
    });
}

function maybeDeferRemoval(el) {
    // Entry point for 12-vdom-patch.js. Returns TRUE if removal was
    // deferred (caller should SKIP its normal removeChild/remove call);
    // FALSE otherwise (caller continues its normal removal path).
    if (!el || el.nodeType !== 1) return false;
    if (!el.hasAttribute || !el.hasAttribute('dj-remove')) return false;
    // An element with no parent cannot be "deferred from removal" — it's
    // already removed. Let the caller handle its own state.
    if (!el.parentNode) return false;
    if (_pendingRemovals.has(el)) return true;  // Already deferring.
    const spec = _parseRemoveSpec(el.getAttribute('dj-remove'));
    if (!spec) return false;
    _runRemove(el, spec);
    return true;
}

// Export on the global namespace for the patch-applier hook to use,
// and for tests to reach into the internals.
globalThis.djust = globalThis.djust || {};
globalThis.djust.maybeDeferRemoval = maybeDeferRemoval;
globalThis.djust.djRemove = {
    _parseRemoveSpec,
    _durationFor,
    _runRemove,
    _finalizeRemoval,
    _cancelRemoval,
    _pendingRemovals,
    _REMOVE_FALLBACK_MS,
    maybeDeferRemoval,
};
