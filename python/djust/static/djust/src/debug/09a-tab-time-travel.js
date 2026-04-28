
        // ============================================================
        // Time Travel Tab (v0.6.1 — dev-only; v0.9.4 #1151 extensions)
        //
        // Renders a scrubable timeline of past events captured by the
        // server-side TimeTravelBuffer. Clicking a timeline entry
        // dispatches a {type:'time_travel_jump', index, which} frame
        // over the main djust WebSocket; the server restores the
        // snapshot and re-renders. Incoming `time_travel_state` frames
        // update the cursor indicator.
        //
        // v0.9.4 (#1151) additions: per-component scrubber, forward-
        // replay button, branch indicator, max-events count. All driven
        // by the augmented time_travel_state frame fields (branch_id,
        // forward_replay_enabled, max_events) and the augmented
        // time_travel_event frame's top-level `components` mirror.
        //
        // CSP-strict: no inline scripts, no inline event handlers. All
        // interactivity goes through the delegated click handler on the
        // panel root (registerTimeTravelClickHandlers).
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
            const branchId = this.timeTravelBranchId || 'main';
            const forwardReplayEnabled = !!this.timeTravelForwardReplayEnabled;
            const maxEvents = typeof this.timeTravelMaxEvents === 'number' ? this.timeTravelMaxEvents : null;
            const expanded = this.timeTravelExpandedRows || {};

            const rows = history.map((entry, idx) => {
                const active = idx === cursor ? ' tt-active' : '';
                const errorBadge = entry.error
                    ? `<span class="tt-error" title="${this.escapeHtml(entry.error)}">✕</span>`
                    : '';
                const paramsPreview = entry.params && Object.keys(entry.params).length > 0
                    ? this.escapeHtml(JSON.stringify(entry.params).slice(0, 60))
                    : '';
                const ts = new Date((entry.ts || 0) * 1000).toLocaleTimeString();

                // Pull the components dict out of state_after (snapshot
                // shape from time_travel.py: __components__ at top level
                // of state_before/state_after). Tolerate truncated
                // entries (large-state suppression in _maybe_push_tt_event).
                const stateAfter = entry.state_after || {};
                const componentsAfter = stateAfter.__components__ || null;
                const hasComponents = componentsAfter && typeof componentsAfter === 'object' && Object.keys(componentsAfter).length > 0;
                const isExpanded = !!expanded[idx];

                let componentBlock = '';
                if (hasComponents && isExpanded) {
                    const compRows = Object.keys(componentsAfter).map((compId) => {
                        const compState = componentsAfter[compId] || {};
                        const compPreview = this.escapeHtml(JSON.stringify(compState).slice(0, 80));
                        return `
                            <div class="tt-comp-row">
                                <span class="tt-comp-id">${this.escapeHtml(compId)}</span>
                                <span class="tt-comp-state">${compPreview}</span>
                                <button type="button" class="tt-comp-jump" data-tt-comp-jump="${idx}" data-tt-comp-id="${this.escapeHtml(compId)}" data-tt-which="before" title="Restore ${this.escapeHtml(compId)} only — leaves other components alone">↶ comp</button>
                                <button type="button" class="tt-comp-jump" data-tt-comp-jump="${idx}" data-tt-comp-id="${this.escapeHtml(compId)}" data-tt-which="after">↷ comp</button>
                            </div>
                        `;
                    }).join('');
                    componentBlock = `<div class="tt-components">${compRows}</div>`;
                }

                const expandToggle = hasComponents
                    ? `<button type="button" class="tt-expand-toggle" data-tt-expand="${idx}" title="${isExpanded ? 'Hide' : 'Show'} per-component state">${isExpanded ? '▼' : '▶'} ${Object.keys(componentsAfter).length} comp</button>`
                    : '';

                return `
                    <div class="tt-row${active}" data-tt-index="${idx}">
                        <div class="tt-row-head">
                            <span class="tt-index">#${idx}</span>
                            <span class="tt-event">${this.escapeHtml(entry.event_name || '?')}</span>
                            ${errorBadge}
                            <span class="tt-ts">${ts}</span>
                            ${expandToggle}
                        </div>
                        ${paramsPreview ? `<div class="tt-params">${paramsPreview}</div>` : ''}
                        <div class="tt-row-actions">
                            <button type="button" class="tt-jump" data-tt-jump="${idx}" data-tt-which="before">↶ before</button>
                            <button type="button" class="tt-jump" data-tt-jump="${idx}" data-tt-which="after">↷ after</button>
                            <button type="button" class="tt-forward-replay" data-tt-forward-replay="${idx}" title="Re-run this event from its state_before; allocates a new branch if not at the tip">⏵ replay</button>
                        </div>
                        ${componentBlock}
                    </div>
                `;
            }).reverse().join('');

            const cursorLabel = cursor >= 0
                ? `Cursor: #${cursor} (${this.escapeHtml(which)})`
                : 'Live (no jump active)';

            const branchBadge = branchId === 'main'
                ? `<span class="tt-branch tt-branch-main">main</span>`
                : `<span class="tt-branch tt-branch-fork" title="Branched timeline — diverged from main via forward-replay">${this.escapeHtml(branchId)}</span>`;

            const countLabel = maxEvents !== null
                ? `${history.length} / ${maxEvents} event${maxEvents === 1 ? '' : 's'}`
                : `${history.length} event${history.length === 1 ? '' : 's'}`;

            const replayHint = forwardReplayEnabled
                ? `<span class="tt-replay-hint">Replay-enabled at cursor</span>`
                : '';

            return `
                <div class="tt-container">
                    <div class="tt-header">
                        <span class="tt-title">Time Travel</span>
                        ${branchBadge}
                        <span class="tt-cursor">${cursorLabel}</span>
                        <span class="tt-count">${countLabel}</span>
                        ${replayHint}
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
            // v0.9.4 (#1151) additive fields. Tolerate undefined for
            // backwards-compat with pre-v0.9.4 servers.
            if (typeof frame.branch_id === 'string') {
                this.timeTravelBranchId = frame.branch_id;
            }
            if (typeof frame.forward_replay_enabled === 'boolean') {
                this.timeTravelForwardReplayEnabled = frame.forward_replay_enabled;
            }
            if (typeof frame.max_events === 'number') {
                this.timeTravelMaxEvents = frame.max_events;
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
            this._sendTimeTravelMessage({ type: 'time_travel_jump', index: index, which: which || 'before' });
        }

        onTimeTravelComponentJumpClick(index, componentId, which) {
            // v0.9.4 (#1151) — per-component scrubber.
            if (typeof index !== 'number' || typeof componentId !== 'string' || !componentId) {
                return;
            }
            this._sendTimeTravelMessage({
                type: 'time_travel_component_jump',
                index: index,
                component_id: componentId,
                which: which || 'before',
            });
        }

        onTimeTravelForwardReplayClick(fromIndex, overrideParams) {
            // v0.9.4 (#1151) — forward-replay button. Sends override_params
            // when supplied (currently only triggered programmatically;
            // the UI button sends null which means "re-run with original
            // params"). Branch allocation happens server-side.
            if (typeof fromIndex !== 'number') return;
            const payload = {
                type: 'forward_replay',
                from_index: fromIndex,
            };
            if (overrideParams !== undefined && overrideParams !== null) {
                payload.override_params = overrideParams;
            }
            this._sendTimeTravelMessage(payload);
        }

        _sendTimeTravelMessage(payload) {
            // Canonical send path — same as the existing
            // onTimeTravelJumpClick. Centralized so the new
            // component-jump and forward-replay handlers don't duplicate
            // the WS-not-ready guard.
            const lv = globalThis.djust && globalThis.djust.liveViewInstance;
            if (!lv || typeof lv.sendMessage !== 'function') {
                if (globalThis.djustDebug) {
                    console.warn('[time-travel] LiveView WS not ready — cannot send', payload);
                }
                return;
            }
            lv.sendMessage(payload);
        }

        toggleTimeTravelExpandRow(index) {
            // v0.9.4 (#1151) — flip the per-row expand state for the
            // component sub-scrubber section. Stored on the panel
            // instance so it survives tab re-renders.
            if (!this.timeTravelExpandedRows) this.timeTravelExpandedRows = {};
            this.timeTravelExpandedRows[index] = !this.timeTravelExpandedRows[index];
            if (this.state && this.state.activeTab === 'timeTravel') {
                try {
                    this.refreshActiveTab();
                } catch (_e) {
                    if (globalThis.djustDebug) {
                        console.warn('[djust] time-travel expand-toggle re-render failed', _e);
                    }
                }
            }
        }

        // Delegated click handler for .tt-jump, .tt-comp-jump,
        // .tt-forward-replay, .tt-expand-toggle buttons. Called once from
        // init() via registerTimeTravelClickHandlers — attaches to the
        // panel root so it survives tab content re-renders (tabs replace
        // innerHTML on every render).
        registerTimeTravelClickHandlers() {
            if (!this.panel || this._ttClickBound) return;
            this._ttClickBound = true;
            this.panel.addEventListener('click', (ev) => {
                const target = ev.target;
                if (!target || typeof target.closest !== 'function') return;

                // 1. Whole-view jump.
                const jumpButton = target.closest('.tt-jump');
                if (jumpButton && this.panel.contains(jumpButton)) {
                    const raw = jumpButton.dataset ? jumpButton.dataset.ttJump : jumpButton.getAttribute('data-tt-jump');
                    const which = (jumpButton.dataset ? jumpButton.dataset.ttWhich : jumpButton.getAttribute('data-tt-which')) || 'before';
                    const index = parseInt(raw, 10);
                    if (isNaN(index)) return;
                    this.onTimeTravelJumpClick(index, which);
                    return;
                }

                // 2. Per-component jump (v0.9.4).
                const compButton = target.closest('.tt-comp-jump');
                if (compButton && this.panel.contains(compButton)) {
                    const raw = compButton.dataset ? compButton.dataset.ttCompJump : compButton.getAttribute('data-tt-comp-jump');
                    const compId = compButton.dataset ? compButton.dataset.ttCompId : compButton.getAttribute('data-tt-comp-id');
                    const which = (compButton.dataset ? compButton.dataset.ttWhich : compButton.getAttribute('data-tt-which')) || 'before';
                    const index = parseInt(raw, 10);
                    if (isNaN(index) || typeof compId !== 'string' || !compId) return;
                    this.onTimeTravelComponentJumpClick(index, compId, which);
                    return;
                }

                // 3. Forward-replay (v0.9.4).
                const replayButton = target.closest('.tt-forward-replay');
                if (replayButton && this.panel.contains(replayButton)) {
                    const raw = replayButton.dataset ? replayButton.dataset.ttForwardReplay : replayButton.getAttribute('data-tt-forward-replay');
                    const fromIndex = parseInt(raw, 10);
                    if (isNaN(fromIndex)) return;
                    this.onTimeTravelForwardReplayClick(fromIndex, null);
                    return;
                }

                // 4. Expand-toggle (v0.9.4).
                const expandButton = target.closest('.tt-expand-toggle');
                if (expandButton && this.panel.contains(expandButton)) {
                    const raw = expandButton.dataset ? expandButton.dataset.ttExpand : expandButton.getAttribute('data-tt-expand');
                    const index = parseInt(raw, 10);
                    if (isNaN(index)) return;
                    this.toggleTimeTravelExpandRow(index);
                    return;
                }
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
            // v0.9.4 (#1151): the server now stamps branch_id on every
            // event push. Track it so the panel header reflects the
            // active branch even before the next time_travel_state ack.
            if (typeof detail.branch_id === 'string') {
                this.timeTravelBranchId = detail.branch_id;
            }
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
