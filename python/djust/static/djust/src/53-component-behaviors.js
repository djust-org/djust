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
//                     close the list after the pick, reflect aria-expanded;
//                     the picked option's value goes to the hidden
//                     `.combobox-value` input the form submits, and typed text
//                     that matches no option is discarded on change unless the
//                     combobox has `data-allow-free-text`. Without a server
//                     search event (`data-filter="client"`) typing filters
//                     the options here.
//   .dj-theme-toggle  switch the colour mode (`data-theme-mode`) when
//                     djust.theming's theme.js is not on the page, and keep
//                     every toggle's aria-checked in step with the mode.
//   .popover-wrapper, .dj-popconfirm-wrapper
//                     open on the trigger (`data-open`, aria-expanded); close
//                     on a click elsewhere, Escape, or a popconfirm button.
//   .collapsible      toggle `collapsible-open` and aria-expanded.
//   .ctx-wrapper      open the menu at the pointer on right-click; close on
//                     the next click or Escape.
//   .code-block-copy  copy the block's code.
//   .dropzone         drag-over styling, put dropped files in the input, show
//                     the file count.
//   .kanban           drag cards between columns; a drop sends the board's
//                     `data-move-event` with {card_id, from_col, to_col}.
//   .rte              toolbar commands (`data-command`) keep the selection;
//                     the editor's HTML is mirrored into its hidden input.
//   .multi-select     search-box filtering.
//   .split-pane       drag the handle to resize (10%-90%).
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

    // Mark `chosen` as the selected option among `options`.
    function _markSelected(options, chosen, activeClass) {
        options.forEach(function (opt) {
            const on = opt === chosen;
            opt.setAttribute('aria-selected', on ? 'true' : 'false');
            opt.classList.toggle(activeClass, on);
        });
    }

    function _comboboxOptions(box) {
        return Array.prototype.slice.call(box.querySelectorAll('.combobox-option'));
    }

    function _pickComboboxOption(box, option) {
        const hidden = box.querySelector('.combobox-value');
        const input = box.querySelector('.combobox-input');
        if (!hidden || !input) return;  // multiple mode: the server renders the tags
        hidden.value = option.getAttribute('data-value') || '';
        input.value = option.textContent.trim();
        _markSelected(_comboboxOptions(box), option, 'combobox-option-selected');
    }

    function _pickRichSelectOption(root, option) {
        const hidden = root.querySelector('input[type=hidden]');
        if (hidden) hidden.value = option.getAttribute('data-value') || '';
        const trigger = root.querySelector('.rich-select-trigger');
        const chevron = trigger && trigger.querySelector('.rich-select-chevron');
        if (trigger && chevron) {
            Array.prototype.slice.call(trigger.childNodes).forEach(function (node) {
                if (node !== chevron) trigger.removeChild(node);
            });
            Array.prototype.slice.call(option.childNodes).forEach(function (node) {
                trigger.insertBefore(node.cloneNode(true), chevron);
            });
        }
        _markSelected(Array.prototype.slice.call(root.querySelectorAll('.rich-select-option')),
            option, 'rich-select-option--active');
    }

    document.addEventListener('click', function (e) {
        const target = e.target;
        const richSelect = _closest(target, '.rich-select');
        _closeRichSelects(richSelect);
        if (richSelect) {
            const richOption = _closest(target, '.rich-select-option');
            if (richOption) {
                _pickRichSelectOption(richSelect, richOption);
                _setRichSelectOpen(richSelect, false);
            } else if (_closest(target, '.rich-select-trigger') &&
                       !richSelect.classList.contains('rich-select--disabled')) {
                _setRichSelectOpen(richSelect, !richSelect.classList.contains('rich-select--open'));
            }
        }

        const comboList = _closest(target, '.combobox-dropdown');
        if (comboList) {
            const box = _closest(comboList, '.combobox');
            const comboOption = _closest(target, '.combobox-option');
            if (comboOption) _pickComboboxOption(box, comboOption);
            const input = box.querySelector('.combobox-input');
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

    // ---- popover / popconfirm -------------------------------------------
    const POPUPS = '.popover-wrapper, .dj-popconfirm-wrapper';

    function _setPopupOpen(wrapper, open) {
        if (open) wrapper.setAttribute('data-open', '');
        else wrapper.removeAttribute('data-open');
        const trigger = wrapper.querySelector('.popover-trigger, .dj-popconfirm-trigger');
        if (trigger) trigger.setAttribute('aria-expanded', open ? 'true' : 'false');
    }

    function _closePopups(except) {
        document.querySelectorAll('.popover-wrapper[data-open], .dj-popconfirm-wrapper[data-open]')
            .forEach(function (w) { if (w !== except) _setPopupOpen(w, false); });
        document.querySelectorAll('.ctx-menu[data-open]').forEach(function (m) {
            m.removeAttribute('data-open');
        });
    }

    function _countText(n) {
        return n ? n + ' file' + (n > 1 ? 's' : '') + ' selected' : '';
    }

    function _showFileCount(zone, files) {
        const count = zone.querySelector('.dz-file-count');
        if (count) count.textContent = _countText(files.length);
        zone.classList.toggle('dropzone-has-file', files.length > 0);
    }

    document.addEventListener('click', function (e) {
        const target = e.target;
        const popup = _closest(target, POPUPS);
        _closePopups(popup);
        if (popup) {
            if (_closest(target, '.popover-trigger, .dj-popconfirm-trigger')) {
                _setPopupOpen(popup, !popup.hasAttribute('data-open'));
            } else if (_closest(target, '.dj-popconfirm__btn')) {
                _setPopupOpen(popup, false);
            }
        }

        const collapsibleTrigger = _closest(target, '.collapsible-trigger');
        if (collapsibleTrigger) {
            const box = _closest(collapsibleTrigger, '.collapsible');
            if (box) {
                const open = box.classList.toggle('collapsible-open');
                collapsibleTrigger.setAttribute('aria-expanded', open ? 'true' : 'false');
            }
        }

        const copyButton = _closest(target, '.code-block-copy');
        if (copyButton) {
            const code = _closest(copyButton, '.code-block').querySelector('code');
            if (code && navigator.clipboard) {
                navigator.clipboard.writeText(code.textContent).then(function () {
                    copyButton.textContent = 'Copied!';
                    setTimeout(function () { copyButton.textContent = 'Copy'; }, 2000);
                });
            }
        }
    });

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') _closePopups(null);
    });

    // ---- context menu -----------------------------------------------------
    document.addEventListener('contextmenu', function (e) {
        const wrapper = _closest(e.target, '.ctx-wrapper');
        if (!wrapper) return;
        const menu = wrapper.querySelector('.ctx-menu');
        if (!menu) return;
        e.preventDefault();
        _closePopups(null);
        const box = wrapper.getBoundingClientRect();
        menu.style.left = (e.clientX - box.left) + 'px';
        menu.style.top = (e.clientY - box.top) + 'px';
        menu.setAttribute('data-open', '1');
    });

    // ---- drag and drop: dropzone, kanban --------------------------------
    document.addEventListener('dragstart', function (e) {
        const card = _closest(e.target, '.kanban-card');
        if (!card || !e.dataTransfer) return;
        e.dataTransfer.setData('card', card.dataset.cardId || '');
        e.dataTransfer.setData('from', card.dataset.colId || '');
        card.classList.add('dragging');
    });

    document.addEventListener('dragend', function (e) {
        const card = _closest(e.target, '.kanban-card');
        if (card) card.classList.remove('dragging');
    });

    document.addEventListener('dragover', function (e) {
        const zone = _closest(e.target, '.dropzone, .kanban-col');
        if (!zone) return;
        e.preventDefault();
        zone.classList.add(zone.classList.contains('dropzone') ? 'dropzone-over' : 'kanban-col-over');
    });

    document.addEventListener('dragleave', function (e) {
        const zone = _closest(e.target, '.dropzone, .kanban-col');
        if (zone) zone.classList.remove('dropzone-over', 'kanban-col-over');
    });

    document.addEventListener('drop', function (e) {
        const zone = _closest(e.target, '.dropzone, .kanban-col');
        if (!zone) return;
        e.preventDefault();
        zone.classList.remove('dropzone-over', 'kanban-col-over');
        const data = e.dataTransfer;
        if (zone.classList.contains('dropzone')) {
            const files = data ? data.files : [];
            if (!files || !files.length) return;
            const input = zone.querySelector('input[type=file]');
            try {
                const transfer = new DataTransfer();
                Array.prototype.forEach.call(files, function (file) { transfer.items.add(file); });
                input.files = transfer.files;
            } catch (_err) { /* no DataTransfer constructor: the count still shows */ }
            _showFileCount(zone, files);
            return;
        }
        const board = _closest(zone, '.kanban');
        const moveEvent = board && board.getAttribute('data-move-event');
        const from = data ? data.getData('from') : '';
        const to = zone.dataset.colId;
        if (moveEvent && from !== to && globalThis.djust && globalThis.djust.handleEvent) {
            globalThis.djust.handleEvent(moveEvent,
                { card_id: data.getData('card'), from_col: from, to_col: to });
        }
    });

    // Typed combobox text, once committed: an exact (case-insensitive) label
    // match picks that option; empty text clears the value; other text is the
    // value only with data-allow-free-text, else the previous choice returns.
    function _commitComboboxText(box, input) {
        const hidden = box.querySelector('.combobox-value');
        if (!hidden) return;
        const text = input.value.trim();
        const options = _comboboxOptions(box);
        const match = options.filter(function (opt) {
            return opt.textContent.trim().toLowerCase() === text.toLowerCase();
        })[0];
        if (match) {
            _pickComboboxOption(box, match);
        } else if (!text) {
            hidden.value = '';
            _markSelected(options, null, 'combobox-option-selected');
        } else if (box.hasAttribute('data-allow-free-text')) {
            hidden.value = text;
            _markSelected(options, null, 'combobox-option-selected');
        } else {
            const current = options.filter(function (opt) {
                return opt.getAttribute('data-value') === hidden.value;
            })[0];
            input.value = current ? current.textContent.trim() : '';
        }
    }

    document.addEventListener('change', function (e) {
        const input = e.target;
        if (!input || !input.classList) return;
        if (input.classList.contains('dropzone-input')) {
            const zone = _closest(input, '.dropzone');
            if (zone) _showFileCount(zone, input.files || []);
        } else if (input.classList.contains('combobox-input')) {
            const box = _closest(input, '.combobox');
            if (box) _commitComboboxText(box, input);
        }
    });

    // ---- rich text editor, multi-select search ----------------------------
    document.addEventListener('mousedown', function (e) {
        const button = _closest(e.target, '.rte-btn[data-command]');
        if (!button) return;
        e.preventDefault();  // keep the editor's selection
        document.execCommand(button.getAttribute('data-command'), false,
            button.getAttribute('data-command-arg'));
    });

    document.addEventListener('input', function (e) {
        const field = e.target;
        if (!field || !field.classList) return;
        if (field.classList.contains('rte-editor')) {
            const hidden = _closest(field, '.rte').querySelector('input[type=hidden]');
            if (hidden) hidden.value = field.innerHTML;
        } else if (field.classList.contains('combobox-input')) {
            const box = _closest(field, '.combobox[data-filter="client"]');
            if (!box) return;
            const query = field.value.trim().toLowerCase();
            _comboboxOptions(box).forEach(function (opt) {
                opt.style.display = opt.textContent.toLowerCase().indexOf(query) >= 0 ? '' : 'none';
            });
        } else if (field.classList.contains('multi-select-search')) {
            const query = field.value.toLowerCase();
            _closest(field, '.multi-select').querySelectorAll('.multi-select-option')
                .forEach(function (item) {
                    item.style.display = item.textContent.toLowerCase().indexOf(query) >= 0 ? '' : 'none';
                });
        }
    });

    // ---- split pane ---------------------------------------------------------
    let _splitDrag = null;
    document.addEventListener('mousedown', function (e) {
        const handle = _closest(e.target, '.sp-handle');
        const pane = handle && _closest(handle, '.split-pane');
        if (!pane) return;
        e.preventDefault();
        _splitDrag = pane;
    });
    document.addEventListener('mousemove', function (e) {
        if (!_splitDrag) return;
        const box = _splitDrag.getBoundingClientRect();
        const horizontal = _splitDrag.classList.contains('split-pane-horizontal');
        const span = horizontal ? box.width : box.height;
        if (!span) return;
        const offset = horizontal ? e.clientX - box.left : e.clientY - box.top;
        const pct = Math.max(10, Math.min(90, offset / span * 100));
        const first = _splitDrag.querySelector('.sp-pane-1');
        if (first) {
            if (horizontal) first.style.width = pct + '%';
            else first.style.height = pct + '%';
        }
    });
    document.addEventListener('mouseup', function () { _splitDrag = null; });

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
