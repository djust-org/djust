
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
