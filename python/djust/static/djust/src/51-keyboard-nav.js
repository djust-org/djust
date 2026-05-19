// ============================================================================
// keyboard-nav — keyboard interaction for djust-native components (#1522)
// ============================================================================
//
// Adds W3C ARIA Authoring-Practices keyboard behavior to the four
// djust-native templatetag components (the `dj-*` class family emitted by
// python/djust/components/templatetags/djust_components.py):
//
//   - Modal / dialog  — focus trap (Tab / Shift+Tab wrap) + Esc-to-close.
//   - Tablist         — ArrowLeft/Right roving tabindex + Home/End.
//   - Accordion       — ArrowUp/Down focus movement + Home/End.
//   - Dropdown menu   — ArrowUp/Down roving + Home/End + Esc-to-close.
//
// CSP-strict (Action #183): one delegated `keydown` listener on `document`
// — no inline <script>, no inline handlers. Delegation survives morphdom
// re-renders for free (the listener stays on `document`). A single
// document-level MutationObserver handles the one thing delegation cannot:
// moving focus into a modal when it appears, and restoring focus to the
// previously-focused element when it is removed.
//
// The Bootstrap-flavoured `_simple.py` component classes (data-bs-toggle
// markup) are intentionally OUT OF SCOPE — those are Bootstrap-JS driven.

