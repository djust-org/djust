        hookIntoLiveView() {
            // Intercept WebSocket messages
            if (window.WebSocket) {
                const originalSend = WebSocket.prototype.send;
                const self = this;

                WebSocket.prototype.send = function(data) {
                    self.captureNetworkMessage({
                        direction: 'sent',
                        type: self.detectMessageType(data),
                        size: new Blob([data]).size,
                        payload: self.parsePayload(data)
                    });

                    return originalSend.call(this, data);
                };

                // Also hook into message receive
                const originalAddEventListener = WebSocket.prototype.addEventListener;
                WebSocket.prototype.addEventListener = function(type, listener) {
                    if (type === 'message') {
                        const wrappedListener = function(event) {
                            const payload = self.parsePayload(event.data);

                            self.captureNetworkMessage({
                                direction: 'received',
                                type: self.detectMessageType(event.data),
                                size: new Blob([event.data]).size,
                                payload: payload
                            });

                            // Process debug information if present
                            if (payload && payload._debug) {
                                self.processDebugInfo(payload._debug);
                            }

                            return listener.call(this, event);
                        };
                        return originalAddEventListener.call(this, type, wrappedListener);
                    }
                    return originalAddEventListener.call(this, type, listener);
                };
            }

            // Hook into LiveView event handling
            if (window.liveView) {
                const originalHandleEvent = window.liveView.handleEvent;
                window.liveView.handleEvent = (event) => {
                    const startTime = performance.now();

                    try {
                        const result = originalHandleEvent.call(window.liveView, event);

                        this.captureEvent({
                            type: 'event',
                            handler: event.handler || event.type,
                            params: event.params,
                            duration: performance.now() - startTime
                        });

                        return result;
                    } catch (error) {
                        this.captureEvent({
                            type: 'event',
                            handler: event.handler || event.type,
                            params: event.params,
                            duration: performance.now() - startTime,
                            error: error.message
                        });
                        throw error;
                    }
                };
            }
        }

        // Process debug information from server
        processDebugInfo(debugInfo) {
            if (!debugInfo) return;

            // Update handlers
            if (debugInfo.handlers) {
                this.handlers = debugInfo.handlers;
                if (this.state.activeTab === 'handlers') {
                    this.renderTabContent();
                }
            }

            // Update components
            if (debugInfo.components) {
                this.components = debugInfo.components;
                if (this.state.activeTab === 'components') {
                    this.renderTabContent();
                }
            }

            // Update variables and capture state change
            if (debugInfo.variables) {
                const isMount = this.stateHistory.length === 0 || debugInfo._isMounted;
                const trigger = isMount ? 'mount' : 'update';
                const eventName = debugInfo._eventName || null;

                // Capture state before updating variables
                this.captureState(trigger, eventName, debugInfo.variables);

                this.variables = debugInfo.variables;
                if (this.state.activeTab === 'variables') {
                    this.renderTabContent();
                }
            }

            // Update performance metrics
            if (debugInfo.performance) {
                this.performance = debugInfo.performance;
                // Update footer stats
                this.updateStats(debugInfo.performance);
            }

            // Update view info
            if (debugInfo.view_name) {
                this.viewInfo = {
                    name: debugInfo.view_name,
                    module: debugInfo.view_module
                };
            }
        }

        updateStats(performance) {
            const footer = this.panel.querySelector('.djust-panel-footer');
            if (!footer) return;

            const stats = footer.querySelector('.djust-stats');
            if (stats && performance) {
                stats.innerHTML = `
                    <div class="djust-stat">Events:<span>${performance.event_count || 0}</span></div>
                    <div class="djust-stat">Patches:<span>${performance.patch_count || 0}</span></div>
                    <div class="djust-stat">Renders:<span>${performance.render_count || 0}</span></div>
                    <div class="djust-stat">Render Time:<span>${performance.render_time ? performance.render_time.toFixed(2) + 'ms' : 'N/A'}</span></div>
                `;
            }
        }

        // Utility methods
        showActivity() {
            this.button.classList.add('active');
            setTimeout(() => this.button.classList.remove('active'), 500);
        }

        updateErrorBadge() {
            const badge = this.button.querySelector('.error-badge');
            if (this.errorCount > 0) {
                badge.textContent = this.errorCount > 99 ? '99+' : this.errorCount;
                badge.style.display = 'block';
                this.button.classList.add('error');
            } else {
                badge.style.display = 'none';
                this.button.classList.remove('error');
            }

            this.updateCounter('error-count', this.errorCount);
        }

        updateCounter(id, count) {
            const counter = document.getElementById(id);
            if (counter) {
                counter.textContent = count;
            }
        }

        detectMessageType(data) {
            try {
                const parsed = JSON.parse(data);
                return parsed.type || 'unknown';
            } catch {
                return 'binary';
            }
        }

        parsePayload(data) {
            try {
                return JSON.parse(data);
            } catch {
                return data;
            }
        }
