
        registerTabs() {
            // Register default tabs
            this.registerTab('events', {
                name: 'Events',
                icon: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M8 1L2 9H6L5 15L14 7H9L10 1H8Z" stroke-linejoin="round"/></svg>',
                render: () => this.renderEventsTab()
            });

            this.registerTab('network', {
                name: 'Network',
                icon: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="7"/><path d="M2 8H14M8 1C6 3 6 5 6 8C6 11 6 13 8 15M8 1C10 3 10 5 10 8C10 11 10 13 8 15"/></svg>',
                render: () => this.renderNetworkTab()
            });

            this.registerTab('patches', {
                name: 'Patches',
                icon: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M2 2L14 14M14 2L2 14"/><rect x="5" y="5" width="6" height="6" stroke-dasharray="2 2"/></svg>',
                render: () => this.renderPatchesTab()
            });

            this.registerTab('components', {
                name: 'Components',
                icon: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="2" width="5" height="5" rx="1"/><rect x="9" y="2" width="5" height="5" rx="1"/><rect x="2" y="9" width="5" height="5" rx="1"/><rect x="9" y="9" width="5" height="5" rx="1"/></svg>',
                render: () => this.renderComponentsTab()
            });

            this.registerTab('state', {
                name: 'State',
                icon: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M8 2V8L11 11M14 8C14 11.3 11.3 14 8 14C4.7 14 2 11.3 2 8C2 4.7 4.7 2 8 2C11.3 2 14 4.7 14 8Z"/></svg>',
                render: () => this.renderStateTab()
            });

            this.registerTab('handlers', {
                name: 'Handlers',
                icon: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M8 8C8 8 5 5 2 8L8 14L14 8C11 5 8 8 8 8Z"/><circle cx="8" cy="8" r="2"/></svg>',
                render: () => this.renderHandlersTab()
            });

            this.registerTab('variables', {
                name: 'Variables',
                icon: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M2 3H14V13H2V3Z"/><path d="M5 6H11M5 8H11M5 10H8"/></svg>',
                render: () => this.renderVariablesTab()
            });

            // Render tab buttons
            this.renderTabButtons();
        }

        registerTab(id, config) {
            this.tabs.set(id, config);
        }

        renderTabButtons() {
            const tabsContainer = this.panel.querySelector('.djust-tabs');
            tabsContainer.innerHTML = '';

            for (const [id, tab] of this.tabs) {
                const tabButton = document.createElement('div');
                tabButton.className = 'djust-tab';
                tabButton.dataset.tab = id;
                if (id === this.state.activeTab) {
                    tabButton.classList.add('active');
                }

                tabButton.innerHTML = `
                    <span class="djust-tab-icon">${tab.icon}</span>
                    <span class="djust-tab-name">${tab.name}</span>
                `;

                tabButton.addEventListener('click', () => this.switchTab(id));
                tabsContainer.appendChild(tabButton);
            }
        }

        switchTab(tabId) {
            this.state.activeTab = tabId;

            // Update active tab button
            this.panel.querySelectorAll('.djust-tab').forEach(tab => {
                tab.classList.toggle('active', tab.dataset.tab === tabId);
            });

            // Render tab content
            this.renderTabContent();
        }

        renderTabContent() {
            const contentContainer = this.panel.querySelector('.djust-tab-content');
            const activeTab = this.tabs.get(this.state.activeTab);

            if (activeTab && activeTab.render) {
                contentContainer.innerHTML = activeTab.render();
            }
        }
