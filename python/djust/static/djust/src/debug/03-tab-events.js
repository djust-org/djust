
        // Tab render methods
        renderEventsTab() {
            if (this.eventHistory.length === 0) {
                return '<div class="empty-state">No events captured yet. Interact with the page to see events.</div>';
            }

            const nameFilter = (this.state.filters.eventName || '').toLowerCase();
            const statusFilter = this.state.filters.eventStatus || 'all';

            const filtered = this.eventHistory.filter(event => {
                const eventName = (event.handler || event.name || 'unknown').toLowerCase();
                if (nameFilter && !eventName.includes(nameFilter)) return false;
                if (statusFilter === 'errors' && !event.error) return false;
                if (statusFilter === 'success' && event.error) return false;
                return true;
            });

            const hasActiveFilters = nameFilter || statusFilter !== 'all';

            return `
                <div class="events-filter-bar">
                    <input type="text"
                        class="events-filter-input"
                        placeholder="Filter by event name..."
                        value="${this.escapeHtml(this.state.filters.eventName || '')}"
                        oninput="window.djustDebugPanel.onEventNameFilter(this.value)" />
                    <select class="events-filter-select"
                        onchange="window.djustDebugPanel.onEventStatusFilter(this.value)">
                        <option value="all"${statusFilter === 'all' ? ' selected' : ''}>All</option>
                        <option value="errors"${statusFilter === 'errors' ? ' selected' : ''}>Errors only</option>
                        <option value="success"${statusFilter === 'success' ? ' selected' : ''}>Success only</option>
                    </select>
                    ${hasActiveFilters ? `<button class="events-filter-clear" onclick="window.djustDebugPanel.clearEventFilters()">Clear</button>` : ''}
                    <span class="events-filter-count">${filtered.length} / ${this.eventHistory.length}</span>
                </div>
                <div class="events-list">
                    ${filtered.length === 0 ? '<div class="empty-state">No events match the current filters.</div>' :
                    filtered.map((event, index) => {
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
                                    ${(event.handler || event.name) ? `<button class="event-replay-btn" data-event-index="${this.eventHistory.indexOf(event)}" onclick="event.stopPropagation(); window.djustDebugPanel.replayEvent(${this.eventHistory.indexOf(event)}, this)" title="Replay this event">⟳</button>` : ''}
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

        onEventNameFilter(value) {
            this.state.filters.eventName = value;
            this.renderTabContent();
        }

        onEventStatusFilter(value) {
            this.state.filters.eventStatus = value;
            this.renderTabContent();
        }

        clearEventFilters() {
            this.state.filters.eventName = '';
            this.state.filters.eventStatus = 'all';
            this.renderTabContent();
        }

        replayEvent(index, btnElement) {
            const event = this.eventHistory[index];
            if (!event) return;

            const handlerName = event.handler || event.name;
            if (!handlerName) return;

            const lv = window.liveView;
            if (!lv || !lv.sendEvent) {
                this.showReplayStatus(btnElement, 'error', 'No connection');
                return;
            }

            this.showReplayStatus(btnElement, 'pending', '');
            const sent = lv.sendEvent(handlerName, event.params || {});
            if (sent) {
                this.showReplayStatus(btnElement, 'success', '');
            } else {
                this.showReplayStatus(btnElement, 'error', 'Send failed');
            }
        }

        showReplayStatus(btnElement, status, message) {
            const original = btnElement.textContent;
            if (status === 'pending') {
                btnElement.textContent = '⏳';
                btnElement.classList.add('replay-pending');
            } else if (status === 'success') {
                btnElement.textContent = '✓';
                btnElement.classList.remove('replay-pending');
                btnElement.classList.add('replay-success');
            } else {
                btnElement.textContent = '✗';
                btnElement.classList.remove('replay-pending');
                btnElement.classList.add('replay-error');
                if (message) btnElement.title = message;
            }

            setTimeout(() => {
                btnElement.textContent = '⟳';
                btnElement.className = 'event-replay-btn';
                btnElement.title = 'Replay this event';
            }, 2000);
        }
