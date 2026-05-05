
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

function _parseTimeMs(s) {
    // CSS time tokens: "550ms", "0.55s", "0s". Returns 0 on parse failure.
    const t = (s || '').trim();
    if (!t) return 0;
    if (t.endsWith('ms')) return parseFloat(t) || 0;
    if (t.endsWith('s')) return (parseFloat(t) || 0) * 1000;
    return 0;
}

function _computeTransitionTiming(el) {
    // Returns {maxMs, propsCount} from the element's computed transition
    // styles. Same logic as 41-dj-transition.js — duplicated here rather
    // than shared because the source files are concatenated as separate
    // modules (no cross-file imports).
    const cs = (typeof getComputedStyle === 'function') ? getComputedStyle(el) : null;
    if (!cs) return { maxMs: 0, propsCount: 0 };
    const props = (cs.transitionProperty || '')
        .split(',').map(s => s.trim()).filter(s => s && s !== 'none');
    const durations = (cs.transitionDuration || '')
        .split(',').map(s => _parseTimeMs(s));
    const delays = (cs.transitionDelay || '')
        .split(',').map(s => _parseTimeMs(s));
    if (props.length === 0) return { maxMs: 0, propsCount: 0 };
    let maxMs = 0;
    for (let i = 0; i < props.length; i++) {
        const dur = durations[i % durations.length] || 0;
        const del = delays[i % delays.length] || 0;
        const total = dur + del;
        if (total > maxMs) maxMs = total;
    }
    return { maxMs: maxMs, propsCount: props.length };
}

function _parseRemoveSpec(raw) {
    if (raw === null || raw === undefined) return null;
    const parts = String(raw).trim().split(/\s+/).filter(Boolean);
    if (parts.length === 0) return null;
    if (parts.length === 1) return { single: parts[0] };
    if (parts.length >= 3) return { start: parts[0], active: parts[1], end: parts[2] };
    // Two tokens — ambiguous, treat as invalid (matches dj-transition).
    if (parts.length === 2) {
        if (globalThis.djustDebug) {
            console.warn('dj-remove: 2-token spec is invalid, use 1 or 3 tokens:', raw);
        }
        return null;
    }
    return null;
}

function _durationFor(el) {
    // Explicit dj-remove-duration attribute wins (clamped to 0–30s).
    const raw = el.getAttribute && el.getAttribute('dj-remove-duration');
    if (raw !== null && raw !== undefined && raw !== '') {
        const n = parseInt(raw, 10);
        if (Number.isFinite(n)) {
            if (n < 0) return 0;
            if (n > 30000) return 30000;
            return n;
        }
    }
    // No author override — read from computed style. Falls back to the
    // hardcoded default only if no transition is declared.
    const timing = _computeTransitionTiming(el);
    if (timing.maxMs > 0) return timing.maxMs + 50;
    return _REMOVE_FALLBACK_MS;
}

function _teardownState(el, state) {
    // Shared cleanup for both _finalizeRemoval and _cancelRemoval: clear
    // the fallback timer, remove the transitionend listener, disconnect
    // the MutationObserver, and drop the WeakMap entry. Safe to call
    // with a state object whose fields are partially populated (e.g.
    // before the raf callback has run).
    if (state.fallback) clearTimeout(state.fallback);
    if (state.onEnd) el.removeEventListener('transitionend', state.onEnd);
    if (state.observer) state.observer.disconnect();
    _pendingRemovals.delete(el);
}

function _finalizeRemoval(el) {
    // Called when the animation completes or the fallback fires. Clean up
    // all pending state and physically detach the element. Guard against
    // the element having been moved or already detached by another patch.
    const state = _pendingRemovals.get(el);
    if (!state) return;
    _teardownState(el, state);
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
    _teardownState(el, state);
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
}

function _runRemove(el, spec) {
    // Phase 1 — start state (three-token form only).
    if (!spec.single) {
        el.classList.add(spec.start);
    }

    const state = { spec: spec };
    _pendingRemovals.set(el, state);

    // Count expected transitionend events (one per transitioning property)
    // so the first-finishing property doesn't cut off slower ones. The
    // count is read AFTER the active class has had a chance to apply —
    // for the 3-token form, that's after the next frame; for the 1-token
    // form, the class is added synchronously in maybeDeferRemoval before
    // _runRemove is called.
    const timing = _computeTransitionTiming(el);
    let remainingEvents = timing.propsCount || 1;

    function onEnd(ev) {
        if (ev.target !== el) return;
        remainingEvents--;
        if (remainingEvents <= 0) _finalizeRemoval(el);
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
    _teardownState,
    _pendingRemovals,
    _REMOVE_FALLBACK_MS,
    maybeDeferRemoval,
};
