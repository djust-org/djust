
        /**
         * Latency Simulator
         *
         * Adds a latency control strip to the debug panel for simulating
         * network delay on WebSocket send/receive. DEBUG_MODE only.
         */

        _initLatencySim() {
            if (!window.DEBUG_MODE) return;

            // Ensure djust namespace
            if (!window.djust) window.djust = {};

            // Load persisted latency settings
            const stored = localStorage.getItem('djust_debug_latency');
            if (stored) {
                try {
                    const cfg = JSON.parse(stored);
                    window.djust._simulatedLatency = cfg.latency || 0;
                    window.djust._simulatedJitter = cfg.jitter || 0;
                } catch (_e) {
                    window.djust._simulatedLatency = 0;
                    window.djust._simulatedJitter = 0;
                }
            } else {
                window.djust._simulatedLatency = 0;
                window.djust._simulatedJitter = 0;
            }

            this._updateLatencyBadge();
        }

        _setLatency(ms, jitter) {
            if (!window.djust) window.djust = {};
            window.djust._simulatedLatency = Math.max(0, Number(ms) || 0);
            if (jitter !== undefined) {
                window.djust._simulatedJitter = Math.max(0, Math.min(1, Number(jitter) || 0));
            }

            // Persist to localStorage
            localStorage.setItem('djust_debug_latency', JSON.stringify({
                latency: window.djust._simulatedLatency,
                jitter: window.djust._simulatedJitter
            }));

            this._updateLatencyBadge();

            // Re-render controls if panel is open
            if (this.state.isOpen) {
                this.renderTabContent();
            }
        }

        _updateLatencyBadge() {
            if (!this.button) return;
            let badge = this.button.querySelector('.latency-badge');
            const latency = (window.djust && window.djust._simulatedLatency) || 0;

            if (latency > 0) {
                if (!badge) {
                    badge = document.createElement('span');
                    badge.className = 'latency-badge';
                    badge.setAttribute('style',
                        'position: absolute; bottom: -4px; left: 50%; transform: translateX(-50%); ' +
                        'background: #f59e0b; color: #000; font-size: 9px; font-weight: bold; ' +
                        'border-radius: 8px; padding: 1px 5px; pointer-events: none; ' +
                        'white-space: nowrap;'
                    );
                    this.button.appendChild(badge);
                }
                badge.textContent = '~' + latency + 'ms';
                badge.style.display = 'block';
            } else if (badge) {
                badge.style.display = 'none';
            }
        }

        renderLatencyControls() {
            const latency = (window.djust && window.djust._simulatedLatency) || 0;
            const jitterPct = Math.round(((window.djust && window.djust._simulatedJitter) || 0) * 100);
            const presets = [0, 50, 100, 200, 500];

            return `
                <div class="latency-controls" style="padding: 8px 12px; background: #1e293b; border-bottom: 1px solid #334155; display: flex; align-items: center; gap: 8px; font-size: 12px;">
                    <span style="color: #94a3b8; font-weight: 600;">Latency</span>
                    ${presets.map(ms => `
                        <button onclick="window.djustDebugPanel._setLatency(${ms})"
                            style="padding: 2px 8px; border-radius: 4px; border: 1px solid ${latency === ms ? '#E57324' : '#475569'}; background: ${latency === ms ? '#E57324' : 'transparent'}; color: ${latency === ms ? '#fff' : '#94a3b8'}; cursor: pointer; font-size: 11px;">
                            ${ms === 0 ? 'Off' : ms + 'ms'}
                        </button>
                    `).join('')}
                    <input type="number" min="0" max="5000" step="10"
                        value="${presets.includes(latency) ? '' : latency}"
                        placeholder="Custom"
                        onchange="window.djustDebugPanel._setLatency(Number(this.value))"
                        style="width: 60px; padding: 2px 4px; background: #0f172a; border: 1px solid #475569; border-radius: 4px; color: #f1f5f9; font-size: 11px;" />
                    <span style="color: #64748b;">ms</span>
                    <span style="color: #475569; margin: 0 4px;">|</span>
                    <span style="color: #94a3b8;">Jitter:</span>
                    <input type="number" min="0" max="100" step="5"
                        value="${jitterPct}"
                        onchange="window.djustDebugPanel._setLatency(window.djust._simulatedLatency, Number(this.value) / 100)"
                        style="width: 45px; padding: 2px 4px; background: #0f172a; border: 1px solid #475569; border-radius: 4px; color: #f1f5f9; font-size: 11px;" />
                    <span style="color: #64748b;">%</span>
                </div>
            `;
        }
