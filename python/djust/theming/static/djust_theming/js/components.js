/**
 * djust_theming - Component behavior layer
 *
 * Thin behavior layer for interactive components (modals, dropdowns, tabs).
 * Uses data-attribute hooks. No dependencies. ~4KB unminified.
 *
 * Auto-initializes on DOMContentLoaded.
 * Re-initializes on djust:dom-update for LiveView compatibility.
 */

(function() {
    'use strict';

    // =========================================================================
    // Modals
    // =========================================================================

    function initModals(root) {
        root = root || document;

        // Open triggers
        root.querySelectorAll('[data-theme-modal-open]').forEach(function(trigger) {
            if (trigger._djustModalBound) return;
            trigger._djustModalBound = true;

            trigger.addEventListener('click', function(e) {
                e.preventDefault();
                var modalId = this.getAttribute('data-theme-modal-open');
                var backdrop = document.querySelector('[data-theme-modal="' + modalId + '"]');
                if (backdrop) {
                    backdrop.style.display = '';
                    // Focus the dialog for accessibility
                    var dialog = backdrop.querySelector('[role="dialog"]');
                    if (dialog) dialog.focus();
                    document.body.style.overflow = 'hidden';
                }
            });
        });

        // Close triggers (buttons inside modals)
        root.querySelectorAll('[data-theme-modal-close]').forEach(function(btn) {
            if (btn._djustModalCloseBound) return;
            btn._djustModalCloseBound = true;

            btn.addEventListener('click', function(e) {
                e.preventDefault();
                var backdrop = this.closest('[data-theme-modal]');
                if (backdrop) {
                    backdrop.style.display = 'none';
                    document.body.style.overflow = '';
                }
            });
        });

        // Backdrop click to close
        root.querySelectorAll('[data-theme-modal]').forEach(function(backdrop) {
            if (backdrop._djustBackdropBound) return;
            backdrop._djustBackdropBound = true;

            backdrop.addEventListener('click', function(e) {
                // Only close if clicking the backdrop itself, not the dialog content
                if (e.target === this) {
                    this.style.display = 'none';
                    document.body.style.overflow = '';
                }
            });
        });
    }

    // ESC key to close topmost modal (delegated at document level)
    function handleModalEsc(e) {
        if (e.key === 'Escape') {
            var modals = document.querySelectorAll('[data-theme-modal]');
            for (var i = modals.length - 1; i >= 0; i--) {
                if (modals[i].style.display !== 'none') {
                    modals[i].style.display = 'none';
                    document.body.style.overflow = '';
                    break;
                }
            }
        }
    }

    // =========================================================================
    // Dropdowns
    // =========================================================================

    function initDropdowns(root) {
        root = root || document;

        root.querySelectorAll('[data-theme-dropdown]').forEach(function(dropdown) {
            if (dropdown._djustDropdownBound) return;
            dropdown._djustDropdownBound = true;

            var trigger = dropdown.querySelector('[aria-haspopup="true"]');
            var menu = dropdown.querySelector('[role="menu"]');
            if (!trigger || !menu) return;

            trigger.addEventListener('click', function(e) {
                e.preventDefault();
                e.stopPropagation();
                var isOpen = menu.style.display !== 'none';
                closeAllDropdowns();
                if (!isOpen) {
                    menu.style.display = '';
                    trigger.setAttribute('aria-expanded', 'true');
                    dropdown.setAttribute('data-open', '');
                    // Focus first menu item
                    var firstItem = menu.querySelector('[role="menuitem"], .dropdown-item');
                    if (firstItem) firstItem.focus();
                }
            });

            // Keyboard navigation inside dropdown
            dropdown.addEventListener('keydown', function(e) {
                if (menu.style.display === 'none') return;

                var items = Array.from(menu.querySelectorAll('[role="menuitem"], .dropdown-item'));
                if (!items.length) return;
                var currentIndex = items.indexOf(document.activeElement);

                switch (e.key) {
                    case 'ArrowDown':
                        e.preventDefault();
                        var nextIndex = currentIndex < items.length - 1 ? currentIndex + 1 : 0;
                        items[nextIndex].focus();
                        break;
                    case 'ArrowUp':
                        e.preventDefault();
                        var prevIndex = currentIndex > 0 ? currentIndex - 1 : items.length - 1;
                        items[prevIndex].focus();
                        break;
                    case 'Escape':
                        e.preventDefault();
                        closeDropdown(dropdown, trigger, menu);
                        trigger.focus();
                        break;
                }
            });
        });
    }

    function closeDropdown(dropdown, trigger, menu) {
        menu.style.display = 'none';
        trigger.setAttribute('aria-expanded', 'false');
        dropdown.removeAttribute('data-open');
    }

    function closeAllDropdowns() {
        document.querySelectorAll('[data-theme-dropdown]').forEach(function(dropdown) {
            var trigger = dropdown.querySelector('[aria-haspopup="true"]');
            var menu = dropdown.querySelector('[role="menu"]');
            if (trigger && menu) {
                closeDropdown(dropdown, trigger, menu);
            }
        });
    }

    // Click outside to close dropdowns
    function handleDropdownClickOutside(e) {
        if (!e.target.closest('[data-theme-dropdown]')) {
            closeAllDropdowns();
        }
    }

    // =========================================================================
    // Tabs
    // =========================================================================

    function initTabs(root) {
        root = root || document;

        root.querySelectorAll('[data-theme-tabs]').forEach(function(tabContainer) {
            if (tabContainer._djustTabsBound) return;
            tabContainer._djustTabsBound = true;

            var tabList = tabContainer.querySelector('[role="tablist"]');
            if (!tabList) return;

            var tabs = Array.from(tabList.querySelectorAll('[role="tab"]'));

            tabs.forEach(function(tab) {
                tab.addEventListener('click', function(e) {
                    e.preventDefault();
                    activateTab(tabContainer, tabs, this);
                });
            });

            // Keyboard navigation
            tabList.addEventListener('keydown', function(e) {
                var currentTab = document.activeElement;
                if (!currentTab || currentTab.getAttribute('role') !== 'tab') return;

                var currentIndex = tabs.indexOf(currentTab);
                var newIndex = -1;

                switch (e.key) {
                    case 'ArrowRight':
                        e.preventDefault();
                        newIndex = currentIndex < tabs.length - 1 ? currentIndex + 1 : 0;
                        break;
                    case 'ArrowLeft':
                        e.preventDefault();
                        newIndex = currentIndex > 0 ? currentIndex - 1 : tabs.length - 1;
                        break;
                    case 'Home':
                        e.preventDefault();
                        newIndex = 0;
                        break;
                    case 'End':
                        e.preventDefault();
                        newIndex = tabs.length - 1;
                        break;
                }

                if (newIndex >= 0) {
                    tabs[newIndex].focus();
                    activateTab(tabContainer, tabs, tabs[newIndex]);
                }
            });
        });
    }

    function activateTab(container, tabs, selectedTab) {
        var activeClass = container.getAttribute('data-active-class') || 'tab-active';
        var hiddenClass = container.getAttribute('data-hidden-class') || 'tab-panel-hidden';

        tabs.forEach(function(tab, index) {
            var panelId = tab.getAttribute('aria-controls');
            var panel = document.getElementById(panelId);
            var isSelected = tab === selectedTab;

            tab.setAttribute('aria-selected', isSelected ? 'true' : 'false');
            tab.setAttribute('tabindex', isSelected ? '0' : '-1');

            // Toggle active class
            if (isSelected) {
                tab.classList.add(activeClass);
            } else {
                tab.classList.remove(activeClass);
            }

            if (panel) {
                if (isSelected) {
                    panel.removeAttribute('hidden');
                    panel.classList.remove(hiddenClass);
                } else {
                    panel.setAttribute('hidden', '');
                    panel.classList.add(hiddenClass);
                }
            }
        });
    }

    // =========================================================================
    // Nav active state (#3318)
    // =========================================================================

    // ``{% theme_nav_item %}`` decides ``active`` from ``request.path`` when it
    // renders. A nav outside the swapped ``dj-root`` is never re-rendered by
    // ``dj-navigate``, so the highlight stayed on the page you left. The tag
    // stamps ``data-dj-nav="<url>"`` on a link whose state it auto-detected, and
    // this recomputes that state from ``location.pathname`` with the SAME rule
    // the tag uses: exact for ``/``, a path prefix for anything else. A link the
    // app pinned with ``active=`` carries no marker and is left alone.
    //
    // It toggles the ``active`` class and ``aria-current`` the template renders,
    // so server-rendered markup stays correct for first paint and without JS.
    function updateNavActive(root) {
        var path = window.location.pathname;
        (root || document).querySelectorAll('[data-dj-nav]').forEach(function(link) {
            var url = link.getAttribute('data-dj-nav');
            var active = url === '/' ? path === '/' : path.indexOf(url) === 0;
            link.classList.toggle('active', active);
            if (active) {
                link.setAttribute('aria-current', 'page');
            } else if (link.getAttribute('aria-current') === 'page') {
                link.removeAttribute('aria-current');
            }
        });
    }

    // =========================================================================
    // Initialization
    // =========================================================================

    function initAll(root) {
        // Not subject to the stand-down below: the nav is server-rendered on
        // every page and only ever moves on the client, which is exactly the
        // LiveView case.
        updateNavActive(root);

        // Stand down on any page djust is driving.
        //
        // The theming components are server-driven when a LiveView hosts them:
        // their markup carries `dj-click`, the state lives on the server, and
        // the framework re-renders from it. Binding here as well would put two
        // writers on the same DOM — the client toggling `display`, the active
        // classes and `aria-expanded` while the server renders the same change
        // — and the loser is whichever runs second.
        //
        // `dj-view` is the signal: the server stamps it onto the mount root of
        // every LiveView render (`mixins/request.py`), and a plain Django page
        // never has one. The theming gallery and the theme editor are plain
        // pages, so they keep this fallback; the catalogue's pages are
        // LiveViews, so they do not.
        if (document.querySelector('[dj-view]')) return;

        initModals(root);
        initDropdowns(root);
        initTabs(root);
    }

    // Initial setup
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() {
            initAll(document);
        });
    } else {
        initAll(document);
    }

    // Global event listeners (only bind once)
    document.addEventListener('keydown', handleModalEsc);
    document.addEventListener('click', handleDropdownClickOutside);

    // Re-init when djust LiveView updates the DOM
    window.addEventListener('djust:dom-update', function() {
        initAll(document);
    });

    // The nav sits outside the swapped dj-root, so follow the URL itself:
    // `djust:navigate-end` is dispatched on `document` when a dj-navigate /
    // live_redirect lands, and back/forward fires `popstate`.
    document.addEventListener('djust:navigate-end', function() {
        updateNavActive(document);
    });
    window.addEventListener('popstate', function() {
        updateNavActive(document);
    });
    // A page restored from the back/forward cache keeps the DOM it was left in.
    window.addEventListener('pageshow', function(e) {
        if (e.persisted) updateNavActive(document);
    });

    // Expose for manual init
    window.djustComponents = {
        initAll: initAll,
        initModals: initModals,
        initDropdowns: initDropdowns,
        initTabs: initTabs,
        updateNavActive: updateNavActive,
    };

})();
