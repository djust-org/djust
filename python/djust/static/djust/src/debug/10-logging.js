        captureEvent(event) {
            this.eventHistory.unshift({
                ...event,
                timestamp: Date.now()
            });

            if (this.eventHistory.length > this.config.maxHistory) {
                this.eventHistory.pop();
            }

            // Update counter
            this.updateCounter('event-count', this.eventHistory.length);

            // Show activity
            this.showActivity();

            // If error, update badge
            if (event.error) {
                this.errorCount++;
                this.updateErrorBadge();
            }

            // Update content if events tab is active
            if (this.state.activeTab === 'events') {
                this.renderTabContent();
            }
        }

        capturePatch(patch) {
            this.patchHistory.unshift({
                ...patch,
                timestamp: Date.now()
            });

            if (this.patchHistory.length > this.config.maxPatchHistory) {
                this.patchHistory.pop();
            }

            this.updateCounter('patch-count', this.patchHistory.length);
            this.showActivity();
        }

        captureNetworkMessage(message) {
            this.networkHistory.unshift({
                ...message,
                timestamp: Date.now()
            });

            if (this.networkHistory.length > this.config.maxHistory) {
                this.networkHistory.pop();
            }

            // Update content if network tab is active
            if (this.state.activeTab === 'network') {
                this.renderTabContent();
            }
        }

        // Public methods for client-dev.js integration
        logEvent(eventName, params, result, duration = 0, elementInfo = null) {
            const event = {
                name: eventName,
                params: params,
                timestamp: Date.now(),
                duration: duration,
                error: null,
                result: null,
                element: elementInfo
            };

            // Check if result contains an error
            if (result && typeof result === 'object' && result.type === 'error') {
                event.error = result.error;
            } else {
                event.result = result;
            }

            this.eventHistory.unshift(event);

            // Capture state after event (if variables available)
            // This provides state timeline tracking tied to events
            if (this.variables && !event.error) {
                this.captureState('event', eventName, this.variables);
            }

            // Update error count
            if (event.error) {
                this.errorCount++;
                this.updateErrorBadge();
            }

            // Limit history
            if (this.eventHistory.length > this.config.maxHistory) {
                this.eventHistory.pop();
            }

            // Update UI if events tab is active
            if (this.state.activeTab === 'events') {
                this.renderTabContent();
            }

            this.showActivity();
        }

        logPatches(patches, duration = 0, performance = null) {
            // Handle timing object with server and client breakdown
            let timing = {};
            let totalDuration = 0;

            if (typeof duration === 'object') {
                timing = duration;
                // Calculate total including server and client time
                totalDuration = (timing.total || 0) + (timing.client || 0);
            } else {
                // Legacy: just client duration
                timing = { client: duration };
                totalDuration = duration;
            }

            const patchEntry = {
                timestamp: Date.now(),
                count: patches.length,
                performance: performance,  // Store comprehensive performance data
                patches: patches,
                timing: timing,
                totalDuration: totalDuration
            };

            this.patchHistory.unshift(patchEntry);

            // Update warning count
            if (performance && performance.timing) {
                const warnings = [];
                this.collectWarningsFromNode(performance.timing, warnings, 0);
                if (warnings.length > 0) {
                    this.warningCount += warnings.length;
                    this.updateCounter('warning-count', this.warningCount);
                }
            }

            // Track memory and context metrics
            this.trackMetrics(performance);

            // Limit history
            if (this.patchHistory.length > (this.config.maxPatchHistory || 50)) {
                this.patchHistory.pop();
            }

            // Update counter
            this.updateCounter('patches', this.patchHistory.length);

            // Update UI if patches tab is active
            if (this.state.activeTab === 'patches') {
                this.renderTabContent();
            }

            this.showActivity();
        }

        logNetwork(message, type = 'unknown') {
            this.captureNetworkMessage({
                ...message,
                _type: type
            });

            // Update counter
            this.updateCounter('network', this.networkHistory.length);

            this.showActivity();
        }

        // Hook into LiveView
