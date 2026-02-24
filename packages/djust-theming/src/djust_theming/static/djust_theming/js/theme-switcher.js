/**
 * djust Theme Switcher
 *
 * Handles dark/light mode toggling with localStorage persistence
 * and system preference detection.
 */

(function() {
    'use strict';

    // Wait for DOM to be ready
    document.addEventListener('DOMContentLoaded', function() {
        const themeToggle = document.getElementById('theme-toggle');
        const htmlElement = document.documentElement;
        const darkIcon = document.querySelector('.theme-icon-dark');
        const lightIcon = document.querySelector('.theme-icon-light');

        // Get theme from localStorage or system preference
        function getPreferredTheme() {
            const stored = localStorage.getItem('theme');
            if (stored) {
                return stored;
            }
            return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
        }

        // Set theme
        function setTheme(theme) {
            htmlElement.setAttribute('data-theme', theme);
            localStorage.setItem('theme', theme);

            // Update icons
            if (theme === 'dark') {
                darkIcon.style.display = 'block';
                lightIcon.style.display = 'none';
            } else {
                darkIcon.style.display = 'none';
                lightIcon.style.display = 'block';
            }
        }

        // Initialize theme
        const currentTheme = getPreferredTheme();
        setTheme(currentTheme);

        // Toggle theme on button click
        themeToggle.addEventListener('click', function() {
            const currentTheme = htmlElement.getAttribute('data-theme') || 'dark';
            const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
            setTheme(newTheme);
        });

        // Listen for system theme changes
        window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', function(e) {
            if (!localStorage.getItem('theme')) {
                setTheme(e.matches ? 'light' : 'dark');
            }
        });
    });
})();
