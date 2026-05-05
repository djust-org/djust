
        formatBytes(bytes) {
            if (bytes === null || bytes === undefined) return 'N/A';
            if (bytes < 1024) return bytes + ' B';
            if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
            return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
        }

        renderStateSizeSection() {
            const debugInfo = window.DJUST_DEBUG_INFO;
            if (!debugInfo || !debugInfo.state_sizes) return '';

            const sizes = debugInfo.state_sizes;
            const keys = Object.keys(sizes);
            if (keys.length === 0) return '';

            const rows = keys.map(key => {
                // eslint-disable-next-line security/detect-object-injection
                const info = sizes[key];
                return `
                    <tr>
                        <td style="padding: 4px 8px; border-bottom: 1px solid #1e293b; font-family: monospace; font-size: 11px;">${this.escapeHtml(key)}</td>
                        <td style="padding: 4px 8px; border-bottom: 1px solid #1e293b; text-align: right; font-size: 11px;">${this.formatBytes(info.memory)}</td>
                        <td style="padding: 4px 8px; border-bottom: 1px solid #1e293b; text-align: right; font-size: 11px;">${this.formatBytes(info.serialized)}</td>
                    </tr>
                `;
            }).join('');

            return `
                <div class="state-size-breakdown" style="margin-bottom: 16px;">
                    <div class="state-timeline-header" style="margin-bottom: 8px;">
                        <div class="state-timeline-title">
                            <span>Size Breakdown</span>
                            <span class="state-count">${keys.length} variable${keys.length === 1 ? '' : 's'}</span>
                        </div>
                    </div>
                    <table style="width: 100%; border-collapse: collapse; font-size: 12px;">
                        <thead>
                            <tr style="color: #94a3b8; text-transform: uppercase; font-size: 10px;">
                                <th style="padding: 4px 8px; text-align: left; border-bottom: 1px solid #334155;">Variable</th>
                                <th style="padding: 4px 8px; text-align: right; border-bottom: 1px solid #334155;">Memory</th>
                                <th style="padding: 4px 8px; text-align: right; border-bottom: 1px solid #334155;">Serialized</th>
                            </tr>
                        </thead>
                        <tbody>${rows}</tbody>
                    </table>
                </div>
            `;
        }

        renderStateTab() {
            const sizeSection = this.renderStateSizeSection();
            const searchQuery = (this.state.searchQuery || '').toLowerCase();
            const filtered = searchQuery
                ? this.stateHistory.filter(entry => {
                    const trigger = (entry.trigger || '').toLowerCase();
                    const eventName = (entry.eventName || '').toLowerCase();
                    const stateStr = JSON.stringify(entry.state || {}).toLowerCase();
                    return trigger.includes(searchQuery) ||
                           eventName.includes(searchQuery) ||
                           stateStr.includes(searchQuery);
                })
                : this.stateHistory;

            if (this.stateHistory.length === 0) {
                return `
                    ${sizeSection}
                    <div class="empty-state">
                        <p>No state changes recorded yet.</p>
                        <p style="font-size: 11px; margin-top: 10px; color: #64748b;">
                            State changes will appear here as you interact with the view.
                        </p>
                    </div>
                `;
            }

            const countLabel = (searchQuery && filtered.length !== this.stateHistory.length)
                ? `${filtered.length} / ${this.stateHistory.length}`
                : `${this.stateHistory.length}`;

            return `
                ${sizeSection}
                <div class="state-timeline-container">
                    <div class="state-timeline-header">
                        <div class="state-timeline-title">
                            <span class="timeline-icon">🕐</span>
                            <span>State Timeline</span>
                            <span class="state-count">${countLabel} change${this.stateHistory.length === 1 ? '' : 's'}</span>
                        </div>
                        <button class="clear-state-btn" onclick="window.djustDebugPanel.clearStateHistory()">
                            Clear History
                        </button>
                    </div>
                    <div class="state-timeline-list">
                        ${filtered.map((entry, index) => {
                            const origIndex = this.stateHistory.indexOf(entry);
                            const prevEntry = this.stateHistory[origIndex + 1];
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
            } catch (_e) {
                // Fallback for non-serializable values
                const clone = Object.create(null); // no prototype to pollute
                const UNSAFE_KEYS = new Set(['__proto__', 'constructor', 'prototype']);
                for (const key of Object.keys(state)) {
                    if (UNSAFE_KEYS.has(key)) continue; // skip prototype-polluting keys
                    const safeKey = String(key);
                    try {
                        Object.defineProperty(clone, safeKey, {
                            // eslint-disable-next-line security/detect-object-injection
                            value: JSON.parse(JSON.stringify(state[key])),
                            writable: true, enumerable: true, configurable: true
                        });
                    } catch {
                        Object.defineProperty(clone, safeKey, {
                            // eslint-disable-next-line security/detect-object-injection
                            value: String(state[key]),
                            writable: true, enumerable: true, configurable: true
                        });
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
                        // eslint-disable-next-line security/detect-object-injection
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
                        // eslint-disable-next-line security/detect-object-injection
                        before: prevState[key],
                        after: undefined
                    });
                // eslint-disable-next-line security/detect-object-injection
                } else if (JSON.stringify(prevState[key]) !== JSON.stringify(currentState[key])) {
                    changes.push({
                        type: 'modified',
                        key: key,
                        // eslint-disable-next-line security/detect-object-injection
                        before: prevState[key],
                        // eslint-disable-next-line security/detect-object-injection
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
                        // eslint-disable-next-line security/detect-object-injection
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
            } catch (_e) {
                return this.escapeHtml(String(value));
            }
        }

        toggleStateEntry(index) {
            if (index >= 0 && index < this.stateHistory.length) {
                // eslint-disable-next-line security/detect-object-injection
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
