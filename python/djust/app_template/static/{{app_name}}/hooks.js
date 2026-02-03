/**
 * {{ app_name }} JavaScript Hooks
 * 
 * Hooks let you run client-side JavaScript when djust elements mount, update, or unmount.
 * Register hooks with djust.registerHook('HookName', HookObject).
 * 
 * Hook Lifecycle:
 * - mounted(el): Called when element first appears in DOM
 * - updated(el): Called when element's data-* attributes change
 * - destroyed(el): Called when element is removed from DOM
 * 
 * Usage in templates:
 *   <div dj-hook="MyHook" data-value="{{ value }}">...</div>
 */

// Ensure djust is available
window.djust = window.djust || { hooks: {} };

/**
 * ChartHook - Example hook for rendering charts
 * 
 * In a real app, you'd use a library like Chart.js:
 *   import Chart from 'chart.js/auto';
 * 
 * This example shows the hook pattern with simple DOM manipulation.
 */
const ChartHook = {
    mounted(el) {
        console.log('[ChartHook] mounted', el.id);
        
        // Parse data from attributes
        const values = JSON.parse(el.dataset.values || '[]');
        const type = el.dataset.type || 'bar';
        const animate = el.dataset.animate === 'true';
        
        // Store reference for updates
        this._el = el;
        
        // Render the chart
        this._renderChart(el, values, type, animate);
    },
    
    updated(el) {
        console.log('[ChartHook] updated', el.id);
        
        // Re-read data and re-render
        const values = JSON.parse(el.dataset.values || '[]');
        const type = el.dataset.type || 'bar';
        const animate = el.dataset.animate === 'true';
        
        this._renderChart(el, values, type, animate);
    },
    
    destroyed(el) {
        console.log('[ChartHook] destroyed', el.id);
        // Clean up any chart instance, event listeners, etc.
        // In Chart.js: this._chart?.destroy();
    },
    
    _renderChart(el, values, type, animate) {
        // Simple ASCII-style bar chart (replace with real charting library)
        const max = Math.max(...values, 1);
        const bars = values.map((v, i) => {
            const height = Math.round((v / max) * 100);
            const label = String.fromCharCode(65 + i); // A, B, C, D, E
            return `
                <div class="chart-bar" style="--height: ${height}%">
                    <div class="bar" style="height: ${height}%; ${animate ? 'transition: height 0.3s;' : ''}"></div>
                    <div class="value">${v}</div>
                    <div class="label">${label}</div>
                </div>
            `;
        }).join('');
        
        el.innerHTML = `
            <div class="simple-chart ${type}">
                ${bars}
            </div>
            <style>
                .simple-chart {
                    display: flex;
                    gap: 1rem;
                    align-items: flex-end;
                    height: 150px;
                    padding: 1rem;
                }
                .chart-bar {
                    flex: 1;
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    height: 100%;
                }
                .chart-bar .bar {
                    width: 100%;
                    background: linear-gradient(to top, #667eea, #764ba2);
                    border-radius: 4px 4px 0 0;
                    margin-top: auto;
                }
                .simple-chart.line .bar {
                    width: 8px;
                    border-radius: 4px;
                }
                .chart-bar .value {
                    font-weight: bold;
                    font-size: 0.875rem;
                    margin-top: 0.5rem;
                }
                .chart-bar .label {
                    color: #666;
                    font-size: 0.75rem;
                }
            </style>
        `;
    }
};

/**
 * AnimatedCounter - Smoothly animate number changes
 * 
 * Usage:
 *   <div dj-hook="AnimatedCounter" data-value="{{ count }}" data-duration="500">
 *       {{ count }}
 *   </div>
 */
const AnimatedCounter = {
    mounted(el) {
        this._currentValue = parseInt(el.dataset.value) || 0;
        this._duration = parseInt(el.dataset.duration) || 300;
        el.textContent = this._currentValue;
    },
    
    updated(el) {
        const newValue = parseInt(el.dataset.value) || 0;
        const duration = parseInt(el.dataset.duration) || 300;
        
        if (newValue === this._currentValue) return;
        
        // Animate from current to new value
        const startValue = this._currentValue;
        const startTime = performance.now();
        
        const animate = (currentTime) => {
            const elapsed = currentTime - startTime;
            const progress = Math.min(elapsed / duration, 1);
            
            // Ease out cubic
            const eased = 1 - Math.pow(1 - progress, 3);
            const currentValue = Math.round(startValue + (newValue - startValue) * eased);
            
            el.textContent = currentValue;
            
            if (progress < 1) {
                requestAnimationFrame(animate);
            } else {
                this._currentValue = newValue;
            }
        };
        
        requestAnimationFrame(animate);
    },
    
    destroyed(el) {
        // No cleanup needed
    }
};

/**
 * FocusTrap - Keep focus within an element (useful for modals)
 * 
 * Usage:
 *   <div dj-hook="FocusTrap">
 *       <input type="text">
 *       <button>Submit</button>
 *   </div>
 */
const FocusTrap = {
    mounted(el) {
        const focusable = el.querySelectorAll(
            'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        );
        
        if (focusable.length === 0) return;
        
        this._first = focusable[0];
        this._last = focusable[focusable.length - 1];
        
        this._handleKeydown = (e) => {
            if (e.key !== 'Tab') return;
            
            if (e.shiftKey && document.activeElement === this._first) {
                e.preventDefault();
                this._last.focus();
            } else if (!e.shiftKey && document.activeElement === this._last) {
                e.preventDefault();
                this._first.focus();
            }
        };
        
        el.addEventListener('keydown', this._handleKeydown);
        this._first.focus();
    },
    
    destroyed(el) {
        el.removeEventListener('keydown', this._handleKeydown);
    }
};

/**
 * LocalStorage - Persist element value to localStorage
 * 
 * Usage:
 *   <input dj-hook="LocalStorage" data-key="username" value="{{ username }}">
 */
const LocalStorage = {
    mounted(el) {
        const key = el.dataset.key;
        if (!key) return;
        
        // Load saved value
        const saved = localStorage.getItem(`djust_${key}`);
        if (saved && el.tagName === 'INPUT') {
            el.value = saved;
            // Trigger change to sync with server (optional)
            // el.dispatchEvent(new Event('input', { bubbles: true }));
        }
        
        // Save on change
        this._handleChange = () => {
            localStorage.setItem(`djust_${key}`, el.value);
        };
        
        el.addEventListener('change', this._handleChange);
        el.addEventListener('input', this._handleChange);
    },
    
    destroyed(el) {
        el.removeEventListener('change', this._handleChange);
        el.removeEventListener('input', this._handleChange);
    }
};


// Register all hooks
// In a real app, you might import these from separate files
if (window.djust.registerHook) {
    window.djust.registerHook('ChartHook', ChartHook);
    window.djust.registerHook('AnimatedCounter', AnimatedCounter);
    window.djust.registerHook('FocusTrap', FocusTrap);
    window.djust.registerHook('LocalStorage', LocalStorage);
} else {
    // Fallback: store in hooks object for later registration
    window.djust.hooks = {
        ...window.djust.hooks,
        ChartHook,
        AnimatedCounter,
        FocusTrap,
        LocalStorage
    };
}

console.log('[{{ app_name }}] Hooks loaded:', Object.keys(window.djust.hooks || {}));
