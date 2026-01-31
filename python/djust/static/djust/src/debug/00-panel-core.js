/**
 * djust Developer Bar
 * Professional debugging tools for djust applications
 */

(function() {
    'use strict';

    // Check if we should load the debug panel
    if (!window.DEBUG_MODE) {
        console.log('[djust] Debug panel disabled (DEBUG_MODE=false)');
        return;
    }
    class DjustDebugPanel {
        constructor(config = {}) {
            this.config = {
                enabled: true,
                position: 'bottom',
                theme: 'dark',
                maxHistory: 100,
                maxPatchHistory: 50,
                shortcuts: {
                    toggle: navigator.platform.match(/Mac/) ? 'Cmd+Shift+D' : 'Ctrl+Shift+D',
                    search: navigator.platform.match(/Mac/) ? 'Cmd+Shift+F' : 'Ctrl+Shift+F',
                    clear: navigator.platform.match(/Mac/) ? 'Cmd+Shift+C' : 'Ctrl+Shift+C',
                },
                ...config
            };

            this.state = {
                isOpen: false,
                activeTab: 'events',
                searchQuery: '',
                filters: {
                    types: [],
                    severity: 'all'
                }
            };

            this.tabs = new Map();
            this.eventHistory = [];
            this.patchHistory = [];
            this.networkHistory = [];
            this.stateHistory = [];  // State timeline tracking
            this.maxStateHistory = 50;  // Maximum state snapshots to keep
            this.errorCount = 0;
            this.warningCount = 0;

            // Performance metrics tracking
            this.memoryHistory = [];  // Array of {timestamp, memory_mb, context_bytes}
            this.maxMemoryHistoryLength = 50;
            this.totalContextSize = 0;
            this.contextSizeCount = 0;

            // Real data from server
            this.handlers = null;
            this.components = null;
            this.variables = null;
            this.performance = null;
            this.viewInfo = null;

            this.init();
        }

        init() {
            this.createFloatingButton();
            this.createPanel();
            this.registerTabs();
            this.attachEventListeners();
            this.hookIntoLiveView();
            this.loadState();

            console.log('[djust] Developer Bar initialized 🐍');
        }
