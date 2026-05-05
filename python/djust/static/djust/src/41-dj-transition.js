
// dj-transition — declarative CSS enter/leave transitions (v0.6.0)
//
// Phoenix JS.transition parity. Orchestrates a three-phase CSS class
// application (start → active → end) so template authors can drive
// CSS transitions without writing a dj-hook.
//
// Usage — three-token form (preferred for explicit phase control):
//   <div dj-transition="opacity-0 transition-opacity-300 opacity-100">
//     Fades in from 0 to 100 opacity over 300 ms.
//   </div>
//
// Usage — single-token short form (matches dj-remove's short form):
//   <div dj-transition="fade-in">
//     Applies the "fade-in" class on the next frame and waits for
//     transitionend. Useful for simple keyframe-driven transitions
//     where one class drives the animation.
//   </div>
//
// The three-token form is "start active end" — each a single class
// name. Commas, parens, or other separators are NOT supported:
// `classList.add` would throw InvalidCharacterError on the resulting
// tokens. (A future enhancement could accept parenthesised multi-class
// groups; one-class-per-phase is the common case and keeps the
// parsing trivial.) Two-token form is rejected as ambiguous (matches
// dj-remove). Closes #1273 for the `dj-transition-group` short-form
// docs claim that depended on this 1-token form working.
//
// Re-trigger from JS: calling `el.setAttribute('dj-transition', spec)`
// re-runs the sequence, even when `spec` is identical to the current
// value — MutationObserver fires on any attribute set, not only value
// changes.
//
// Lifecycle:
//   Phase 1 (start):  applied synchronously when the attribute appears
//                     or changes. Sets the pre-transition state.
//   Phase 2 (active): applied on the next animation frame — the
//                     transition begins. Adding the `transition-*`
//                     classes here ensures the browser sees the start
//                     state first.
//   Phase 3 (end):    applied on the same frame as phase 2 — the
//                     final target state. Kept on the element after
//                     the transition completes.
//
// On `transitionend`, phase-2 classes are removed (they typically
// carry the `transition-*` helper and are not needed once the animation
// has completed). A computed fallback timeout cleans up phase-2 classes
// if `transitionend` never fires (e.g. zero-duration transitions or
// display: none).
//
// The fallback duration is auto-derived from the element's computed
// `transition-duration` + `transition-delay` (longest pair across all
// transitioning properties) plus a 50ms grace window. For multi-property
// transitions, we count expected `transitionend` events from
// `transition-property` and only run cleanup after all have fired —
// otherwise the first-finishing property would cut off slower ones.
//
// `_FALLBACK_MS_DEFAULT` is the fallback used only when computed-style
// reading fails or yields zero (e.g. element has no transition rule yet).

const _djTransitionState = new WeakMap();
const _FALLBACK_MS_DEFAULT = 600;

function _parseTimeMs(s) {
    // CSS time tokens: "550ms", "0.55s", "0s". Returns 0 on parse failure.
    const t = (s || '').trim();
    if (!t) return 0;
    if (t.endsWith('ms')) return parseFloat(t) || 0;
    if (t.endsWith('s')) return (parseFloat(t) || 0) * 1000;
    return 0;
}

