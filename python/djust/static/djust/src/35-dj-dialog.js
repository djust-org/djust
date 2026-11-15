
// dj-dialog — native <dialog> modal integration (v0.5.1 P2)
//
// Usage:
//   <dialog id="settings" dj-dialog="open">...</dialog>
//
// When the attribute value changes from close → open, showModal() is called
// (which adds backdrop, focus-trap, and Escape handling — all browser-native).
// When it changes from open → close, close() is called.
//
// Reverse sync (closes #1267):
//   <dialog id="settings"
//           dj-dialog="open"
//           dj-dialog-close-event="close_settings">
//     ...
//   </dialog>
//
// When the user closes the dialog client-side (Escape, backdrop click, or
// dialog.close() from JS), djust dispatches the configured event name to
// the server so server state stays in sync (e.g., flip
// ``self.show_settings`` to False). Without this, the dialog closes
// locally but the server still thinks it's open — re-opening from the
// server is a no-op because the morph re-asserting ``dj-dialog="open"``
// doesn't change the attribute value.
//
// No close control (#4134):
//   <dialog dj-dialog="open" dj-dialog-no-close>...</dialog>
//
// {% modal native=True close_event=None %} has no close event to send, so the
// browser must not close it behind the server's back: Escape (`cancel`) is
// prevented, a backdrop click does nothing, and a `close` that still gets
// through (Chrome can let a repeated Escape close it) re-opens it while
// dj-dialog is still "open". Only the server closes it, by setting
// dj-dialog="close".
//
// Leverages the HTML <dialog> element's own modal behavior so djust doesn't
// re-implement focus management. A MutationObserver watches every <dialog>
// on the page; VDOM morphs that swap the dj-dialog value fire the right
// showModal/close call automatically.

// Tracks which dialog elements have had a `close` listener installed so we
// don't double-bind on subsequent attribute changes. WeakMap so detached
// dialogs are GC'd.
const _dialogsWithCloseListener = new WeakMap();

function _installCloseListenerOnce(el) {
    if (_dialogsWithCloseListener.has(el)) return;
    _dialogsWithCloseListener.set(el, true);
    el.addEventListener('cancel', function (e) {
        // Read at fire time: a morph may add or drop the marker (#4134).
        if (el.hasAttribute('dj-dialog-no-close')) e.preventDefault();
    });
    el.addEventListener('close', function () {
        // Read at fire time so morph attribute updates take effect.
        const eventName = el.getAttribute('dj-dialog-close-event');
        if (!eventName) {
            _reopenIfStillOpen(el);
            return;
        }
        // #1706: read the published alias, NOT the bare `handleEvent` symbol.
        // `handleEvent` is declared in 11-event-handler.js, inside the
        // double-load-guard `else {}` block (block-scoped); this module runs
        // at bundle top level, OUTSIDE that block, so the bare reference is
        // out of scope even unminified (the `typeof` guard silently returns
        // "undefined" and the close-event never fires) and throws
        // ReferenceError under terser-minified bundles. Reading
        // `globalThis.djust.handleEvent` is minification-independent. Same
        // class as #1676 / #1688. Pass the dialog element as the trigger for
        // loading-state and activity-gate machinery.
        const _handleEvent = globalThis.djust && globalThis.djust.handleEvent;
        if (typeof _handleEvent === 'function') {
            _handleEvent(eventName, { _targetElement: el });
        }
    });
}

// A dialog without a close control that closed anyway, while the server still
// says it is open: show it again, since nothing told the server (#4134).
function _reopenIfStillOpen(el) {
    if (!el.hasAttribute('dj-dialog-no-close') || !el.isConnected || el.open) return;
    const state = (el.getAttribute('dj-dialog') || '').trim().toLowerCase();
    if (state !== 'open') return;
    try { el.showModal(); }
    catch (_e) { el.setAttribute('open', ''); }
}

function _syncDialogState(el) {
    if (!(el instanceof HTMLDialogElement)) return;
    // Install native `close` listener on first encounter (idempotent).
    // Closes #1267.
    _installCloseListenerOnce(el);
    const state = (el.getAttribute('dj-dialog') || '').trim().toLowerCase();
    if (state === 'open') {
        if (!el.open) {
            try { el.showModal(); }
            catch (_e) {
                // Some browsers throw if the element is already modal or
                // in an inconsistent DOM state — fall back to the boolean
                // open attribute so the dialog is at least visible.
                el.setAttribute('open', '');
            }
        }
    } else if (state === 'close' || state === 'closed') {
        if (el.open) el.close();
    }
}

function _syncAllDialogs(root) {
    const scope = root || document;
    const dialogs = scope.querySelectorAll('dialog[dj-dialog]');
    dialogs.forEach(_syncDialogState);
}

function _installDjDialogObserver() {
    // Initial pass — handle dialogs rendered at page load.
    _syncAllDialogs();

    // Watch for attribute changes on any <dialog> in the tree. Single
    // document-level observer rather than per-element listeners so VDOM
    // morphs that swap dj-dialog pick it up without re-registration.
    const observer = new MutationObserver(function (mutations) {
        mutations.forEach(function (m) {
            if (m.type === 'attributes' && m.attributeName === 'dj-dialog') {
                if (m.target instanceof HTMLDialogElement) {
                    _syncDialogState(m.target);
                }
            } else if (m.type === 'childList') {
                m.addedNodes.forEach(function (node) {
                    if (node.nodeType === 1) {
                        if (node instanceof HTMLDialogElement && node.hasAttribute('dj-dialog')) {
                            _syncDialogState(node);
                        } else if (node.querySelectorAll) {
                            _syncAllDialogs(node);
                        }
                    }
                });
            }
        });
    });
    observer.observe(document.documentElement, {
        attributes: true,
        attributeFilter: ['dj-dialog'],
        subtree: true,
        childList: true,
    });
}

if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _installDjDialogObserver);
    } else {
        _installDjDialogObserver();
    }
}

// {% modal native=True %} has no scrim element: a click on the backdrop lands
// on the <dialog> itself (its content fills it), and closes it like the
// <div> modal's scrim. close() fires `close`, which reports to the server.
if (typeof document !== 'undefined') {
    document.addEventListener('click', function (e) {
        const el = e.target;
        if (el instanceof HTMLDialogElement && el.open &&
            el.classList.contains('dj-modal--native') &&
            !el.hasAttribute('dj-dialog-no-close')) {
            el.close();
        }
    });
}

globalThis.djust = globalThis.djust || {};
globalThis.djust.djDialog = {
    _syncDialogState,
    _syncAllDialogs,
    _installCloseListenerOnce,
    _reopenIfStillOpen,
};
