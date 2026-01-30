
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
