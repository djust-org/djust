/**
 * djust i18n - Client-side internationalization support
 *
 * Provides:
 * - Language switching without page reload
 * - RTL detection and document updates
 * - Translation access from JavaScript
 * - Locale-aware formatting helpers
 *
 * The main i18n object is initialized by {% djust_i18n_js %} template tag.
 * This module extends it with additional functionality.
 */

(function() {
  'use strict';

  // Ensure djust namespace exists
  window.djust = window.djust || {};

  // RTL languages list (fallback if not provided by server)
  const RTL_LANGUAGES = [
    'ar', 'arc', 'dv', 'fa', 'ha', 'he', 'khw', 'ks', 'ku', 'ps', 'ur', 'yi'
  ];

  /**
   * Initialize i18n if not already done by template tag
   */
  function ensureI18n() {
    if (!window.djust.i18n) {
      window.djust.i18n = {
        _config: {
          lang: document.documentElement.lang || 'en',
          isRtl: false,
          dir: 'ltr',
          translations: {},
          availableLanguages: [],
          rtlLanguages: RTL_LANGUAGES
        },

        get lang() { return this._config.lang; },
        get isRtl() { return this._config.isRtl; },
        get dir() { return this._config.dir; },
        get availableLanguages() { return this._config.availableLanguages; },

        get: function(key, fallback) {
          return this._config.translations[key] || fallback || key;
        },

        isRtlLanguage: function(lang) {
          const baseLang = (lang || '').split('-')[0].toLowerCase();
          return this._config.rtlLanguages.indexOf(baseLang) !== -1;
        },

        addTranslations: function(translations) {
          Object.assign(this._config.translations, translations);
        },

        _setLanguage: function(lang, dir, isRtl) {
          this._config.lang = lang;
          this._config.dir = dir;
          this._config.isRtl = isRtl;

          document.documentElement.lang = lang;
          document.documentElement.dir = dir;

          window.dispatchEvent(new CustomEvent('djust:language-changed', {
            detail: { lang, dir, isRtl }
          }));
        }
      };

      // Detect RTL from current lang attribute
      const baseLang = (document.documentElement.lang || 'en').split('-')[0].toLowerCase();
      if (RTL_LANGUAGES.indexOf(baseLang) !== -1) {
        window.djust.i18n._config.isRtl = true;
        window.djust.i18n._config.dir = 'rtl';
        document.documentElement.dir = 'rtl';
      }
    }
  }

  /**
   * Extended i18n functionality
   */
  const I18nExtensions = {
    /**
     * Format a number using the current locale
     * @param {number} value - Number to format
     * @param {Object} options - Intl.NumberFormat options
     * @returns {string} Formatted number
     */
    formatNumber: function(value, options = {}) {
      try {
        return new Intl.NumberFormat(this.lang, options).format(value);
      } catch (e) {
        console.warn('Number formatting failed:', e);
        return String(value);
      }
    },

    /**
     * Format a currency value
     * @param {number} value - Amount to format
     * @param {string} currency - Currency code (e.g., 'USD')
     * @param {Object} options - Additional Intl.NumberFormat options
     * @returns {string} Formatted currency
     */
    formatCurrency: function(value, currency = 'USD', options = {}) {
      try {
        return new Intl.NumberFormat(this.lang, {
          style: 'currency',
          currency: currency,
          ...options
        }).format(value);
      } catch (e) {
        console.warn('Currency formatting failed:', e);
        return `${currency} ${value}`;
      }
    },

    /**
     * Format a percentage
     * @param {number} value - Value to format (0.5 = 50%)
     * @param {Object} options - Additional Intl.NumberFormat options
     * @returns {string} Formatted percentage
     */
    formatPercent: function(value, options = {}) {
      try {
        return new Intl.NumberFormat(this.lang, {
          style: 'percent',
          ...options
        }).format(value);
      } catch (e) {
        console.warn('Percent formatting failed:', e);
        return `${Math.round(value * 100)}%`;
      }
    },

    /**
     * Format a date
     * @param {Date|string|number} value - Date to format
     * @param {Object|string} options - Intl.DateTimeFormat options or preset name
     * @returns {string} Formatted date
     */
    formatDate: function(value, options = {}) {
      const date = value instanceof Date ? value : new Date(value);

      // Handle preset names
      if (typeof options === 'string') {
        const presets = {
          short: { dateStyle: 'short' },
          medium: { dateStyle: 'medium' },
          long: { dateStyle: 'long' },
          full: { dateStyle: 'full' }
        };
        options = presets[options] || presets.medium;
      }

      try {
        return new Intl.DateTimeFormat(this.lang, options).format(date);
      } catch (e) {
        console.warn('Date formatting failed:', e);
        return date.toLocaleDateString();
      }
    },

    /**
     * Format a time
     * @param {Date|string|number} value - Time to format
     * @param {Object|string} options - Intl.DateTimeFormat options or preset name
     * @returns {string} Formatted time
     */
    formatTime: function(value, options = {}) {
      const date = value instanceof Date ? value : new Date(value);

      // Handle preset names
      if (typeof options === 'string') {
        const presets = {
          short: { timeStyle: 'short' },
          medium: { timeStyle: 'medium' },
          long: { timeStyle: 'long' },
          full: { timeStyle: 'full' }
        };
        options = presets[options] || presets.medium;
      }

      try {
        return new Intl.DateTimeFormat(this.lang, options).format(date);
      } catch (e) {
        console.warn('Time formatting failed:', e);
        return date.toLocaleTimeString();
      }
    },

    /**
     * Format a datetime
     * @param {Date|string|number} value - Datetime to format
     * @param {Object|string} options - Intl.DateTimeFormat options or preset name
     * @returns {string} Formatted datetime
     */
    formatDateTime: function(value, options = {}) {
      const date = value instanceof Date ? value : new Date(value);

      // Handle preset names
      if (typeof options === 'string') {
        const presets = {
          short: { dateStyle: 'short', timeStyle: 'short' },
          medium: { dateStyle: 'medium', timeStyle: 'medium' },
          long: { dateStyle: 'long', timeStyle: 'long' },
          full: { dateStyle: 'full', timeStyle: 'full' }
        };
        options = presets[options] || presets.medium;
      }

      try {
        return new Intl.DateTimeFormat(this.lang, options).format(date);
      } catch (e) {
        console.warn('DateTime formatting failed:', e);
        return date.toLocaleString();
      }
    },

    /**
     * Format relative time (e.g., "2 hours ago", "in 3 days")
     * @param {Date|string|number} value - Date to compare against now
     * @param {Object} options - Intl.RelativeTimeFormat options
     * @returns {string} Relative time string
     */
    formatRelativeTime: function(value, options = {}) {
      const date = value instanceof Date ? value : new Date(value);
      const now = new Date();
      const diffMs = date - now;
      const diffSecs = Math.round(diffMs / 1000);
      const diffMins = Math.round(diffSecs / 60);
      const diffHours = Math.round(diffMins / 60);
      const diffDays = Math.round(diffHours / 24);
      const diffWeeks = Math.round(diffDays / 7);
      const diffMonths = Math.round(diffDays / 30);
      const diffYears = Math.round(diffDays / 365);

      try {
        const rtf = new Intl.RelativeTimeFormat(this.lang, {
          numeric: 'auto',
          ...options
        });

        if (Math.abs(diffSecs) < 60) {
          return rtf.format(diffSecs, 'second');
        } else if (Math.abs(diffMins) < 60) {
          return rtf.format(diffMins, 'minute');
        } else if (Math.abs(diffHours) < 24) {
          return rtf.format(diffHours, 'hour');
        } else if (Math.abs(diffDays) < 7) {
          return rtf.format(diffDays, 'day');
        } else if (Math.abs(diffWeeks) < 4) {
          return rtf.format(diffWeeks, 'week');
        } else if (Math.abs(diffMonths) < 12) {
          return rtf.format(diffMonths, 'month');
        } else {
          return rtf.format(diffYears, 'year');
        }
      } catch (e) {
        console.warn('Relative time formatting failed:', e);
        return date.toLocaleString();
      }
    },

    /**
     * Get plural form of a word
     * @param {number} count - Number for pluralization
     * @param {Object} forms - Object with plural forms { one: '...', other: '...' }
     * @returns {string} Appropriate plural form
     */
    plural: function(count, forms) {
      try {
        const pr = new Intl.PluralRules(this.lang);
        const rule = pr.select(count);
        return forms[rule] || forms.other || forms.one || '';
      } catch (e) {
        console.warn('Plural rules failed:', e);
        return count === 1 ? (forms.one || '') : (forms.other || '');
      }
    },

    /**
     * Get list formatter
     * @param {Array} items - Items to format as a list
     * @param {Object} options - Intl.ListFormat options
     * @returns {string} Formatted list (e.g., "A, B, and C")
     */
    formatList: function(items, options = {}) {
      try {
        return new Intl.ListFormat(this.lang, {
          style: 'long',
          type: 'conjunction',
          ...options
        }).format(items);
      } catch (e) {
        console.warn('List formatting failed:', e);
        return items.join(', ');
      }
    },

    /**
     * Update RTL styles on elements with data-rtl attributes
     */
    updateRtlElements: function() {
      const isRtl = this.isRtl;

      // Elements with data-rtl-class
      document.querySelectorAll('[data-rtl-class]').forEach(el => {
        const rtlClass = el.dataset.rtlClass;
        const ltrClass = el.dataset.ltrClass || '';

        if (isRtl) {
          el.classList.remove(...ltrClass.split(' ').filter(Boolean));
          el.classList.add(...rtlClass.split(' ').filter(Boolean));
        } else {
          el.classList.remove(...rtlClass.split(' ').filter(Boolean));
          if (ltrClass) {
            el.classList.add(...ltrClass.split(' ').filter(Boolean));
          }
        }
      });

      // Elements with data-rtl-style (inline style toggle)
      document.querySelectorAll('[data-rtl-style]').forEach(el => {
        const rtlStyle = el.dataset.rtlStyle;
        const ltrStyle = el.dataset.ltrStyle || '';

        if (isRtl && rtlStyle) {
          el.style.cssText = rtlStyle;
        } else if (ltrStyle) {
          el.style.cssText = ltrStyle;
        }
      });
    }
  };

  // Initialize on load
  ensureI18n();

  // Extend i18n object with additional methods
  Object.assign(window.djust.i18n, I18nExtensions);

  /**
   * Handle i18n commands from server
   */
  function handleI18nCommand(command) {
    switch (command.type) {
      case 'set_language':
        window.djust.i18n._setLanguage(command.lang, command.dir, command.is_rtl);
        window.djust.i18n.updateRtlElements();
        break;

      case 'persist_language':
        // Set cookie for language preference
        const maxAge = 365 * 24 * 60 * 60; // 1 year
        document.cookie = `djust_lang=${command.lang};path=/;max-age=${maxAge};SameSite=Lax`;
        break;

      case 'add_translations':
        window.djust.i18n.addTranslations(command.translations);
        break;
    }
  }

  // Register command handler with WebSocket
  if (window.djust && window.djust.registerCommandHandler) {
    window.djust.registerCommandHandler('i18n', handleI18nCommand);
  }

  // Listen for language change events from server responses
  document.addEventListener('djust:response', function(event) {
    const response = event.detail;
    if (response && response.i18n_commands) {
      response.i18n_commands.forEach(handleI18nCommand);
    }
  });

  // Initial RTL element update
  document.addEventListener('DOMContentLoaded', function() {
    if (window.djust.i18n) {
      window.djust.i18n.updateRtlElements();
    }
  });

  // Update RTL elements after VDOM patches
  document.addEventListener('djust:patched', function() {
    if (window.djust.i18n) {
      window.djust.i18n.updateRtlElements();
    }
  });

  /**
   * CSS helper: inject RTL-aware utility classes
   */
  function injectRtlStyles() {
    const styleId = 'djust-i18n-styles';
    if (document.getElementById(styleId)) return;

    const style = document.createElement('style');
    style.id = styleId;
    style.textContent = `
      /* RTL-aware flex utilities */
      [dir="rtl"] .djust-flex-row { flex-direction: row-reverse; }
      [dir="rtl"] .djust-text-start { text-align: right; }
      [dir="rtl"] .djust-text-end { text-align: left; }
      [dir="ltr"] .djust-text-start { text-align: left; }
      [dir="ltr"] .djust-text-end { text-align: right; }

      /* RTL-aware margin/padding (start/end) */
      [dir="rtl"] .djust-ms-1 { margin-right: 0.25rem; margin-left: 0; }
      [dir="rtl"] .djust-me-1 { margin-left: 0.25rem; margin-right: 0; }
      [dir="rtl"] .djust-ms-2 { margin-right: 0.5rem; margin-left: 0; }
      [dir="rtl"] .djust-me-2 { margin-left: 0.5rem; margin-right: 0; }
      [dir="rtl"] .djust-ms-3 { margin-right: 1rem; margin-left: 0; }
      [dir="rtl"] .djust-me-3 { margin-left: 1rem; margin-right: 0; }
      [dir="rtl"] .djust-ps-1 { padding-right: 0.25rem; padding-left: 0; }
      [dir="rtl"] .djust-pe-1 { padding-left: 0.25rem; padding-right: 0; }
      [dir="rtl"] .djust-ps-2 { padding-right: 0.5rem; padding-left: 0; }
      [dir="rtl"] .djust-pe-2 { padding-left: 0.5rem; padding-right: 0; }
      [dir="rtl"] .djust-ps-3 { padding-right: 1rem; padding-left: 0; }
      [dir="rtl"] .djust-pe-3 { padding-left: 1rem; padding-right: 0; }

      [dir="ltr"] .djust-ms-1 { margin-left: 0.25rem; }
      [dir="ltr"] .djust-me-1 { margin-right: 0.25rem; }
      [dir="ltr"] .djust-ms-2 { margin-left: 0.5rem; }
      [dir="ltr"] .djust-me-2 { margin-right: 0.5rem; }
      [dir="ltr"] .djust-ms-3 { margin-left: 1rem; }
      [dir="ltr"] .djust-me-3 { margin-right: 1rem; }
      [dir="ltr"] .djust-ps-1 { padding-left: 0.25rem; }
      [dir="ltr"] .djust-pe-1 { padding-right: 0.25rem; }
      [dir="ltr"] .djust-ps-2 { padding-left: 0.5rem; }
      [dir="ltr"] .djust-pe-2 { padding-right: 0.5rem; }
      [dir="ltr"] .djust-ps-3 { padding-left: 1rem; }
      [dir="ltr"] .djust-pe-3 { padding-right: 1rem; }

      /* RTL-aware float */
      [dir="rtl"] .djust-float-start { float: right; }
      [dir="rtl"] .djust-float-end { float: left; }
      [dir="ltr"] .djust-float-start { float: left; }
      [dir="ltr"] .djust-float-end { float: right; }

      /* RTL-aware borders */
      [dir="rtl"] .djust-border-start { border-right: 1px solid; border-left: 0; }
      [dir="rtl"] .djust-border-end { border-left: 1px solid; border-right: 0; }
      [dir="ltr"] .djust-border-start { border-left: 1px solid; }
      [dir="ltr"] .djust-border-end { border-right: 1px solid; }

      /* Transition for language switch */
      .djust-i18n-transition {
        transition: all 0.2s ease;
      }
    `;
    document.head.appendChild(style);
  }

  // Inject styles when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', injectRtlStyles);
  } else {
    injectRtlStyles();
  }

})();
