        hookIntoLiveView() {
            // Intercept WebSocket messages
            if (window.WebSocket) {
                const originalSend = WebSocket.prototype.send;
                const self = this;

                WebSocket.prototype.send = function(data) {
                    const payload = self.parsePayload(data);

                    self.captureNetworkMessage({
                        direction: 'sent',
                        type: self.detectMessageType(data),
                        size: new Blob([data]).size,
                        payload: payload
                    });

                    // Capture events from sent messages
                    if (payload && payload.type === 'event') {
                        self._pendingEvents = self._pendingEvents || {};
                        const eventKey = (payload.event || payload.handler) + '_' + Date.now();
                        self._pendingEvents[eventKey] = {
                            handler: payload.event || payload.handler,
                            params: payload.params || payload.data || {},
                            startTime: performance.now()
                        };
                    }

                    return originalSend.call(this, data);
                };

                // Also hook into message receive via addEventListener
                const originalAddEventListener = WebSocket.prototype.addEventListener;
                WebSocket.prototype.addEventListener = function(type, listener) {
                    if (type === 'message') {
                        const wrappedListener = function(event) {
                            self._handleReceivedMessage(event);
                            return listener.call(this, event);
                        };
                        return originalAddEventListener.call(this, type, wrappedListener);
                    }
                    return originalAddEventListener.call(this, type, listener);
                };

                // Hook into onmessage property setter to capture messages
                // assigned via ws.onmessage = handler (bypasses addEventListener)
                const onmessageDescriptor = Object.getOwnPropertyDescriptor(WebSocket.prototype, 'onmessage');
                if (onmessageDescriptor) {
                    Object.defineProperty(WebSocket.prototype, 'onmessage', {
                        set(handler) {
                            if (typeof handler !== 'function') {
                                return onmessageDescriptor.set.call(this, handler);
                            }
                            const wrappedHandler = function(event) {
                                self._handleReceivedMessage(event);
                                return handler.call(this, event);
                            };
                            onmessageDescriptor.set.call(this, wrappedHandler);
                        },
                        get() {
                            return onmessageDescriptor.get.call(this);
                        },
                        configurable: true
                    });
                }
            }

            // Initialize pending events tracker for matching sent events to responses
            this._pendingEvents = {};

            // Also hook into any existing WebSocket instance
            this._hookExistingWebSocket();
        }

        _hookExistingWebSocket() {
            const lv = window.djust && window.djust.liveViewInstance;
            const ws = lv && lv.ws;
            if (!ws || ws._djustDebugHooked) return;

            const self = this;
            const originalSend = ws.send.bind(ws);
            ws.send = function(data) {
                const payload = self.parsePayload(data);
                self.captureNetworkMessage({
                    direction: 'sent',
                    type: self.detectMessageType(data),
                    size: typeof data === 'string' ? data.length : 0,
                    payload: payload
                });
                return originalSend(data);
            };

            // Hook the existing onmessage handler by reassigning it through our
            // patched WebSocket.prototype.onmessage setter. This causes the setter
            // to wrap the handler with _handleReceivedMessage so future received
            // messages are captured.
            //
            // We MUST NOT call native WebSocket/EventTarget functions via .call()
            // from external code — V8 throws "Illegal invocation" because native
            // bindings only pass their brand check when invoked through property
            // access/assignment, not through explicit Function.prototype.call().
            // Property access (ws.onmessage) triggers the patched getter which
            // itself calls the native getter internally — that works because it's
            // within the getter body where V8 has already validated the receiver.
            const currentOnmessage = ws.onmessage;
            if (currentOnmessage) {
                ws.onmessage = currentOnmessage;
            }

            ws._djustDebugHooked = true;
        }

        _handleReceivedMessage(event) {
            const payload = this.parsePayload(event.data);

            this.captureNetworkMessage({
                direction: 'received',
                type: this.detectMessageType(event.data),
                size: new Blob([event.data]).size,
                payload: payload
            });

            // Process debug information if present
            if (payload && payload._debug) {
                this.processDebugInfo(payload._debug);
            }

            // Match response to pending events and capture completed event
            if (payload && this._pendingEvents) {
                const isEventResponse = payload.type === 'patch' || payload.type === 'html_update' || payload.type === 'error' || payload.type === 'noop';
                if (isEventResponse) {
                    // Find the most recent pending event (FIFO)
                    const keys = Object.keys(this._pendingEvents);
                    if (keys.length > 0) {
                        const key = keys[0];
                        const pending = this._pendingEvents[key];
                        delete this._pendingEvents[key];

                        this.captureEvent({
                            type: 'event',
                            handler: pending.handler,
                            params: pending.params,
                            duration: performance.now() - pending.startTime,
                            error: payload.type === 'error' ? (payload.error || 'Server error') : null
                        });
                    }
                }
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

            // Update patch history
            if (debugInfo.patches && debugInfo.patches.length > 0) {
                this.patchHistory.unshift({
                    patches: debugInfo.patches,
                    timestamp: Date.now(),
                    performance: debugInfo.performance || null,
                });
                if (this.patchHistory.length > (this.config.maxPatchHistory || 50)) {
                    this.patchHistory.pop();
                }
                if (this.state.activeTab === 'patches') {
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
