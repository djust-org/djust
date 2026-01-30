
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
