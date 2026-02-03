/**
 * djust Admin LiveView Integration
 * Provides real-time filtering, inline editing, and action progress
 */

(function() {
    'use strict';

    const config = window.DJUST_ADMIN_CONFIG || {};

    const djustAdmin = {
        // Current state
        filterDebounceTimer: null,
        progressEventSource: null,

        /**
         * Initialize djust admin features
         */
        init: function() {
            if (config.liveFilters) {
                this.initLiveFilters();
            }
            if (config.liveInlineEditing) {
                this.initInlineEditing();
            }
            if (config.refreshInterval) {
                this.initAutoRefresh();
            }
            this.initLiveActions();
            this.initWidgetRefresh();
        },

        /**
         * Initialize live filtering
         */
        initLiveFilters: function() {
            const searchInput = document.getElementById('djust-live-search');
            if (!searchInput) return;

            searchInput.addEventListener('input', (e) => {
                clearTimeout(this.filterDebounceTimer);
                this.filterDebounceTimer = setTimeout(() => {
                    this.performFilter(e.target.value);
                }, 300);
            });
        },

        /**
         * Perform live filter via AJAX
         */
        performFilter: async function(query) {
            const resultList = document.getElementById('djust-result-list');
            const countEl = document.getElementById('djust-filter-count');
            
            if (!resultList) return;

            try {
                const response = await fetch('djust/filter/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': config.csrfToken,
                    },
                    body: JSON.stringify({ q: query }),
                });

                const data = await response.json();
                
                if (data.error) {
                    console.error('Filter error:', data.error);
                    return;
                }

                // Update count
                if (countEl) {
                    countEl.textContent = `${data.count} result${data.count !== 1 ? 's' : ''}`;
                }

                // Note: Full result list update would require more integration
                // This provides the filtered data for custom implementations
                document.dispatchEvent(new CustomEvent('djust:filter', {
                    detail: { query, results: data.results, count: data.count }
                }));

            } catch (error) {
                console.error('Filter request failed:', error);
            }
        },

        /**
         * Initialize inline editing
         */
        initInlineEditing: function() {
            document.addEventListener('click', (e) => {
                const editable = e.target.closest('.djust-inline-edit');
                if (editable) {
                    this.focusInlineEdit(editable);
                }
            });

            document.addEventListener('blur', (e) => {
                const editable = e.target.closest('.djust-inline-edit');
                if (editable) {
                    this.saveInlineEdit(editable);
                }
            }, true);

            document.addEventListener('keydown', (e) => {
                const editable = e.target.closest('.djust-inline-edit');
                if (editable) {
                    if (e.key === 'Enter') {
                        e.preventDefault();
                        editable.blur();
                    } else if (e.key === 'Escape') {
                        // Restore original value
                        editable.textContent = editable.dataset.originalValue || '';
                        editable.blur();
                    }
                }
            });
        },

        /**
         * Focus inline edit field
         */
        focusInlineEdit: function(el) {
            // Store original value for cancel
            el.dataset.originalValue = el.textContent;
            
            // Select all text
            const range = document.createRange();
            range.selectNodeContents(el);
            const selection = window.getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
        },

        /**
         * Save inline edit via AJAX
         */
        saveInlineEdit: async function(el) {
            const newValue = el.textContent.trim();
            const originalValue = el.dataset.originalValue;
            
            // Skip if no change
            if (newValue === originalValue) return;

            const pk = el.dataset.pk;
            const field = el.dataset.field;

            el.classList.add('djust-inline-edit--saving');

            try {
                const response = await fetch('djust/inline-edit/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': config.csrfToken,
                    },
                    body: JSON.stringify({ pk, field, value: newValue }),
                });

                const data = await response.json();

                if (data.error) {
                    el.classList.add('djust-inline-edit--error');
                    el.textContent = originalValue;
                    console.error('Inline edit error:', data.error);
                    
                    // Show error notification
                    this.showNotification(data.error, 'error');
                } else {
                    el.textContent = data.value;
                    delete el.dataset.originalValue;
                    
                    // Dispatch success event
                    document.dispatchEvent(new CustomEvent('djust:inline-edit', {
                        detail: { pk, field, value: data.value }
                    }));
                }
            } catch (error) {
                el.classList.add('djust-inline-edit--error');
                el.textContent = originalValue;
                console.error('Inline edit request failed:', error);
            } finally {
                el.classList.remove('djust-inline-edit--saving');
                setTimeout(() => el.classList.remove('djust-inline-edit--error'), 3000);
            }
        },

        /**
         * Initialize live actions with progress
         */
        initLiveActions: function() {
            // Intercept action form submission
            const actionForm = document.querySelector('#changelist-form');
            if (!actionForm) return;

            actionForm.addEventListener('submit', (e) => {
                const action = actionForm.querySelector('[name="action"]').value;
                const selectedIds = Array.from(
                    actionForm.querySelectorAll('input[name="_selected_action"]:checked')
                ).map(input => input.value);

                // Check if this is a live action
                // The form will submit normally if not intercepted
                if (this.isLiveAction(action)) {
                    e.preventDefault();
                    this.runLiveAction(action, selectedIds);
                }
            });
        },

        /**
         * Check if action is a live action
         */
        isLiveAction: function(actionName) {
            // This would need to be populated from server-side
            // For now, check data attribute or config
            const actionSelect = document.querySelector('[name="action"]');
            const option = actionSelect?.querySelector(`option[value="${actionName}"]`);
            return option?.dataset.liveAction === 'true';
        },

        /**
         * Run live action with progress streaming
         */
        runLiveAction: function(action, selectedIds) {
            const modal = document.getElementById('djust-action-progress-modal');
            const title = document.getElementById('djust-action-title');
            const progressBar = document.getElementById('djust-progress-fill');
            const progressText = document.getElementById('djust-progress-text');
            const progressCount = document.getElementById('djust-progress-count');
            const progressMessage = document.getElementById('djust-progress-message');

            if (!modal) return;

            // Reset and show modal
            title.textContent = 'Processing...';
            progressBar.style.width = '0%';
            progressText.textContent = '0%';
            progressCount.textContent = '';
            progressMessage.textContent = '';
            modal.style.display = 'flex';

            // Build URL with params
            const params = new URLSearchParams();
            params.set('action', action);
            selectedIds.forEach(id => params.append('ids', id));

            // Open SSE connection
            const url = `djust/action-progress/?${params.toString()}`;
            this.progressEventSource = new EventSource(url);

            this.progressEventSource.onmessage = (e) => {
                const data = JSON.parse(e.data);

                if (data.error) {
                    progressMessage.textContent = `Error: ${data.error}`;
                    progressMessage.style.color = '#ba2121';
                    this.closeProgressEventSource();
                    return;
                }

                if (data.complete) {
                    progressBar.style.width = '100%';
                    progressText.textContent = '100%';
                    progressMessage.textContent = 'Complete!';
                    this.closeProgressEventSource();
                    
                    // Auto-refresh after 1 second
                    setTimeout(() => {
                        modal.style.display = 'none';
                        window.location.reload();
                    }, 1000);
                    return;
                }

                // Update progress
                if (data.percent !== undefined) {
                    progressBar.style.width = `${data.percent}%`;
                    progressText.textContent = `${data.percent}%`;
                }
                if (data.current !== undefined && data.total !== undefined) {
                    progressCount.textContent = `${data.current} / ${data.total}`;
                }
                if (data.message) {
                    progressMessage.textContent = data.message;
                }
            };

            this.progressEventSource.onerror = () => {
                progressMessage.textContent = 'Connection lost';
                progressMessage.style.color = '#ba2121';
                this.closeProgressEventSource();
            };
        },

        /**
         * Close progress modal
         */
        closeProgressModal: function() {
            const modal = document.getElementById('djust-action-progress-modal');
            if (modal) modal.style.display = 'none';
            this.closeProgressEventSource();
        },

        /**
         * Close SSE connection
         */
        closeProgressEventSource: function() {
            if (this.progressEventSource) {
                this.progressEventSource.close();
                this.progressEventSource = null;
            }
        },

        /**
         * Initialize auto-refresh
         */
        initAutoRefresh: function() {
            if (!config.refreshInterval) return;

            setInterval(() => {
                // Dispatch refresh event for custom handling
                document.dispatchEvent(new CustomEvent('djust:refresh'));
            }, config.refreshInterval);
        },

        /**
         * Initialize widget auto-refresh
         */
        initWidgetRefresh: function() {
            document.querySelectorAll('[data-refresh-interval]').forEach(widget => {
                const interval = parseInt(widget.dataset.refreshInterval, 10);
                if (interval > 0) {
                    setInterval(() => {
                        document.dispatchEvent(new CustomEvent('djust:widget-refresh', {
                            detail: { widgetId: widget.id }
                        }));
                    }, interval);
                }
            });
        },

        /**
         * Show notification
         */
        showNotification: function(message, type = 'info') {
            // Create notification element
            const notification = document.createElement('div');
            notification.className = `djust-notification djust-notification--${type}`;
            notification.textContent = message;
            
            // Add to page
            document.body.appendChild(notification);
            
            // Animate in
            setTimeout(() => notification.classList.add('djust-notification--visible'), 10);
            
            // Remove after delay
            setTimeout(() => {
                notification.classList.remove('djust-notification--visible');
                setTimeout(() => notification.remove(), 300);
            }, 3000);
        }
    };

    // Initialize on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => djustAdmin.init());
    } else {
        djustAdmin.init();
    }

    // Expose globally
    window.djustAdmin = djustAdmin;

})();