(function () {
    // Selector for keyboard-reachable controls, in DOM order.
    const FOCUSABLE_SELECTOR = [
        'a[href]',
        'button:not([disabled])',
        'input:not([disabled])',
        'select:not([disabled])',
        'textarea:not([disabled])',
        '[tabindex]:not([tabindex="-1"])',
    ].join(',');

    // Stack of currently-open modal dialogs (top = most recently opened).
    // Each entry: { el: <dialog element>, returnFocus: <element|null> }.
    const _dialogStack = [];

    // -----------------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------------

    function _isDialog(el) {
        if (!el || el.nodeType !== 1) return false;
        return el.getAttribute('role') === 'dialog' ||
            (el.classList && el.classList.contains('dj-modal'));
    }

    // All keyboard-reachable descendants of `container`, in DOM order.
    // jsdom has no layout engine, so we deliberately do NOT filter on
    // `offsetParent` / computed visibility — that would be unreliable in
    // tests and is a best-effort filter at most in real browsers.
    function _focusable(container) {
        return Array.prototype.slice.call(
            container.querySelectorAll(FOCUSABLE_SELECTOR)
        );
    }

    // Dispatch a server event by reading the event name off an element's
    // `dj-click` attribute. handleEvent is defined globally by
    // 11-event-handler.js (same usage as 35-dj-dialog.js).
    function _dispatchFrom(el) {
        if (!el) return false;
        const name = el.getAttribute('dj-click');
        if (!name) return false;
        if (typeof handleEvent === 'function') {
            handleEvent(name, { _targetElement: el });
            return true;
        }
        return false;
    }

    // The currently top-most open dialog, or null.
    function _topDialog() {
        return _dialogStack.length
            ? _dialogStack[_dialogStack.length - 1].el
            : null;
    }

    // Wrap-around index arithmetic. Covers index 0, mid, len-1 and the
    // out-of-range cases at both ends (CLAUDE.md #1199).
    function _wrapIndex(i, len) {
        if (len <= 0) return 0;
        return ((i % len) + len) % len;
    }

    // First / last element of a list.
    function _firstOf(list) {
        return list.length ? list[0] : null;
    }
    function _lastOf(list) {
        return list.length ? list[list.length - 1] : null;
    }

    // Element at wrapped index `i` of `list`.
    function _at(list, i) {
        return list[_wrapIndex(i, list.length)];
    }

    // -----------------------------------------------------------------------
    // Focus trap — modal / role="dialog"
    // -----------------------------------------------------------------------

    function _trapFocus(dialog, e) {
        const focusables = _focusable(dialog);
        if (focusables.length === 0) {
            // No focusable children: trap focus on the container itself.
            e.preventDefault();
            if (dialog.getAttribute('tabindex') === null) {
                dialog.setAttribute('tabindex', '-1');
            }
            dialog.focus();
            return;
        }
        const first = _firstOf(focusables);
        const last = _lastOf(focusables);
        const active = dialog.ownerDocument.activeElement;
        if (e.shiftKey && active === first) {
            e.preventDefault();
            last.focus();
        } else if (!e.shiftKey && active === last) {
            e.preventDefault();
            first.focus();
        }
        // Mid-list Tab: let the browser advance naturally.
    }

    // Esc inside a modal — dispatch the modal's configured close event so
    // server state stays in sync (mirrors 35-dj-dialog.js reverse-sync).
    function _closeModal(dialog) {
        const closer = dialog.querySelector('.dj-modal__close[dj-click]') ||
            dialog.querySelector('[dj-click]');
        return _dispatchFrom(closer);
    }

    // -----------------------------------------------------------------------
    // Roving — generic next/prev focus movement over a list of elements
    // -----------------------------------------------------------------------

    // Move focus among `elements` based on the pressed key. `rove` controls
    // whether tabindex is juggled (tablist) or focus is moved only
    // (accordion / dropdown menu). Returns true if the key was handled.
    function _moveFocus(elements, current, key, forwardKeys, backKeys, rove) {
        if (elements.length === 0) return false;
        const idx = elements.indexOf(current);
        let target = null;
        if (forwardKeys.indexOf(key) !== -1) {
            target = _at(elements, (idx < 0 ? -1 : idx) + 1);
        } else if (backKeys.indexOf(key) !== -1) {
            target = _at(elements, (idx < 0 ? 0 : idx) - 1);
        } else if (key === 'Home') {
            target = _firstOf(elements);
        } else if (key === 'End') {
            target = _lastOf(elements);
        }
        if (!target) return false;
        if (rove) {
            // Exactly one element in the tab order at a time.
            elements.forEach(function (el) {
                el.setAttribute('tabindex', el === target ? '0' : '-1');
            });
        }
        target.focus();
        return true;
    }

    // -----------------------------------------------------------------------
    // Per-pattern handlers
    // -----------------------------------------------------------------------

    function _handleTablist(tablist, target, e) {
        const tabs = Array.prototype.slice.call(
            tablist.querySelectorAll('[role="tab"]')
        );
        const current = target.closest('[role="tab"]');
        if (!current) return false;
        if (_moveFocus(tabs, current, e.key,
            ['ArrowRight', 'ArrowDown'], ['ArrowLeft', 'ArrowUp'], true)) {
            e.preventDefault();
            return true;
        }
        return false;
    }

    function _handleAccordion(accordion, target, e) {
        const triggers = Array.prototype.slice.call(
            accordion.querySelectorAll('.dj-accordion__trigger')
        );
        const current = target.closest('.dj-accordion__trigger');
        if (!current) return false;
        // Focus-movement only — accordion headers keep their native tab
        // order (no tabindex juggling, per W3C APG).
        if (_moveFocus(triggers, current, e.key,
            ['ArrowDown'], ['ArrowUp'], false)) {
            e.preventDefault();
            return true;
        }
        return false;
    }

    // True when a dropdown is in its open state. The dropdown templatetag
    // emits a bare `data-open` attribute when open
    // (djust_components.py:368/386); `hasAttribute` also matches the
    // `data-open="true"` form used by other component variants.
    function _dropdownOpen(dropdown) {
        return !!dropdown && dropdown.hasAttribute('data-open');
    }

    const _MENUITEM_SELECTOR =
        '[role="menuitem"], a[href], button:not([disabled])';

    function _handleDropdown(dropdown, target, e) {
        const menu = dropdown.querySelector('[role="menu"]');
        const trigger = dropdown.querySelector('.dj-dropdown__trigger');
        if (e.key === 'Escape') {
            e.preventDefault();
            _dispatchFrom(trigger);
            if (trigger) trigger.focus();
            return true;
        }
        if (!menu) return false;
        const menuitems = Array.prototype.slice.call(
            menu.querySelectorAll(_MENUITEM_SELECTOR)
        );
        if (menuitems.length === 0) return false;
        // First Arrow/Home/End from the trigger focuses an item.
        const current = target.closest('[role="menuitem"], a[href], button');
        if (!current || menuitems.indexOf(current) === -1) {
            if (e.key === 'ArrowDown' || e.key === 'ArrowUp' ||
                e.key === 'Home' || e.key === 'End') {
                e.preventDefault();
                const wantLast = e.key === 'ArrowUp' || e.key === 'End';
                const item = wantLast ? _lastOf(menuitems) : _firstOf(menuitems);
                item.focus();
                return true;
            }
            return false;
        }
        if (_moveFocus(menuitems, current, e.key,
            ['ArrowDown'], ['ArrowUp'], false)) {
            e.preventDefault();
            return true;
        }
        return false;
    }

    // -----------------------------------------------------------------------
    // Delegated keydown dispatcher
    // -----------------------------------------------------------------------

    function _handleKeydown(e) {
        const target = e.target;
        if (!target || typeof target.closest !== 'function') return;

        // 1. Modal / dialog — most specific. Focus trap + Esc-to-close.
        const dialog = target.closest('[role="dialog"], .dj-modal');
        if (dialog && _isDialog(dialog)) {
            // With nested dialogs the trap always acts on the TOP dialog.
            const top = _topDialog() || dialog;
            // A dropdown nested INSIDE this dialog still needs arrow roving
            // and Esc-to-close (#1533). `closest` matches a dropdown that is
            // a descendant of the dialog; `dialog.contains` makes the intent
            // explicit (and guards a dropdown outside the dialog subtree).
            const innerDropdown = target.closest('.dj-dropdown');
            const dropdownInDialog =
                innerDropdown && dialog.contains(innerDropdown);
            // Tab is handled FIRST and returned — the focus trap is always
            // dialog-scoped and must never fall through to the dropdown.
            if (e.key === 'Tab') {
                _trapFocus(top, e);
                return;
            }
            if (e.key === 'Escape') {
                // An open inner dropdown consumes Esc first (close the
                // dropdown, refocus its trigger); a closed/absent dropdown
                // lets Esc close the dialog as before.
                if (dropdownInDialog && _dropdownOpen(innerDropdown)) {
                    e.preventDefault();
                    _handleDropdown(innerDropdown, target, e);
                    return;
                }
                e.preventDefault();
                _closeModal(top);
                return;
            }
            // Arrow / Home / End — route to a nested dropdown if present.
            if (dropdownInDialog &&
                _handleDropdown(innerDropdown, target, e)) {
                return;
            }
            // The dialog still swallows non-dropdown arrow keys (unchanged).
            return;
        }

        // 2. Dropdown — Arrow roving + Esc.
        const dropdown = target.closest('.dj-dropdown');
        if (dropdown && _handleDropdown(dropdown, target, e)) return;

        // 3. Tablist — Arrow roving.
        const tablist = target.closest('[role="tablist"]');
        if (tablist && _handleTablist(tablist, target, e)) return;

        // 4. Accordion — Arrow focus movement.
        const accordion = target.closest('.dj-accordion');
        if (accordion && _handleAccordion(accordion, target, e)) return;
    }

    // -----------------------------------------------------------------------
    // Modal presence observer — focus-in on open, focus-restore on close
    // -----------------------------------------------------------------------

    function _onDialogAdded(dialog) {
        const tracked = _dialogStack.some(function (entry) {
            return entry.el === dialog;
        });
        if (tracked) return;
        const doc = dialog.ownerDocument;
        const returnFocus = doc.activeElement;
        _dialogStack.push({ el: dialog, returnFocus: returnFocus });
        const focusables = _focusable(dialog);
        if (focusables.length > 0) {
            _firstOf(focusables).focus();
        } else {
            if (dialog.getAttribute('tabindex') === null) {
                dialog.setAttribute('tabindex', '-1');
            }
            dialog.focus();
        }
    }

    function _onDialogRemoved(dialog) {
        let idx = -1;
        for (let i = _dialogStack.length - 1; i >= 0; i--) {
            // eslint-disable-next-line security/detect-object-injection
            if (_dialogStack[i].el === dialog) {
                idx = i;
                break;
            }
        }
        if (idx < 0) return;
        const entry = _dialogStack.splice(idx, 1)[0];
        if (entry.returnFocus &&
            typeof entry.returnFocus.focus === 'function' &&
            entry.returnFocus.isConnected) {
            entry.returnFocus.focus();
        } else if (document.body &&
                   typeof document.body.focus === 'function') {
            // The recorded return target was removed from the DOM while
            // the dialog was open (e.g. a morphdom patch replaced the
            // opener's region). Focusing a detached node is a silent
            // no-op, which would strand keyboard focus; fall back to the
            // document body so focus lands somewhere reachable (#1532).
            document.body.focus();
        }
    }

    function _scanForDialogs(node, onFound) {
        if (node.nodeType !== 1) return;
        if (_isDialog(node)) onFound(node);
        if (typeof node.querySelectorAll === 'function') {
            const nested = node.querySelectorAll('[role="dialog"], .dj-modal');
            nested.forEach(function (n) {
                if (_isDialog(n)) onFound(n);
            });
        }
    }

    function _installObserver() {
        const doc = document;
        // Initial pass — dialogs present at page load.
        const initial = doc.querySelectorAll('[role="dialog"], .dj-modal');
        initial.forEach(function (el) {
            if (_isDialog(el)) _onDialogAdded(el);
        });

        const observer = new MutationObserver(function (mutations) {
            mutations.forEach(function (m) {
                if (m.type !== 'childList') return;
                m.addedNodes.forEach(function (n) {
                    _scanForDialogs(n, _onDialogAdded);
                });
                m.removedNodes.forEach(function (n) {
                    _scanForDialogs(n, _onDialogRemoved);
                });
            });
        });
        observer.observe(doc.documentElement, {
            childList: true,
            subtree: true,
        });
    }

    function _init() {
        document.addEventListener('keydown', _handleKeydown, false);
        _installObserver();
    }

    if (typeof document !== 'undefined') {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', _init);
        } else {
            _init();
        }
    }

    // Small test surface (mirrors 35-dj-dialog.js).
    globalThis.djust = globalThis.djust || {};
    globalThis.djust.keyboardNav = {
        _handleKeydown: _handleKeydown,
        _focusable: _focusable,
        _wrapIndex: _wrapIndex,
        _dialogStack: _dialogStack,
    };
})();
