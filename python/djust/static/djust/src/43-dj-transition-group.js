
// dj-transition-group — declarative enter/leave animation orchestration for
// lists of children (v0.6.0, phase 2c).
//
// React `<TransitionGroup>` / Vue `<transition-group>` parity. Authors mark
// a parent container and specify an enter spec and a leave spec once; djust
// wires those specs onto each child automatically so that new children
// animate in (via the existing `dj-transition` runner) and removed children
// animate out (via the existing `dj-remove` runner). This module does NOT
// re-implement the phase-1/2/3 class-cycling machinery or the pending-removal
// deferral — it orchestrates the two primitives by setting their attributes
// on children.
//
// Usage — long form (preferred):
//   <ul dj-transition-group
//       dj-group-enter="opacity-0 transition-opacity-300 opacity-100"
//       dj-group-leave="opacity-100 transition-opacity-300 opacity-0">
//     {% for t in toasts %}<li>{{ t.text }}</li>{% endfor %}
//   </ul>
//
// Usage — short form (one attribute, pipe-separated):
//   <ul dj-transition-group="fade-in | fade-out">...</ul>
//   <ul dj-transition-group="opacity-0 t-opacity-300 opacity-100 | opacity-100 t-opacity-300 opacity-0">...</ul>
//
// Each half accepts the same shapes as dj-transition / dj-remove:
//   three-token: "start active end" (phase-cycling CSS transition)
//   one-token:   "fade-out" (single-class, transitionend-driven)
//
// Initial children — by default, only the leave spec is copied onto each
// existing child at mount (so an exit animation plays if they're later
// removed, but nothing animates in on first paint). Opt in to initial
// enter animation with the `dj-group-appear` attribute:
//   <ul dj-transition-group dj-group-appear ...>...</ul>
//
// Never overwrites author-specified `dj-transition` or `dj-remove` on a
// child — an escape hatch for per-item overrides.
//
// Implementation note — MutationObserver fires AFTER the DOM mutation, so
// we cannot defer a removal from the parent observer (the child is already
// detached by the time we hear about it). Removal deferral relies entirely
// on `dj-remove` being present on the child BEFORE the removal patch runs.
// That is why this module sets `dj-remove` at ADD time, not at REMOVE time.

const _ENTER_ATTR = 'dj-transition';
const _LEAVE_ATTR = 'dj-remove';
const _GROUP_ATTR = 'dj-transition-group';
const _GROUP_ENTER_ATTR = 'dj-group-enter';
const _GROUP_LEAVE_ATTR = 'dj-group-leave';
const _APPEAR_ATTR = 'dj-group-appear';

const _installedParents = new WeakMap();
let _rootObserverInstalled = false;

function _parseGroupAttr(raw) {
    if (raw === null || raw === undefined) return null;
    const s = String(raw).trim();
    if (s === '') return null;
    if (s.indexOf('|') === -1) return null;
    const parts = s.split('|').map(function (p) { return p.trim(); });
    if (parts.length !== 2) return null;
    if (!parts[0] || !parts[1]) return null;
    return { enter: parts[0], leave: parts[1] };
}

function _resolveSpecs(parent) {
    const longEnter = parent.getAttribute(_GROUP_ENTER_ATTR);
    const longLeave = parent.getAttribute(_GROUP_LEAVE_ATTR);
    const short = _parseGroupAttr(parent.getAttribute(_GROUP_ATTR));
    return {
        enter: longEnter || (short && short.enter) || null,
        leave: longLeave || (short && short.leave) || null,
    };
}

function _handleChildAdded(child, parent, opts) {
    if (!child || child.nodeType !== 1) return;
    const specs = _resolveSpecs(parent);
    if (specs.leave && !child.hasAttribute(_LEAVE_ATTR)) {
        child.setAttribute(_LEAVE_ATTR, specs.leave);
    }
    if (opts && opts.applyEnter && specs.enter && !child.hasAttribute(_ENTER_ATTR)) {
        child.setAttribute(_ENTER_ATTR, specs.enter);
        // dj-transition's document-level observer picks up the attribute
        // mutation and runs the phase sequence.
    }
}

function _install(parent) {
    if (!parent || parent.nodeType !== 1) return;
    if (_installedParents.has(parent)) return;
    const appear = parent.hasAttribute(_APPEAR_ATTR);
    const initialChildren = Array.prototype.slice.call(parent.children);
    initialChildren.forEach(function (child) {
        _handleChildAdded(child, parent, { applyEnter: appear });
    });
    if (typeof MutationObserver === 'undefined') {
        _installedParents.set(parent, { observer: null });
        return;
    }
    const observer = new MutationObserver(function (mutations) {
        mutations.forEach(function (m) {
            if (m.type !== 'childList') return;
            m.addedNodes.forEach(function (node) {
                _handleChildAdded(node, parent, { applyEnter: true });
            });
        });
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
    document.querySelectorAll('[' + _GROUP_ATTR + ']').forEach(_install);
}

function _installRootObserver() {
    if (_rootObserverInstalled) return;
    _rootObserverInstalled = true;
    _rescan();
    const rootObserver = new MutationObserver(function (mutations) {
        mutations.forEach(function (m) {
            if (m.type === 'attributes' && m.attributeName === _GROUP_ATTR) {
                if (m.target.hasAttribute(_GROUP_ATTR)) {
                    _install(m.target);
                }
            } else if (m.type === 'childList') {
                m.addedNodes.forEach(function (node) {
                    if (node.nodeType !== 1) return;
                    if (node.hasAttribute && node.hasAttribute(_GROUP_ATTR)) {
                        _install(node);
                    }
                    if (node.querySelectorAll) {
                        node.querySelectorAll('[' + _GROUP_ATTR + ']').forEach(_install);
                    }
                });
                m.removedNodes.forEach(function (node) {
                    if (node.nodeType !== 1) return;
                    if (node.hasAttribute && node.hasAttribute(_GROUP_ATTR)) {
                        _uninstall(node);
                    }
                    if (node.querySelectorAll) {
                        node.querySelectorAll('[' + _GROUP_ATTR + ']').forEach(_uninstall);
                    }
                });
            }
        });
    });
    rootObserver.observe(document.documentElement, {
        attributes: true,
        attributeFilter: [_GROUP_ATTR],
        subtree: true,
        childList: true,
    });
}

if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _installRootObserver);
    } else {
        _installRootObserver();
    }
}

globalThis.djust = globalThis.djust || {};
globalThis.djust.djTransitionGroup = {
    _install: _install,
    _uninstall: _uninstall,
    _rescan: _rescan,
    _handleChildAdded: _handleChildAdded,
    _parseGroupAttr: _parseGroupAttr,
    _resolveSpecs: _resolveSpecs,
    _installedParents: _installedParents,
};
