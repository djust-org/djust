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

        createFloatingButton() {
            // Check if button already exists
            const existingButton = document.getElementById('djust-debug-button');
            if (existingButton) {
                existingButton.remove();
            }

            this.button = document.createElement('button');
            this.button.id = 'djust-debug-button';
            this.button.className = 'djust-debug-button';
            this.button.setAttribute('aria-label', 'djust Developer Tools');
            this.button.setAttribute('title', `djust Developer Tools (${this.config.shortcuts.toggle})`);

            // Use the djust icon
            this.button.innerHTML = `
                <img src="/static/images/djust-icon.png" alt="djust" class="djust-logo">
                <span class="error-badge" style="display: none;">0</span>
            `;

            // Check if styles already exist
            const existingStyle = document.getElementById('djust-debug-button-styles');
            if (!existingStyle) {
                // Add styles only if they don't exist
                const style = document.createElement('style');
                style.id = 'djust-debug-button-styles';
                style.textContent = `
                .djust-debug-button {
                    position: fixed;
                    bottom: 20px;
                    right: 20px;
                    width: 50px;
                    height: 50px;
                    padding: 0;
                    background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
                    border: 2px solid rgba(229, 115, 36, 0.3);
                    border-radius: 50%;
                    cursor: pointer;
                    z-index: 10000;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
                    transition: all 0.3s ease;
                }

                .djust-debug-button:hover {
                    transform: scale(1.1);
                    border-color: rgba(229, 115, 36, 0.6);
                    box-shadow: 0 6px 20px rgba(229, 115, 36, 0.3);
                }

                .djust-debug-button.active {
                    animation: activity-pulse 0.5s ease;
                }

                .djust-debug-button .djust-logo {
                    width: 30px;
                    height: 30px;
                    object-fit: contain;
                    filter: brightness(1.1);
                    transition: transform 0.3s ease;
                }

                .djust-debug-button:hover .djust-logo {
                    transform: rotate(10deg) scale(1.1);
                }

                .djust-debug-button .error-badge {
                    position: absolute;
                    top: -5px;
                    right: -5px;
                    background: #ef4444;
                    color: white;
                    font-size: 10px;
                    font-weight: bold;
                    padding: 2px 6px;
                    border-radius: 10px;
                    min-width: 18px;
                    text-align: center;
                    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.2);
                }

                @keyframes activity-pulse {
                    0% { transform: scale(1); }
                    50% { transform: scale(1.15); }
                    100% { transform: scale(1); }
                }

                .djust-debug-button.error {
                    animation: error-shake 0.5s ease;
                    border-color: #ef4444;
                }

                @keyframes error-shake {
                    0%, 100% { transform: translateX(0); }
                    25% { transform: translateX(-5px); }
                    75% { transform: translateX(5px); }
                }
            `;

                document.head.appendChild(style);
            }

            document.body.appendChild(this.button);

            // Button click handler
            this.button.addEventListener('click', () => this.toggle());
        }

        createPanel() {
            // Check if panel already exists
            const existingPanel = document.getElementById('djust-debug-panel');
            if (existingPanel) {
                existingPanel.remove();
            }

            this.panel = document.createElement('div');
            this.panel.id = 'djust-debug-panel';
            this.panel.className = 'djust-debug-panel';
            // Don't set display:none inline, let CSS handle it

            this.panel.innerHTML = `
                <div class="djust-panel-header">
                    <div class="djust-panel-title">
                        <img src="/static/images/djust-icon.png" alt="djust" class="djust-panel-logo">
                        <span>djust Developer Tools</span>
                        <span class="djust-version">v0.5.0</span>
                    </div>
                    <div class="djust-panel-controls">
                        <input type="text" class="djust-search" placeholder="Search...">
                        <button class="djust-btn-export" title="Export Session">
                            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                                <path d="M8 11V3M8 3L5 6M8 3L11 6M3 13H13" stroke-linecap="round" stroke-linejoin="round"/>
                            </svg>
                        </button>
                        <button class="djust-btn-import" title="Import Session">
                            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                                <path d="M8 3V11M8 11L5 8M8 11L11 8M3 13H13" stroke-linecap="round" stroke-linejoin="round"/>
                            </svg>
                        </button>
                        <button class="djust-btn-clear" title="Clear All">
                            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                                <path d="M5 2V1H11V2M2 2H14M3 2V14H13V2M6 5V11M10 5V11" stroke-linecap="round" stroke-linejoin="round"/>
                            </svg>
                        </button>
                        <button class="djust-btn-settings" title="Settings">
                            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                                <circle cx="8" cy="8" r="2"/>
                                <path d="M8 1V3M8 13V15M1 8H3M13 8H15M2.5 2.5L4 4M12 12L13.5 13.5M2.5 13.5L4 12M12 4L13.5 2.5"/>
                            </svg>
                        </button>
                        <button class="djust-btn-close" title="Close">
                            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M4 4L12 12M4 12L12 4" stroke-linecap="round"/>
                            </svg>
                        </button>
                    </div>
                </div>
                <div class="djust-panel-body">
                    <div class="djust-panel-sidebar">
                        <div class="djust-tabs"></div>
                    </div>
                    <div class="djust-panel-content">
                        <div class="djust-tab-content"></div>
                    </div>
                </div>
                <div class="djust-panel-footer">
                    <div class="djust-status">
                        <span class="djust-status-indicator"></span>
                        <span class="djust-status-text">Connected</span>
                    </div>
                    <div class="djust-stats">
                        <span class="djust-stat">Events: <span id="event-count">0</span></span>
                        <span class="djust-stat">Patches: <span id="patch-count">0</span></span>
                        <span class="djust-stat">Errors: <span id="error-count">0</span></span>
                        <span class="djust-stat warnings-stat">Warnings: <span id="warning-count">0</span></span>
                    </div>
                </div>
            `;

            // Add panel styles
            const panelStyle = document.createElement('style');
            panelStyle.id = 'djust-debug-panel-styles';
            panelStyle.textContent = `
                .djust-debug-panel {
                    position: fixed !important;
                    bottom: 0px !important;
                    left: 0px !important;
                    right: 0px !important;
                    width: 100% !important;
                    height: 400px !important;
                    background: #0f172a !important;
                    border-top: 2px solid #E57324 !important;
                    color: #f1f5f9 !important;
                    font-family: 'JetBrains Mono', 'Monaco', 'Courier New', monospace;
                    font-size: 13px;
                    z-index: 999999 !important;
                    display: none; /* Initially hidden */
                    flex-direction: column;
                    box-shadow: 0 -4px 12px rgba(0, 0, 0, 0.5);
                    transform: translateZ(0); /* Force GPU acceleration */
                    -webkit-transform: translateZ(0);
                }

                .djust-panel-header {
                    display: flex !important;
                    justify-content: space-between;
                    align-items: center;
                    padding: 10px 15px;
                    background: #1e293b !important;
                    border-bottom: 1px solid #334155;
                    min-height: 40px;
                }

                .djust-panel-title {
                    display: flex;
                    align-items: center;
                    gap: 10px;
                }

                .djust-panel-logo {
                    width: 24px;
                    height: 24px;
                    object-fit: contain;
                }

                .djust-version {
                    font-size: 11px;
                    color: #64748b;
                    margin-left: 5px;
                }

                .djust-panel-controls {
                    display: flex;
                    align-items: center;
                    gap: 8px;
                }

                .djust-search {
                    padding: 6px 12px;
                    background: rgba(15, 23, 42, 0.6);
                    border: 1px solid rgba(148, 163, 184, 0.2);
                    border-radius: 6px;
                    color: #f1f5f9;
                    width: 200px;
                    font-size: 13px;
                    transition: all 0.2s ease;
                }

                .djust-search:focus {
                    outline: none;
                    border-color: #E57324;
                    background: rgba(15, 23, 42, 0.8);
                    box-shadow: 0 0 0 2px rgba(229, 115, 36, 0.2);
                }

                .djust-search::placeholder {
                    color: #64748b;
                }

                .djust-panel-controls button {
                    background: transparent;
                    border: 1px solid transparent;
                    cursor: pointer;
                    padding: 6px;
                    border-radius: 6px;
                    color: #94a3b8;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    transition: all 0.15s ease;
                    width: 32px;
                    height: 32px;
                }

                .djust-panel-controls button:hover {
                    background: rgba(148, 163, 184, 0.1);
                    border-color: rgba(148, 163, 184, 0.2);
                    color: #f1f5f9;
                }

                .djust-panel-controls button:active {
                    background: rgba(148, 163, 184, 0.15);
                    transform: scale(0.95);
                }

                .djust-panel-controls button.djust-btn-close:hover {
                    background: rgba(239, 68, 68, 0.1);
                    border-color: rgba(239, 68, 68, 0.3);
                    color: #ef4444;
                }

                .djust-panel-controls button svg {
                    width: 16px;
                    height: 16px;
                }

                .djust-panel-body {
                    display: flex !important;
                    flex: 1;
                    overflow: hidden;
                    background: #0f172a !important;
                    color: white !important;
                }

                .djust-panel-sidebar {
                    width: 200px;
                    background: #1e293b;
                    border-right: 1px solid #334155;
                    overflow-y: auto;
                }

                .djust-tabs {
                    padding: 10px;
                    display: flex;
                    flex-direction: column;
                    gap: 2px;
                }

                .djust-tab {
                    display: flex;
                    align-items: center;
                    gap: 10px;
                    padding: 10px 12px;
                    cursor: pointer;
                    transition: all 0.2s ease;
                    border-radius: 6px;
                    position: relative;
                    color: #94a3b8;
                    font-size: 13px;
                    font-weight: 500;
                }

                .djust-tab:hover {
                    background: rgba(148, 163, 184, 0.08);
                    color: #f1f5f9;
                }

                .djust-tab.active {
                    background: rgba(229, 115, 36, 0.1);
                    color: #E57324;
                }

                .djust-tab.active::before {
                    content: '';
                    position: absolute;
                    left: 0;
                    top: 50%;
                    transform: translateY(-50%);
                    width: 3px;
                    height: 20px;
                    background: #E57324;
                    border-radius: 0 3px 3px 0;
                }

                .djust-tab-icon {
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    width: 16px;
                    height: 16px;
                }

                .djust-tab-icon svg {
                    width: 16px;
                    height: 16px;
                }

                .djust-tab-name {
                    flex: 1;
                }

                .djust-panel-content {
                    flex: 1;
                    overflow: auto;
                    padding: 15px;
                }

                .djust-panel-footer {
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    padding: 8px 15px;
                    background: rgba(30, 41, 59, 0.5);
                    border-top: 1px solid rgba(51, 65, 85, 0.5);
                    font-size: 11px;
                    backdrop-filter: blur(10px);
                }

                .djust-status {
                    display: flex;
                    align-items: center;
                    gap: 8px;
                }

                .djust-status-indicator {
                    width: 8px;
                    height: 8px;
                    background: #10b981;
                    border-radius: 50%;
                    box-shadow: 0 0 0 2px rgba(16, 185, 129, 0.2);
                    animation: pulse 2s infinite;
                }

                @keyframes pulse {
                    0%, 100% {
                        opacity: 1;
                        box-shadow: 0 0 0 2px rgba(16, 185, 129, 0.2);
                    }
                    50% {
                        opacity: 0.6;
                        box-shadow: 0 0 0 4px rgba(16, 185, 129, 0.1);
                    }
                }

                .djust-stats {
                    display: flex;
                    gap: 16px;
                    color: #64748b;
                    font-weight: 500;
                }

                .djust-stat span:last-child {
                    color: #f1f5f9;
                    font-weight: 600;
                    margin-left: 4px;
                }

                .djust-stat.warnings-stat {
                    color: #fb923c;
                }

                .djust-stat.warnings-stat span {
                    color: #fdba74;
                }

                /* Compact item styles */
                .event-item, .network-item, .patch-item {
                    border: 1px solid rgba(51, 65, 85, 0.3);
                    border-radius: 4px;
                    margin-bottom: 4px;
                    background: rgba(30, 41, 59, 0.3);
                    transition: all 0.15s ease;
                }

                .event-item:hover, .network-item:hover, .patch-item:hover {
                    background: rgba(30, 41, 59, 0.5);
                    border-color: rgba(229, 115, 36, 0.3);
                }

                .event-item.error {
                    border-color: rgba(239, 68, 68, 0.5);
                    background: rgba(127, 29, 29, 0.2);
                }

                /* Expandable headers */
                .event-header, .network-header, .patch-header {
                    padding: 8px 12px;
                    display: flex;
                    align-items: center;
                    gap: 8px;
                    cursor: default;
                    font-size: 12px;
                    color: #e2e8f0;
                }

                .expandable .event-header,
                .expandable .network-header,
                .expandable .patch-header {
                    cursor: pointer;
                }

                .expand-icon {
                    width: 12px;
                    display: inline-block;
                    transition: transform 0.15s ease;
                    color: #94a3b8;
                }

                .expanded .expand-icon {
                    transform: rotate(90deg);
                }

                /* Compact info spans */
                .event-name, .patch-count, .network-type {
                    font-weight: 600;
                    color: #f1f5f9;
                }

                .event-duration, .patch-duration, .network-size {
                    padding: 2px 6px;
                    background: rgba(59, 130, 246, 0.2);
                    border-radius: 3px;
                    font-size: 11px;
                    color: #93c5fd;
                }

                .timing-badge {
                    padding: 2px 6px;
                    border-radius: 3px;
                    font-size: 11px;
                    font-family: 'SF Mono', Monaco, monospace;
                    margin-left: 6px;
                    display: inline-block;
                }

                .timing-badge.server-handler {
                    background: rgba(168, 85, 247, 0.2);
                    color: #c084fc;
                }

                .timing-badge.server-render {
                    background: rgba(236, 72, 153, 0.2);
                    color: #f9a8d4;
                }

                .timing-badge.server-total {
                    background: rgba(34, 197, 94, 0.2);
                    color: #86efac;
                }

                .timing-badge.client-apply {
                    background: rgba(59, 130, 246, 0.2);
                    color: #93c5fd;
                }

                .timing-badge.round-trip {
                    background: rgba(251, 146, 60, 0.2);
                    color: #fdba74;
                    font-weight: 600;
                }

                .event-param-count {
                    padding: 2px 6px;
                    background: rgba(148, 163, 184, 0.15);
                    border-radius: 3px;
                    font-size: 11px;
                    color: #94a3b8;
                }

                .element-badge {
                    padding: 2px 8px;
                    background: rgba(245, 158, 11, 0.2);
                    border: 1px solid rgba(245, 158, 11, 0.3);
                    border-radius: 3px;
                    font-size: 11px;
                    color: #fbbf24;
                    font-family: 'SF Mono', Monaco, monospace;
                    margin-left: 8px;
                }

                .element-info {
                    padding: 8px 12px;
                    background: rgba(30, 41, 59, 0.5);
                    border-radius: 4px;
                    font-family: 'SF Mono', Monaco, monospace;
                    font-size: 11px;
                    color: #cbd5e1;
                    line-height: 1.6;
                }

                .element-info div {
                    margin: 2px 0;
                }

                .element-info strong {
                    color: #fbbf24;
                }

                .event-time, .patch-time, .network-time {
                    margin-left: auto;
                    font-size: 11px;
                    color: #64748b;
                }

                .network-direction {
                    width: 16px;
                    text-align: center;
                    font-weight: bold;
                }

                .network-direction:contains('↑') { color: #10b981; }
                .network-direction:contains('↓') { color: #3b82f6; }

                .network-debug {
                    font-size: 14px;
                }

                .patch-types {
                    font-size: 11px;
                    color: #94a3b8;
                }

                /* Expanded details */
                .event-details, .network-details, .patch-details {
                    padding: 12px;
                    border-top: 1px solid rgba(51, 65, 85, 0.3);
                    background: rgba(15, 23, 42, 0.3);
                    max-height: 400px;
                    overflow-y: auto;
                }

                .event-section {
                    margin-bottom: 12px;
                }

                .event-section:last-child {
                    margin-bottom: 0;
                }

                .event-section-title {
                    font-size: 11px;
                    font-weight: 600;
                    color: #94a3b8;
                    text-transform: uppercase;
                    margin-bottom: 6px;
                    letter-spacing: 0.05em;
                }

                .event-section pre,
                .network-payload pre,
                .patch-value pre {
                    margin: 0;
                    padding: 8px;
                    background: rgba(0, 0, 0, 0.3);
                    border-radius: 4px;
                    font-size: 11px;
                    color: #e2e8f0;
                    overflow-x: auto;
                    white-space: pre-wrap;
                    word-break: break-word;
                }

                .event-error-message {
                    color: #f87171;
                    padding: 8px;
                    background: rgba(127, 29, 29, 0.3);
                    border-radius: 4px;
                    font-size: 12px;
                }

                .patch-op {
                    padding: 6px 8px;
                    margin-bottom: 4px;
                    background: rgba(30, 41, 59, 0.3);
                    border-radius: 3px;
                    font-size: 11px;
                    display: flex;
                    flex-wrap: wrap;
                    align-items: baseline;
                    gap: 6px;
                }

                .patch-index {
                    color: #64748b;
                    min-width: 30px;
                }

                .patch-type {
                    font-weight: 600;
                    padding: 1px 4px;
                    background: rgba(59, 130, 246, 0.2);
                    border-radius: 2px;
                    color: #93c5fd;
                }

                .patch-path {
                    color: #a5b4fc;
                    font-family: var(--djust-font-mono);
                }

                .patch-value {
                    width: 100%;
                    margin-top: 4px;
                }

                /* Empty state */
                .empty-state {
                    padding: 40px;
                    text-align: center;
                    color: #64748b;
                    font-style: italic;
                }

                /* List containers */
                .events-list, .network-list, .patches-list {
                    padding: 8px;
                }

                /* Performance Tree Styles */
                .performance-tree {
                    font-family: 'SF Mono', Monaco, monospace;
                    font-size: 12px;
                    line-height: 1.6;
                    color: #e2e8f0;
                    padding: 12px;
                    background: rgba(15, 23, 42, 0.5);
                    border-radius: 6px;
                    margin-top: 12px;
                }

                .timing-node {
                    margin-left: 20px;
                    padding: 4px 0;
                    border-left: 1px solid rgba(148, 163, 184, 0.2);
                    position: relative;
                }

                .timing-node:before {
                    content: '';
                    position: absolute;
                    left: -1px;
                    top: 14px;
                    width: 12px;
                    height: 1px;
                    background: rgba(148, 163, 184, 0.3);
                }

                .timing-node.root {
                    margin-left: 0;
                    border-left: none;
                }

                .timing-node.root:before {
                    display: none;
                }

                .timing-node-name {
                    font-weight: 600;
                    color: #f1f5f9;
                    margin-right: 8px;
                }

                .timing-duration {
                    padding: 2px 6px;
                    border-radius: 3px;
                    font-size: 11px;
                    font-weight: 500;
                    margin-right: 8px;
                }

                .timing-duration.timing-fast {
                    background: rgba(34, 197, 94, 0.2);
                    color: #86efac;
                }

                .timing-duration.timing-medium {
                    background: rgba(251, 191, 36, 0.2);
                    color: #fde047;
                }

                .timing-duration.timing-slow {
                    background: rgba(239, 68, 68, 0.2);
                    color: #fca5a5;
                }

                .timing-warning-icon {
                    margin-left: 8px;
                    cursor: pointer;
                    font-size: 14px;
                    display: inline-block;
                    transition: transform 0.2s ease;
                }

                .timing-warning-icon:hover {
                    transform: scale(1.2);
                }

                .timing-warnings-details {
                    margin: 8px 0 8px 20px;
                    padding: 8px;
                    background: rgba(15, 23, 42, 0.5);
                    border-left: 2px solid rgba(251, 146, 60, 0.5);
                    border-radius: 4px;
                }

                .timing-warning-item {
                    padding: 8px;
                    margin-bottom: 8px;
                    border-radius: 4px;
                    border-left: 3px solid;
                }

                .timing-warning-item:last-child {
                    margin-bottom: 0;
                }

                .timing-warning-item.severity-high {
                    border-left-color: #ef4444;
                    background: rgba(127, 29, 29, 0.2);
                }

                .timing-warning-item.severity-medium {
                    border-left-color: #f59e0b;
                    background: rgba(120, 53, 15, 0.2);
                }

                .timing-warning-item.severity-low {
                    border-left-color: #3b82f6;
                    background: rgba(30, 58, 138, 0.2);
                }

                .timing-warning-header {
                    font-weight: 600;
                    color: #fdba74;
                    margin-bottom: 4px;
                    font-size: 11px;
                }

                .timing-warning-type {
                    display: inline-block;
                }

                .timing-warning-message {
                    color: #f1f5f9;
                    font-size: 11px;
                    line-height: 1.5;
                }

                .timing-warning-recommendation {
                    margin-top: 6px;
                    padding: 6px;
                    background: rgba(16, 185, 129, 0.1);
                    border-left: 2px solid #10b981;
                    border-radius: 3px;
                    font-size: 10px;
                    color: #86efac;
                }

                .timing-metadata {
                    color: #94a3b8;
                    font-size: 11px;
                    margin-left: 8px;
                }

                .query-info {
                    background: rgba(168, 85, 247, 0.1);
                    color: #c084fc;
                    padding: 1px 4px;
                    border-radius: 3px;
                    font-size: 10px;
                    margin-left: 4px;
                }

                .memory-info {
                    background: rgba(59, 130, 246, 0.1);
                    color: #93c5fd;
                    padding: 1px 4px;
                    border-radius: 3px;
                    font-size: 10px;
                    margin-left: 4px;
                }

                .perf-warning-details {
                    margin-top: 4px;
                    padding: 8px;
                    background: rgba(239, 68, 68, 0.1);
                    border-left: 3px solid #ef4444;
                    border-radius: 3px;
                    font-size: 11px;
                    color: #fca5a5;
                }

                .perf-warning-title {
                    font-weight: 600;
                    margin-bottom: 4px;
                }

                .perf-warning-message {
                    opacity: 0.9;
                }

                /* Warnings Summary Styles */
                .warnings-summary {
                    background: rgba(15, 23, 42, 0.5);
                    border: 2px solid rgba(251, 146, 60, 0.3);
                    border-radius: 8px;
                    margin: 12px 8px;
                    overflow: hidden;
                }

                .warnings-header {
                    display: flex;
                    align-items: center;
                    gap: 10px;
                    padding: 12px 16px;
                    background: rgba(251, 146, 60, 0.1);
                    border-bottom: 1px solid rgba(251, 146, 60, 0.2);
                }

                .warnings-icon {
                    font-size: 20px;
                }

                .warnings-title {
                    font-weight: 600;
                    font-size: 14px;
                    color: #fdba74;
                }

                .warnings-count {
                    margin-left: auto;
                    padding: 2px 8px;
                    background: rgba(251, 146, 60, 0.2);
                    border-radius: 12px;
                    font-size: 12px;
                    font-weight: 600;
                    color: #fb923c;
                }

                .warnings-list {
                    padding: 8px;
                }

                .warning-group {
                    margin-bottom: 12px;
                }

                .warning-group:last-child {
                    margin-bottom: 0;
                }

                .warning-type-header {
                    display: flex;
                    align-items: center;
                    gap: 8px;
                    padding: 8px 12px;
                    background: rgba(30, 41, 59, 0.5);
                    border-radius: 6px;
                    margin-bottom: 6px;
                    font-size: 13px;
                    font-weight: 600;
                }

                .warning-type-name {
                    color: #f1f5f9;
                }

                .warning-type-count {
                    margin-left: auto;
                    padding: 2px 6px;
                    background: rgba(148, 163, 184, 0.2);
                    border-radius: 10px;
                    font-size: 11px;
                    color: #94a3b8;
                }

                .warning-item {
                    padding: 8px 12px;
                    margin-bottom: 4px;
                    border-left: 3px solid;
                    background: rgba(30, 41, 59, 0.3);
                    border-radius: 4px;
                    font-size: 12px;
                    line-height: 1.6;
                }

                .warning-item.severity-high {
                    border-left-color: #ef4444;
                    background: rgba(127, 29, 29, 0.2);
                }

                .warning-item.severity-medium {
                    border-left-color: #f59e0b;
                    background: rgba(120, 53, 15, 0.2);
                }

                .warning-item.severity-low {
                    border-left-color: #3b82f6;
                    background: rgba(30, 58, 138, 0.2);
                }

                .warning-message {
                    color: #f1f5f9;
                    margin-bottom: 4px;
                    font-weight: 500;
                }

                .warning-source {
                    font-size: 11px;
                    color: #94a3b8;
                    font-style: italic;
                    margin-bottom: 6px;
                }

                .warning-details {
                    display: flex;
                    flex-wrap: wrap;
                    gap: 8px;
                    margin-top: 6px;
                }

                .warning-detail {
                    padding: 2px 8px;
                    background: rgba(148, 163, 184, 0.15);
                    border-radius: 10px;
                    font-size: 11px;
                    color: #cbd5e1;
                }

                .warning-recommendation {
                    width: 100%;
                    padding: 6px 10px;
                    background: rgba(16, 185, 129, 0.1);
                    border-left: 2px solid #10b981;
                    border-radius: 3px;
                    font-size: 11px;
                    color: #86efac;
                    margin-top: 6px;
                }

                /* Enhanced recommendations list styles */
                .recommendations-list {
                    margin-top: 10px;
                    padding: 10px;
                    background: rgba(15, 23, 42, 0.6);
                    border-radius: 6px;
                    border: 1px solid rgba(59, 130, 246, 0.2);
                }

                .recommendations-header {
                    font-size: 12px;
                    font-weight: 600;
                    color: #e2e8f0;
                    margin-bottom: 10px;
                    padding-bottom: 6px;
                    border-bottom: 1px solid rgba(148, 163, 184, 0.2);
                }

                .recommendation-item {
                    padding: 10px;
                    margin-bottom: 8px;
                    border-radius: 4px;
                    background: rgba(30, 41, 59, 0.5);
                    border-left: 3px solid #64748b;
                }

                .recommendation-item:last-child {
                    margin-bottom: 0;
                }

                .recommendation-item.priority-high {
                    border-left-color: #f59e0b;
                    background: rgba(245, 158, 11, 0.08);
                }

                .recommendation-item.priority-medium {
                    border-left-color: #3b82f6;
                    background: rgba(59, 130, 246, 0.08);
                }

                .recommendation-item.priority-low {
                    border-left-color: #10b981;
                    background: rgba(16, 185, 129, 0.08);
                }

                .recommendation-title {
                    font-size: 12px;
                    font-weight: 600;
                    color: #f1f5f9;
                    margin-bottom: 4px;
                }

                .recommendation-description {
                    font-size: 11px;
                    color: #cbd5e1;
                    line-height: 1.5;
                    margin-bottom: 6px;
                }

                .recommendation-code {
                    background: rgba(15, 23, 42, 0.8);
                    padding: 8px 10px;
                    border-radius: 4px;
                    font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
                    font-size: 11px;
                    color: #a5f3fc;
                    overflow-x: auto;
                    white-space: pre-wrap;
                    word-break: break-all;
                }

                .warning-docs-link,
                .timing-warning-docs-link {
                    display: inline-block;
                    margin-top: 8px;
                    padding: 4px 10px;
                    background: rgba(59, 130, 246, 0.15);
                    border: 1px solid rgba(59, 130, 246, 0.3);
                    border-radius: 4px;
                    color: #93c5fd;
                    font-size: 11px;
                    text-decoration: none;
                    transition: all 0.2s ease;
                }

                .warning-docs-link:hover,
                .timing-warning-docs-link:hover {
                    background: rgba(59, 130, 246, 0.25);
                    border-color: rgba(59, 130, 246, 0.5);
                    color: #bfdbfe;
                }

                .warning-more {
                    padding: 6px 12px;
                    text-align: center;
                    font-size: 11px;
                    color: #64748b;
                    font-style: italic;
                }

                /* Performance Metrics Styles */
                .performance-metrics {
                    background: rgba(15, 23, 42, 0.5);
                    border: 2px solid rgba(59, 130, 246, 0.3);
                    border-radius: 8px;
                    margin: 12px 8px;
                    overflow: hidden;
                }

                .metrics-header {
                    display: flex;
                    align-items: center;
                    gap: 10px;
                    padding: 12px 16px;
                    background: rgba(59, 130, 246, 0.1);
                    border-bottom: 1px solid rgba(59, 130, 246, 0.2);
                }

                .metrics-icon {
                    font-size: 18px;
                }

                .metrics-title {
                    font-weight: 600;
                    font-size: 14px;
                    color: #93c5fd;
                }

                .metrics-grid {
                    display: grid;
                    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                    gap: 12px;
                    padding: 12px;
                }

                .metric-card {
                    background: rgba(30, 41, 59, 0.5);
                    border: 1px solid rgba(148, 163, 184, 0.2);
                    border-radius: 6px;
                    padding: 12px;
                    transition: all 0.2s ease;
                }

                .metric-card:hover {
                    border-color: rgba(59, 130, 246, 0.4);
                    background: rgba(30, 41, 59, 0.7);
                }

                .metric-label {
                    font-size: 11px;
                    font-weight: 600;
                    color: #94a3b8;
                    text-transform: uppercase;
                    letter-spacing: 0.05em;
                    margin-bottom: 6px;
                }

                .metric-value {
                    font-size: 24px;
                    font-weight: 700;
                    color: #f1f5f9;
                    margin-bottom: 8px;
                    font-family: 'SF Mono', Monaco, monospace;
                }

                .metric-sparkline {
                    margin: 8px 0;
                    height: 20px;
                }

                .sparkline-svg {
                    width: 100%;
                    height: 20px;
                }

                .sparkline-empty {
                    font-size: 10px;
                    color: #64748b;
                    font-style: italic;
                    text-align: center;
                }

                .metric-chart {
                    margin: 8px 0;
                }

                .metric-bar {
                    width: 100%;
                    height: 8px;
                    background: rgba(148, 163, 184, 0.2);
                    border-radius: 4px;
                    overflow: hidden;
                }

                .metric-bar-fill {
                    height: 100%;
                    border-radius: 4px;
                    transition: width 0.3s ease;
                }

                .metric-bar-fill.bar-low {
                    background: linear-gradient(90deg, #10b981, #34d399);
                }

                .metric-bar-fill.bar-medium {
                    background: linear-gradient(90deg, #f59e0b, #fbbf24);
                }

                .metric-bar-fill.bar-high {
                    background: linear-gradient(90deg, #ef4444, #f87171);
                }

                .metric-details {
                    font-size: 11px;
                    color: #94a3b8;
                    margin-top: 6px;
                    line-height: 1.5;
                }

                .metric-details:last-child {
                    margin-bottom: 0;
                }

                /* WebSocket Statistics Styles (Phase 2.1) */
                .websocket-stats {
                    background: rgba(15, 23, 42, 0.5);
                    border: 2px solid rgba(59, 130, 246, 0.3);
                    border-radius: 8px;
                    margin: 8px 8px 16px 8px;
                    overflow: hidden;
                }

                .stats-header {
                    background: linear-gradient(135deg, rgba(59, 130, 246, 0.1) 0%, rgba(99, 102, 241, 0.1) 100%);
                    padding: 12px 16px;
                    border-bottom: 1px solid rgba(59, 130, 246, 0.2);
                    display: flex;
                    align-items: center;
                    gap: 8px;
                }

                .stats-icon {
                    font-size: 18px;
                }

                .stats-title {
                    font-size: 13px;
                    font-weight: 600;
                    color: #60a5fa;
                }

                .stats-grid {
                    display: grid;
                    grid-template-columns: repeat(3, 1fr);
                    gap: 1px;
                    background: rgba(59, 130, 246, 0.1);
                    padding: 1px;
                }

                .stat-item {
                    background: rgba(15, 23, 42, 0.8);
                    padding: 12px;
                    text-align: center;
                }

                .stat-label {
                    font-size: 11px;
                    color: #94a3b8;
                    margin-bottom: 4px;
                    font-weight: 500;
                }

                .stat-value {
                    font-size: 16px;
                    font-weight: 700;
                    color: #60a5fa;
                }

                .network-header-row {
                    padding: 12px 16px;
                    background: rgba(15, 23, 42, 0.5);
                    border-bottom: 1px solid rgba(59, 130, 246, 0.2);
                }

                .network-title {
                    font-size: 12px;
                    font-weight: 600;
                    color: #94a3b8;
                }

                /* Variables Tab Styles */
                .variables-container {
                    padding: 8px;
                }

                .variables-summary {
                    background: rgba(15, 23, 42, 0.5);
                    border: 2px solid rgba(139, 92, 246, 0.3);
                    border-radius: 8px;
                    margin-bottom: 16px;
                    overflow: hidden;
                }

                .summary-header {
                    display: flex;
                    align-items: center;
                    gap: 10px;
                    padding: 12px 16px;
                    background: rgba(139, 92, 246, 0.1);
                    border-bottom: 1px solid rgba(139, 92, 246, 0.2);
                }

                .summary-icon {
                    font-size: 18px;
                }

                .summary-title {
                    font-weight: 600;
                    font-size: 14px;
                    color: #c4b5fd;
                }

                .summary-count {
                    margin-left: auto;
                    padding: 2px 8px;
                    background: rgba(139, 92, 246, 0.2);
                    border-radius: 12px;
                    font-size: 12px;
                    font-weight: 600;
                    color: #a78bfa;
                }

                .summary-stats {
                    display: grid;
                    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
                    gap: 12px;
                    padding: 12px 16px;
                }

                .summary-stat {
                    text-align: center;
                }

                .stat-label {
                    font-size: 11px;
                    color: #94a3b8;
                    margin-bottom: 4px;
                    text-transform: uppercase;
                    letter-spacing: 0.05em;
                }

                .stat-value {
                    font-size: 18px;
                    font-weight: 700;
                    color: #c4b5fd;
                    font-family: 'SF Mono', Monaco, monospace;
                }

                .variables-list {
                    display: flex;
                    flex-direction: column;
                    gap: 8px;
                }

                .variable-item {
                    background: rgba(30, 41, 59, 0.5);
                    border: 1px solid rgba(148, 163, 184, 0.2);
                    border-radius: 6px;
                    overflow: hidden;
                    transition: all 0.2s ease;
                }

                .variable-item:hover {
                    border-color: rgba(139, 92, 246, 0.4);
                    background: rgba(30, 41, 59, 0.7);
                }

                .variable-item.expanded {
                    border-color: rgba(139, 92, 246, 0.5);
                }

                .variable-header {
                    display: flex;
                    align-items: center;
                    gap: 8px;
                    padding: 10px 12px;
                    cursor: pointer;
                    user-select: none;
                }

                .expand-icon {
                    font-size: 10px;
                    color: #94a3b8;
                    transition: transform 0.2s ease;
                    width: 12px;
                }

                .variable-item.expanded .expand-icon {
                    transform: rotate(90deg);
                }

                .variable-name {
                    font-weight: 600;
                    color: #c4b5fd;
                    font-family: 'SF Mono', Monaco, monospace;
                    flex: 1;
                }

                .variable-type {
                    padding: 2px 8px;
                    background: rgba(148, 163, 184, 0.15);
                    border-radius: 10px;
                    font-size: 11px;
                    color: #cbd5e1;
                    font-family: 'SF Mono', Monaco, monospace;
                }

                .variable-size {
                    padding: 2px 8px;
                    background: rgba(59, 130, 246, 0.15);
                    border-radius: 10px;
                    font-size: 11px;
                    color: #93c5fd;
                    font-weight: 600;
                }

                .variable-percentage {
                    padding: 2px 8px;
                    background: rgba(139, 92, 246, 0.15);
                    border-radius: 10px;
                    font-size: 11px;
                    color: #c4b5fd;
                    font-weight: 600;
                    min-width: 45px;
                    text-align: center;
                }

                .variable-bar {
                    width: 100%;
                    height: 4px;
                    background: rgba(148, 163, 184, 0.2);
                }

                .variable-bar-fill {
                    height: 100%;
                    transition: width 0.3s ease;
                }

                .variable-bar-fill.var-bar-low {
                    background: linear-gradient(90deg, #10b981, #34d399);
                }

                .variable-bar-fill.var-bar-medium {
                    background: linear-gradient(90deg, #f59e0b, #fbbf24);
                }

                .variable-bar-fill.var-bar-high {
                    background: linear-gradient(90deg, #ef4444, #f87171);
                }

                .variable-details {
                    padding: 12px;
                    background: rgba(15, 23, 42, 0.5);
                    border-top: 1px solid rgba(148, 163, 184, 0.2);
                }

                .variable-section {
                    margin-bottom: 12px;
                }

                .variable-section:last-child {
                    margin-bottom: 0;
                }

                .variable-section-title {
                    font-size: 11px;
                    font-weight: 600;
                    color: #94a3b8;
                    text-transform: uppercase;
                    letter-spacing: 0.05em;
                    margin-bottom: 8px;
                }

                .variable-value {
                    background: rgba(0, 0, 0, 0.3);
                    padding: 8px;
                    border-radius: 4px;
                    font-size: 12px;
                    color: #e2e8f0;
                    font-family: 'SF Mono', Monaco, monospace;
                    overflow-x: auto;
                    max-height: 200px;
                    overflow-y: auto;
                }

                .variable-stats {
                    display: flex;
                    flex-direction: column;
                    gap: 6px;
                }

                .variable-stat {
                    font-size: 12px;
                    color: #cbd5e1;
                }

                .variable-stat strong {
                    color: #f1f5f9;
                    font-weight: 600;
                }
            `;

            document.head.appendChild(panelStyle);
            document.body.appendChild(this.panel);
        }

        registerTabs() {
            // Register default tabs
            this.registerTab('events', {
                name: 'Events',
                icon: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M8 1L2 9H6L5 15L14 7H9L10 1H8Z" stroke-linejoin="round"/></svg>',
                render: () => this.renderEventsTab()
            });

            this.registerTab('network', {
                name: 'Network',
                icon: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="7"/><path d="M2 8H14M8 1C6 3 6 5 6 8C6 11 6 13 8 15M8 1C10 3 10 5 10 8C10 11 10 13 8 15"/></svg>',
                render: () => this.renderNetworkTab()
            });

            this.registerTab('patches', {
                name: 'Patches',
                icon: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M2 2L14 14M14 2L2 14"/><rect x="5" y="5" width="6" height="6" stroke-dasharray="2 2"/></svg>',
                render: () => this.renderPatchesTab()
            });

            this.registerTab('components', {
                name: 'Components',
                icon: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="2" width="5" height="5" rx="1"/><rect x="9" y="2" width="5" height="5" rx="1"/><rect x="2" y="9" width="5" height="5" rx="1"/><rect x="9" y="9" width="5" height="5" rx="1"/></svg>',
                render: () => this.renderComponentsTab()
            });

            this.registerTab('state', {
                name: 'State',
                icon: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M8 2V8L11 11M14 8C14 11.3 11.3 14 8 14C4.7 14 2 11.3 2 8C2 4.7 4.7 2 8 2C11.3 2 14 4.7 14 8Z"/></svg>',
                render: () => this.renderStateTab()
            });

            this.registerTab('handlers', {
                name: 'Handlers',
                icon: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M8 8C8 8 5 5 2 8L8 14L14 8C11 5 8 8 8 8Z"/><circle cx="8" cy="8" r="2"/></svg>',
                render: () => this.renderHandlersTab()
            });

            this.registerTab('variables', {
                name: 'Variables',
                icon: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M2 3H14V13H2V3Z"/><path d="M5 6H11M5 8H11M5 10H8"/></svg>',
                render: () => this.renderVariablesTab()
            });

            // Render tab buttons
            this.renderTabButtons();
        }

        registerTab(id, config) {
            this.tabs.set(id, config);
        }

        renderTabButtons() {
            const tabsContainer = this.panel.querySelector('.djust-tabs');
            tabsContainer.innerHTML = '';

            for (const [id, tab] of this.tabs) {
                const tabButton = document.createElement('div');
                tabButton.className = 'djust-tab';
                tabButton.dataset.tab = id;
                if (id === this.state.activeTab) {
                    tabButton.classList.add('active');
                }

                tabButton.innerHTML = `
                    <span class="djust-tab-icon">${tab.icon}</span>
                    <span class="djust-tab-name">${tab.name}</span>
                `;

                tabButton.addEventListener('click', () => this.switchTab(id));
                tabsContainer.appendChild(tabButton);
            }
        }

        switchTab(tabId) {
            this.state.activeTab = tabId;

            // Update active tab button
            this.panel.querySelectorAll('.djust-tab').forEach(tab => {
                tab.classList.toggle('active', tab.dataset.tab === tabId);
            });

            // Render tab content
            this.renderTabContent();
        }

        renderTabContent() {
            const contentContainer = this.panel.querySelector('.djust-tab-content');
            const activeTab = this.tabs.get(this.state.activeTab);

            if (activeTab && activeTab.render) {
                contentContainer.innerHTML = activeTab.render();
            }
        }

        // Tab render methods
        renderEventsTab() {
            if (this.eventHistory.length === 0) {
                return '<div class="empty-state">No events captured yet. Interact with the page to see events.</div>';
            }

            return `
                <div class="events-list">
                    ${this.eventHistory.map((event, index) => {
                        const hasDetails = event.params || event.error || event.result;
                        const paramCount = event.params ? Object.keys(event.params).length : 0;

                        return `
                            <div class="event-item ${event.error ? 'error' : ''} ${hasDetails ? 'expandable' : ''}" data-index="${index}">
                                <div class="event-header" ${hasDetails ? 'onclick="window.djustDebugPanel.toggleExpand(this)"' : ''}>
                                    ${hasDetails ? '<span class="expand-icon">▶</span>' : ''}
                                    <span class="event-name">${event.handler || event.name || 'unknown'}</span>
                                    ${event.element ? this.renderElementBadge(event.element) : ''}
                                    ${event.duration ? `<span class="event-duration">${event.duration.toFixed(1)}ms</span>` : ''}
                                    ${paramCount > 0 ? `<span class="event-param-count">${paramCount} param${paramCount === 1 ? '' : 's'}</span>` : ''}
                                    ${event.error ? '<span class="event-status">❌</span>' : ''}
                                    <span class="event-time">${this.formatTime(event.timestamp)}</span>
                                </div>
                                ${hasDetails ? `
                                    <div class="event-details" style="display: none;">
                                        ${event.element ? `
                                            <div class="event-section">
                                                <div class="event-section-title">Element:</div>
                                                <div class="element-info">
                                                    <div><strong>&lt;${event.element.tagName}&gt;</strong></div>
                                                    ${event.element.id ? `<div>ID: ${event.element.id}</div>` : ''}
                                                    ${event.element.className ? `<div>Class: ${event.element.className}</div>` : ''}
                                                    ${event.element.text ? `<div>Text: "${event.element.text}"</div>` : ''}
                                                    ${Object.keys(event.element.attributes).length > 0 ? `
                                                        <div>Attributes: ${Object.entries(event.element.attributes)
                                                            .map(([key, val]) => `${key}="${val}"`)
                                                            .join(', ')}</div>
                                                    ` : ''}
                                                </div>
                                            </div>
                                        ` : ''}
                                        ${event.params ? `
                                            <div class="event-section">
                                                <div class="event-section-title">Parameters:</div>
                                                <pre>${JSON.stringify(event.params, null, 2)}</pre>
                                            </div>
                                        ` : ''}
                                        ${event.result ? `
                                            <div class="event-section">
                                                <div class="event-section-title">Result:</div>
                                                <pre>${JSON.stringify(event.result, null, 2)}</pre>
                                            </div>
                                        ` : ''}
                                        ${event.error ? `
                                            <div class="event-section error">
                                                <div class="event-section-title">Error:</div>
                                                <div class="event-error-message">${event.error}</div>
                                            </div>
                                        ` : ''}
                                    </div>
                                ` : ''}
                            </div>
                        `;
                    }).join('')}
                </div>
            `;
        }

        renderNetworkTab() {
            // Get WebSocket stats from liveview client (Phase 2.1: WebSocket Inspector)
            const stats = window.liveview && window.liveview.stats ? window.liveview.stats : null;
            const messages = stats ? stats.messages : this.networkHistory;

            if (messages.length === 0) {
                return '<div class="empty-state">No WebSocket messages captured yet.</div>';
            }

            // Calculate uptime
            const uptime = stats && stats.connectedAt ?
                Math.floor((Date.now() - stats.connectedAt) / 1000) : 0;
            const uptimeStr = uptime > 0 ?
                `${Math.floor(uptime / 60)}m ${uptime % 60}s` : 'N/A';

            return `
                ${stats ? `
                <div class="websocket-stats">
                    <div class="stats-header">
                        <span class="stats-icon">📡</span>
                        <span class="stats-title">WebSocket Statistics</span>
                    </div>
                    <div class="stats-grid">
                        <div class="stat-item">
                            <div class="stat-label">Messages Sent</div>
                            <div class="stat-value">${stats.sent}</div>
                        </div>
                        <div class="stat-item">
                            <div class="stat-label">Messages Received</div>
                            <div class="stat-value">${stats.received}</div>
                        </div>
                        <div class="stat-item">
                            <div class="stat-label">Bytes Sent</div>
                            <div class="stat-value">${this.formatBytes(stats.sentBytes)}</div>
                        </div>
                        <div class="stat-item">
                            <div class="stat-label">Bytes Received</div>
                            <div class="stat-value">${this.formatBytes(stats.receivedBytes)}</div>
                        </div>
                        <div class="stat-item">
                            <div class="stat-label">Reconnections</div>
                            <div class="stat-value">${stats.reconnections}</div>
                        </div>
                        <div class="stat-item">
                            <div class="stat-label">Uptime</div>
                            <div class="stat-value">${uptimeStr}</div>
                        </div>
                    </div>
                </div>
                ` : ''}
                <div class="network-list">
                    <div class="network-header-row">
                        <span class="network-title">Recent Messages (${messages.length})</span>
                    </div>
                    ${messages.map((msg, index) => {
                        const hasPayload = msg.data || (msg.payload && Object.keys(msg.payload).length > 0);
                        const hasDebugInfo = msg.payload && msg.payload._debug;
                        const payload = msg.data || msg.payload;
                        const type = msg.type || (payload ? (payload.type || payload.event || 'data') : 'unknown');

                        return `
                            <div class="network-item ${msg.direction} ${hasPayload ? 'expandable' : ''}" data-index="${index}">
                                <div class="network-header" ${hasPayload ? 'onclick="window.djustDebugPanel.toggleExpand(this)"' : ''}>
                                    ${hasPayload ? '<span class="expand-icon">▶</span>' : ''}
                                    <span class="network-direction">${msg.direction === 'sent' ? '↑' : '↓'}</span>
                                    <span class="network-type">${type}</span>
                                    ${hasDebugInfo ? '<span class="network-debug">🐛</span>' : ''}
                                    <span class="network-size">${this.formatBytes(msg.size)}</span>
                                    <span class="network-time">${this.formatTime(msg.timestamp)}</span>
                                </div>
                                ${hasPayload ? `
                                    <div class="network-details" style="display: none;">
                                        <div class="network-payload">
                                            <pre>${JSON.stringify(payload, null, 2)}</pre>
                                        </div>
                                    </div>
                                ` : ''}
                            </div>
                        `;
                    }).join('')}
                </div>
            `;
        }

        renderPatchesTab() {
            if (this.patchHistory.length === 0) {
                return '<div class="empty-state">No DOM patches applied yet. Interact with the page to see patches.</div>';
            }

            // Collect all warnings from recent patches
            const recentWarnings = this.collectRecentWarnings();

            return `
                ${this.renderPerformanceMetrics()}
                ${recentWarnings.length > 0 ? this.renderWarningsSummary(recentWarnings) : ''}
                <div class="patches-list">
                    ${this.patchHistory.map((entry, index) => {
                        const hasDetails = entry.patches && entry.patches.length > 0;
                        const patchTypes = [...new Set(entry.patches.map(p => p.type || p.op || 'update'))];

                        return `
                            <div class="patch-item ${hasDetails ? 'expandable' : ''}" data-index="${index}">
                                <div class="patch-header" ${hasDetails ? 'onclick="window.djustDebugPanel.toggleExpand(this)"' : ''}>
                                    ${hasDetails ? '<span class="expand-icon">▶</span>' : ''}
                                    <span class="patch-count">${entry.count} patch${entry.count === 1 ? '' : 'es'}</span>
                                    <span class="patch-types">[${patchTypes.join(', ')}]</span>
                                    ${this.renderTimingBadges(entry.timing)}
                                    <span class="patch-time">${this.formatTime(entry.timestamp)}</span>
                                </div>
                                ${hasDetails ? `
                                    <div class="patch-details" style="display: none;">
                                        ${entry.performance ? this.renderPerformanceTree(entry.performance) : ''}
                                        ${entry.patches.map((patch, pIdx) => `
                                            <div class="patch-op">
                                                <span class="patch-index">#${pIdx + 1}</span>
                                                <span class="patch-type">${patch.type || patch.op || 'unknown'}</span>
                                                ${patch.path ? `<span class="patch-path">${patch.path}</span>` : ''}
                                                ${patch.value ? `
                                                    <div class="patch-value">
                                                        <pre>${typeof patch.value === 'string' ?
                                                            (patch.value.length > 200 ? patch.value.substring(0, 200) + '...' : patch.value) :
                                                            JSON.stringify(patch.value, null, 2)}</pre>
                                                    </div>
                                                ` : ''}
                                            </div>
                                        `).join('')}
                                    </div>
                                ` : ''}
                            </div>
                        `;
                    }).join('')}
                </div>
            `;
        }

        renderComponentsTab() {
            if (!this.components) {
                return '<div class="empty-state">No components detected. Components will appear after the view is mounted.</div>';
            }

            return `
                <div class="components-tree">
                    ${this.renderComponentNode(this.components)}
                </div>
            `;
        }

        renderComponentNode(component, level = 0) {
            if (!component) return '';

            return `
                <div class="component-node" style="padding-left: ${level * 20}px">
                    <div class="component-header">
                        <span class="component-name">${component.name || 'Unknown'}</span>
                        <span class="component-type">${component.type || 'Component'}</span>
                    </div>
                    ${component.state ? `
                        <div class="component-state" style="padding-left: ${(level + 1) * 20}px">
                            <pre>${JSON.stringify(component.state, null, 2)}</pre>
                        </div>
                    ` : ''}
                    ${component.children ? component.children.map(child =>
                        this.renderComponentNode(child, level + 1)
                    ).join('') : ''}
                </div>
            `;
        }

        renderComponentTree(components, level = 0) {
            return components.map(comp => `
                <div class="component-node" style="padding-left: ${level * 20}px">
                    <div class="component-header">
                        <span class="component-name">${comp.name}</span>
                        <span class="component-type">${comp.type}</span>
                    </div>
                    ${comp.children ? this.renderComponentTree(comp.children, level + 1) : ''}
                </div>
            `).join('');
        }

        renderStateTab() {
            if (this.stateHistory.length === 0) {
                return `
                    <div class="empty-state">
                        <p>No state changes recorded yet.</p>
                        <p style="font-size: 11px; margin-top: 10px; color: #64748b;">
                            State changes will appear here as you interact with the view.
                        </p>
                    </div>
                `;
            }

            return `
                <div class="state-timeline-container">
                    <div class="state-timeline-header">
                        <div class="state-timeline-title">
                            <span class="timeline-icon">🕐</span>
                            <span>State Timeline</span>
                            <span class="state-count">${this.stateHistory.length} change${this.stateHistory.length === 1 ? '' : 's'}</span>
                        </div>
                        <button class="clear-state-btn" onclick="window.djustDebugPanel.clearStateHistory()">
                            Clear History
                        </button>
                    </div>
                    <div class="state-timeline-list">
                        ${this.stateHistory.map((entry, index) => {
                            const prevEntry = this.stateHistory[index + 1];
                            const changes = this.computeStateDiff(prevEntry?.state, entry.state);
                            const hasChanges = changes.length > 0;
                            const isExpanded = entry._expanded || false;

                            return `
                                <div class="state-entry ${hasChanges ? 'has-changes' : ''} ${isExpanded ? 'expanded' : ''}" data-index="${index}">
                                    <div class="state-entry-header" onclick="window.djustDebugPanel.toggleStateEntry(${index})">
                                        <span class="expand-icon">▶</span>
                                        <span class="state-trigger ${entry.trigger === 'mount' ? 'trigger-mount' : 'trigger-event'}">
                                            ${entry.trigger === 'mount' ? '🚀' : '⚡'} ${entry.trigger}
                                        </span>
                                        ${entry.eventName ? `<span class="state-event-name">${this.escapeHtml(entry.eventName)}</span>` : ''}
                                        <span class="state-change-count">${changes.length} change${changes.length === 1 ? '' : 's'}</span>
                                        <span class="state-time">${this.formatTime(entry.timestamp)}</span>
                                    </div>
                                    <div class="state-entry-details" style="display: ${isExpanded ? 'block' : 'none'};">
                                        ${hasChanges ? `
                                            <div class="state-changes">
                                                <div class="state-section-title">Changes</div>
                                                ${changes.map(change => `
                                                    <div class="state-change-item ${change.type}">
                                                        <span class="change-type-badge ${change.type}">${change.type}</span>
                                                        <span class="change-key">${this.escapeHtml(change.key)}</span>
                                                        ${change.type !== 'removed' ? `
                                                            <div class="change-values">
                                                                ${change.type === 'modified' ? `
                                                                    <div class="change-before">
                                                                        <span class="change-label">Before:</span>
                                                                        <pre>${this.formatStateValue(change.before)}</pre>
                                                                    </div>
                                                                ` : ''}
                                                                <div class="change-after">
                                                                    <span class="change-label">${change.type === 'modified' ? 'After:' : 'Value:'}</span>
                                                                    <pre>${this.formatStateValue(change.after)}</pre>
                                                                </div>
                                                            </div>
                                                        ` : `
                                                            <div class="change-values">
                                                                <div class="change-before">
                                                                    <span class="change-label">Was:</span>
                                                                    <pre>${this.formatStateValue(change.before)}</pre>
                                                                </div>
                                                            </div>
                                                        `}
                                                    </div>
                                                `).join('')}
                                            </div>
                                        ` : `
                                            <div class="no-changes">No state changes detected</div>
                                        `}
                                        <div class="state-snapshot">
                                            <div class="state-section-title">Full State Snapshot</div>
                                            <pre class="state-snapshot-content">${this.formatStateValue(entry.state)}</pre>
                                        </div>
                                    </div>
                                </div>
                            `;
                        }).join('')}
                    </div>
                </div>
            `;
        }

        captureState(trigger, eventName = null, state = null) {
            // Capture current state from variables if not provided
            const currentState = state || (this.variables ? { ...this.variables } : {});
            const now = Date.now();

            // Deduplication: skip if same event was captured within 100ms
            // This prevents duplicate entries from logEvent() and processDebugInfo()
            if (this.stateHistory.length > 0) {
                const lastEntry = this.stateHistory[0];
                const timeDiff = now - lastEntry.timestamp;
                if (timeDiff < 100 && lastEntry.eventName === eventName && lastEntry.trigger === trigger) {
                    return; // Skip duplicate
                }
            }

            const entry = {
                trigger: trigger,
                eventName: eventName,
                state: this.cloneState(currentState),
                timestamp: now,
                _expanded: false
            };

            this.stateHistory.unshift(entry);

            // Keep within limit
            if (this.stateHistory.length > this.maxStateHistory) {
                this.stateHistory.pop();
            }

            // Update tab if active
            if (this.state.activeTab === 'state') {
                this.renderTabContent();
            }
        }

        cloneState(state) {
            // Deep clone the state to avoid reference issues
            try {
                return JSON.parse(JSON.stringify(state));
            } catch (e) {
                // Fallback for non-serializable values
                const clone = {};
                for (const key in state) {
                    try {
                        clone[key] = JSON.parse(JSON.stringify(state[key]));
                    } catch {
                        clone[key] = String(state[key]);
                    }
                }
                return clone;
            }
        }

        computeStateDiff(prevState, currentState) {
            const changes = [];

            if (!prevState) {
                // Initial state - all keys are "added"
                for (const key in currentState) {
                    changes.push({
                        type: 'added',
                        key: key,
                        before: undefined,
                        after: currentState[key]
                    });
                }
                return changes;
            }

            // Check for modified and removed keys
            for (const key in prevState) {
                if (!(key in currentState)) {
                    changes.push({
                        type: 'removed',
                        key: key,
                        before: prevState[key],
                        after: undefined
                    });
                } else if (JSON.stringify(prevState[key]) !== JSON.stringify(currentState[key])) {
                    changes.push({
                        type: 'modified',
                        key: key,
                        before: prevState[key],
                        after: currentState[key]
                    });
                }
            }

            // Check for added keys
            for (const key in currentState) {
                if (!(key in prevState)) {
                    changes.push({
                        type: 'added',
                        key: key,
                        before: undefined,
                        after: currentState[key]
                    });
                }
            }

            return changes;
        }

        formatStateValue(value) {
            if (value === undefined) return 'undefined';
            if (value === null) return 'null';
            try {
                const json = JSON.stringify(value, null, 2);
                // Truncate very long values
                let result = json;
                if (json.length > 500) {
                    result = json.substring(0, 500) + '\n... (truncated)';
                }
                // Escape HTML to prevent XSS
                return this.escapeHtml(result);
            } catch (e) {
                return this.escapeHtml(String(value));
            }
        }

        toggleStateEntry(index) {
            if (index >= 0 && index < this.stateHistory.length) {
                this.stateHistory[index]._expanded = !this.stateHistory[index]._expanded;
                this.renderTabContent();
            }
        }

        clearStateHistory() {
            this.stateHistory = [];
            if (this.state.activeTab === 'state') {
                this.renderTabContent();
            }
        }

        renderHandlersTab() {
            if (!this.handlers || this.handlers.length === 0) {
                return '<div class="empty-state">No event handlers detected. Handlers will appear after the view is mounted.</div>';
            }

            return `
                <div class="handlers-list">
                    ${this.handlers.map(handler => `
                        <div class="handler-item">
                            <div class="handler-header">
                                <div class="handler-name">${handler.name}</div>
                                ${handler.decorators && handler.decorators.length > 0 ? `
                                    <div class="handler-decorators">
                                        ${handler.decorators.map(d => `<span class="decorator">@${d}</span>`).join(' ')}
                                    </div>
                                ` : ''}
                            </div>
                            <div class="handler-description">${handler.description || 'No description'}</div>
                            <div class="handler-params">
                                ${handler.parameters && handler.parameters.length > 0 ?
                                    handler.parameters.map(param =>
                                        `<span class="param ${param.required ? 'required' : 'optional'}">
                                            ${param.name}: ${param.type}
                                            ${param.default !== null && param.default !== undefined ? ` = ${param.default}` : ''}
                                        </span>`
                                    ).join(', ') : 'No parameters'}
                            </div>
                            ${handler.source_file ? `
                                <div class="handler-source">
                                    ${handler.source_file}:${handler.source_line || 0}
                                </div>
                            ` : ''}
                        </div>
                    `).join('')}
                </div>
            `;
        }

        renderVariablesTab() {
            if (!this.variables) {
                return '<div class="empty-state">No public variables detected. Variables will appear after the view is mounted.</div>';
            }

            const entries = Object.entries(this.variables);
            if (entries.length === 0) {
                return '<div class="empty-state">No public variables detected.</div>';
            }

            // Calculate total size
            const totalSize = entries.reduce((sum, [_, info]) => {
                return sum + (info.size_bytes || 0);
            }, 0);

            // Sort by size (largest first)
            const sortedEntries = entries.sort((a, b) => {
                return (b[1].size_bytes || 0) - (a[1].size_bytes || 0);
            });

            return `
                <div class="variables-container">
                    <div class="variables-summary">
                        <div class="summary-header">
                            <span class="summary-icon">📊</span>
                            <span class="summary-title">Context Variables</span>
                            <span class="summary-count">${entries.length} variable${entries.length === 1 ? '' : 's'}</span>
                        </div>
                        <div class="summary-stats">
                            <div class="summary-stat">
                                <div class="stat-label">Total Size</div>
                                <div class="stat-value">${this.formatBytes(totalSize)}</div>
                            </div>
                            <div class="summary-stat">
                                <div class="stat-label">Largest Variable</div>
                                <div class="stat-value">${sortedEntries[0] ? sortedEntries[0][0] : 'N/A'}</div>
                            </div>
                        </div>
                    </div>
                    <div class="variables-list">
                        ${sortedEntries.map(([name, info], index) => {
                            const sizeBytes = info.size_bytes || 0;
                            const percentage = totalSize > 0 ? (sizeBytes / totalSize) * 100 : 0;
                            const barClass = percentage > 50 ? 'var-bar-high' : percentage > 25 ? 'var-bar-medium' : 'var-bar-low';

                            return `
                                <div class="variable-item expandable" data-index="${index}">
                                    <div class="variable-header" onclick="window.djustDebugPanel.toggleExpand(this)">
                                        <span class="expand-icon">▶</span>
                                        <span class="variable-name">${name}</span>
                                        <span class="variable-type">${info.type}</span>
                                        <span class="variable-size">${this.formatBytes(sizeBytes)}</span>
                                        <span class="variable-percentage">${percentage.toFixed(1)}%</span>
                                    </div>
                                    <div class="variable-bar">
                                        <div class="variable-bar-fill ${barClass}" style="width: ${percentage}%"></div>
                                    </div>
                                    <div class="variable-details" style="display: none;">
                                        <div class="variable-section">
                                            <div class="variable-section-title">Value Preview</div>
                                            <pre class="variable-value">${info.value}</pre>
                                        </div>
                                        <div class="variable-section">
                                            <div class="variable-section-title">Stats</div>
                                            <div class="variable-stats">
                                                <div class="variable-stat">Type: <strong>${info.type}</strong></div>
                                                <div class="variable-stat">Size: <strong>${this.formatBytes(sizeBytes)}</strong></div>
                                                <div class="variable-stat">% of Total: <strong>${percentage.toFixed(2)}%</strong></div>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            `;
                        }).join('')}
                    </div>
                </div>
            `;
        }

        toggleExpand(headerElement) {
            const item = headerElement.parentElement;
            const details = item.querySelector('.variable-details');
            const icon = headerElement.querySelector('.expand-icon');

            if (details.style.display === 'none') {
                details.style.display = 'block';
                icon.textContent = '▼';
                item.classList.add('expanded');
            } else {
                details.style.display = 'none';
                icon.textContent = '▶';
                item.classList.remove('expanded');
            }
        }

        // Event capture methods
        captureEvent(event) {
            this.eventHistory.unshift({
                ...event,
                timestamp: Date.now()
            });

            if (this.eventHistory.length > this.config.maxHistory) {
                this.eventHistory.pop();
            }

            // Update counter
            this.updateCounter('event-count', this.eventHistory.length);

            // Show activity
            this.showActivity();

            // If error, update badge
            if (event.error) {
                this.errorCount++;
                this.updateErrorBadge();
            }

            // Update content if events tab is active
            if (this.state.activeTab === 'events') {
                this.renderTabContent();
            }
        }

        capturePatch(patch) {
            this.patchHistory.unshift({
                ...patch,
                timestamp: Date.now()
            });

            if (this.patchHistory.length > this.config.maxPatchHistory) {
                this.patchHistory.pop();
            }

            this.updateCounter('patch-count', this.patchHistory.length);
            this.showActivity();
        }

        captureNetworkMessage(message) {
            this.networkHistory.unshift({
                ...message,
                timestamp: Date.now()
            });

            if (this.networkHistory.length > this.config.maxHistory) {
                this.networkHistory.pop();
            }

            // Update content if network tab is active
            if (this.state.activeTab === 'network') {
                this.renderTabContent();
            }
        }

        // Public methods for client-dev.js integration
        logEvent(eventName, params, result, duration = 0, elementInfo = null) {
            const event = {
                name: eventName,
                params: params,
                timestamp: Date.now(),
                duration: duration,
                error: null,
                result: null,
                element: elementInfo
            };

            // Check if result contains an error
            if (result && typeof result === 'object' && result.type === 'error') {
                event.error = result.error;
            } else {
                event.result = result;
            }

            this.eventHistory.unshift(event);

            // Capture state after event (if variables available)
            // This provides state timeline tracking tied to events
            if (this.variables && !event.error) {
                this.captureState('event', eventName, this.variables);
            }

            // Update error count
            if (event.error) {
                this.errorCount++;
                this.updateErrorBadge();
            }

            // Limit history
            if (this.eventHistory.length > this.config.maxHistory) {
                this.eventHistory.pop();
            }

            // Update UI if events tab is active
            if (this.state.activeTab === 'events') {
                this.renderTabContent();
            }

            this.showActivity();
        }

        logPatches(patches, duration = 0, performance = null) {
            // Handle timing object with server and client breakdown
            let timing = {};
            let totalDuration = 0;

            if (typeof duration === 'object') {
                timing = duration;
                // Calculate total including server and client time
                totalDuration = (timing.total || 0) + (timing.client || 0);
            } else {
                // Legacy: just client duration
                timing = { client: duration };
                totalDuration = duration;
            }

            const patchEntry = {
                timestamp: Date.now(),
                count: patches.length,
                performance: performance,  // Store comprehensive performance data
                patches: patches,
                timing: timing,
                totalDuration: totalDuration
            };

            this.patchHistory.unshift(patchEntry);

            // Update warning count
            if (performance && performance.timing) {
                const warnings = [];
                this.collectWarningsFromNode(performance.timing, warnings, 0);
                if (warnings.length > 0) {
                    this.warningCount += warnings.length;
                    this.updateCounter('warning-count', this.warningCount);
                }
            }

            // Track memory and context metrics
            this.trackMetrics(performance);

            // Limit history
            if (this.patchHistory.length > (this.config.maxPatchHistory || 50)) {
                this.patchHistory.pop();
            }

            // Update counter
            this.updateCounter('patches', this.patchHistory.length);

            // Update UI if patches tab is active
            if (this.state.activeTab === 'patches') {
                this.renderTabContent();
            }

            this.showActivity();
        }

        logNetwork(message, type = 'unknown') {
            this.captureNetworkMessage({
                ...message,
                _type: type
            });

            // Update counter
            this.updateCounter('network', this.networkHistory.length);

            this.showActivity();
        }

        // Hook into LiveView
        hookIntoLiveView() {
            // Intercept WebSocket messages
            if (window.WebSocket) {
                const originalSend = WebSocket.prototype.send;
                const self = this;

                WebSocket.prototype.send = function(data) {
                    self.captureNetworkMessage({
                        direction: 'sent',
                        type: self.detectMessageType(data),
                        size: new Blob([data]).size,
                        payload: self.parsePayload(data)
                    });

                    return originalSend.call(this, data);
                };

                // Also hook into message receive
                const originalAddEventListener = WebSocket.prototype.addEventListener;
                WebSocket.prototype.addEventListener = function(type, listener) {
                    if (type === 'message') {
                        const wrappedListener = function(event) {
                            const payload = self.parsePayload(event.data);

                            self.captureNetworkMessage({
                                direction: 'received',
                                type: self.detectMessageType(event.data),
                                size: new Blob([event.data]).size,
                                payload: payload
                            });

                            // Process debug information if present
                            if (payload && payload._debug) {
                                self.processDebugInfo(payload._debug);
                            }

                            return listener.call(this, event);
                        };
                        return originalAddEventListener.call(this, type, wrappedListener);
                    }
                    return originalAddEventListener.call(this, type, listener);
                };
            }

            // Hook into LiveView event handling
            if (window.liveView) {
                const originalHandleEvent = window.liveView.handleEvent;
                window.liveView.handleEvent = (event) => {
                    const startTime = performance.now();

                    try {
                        const result = originalHandleEvent.call(window.liveView, event);

                        this.captureEvent({
                            type: 'event',
                            handler: event.handler || event.type,
                            params: event.params,
                            duration: performance.now() - startTime
                        });

                        return result;
                    } catch (error) {
                        this.captureEvent({
                            type: 'event',
                            handler: event.handler || event.type,
                            params: event.params,
                            duration: performance.now() - startTime,
                            error: error.message
                        });
                        throw error;
                    }
                };
            }
        }

        // Process debug information from server
        processDebugInfo(debugInfo) {
            if (!debugInfo) return;

            // Update handlers
            if (debugInfo.handlers) {
                this.handlers = debugInfo.handlers;
                if (this.state.activeTab === 'handlers') {
                    this.renderTabContent();
                }
            }

            // Update components
            if (debugInfo.components) {
                this.components = debugInfo.components;
                if (this.state.activeTab === 'components') {
                    this.renderTabContent();
                }
            }

            // Update variables and capture state change
            if (debugInfo.variables) {
                const isMount = this.stateHistory.length === 0 || debugInfo._isMounted;
                const trigger = isMount ? 'mount' : 'update';
                const eventName = debugInfo._eventName || null;

                // Capture state before updating variables
                this.captureState(trigger, eventName, debugInfo.variables);

                this.variables = debugInfo.variables;
                if (this.state.activeTab === 'variables') {
                    this.renderTabContent();
                }
            }

            // Update performance metrics
            if (debugInfo.performance) {
                this.performance = debugInfo.performance;
                // Update footer stats
                this.updateStats(debugInfo.performance);
            }

            // Update view info
            if (debugInfo.view_name) {
                this.viewInfo = {
                    name: debugInfo.view_name,
                    module: debugInfo.view_module
                };
            }
        }

        updateStats(performance) {
            const footer = this.panel.querySelector('.djust-panel-footer');
            if (!footer) return;

            const stats = footer.querySelector('.djust-stats');
            if (stats && performance) {
                stats.innerHTML = `
                    <div class="djust-stat">Events:<span>${performance.event_count || 0}</span></div>
                    <div class="djust-stat">Patches:<span>${performance.patch_count || 0}</span></div>
                    <div class="djust-stat">Renders:<span>${performance.render_count || 0}</span></div>
                    <div class="djust-stat">Render Time:<span>${performance.render_time ? performance.render_time.toFixed(2) + 'ms' : 'N/A'}</span></div>
                `;
            }
        }

        // Utility methods
        showActivity() {
            this.button.classList.add('active');
            setTimeout(() => this.button.classList.remove('active'), 500);
        }

        updateErrorBadge() {
            const badge = this.button.querySelector('.error-badge');
            if (this.errorCount > 0) {
                badge.textContent = this.errorCount > 99 ? '99+' : this.errorCount;
                badge.style.display = 'block';
                this.button.classList.add('error');
            } else {
                badge.style.display = 'none';
                this.button.classList.remove('error');
            }

            this.updateCounter('error-count', this.errorCount);
        }

        updateCounter(id, count) {
            const counter = document.getElementById(id);
            if (counter) {
                counter.textContent = count;
            }
        }

        detectMessageType(data) {
            try {
                const parsed = JSON.parse(data);
                return parsed.type || 'unknown';
            } catch {
                return 'binary';
            }
        }

        parsePayload(data) {
            try {
                return JSON.parse(data);
            } catch {
                return data;
            }
        }

        trackMetrics(performance) {
            if (!performance) return;

            const metrics = {
                timestamp: Date.now(),
                memory_mb: null,
                context_bytes: null
            };

            // Extract memory usage from timing tree
            if (performance.timing && performance.timing.metadata && performance.timing.metadata.memory) {
                metrics.memory_mb = performance.timing.metadata.memory.current_mb;
            }

            // Extract context size
            if (performance.context_size_bytes) {
                metrics.context_bytes = performance.context_size_bytes;
                this.totalContextSize += performance.context_size_bytes;
                this.contextSizeCount++;
            }

            // Add to history
            if (metrics.memory_mb !== null || metrics.context_bytes !== null) {
                this.memoryHistory.unshift(metrics);

                // Limit history
                if (this.memoryHistory.length > this.maxMemoryHistoryLength) {
                    this.memoryHistory.pop();
                }
            }
        }

        renderPerformanceMetrics() {
            if (this.memoryHistory.length === 0 && this.networkHistory.length === 0) {
                return '';
            }

            // Calculate stats
            const memoryStats = this.calculateMemoryStats();
            const contextStats = this.calculateContextStats();
            const networkStats = this.calculateNetworkStats();

            return `
                <div class="performance-metrics">
                    <div class="metrics-header">
                        <span class="metrics-icon">📊</span>
                        <span class="metrics-title">Performance Metrics</span>
                    </div>
                    <div class="metrics-grid">
                        ${memoryStats.hasData ? `
                            <div class="metric-card">
                                <div class="metric-label">Memory Usage</div>
                                <div class="metric-value">${memoryStats.current.toFixed(1)} MB</div>
                                <div class="metric-sparkline">${this.renderMemorySparkline()}</div>
                                <div class="metric-details">
                                    Peak: ${memoryStats.peak.toFixed(1)} MB
                                    · Avg: ${memoryStats.average.toFixed(1)} MB
                                </div>
                            </div>
                        ` : ''}
                        ${contextStats.hasData ? `
                            <div class="metric-card">
                                <div class="metric-label">Context Size</div>
                                <div class="metric-value">${this.formatBytes(contextStats.current)}</div>
                                <div class="metric-chart">${this.renderContextBar(contextStats.current, contextStats.max)}</div>
                                <div class="metric-details">
                                    Max: ${this.formatBytes(contextStats.max)}
                                    · Avg: ${this.formatBytes(contextStats.average)}
                                </div>
                            </div>
                        ` : ''}
                        ${networkStats.hasData ? `
                            <div class="metric-card">
                                <div class="metric-label">WebSocket Traffic</div>
                                <div class="metric-value">${this.formatBytes(networkStats.totalBytes)}</div>
                                <div class="metric-details">
                                    ↑ Sent: ${this.formatBytes(networkStats.sentBytes)}
                                    · ↓ Received: ${this.formatBytes(networkStats.receivedBytes)}
                                </div>
                                <div class="metric-details">
                                    Messages: ${networkStats.totalMessages}
                                    · Avg: ${this.formatBytes(networkStats.avgMessageSize)}
                                </div>
                            </div>
                        ` : ''}
                        <div class="metric-card">
                            <div class="metric-label">Patch Performance</div>
                            <div class="metric-value">${this.patchHistory.length} patches</div>
                            <div class="metric-details">
                                Avg time: ${this.calculateAvgPatchTime().toFixed(1)}ms
                                · Warnings: ${this.warningCount}
                            </div>
                        </div>
                    </div>
                </div>
            `;
        }

        calculateMemoryStats() {
            const memoryData = this.memoryHistory
                .map(m => m.memory_mb)
                .filter(m => m !== null);

            if (memoryData.length === 0) {
                return { hasData: false };
            }

            return {
                hasData: true,
                current: memoryData[0] || 0,
                peak: Math.max(...memoryData),
                average: memoryData.reduce((a, b) => a + b, 0) / memoryData.length
            };
        }

        calculateContextStats() {
            const contextData = this.memoryHistory
                .map(m => m.context_bytes)
                .filter(c => c !== null);

            if (contextData.length === 0) {
                return { hasData: false };
            }

            return {
                hasData: true,
                current: contextData[0] || 0,
                max: Math.max(...contextData),
                average: this.totalContextSize / this.contextSizeCount
            };
        }

        calculateNetworkStats() {
            if (this.networkHistory.length === 0) {
                return { hasData: false };
            }

            let sentBytes = 0;
            let receivedBytes = 0;
            let totalMessages = this.networkHistory.length;

            this.networkHistory.forEach(msg => {
                if (msg.direction === 'sent') {
                    sentBytes += msg.size || 0;
                } else {
                    receivedBytes += msg.size || 0;
                }
            });

            const totalBytes = sentBytes + receivedBytes;
            const avgMessageSize = totalBytes / totalMessages;

            return {
                hasData: true,
                totalBytes,
                sentBytes,
                receivedBytes,
                totalMessages,
                avgMessageSize
            };
        }

        calculateAvgPatchTime() {
            if (this.patchHistory.length === 0) return 0;

            const total = this.patchHistory.reduce((sum, entry) => {
                return sum + (entry.totalDuration || 0);
            }, 0);

            return total / this.patchHistory.length;
        }

        renderMemorySparkline() {
            const memoryData = this.memoryHistory
                .map(m => m.memory_mb)
                .filter(m => m !== null)
                .slice(0, 20)  // Last 20 data points
                .reverse();     // Oldest to newest

            if (memoryData.length < 2) {
                return '<div class="sparkline-empty">Not enough data</div>';
            }

            const max = Math.max(...memoryData);
            const min = Math.min(...memoryData);
            const range = max - min || 1;

            // Create simple SVG sparkline
            const width = 100;
            const height = 20;
            const step = width / (memoryData.length - 1);

            const points = memoryData.map((value, index) => {
                const x = index * step;
                const y = height - ((value - min) / range) * height;
                return `${x},${y}`;
            }).join(' ');

            return `
                <svg width="${width}" height="${height}" class="sparkline-svg">
                    <polyline
                        points="${points}"
                        fill="none"
                        stroke="#60a5fa"
                        stroke-width="1.5"
                    />
                </svg>
            `;
        }

        renderContextBar(current, max) {
            const percentage = max > 0 ? (current / max) * 100 : 0;
            const colorClass = percentage > 80 ? 'bar-high' : percentage > 50 ? 'bar-medium' : 'bar-low';

            return `
                <div class="metric-bar">
                    <div class="metric-bar-fill ${colorClass}" style="width: ${percentage}%"></div>
                </div>
            `;
        }

        collectRecentWarnings() {
            const warnings = [];
            const recentPatches = this.patchHistory.slice(0, 10); // Last 10 patches

            recentPatches.forEach((entry, entryIdx) => {
                if (entry.performance && entry.performance.timing) {
                    this.collectWarningsFromNode(entry.performance.timing, warnings, entryIdx);
                }
            });

            return warnings;
        }

        collectWarningsFromNode(node, warnings, entryIdx) {
            if (node.warnings && node.warnings.length > 0) {
                node.warnings.forEach(warning => {
                    warnings.push({
                        ...warning,
                        node: node.name,
                        entryIdx: entryIdx,
                        timestamp: this.patchHistory[entryIdx].timestamp
                    });
                });
            }

            if (node.children) {
                node.children.forEach(child => {
                    this.collectWarningsFromNode(child, warnings, entryIdx);
                });
            }
        }

        renderWarningsSummary(warnings) {
            // Group warnings by type
            const grouped = {};
            warnings.forEach(w => {
                const type = w.type || 'unknown';
                if (!grouped[type]) {
                    grouped[type] = [];
                }
                grouped[type].push(w);
            });

            return `
                <div class="warnings-summary">
                    <div class="warnings-header">
                        <span class="warnings-icon">⚠️</span>
                        <span class="warnings-title">Performance Warnings</span>
                        <span class="warnings-count">${warnings.length} issue${warnings.length === 1 ? '' : 's'}</span>
                    </div>
                    <div class="warnings-list">
                        ${Object.entries(grouped).map(([type, items]) => `
                            <div class="warning-group">
                                <div class="warning-type-header">
                                    ${this.getWarningIcon(type)}
                                    <span class="warning-type-name">${this.formatWarningType(type)}</span>
                                    <span class="warning-type-count">${items.length}</span>
                                </div>
                                ${items.slice(0, 3).map(warning => `
                                    <div class="warning-item ${this.getWarningSeverity(type)}">
                                        <div class="warning-message">${warning.message}</div>
                                        ${warning.node ? `<div class="warning-source">in ${warning.node}</div>` : ''}
                                        ${this.renderWarningDetails(warning)}
                                    </div>
                                `).join('')}
                                ${items.length > 3 ? `
                                    <div class="warning-more">+ ${items.length - 3} more ${this.formatWarningType(type)} warnings</div>
                                ` : ''}
                            </div>
                        `).join('')}
                    </div>
                </div>
            `;
        }

        getWarningIcon(type) {
            const icons = {
                'n_plus_one': '🔄',
                'slow_queries': '🐌',
                'slow_handler': '⏱️',
                'slow_template': '📄',
                'excessive_patches': '📦',
                'memory_usage': '💾',
                'missing_limit': '⚡'
            };
            return icons[type] || '⚠️';
        }

        formatWarningType(type) {
            const names = {
                'n_plus_one': 'N+1 Queries',
                'slow_queries': 'Slow Queries',
                'slow_handler': 'Slow Handlers',
                'slow_template': 'Slow Templates',
                'excessive_patches': 'Excessive Patches',
                'memory_usage': 'Memory Issues',
                'missing_limit': 'Missing LIMIT Clauses'
            };
            return names[type] || type.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
        }

        getWarningSeverity(type) {
            const severity = {
                'n_plus_one': 'severity-high',
                'slow_queries': 'severity-high',
                'slow_handler': 'severity-medium',
                'slow_template': 'severity-medium',
                'excessive_patches': 'severity-low',
                'memory_usage': 'severity-high',
                'missing_limit': 'severity-medium'
            };
            return severity[type] || 'severity-low';
        }

        renderWarningDetails(warning) {
            let details = [];

            if (warning.query_count) {
                details.push(`<span class="warning-detail">Queries: ${warning.query_count}</span>`);
            }

            if (warning.patch_count) {
                details.push(`<span class="warning-detail">Patches: ${warning.patch_count}</span>`);
            }

            if (warning.threshold) {
                details.push(`<span class="warning-detail">Threshold: ${warning.threshold}ms</span>`);
            }

            // Handle legacy single recommendation
            if (warning.recommendation) {
                details.push(`<span class="warning-recommendation">💡 ${warning.recommendation}</span>`);
            }

            // Handle new recommendations array with detailed info
            if (warning.recommendations && Array.isArray(warning.recommendations)) {
                details.push(this.renderRecommendationsList(warning.recommendations));
            }

            // Add docs link if available
            if (warning.docs_url) {
                details.push(`<a href="${warning.docs_url}" target="_blank" class="warning-docs-link">📖 View documentation</a>`);
            }

            return details.length > 0 ? `<div class="warning-details">${details.join('')}</div>` : '';
        }

        renderRecommendationsList(recommendations) {
            if (!recommendations || recommendations.length === 0) {
                return '';
            }

            const sortedRecs = [...recommendations].sort((a, b) => {
                const priorityOrder = { high: 0, medium: 1, low: 2 };
                return (priorityOrder[a.priority] || 2) - (priorityOrder[b.priority] || 2);
            });

            let html = '<div class="recommendations-list">';
            html += '<div class="recommendations-header">💡 Recommendations:</div>';

            sortedRecs.forEach((rec, idx) => {
                const priorityClass = `priority-${rec.priority || 'medium'}`;
                html += `<div class="recommendation-item ${priorityClass}">`;
                html += `<div class="recommendation-title">${idx + 1}. ${rec.title || 'Suggestion'}</div>`;

                if (rec.description) {
                    html += `<div class="recommendation-description">${rec.description}</div>`;
                }

                if (rec.code_example) {
                    html += `<div class="recommendation-code"><code>${this.escapeHtml(rec.code_example)}</code></div>`;
                }

                html += '</div>';
            });

            html += '</div>';
            return html;
        }

        escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        renderPerformanceTree(performance) {
            if (!performance || !performance.timing) {
                return '';
            }

            const renderNode = (node, depth = 0) => {
                const indent = '│  '.repeat(depth);
                const isLast = false; // Can be enhanced to track last child
                const prefix = depth === 0 ? '' : isLast ? '└─ ' : '├─ ';

                let html = '<div class="timing-node" style="margin-left: ' + (depth * 20) + 'px;">';

                // Node header with timing
                html += '<div class="timing-node-header">';
                html += '<span class="timing-node-prefix">' + prefix + '</span>';
                html += '<span class="timing-node-name">' + node.name + '</span>';

                if (node.duration_ms) {
                    let colorClass = 'timing-fast';
                    if (node.duration_ms > 100) colorClass = 'timing-slow';
                    else if (node.duration_ms > 50) colorClass = 'timing-medium';

                    html += '<span class="timing-duration ' + colorClass + '">' +
                            node.duration_ms.toFixed(1) + 'ms</span>';
                }

                // Add warnings if present
                if (node.warnings && node.warnings.length > 0) {
                    html += '<span class="timing-warning-icon" title="Click to see warnings">⚠️</span>';
                }

                html += '</div>';

                // Render warnings details if present
                if (node.warnings && node.warnings.length > 0) {
                    html += '<div class="timing-warnings-details">';
                    node.warnings.forEach(warning => {
                        const severityClass = this.getWarningSeverity(warning.type || 'unknown');
                        html += '<div class="timing-warning-item ' + severityClass + '">';
                        html += '<div class="timing-warning-header">';
                        html += '<span class="timing-warning-type">' + this.getWarningIcon(warning.type) + ' ';
                        html += this.formatWarningType(warning.type) + '</span>';
                        html += '</div>';
                        html += '<div class="timing-warning-message">' + warning.message + '</div>';

                        // Handle legacy single recommendation
                        if (warning.recommendation) {
                            html += '<div class="timing-warning-recommendation">💡 ' + warning.recommendation + '</div>';
                        }

                        // Handle new recommendations array with detailed info
                        if (warning.recommendations && Array.isArray(warning.recommendations)) {
                            html += this.renderRecommendationsList(warning.recommendations);
                        }

                        // Add docs link if available
                        if (warning.docs_url) {
                            html += '<a href="' + warning.docs_url + '" target="_blank" class="timing-warning-docs-link">📖 View documentation</a>';
                        }

                        html += '</div>';
                    });
                    html += '</div>';
                }

                // Metadata (queries, memory, etc.)
                if (node.metadata) {
                    if (node.metadata.query_count) {
                        html += '<div class="timing-metadata">';
                        html += '<span class="metadata-label">Queries:</span> ';
                        html += '<span class="metadata-value">' + node.metadata.query_count + '</span>';
                        html += ' <span class="metadata-detail">(' +
                                (node.metadata.query_time_ms || 0).toFixed(1) + 'ms)</span>';
                        html += '</div>';
                    }

                    if (node.metadata.memory) {
                        const mem = node.metadata.memory;
                        html += '<div class="timing-metadata">';
                        html += '<span class="metadata-label">Memory:</span> ';
                        html += '<span class="metadata-value">+' + mem.delta_mb + 'MB</span>';
                        html += '</div>';
                    }
                }

                // Render children
                if (node.children && node.children.length > 0) {
                    html += '<div class="timing-children">';
                    node.children.forEach(child => {
                        html += renderNode(child, depth + 1);
                    });
                    html += '</div>';
                }

                html += '</div>';
                return html;
            };

            return '<div class="performance-tree">' + renderNode(performance.timing) + '</div>';
        }

        formatTime(timestamp) {
            const date = new Date(timestamp);
            return date.toLocaleTimeString('en-US', {
                hour12: false,
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
                fractionalSecondDigits: 3
            });
        }

        escapeHtml(str) {
            if (str === null || str === undefined) return '';
            const text = String(str);
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        renderElementBadge(element) {
            if (!element) return '';

            let label = `<${element.tagName}>`;
            if (element.id) {
                label = `#${element.id}`;
            } else if (element.className) {
                // Get first class name
                const firstClass = element.className.split(' ')[0];
                label = `.${firstClass}`;
            }

            const tooltip = [];
            tooltip.push(`<${element.tagName}>`);
            if (element.id) tooltip.push(`id="${element.id}"`);
            if (element.className) tooltip.push(`class="${element.className}"`);
            if (element.text) tooltip.push(`text="${element.text.substring(0, 30)}..."`);

            return `<span class="element-badge" title="${tooltip.join(' ')}">${label}</span>`;
        }

        renderTimingBadges(timing) {
            if (!timing) return '';

            const badges = [];

            // Server handler time
            if (timing.handler !== undefined) {
                badges.push(`<span class="timing-badge server-handler" title="Python handler execution">Handler: ${timing.handler.toFixed(1)}ms</span>`);
            }

            // Server render time
            if (timing.render !== undefined) {
                badges.push(`<span class="timing-badge server-render" title="Rust VDOM render">Render: ${timing.render.toFixed(1)}ms</span>`);
            }

            // Server total time
            if (timing.total !== undefined) {
                badges.push(`<span class="timing-badge server-total" title="Total server processing">Server: ${timing.total.toFixed(1)}ms</span>`);
            }

            // Client DOM apply time
            if (timing.client !== undefined) {
                badges.push(`<span class="timing-badge client-apply" title="Client DOM patching">DOM: ${timing.client.toFixed(1)}ms</span>`);
            }

            // Calculate and show round-trip time if we have both server and client
            if (timing.total !== undefined && timing.client !== undefined) {
                const roundTrip = timing.total + timing.client;
                badges.push(`<span class="timing-badge round-trip" title="Total round-trip time">Total: ${roundTrip.toFixed(1)}ms</span>`);
            }

            return badges.join('');
        }

        formatBytes(bytes) {
            if (bytes < 1024) return `${bytes}B`;
            if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
            return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
        }

        toggleExpand(headerElement) {
            const item = headerElement.parentElement;
            const details = item.querySelector('.event-details, .patch-details, .network-details');
            const icon = headerElement.querySelector('.expand-icon');

            if (!details) return;

            if (details.style.display === 'none') {
                details.style.display = 'block';
                icon.textContent = '▼';
                item.classList.add('expanded');
            } else {
                details.style.display = 'none';
                icon.textContent = '▶';
                item.classList.remove('expanded');
            }
        }

        // Panel control methods
        open() {
            this.state.isOpen = true;

            // Force position within viewport
            const viewportHeight = window.innerHeight;
            const panelHeight = 400;
            const topPosition = viewportHeight - panelHeight;

            this.panel.setAttribute('style', `
                display: flex !important;
                position: fixed !important;
                top: ${topPosition}px !important;
                left: 0px !important;
                right: 0px !important;
                width: 100% !important;
                height: ${panelHeight}px !important;
                background: #0f172a !important;
                border-top: 2px solid #E57324 !important;
                color: #f1f5f9 !important;
                z-index: 999999 !important;
                box-shadow: 0 -4px 12px rgba(0, 0, 0, 0.5) !important;
            `);

            this.renderTabContent();
            this.saveState();
        }

        close() {
            this.state.isOpen = false;
            this.panel.setAttribute('style', 'display: none !important;');
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

        // State persistence
        saveState() {
            localStorage.setItem('djust-debug-state', JSON.stringify(this.state));
        }

        loadState() {
            const saved = localStorage.getItem('djust-debug-state');
            if (saved) {
                try {
                    const parsedState = JSON.parse(saved);
                    this.state = { ...this.state, ...parsedState };

                    // Restore panel visibility and active tab if saved
                    // Combined into single setTimeout to reduce queued microtasks
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
            // TODO: Implement search functionality
            console.log('[djust] Searching for:', this.state.searchQuery);
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
                            console.log('[djust] Debug session imported successfully');
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

            console.log('[djust] Debug panel destroyed');
        }
    }

    // Export DjustDebugPanel to window for manual initialization
    window.DjustDebugPanel = DjustDebugPanel;

})();
