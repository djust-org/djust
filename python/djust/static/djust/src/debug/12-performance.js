
        trackMetrics(performance) {
            if (!performance) return;

            const metrics = {
                timestamp: Date.now(),
                memory_mb: null,
                context_bytes: null
            };

            // Extract memory usage from timing tree
            if (performance.timing && performance.timing.metadata && performance.timing.metadata.memory) {
                metrics.memory_mb = performance.timing.metadata.memory.current_mb;
            }

            // Extract context size
            if (performance.context_size_bytes) {
                metrics.context_bytes = performance.context_size_bytes;
                this.totalContextSize += performance.context_size_bytes;
                this.contextSizeCount++;
            }

            // Add to history
            if (metrics.memory_mb !== null || metrics.context_bytes !== null) {
                this.memoryHistory.unshift(metrics);

                // Limit history
                if (this.memoryHistory.length > this.maxMemoryHistoryLength) {
                    this.memoryHistory.pop();
                }
            }
        }

        renderPerformanceMetrics() {
            if (this.memoryHistory.length === 0 && this.networkHistory.length === 0) {
                return '';
            }

            // Calculate stats
            const memoryStats = this.calculateMemoryStats();
            const contextStats = this.calculateContextStats();
            const networkStats = this.calculateNetworkStats();

            return `
                <div class="performance-metrics">
                    <div class="metrics-header">
                        <span class="metrics-icon">📊</span>
                        <span class="metrics-title">Performance Metrics</span>
                    </div>
                    <div class="metrics-grid">
                        ${memoryStats.hasData ? `
                            <div class="metric-card">
                                <div class="metric-label">Memory Usage</div>
                                <div class="metric-value">${memoryStats.current.toFixed(1)} MB</div>
                                <div class="metric-sparkline">${this.renderMemorySparkline()}</div>
                                <div class="metric-details">
                                    Peak: ${memoryStats.peak.toFixed(1)} MB
                                    · Avg: ${memoryStats.average.toFixed(1)} MB
                                </div>
                            </div>
                        ` : ''}
                        ${contextStats.hasData ? `
                            <div class="metric-card">
                                <div class="metric-label">Context Size</div>
                                <div class="metric-value">${this.formatBytes(contextStats.current)}</div>
                                <div class="metric-chart">${this.renderContextBar(contextStats.current, contextStats.max)}</div>
                                <div class="metric-details">
                                    Max: ${this.formatBytes(contextStats.max)}
                                    · Avg: ${this.formatBytes(contextStats.average)}
                                </div>
                            </div>
                        ` : ''}
                        ${networkStats.hasData ? `
                            <div class="metric-card">
                                <div class="metric-label">WebSocket Traffic</div>
                                <div class="metric-value">${this.formatBytes(networkStats.totalBytes)}</div>
                                <div class="metric-details">
                                    ↑ Sent: ${this.formatBytes(networkStats.sentBytes)}
                                    · ↓ Received: ${this.formatBytes(networkStats.receivedBytes)}
                                </div>
                                <div class="metric-details">
                                    Messages: ${networkStats.totalMessages}
                                    · Avg: ${this.formatBytes(networkStats.avgMessageSize)}
                                </div>
                            </div>
                        ` : ''}
                        <div class="metric-card">
                            <div class="metric-label">Patch Performance</div>
                            <div class="metric-value">${this.patchHistory.length} patches</div>
                            <div class="metric-details">
                                Avg time: ${this.calculateAvgPatchTime().toFixed(1)}ms
                                · Warnings: ${this.warningCount}
                            </div>
                        </div>
                    </div>
                </div>
            `;
        }

        calculateMemoryStats() {
            const memoryData = this.memoryHistory
                .map(m => m.memory_mb)
                .filter(m => m !== null);

            if (memoryData.length === 0) {
                return { hasData: false };
            }

            return {
                hasData: true,
                current: memoryData[0] || 0,
                peak: Math.max(...memoryData),
                average: memoryData.reduce((a, b) => a + b, 0) / memoryData.length
            };
        }

        calculateContextStats() {
            const contextData = this.memoryHistory
                .map(m => m.context_bytes)
                .filter(c => c !== null);

            if (contextData.length === 0) {
                return { hasData: false };
            }

            return {
                hasData: true,
                current: contextData[0] || 0,
                max: Math.max(...contextData),
                average: this.totalContextSize / this.contextSizeCount
            };
        }

        calculateNetworkStats() {
            if (this.networkHistory.length === 0) {
                return { hasData: false };
            }

            let sentBytes = 0;
            let receivedBytes = 0;
            let totalMessages = this.networkHistory.length;

            this.networkHistory.forEach(msg => {
                if (msg.direction === 'sent') {
                    sentBytes += msg.size || 0;
                } else {
                    receivedBytes += msg.size || 0;
                }
            });

            const totalBytes = sentBytes + receivedBytes;
            const avgMessageSize = totalBytes / totalMessages;

            return {
                hasData: true,
                totalBytes,
                sentBytes,
                receivedBytes,
                totalMessages,
                avgMessageSize
            };
        }

        calculateAvgPatchTime() {
            if (this.patchHistory.length === 0) return 0;

            const total = this.patchHistory.reduce((sum, entry) => {
                return sum + (entry.totalDuration || 0);
            }, 0);

            return total / this.patchHistory.length;
        }

        renderMemorySparkline() {
            const memoryData = this.memoryHistory
                .map(m => m.memory_mb)
                .filter(m => m !== null)
                .slice(0, 20)  // Last 20 data points
                .reverse();     // Oldest to newest

            if (memoryData.length < 2) {
                return '<div class="sparkline-empty">Not enough data</div>';
            }

            const max = Math.max(...memoryData);
            const min = Math.min(...memoryData);
            const range = max - min || 1;

            // Create simple SVG sparkline
            const width = 100;
            const height = 20;
            const step = width / (memoryData.length - 1);

            const points = memoryData.map((value, index) => {
                const x = index * step;
                const y = height - ((value - min) / range) * height;
                return `${x},${y}`;
            }).join(' ');

            return `
                <svg width="${width}" height="${height}" class="sparkline-svg">
                    <polyline
                        points="${points}"
                        fill="none"
                        stroke="#60a5fa"
                        stroke-width="1.5"
                    />
                </svg>
            `;
        }

        renderContextBar(current, max) {
            const percentage = max > 0 ? (current / max) * 100 : 0;
            const colorClass = percentage > 80 ? 'bar-high' : percentage > 50 ? 'bar-medium' : 'bar-low';

            return `
                <div class="metric-bar">
                    <div class="metric-bar-fill ${colorClass}" style="width: ${percentage}%"></div>
                </div>
            `;
        }
