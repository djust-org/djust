
        renderHandlersTab() {
            // Normalize handlers: accept both array and object/dict formats
            let handlers = this.handlers;
            if (handlers && !Array.isArray(handlers) && typeof handlers === 'object') {
                handlers = Object.values(handlers);
            }
            if (!handlers || handlers.length === 0) {
                return '<div class="empty-state">No event handlers detected. Handlers will appear after the view is mounted.</div>';
            }

            return `
                <div class="handlers-list">
                    ${handlers.map(handler => `
                        <div class="handler-item">
                            <div class="handler-header">
                                <div class="handler-name">${this.escapeHtml(handler.name)}</div>
                                ${handler.decorators && handler.decorators.length > 0 ? `
                                    <div class="handler-decorators">
                                        ${handler.decorators.map(d => `<span class="decorator">@${this.escapeHtml(d)}</span>`).join(' ')}
                                    </div>
                                ` : ''}
                            </div>
                            <div class="handler-description">${this.escapeHtml(handler.description || 'No description')}</div>
                            <div class="handler-params">
                                ${handler.parameters && handler.parameters.length > 0 ?
                                    handler.parameters.map(param =>
                                        `<span class="param ${param.required ? 'required' : 'optional'}">
                                            ${this.escapeHtml(param.name)}: ${this.escapeHtml(param.type)}
                                            ${param.default !== null && param.default !== undefined ? ` = ${this.escapeHtml(param.default)}` : ''}
                                        </span>`
                                    ).join(', ') : 'No parameters'}
                            </div>
                            ${handler.source_file ? `
                                <div class="handler-source">
                                    ${this.escapeHtml(handler.source_file)}:${this.escapeHtml(handler.source_line || 0)}
                                </div>
                            ` : ''}
                        </div>
                    `).join('')}
                </div>
            `;
        }
