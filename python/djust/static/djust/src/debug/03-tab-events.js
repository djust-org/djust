
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
