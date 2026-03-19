
// ============================================================================
// Page Metadata — Dynamic document title and meta tag updates
// ============================================================================

(function () {

    // CSS.escape fallback for environments that don't support it (e.g., older browsers)
    var cssEscape = (typeof CSS !== 'undefined' && CSS.escape)
        ? CSS.escape
        : function (s) { return s.replace(/([^\w-])/g, '\\$1'); };

    /**
     * Handle a page metadata command from the server.
     *
     * data.action === 'title': update document.title
     * data.action === 'meta':  update or create a <meta> tag
     */
    function handlePageMetadata(data) {
        if (globalThis.djustDebug) console.log('[LiveView] page_metadata:', data);

        if (data.action === 'title') {
            document.title = data.value;
        } else if (data.action === 'meta') {
            var name = data.name;
            // Support both name= and property= attributes (og: and twitter: use property)
            var isOg = name.indexOf('og:') === 0 || name.indexOf('twitter:') === 0;
            var attr = isOg ? 'property' : 'name';
            var selector = 'meta[' + attr + '="' + cssEscape(name) + '"]';
            var el = document.querySelector(selector);
            if (el) {
                el.setAttribute('content', data.content);
            } else {
                el = document.createElement('meta');
                el.setAttribute(attr, name);
                el.setAttribute('content', data.content);
                document.head.appendChild(el);
            }
        }
    }

    // Expose to djust namespace
    window.djust.pageMetadata = {
        handlePageMetadata: handlePageMetadata,
    };

})();
