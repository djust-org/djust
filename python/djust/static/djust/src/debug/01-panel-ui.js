
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