function _computeTransitionTiming(el) {
    // Inspect `transition-property`, `transition-duration`, `transition-delay`
    // and return {maxMs, propsCount}. CSS spec: when *-duration / *-delay
    // have fewer comma-separated values than -property, they cycle. When
    // they have more, extras are ignored.
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

function _parseSpec(raw) {
    const input = (raw || '').trim();
    // Reject comma, paren, or bracket separators. `classList.add` throws
    // InvalidCharacterError on tokens containing these characters — catch
    // malformed specs up front and log in debug mode instead of letting
    // the error surface at runtime.
    if (/[,()[\]]/.test(input)) {
        if (globalThis.djustDebug) {
            console.warn('[djust] dj-transition: commas, parens, and brackets are not supported in spec:', raw);
        }
        return null;
    }
    const parts = input.split(/\s+/).filter(Boolean);
    if (parts.length === 0) return null;
    // 1-token form: apply one class on the next frame, wait for
    // transitionend. Mirrors dj-remove's 1-token shape so dj-transition-group
    // short-form (e.g. `dj-transition-group="fade-in | fade-out"`) works
    // as documented at 43-dj-transition-group.js:22-23. Closes #1273.
    if (parts.length === 1) return { single: parts[0] };
    // 2-token: ambiguous — could be (start, active) or (active, end).
    // Reject up-front (matches dj-remove's behavior at 42-dj-remove.js:55).
    if (parts.length === 2) {
        if (globalThis.djustDebug) {
            console.warn('[djust] dj-transition: 2-token spec is invalid, use 1 or 3 tokens:', raw);
        }
        return null;
    }
    return { start: parts[0], active: parts[1], end: parts[2] };
}

function _runTransition(el, spec) {
    // Cancel any previous sequence on the same element.
    const prev = _djTransitionState.get(el);
    if (prev && prev.fallback) clearTimeout(prev.fallback);
    if (prev && prev.onEnd) el.removeEventListener('transitionend', prev.onEnd);

    const _raf = globalThis.requestAnimationFrame || function (cb) { return setTimeout(cb, 16); };
    const state = {};
    _djTransitionState.set(el, state);

    // 1-token short form: apply the single class on the next frame and
    // wait for transitionend. No phase-cycling cleanup — the class stays
    // on the element after the transition (the author can remove it
    // separately via VDOM patch if desired). Closes #1273.
    if (spec.single) {
        _raf(function () {
            el.classList.add(spec.single);

            // Compute timing AFTER the class is applied so the new
            // transition rule is reflected in getComputedStyle.
            const timing = _computeTransitionTiming(el);
            const fallbackMs = timing.maxMs > 0 ? timing.maxMs + 50 : _FALLBACK_MS_DEFAULT;
            let remainingEvents = timing.propsCount || 1;

            function cleanup() {
                if (!el.isConnected) {
                    _djTransitionState.delete(el);
                    return;
                }
                if (state.fallback) clearTimeout(state.fallback);
                el.removeEventListener('transitionend', onEnd);
                _djTransitionState.delete(el);
            }

            function onEnd(ev) {
                if (ev.target !== el) return;
                remainingEvents--;
                if (remainingEvents <= 0) cleanup();
            }
            state.onEnd = onEnd;
            el.addEventListener('transitionend', onEnd);
            state.fallback = setTimeout(cleanup, fallbackMs);
        });
        return;
    }

    // Phase 1 — start state.
    el.classList.add(spec.start);

    // Phase 2 + 3 — schedule on the next frame so the browser commits
    // the phase-1 layout before the transition classes land.
    _raf(function () {
        el.classList.remove(spec.start);
        el.classList.add(spec.active);
        el.classList.add(spec.end);

        // Compute timing AFTER active+end land so getComputedStyle picks
        // up the transition rule from the active class.
        const timing = _computeTransitionTiming(el);
        const fallbackMs = timing.maxMs > 0 ? timing.maxMs + 50 : _FALLBACK_MS_DEFAULT;
        let remainingEvents = timing.propsCount || 1;

        function cleanup() {
            // Guard against detached elements — if the node has been
            // removed from the DOM before this fires (typically the
            // fallback path), skip classList/listener work. classList on
            // a detached node is technically safe but any parentNode
            // access downstream would NPE.
            if (!el.isConnected) {
                _djTransitionState.delete(el);
                return;
            }
            el.classList.remove(spec.active);
            if (state.fallback) clearTimeout(state.fallback);
            el.removeEventListener('transitionend', onEnd);
            _djTransitionState.delete(el);
        }

        function onEnd(ev) {
            // Only react to transitions on THIS element, not bubbled
            // transitions from children. Decrement the expected event
            // count and only cleanup once all properties have finished —
            // otherwise the fastest property would cut off slower ones.
            if (ev.target !== el) return;
            remainingEvents--;
            if (remainingEvents <= 0) cleanup();
        }
        state.onEnd = onEnd;
        el.addEventListener('transitionend', onEnd);

        // Fallback in case transitionend never fires (zero-duration
        // transitions or detached/hidden elements). Sized from the
        // computed transition duration + 50ms grace.
        state.fallback = setTimeout(cleanup, fallbackMs);
    });
}

function _installDjTransitionFor(el) {
    const raw = el.getAttribute('dj-transition');
    const spec = _parseSpec(raw);
    if (!spec) return;
    _runTransition(el, spec);
}

function _installDjTransitionObserver() {
    document.querySelectorAll('[dj-transition]').forEach(_installDjTransitionFor);

    const rootObserver = new MutationObserver(function (mutations) {
        mutations.forEach(function (m) {
            if (m.type === 'attributes' && m.attributeName === 'dj-transition') {
                // Attribute changed (including re-asserted with same value) —
                // re-run the sequence so authors can retrigger from JS.
                _installDjTransitionFor(m.target);
            } else if (m.type === 'childList') {
                m.addedNodes.forEach(function (node) {
                    if (node.nodeType !== 1) return;
                    if (node.hasAttribute && node.hasAttribute('dj-transition')) {
                        _installDjTransitionFor(node);
                    }
                    if (node.querySelectorAll) {
                        node.querySelectorAll('[dj-transition]').forEach(_installDjTransitionFor);
                    }
                });
            }
        });
    });
    rootObserver.observe(document.documentElement, {
        attributes: true,
        attributeFilter: ['dj-transition'],
        subtree: true,
        childList: true,
    });
}

if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _installDjTransitionObserver);
    } else {
        _installDjTransitionObserver();
    }
}

globalThis.djust = globalThis.djust || {};
globalThis.djust.djTransition = {
    _parseSpec,
    _runTransition,
    _installDjTransitionFor,
};
