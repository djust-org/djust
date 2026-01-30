
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
