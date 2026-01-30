
        renderComponentsTab() {
            if (!this.components) {
                return '<div class="empty-state">No components detected. Components will appear after the view is mounted.</div>';
            }

            return `
                <div class="components-tree">
                    ${this.renderComponentNode(this.components)}
                </div>
            `;
        }

        renderComponentNode(component, level = 0) {
            if (!component) return '';

            return `
                <div class="component-node" style="padding-left: ${level * 20}px">
                    <div class="component-header">
                        <span class="component-name">${component.name || 'Unknown'}</span>
                        <span class="component-type">${component.type || 'Component'}</span>
                    </div>
                    ${component.state ? `
                        <div class="component-state" style="padding-left: ${(level + 1) * 20}px">
                            <pre>${JSON.stringify(component.state, null, 2)}</pre>
                        </div>
                    ` : ''}
                    ${component.children ? component.children.map(child =>
                        this.renderComponentNode(child, level + 1)
                    ).join('') : ''}
                </div>
            `;
        }

        renderComponentTree(components, level = 0) {
            return components.map(comp => `
                <div class="component-node" style="padding-left: ${level * 20}px">
                    <div class="component-header">
                        <span class="component-name">${comp.name}</span>
                        <span class="component-type">${comp.type}</span>
                    </div>
                    ${comp.children ? this.renderComponentTree(comp.children, level + 1) : ''}
                </div>
            `).join('');
        }
