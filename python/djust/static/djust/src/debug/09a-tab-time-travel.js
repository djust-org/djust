
        // ============================================================
        // Time Travel Tab (v0.6.1 — dev-only)
        //
        // Renders a scrubable timeline of past events captured by the
        // server-side TimeTravelBuffer. Clicking a timeline entry
        // dispatches a {type:'time_travel_jump', index, which} frame
        // over the main djust WebSocket; the server restores the
        // snapshot and re-renders. Incoming `time_travel_state` frames
        // update the cursor indicator.
        //
        // All console.log calls are guarded behind `globalThis.djustDebug`
        // per the djust JS size-budget rules.
        // ============================================================

        renderTimeTravelTab() {
            const history = this.timeTravelHistory || [];
            if (history.length === 0) {
                return '<div class="empty-state">No time-travel snapshots captured yet. Trigger an event on a view with <code>time_travel_enabled = True</code> to populate the timeline.</div>';
            }

            const cursor = typeof this.timeTravelCursor === 'number' ? this.timeTravelCursor : -1;
            const which = this.timeTravelWhich || 'before';

            const rows = history.map((entry, idx) => {
                const active = idx === cursor ? ' tt-active' : '';
                const errorBadge = entry.error
                    ? `<span class="tt-error" title="${this.escapeHtml(entry.error)}">✕</span>`
                    : '';
                const paramsPreview = entry.params && Object.keys(entry.params).length > 0
                    ? this.escapeHtml(JSON.stringify(entry.params).slice(0, 60))
                    : '';
                const ts = new Date((entry.ts || 0) * 1000).toLocaleTimeString();
                return `
                    <div class="tt-row${active}" data-tt-index="${idx}">
                        <div class="tt-row-head">
                            <span class="tt-index">#${idx}</span>
                            <span class="tt-event">${this.escapeHtml(entry.event_name || '?')}</span>
                            ${errorBadge}
                            <span class="tt-ts">${ts}</span>
                        </div>
                        ${paramsPreview ? `<div class="tt-params">${paramsPreview}</div>` : ''}
                        <div class="tt-row-actions">
                            <button type="button" class="tt-jump" data-tt-jump="${idx}" data-tt-which="before">↶ before</button>
                            <button type="button" class="tt-jump" data-tt-jump="${idx}" data-tt-which="after">↷ after</button>
                        </div>
                    </div>
                `;
            }).reverse().join('');

            const cursorLabel = cursor >= 0
                ? `Cursor: #${cursor} (${this.escapeHtml(which)})`
                : 'Live (no jump active)';

            return `
                <div class="tt-container">
                    <div class="tt-header">
                        <span class="tt-title">Time Travel</span>
                        <span class="tt-cursor">${cursorLabel}</span>
                        <span class="tt-count">${history.length} event${history.length === 1 ? '' : 's'}</span>
                    </div>
                    <div class="tt-timeline">${rows}</div>
                </div>
            `;
        }

        onTimeTravelHistory(history) {
            this.timeTravelHistory = Array.isArray(history) ? history : [];
        }

        onTimeTravelState(frame) {
            if (!frame) return;
            if (typeof frame.cursor === 'number') {
                this.timeTravelCursor = frame.cursor;
            }
            if (frame.which) {
                this.timeTravelWhich = frame.which;
            }
            // Re-render the tab if currently active.
            if (this.state && this.state.activeTab === 'timeTravel') {
                try {
                    this.refreshActiveTab();
                } catch (_e) {
                    if (globalThis.djustDebug) {
                        console.warn('[djust] time-travel re-render failed', _e);
                    }
                }
            }
        }

        captureTimeTravelEvent(entry) {
            // Append a captured entry (used by tests and the integration
            // layer that observes server-sent event/patch frames).
            if (!this.timeTravelHistory) this.timeTravelHistory = [];
            this.timeTravelHistory.push(entry);
        }

        onTimeTravelJumpClick(index, which) {
            if (typeof index !== 'number') return;
            // Canonical send path: window.djust.liveViewInstance (same path
            // used by replay in 03-tab-events.js and _hookExistingWebSocket
            // in 11-integration.js). globalThis.djust.websocket is NOT
            // assigned anywhere in the codebase — using it was a silent bug.
            const lv = globalThis.djust && globalThis.djust.liveViewInstance;
            const payload = { type: 'time_travel_jump', index: index, which: which || 'before' };
            if (!lv || typeof lv.sendMessage !== 'function') {
                if (globalThis.djustDebug) {
                    console.warn('[time-travel] LiveView WS not ready — cannot jump', payload);
                }
                return;
            }
            lv.sendMessage(payload);
        }

        // Delegated click handler for .tt-jump buttons. Called once from
        // init() via registerTimeTravelClickHandlers — attaches to the
        // panel root so it survives tab content re-renders (tabs replace
        // innerHTML on every render).
        registerTimeTravelClickHandlers() {
            if (!this.panel || this._ttClickBound) return;
            this._ttClickBound = true;
            this.panel.addEventListener('click', (ev) => {
                const target = ev.target;
                if (!target || typeof target.closest !== 'function') return;
                const button = target.closest('.tt-jump');
                if (!button || !this.panel.contains(button)) return;
                const raw = button.dataset ? button.dataset.ttJump : button.getAttribute('data-tt-jump');
                const which = (button.dataset ? button.dataset.ttWhich : button.getAttribute('data-tt-which')) || 'before';
                const index = parseInt(raw, 10);
                if (isNaN(index)) return;
                this.onTimeTravelJumpClick(index, which);
            });
        }

        // Listener for `djust:time-travel-event` CustomEvents dispatched
        // from the client WebSocket layer when the server pushes a new
        // snapshot. Appends the entry into the buffer and re-renders
        // the tab if it is currently active.
        onTimeTravelEvent(detail) {
            if (!detail || !detail.entry) return;
            if (!this.timeTravelHistory) this.timeTravelHistory = [];
            this.timeTravelHistory.push(detail.entry);
            if (this.state && this.state.activeTab === 'timeTravel') {
                try {
                    if (typeof this.refreshActiveTab === 'function') {
                        this.refreshActiveTab();
                    } else if (typeof this.renderTabContent === 'function') {
                        this.renderTabContent();
                    }
                } catch (_e) {
                    if (globalThis.djustDebug) {
                        console.warn('[djust] time-travel event re-render failed', _e);
                    }
                }
            }
        }
