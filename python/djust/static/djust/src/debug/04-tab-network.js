
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
