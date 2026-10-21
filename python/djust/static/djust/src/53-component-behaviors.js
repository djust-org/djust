// ============================================================================
// Component behaviours: delegated, CSP-safe
// ============================================================================
//
// The djust.components tags render markup only. The interactive parts that
// used to be inline event handlers (onclick=, onkeydown=, oninput=, ...) live
// here as listeners on `document`, so they work under a Content-Security-
// Policy without 'unsafe-inline' and keep working across VDOM morphs without
// re-binding:
//
//   .rich-select      open/close on click, Enter/Space and Escape; close on an
//                     option pick or a click elsewhere; search-box filtering.
//   .combobox         keep focus in the input while an option is pressed,
//                     close the list after the pick, reflect aria-expanded.
//   .dj-theme-toggle  switch the colour mode (`data-theme-mode`) when
//                     djust.theming's theme.js is not on the page, and keep
//                     every toggle's aria-checked in step with the mode.
// ============================================================================

(function initComponentBehaviors() {
    if (typeof document === 'undefined' || document._djustComponentBehaviors) return;
    document._djustComponentBehaviors = true;

    const THEME_MODE_KEY = 'djust-theme-mode';
    const THEME_MODES = ['light', 'dark', 'system'];

    function _closest(target, selector) {
        return target && target.closest ? target.closest(selector) : null;
    }

    function _setRichSelectOpen(root, open) {
        root.classList.toggle('rich-select--open', open);
        const trigger = root.querySelector('.rich-select-trigger');
        if (trigger) trigger.setAttribute('aria-expanded', open ? 'true' : 'false');
    }

    function _closeRichSelects(except) {
        document.querySelectorAll('.rich-select--open').forEach(function (el) {
            if (el !== except) _setRichSelectOpen(el, false);
        });
    }

    function _syncThemeToggles(mode) {
        document.querySelectorAll('.dj-theme-toggle').forEach(function (toggle) {
            toggle.setAttribute('data-current', mode);
            toggle.querySelectorAll('[data-theme-mode]').forEach(function (btn) {
                const on = btn.getAttribute('data-theme-mode') === mode;
                btn.setAttribute('aria-checked', on ? 'true' : 'false');
                btn.classList.toggle('dj-theme-toggle__btn--active', on);
            });
        });
    }

    function _setThemeMode(mode) {
        if (THEME_MODES.indexOf(mode) < 0) return;
        // djust.theming's theme.js switches the mode itself on any
        // [data-theme-mode] click; only stand in for it when it is absent.
        if (!globalThis.djustTheme) {
            try { localStorage.setItem(THEME_MODE_KEY, mode); } catch (_err) { /* storage blocked */ }
            const prefersDark = globalThis.matchMedia &&
                globalThis.matchMedia('(prefers-color-scheme: dark)').matches;
            const dark = mode === 'dark' || (mode === 'system' && prefersDark);
            document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
            document.documentElement.setAttribute('data-theme-mode', mode);
        }
        _syncThemeToggles(mode);
    }

    document.addEventListener('click', function (e) {
        const target = e.target;
        const richSelect = _closest(target, '.rich-select');
        _closeRichSelects(richSelect);
        if (richSelect) {
            if (_closest(target, '.rich-select-option')) {
                _setRichSelectOpen(richSelect, false);
            } else if (_closest(target, '.rich-select-trigger') &&
                       !richSelect.classList.contains('rich-select--disabled')) {
                _setRichSelectOpen(richSelect, !richSelect.classList.contains('rich-select--open'));
            }
        }

        const comboList = _closest(target, '.combobox-dropdown');
        if (comboList) {
            const input = _closest(comboList, '.combobox').querySelector('.combobox-input');
            if (input) input.blur();
        }

        const modeButton = _closest(target, '.dj-theme-toggle [data-theme-mode]');
        if (modeButton) _setThemeMode(modeButton.getAttribute('data-theme-mode'));
    });

    document.addEventListener('keydown', function (e) {
        const richSelect = _closest(e.target, '.rich-select');
        if (!richSelect) return;
        const open = richSelect.classList.contains('rich-select--open');
        if (e.key === 'Escape' && open) {
            _setRichSelectOpen(richSelect, false);
            const trigger = richSelect.querySelector('.rich-select-trigger');
            if (trigger) trigger.focus();
            return;
        }
        if ((e.key === 'Enter' || e.key === ' ') && _closest(e.target, '.rich-select-trigger') &&
            !richSelect.classList.contains('rich-select--disabled')) {
            e.preventDefault();
            _setRichSelectOpen(richSelect, !open);
        }
    });

    document.addEventListener('input', function (e) {
        const box = e.target;
        if (!box || !box.classList || !box.classList.contains('rich-select-search-input')) return;
        const list = _closest(box, '.rich-select-dropdown');
        if (!list) return;
        const query = box.value.toLowerCase();
        list.querySelectorAll('.rich-select-option').forEach(function (item) {
            item.style.display = item.textContent.toLowerCase().indexOf(query) >= 0 ? '' : 'none';
        });
    });

    // An option press must not move focus out of the input: the list is
    // shown by `.combobox-input:focus ~ .combobox-dropdown`.
    document.addEventListener('mousedown', function (e) {
        if (_closest(e.target, '.combobox-dropdown')) e.preventDefault();
    });

    function _comboExpanded(e, expanded) {
        const input = e.target;
        if (input && input.classList && input.classList.contains('combobox-input')) {
            input.setAttribute('aria-expanded', expanded ? 'true' : 'false');
        }
    }
    document.addEventListener('focusin', function (e) { _comboExpanded(e, true); });
    document.addEventListener('focusout', function (e) { _comboExpanded(e, false); });

    function _syncStoredThemeMode() {
        let stored = null;
        try { stored = localStorage.getItem(THEME_MODE_KEY); } catch (_err) { /* storage blocked */ }
        if (stored && THEME_MODES.indexOf(stored) >= 0) _syncThemeToggles(stored);
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _syncStoredThemeMode);
    } else {
        _syncStoredThemeMode();
    }
})();
