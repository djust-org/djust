
// dj-transition — declarative CSS enter/leave transitions (v0.6.0)
//
// Phoenix JS.transition parity. Orchestrates a three-phase CSS class
// application (start → active → end) so template authors can drive
// CSS transitions without writing a dj-hook.
//
// Usage:
//   <div dj-transition="opacity-0 transition-opacity-300 opacity-100">
//     Fades in from 0 to 100 opacity over 300 ms.
//   </div>
//
// The attribute value is three space-separated tokens — start, active,
// end — each a single class name. Commas, parens, or other separators
// are NOT supported: `classList.add` would throw InvalidCharacterError
// on the resulting tokens. (A future enhancement could accept
// parenthesised multi-class groups; one-class-per-phase is the common
// case and keeps the parsing trivial.)
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
// has completed). A 600 ms fallback timeout cleans up phase-2 classes if
// `transitionend` never fires (e.g. zero-duration transitions or
// display: none).

const _djTransitionState = new WeakMap();
const _FALLBACK_MS = 600;

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
    if (parts.length < 3) return null;
    return { start: parts[0], active: parts[1], end: parts[2] };
}

function _runTransition(el, spec) {
    // Cancel any previous sequence on the same element.
    const prev = _djTransitionState.get(el);
    if (prev && prev.fallback) clearTimeout(prev.fallback);
    if (prev && prev.onEnd) el.removeEventListener('transitionend', prev.onEnd);

    // Phase 1 — start state.
    el.classList.add(spec.start);

    const state = {};
    _djTransitionState.set(el, state);

    // Phase 2 + 3 — schedule on the next frame so the browser commits
    // the phase-1 layout before the transition classes land.
    const _raf = globalThis.requestAnimationFrame || function (cb) { return setTimeout(cb, 16); };
    _raf(function () {
        el.classList.remove(spec.start);
        el.classList.add(spec.active);
        el.classList.add(spec.end);

        function cleanup() {
            // Guard against detached elements — if the node has been
            // removed from the DOM before this fires (typically the 600 ms
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
            // transitions from children.
            if (ev.target !== el) return;
            cleanup();
        }
        state.onEnd = onEnd;
        el.addEventListener('transitionend', onEnd);

        // Fallback in case transitionend never fires.
        state.fallback = setTimeout(cleanup, _FALLBACK_MS);
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
