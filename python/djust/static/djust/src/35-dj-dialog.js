
// dj-dialog — native <dialog> modal integration (v0.5.1 P2)
//
// Usage:
//   <dialog id="settings" dj-dialog="open">...</dialog>
//
// When the attribute value changes from close → open, showModal() is called
// (which adds backdrop, focus-trap, and Escape handling — all browser-native).
// When it changes from open → close, close() is called.
//
// Leverages the HTML <dialog> element's own modal behavior so djust doesn't
// re-implement focus management. A MutationObserver watches every <dialog>
// on the page; VDOM morphs that swap the dj-dialog value fire the right
// showModal/close call automatically.

function _syncDialogState(el) {
    if (!(el instanceof HTMLDialogElement)) return;
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
};
