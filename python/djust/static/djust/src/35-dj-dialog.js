
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
    el.addEventListener('close', function () {
        // Read at fire time so morph attribute updates take effect.
        const eventName = el.getAttribute('dj-dialog-close-event');
        if (!eventName) return;
        // handleEvent is defined globally by 11-event-handler.js. Pass
        // the dialog element as the trigger for loading-state and
        // activity-gate machinery.
        if (typeof handleEvent === 'function') {
            handleEvent(eventName, { _targetElement: el });
        }
    });
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

globalThis.djust = globalThis.djust || {};
globalThis.djust.djDialog = {
    _syncDialogState,
    _syncAllDialogs,
    _installCloseListenerOnce,
};
