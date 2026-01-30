
        renderHandlersTab() {
            if (!this.handlers || this.handlers.length === 0) {
                return '<div class="empty-state">No event handlers detected. Handlers will appear after the view is mounted.</div>';
            }

            return `
                <div class="handlers-list">
                    ${this.handlers.map(handler => `
                        <div class="handler-item">
                            <div class="handler-header">
                                <div class="handler-name">${handler.name}</div>
                                ${handler.decorators && handler.decorators.length > 0 ? `
                                    <div class="handler-decorators">
                                        ${handler.decorators.map(d => `<span class="decorator">@${d}</span>`).join(' ')}
                                    </div>
                                ` : ''}
                            </div>
                            <div class="handler-description">${handler.description || 'No description'}</div>
                            <div class="handler-params">
                                ${handler.parameters && handler.parameters.length > 0 ?
                                    handler.parameters.map(param =>
                                        `<span class="param ${param.required ? 'required' : 'optional'}">
                                            ${param.name}: ${param.type}
                                            ${param.default !== null && param.default !== undefined ? ` = ${param.default}` : ''}
                                        </span>`
                                    ).join(', ') : 'No parameters'}
                            </div>
                            ${handler.source_file ? `
                                <div class="handler-source">
                                    ${handler.source_file}:${handler.source_line || 0}
                                </div>
                            ` : ''}
                        </div>
                    `).join('')}
                </div>
            `;
        }
