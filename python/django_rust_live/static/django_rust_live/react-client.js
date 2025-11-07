/**
 * Django Rust Live - React Integration Client
 *
 * Provides client-side React component hydration for server-rendered components.
 * Automatically detects and hydrates React components marked with data-react-component.
 */

class ReactHydrationManager {
    constructor() {
        this.components = new Map();
        this.hydratedElements = new WeakSet();
        this.componentModules = {};
    }

    /**
     * Register a React component for hydration
     * @param {string} name - Component name
     * @param {Function|string} component - React component or module path
     */
    register(name, component) {
        if (typeof component === 'string') {
            // Module path - will be loaded dynamically
            this.componentModules[name] = component;
        } else {
            // Direct component reference
            this.components.set(name, component);
        }
    }

    /**
     * Load a component module dynamically
     * @param {string} modulePath - Path to ES module
     * @param {string} exportName - Named export (default: 'default')
     */
    async loadComponent(modulePath, exportName = 'default') {
        try {
            const module = await import(modulePath);
            return module[exportName] || module.default;
        } catch (error) {
            console.error(`Failed to load component from ${modulePath}:`, error);
            return null;
        }
    }

    /**
     * Parse props from data attribute
     * @param {string} propsJson - JSON string of props
     */
    parseProps(propsJson) {
        try {
            return JSON.parse(propsJson.replace(/&quot;/g, '"'));
        } catch (error) {
            console.error('Failed to parse component props:', error);
            return {};
        }
    }

    /**
     * Hydrate a single React component element
     * @param {HTMLElement} element - DOM element to hydrate
     */
    async hydrateElement(element) {
        if (this.hydratedElements.has(element)) {
            return; // Already hydrated
        }

        const componentName = element.dataset.reactComponent;
        const propsJson = element.dataset.reactProps;
        const modulePath = element.dataset.reactModule;
        const exportName = element.dataset.reactExport || 'default';

        if (!componentName) {
            console.warn('Element missing data-react-component attribute');
            return;
        }

        // Get or load component
        let Component = this.components.get(componentName);

        if (!Component && modulePath) {
            Component = await this.loadComponent(modulePath, exportName);
            if (Component) {
                this.components.set(componentName, Component);
            }
        }

        if (!Component && this.componentModules[componentName]) {
            Component = await this.loadComponent(
                this.componentModules[componentName],
                exportName
            );
            if (Component) {
                this.components.set(componentName, Component);
            }
        }

        if (!Component) {
            console.warn(`React component "${componentName}" not found`);
            return;
        }

        // Parse props
        const props = propsJson ? this.parseProps(propsJson) : {};

        // Hydrate with React
        if (window.React && window.ReactDOM) {
            const React = window.React;
            const ReactDOM = window.ReactDOM;

            try {
                // Use hydrateRoot for React 18+ or hydrate for React 17
                if (ReactDOM.hydrateRoot) {
                    ReactDOM.hydrateRoot(element, React.createElement(Component, props));
                } else if (ReactDOM.hydrate) {
                    ReactDOM.hydrate(React.createElement(Component, props), element);
                } else {
                    // Fallback to render
                    ReactDOM.render(React.createElement(Component, props), element);
                }

                this.hydratedElements.add(element);
                console.log(`Hydrated React component: ${componentName}`);
            } catch (error) {
                console.error(`Failed to hydrate component "${componentName}":`, error);
            }
        } else {
            console.error('React and ReactDOM must be loaded to hydrate components');
        }
    }

    /**
     * Hydrate all React components in the document
     */
    async hydrateAll() {
        const elements = document.querySelectorAll('[data-react-component]');
        const hydrationPromises = Array.from(elements).map(el => this.hydrateElement(el));
        await Promise.all(hydrationPromises);
    }

    /**
     * Watch for new React components added to the DOM
     */
    observeDOM() {
        const observer = new MutationObserver((mutations) => {
            for (const mutation of mutations) {
                for (const node of mutation.addedNodes) {
                    if (node.nodeType === 1) { // Element node
                        if (node.dataset && node.dataset.reactComponent) {
                            this.hydrateElement(node);
                        }
                        // Check children
                        const children = node.querySelectorAll('[data-react-component]');
                        children.forEach(child => this.hydrateElement(child));
                    }
                }
            }
        });

        observer.observe(document.body, {
            childList: true,
            subtree: true
        });

        return observer;
    }

    /**
     * Initialize React hydration on page load
     */
    init() {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', () => {
                this.hydrateAll();
                this.observeDOM();
            });
        } else {
            this.hydrateAll();
            this.observeDOM();
        }
    }
}

// Create global instance
const reactHydration = new ReactHydrationManager();

// Export for use in modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = reactHydration;
}

// Export for global access
window.DjangoRustLiveReact = reactHydration;

// Auto-initialize if React is already loaded
if (window.React && window.ReactDOM) {
    reactHydration.init();
} else {
    // Wait for React to load
    const checkReact = setInterval(() => {
        if (window.React && window.ReactDOM) {
            clearInterval(checkReact);
            reactHydration.init();
        }
    }, 100);

    // Timeout after 10 seconds
    setTimeout(() => clearInterval(checkReact), 10000);
}
