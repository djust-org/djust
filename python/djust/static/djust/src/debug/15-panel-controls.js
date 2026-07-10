        open() {
            this.state.isOpen = true;
            this._applyDockStyles();
            this.renderTabContent();
            this.saveState();
        }

        close() {
            this.state.isOpen = false;
            this.panel.setAttribute('style', 'display: none !important;');
            this._updateButtonOffset();
            this.saveState();
        }

        toggle() {
            if (this.state.isOpen) {
                this.close();
            } else {
                this.open();
            }
        }

        clear() {
            this.eventHistory = [];
            this.patchHistory = [];
            this.networkHistory = [];
            this.errorCount = 0;
            this.warningCount = 0;
            this.memoryHistory = [];
            this.totalContextSize = 0;
            this.contextSizeCount = 0;
            this.updateErrorBadge();
            this.updateCounter('event-count', 0);
            this.updateCounter('patch-count', 0);
            this.updateCounter('error-count', 0);
            this.updateCounter('warning-count', 0);
            this.renderTabContent();
        }

        // State persistence — scoped per view
        _stateKey() {
            const viewName = (window.DJUST_DEBUG_INFO && window.DJUST_DEBUG_INFO.view_class) || 'global';
            return `djust-debug-state:${viewName}`;
        }

        saveState() {
            // Only persist UI preferences, not data or filters
            const uiState = {
                isOpen: this.state.isOpen,
                activeTab: this.state.activeTab,
                dock: this.state.dock,
                panelHeight: this.state.panelHeight,
                panelWidth: this.state.panelWidth,
            };
            localStorage.setItem(this._stateKey(), JSON.stringify(uiState));
        }

        loadState() {
            const saved = localStorage.getItem(this._stateKey());
            if (saved) {
                try {
                    const parsedState = JSON.parse(saved);
                    // Merge only UI preferences into state
                    this.state.isOpen = parsedState.isOpen || false;
                    this.state.activeTab = parsedState.activeTab || 'events';
                    if (this._dockPositions().includes(parsedState.dock)) {
                        this.state.dock = parsedState.dock;
                    }
                    if (Number.isFinite(parsedState.panelHeight)) {
                        this.state.panelHeight = parsedState.panelHeight;
                    }
                    if (Number.isFinite(parsedState.panelWidth)) {
                        this.state.panelWidth = parsedState.panelWidth;
                    }
                    this._updateDockButtons();
                    this._positionResizeHandle();

                    // Restore panel visibility and active tab if saved
                    if (parsedState.isOpen || parsedState.activeTab) {
                        setTimeout(() => {
                            if (parsedState.isOpen) this.open();
                            if (parsedState.activeTab) this.switchTab(parsedState.activeTab);
                        }, 0);
                    }
                } catch (e) {
                    console.warn('[djust] Failed to load debug panel state:', e);
                }
            }
        }

        // Event listeners
        attachEventListeners() {
            // Keyboard shortcuts
            this.keydownHandler = (e) => {
                const isMac = navigator.platform.match(/Mac/);
                const ctrlKey = isMac ? e.metaKey : e.ctrlKey;

                if (ctrlKey && e.shiftKey) {
                    switch (e.key.toUpperCase()) {
                        case 'D':
                            e.preventDefault();
                            this.toggle();
                            break;
                        case 'F':
                            e.preventDefault();
                            if (this.state.isOpen) {
                                this.panel.querySelector('.djust-search').focus();
                            }
                            break;
                        case 'C':
                            e.preventDefault();
                            if (this.state.isOpen) {
                                this.clear();
                            }
                            break;
                    }
                }
            };
            document.addEventListener('keydown', this.keydownHandler);

            // Panel controls
            this.panel.querySelector('.djust-btn-close').addEventListener('click', () => this.close());
            this.panel.querySelector('.djust-btn-clear').addEventListener('click', () => this.clear());
            this.panel.querySelector('.djust-btn-export').addEventListener('click', () => this.export());
            this.panel.querySelector('.djust-btn-import').addEventListener('click', () => this.import());

            // Search
            const searchInput = this.panel.querySelector('.djust-search');
            searchInput.addEventListener('input', (e) => {
                this.state.searchQuery = e.target.value;
                this.performSearch();
            });
        }

        performSearch() {
            this.renderTabContent();
        }

        export() {
            const data = {
                version: '1.0.0',
                timestamp: Date.now(),
                events: this.eventHistory,
                network: this.networkHistory,
                patches: this.patchHistory,
                state: this.state
            };

            const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `djust-debug-${Date.now()}.json`;
            a.click();
            URL.revokeObjectURL(url);
        }

        import() {
            const input = document.createElement('input');
            input.type = 'file';
            input.accept = 'application/json';
            input.onchange = (e) => {
                const file = e.target.files[0];
                if (file) {
                    const reader = new FileReader();
                    reader.onload = (e) => {
                        try {
                            const data = JSON.parse(e.target.result);
                            this.eventHistory = data.events || [];
                            this.networkHistory = data.network || [];
                            this.patchHistory = data.patches || [];
                            this.renderTabContent();
                            if (globalThis.djustDebug) console.log('[djust] Debug session imported successfully');
                        } catch (err) {
                            console.error('[djust] Failed to import debug session:', err);
                        }
                    };
                    reader.readAsText(file);
                }
            };
            input.click();
        }

        destroy() {
            // Remove event listeners
            if (this.keydownHandler) {
                document.removeEventListener('keydown', this.keydownHandler);
            }

            // Remove DOM elements
            if (this.button && this.button.parentNode) {
                this.button.remove();
            }
            if (this.panel && this.panel.parentNode) {
                this.panel.remove();
            }

            // Clear data
            this.eventHistory = [];
            this.networkHistory = [];
            this.patchHistory = [];
            this.stateHistory = [];
            this.components = null;
            this.variables = {};

            if (globalThis.djustDebug) console.log('[djust] Debug panel destroyed');
        }
    }

    // Export DjustDebugPanel to window for manual initialization
    window.DjustDebugPanel = DjustDebugPanel;

})();
