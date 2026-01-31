
        // Tab render methods
        renderEventsTab() {
            const nameFilter = (this.state.filters.nameQuery || '').toLowerCase();
            const statusFilter = this.state.filters.severity || 'all';

            const filtered = this.eventHistory.filter(event => {
                if (nameFilter) {
                    const eventName = (event.handler || event.name || '').toLowerCase();
                    if (!eventName.includes(nameFilter)) return false;
                }
                if (statusFilter === 'error' && !event.error) return false;
                if (statusFilter === 'success' && event.error) return false;
                return true;
            });

            const filterBar = `
                <div class="events-filter-bar" style="display:flex;gap:8px;align-items:center;padding:6px 8px;border-bottom:1px solid #334155;background:#1e293b;">
                    <input type="text" class="events-name-filter" placeholder="Filter by event name…"
                        value="${this.escapeHtml(this.state.filters.nameQuery || '')}"
                        oninput="window.djustDebugPanel.setEventNameFilter(this.value)"
                        style="flex:1;background:#0f172a;border:1px solid #475569;color:#f1f5f9;padding:3px 8px;border-radius:3px;font-size:12px;">
                    <button class="events-filter-btn ${statusFilter === 'all' ? 'active' : ''}"
                        onclick="window.djustDebugPanel.setEventStatusFilter('all')"
                        style="padding:2px 8px;font-size:11px;border-radius:3px;cursor:pointer;border:1px solid #475569;background:${statusFilter === 'all' ? '#E57324' : '#1e293b'};color:#f1f5f9;">All</button>
                    <button class="events-filter-btn ${statusFilter === 'error' ? 'active' : ''}"
                        onclick="window.djustDebugPanel.setEventStatusFilter('error')"
                        style="padding:2px 8px;font-size:11px;border-radius:3px;cursor:pointer;border:1px solid #475569;background:${statusFilter === 'error' ? '#dc2626' : '#1e293b'};color:#f1f5f9;">Errors</button>
                    <button class="events-filter-btn ${statusFilter === 'success' ? 'active' : ''}"
                        onclick="window.djustDebugPanel.setEventStatusFilter('success')"
                        style="padding:2px 8px;font-size:11px;border-radius:3px;cursor:pointer;border:1px solid #475569;background:${statusFilter === 'success' ? '#16a34a' : '#1e293b'};color:#f1f5f9;">Success</button>
                    <button onclick="window.djustDebugPanel.clearEventFilters()"
                        style="padding:2px 8px;font-size:11px;border-radius:3px;cursor:pointer;border:1px solid #475569;background:#1e293b;color:#94a3b8;">Clear</button>
                </div>
            `;

            if (this.eventHistory.length === 0) {
                return filterBar + '<div class="empty-state">No events captured yet. Interact with the page to see events.</div>';
            }

            if (filtered.length === 0) {
                return filterBar + '<div class="empty-state">No matching events. Try adjusting your filters.</div>';
            }

            return filterBar + `
                <div class="events-list">
                    ${filtered.map((event, index) => {
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

        setEventNameFilter(value) {
            this.state.filters.nameQuery = value;
            this.saveState();
            this.renderTabContent();
        }

        setEventStatusFilter(value) {
            this.state.filters.severity = value;
            this.saveState();
            this.renderTabContent();
        }

        clearEventFilters() {
            this.state.filters.nameQuery = '';
            this.state.filters.severity = 'all';
            this.saveState();
            this.renderTabContent();
        }
